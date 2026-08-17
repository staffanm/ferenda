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
    Whether to stop short at all is the ``watermark`` **policy**: pass one for a
    deep archive, or ``None`` for a source whose upstream is a short, complete
    listing (edpb, rs) -- there is no depth to stop short of and no backlog to
    strand, so the walk simply visits every entry every run.
  * :class:`Skip` / :func:`guarded_enumerate` -- an enumeration hole (a flaky
    index page) becomes a recorded Skip that withholds a clean completion,
    instead of aborting the run or being lost.
  * :func:`record_unchanged` / :func:`write_record` / :func:`store_record` --
    the harvest record on disk. Rewriting a record that has not changed would
    re-stale the whole downstream parse for nothing, so every downloader
    compares before writing; this is that comparison, once.
  * :func:`pdf_path` / :func:`page_path` / :func:`select_pending` /
    :func:`walk_records` -- the whole download half of a source whose upstream
    is a short, complete listing of records that each name one document (edpb,
    rs): where the record and its document go, how ``--only`` narrows the
    listing, and the walk that stores both.

A vertical supplies its own enumeration (how to list the upstream) and its own
resolve (how to fetch + store one item) as callables, plus an ``item_key`` that
reads the per-item basefile / date / on-disk state the loop needs. The window
sizes (``lookahead_limit``, ``safety_days``) are per-source constructor
parameters -- publication cadence differs, so each call site states its own.
"""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Container, Iterable, Iterator, Mapping, Sequence

from . import compress
from .util import (
    Reporter,
    basefile_slug,
    document_extension,
    record_path,
    write_atomic,
)


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
    changed), per-doc errors, and enumeration Skips."""
    seen: int
    new: int
    errors: int
    skips: int


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
         item_key: Callable[[Any], ItemKey | None],
         watermark: HarvestWatermark | None,
         full: bool = False, only: str | None = None, limit: int | None = None,
         budget: float | None = None,
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

    ``budget`` (seconds) is the sanity trip for the routine case: an
    *incremental* run still walking past it stops as if ``--limit``-truncated
    (loudly, store left dirty, next run resumes) instead of grinding for hours
    against a sick upstream. Backfills (``--full`` or a first harvest) are
    exempt -- they legitimately take long. The check runs between items; to
    bound the time a single blocked fetch can burn, the caller also sets the
    session ``deadline`` that ``lib.net.request`` honours.

    ``watermark`` is the stop policy. With one, the lifecycle is driven here:
    unless this is an ``--only`` run, :meth:`HarvestWatermark.begin` marks the
    store dirty up front and :meth:`HarvestWatermark.complete` clears it only on
    a clean, untruncated run -- a ``--limit`` truncation, an enumeration Skip, a
    per-doc error or a zero-item run all leave the store dirty so the next run
    re-walks the backlog.

    ``watermark=None`` says this source's upstream is a *complete listing*: a
    short index, walked whole on every run. There is then nothing to stop short
    of and no backlog to strand, so no watermark is kept and no early stop is
    applied -- the walk visits every entry, and ``item_key`` decides per item
    whether there is anything to do. For those sources ``is_downloaded`` is the
    interesting bit: it should report whether the *record on disk is already
    current* (:func:`record_unchanged`), not merely whether some file exists, so
    an upstream metadata edit is picked up while an unchanged entry costs
    nothing. Returns a :class:`WalkResult`."""
    backfill = full or watermark is None or watermark.last_harvest is None
    rep = reporter or Reporter()
    seen = new = errors = skips = 0
    newest_date: str | None = None
    start = time.monotonic()
    tripped = False

    if only is None and watermark is not None:
        watermark.begin()

    for item in guarded_enumerate(items, log):
        if budget is not None and not backfill and only is None \
                and time.monotonic() - start > budget:
            tripped = True
            log("  %s: sanity trip -- incremental walk still running after %.0fs "
                "(%d seen, %d new); stopping, the store stays dirty and the next "
                "run resumes" % (scope, budget, seen, new))
            break
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
            break                             # (watermark is not None here:
                                              #  backfill is True without one)
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

    if only is None and watermark is not None:
        # a budget trip is a truncation: the un-walked backlog below must not
        # let complete() advance the date or clear begin()'s dirty flag
        truncated = (bool(limit) and new >= limit) or tripped
        if not truncated:
            # a Skip (missed unknown docs), a per-doc error or a zero-item run
            # (a page that loaded but matched nothing, indistinguishable from
            # selector rot) all keep the store dirty for the next run to retry.
            problem = errors > 0 or skips > 0 or seen == 0
            watermark.complete(newest_date, errors=1 if problem else 0, log=log)
        # a truncated run just leaves the dirty flag begin() set -- the un-fetched
        # backlog below the cap is retried (past the consecutive-hit stop) next run

    return WalkResult(seen, new, errors, skips)


# --------------------------------------------------------------------------
# the harvest record on disk
# --------------------------------------------------------------------------

def record_unchanged(path: Path, record: dict, *companions: Path) -> bool:
    """True when `path` already holds exactly `record` **and** every companion
    file is present -- the downloader's "nothing to do here" test.

    The companions matter: a stored record is the assertion that the document
    behind it is on disk, so a record that matches while its PDF or landing page
    is missing is *not* current, and the item must be re-resolved. Callers that
    fetch before overwriting (jk, arn) use this as the predicate and
    :func:`write_record` after the fetch succeeds; callers with nothing else to
    fetch use :func:`store_record`, which is the two together.
    """
    return (compress.exists(path)
            and all(compress.exists(c) for c in companions)
            and compress.read_json(path) == record)


def write_record(path: Path, record: dict) -> None:
    """Store one harvest record as pretty-printed, non-ASCII-escaped JSON --
    the on-disk form every source's parse stage reads back."""
    compress.write_download(path, json.dumps(record, ensure_ascii=False,
                                             indent=2))


def store_record(path: Path, record: dict, *companions: Path,
                 full: bool = False) -> bool:
    """Write `record` to `path` unless it is already stored there unchanged (and
    every companion is present). Returns True when it was written. `full`
    rewrites unconditionally -- the `--force` re-verification path."""
    if not full and record_unchanged(path, record, *companions):
        return False
    write_record(path, record)
    return True


# --------------------------------------------------------------------------
# a complete listing of records, each naming one document
# --------------------------------------------------------------------------
#
# The shape a source has when its upstream is a short, fully enumerable listing
# and every entry is one metadata record plus one document: the basefile's first
# segment names the store subdirectory it is filed under ("fk/2025:01" ->
# ``<root>/fk/``), and the walk visits every entry every run, storing what moved.
#
# The document is a PDF for most of these sources and a web page for the ones
# that publish their documents as pages (rs's Skatteverket), so where it goes
# and what it must be are the two parameters ``walk_records`` takes.

#: one listing entry: the record to store, and how to fetch the document it
#: names (None where it names none)
Pending = tuple[dict, Callable[[], bytes | str] | None]


def pdf_path(root: Path | str, basefile: str) -> Path:
    """The document PDF beside its harvest record ("fk/2025:01" ->
    ``<root>/fk/fk-2025-01.pdf``)."""
    return (Path(root) / basefile.split("/", 1)[0]
            / (basefile_slug(basefile) + ".pdf"))


def page_path(root: Path | str, basefile: str) -> Path:
    """The document's own web page beside its harvest record ("skv/8-492402" ->
    ``<root>/skv/skv-8-492402.html``), for a source whose publisher issues the
    document *as* a page rather than as a PDF."""
    return (Path(root) / basefile.split("/", 1)[0]
            / (basefile_slug(basefile) + ".html"))


def verify_pdf(data: bytes | str) -> None:
    """Reject a body that is not a PDF -- a WAF challenge or an error page
    served 200 under a ``.pdf`` URL. The default ``walk_records`` check."""
    # a source whose body() hands back text is this package's own bug, and must
    # not be logged as the upstream serving the wrong thing
    assert isinstance(data, bytes), "a PDF body must be bytes, not text"
    if document_extension(data) != ".pdf":
        raise ValueError("served a non-PDF body; record left unwritten")


def _record_json(root: Path | str, basefile: str) -> Path:
    """The harvest record beside that document ("fk/2025:01" ->
    ``<root>/fk/fk-2025-01.json``)."""
    return record_path(root, basefile.split("/", 1)[0], basefile)


def select_pending(pending: list[Pending], only: str | None,
                   missing: str) -> list[Pending]:
    """The one ``(record, body)`` pair `only` names by basefile, or every pair.
    `missing` is the source's own message for "the listing carries no such
    document", %-formatted with `only` -- an `--only` that names nothing is a
    typo or a document that has gone, and either way the run has nothing to do
    and says which."""
    if not only:
        return pending
    picked = [item for item in pending if item[0]["basefile"] == only]
    if not picked:
        # user-typed --only: load-bearing validation raises, never asserts --
        # under python -O an assert here made the run a silent "0 seen" no-op
        raise ValueError(missing % only)
    return picked


def walk_records(root: Path | str, pending: Iterable[Pending], *,
                 delay: float, full: bool = False, limit: int | None = None,
                 scope: str = "", total: int | None = None,
                 document: Callable[[Path | str, str], Path] = pdf_path,
                 verify: Callable[[bytes | str], None] = verify_pdf,
                 refetch_when_changed: bool = False) -> tuple[int, int]:
    """Store a listing's records and the documents they name, through
    :func:`walk` with **no watermark**: the listing is enumerated whole on every
    run, so there is no depth to stop short of. Returns ``(seen, new)``.

    `pending` items are ``(record, body)``, where `body` returns the document --
    one HTTP GET, a ZIP fetch and a member extraction, a browser navigation,
    whatever the source's route is -- or is None for a record that names no
    document at all (a register entry: a repealed statement kept in a
    förteckning with its text withdrawn).

    `document` says where one basefile's document is stored and `verify` what it
    must be: :func:`pdf_path` / :func:`verify_pdf` for a publisher that issues
    PDFs, :func:`page_path` and the source's own check for one that issues web
    pages.

    `refetch_when_changed` says whether the *document* can change while keeping
    its identity. For a publisher that issues PDFs it cannot -- a new document
    gets a new number -- so an already-stored file is left alone and a listing
    edit costs one record write. A publisher that issues web pages revises them
    in place: Skatteverket adds "Detta ställningstagande ska inte längre
    tillämpas" to the page it has withdrawn, and the register entry moves at the
    same moment. Without this flag the record would take the new date and
    currency while the stored page kept the superseded text.

    `pending` is normally the whole listing as a list, and the progress line
    counts against its length. A source that has to be able to *stop* mid-walk
    -- a browser-transported one whose upstream starts refusing -- passes a
    generator instead and states the `total` itself, so the walk simply runs out
    of entries where the source decided to stop.

    ``is_downloaded`` is reported as "this record is already current on disk,
    document and all" rather than "some file exists", so an unchanged entry
    costs nothing while an upstream retitling *or a vanished document* is still
    picked up.

    A document that cannot be stored raises, so :func:`walk` counts and logs it
    and the record is **not** written -- a stored record is the assertion that
    the document behind it is on disk, which is what lets a parse read an absent
    one as "the publisher published none" rather than "the fetch broke". The
    previous good record stays and the next run retries.

    ``limit`` is :func:`walk`'s: it caps documents actually *fetched or
    changed*, not entries looked at. On a steady-state run where nothing moved,
    ``--limit`` therefore walks the whole listing and fetches nothing, which is
    the cheap case anyway."""
    def item_key(item: Pending) -> ItemKey:
        record, body = item
        bf = record["basefile"]
        return ItemKey(bf, record_unchanged(
            _record_json(root, bf), record,
            *((document(root, bf),) if body else ())))

    def resolve(item: Pending) -> bool:
        record, body = item
        bf = record["basefile"]
        # resolve only runs for an entry `item_key` called not-current, so a
        # mutable document is refetched exactly when its record moved
        if body and (full or refetch_when_changed
                     or not compress.exists(document(root, bf))):
            data = body()
            time.sleep(delay)
            verify(data)
            compress.write_download(document(root, bf), data)
        # no companion here: a record that has not changed is not rewritten
        # just because its document was refetched above
        return store_record(_record_json(root, bf), record, full=full)

    if total is None:
        # a generator listing has no length to count the progress line against,
        # and losing the ETA silently is worse than saying so
        assert isinstance(pending, list), \
            "a %s listing that is not a list must state its own total" % scope
        total = len(pending)
    result = walk(pending, resolve=resolve, item_key=item_key, watermark=None,
                  full=full, limit=limit, scope=scope, count_label="changed",
                  total=total)
    return result.seen, result.new


# --------------------------------------------------------------------------
# the per-scope entry point
# --------------------------------------------------------------------------

def fan_out(scopes: Sequence[str], work: Callable[[str, Callable[[str], None]],
                                                  tuple[int, int]],
            *, jobs: int = 1, serial: Container[str] = (),
            label: str = "harvest", log: Callable[[str], None] = print,
            ) -> dict[str, tuple[int, int]]:
    """Run `work(scope, log)` once per scope, concurrently when `jobs > 1`, and
    return ``{scope: (seen, new)}``.

    A source's scopes are separate upstreams on separate hosts, so fanning them
    out is polite -- each worker still paces its own host -- and the wall time
    drops from the sum of every site to roughly the slowest single one.

    Concurrent per-scope progress lines would collide, so each worker writes into
    its own buffer and the coordinator prints that buffer, then the scope's
    summary, above one aggregate '(done/total)' line. The line names what is
    still in flight, so a slow scope reads as working rather than as a frozen
    counter. Workers should report through a `NullReporter`; the caller's `work`
    decides that, since only it knows what its runner takes.

    `serial` names scopes that must not run concurrently with each other -- the
    browser-driven ones, where `DetachedChrome` points a *process-global* DISPLAY
    at its own Xvfb and Playwright's sync API is single-threaded, so two at once
    corrupt each other's display and stall. They still overlap the HTTP scopes,
    which are the overwhelming majority.

    Sequential whenever there is nothing to gain (`jobs <= 1`, one scope), and
    then each scope keeps its own live progress line."""
    scopes = list(scopes)
    elapsed: dict[str, float] = {}
    totals: dict[str, tuple[int, int]] = {}

    def timed(scope, into):
        started = time.monotonic()
        try:
            return work(scope, into)
        finally:
            elapsed[scope] = time.monotonic() - started

    if jobs <= 1 or len(scopes) <= 1:
        for scope in scopes:
            totals[scope] = timed(scope, log)
            log("%s %s: %d seen, %d new" % (label, scope, *totals[scope]))
        _log_scope_times(elapsed, label, log)
        return totals

    buffers: dict[str, list[str]] = {scope: [] for scope in scopes}
    running: set[str] = set()
    state = threading.Lock()          # guards `running`
    serial_lock = threading.Lock()

    def worker(scope):
        with state:
            running.add(scope)
        try:
            gate = serial_lock if scope in serial else nullcontext()
            with gate:
                return timed(scope, buffers[scope].append)
        finally:
            with state:
                running.discard(scope)

    rep = Reporter()
    done = new_total = 0
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = {pool.submit(worker, scope): scope for scope in scopes}
        for future in as_completed(futures):
            scope = futures[future]
            totals[scope] = seen, new = future.result()
            new_total += new
            done += 1
            rep.clear()               # lift the live line off the row
            for line in buffers[scope]:
                log(line)
            log("%s %s: %d seen, %d new" % (label, scope, seen, new))
            with state:
                busy = sorted(running)
            rep.update(done, len(scopes), scope=label, new=new_total,
                       note="  [running: %s]" % ", ".join(busy) if busy else "")
    rep.done()
    _log_scope_times(elapsed, label, log)
    return totals


def _log_scope_times(elapsed, label, log):
    """Which upstream actually cost the time. The run ledger measures a whole
    source (`avg` download is 553 s), which is the wrong grain: these scopes are
    separate hosts, so a fan-out's gain is bounded by the slowest one alone, and
    nothing else records which that is."""
    if len(elapsed) > 1:
        log("%s scope times: %s" % (
            label, ", ".join("%s %.1fs" % (scope, secs) for scope, secs
                             in sorted(elapsed.items(), key=lambda kv: -kv[1]))))


def dispatch_scopes(root: Path | str, scopes: Iterable[str] | None,
                    runners: Mapping[str, Callable[..., tuple[int, int]]],
                    default: Iterable[str], *, full: bool = False,
                    only: str | None = None, limit: int | None = None,
                    delay: float = 0.5, jobs: int = 1,
                    serial: Container[str] = (), label: str = "harvest",
                    log: Callable[[str], None] = print,
                    ) -> dict[str, tuple[int, int]]:
    """Run one harvest per named scope -- all of `default` when `scopes` is
    None -- and return ``{scope: (seen, new)}``, the shape `build` expects of a
    multi-scope source's ``sync``.

    A source's scopes are its organs, agencies or series: separate upstreams
    that share nothing but the entry point. ``only`` is a basefile
    ("fk/2025:01"), so it names its own scope -- it is passed to the runner it
    belongs to and withheld from every other, which is what lets a whole-source
    run narrow to one document without every runner having to recognise a
    basefile that is not its own."""
    run = list(scopes or default)
    # an --only whose scope is not in the run would otherwise be withheld from
    # every runner -- and the run silently harvests everything BUT the one
    # document asked for
    if only and not any(only.startswith(scope + "/") for scope in run):
        raise ValueError("--only %s names no scope in this run (%s)"
                         % (only, ", ".join(run)))
    for scope in run:
        if scope not in runners:      # user-typed scope: raise, never assert
            raise ValueError("no harvest scope %r" % scope)
    # `--only` narrows to one document, so there is one scope to run and nothing
    # to fan out; the pool would only cost it its own live progress line.
    def one(scope, _log):
        return runners[scope](
            str(root), full=full, limit=limit, delay=delay,
            only=only if only and only.startswith(scope + "/") else None)
    return fan_out(run, one, jobs=1 if only else jobs, serial=serial,
                   label=label, log=log)

