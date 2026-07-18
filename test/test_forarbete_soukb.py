"""Tests for the KB SOU body re-downloader (forarbete/soukb.py).

Hermetic: no network -- a fake session serves the index, the URN resolver pages
and the PDF bytes. Two things are load-bearing and get the most attention:

  * `basefile_of` -- the index label is the *only* source of the basefile now,
    so its transform decides both the document identity and its flat filename. A
    silent miss (the 17 forms the legacy regex skipped) would drop documents or
    mint a colliding basefile.
  * the multi-volume grouping -- 128 labels repeat across several URNs (the
    volumes of one SOU); they must land as one record with a `files` *list*, not
    collide onto a single PDF.
"""

import json

import pytest

from accommodanda.forarbete import soukb
from accommodanda.forarbete.soukb import basefile_of
from accommodanda.lib import compress, layout


@pytest.mark.parametrize("label, basefile", [
    ("1922:1", "1922:1"),                         # plain
    ("1922:1 första serien", "1922:1fs"),         # the retroactive first series
    ("1989:53 A", "1989:53a"),                    # legacy space-prefixed suffix
    ("1989:53A", "1989:53a"),                     # same doc, no space (regex miss)
    ("1994:11E", "1994:11e"),                     # a non-A/B letter suffix
    ("1952:16/17", "1952:16-17"),                 # combined double issue, slash-free
])
def test_basefile_of_ports_and_broadens_the_legacy_transform(label, basefile):
    """The number+series label collapses to a flat, filesystem-safe basefile,
    including the forms the legacy `\\d{4}:\\d+(?:| A| B)(?:| första serien)$`
    regex skipped (bare letter suffixes, combined issues)."""
    assert basefile_of(label) == basefile


# --- fake network --------------------------------------------------------

INDEX = """
<html><body>
<a href="http://urn.kb.se/resolve?urn=urn:nbn:se:kb:sou-100">1922:1</a> Plain one. <br>
<a href="http://urn.kb.se/resolve?urn=urn:nbn:se:kb:sou-101">1922:1 första serien</a> The first series. <br>
<a href="http://urn.kb.se/resolve?urn=urn:nbn:se:kb:sou-200">1987:3</a> Volume one. <br>
<a href="http://urn.kb.se/resolve?urn=urn:nbn:se:kb:sou-201">1987:3</a> Volume two. <br>
<a href="/some/other/link">not a sou</a>
</body></html>
"""

# each URN resolver page carries exactly one .pdf link (the digark scan)
URN_PAGES = {
    "sou-100": '<a href="https://weburn.kb.se/sou/1/a.pdf">pdf</a>',
    "sou-101": '<a href="https://weburn.kb.se/sou/1/b.pdf">pdf</a>',
    "sou-200": '<a href="https://weburn.kb.se/sou/2/v1.pdf">pdf</a>',
    "sou-201": '<a href="https://weburn.kb.se/sou/2/v2.pdf">pdf</a>',
}


class FakeResponse:
    def __init__(self, *, text="", content=b""):
        self.text = text
        self.content = content


def _fake_request(pdf_bytes=b"%PDF-1.4 scan"):
    def request(session, method, url, **kwargs):
        if url == soukb.INDEX_URL:
            return FakeResponse(text=INDEX)
        if url.endswith(".pdf"):
            return FakeResponse(content=pdf_bytes)
        if "urn.kb.se" in url:
            urn = url.rsplit(":", 1)[1]
            return FakeResponse(text=URN_PAGES[urn])
        raise AssertionError("unexpected url: %s" % url)
    return request


@pytest.fixture
def soudir(tmp_path):
    (tmp_path / "sou").mkdir()
    return tmp_path


def test_walk_index_groups_the_volumes_of_one_sou(monkeypatch):
    """A label repeated across URNs (the 128 multi-volume SOUs) becomes one entry
    whose url list is the volumes in index order; the title is the first volume's,
    read from the anchor's `next_sibling` text node."""
    monkeypatch.setattr(soukb, "request", _fake_request())
    entries = soukb.walk_index(None)
    assert [(b, t, len(u)) for b, t, u in entries] == [
        ("1922:1", "Plain one.", 1),
        ("1922:1fs", "The first series.", 1),
        ("1987:3", "Volume one.", 2),
    ]
    assert entries[2][2] == ["http://urn.kb.se/resolve?urn=urn:nbn:se:kb:sou-200",
                             "http://urn.kb.se/resolve?urn=urn:nbn:se:kb:sou-201"]


def test_walk_index_raises_on_an_unparseable_label(monkeypatch):
    """A changed index (an anchor whose text is not a SOU label) fails loudly
    rather than silently dropping documents (rule:errors-drive-retry-use-raise)."""
    def request(session, method, url, **kwargs):
        return FakeResponse(text='<a href="x/sou-9">not a label</a> t <br>')

    monkeypatch.setattr(soukb, "request", request)
    with pytest.raises(ValueError, match="unparseable label"):
        soukb.walk_index(None)


def test_pdf_url_raises_when_the_resolver_has_no_pdf(monkeypatch):
    """A dead or restructured URN page (no `.pdf` link) fails loudly rather than
    fetching something arbitrary (rule:errors-drive-retry-use-raise)."""
    monkeypatch.setattr(soukb, "request",
                        lambda *a, **k: FakeResponse(text="<html>gone</html>"))
    with pytest.raises(ValueError, match="no scan PDF"):
        soukb.pdf_url(None, "http://urn.kb.se/resolve?urn=x")


def test_download_one_writes_the_body_pdf_and_a_fresh_record(monkeypatch, soudir):
    """A single-part SOU: the scan PDF is the body, so it lands in `files` and a
    fresh record is written keyed by the index basefile."""
    monkeypatch.setattr(soukb, "request", _fake_request(b"%PDF-1.4 one"))
    entry = ("1922:1", "Plain one.",
             ["http://urn.kb.se/resolve?urn=urn:nbn:se:kb:sou-100"])
    assert soukb.download_one(None, soudir, entry, delay=0) is True
    assert (layout.fa_dir(soudir, "sou", "1922:1")
            / "1922-1.pdf").read_bytes() == b"%PDF-1.4 one"
    record = json.loads(compress.read_text(
        layout.fa_record_file(soudir, "sou", "1922:1")))
    assert record == {"type": "sou", "basefile": "1922:1",
                      "identifier": "SOU 1922:1", "title": "Plain one.",
                      "date": None,
                      "orig_url": "http://urn.kb.se/resolve?urn=urn:nbn:se:kb:sou-100",
                      "url": "http://urn.kb.se/resolve?urn=urn:nbn:se:kb:sou-100",
                      "files": ["1922-1.pdf"]}


def test_download_one_names_multi_volume_parts_in_order(monkeypatch, soudir):
    """The 128 multi-volume SOUs: each URN is a part, named like
    `download.download_document` (`<slug>.pdf`, `<slug>-1.pdf`, ...), all listed
    in `files` -- never colliding onto one PDF."""
    monkeypatch.setattr(soukb, "request", _fake_request(b"%PDF-1.4 vol"))
    entry = ("1987:3", "Volume one.",
             ["http://urn.kb.se/resolve?urn=urn:nbn:se:kb:sou-200",
              "http://urn.kb.se/resolve?urn=urn:nbn:se:kb:sou-201"])
    assert soukb.download_one(None, soudir, entry, delay=0) is True
    assert sorted(p.name for p in
                  layout.fa_dir(soudir, "sou", "1987:3").glob("*.pdf")) \
        == ["1987-3-1.pdf", "1987-3.pdf"]
    record = json.loads(compress.read_text(
        layout.fa_record_file(soudir, "sou", "1987:3")))
    assert record["files"] == ["1987-3.pdf", "1987-3-1.pdf"]


def test_download_one_is_resumable_from_disk(monkeypatch, soudir):
    """An entry already complete on disk (record + all parts) is skipped without a
    request, so a killed hundreds-of-GB run just gets rerun."""
    d = layout.fa_dir(soudir, "sou", "1922:1")
    d.mkdir(parents=True, exist_ok=True)
    (d / "1922-1.pdf").write_bytes(b"%PDF old")
    layout.fa_record_file(soudir, "sou", "1922:1").write_text("{}")

    def explode(*a, **kw):
        raise AssertionError("re-fetched an entry already on disk")

    monkeypatch.setattr(soukb, "request", explode)
    entry = ("1922:1", "Plain one.",
             ["http://urn.kb.se/resolve?urn=urn:nbn:se:kb:sou-100"])
    assert soukb.download_one(None, soudir, entry, delay=0) is False


def test_download_one_rejects_non_pdf_bytes(monkeypatch, soudir):
    """KB serves the scan as application/octet-stream, so the magic is the only
    proof we got a PDF: an error page stored as one would parse to an empty body
    forever (rule:errors-drive-retry-use-raise)."""
    monkeypatch.setattr(soukb, "request", _fake_request(b"<html>404</html>"))
    entry = ("1922:1", "Plain one.",
             ["http://urn.kb.se/resolve?urn=urn:nbn:se:kb:sou-100"])
    with pytest.raises(ValueError, match="KB served no PDF"):
        soukb.download_one(None, soudir, entry, delay=0)
    assert not (layout.fa_dir(soudir, "sou", "1922:1")
                / "1922-1.pdf").exists()                     # nothing written


def test_sync_walks_the_index_and_honours_limit(monkeypatch, soudir):
    """`sync` builds its work list from the index and stops after `--limit`
    entries fetched, so a test slice never runs the full crawl."""
    monkeypatch.setattr(soukb, "request", _fake_request())
    assert soukb.sync(soudir, limit=1) == (1, 1)
    assert [p.name for p in
            layout.fa_dir(soudir, "sou", "1922:1").glob("*.pdf")] == ["1922-1.pdf"]
