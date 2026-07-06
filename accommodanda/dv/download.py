"""Downloader for vägledande avgöranden from the courts' publication
service at rattspraxis.etjanst.domstol.se (the successor to the old
lagrummet.se/vagledande-avgoranden zip feed).

The service is an Angular SPA over a JSON API:

  POST /api/v1/sok                      paged search; with filter=null it
                                        enumerates the whole corpus
                                        (~17k publications, 1981-).
                                        Page size capped at 100.
  GET  /api/v1/publiceringar/{id}       single record -- same fields as a
                                        search hit, so the downloader never
                                        needs it
  GET  /api/v1/bilagor/{fillagringId}   attachment (PDF); the id contains
                                        slashes and must be %-encoded

A record carries the decision metadata (court, målnummer, referatnummer,
avgörandedatum, lagrum, keywords, summary), the full decision text as
HTML in `innehall`, and a list of PDF attachments. Records are keyed by
UUID and stored verbatim:

  DESTDIR/{domstolKod}/{id}.json
  DESTDIR/{domstolKod}/{id}/{filnamn}     attachments

The initial download pages the corpus in ascending avgörandedatum order
(new publications append at the end, so the iteration is stable).
Incremental runs page in descending order through the shared download loop
(lib.harvest.walk), whose begin/complete watermark lifecycle makes a
crashed or `--limit`-truncated run leave the store dirty so the next run
re-walks the backlog instead of trusting the truncated run's fresh records.

The walk is ordered by avgörandedatum and the API record carries no
publication or last-modified date, so an incremental run can only cover
late publication through the safety window below the watermark
(see the call site in :func:`sync`). A referat published later than that
window after its decision date, and any upstream edit to an old record,
surfaces only under `--full`: keep a periodic `--full` sweep cron'd as the
backstop for both.

  python -m accommodanda.dv DESTDIR [--full] [--no-bilagor] [--limit N]
"""

import argparse
import json
import re
import time
from datetime import date
from pathlib import Path
from urllib.parse import quote

from ..lib.harvest import HarvestWatermark, ItemKey, walk
from ..lib.net import HARVESTER_UA as USER_AGENT
from ..lib.net import make_session, request
from ..lib.util import write_atomic

API = "https://rattspraxis.etjanst.domstol.se/api/v1"
PAGE_SIZE = 100
COMPLETE = ".complete"   # legacy marker, superseded by (migrated into) the watermark


def is_dv_downloaded(destdir, record, check_bilagor=True):
    path = record_dir(destdir, record).with_suffix(".json")
    if not path.exists():
        return False
    if check_bilagor:
        dirpath = record_dir(destdir, record)
        for bilaga in record["bilagaLista"]:
            if not bilaga.get("fillagringId"):
                continue
            name = Path(bilaga["filnamn"]).name
            target = dirpath / name
            if not (target.exists() and target.stat().st_size > 0):
                return False
    return True

# record path segments come straight from the API response; validate them so a
# malformed/compromised record can't escape destdir via "..", "/" or an
# absolute path. id is a UUID, domstolKod a short alphabetic court code.
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
                     r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
DOMSTOLKOD_RE = re.compile(r"[A-Za-zÅÄÖåäö0-9]+")


def record_dir(destdir, record):
    """``destdir/{domstolKod}/{id}`` with both segments validated."""
    kod, rid = record["domstol"]["domstolKod"], record["id"]
    assert DOMSTOLKOD_RE.fullmatch(kod), "unexpected domstolKod: %r" % kod
    assert UUID_RE.fullmatch(rid), "unexpected record id: %r" % rid
    return destdir / kod / rid


def search_page(session, index, asc):
    return request(session, "POST", API + "/sok", parse_json=True, json={
        "antalPerSida": PAGE_SIZE, "asc": asc, "sidIndex": index,
        "filter": None, "sortorder": "avgorandedatum"})


def fetch_record(session, record_id):
    """Fetch a single publication record by its UUID (the per-document path,
    `GET /api/v1/publiceringar/{id}` -- same fields as a search hit)."""
    return request(session, "GET",
                   API + "/publiceringar/" + quote(record_id, safe=""),
                   parse_json=True)


def save_record(destdir, record):
    """Store the record verbatim; returns True if new or changed."""
    path = record_dir(destdir, record).with_suffix(".json")
    if path.exists() and json.loads(path.read_text()) == record:
        return False
    write_atomic(path, json.dumps(record, ensure_ascii=False,
                                  indent=2).encode())
    return True


def download_bilagor(session, destdir, record, delay):
    dirpath = record_dir(destdir, record)
    for bilaga in record["bilagaLista"]:
        if not bilaga.get("fillagringId"):
            # seen in the wild: an attachment entry with a filename but
            # no uploaded file (upstream publication error)
            print("%s: bilaga %r has no fillagringId, skipping"
                  % (record["id"], bilaga.get("filnamn")), flush=True)
            continue
        # the API-supplied filename is reduced to its basename so it can't
        # carry directory components out of dirpath
        name = Path(bilaga["filnamn"]).name
        assert name and name not in (".", ".."), \
            "unexpected bilaga filename: %r" % bilaga["filnamn"]
        target = dirpath / name
        if target.exists() and target.stat().st_size > 0:
            continue
        url = API + "/bilagor/" + quote(bilaga["fillagringId"], safe="")
        response = request(session, "GET", url, timeout=120)
        write_atomic(target, response.content)
        time.sleep(delay)


def sync(destdir, full=False, bilagor=True, limit=None, delay=0.3):
    """Download publications into destdir, returning (seen, changed).

    Backfilled -- the whole corpus walked oldest-first, downloading whatever
    is missing -- when `--full` is given or no run has ever completed (an
    interrupted initial load is resumed; `--full` additionally re-resolves
    records already on disk, picking up upstream edits). Once caught up,
    later runs go incremental: newest-first through lib.harvest.walk, which
    marks the store dirty up front and completes (advancing the watermark)
    only on a clean, untruncated run -- a crash or `--limit` truncation
    leaves the store dirty so the next run re-walks down to the date boundary
    instead of stopping above the truncated run's un-fetched backlog."""
    destdir = Path(destdir)
    session = make_session(USER_AGENT)
    watermark_path = destdir / ".watermark.json"

    # Migrate legacy complete marker to watermark
    marker = destdir / COMPLETE
    if marker.exists() and not watermark_path.exists():
        HarvestWatermark(watermark_path).save(date.today().isoformat())

    # Referat are published on the curator's schedule, not the court's: the
    # yearly series lag their avgörandedatum by months and pick up stragglers
    # from the previous year. The walk is ordered by avgörandedatum (the API
    # record carries no publication date), so only this window below the
    # watermark can catch a late publication -- sized to that cadence: one
    # year. The consecutive-hit stop would preempt any date window (a few
    # weeks of already-downloaded decisions suffice to trip it), so its limit
    # is raised well past a year's record volume (~700) to a pure fuse; the
    # date-conclusive stop is what ends an incremental run. Publications
    # later than a year after their decision date, and edits to old records,
    # are covered by the periodic `--full` sweep (see module docstring).
    watermark = HarvestWatermark(watermark_path, lookahead_limit=5000,
                                 safety_days=365)
    backfill = full or watermark.last_harvest is None

    def records():
        index = 0
        while True:
            page = search_page(session, index, asc=backfill)
            if not page["publiceringLista"]:
                return           # exhausted the corpus, not an early stop
            yield from page["publiceringLista"]
            index += 1
            time.sleep(delay)

    def item_key(record):
        return ItemKey(
            basefile=record["id"],
            is_downloaded=is_dv_downloaded(destdir, record,
                                           check_bilagor=bilagor),
            date=record.get("avgorandedatum"))

    def resolve(record):
        changed = save_record(destdir, record)
        if bilagor:
            download_bilagor(session, destdir, record, delay)
        return changed

    result = walk(records(), resolve=resolve, item_key=item_key,
                  watermark=watermark, full=full, limit=limit, scope="dv",
                  count_label="changed")
    return result.seen, result.new


def main():
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("destdir",
                        help="target directory, e.g. site/data/downloaded/dom")
    parser.add_argument("--full", action="store_true",
                        help="walk the entire corpus oldest-first, "
                             "re-checking already-downloaded records "
                             "(picks up late publications and upstream edits)")
    parser.add_argument("--no-bilagor", dest="bilagor", action="store_false",
                        help="skip PDF attachments")
    parser.add_argument("--limit", type=int,
                        help="stop after downloading N new/changed records "
                             "(leaves the run incomplete; the next run "
                             "re-walks the backlog)")
    parser.add_argument("--delay", type=float, default=0.3,
                        help="seconds between requests (default 0.3)")
    args = parser.parse_args()
    seen, changed = sync(args.destdir, full=args.full, bilagor=args.bilagor,
                         limit=args.limit, delay=args.delay)
    print("%d records seen, %d new/changed" % (seen, changed))


if __name__ == "__main__":
    main()
