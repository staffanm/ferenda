"""Run instrumentation: the formats and reducers behind the ops dashboard.

Three small files under ``DATA/.build/``, owned entirely by this module so
the build driver writes and the API reads through the same code without
either importing the other:

* ``runs.ndjson`` -- an append-only run ledger, one flushed JSON line per
  event (run-start, one segment per (step, source) execution, run-end).
  Written only by the parent build process; single-writer by assumption
  (the manifest already shares it) -- two concurrent invocations would
  interleave appends and race `prune`, which is accepted, not defended
  against. The *readers* do defend against its one visible consequence: a
  run whose run-start a concurrent prune rewrote away is reported as
  "damaged" rather than taking the whole ledger read down with it (see
  `_run_start`).
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
import os
import re
import socket
import statistics
from datetime import datetime, timezone
from pathlib import Path

from .util import append_json_line, now_iso, read_json_lines, write_atomic

SLOWEST_CAP = 20          # slowest-docs samples kept per segment event
TB_CAP = 4096             # chars of traceback kept per errors.json entry (the tail)
SAMPLE_LIMIT = 200        # full tracebacks per (source, stage) per apply_outcomes call


# the run id's host segment: the short hostname, reduced to the characters a
# `<ts>-<host>-<pid>` id can carry unambiguously. A dot-qualified name keeps only
# its first label, so "lagen.lysator.liu.se" reads "lagen"; anything else becomes
# "-" so the id keeps exactly three fields.
_RUN_HOST_OK = re.compile(r"[^A-Za-z0-9_]+")


def this_host():
    """This machine's short name for a run id -- 'lagen', 'staffan-desktop'.

    `LAGEN_HOST` wins when set, because in a container `gethostname()` is the
    container id: prod's ledger would name every run after a 12-hex string that
    changes on each `up -d`, which is worse than no name at all. Prod sets the
    variable (or compose's `hostname:`) to the machine a person would say."""
    return _RUN_HOST_OK.sub(
        "-", (os.environ.get("LAGEN_HOST") or socket.gethostname()
              ).partition(".")[0]) or "unknown"


def make_run_id(pid, dt=None, host=None):
    """A timestamp-sortable, per-process-unique run id naming the machine that
    ran it: ``20260704T101112.004711Z-lagen-4711``.

    The microseconds matter -- two runs started in the same process and
    wall-clock second would otherwise share an id and silently merge in the
    ledger. The host matters because the ledger travels: a corpus built on dev
    is rsynced to prod, so prod's ledger carries runs from both machines, and
    `_classify` can only read a pid against the process table of the host that
    minted it.

    The pid stays the last `-` field, which is what `_run_start` recovers from a
    headless group, so a host name containing `-` cannot hide it."""
    return "%s-%s-%d" % (
        (dt or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%S.%fZ"),
        host or this_host(), pid)


def run_host(run):
    """The machine a run id names, or None for a legacy two-field id minted
    before run ids carried one. Ledgers hold both for as long as `prune` keeps
    the old runs, so every reader has to admit the None."""
    parts = run.split("-")
    return "-".join(parts[1:-1]) if len(parts) >= 3 else None


# --------------------------------------------------------------------------
# runs.ndjson -- writers
# --------------------------------------------------------------------------

def _append_event(path, obj):
    """Append one JSON line to the run ledger. The mechanics are
    `util.append_json_line`, shared with the served-site error ledger."""
    append_json_line(path, obj)


def emit_run_start(path, run, argv, pid, t=None):
    _append_event(path, {"event": "run-start", "run": run, "t": t or now_iso(),
                        "argv": list(argv), "pid": pid})


def emit_segment(path, run, step, source, secs, *, total=None, ran=None,
                 errors=0, skipped_fresh=0, skipdoc=0, status, slowest=(),
                 t=None):
    """One (step, source) execution -- including watermark-skipped steps
    (`status="skipped"`, secs≈0) so a run detail shows the whole pipeline."""
    _append_event(path, {
        "event": "segment", "run": run, "t": t or now_iso(), "step": step,
        "source": source, "secs": secs, "total": total, "ran": ran,
        "errors": errors, "skipped_fresh": skipped_fresh, "skipdoc": skipdoc,
        "status": status, "slowest": [list(s) for s in slowest][:SLOWEST_CAP]})


def emit_run_end(path, run, secs, ok, errors, t=None):
    _append_event(path, {"event": "run-end", "run": run, "t": t or now_iso(),
                        "secs": secs, "ok": ok, "errors": errors})


# --------------------------------------------------------------------------
# runs.ndjson -- reducers
# --------------------------------------------------------------------------

def _iter_events(path):
    """Every ledger event, in file order -- `util.read_json_lines`, shared with
    the served-site error ledger, which meets the same torn-final-line case (a
    crash mid-append) and narrows the tolerance the same way."""
    return read_json_lines(path)


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


def _classify(run, pid, has_end):
    """A run's state: complete when its run-end landed; a run-start without a
    run-end is still running iff its pid is alive, else it crashed.

    "Its pid is alive" is only answerable on the machine that minted the id. A
    corpus built on dev is rsynced to prod, so prod's ledger carries dev's runs,
    and reading dev's pid against prod's /proc compares unrelated numbers: an
    unfinished dev run reads "running" whenever prod happens to hold that pid,
    and "aborted" otherwise. Neither is a fact about the run. A foreign (or
    legacy id-less) host is reported as `incomplete` -- what the ledger actually
    proves -- rather than a guess dressed as a state."""
    if has_end:
        return "complete"
    host = run_host(run)
    if host is not None and host != this_host():
        return "incomplete"
    return "running" if Path("/proc/%d" % pid).exists() else "aborted"


def _run_start(run, events):
    """The group's run-start event, synthesized when it is missing.

    Missing is a state this module's own writer admits to (see the module
    docstring: concurrent invocations race `prune`, which rewrites the whole
    file from a snapshot taken before the other process appended its
    run-start). It is rare -- it needs two `lagen` invocations within the same
    prune -- but it happened, and a reducer that raised `StopIteration` on it
    took down `lagen all runs` and the ops dashboard for every *other* run in
    the ledger too. So a headless group is reported as the damaged record it
    is rather than crashing the read: the run id still carries the timestamp
    and pid it was minted from, and the surviving segments still carry their
    step, source and error counts."""
    start = next((ev for ev in events if ev["event"] == "run-start"), None)
    if start is not None:
        return start, False
    return {"t": events[0]["t"], "argv": None,
            "pid": int(run.rpartition("-")[2])}, True


def _run_summary(run, events):
    start, headless = _run_start(run, events)
    end = next((ev for ev in events if ev["event"] == "run-end"), None)
    segments = [ev for ev in events if ev["event"] == "segment"]
    return {"run": run, "t": start["t"], "argv": start["argv"],
            "pid": start["pid"], "host": run_host(run),
            "status": ("damaged" if headless
                       else _classify(run, start["pid"], end is not None)),
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
            start, headless = _run_start(run, events)
            end = next((ev for ev in events if ev["event"] == "run-end"), None)
            return {"run": run, "start": start, "host": run_host(run),
                    "segments": [ev for ev in events if ev["event"] == "segment"],
                    "end": end,
                    "status": ("damaged" if headless
                               else _classify(run, start["pid"],
                                              end is not None))}
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


def last_segments(path):
    """Per (step, source): the most recent segment event, whatever its outcome.

    `last_success` answers "when did this last work"; this answers "what
    happened the last time it ran", which is what a health cell has to show --
    a step whose last three runs all failed is not healthy just because it
    succeeded a week ago."""
    return {(ev["step"], ev["source"]): ev for ev in _iter_events(path)
            if ev["event"] == "segment"}


def duration_history(path, n=None):
    """Per (step, source): how long each executed run of that step took *per
    document* across runs (the last `n` if given), and the multiple the latest
    run stands at against their median. Skipped segments (secs≈0) would poison
    the median, so they are excluded.

    Per document, not per run, because runs are not the same size. A whole-site
    generate of 329,126 pages and a one-page generate are both `generate`, and
    against a median dominated by the small ones the big one measured 285x --
    a number about the corpus, not about the code. Rate makes them comparable.
    A segment reporting no counts (a harvest) keeps raw seconds, which is the
    best it can offer; `rate` says which of the two a key is measured in."""
    series = {}
    for ev in _iter_events(path):
        if ev["event"] == "segment" and ev["status"] != "skipped":
            series.setdefault((ev["step"], ev["source"]), []).append(ev)
    out = {}
    for key, evs in series.items():
        if n is not None:
            evs = evs[-n:]
        # Rate needs a positive count on the latest sample and on enough others
        # to have a median. Samples without one are dropped rather than mixed
        # in: a generate ledger holds runs of 1, 2,859 and 329,123 pages, and a
        # single count-less entry among them used to force the whole key back to
        # raw seconds -- where the full run measured 285x its own median.
        rates = [ev["secs"] / ev["ran"] for ev in evs if ev["ran"]]
        rate = bool(evs[-1]["ran"]) and len(rates) >= 2
        vals = rates if rate else [ev["secs"] for ev in evs]
        median = statistics.median(vals)
        out[key] = {"secs": [ev["secs"] for ev in evs], "vals": vals,
                    "latest": vals[-1], "median": median, "rate": rate,
                    "ratio": vals[-1] / median if median else 0,
                    "regression": len(vals) >= 2 and vals[-1] > 1.5 * median}
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
