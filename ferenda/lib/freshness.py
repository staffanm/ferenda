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

import faulthandler
import functools
import hashlib
import json
import multiprocessing
import os
import sqlite3
import sys
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
MANIFEST_DB = config.DATA / ".build" / "manifest.sqlite"
INFLIGHT = config.DATA / ".build" / "inflight"    # per-pid last-started slot files (_run_parallel)
FINGERPRINTS = config.DATA / ".build" / "fingerprints.json"   # small per-(step,source) gates
RUNS = config.DATA / ".build" / "runs.ndjson"             # append-only run ledger
ERRORS = config.DATA / ".build" / "errors.json"           # per-doc latest-outcome store
STATUS = config.DATA / ".build" / "status.json"           # rolling health snapshot

# how often stage_fingerprint() reports progress -- tqdm's own default
# mininterval; a real util.invocation_bar renders a full nested frame per
# util.status() call (terminal-size query, refresh, ETA recompute), so
# calling it every completion made the reporting itself the bottleneck on a
# fast, high-count scan (2026-09-04)
_REPORT_INTERVAL = 0.1


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


def file_fingerprint(paths, *, label=None):
    """A cheap, content-insensitive fingerprint of a file set: each path with its
    size + mtime, no contents read. Detects any add / remove / rewrite (parse
    rewrites an artifact, bumping its mtime), so relate/dump can skip a source
    whose artifacts are all untouched since last run -- instead of re-reading and
    re-hashing every file. --force or a code-version change overrides it.

    `label` ("<source> <verb>") turns the walk into a reported "checking
    staleness" line, throttled to `_REPORT_INTERVAL` exactly as
    `stage_fingerprint` reports its own: over a source with 200,000 artifacts
    this is tens of seconds that otherwise print nothing at all. A caller with
    nothing to report (a test, a scan too short to notice) leaves it out and
    the walk stays silent."""
    h = hashlib.sha256()
    total = len(paths) if label else 0
    last_report = 0.0
    for done, p in enumerate(paths, 1):      # a source's lister yields them sorted
        st = compress.stat(p)                # the real (possibly .br) file's size+mtime
        h.update(("%s\x1f%d\x1f%d\x1e" % (p, st.st_size, st.st_mtime_ns)).encode())
        if label:
            now = time.perf_counter()
            if done == total or now - last_report >= _REPORT_INTERVAL:
                util.status(done, total, "%s  checking staleness" % label)
                last_report = now
    return h.hexdigest()


def _stage_fingerprint_one(stage: protocol.Stage, bf: str) -> bytes:
    return b"".join([bf.encode(),
                     *(token for _p, token in _size_mtime(stage.inputs(bf))),
                     b"\x1e"])


def stage_fingerprint(source: protocol.Source, stage_name: str) -> str:
    """A cheap fingerprint of a per-document stage's inputs (parse, versions):
    each basefile plus its input files' size+mtime (no content read). Unchanged
    ⟹ no document needs re-running and none appeared, so the whole per-document
    freshness scan (which content-hashes every input) can be skipped. Basefiles
    are folded in so a newly-downloaded doc whose input doesn't exist yet still
    moves the mark.

    Deliberately serial, no thread pool: measured directly against a real
    97,000-basefile corpus (2026-09-04), each basefile's own contribution
    costs ~43 microseconds with no gradient anywhere in the run (first,
    middle and last tenths of the scan measured identical) -- a thread pool's
    own per-task dispatch overhead (`Future` creation, GIL handoff,
    `as_completed`'s bookkeeping) dominates work that small, and gets *worse*
    with more workers: 5.0s serial vs 7.5s/22.0s/27.3s at 4/8/16 threads, on
    this dev host's NVMe storage. An earlier version threaded this on the
    (untested) assumption that the `stat()` calls behind `compress.exists`/
    `compress.stat` are I/O-wait-bound the way NFS would make them -- true on
    NFS, not measured, and evidently false on fast local storage, where the
    per-call cost is too small to ever amortise thread dispatch. If prod's
    HDD-class storage (see prod-hardware-reality) turns out to make this slow
    enough to be worth revisiting, thread it back in *there*, with numbers
    from that host -- not by assumption.

    Reports its own progress through the usual `util.status` line (stripped
    to "checking staleness" beneath an open invocation bar, same as any other
    per-basefile loop): this scan used to run silently, so a slow one read as
    a hang rather than as work in progress -- exactly the frozen "0/?" the
    nested bar's own priming (see `InvocationBar.start`) was primed to avoid,
    just one call earlier than that priming ever gets to fire. Throttled to
    `_REPORT_INTERVAL` seconds, not called on every completion: under a real
    `util.invocation_bar`, every `util.status()` call renders a full nested
    tqdm frame -- a terminal-size query, a refresh, an ETA recompute -- and
    calling that once per basefile made the *reporting* itself a measurable
    part of the cost."""
    stage = source.stages[stage_name]
    basefiles = list(protocol.stage_basefiles(source, stage_name))
    total = len(basefiles)
    h = hashlib.sha256()
    last_report = 0.0
    for done, bf in enumerate(basefiles, 1):
        h.update(_stage_fingerprint_one(stage, bf))
        now = time.perf_counter()
        if done == total or now - last_report >= _REPORT_INTERVAL:
            util.status(done, total, "%s %s  checking staleness"
                      % (source.name, stage_name))
            last_report = now
    return h.hexdigest()


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


def _reconcile_orphans(source, valid):
    if RUN_ID is None:
        return
    runlog.reconcile_orphans(ERRORS, source, set(valid))


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

# How long the parent tolerates total result silence before declaring a hang.
# Worker *crashes* no longer wait this out -- a dead worker is spotted by the
# WORKER_POLL sweep and its in-flight document rebuilt (see _run_parallel) --
# so this backstop only fires for a wedged-but-alive worker, where there is no
# corpse to find. It has to clear the slowest single document by a wide margin:
# the worst on record is `sfs versions 1999:1229` at 1 454 s, so an hour is
# ~2.5x the observed maximum -- long enough that a legitimately slow document
# is never mistaken for a hang, short enough that a real hang surfaces the
# same night instead of never.
LOST_RESULT_TIMEOUT = 3600

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


def _run_parallel(source, action, order, jobs, absorb, force=None):
    """Fan the basefiles out across `jobs` worker processes, absorbing each
    result as it completes (imap_unordered: continuous feeding, no barriers,
    a slow doc stalls nothing but itself).

    `force` overrides the run's `--force` for these builds (None = use it).
    A worker reads it off its copy of the run options, which is why the override
    travels in the options the pool is initialised with rather than per job.

    `order` is already in dispatch order -- descending expected duration, with
    the never-built document first (`expected_secs`). This starts the slow tail
    early, so the final straggler is a fast document rather than a long scan."""
    jobs_list = [(source.name, action, bf) for bf in order]
    # A worker that dies hard (a C-extension segfault) before it hits
    # maxtasksperchild loses its in-flight result, and imap_unordered then waits
    # for it forever -- multiprocessing.Pool has no BrokenProcessPool equivalent
    # to ProcessPoolExecutor's, whose respawn path we can't use (it deadlocked,
    # see MAX_DOCS_PER_WORKER). Observed: a förarbete parse sat at
    # "(97212/97213) ... ETA 00:00" with all 26 workers asleep, and would have
    # sat there indefinitely. _worker_init's faulthandler dumps the crashing
    # worker's stack first, so the crash itself is not silent -- but the hang
    # that follows was, which is the part that costs a night.
    #
    # So: whenever results pause for WORKER_POLL seconds, sweep for corpses.
    # A timeout tick guarantees the result queue is empty (a queued result
    # returns instantly, no timeout), so an inflight slot (see _worker) whose
    # pid is not among the pool's live workers is a worker that died without
    # delivering -- if its slot names a doc still outstanding, that doc's
    # result is never coming. pool._pool is private but has been the worker
    # list since 2.6; there is no public liveness API. Once everything still
    # outstanding is attributed to a corpse, stop waiting and rebuild those
    # documents one at a time in fresh subprocesses below -- the crashes are
    # rare and not document-deterministic (the same doc rebuilds fine), so the
    # run completes instead of aborting at 99.99% after an hour-long stall.
    # LOST_RESULT_TIMEOUT stays as the hang backstop (rule:fail-fast); the
    # serial `--jobs 1` path takes no pool and remains the diagnostic fallback.
    INFLIGHT.mkdir(parents=True, exist_ok=True)
    for slot in INFLIGHT.iterdir():        # stale slots from an earlier run
        slot.unlink()
    outstanding = {bf for _source, _action, bf in jobs_list}
    lost = set()                           # attributed to a dead worker
    quiet = 0.0                            # seconds since the last result
    # every child of this run -- the pool's workers and the isolated rebuilds
    # below -- runs under these, so the force override cannot drift between them
    options = (protocol.RUN if force is None
               else replace(protocol.RUN, force=force))
    with multiprocessing.Pool(processes=jobs, initializer=_worker_init,
                              initargs=(options,),
                              maxtasksperchild=MAX_DOCS_PER_WORKER) as pool:
        results = pool.imap_unordered(_worker, jobs_list, chunksize=1)
        while len(outstanding) > len(lost):
            try:
                basefile, res = results.next(timeout=WORKER_POLL)
            except StopIteration:
                break
            except multiprocessing.TimeoutError:
                quiet += WORKER_POLL
                alive = {p.pid for p in pool._pool}  # ty: ignore[unresolved-attribute]  # no public liveness API; _pool stable since 2.6
                for slot in INFLIGHT.iterdir():
                    if int(slot.name) in alive:
                        continue           # current worker, doc in flight
                    bf = slot.read_text()
                    slot.unlink()          # attribute a corpse only once
                    if bf in outstanding and bf not in lost:
                        lost.add(bf)
                        util.write("%s %s: worker died building %s; queued "
                                  "for an isolated rebuild once the pool drains"
                                  % (source.name, action, bf), err=True)
                if quiet >= LOST_RESULT_TIMEOUT:
                    raise RuntimeError(
                        "%s %s: no worker result in %d s with %d document(s) "
                        "outstanding (%d attributed to dead workers) -- a "
                        "worker is hung. The results that did arrive are "
                        "saved; re-run to finish. Outstanding: %s"
                        % (source.name, action, int(quiet), len(outstanding),
                           len(lost), ", ".join(sorted(outstanding)[:10])
                           + (" ..." if len(outstanding) > 10 else ""))) \
                        from None
                continue
            quiet = 0.0
            outstanding.discard(basefile)
            lost.discard(basefile)   # delivered after all: nothing was lost
            absorb(res, basefile)
    # the loop exits only with every un-lost result absorbed; any other state
    # (an imap accounting anomaly delivering StopIteration with residue) must
    # crash with a diagnosis here, not be papered over by the rebuild below
    assert outstanding == lost, (outstanding, lost)
    for bf in sorted(outstanding):
        # every doc still outstanding lost its worker: rebuild it one at a
        # time in a fresh subprocess, under the same force the pool's workers
        # got. A crash there costs the document, never the run.
        absorb(_rebuild_isolated(source, action, bf, options), bf)


def expected_secs(source_name, action, basefiles, manifest):
    """`(weights, order)` -- the expected seconds per basefile, and the basefiles
    in dispatch order.

    Two consumers, one pass over the manifest: `_run_parallel` dispatches
    longest-first, and the ETA paces on the same numbers. They need the unknown
    basefile (new, or never built by this recipe) treated differently, which is
    why both come from here rather than from one dict:

    * for *ordering* an unknown document is assumed slowest and goes first, so a
      new forty-minute scan cannot land at the end of the run;
    * for *summing* it cannot be infinite, so it weighs the corpus mean.

    Costs one dict lookup per basefile -- what the dispatch sort was already
    paying on its own."""
    secs = {}
    for bf in basefiles:
        entry = manifest.get(manifest_key(source_name, action, bf))
        secs[bf] = entry.get("secs") if entry else None
    known = [v for v in secs.values() if v is not None]
    mean = (sum(known) / len(known)) if known else 1.0
    weights = {bf: (mean if v is None else v) for bf, v in secs.items()}
    # unknown first (True > False), then by expected duration, longest first
    order = sorted(basefiles,
                   key=lambda bf: (secs[bf] is None, weights[bf]), reverse=True)
    return weights, order


def run_action(source, action, basefiles, jobs, force=None):
    """Run `action` over `basefiles`, in parallel where the stage allows it,
    reporting progress. `force` overrides the run's `--force` for this action
    (None = use it) -- see `build_one`."""
    manifest = load_manifest()
    merged = Result()
    total = len(basefiles)
    done = actual = 0
    # expected cost per basefile: the dispatch order and the ETA read the same
    # numbers, so they are computed once here and threaded down
    weights, order = expected_secs(source.name, action, basefiles, manifest)
    total_work = sum(weights.values())
    done_work = 0.0

    def persist():
        if merged.updates and not protocol.RUN.dry_run:
            manifest.update(merged.updates)

    def absorb(res, basefile):
        nonlocal done, actual, done_work
        _absorb(merged, res)
        done += 1
        done_work += weights.get(basefile, 0.0)
        # a basefile that only refreshed fresh dependencies (no run/skip/error) is
        # a near-instant skip; the rest are real work the ETA should be paced on
        if res.done or res.skips or res.errors:
            actual += 1
        _progress(source.name, action, done, total, actual, merged, basefile,
                  (done_work, total_work))
        if done % SAVE_EVERY == 0:
            persist()       # checkpoint so a kill mid-run doesn't lose progress

    try:
        # a single basefile can never use more than one worker, so run it here
        # rather than through the pool. Not just an optimisation: a pool worker is
        # daemonic, and a recipe that parallelises internally (stats compute fans
        # its corpus scan over a ProcessPoolExecutor) cannot spawn children there.
        if jobs > 1 and len(basefiles) > 1 and not protocol.RUN.dry_run:
            _run_parallel(source, action, order, jobs, absorb, force)
        else:
            for bf in basefiles:
                absorb(build_one(source, action, bf, manifest, force), bf)
    finally:
        # always flush what was done -- on normal completion AND on Ctrl-C, so an
        # interrupted slow source (forarbete) keeps the docs it already parsed
        if total:
            sys.stderr.write("\n")
        persist()
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
    global _FINGERPRINTS_CACHE
    _FINGERPRINTS_CACHE = store
    util.write_atomic(FINGERPRINTS, json.dumps(store, ensure_ascii=False,
                                               sort_keys=True, indent=0))
