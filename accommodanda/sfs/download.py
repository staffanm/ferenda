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

The harvest writes the beta-API JSON flat under the download root, with
superseded consolidations tucked into a sibling archive/ subtree (any legacy
SFST/SFSR HTML lives in its own sfst/, sfsr/ siblings):

  {year}/{nr}.json                     the current consolidation
  archive/{year}/{nr}/{version}.json   superseded consolidations

  python -m accommodanda.sfs.download DESTDIR [--full] [--limit N]
"""

import argparse
import json
import os
import re
import time
from pathlib import Path

from ..lib.net import make_session, request
from ..lib.util import progress

ENDPOINT = "https://beta.rkrattsbaser.gov.se/elasticsearch/SearchEsByRawJson"
PAGE_SIZE = 100
PAGE_DELAY = 1.0           # seconds between pages -- conservative vs. throttling
USER_AGENT = "lagen.nu harvester (https://lagen.nu/, staffan@tomtebo.org)"
WATERMARK = ".watermark"   # file under destdir: max uppdateradDateTime harvested

# fulltext.andringInford looks like "t.o.m. SFS 2026:764"; pull the SFS nr
RE_VERSION = re.compile(r"(\d+:\s?\d+)")


def _es(session, esquery):
    """Run an ES query through the rkrattsbaser passthrough and return the
    parsed response, with lib.net's retry/throttle/diagnostics handling."""
    return request(session, "POST", ENDPOINT, parse_json=True, json={
        "searchIndexes": ["Sfs"], "api": "search", "json": esquery})


def fetch_one(session, beteckning):
    """Fetch a single published act's ``_source`` by beteckning, or None if
    the beta database has no published act with that number."""
    hits = _es(session, {"query": {"bool": {"must": [
        {"term": {"beteckning.keyword": beteckning}},
        {"term": {"publicerad": True}}]}}, "size": 1})["hits"]["hits"]
    return hits[0]["_source"] if hits else None


def search(session, query, search_after=None):
    """POST one page (PAGE_SIZE hits) for the given ES `query`, which must
    carry its own `sort`; returns the parsed response. search_after pages past
    ES's 10k from+size ceiling."""
    body = dict(query, size=PAGE_SIZE, track_total_hits=True)
    if search_after is not None:
        body["search_after"] = search_after
    return _es(session, body)


def write_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def read_watermark(destdir):
    """The max uppdateradDateTime harvested by a previous clean run, or None
    when there is no prior run (so a full backfill is due)."""
    path = Path(destdir) / WATERMARK
    return path.read_text().strip() if path.exists() else None


def write_watermark(destdir, value):
    write_atomic(Path(destdir) / WATERMARK, value.encode())


class MalformedBeteckning(ValueError):
    """A document's beteckning is missing or unusable as a path segment.
    Raised so the harvest can skip the one record instead of aborting the
    whole sweep."""


def _split_beteckning(beteckning):
    """Split a beteckning ("year:nr") into the two path segments it becomes
    on disk. The year is not always purely numeric -- some acts carry a
    letter-prefixed series (e.g. 'N2026:3') -- so we validate *path safety*
    rather than a digit year: a beteckning that could escape destdir is a
    corrupt record, not something to store."""
    if not isinstance(beteckning, str) or ":" not in beteckning:
        raise MalformedBeteckning("missing or malformed beteckning: %r" % beteckning)
    year, nr = beteckning.split(":", 1)
    nr = nr.replace(" ", "_")
    for seg in (year, nr):
        if not seg or seg.startswith(".") or "/" in seg or "\\" in seg:
            raise MalformedBeteckning("unsafe beteckning: %r" % beteckning)
    return year, nr


def source_path(destdir, beteckning):
    year, nr = _split_beteckning(beteckning)
    return destdir / year / ("%s.json" % nr)


def archive_path(destdir, beteckning, version):
    year, nr = _split_beteckning(beteckning)
    safe = version.replace(":", "_").replace(" ", "_").replace("/", "_")
    return destdir / "archive" / year / nr / ("%s.json" % safe)


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


def sync(destdir, full=False, limit=None, delay=PAGE_DELAY):
    """Harvest published acts into destdir, returning (seen, new, updated,
    skipped). The mode is chosen automatically from the stored watermark (the
    max uppdateradDateTime harvested by the last clean run):

    * **Backfill** -- `full` is set, or no watermark exists yet. Sweeps the
      whole corpus oldest-first by the immutable grundforfattningId, so even
      acts with no uppdateradDateTime are captured. The watermark is written
      only on clean completion; a crashed backfill leaves none and restarts.

    * **Incremental** -- a watermark exists. Asks the server for only the acts
      changed since (uppdateradDateTime >= watermark), oldest-change-first.
      Changes therefore arrive in timestamp order, so the watermark is
      checkpointed after every page and an interrupted run resumes where it
      stopped. An amendment to an old base act bumps that act's
      uppdateradDateTime, so it surfaces here despite its old SFS number.
    """
    destdir = Path(destdir)
    session = make_session(USER_AGENT)
    watermark = read_watermark(destdir)
    backfill = full or watermark is None
    if backfill:
        query = {"query": {"term": {"publicerad": True}},
                 "sort": [{"grundforfattningId": "asc"}]}
    else:
        query = {"query": {"bool": {"must": [
                     {"term": {"publicerad": True}},
                     {"range": {"uppdateradDateTime": {"gte": watermark}}}]}},
                 "sort": [{"uppdateradDateTime": "asc"},
                          {"grundforfattningId": "asc"}]}
    print("sfs harvest: %s (since %s)"
          % ("full backfill" if backfill else "incremental",
             watermark or "scratch"), flush=True)

    after = None
    seen = new = updated = skipped = page_no = 0
    high = watermark
    truncated = False
    while True:
        page = search(session, query, after)
        hits = page["hits"]["hits"]
        if not hits:
            break
        page_no += 1
        for hit in hits:
            after = hit["sort"]
            seen += 1
            try:
                status = save_document(destdir, hit["_source"])
            except MalformedBeteckning as exc:
                skipped += 1
                print("  skipped: %s" % exc, flush=True)
                continue
            new += status == "new"
            updated += status == "updated"
            ts = hit["_source"].get("uppdateradDateTime")
            if ts and (high is None or ts > high):
                high = ts
            if limit and seen >= limit:
                truncated = True
                break
        progress(seen, page["hits"]["total"]["value"], page=page_no,
                 new=new, updated=updated)
        # incremental arrives in timestamp order, so the running max is a safe
        # resume point -- checkpoint each page. Backfill arrives in base-id
        # order, so its max is only valid once the whole sweep completes.
        if not backfill and high and high != watermark:
            write_watermark(destdir, high)
            watermark = high
        if truncated:
            break
        time.sleep(delay)
    if backfill and high and not truncated:
        write_watermark(destdir, high)
    return seen, new, updated, skipped


def list_basefiles(destdir):
    """SFS basefiles ("year:nr") harvested into destdir, for the build driver.
    The deeper archive/<year>/<nr>/ subtree of superseded versions is naturally
    excluded by the one-level-deep year/nr.json glob."""
    return sorted("%s:%s" % (p.parent.name, p.stem.replace("_", " "))
                  for p in Path(destdir).glob("*/*.json"))


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
    seen, new, updated, skipped = sync(args.destdir, full=args.full,
                                       limit=args.limit, delay=args.delay)
    print("%d seen, %d new, %d updated, %d skipped"
          % (seen, new, updated, skipped))


if __name__ == "__main__":
    main()
