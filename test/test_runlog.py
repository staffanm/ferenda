"""lib/runlog: the run-ledger, error-store and status-snapshot formats.

Everything runs against tmp_path -- the module takes explicit Paths and
knows nothing about sources, so these are pure format/reducer checks:
ledger roundtrip, aborted/running classification, outcome set/clear with
traceback capping, status-cell stamping, prune and the duration-regression
flag."""

import json
import os
from datetime import datetime, timezone

import pytest

from ferenda.lib import runlog


def _write_run(path, run, pid=4711, segments=(), end=True, base_t="2026-07-04T10:00:0"):
    """One complete (or start-only) run in the ledger; `segments` are
    (step, source, secs, errors[, status]) tuples."""
    runlog.emit_run_start(path, run, ["lagen", "all", "all"], pid,
                          t=base_t + "0Z")
    total_errors = 0
    for i, seg in enumerate(segments):
        step, source, secs, errors = seg[:4]
        status = seg[4] if len(seg) > 4 else ("errors" if errors else "ok")
        total_errors += errors
        runlog.emit_segment(path, run, step, source, secs, total=10, ran=10,
                            errors=errors, status=status,
                            t=base_t + "%dZ" % (i + 1))
        # (the timestamp scheme allows ≤9 segments per synthetic run)
    if end:
        runlog.emit_run_end(path, run, 12.5, ok=total_errors == 0,
                            errors=total_errors, t=base_t + "9Z")


def test_ledger_roundtrip(tmp_path):
    path = tmp_path / "runs.ndjson"
    _write_run(path, "r1", segments=[("parse", "sfs", 3.0, 2),
                                     ("parse", "dv", 4.0, 0)])
    runs = runlog.read_runs(path)
    assert len(runs) == 1
    (run,) = runs
    assert run["run"] == "r1"
    assert run["status"] == "complete"
    assert run["ok"] is False and run["errors"] == 2
    assert run["segments"] == 2
    assert run["sources"] == ["dv", "sfs"]
    assert run["argv"] == ["lagen", "all", "all"]

    detail = runlog.run_detail(path, "r1")
    assert [s["source"] for s in detail["segments"]] == ["sfs", "dv"]
    assert detail["start"]["event"] == "run-start"
    assert detail["end"]["secs"] == 12.5
    assert detail["status"] == "complete"
    assert runlog.run_detail(path, "nonexistent") is None


def test_read_runs_newest_first(tmp_path):
    path = tmp_path / "runs.ndjson"
    _write_run(path, "r1", base_t="2026-07-04T10:00:0")
    _write_run(path, "r2", base_t="2026-07-04T11:00:0")
    assert [r["run"] for r in runlog.read_runs(path)] == ["r2", "r1"]


def test_missing_ledger_is_empty(tmp_path):
    path = tmp_path / "runs.ndjson"
    assert runlog.read_runs(path) == []
    assert runlog.run_detail(path, "r1") is None
    assert runlog.last_success(path) == {}
    assert runlog.duration_history(path) == {}
    runlog.prune(path)                          # no-op, must not create the file
    assert not path.exists()


def test_aborted_and_running_classification(tmp_path):
    path = tmp_path / "runs.ndjson"
    # a pid that cannot exist -> the crashed run reads as aborted
    _write_run(path, "r1", pid=2**31 - 1, end=False)
    assert runlog.read_runs(path)[0]["status"] == "aborted"
    # our own pid is alive -> running
    path2 = tmp_path / "runs2.ndjson"
    _write_run(path2, "r1", pid=os.getpid(), end=False)
    assert runlog.read_runs(path2)[0]["status"] == "running"


def test_run_id_is_timestamp_sortable():
    assert runlog.make_run_id(4711).endswith("-4711")
    ts = runlog.make_run_id(1).split("-")[0]
    assert len(ts) == 23 and ts[15] == "."   # 20260704T101112.004711Z (µs)


def test_run_id_names_the_host_and_keeps_the_pid_last():
    """`<ts>-<host>-<pid>`. The pid stays the last `-` field because
    `_run_start` recovers it from the id when a concurrent prune ate the
    run-start, so a host name containing `-` must not hide it."""
    dt = datetime(2026, 7, 4, 10, 11, 12, 4711, tzinfo=timezone.utc)
    assert runlog.make_run_id(4711, dt, host="lagen") \
        == "20260704T101112.004711Z-lagen-4711"
    assert runlog.run_host("20260704T101112.004711Z-lagen-4711") == "lagen"
    hyphenated = runlog.make_run_id(4711, dt, host="staffan-desktop")
    assert runlog.run_host(hyphenated) == "staffan-desktop"
    assert int(hyphenated.rpartition("-")[2]) == 4711
    # a legacy two-field id names no host, and every reader must admit that
    assert runlog.run_host("20260704T101112.004711Z-4711") is None
    # the real host name is a bare label: non-empty, and the dot-qualified
    # tail dropped, so run_host can never confuse it with the timestamp field
    assert runlog.this_host() and "." not in runlog.this_host()


def test_a_foreign_hosts_unfinished_run_is_not_classified_by_pid(tmp_path):
    """A dev-built corpus is rsynced to prod, so prod's ledger holds dev's runs.
    Reading dev's pid against prod's /proc compares unrelated numbers, so a
    run from another host reports what the ledger proves -- `incomplete`."""
    path = tmp_path / "runs.ndjson"
    # our own live pid, but minted on another machine: must NOT read "running"
    _write_run(path, runlog.make_run_id(os.getpid(), host="someotherbox"),
               pid=os.getpid(), end=False)
    assert runlog.read_runs(path)[0]["status"] == "incomplete"
    assert runlog.read_runs(path)[0]["host"] == "someotherbox"
    # the same pid minted here is still classified by the process table
    path2 = tmp_path / "runs2.ndjson"
    _write_run(path2, runlog.make_run_id(os.getpid()), pid=os.getpid(), end=False)
    assert runlog.read_runs(path2)[0]["status"] == "running"


def test_run_id_unique_within_process_second():
    # two runs in the same process+second must not collide (µs differentiates)
    dt = datetime(2026, 7, 4, 10, 11, 12, 4711, tzinfo=timezone.utc)
    other = datetime(2026, 7, 4, 10, 11, 12, 9999, tzinfo=timezone.utc)
    assert runlog.make_run_id(1, dt) != runlog.make_run_id(1, other)


def test_apply_outcomes_sets_on_error_clears_on_success(tmp_path):
    path = tmp_path / "errors.json"
    data = runlog.apply_outcomes(
        path, "sfs", errors=[("parse", "1999:175", "ValueError: boom", "Traceback…")],
        done=[], run="r1", t="2026-07-04T10:00:00Z")
    key = "sfs/parse/1999:175"
    assert data[key]["error"] == "ValueError: boom"
    assert data[key]["traceback"] == "Traceback…"
    assert data[key]["run"] == "r1" and data[key]["t"] == "2026-07-04T10:00:00Z"
    assert runlog.read_errors(path) == data

    # a later successful run heals the entry; unrelated entries survive
    runlog.apply_outcomes(path, "sfs",
                          errors=[("parse", "2018:585", "KeyError: x", "tb")],
                          done=[("parse", "1999:175")], run="r2")
    data = runlog.read_errors(path)
    assert key not in data
    assert "sfs/parse/2018:585" in data
    # clearing a never-failed doc is a no-op, not an error (self-healing)
    runlog.apply_outcomes(path, "sfs", errors=[], done=[("parse", "1736:0123")],
                          run="r3")


def test_traceback_capping(tmp_path):
    path = tmp_path / "errors.json"
    long_tb = "x" * 10000 + "TAIL"
    errors = [("parse", "bf%d" % i, "Boom", long_tb) for i in range(4)]
    data = runlog.apply_outcomes(path, "sfs", errors=errors, done=[], run="r1",
                                 tb_cap=4096, sample_limit=2)
    # first sample_limit failures keep a (tail-truncated) traceback
    for i in (0, 1):
        tb = data["sfs/parse/bf%d" % i]["traceback"]
        assert len(tb) == 4096 and tb.endswith("TAIL")
    # later ones store the error line only
    for i in (2, 3):
        assert data["sfs/parse/bf%d" % i]["traceback"] is None
    # the sample budget is per (source, stage): another stage gets its own
    data = runlog.apply_outcomes(
        path, "sfs", errors=[("generate", "bf0", "Boom", "tb")], done=[],
        run="r1", sample_limit=2)
    assert data["sfs/generate/bf0"]["traceback"] == "tb"


def test_update_status_cell(tmp_path):
    path = tmp_path / "status.json"
    cell = {"total": 100, "fresh": 98, "stale": 0, "missing": 0, "failed": 2,
            "empty": 0, "run": "r1"}
    runlog.update_status_cell(path, "sfs", "parse", cell, t="2026-07-04T10:00:00Z")
    data = runlog.update_status_cell(path, "sfs", "generate",
                                     {"total": 100, "fresh": 100, "run": "r1"},
                                     t="2026-07-04T11:00:00Z")
    assert data["sfs"]["parse"] == {**cell, "t": "2026-07-04T10:00:00Z"}
    assert data["sfs"]["generate"]["t"] == "2026-07-04T11:00:00Z"
    assert data["_updated"] == "2026-07-04T11:00:00Z"        # advances with each write
    assert runlog.read_status(path) == data


def test_prune_keeps_last_n_and_trailing_incomplete(tmp_path):
    path = tmp_path / "runs.ndjson"
    for i in range(7):
        _write_run(path, "r%d" % i, segments=[("parse", "sfs", 1.0, 0)])
    _write_run(path, "tail", end=False, segments=[("parse", "dv", 1.0, 0)])
    runlog.prune(path, keep=3)
    runs = runlog.read_runs(path)
    # newest-first: the trailing incomplete run, then the last 3 complete ones
    assert [r["run"] for r in runs] == ["tail", "r6", "r5", "r4"]
    assert runs[0]["status"] == "aborted"       # pid 4711 is not ours
    # the incomplete run kept its segment lines, not just the start
    assert runlog.run_detail(path, "tail")["segments"][0]["source"] == "dv"
    # every surviving line is valid JSON (the rewrite didn't split lines)
    for line in path.read_text().splitlines():
        json.loads(line)


def test_torn_last_line_tolerated(tmp_path):
    # a crash mid-append leaves a partial JSON line at the end of the ledger; it
    # must be dropped, not raise, or every subsequent read (and prune, run at the
    # start of every build) would 500 / brick the build
    path = tmp_path / "runs.ndjson"
    _write_run(path, "r1", segments=[("parse", "sfs", 1.0, 0)])
    with path.open("a", encoding="utf-8") as f:
        f.write('{"event": "run-start", "run": "r2", "pi')   # torn append
    runs = runlog.read_runs(path)
    assert [r["run"] for r in runs] == ["r1"]                # r1 intact, torn line gone
    runlog.prune(path)                                       # must not raise
    assert [r["run"] for r in runlog.read_runs(path)] == ["r1"]
    # prune rewrote the ledger without the torn line -- every surviving line valid
    for line in path.read_text().splitlines():
        json.loads(line)


def test_torn_middle_line_still_raises(tmp_path):
    # corruption anywhere but the last line is a real integrity failure
    path = tmp_path / "runs.ndjson"
    _write_run(path, "r1", segments=[("parse", "sfs", 1.0, 0)])
    lines = path.read_text().splitlines()
    lines[1] = '{"event": "segm'                             # break a middle line
    path.write_text("\n".join(lines) + "\n")
    with pytest.raises(json.JSONDecodeError):
        runlog.read_runs(path)
    with pytest.raises(json.JSONDecodeError):
        runlog.prune(path)


def test_duration_history_regression_flag(tmp_path):
    path = tmp_path / "runs.ndjson"
    for i, secs in enumerate([1.0, 1.0, 1.0, 10.0]):
        _write_run(path, "r%d" % i, segments=[("parse", "sfs", secs, 0),
                                              ("parse", "dv", 1.0, 0)])
    hist = runlog.duration_history(path)
    assert hist[("parse", "sfs")]["secs"] == [1.0, 1.0, 1.0, 10.0]
    # `_write_run` reports ran=10 on every segment, so the series is a rate:
    # 10 s over 10 documents is 1.0 s/doc against a 0.1 s/doc median
    assert hist[("parse", "sfs")]["rate"] is True
    assert hist[("parse", "sfs")]["latest"] == 1.0
    assert hist[("parse", "sfs")]["ratio"] == 10.0
    assert hist[("parse", "sfs")]["regression"] is True
    assert hist[("parse", "dv")]["regression"] is False
    # n limits the window (and can flip the verdict: last 1 sample has median 10)
    assert runlog.duration_history(path, n=2)[("parse", "sfs")]["secs"] == [1.0, 10.0]
    assert runlog.duration_history(path, n=1)[("parse", "sfs")]["regression"] is False


def test_a_bigger_run_is_not_a_slower_one(tmp_path):
    """Runs are not the same size. A whole-site generate of 329,126 pages and a
    one-page generate are both `generate`; against a median dominated by the
    small ones the big one measured 285x, which is a fact about the corpus, not
    the code. The comparison is per document, so equal rates never flag."""
    path = tmp_path / "runs.ndjson"
    runlog.emit_run_start(path, "r0", ["lagen", "all", "generate"], 4711)
    for i, (secs, ran) in enumerate([(1.0, 1), (1.0, 1), (1400.0, 1400)]):
        runlog.emit_segment(path, "r0", "generate", "__site__", secs,
                            total=ran, ran=ran, errors=0, status="ok",
                            t="2026-07-04T10:00:0%dZ" % i)
    hist = runlog.duration_history(path)[("generate", "__site__")]
    assert hist["rate"] is True
    assert hist["ratio"] == 1.0 and hist["regression"] is False

    # a sample that carries no count cannot yield a rate; it is dropped rather
    # than dragging the whole key back to raw seconds, where the big run reads
    # as a huge regression against the small ones
    runlog.emit_segment(path, "r0", "generate", "__site__", 0.0, total=None,
                        ran=None, errors=0, status="ok", t="2026-07-04T10:00:04Z")
    runlog.emit_segment(path, "r0", "generate", "__site__", 1400.0, total=1400,
                        ran=1400, errors=0, status="ok", t="2026-07-04T10:00:05Z")
    hist = runlog.duration_history(path)[("generate", "__site__")]
    assert hist["rate"] is True and hist["regression"] is False


def test_last_success_ignores_skips_and_errors(tmp_path):
    path = tmp_path / "runs.ndjson"
    _write_run(path, "r1", base_t="2026-07-04T10:00:0",
               segments=[("parse", "sfs", 3.0, 0)])
    _write_run(path, "r2", base_t="2026-07-04T11:00:0",
               segments=[("parse", "sfs", 3.0, 2),                 # errors: no
                         ("parse", "dv", 0.0, 0, "skipped")])      # skipped: no
    ls = runlog.last_success(path)
    assert ls[("parse", "sfs")] == "2026-07-04T10:00:01Z"   # r1's segment, not r2's
    assert ("parse", "dv") not in ls


def test_a_run_whose_start_a_concurrent_prune_ate_is_damaged_not_fatal(tmp_path):
    # prune() rewrites the whole ledger from a snapshot, so a second `lagen`
    # starting inside that window loses its run-start line (the module docstring
    # accepts the race). Reading that group used to raise StopIteration, which
    # took `lagen all runs` and the ops dashboard down for every *other* run in
    # the ledger too.
    path = tmp_path / "runs.ndjson"
    _write_run(path, "r1", segments=[("parse", "sfs", 3.0, 0)])
    runlog.emit_segment(path, "20260724T071444.323709Z-2206569", "parse", "dv",
                        5.0, errors=2, status="errors", t="2026-07-24T07:14:49Z")
    runlog.emit_run_end(path, "20260724T071444.323709Z-2206569", 5.4, ok=False,
                        errors=2, t="2026-07-24T07:14:50Z")

    runs = runlog.read_runs(path)
    assert [r["status"] for r in runs] == ["damaged", "complete"]
    headless = runs[0]
    assert headless["argv"] is None                  # the command line is lost
    assert headless["pid"] == 2206569                # but the run id still has it
    assert headless["t"] == "2026-07-24T07:14:49Z"   # ... and the first event's time
    assert headless["errors"] == 2 and headless["sources"] == ["dv"]
    assert runlog.run_detail(path, "20260724T071444.323709Z-2206569")["status"] \
        == "damaged"
