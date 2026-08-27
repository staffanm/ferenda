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
from pathlib import Path
from urllib.parse import quote, unquote

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
# whether the act still states law, and when it stopped. CELLAR names these
# `resource_legal_in-force` ("1"/"0") and `resource_legal_date_end-of-validity`;
# the earlier `start_of_validity`/`end_of_validity` names kept here matched no
# triple in the graph, which is why no stored notice ever carried a repeal date.
P_IN_FORCE = CDM + "resource_legal_in-force"
P_END_OF_VALIDITY = CDM + "resource_legal_date_end-of-validity"
# What an act does to another act instead of stating law of its own. CELLAR
# tags the same maintenance job either way -- the 2026 terrorist-list
# regulation (32026R1878) carries `amends`, the 2025 one (32025R1578) carries
# `implements` -- so a consumer asking "is this a base act" has to read both.
P_AMENDS = CDM + "resource_legal_amends_resource_legal"
P_IMPLEMENTS = CDM + "resource_legal_implements_resource_legal"
RELATION_PREDICATES = {"amends": P_AMENDS, "implements": P_IMPLEMENTS}
# CELLAR writes this end-of-validity when the act has no end date at all, so it
# is a placeholder, not a date
OPEN_ENDED = "9999-12-31"
META_PREDICATES = {CDM + p for p in (
    "resource_legal_id_celex", "resource_legal_id_sector", "work_date_document",
    "expression_title", "expression_subtitle",
    "resource_legal_date_entry-into-force",
    "work_is_about_concept_eurovoc")} | {P_IN_FORCE, P_END_OF_VALIDITY,
                                         P_AMENDS, P_IMPLEMENTS}
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
    return keep_triples(subprocess.run(
        ["rapper", "-q", "-i", "rdfxml", "-o", "ntriples", "-",
         "http://publications.europa.eu/"],
        input=rdfxml, capture_output=True, check=True).stdout.decode().splitlines())


def keep_triples(lines):
    """The n-triples lines whose predicate is in KEEP_PREDICATES, as
    (raw_line, subject, predicate, object). Split out of `_ntriples` so it can
    be exercised without `rapper`: this is the step that decides what a
    dump-imported notice keeps, and a predicate name that matches nothing here
    fails silently -- a filter that keeps nothing looks exactly like a source
    that says nothing."""
    kept = []
    for line in lines:
        if not line:
            continue
        subject, predicate, rest = line.split(" ", 2)
        pred = predicate[1:-1]
        if pred in KEEP_PREDICATES:
            obj = rest.rstrip()[:-1].rstrip()     # drop the trailing ' .'
            kept.append((line, _term(subject), pred, _term(obj)))
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
            "SELECT ?celex ?wdate ?concept ?inforce ?eov WHERE { "
            "VALUES ?celex { %s } "
            "?w cdm:resource_legal_id_celex ?celex . "
            "OPTIONAL { ?w cdm:work_date_document ?wdate } "
            "OPTIONAL { ?w cdm:work_is_about_concept_eurovoc ?concept } "
            "OPTIONAL { ?w cdm:resource_legal_in-force ?inforce } "
            "OPTIONAL { ?w cdm:resource_legal_date_end-of-validity ?eov } }"
            % _literals(celexes))


# What a *new* act says it repeals. The repeal is recorded on both acts, but
# asymmetrically: the new act carries the outgoing edge, and the old one carries
# only its changed `resource_legal_in-force` / end-of-validity -- there is no
# `repealed_by` edge to read. So an act repealed years after we harvested it
# never tells us on its own; something has to go back and re-read it, and this
# edge on the incoming act is what names which ones.
REPEALS = ("cdm:resource_legal_repeals_resource_legal"
           "|cdm:resource_legal_implicitly_repeals_resource_legal")


def _repeals_query(celexes):
    # kept out of _metadata_query on purpose: that query already crosses eurovoc
    # concepts with end-of-validity dates, and a third multi-valued OPTIONAL
    # multiplies the row count again for no gain
    return (PREFIXES + "SELECT ?celex ?repealed WHERE { VALUES ?celex { %s } "
            "?w cdm:resource_legal_id_celex ?celex . "
            "?w %s ?r . ?r cdm:resource_legal_id_celex ?repealed }"
            % (_literals(celexes), REPEALS))


# What an act amends or implements, as CELEX. Kept out of _metadata_query for
# the same reason the repeals query is: that query already crosses eurovoc
# concepts with end-of-validity dates, and a fourth multi-valued OPTIONAL
# multiplies the row count again. One query answers for both relations -- an
# act that maintains another usually carries only one of them, and asking twice
# doubles the round trips for no gain.
def _relations_query(celexes):
    return (PREFIXES + "SELECT ?celex ?p ?target WHERE { VALUES ?celex { %s } "
            "?w cdm:resource_legal_id_celex ?celex . "
            "VALUES ?p { <%s> <%s> } ?w ?p ?r . "
            "?r cdm:resource_legal_id_celex ?target }"
            % (_literals(celexes), P_AMENDS, P_IMPLEMENTS))


def fetch_relations(session, celexes):
    """``{celex: {"amends": [celex, ...], "implements": [...]}}`` for the acts
    that carry either relation. An act carrying neither is simply absent, and
    so is one CELLAR did not answer for: this makes no distinction between the
    two, unlike `fetch_metadata`'s `answered` set. `refresh_metadata` therefore
    treats a stored relation as a ratchet -- once written it is never cleared,
    the way a repeal never lifts. That is deliberate: an empty answer from the
    endpoint is a hiccup as often as it is a fact, and clearing on one would
    put an amending act back into every population that excludes it.

    This is what separates a base act from the acts that only maintain it. The
    corpus needs it because the wording never settles: the same terrorist-list
    regulation is titled "om ändring för nittioåttonde gången av" one year and
    "om genomförande av artikel 2.3 i" the next, and a title test built on one
    form silently keeps every act written in the other.

    A relation whose target CELLAR states without a CELEX is dropped -- we key
    documents by CELEX everywhere, and a target we cannot name is a relation we
    cannot store."""
    out = defaultdict(lambda: defaultdict(list))
    by_uri = {uri: key for key, uri in RELATION_PREDICATES.items()}
    for row in _chunked(session, _relations_query, celexes, SELECT_CHUNK):
        key = by_uri[row["p"]["value"]]
        target = row["target"]["value"]
        if target not in out[row["celex"]["value"]][key]:
            out[row["celex"]["value"]][key].append(target)
    return {celex: {k: sorted(v) for k, v in rel.items()}
            for celex, rel in out.items()}


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
    """The metadata kept in the synthesized notice, as
    (work_date, eurovoc concepts, validity pair, answered) -- each of the first
    three keyed by CELEX, the last a set. The work date also feeds the per-CELEX
    refetch.

    The validity pair is CELLAR's own answer to "does this act still state law":
    `resource_legal_in-force` ("1"/"0") and the date it stopped
    (`latest_end_of_validity` picks it out of everything the work states).
    Reading the pair rather than the date alone matters: 32006L0040 carries an
    end date of 2009-04-28 and is still in force.

    `answered` is the CELEX the endpoint bound a row for at all. A caller that
    *rewrites* stored metadata from this answer needs it: an empty answer for a
    chunk -- an endpoint hiccup, a CELEX withdrawn from the graph -- is
    indistinguishable from "this work carries no metadata" without it, and
    writing the second as if it were the first quietly strips a notice."""
    wdate, concepts = {}, defaultdict(list)
    in_force, ends, answered = {}, defaultdict(set), set()
    for row in _chunked(session, _metadata_query, celexes, SELECT_CHUNK):
        celex = row["celex"]["value"]
        answered.add(celex)
        if "wdate" in row:
            wdate[celex] = row["wdate"]["value"][:10]
        concept = row.get("concept", {}).get("value")
        if concept and concept not in concepts[celex]:
            concepts[celex].append(concept)
        if "inforce" in row:
            in_force[celex] = row["inforce"]["value"]
        ends[celex].add(row.get("eov", {}).get("value", "")[:10])
    # only the CELEX that actually stated something about validity, so a caller
    # rewriting a notice can tell "CELLAR says it is in force" from "CELLAR said
    # nothing this time" and keep what it already had for the second
    validity = {celex: (in_force.get(celex), latest_end_of_validity(ends[celex]))
                for celex in answered
                if celex in in_force or latest_end_of_validity(ends[celex])}
    return wdate, concepts, validity, answered


def latest_end_of_validity(dates):
    """The end-of-validity date to believe, out of everything CELLAR states for
    one work: the latest that is not the OPEN_ENDED placeholder, or None.

    An act carries several -- 31981L0576 carries 1996-08-05 and 2014-10-31 --
    and EUR-Lex prints the last. Both readers of the pair apply this: the SPARQL
    answer (`fetch_metadata`) and the stored notice (`notice_repeal_date`)."""
    return max((d for d in dates if d and d != OPEN_ENDED), default=None)


def fetch_repeals(session, celexes):
    """celex -> [CELEX it repeals] for those that repeal anything at all --
    the works whose stored metadata is now out of date.

    Both the express repeal clause (`repeals`) and the implied one
    (`implicitly_repeals`) count: 32016R0679 repeals 31995L0046 expressly and
    32003R1882 by implication, and both stopped applying.

    This names 64% of the acts CELLAR reports out of force (measured over 600
    random non-caselaw documents in the corpus: 225 out of force, 145 named by
    a repeal edge). The rest end by their own terms with no act repealing them,
    and nothing at download time announces those -- they need the periodic
    re-read `refresh_metadata` does."""
    repeals = defaultdict(list)
    for row in _chunked(session, _repeals_query, celexes, SELECT_CHUNK):
        target = row["repealed"]["value"]
        celex = row["celex"]["value"]
        if target not in repeals[celex]:
            repeals[celex].append(target)
    return repeals


def notice_ttl(celex, wdate, eurovoc, validity=(None, None), relations=None):
    """The metadata we keep for a downloaded CELEX, as n-triples (a subset of
    turtle) on the stable CELLAR celex URI: celex, sector, work date, any eurovoc
    concepts, the validity pair (in-force flag + end-of-validity date), and what
    the act amends or implements (`fetch_relations`, as CELEX targets). The
    live path no longer fetches the tree notice, so this stands in for it -- the
    metadata worth keeping, and the on-disk marker the harvester and parser key
    on. Both validity triples are written as CELLAR states them; reading a repeal
    out of them is the consumer's job (`cellar.notice_repeal_date`), which
    has to do it for a bulk-unpacked notice in any case."""
    in_force, end_of_validity = validity
    subj = "<%s>" % (CELLAR % quote(celex, safe=""))
    triples = ['%s <%s> "%s" .' % (subj, CDM + "resource_legal_id_celex", celex),
               '%s <%s> "%s" .' % (subj, CDM + "resource_legal_id_sector",
                                   celex[0])]
    if wdate:
        triples.append('%s <%s> "%s"^^<%s> .'
                       % (subj, CDM + "work_date_document", wdate, XSD_DATE))
    if in_force is not None:
        triples.append('%s <%s> "%s" .' % (subj, P_IN_FORCE, in_force))
    if end_of_validity:
        triples.append('%s <%s> "%s"^^<%s> .'
                       % (subj, P_END_OF_VALIDITY, end_of_validity, XSD_DATE))
    for concept in eurovoc:
        triples.append('%s <%s> <%s> .'
                       % (subj, CDM + "work_is_about_concept_eurovoc", concept))
    # the target is written as its CELLAR celex URI, the same subject shape this
    # notice uses, so the triple reads the same way whether it was synthesized
    # here or unpacked from a dump notice
    for key, pred in RELATION_PREDICATES.items():
        for target in (relations or {}).get(key, ()):
            triples.append('%s <%s> <%s> .'
                           % (subj, pred, CELLAR % quote(target, safe="")))
    return ("\n".join(triples) + "\n").encode()


# --------------------------------------------------------------------------
# notice.ttl, read back
# --------------------------------------------------------------------------

# the work date line in a stored notice.ttl, in both its shapes: the live
# path's synthesized n-triples ('<...cdm#work_date_document> "2016-04-27"^^...')
# and the bulk unpacker's turtle subset ('j.0:work_date_document "1982-03-31"^^...')
RE_NOTICE_WDATE = re.compile(r'work_date_document>?\s+"(\d{4}-\d{2}-\d{2})')


def notice_work_date(doc_dir):
    """The CELLAR work date kept in the document dir's notice.ttl, or None.
    The authoritative document date for a manifestation that carries none of
    its own (old ECR judgment Formex has an empty TITLE; pre-2004 OJ html has
    no bibliographic markup)."""
    text = _notice_text(doc_dir)
    return _first(RE_NOTICE_WDATE, text) if text is not None else None


def _notice_text(doc_dir):
    path = Path(doc_dir) / "notice.ttl"
    if not compress.exists(path):
        return None
    return compress.read_bytes(path).decode("utf-8", "replace")


def _first(pattern, text):
    m = pattern.search(text)
    return m.group(1) if m else None


# the validity pair a notice keeps (`notice_ttl` / `META_PREDICATES`), across
# every notice shape on disk: the n-triples subset
# ('<...cdm#resource_legal_in-force> "0"'), the bulk unpacker's prefixed turtle
# ('j.0:resource_legal_in-force "0"'), and the older tree notices, which write
# the flag as a turtle boolean instead of a digit
# ('j.0:resource_legal_in-force false'). Reading only the digit form missed the
# boolean silently -- an act out of force simply read as in force, which is the
# failure this whole path exists to prevent.
RE_NOTICE_IN_FORCE = re.compile(
    r'resource_legal_in-force>?\s+"?(0|1|true|false)\b')
OUT_OF_FORCE = ("0", "false")
RE_NOTICE_END_OF_VALIDITY = re.compile(
    r'resource_legal_date_end-of-validity>?\s+"(\d{4}-\d{2}-\d{2})')


def notice_validity(doc_dir):
    """The (in-force flag, end-of-validity date) pair a stored notice records,
    each None when the notice does not state it. The stored counterpart of
    `fetch_metadata`'s validity pair, so a refresh can fall back on what is
    already on disk rather than erasing it."""
    text = _notice_text(doc_dir)
    if text is None:
        return (None, None)
    return (_first(RE_NOTICE_IN_FORCE, text),
            latest_end_of_validity(RE_NOTICE_END_OF_VALIDITY.findall(text)))


def notice_repeal_date(doc_dir):
    """The date this act stopped stating law, per CELLAR's own validity
    metadata, or None -- what the catalog stores as `expired` and every listing
    filters on.

    Two triples, and both are needed. `resource_legal_in-force` is the flag
    EUR-Lex prints as "In force" / "No longer in force"; an act still in force
    can carry a past end-of-validity date (32006L0040 carries 2009-04-28 and is
    in force), so the date alone would repeal acts that are not repealed. An act
    can also carry several dates -- 31981L0576 carries 1996-08-05 and 2014-10-31
    -- and EUR-Lex prints the last one, so the latest wins. `9999-12-31` is
    CELLAR's placeholder for "no end date" and never counts as one.

    An act out of force with no end date at all -- CELLAR carries `false` and
    only the OPEN_ENDED placeholder for the three Brexit withdrawal-agreement
    documents -- yields None and stays listed. This column is a date the
    listings compare against today; there is no date to put in it, and inventing
    one would state a repeal the source does not. `refresh_metadata`'s caller
    counts them instead of letting them pass silently.

    A repealed act keeps its page and stays reachable through the reference
    graph: 32016R0679 article 94 repeals 31995L0046, and that citation still
    resolves. What the date removes is the *listing* -- browse, search, the
    API's document enumeration and the context rail."""
    in_force, end_of_validity = notice_validity(doc_dir)
    return end_of_validity if in_force in OUT_OF_FORCE else None


# What the notice records about maintenance relations, across every notice
# shape on disk: the n-triples subset this module synthesizes
# ('<...cdm#resource_legal_amends_resource_legal> <...celex/32002R0881>') and
# the bulk unpacker's prefixed turtle ('j.0:resource_legal_amends... <...>').
# The target is everything after 'celex/', NOT the last path segment: a treaty
# CELEX carries a document suffix of its own ('11992M/TXT', '12007L/TXTR(01)'),
# and 1 902 of the eurlex documents we hold are keyed that way. Taking the last
# segment reads 12007L/TXT as the CELEX "TXT" -- the trap facets._eu_celex
# already documents.
RE_NOTICE_RELATION = re.compile(
    r"resource_legal_(amends|implements)_resource_legal>?\s+"
    r"<[^>]*/celex/([^>]+)>")


def notice_relations(doc_dir):
    """``{"amends": [celex, ...], "implements": [...]}`` for the acts the
    stored notice says this one maintains -- each key absent when the notice
    records none.

    An act carrying either relation is not a base act: it changes or carries
    out another act rather than stating law of its own. That distinction is the
    difference between measuring how deep the law reaches and measuring how
    often a list is reissued -- the EU amendment ladders run to 71 references
    where the base acts alone run to 23.

    The CELEX is unquoted on the way out because `notice_ttl` percent-encodes
    it on the way in (as it does the subject). Without that, 11997D/TXTR(01)
    round-trips as `11997D%2FTXTR%2801%29` and the artifact gets a uri no
    document has. `unquote` is a no-op on a plain CELEX and on the unencoded
    form a dump notice carries, so one read serves every notice shape."""
    text = _notice_text(doc_dir)
    out = defaultdict(list)
    for key, target in RE_NOTICE_RELATION.findall(text or ""):
        celex = unquote(target)
        if celex not in out[key]:
            out[key].append(celex)
    return {k: sorted(v) for k, v in out.items()}


def content_filename(code, filetype, content):
    """The stored filename for a fetched item. CELLAR often returns a Formex
    manifestation not as a single .fmx4 but as a zip of several .fmx4 files (the
    act plus one per annex); flag that as `{lang}.fmx4.zip` so the parser and
    other consumers can tell without sniffing."""
    suffix = SUFFIX.get(filetype, ".pdf")
    if content.startswith(ZIP_MAGIC):
        suffix = suffix + ".zip"
    return code + suffix


def store_document(session, target, celex, wdate, selection, eurovoc,
                   validity=(None, None), relations=None):
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
        compress.write_download(target / "notice.ttl",
                                notice_ttl(celex, wdate, eurovoc, validity,
                                           relations))
    return stored
