"""Tests for the KB proposition-scan facsimile fetcher (forarbete/propkb.py).

Hermetic: no network -- the fetching cases drive a fake session. The scan url is
derived from a record's stored ABBYY xml url rather than discovered by a listing
crawl, so that derivation is the module's load-bearing rule -- it decides which
79 GB of bytes get fetched, and a silent mis-derivation would fetch the wrong
ones.
"""

import json

import pytest

from accommodanda.forarbete import propkb
from accommodanda.forarbete.propkb import scan_url, wanted
from accommodanda.lib import layout

XML = ("https://weburn.kb.se/riks/tvåkammarriksdagen/xml/1937/"
       "web_prop_1937____141/prop_1937____141.xml")
PDF = ("https://weburn.kb.se/riks/tvåkammarriksdagen/pdf/web/1937/"
       "web_prop_1937____141/prop_1937____141.pdf")


def test_scan_url_is_the_old_downloaders_inverse():
    """The old PropKB derived the xml from the pdf with
    `.replace(".pdf", ".xml").replace("pdf/web", "xml")`; we go the other way."""
    assert scan_url(XML) == PDF
    assert PDF.replace(".pdf", ".xml").replace("pdf/web", "xml") == XML


def test_scan_url_rewrites_only_the_first_path_segment():
    """`/xml/` is replaced once: a document whose *name* contains "xml" must keep
    it, else the fetch would 404 on a mangled filename."""
    url = ("https://weburn.kb.se/riks/tvåkammarriksdagen/xml/1901/"
           "web_prop_1901____xml/prop_1901____xml.xml")
    assert scan_url(url) == ("https://weburn.kb.se/riks/tvåkammarriksdagen/"
                             "pdf/web/1901/web_prop_1901____xml/"
                             "prop_1901____xml.pdf")


@pytest.mark.parametrize("url", [
    PDF,                                    # already a scan url
    "https://example.com/prop.xml",         # not KB
    "https://weburn.kb.se/riks/tvåkammarriksdagen/xml/1937/x/prop.html",
])
def test_scan_url_rejects_a_non_kb_xml_url(url):
    """A mis-keyed record fails loudly rather than fetching something arbitrary
    (rule:errors-drive-retry-use-raise)."""
    with pytest.raises(ValueError, match="not a KB ABBYY xml url"):
        scan_url(url)


def test_wanted_selects_only_xml_only_kb_records():
    """The 17,297 xml-only records need their scan; the 1,769 whose body already
    *is* the KB scan, and every non-KB prop, do not."""
    assert wanted({"orig_url": XML})
    assert not wanted({"orig_url": PDF})
    assert not wanted({"orig_url": "https://www.regeringen.se/x.pdf"})
    assert not wanted({})


# --- the fetch itself ----------------------------------------------------

RECORD = {"type": "prop", "basefile": "1937:141", "identifier": "Prop. 1937:141",
          "orig_url": XML, "body_format": "abbyy", "files": ["1937-141.xml"]}


class FakeResponse:
    def __init__(self, content):
        self.content = content


def _fake_request(payload):
    def request(session, method, url, **kwargs):
        assert url == PDF, "fetched the wrong url: %s" % url
        return FakeResponse(payload)
    return request


@pytest.fixture
def propdir(tmp_path):
    (tmp_path / "prop").mkdir()
    return tmp_path


def test_download_one_writes_the_scan_and_never_touches_the_record(
        monkeypatch, propdir):
    """The whole design in one assertion: the scan lands at the
    `fa_facsimile_pdf` slot and the record is left byte-identical. A record
    rewrite would re-stale 17k parses (`build.fa_parse_inputs` hashes it) and,
    if it reached `files`, flip the body off the ABBYY OCR."""
    monkeypatch.setattr(propkb, "request", _fake_request(b"%PDF-1.4 scan"))
    record = dict(RECORD)
    assert propkb.download_one(None, propdir, record, delay=0) is True
    assert (layout.fa_dir(propdir, "prop", "1937:141")
            / "1937-141.pdf").read_bytes() == b"%PDF-1.4 scan"
    assert record == RECORD                  # no facsimile key, no files change


def test_download_one_is_resumable_from_disk(monkeypatch, propdir):
    """A scan already on disk is skipped without a request, so a killed run just
    gets rerun -- resumability comes from the bytes, not from bookkeeping."""
    scan = layout.fa_dir(propdir, "prop", "1937:141") / "1937-141.pdf"
    scan.parent.mkdir(parents=True, exist_ok=True)
    scan.write_bytes(b"%PDF-1.4 old")

    def explode(*a, **kw):
        raise AssertionError("re-fetched a scan that was already on disk")

    monkeypatch.setattr(propkb, "request", explode)
    assert propkb.download_one(None, propdir, dict(RECORD), delay=0) is False


def test_download_one_rejects_non_pdf_bytes(monkeypatch, propdir):
    """KB serves the scan as application/octet-stream, so the magic is the only
    proof we got a PDF: an error page stored as one would render as a blank
    facsimile forever (rule:errors-drive-retry-use-raise)."""
    monkeypatch.setattr(propkb, "request", _fake_request(b"<html>404</html>"))
    with pytest.raises(ValueError, match="KB served no PDF"):
        propkb.download_one(None, propdir, dict(RECORD), delay=0)
    assert not (layout.fa_dir(propdir, "prop", "1937:141")
                / "1937-141.pdf").exists()                    # nothing written


def test_sync_skips_non_kb_records_and_the_watermark(monkeypatch, propdir):
    """`sync`'s work list is the record set filtered by `wanted`: a regeringen
    prop and the harvest watermark that shares the record glob are both passed
    over rather than mis-derived into a fetch."""
    for basefile, body in (("1937:141", RECORD),
                           ("2020:1", {"type": "prop", "basefile": "2020:1",
                                       "orig_url": "https://regeringen.se/a.pdf"})):
        recpath = layout.fa_record_file(propdir, "prop", basefile)
        recpath.parent.mkdir(parents=True, exist_ok=True)
        recpath.write_text(json.dumps(body))
    # the watermark sits at the type level (prop/), a level above the year-
    # segmented records, so sync's `*/*.json` record glob never reaches it
    (propdir / "prop" / ".watermark.json").write_text(
        json.dumps({"last_harvest": "2026-07-14"}))
    monkeypatch.setattr(propkb, "request", _fake_request(b"%PDF-1.4 scan"))
    assert propkb.sync(propdir) == (1, 1)
    assert sorted(p.name for p in (propdir / "prop").glob("*/*.pdf")) \
        == ["1937-141.pdf"]
