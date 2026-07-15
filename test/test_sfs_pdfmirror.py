"""Source routing and negative-answer bookkeeping for the published-SFS-PDF
mirror. Network-free: the printed series' URL is derivable, and the online
series' doc-page scrape is driven with fake sessions so the parsing is
exercised without hitting the publisher.
"""

import json

import pytest
import requests

from accommodanda.lib import compress, layout
from accommodanda.sfs import pdfmirror as m


class _Resp:
    def __init__(self, text="", content=b"", status_code=200, url=""):
        self.text = text
        self.content = content
        self.status_code = status_code
        self.url = url
        self.headers = {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError("HTTP %d" % self.status_code, response=self)


class _Session:
    """A session whose every request returns the same canned page."""
    def __init__(self, text):
        self._text = text

    def request(self, method, url, **kwargs):
        return _Resp(self._text)


class _MapSession:
    """A session serving canned responses per URL; anything unmapped 404s, as
    both publishers do for an act they have no PDF for. Records every URL asked
    for, so a test can assert what a run did *not* fetch."""
    def __init__(self, pages):
        self.pages = pages
        self.asked = []

    def request(self, method, url, **kwargs):
        self.asked.append(url)
        if url not in self.pages:
            return _Resp(status_code=404, url=url)
        body = self.pages[url]
        if isinstance(body, _Resp):
            return body
        if isinstance(body, bytes):
            return _Resp(content=body, url=url)
        return _Resp(text=body, url=url)


# --------------------------------------------------------------------------
# which source holds an act, and the URL it holds it at
# --------------------------------------------------------------------------

def test_the_two_source_boundaries():
    """Both boundaries are exact act numbers, and the ranges meet: every act
    from 1998:306 on has exactly one source, and nothing before it has any."""
    assert not m.has_facsimile("1962:700")
    assert not m.has_facsimile("1998:305")            # the printed mirror 404s
    assert m.has_facsimile("1998:306")                # ...and holds this one
    assert not m.is_online_series("1998:306")
    assert not m.is_online_series("2018:159")         # last of the printed series
    assert m.is_online_series("2018:160")             # first published online
    assert m.is_online_series("2026:1502")
    assert m.has_facsimile("2018:160")                # online implies a facsimile


def test_printed_series_url_is_derivable():
    # matches the real rkrattsdb SFSdoc paths for the example acts
    assert m.pdf_url(None, "2007:90") == \
        "https://rkrattsdb.gov.se/SFSdoc/07/070090.PDF"
    assert m.pdf_url(None, "2002:780") == \
        "https://rkrattsdb.gov.se/SFSdoc/02/020780.PDF"
    assert m.pdf_url(None, "2017:1272") == \
        "https://rkrattsdb.gov.se/SFSdoc/17/171272.PDF"


def test_acts_before_the_printed_series_have_no_url():
    assert m.pdf_url(None, "1962:700") is None
    assert m.pdf_url(None, "1998:305") is None


def test_printed_series_covers_early_2018():
    """2018:1-159 predate the 1 April switch, so they sit in the printed series
    -- and must not cost a doc-page fetch that could only 404."""
    session = _MapSession({})
    assert m.pdf_url(session, "2018:1") == \
        "https://rkrattsdb.gov.se/SFSdoc/18/180001.PDF"
    assert m.pdf_url(session, "2018:159") == \
        "https://rkrattsdb.gov.se/SFSdoc/18/180159.PDF"
    assert session.asked == []


def test_online_series_scrapes_the_doc_page():
    page = ('<html><body><a href="/sites/default/files/sfs/2021-06/'
            'SFS2021-734.pdf">Ladda ner (pdf 562 kB)</a></body></html>')
    assert m.pdf_url(_Session(page), "2021:734") == \
        "https://svenskforfattningssamling.se/sites/default/files/sfs/" \
        "2021-06/SFS2021-734.pdf"


def test_online_series_absolute_href_kept():
    page = '<a href="https://svenskforfattningssamling.se/x/SFS2024-67.pdf">pdf</a>'
    assert m.pdf_url(_Session(page), "2024:67") == \
        "https://svenskforfattningssamling.se/x/SFS2024-67.pdf"


def test_online_series_single_quoted_href():
    page = "<a href='/x/SFS2024-67.pdf'>pdf</a>"
    assert m.pdf_url(_Session(page), "2024:67") == \
        "https://svenskforfattningssamling.se/x/SFS2024-67.pdf"


def test_online_series_no_pdf_link_is_none():
    assert m.pdf_url(_Session("<html>no pdf here</html>"), "2019:1") is None


def test_online_series_missing_doc_page_is_none():
    """The doc page is the only view of the online series, so its absence is
    the answer: the act has no published PDF."""
    assert m.pdf_url(_MapSession({}), "2019:1") is None


def test_each_source_is_paced_at_its_own_rate():
    """rkrattsdb allows a hard 120 requests then 403s the host for ~30s, so it
    must be asked no faster than ~2/s; svenskforfattningssamling refused nothing
    at ~3/s. The act's number is what says which one a fetch will reach."""
    assert m.source_delay("2007:90") == m.RKRATTSDB_DELAY
    assert m.source_delay("2018:159") == m.RKRATTSDB_DELAY
    assert m.source_delay("2018:160") == m.SVENSK_DELAY
    assert m.source_delay("2021:734") == m.SVENSK_DELAY
    # the quota is 120 requests a minute; anything quicker walks into the 403s
    assert m.RKRATTSDB_DELAY >= 60 / 120


def test_sort_key_is_year_then_number():
    order = sorted(["2023:395", "2004:629", "2020:120", "2004:1037"],
                   key=m._sort_key)
    assert order == ["2004:629", "2004:1037", "2020:120", "2023:395"]


# --------------------------------------------------------------------------
# what costs a request, and what a negative answer is remembered as
# --------------------------------------------------------------------------

def test_acts_before_any_source_are_never_asked_about_or_recorded(tmp_path,
                                                                  monkeypatch):
    """A corpus sweep walks ~34k of these. They cost no request, and recording
    them would bloat the store with an era that will never gain a facsimile."""
    monkeypatch.setattr(layout, "SFS_DOWNLOADED", tmp_path)
    state = m.MirrorState(tmp_path)
    session = _MapSession({})
    assert m.fetch_one(session, state, "1962:700", delay=0) is None
    assert m.fetch_one(session, state, "1998:305", delay=0) is None
    assert session.asked == []
    assert state.absent == set()


def test_printed_series_404_is_recorded_not_raised(tmp_path, monkeypatch):
    """The regression: a derived rkrattsdb URL that 404s used to abort the whole
    corpus-wide run. It is the mirror saying it holds no facsimile."""
    monkeypatch.setattr(layout, "SFS_DOWNLOADED", tmp_path)
    session = _MapSession({})
    state = m.MirrorState(tmp_path)
    assert m.fetch_one(session, state, "2007:9999", delay=0) is None
    assert "2007:9999" in state.absent
    assert json.loads((tmp_path / ".mirror.json").read_text())["absent"] == ["2007:9999"]

    # ...and a rerun does not ask again
    session.asked.clear()
    assert m.fetch_one(session, m.MirrorState(tmp_path), "2007:9999", delay=0) is None
    assert session.asked == []


def test_non_404_failure_still_raises(tmp_path, monkeypatch):
    """Any other error is a broken run, not evidence that the act has no PDF --
    it must never be recorded as absent. (410 rather than a 5xx: both take this
    branch, but a retryable status would sleep through net.request's backoff.)"""
    monkeypatch.setattr(layout, "SFS_DOWNLOADED", tmp_path)
    session = _MapSession({m.RKRATTSDB % ("07", "07", "0090"):
                           _Resp(status_code=410, url="x")})
    state = m.MirrorState(tmp_path)
    with pytest.raises(requests.HTTPError):
        m.fetch_one(session, state, "2007:90", delay=0)
    assert state.absent == set()


def test_doc_page_without_pdf_link_is_recorded_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(layout, "SFS_DOWNLOADED", tmp_path)
    state = m.MirrorState(tmp_path)
    session = _MapSession({m.SVENSK_DOC % ("2019", "1"): "<html>no pdf here</html>"})
    assert m.fetch_one(session, state, "2019:1", delay=0) is None
    assert "2019:1" in state.absent


def test_missing_doc_page_is_recorded_absent(tmp_path, monkeypatch):
    """An online-series act the publisher has no doc page for: one ask, then the
    answer is remembered rather than re-fetched every run."""
    monkeypatch.setattr(layout, "SFS_DOWNLOADED", tmp_path)
    state = m.MirrorState(tmp_path)
    session = _MapSession({})
    assert m.fetch_one(session, state, "2019:99999", delay=0) is None
    assert "2019:99999" in state.absent
    session.asked.clear()
    assert m.fetch_one(session, m.MirrorState(tmp_path), "2019:99999", delay=0) is None
    assert session.asked == []


def test_fetch_rejects_non_pdf_body(tmp_path, monkeypatch):
    class _Download:
        content = b"<html>gateway error</html>"

    monkeypatch.setattr(layout, "SFS_DOWNLOADED", tmp_path)
    monkeypatch.setattr(m, "request", lambda *args, **kwargs: _Download())
    with pytest.raises(ValueError, match="non-PDF content"):
        m.fetch_one(None, m.MirrorState(tmp_path), "2017:1272", delay=0)


def test_fetch_stores_the_pdf(tmp_path, monkeypatch):
    monkeypatch.setattr(layout, "SFS_DOWNLOADED", tmp_path)
    state = m.MirrorState(tmp_path)
    session = _MapSession({m.RKRATTSDB % ("07", "07", "0090"): b"%PDF-1.4 body"})
    assert m.fetch_one(session, state, "2007:90", delay=0) == layout.sfs_pdf("2007:90")
    assert compress.exists(layout.sfs_pdf("2007:90"))
    # already present: idempotent, and costs nothing -- not even the source's
    # pacing wait, which is why this one does not pass delay=0
    session.asked.clear()
    assert m.fetch_one(session, state, "2007:90") is None
    assert session.asked == []


def test_a_found_pdf_clears_a_stale_negative(tmp_path, monkeypatch):
    """--full re-asks about an act previously recorded absent. If the upstream
    has since published it, the store must not claim both at once."""
    monkeypatch.setattr(layout, "SFS_DOWNLOADED", tmp_path)
    state = m.MirrorState(tmp_path)
    state.record_absent("2007:90")
    session = _MapSession({m.RKRATTSDB % ("07", "07", "0090"): b"%PDF-1.4 body"})
    assert m.fetch_one(session, state, "2007:90", force=True, delay=0)
    assert state.absent == set()
    assert m.MirrorState(tmp_path).absent == set()


def test_mirror_state_round_trips(tmp_path):
    state = m.MirrorState(tmp_path)
    state.record_absent("2007:9999")
    assert m.MirrorState(tmp_path).absent == {"2007:9999"}
