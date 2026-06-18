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

  python -m accommodanda.eurlex.download acts     [--full] [--since YYYY-MM-DD] [--limit N]
  python -m accommodanda.eurlex.download treaties
  python -m accommodanda.eurlex.download caselaw  [--lang swe,eng] [--source soap]
"""

import argparse
import os
import re
import time
from dataclasses import dataclass
from datetime import date
from html import escape
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree as ET

from rdflib import Graph, Namespace
from rdflib.namespace import OWL

from ..lib.net import make_session

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

CDM = Namespace("http://publications.europa.eu/ontology/cdm#")
SEARCH_NS = "{http://eur-lex.europa.eu/search}"


@dataclass(frozen=True)
class Sector:
    name: str
    digit: str                 # the CELEX sector digit
    prefixes: tuple            # CELEX descriptor prefixes to query per year
    celex_re: re.Pattern       # the accepted CELEX shape within the sector
    first_year: int

# acts query R and L separately (one prefix each) so each yearly slice is
# small; treaties and case law take the whole sector-year prefix and filter by
# shape. The treaty filter keeps only the consolidated treaty texts (.../TXT),
# not the ~9800 other sector-1 documents.
SECTORS = {
    "treaties": Sector("treaties", "1", ("",),
                       re.compile(r"1\d{4}[A-Z]{1,2}/TXT"), 1951),
    "acts": Sector("acts", "3", ("R", "L"),
                   re.compile(r"3\d{4}[RL]\d{4}(\(\d+\))?$"), 1952),
    "caselaw": Sector("caselaw", "6", ("",),
                      re.compile(r"6\d{4}[A-Z]{2}\d{4}$"), 1954),
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
    response = session.get(SPARQL, timeout=90,
                           params={"query": query,
                                   "format": "application/sparql-results+json"})
    response.raise_for_status()
    return response.json()["results"]["bindings"]


def _enum_query(celex_prefix, since):
    """A DISTINCT CELEX listing for one sector-year-descriptor prefix; `since`
    (a date) restricts to documents whose work date is on/after it."""
    datejoin = "?w cdm:work_date_document ?d ." if since else ""
    datefilter = (' FILTER(?d >= "%s"^^xsd:date)' % since.isoformat()
                  if since else "")
    return ("PREFIX cdm: <http://publications.europa.eu/ontology/cdm#> "
            "PREFIX xsd: <http://www.w3.org/2001/XMLSchema#> "
            "SELECT DISTINCT ?celex WHERE { "
            "?w cdm:resource_legal_id_celex ?celex . %s "
            'FILTER(STRSTARTS(STR(?celex), "%s"))%s } ORDER BY ?celex'
            % (datejoin, celex_prefix, datefilter))


def enumerate_celex(session, sector, since=None):
    """Yield the sector's CELEX numbers, oldest year first, one yearly SPARQL
    slice at a time (each slice is far under the endpoint's practical limits)."""
    for year in range(sector.first_year, date.today().year + 1):
        for prefix in sector.prefixes:
            rows = sparql_select(session, _enum_query(
                "%s%d%s" % (sector.digit, year, prefix), since))
            for row in rows:
                celex = row["celex"]["value"]
                if sector.celex_re.match(celex):
                    yield celex


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
    response = session.post(SOAP_ENDPOINT, timeout=60, data=envelope.encode(),
                            headers={"Content-Type": 'application/soap+xml; '
                                     'charset=utf-8; action="https://eur-lex.'
                                     'europa.eu/EURLexWebService/doQuery"'})
    response.raise_for_status()
    return ET.fromstring(response.content)


def enumerate_celex_soap(session, sector, since=None):
    """Same contract as enumerate_celex, over the SOAP service. Slices the DN
    (CELEX) wildcard query by year to stay under the per-search cap."""
    for year in range(sector.first_year, date.today().year + 1):
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
                    if celex and sector.celex_re.match(celex):
                        yield celex
                if len(hits) < SOAP_PAGESIZE:
                    break
                page += 1


# --------------------------------------------------------------------------
# content -- tree notice -> best manifestation per language -> item
# --------------------------------------------------------------------------

def fetch_notice(session, celex):
    """The CDM tree notice for a CELEX as an rdflib Graph (the work, its
    language expressions, their manifestations and downloadable items)."""
    response = session.get(CELLAR % quote(celex, safe=""), timeout=90,
                           headers={"Accept": "application/rdf+xml;notice=tree"})
    response.raise_for_status()
    return Graph().parse(data=response.content, format="xml")


def _manifestation_items(graph, manifestation):
    """Items of a manifestation. The CELEX/OJ-form manifestation node usually
    carries no items directly -- they hang off the cellar-form manifestation it
    is owl:sameAs -- so resolve through sameAs in both directions."""
    nodes = ({manifestation} | set(graph.objects(manifestation, OWL.sameAs))
             | set(graph.subjects(OWL.sameAs, manifestation)))
    items = []
    for node in nodes:
        items += graph.subjects(CDM.item_belongs_to_manifestation, node)
    return items


def _is_wrapper(graph, item):
    """A Formex manifestation carries both the real `.xml` and a `.doc.xml`
    wrapper item; the wrapper's stream URIs (owl:sameAs) all end in `.doc.xml`."""
    streams = [str(o) for o in graph.objects(item, OWL.sameAs)]
    return bool(streams) and all(s.endswith((".doc.xml", ".doc")) for s in streams)


def _choose_item(graph, items):
    """The real content item -- drop the `.doc.xml` wrapper when present."""
    real = [i for i in items if not _is_wrapper(graph, i)]
    return (real or items)[0]


def _best_type(by_type):
    """The richest manifestation type present: fmx4 > xhtml > html > any pdf."""
    for filetype in TEXT_PREFERENCE:
        if filetype in by_type:
            return filetype
    pdfs = sorted(t for t in by_type if t.startswith("pdf"))
    return pdfs[0] if pdfs else None


def select_items(graph, languages):
    """For each requested language with an expression, the best available
    (lang, filetype, item_url), preferring fmx4 > xhtml > html > pdf."""
    expressions = {}
    for expr, _work in graph.subject_objects(CDM.expression_belongs_to_work):
        lang = graph.value(expr, CDM.expression_uses_language)
        if lang is not None:
            expressions.setdefault(str(lang).rsplit("/", 1)[1].lower(), expr)

    out = []
    for code in languages:
        expr = expressions.get(code)
        if expr is None:
            continue
        by_type = {}
        for m in graph.objects(expr, CDM.expression_manifested_by_manifestation):
            mtype = graph.value(m, CDM.manifestation_type)
            items = _manifestation_items(graph, m)
            if mtype is not None and items:
                by_type[str(mtype)] = _choose_item(graph, items)
        filetype = _best_type(by_type)
        if filetype:
            out.append((code, filetype, str(by_type[filetype])))
    return out


def content_filename(code, filetype, content):
    """The stored filename for a fetched item. CELLAR often returns a Formex
    manifestation not as a single .fmx4 but as a zip of several .fmx4 files (the
    act plus one per annex); flag that as `{lang}.zip.fmx4` so the parser and
    other consumers can tell without sniffing."""
    suffix = SUFFIX.get(filetype, ".pdf")
    if content.startswith(ZIP_MAGIC):
        suffix = ".zip" + suffix
    return code + suffix


def download_document(session, root, celex, languages, delay):
    """Fetch a CELEX's tree notice and its best content per language. Returns
    the languages stored (empty if none of the requested languages exist)."""
    target = doc_dir(root, celex)
    graph = fetch_notice(session, celex)
    write_atomic(target / "notice.ttl", graph.serialize(format="turtle").encode())
    stored = []
    for code, filetype, url in select_items(graph, languages):
        response = session.get(url, timeout=180)
        response.raise_for_status()
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


def sync(root, sector_name, full=False, since=None, limit=None, delay=0.3,
         languages=LANGUAGES, source="sparql"):
    """Harvest a sector into root. Incremental by default: re-fetches only
    CELEX not already on disk (`--full` re-fetches everything, `--since` narrows
    discovery to recently dated documents). Returns (seen, stored, skipped)."""
    root = Path(root)
    sector = SECTORS[sector_name]
    session = make_session(USER_AGENT)
    enumerate_fn = enumerate_celex_soap if source == "soap" else enumerate_celex
    seen = stored = skipped = 0
    for celex in enumerate_fn(session, sector, since):
        if limit and seen >= limit:
            break
        seen += 1
        if not full and is_downloaded(root, celex):
            skipped += 1
            continue
        langs = download_document(session, root, celex, languages, delay)
        if langs:
            stored += 1
        else:
            print("%s: no manifestation in %s" % (celex, "/".join(languages)),
                  flush=True)
        if seen % 50 == 0:
            print("%s: %d seen, %d stored, %d skipped"
                  % (sector_name, seen, stored, skipped), flush=True)
        time.sleep(delay)
    return seen, stored, skipped


def list_basefiles(root):
    """CELEX basefiles harvested into root, recovered from the path."""
    return sorted(p.parent.name.replace("_", "/")
                  for p in Path(root).glob("*/*/notice.ttl"))


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("sector", choices=sorted(SECTORS))
    parser.add_argument("root", nargs="?", default="site/data/eurlex",
                        help="target directory (default site/data/eurlex)")
    parser.add_argument("--full", action="store_true",
                        help="re-fetch every document, not just new ones")
    parser.add_argument("--since", type=date.fromisoformat, metavar="YYYY-MM-DD",
                        help="only discover documents dated on/after this")
    parser.add_argument("--limit", type=int, help="stop after N documents")
    parser.add_argument("--lang", default=",".join(LANGUAGES),
                        help="comma-separated languages (default swe,eng)")
    parser.add_argument("--source", choices=("sparql", "soap"), default="sparql",
                        help="discovery backend (default sparql)")
    parser.add_argument("--delay", type=float, default=0.3,
                        help="seconds between requests (default 0.3)")
    args = parser.parse_args()
    seen, stored, skipped = sync(
        args.root, args.sector, full=args.full, since=args.since,
        limit=args.limit, delay=args.delay,
        languages=tuple(args.lang.split(",")), source=args.source)
    print("%s: %d seen, %d stored, %d skipped"
          % (args.sector, seen, stored, skipped))


if __name__ == "__main__":
    main()
