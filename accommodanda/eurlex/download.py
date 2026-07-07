"""Harvester for EU legal sources from the Publications Office CELLAR
repository, keyed by CELEX number.

Three sectors (the leading CELEX digit), the interesting starting set:

  1  basic treaties      -- the consolidated treaty texts (CELEX .../TXT)
  3  secondary law       -- regulations (R) and directives (L)
  6  Court of Justice     -- judgments, orders and AG opinions (case law)

Why CELLAR, and why SPARQL for discovery: every other route is partial. The
bulk data dumps cover only sector 3 in force; the EU Open Data portal only OJ
from 2004. CELLAR is the one complete repository of what we want, and Formex
(structured XML) is its richest manifestation. The hard part is *discovery* --
which CELEX numbers exist -- so we enumerate that from the auth-free CELLAR
SPARQL endpoint (no 10,000-result cap, unlike the SOAP service) and fetch each
document's content from CELLAR by CELEX.

Per document we need the best manifestation per language (fmx4 > xhtml > html >
pdf) and its content item URL. The CDM "tree notice" carries that, but CELLAR
spends ~10s assembling one (a judgment's runs to 500k+ triples across 24
languages and the citation closure) for the ~6 edges we use -- the dominant cost
of the whole harvest. So instead we read the same work -> expression ->
manifestation -> item edges straight from the SPARQL endpoint, one batched query
per year-slice of CELEX rather than one notice per document, and store:

  {root}/{year}/{celex}/notice.ttl       the metadata we keep (celex, sector,
                                         work date, eurovoc), synthesized
  {root}/{year}/{celex}/{lang}.{ext}     content per language (e.g. swe.fmx4)

CELEX is the basefile throughout (treaty CELEX contain '/', stored with '/'
mapped to '_' in the path -- the only substitution, so it is reversible).
Languages default to swe + eng.

A registered EUR-Lex SOAP account enables a secondary enumerator over the
expert search service (--source soap) -- a cross-check/fallback for the
unmetered but SLA-less SPARQL endpoint. It reads credentials from the
environment (EURLEX_USERNAME / EURLEX_PASSWORD); they are never stored on disk.

Harvested via `lagen eurlex download [treaties|acts|caselaw]
[--since YYYY-MM-DD] [--lang swe,eng] [--source sparql|soap]`; no sector = all
three. CELEX-specific refetch is `lagen eurlex download <CELEX>`.
"""

import json
import os
import re
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from html import escape
from pathlib import Path
from urllib.parse import quote

from lxml import etree  # ty: ignore[unresolved-import]  # lxml ships no stubs

from ..lib.net import HARVESTER_UA as USER_AGENT
from ..lib.net import make_session, request
from ..lib.util import Reporter, write_atomic

SPARQL = "https://publications.europa.eu/webapi/rdf/sparql"
CELLAR = "http://publications.europa.eu/resource/celex/%s"
SOAP_ENDPOINT = "https://eur-lex.europa.eu/EURLexWebService"

LANGUAGES = ("swe", "eng")
# manifestation types we'll take, richest first; any pdf* sub-type (pdf,
# pdfa1a, pdfa2a, pdfx, ...) is the last resort. The stored file suffix:
TEXT_PREFERENCE = ("fmx4", "xhtml", "html")
SUFFIX = {"fmx4": ".fmx4", "xhtml": ".xhtml", "html": ".html"}
ZIP_MAGIC = b"PK\x03\x04"

CDM = "http://publications.europa.eu/ontology/cdm#"
OWL_SAMEAS = "http://www.w3.org/2002/07/owl#sameAs"
SEARCH_NS = "{http://eur-lex.europa.eu/search}"

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


@dataclass(frozen=True)
class Sector:
    name: str
    digit: str                 # the CELEX sector digit
    prefixes: tuple            # CELEX descriptor prefixes to query per year
    celex_re: re.Pattern       # the accepted CELEX shape within the sector
    first_year: int
    # Does the CELEX year track the work date? For legislation and treaties the
    # CELEX year is the adoption/consolidation year, which equals the work date
    # year, so with a date floor the walk may start at the floor's year. For
    # caselaw it does NOT: the CELEX year is the CASE year while the work date is
    # the DECISION date, which can fall years later -- so caselaw must walk every
    # year regardless of the floor (see enum_years).
    wdate_follows_celex_year: bool

# The CELEX descriptor (the 2-letter code after the year) names the court and
# document kind: first letter C/T/F = Court of Justice / General Court / Civil
# Service Tribunal; second letter J = judgment, C = Advocate-General opinion.
# We want the rulings and opinions, not the OJ C-series notices that dominate
# sector 6 by volume (N = notice a case was lodged, A = summary of the ruling,
# B = summary of an order -- all redundant pointers to the J/O documents) nor
# the procedural orders (O). For 2008 that is 914 of 3220 documents.
CASELAW_TYPES = ("CJ", "CC", "TJ", "TC", "FJ")

# acts query R and L separately, case law per wanted descriptor (one prefix
# each) so each yearly slice is small and the unwanted bulk is never fetched;
# treaties take the whole sector-year prefix and filter by shape (keeping only
# the consolidated treaty texts .../TXT, not the ~9800 other sector-1 docs).
SECTORS = {
    "treaties": Sector("treaties", "1", ("",),
                       re.compile(r"1\d{4}[A-Z]{1,2}/TXT"), 1951, True),
    "acts": Sector("acts", "3", ("R", "L"),
                   re.compile(r"3\d{4}[RL]\d{4}(\(\d+\))?$"), 1952, True),
    "caselaw": Sector("caselaw", "6", CASELAW_TYPES,
                      re.compile(r"6\d{4}(?:%s)\d{4}$" % "|".join(CASELAW_TYPES)),
                      1954, False),
}


def celex_slug(celex):
    """Filesystem form of a CELEX. Only '/' (treaty texts) is substituted, so
    the basefile is recoverable from the path."""
    return celex.replace("/", "_")


def doc_dir(root, celex):
    return Path(root) / celex[1:5] / celex_slug(celex)


# --------------------------------------------------------------------------
# discovery -- CELEX enumeration via the CELLAR SPARQL endpoint
# --------------------------------------------------------------------------

def sparql_select(session, query):
    # POST: the selection/metadata queries pass the year's CELEX in a VALUES
    # block, far past what a GET URL holds (the endpoint accepts either).
    return request(session, "POST", SPARQL, parse_json=True, timeout=120,
                   data={"query": query,
                         "format": "application/sparql-results+json"}
                   )["results"]["bindings"]


def _enum_query(celex_prefix, since):
    """A DISTINCT (CELEX, work-date) listing for one sector-year-descriptor
    prefix; the date feeds the watermark. `since` (a date) restricts to
    documents whose work date is on/after it -- but a document with no
    work_date_document (a modelled state: enumerate_celex stores None,
    notice_ttl handles it) must survive the filter, hence the !BOUND clause. A
    plain `?d >= ...` evaluates error->false for an unbound ?d and would drop
    every wdate-less work from every incremental run."""
    datefilter = (' FILTER(!BOUND(?d) || ?d >= "%s"^^xsd:date)' % since.isoformat()
                  if since else "")
    return ("PREFIX cdm: <http://publications.europa.eu/ontology/cdm#> "
            "PREFIX xsd: <http://www.w3.org/2001/XMLSchema#> "
            "SELECT DISTINCT ?celex ?d WHERE { "
            "?w cdm:resource_legal_id_celex ?celex . "
            "OPTIONAL { ?w cdm:work_date_document ?d . } "
            'FILTER(STRSTARTS(STR(?celex), "%s"))%s } ORDER BY ?celex'
            % (celex_prefix, datefilter))


def enum_years(sector, since):
    """The CELEX years to walk for `sector` given a work-date floor `since`.

    The enumeration start is decoupled from the date floor because a sector's
    CELEX year does not always track its work date. Legislation (sector 3) and
    treaties (sector 1) carry `wdate_follows_celex_year`: the CELEX year is the
    adoption/consolidation year, equal to the work date year, so with a floor
    the walk may start at `since.year` and never re-query the decades below it.

    Caselaw (sector 6) does not: a judgment's CELEX year is the CASE year while
    work_date_document is the DECISION date, which can fall years later (a case
    filed 2020, decided 2025 -- the same lag the resume watermark documents).
    Starting at `since.year` there would make every `62020CJ...` slice
    permanently invisible to a 2025 floor, so caselaw walks from first_year every
    run and lets the per-slice wdate FILTER prune the years that hold nothing new
    (an empty year-slice is one cheap query)."""
    start = (max(sector.first_year, since.year)
             if since and sector.wdate_follows_celex_year else sector.first_year)
    return range(start, date.today().year + 1)


def enumerate_celex(session, sector, since=None):
    """Yield (year, [(CELEX, work_date), ...]) per year, oldest first. Each
    year's slice is fetched whole (one SPARQL query, or two for acts' R/L
    prefixes), so the caller knows the year's exact size up front. With `since`
    set, the walk is bounded by enum_years (which years) and the per-slice wdate
    FILTER (which documents within a year)."""
    for year in enum_years(sector, since):
        print("  querying %s %d ..." % (sector.name, year),
              file=sys.stderr, flush=True)
        items, seen = [], set()
        for prefix in sector.prefixes:
            rows = sparql_select(session, _enum_query(
                "%s%d%s" % (sector.digit, year, prefix), since))
            for row in rows:
                celex = row["celex"]["value"]
                if celex in seen or not sector.celex_re.match(celex):
                    continue
                seen.add(celex)
                wdate = row.get("d", {}).get("value")
                items.append((celex, wdate[:10] if wdate else None))
        if items:
            yield year, sorted(items)


# --------------------------------------------------------------------------
# discovery -- secondary enumerator over the SOAP expert search service
# --------------------------------------------------------------------------

SOAP_ENVELOPE = """<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope">
 <soap:Header><wsse:Security xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd">
  <wsse:UsernameToken><wsse:Username>%s</wsse:Username>
  <wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordText">%s</wsse:Password>
  </wsse:UsernameToken></wsse:Security></soap:Header>
 <soap:Body><sear:searchRequest xmlns:sear="http://eur-lex.europa.eu/search">
  <sear:expertQuery>%s</sear:expertQuery><sear:page>%d</sear:page>
  <sear:pageSize>%d</sear:pageSize><sear:searchLanguage>en</sear:searchLanguage>
 </sear:searchRequest></soap:Body></soap:Envelope>"""

SOAP_PAGESIZE = 100


def soap_search(session, expert_query, page):
    """One page of the EUR-Lex expert search. Credentials come from the
    environment; the service caps a single search at 10,000 results, so callers
    slice the query (e.g. by year) to stay under it."""
    user, password = os.environ["EURLEX_USERNAME"], os.environ["EURLEX_PASSWORD"]
    envelope = SOAP_ENVELOPE % (escape(user), escape(password),
                                escape(expert_query, quote=False),
                                page, SOAP_PAGESIZE)
    response = request(session, "POST", SOAP_ENDPOINT, timeout=60,
                       data=envelope.encode(),
                       headers={"Content-Type": 'application/soap+xml; '
                                'charset=utf-8; action="https://eur-lex.'
                                'europa.eu/EURLexWebService/doQuery"'})
    # remote XML: no DTD/entity expansion (stdlib ElementTree would expand
    # nested entities unbounded)
    return etree.fromstring(response.content, etree.XMLParser(
        resolve_entities=False, load_dtd=False, no_network=True,
        remove_comments=True, remove_pis=True))


def enumerate_celex_soap(session, sector, since=None):
    """Same contract as enumerate_celex, over the SOAP service (which exposes no
    per-hit work date, so it pairs each CELEX with None -- a soap run does not
    advance the watermark). Slices the DN (CELEX) wildcard query by year to stay
    under the per-search cap, walking the years enum_years selects."""
    for year in enum_years(sector, since):
        items, seen = [], set()
        for prefix in sector.prefixes:
            query = "DN = %s%d%s*" % (sector.digit, year, prefix)
            if since:
                query += since.strftime(" AND DD >= %d/%m/%Y")
            page = 1
            while True:
                tree = soap_search(session, query, page)
                hits = tree.findall(".//%sresult" % SEARCH_NS)
                for result in hits:
                    node = result.find(".//%sID_CELEX" % SEARCH_NS)
                    celex = node[0].text if node is not None and len(node) else None
                    if celex and celex not in seen and sector.celex_re.match(celex):
                        seen.add(celex)
                        items.append((celex, None))
                if len(hits) < SOAP_PAGESIZE:
                    break
                page += 1
        if items:
            yield year, sorted(items)


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


def fetch_selection(session, celexes, languages):
    """For each CELEX, the ranked content candidates per requested language: a
    list `(code, [(filetype, item_url), ...])` ordered fmx4 > xhtml > html > pdf,
    with the .doc.xml wrapper item dropped. store_document fetches down each
    language's list until one item's bytes match its format -- the bulk
    replacement for per-document tree-notice selection."""
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
                # all items wrappers (a wrapper-only Formex work): skip the
                # type so the document degrades to the next one -- shipping
                # the .doc.xml manifest as content is worse than falling back
                # to html/pdf (bulk.py's _select_content degrades the same way)
                if real:
                    candidates.append((filetype, real[0]))
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
    language. `selection` is the [(lang, [(filetype, item_url), ...])] candidate
    list fetch_selection returns for this CELEX. Returns the languages stored.
    Throttling is the caller's (one delay per document, not per content item).

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
        for filetype, url in candidates:
            response = request(session, "GET", url, timeout=180)
            if not _content_ok(filetype, response.content):
                continue                # placeholder for this type: try the next
            name = content_filename(code, filetype, response.content)
            write_atomic(target / name, response.content)
            # a re-fetch may land a different manifestation type or zip-ness, so
            # clear any earlier content file for this language
            for old in target.glob(code + ".*"):
                if old.name != name:
                    old.unlink()
            stored.append(code)
            break
    if stored:
        write_atomic(target / "notice.ttl", notice_ttl(celex, wdate, eurovoc))
    return stored


def download_document(session, root, celex, languages, delay):
    """Fetch a single CELEX's content, selecting over SPARQL. Returns the
    languages stored (empty if none of the requested languages exist). The sweep
    (`sync`) selects in bulk; this serves the explicit per-CELEX refetch."""
    selection = fetch_selection(session, [celex], languages)
    wdate, eurovoc = fetch_metadata(session, [celex])
    stored = store_document(session, doc_dir(root, celex), celex,
                            wdate.get(celex), selection.get(celex, []),
                            eurovoc.get(celex, []))
    time.sleep(delay)
    return stored


# --------------------------------------------------------------------------
# the harvest
# --------------------------------------------------------------------------

def is_downloaded(root, celex):
    return (doc_dir(root, celex) / "notice.ttl").exists()


def prune_empty(root, remove=True):
    """Count (and, unless `remove` is False, delete) harvest dirs that hold only
    a notice.ttl and no Swedish/English content -- metadata-only works (a
    pre-accession act never translated) that earlier runs left behind before
    store_document learned to skip them. The harvest dir is rebuildable, so this
    is safe to re-run. Returns the number of such dirs."""
    root = Path(root)
    n = 0
    for notice in root.glob("*/*/notice.ttl"):
        d = notice.parent
        if all(p.name == "notice.ttl" for p in d.iterdir()):
            if remove:
                notice.unlink()
                d.rmdir()
            n += 1
    return n


# CELLAR indexes a document within months of its work date, so a work date
# older than the last completed run minus this lag can no longer gain new
# documents. That lets the incremental window advance with *run* recency
# instead of staying pinned to a quiet sector's last document (treaties: none
# published since 2022, which used to mean re-querying 2022..today every run).
RECENCY_WINDOW = timedelta(days=183)


def read_watermark(root, sector_name):
    """The sector's discovery watermark from the last clean run, as a
    (high, run) pair: `high` the max work date downloaded, `run` the date that
    run happened. `high` is NOT a clean "everything below was seen" boundary:
    CELLAR indexes documents out of work-date order by up to RECENCY_WINDOW, so
    a work dated below `high` can still surface after the run that set `high` --
    which is why the incremental floor reaches below `high` (see
    incremental_floor), not up to it. `run` is None on a legacy plain-date file
    or after an interrupted walk's resume write (recency must not be claimed by
    a walk that did not finish); (None, None) with no prior run at all ->
    enumerate from the sector's first year."""
    path = Path(root) / (".watermark-" + sector_name)
    if not path.exists():
        return None, None
    text = path.read_text().strip()
    if text.startswith("{"):
        data = json.loads(text)
        return (date.fromisoformat(data["high"]),
                date.fromisoformat(data["run"]) if "run" in data else None)
    return date.fromisoformat(text), None      # legacy plain-date format


def write_watermark(root, sector_name, high, run=None):
    payload = {"high": str(high)}
    if run:
        payload["run"] = run.isoformat()
    write_atomic(Path(root) / (".watermark-" + sector_name),
                 json.dumps(payload).encode())


def incremental_floor(high, run, window=RECENCY_WINDOW):
    """The discovery floor (a work-date `since`) for an incremental run.

    The floor is `run - window`, NOT `high` and not `max(high, run - window)`.
    CELLAR indexes a document within `window` of its work date, so the last
    clean run (which saw everything indexed by its date `run`) is guaranteed to
    have seen only works whose work date is <= `run - window`; anything dated
    after that might still be un-indexed, or indexed later out of order, at the
    time that run finished. So the floor must reach down to `run - window`:

    - active sector (`high` recent, ~`run`): this reaches BELOW `high` by the
      lag allowance, catching a work dated under `high` but indexed later --
      which `max(high, run - window)` pinned at `high` and lost forever.
    - quiet sector (`high` old, e.g. treaties last published 2022): the floor
      still advances with run recency to `run - window` instead of pinning to
      the sector's last document, so a steadily-running quiet sector stops
      re-querying the years since it went quiet.
    - dormant harvester (an old `run`): the floor sits at that old run's
      `run - window`, so the years published while the harvester slept are
      re-walked, not skipped.

    `high` only decides the degenerate cases: no prior high -> None (enumerate
    from first_year); a legacy watermark with the run date unknown -> `high`,
    the one date we have."""
    if high is None:
        return None
    if run is None:
        return high            # legacy watermark: only the document date known
    return run - window


def read_pending(root, sector_name):
    """The no-content CELEX recorded by earlier runs, awaiting retry (see
    write_pending / store_document). A JSON list of CELEX strings; [] if none."""
    path = Path(root) / (".pending-" + sector_name)
    if not path.exists():
        return []
    return json.loads(path.read_text())


def write_pending(root, sector_name, celexes):
    write_atomic(Path(root) / (".pending-" + sector_name),
                 json.dumps(sorted(celexes)).encode())


def worth_retrying(wdate, today=None, window=RECENCY_WINDOW):
    """Whether a CELEX that stored no content belongs on the retry sidecar.

    Only recent no-content works: a just-published act still awaiting its
    Swedish/English translation, or a scanned old judgment (a TIFF placeholder)
    still awaiting its real Formex, can plausibly gain content -- and its work
    date is recent (within `window` of now, since content lands within the
    indexing lag). An old contentless work is a permanent never-translated act;
    retrying it every run is pure waste and would bloat the sidecar during a
    --full or first (unwatermarked) walk over the pre-accession decades. A
    wdate-less work is kept: we cannot date it, and dropping it would lose it."""
    if wdate is None:
        return True
    return wdate >= ((today or date.today()) - window).isoformat()


def sync(root, sector_name, full=False, since=None, limit=None, delay=0.3,
         languages=LANGUAGES, source="sparql"):
    """Download a sector into root, returning (seen, stored, skipped).

    Incremental by default: re-fetches only CELEX not already on disk, and
    bounds discovery by a per-sector watermark -- the max work date downloaded
    in the last clean run, with the floor reaching a lag allowance BELOW it
    (`incremental_floor`) so a work indexed out of order is still caught, while
    a quiet sector stops re-querying the years since its last document. `--full`
    re-fetches every document and re-walks from the sector's first year; an
    explicit `--since` is a manual one-off window that overrides, but does not
    move, the watermark. A clean (un-truncated) run advances it -- except
    `--source soap`, which never writes the watermark: it carries no per-hit
    work date (enumerate_celex_soap pairs every CELEX with None, so `high`
    could never advance anyway) and, unlike SPARQL, cannot be trusted to have
    seen everything up to today (the expert search service silently caps a
    single query at 10,000 hits with no signal we truncated), so it must not
    even advance the resume-safety `run` date either.

    Recent works that stored no content (an untranslated act, a scanned-TIFF
    judgment) are recorded on a per-sector retry sidecar (read_pending /
    write_pending) and re-attempted at the start of every incremental run, since
    the floor would otherwise bury them once their date ages past the window.

    Edits to already-stored documents surface only under `--full`: discovery
    keys on work date, so a re-dated/corrected old document is not re-seen."""
    root = Path(root)
    sector = SECTORS[sector_name]
    session = make_session(USER_AGENT)
    enumerate_fn = enumerate_celex_soap if source == "soap" else enumerate_celex

    manual = since is not None        # explicit --since: don't move the watermark
    wm_high, wm_run = ((None, None) if (full or manual)
                       else read_watermark(root, sector_name))
    if since is None and not full:
        since = incremental_floor(wm_high, wm_run)   # incremental discovery floor

    seen = stored = skipped = 0
    high = wm_high.isoformat() if wm_high else None
    truncated = False
    rep = Reporter()

    # Retry the no-content works earlier runs recorded before walking the years:
    # the walk's floor no longer enumerates the older ones, so this is their only
    # second chance (a TIFF that gained a real Formex, an act since translated).
    retry = set() if full else set(read_pending(root, sector_name))
    for celex in sorted(retry):
        if is_downloaded(root, celex):
            retry.discard(celex)                 # gained content some other way
            continue
        sel = fetch_selection(session, [celex], languages)
        meta_wdate, meta_eurovoc = fetch_metadata(session, [celex])
        if store_document(session, doc_dir(root, celex), celex,
                          meta_wdate.get(celex), sel.get(celex, []),
                          meta_eurovoc.get(celex, [])):
            stored += 1
            retry.discard(celex)
        elif not worth_retrying(meta_wdate.get(celex)):
            retry.discard(celex)                 # aged out, still empty: give up
        time.sleep(delay)

    for year, items in enumerate_fn(session, sector, since):
        scope = "%s %d" % (sector_name, year)
        total = len(items)                       # the year-slice's exact size
        # one batched selection (+ metadata) query for the whole year's pending
        # CELEX, replacing a ~10s tree notice per document; a fully-downloaded
        # year (incremental steady state) queries nothing.
        pending = [celex for celex, _ in items
                   if full or not is_downloaded(root, celex)]
        selection, eurovoc = {}, {}
        if pending:
            selection = fetch_selection(session, pending, languages)
            _meta_wdate, eurovoc = fetch_metadata(session, pending)
        rep.reset()                     # don't bill the year's queries to doc 1
        y_seen = y_stored = y_skipped = 0
        for celex, wdate in items:
            if limit and seen >= limit:
                truncated = True
                break
            seen += 1
            y_seen += 1
            if not full and is_downloaded(root, celex):
                skipped += 1
                y_skipped += 1          # already on disk: no network, no delay
                fetched = False
            else:
                if store_document(session, doc_dir(root, celex), celex, wdate,
                                  selection.get(celex, []),
                                  eurovoc.get(celex, [])):
                    stored += 1
                    y_stored += 1
                    retry.discard(celex)
                else:
                    print("%s: no manifestation in %s"
                          % (celex, "/".join(languages)), flush=True)
                    if worth_retrying(wdate):
                        retry.add(celex)   # a recent work may gain content later
                time.sleep(delay)       # politeness applies only to real fetches
                fetched = True
            if wdate and (high is None or wdate > high):
                high = wdate
            # each download is a slow network round-trip (~10s): show progress as
            # they happen (with the elapsed since the last line, so the per-fetch
            # cost is visible), plus a periodic tick through long stretches of skips
            if fetched or y_seen % 50 == 0:
                rep.update(y_seen, total, scope=scope,
                           stored=y_stored, skipped=y_skipped)
        rep.update(y_seen, total, scope=scope, stored=y_stored, skipped=y_skipped)
        rep.done()                  # finish the year's overwriting line
        if truncated:
            break
        # Resume safety net: persist progress after each completed past year, so
        # an interrupted run resumes from there instead of re-enumerating every
        # year from the sector's first (the per-year SPARQL query is the real
        # cost, not the on-disk skips). We store the *next* year's start, not the
        # max work date: a caselaw work date can fall years after its CELEX year
        # (a case filed in 2000, decided 2005), so a max-date floor would skip
        # the years between on resume; a work date is always >= its CELEX year,
        # so a year-start floor never hides a document.
        if not manual and source == "sparql" and year < date.today().year:
            write_watermark(root, sector_name, date(year + 1, 1, 1).isoformat())
    if not manual and not truncated and high and source == "sparql":
        # precise floor for incrementals, plus the run date whose recency lets
        # the next run's floor advance past a quiet sector's last document
        write_watermark(root, sector_name, high, run=date.today())
    if not full:
        # persist the retry sidecar: successes and aged-out entries were dropped
        # above, recent no-content works added; worth_retrying bounds it by work
        # date, so even a --since sweep over old years cannot bloat it.
        write_pending(root, sector_name, retry)
    return seen, stored, skipped


def list_basefiles(root):
    """CELEX basefiles harvested into root, recovered from the path."""
    return sorted(p.parent.name.replace("_", "/")
                  for p in Path(root).glob("*/*/notice.ttl"))
