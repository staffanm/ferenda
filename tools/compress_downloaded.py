"""One-time backfill: Brotli-compress the already-on-disk downloaded/ tree to
match today's storage policy (commit 99370162 routed the downloaders through
lib/compress.write_download, but a corpus fetched before that is still plain).

Applies the *exact* funnel policy so the result is indistinguishable from a
fresh download:
  * dotfiles (.watermark-*, .complete, .DS_Store, ...) are state/marker files
    written straight through write_atomic, never the compress funnel -> left plain.
  * already-compressed payloads (PDF/zip/docx/... per INCOMPRESSIBLE_SUFFIXES)
    -> left plain (download_encodings returns ()).
  * files below MIN_SIZE -> left plain (write_bytes stores them plain).
  * everything else (html/json/ttl/fmx4/xhtml/xml/...) -> replaced by its .br
    variant, the plain original removed (write_bytes clears stale siblings).

Idempotent: a logical path that already has a .br/.gz variant on disk is skipped.
Only the downloaded/ tree is touched; artifact/ and generated/ are out of scope.
"""

import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from accommodanda import config
from accommodanda.lib import compress

DOWNLOADED = config.DATA / "downloaded"


def target_encodings(path):
    """The download storage policy for `path`, or () to leave it plain."""
    return compress.download_encodings(path)


def scan():
    """Every plain downloaded file that today's policy would store compressed
    and that has no compressed variant yet -- (path, encodings) worklist."""
    tasks = []
    for dirpath, _dirs, files in os.walk(DOWNLOADED):
        for name in files:
            if name.startswith("."):
                continue                      # marker/state file, not funnelled
            if name.endswith(compress.SUFFIXES):
                continue                      # already a .br/.gz variant
            p = Path(dirpath) / name
            if any((p.parent / (name + s)).exists() for s in compress.SUFFIXES):
                continue                      # already has a compressed sibling
            encs = target_encodings(p)
            if not encs:
                continue                      # incompressible payload -> plain
            if p.stat().st_size < compress.MIN_SIZE:
                continue                      # too small -> plain
            tasks.append((str(p), encs))
    return tasks


def compress_one(task):
    """Read a plain file, write its compressed variant, drop the plain original.
    Returns (orig_size, new_size) on success or ('ERR', path, message)."""
    path, encs = task
    p = Path(path)
    try:
        data = p.read_bytes()
        compress.write_bytes(p, data, encodings=encs)
        new = compress.resolve(p)
        return (len(data), new.stat().st_size if new else 0)
    except Exception as exc:                  # isolate one bad file, keep going
        return ("ERR", path, "%s: %s" % (type(exc).__name__, exc))


def main():
    assert config.COMPRESS, "config.COMPRESS is off -- would store plain, aborting"
    assert DOWNLOADED.is_dir(), "no downloaded tree at %s" % DOWNLOADED
    print("scanning %s ..." % DOWNLOADED, flush=True)
    tasks = scan()
    print("%d files to compress" % len(tasks), flush=True)
    if not tasks:
        return

    workers = min(32, (os.cpu_count() or 4))
    done = orig_total = new_total = errors = 0
    start = time.time()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for res in pool.map(compress_one, tasks, chunksize=64):
            done += 1
            if res[0] == "ERR":
                errors += 1
                print("ERROR %s -- %s" % (res[1], res[2]), flush=True)
            else:
                orig_total += res[0]
                new_total += res[1]
            if done % 5000 == 0:
                rate = done / (time.time() - start)
                print("  %d/%d  %.0f/s  saved %.2f GB so far"
                      % (done, len(tasks), rate,
                         (orig_total - new_total) / 1e9), flush=True)

    dt = time.time() - start
    print("done: %d compressed, %d errors, %.0fs" % (done - errors, errors, dt))
    print("bytes: %.2f GB -> %.2f GB (saved %.2f GB, %.1f%%)"
          % (orig_total / 1e9, new_total / 1e9, (orig_total - new_total) / 1e9,
             100 * (orig_total - new_total) / orig_total if orig_total else 0))
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
