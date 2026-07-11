"""Bulk harvester for the public HUDOC JSON and HTML-conversion endpoints.

HUDOC does not advertise a bulk dump.  Its own result UI, however, pages over
``/app/query/results`` and retrieves the selected document from
``/app/conversion/docx/html/body``.  This module uses those same read-only
interfaces, newest first, and stores one metadata record plus one HTML body per
HUDOC item id.  Scope: Grand Chamber and Chamber judgments in the selected
languages (English by default -- 524 + 21,137 documents); Committee judgments,
decisions, advisory opinions, legal summaries, resolutions and communicated
cases are not harvested (``--only <itemid>`` can still fetch one deliberately).
Body downloads are the whole cost of a run --
the result pages are two orders of magnitude fewer -- so a small worker pool
keeps ``WORKERS`` body fetches in flight ahead of the walk, each worker pacing
itself by ``delay``.
"""

import json
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..lib import compress
from ..lib.harvest import HarvestWatermark, ItemKey, walk
from ..lib.net import HARVESTER_UA as USER_AGENT
from ..lib.net import make_session, request

BASE = "https://hudoc.echr.coe.int"
QUERY_ENDPOINT = BASE + "/app/query/results"
BODY_ENDPOINT = BASE + "/app/conversion/docx/html/body"
PAGE_SIZE = 500
WORKERS = 4
RANKING_MODEL = "11111111-0000-0000-0000-000000000000"
DEFAULT_LANGUAGES = ("ENG",)
FIELDS = (
    "itemid", "docname", "doctype", "application", "article", "conclusion",
    "decisiondate", "judgementdate", "kpdate", "documentcollectionid2",
    "languageisocode", "ecli", "appno", "respondent", "representedby",
    "separateopinion", "importance", "originatingbody", "isplaceholder",
)


def record_path(root, itemid):
    return Path(root) / (itemid + ".json")


def body_path(root, itemid):
    return Path(root) / (itemid + ".html")


def query_for(languages=DEFAULT_LANGUAGES, itemid=None):
    if itemid:
        return 'itemid:"%s"' % itemid
    language = " OR ".join('languageisocode:"%s"' % lang.upper()
                           for lang in languages)
    # GRANDCHAMBER is a subset of CHAMBER in HUDOC's collections; both are
    # spelled out so the intended scope is readable. COMMITTEE is disjoint
    # from CHAMBER and stays out, as do all non-judgment collections.
    return ('documentcollectionid2:"CASELAW"'
            ' AND documentcollectionid2:"JUDGMENTS"'
            ' AND (documentcollectionid2:"GRANDCHAMBER"'
            ' OR documentcollectionid2:"CHAMBER")'
            ' AND (%s)' % language)


def search_page(session, start, languages=DEFAULT_LANGUAGES, itemid=None,
                page_size=PAGE_SIZE):
    return request(session, "GET", QUERY_ENDPOINT, parse_json=True, timeout=120,
                   params={"query": query_for(languages, itemid),
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


def enumerate_records(session, languages=DEFAULT_LANGUAGES, page_size=PAGE_SIZE,
                      delay=0.2):
    """Yield every selected result newest first. A failed result page raises;
    walk's guarded_enumerate turns that into a Skip and a dirty (retried) run."""
    start = 0
    while True:
        envelope = search_page(session, start, languages, page_size=page_size)
        results = envelope.get("results") or []
        for result in results:
            yield result_record(result)
        start += len(results)
        if not results or start >= int(envelope["resultcount"]):
            return
        time.sleep(delay)


def _date(record):
    value = record.get("kpdate") or ""
    return value[:10] if len(value) >= 10 else None


def _placeholder(record):
    return str(record.get("isplaceholder", "")).lower() == "true"


def fetch_body(session, itemid, delay):
    response = request(session, "GET", BODY_ENDPOINT, timeout=180,
                       params={"library": "ECHR", "id": itemid})
    if "<" not in response.text:
        raise ValueError("%s: HUDOC returned an empty HTML body" % itemid)
    time.sleep(delay)                       # per-worker pacing
    return response


def save_record(root, record, body):
    """Store one metadata record and, when ``body`` (an in-flight or finished
    body fetch) is given, its HTML body.  Returns whether anything changed."""
    itemid = record["itemid"]
    record_file = record_path(root, itemid)
    changed = not (compress.exists(record_file)
                   and json.loads(compress.read_text(record_file)) == record)
    if changed:
        compress.write_download(record_file,
                                json.dumps(record, ensure_ascii=False, indent=2))
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
    return sorted(path.stem for path in compress.glob(root, "*.json")
                  if not path.name.startswith("."))     # skip .watermark.json


def sync(root, full=False, only=None, languages=DEFAULT_LANGUAGES, limit=None,
         delay=0.2, workers=WORKERS, log=print):
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

    try:
        if only:
            envelope = search_page(session, 0, languages, itemid=only, page_size=1)
            results = envelope.get("results") or []
            if not results:
                raise ValueError("HUDOC contains no item %s" % only)
            record = result_record(results[0])
            return 1, int(save_record(root, record, submit(record)))

        watermark = HarvestWatermark(root / ".watermark.json",
                                     lookahead_limit=100, safety_days=30)
        items = _prefetched(enumerate_records(session, languages, delay=delay),
                            submit, depth=workers * 2)

        def item_key(pair):
            record, _ = pair
            if _placeholder(record):
                return None
            itemid = record["itemid"]
            downloaded = (compress.exists(record_path(root, itemid))
                          and compress.exists(body_path(root, itemid)))
            return ItemKey(itemid, downloaded, _date(record))

        result = walk(
            items,
            resolve=lambda pair: save_record(root, pair[0], pair[1]),
            item_key=item_key,
            watermark=watermark,
            full=full,
            only=only,
            limit=limit,
            scope="hudoc",
            count_label="changed",
            log=log,
        )
        return result.seen, result.new
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
