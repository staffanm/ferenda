"""The ops dashboard (accommodanda/api/ops.py) over FastAPI's TestClient: a
fixture ledger/errors/status written into tmp_path with the runlog emit_*
helpers and the ops path constants monkeypatched. The dashboard rides the inline
editor's session (auth.require_editor), so tests log in as an editor rather than
present a token. No network, no build driver."""

import json
import os

import pytest
from fastapi.testclient import TestClient

from accommodanda import config
from accommodanda.api import app as api
from accommodanda.api import auth, ops
from accommodanda.lib import runlog


@pytest.fixture
def editor_auth(monkeypatch):
    """Editor-session scaffolding shared by every ops test: `/ops` is gated by
    auth.require_editor, so a request is authorised by a logged-in editor cookie
    (config.EDITORS), not a token. COOKIE_SECURE off so TestClient's plain-http
    jar replays the cookie; a fresh rate limiter so login attempts don't bleed
    across tests (the real one is a module singleton keyed on the fake IP)."""
    monkeypatch.setattr(config, "EDITOR_SECRET", "test-signing-key")
    monkeypatch.setattr(config, "COOKIE_SECURE", False)
    monkeypatch.setattr(auth, "_login_limiter", auth._RateLimiter())
    monkeypatch.setattr(config, "EDITORS", {"anna": {
        "name": "Anna Ek", "email": "anna@example.org",
        "pwhash": auth.hash_password("hunter2", rounds=1000)}})


def _login(client):
    """Log the TestClient in as editor `anna` (sets the session cookie on its
    jar) and return it, so subsequent /ops requests carry the editor session."""
    assert client.post("/api/v1/auth/login",
                       json={"username": "anna", "password": "hunter2"}
                       ).status_code == 200
    return client


@pytest.fixture
def ledger(tmp_path, monkeypatch, editor_auth):
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
    return {"good": good, "bad": bad, "dir": build}


@pytest.fixture
def client(ledger):
    return _login(TestClient(api.app))


# -- auth -----------------------------------------------------------------

def test_unauthenticated_401(ledger):
    # /ops rides the editor session: no cookie -> 401 (log in), like the edit routes
    assert TestClient(api.app).get("/ops").status_code == 401


def test_editing_disabled_403(ledger, monkeypatch):
    # an unset editor_secret disables editing wholesale -- and the dashboard with it
    monkeypatch.setattr(config, "EDITOR_SECRET", None)
    r = TestClient(api.app).get("/ops")
    assert r.status_code == 403
    assert "editor_secret" in r.json()["detail"]


# -- /ops overview --------------------------------------------------------

def test_overview_renders_matrix_and_failures(client):
    r = client.get("/ops")
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
    body = client.get("/ops").text
    assert ledger["good"] in body and ledger["bad"] in body


def test_versions_cell_renders_a_column(tmp_path, monkeypatch, editor_auth):
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
    body = _login(TestClient(api.app)).get("/ops").text
    assert "<th>versions</th>" in body


# -- /ops/runs ------------------------------------------------------------

def test_runs_table_newest_first(client, ledger):
    r = client.get("/ops/runs")
    assert r.status_code == 200
    body = r.text
    assert "lagen sfs parse" in body
    # the failing run is newer, so it appears before the good one
    assert body.index(ledger["bad"]) < body.index(ledger["good"])


# -- /ops/runs/{id} -------------------------------------------------------

def test_run_detail_shows_bars_segments_and_errors(client, ledger):
    r = client.get("/ops/runs/%s" % ledger["bad"])
    assert r.status_code == 200
    body = r.text
    assert "timings" in body and "segments" in body
    assert 'class="bar"' in body                    # proportional timing bar
    assert "ValueError: broken input" in body       # grouped run error
    assert "1999:9" in body


def test_run_detail_skipped_segment_present(client, ledger):
    body = client.get("/ops/runs/%s" % ledger["good"]).text
    # the watermark-skipped dv parse must still show in the segment table
    assert "dv" in body


def test_run_detail_unknown_404(client):
    assert client.get("/ops/runs/nope-0").status_code == 404


# -- /ops/failures --------------------------------------------------------

def test_failures_lists_traceback_in_details(client):
    r = client.get("/ops/failures")
    assert r.status_code == 200
    body = r.text
    assert "1 failing docs" in body
    assert "<details>" in body and "Traceback (most recent call last)" in body


def test_failures_source_filter(client):
    assert client.get("/ops/failures", params={"source": "sfs"}
                      ).text.count("1999:9") >= 1
    body = client.get("/ops/failures", params={"source": "dv"}).text
    assert "0 failing docs" in body and "1999:9" not in body


def test_failures_stage_filter(client):
    body = client.get("/ops/failures", params={"stage": "generate"}).text
    assert "0 failing docs" in body


# -- empty states ---------------------------------------------------------

def test_empty_states_render_without_files(tmp_path, monkeypatch, editor_auth):
    empty = tmp_path / ".build"
    monkeypatch.setattr(ops, "RUNS", empty / "runs.ndjson")
    monkeypatch.setattr(ops, "ERRORS", empty / "errors.json")
    monkeypatch.setattr(ops, "STATUS", empty / "status.json")
    monkeypatch.setattr(ops, "CATALOG", tmp_path / "catalog.sqlite")
    c = _login(TestClient(api.app))
    assert "no runs recorded yet" in c.get("/ops").text
    assert c.get("/ops/runs").status_code == 200
    assert "no matching failures" in c.get("/ops/failures").text
    # a status snapshot older than the threshold would banner; with none, no banner
    assert '<div class="banner">' not in c.get("/ops").text


def test_stale_snapshot_banner(client, ledger):
    # rewrite _updated to well past the 26h threshold
    status = ledger["dir"] / "status.json"
    data = json.loads(status.read_text())
    data["_updated"] = "2020-01-01T00:00:00Z"
    status.write_text(json.dumps(data))
    body = client.get("/ops").text
    assert '<div class="banner">' in body and "No completed run since" in body
