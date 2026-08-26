"""Tests for on-demand page facsimiles: the lib renderer, the disk cache, and
the API endpoint in both its documented and legacy-path forms."""

import json
import threading
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from ferenda import config
from ferenda.api import app as api
from ferenda.api import facsimiles
from ferenda.lib import annstore, compress, facsimile, layout

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
    propdir = layout.fa_dir(fa, "prop", "2013/14:116")     # year-segmented slot
    propdir.mkdir(parents=True)
    (propdir / "2013-14-116.pdf").write_bytes(MINI_PDF)
    compress.write_download(propdir / "2013-14-116.json", json.dumps(
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
        "G8": {"sfs": "2021:734", "page": 1, "bbox": [0, 0, 5000, 5000]},  # off-page
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
        layout.fa_dir(tmp_path / "forarbete", "prop", "2013/14:116")
        / "2013-14-116.pdf", 1,
        tmp_path / "out" / "sid1.png")
    assert out.read_bytes()[:4] == PNG_MAGIC
    assert not list(out.parent.glob("*.tmp*"))       # temp root cleaned up


def test_cached_renders_once(corpus, monkeypatch):
    pdf = layout.fa_dir(corpus / "forarbete", "prop", "2013/14:116") / "2013-14-116.pdf"
    calls = []
    real = facsimile.render_page
    monkeypatch.setattr(facsimile, "render_page",
                        lambda *a: calls.append(a) or real(*a))
    first = facsimile.cached("forarbete", "prop/2013-14-116", pdf, 1,
                                dpi=facsimile.DPI)
    second = facsimile.cached("forarbete", "prop/2013-14-116", pdf, 1,
                                dpi=facsimile.DPI)
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
    # bbox is PDF points top-left; the crop is those points scaled by CROP_DPI/72
    # -- twice the page DPI, so a small region still stands up to the lightbox
    out = facsimile.render_region(
        tmp_path / "sfs" / "pdf" / "2021" / "734.pdf", 1, [72, 72, 300, 200],
        tmp_path / "out" / "crop.png", facsimile.CROP_DPI)
    data = out.read_bytes()
    assert data[:4] == PNG_MAGIC
    assert facsimile.png_size(data) == (round(228 * facsimile.CROP_DPI / 72),
                                       round(128 * facsimile.CROP_DPI / 72))
    assert not list(out.parent.glob("*.tmp*"))       # temp root cleaned up


def test_cached_crop_renders_once_keyed_by_bbox(corpus, monkeypatch):
    pdf = corpus / "sfs" / "pdf" / "2021" / "734.pdf"
    calls = []
    real = facsimile.render_region
    monkeypatch.setattr(facsimile, "render_region",
                        lambda *a: calls.append(a) or real(*a))
    a = facsimile.cached("sfs", "2021:734", pdf, 1, [72, 72, 300, 200],
                         dpi=facsimile.CROP_DPI)
    b = facsimile.cached("sfs", "2021:734", pdf, 1, [72, 72, 300, 200],
                         dpi=facsimile.CROP_DPI)
    assert a == b == layout.facsimile_crop("sfs", "2021:734", 1,
                                           [72, 72, 300, 200], facsimile.CROP_DPI)
    assert len(calls) == 1                            # second hit from cache
    # a different bbox is a different cache file (re-verification never stale)
    facsimile.cached("sfs", "2021:734", pdf, 1, [72, 72, 300, 300],
                     dpi=facsimile.CROP_DPI)
    assert len(calls) == 2


def test_sfs_graphic_endpoint_crops_from_provenance_pdf(corpus):
    client = TestClient(api.app)
    r = client.get("/api/v1/sfs-graphic",
                   params={"uri": "https://lagen.nu/2002:780", "node": "G1"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content[:4] == PNG_MAGIC
    assert facsimile.png_size(r.content) == (round(228 * facsimile.CROP_DPI / 72),
                                            round(128 * facsimile.CROP_DPI / 72))
    assert "immutable" in r.headers["cache-control"]
    # the crop is cached under the *source* SFS (2021:734), not the viewed one
    assert layout.facsimile_crop("sfs", "2021:734", 1, [72, 72, 300, 200],
                                 facsimile.CROP_DPI).exists()


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


# --------------------------------------------------------------------------
# the endpoint's crop parameter
# --------------------------------------------------------------------------

def test_bbox_query_parses_to_the_renderer_shape():
    assert facsimiles.parse_bbox("331,338,476,452") == [331.0, 338.0, 476.0, 452.0]
    assert facsimiles.parse_bbox("220.9,225.5,317.6,301.6") == [
        220.9, 225.5, 317.6, 301.6]


@pytest.mark.parametrize("raw", [
    "1,2,3",                 # too few
    "1,2,3,4,5",             # too many
    "a,2,3,4",               # not numbers
    "3,2,1,4",               # x1 <= x0
    "1,4,3,2",               # y1 <= y0
    "-1,0,3,4",              # negative origin
])
def test_a_malformed_bbox_is_client_error_not_an_assertion(raw):
    """The crop renderer asserts its bbox, which is right for an internal
    invariant and wrong for a query string: a bad one is the caller's mistake,
    so it is a 400 rather than a 500 with a traceback."""
    with pytest.raises(HTTPException) as exc:
        facsimiles.parse_bbox(raw)
    assert exc.value.status_code == 400


# --------------------------------------------------------------------------
# the crop must lie on the page, and two threads must not race
# --------------------------------------------------------------------------

def test_page_size_reads_the_mediabox(corpus):
    pdf = corpus / "sfs" / "pdf" / "2021" / "734.pdf"
    assert facsimile.page_size(pdf, 1) == (595.0, 842.0)


def test_a_crop_off_the_page_is_refused(corpus, tmp_path):
    pdf = corpus / "sfs" / "pdf" / "2021" / "734.pdf"
    with pytest.raises(facsimile.OffPage):
        facsimile.render_region(pdf, 1, [0, 0, 5000, 5000],
                                tmp_path / "out" / "crop.png", facsimile.CROP_DPI)
    assert not (tmp_path / "out" / "crop.png").exists()


def test_a_full_bleed_crop_still_renders(corpus, tmp_path):
    """A figure rectangle is derived by scaling poppler's pixel geometry, so a
    full-bleed illustration rounds to a fraction of a point past the page edge.
    `EDGE_SLOP` absorbs that; a rectangle past the slop does not render."""
    pdf = corpus / "sfs" / "pdf" / "2021" / "734.pdf"
    edge = [0, 0, 595 + facsimile.EDGE_SLOP, 842 + facsimile.EDGE_SLOP]
    out = facsimile.render_region(pdf, 1, edge, tmp_path / "out" / "bleed.png",
                                  facsimile.CROP_DPI)
    assert out.read_bytes()[:4] == PNG_MAGIC
    with pytest.raises(facsimile.OffPage):
        facsimile.render_region(pdf, 1, [0, 0, 595, 842 + 2 * facsimile.EDGE_SLOP],
                                tmp_path / "out" / "past.png", facsimile.CROP_DPI)


def test_a_stored_off_page_bbox_fails_loudly(corpus):
    """The same rectangle out of a reviewed .graphics layer is not client input.
    It is a corpus fault, and a 400 would report it to the reader as their
    mistake and leave the bad layer in place. It raises instead."""
    client = TestClient(api.app)
    with pytest.raises(facsimile.OffPage):
        client.get("/api/v1/sfs-graphic",
                   params={"uri": "https://lagen.nu/2002:780", "node": "G8"})


def test_an_off_page_crop_is_a_400_and_mints_no_cache_entry(corpus):
    """A bbox `valid_bbox` accepts can still lie past the page edge. Unchecked
    it renders whitespace, and writes that whitespace into a cache file. The
    check refuses the render; it is not a bound on cache size (the in-page
    crops alone are past counting -- eviction is the cron job)."""
    client = TestClient(api.app)
    r = client.get("/api/v1/facsimile",
                   params={"uri": "https://lagen.nu/2021:734", "sid": 1,
                           "bbox": "0,0,5000,5000"})
    assert r.status_code == 400
    assert not layout.facsimile_crop("sfs", "2021:734", 1,
                                     [0, 0, 5000, 5000],
                                     facsimile.CROP_DPI).exists()


def test_a_page_the_pdf_lacks_is_still_a_404_with_a_crop(corpus):
    client = TestClient(api.app)
    r = client.get("/api/v1/facsimile",
                   params={"uri": "https://lagen.nu/2021:734", "sid": 9,
                           "bbox": "0,0,100,100"})
    assert r.status_code == 404


def test_concurrent_requests_for_one_page_render_it_once(corpus, monkeypatch):
    """The endpoints are synchronous, so uvicorn serves them from a thread
    pool: without the per-key lock four readers opening the same facsimile pay
    for four renders and write the same temp file over each other."""
    pdf = layout.fa_dir(corpus / "forarbete", "prop", "2013/14:116") / "2013-14-116.pdf"
    calls, real = [], facsimile.render_page

    def slow(*a):
        calls.append(a)
        time.sleep(0.05)                 # widen the check-then-render window
        return real(*a)

    monkeypatch.setattr(facsimile, "render_page", slow)
    out = []
    threads = [threading.Thread(
        target=lambda: out.append(
            facsimile.cached("forarbete", "prop/2013-14-116", pdf, 1,
                                dpi=facsimile.DPI)))
        for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1
    assert len(out) == 4 and len(set(out)) == 1
    assert out[0].read_bytes()[:4] == PNG_MAGIC
    assert not list(out[0].parent.glob("*.tmp*"))


def test_stor_asks_for_the_full_size_render(corpus):
    """The lightbox and the thumbnail are two renders of one crop, not one
    stretched: `stor=1` is what asks for the larger, and it must actually
    arrive, or the overlay shows a blown-up thumbnail."""
    client = TestClient(api.app)
    params = {"uri": "https://lagen.nu/2002:780", "node": "G1"}
    small = client.get("/api/v1/sfs-graphic", params=params)
    large = client.get("/api/v1/sfs-graphic", params={**params, "stor": 1})
    assert small.status_code == large.status_code == 200
    ratio = facsimile.CROP_DPI_LARGE / facsimile.CROP_DPI
    # poppler rounds each edge of the crop window independently, so the two
    # renders can differ by a pixel from the exact ratio -- what is asserted is
    # that the larger one really was rendered larger, not merely scaled
    assert all(abs(big - round(small_px * ratio)) <= 1 for big, small_px
               in zip(facsimile.png_size(large.content),
                      facsimile.png_size(small.content), strict=True))


def test_two_resolutions_of_one_bbox_are_two_cache_files(corpus):
    """The cache path carries the DPI. Without it, raising the resolution would
    keep serving whatever render happened to be on disk under the shared name --
    for as long as the (externally evicted) cache kept it."""
    pdf = corpus / "sfs" / "pdf" / "2021" / "734.pdf"
    bbox = [72, 72, 300, 200]
    small = facsimile.cached("sfs", "2021:734", pdf, 1, bbox,
                             dpi=facsimile.CROP_DPI)
    large = facsimile.cached("sfs", "2021:734", pdf, 1, bbox,
                             dpi=facsimile.CROP_DPI_LARGE)
    assert small != large
    assert facsimile.png_size(large.read_bytes())[0] > \
        facsimile.png_size(small.read_bytes())[0]


def test_a_forarbete_illustration_keeps_the_page_resolution(corpus, monkeypatch):
    """A förarbete figure is shown once at column width and nothing opens it
    larger, so it is not rendered at the SFS graphics' thumbnail resolution --
    that would be four times the pixels for detail no reader can reach."""
    asked = []
    real = facsimile.cached
    monkeypatch.setattr(facsimile, "cached",
                        lambda *a, **kw: asked.append(kw["dpi"]) or real(*a, **kw))
    client = TestClient(api.app)
    r = client.get("/api/v1/facsimile",
                   params={"uri": "https://lagen.nu/2021:734", "sid": 1,
                           "bbox": "72,72,300,200"})
    assert r.status_code == 200
    assert asked == [facsimile.DPI]
