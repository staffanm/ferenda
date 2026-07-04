"""The editor's REST surface (accommodanda/api/auth.py + edit.py) over FastAPI's
TestClient: the auth gate, and a full login -> edit -> cart -> commit round-trip
with the page rebuild stubbed (the real relate/generate is build's concern)."""

import subprocess

import pytest
from fastapi.testclient import TestClient

from accommodanda import config
from accommodanda.api import app as api
from accommodanda.api import auth, edit, editcart
from accommodanda.wiki import parse as wiki


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          text=True, capture_output=True).stdout.strip()


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A configured editor (`anna`), a git content repo, an isolated cart, and a
    rebuild stub that records what it was asked to regenerate."""
    root = tmp_path / "wiki"
    (root / "commentary" / "sfs" / "1915").mkdir(parents=True)
    (root / "commentary" / "sfs" / "1915" / "218.md").write_text(
        "---\nannotates: 1915:218\n---\n## 1 §\n\nUrsprunglig.\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Seed")
    _git(root, "config", "user.email", "seed@example.org")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "seed")

    monkeypatch.setattr(config, "EDITOR_SECRET", "test-signing-key")
    monkeypatch.setattr(config, "EDITORS", {"anna": {
        "name": "Anna Ek", "email": "anna@example.org",
        "pwhash": auth.hash_password("hunter2", rounds=1000)}})
    monkeypatch.setattr("accommodanda.config.WIKI_ROOT", root)
    monkeypatch.setattr(editcart, "EDITS", tmp_path / "edits")

    rebuilt = []
    monkeypatch.setattr(edit, "_rebuild",
                        lambda changes: (rebuilt.extend(changes) or ["/1915:218"]))
    wiki.kommentar_index.cache_clear()
    wiki.begrepp_index.cache_clear()
    yield root, rebuilt
    wiki.kommentar_index.cache_clear()


def _login(client):
    return client.post("/api/v1/auth/login",
                       json={"username": "anna", "password": "hunter2"})


def test_write_requires_login(env):
    c = TestClient(api.app)
    assert c.get("/api/v1/edit/region",
                 params={"kind": "kommentar", "ref": "1915:218", "anchor": "P1"}
                 ).status_code == 401
    assert c.post("/api/v1/edit/commit", json={"message": "x"}).status_code == 401


def test_login_bad_password(env):
    c = TestClient(api.app)
    assert c.post("/api/v1/auth/login",
                  json={"username": "anna", "password": "wrong"}).status_code == 401


def test_login_me_logout(env):
    c = TestClient(api.app)
    assert _login(c).json() == {"username": "anna", "name": "Anna Ek"}
    assert c.get("/api/v1/auth/me").json()["username"] == "anna"
    c.post("/api/v1/auth/logout")
    assert c.get("/api/v1/auth/me").status_code == 401


def test_full_edit_commit_flow(env):
    root, rebuilt = env
    c = TestClient(api.app)
    _login(c)

    seeded = c.get("/api/v1/edit/region",
                   params={"kind": "kommentar", "ref": "1915:218", "anchor": "P1"})
    assert seeded.json()["markdown"].startswith("## 1 §")

    r = c.post("/api/v1/edit/region", json={
        "kind": "kommentar", "ref": "1915:218", "anchor": "P1",
        "new_text": "## 1 §\n\nAnnas kommentar med [FB](sfs:1949:381).\n"})
    assert r.json() == {"cart": 1}
    assert len(c.get("/api/v1/edit/cart").json()["drafts"]) == 1

    done = c.post("/api/v1/edit/commit", json={"message": "kommentera 1 §"})
    assert done.status_code == 200
    body = done.json()
    assert body["rebuilt"] == ["/1915:218"]
    assert rebuilt == [{"kind": "kommentar", "basefile": "1915:218"}]
    assert body["sha"] == _git(root, "rev-parse", "HEAD")
    assert _git(root, "log", "-1", "--format=%an|%s") == "Anna Ek|kommentera 1 §"
    assert "Annas kommentar" in \
        (root / "commentary" / "sfs" / "1915" / "218.md").read_text()
    assert c.get("/api/v1/edit/cart").json()["drafts"] == []


def test_commit_conflict_returns_409(env):
    root, _ = env
    c = TestClient(api.app)
    _login(c)
    c.post("/api/v1/edit/region", json={
        "kind": "kommentar", "ref": "1915:218", "anchor": "P1",
        "new_text": "## 1 §\n\nAnnas.\n"})
    # a concurrent change on disk moves the region out from under the draft
    (root / "commentary" / "sfs" / "1915" / "218.md").write_text(
        "---\nannotates: 1915:218\n---\n## 1 §\n\nNågon annans.\n", encoding="utf-8")
    r = c.post("/api/v1/edit/commit", json={"message": "should 409"})
    assert r.status_code == 409
    assert r.json()["detail"]["conflicts"] == ["kommentar:1915:218#P1"]


def test_disabled_when_no_secret(env, monkeypatch):
    monkeypatch.setattr(config, "EDITOR_SECRET", None)
    c = TestClient(api.app)
    assert c.get("/api/v1/auth/me").status_code == 403
    assert c.post("/api/v1/auth/login",
                  json={"username": "anna", "password": "hunter2"}).status_code == 403
