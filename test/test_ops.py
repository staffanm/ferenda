"""The ops dashboard (accommodanda/api/ops.py) over FastAPI's TestClient: a
fixture ledger/errors/status written into tmp_path with the runlog emit_*
helpers, the ops path constants + config.OPS_TOKEN monkeypatched. No network,
no build driver."""

import json
import os

import pytest
from fastapi.testclient import TestClient

from accommodanda import config
from accommodanda.api import app as api
from accommodanda.api import ops
from accommodanda.lib import runlog

AUTH = ("ops", "s3cret")


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    """A small but realistic .build ledger: one clean run and one run with a
    failing sfs parse, a matching errors.json, and a status snapshot with one
    failed cell. Returns the run ids so tests can address the detail view."""
    build = tmp_path / ".build"
    build.mkdir()
    runs, errors, status = build / "runs.ndjson", build / "errors.json", build / "status.json"

    good = runlog.make_run_id(os.getpid())
    runlog.emit_run_start(runs, good, ["lagen", "sfs", "parse"], os.getpid())
    runlog.emit_segment(runs, good, "parse", "sfs", 12.5, total=3, ran=3,
                        errors=0, status="ok", slowest=[["2018:585", 8.0]])
    runlog.emit_segment(runs, good, "parse", "dv", 0.0, total=5, ran=0,
                        errors=0, skipped_fresh=5, status="skipped")
    runlog.emit_run_end(runs, good, 13.0, ok=True, errors=0)

    bad = runlog.make_run_id(os.getpid() + 1)          # distinct id
    runlog.emit_run_start(runs, bad, ["lagen", "sfs", "parse", "1999:9"], os.getpid())
    runlog.emit_segment(runs, bad, "parse", "sfs", 4.0, total=1, ran=1,
                        errors=1, status="errors")
    runlog.emit_run_end(runs, bad, 4.5, ok=False, errors=1)

    runlog.apply_outcomes(
        errors, "sfs",
        [("parse", "1999:9", "ValueError: broken input",
          "Traceback (most recent call last):\n  ...\nValueError: broken input")],
        [], bad)

    runlog.update_status_cell(status, "sfs", "parse",
                              {"total": 3, "fresh": 2, "stale": 0, "missing": 0,
                               "failed": 1, "empty": 0, "run": bad})
    runlog.update_status_cell(status, "dv", "parse",
                              {"total": 5, "fresh": 5, "stale": 0, "missing": 0,
                               "failed": 0, "empty": 0, "run": good})

    monkeypatch.setattr(ops, "RUNS", runs)
    monkeypatch.setattr(ops, "ERRORS", errors)
    monkeypatch.setattr(ops, "STATUS", status)
    monkeypatch.setattr(ops, "CATALOG", tmp_path / "catalog.sqlite")  # absent
    monkeypatch.setattr(config, "OPS_TOKEN", "s3cret")
    return {"good": good, "bad": bad, "dir": build}


@pytest.fixture
def client(ledger):
    return TestClient(api.app)


# -- auth -----------------------------------------------------------------

def test_unauthenticated_401(client):
    r = client.get("/ops")
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Basic"


def test_wrong_password_401(client):
    assert client.get("/ops", auth=("ops", "nope")).status_code == 401


def test_non_ascii_password_401_not_500(client):
    # secrets.compare_digest raises TypeError on a non-ASCII str; the auth gate
    # must compare on bytes so a garbage password is a clean 401, not a 500
    assert client.get("/ops", auth=("ops", "pässwörd")).status_code == 401


def test_token_unset_403(client, monkeypatch):
    monkeypatch.setattr(config, "OPS_TOKEN", None)
    r = client.get("/ops", auth=AUTH)
    assert r.status_code == 403
    assert "ops_token" in r.json()["detail"]


# -- /ops overview --------------------------------------------------------

def test_overview_renders_matrix_and_failures(client):
    r = client.get("/ops", auth=AUTH)
    assert r.status_code == 200
    body = r.text
    assert "pipeline health" in body
    assert "sfs" in body and "dv" in body
    assert "1 failed" in body                       # the failed sfs parse cell
    assert "1 docs failing overall" in body
    assert 'http-equiv="refresh"' in body
    # catalog absent -> a rendered empty-state, not a 503
    assert "catalog not built" in body


def test_overview_lists_recent_runs(client, ledger):
    body = client.get("/ops", auth=AUTH).text
    assert ledger["good"] in body and ledger["bad"] in body


def test_versions_cell_renders_a_column(tmp_path, monkeypatch):
    # sfs writes a ("sfs", "versions") status cell; the matrix must grow a
    # versions column for it rather than silently hiding the cell
    build = tmp_path / ".build"
    build.mkdir()
    status = build / "status.json"
    runlog.update_status_cell(status, "sfs", "versions",
                              {"total": 4, "fresh": 4, "stale": 0, "missing": 0,
                               "failed": 0, "empty": 0, "run": "r1"})
    monkeypatch.setattr(ops, "RUNS", build / "runs.ndjson")
    monkeypatch.setattr(ops, "ERRORS", build / "errors.json")
    monkeypatch.setattr(ops, "STATUS", status)
    monkeypatch.setattr(ops, "CATALOG", tmp_path / "catalog.sqlite")
    monkeypatch.setattr(config, "OPS_TOKEN", "s3cret")
    body = TestClient(api.app).get("/ops", auth=AUTH).text
    assert "<th>versions</th>" in body


# -- /ops/runs ------------------------------------------------------------

def test_runs_table_newest_first(client, ledger):
    r = client.get("/ops/runs", auth=AUTH)
    assert r.status_code == 200
    body = r.text
    assert "lagen sfs parse" in body
    # the failing run is newer, so it appears before the good one
    assert body.index(ledger["bad"]) < body.index(ledger["good"])


# -- /ops/runs/{id} -------------------------------------------------------

def test_run_detail_shows_bars_segments_and_errors(client, ledger):
    r = client.get("/ops/runs/%s" % ledger["bad"], auth=AUTH)
    assert r.status_code == 200
    body = r.text
    assert "timings" in body and "segments" in body
    assert 'class="bar"' in body                    # proportional timing bar
    assert "ValueError: broken input" in body       # grouped run error
    assert "1999:9" in body


def test_run_detail_skipped_segment_present(client, ledger):
    body = client.get("/ops/runs/%s" % ledger["good"], auth=AUTH).text
    # the watermark-skipped dv parse must still show in the segment table
    assert "dv" in body


def test_run_detail_unknown_404(client):
    assert client.get("/ops/runs/nope-0", auth=AUTH).status_code == 404


# -- /ops/failures --------------------------------------------------------

def test_failures_lists_traceback_in_details(client):
    r = client.get("/ops/failures", auth=AUTH)
    assert r.status_code == 200
    body = r.text
    assert "1 failing docs" in body
    assert "<details>" in body and "Traceback (most recent call last)" in body


def test_failures_source_filter(client):
    assert client.get("/ops/failures", params={"source": "sfs"},
                      auth=AUTH).text.count("1999:9") >= 1
    body = client.get("/ops/failures", params={"source": "dv"}, auth=AUTH).text
    assert "0 failing docs" in body and "1999:9" not in body


def test_failures_stage_filter(client):
    body = client.get("/ops/failures", params={"stage": "generate"}, auth=AUTH).text
    assert "0 failing docs" in body


# -- empty states ---------------------------------------------------------

def test_empty_states_render_without_files(tmp_path, monkeypatch):
    empty = tmp_path / ".build"
    monkeypatch.setattr(ops, "RUNS", empty / "runs.ndjson")
    monkeypatch.setattr(ops, "ERRORS", empty / "errors.json")
    monkeypatch.setattr(ops, "STATUS", empty / "status.json")
    monkeypatch.setattr(ops, "CATALOG", tmp_path / "catalog.sqlite")
    monkeypatch.setattr(config, "OPS_TOKEN", "s3cret")
    c = TestClient(api.app)
    assert "no runs recorded yet" in c.get("/ops", auth=AUTH).text
    assert c.get("/ops/runs", auth=AUTH).status_code == 200
    assert "no matching failures" in c.get("/ops/failures", auth=AUTH).text
    # a status snapshot older than the threshold would banner; with none, no banner
    assert '<div class="banner">' not in c.get("/ops", auth=AUTH).text


def test_stale_snapshot_banner(client, ledger):
    # rewrite _updated to well past the 26h threshold
    status = ledger["dir"] / "status.json"
    data = json.loads(status.read_text())
    data["_updated"] = "2020-01-01T00:00:00Z"
    status.write_text(json.dumps(data))
    body = client.get("/ops", auth=AUTH).text
    assert '<div class="banner">' in body and "No completed run since" in body
