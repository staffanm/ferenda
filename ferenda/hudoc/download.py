"""Bulk harvester for the public HUDOC JSON and HTML-conversion endpoints.

HUDOC does not advertise a bulk dump.  Its own result UI, however, pages over
``/app/query/results`` and retrieves the selected document from
``/app/conversion/docx/html/body``.  This module uses those same read-only
interfaces, newest first, and stores one metadata record plus one HTML body per
HUDOC item id.  Body downloads are the whole cost of a run --
the result pages are two orders of magnitude fewer -- so a small worker pool
keeps ``WORKERS`` body fetches in flight ahead of the walk, each worker pacing
itself by ``delay``.

**Scope** is two collections, harvested as two scopes (``lagen hudoc download
judgments|decisions``, both by default), in the selected languages (English by
default): the Court's Grand Chamber and Chamber ``judgments`` (21,672) and its
``decisions`` (33,633).  A decision is where the Court says why a complaint
never reaches the merits -- domestic remedies not exhausted, the time limit
missed, a fourth-instance appeal -- so it answers the question a reader asks
before they ever have a judgment to read.  It is also where most of the Court's
Swedish output lives: 166 Swedish judgments against 922 Swedish decisions.
Committee judgments, advisory opinions, legal summaries, resolutions and
communicated cases stay out (``--only <itemid>`` can still fetch one
deliberately); a Committee judgment in particular applies settled law to a
repetitive violation, and not one of the 7,541 is against Sweden.

**The walk is sliced by year**, newest year first, because HUDOC serves no
result past ``start=10000`` -- it keeps reporting the true ``resultcount`` and
returns an empty page.  An unsliced judgments walk therefore stopped dead at
the 10,000th document, which is why the store reached back only to
2009-09-22 and held 7,060 of 21,672 judgments.  A year is at most 1,623
documents (judgments, 2009), so one year is always a whole slice; a year that
outgrows the cap raises rather than silently truncating, and the enumeration
of a whole collection checks its summed year counts against the collection
total (verified equal for both collections, so no document falls outside
``FIRST_YEAR``..today).

Each collection walks under its **own** watermark.  Their streams would
otherwise interleave -- a judgment and a decision from the same year arrive in
no common date order -- and the first collection's date stop would end the walk
before the second was reached.
"""

import sys
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from ..lib import compress, util
from ..lib.harvest import (
    HarvestWatermark,
    ItemKey,
    flat_path,
    store_record,
    walk,
)
from ..lib.net import HARVESTER_UA as USER_AGENT
from ..lib.net import make_session, request

BASE = "https://hudoc.echr.coe.int"
QUERY_ENDPOINT = BASE + "/app/query/results"
BODY_ENDPOINT = BASE + "/app/conversion/docx/html/body"
PAGE_SIZE = 500
WORKERS = 4
HTTP_NO_CONTENT = 204
RANKING_MODEL = "11111111-0000-0000-0000-000000000000"
DEFAULT_LANGUAGES = ("ENG",)
FIELDS = (
    "itemid", "docname", "doctype", "application", "article", "conclusion",
    "decisiondate", "judgementdate", "kpdate", "documentcollectionid2",
    "languageisocode", "ecli", "appno", "respondent", "representedby",
    "separateopinion", "importance", "originatingbody", "isplaceholder",
)


def record_path(root, itemid):
    return flat_path(root, itemid)


def body_path(root, itemid):
    return flat_path(root, itemid, ".html")


# the harvested collections, each its own download scope and its own watermark.
# GRANDCHAMBER is a subset of CHAMBER in HUDOC's collections; both are spelled
# out so the intended scope is readable. COMMITTEE is disjoint from CHAMBER and
# stays out, as do the remaining non-judgment collections.
COLLECTIONS = {
    "judgments": ('documentcollectionid2:"JUDGMENTS"'
                  ' AND (documentcollectionid2:"GRANDCHAMBER"'
                  ' OR documentcollectionid2:"CHAMBER")'),
    "decisions": 'documentcollectionid2:"DECISIONS"',
}
DEFAULT_COLLECTIONS = ("judgments", "decisions")
SUMMARIES = "summaries"
# every filter the year-sliced walk can run, which is the document collections
# plus the Court's own Case-Law Information Notes. A summary is not a document
# of ours -- it says what the judgment says -- so it is harvested as metadata
# and linked from the document it summarises (`summaries.py`), never stored as a
# document, which is why it is no download scope.
FILTERS = COLLECTIONS | {SUMMARIES: 'documentcollectionid2:"CLIN"'}
# the oldest kpdate either collection carries -- Commission-era decisions reach
# back further than the Court's first judgment (Lawless, 1960). The enumeration
# proves this floor rather than trusting it: the summed year counts must equal
# the collection total.
FIRST_YEAR = 1955
# HUDOC returns an empty page past this offset while still reporting the true
# resultcount, so a slice larger than this loses documents silently
PAGING_CAP = 10000


def watermark_path(root, collection):
    return Path(root) / (".watermark-%s.json" % collection)


def year_filter(year):
    return ("kpdate:[%d-01-01T00:00:00.0Z TO %d-12-31T23:59:59.0Z]"
            % (year, year))


def query_for(languages=DEFAULT_LANGUAGES, itemid=None, collection="judgments",
              year=None):
    if itemid:
        return 'itemid:"%s"' % itemid
    language = " OR ".join('languageisocode:"%s"' % lang.upper()
                           for lang in languages)
    query = ('documentcollectionid2:"CASELAW" AND %s AND (%s)'
             % (FILTERS[collection], language))
    return "%s AND %s" % (query, year_filter(year)) if year else query


def search_page(session, start, languages=DEFAULT_LANGUAGES, itemid=None,
                page_size=PAGE_SIZE, collection="judgments", year=None):
    return request(session, "GET", QUERY_ENDPOINT, parse_json=True, timeout=120,
                   params={"query": query_for(languages, itemid, collection, year),
                           "select": ",".join(FIELDS),
                           "sort": "kpdate Descending", "start": str(start),
                           "length": str(page_size),
                           "rankingModelId": RANKING_MODEL})


def result_record(result):
    record = dict(result["columns"])
    record.pop("rank", None)
    if not record.get("itemid"):
        raise ValueError("HUDOC result carries no itemid")
    return record


def _enumerate_year(session, languages, collection, year, page_size, delay):
    """Yield one year of `collection`, newest first, and return how many the
    year held. Raises when the year outgrows what HUDOC will page over -- past
    `PAGING_CAP` the endpoint answers with an empty page instead of an error, so
    an unchecked slice loses its tail without a trace."""
    start = 0
    while True:
        envelope = search_page(session, start, languages, page_size=page_size,
                               collection=collection, year=year)
        count = int(envelope["resultcount"])
        if count > PAGING_CAP:
            raise ValueError(
                "HUDOC %s %d holds %d documents, past the %d HUDOC will page "
                "over -- the year needs splitting into smaller slices"
                % (collection, year, count, PAGING_CAP))
        results = envelope.get("results") or []
        for result in results:
            yield result_record(result)
        start += len(results)
        if not results or start >= count:
            return
        time.sleep(delay)


def enumerate_records(session, languages=DEFAULT_LANGUAGES,
                      collection="judgments", page_size=PAGE_SIZE, delay=0.2,
                      first_year=FIRST_YEAR, last_year=None):
    """Yield every record in `collection` newest first, one year at a time.

    The year slices are what makes the walk complete: HUDOC pages over at most
    `PAGING_CAP` results per query, well under either collection's size. Years
    descend, and each year's page descends by date, so the stream is globally
    newest-first and `walk`'s watermark stop keeps working unchanged.

    An exhausted enumeration checks itself: the years must account for every
    document the collection reports. A mismatch means documents sit outside
    `first_year`..`last_year` (or a page went missing), which would otherwise
    read as a complete harvest. The count is read again at the end because the
    walk takes hours: a collection that grew or shrank under it explains a
    mismatch that is nobody's bug, and the message has to let the two apart. A
    failed result page raises; walk's guarded_enumerate turns either into a Skip
    and a dirty (retried) run."""
    last_year = last_year or date.today().year
    expected = int(search_page(session, 0, languages, page_size=1,
                               collection=collection)["resultcount"])
    covered = 0
    for year in range(last_year, first_year - 1, -1):
        for record in _enumerate_year(session, languages, collection, year,
                                      page_size, delay):
            covered += 1
            yield record
        time.sleep(delay)
    if covered != expected:
        raise ValueError(
            "HUDOC %s: walked %d documents over %d..%d, but the collection held "
            "%d when the walk started and holds %d now -- either some fall "
            "outside the harvested years, or it changed under the walk"
            % (collection, covered, first_year, last_year, expected,
               int(search_page(session, 0, languages, page_size=1,
                               collection=collection)["resultcount"])))


def _date(record):
    value = record.get("kpdate") or ""
    return value[:10] if len(value) >= 10 else None


def _placeholder(record):
    return str(record.get("isplaceholder", "")).lower() == "true"


def fetch_body(session, itemid, delay):
    response = request(session, "GET", BODY_ENDPOINT, timeout=180,
                       params={"library": "ECHR", "id": itemid})
    # 204 is HUDOC stating that this item has no convertible text at all, which
    # is a fact about the document, not a failed fetch: mostly pre-1980
    # Commission decisions it holds as metadata only (GREECE v. THE UNITED
    # KINGDOM, 1956). The empty body is stored as the faithful record of that
    # answer -- the same convention as dv's zero-byte .doc files. Raising here
    # instead would store the metadata record, never a body, and leave the item
    # looking un-downloaded: it would be re-fetched on every run and fail
    # `parse` forever, which is what 11 documents were doing.
    if response.status_code != HTTP_NO_CONTENT and "<" not in response.text:
        raise ValueError("%s: HUDOC returned an empty HTML body" % itemid)
    time.sleep(delay)                       # per-worker pacing
    return response


def save_record(root, record, body):
    """Store one metadata record and, when ``body`` (an in-flight or finished
    body fetch) is given, its HTML body.  Returns whether anything changed."""
    itemid = record["itemid"]
    # the body is stored whether or not the metadata moved, so it is not a
    # store_record companion: `changed` reports the record alone
    changed = store_record(record_path(root, itemid), record)
    if body is not None:
        compress.write_download(body_path(root, itemid), body.result().content)
    return changed


def _prefetched(records, submit, depth):
    """Pair each enumerated record with its in-flight body download, keeping
    up to ``depth`` records ahead of the consumer so the pool stays busy."""
    buffer = deque()
    for record in records:
        buffer.append((record, submit(record)))
        if len(buffer) >= depth:
            yield buffer.popleft()
    yield from buffer


def list_basefiles(root):
    return compress.list_stems(root)                    # skips .watermark.json


def unique_index(root, key_of, label, log=print):
    """``key -> basefile`` over the harvested records -- the shared half of both
    joins that have to find one stored case from a key some other document
    repeats (`summaries.py` on application number and date, `translations.py` on
    the ECLI).

    Two records under one key have two causes, and neither join can act without
    being told which:

      * **different languages** -- the store was harvested with ``--lang
        ENG,FRE``, and every expression of a case repeats its identity, its
        dates and its ECLI. Nothing distinguishes them and no join is possible,
        so this raises.
      * **the same language** -- HUDOC's own data. It stores some decisions
        twice under two item ids (GRACZYK MARIAN v. POLAND is both 001-81024 and
        001-103335), and it mints one ECLI for decisions taken together
        (RORISON, LINDOW, HENNIS and STEVENSON v. THE UNITED KINGDOM share
        ECLI:CE:ECHR:2008:0429DEC006187800). Such a key identifies no single
        case, so it is dropped and counted: a summary or a translation reaching
        only that key finds no host, which is the true answer.

    Measured over 39,046 stored records: 10 ECLIs and 121 (application number,
    date) pairs are claimed by more than one case, every one of them
    same-language, costing 51 cases their key. The judgments-only store this
    started from had none, which is why both joins first assumed uniqueness."""
    basefiles = list_basefiles(root)
    index, languages, ambiguous = {}, {}, set()
    for done, basefile in enumerate(basefiles, 1):
        util.status(done, len(basefiles), "hudoc  indexing stored cases")
        record = compress.read_json(record_path(root, basefile))
        language = record.get("languageisocode")
        for key in key_of(record):
            if key not in index:
                index[key], languages[key] = basefile, language
            elif language != languages[key]:
                raise ValueError(
                    "%s and %s share %s %s in different languages -- the store "
                    "holds more than one expression of this case, and no join "
                    "can tell them apart (see --lang)"
                    % (index[key], basefile, label, key))
            else:
                ambiguous.add(key)
    sys.stderr.write("\n")                 # close the live counter's line
    for key in ambiguous:
        del index[key]
    log("  indexed %d stored cases; %d %s claimed by more than one case "
        "identify none" % (len(basefiles), len(ambiguous), label))
    return index


def sync(root, full=False, only=None, languages=DEFAULT_LANGUAGES,
         collections=DEFAULT_COLLECTIONS, limit=None, delay=0.2, workers=WORKERS,
         log=print):
    root = Path(root)
    session = make_session(USER_AGENT)
    pool = ThreadPoolExecutor(max_workers=workers)

    def submit(record):
        """An in-flight body fetch when the walk will need one, else None."""
        itemid = record["itemid"]
        if _placeholder(record) or (not full
                                    and compress.exists(body_path(root, itemid))):
            return None
        return pool.submit(fetch_body, session, itemid, delay)

    def item_key(pair):
        record, _ = pair
        if _placeholder(record):
            return None
        itemid = record["itemid"]
        downloaded = (compress.exists(record_path(root, itemid))
                      and compress.exists(body_path(root, itemid)))
        return ItemKey(itemid, downloaded, _date(record))

    try:
        if only:
            envelope = search_page(session, 0, languages, itemid=only, page_size=1)
            results = envelope.get("results") or []
            if not results:
                raise ValueError("HUDOC contains no item %s" % only)
            record = result_record(results[0])
            return 1, int(save_record(root, record, submit(record)))

        seen = new = 0
        for collection in collections:
            watermark = HarvestWatermark(watermark_path(root, collection),
                                         lookahead_limit=100, safety_days=30)
            items = _prefetched(
                enumerate_records(session, languages, collection, delay=delay),
                submit, depth=workers * 2)
            result = walk(
                items,
                resolve=lambda pair: save_record(root, pair[0], pair[1]),
                item_key=item_key,
                watermark=watermark,
                full=full,
                only=only,
                # a limit is the run's whole budget, not each collection's, so
                # what the earlier collections already spent comes off it
                limit=None if limit is None else limit - new,
                scope="hudoc %s" % collection,
                count_label="changed",
                log=log,
            )
            seen += result.seen
            new += result.new
            if limit is not None and new >= limit:
                break
        return seen, new
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
