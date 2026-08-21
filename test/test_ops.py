"""The ops dashboard (accommodanda/api/ops.py) over FastAPI's TestClient: a
fixture ledger/errors/status written into tmp_path with the runlog emit_*
helpers and the ops path constants monkeypatched. The dashboard rides the inline
editor's session (auth.require_editor), so tests log in as an editor rather than
present a token. No network, no build driver."""

import json
import re
import os

import pytest
from fastapi.testclient import TestClient
from opensearchpy.exceptions import OpenSearchException

from accommodanda import config
from accommodanda.api import app as api
from accommodanda.api import auth, db, ops
from accommodanda.lib import catalog, runlog


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


@pytest.fixture(autouse=True)
def _stub_index(monkeypatch):
    """Keep the ops tests hermetic (the docstring's "no network" promise): the
    overview reads _index.store_size(), which would otherwise open a cluster
    connection. Stub a fixed byte count; individual tests override as needed."""
    monkeypatch.setattr(ops._index, "store_size", lambda: 42_000_000)


def _health_columns(body):
    """The corpus & pipeline table's column headers. Asserting on a bare
    `<th>x</th>` would also match a row heading (the "other steps" table heads
    each row with its step name) and the explanatory prose under either."""
    thead = re.search(r"<thead>(.*?)</thead>", body, re.S)
    return re.findall(r"<th[^>]*>(.*?)</th>", thead.group(1)) if thead else []


def _login(client):
    """Log the TestClient in as editor `anna` (sets the session cookie on its
    jar) and return it, so subsequent /ops requests carry the editor session."""
    assert client.post("/internal-api/v1/auth/login",
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
    monkeypatch.setattr(db, "CATALOG", tmp_path / "catalog.sqlite")  # absent
    return {"good": good, "bad": bad, "dir": build}


@pytest.fixture
def client(ledger):
    return _login(TestClient(api.app))


# -- auth -----------------------------------------------------------------

def test_unauthenticated_401(ledger):
    # /ops rides the editor session: no cookie -> 401 (log in), like the edit routes
    assert TestClient(api.app).get("/ops").status_code == 401


def test_trailing_slash_redirects_to_the_dashboard(ledger):
    # the dashboard is registered at exactly /ops, and serve() mounts the static
    # site at "/", so Starlette's own redirect_slashes never fires and /ops/ 404s.
    # editor.js sent the user to /ops/ after login, so every successful login
    # ended on that 404; this is the server half of the fix.
    r = TestClient(api.app).get("/ops/", follow_redirects=False)
    assert r.status_code == 308
    assert r.headers["location"] == "/ops"


def test_editing_disabled_403(ledger, monkeypatch):
    # an unset editor_secret disables editing wholesale -- and the dashboard with it
    monkeypatch.setattr(config, "EDITOR_SECRET", None)
    r = TestClient(api.app).get("/ops")
    assert r.status_code == 403
    assert "editor_secret" in r.json()["detail"]


# -- /ops overview --------------------------------------------------------

def test_overview_renders_one_table_of_corpus_and_pipeline(client):
    """Corpus size and pipeline health were two tables keyed by the same
    sources; they are one now, and its cells come from the run ledger."""
    r = client.get("/ops")
    assert r.status_code == 200
    body = r.text
    assert "corpus &amp; pipeline" in body
    assert "sfs" in body and "dv" in body
    assert "1 err" in body                          # the failing sfs parse cell
    assert "1 docs failing" in body
    assert 'http-equiv="refresh"' in body
    # the two old headings are gone, not merely renamed around
    assert "<h2>pipeline health</h2>" not in body
    assert "<h2>corpus</h2>" not in body
    # the catalog-delta table went with them -- it is a cell now
    assert "<h2>catalog delta</h2>" not in body


def test_overview_reads_stage_cells_from_the_ledger_not_the_snapshot(
        tmp_path, monkeypatch, editor_auth):
    """relate/index/dump/generate never write a per-source status cell -- only
    a full-source parse/download does -- so reading status.json left those four
    columns blank for every source while the ledger held 23 runs of each."""
    build = tmp_path / ".build"
    build.mkdir()
    runs = build / "runs.ndjson"
    run = runlog.make_run_id(os.getpid())
    runlog.emit_run_start(runs, run, ["lagen", "all", "relate"], os.getpid())
    for step in ("relate", "index", "dump", "generate"):
        runlog.emit_segment(runs, run, step, "sfs", 3.0, total=9, ran=9,
                            errors=0, status="ok")
    runlog.emit_run_end(runs, run, 12.0, ok=True, errors=0)
    monkeypatch.setattr(ops, "RUNS", runs)
    monkeypatch.setattr(ops, "ERRORS", build / "errors.json")
    monkeypatch.setattr(ops, "STATUS", build / "status.json")   # deliberately absent
    monkeypatch.setattr(db, "CATALOG", tmp_path / "catalog.sqlite")
    body = _login(TestClient(api.app)).get("/ops").text
    for step in ("relate", "index", "dump", "generate"):
        assert "<th>%s</th>" % step in body
    # four filled cells, from a snapshot that does not exist at all
    assert body.count("9/9") == 4


def test_overview_lists_recent_runs(client, ledger):
    body = client.get("/ops").text
    assert ledger["good"] in body and ledger["bad"] in body


# -- system section (version / wiki / index size) -------------------------

def test_system_section_renders_version_wiki_and_index_size(client, monkeypatch):
    """The system facts are one line above the table now, not a table of their
    own -- and it names the host, so a dashboard open on two machines says
    which one it is describing."""
    monkeypatch.setattr("accommodanda.api.ops.git.push_state", lambda repo: (2, True))
    body = client.get("/ops").text
    assert "lagen-wiki" in body and runlog.this_host() in body
    assert "2 unpushed" in body and "uncommitted" in body
    assert "42.0 MB" in body                         # _human_bytes(42_000_000)


def test_system_section_wiki_up_to_date(client, monkeypatch):
    monkeypatch.setattr("accommodanda.api.ops.git.push_state", lambda repo: (0, False))
    assert "up to date" in client.get("/ops").text


def test_system_section_index_unavailable_when_cluster_down(client, monkeypatch):
    def boom():
        raise OpenSearchException("cluster down")
    monkeypatch.setattr(ops._index, "store_size", boom)
    assert "unavailable" in client.get("/ops").text


def test_corpus_section_lists_docs_and_size_per_source(client, tmp_path, monkeypatch):
    cat = tmp_path / "corpus.sqlite"
    con = catalog.connect(cat)
    con.executemany(
        "INSERT INTO documents (uri, source, kind, label, title, path, art_size) "
        "VALUES (?,?,?,?,?,?,?)",
        [("u1", "sfs", "law", "L1", "T1", "p1", 1000),
         ("u2", "sfs", "law", "L2", "T2", "p2", 2000),
         ("u3", "dv", "case", "C1", "T3", "p3", 500)])
    con.commit()
    con.close()
    monkeypatch.setattr(db, "CATALOG", cat)
    body = client.get("/ops").text
    assert "3.0 kB" in body                          # sfs: 1000 + 2000
    assert "500 B" in body                           # dv
    assert "3.5 kB" in body                          # total 3500
    # the counts sit on the same row as that source's stage cells
    assert "corpus &amp; pipeline" in body


def test_a_one_source_step_is_listed_not_given_a_column(tmp_path, monkeypatch,
                                                        editor_auth):
    """`versions` is sfs's alone and `compute` is stats'. As columns they were
    18 empty cells and one number each, so steps off the common pipeline are
    listed under the table instead."""
    build = tmp_path / ".build"
    build.mkdir()
    runs = build / "runs.ndjson"
    run = runlog.make_run_id(os.getpid())
    runlog.emit_run_start(runs, run, ["lagen", "sfs", "versions"], os.getpid())
    runlog.emit_segment(runs, run, "versions", "sfs", 7.0, total=4, ran=4,
                        errors=0, status="ok")
    runlog.emit_segment(runs, run, "parse", "dv", 1.0, total=2, ran=2,
                        errors=0, status="ok")
    runlog.emit_run_end(runs, run, 8.0, ok=True, errors=0)
    monkeypatch.setattr(ops, "RUNS", runs)
    monkeypatch.setattr(ops, "ERRORS", build / "errors.json")
    monkeypatch.setattr(ops, "STATUS", build / "status.json")
    monkeypatch.setattr(db, "CATALOG", tmp_path / "catalog.sqlite")
    body = _login(TestClient(api.app)).get("/ops").text
    assert "versions" not in _health_columns(body)  # not a column
    assert "parse" in _health_columns(body)         # the common spine still is
    assert "other steps" in body and "4/4" in body  # but its result is shown


# -- /ops/runs ------------------------------------------------------------

def test_runs_table_newest_first(client, ledger):
    r = client.get("/ops/runs")
    assert r.status_code == 200
    body = r.text
    # the command without its `lagen` head -- every row would carry that word
    assert "sfs parse" in body
    assert "did what" in body and "sources" in body
    # the failing run is newer, so it appears before the good one
    assert body.index(ledger["bad"]) < body.index(ledger["good"])


def test_runs_table_names_the_host_and_will_not_guess_a_foreign_pid(
        tmp_path, monkeypatch, editor_auth):
    """Runs happen on dev and on prod, and the corpus is rsynced between them,
    so one ledger holds both. A run from the other machine cannot be classified
    by pid here -- /proc is this host's."""
    build = tmp_path / ".build"
    build.mkdir()
    runs = build / "runs.ndjson"
    away = runlog.make_run_id(os.getpid(), host="otherbox")
    runlog.emit_run_start(runs, away, ["lagen", "all", "all"], os.getpid())
    runlog.emit_segment(runs, away, "parse", "sfs", 1.0, total=1, ran=1,
                        errors=0, status="ok")            # no run-end: unfinished
    monkeypatch.setattr(ops, "RUNS", runs)
    monkeypatch.setattr(ops, "ERRORS", build / "errors.json")
    monkeypatch.setattr(ops, "STATUS", build / "status.json")
    monkeypatch.setattr(db, "CATALOG", tmp_path / "catalog.sqlite")
    body = _login(TestClient(api.app)).get("/ops/runs").text
    assert "otherbox" in body
    assert "ran elsewhere" in body
    # our live pid must not have been read as "running" for another host's run.
    # Assert on the outcome cell, not the page: the table's own note explains
    # the running/aborted distinction in prose.
    outcomes = re.findall(r'<td class="(\w+)">(?:complete|running|aborted'
                          r'|incomplete|errors|damaged)', body)
    assert outcomes == ["incomplete"], outcomes


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
    monkeypatch.setattr(db, "CATALOG", tmp_path / "catalog.sqlite")
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
