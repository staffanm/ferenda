"""Harvester for vägledande avgöranden from the courts' publication
service at rattspraxis.etjanst.domstol.se (the successor to the old
lagrummet.se/vagledande-avgoranden zip feed).

The service is an Angular SPA over a JSON API:

  POST /api/v1/sok                      paged search; with filter=null it
                                        enumerates the whole corpus
                                        (~17k publications, 1981-).
                                        Page size capped at 100.
  GET  /api/v1/publiceringar/{id}       single record -- same fields as a
                                        search hit, so the harvester never
                                        needs it
  GET  /api/v1/bilagor/{fillagringId}   attachment (PDF); the id contains
                                        slashes and must be %-encoded

A record carries the decision metadata (court, målnummer, referatnummer,
avgörandedatum, lagrum, keywords, summary), the full decision text as
HTML in `innehall`, and a list of PDF attachments. Records are keyed by
UUID and stored verbatim:

  DESTDIR/{domstolKod}/{id}.json
  DESTDIR/{domstolKod}/{id}/{filnamn}     attachments

Initial harvest pages the corpus in ascending avgörandedatum order (new
publications append at the end, so the iteration is stable). Incremental
runs page in descending order and stop at the first page with no new or
changed records.

  python -m accommodanda.dv DESTDIR [--full] [--no-bilagor] [--limit N]
"""

import argparse
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import quote

from ..lib.net import make_session, request
from ..lib.util import progress

API = "https://rattspraxis.etjanst.domstol.se/api/v1"
PAGE_SIZE = 100
COMPLETE = ".complete"   # marker under destdir: corpus walked clean at least once
USER_AGENT = "lagen.nu harvester (https://lagen.nu/, staffan@tomtebo.org)"

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


def write_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


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
    """Harvest publications into destdir, returning (seen, changed).

    Backfilled -- the whole corpus walked oldest-first, downloading whatever is
    missing -- when `--full` is given or it has never been cleanly walked (no
    `.complete` marker: a first run, or one interrupted partway). The marker is
    written only on a clean full pass, so an interrupted initial load is
    resumed, not mistaken for finished. Once complete, later runs go
    incremental: newest-first, stopping at the first page with nothing new or
    changed. (Edits to old records still surface only under `--full` -- the API
    exposes no last-modified field to walk by.)"""
    destdir = Path(destdir)
    session = make_session(USER_AGENT)
    marker = destdir / COMPLETE
    backfill = full or not marker.exists()
    seen = changed = index = 0
    completed = False
    while True:
        page = search_page(session, index, asc=backfill)
        records = page["publiceringLista"]
        if not records:
            completed = True   # exhausted the corpus, not an early stop
            break
        page_changed = 0
        truncated = False
        for record in records:
            if save_record(destdir, record):
                page_changed += 1
            if bilagor:
                # checks per-file existence, so a --no-bilagor harvest
                # can be backfilled by a later run
                download_bilagor(session, destdir, record, delay)
            seen += 1
            if limit and seen >= limit:
                truncated = True
                break
        changed += page_changed
        progress(seen, page["total"], page=index + 1, changed=page_changed)
        if truncated:
            break
        if not backfill and page_changed == 0:
            break  # incremental: everything older is already harvested
        index += 1
        time.sleep(delay)
    if completed and backfill:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("")
    return seen, changed


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("destdir",
                        help="target directory, e.g. site/data/dv/downloaded")
    parser.add_argument("--full", action="store_true",
                        help="walk the entire corpus oldest-first instead "
                             "of stopping at already-harvested records")
    parser.add_argument("--no-bilagor", dest="bilagor", action="store_false",
                        help="skip PDF attachments")
    parser.add_argument("--limit", type=int, help="stop after N records")
    parser.add_argument("--delay", type=float, default=0.3,
                        help="seconds between requests (default 0.3)")
    args = parser.parse_args()
    seen, changed = sync(args.destdir, full=args.full, bilagor=args.bilagor,
                         limit=args.limit, delay=args.delay)
    print("%d records seen, %d new/changed" % (seen, changed))


if __name__ == "__main__":
    main()
