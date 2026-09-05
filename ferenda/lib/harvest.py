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
  * :func:`paginated` -- the walk over a ``?page=N`` listing view, which ends
    on a page that names no row the walk has not seen. A Drupal view serves
    its last page again past its end, so stopping on an *empty* page never
    stops (six agency libraries in guidance grew this loop).
  * :func:`record_unchanged` / :func:`write_record` / :func:`store_record` --
    the harvest record on disk. Rewriting a record that has not changed would
    re-stale the whole downstream parse for nothing, so every downloader
    compares before writing; this is that comparison, once.
  * :func:`flat_path` / :func:`select_one` -- the flat store the treaty and
    case-law sources keep (the file name is the basefile), and the ``--only``
    guard over the enumeration such a source has just read.
  * :func:`pdf_path` / :func:`page_path` / :func:`select_pending` /
    :func:`walk_records` -- the whole download half of a source whose upstream
    is a short, complete listing of records that each name one document (edpb,
    rs): where the record and its document go, how ``--only`` narrows the
    listing, and the walk that stores both.
  * :func:`fetch_worklist` -- the repair pass over an enumerated work-list
    (the bodies a harvested record still lacks), on one progress line.
  * :func:`issue_walk` -- the whole download half of a source published in
    *issues*: a journal's archive names its issues, each issue names its
    articles, and the walk stops once the newest issues are on disk in full.
  * :func:`fan_out` / :func:`dispatch_scopes` -- one harvest per scope of a
    multi-scope source (its agencies, organs or series), concurrently across
    the separate hosts, each runner pacing its own. With ``strict=False`` a
    scope that fails is reported and re-run alone instead of taking the whole
    run down (lawreview's nine journals run this way).

A vertical supplies its own enumeration (how to list the upstream) and its own
resolve (how to fetch + store one item) as callables, plus an ``item_key`` that
reads the per-item basefile / date / on-disk state the loop needs. The window
sizes (``lookahead_limit``, ``safety_days``) are per-source constructor
parameters -- publication cadence differs, so each call site states its own.
"""

import json
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import (
    Any,
    Callable,
    Container,
    Hashable,
    Iterable,
    Iterator,
    Mapping,
    Sequence,
)

from . import compress, net
from .util import (
    Reporter,
    approximate_date,
    basefile_slug,
    confine,
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
    watermark's date-conclusive stop; None when the item carries no date).

    ``provisional`` splits "on disk" from "evidence of having caught up", for
    an upstream that lists a document before it publishes it. Such a
    placeholder record is current -- the walk must not re-fetch it -- but it is
    no proof the walk has reached the caught-up depth, because its date is the
    *planned* one and can post-date documents published since the last harvest
    (riksdagen lists a betänkande's planned debate before the printed report
    exists). ``is_downloaded`` therefore stays "on disk AND conclusive": it
    alone feeds the watermark gate, while either bit means "do not fetch"."""
    basefile: str
    is_downloaded: bool
    date: str | None = None
    provisional: bool = False


@dataclass
class WalkResult:
    """The tally of one :func:`walk`: items enumerated, items newly fetched (or
    changed), per-doc errors, enumeration Skips, and the newest date the walk
    saw (the watermark date it saved, or the one a caller that drives its own
    watermark over several walks needs back)."""
    seen: int
    new: int
    errors: int
    skips: int
    newest_date: str | None = None


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
         full: bool = False, deep: bool = False, only: str | None = None,
         limit: int | None = None, budget: float | None = None,
         dates_watermark: Callable[[Any], bool] = lambda item: True,
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

    ``deep`` walks the whole listing without re-resolving anything -- what a
    source means by ``--full`` when its documents never change once published
    and its listing runs to tens of thousands of them (forarbete): the point is
    to reach everything the incremental stop has been skipping past, not to
    fetch the corpus again. ``full`` implies it.

    ``dates_watermark`` says which items may advance the watermark date, which
    is otherwise the newest date any item carried. An upstream that lists a
    document *before* it publishes it dates that entry by its planned debate,
    in the future: saving that as "how far we have caught up" would erode the
    safety margin, so the source passes a predicate that names the published
    items (see :class:`ItemKey`'s ``provisional`` for the same split applied to
    the stop signal).

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
    backfill = full or deep or watermark is None or watermark.last_harvest is None
    rep = reporter or Reporter()
    # How far back this walk means to go, on the live line. A download has no
    # staleness scan to report -- nothing on disk decides what it fetches --
    # so the watermark is the one thing that says where it stops. A complete
    # listing (`watermark=None`) has no boundary to name and says nothing.
    if watermark is None:
        since = ""
    elif full or deep:
        since = "  (full sweep)"
    elif watermark.last_harvest is None:
        since = "  (first harvest)"
    else:
        since = "  (from %s)" % watermark.last_harvest
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

        if key.date and dates_watermark(item):
            newest_date = key.date if newest_date is None else max(newest_date, key.date)

        if not backfill and watermark.should_stop(key.is_downloaded, key.date):
            break                             # (watermark is not None here:
                                              #  backfill is True without one)
        if (key.is_downloaded or key.provisional) and not full:
            continue                          # on disk already; --full re-resolves

        try:
            if resolve(item):
                new += 1
        except Exception as exc:  # noqa: BLE001 — one bad doc must not abort the walk: counted, and the run stays dirty so it is retried (rule:no-catch-log-continue)
            errors += 1
            log("  %s %s: %s" % (scope, key.basefile, exc))
        rep.update(seen, total, scope=scope, note=since, **{count_label: new})
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

    return WalkResult(seen, new, errors, skips, newest_date)


# --------------------------------------------------------------------------
# a paged listing view
# --------------------------------------------------------------------------

def paginated(fetch_page: Callable[[int], str], rows: Callable[[str], list],
              key: Callable[[Any], Hashable] = lambda r: r, *,
              cap: int, what: str = "listing") -> tuple[list, int]:
    """Walk a ``?page=N`` view until a page names no row the walk has not seen
    -- never until a page is empty: a Drupal pager serves its last page again.

    Returns ``(every row in listing order, how many pages were fetched)``. The
    stop page counts: the walk had to read it to learn it was the end.

    A view that stopped on emptiness would loop instead of ending, since past
    its last row the site keeps serving the view shell rather than a 404
    (measured on one agency's library: 2,006 rows reported for 584 documents).
    The same rule also absorbs the other way a Drupal view repeats itself --
    rows shifting between pages while the walk runs, because the view sorts by
    publication date and the agency inserts into it.

    `rows` reads one page body into its rows and `key` reads one row's
    identity, defaulting to the row itself for a listing whose rows are their
    own URLs. A row already seen is dropped, so a shifted row is not filed
    twice.

    `cap` is required: an un-capped walk over a pager that never repeats is a
    hang, so the walk raises after `cap` pages. `what` is the source's own noun
    for the listing, for that message."""
    found: list = []
    seen: set[Hashable] = set()
    for page in range(cap):
        fresh = [row for row in rows(fetch_page(page)) if key(row) not in seen]
        if not fresh:
            return found, page + 1
        seen.update(key(row) for row in fresh)
        found.extend(fresh)
    # a pager that never repeats is a changed site, not a large corpus, and the
    # walk must say so rather than crawl on (rule:errors-drive-retry-use-raise:
    # under -O an assert here would let the harvest run unbounded)
    raise ValueError("the %s pager still named new rows after %d pages "
                     "(%d so far) -- it no longer terminates"
                     % (what, cap, len(found)))


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


def stored_index(directory: Path, key: str, value: Callable[[dict], Any],
                 document: Callable[[dict], Path] | None = None) -> dict[Any, Any]:
    """``{record[key]: value(record)}`` over the records already stored in
    `directory` -- what a harvest asks before it fetches anything, so a document
    it has already read is not read again.

    Records that carry no `key` are skipped, which is how a source with more
    than one kind of record picks out the ones this index is about. The listing
    is `compress.glob`, so a record stored as ``.json.br`` is seen like any
    other (rule:second-use-goes-to-lib: acer, edps, eiopa, esma and rs each had
    this loop).

    `document` names one record's document file, and drops the record when that
    file is not on disk. A source whose record is only an assertion *about* a
    document (easa: the record says which annex a leaf holds, the annex PDF is
    the document) must not skip a leaf whose PDF never stored -- the run would
    report it known and never fetch it again."""
    return {record[key]: value(record)
            for path in sorted(compress.glob(directory, "*.json"))
            if (record := compress.read_json(path)).get(key)
            and (document is None or compress.exists(document(record)))}


def document_item_key(record: dict, record_file: Path, *documents: Path,
                      date: str | None = None) -> "ItemKey":
    """The watermark walkers' shared ``item_key``: an item is current only
    when its stored record matches `record` and every named document file is
    on disk. `documents` is empty for a record that names no document (a
    print-only article), whose record alone is then the state."""
    return ItemKey(record["basefile"],
                   record_unchanged(record_file, record, *documents),
                   date=date)


def resolve_document(record: dict, record_file: Path, document_file: Path,
                     fetch: Callable[[], bytes | str] | None,
                     verify: Callable[[bytes | str], None], *,
                     full: bool = False, delay: float = 0.0) -> bool:
    """The watermark walkers' shared ``resolve``: fetch, verify and store the
    document when it is missing or the run is full, then store the record.
    `fetch` is None for a record that names no document. The document is
    re-read only when it is missing (`walk_records` without
    `refetch_when_changed`: these publications revise nothing in place), and
    a repaired document under an unchanged record counts as nothing new, the
    `walk_records` rule."""
    if fetch is not None and (full or not compress.exists(document_file)):
        data = fetch()
        time.sleep(delay)
        verify(data)
        compress.write_download(document_file, data)
    return store_record(record_file, record, full=full)


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


def flat_path(root: Path | str, basefile: str, suffix: str = ".json") -> Path:
    """One file in a *flat* harvest store, where the file name IS the basefile
    ("001-58876" -> ``<root>/001-58876.json``).

    The treaty and case-law sources (coe, icrc, icc, icj, untc, hudoc) file
    every document of theirs in one directory under an identifier the publisher
    already made unique -- an item id, a treaty number, a document number -- so
    there is no subdirectory to place it in and nothing to slug. That is what
    :func:`compress.list_stems` reads back, and what makes `list_basefiles` a
    directory listing rather than a walk that opens every record."""
    return Path(root) / confine(Path(basefile + suffix), basefile, str(root))


def select_one(records: Sequence[Any], key_of: Callable[[Any], Any],
               only: Any, missing: str) -> Any:
    """The one record `only` names by `key_of`, for a source whose ``--only``
    picks out of an enumeration it has just read.

    `missing` is the source's own message for "the listing carries no such
    document", %-formatted with `only` -- an ``--only`` that names nothing is a
    typo or a document that has gone, and either way the run has nothing to do
    and says which. The caller normalises `only` into the form `key_of`
    returns, so the message names the identifier actually looked for."""
    record = next((record for record in records if key_of(record) == only),
                  None)
    if record is None:
        # user-typed --only: load-bearing validation raises, never asserts --
        # under python -O an assert here made the run a silent "0 seen" no-op
        raise ValueError(missing % only)
    return record


def pdf_path(root: Path | str, basefile: str) -> Path:
    """The document PDF beside its harvest record ("fk/2025:01" ->
    ``<root>/fk/fk-2025-01.pdf``)."""
    return Path(root) / confine(Path(basefile.split("/", 1)[0])
                                / (basefile_slug(basefile) + ".pdf"),
                                basefile, str(root))


def page_path(root: Path | str, basefile: str) -> Path:
    """The document's own web page beside its harvest record ("skv/8-492402" ->
    ``<root>/skv/skv-8-492402.html``), for a source whose publisher issues the
    document *as* a page rather than as a PDF."""
    return Path(root) / confine(Path(basefile.split("/", 1)[0])
                                / (basefile_slug(basefile) + ".html"),
                                basefile, str(root))


def page_verifier(marker: str,
                  what: str = "article") -> Callable[[bytes | str], None]:
    """A document check for a source whose publisher issues its documents as
    web pages: the served body must carry `marker`, the one string only the
    real document's layout sets -- a WAF challenge, an error page and a
    listing served in the document's place all lack it. (The default
    ``walk_records``/``resolve_document`` check is PDF-shaped; see
    :func:`verify_pdf`.) `what` is the source's own noun for the document,
    for the error line."""
    def verify(data: bytes | str) -> None:
        assert isinstance(data, str), "a page body must be text, not bytes"
        if marker not in data:
            raise ValueError("served a non-%s page; record left unwritten"
                             % what)
    return verify


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


def walk_records(root: Path | str, pending: Iterable[Pending | Skip], *,
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
    förteckning with its text withdrawn). An item may instead be a
    :class:`Skip`, an enumeration hole the source met and wants recorded
    (a listing page the upstream failed to serve) -- ``walk`` logs it and
    keeps walking.

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
# a fully enumerated work-list
# --------------------------------------------------------------------------

def fetch_worklist(worklist: Sequence[Any], fetch: Callable[[Any], object], *,
                   scope: str, limit: int | None = None,
                   count_label: str = "fetched",
                   reporter: Reporter | None = None) -> tuple[int, int]:
    """Fetch every item of an already enumerated `worklist`, counting what it
    stored, on one live progress line. Returns ``(seen, fetched)``.

    This is the *repair* pass, not the harvest: the documents are known and on
    the catalog already, and what is missing is their bodies (the KB scans
    behind the pre-1971 propositioner, the SOU facsimiles, the riksdagen prop
    bodies). There is nothing to enumerate incrementally and no watermark to
    keep -- an item whose body is on disk is simply not on the list, which is
    what makes an interrupted run resumable by re-running it.

    The list is materialised up front so the line carries a real total and an
    ETA (rule:one-line-progress). `fetch` stores one item and returns a truthy
    value when it wrote something; `limit` stops the run after that many items
    actually fetched (a test slice), leaving the rest for the next run."""
    rep = reporter or Reporter()
    seen = fetched = 0
    for item in worklist:
        seen += 1
        if fetch(item):
            fetched += 1
        rep.update(seen, len(worklist), scope=scope, **{count_label: fetched})
        if limit and fetched >= limit:
            break
    rep.done()
    return seen, fetched


# --------------------------------------------------------------------------
# an archive published in issues
# --------------------------------------------------------------------------

def issue_walk(root: Path | str, scope: str, issues: Iterable[Any],
               records: Callable[[Any], Iterable[Any]], *,
               body: Callable[[dict], Callable[[], bytes | str] | None],
               missing: str, delay: float, full: bool = False,
               only: str | None = None, limit: int | None = None,
               document: Callable[[Path | str, str], Path] = pdf_path,
               verify: Callable[[bytes | str], None] = verify_pdf,
               date: Callable[[dict], str | None] =
               lambda record: approximate_date(record["year"]),
               ) -> tuple[int, int]:
    """Harvest an archive whose upstream is a listing of *issues*, each naming
    the records it holds, newest issue first. Returns ``(seen, new)``.

    This is the periodical's shape: a journal's archive page names its issues,
    each issue page names its articles, and each article is one record plus one
    document (a PDF, or the article's own web page). The archive runs back
    decades and nothing in it changes once published, so the walk is
    :func:`walk` with a watermark: a run whose newest issues are on disk in
    full has caught up and stops there, and the backlist behind it is never
    re-read. `issues` is anything the source enumerates, in the order it wants
    walked -- an issue handle, a year page, one article's own page -- and
    `records` reads one of them into its records (or into a :class:`Skip`, for
    an issue page the upstream failed to serve).

    `body` says how to fetch one record's document, and returns None for a
    record that names none (an article the journal lists but publishes on
    paper only): the record alone is then that entry's state, exactly as in
    :func:`walk_records`. `document` and `verify` say where the document goes
    and what it must be -- :func:`pdf_path`/:func:`verify_pdf` for a journal
    that publishes PDFs, :func:`page_path` and a :func:`page_verifier` for one
    that publishes its articles as pages.

    `date` is what dates a record for the watermark; the default reads the
    year the journal states, since a journal dates by issue and not by day.

    `only` narrows to the one basefile, watermark untouched, and raises
    `missing` (%-formatted with `only`) when the walk met no such record --
    the same load-bearing validation as :func:`select_pending`, which an
    ``--only`` run of a lazy walk cannot make against a materialised list. The
    source narrows `issues` itself where its basefile names its issue, so
    ``--only`` costs one issue page rather than the whole archive."""
    # a journal's issues are months apart and its archive is deep, so a short
    # run of hits is already conclusive while the date window must be generous
    watermark = HarvestWatermark(Path(root) / scope / ".watermark.json",
                                 lookahead_limit=3, safety_days=30)

    def item_key(record: dict) -> ItemKey:
        basefile = record["basefile"]
        return document_item_key(
            record, record_path(root, scope, basefile),
            # a record whose document the journal never published is current on
            # its own; one that names a document is not current without it
            *((document(root, basefile),) if body(record) is not None else ()),
            date=date(record))

    def resolve(record: dict) -> bool:
        basefile = record["basefile"]
        return resolve_document(record, record_path(root, scope, basefile),
                                document(root, basefile), body(record), verify,
                                full=full, delay=delay)

    def stream() -> Iterator[Any]:
        for issue in issues:
            yield from records(issue)

    result = walk(stream(), resolve=resolve, item_key=item_key,
                  watermark=watermark, full=full, limit=limit, only=only,
                  scope=scope)
    if only is not None and result.new == 0:
        # user-typed --only: load-bearing validation raises, never asserts --
        # under python -O an assert here made the run a silent "0 seen" no-op
        raise ValueError(missing % only)
    return result.seen, result.new


# --------------------------------------------------------------------------
# the per-scope entry point
# --------------------------------------------------------------------------

def fan_out(scopes: Sequence[str], work: Callable[[str, Callable[[str], None]],
                                                  tuple[int, int]],
            *, jobs: int = 1, serial: Container[str] = (),
            label: str = "harvest", log: Callable[[str], None] = print,
            strict: bool = True,
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

    `strict` says a scope that raises takes the whole run down with it. A run
    over separate hosts (`strict=False`) instead reports the failure -- the
    scope's own lines, then its error with the traceback -- and carries on with
    the remaining scopes. When every scope has finished it raises one error
    naming every scope that failed, so the run ends red: the failed scope is
    the one to fix and re-run, and the ones that succeeded keep the data they
    already stored.

    Sequential whenever there is nothing to gain (`jobs <= 1`, one scope), and
    then each scope keeps its own live progress line."""
    scopes = list(scopes)
    elapsed: dict[str, float] = {}
    requests_made: dict[str, int] = {}
    totals: dict[str, tuple[int, int]] = {}
    failures: dict[str, str] = {}

    def timed(scope, into):
        started = time.monotonic()
        # per-thread, so a fanned-out scope is billed its own requests
        with net.counted() as made:
            try:
                return work(scope, into)
            finally:
                elapsed[scope] = time.monotonic() - started
                requests_made[scope] = made()

    def note_failure(scope, err, lines=()):
        failures[scope] = "%s: %s" % (type(err).__name__, err)
        for line in lines:
            log(line)
        log("%s %s: FAILED %s" % (label, scope, failures[scope]))
        log(traceback.format_exc())

    if jobs <= 1 or len(scopes) <= 1:
        for scope in scopes:
            try:
                totals[scope] = timed(scope, log)
            # scope-level resilience (rule:no-catch-log-continue, catalogued):
            # one broken host must not take the other scopes down; the failure
            # is logged with its traceback and the run still ends red with a
            # RuntimeError naming every failed scope
            except Exception as err:
                if strict:
                    raise
                note_failure(scope, err)
                continue
            log("%s %s: %d seen, %d new" % (label, scope, *totals[scope]))
        _log_scope_costs(elapsed, requests_made, label, log)
        if failures:
            raise RuntimeError(_failure_summary(label, scopes, failures))
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
            try:
                seen, new = future.result()
            # the same catalogued scope-level resilience as the serial path
            except Exception as err:
                if strict:
                    raise
                note_failure(scope, err, buffers[scope])
                done += 1
                rep.clear()               # lift the live line off the row
                with state:
                    busy = sorted(running)
                rep.update(done, len(scopes), scope=label, new=new_total,
                           note="  [running: %s]" % ", ".join(busy) if busy
                           else "")
                continue
            totals[scope] = seen, new
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
    _log_scope_costs(elapsed, requests_made, label, log)
    if failures:
        raise RuntimeError(_failure_summary(label, scopes, failures))
    return totals


def _failure_summary(label, scopes, failures):
    """The one error a non-strict fan_out raises when every scope has finished
    and at least one failed: the run ends red, naming each failed scope with
    its error, in the order the run took them."""
    failed = ["%s (%s)" % (scope, failures[scope])
              for scope in scopes if scope in failures]
    return ("%s: %d of %d scopes failed -- %s" %
            (label, len(failed), len(scopes), "; ".join(failed)))


def _log_scope_costs(elapsed, requests_made, label, log):
    """What each upstream cost: wall clock and HTTP attempts.

    The run ledger measures a whole source (`avg` download is 553 s over 3,888
    documents), which is the wrong grain twice over. These scopes are separate
    hosts, so a fan-out's gain is bounded by the slowest one alone; and a
    document count says nothing about how chatty the harvest was -- 1,025
    documents can be 1,025 fetches or three SPARQL queries returning 1,025
    records. Only the request count tells those apart, and it is the number to
    reduce."""
    if len(elapsed) > 1:
        log("%s scope cost: %s" % (
            label, ", ".join(
                "%s %.1fs/%dreq" % (scope, secs, requests_made.get(scope, 0))
                for scope, secs in sorted(elapsed.items(), key=lambda kv: -kv[1]))))


def dispatch_scopes(root: Path | str, scopes: Iterable[str] | None,
                    runners: Mapping[str, Callable[..., tuple[int, int]]],
                    default: Iterable[str], *, full: bool = False,
                    only: str | None = None, limit: int | None = None,
                    delay: float = 0.5, jobs: int = 1,
                    serial: Container[str] = (), label: str = "harvest",
                    log: Callable[[str], None] = print, strict: bool = True,
                    ) -> dict[str, tuple[int, int]]:
    """Run one harvest per named scope -- all of `default` when `scopes` is
    None -- and return ``{scope: (seen, new)}``, the shape `build` expects of a
    multi-scope source's ``sync``.

    A source's scopes are its organs, agencies or series: separate upstreams
    that share nothing but the entry point. ``only`` is a basefile
    ("fk/2025:01"), so it names its own scope -- it is passed to the runner it
    belongs to and withheld from every other, which is what lets a whole-source
    run narrow to one document without every runner having to recognise a
    basefile that is not its own. ``strict=False`` reports a failing scope and
    carries on with the rest, the way `fan_out` does."""
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
                   label=label, log=log, strict=strict)

