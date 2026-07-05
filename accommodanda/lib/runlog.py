"""Run instrumentation: the formats and reducers behind the ops dashboard.

Three small files under ``DATA/.build/``, owned entirely by this module so
the build driver writes and the API reads through the same code without
either importing the other:

* ``runs.ndjson`` -- an append-only run ledger, one flushed JSON line per
  event (run-start, one segment per (step, source) execution, run-end).
  Written only by the parent build process; single-writer by assumption
  (the manifest already shares it) -- two concurrent invocations would
  interleave appends and race `prune`, which is accepted, not defended
  against.
* ``errors.json`` -- a keyed latest-outcome store per document
  ("<source>/<stage>/<basefile>"), set on error and deleted on success, so
  "failed" is distinguishable from "never tried" and the store stays
  bounded by the currently-failing docs.
* ``status.json`` -- a rolling per-source-per-stage health snapshot; this
  module only owns the cell write + `_updated` stamping, the caller owns
  the cell contents.

Pure functions taking explicit Paths -- no source knowledge, no build.py
import (build.py imports the API app, so the dependency must point this way).
"""

import json
import statistics
from datetime import datetime, timezone
from pathlib import Path

from .util import write_atomic

SLOWEST_CAP = 20          # slowest-docs samples kept per segment event
TB_CAP = 4096             # chars of traceback kept per errors.json entry (the tail)
SAMPLE_LIMIT = 200        # full tracebacks per (source, stage) per apply_outcomes call


def now_iso(dt=None):
    """ISO-8601 UTC second-resolution timestamp; `dt` injectable for tests."""
    return (dt or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_run_id(pid, dt=None):
    """A timestamp-sortable, per-process-unique run id:
    ``20260704T101112.004711Z-4711``. The microseconds matter -- two runs
    started in the same process and wall-clock second would otherwise share an
    id and silently merge in the ledger."""
    return "%s-%d" % ((dt or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%S.%fZ"),
                      pid)


# --------------------------------------------------------------------------
# runs.ndjson -- writers
# --------------------------------------------------------------------------

def append_event(path, obj):
    """Append one JSON line to the ledger, flushed so a crash right after the
    write still leaves the event on disk."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")
        f.flush()


def emit_run_start(path, run, argv, pid, t=None):
    append_event(path, {"event": "run-start", "run": run, "t": t or now_iso(),
                        "argv": list(argv), "pid": pid})


def emit_segment(path, run, step, source, secs, *, total=None, ran=None,
                 errors=0, skipped_fresh=0, skipdoc=0, status, slowest=(),
                 t=None):
    """One (step, source) execution -- including watermark-skipped steps
    (`status="skipped"`, secs≈0) so a run detail shows the whole pipeline."""
    append_event(path, {
        "event": "segment", "run": run, "t": t or now_iso(), "step": step,
        "source": source, "secs": secs, "total": total, "ran": ran,
        "errors": errors, "skipped_fresh": skipped_fresh, "skipdoc": skipdoc,
        "status": status, "slowest": [list(s) for s in slowest][:SLOWEST_CAP]})


def emit_run_end(path, run, secs, ok, errors, t=None):
    append_event(path, {"event": "run-end", "run": run, "t": t or now_iso(),
                        "secs": secs, "ok": ok, "errors": errors})


# --------------------------------------------------------------------------
# runs.ndjson -- reducers
# --------------------------------------------------------------------------

def _iter_events(path):
    """Every ledger event, in file order; a missing ledger is just empty. A
    torn final line -- a crash mid-append leaves a partial JSON line -- is
    dropped so one interrupted write does not brick every subsequent read (and,
    via prune(), every subsequent build). The catch is deliberately narrowed to
    the *last* line only: corruption anywhere earlier is a real integrity
    failure and must still raise (rule:narrow-what-you-catch)."""
    path = Path(path)
    if not path.exists():
        return []
    lines = [line for line in path.read_text(encoding="utf-8").splitlines()
             if line.strip()]
    events = []
    for i, line in enumerate(lines):
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            if i == len(lines) - 1:
                break
            raise
    return events


def _group_runs(events):
    """Events grouped per run id, in first-encounter order: [(run_id, [events])]."""
    order = []
    groups = {}
    for ev in events:
        run = ev["run"]
        if run not in groups:
            order.append(run)
            groups[run] = []
        groups[run].append(ev)
    return [(run, groups[run]) for run in order]


def _classify(pid, has_end):
    """A run's state: complete when its run-end landed; a run-start without a
    run-end is still running iff its pid is alive, else it crashed."""
    if has_end:
        return "complete"
    return "running" if Path("/proc/%d" % pid).exists() else "aborted"


def _run_summary(run, events):
    start = next(ev for ev in events if ev["event"] == "run-start")
    end = next((ev for ev in events if ev["event"] == "run-end"), None)
    segments = [ev for ev in events if ev["event"] == "segment"]
    return {"run": run, "t": start["t"], "argv": start["argv"],
            "pid": start["pid"], "status": _classify(start["pid"], end is not None),
            "secs": end["secs"] if end else None,
            "ok": end["ok"] if end else None,
            "errors": end["errors"] if end else sum(s["errors"] for s in segments),
            "segments": len(segments),
            "sources": sorted({s["source"] for s in segments})}


def read_runs(path):
    """Newest-first run summaries from the ledger."""
    return [_run_summary(run, events)
            for run, events in reversed(_group_runs(_iter_events(path)))]


def run_detail(path, run_id):
    """One run's full picture -- its start event, segments in execution order,
    end event (None while running/aborted) and classified status -- or None
    when the ledger has no such run."""
    for run, events in _group_runs(_iter_events(path)):
        if run == run_id:
            start = next(ev for ev in events if ev["event"] == "run-start")
            end = next((ev for ev in events if ev["event"] == "run-end"), None)
            return {"run": run, "start": start,
                    "segments": [ev for ev in events if ev["event"] == "segment"],
                    "end": end,
                    "status": _classify(start["pid"], end is not None)}
    return None


def last_success(path):
    """Per (step, source): the timestamp of the last error-free executed
    segment. Watermarks store hashes, not times, so this is the only "when did
    X last succeed". A skipped segment proves nothing ran, so it doesn't count."""
    out = {}
    for ev in _iter_events(path):
        if (ev["event"] == "segment" and ev["errors"] == 0
                and ev["status"] != "skipped"):
            out[(ev["step"], ev["source"])] = ev["t"]
    return out


def duration_history(path, n=None):
    """Per (step, source): the executed segments' durations across runs (the
    last `n` if given), with a regression flag when the latest run took more
    than 1.5x the median. Skipped segments (secs≈0) would poison the median,
    so they are excluded."""
    series = {}
    for ev in _iter_events(path):
        if ev["event"] == "segment" and ev["status"] != "skipped":
            series.setdefault((ev["step"], ev["source"]), []).append(ev["secs"])
    out = {}
    for key, secs in series.items():
        if n is not None:
            secs = secs[-n:]
        median = statistics.median(secs)
        out[key] = {"secs": secs, "latest": secs[-1], "median": median,
                    "regression": len(secs) >= 2 and secs[-1] > 1.5 * median}
    return out


def prune(path, keep=500):
    """Atomically rewrite the ledger keeping the last `keep` complete runs (a
    run's lines from run-start through run-end) plus any trailing incomplete
    run. Missing ledger: nothing to prune."""
    path = Path(path)
    if not path.exists():
        return
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    blocks = []               # each: the lines of one complete run
    current = []
    for i, line in enumerate(lines):
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            # a torn final line (crash mid-append) is dropped so a single partial
            # write cannot brick every build; earlier corruption still raises
            # (rule:narrow-what-you-catch)
            if i == len(lines) - 1:
                break
            raise
        current.append(line)
        if ev["event"] == "run-end":
            blocks.append(current)
            current = []
    kept = blocks[-keep:] if keep else []
    write_atomic(path, "".join(line for block in kept for line in block)
                 + "".join(current))


# --------------------------------------------------------------------------
# errors.json -- per-document latest-outcome store
# --------------------------------------------------------------------------

def read_errors(path):
    path = Path(path)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def apply_outcomes(path, source, errors, done, run, t=None, *,
                   tb_cap=TB_CAP, sample_limit=SAMPLE_LIMIT):
    """Fold one action's outcomes into the store: delete the entry for every
    (stage, basefile) in `done` (it succeeded, so any recorded failure is
    healed), set an entry for every (stage, basefile, msg, tb) in `errors`.

    Systemic-failure guard: a code regression failing a whole source would
    otherwise store 100k+ full tracebacks (a multi-hundred-MB rewrite
    mid-incident), so each traceback is truncated to its last `tb_cap` chars
    and only the first `sample_limit` failures per (source, stage) in this
    call keep one at all -- later entries carry the one-line error only.
    Returns the updated store (also written atomically to `path`)."""
    t = t or now_iso()
    data = read_errors(path)
    for stage, basefile in done:
        data.pop("%s/%s/%s" % (source, stage, basefile), None)
    sampled = {}                                    # stage -> tracebacks stored
    for stage, basefile, msg, tb in errors:
        sampled[stage] = sampled.get(stage, 0) + 1
        data["%s/%s/%s" % (source, stage, basefile)] = {
            "error": msg,
            "traceback": tb[-tb_cap:] if tb and sampled[stage] <= sample_limit
            else None,
            "run": run, "t": t}
    write_atomic(path, json.dumps(data, ensure_ascii=False))
    return data


def reconcile_orphans(path, source, valid):
    """Drop `source` error entries whose basefile is no longer in `valid` -- the
    source's current basefile set. These are orphans: a document that left the
    corpus, or one an enumerator-bug once emitted (e.g. a `.watermark` mistaken
    for a basefile) and no longer does, so it is never re-run and its stale error
    can never self-heal. Only safe after a full-source run, which proves `valid`
    is complete. Keys are ``source/stage/basefile`` (basefile may contain '/'),
    so strip the ``source/stage/`` prefix to recover the basefile. Returns the
    updated store (also written atomically)."""
    data = read_errors(path)
    prefix = source + "/"
    dropped = [k for k in data if k.startswith(prefix)
               and "/" in k[len(prefix):]
               and k[len(prefix):].split("/", 1)[1] not in valid]
    for k in dropped:
        del data[k]
    write_atomic(path, json.dumps(data, ensure_ascii=False))
    return data


# --------------------------------------------------------------------------
# status.json -- rolling health snapshot
# --------------------------------------------------------------------------

def read_status(path):
    path = Path(path)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def update_status_cell(path, source, stage, cell, t=None):
    """Write one (source, stage) cell and stamp it + the top-level `_updated`.
    The caller owns the cell contents (total/fresh/stale/missing/failed/empty/
    run per the snapshot schema); this only adds `t`. Returns the snapshot."""
    t = t or now_iso()
    data = read_status(path)
    data.setdefault(source, {})[stage] = {**cell, "t": t}
    data["_updated"] = t
    write_atomic(path, json.dumps(data, ensure_ascii=False))
    return data
