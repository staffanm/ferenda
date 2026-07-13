"""Tests for on-demand page facsimiles: the lib renderer, the disk cache, and
the API endpoint in both its documented and legacy-path forms."""

import json

import pytest
from fastapi.testclient import TestClient

from accommodanda import config
from accommodanda.api import app as api
from accommodanda.lib import annstore, compress, facsimile, layout

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
    # an SFS whose published PDF the mirror fetched, plus a hand-authored
    # (verified) .graphics layer for a *different* statute (2002:780) whose gaps
    # are cropped from this PDF -- the provenance indirection the endpoint walks
    sfs = tmp_path / "sfs"
    (sfs / "pdf" / "2021").mkdir(parents=True)
    (sfs / "pdf" / "2021" / "734.pdf").write_bytes(MINI_PDF)
    ann = tmp_path / "ann"
    (ann / "sfs" / "2002").mkdir(parents=True)
    (ann / "sfs" / "2002" / "780.graphics").write_text(json.dumps({
        "meta": {"status": "verified"},
        "G1": {"sfs": "2021:734", "page": 1, "bbox": [72, 72, 300, 200]},
        "G2": {"sfs": "2021:734", "page": 1},          # no bbox -> whole page
        "G9": {"sfs": "2099:1", "page": 1, "bbox": [0, 0, 10, 10]},  # unmirrored
    }))
    (ann / "sfs" / "2002" / "781.graphics").write_text(json.dumps({
        "meta": {"status": "generated"},
        "g-draft": {"sfs": "2021:734", "page": 1, "bbox": [1, 1, 10, 10]},
    }))
    monkeypatch.setattr(layout, "FA_DOWNLOADED", fa)
    monkeypatch.setattr(layout, "FORESKRIFT_DOWNLOADED", fs)
    monkeypatch.setattr(layout, "AVG_DOWNLOADED", tmp_path / "avg")
    monkeypatch.setattr(layout, "SFS_DOWNLOADED", sfs)
    monkeypatch.setattr(annstore, "ROOT", ann)
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


# ---- sfs-graphic crops -----------------------------------------------------

def test_render_region_crops_to_bbox_pixels(corpus, tmp_path):
    # bbox is PDF points top-left; the crop is those points scaled by DPI/72
    out = facsimile.render_region(
        tmp_path / "sfs" / "pdf" / "2021" / "734.pdf", 1, [72, 72, 300, 200],
        tmp_path / "out" / "crop.png")
    data = out.read_bytes()
    assert data[:4] == PNG_MAGIC
    assert facsimile.png_size(data) == (round(228 * 150 / 72), round(128 * 150 / 72))
    assert not list(out.parent.glob("*.tmp*"))       # temp root cleaned up


def test_cached_region_renders_once_keyed_by_bbox(corpus, monkeypatch):
    pdf = corpus / "sfs" / "pdf" / "2021" / "734.pdf"
    calls = []
    real = facsimile.render_region
    monkeypatch.setattr(facsimile, "render_region",
                        lambda *a: calls.append(a) or real(*a))
    a = facsimile.cached_region("sfs", "2021:734", pdf, 1, [72, 72, 300, 200])
    b = facsimile.cached_region("sfs", "2021:734", pdf, 1, [72, 72, 300, 200])
    assert a == b == layout.facsimile_crop("sfs", "2021:734", 1,
                                           [72, 72, 300, 200])
    assert len(calls) == 1                            # second hit from cache
    # a different bbox is a different cache file (re-verification never stale)
    facsimile.cached_region("sfs", "2021:734", pdf, 1, [72, 72, 300, 300])
    assert len(calls) == 2


def test_sfs_graphic_endpoint_crops_from_provenance_pdf(corpus):
    client = TestClient(api.app)
    r = client.get("/api/v1/sfs-graphic",
                   params={"uri": "https://lagen.nu/2002:780", "node": "G1"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content[:4] == PNG_MAGIC
    assert facsimile.png_size(r.content) == (round(228 * 150 / 72), round(128 * 150 / 72))
    assert "immutable" in r.headers["cache-control"]
    # the crop is cached under the *source* SFS (2021:734), not the viewed one
    assert layout.facsimile_crop("sfs", "2021:734", 1, [72, 72, 300, 200]).exists()


def test_sfs_graphic_whole_page_when_bbox_omitted(corpus):
    client = TestClient(api.app)
    r = client.get("/api/v1/sfs-graphic",
                   params={"uri": "https://lagen.nu/2002:780", "node": "G2"})
    assert r.status_code == 200
    # no bbox -> the whole source page, identical to its plain facsimile
    full = client.get("/api/v1/facsimile",
                      params={"uri": "https://lagen.nu/2021:734", "sid": 1})
    assert facsimile.png_size(r.content) == facsimile.png_size(full.content)


def test_sfs_graphic_cache_buster_is_ignored(corpus):
    client = TestClient(api.app)
    r = client.get("/api/v1/sfs-graphic",
                   params={"uri": "https://lagen.nu/2002:780", "node": "G1",
                           "v": "deadbeef"})
    assert r.status_code == 200


def test_sfs_graphic_404s(corpus):
    client = TestClient(api.app)
    # unknown gap id in an existing layer
    assert client.get("/api/v1/sfs-graphic", params={
        "uri": "https://lagen.nu/2002:780", "node": "G7"}).status_code == 404
    # a statute with no graphics layer at all
    assert client.get("/api/v1/sfs-graphic", params={
        "uri": "https://lagen.nu/1999:175", "node": "G1"}).status_code == 404
    # the gap points at a source SFS whose PDF was never mirrored
    assert client.get("/api/v1/sfs-graphic", params={
        "uri": "https://lagen.nu/2002:780", "node": "G9"}).status_code == 404
    # generated vision candidates are not part of the public legal text
    assert client.get("/api/v1/sfs-graphic", params={
        "uri": "https://lagen.nu/2002:781", "node": "g-draft"}).status_code == 404


def test_sfs_full_page_facsimile_resolver(corpus):
    # the _sfs_pdf resolver also serves a full published-SFS page facsimile
    client = TestClient(api.app)
    r = client.get("/api/v1/facsimile",
                   params={"uri": "https://lagen.nu/2021:734", "sid": 1})
    assert r.status_code == 200
    assert r.content[:4] == PNG_MAGIC
