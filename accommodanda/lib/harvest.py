"""Shared incremental-download core for the source downloaders.

Every vertical that walks a large, deep upstream archive newest-first faces the
same problem: how to stop well short of the full depth on a routine run without
ever *permanently* skipping a document. Four verticals grew four slightly
different answers to that (dv, forarbete, foreskrift, avg/jo), and each had its
own way to silently strand a document -- a crashed run, a ``--limit`` truncation,
a transient per-doc failure or selector rot advancing the watermark past
un-fetched records. This module is the one hardened mechanism they share:

  * :class:`HarvestWatermark` -- the "have we caught up yet" gate, with a
    never-regress date save, a crash-safe ``dirty`` flag, and two independent
    stop signals (a run of consecutive already-downloaded items, or one
    already-downloaded item conclusively older than the watermark).
  * :func:`walk` -- the newest-first download loop over an item stream: it
    drives the watermark's ``begin``/``complete`` lifecycle, applies the stop
    decision, survives a single bad document, and turns any failure into a
    *dirty* store so the next run re-walks the backlog rather than skipping it.
  * :class:`Skip` / :func:`guarded_enumerate` -- an enumeration hole (a flaky
    index page) becomes a recorded Skip that withholds a clean completion,
    instead of aborting the run or being lost.

A vertical supplies its own enumeration (how to list the upstream) and its own
resolve (how to fetch + store one item) as callables, plus an ``item_key`` that
reads the per-item basefile / date / on-disk state the loop needs. The window
sizes (``lookahead_limit``, ``safety_days``) are per-source constructor
parameters -- publication cadence differs, so each call site states its own.
"""

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from .util import Reporter, write_atomic


class HarvestWatermark:
    """The "have we caught up yet" gate for an incremental listing walk over a
    large, deep archive -- lets a walk stop well short of the full depth
    without a blanket "stop at the very first already-known item" rule, which
    a listing that resurfaces an old item (e.g. an "updated" bump) can trip
    prematurely.

    Two independent signals, either enough to stop:
      * ``lookahead_limit`` consecutive already-downloaded items in a row (no
        date info needed) -- a run of hits this long is not a coincidence.
      * ONE item already downloaded whose own date is older than the last
        download's date minus ``safety_days`` -- conclusive on its own, since
        it is unambiguously past the point anything could still be new. A
        *missing* item this old is never grounds to stop on its own -- it is
        a gap to fill, not evidence of having caught up (also resets the
        consecutive counter, since a gap breaks any run of hits).

    Two failure modes the plain gate got wrong, both fixed here:

      * **never-regress date.** ``save(None)`` / ``complete(None, ...)`` keep
        the stored date rather than clobbering it: a run that enumerated no
        dated items (selector rot on the date field, an empty listing) must
        not erase the signal that says how far we have caught up.
      * **dirty flag.** :meth:`begin` marks the store dirty at the start of a
        mutating run; :meth:`complete` clears it only on a clean run (no
        errors). A run left dirty (crashed, ``--limit``-truncated, or a
        per-doc failure) means fresh records may sit *above* un-fetched
        backlog, so the next run must not trust a run of consecutive hits to
        mean "caught up": while the store was dirty at load time,
        :meth:`should_stop` disables the consecutive-hit stop but keeps the
        date-conclusive one (an already-downloaded item conclusively past the
        boundary is still valid evidence). The dirty run walks down to that
        boundary, naturally retrying whatever the previous run stranded --
        self-healing, bounded by ``safety_days`` beyond the failure point.

    Persisted as ``{"last_harvest": "<iso date>|null", "dirty": bool}`` at
    ``filepath`` (an older ``{"last_harvest": ...}`` file loads fine; ``dirty``
    defaults False). ``begin``/``complete`` are the recommended lifecycle;
    ``save`` remains for callers that manage their own."""

    def __init__(self, filepath, lookahead_limit=5, safety_days=14):
        self.filepath = Path(filepath)
        self.lookahead_limit = lookahead_limit
        self.safety_days = safety_days
        self.last_harvest: str | None = None
        self.dirty: bool = False
        if self.filepath.exists():
            data = json.loads(self.filepath.read_text())
            self.last_harvest = data.get("last_harvest")
            self.dirty = bool(data.get("dirty", False))
        # The stop decision keys on the store's state *as loaded*: begin() will
        # mark the file dirty for crash-safety, but that must not disable this
        # run's own consecutive-hit stop -- only a *prior* run's dirtiness does.
        self._dirty_at_load = self.dirty
        self._consecutive = 0

    def get_limit_date(self) -> date | None:
        """The date past which an already-downloaded item is conclusive on its
        own, or None before any download has ever completed."""
        return (date.fromisoformat(self.last_harvest) - timedelta(days=self.safety_days)
                if self.last_harvest else None)

    def should_stop(self, is_downloaded: bool, item_date_str: str | None = None) -> bool:
        limit = self.get_limit_date()
        if item_date_str is not None and limit is not None:
            if date.fromisoformat(item_date_str) < limit:
                if not is_downloaded:
                    self._consecutive = 0
                    return False               # a gap, not evidence of catching up
                return True                    # old and already have it -- conclusive,
                #                                valid even when the store is dirty
        self._consecutive = self._consecutive + 1 if is_downloaded else 0
        if self._dirty_at_load:
            return False                       # backlog may sit above: don't trust a
            #                                    run of hits, walk to the date boundary
        return self._consecutive >= self.lookahead_limit

    def _write(self) -> None:
        write_atomic(self.filepath, json.dumps(
            {"last_harvest": self.last_harvest, "dirty": self.dirty}))

    def save(self, date_str: str | None, log: Callable[[str], Any] = print) -> None:
        """Advance the watermark to ``date_str`` and mark the store clean. A
        None date keeps the stored value (never-regress) and warns -- a run
        that saw no dated items must not erase how far we had caught up."""
        if date_str is None:
            log("  watermark: run observed no dated items -- keeping %s"
                % (self.last_harvest or "no prior date"))
        else:
            self.last_harvest = date_str
        self.dirty = False
        self._write()

    def begin(self) -> None:
        """Mark the store dirty at the start of a mutating run, so a crash or
        truncation before :meth:`complete` leaves the next run to re-walk the
        backlog rather than trust a run of consecutive hits."""
        self.dirty = True
        self._write()

    def complete(self, newest_date_str: str | None, errors: int = 0,
                 log: Callable[[str], Any] = print) -> None:
        """Finish a mutating run: advance the date (never-regress on None) and
        clear the dirty flag ONLY when ``errors == 0``. A non-zero ``errors``
        (a per-doc failure, an enumeration Skip, or a zero-item run) leaves the
        store dirty so the next run walks past the consecutive-hit stop and
        retries whatever was stranded."""
        if newest_date_str is None:
            log("  watermark: run observed no dated items -- keeping %s"
                % (self.last_harvest or "no prior date"))
        else:
            self.last_harvest = newest_date_str
        self.dirty = errors != 0
        self._write()


@dataclass
class Skip:
    """A non-fatal hole in an enumeration. Upstream indexes are flaky -- a
    per-year page 500s, one sitemap of several times out, a 'show all' list is
    briefly down -- so a multi-page enumerator yields this instead of a document
    when it cannot fetch one page but can keep walking the rest. :func:`walk`
    logs it and leaves the store dirty, so the missed page is retried on the
    next run rather than silently lost. (An *expected* empty page -- a year with
    no documents -- is not a Skip; the enumerator just yields nothing for it.)"""
    reason: str


@dataclass
class ItemKey:
    """What :func:`walk` needs to read off one enumerated item to place it: its
    stable ``basefile`` (for ``--only`` matching and logging), whether it is
    already on disk, and its own publication ``date`` (ISO, drives the
    watermark's date-conclusive stop; None when the item carries no date)."""
    basefile: str
    is_downloaded: bool
    date: str | None = None


@dataclass
class WalkResult:
    """The tally of one :func:`walk`: items enumerated, items newly fetched (or
    changed), per-doc errors, enumeration Skips, and the newest item date seen
    (what the watermark advanced to)."""
    seen: int
    new: int
    errors: int
    skips: int
    newest_date: str | None


def guarded_enumerate(items: Iterable[Any], log: Callable[[str], Any] = print) -> Iterator[Any]:
    """Iterate ``items`` so that an exception escaping the enumerator (a
    single-call API or index page that died outright -- the listing endpoint is
    down, returns malformed JSON, 403s) ends the walk with a trailing
    :class:`Skip` instead of aborting the whole run. Multi-page enumerators yield
    their own :class:`Skip` for individual bad pages and keep going; this catches
    whatever they let through. Either way the source is left incomplete (dirty)
    and retried."""
    walker = iter(items)
    while True:
        try:
            item = next(walker)
        except StopIteration:
            return
        except Exception as exc:  # noqa: BLE001 — index endpoint failed: becomes a Skip, the run stays dirty and retries (rule:no-catch-log-continue)
            yield Skip("enumeration aborted: %r" % exc)
            return
        yield item


def walk(items: Iterable[Any], *, resolve: Callable[[Any], object],
         item_key: Callable[[Any], ItemKey | None], watermark: HarvestWatermark,
         full: bool = False, only: str | None = None, limit: int | None = None,
         scope: str = "", count_label: str = "new", total: int | None = None,
         log: Callable[[str], Any] = print, reporter: Reporter | None = None) -> WalkResult:
    """Run the shared newest-first download loop over ``items``.

    ``items`` yields domain items (or :class:`Skip` records for enumeration
    holes); ``item_key`` reads each item's :class:`ItemKey` (or None to ignore a
    non-document item, e.g. a listing hit with no parsable identifier);
    ``resolve`` fetches + stores one item and returns a truthy value when it
    wrote something new/changed (counted into ``new``). ``full`` re-resolves
    items already on disk; ``only`` fetches just the one matching basefile;
    ``limit`` caps the number of new fetches.

    The watermark lifecycle is driven here: unless this is an ``--only`` run,
    :meth:`HarvestWatermark.begin` marks the store dirty up front and
    :meth:`HarvestWatermark.complete` clears it only on a clean, untruncated run
    -- a ``--limit`` truncation, an enumeration Skip, a per-doc error or a
    zero-item run all leave the store dirty so the next run re-walks the
    backlog. Returns a :class:`WalkResult`."""
    backfill = full or watermark.last_harvest is None
    rep = reporter or Reporter()
    seen = new = errors = skips = 0
    newest_date: str | None = None

    if only is None:
        watermark.begin()

    for item in guarded_enumerate(items, log):
        if isinstance(item, Skip):
            skips += 1
            log("  %s enumerate: %s" % (scope, item.reason))
            continue
        key = item_key(item)
        if key is None:
            continue                          # not an enumerable document
        seen += 1

        if only is not None:
            if key.basefile != only:
                continue
            resolve(item)
            new = 1
            break

        if key.date:
            newest_date = key.date if newest_date is None else max(newest_date, key.date)

        if not backfill and watermark.should_stop(key.is_downloaded, key.date):
            break
        if key.is_downloaded and not full:
            continue                          # on disk already; --full re-resolves

        try:
            if resolve(item):
                new += 1
        except Exception as exc:  # noqa: BLE001 — one bad doc must not abort the walk: counted, and the run stays dirty so it is retried (rule:no-catch-log-continue)
            errors += 1
            log("  %s %s: %s" % (scope, key.basefile, exc))
        rep.update(seen, total, scope=scope, **{count_label: new})
        if limit and new >= limit:
            break
    rep.done()

    if only is None:
        truncated = bool(limit) and new >= limit
        if not truncated:
            # a Skip (missed unknown docs), a per-doc error or a zero-item run
            # (a page that loaded but matched nothing, indistinguishable from
            # selector rot) all keep the store dirty for the next run to retry.
            problem = errors > 0 or skips > 0 or seen == 0
            watermark.complete(newest_date, errors=1 if problem else 0, log=log)
        # a truncated run just leaves the dirty flag begin() set -- the un-fetched
        # backlog below the cap is retried (past the consecutive-hit stop) next run

    return WalkResult(seen, new, errors, skips, newest_date)
