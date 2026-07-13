"""URL construction for the published-SFS-PDF mirror. Network-free: the
pre-2018 URL is derivable, and the post-2018 doc-page scrape is driven with a
fake session so the href parsing is exercised without hitting the publisher.
"""

import pytest

from accommodanda.lib import layout
from accommodanda.sfs import pdfmirror as m


class _Resp:
    def __init__(self, text):
        self.text = text
        self.status_code = 200

    def raise_for_status(self):
        pass


class _Session:
    """A session whose every request returns the same canned page."""
    def __init__(self, text):
        self._text = text

    def request(self, method, url, **kwargs):
        return _Resp(self._text)


def test_pre_2018_url_is_derivable():
    # matches the real rkrattsdb SFSdoc paths for the example acts
    assert m.pdf_url(None, "2007:90") == \
        "https://rkrattsdb.gov.se/SFSdoc/07/070090.PDF"
    assert m.pdf_url(None, "2002:780") == \
        "https://rkrattsdb.gov.se/SFSdoc/02/020780.PDF"
    assert m.pdf_url(None, "2017:1272") == \
        "https://rkrattsdb.gov.se/SFSdoc/17/171272.PDF"


def test_pre_1998_has_no_pdf():
    assert m.pdf_url(None, "1962:700") is None


def test_post_2018_scrapes_the_doc_page():
    page = ('<html><body><a href="/sites/default/files/sfs/2021-06/'
            'SFS2021-734.pdf">Ladda ner (pdf 562 kB)</a></body></html>')
    assert m.pdf_url(_Session(page), "2021:734") == \
        "https://svenskforfattningssamling.se/sites/default/files/sfs/" \
        "2021-06/SFS2021-734.pdf"


def test_post_2018_absolute_href_kept():
    page = '<a href="https://svenskforfattningssamling.se/x/SFS2024-67.pdf">pdf</a>'
    assert m.pdf_url(_Session(page), "2024:67") == \
        "https://svenskforfattningssamling.se/x/SFS2024-67.pdf"


def test_post_2018_single_quoted_href():
    page = "<a href='/x/SFS2024-67.pdf'>pdf</a>"
    assert m.pdf_url(_Session(page), "2024:67") == \
        "https://svenskforfattningssamling.se/x/SFS2024-67.pdf"


def test_early_2018_falls_back_to_printed_series():
    assert m.pdf_url(_Session("<html>no online PDF</html>"), "2018:1") == \
        "https://rkrattsdb.gov.se/SFSdoc/18/180001.PDF"


def test_post_2018_no_pdf_link_is_none():
    assert m.pdf_url(_Session("<html>no pdf here</html>"), "2019:1") is None


def test_sort_key_is_year_then_number():
    order = sorted(["2023:395", "2004:629", "2020:120", "2004:1037"],
                   key=m._sort_key)
    assert order == ["2004:629", "2004:1037", "2020:120", "2023:395"]


def test_fetch_rejects_non_pdf_body(tmp_path, monkeypatch):
    class _Download:
        content = b"<html>gateway error</html>"

    monkeypatch.setattr(layout, "SFS_DOWNLOADED", tmp_path)
    monkeypatch.setattr(m, "request", lambda *args, **kwargs: _Download())
    with pytest.raises(ValueError, match="non-PDF content"):
        m.fetch_one(None, "2017:1272")
