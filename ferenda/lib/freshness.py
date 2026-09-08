"""The build driver's content-hash freshness engine.

Freshness is content-based, never mtime-based for correctness decisions: a
per-document stage is fresh when its output exists and the manifest records the
same input hash *and* the same recipe version (a hash over the stage's own
implementation files). Around that sit the per-document manifest (SQLite), the
coarse per-(step, source) fingerprint gates, the parallel driver and its
crash-recovery, and the run ledger's emission helpers.

`build.py` composes the sources and owns the CLI; this module knows only the
source/stage protocol, imported as `protocol` because `stage` is the name a
Stage instance carries throughout the engine.
"""

import collections
import faulthandler
import functools
import gzip
import hashlib
import heapq
import itertools
import json
import multiprocessing
import os
import shutil
import sqlite3
import sys
import threading
import time
import traceback
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field, replace
from pathlib import Path

from .. import config
from . import compress, runlog, util
from . import stage as protocol
from .errors import SkipDocument

MANIFEST = config.DATA / ".build" / "manifest.json"     # legacy; migrated into the DB
# The per-document manifest is SQLite, and SQLite over NFS pays a round trip
# per page read and per lock: generate's planning loop looked up 458,674
# pages in it at 400 NFS reads/s (2026-09-07). It lives beside the catalog,
# on the local disk `catalog_root` exists for; a machine that rsyncs the
# corpus copies it with the catalog, not with the data tree.
MANIFEST_DB = config.CATALOG_ROOT / ".build" / "manifest.sqlite"
_MANIFEST_DB_ON_DATA = config.DATA / ".build" / "manifest.sqlite"   # until 2026-09-07
INFLIGHT = config.DATA / ".build" / "inflight"    # per-pid last-started slot files (_run_parallel)
FINGERPRINTS = config.DATA / ".build" / "fingerprints.json"   # small per-(step,source) gates
RUNS = config.DATA / ".build" / "runs.ndjson"             # append-only run ledger
ERRORS = config.DATA / ".build" / "errors.json"           # per-doc latest-outcome store
STATUS = config.DATA / ".build" / "status.json"           # rolling health snapshot

# how many paths one stat job covers (`stat_records`): a GETATTR per path on
# the production NFS mount, ~0.37 ms, so a job is under a second
FINGERPRINT_CHUNK = 2000
# how many keys one staleness-scan job covers (`_scan_chunk`): one manifest
# query and one directory cache per chunk, and the unit `_run_parallel` pulls
# from the feed between looks at the pool. On the production NFS mount a key
# costs ~1.7 ms in a scan worker, so a chunk is about a second of work --
# enough to amortise the query, small enough that the status line moves
# (2026-09-06)
SCAN_CHUNK = 500


# --------------------------------------------------------------------------
# freshness
# --------------------------------------------------------------------------

def hash_files(paths):
    """Content hash over the existing files in `paths` (order-independent
    in declaration but name-tagged so a rename counts). Compress-aware: a path
    stored as a `.br`/`.gz` variant is hashed over its *decompressed* content and
    logical name, so an artifact's fingerprint is stable across compression
    settings (and identical to when it was stored plain)."""
    h = hashlib.sha256()
    for p in sorted(map(Path, paths), key=str):
        if compress.exists(p):
            h.update(p.name.encode())
            h.update(compress.read_bytes(p))
    return h.hexdigest()


def _size_mtime(paths):
    """`(path, token)` for each *existing* file among `paths`, sorted by path
    -- the token is the file's size+mtime, no content read. `hash_files`'s
    own iteration shape (a missing input, e.g. förarbete's OCR sidecar
    "listed even while absent", is silently skipped rather than an error),
    shared by the two cheap fingerprints below."""
    for p in sorted(map(Path, paths), key=str):
        if compress.exists(p):
            st = compress.stat(p)
            yield p, ("\x1f%d\x1f%d" % (st.st_size, st.st_mtime_ns)).encode()


def _cheap_inputs_fingerprint(paths):
    """`hash_files`'s shape (existing files only, name-tagged) but size+mtime
    like `file_fingerprint`, not a content read: the fast pre-check
    `_inputs_hash` uses to decide whether the expensive content hash even
    needs recomputing."""
    h = hashlib.sha256()
    for p, token in _size_mtime(paths):
        h.update(p.name.encode())
        h.update(token)
    return h.hexdigest()


def _inputs_hash(entry, inputs):
    """`(inputs_hash, inputs_wm)` for a stage's input files -- the content hash
    `is_fresh` compares against, reusing `entry`'s last one whenever the cheap
    size+mtime fingerprint proves nothing has touched any input since it was
    computed. Full content hashing (`hash_files`: read + decompress + sha256
    every input) does not scale -- at 170,000+ eurlex documents it dominated a
    freshness check that, for all but a couple of them, changed nothing (this
    session's live rebuild, 2026-09-03). `hash_files` reads content rather than
    stat'ing precisely so a `.br` migration doesn't restale the whole corpus;
    that guarantee is kept exactly, just paid for again only when the cheap
    check can't already rule a change out -- including the first run after
    this code ships, since no existing manifest entry has `inputs_wm` yet."""
    wm = _cheap_inputs_fingerprint(inputs)
    if entry and entry.get("inputs_wm") == wm:
        return entry["inputs"], wm
    return hash_files(inputs), wm


@functools.cache
def recipe_version(code):
    return hash_files(code) if code else "0"


def manifest_key(source, stage, basefile):
    return "%s/%s/%s" % (source, stage, basefile)


def is_fresh(manifest, source, stage, basefile, inputs_hash=None):
    out = stage.output(basefile)
    if not compress.exists(out):        # the output may be stored precompressed
        return False
    if stage.always:                    # unhashable inputs -- never fresh
        return False
    inputs = stage.inputs(basefile)
    if not inputs and not stage.code:
        # nothing to version the output against (e.g. download: the "input" is
        # a remote service, not a file) -- an existing output is by definition
        # up to date, whether the driver or the bulk harvester produced it
        return True
    entry = manifest.get(manifest_key(source.name, stage.name, basefile))
    if inputs_hash is None:
        inputs_hash, _ = _inputs_hash(entry, inputs)
    return bool(entry) \
        and entry["inputs"] == inputs_hash \
        and (protocol.RUN.ignore_code_changes
             or entry["version"] == recipe_version(stage.code))


def code_changed(store, kind, source, code):
    """Whether `source`'s extraction/index code changed since its last <kind> run
    (relate/index/parse/generate -- the steps gated by the coarse fingerprint store,
    not the per-doc manifest). True forces a full rebuild of that source, the same
    recipe-version rule parse/generate use per-doc, so editing catalog.py /
    search.py / text.py / render.py re-stales the step without a blanket --force;
    `--ignore-code-changes` pins it fresh. Keyed per source so a partial run can't
    mark another source current."""
    if protocol.RUN.ignore_code_changes:
        return False
    entry = store.get(manifest_key(kind, "__code__", source))
    return not entry or entry["version"] != recipe_version(code)


def record_code_version(store, kind, source, code):
    store[manifest_key(kind, "__code__", source)] = {
        "version": recipe_version(code)}


def _stat_job(chunk):
    # one stat job: the real (possibly .br) file's size+mtime for each path,
    # through the directory cache so the plain-name misses cost no lookups
    records = []
    with compress.dir_cache():
        for p in chunk:
            st = compress.stat(p)
            records.append((p, st.st_size, st.st_mtime_ns))
    return records


# One stat pass per run: relate, dump, the cross-pass gate and generate's gate
# each walk the same artifact and layer sets, and on the production NFS mount
# the first walk is the cold one (parse: 461 s on 2026-09-07) while every
# repeat is round trips for an answer already in hand. Keyed per path; a step
# that writes artifacts or layers drops the cache (`forget_stats`), so a
# later walk sees what it wrote.
_STAT_CACHE: dict[str, tuple[str, int, int]] = {}
# what this run rebuilt, per (source, stage): the basefiles whose recipe ran.
# A downstream step reads it to act on exactly those documents (generate
# renders them and the pages that show them) instead of re-deriving the
# answer from the filesystem. Absent for a stage this run did not drive.
RUN_REBUILT: dict[tuple[str, str], set[str]] = {}


def generate_caught_up():
    """Whether every document an earlier run parsed has been through a full
    generate since: the last run on the ledger (other than this one) that
    rebuilt any document also completed and carried a full generate, ok or
    skipped. False when it did not -- that run's documents are not in this
    run's `RUN_REBUILT`, so a generate that renders only this run's changes
    and their neighbours would leave them stale. True with no such run on
    record. A run without an id (a test, a dry run) is never caught up."""
    if RUN_ID is None:
        return False
    for run, events in reversed(runlog.runs_events(RUNS)):
        if run == RUN_ID:
            continue
        segments = [ev for ev in events if ev["event"] == "segment"]
        if not any(ev["step"] == "parse" and ev.get("ran") for ev in segments):
            continue
        return (any(ev["event"] == "run-end" and ev.get("ok") for ev in events)
                and any(ev["step"] == "generate" and ev["source"] == "__site__"
                        and ev["status"] in ("ok", "skipped") for ev in segments))
    return True


def write_stat_records(path, records):
    """Persist `stat_records` output at `path`: one ``path\tsize\tmtime_ns``
    line per file, gzipped. What lets a later run tell which files of a set
    are new, changed or gone (`changed_stat_records`) without the previous
    run's memory: the dumps keep one per source, generate one for the
    own-page sidecars."""
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        for p, size, mtime_ns in records:
            fh.write("%s\t%d\t%d\n" % (p, size, mtime_ns))


def read_stat_records(path):
    """The records `write_stat_records` wrote, or None when there are none."""
    path = Path(path)
    if not path.exists():
        return None
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return [(p, int(size), int(mtime))
                for p, size, mtime in (line.rstrip("\n").split("\t") for line in fh)]


def changed_stat_records(previous, current):
    """``(new, changed, gone)`` paths between two record lists: in `current`
    only, in both with another size or mtime, in `previous` only. None when
    there is no `previous`."""
    if previous is None:
        return None
    before = {p: (size, mtime) for p, size, mtime in previous}
    now = {p: (size, mtime) for p, size, mtime in current}
    return ([p for p in now if p not in before],
            [p for p, mark in now.items() if p in before and before[p] != mark],
            [p for p in before if p not in now])


def forget_stats():
    """Drop the run's stat cache: a stage or hook wrote files it covers."""
    _STAT_CACHE.clear()


def stat_records(paths, *, label=None, jobs=1):
    """``(path, size, mtime_ns)`` for every path, in the caller's order -- the
    real (possibly .br) file's stat, `FINGERPRINT_CHUNK` paths per job across
    `jobs` processes (`util.pooled`). A stat is a GETATTR round trip on the
    production NFS mount, ~0.37 ms each and serial: 36 s over förarbete's
    97,000 artifacts, 63 s over eurlex's 172,000, at the start of every
    relate, dump and generate (2026-09-06). Sixteen processes overlap them.
    A missing path raises FileNotFoundError, from the worker.

    `label` ("<source> <verb>") turns the walk into a reported "checking
    staleness" line as each chunk lands (`util.status_throttle`): over a
    source with 200,000 artifacts this is seconds that otherwise print
    nothing at all. A caller with nothing to report (a test, a walk too
    short to notice) leaves it out and the walk stays silent."""
    paths = [str(p) for p in paths]
    # cached only inside a pipeline run, where every write to these trees goes
    # through a stage or hook that drops the cache; a bare call (a test, a
    # tool) reads the filesystem as it is
    if RUN_ID is None:
        _STAT_CACHE.clear()
    missing = [p for p in paths if p not in _STAT_CACHE]
    report = util.status_throttle() if label and missing else None
    seen = 0
    for part in util.pooled(_stat_job, missing, jobs, chunk=FINGERPRINT_CHUNK):
        _STAT_CACHE.update((p, rec) for rec in part for p in [rec[0]])
        seen += len(part)
        if report:
            report(seen, len(missing), "%s  checking staleness" % label)
    return [_STAT_CACHE[p] for p in paths]


def fingerprint_of(records):
    """The digest over `stat_records` output: each path with its size + mtime,
    in order. The same bytes `file_fingerprint` hashed before the stat pass
    was split out, so a stored fingerprint stays valid across that change."""
    h = hashlib.sha256()
    for p, size, mtime_ns in records:
        h.update(("%s\x1f%d\x1f%d\x1e" % (p, size, mtime_ns)).encode())
    return h.hexdigest()


def file_fingerprint(paths, *, label=None, jobs=1):
    """A cheap, content-insensitive fingerprint of a file set: each path with its
    size + mtime, no contents read. Detects any add / remove / rewrite (parse
    rewrites an artifact, bumping its mtime), so relate/dump can skip a source
    whose artifacts are all untouched since last run -- instead of re-reading and
    re-hashing every file. --force or a code-version change overrides it.
    `label` and `jobs` are `stat_records`'. A caller that also needs the
    stats themselves (relate, whose per-artifact loop compares them) takes
    `stat_records` and `fingerprint_of` apart."""
    return fingerprint_of(stat_records(paths, label=label, jobs=jobs))


def fingerprint_fresh(store, kind, source, wm):
    """Whether `source`'s last <kind> run saw the same input fingerprint -- i.e. no
    input changed since, so the whole step can be skipped (combined with a code
    check and --force by the caller)."""
    entry = store.get(manifest_key(kind, "__fp__", source))
    return bool(entry) and entry["wm"] == wm


def record_fingerprint(store, kind, source, wm):
    store[manifest_key(kind, "__fp__", source)] = {"wm": wm}


def up_to_date(store, kind, source, wm, code):
    """A relate/index/dump/parse/generate step can be skipped for `source` when
    neither its inputs (fingerprint) nor its code changed and --force isn't set."""
    return (not protocol.RUN.force and not code_changed(store, kind, source, code)
            and fingerprint_fresh(store, kind, source, wm))


def record_step(store, kind, source, wm, code):
    record_fingerprint(store, kind, source, wm)
    record_code_version(store, kind, source, code)


# --------------------------------------------------------------------------
# the driver
# --------------------------------------------------------------------------

@dataclass
class Result:
    planned: list[tuple[str, str]] = field(default_factory=list)
    done: list[tuple[str, str]] = field(default_factory=list)
    errors: list[tuple[str, str, str, str]] = field(default_factory=list)
    updates: dict[str, dict] = field(default_factory=dict)  # manifest key -> entry
    skips: list[tuple[str, str]] = field(default_factory=list)   # SkipDocument
    fresh: list[tuple[str, str]] = field(default_factory=list)   # skipped as fresh
    timings: list[tuple[str, str, float]] = field(default_factory=list)


def ensure(source, stage_name, basefile, manifest, res, force, no_deps):
    """Bring (stage, basefile) up to date, recursing into its dependency
    first (unless --no-deps). `force` applies to the named stage only; the
    dependency is still freshness-checked. Returns True on success."""
    stage = source.stages[stage_name]
    if stage.depends and not no_deps:
        if not ensure(source, stage.depends, basefile, manifest, res,
                      False, no_deps):
            return False
    # resolved once: the freshness check and the post-run manifest entry use
    # the same digest and watermark (the recipe reads the inputs, never
    # writes them)
    inputs = stage.inputs(basefile)
    entry = manifest.get(manifest_key(source.name, stage_name, basefile))
    inputs_hash, inputs_wm = _inputs_hash(entry, inputs)
    if not force and is_fresh(manifest, source, stage, basefile, inputs_hash):
        # fresh ⟹ a valid, up-to-date artifact exists (is_fresh checks output
        # existence), so the doc is not failing -- heal any stale error left by an
        # earlier transient failure (e.g. an input momentarily missing) without a
        # --force re-parse. report() folds res.fresh into the error-clear set.
        res.fresh.append((stage_name, basefile))
        if entry and entry.get("inputs_wm") != inputs_wm:
            # nothing ran (this basefile is not "done"), but the cheap
            # watermark just computed still needs recording -- a confirmed-
            # fresh basefile that is never rebuilt (most of a corpus, on
            # every ordinary run) would otherwise recompute the full content
            # hash forever, having no other occasion to learn the watermark
            # its own last content hash was actually taken from
            res.updates[manifest_key(source.name, stage_name, basefile)] = \
                {**entry, "inputs_wm": inputs_wm}
        return True
    res.planned.append((stage_name, basefile))
    if protocol.RUN.dry_run:
        return True
    t0 = time.perf_counter()
    try:
        stage.output(basefile).parent.mkdir(parents=True, exist_ok=True)
        stage.run(basefile)
    except SkipDocument:
        # a deliberately empty document (removed/expired): write an empty
        # artifact so it is considered built and not retried every run. Via
        # compress so any prior compressed variant at this path is cleared (the
        # empty placeholder itself stays plain -- below the size floor).
        compress.write_bytes(stage.output(basefile), b"",
                             encodings=compress.ARTIFACT_ENCODINGS)
        res.skips.append((stage_name, basefile))
    except Exception as e:  # noqa: BLE001 — per-doc resilience point: recorded in res.errors, run continues (rule:no-catch-log-continue)
        res.errors.append((stage_name, basefile, "%s: %s"
                           % (type(e).__name__, e), traceback.format_exc()))
        return False
    elapsed = time.perf_counter() - t0
    res.timings.append((stage_name, basefile, elapsed))
    res.updates[manifest_key(source.name, stage_name, basefile)] = {
        "inputs": inputs_hash,
        "inputs_wm": inputs_wm,
        "version": recipe_version(stage.code),
        # last real build duration -- _run_parallel schedules descending on it
        # (unknown first), so slice pools stay duration-homogeneous and the
        # end-of-slice straggler barrier stays small
        "secs": round(elapsed, 3)}
    res.done.append((stage_name, basefile))
    return True


def vlog(msg):
    """Print `msg` to stderr when -v/--verbose is set, else do nothing -- the
    progress hook the long ai-* passes feed so a multi-minute vision run names
    what it is doing rather than hanging silently."""
    if protocol.RUN.verbose:
        print(msg, file=sys.stderr, flush=True)


# The current invocation's run id, minted once in main() for a pipeline action
# (workers never need it, so it stays off RunOptions). INVARIANT: no run id ⇒
# every ledger/errors emission is a no-op -- the single rule that covers
# --dry-run, serve and runs, so no guard has to be scattered around the choke
# points. The ledger/errors emissions go through the three helpers below;
# `status` is the deliberate exception -- it carries no run id yet writes
# status.json's authoritative snapshot cell directly (see cmd_status), so it
# does not route through them. RUN_ERRORS accumulates THIS run's segment error
# total (not the per-source currently-failing count report() prints) for the
# run-end verdict.
RUN_ID = None
RUN_ERRORS = 0


def start_run(pid=None):
    """Reset this run's ledger state and, for a pipeline action, mint its run id
    (returned). `pid` None leaves the id unset -- the no-run-id invariant above.
    A `global` in the CLI module cannot rebind these, so main() goes through
    here: once bare to clear a prior in-process run, once with the pid when the
    action is a pipeline one."""
    global RUN_ID, RUN_ERRORS
    RUN_ID = None
    RUN_ERRORS = 0
    _STAT_CACHE.clear()
    RUN_REBUILT.clear()
    if pid is not None:
        RUN_ID = runlog.make_run_id(pid)
    return RUN_ID


def _emit_segment(step, source, secs, *, total=None, ran=None, errors=0,
                  skipped_fresh=0, skipdoc=0, status, slowest=()):
    global RUN_ERRORS
    if RUN_ID is None:
        return
    RUN_ERRORS += errors
    runlog.emit_segment(RUNS, RUN_ID, step, source, secs, total=total, ran=ran,
                        errors=errors, skipped_fresh=skipped_fresh,
                        skipdoc=skipdoc, status=status, slowest=slowest)


def _apply_outcomes(source, errors, done):
    if RUN_ID is None:
        return
    runlog.apply_outcomes(ERRORS, source, errors, done, RUN_ID)


def _reconcile_orphans(source, stage, valid):
    if RUN_ID is None:
        return
    runlog.reconcile_orphans(ERRORS, source, stage, set(valid))


def _update_status_cell(source, stage, cell):
    if RUN_ID is None:
        return
    runlog.update_status_cell(STATUS, source, stage, cell)


def build_one(source, action, basefile, manifest, force=None):
    """Bring one document's `action` up to date. `force` overrides the run's own
    `--force` for this build (None = use it): a targeted generate's upstream
    prerequisites stay freshness-checked, because the `--force` was aimed at the
    generate the user named, not at re-parsing its inputs."""
    res = Result()
    ensure(source, action, basefile, manifest, res,
           protocol.RUN.force if force is None else force, protocol.RUN.no_deps)
    return res


def _worker(job):
    # SOURCES is filled by ferenda.build at import time, and a pool child
    # inherits the filled registry from the forkserver process, which preloads
    # __main__ (the `lagen` console script / `python -m ferenda.build`).
    source_name, action, basefile = job
    # overwrite this worker's last-started slot before building: if the process
    # dies hard mid-document (the C-extension heap corruption chronicled at
    # MAX_DOCS_PER_WORKER), the parent reads the slot to attribute the lost
    # document (see _run_parallel). Overwritten, never cleared -- a slot naming
    # an already-absorbed doc attributes nothing, so no per-doc unlink needed.
    (INFLIGHT / str(os.getpid())).write_text(basefile)
    return basefile, build_one(protocol.SOURCES[source_name], action, basefile,
                               load_manifest())


def _worker_init(run_options: protocol.RunOptions):
    # child processes re-import this module fresh -- carry the run options
    # across the process boundary. The manifest is NOT shipped through
    # initargs: each worker re-imports the module fresh and load_manifest opens
    # its own read connection there, seeing the parent's checkpointed commits.
    protocol.set_run(run_options)
    # a forked worker is a COW copy of the parent, not a fresh interpreter --
    # it inherits the parent's tqdm bar objects and _TqdmRedirect-wrapped
    # streams verbatim, neither safe nor meaningful in this process (see
    # util.reset_worker_state)
    util.reset_worker_state()
    # a worker that dies hard in a C extension leaves the parent little to go
    # on; faulthandler dumps the crashing worker's Python stack (and thus the
    # basefile in flight) to stderr before it dies
    faulthandler.enable()


def _progress(source, action, done, total, actual, merged, basefile, work=None):
    """Live one-line counter on stderr (the shared util.status pattern), carrying
    the source/action, the running counts, and the most recently completed
    basefile. `actual` is the count of basefiles that actually built (ran a recipe,
    hit a SkipDocument, or errored) rather than being skipped as already fresh, so
    the ETA is paced on real work and not diluted by a corpus of fresh skips.
    `work` is ``(done, total)`` expected seconds, which paces the ETA on cost
    instead of on job count -- the two diverge sharply because the driver
    dispatches the slowest documents first (see `util._eta_suffix`)."""
    verb = "planned" if protocol.RUN.dry_run else "ran"
    count = len(merged.planned) if protocol.RUN.dry_run else len(merged.done)
    util.status(done, total, "%s %s  %s %d  err %d  %s"
                % (source, action, verb, count, len(merged.errors), basefile),
                actual=actual, work=work)


SAVE_EVERY = 1000      # checkpoint the manifest mid-run, every this many docs

# A worker is retired and replaced after this many docs: CPython 3.14's
# incremental GC has corrupted long-lived worker heaps under allocation storms
# (a segfault "Garbage-collecting" in lark's Earley objects, ~90k docs into
# one worker's lifetime), and recycling caps how much damage can accumulate.
# multiprocessing.Pool's decades-old maxtasksperchild -- not
# ProcessPoolExecutor's max_tasks_per_child, whose respawn path deadlocked
# under rapid task churn (all workers hit the limit, none were replaced, and
# the driver waited forever on futures no worker would ever take).
MAX_DOCS_PER_WORKER = 1000

# How long the parent tolerates total result silence before declaring a hang
# (`util.HANG_TIMEOUT`, sized there against the slowest document on record).
# Worker *crashes* no longer wait this out -- a dead worker is spotted by the
# WORKER_POLL sweep and its in-flight document rebuilt (see _run_parallel) --
# so this backstop only fires for a wedged-but-alive worker, where there is no
# corpse to find.
LOST_RESULT_TIMEOUT = util.HANG_TIMEOUT

# Between-results poll interval: with no result arriving for this long, the
# parent sweeps the pool's workers for corpses. Bounds crash-detection latency
# without waking a busy parent (a tick only happens when results have paused).
WORKER_POLL = 60

# How many times a lost document is retried in a fresh subprocess before it is
# recorded as an error. The crash that lost it is not document-deterministic
# (the same document rebuilds fine), so a second heap almost always finishes
# it; a document that dies twice in a row is a real defect and belongs in the
# error list, not in a third attempt.
REBUILD_ATTEMPTS = 2


def _rebuild_isolated(source, action, basefile, options):
    """Rebuild one lost document in a single-use subprocess and return its
    `Result`. `options` is the run options the child runs under, built once by
    the caller so the pool's workers and this rebuild get the same `force`.

    The parallel driver's parent builds no document itself (the serial path at
    `run_action` still does -- with no pool, a crash there costs only itself).
    The parent holds the run's absorbed results and its manifest checkpoints,
    and the heap corruption chronicled at MAX_DOCS_PER_WORKER kills whichever
    process meets it: in-parent, a second crash would throw away hours of
    completed work at the very end of a run. A crash here costs one
    subprocess. ProcessPoolExecutor reports a dead child as BrokenProcessPool
    (public API, unlike Pool's silent wait), so the retry is a plain loop, and
    REBUILD_ATTEMPTS failures become an error record like any other
    per-document failure."""
    for attempt in range(1, REBUILD_ATTEMPTS + 1):
        with ProcessPoolExecutor(max_workers=1, initializer=_worker_init,
                                 initargs=(options,)) as pool:
            try:
                return pool.submit(_worker,
                                   (source.name, action, basefile)).result()[1]
            except BrokenProcessPool:
                util.write("%s %s: isolated rebuild of %s crashed (attempt %d/%d)"
                          % (source.name, action, basefile, attempt,
                             REBUILD_ATTEMPTS), err=True)
    res = Result()
    # the evidence is the child's faulthandler stack on this run's stderr (see
    # _worker_init) -- the error record can only name where to find it, since
    # the process that held the traceback is gone
    msg = ("BrokenProcessPool: the worker died in each of %d isolated rebuild "
           "attempts; the crash stacks are on this run's stderr"
           % REBUILD_ATTEMPTS)
    res.errors.append((action, basefile, msg, msg + "\n"))
    return res


def _cheaply_fresh(source, stage_name, basefile, manifest, force, no_deps, fresh):
    """The scan's answer to "does this document need a worker?" -- `ensure`'s
    own test, minus the content hash: the stage's output exists, its recipe
    version is the one recorded, and its inputs' size+mtime mark is the one
    the manifest was last built from (`_inputs_hash`'s cheap pre-check). A
    dependency is checked first the way `ensure` recurses into it, without
    `force` (which names one stage). Every stage found fresh along the way is
    appended to `fresh` as ``(stage, basefile)``, what `ensure` reports for
    the same outcome, so `report` heals the same ledger entries.

    False means "maybe stale": the worker's `ensure` still compares content
    hashes, so a file whose mtime moved without its bytes changing (a
    compression migration) is found fresh there, exactly as before. The scan
    only decides who gets a worker."""
    stage = source.stages[stage_name]
    if stage.depends and not no_deps and not _cheaply_fresh(
            source, stage.depends, basefile, manifest, False, no_deps, fresh):
        return False
    if force or stage.always or not compress.exists(stage.output(basefile)):
        return False
    inputs = stage.inputs(basefile)
    if inputs or stage.code:
        entry = manifest.get(manifest_key(source.name, stage_name, basefile))
        if not entry:
            return False
        if not (protocol.RUN.ignore_code_changes
                or entry["version"] == recipe_version(stage.code)):
            return False
        if entry.get("inputs_wm") != _cheap_inputs_fingerprint(inputs):
            return False
    fresh.append((stage_name, basefile))
    return True


def _scan_chunk(source, action, chunk, force, no_deps):
    """`_cheaply_fresh` over the basefiles in `chunk`, as one unit of scan
    work: one manifest query for every key the chain can ask about
    (`Manifest.get_many`) and one directory cache (`compress.dir_cache`) for
    the chunk's stat traffic. Returns ``(stale, fresh, n_fresh)``: the stale
    basefiles each with the manifest's last build seconds (None when never
    built), the ``(stage, basefile)`` pairs found fresh along the way, and
    the number of documents found fresh. Runs in a scan worker
    (`_scan_job`) or, with one job, in the parent."""
    chain = []
    name = action
    while name:
        chain.append(name)
        name = None if no_deps else source.stages[name].depends
    known = load_manifest().get_many(
        manifest_key(source.name, stage, bf) for bf in chunk for stage in chain)
    stale, fresh = [], []
    with compress.dir_cache():
        for bf in chunk:
            if not _cheaply_fresh(source, action, bf, known, force, no_deps, fresh):
                entry = known.get(manifest_key(source.name, action, bf))
                stale.append((bf, entry.get("secs") if entry else None))
    return stale, fresh, len(chunk) - len(stale)


def _scan_job(source_name, action, force, no_deps, chunk):
    # the scan worker's job: SOURCES is filled the way _worker's is, from
    # the forkserver's preloaded __main__
    return _scan_chunk(protocol.SOURCES[source_name], action, chunk, force, no_deps)


def _scan_results(source, action, basefiles, force, jobs):
    """`_scan_chunk`'s answer for each `SCAN_CHUNK` of `basefiles`, in order
    -- `util.pooled` over `jobs` scan workers, each carrying the run options
    (`_worker_init`: `_cheaply_fresh` reads `RUN.ignore_code_changes`). On
    the production NFS mount a document's staleness check is round trips (a
    directory revalidation, a GETATTR per input) that only overlap across
    processes: a thread pool gained 10% against the GIL, where sixteen scan
    workers plus the chunked manifest read and the directory cache took
    eurlex parse with nothing stale from 1172 s to 127 s (2026-09-06). A
    ProcessPoolExecutor, not multiprocessing.Pool: a scan worker runs no
    parser, so the heap corruption that made the build pool grow a corpse
    sweep (see `_run_parallel`) has nothing to strike, and a worker that dies
    anyway is a defect the executor reports as BrokenProcessPool rather than
    silence (rule:fail-fast)."""
    return util.pooled(
        functools.partial(_scan_job, source.name, action, force, protocol.RUN.no_deps),
        basefiles, jobs, chunk=SCAN_CHUNK, timeout=LOST_RESULT_TIMEOUT,
        initializer=_worker_init, initargs=(protocol.RUN,))


def _scan(source, action, basefiles, force, merged, expected, counts, jobs):
    """Walk `basefiles` once, a chunk of `SCAN_CHUNK` at a time, and yield
    the ones that need a worker, as each chunk's answer lands. A fresh
    document is booked into `merged.fresh` on the spot and never reaches the
    pool -- the round-trip a fresh document used to cost (eurlex parse:
    43-52 s for 172,000 of them with nothing to parse, 2026-09-03) is gone,
    and so is the separate whole-source fingerprint pass that existed to
    spare it. `expected[basefile]` gets the manifest's last build seconds
    (None when never built), what the dispatcher orders on and the ETA
    weighs. `counts["fresh"]` is the number of documents booked fresh --
    `merged.fresh` holds one ``(stage, basefile)`` pair per stage of a fresh
    document's chain, so its length is not that number.

    Reports as "checking staleness" on the usual status line as chunks
    land (`util.status_throttle`); the first and last chunk always report."""
    total = len(basefiles)
    report = util.status_throttle()
    done = stale = 0
    for found, fresh, n_fresh in _scan_results(source, action, basefiles, force, jobs):
        merged.fresh += fresh
        counts["fresh"] += n_fresh
        done += len(found) + n_fresh
        stale += len(found)
        report(done, total, "%s %s  checking staleness  %d stale"
               % (source.name, action, stale))
        for bf, secs in found:
            expected[bf] = secs
            yield bf


def _priority(expected, basefile):
    """The dispatch order as a heap key: a never-built document first (it is
    assumed slowest -- a new forty-minute scan must not land at the end of
    the run), then by last build duration, longest first. Two consumers,
    one manifest lookup made by `_scan`: the order and the ETA's weights."""
    secs = expected[basefile]
    return (secs is not None, -(secs or 0.0))


def _run_parallel(source, action, feed, jobs, absorb, priority, force=None):
    """Fan the stale documents `feed` yields out across `jobs` worker
    processes, absorbing each result as it completes.

    The feed is the staleness scan itself, pulled `SCAN_CHUNK` keys at a
    time between looks at the pool, so the first worker starts on the first
    stale document found, seconds into a scan that takes 5-16 s on the big
    sources, instead of after every pass over the tree. What is known so far
    sits on a heap keyed by `priority` (never-built first, then longest
    last build), and a free worker always takes the most expensive known
    document. The order is not strictly slowest-first during the scan's few
    seconds; once it ends the heap holds everything and it is, so the tail
    of the run is the cheap documents and the final straggler a fast one
    rather than a long scan.

    `force` overrides the run's `--force` for these builds (None = use it).
    A worker reads it off its copy of the run options, which is why the
    override travels in the options the pool is initialised with rather than
    per job."""
    # A worker that dies hard (a C-extension segfault) before it hits
    # maxtasksperchild loses its in-flight result, and the pool then never
    # delivers it -- multiprocessing.Pool has no BrokenProcessPool equivalent
    # to ProcessPoolExecutor's, whose respawn path we can't use (it deadlocked,
    # see MAX_DOCS_PER_WORKER). Observed: a förarbete parse sat at
    # "(97212/97213) ... ETA 00:00" with all 26 workers asleep, and would have
    # sat there indefinitely. _worker_init's faulthandler dumps the crashing
    # worker's stack first, so the crash itself is not silent -- but the hang
    # that follows was, which is the part that costs a night.
    #
    # So: whenever results pause for WORKER_POLL seconds, sweep for corpses.
    # An inflight slot (see _worker) whose pid is not among the pool's live
    # workers is a worker that died without delivering -- if its slot names a
    # doc still outstanding, that doc's result is never coming. pool._pool is
    # private but has been the worker list since 2.6; there is no public
    # liveness API. Once everything still outstanding is attributed to a
    # corpse, stop waiting and rebuild those documents one at a time in fresh
    # subprocesses below -- the crashes are rare and not document-deterministic
    # (the same doc rebuilds fine), so the run completes instead of aborting at
    # 99.99% after an hour-long stall. LOST_RESULT_TIMEOUT stays as the hang
    # backstop (rule:fail-fast); the serial `--jobs 1` path takes no pool and
    # remains the diagnostic fallback.
    INFLIGHT.mkdir(parents=True, exist_ok=True)
    for slot in INFLIGHT.iterdir():        # stale slots from an earlier run
        slot.unlink()
    # every child of this run -- the pool's workers and the isolated rebuilds
    # below -- runs under these, so the force override cannot drift between them
    options = (protocol.RUN if force is None
               else replace(protocol.RUN, force=force))
    heap = []                              # (priority, seq, basefile)
    seq = itertools.count()
    cond = threading.Condition()
    arrived = collections.deque()          # (basefile, Result), from the pool's thread
    broken = []                            # an exception a worker raised outright
    def on_result(item):
        with cond:
            arrived.append(item)
            cond.notify()
    def on_error(exc):
        with cond:
            broken.append(exc)
            cond.notify()
    outstanding = set()                    # dispatched, not yet absorbed
    lost = set()                           # attributed to a dead worker
    quiet = 0.0                            # seconds since the last result
    feeding = True
    with multiprocessing.Pool(processes=jobs, initializer=_worker_init,
                              initargs=(options,),
                              maxtasksperchild=MAX_DOCS_PER_WORKER) as pool:
        while True:
            if feeding:
                for _ in range(SCAN_CHUNK):
                    try:
                        bf = next(feed)
                    except StopIteration:
                        feeding = False
                        break
                    heapq.heappush(heap, (priority(bf), next(seq), bf))
            while heap and len(outstanding) - len(lost) < jobs:
                _, _, bf = heapq.heappop(heap)
                outstanding.add(bf)
                pool.apply_async(_worker, ((source.name, action, bf),),
                                 callback=on_result, error_callback=on_error)
            with cond:
                if broken:
                    raise broken[0]
                items = list(arrived)      # everything that landed: absorbing
                arrived.clear()            # frees capacity for the next dispatch
                if items:
                    pass
                elif feeding:
                    continue               # the scan is the work meanwhile
                elif not heap and len(outstanding) == len(lost):
                    break
                elif cond.wait(timeout=WORKER_POLL):
                    continue               # a result landed: pick it up above
                else:
                    quiet += WORKER_POLL
                    alive = {p.pid for p in pool._pool}  # ty: ignore[unresolved-attribute]  # no public liveness API; _pool stable since 2.6
                    for slot in INFLIGHT.iterdir():
                        if int(slot.name) in alive:
                            continue       # current worker, doc in flight
                        bf = slot.read_text()
                        slot.unlink()      # attribute a corpse only once
                        if bf in outstanding and bf not in lost:
                            lost.add(bf)
                            util.write("%s %s: worker died building %s; queued "
                                       "for an isolated rebuild once the pool "
                                       "drains" % (source.name, action, bf),
                                       err=True)
                    if quiet >= LOST_RESULT_TIMEOUT:
                        raise RuntimeError(
                            "%s %s: no worker result in %d s with %d "
                            "document(s) outstanding (%d attributed to dead "
                            "workers) -- a worker is hung. The results that "
                            "did arrive are saved; re-run to finish. "
                            "Outstanding: %s"
                            % (source.name, action, int(quiet),
                               len(outstanding), len(lost),
                               ", ".join(sorted(outstanding)[:10])
                               + (" ..." if len(outstanding) > 10 else "")))
                    continue
            quiet = 0.0
            for basefile, res in items:
                outstanding.discard(basefile)
                lost.discard(basefile)     # delivered after all: nothing was lost
                absorb(res, basefile)
    # the loop exits only with every un-lost result absorbed; any other state
    # must crash with a diagnosis here, not be papered over by the rebuild below
    assert outstanding == lost, (outstanding, lost)
    for bf in sorted(outstanding):
        # every doc still outstanding lost its worker: rebuild it one at a
        # time in a fresh subprocess, under the same force the pool's workers
        # got. A crash there costs the document, never the run.
        absorb(_rebuild_isolated(source, action, bf, options), bf)


def run_action(source, action, basefiles, jobs, force=None):
    """Run `action` over `basefiles`: one scan decides which of them need a
    worker (`_scan`), and those go to the pool as they are found, most
    expensive first (`_run_parallel`) -- or, with one job, one basefile or a
    dry run, are built here in scan order. `force` overrides the run's
    `--force` for this action (None = use it) -- see `build_one`."""
    manifest = load_manifest()
    merged = Result()
    total = len(basefiles)
    expected = {}                          # stale basefile -> last build secs, or None
    counts = {"fresh": 0}                  # documents the scan booked fresh
    absorbed = actual = 0
    done_work = total_work = 0.0
    known_sum, known_n = 0.0, 0
    weights = {}
    def weight(basefile):
        # a never-built document weighs the mean of the stale ones with a
        # record met so far (1.0 before any), so the ETA's total is finite;
        # the order treats it as slowest
        nonlocal known_sum, known_n
        secs = expected[basefile]
        if secs is not None:
            known_sum += secs
            known_n += 1
        w = secs if secs is not None else (known_sum / known_n if known_n else 1.0)
        weights[basefile] = w
        return w
    def feed():
        nonlocal total_work
        for bf in _scan(source, action, basefiles,
                        protocol.RUN.force if force is None else force,
                        merged, expected, counts, jobs):
            total_work += weight(bf)
            yield bf
    def persist():
        if merged.updates and not protocol.RUN.dry_run:
            manifest.update(merged.updates)
    def absorb(res, basefile):
        nonlocal absorbed, actual, done_work
        _absorb(merged, res)
        absorbed += 1
        done_work += weights[basefile]     # every absorbed key came out of feed()
        # a basefile that only refreshed fresh dependencies (no run/skip/error) is
        # a near-instant skip; the rest are real work the ETA should be paced on
        if res.done or res.skips or res.errors:
            actual += 1
        _progress(source.name, action, counts["fresh"] + absorbed, total,
                  actual, merged, basefile, (done_work, total_work))
        if absorbed % SAVE_EVERY == 0:
            persist()       # checkpoint so a kill mid-run doesn't lose progress
    try:
        # a single basefile can never use more than one worker, so run it here
        # rather than through the pool. Not just an optimisation: a pool worker is
        # daemonic, and a recipe that parallelises internally (stats compute fans
        # its corpus scan over a ProcessPoolExecutor) cannot spawn children there.
        if jobs > 1 and total > 1 and not protocol.RUN.dry_run:
            _run_parallel(source, action, feed(), jobs, absorb,
                          lambda bf: _priority(expected, bf), force)
        else:
            for bf in feed():
                absorb(build_one(source, action, bf, manifest, force), bf)
    finally:
        # always flush what was done -- on normal completion AND on Ctrl-C, so an
        # interrupted slow source (forarbete) keeps the docs it already parsed
        if total:
            sys.stderr.write("\n")
        persist()
    if not protocol.RUN.dry_run:
        RUN_REBUILT[(source.name, action)] = {
            bf for stage, bf in merged.done if stage == action}
        if merged.done:
            forget_stats()
    return merged


def _absorb(into, res):
    into.planned += res.planned
    into.done += res.done
    into.errors += res.errors
    into.updates.update(res.updates)
    into.skips += res.skips
    into.fresh += res.fresh
    into.timings += res.timings


# --------------------------------------------------------------------------
# manifest persistence. An entry per (source, stage, basefile), in SQLite keyed
# on the manifest_key -- because the former single ~133 MB JSON had no random
# access: any run, even a one-page scoped generate, paid a full parse to read
# one entry and a full rewrite to record one. Entries stay schemaless (one JSON
# value per key: inputs/version/secs/...), so callers see the same dict-shaped
# get/update the JSON manifest offered; update() commits immediately, which IS
# the mid-run checkpoint (SAVE_EVERY), so there is no separate save step.
# --------------------------------------------------------------------------

class Manifest:
    """The per-document build manifest over SQLite. Only two operations exist
    (`get` one entry, `update`/upsert a batch), mirroring the dict the JSON
    manifest used to load into -- test fixtures still pass a plain dict.
    Pool workers each build their own instance: under the forkserver start
    method (the 3.14 Linux default) the module re-imports fresh per worker and
    load_manifest opens a new connection there. The pid guard on `con` is
    insurance for the one path that could still carry the object across a
    process boundary (a future fork-context pool) -- a SQLite connection must
    never be used across one. Workers only ever read; the parent is the sole
    writer (persist()/cmd_generate), exactly as before."""

    def __init__(self, path):
        self.path = path
        self._con = None
        self._pid = None

    @property
    def con(self):
        if self._con is None or self._pid != os.getpid():
            self._con = sqlite3.connect(self.path, timeout=30)
            self._con.execute("CREATE TABLE IF NOT EXISTS manifest "
                              "(key TEXT PRIMARY KEY, entry TEXT NOT NULL)")
            self._pid = os.getpid()
        return self._con

    def get(self, key):
        row = self.con.execute("SELECT entry FROM manifest WHERE key = ?",
                               (key,)).fetchone()
        return json.loads(row[0]) if row else None

    # SQLite's default variable ceiling is 32,766; well under it per statement
    GET_MANY_BATCH = 900

    def get_many(self, keys):
        """``{key: entry}`` for the keys that exist, a few hundred per
        statement. One `get` is one SQLite transaction, and a transaction on
        the production NFS mount costs four lock round trips plus the cache
        invalidation they force -- 2.5 ms, half of what a staleness check per
        document cost (2026-09-06). The scan asks for a chunk's keys at once
        instead."""
        found = {}
        for batch in itertools.batched(keys, self.GET_MANY_BATCH, strict=False):
            found.update(
                (k, json.loads(v)) for k, v in self.con.execute(
                    "SELECT key, entry FROM manifest WHERE key IN (%s)"
                    % ",".join("?" * len(batch)), batch))
        return found

    def update(self, entries):
        if not entries:
            return
        self.con.executemany(
            "INSERT INTO manifest (key, entry) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET entry = excluded.entry",
            [(k, json.dumps(v, ensure_ascii=False, sort_keys=True))
             for k, v in entries.items()])
        self.con.commit()


_MANIFEST_CACHE = None


def load_manifest():
    global _MANIFEST_CACHE
    if _MANIFEST_CACHE is None:
        MANIFEST_DB.parent.mkdir(parents=True, exist_ok=True)
        if not MANIFEST_DB.exists() and _MANIFEST_DB_ON_DATA != MANIFEST_DB \
                and _MANIFEST_DB_ON_DATA.exists():
            # one-time move off the data tree: copy under a scratch name and
            # rename, so a run interrupted mid-copy finds no half manifest and
            # copies again. The old file stays where it was for an older image.
            part = MANIFEST_DB.with_name(MANIFEST_DB.name + ".part")
            shutil.copy2(_MANIFEST_DB_ON_DATA, part)
            os.replace(part, MANIFEST_DB)
        m = Manifest(MANIFEST_DB)
        if MANIFEST.exists():
            # one-time migration from the retired JSON manifest. The JSON's
            # presence IS the migration-incomplete flag: it is removed only
            # after its content is committed, so an interrupted migration
            # (connect() creates the DB file before the rows land) simply
            # re-runs -- update() upserts, so a partial first pass is harmless.
            # Workers never race this: the parent loads (and thus migrates)
            # before any pool is spawned.
            m.update(json.loads(MANIFEST.read_text()))
            MANIFEST.unlink()
        _MANIFEST_CACHE = m
    return _MANIFEST_CACHE


# The coarse per-(step, source) fingerprints live in their own tiny file, NOT the
# big per-doc manifest -- so a no-op run reads only this to decide every step can
# be skipped, never opening the per-doc manifest DB (consulted only when a
# source actually changed and needs the per-document freshness scan).
_FINGERPRINTS_CACHE = None


def _carried_over_gates():
    """The gates from the store's former name and key shape
    (``watermarks.json``, ``<step>/__wm__/<source>``), translated.

    "Watermark" was the wrong word: these are sha256 digests of a file set's
    names, sizes and mtimes, not high-water marks -- the harvest side's
    `lib.harvest.HarvestWatermark`, which really is a position marker, keeps
    the name. Renaming the file would otherwise have silently discarded every
    gate and made the next run re-scan (and content-hash) every source for
    nothing. Delete this once the old file is gone from every deployment."""
    legacy = FINGERPRINTS.with_name("watermarks.json")
    if not legacy.exists():
        return {}
    return {key.replace("/__wm__/", "/__fp__/"): value
            for key, value in json.loads(legacy.read_text()).items()}


def load_fingerprints():
    global _FINGERPRINTS_CACHE
    if _FINGERPRINTS_CACHE is None:
        _FINGERPRINTS_CACHE = (json.loads(FINGERPRINTS.read_text())
                               if FINGERPRINTS.exists() else _carried_over_gates())
    return _FINGERPRINTS_CACHE


def save_fingerprints(store):
    # A plan is not a run. Six call sites record a gate once their step
    # finishes, and none of them knew about --dry-run, so `lagen all rebuild -n`
    # wrote the store: the plan printed every stale document and then marked
    # each source current, and the real run that followed skipped the whole
    # stale tree. The guard belongs here rather than at the call sites, so a
    # seventh one cannot reintroduce it.
    if protocol.RUN.dry_run:
        return
    # The whole store is rewritten from a process-local snapshot, which is only
    # safe because there is exactly one writer: `build.main` holds the corpus
    # writer lease (lib/writerlock) for the length of a pipeline invocation.
    # Without it, two runs each load the file, and the second to finish writes
    # back a dict that never saw the first one's completed gates -- so a source
    # marked current is silently marked stale again, or worse, the other way.
    global _FINGERPRINTS_CACHE
    _FINGERPRINTS_CACHE = store
    util.write_atomic(FINGERPRINTS, json.dumps(store, ensure_ascii=False,
                                               sort_keys=True, indent=0))
