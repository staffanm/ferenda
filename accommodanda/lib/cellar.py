"""The CELLAR machinery: ask the EU:s publikationsbyrå for a document by its
CELEX and get back the best text it holds, in the best language it holds it in.

Extracted from `eurlex/download.py` when a second source needed it
(rule:second-use-goes-to-lib). `eurlex` harvests whole CELEX sectors; the
`guidance` source harvests the works one named EU body authored and stores them
under that body's own number rather than under a CELEX. Both need exactly this:
*given a CELEX, fetch its content*. Neither the sector walk nor the number
scheme belongs here -- this module knows CELEX in and bytes out, and nothing
about who asked.

Two preferences are the reason this is worth sharing rather than reimplementing,
because both are answers to how CELLAR actually behaves rather than choices:

  * **language**, `LANGUAGES` -- Swedish where the body published one, English
    otherwise. A caller may narrow it; the order is the fallback.
  * **format**, `TEXT_PREFERENCE` -- Formex, then XHTML, then HTML, then any
    PDF. Richest first, and *verified*: a manifestation may promise `fmx4` and
    serve a scanned TIFF placeholder, so `_content_ok` reads the bytes and the
    next format is fetched instead of the promise being believed.
"""

import re
import subprocess
from collections import defaultdict
from urllib.parse import quote

from . import compress
from .net import request

SPARQL = "https://publications.europa.eu/webapi/rdf/sparql"
CELLAR = "http://publications.europa.eu/resource/celex/%s"
LANGUAGES = ("swe", "eng")
# manifestation types we'll take, richest first; any pdf* sub-type (pdf,
# pdfa1a, pdfa2a, pdfx, ...) is the last resort. The stored file suffix:
TEXT_PREFERENCE = ("fmx4", "xhtml", "html")
SUFFIX = {"fmx4": ".fmx4", "xhtml": ".xhtml", "html": ".html"}
ZIP_MAGIC = b"PK\x03\x04"
# CELLAR serves a whole manifestation (all its items in one archive) to an
# explicit zip Accept; a bare item URL is fetched with the session default.
ZIP_ACCEPT = "application/zip"

CDM = "http://publications.europa.eu/ontology/cdm#"
OWL_SAMEAS = "http://www.w3.org/2002/07/owl#sameAs"

# The bulk unpacker still turns a dump's per-work rdf/xml notice into our stored
# notice.ttl. Such a notice is huge (a court judgment's runs to 500k+ triples
# across 24 languages, citation closure and provenance) and we read ~6 edges out
# of it, so we stream the rdf/xml through raptor's `rapper` (C, constant-memory)
# to n-triples, keep only the predicates a little metadata needs, and store that
# subset -- itself valid turtle. (The live download path no longer fetches these
# notices at all; it selects over SPARQL -- see fetch_selection.)
P_EXPR_WORK = CDM + "expression_belongs_to_work"
P_EXPR_LANG = CDM + "expression_uses_language"
P_EXPR_MANIF = CDM + "expression_manifested_by_manifestation"
P_MANIF_EXPR = CDM + "manifestation_manifests_expression"   # the inverse edge
P_MANIF_TYPE = CDM + "manifestation_type"
P_ITEM_MANIF = CDM + "item_belongs_to_manifestation"
# selection needs these edges; the rest are metadata worth keeping in the subset
SELECT_PREDICATES = {P_EXPR_WORK, P_EXPR_LANG, P_EXPR_MANIF, P_MANIF_EXPR,
                     P_MANIF_TYPE, P_ITEM_MANIF, OWL_SAMEAS}
META_PREDICATES = {CDM + p for p in (
    "resource_legal_id_celex", "resource_legal_id_sector", "work_date_document",
    "expression_title", "expression_subtitle", "start_of_validity",
    "end_of_validity", "work_is_about_concept_eurovoc")}
KEEP_PREDICATES = SELECT_PREDICATES | META_PREDICATES


def sparql_select(session, query):
    # POST: the selection/metadata queries pass the year's CELEX in a VALUES
    # block, far past what a GET URL holds (the endpoint accepts either).
    return request(session, "POST", SPARQL, parse_json=True, timeout=120,
                   data={"query": query,
                         "format": "application/sparql-results+json"}
                   )["results"]["bindings"]


# --------------------------------------------------------------------------
# content -- tree notice -> best manifestation per language -> item
# --------------------------------------------------------------------------

def _term(token):
    """The bare value of an n-triples term: a URI/blank-node id, or a literal's
    lexical value (we never join on literals, so datatype/language are dropped)."""
    if token.startswith("<"):
        return token[1:-1]
    if token.startswith('"'):
        return token[1:token.rfind('"')]
    return token                                  # _:blank node


def _ntriples(rdfxml):
    """Stream rdf/xml bytes through raptor's `rapper` (C, constant-memory) to
    n-triples, returning the kept lines as (raw_line, subject, predicate, object)
    -- only lines whose predicate is in KEEP_PREDICATES. The raw lines double as
    the stored notice, since n-triples is a subset of turtle."""
    out = subprocess.run(
        ["rapper", "-q", "-i", "rdfxml", "-o", "ntriples", "-",
         "http://publications.europa.eu/"],
        input=rdfxml, capture_output=True, check=True).stdout.decode()
    kept = []
    for line in out.splitlines():
        if not line:
            continue
        s, p, rest = line.split(" ", 2)
        pred = p[1:-1]
        if pred in KEEP_PREDICATES:
            obj = rest.rstrip()[:-1].rstrip()     # drop the trailing ' .'
            kept.append((line, _term(s), pred, _term(obj)))
    return kept


class Notice:
    """The kept triples of a tree notice -- all any caller does with one is
    persist it (`ttl()`); the old per-triple lookup surface is gone with the
    tree-notice fetch path it served."""

    def __init__(self, triples):
        self.lines = [line for line, *_ in triples]

    def ttl(self):
        return ("\n".join(self.lines) + "\n").encode()


def parse_notice(rdfxml):
    """A Notice from rdf/xml bytes -- the download path fetches them from CELLAR,
    the bulk unpacker reads them out of a dump."""
    return Notice(_ntriples(rdfxml))


# --- selection over SPARQL: the live path's replacement for the tree notice ---
# We read the work -> expression -> manifestation -> item edges straight from the
# endpoint in batches keyed by CELEX, instead of assembling a ~10s tree notice
# per document. The endpoint's query planner chokes on the manifestation join
# combined with an owl:sameAs OPTIONAL over a whole year, so streams (needed only
# to drop the .doc.xml wrapper item) are resolved in a second, item-scoped query.

PREFIXES = ("PREFIX cdm: <http://publications.europa.eu/ontology/cdm#> "
            "PREFIX owl: <http://www.w3.org/2002/07/owl#> ")
XSD_STRING = "http://www.w3.org/2001/XMLSchema#string"
XSD_DATE = "http://www.w3.org/2001/XMLSchema#date"
SELECT_CHUNK = 1000        # CELEX per selection/metadata query
STREAM_CHUNK = 500         # items per wrapper-resolution query


def _literals(values):
    return " ".join('"%s"^^<%s>' % (v, XSD_STRING) for v in values)


def _uris(values):
    return " ".join("<%s>" % v for v in values)


def _chunked(session, build_query, terms, size):
    """Run a VALUES-based query over `terms` in chunks, concatenating the result
    bindings -- the endpoint takes these by POST, so chunking only keeps a single
    query (and its result) a sane size."""
    rows = []
    for i in range(0, len(terms), size):
        rows += sparql_select(session, build_query(terms[i:i + size]))
    return rows


def _selection_query(celexes, languages):
    langs = ", ".join('"%s"' % code.upper() for code in languages)
    return (PREFIXES +
            "SELECT ?celex ?lang ?mtype ?item WHERE { VALUES ?celex { %s } "
            "?w cdm:resource_legal_id_celex ?celex . "
            "?expr cdm:expression_belongs_to_work ?w ; "
            "cdm:expression_uses_language ?langc . "
            "?manif cdm:manifestation_manifests_expression ?expr ; "
            "cdm:manifestation_type ?mtype . "
            "?item cdm:item_belongs_to_manifestation ?manif . "
            "BIND(REPLACE(STR(?langc), '.*/', '') AS ?lang) "
            "FILTER(?lang IN (%s)) }" % (_literals(celexes), langs))


def _stream_query(items):
    return (PREFIXES + "SELECT ?item ?stream WHERE { VALUES ?item { %s } "
            "?item owl:sameAs ?stream }" % _uris(items))


def _metadata_query(celexes):
    return (PREFIXES +
            "SELECT ?celex ?wdate ?concept WHERE { VALUES ?celex { %s } "
            "?w cdm:resource_legal_id_celex ?celex . "
            "OPTIONAL { ?w cdm:work_date_document ?wdate } "
            "OPTIONAL { ?w cdm:work_is_about_concept_eurovoc ?concept } }"
            % _literals(celexes))


def _ranked_types(by_type):
    """The manifestation types present, richest first: fmx4 > xhtml > html >
    any pdf. A document is fetched down this list until one yields content that
    matches its declared format (see _content_ok) -- some scanned old judgments
    expose an `fmx4`-typed manifestation whose item is actually a TIFF image, so
    the richest *type* is not always the richest *content*."""
    ranked = [t for t in TEXT_PREFERENCE if t in by_type]
    return ranked + sorted(t for t in by_type if t.startswith("pdf"))


def _content_ok(filetype, content):
    """Whether a fetched item's bytes match the format its manifestation type
    promises. CELLAR sometimes serves a scanned image (TIFF: II*\\0 / MM\\0*)
    under an `fmx4`/`xhtml`/`html` manifestation; such a placeholder fails here so
    the caller falls back to the next type (which carries the real text).

    `filetype` always comes from `_ranked_types`, so it is one of
    TEXT_PREFERENCE or a `pdf*` type -- an unrecognised type here means that
    set changed without teaching this function the new type's signature, so
    it must raise rather than wave the content through unchecked."""
    if filetype == "fmx4":
        return content.lstrip()[:1] == b"<" or content.startswith(ZIP_MAGIC)
    if filetype in ("xhtml", "html"):
        return content.lstrip()[:1] == b"<"
    if filetype.startswith("pdf"):
        return content.startswith(b"%PDF")
    raise ValueError("no content check for manifestation type %r" % filetype)


def _is_wrapper(streams):
    """A Formex manifestation carries both the real `.xml` content item and a
    `.doc.xml` wrapper item; the wrapper's stream URIs all end in `.doc.xml`."""
    return bool(streams) and all(s.endswith((".doc.xml", ".doc")) for s in streams)


def _resolve_streams(session, items):
    """item URL -> its owl:sameAs stream URIs, for the items that need wrapper
    disambiguation (every fmx4 item, plus any other manifestation carrying
    more than one item)."""
    streams = defaultdict(list)
    for row in _chunked(session, _stream_query, sorted(items), STREAM_CHUNK):
        streams[row["item"]["value"]].append(row["stream"]["value"])
    return streams


def manifestation_url(item_url):
    """The manifestation an item belongs to: the item URL minus its `/DOC_n`
    tail. Fetching *that* with a zip Accept yields every item at once, which is
    how a multi-part act is taken whole (see fetch_selection).

    The `/DOC_n` shape is CELLAR's item convention. A URL without it means the
    convention changed, and the derived URL would silently address something
    else -- so it raises: this runs inside the harvest walk, where a failure is
    a recorded rejection and the run continues, and an `assert` would vanish
    under `python -O` and fetch the wrong resource
    (rule:errors-drive-retry-use-raise)."""
    base, sep, doc = item_url.rpartition("/")
    if not (sep and re.fullmatch(r"DOC_\d+", doc)):
        raise ValueError("not a CELLAR item URL: %r" % item_url)
    return base


def fetch_selection(session, celexes, languages):
    """For each CELEX, the ranked content candidates per requested language: a
    list `(code, [(filetype, url, accept), ...])` ordered fmx4 > xhtml > html >
    pdf, with the .doc.xml wrapper item dropped. store_document fetches down each
    language's list until one item's bytes match its format -- the bulk
    replacement for per-document tree-notice selection.

    `accept` is None for a plain item fetch, ZIP_ACCEPT for a *multi-part*
    Formex manifestation: an act published across several OJ files (the main
    text plus one file per annex -- 2004/18 has twelve) exposes one item per
    part, and no single item is the document. Those are fetched as the whole
    manifestation in one zip, which is exactly the `.fmx4.zip` bundle the bulk
    importer produces and `parse.formex_members` already reads in order. Only
    Formex takes this route: a zip is not readable as xhtml/html/pdf content,
    so a multi-part manifestation of those types still yields its first part."""
    code_of = {code.upper(): code for code in languages}
    tree = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for row in _chunked(session, lambda c: _selection_query(c, languages),
                        celexes, SELECT_CHUNK):
        code = code_of.get(row["lang"]["value"])
        if code:
            (tree[row["celex"]["value"]][code]
                 [row["mtype"]["value"]].append(row["item"]["value"]))

    # wrapper disambiguation (the real .xml content item vs its .doc.xml
    # wrapper): every fmx4 item needs its streams resolved -- a wrapper-only
    # work's Formex manifestation carries the .doc.xml wrapper as its *single*
    # item, so multi-item is not a sufficient trigger there -- plus any other
    # type's manifestation carrying more than one item.
    ambiguous = {i for by_lang in tree.values() for by_type in by_lang.values()
                 for mtype, items in by_type.items()
                 if mtype == "fmx4" or len(items) > 1 for i in items}
    streams = _resolve_streams(session, ambiguous) if ambiguous else {}

    out = defaultdict(list)
    for celex, by_lang in tree.items():
        for code, by_type in by_lang.items():
            candidates = []
            for filetype in _ranked_types(by_type):
                real = [i for i in by_type[filetype]
                        if not _is_wrapper(streams.get(i, ()))]
                if len(real) > 1 and filetype == "fmx4":
                    # a multi-part act: no single item is the document, so take
                    # the whole manifestation as one zip
                    candidates.append((filetype, manifestation_url(real[0]),
                                       ZIP_ACCEPT))
                elif real:
                    candidates.append((filetype, real[0], None))
                # else: every item was a wrapper (a wrapper-only Formex work).
                # The type is skipped entirely so the document degrades to the
                # next one -- shipping the .doc.xml manifest as content is worse
                # than falling back to html/pdf (bulk.py's _select_content
                # degrades the same way)
            if candidates:
                out[celex].append((code, candidates))
    return out


def fetch_metadata(session, celexes):
    """celex -> (work_date or None, [eurovoc concept URIs]) -- the metadata kept
    in the synthesized notice (the work date also feeds the per-CELEX refetch)."""
    wdate, concepts = {}, defaultdict(list)
    for row in _chunked(session, _metadata_query, celexes, SELECT_CHUNK):
        celex = row["celex"]["value"]
        if "wdate" in row:
            wdate[celex] = row["wdate"]["value"][:10]
        concept = row.get("concept", {}).get("value")
        if concept and concept not in concepts[celex]:
            concepts[celex].append(concept)
    return wdate, concepts


def notice_ttl(celex, wdate, eurovoc):
    """The metadata we keep for a downloaded CELEX, as n-triples (a subset of
    turtle) on the stable CELLAR celex URI: celex, sector, work date and any
    eurovoc concepts. The live path no longer fetches the tree notice, so this
    stands in for it -- the metadata worth keeping, and the on-disk marker the
    harvester and parser key on."""
    subj = "<%s>" % (CELLAR % quote(celex, safe=""))
    triples = ['%s <%s> "%s" .' % (subj, CDM + "resource_legal_id_celex", celex),
               '%s <%s> "%s" .' % (subj, CDM + "resource_legal_id_sector",
                                   celex[0])]
    if wdate:
        triples.append('%s <%s> "%s"^^<%s> .'
                       % (subj, CDM + "work_date_document", wdate, XSD_DATE))
    for concept in eurovoc:
        triples.append('%s <%s> <%s> .'
                       % (subj, CDM + "work_is_about_concept_eurovoc", concept))
    return ("\n".join(triples) + "\n").encode()


def content_filename(code, filetype, content):
    """The stored filename for a fetched item. CELLAR often returns a Formex
    manifestation not as a single .fmx4 but as a zip of several .fmx4 files (the
    act plus one per annex); flag that as `{lang}.fmx4.zip` so the parser and
    other consumers can tell without sniffing."""
    suffix = SUFFIX.get(filetype, ".pdf")
    if content.startswith(ZIP_MAGIC):
        suffix = suffix + ".zip"
    return code + suffix


def store_document(session, target, celex, wdate, selection, eurovoc):
    """Write a CELEX's synthesized notice and fetch its selected content per
    language. `selection` is the [(lang, [(filetype, url, accept), ...])]
    candidate list fetch_selection returns for this CELEX. Returns the languages
    stored. Throttling is the caller's (one delay per document, not per item).

    Each language's candidates are tried richest-first; the first item whose bytes
    match its format wins. A CELLAR manifestation can promise `fmx4` but serve a
    scanned TIFF image -- that placeholder is rejected (see _content_ok) and the
    next type (the one carrying the real text) is fetched instead.

    A CELEX with no *stored* content -- `selection` empty (a pre-accession act
    never translated), or every candidate rejected by _content_ok (a
    scanned-TIFF placeholder in each language) -- gets *no notice at all*: a
    notice with no document is dead weight the parser can only skip, and
    (is_downloaded keys on the notice) it would permanently mask the work from
    a later run that does find content. The notice is therefore written after
    the first successful content store, never before.

    No-notice alone is not enough to actually retry, though: once the work's
    date falls below the incremental floor the walk stops enumerating it. So
    sync records recent no-content CELEX on a retry sidecar (read_pending /
    write_pending) and re-attempts them at the start of every incremental run --
    that, not the missing notice, is what lets a work that only later gains
    content (a TIFF replaced by real Formex, an act translated after
    publication) be picked up."""
    if not selection:
        return []
    stored = []
    for code, candidates in selection:
        for filetype, url, accept in candidates:
            response = request(session, "GET", url, timeout=180,
                               headers={"Accept": accept} if accept else None)
            if not _content_ok(filetype, response.content):
                continue                # placeholder for this type: try the next
            name = content_filename(code, filetype, response.content)
            compress.write_download(target / name, response.content)
            # a re-fetch may land a different manifestation type or zip-ness, so
            # clear any earlier content file for this language
            for old in compress.glob(target, code + ".*"):
                if old.name != name:
                    compress.unlink(old)
            stored.append(code)
            break
    if stored:
        compress.write_download(target / "notice.ttl", notice_ttl(celex, wdate, eurovoc))
    return stored
