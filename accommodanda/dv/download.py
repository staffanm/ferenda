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
import time
from pathlib import Path
from urllib.parse import quote

import requests

API = "https://rattspraxis.etjanst.domstol.se/api/v1"
PAGE_SIZE = 100
USER_AGENT = "lagen.nu harvester (https://lagen.nu/, staffan@tomtebo.org)"


def make_session():
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    return session


def search_page(session, index, asc):
    response = session.post(API + "/sok", timeout=60, json={
        "antalPerSida": PAGE_SIZE, "asc": asc, "sidIndex": index,
        "filter": None, "sortorder": "avgorandedatum"})
    response.raise_for_status()
    return response.json()


def fetch_record(session, record_id):
    """Fetch a single publication record by its UUID (the per-document path,
    `GET /api/v1/publiceringar/{id}` -- same fields as a search hit)."""
    response = session.get(
        API + "/publiceringar/" + quote(record_id, safe=""), timeout=60)
    response.raise_for_status()
    return response.json()


def write_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def save_record(destdir, record):
    """Store the record verbatim; returns True if new or changed."""
    path = destdir / record["domstol"]["domstolKod"] / (record["id"] + ".json")
    if path.exists() and json.loads(path.read_text()) == record:
        return False
    write_atomic(path, json.dumps(record, ensure_ascii=False,
                                  indent=2).encode())
    return True


def download_bilagor(session, destdir, record, delay):
    dirpath = (destdir / record["domstol"]["domstolKod"] / record["id"])
    for bilaga in record["bilagaLista"]:
        if not bilaga.get("fillagringId"):
            # seen in the wild: an attachment entry with a filename but
            # no uploaded file (upstream publication error)
            print("%s: bilaga %r has no fillagringId, skipping"
                  % (record["id"], bilaga.get("filnamn")), flush=True)
            continue
        target = dirpath / bilaga["filnamn"].replace("/", "_")
        if target.exists() and target.stat().st_size > 0:
            continue
        url = API + "/bilagor/" + quote(bilaga["fillagringId"], safe="")
        response = session.get(url, timeout=120)
        response.raise_for_status()
        write_atomic(target, response.content)
        time.sleep(delay)


def sync(destdir, full=False, bilagor=True, limit=None, delay=0.3):
    """Harvest publications into destdir. Returns (seen, changed)."""
    destdir = Path(destdir)
    session = make_session()
    seen = changed = index = 0
    while True:
        page = search_page(session, index, asc=full)
        records = page["publiceringLista"]
        if not records:
            break
        page_changed = 0
        for record in records:
            if save_record(destdir, record):
                page_changed += 1
            if bilagor:
                # checks per-file existence, so a --no-bilagor harvest
                # can be backfilled by a later run
                download_bilagor(session, destdir, record, delay)
            seen += 1
            if limit and seen >= limit:
                break
        changed += page_changed
        print("page %d (%d/%d): %d new/changed" %
              (index, seen, page["total"], page_changed), flush=True)
        if limit and seen >= limit:
            break
        if not full and page_changed == 0:
            break  # incremental: everything older is already harvested
        index += 1
        time.sleep(delay)
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
