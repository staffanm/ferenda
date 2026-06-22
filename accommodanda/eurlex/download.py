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

Per document we fetch the CDM "tree notice" (work -> language expressions ->
manifestations -> items) directly by CELEX -- no need to discover the opaque
cellar id first -- pick the best manifestation per language (fmx4 > xhtml >
html > pdf), and store:

  {root}/{year}/{celex}/notice.ttl       the tree-notice graph (metadata)
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

import os
import re
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from html import escape
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree as ET

from ..lib.net import make_session, request
from ..lib.util import progress

SPARQL = "https://publications.europa.eu/webapi/rdf/sparql"
CELLAR = "http://publications.europa.eu/resource/celex/%s"
SOAP_ENDPOINT = "https://eur-lex.europa.eu/EURLexWebService"
USER_AGENT = "lagen.nu harvester (https://lagen.nu/, staffan@tomtebo.org)"

LANGUAGES = ("swe", "eng")
# manifestation types we'll take, richest first; any pdf* sub-type (pdf,
# pdfa1a, pdfa2a, pdfx, ...) is the last resort. The stored file suffix:
TEXT_PREFERENCE = ("fmx4", "xhtml", "html")
SUFFIX = {"fmx4": ".fmx4", "xhtml": ".xhtml", "html": ".html"}
ZIP_MAGIC = b"PK\x03\x04"

CDM = "http://publications.europa.eu/ontology/cdm#"
OWL_SAMEAS = "http://www.w3.org/2002/07/owl#sameAs"
SEARCH_NS = "{http://eur-lex.europa.eu/search}"

# A CELLAR tree notice is huge (a court judgment's runs to 500k+ triples across
# 24 languages, citation closure and provenance) and we read ~6 edges out of it.
# rdflib parsing + turtle re-serialising those is the dominant per-document cost
# on case law. Instead we stream the rdf/xml through raptor's `rapper` (C,
# constant-memory) to n-triples, keep only the predicates the selection and a
# little metadata need, and store that subset -- itself valid turtle.
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
                       re.compile(r"1\d{4}[A-Z]{1,2}/TXT"), 1951),
    "acts": Sector("acts", "3", ("R", "L"),
                   re.compile(r"3\d{4}[RL]\d{4}(\(\d+\))?$"), 1952),
    "caselaw": Sector("caselaw", "6", CASELAW_TYPES,
                      re.compile(r"6\d{4}(?:%s)\d{4}$" % "|".join(CASELAW_TYPES)),
                      1954),
}


def write_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


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
    return request(session, "GET", SPARQL, parse_json=True, timeout=90,
                   params={"query": query,
                           "format": "application/sparql-results+json"}
                   )["results"]["bindings"]


def _enum_query(celex_prefix, since):
    """A DISTINCT (CELEX, work-date) listing for one sector-year-descriptor
    prefix; the date feeds the watermark. `since` (a date) restricts to
    documents whose work date is on/after it."""
    datefilter = (' FILTER(?d >= "%s"^^xsd:date)' % since.isoformat()
                  if since else "")
    return ("PREFIX cdm: <http://publications.europa.eu/ontology/cdm#> "
            "PREFIX xsd: <http://www.w3.org/2001/XMLSchema#> "
            "SELECT DISTINCT ?celex ?d WHERE { "
            "?w cdm:resource_legal_id_celex ?celex . "
            "OPTIONAL { ?w cdm:work_date_document ?d . } "
            'FILTER(STRSTARTS(STR(?celex), "%s"))%s } ORDER BY ?celex'
            % (celex_prefix, datefilter))


def enumerate_celex(session, sector, since=None):
    """Yield (year, [(CELEX, work_date), ...]) per year, oldest first. Each
    year's slice is fetched whole (one SPARQL query, or two for acts' R/L
    prefixes), so the caller knows the year's exact size up front. With `since`
    set, the walk starts at that year, never re-querying the decades below it."""
    start = max(sector.first_year, since.year) if since else sector.first_year
    for year in range(start, date.today().year + 1):
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
    envelope = SOAP_ENVELOPE % (user, password,
                                escape(expert_query, quote=False),
                                page, SOAP_PAGESIZE)
    response = request(session, "POST", SOAP_ENDPOINT, timeout=60,
                       data=envelope.encode(),
                       headers={"Content-Type": 'application/soap+xml; '
                                'charset=utf-8; action="https://eur-lex.'
                                'europa.eu/EURLexWebService/doQuery"'})
    return ET.fromstring(response.content)


def enumerate_celex_soap(session, sector, since=None):
    """Same contract as enumerate_celex, over the SOAP service (which exposes no
    per-hit work date, so it pairs each CELEX with None -- a soap run does not
    advance the watermark). Slices the DN (CELEX) wildcard query by year to stay
    under the per-search cap, starting at the `since` year."""
    start = max(sector.first_year, since.year) if since else sector.first_year
    for year in range(start, date.today().year + 1):
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
    """The kept triples of a tree notice, indexed for the few lookups selection
    needs -- a tiny stand-in for the rdflib Graph we used to materialise."""

    def __init__(self, triples):
        self.lines = [line for line, *_ in triples]
        self._spo = defaultdict(list)             # (subject, predicate) -> objects
        self._pos = defaultdict(list)             # (predicate, object) -> subjects
        self._by_pred = defaultdict(list)         # predicate -> [(subject, object)]
        for _line, s, p, o in triples:
            self._spo[(s, p)].append(o)
            self._pos[(p, o)].append(s)
            self._by_pred[p].append((s, o))

    def value(self, s, p):
        objs = self._spo.get((s, p))
        return objs[0] if objs else None

    def objects(self, s, p):
        return self._spo.get((s, p), ())

    def subjects(self, p, o):
        return self._pos.get((p, o), ())

    def subject_objects(self, p):
        return self._by_pred.get(p, ())

    def ttl(self):
        return ("\n".join(self.lines) + "\n").encode()


def parse_notice(rdfxml):
    """A Notice from rdf/xml bytes -- the download path fetches them from CELLAR,
    the bulk unpacker reads them out of a dump."""
    return Notice(_ntriples(rdfxml))


def fetch_notice(session, celex):
    """The CDM tree notice for a CELEX, filtered to the predicates we keep."""
    response = request(session, "GET", CELLAR % quote(celex, safe=""),
                       timeout=90,
                       headers={"Accept": "application/rdf+xml;notice=tree"})
    return parse_notice(response.content)


def _manifestation_items(notice, manifestation):
    """Items of a manifestation. The CELEX/OJ-form manifestation node usually
    carries no items directly -- they hang off the cellar-form manifestation it
    is owl:sameAs -- so resolve through sameAs in both directions."""
    nodes = ({manifestation} | set(notice.objects(manifestation, OWL_SAMEAS))
             | set(notice.subjects(OWL_SAMEAS, manifestation)))
    items = []
    for node in nodes:
        items += notice.subjects(P_ITEM_MANIF, node)
    return items


def _is_wrapper(notice, item):
    """A Formex manifestation carries both the real `.xml` and a `.doc.xml`
    wrapper item; the wrapper's stream URIs (owl:sameAs) all end in `.doc.xml`."""
    streams = list(notice.objects(item, OWL_SAMEAS))
    return bool(streams) and all(s.endswith((".doc.xml", ".doc")) for s in streams)


def _choose_item(notice, items):
    """The real content item -- drop the `.doc.xml` wrapper when present."""
    real = [i for i in items if not _is_wrapper(notice, i)]
    return (real or items)[0]


def _best_type(by_type):
    """The richest manifestation type present: fmx4 > xhtml > html > any pdf."""
    for filetype in TEXT_PREFERENCE:
        if filetype in by_type:
            return filetype
    pdfs = sorted(t for t in by_type if t.startswith("pdf"))
    return pdfs[0] if pdfs else None


def _manifestations(notice, expr):
    """Manifestations of an expression, from whichever direction the notice
    carries the edge: the inferred live notice has the forward edge; a notice
    built from a non-inferred dump may only have the inverse."""
    return list(dict.fromkeys(list(notice.objects(expr, P_EXPR_MANIF))
                              + list(notice.subjects(P_MANIF_EXPR, expr))))


def select_items(notice, languages):
    """For each requested language with an expression, the best available
    (lang, filetype, item_url), preferring fmx4 > xhtml > html > pdf."""
    expressions = {}
    for expr, _work in notice.subject_objects(P_EXPR_WORK):
        lang = notice.value(expr, P_EXPR_LANG)
        if lang is not None:
            expressions.setdefault(lang.rsplit("/", 1)[1].lower(), expr)

    out = []
    for code in languages:
        expr = expressions.get(code)
        if expr is None:
            continue
        by_type = {}
        for m in _manifestations(notice, expr):
            mtype = notice.value(m, P_MANIF_TYPE)
            items = _manifestation_items(notice, m)
            if mtype is not None and items:
                by_type[mtype] = _choose_item(notice, items)
        filetype = _best_type(by_type)
        if filetype:
            out.append((code, filetype, by_type[filetype]))
    return out


def content_filename(code, filetype, content):
    """The stored filename for a fetched item. CELLAR often returns a Formex
    manifestation not as a single .fmx4 but as a zip of several .fmx4 files (the
    act plus one per annex); flag that as `{lang}.fmx4.zip` so the parser and
    other consumers can tell without sniffing."""
    suffix = SUFFIX.get(filetype, ".pdf")
    if content.startswith(ZIP_MAGIC):
        suffix = suffix + ".zip"
    return code + suffix


def download_document(session, root, celex, languages, delay):
    """Fetch a CELEX's tree notice and its best content per language. Returns
    the languages stored (empty if none of the requested languages exist)."""
    target = doc_dir(root, celex)
    notice = fetch_notice(session, celex)
    write_atomic(target / "notice.ttl", notice.ttl())
    stored = []
    for code, filetype, url in select_items(notice, languages):
        response = request(session, "GET", url, timeout=180)
        name = content_filename(code, filetype, response.content)
        write_atomic(target / name, response.content)
        # a re-fetch may land a different manifestation type or zip-ness, so
        # clear any earlier content file for this language
        for old in target.glob(code + ".*"):
            if old.name != name:
                old.unlink()
        stored.append(code)
        time.sleep(delay)
    return stored


# --------------------------------------------------------------------------
# the harvest
# --------------------------------------------------------------------------

def is_downloaded(root, celex):
    return (doc_dir(root, celex) / "notice.ttl").exists()


def read_watermark(root, sector_name):
    """The max work date harvested for this sector in a previous clean run, or
    None (no prior run -> enumerate from the sector's first year)."""
    path = Path(root) / (".watermark-" + sector_name)
    return date.fromisoformat(path.read_text().strip()) if path.exists() else None


def write_watermark(root, sector_name, value):
    write_atomic(Path(root) / (".watermark-" + sector_name), str(value).encode())


def sync(root, sector_name, full=False, since=None, limit=None, delay=0.3,
         languages=LANGUAGES, source="sparql"):
    """Harvest a sector into root, returning (seen, stored, skipped).

    Incremental by default: re-fetches only CELEX not already on disk, and
    bounds discovery by a per-sector watermark (the max work date harvested in
    the last clean run) -- so an incremental run enumerates only from the
    watermark's year onward, never re-querying the decades below it. `--full`
    re-fetches every document and re-walks from the sector's first year; an
    explicit `--since` is a manual one-off window that overrides, but does not
    move, the watermark. A clean (un-truncated) run advances it.

    Edits to already-stored documents surface only under `--full`: discovery
    keys on work date, so a re-dated/corrected old document is not re-seen."""
    root = Path(root)
    sector = SECTORS[sector_name]
    session = make_session(USER_AGENT)
    enumerate_fn = enumerate_celex_soap if source == "soap" else enumerate_celex

    manual = since is not None        # explicit --since: don't move the watermark
    watermark = None if (full or manual) else read_watermark(root, sector_name)
    if since is None and not full:
        since = watermark             # incremental discovery floor

    seen = stored = skipped = 0
    high = watermark.isoformat() if watermark else None
    truncated = False
    last_t = time.perf_counter()

    def emit(year, y_seen, total, y_stored, y_skipped):
        nonlocal last_t
        now = time.perf_counter()
        progress(y_seen, total, scope="%s %d" % (sector_name, year),
                 stored=y_stored, skipped=y_skipped, elapsed=now - last_t,
                 stamp=True)
        last_t = now

    for year, items in enumerate_fn(session, sector, since):
        total = len(items)                       # the year-slice's exact size
        y_seen = y_stored = y_skipped = last_emit = 0
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
                if download_document(session, root, celex, languages, delay):
                    stored += 1
                    y_stored += 1
                else:
                    print("%s: no manifestation in %s"
                          % (celex, "/".join(languages)), flush=True)
                time.sleep(delay)       # politeness applies only to real fetches
                fetched = True
            if wdate and (high is None or wdate > high):
                high = wdate
            # each download is a slow network round-trip (~10s): show progress as
            # they happen (with the elapsed since the last line, so the per-fetch
            # cost is visible), plus a periodic tick through long stretches of skips
            if fetched or y_seen % 50 == 0:
                emit(year, y_seen, total, y_stored, y_skipped)
                last_emit = y_seen
        if y_seen != last_emit:     # final line for the year, if not just emitted
            emit(year, y_seen, total, y_stored, y_skipped)
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
        if not manual and year < date.today().year:
            write_watermark(root, sector_name, date(year + 1, 1, 1).isoformat())
    if not manual and not truncated and high:
        write_watermark(root, sector_name, high)   # precise floor for incrementals
    return seen, stored, skipped


def list_basefiles(root):
    """CELEX basefiles harvested into root, recovered from the path."""
    return sorted(p.parent.name.replace("_", "/")
                  for p in Path(root).glob("*/*/notice.ttl"))
