"""The editor's REST surface (accommodanda/api/auth.py + edit.py) over FastAPI's
TestClient: the auth gate, and a full login -> edit -> cart -> commit round-trip
with the page rebuild stubbed (the real relate/generate is build's concern)."""

import subprocess

import pytest
from fastapi import HTTPException
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
    # TestClient talks plain http; a Secure cookie would never be replayed by
    # its cookie jar, so tests run as the plain-http dev case does.
    monkeypatch.setattr(config, "COOKIE_SECURE", False)
    # a fresh rate limiter per test -- the real one is a module-level
    # singleton so attempts would otherwise bleed across tests (every
    # TestClient request looks like it comes from the same fake IP)
    monkeypatch.setattr(auth, "_login_limiter", auth._RateLimiter())
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


# --------------------------------------------------------------------------
# login rate limiting (_RateLimiter + the concurrency semaphore)
# --------------------------------------------------------------------------

def test_login_rate_limited_after_repeated_failures(env):
    c = TestClient(api.app)
    for _ in range(auth._LOGIN_FREE_ATTEMPTS):
        r = c.post("/api/v1/auth/login",
                   json={"username": "anna", "password": "wrong"})
        assert r.status_code == 401
    # the free quota is spent -- the next attempt is throttled, not even
    # given the chance to fail on the password
    limited = c.post("/api/v1/auth/login",
                     json={"username": "anna", "password": "wrong"})
    assert limited.status_code == 429
    assert "Retry-After" in limited.headers


def test_rate_limiter_tracks_keys_independently():
    # a direct unit test of _RateLimiter itself -- exercised over HTTP, a
    # single TestClient always presents the same fake IP, so the (ip, ...)
    # key would confound a from-scratch (user, ...) key in the same request
    # stream. The limiter's job is to keep unrelated keys independent, which
    # is easiest to prove directly.
    limiter = auth._RateLimiter()
    for _ in range(auth._LOGIN_FREE_ATTEMPTS):
        limiter.check(("user", "alice"))       # within quota -- no raise
    with pytest.raises(HTTPException) as exc:
        limiter.check(("user", "alice"))
    assert exc.value.status_code == 429
    # bob's key is untouched by alice's history
    limiter.check(("user", "bob"))
    limiter.check(("ip", "10.0.0.1"))


def test_login_success_resets_the_rate_limit(env):
    c = TestClient(api.app)
    for _ in range(auth._LOGIN_FREE_ATTEMPTS - 1):
        c.post("/api/v1/auth/login", json={"username": "anna", "password": "wrong"})
    assert _login(c).status_code == 200
    c.post("/api/v1/auth/logout")
    # the successful login reset anna's counter -- back to a fresh quota
    assert c.post("/api/v1/auth/login",
                  json={"username": "anna", "password": "wrong"}).status_code == 401


def test_login_concurrency_cap_rejects_beyond_the_semaphore(env, monkeypatch):
    # simulate the semaphore already being fully checked out by concurrent
    # requests: the next login must be rejected before any pbkdf2 work runs,
    # rather than queueing indefinitely in the sync threadpool
    for _ in range(auth._LOGIN_MAX_CONCURRENT):
        assert auth._LOGIN_SEM.acquire(blocking=False)
    try:
        c = TestClient(api.app)
        r = c.post("/api/v1/auth/login",
                   json={"username": "anna", "password": "hunter2"})
        assert r.status_code == 429
    finally:
        for _ in range(auth._LOGIN_MAX_CONCURRENT):
            auth._LOGIN_SEM.release()


# --------------------------------------------------------------------------
# session revocation: a password change invalidates outstanding sessions
# --------------------------------------------------------------------------

def test_password_change_revokes_outstanding_sessions(env, monkeypatch):
    c = TestClient(api.app)
    _login(c)
    assert c.get("/api/v1/auth/me").status_code == 200
    # anna's password (and so her pwhash) changes -- e.g. after a suspected
    # compromise -- with no server-side session table to also update
    monkeypatch.setitem(config.EDITORS, "anna",
                        {**config.EDITORS["anna"],
                         "pwhash": auth.hash_password("newpassword", rounds=1000)})
    # the old cookie's embedded pwhash fingerprint no longer matches -> 401,
    # even though the signature and expiry are still perfectly valid
    assert c.get("/api/v1/auth/me").status_code == 401
