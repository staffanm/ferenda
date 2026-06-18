"""Harvester for consolidated SFS (Svensk författningssamling) from the new
rättsdatabaser at beta.rkrattsbaser.gov.se.

The site is an ASP.NET SPA over a raw-Elasticsearch passthrough:

  POST /elasticsearch/SearchEsByRawJson
       body {"searchIndexes":["Sfs"], "api":"search", "json": <ES query>}

The ES `_source` is the *entire* consolidated act in one JSON object: the
plain-text body (``fulltext.forfattningstext``, already in the exact layout
the parser consumes), the register (förarbeten/CELEX/departement) and the
list of amending acts (``andringsforfattningar``). One request per document
replaces the old two-page SFST+SFSR HTML scrape.

Enumeration: the corpus is ~13.8k acts and there is no advanced search to
drive, but the raw ES query lets us walk the whole set with ``match_all`` +
a sort key + ``search_after`` (which pages past ES's 10k ``from``+``size``
window). No blind SFS-number guessing like the old system.

Versioning: unlike a court decision, a consolidated act's *content* changes
over time as amending acts are folded in. Each distinct consolidation is
identified by ``fulltext.andringInford`` ("t.o.m. SFS 2026:764"). When a
re-download carries a different ``andringInford`` than the copy on disk, the
old copy is moved to the archive under its own version id before the new one
overwrites it, so every historical consolidation stays retrievable -- the
job the old downloader's get_archive_version/archive machinery did.

The new harvest lives in its own tree, parallel to (not mixed with) the
legacy SFST/SFSR HTML pages -- mirroring the dv/ vs domstol/ split, so the
frozen HTML corpus the golden was derived from stays pristine:

  source/{year}/{nr}.json                     the current consolidation
  source/archive/{year}/{nr}/{version}.json   superseded consolidations

  python -m accommodanda.sfs.download DESTDIR [--full] [--limit N]
"""

import argparse
import json
import os
import re
import time
from pathlib import Path

from ..lib.net import make_session

ENDPOINT = "https://beta.rkrattsbaser.gov.se/elasticsearch/SearchEsByRawJson"
PAGE_SIZE = 100
USER_AGENT = "lagen.nu harvester (https://lagen.nu/, staffan@tomtebo.org)"

# fulltext.andringInford looks like "t.o.m. SFS 2026:764"; pull the SFS nr
RE_VERSION = re.compile(r"(\d+:\s?\d+)")


def fetch_one(session, beteckning):
    """Fetch a single published act's ``_source`` by beteckning, or None if
    the beta database has no published act with that number."""
    query = {"query": {"bool": {"must": [
        {"term": {"beteckning.keyword": beteckning}},
        {"term": {"publicerad": True}}]}}, "size": 1}
    response = session.post(ENDPOINT, timeout=60, json={
        "searchIndexes": ["Sfs"], "api": "search", "json": query})
    response.raise_for_status()
    hits = response.json()["hits"]["hits"]
    return hits[0]["_source"] if hits else None


def search(session, sort_field, order, search_after=None):
    """One page of published acts, sorted for stable search_after paging.
    grundforfattningId is the immutable tiebreaker."""
    query = {
        "query": {"term": {"publicerad": True}},
        "size": PAGE_SIZE,
        "track_total_hits": True,
        "sort": [{sort_field: order}, {"grundforfattningId": "asc"}],
    }
    if search_after is not None:
        query["search_after"] = search_after
    response = session.post(ENDPOINT, timeout=60, json={
        "searchIndexes": ["Sfs"], "api": "search", "json": query})
    response.raise_for_status()
    return response.json()


def write_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _split_beteckning(beteckning):
    year, nr = beteckning.split(":", 1)
    nr = nr.replace(" ", "_")
    # beteckning comes from the remote _source and becomes path segments;
    # assert it can't carry a path separator or "..".
    assert year.isdigit() and "/" not in nr and "\\" not in nr \
        and ".." not in nr, "unexpected beteckning: %r" % beteckning
    return year, nr


def source_path(destdir, beteckning):
    year, nr = _split_beteckning(beteckning)
    return destdir / "source" / year / ("%s.json" % nr)


def archive_path(destdir, beteckning, version):
    year, nr = _split_beteckning(beteckning)
    safe = version.replace(":", "_").replace(" ", "_").replace("/", "_")
    return destdir / "source" / "archive" / year / nr / ("%s.json" % safe)


def version_id(source):
    """The consolidation's identity: the last amending act folded in. An
    un-amended base act has no andringInford and is its own version."""
    andring = (source.get("fulltext") or {}).get("andringInford")
    if andring:
        match = RE_VERSION.search(andring)
        if match:
            return match.group(1).replace(" ", "")
    return source["beteckning"]


def serialize(source):
    return json.dumps(source, ensure_ascii=False, indent=2,
                      sort_keys=True).encode()


def save_document(destdir, source):
    """Store the act, archiving any superseded consolidation first.
    Returns "new", "updated" or "unchanged"."""
    path = source_path(destdir, source["beteckning"])
    new = serialize(source)
    if not path.exists():
        write_atomic(path, new)
        return "new"
    old = path.read_bytes()
    if old == new:
        return "unchanged"
    old_version = version_id(json.loads(old))
    if old_version != version_id(source):
        # the on-disk copy is a genuinely older consolidation -- preserve
        # it. A re-fetch of the same version (data correction) just
        # overwrites current, mirroring the old archive(overwrite=True).
        write_atomic(archive_path(destdir, source["beteckning"], old_version),
                     old)
    write_atomic(path, new)
    return "updated"


def sync(destdir, full=False, limit=None, delay=0.3):
    """Harvest published acts into destdir. Full mode walks the whole corpus
    oldest-first by the immutable grundforfattningId; incremental mode walks
    newest-first by uppdateradDateTime and stops at the first page with no
    new or changed document. Returns (seen, new, updated)."""
    destdir = Path(destdir)
    session = make_session(USER_AGENT)
    sort_field, order = (("grundforfattningId", "asc") if full
                         else ("uppdateradDateTime", "desc"))
    after = None
    seen = new = updated = 0
    while True:
        page = search(session, sort_field, order, after)
        hits = page["hits"]["hits"]
        if not hits:
            break
        page_changed = 0
        for hit in hits:
            status = save_document(destdir, hit["_source"])
            new += status == "new"
            updated += status == "updated"
            page_changed += status != "unchanged"
            after = hit["sort"]
            seen += 1
            if limit and seen >= limit:
                break
        total = page["hits"]["total"]["value"]
        print("%d/%d seen: %d new/changed this page"
              % (seen, total, page_changed), flush=True)
        if limit and seen >= limit:
            break
        if not full and page_changed == 0:
            break  # incremental: everything older is already harvested
        time.sleep(delay)
    return seen, new, updated


def list_basefiles(destdir):
    """SFS basefiles ("year:nr") harvested into destdir, for the build
    driver. The archive/ subtree is excluded (only top-level year dirs)."""
    return sorted("%s:%s" % (p.parent.name, p.stem.replace("_", " "))
                  for p in (Path(destdir) / "source").glob("*/*.json"))


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("destdir", help="target dir, e.g. site/data/sfs")
    parser.add_argument("--full", action="store_true",
                        help="walk the entire corpus oldest-first instead of "
                             "stopping at the first already-harvested page")
    parser.add_argument("--limit", type=int, help="stop after N documents")
    parser.add_argument("--delay", type=float, default=0.3,
                        help="seconds between pages (default 0.3)")
    args = parser.parse_args()
    seen, new, updated = sync(args.destdir, full=args.full, limit=args.limit,
                              delay=args.delay)
    print("%d seen, %d new, %d updated" % (seen, new, updated))


if __name__ == "__main__":
    main()
