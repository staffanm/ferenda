"""Tests for the eurlex harvest's handling of metadata-only works -- a CELEX
with no Swedish/English manifestation (a pre-accession act never translated)
must not be left on disk as a bare notice, and prune_empty cleans up any such
dirs earlier runs created -- and its content-format fallback (a scanned TIFF
served under an fmx4 manifestation is rejected for the next text type)."""

from accommodanda.eurlex import download as D

TIFF = b"II*\x00\x12p\x00\x00"          # little-endian TIFF magic + noise


def test_store_document_skips_a_work_with_no_manifestation(tmp_path):
    # empty selection -> no swe/eng manifestation: nothing is written, not even a
    # notice (session is never touched, so None is fine)
    stored = D.store_document(None, tmp_path / "1965" / "31965R0163",
                              "31965R0163", "1965-11-25", [], [])
    assert stored == []
    assert not (tmp_path / "1965").exists()


def test_content_ok_rejects_image_under_a_text_type():
    assert not D._content_ok("fmx4", TIFF)          # scanned placeholder
    assert D._content_ok("fmx4", b"  <?xml ?>")     # real Formex
    assert D._content_ok("fmx4", D.ZIP_MAGIC + b"x")  # zipped Formex bundle
    assert not D._content_ok("xhtml", TIFF)
    assert D._content_ok("html", b"<!DOCTYPE html>")
    assert D._content_ok("pdf", b"%PDF-1.4")
    assert not D._content_ok("pdf", TIFF)


def test_ranked_types_orders_fmx4_xhtml_html_then_pdf():
    by_type = {"html": [1], "pdf": [2], "fmx4": [3], "xhtml": [4], "pdfa1a": [5]}
    assert D._ranked_types(by_type) == ["fmx4", "xhtml", "html", "pdf", "pdfa1a"]


def test_store_document_falls_back_when_fmx4_is_a_scanned_image(tmp_path,
                                                                monkeypatch):
    # CELLAR serves a TIFF under the fmx4-typed manifestation of some scanned old
    # judgments; store_document must reject it and fetch the next type's real text
    bodies = {"u-fmx4": TIFF, "u-xhtml": b"<?xml version='1.0'?><html/>"}
    fetched = []

    class Resp:
        def __init__(self, content):
            self.content = content

    def fake_request(session, method, url, **kw):
        fetched.append(url)
        return Resp(bodies[url])

    monkeypatch.setattr(D, "request", fake_request)
    target = tmp_path / "1993" / "61993CC0425"
    selection = [("swe", [("fmx4", "u-fmx4"), ("xhtml", "u-xhtml")])]
    stored = D.store_document(object(), target, "61993CC0425", "1993-01-01",
                              selection, [])
    assert stored == ["swe"]
    assert fetched == ["u-fmx4", "u-xhtml"]            # tried fmx4, fell back
    assert (target / "swe.xhtml").read_bytes() == bodies["u-xhtml"]
    assert not (target / "swe.fmx4").exists()
    assert (target / "notice.ttl").exists()            # content stored -> notice


def test_store_document_writes_no_notice_when_every_candidate_is_rejected(
        tmp_path, monkeypatch):
    # every candidate in every language is a scanned-TIFF placeholder: nothing
    # is stored and, crucially, no notice.ttl -- is_downloaded keys on the
    # notice, so an early notice would permanently mask the work from later
    # runs that do find content
    class Resp:
        content = TIFF

    monkeypatch.setattr(D, "request", lambda *a, **kw: Resp())
    target = tmp_path / "1993" / "61993CC0425"
    selection = [("swe", [("fmx4", "u1"), ("pdf", "u2")]),
                 ("eng", [("fmx4", "u3")])]
    stored = D.store_document(object(), target, "61993CC0425", "1993-01-01",
                              selection, [])
    assert stored == []
    assert not (target / "notice.ttl").exists()
    assert not D.is_downloaded(tmp_path, "61993CC0425")


def test_prune_empty_removes_notice_only_dirs_keeps_documents(tmp_path):
    notice_only = tmp_path / "1965" / "31965R0163"
    notice_only.mkdir(parents=True)
    (notice_only / "notice.ttl").write_text("x")

    with_doc = tmp_path / "1990" / "31990L0630"
    with_doc.mkdir(parents=True)
    (with_doc / "notice.ttl").write_text("x")
    (with_doc / "swe.html").write_text("<body/>")

    assert D.prune_empty(tmp_path, remove=False) == 1     # counts, removes nothing
    assert notice_only.exists()

    assert D.prune_empty(tmp_path) == 1                   # removes the notice-only dir
    assert not notice_only.exists()
    assert (with_doc / "swe.html").exists()               # the real document is kept
