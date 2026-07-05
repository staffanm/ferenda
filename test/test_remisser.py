"""remisser vertical (regeringen.se public-referral responses): listing + case
detail parsing and the two-pass sync driver.

Hermetic: parses the three captured live pages under test/files/remisser/ and
drives sync() against a stubbed session -- no network."""

import json
from pathlib import Path

import pytest
import requests

from accommodanda.lib import layout
from accommodanda.remisser import download
from accommodanda.remisser.model import Remiss, Remissinstans


def _redirect(tmp_path, monkeypatch):
    """Point remisser's download tree (records + answer PDFs) at tmp_path."""
    monkeypatch.setattr(layout, "REMISSER_DOWNLOADED", tmp_path / "downloaded")

FILES = Path(__file__).parent / "files" / "remisser"
CLOSED_URL = ("https://www.regeringen.se/remisser/2026/04/remiss-av-"
              "adelmetallutredningen--en-moderniserad-reglering-av-handel-med-"
              "adelmetallarbeten-sou-202614/")
OPEN_URL = ("https://www.regeringen.se/remisser/2026/07/"
            "remiss-av--ds-202615-galdenarens-avtal-i-konkurs/")


def _closed():
    return download.parse_case((FILES / "case-closed.html").read_text(), CLOSED_URL)


def _open():
    return download.parse_case((FILES / "case-open.html").read_text(), OPEN_URL)


def test_parse_listing():
    items = download.parse_listing((FILES / "listing.html").read_text())
    assert len(items) == 20
    ds = next(i for i in items
              if i["basefile"] == "remiss-av--ds-202615-galdenarens-avtal-i-konkurs")
    assert ds["url"] == OPEN_URL
    assert ds["title"].startswith("Remiss av")


def test_parse_case_closed():
    r = _closed()
    assert r.basefile == ("remiss-av-adelmetallutredningen--en-moderniserad-"
                          "reglering-av-handel-med-adelmetallarbeten-sou-202614")
    assert r.dnr == "KN2026/00741"
    assert r.departement == "Klimat- och näringslivsdepartementet"
    assert r.publicerad == "2026-04-09"
    assert r.uppdaterad == "2026-06-30"
    assert r.sista_svarsdag == "2026-06-30"
    assert r.remitterat == [{"typ": "sou", "basefile": "2026:14"}]
    assert r.remissinstanser_pdf and r.remissinstanser_pdf.endswith(".pdf")
    assert len(r.svar) == 17
    assert r.svar[0].organisation == "Förvaltningsrätten i Jönköping"
    assert r.svar[0].source_url.endswith("/forvaltningsratten-i-jonkoping.pdf")
    assert all(not s.downloaded for s in r.svar)


def test_parse_case_open():
    r = _open()
    assert r.dnr is None
    assert r.svar == []
    assert r.departement == "Justitiedepartementet"
    assert r.remitterat == [{"typ": "ds", "basefile": "2026:15"}]
    # the "senast den …" deadline phrasing is still recovered
    assert r.sista_svarsdag == "2026-10-30"


def test_parse_case_no_genvagar_falls_back_to_title():
    """A real case page (SOU 2026:8) carries no "Genvägar" island at all --
    remitterat must still resolve from the "(SOU 2026:8)" named in the title."""
    html = (FILES / "case-no-genvagar.html").read_text()
    url = ("https://www.regeringen.se/remisser/2026/03/"
           "remiss-av-sou-20268-rattssaker-samhallsvard-for-barn-och-unga/")
    r = download.parse_case(html, url)
    assert r.remitterat == [{"typ": "sou", "basefile": "2026:8"}]
    assert r.dnr == "S2026/00236"
    assert len(r.svar) == 42
    assert r.sista_svarsdag == "2026-08-10"


def test_record_round_trips():
    r = _closed()
    back = Remiss.from_dict(r.to_dict())
    assert back.to_dict() == r.to_dict()
    assert isinstance(back.svar[0], Remissinstans)


def test_sync_two_passes(tmp_path, monkeypatch):
    listing = """
    <ul class="list--block">
      <li><div class="sortcompact"><a href="/remisser/2026/07/open-case/">Open</a>
        <div class="block--timeLinks"><p>Publicerad
          <time datetime="2026-07-02">02 juli 2026</time></p></div></div></li>
      <li><div class="sortcompact"><a href="/remisser/2026/04/closed-case/">Closed</a>
        <div class="block--timeLinks"><p>Publicerad
          <time datetime="2026-04-09">09 april 2026</time></p></div></div></li>
    </ul>"""
    open_html = (FILES / "case-open.html").read_text()
    closed_html = (FILES / "case-closed.html").read_text()

    class Resp:
        def __init__(self, text=None, content=None):
            self.text, self.content = text, content

    def fake_request(session, method, url, **kw):
        if "/remisser/?p=1" in url:
            return Resp(text=listing)
        if "/remisser/?p=" in url:
            return Resp(text="<html></html>")
        if url.endswith("/open-case/"):
            return Resp(text=open_html)
        if url.endswith("/closed-case/"):
            return Resp(text=closed_html)
        if url.endswith(".pdf"):
            return Resp(content=b"%PDF-1.4 fake")
        raise AssertionError("unexpected url %s" % url)

    monkeypatch.setattr(download, "request", fake_request)
    monkeypatch.setattr(download, "make_session", lambda ua: object())
    monkeypatch.setattr(download.time, "sleep", lambda s: None)
    _redirect(tmp_path, monkeypatch)

    summary = download.sync(delay=0)
    assert summary["new"] == 2
    assert summary["fetched"] == 17
    assert download.list_basefiles() == ["closed-case", "open-case"]
    assert len(list((tmp_path / "downloaded" / "closed-case").glob("*.pdf"))) == 17
    record = json.loads((tmp_path / "downloaded" / "closed-case.json").read_text())
    assert all(s["downloaded"] for s in record["svar"])

    # a second run is incremental (no new cases) and re-fetches no PDF
    again = download.sync(delay=0)
    assert again["new"] == 0
    assert again["fetched"] == 0


def test_sync_stubs_unreachable_case_and_recovers(tmp_path, monkeypatch):
    """A case page that HTTP-errors in pass 1 must still leave a record on
    disk: the on-disk slug is the incremental stop condition, so an older
    failed case written *after* newer slugs would otherwise fall behind the
    watermark and vanish from every later incremental run. The stub (no
    deadline -> still open) is re-polled until a fetch succeeds."""
    listing = """
    <ul class="list--block">
      <li><div class="sortcompact"><a href="/remisser/2026/07/open-case/">Open</a>
        </div></li>
      <li><div class="sortcompact"><a href="/remisser/2026/04/closed-case/">Broken case title</a></div></li>
    </ul>"""
    open_html = (FILES / "case-open.html").read_text()
    closed_html = (FILES / "case-closed.html").read_text()
    broken = {"https://www.regeringen.se/remisser/2026/04/closed-case/"}

    class Resp:
        def __init__(self, text=None, content=None):
            self.text, self.content = text, content

    def fake_request(session, method, url, **kw):
        if url in broken:
            raise requests.HTTPError("500 Server Error: %s" % url)
        if "/remisser/?p=1" in url:
            return Resp(text=listing)
        if "/remisser/?p=" in url:
            return Resp(text="<html></html>")
        if url.endswith("/open-case/"):
            return Resp(text=open_html)
        if url.endswith("/closed-case/"):
            return Resp(text=closed_html)
        if url.endswith(".pdf"):
            return Resp(content=b"%PDF-1.4 fake")
        raise AssertionError("unexpected url %s" % url)

    monkeypatch.setattr(download, "request", fake_request)
    monkeypatch.setattr(download, "make_session", lambda ua: object())
    monkeypatch.setattr(download.time, "sleep", lambda s: None)
    _redirect(tmp_path, monkeypatch)

    summary = download.sync(delay=0)
    assert summary["new"] == 1 and summary["failed"] == 1
    # the failed case exists as a stub carrying the listing facts, so the
    # incremental walk still stops at it next run
    stub = json.loads((tmp_path / "downloaded" / "closed-case.json").read_text())
    assert stub["titel"] == "Broken case title"
    assert stub["svar"] == [] and stub["sista_svarsdag"] is None

    # once the page is reachable, the normal repoll pass completes the record
    broken.clear()
    again = download.sync(delay=0)
    assert again["new"] == 0 and again["failed"] == 0
    record = json.loads((tmp_path / "downloaded" / "closed-case.json").read_text())
    assert record["dnr"] == "KN2026/00741"
    assert len(record["svar"]) == 17 and again["fetched"] == 17


def test_fetch_pending_rejects_duplicate_org_slugs(tmp_path):
    """Two answer PDFs sharing a basename would silently overwrite each other
    under downloaded/<case>/<slug>.pdf and mis-join both basefiles to the
    first organisation -- _fetch_pending fails fast instead."""
    remiss = Remiss(
        basefile="case", titel="t", url="https://example.org/case/",
        svar=[Remissinstans(organisation="Ale kommun",
                            source_url="https://x/contentassets/aa/remissvar.pdf"),
              Remissinstans(organisation="Kammarkollegiet",
                            source_url="https://x/contentassets/bb/remissvar.pdf")])
    with pytest.raises(AssertionError, match="duplicate org slugs"):
        download._fetch_pending(object(), remiss, 0)
