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
import sys
import time
from dataclasses import dataclass
from datetime import date, timedelta
from html import escape
from pathlib import Path

from lxml import etree  # ty: ignore[unresolved-import]  # lxml ships no stubs

from ..lib import compress
from ..lib.cellar import (
    LANGUAGES,
    SELECT_CHUNK,
    fetch_metadata,
    fetch_relations,
    fetch_repeals,
    fetch_selection,
    notice_relations,
    notice_repeal_date,
    notice_ttl,
    notice_validity,
    notice_work_date,
    sparql_select,
    store_document,
)
from ..lib.net import HARVESTER_UA as USER_AGENT
from ..lib.net import make_session, request
from ..lib.util import Reporter, write_atomic

SOAP_ENDPOINT = "https://eur-lex.europa.eu/EURLexWebService"

SEARCH_NS = "{http://eur-lex.europa.eu/search}"


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
    # the DECISION date, which can fall a few years later -- so caselaw reaches a
    # bounded lookback below the floor rather than tracking it exactly (see
    # enum_years).
    wdate_follows_celex_year: bool
    # Enumerate only works that carry a downloadable item in one of the wanted
    # languages? Sector 6 lists thousands of judgments available only in the
    # procedural language(s); with no swe/eng content they can never be stored, yet
    # each was re-selected (a wasted round trip) and logged "no manifestation"
    # every run. A FILTER EXISTS on the full work->expression->manifestation->item
    # chain (the one the content fetch walks) drops them at discovery. Safe: a work
    # fetch_selection can pull content for is exactly one with such an item, so the
    # filter never hides a case we could actually store. Off for treaties/acts,
    # which carry every official language anyway (and would only pay the extra join).
    require_language_expression: bool = False
    # Can a work in this sector repeal another? Only legislation does. Asking
    # the caselaw sector what its judgments repeal is 32,000 CELEX of query for
    # a guaranteed empty answer.
    repeals: bool = False

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
                   re.compile(r"3\d{4}[RL]\d{4}(\(\d+\))?$"), 1952, True,
                   repeals=True),
    "caselaw": Sector("caselaw", "6", CASELAW_TYPES,
                      re.compile(r"6\d{4}(?:%s)\d{4}$" % "|".join(CASELAW_TYPES)),
                      1954, False, require_language_expression=True),
}


# the sector digits whose works can repeal another -- `sync` reads the flag off
# the Sector it is walking, `download_document` has only a CELEX
REPEALING_SECTORS = frozenset(s.digit for s in SECTORS.values() if s.repeals)


def celex_slug(celex):
    """Filesystem form of a CELEX. Only '/' (treaty texts) is substituted, so
    the basefile is recoverable from the path."""
    return celex.replace("/", "_")


def doc_dir(root, celex):
    return Path(root) / celex[1:5] / celex_slug(celex)


# --------------------------------------------------------------------------
# discovery -- CELEX enumeration via the CELLAR SPARQL endpoint
# --------------------------------------------------------------------------



def _enum_query(celex_prefix, since, languages=None):
    """A DISTINCT (CELEX, work-date) listing for one sector-year-descriptor
    prefix; the date feeds the watermark. `since` (a date) restricts to
    documents whose work date is on/after it -- but a document with no
    work_date_document (a modelled state: enumerate_celex stores None,
    notice_ttl handles it) must survive the filter, hence the !BOUND clause. A
    plain `?d >= ...` evaluates error->false for an unbound ?d and would drop
    every wdate-less work from every incremental run.

    `languages` (set for sectors with `require_language_expression`) adds a
    FILTER EXISTS on the work->expression->language edge, so only works that
    carry an expression in one of those languages are listed -- the discovery-time
    filter that keeps sector 6's procedural-language-only judgments out of the
    per-document selection entirely. EXISTS (not a join) keeps one row per CELEX."""
    datefilter = (' FILTER(!BOUND(?d) || ?d >= "%s"^^xsd:date)' % since.isoformat()
                  if since else "")
    langfilter = ""
    if languages:
        langs = ", ".join('"%s"' % code.upper() for code in languages)
        # the full work->expression->manifestation->item chain _selection_query
        # walks, so a listed work is exactly one fetch_selection returns content
        # for: a swe/eng *expression* alone is not enough (some works carry one
        # with no downloadable item, which still logged "no manifestation" and got
        # re-selected every run). Verified against the live endpoint: item-level is
        # both more precise and no slower, and -- being the same chain the content
        # fetch uses -- can never hide a work we could actually store. FILTER EXISTS
        # (no owl:sameAs) short-circuits per work, so it dodges the whole-year
        # manifestation-join blow-up the selection query is chunked to avoid.
        langfilter = (" FILTER EXISTS { ?expr cdm:expression_belongs_to_work ?w ; "
                      "cdm:expression_uses_language ?langc . "
                      "?manif cdm:manifestation_manifests_expression ?expr . "
                      "?item cdm:item_belongs_to_manifestation ?manif . "
                      "FILTER(REPLACE(STR(?langc), '.*/', '') IN (%s)) }" % langs)
    return ("PREFIX cdm: <http://publications.europa.eu/ontology/cdm#> "
            "PREFIX xsd: <http://www.w3.org/2001/XMLSchema#> "
            "SELECT DISTINCT ?celex ?d WHERE { "
            "?w cdm:resource_legal_id_celex ?celex . "
            "OPTIONAL { ?w cdm:work_date_document ?d . } "
            'FILTER(STRSTARTS(STR(?celex), "%s"))%s%s } ORDER BY ?celex'
            % (celex_prefix, datefilter, langfilter))


# How many years a caselaw CELEX (case-filing year) can lag behind its work
# date (decision date): a case filed in year Y is decided within Y+LAG. This
# bounds how far below the incremental floor caselaw discovery must reach.
#
# 5 is a deliberate, checked ceiling, not a guess: the longest recorded
# lodging-to-judgment gap in the Court's history is a little over 3 years, so 5
# carries ~2 years of headroom above the worst case ever observed. Do not flag
# this as an unbounded-completeness risk in review -- the bound is empirical.
CASELAW_DECISION_LAG_YEARS = 5


def enum_years(sector, since):
    """The CELEX years to walk for `sector` given a work-date floor `since`.

    The enumeration start is decoupled from the date floor because a sector's
    CELEX year does not always track its work date. Legislation (sector 3) and
    treaties (sector 1) carry `wdate_follows_celex_year`: the CELEX year is the
    adoption/consolidation year, equal to the work date year, so with a floor
    the walk may start at `since.year` and never re-query the decades below it.

    Caselaw (sector 6) does not: a judgment's CELEX year is the CASE year while
    work_date_document is the DECISION date, which can fall a few years later (a
    case filed 2020, decided 2025). Tracking `since.year` exactly would make a
    slice like `62020CJ...` invisible to a 2025 floor, so caselaw reaches back
    CASELAW_DECISION_LAG_YEARS below the floor -- enough to cover the
    filing-to-decision lag without re-walking (and re-querying) every year back
    to first_year on every incremental run."""
    if since is None:
        start = sector.first_year
    elif sector.wdate_follows_celex_year:
        start = max(sector.first_year, since.year)
    else:
        start = max(sector.first_year, since.year - CASELAW_DECISION_LAG_YEARS)
    return range(start, date.today().year + 1)


def enumerate_celex(session, sector, since=None, languages=None):
    """Yield (year, [(CELEX, work_date), ...]) per year, oldest first. Each
    year's slice is fetched whole (one SPARQL query, or two for acts' R/L
    prefixes), so the caller knows the year's exact size up front. With `since`
    set, the walk is bounded by enum_years (which years) and the per-slice wdate
    FILTER (which documents within a year). `languages` is applied as a
    has-an-expression-in-these-languages discovery filter only for sectors that
    ask for it (`require_language_expression`)."""
    langfilter = languages if sector.require_language_expression else None
    for year in enum_years(sector, since):
        print("  querying %s %d ..." % (sector.name, year),
              file=sys.stderr, flush=True)
        items, seen = [], set()
        for prefix in sector.prefixes:
            rows = sparql_select(session, _enum_query(
                "%s%d%s" % (sector.digit, year, prefix), since, langfilter))
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


def enumerate_celex_soap(session, sector, since=None, languages=None):
    """Same contract as enumerate_celex, over the SOAP service (which exposes no
    per-hit work date, so it pairs each CELEX with None -- a soap run does not
    advance the watermark). Slices the DN (CELEX) wildcard query by year to stay
    under the per-search cap, walking the years enum_years selects. `languages`
    is accepted for a uniform enumerate signature but unused: the expert-search
    service has no expression-language predicate to filter on (the swe/eng test
    falls to store_document, as it did before the SPARQL discovery filter)."""
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




def download_document(session, root, celex, languages, delay):
    """Fetch a single CELEX's content, selecting over SPARQL. Returns the
    languages stored (empty if none of the requested languages exist). The sweep
    (`sync`) selects in bulk; this serves the explicit per-CELEX refetch."""
    selection = fetch_selection(session, [celex], languages)
    wdate, eurovoc, validity, _answered = fetch_metadata(session, [celex])
    relations = fetch_relations(session, [celex])
    stored = store_document(session, doc_dir(root, celex), celex,
                            wdate.get(celex), selection.get(celex, []),
                            eurovoc.get(celex, []),
                            validity.get(celex, (None, None)),
                            relations.get(celex))
    if stored and celex[0] in REPEALING_SECTORS:
        for target, when in refresh_repeal_targets(session, root, [celex]):
            print("%s repeals %s (no longer in force %s)"
                  % (celex, target, when), flush=True)
    time.sleep(delay)
    return stored


# --------------------------------------------------------------------------
# the harvest
# --------------------------------------------------------------------------

def is_downloaded(root, celex):
    return compress.exists(doc_dir(root, celex) / "notice.ttl")


def prune_empty(root, remove=True):
    """Count (and, unless `remove` is False, delete) harvest dirs that hold only
    a notice.ttl and no Swedish/English content -- metadata-only works (a
    pre-accession act never translated) that earlier runs left behind before
    store_document learned to skip them. The harvest dir is rebuildable, so this
    is safe to re-run. Returns the number of such dirs."""
    root = Path(root)
    n = 0
    for notice in compress.glob(root, "*/*/notice.ttl"):
        d = notice.parent
        if all(compress.logical(p).name == "notice.ttl" for p in d.iterdir()):
            if remove:
                compress.unlink(notice)
                d.rmdir()
            n += 1
    return n


def refresh_repeal_targets(session, root, celexes):
    """Re-read the metadata of every document `celexes` repeals and we hold.
    Returns the (celex, repeal date) pairs whose repeal date this call *changed*
    -- compared against what the notice said before, so a target already
    recorded as repealed is not reported again on every run.

    This is what keeps the corpus current without re-reading it. A repeal is
    recorded on the *repealed* act -- its `resource_legal_in-force` flips to 0
    -- and our walk is bounded by work date, so it never returns to a 1995
    directive repealed in 2018. The incoming act is the only thing that
    announces the change while we are already talking to CELLAR about it, so
    each newly stored act is asked what it repeals and the targets we hold are
    re-read on the spot.

    It does not catch every repeal: an act that simply ends by its own terms is
    named by no incoming act (36% of the out-of-force acts measured), and for
    those `refresh_metadata` over the corpus stays the backstop."""
    targets = sorted({t for repealed in fetch_repeals(session, celexes).values()
                      for t in repealed if is_downloaded(root, t)})
    before = {t: notice_repeal_date(doc_dir(root, t)) for t in targets}
    refreshed = refresh_metadata(session, root, targets)
    next(refreshed)             # the work-list size, of no use here
    return [(celex, repealed)
            for celex, repealed, _in_force, _written in refreshed
            if repealed and repealed != before[celex]]


def refresh_metadata(session, root, celexes=None, limit=None,
                     chunk=SELECT_CHUNK):
    """Re-read the CELLAR metadata of already-downloaded CELEX and rewrite their
    notice.ttl, without refetching a byte of content.

    This is also how a corpus harvested before the notice carried the amends /
    implements relations learns which of its acts are base acts and which only
    maintain another one.

    Yields the work-list size first -- it is known before any query runs, so a
    caller's progress line can carry a real total -- then
    (celex, repeal date or None, in-force flag, rewritten) per document. The
    caller counts the acts CELLAR reports out of force but gives no end date
    for (they keep no repeal date and stay listed) and the ones it answered
    nothing about; a run that reported neither would read as complete.

    This is how a corpus harvested before the notice carried the validity pair
    learns which of its acts no longer state law. The metadata query takes a
    VALUES list, so a thousand documents cost one round trip; the content -- the
    expensive part -- is untouched, and the parse that follows re-reads the
    notice each document is already parsed from.

    A document CELLAR answers with no work date keeps the one its notice
    already carries: the rewrite must not cost a document its date, which is the
    only thing the parser reads out of a notice besides the repeal. A document
    CELLAR does not answer for **at all** is not rewritten -- an empty answer is
    an endpoint hiccup as often as it is a fact, and rewriting on one would
    replace a stored notice with a stub. Such a CELEX yields `(celex, None,
    None)` with `rewritten` False, so the caller counts it rather than reporting
    it as done.

    A named CELEX the corpus does not hold is skipped rather than given a
    notice of its own: `is_downloaded` keys on the notice, so writing one for a
    document with no content would mark it downloaded for ever.

    `celexes` defaults to every downloaded document *whose notice does not
    already record a repeal* -- a repeal never lifts, so re-reading one asks
    CELLAR a question it has already answered. That is what keeps the periodic
    audit shrinking rather than costing the whole corpus every time. `limit`
    bounds the work either way; re-run to go deeper. Pass the whole corpus
    (`celexes=list_basefiles(root)`) for the one-off backfill that gives every
    act its relations.

    No delay of its own: every query goes out through `net.request`, which paces
    the host to the Crawl-delay its robots.txt asks for. A second sleep here
    would be the per-source throttle that pacing replaced.
    """
    root = Path(root)
    celexes = ([c for c in celexes if is_downloaded(root, c)]
               if celexes is not None
               else [c for c in list_basefiles(root)
                     if not notice_repeal_date(doc_dir(root, c))])
    if limit:
        celexes = celexes[:limit]
    yield len(celexes)          # the work-list size, before any query runs
    for i in range(0, len(celexes), chunk):
        batch = celexes[i:i + chunk]
        wdate, eurovoc, validity, answered = fetch_metadata(session, batch)
        relations = fetch_relations(session, batch)
        for celex in batch:
            if celex not in answered:
                yield celex, None, None, False
                continue
            target = doc_dir(root, celex)
            # both the work date and the validity pair fall back on what the
            # notice already holds. CELLAR answering about a work without
            # restating its validity is not the work becoming valid again -- a
            # repeal never lifts -- and overwriting on a thin answer would erase
            # the one fact this whole path exists for.
            pair = validity.get(celex) or notice_validity(target)
            # the relations fall back on the notice for the same reason the date
            # and the validity pair do: CELLAR answering about a work without
            # restating what it amends is not the act becoming a base act.
            compress.write_download(target / "notice.ttl", notice_ttl(
                celex, wdate.get(celex) or notice_work_date(target),
                eurovoc.get(celex, []), pair,
                relations.get(celex) or notice_relations(target)))
            # the repeal is read back off the written notice rather than derived
            # here, so what the caller counts is what the next parse will read
            yield celex, notice_repeal_date(target), pair[0], True


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

    Each year's newly stored acts are also asked what they repeal, and every
    target the corpus already holds is re-read on the spot
    (`refresh_repeal_targets`) -- the repeal is recorded on the repealed act,
    which this walk would otherwise never revisit.

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

    seen = stored = skipped = repealed = 0
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
        meta_wdate, meta_eurovoc, meta_validity, _ans = fetch_metadata(
            session, [celex])
        meta_relations = fetch_relations(session, [celex])
        if store_document(session, doc_dir(root, celex), celex,
                          meta_wdate.get(celex), sel.get(celex, []),
                          meta_eurovoc.get(celex, []),
                          meta_validity.get(celex, (None, None)),
                          meta_relations.get(celex)):
            stored += 1
            retry.discard(celex)
        elif not worth_retrying(meta_wdate.get(celex)):
            retry.discard(celex)                 # aged out, still empty: give up
        time.sleep(delay)

    for year, items in enumerate_fn(session, sector, since, languages=languages):
        scope = "%s %d" % (sector_name, year)
        total = len(items)                       # the year-slice's exact size
        # one batched selection (+ metadata) query for the whole year's pending
        # CELEX, replacing a ~10s tree notice per document; a fully-downloaded
        # year (incremental steady state) queries nothing.
        pending = [celex for celex, _ in items
                   if full or not is_downloaded(root, celex)]
        selection, eurovoc, validity, relations = {}, {}, {}, {}
        if pending:
            selection = fetch_selection(session, pending, languages)
            _meta_wdate, eurovoc, validity, _ans = fetch_metadata(
                session, pending)
            relations = fetch_relations(session, pending)
        rep.reset()                     # don't bill the year's queries to doc 1
        y_seen = y_stored = y_skipped = 0
        y_new = []                      # the CELEX actually stored this year
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
                                  eurovoc.get(celex, []),
                                  validity.get(celex, (None, None)),
                                  relations.get(celex)):
                    stored += 1
                    y_stored += 1
                    y_new.append(celex)
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
                rep.update(y_seen, total, scope=scope, actual=y_stored,
                           stored=y_stored, skipped=y_skipped)
        rep.update(y_seen, total, scope=scope, actual=y_stored,
                   stored=y_stored, skipped=y_skipped)
        rep.done()                  # finish the year's overwriting line
        # what this year's new acts repeal: their targets' stored metadata still
        # says "in force", and this run is the only moment anything says
        # otherwise. Asked per year, over the acts actually stored, so a
        # fully-downloaded year queries nothing.
        if y_new and sector.repeals:
            for target, when in refresh_repeal_targets(session, root, y_new):
                repealed += 1
                print("  %s: no longer in force %s" % (target, when), flush=True)
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
    if repealed:
        print("eurlex %s: %d held document(s) re-read as no longer in force"
              % (sector_name, repealed), flush=True)
    return seen, stored, skipped


def list_basefiles(root):
    """CELEX basefiles harvested into root, recovered from the path."""
    return sorted(p.parent.name.replace("_", "/")
                  for p in compress.glob(Path(root), "*/*/notice.ttl"))
