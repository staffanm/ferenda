"""Tests for on-demand page facsimiles: the lib renderer, the disk cache, and
the API endpoint in both its documented and legacy-path forms."""

import json

import pytest
from fastapi.testclient import TestClient

from accommodanda import config
from accommodanda.api import app as api
from accommodanda.lib import compress, facsimile, layout

# a minimal one-page A4 PDF poppler accepts (blank page)
MINI_PDF = (b"%PDF-1.4\n"
            b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
            b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]>>endobj\n"
            b"trailer<</Root 1 0 R>>")

PNG_MAGIC = b"\x89PNG"


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    """A downloaded förarbete + föreskrift with one-page PDFs, and the
    facsimile cache, all under tmp_path. The harvest records are written
    through `compress.write_download` with compression forced on, matching
    production (records land as `.br`) -- padded past MIN_SIZE so they
    actually compress, so this exercises the compress-aware resolvers
    rather than a plain-file fallback that production never has."""
    monkeypatch.setattr(config, "COMPRESS", True)
    fa = tmp_path / "forarbete"
    (fa / "prop").mkdir(parents=True)
    (fa / "prop" / "2013-14-116.pdf").write_bytes(MINI_PDF)
    compress.write_download(fa / "prop" / "2013-14-116.json", json.dumps(
        {"type": "prop", "basefile": "2013/14:116",
         "identifier": "Prop. 2013/14:116", "files": ["2013-14-116.pdf"],
         "padding": "x" * 600}))
    fs = tmp_path / "foreskrift"
    (fs / "mcffs").mkdir(parents=True)
    (fs / "mcffs" / "mcffs-2026-1-regulation.pdf").write_bytes(MINI_PDF)
    compress.write_download(fs / "mcffs" / "mcffs-2026-1.json", json.dumps(
        {"fs": "mcffs", "basefile": "mcffs/2026:1",
         "files": {"regulation": {"name": "mcffs-2026-1-regulation.pdf"}},
         "padding": "x" * 600}))
    monkeypatch.setattr(layout, "FA_DOWNLOADED", fa)
    monkeypatch.setattr(layout, "FORESKRIFT_DOWNLOADED", fs)
    monkeypatch.setattr(layout, "AVG_DOWNLOADED", tmp_path / "avg")
    monkeypatch.setattr(layout, "FACSIMILE", tmp_path / "cache")
    return tmp_path


def test_render_page_produces_png(corpus, tmp_path):
    out = facsimile.render_page(
        tmp_path / "forarbete" / "prop" / "2013-14-116.pdf", 1,
        tmp_path / "out" / "sid1.png")
    assert out.read_bytes()[:4] == PNG_MAGIC
    assert not list(out.parent.glob("*.tmp*"))       # temp root cleaned up


def test_cached_page_renders_once(corpus, monkeypatch):
    pdf = corpus / "forarbete" / "prop" / "2013-14-116.pdf"
    calls = []
    real = facsimile.render_page
    monkeypatch.setattr(facsimile, "render_page",
                        lambda *a: calls.append(a) or real(*a))
    first = facsimile.cached_page("forarbete", "prop/2013-14-116", pdf, 1)
    second = facsimile.cached_page("forarbete", "prop/2013-14-116", pdf, 1)
    assert first == second == layout.facsimile("forarbete",
                                               "prop/2013-14-116", 1)
    assert len(calls) == 1                           # second hit from cache


def test_api_endpoint_serves_png_with_immutable_cache(corpus):
    client = TestClient(api.app)
    r = client.get("/api/v1/facsimile",
                   params={"uri": "https://lagen.nu/prop/2013/14:116",
                           "sid": 1})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content[:4] == PNG_MAGIC
    assert "immutable" in r.headers["cache-control"]


def test_legacy_path_grammar_both_arities(corpus):
    client = TestClient(api.app)
    assert client.get("/prop/2013/14:116/sid1.png").status_code == 200
    assert client.get("/mcffs/2026:1/sid1.png").status_code == 200


def test_missing_document_page_and_source_404(corpus):
    client = TestClient(api.app)
    assert client.get("/prop/2099/00:1/sid1.png").status_code == 404
    assert client.get("/prop/2013/14:116/sid99.png").status_code == 404
    # no downloaded avg corpus in the fixture
    assert client.get("/avg/jo/2340-2025/sid1.png").status_code == 404


def test_path_traversal_shapes_rejected(corpus):
    client = TestClient(api.app)
    r = client.get("/api/v1/facsimile",
                   params={"uri": "https://lagen.nu/prop/../14:116", "sid": 1})
    assert r.status_code == 404
    assert client.get("/sou/..%2F..%2Fetc/sid1.png").status_code == 404
