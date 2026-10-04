"""SQLite's own page cache for a single-process batch pass over the catalog or
the unit store, sized from the memory the host can spare.

The default 2 MB cache re-reads the same pages again and again: the unit store
keeps a document's units on unrelated pages, and with that cache an update of
11,138 changed documents took 51,662 s on production's disk (2026-10-01).
A larger cache is memory taken
from the OS page cache, which is also what keeps the OpenSearch index fast. So
the cache is the smallest of:

* the database file's size -- a cache never needs more than the file;
* the host's available memory, less a margin -- and on a host that serves
  readers (config.yml's `search_business_hours`), less the OpenSearch
  index's size during business hours, when search is what readers wait on;
* the room left under this process's cgroup memory limit, less a margin --
  past the limit the kernel kills the container, the server included.

Only for one connection in one process: never a serving connection or a pool
worker's, since the cache grows to its cap as a connection reads pages. A pass
that also runs workers reserves their memory first. A long pass sizes the cache
again when `search_hours` changes, and only then: available memory no longer
counts what the cache already holds, so sizing it again at the same hour would
shrink it for nothing. Into business hours that errs small, which is the side
search is on.

`prewarm` reads the database file once, in order, into the OS page cache
before a pass: on prod's disk that runs at 53 MB/s, where the pass's own
random reads run at 51 pages a second. A pass calls it once, at its start. It
is skipped in business hours (the page cache is the index's then) and when the
file does not fit in the memory the cache may take.

In business hours the index size comes from OpenSearch itself. A cluster that
does not answer stops the pass: the cache cannot leave room for an index it
cannot measure, and the unit store's `pending` mark makes the next run resume."""

import datetime
import sqlite3
import time
from pathlib import Path
from zoneinfo import ZoneInfo

from .. import config
from . import util
from .search import SearchIndex

STOCKHOLM = ZoneInfo("Europe/Stockholm")
BUSINESS_HOURS = range(8, 18)          # Stockholm hours, Monday to Friday
MARGIN = 2 << 30                       # bytes left for everything else
MIN_CACHE = 64 << 20                   # what a pass gets when memory is short


def meminfo(field):
    """`field` of /proc/meminfo, in bytes."""
    for line in Path("/proc/meminfo").read_text().splitlines():
        name, value = line.split(":", 1)
        if name == field:
            return int(value.split()[0]) * 1024
    raise ValueError("/proc/meminfo has no %s" % field)


def _cgroup_room():
    """Bytes this process's cgroup can still take before its limit, or None
    when the cgroup sets no limit. Page cache counts toward the limit too, but
    the kernel reclaims it first, so only anonymous memory is subtracted."""
    lines = Path("/proc/self/cgroup").read_text().splitlines()
    assert len(lines) == 1 and lines[0].startswith("0::"), \
        "sqlcache reads the cgroup v2 memory limit; /proc/self/cgroup says %r" % lines
    group = Path("/sys/fs/cgroup") / lines[0][3:].lstrip("/")
    assert (group / "memory.max").exists(), \
        "no memory.max in %s: the process sits in a root cgroup" % group
    limit = (group / "memory.max").read_text().strip()
    if limit == "max":
        return None
    stat = dict(line.split() for line in (group / "memory.stat").read_text().splitlines())
    return int(limit) - int(stat["anon"])


def business_hours(now=None):
    now = now or datetime.datetime.now(STOCKHOLM)
    return now.weekday() < 5 and now.hour in BUSINESS_HOURS


def search_hours():
    """Whether the OpenSearch index has first claim on memory now."""
    return config.SEARCH_BUSINESS_HOURS and business_hours()


def _spare(reserve):
    """Bytes a batch pass may fill now: available memory and the cgroup room,
    less `reserve` (the memory the pass's own workers will take), the
    OpenSearch index in search hours, and the margin."""
    spare = meminfo("MemAvailable") - reserve
    if search_hours():
        spare -= SearchIndex().store_bytes()
    room = _cgroup_room()
    if room is not None:
        spare = min(spare, room - reserve)
    return spare - MARGIN


def cache_bytes(db_path, reserve=0):
    """The batch cache for the database at `db_path`, in bytes."""
    need = Path(db_path).stat().st_size
    if need <= MIN_CACHE:
        return need
    return max(MIN_CACHE, min(need, _spare(reserve)))


PREWARM_CHUNK = 8 << 20


def prewarm(db_path, reserve=0):
    """Read the database at `db_path` front to back, so the OS page cache holds
    it for the random reads of the pass that follows."""
    path = Path(db_path)
    size = path.stat().st_size
    if size <= MIN_CACHE or search_hours() or size > _spare(reserve):
        return
    t0 = time.perf_counter()
    buf = bytearray(PREWARM_CHUNK)
    with open(path, "rb", buffering=0) as f:
        while f.readinto(buf):
            pass
    util.write("sqlcache: read %s into the page cache (%.1f GB, %.0f s)"
               % (path.name, size / 1e9, time.perf_counter() - t0), err=True)


def batch_cache(con: sqlite3.Connection, db_path, reserve=0) -> sqlite3.Connection:
    """`con`, the connection to `db_path`, with the batch page cache."""
    con.execute("PRAGMA cache_size=-%d" % (cache_bytes(db_path, reserve) // 1024))
    return con
