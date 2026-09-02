"""The crop-review editor's HTTP surface (ferenda/api/graphics.py): the
auth gate, the deliberate `publishable` bypass for a logged-in editor, and the
conflict check that stops a reviewer signing off a crop that moved under them.
"""

import json
import subprocess

import pytest
from fastapi.testclient import TestClient

from ferenda import config
from ferenda.api import app as api
from ferenda.api import auth, editcart, graphicsedit
from ferenda.lib import annstore, facsimile

LAYER = {
    "meta": {"status": "generated", "model": "moonshotai/Kimi-K2.6",
             "generated": "2026-08-20", "inputs": {},
             "uri": "https://lagen.nu/2006:171"},
    "g-pending": {"sfs": "2006:1574", "page": 2, "alt": "Tabell",
                  "bbox": [53, 46, 422, 389],
                  "identity": {"sort": "tabell", "anchor": "bidrag",
                               "code": None, "occurrence": 1}},
}


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          text=True, capture_output=True).stdout.strip()


@pytest.fixture
def webenv(tmp_path, monkeypatch):
    """A wiki repo holding one unreviewed layer, a configured editor, an
    isolated cart, and a page renderer stubbed off the real PDF corpus."""
    root = tmp_path / "wiki"
    monkeypatch.setattr(annstore, "ROOT", root / "ann")
    p = annstore.path("sfs", "2006:171", ".graphics")
    p.parent.mkdir(parents=True)
    annstore.dump(p, LAYER)
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Seed")
    _git(root, "config", "user.email", "seed@example.org")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "seed")

    monkeypatch.setattr(config, "WIKI_ROOT", root)
    monkeypatch.setattr(config, "EDITOR_SECRET", "test-signing-key")
    monkeypatch.setattr(config, "COOKIE_SECURE", False)
    monkeypatch.setattr(config, "EDITORS", {"anna": {
        "name": "Anna Ek", "email": "anna@example.org",
        "pwhash": auth.hash_password("hunter2", rounds=1000)}})
    monkeypatch.setattr(editcart, "EDITS", tmp_path / "edits")
    monkeypatch.setattr(graphicsedit, "_page_count", lambda src: 7)
    png = tmp_path / "page.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")
    monkeypatch.setattr(facsimile, "cached",
                        lambda *a, **kw: png)
    monkeypatch.setattr("ferenda.api.facsimiles.sfs_source_pdf",
                        lambda src: tmp_path / "fake.pdf")
    return p


def _login(c):
    return c.post("/internal-api/v1/auth/login",
                  json={"username": "anna", "password": "hunter2"})


CROP = {"sfs": "2006:1574", "page": 2, "bbox": "53,46,422,389"}


@pytest.mark.parametrize("path, params", [
    ("/internal-api/v1/graphics/queue", {}),
    ("/internal-api/v1/graphics/review", {}),
    ("/internal-api/v1/graphics/crop", CROP),
    ("/internal-api/v1/graphics/page", {"sfs": "2006:1574", "page": 2}),
    ("/internal-api/v1/graphics/pagesize", {"sfs": "2006:1574", "page": 2}),
])
def test_every_read_route_refuses_an_anonymous_caller(webenv, path, params):
    assert TestClient(api.app).get(path, params=params).status_code == 401


def test_the_mutating_route_refuses_an_anonymous_caller(webenv):
    """The route where a dropped gate would actually cost something: an
    anonymous caller writing into an editor's cart."""
    r = TestClient(api.app).post(
        "/internal-api/v1/graphics/cart",
        json={"ref": "2006:171", "anchor": "g-pending", "page": 2,
              "bbox": None, "verified": True, "base_sha": "x"})
    assert r.status_code == 401


def test_the_queue_lists_the_unreviewed_crop(webenv):
    c = TestClient(api.app)
    _login(c)
    pending = c.get("/internal-api/v1/graphics/queue").json()["pending"]
    assert [e["anchor"] for e in pending] == ["g-pending"]
    assert pending[0]["sort"] == "tabell" and pending[0]["pages"] == 7


def test_an_editor_sees_a_crop_the_public_is_refused(webenv):
    """The bypass this editor exists for -- and its exact limit."""
    c = TestClient(api.app)
    _login(c)
    assert c.get("/internal-api/v1/graphics/crop", params=CROP).status_code == 200
    anon = TestClient(api.app)
    assert anon.get("/api/v1/sfs-graphic",
                    params={"uri": "https://lagen.nu/2006:171",
                            "node": "g-pending"}).status_code == 404


@pytest.mark.parametrize("bbox", ["1,2,3", "a,b,c,d", "9,9,1,1"])
def test_a_malformed_rectangle_is_a_400(webenv, bbox):
    c = TestClient(api.app)
    _login(c)
    assert c.get("/internal-api/v1/graphics/crop",
                 params={**CROP, "bbox": bbox}).status_code == 400


def test_an_unknown_gap_is_a_404(webenv):
    c = TestClient(api.app)
    _login(c)
    r = c.post("/internal-api/v1/graphics/cart",
               json={"ref": "2006:171", "anchor": "g-gone", "page": 2,
                     "bbox": None, "verified": True, "base_sha": "x"})
    assert r.status_code == 404


def test_carting_a_decision_and_the_stale_check(webenv):
    c = TestClient(api.app)
    _login(c)
    entry = c.get("/internal-api/v1/graphics/queue").json()["pending"][0]
    body = {"ref": entry["ref"], "anchor": entry["anchor"], "page": 2,
            "bbox": [60, 50, 400, 380], "verified": True,
            "base_sha": entry["base_sha"]}
    assert c.post("/internal-api/v1/graphics/cart", json=body).json() == {"carted": 1}
    assert c.post("/internal-api/v1/graphics/cart",
                  json={**body, "base_sha": "stale"}).status_code == 409
    # carting writes to the cart store only -- the layer is untouched until commit
    assert "verified" not in json.loads(webenv.read_text())["g-pending"]
