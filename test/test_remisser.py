"""remisser vertical (regeringen.se public-referral responses): listing + case
detail parsing and the two-pass sync driver.

Hermetic: parses the three captured live pages under test/files/remisser/ and
drives sync() against a stubbed session -- no network."""

import json
from datetime import date
from pathlib import Path

import pytest
import requests

from accommodanda.lib import compress, layout
from accommodanda.remisser import download
from accommodanda.remisser.model import Remiss, Remissinstans


def _redirect(tmp_path, monkeypatch):
    """Point remisser's download tree (records + answer PDFs) and its
    examined-ärende index at tmp_path."""
    monkeypatch.setattr(layout, "REMISSER_DOWNLOADED", tmp_path / "downloaded")
    monkeypatch.setattr(layout, "REMISSER_SEEN",
                        tmp_path / "downloaded" / ".seen.json")

FILES = Path(__file__).parent / "files" / "remisser"
CLOSED_URL = ("https://www.regeringen.se/remisser/2026/04/remiss-av-"
              "adelmetallutredningen--en-moderniserad-reglering-av-handel-med-"
              "adelmetallarbeten-sou-202614/")
OPEN_URL = ("https://www.regeringen.se/remisser/2026/07/"
            "remiss-av--ds-202615-galdenarens-avtal-i-konkurs/")


def _closed():
    return download.parse_arende((FILES / "case-closed.html").read_text(), CLOSED_URL)


def _open():
    return download.parse_arende((FILES / "case-open.html").read_text(), OPEN_URL)


def test_parse_listing():
    items = download.parse_listing((FILES / "listing.html").read_text())
    assert len(items) == 20
    ds = next(i for i in items
              if i["slug"] == "remiss-av--ds-202615-galdenarens-avtal-i-konkurs")
    assert ds["url"] == OPEN_URL
    assert ds["title"].startswith("Remiss av")


def test_parse_case_closed():
    r = _closed()
    # the ärende is keyed on the document it remits, not on the regeringen.se slug
    assert r.basefile == "sou/2026:14"
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


def test_parse_case_promemoria_keys_on_dnr():
    """A departementspromemoria is remitted with a /rattsliga-dokument/ link (so
    it is regeringen's own document) but carries no series number -- the modern
    replacement for a numbered Ds. Its basefile is the diarienummer, exactly how
    the forarbete vertical keys the `pm` type."""
    r = download.parse_arende((FILES / "case-promemoria.html").read_text(),
                            "https://www.regeringen.se/remisser/2026/07/remiss-av-"
                            "promemorian-nationellt-forbud-mot-pfas-i-vissa-"
                            "konsumentprodukter/")
    assert r.dnr == "KN2026/01597"
    # the landing slug rides along: forarbete keys a promemoria on the dnr only
    # when its own listing stated one, else on this slug, and the remiss page
    # cannot tell which -- so the join gets both candidates
    assert r.remitterat == [{
        "typ": "pm", "basefile": "KN2026/01597",
        "slug": "nationellt-forbud-mot-pfas-i-vissa-konsumentprodukter"}]
    assert r.basefile == "pm/KN2026/01597"
    assert not r.externt_dokument


def test_match_forarbete_drops_the_subarende_suffix():
    """A remiss opened as sub-ärende 1 of the promemoria's own case is filed as
    "KN2026/01497–1" (en-dash on the page), but the promemoria itself lives under
    the parent dnr -- the cross-ref must name the parent, not the sub-ärende."""
    pm = "/rattsliga-dokument/departementsserien-och-promemorior/2026/07/nagot/"
    expected = {"typ": "pm", "basefile": "KN2026/01497", "slug": "nagot"}
    assert download._match_forarbete(pm, "Promemoria: Något", "KN2026/01497–1") == expected
    # a hyphen spelling of the same suffix, and a dnr with no suffix at all
    assert download._match_forarbete(pm, "Promemoria: Något", "KN2026/01497-2") == expected
    assert download._match_forarbete(pm, "Promemoria: Något", "KN2026/01497") == expected


def test_match_forarbete_falls_back_to_the_landing_slug_without_a_dnr():
    """A promemoria whose page states no diarienummer is keyed on its landing
    slug -- the same fallback `forarbete` applies when its own listing text
    carries no dnr (lib.regeringen.pm_identity)."""
    pm = "/rattsliga-dokument/departementsserien-och-promemorior/2026/07/nagot/"
    assert download._match_forarbete(pm, "Promemoria: Något", None) == {
        "typ": "pm", "basefile": "nagot", "slug": "nagot"}


@pytest.mark.parametrize("fixture,url", [
    # the Commission's own site -- an off-site absolute URL
    ("case-extern-eu.html", "https://www.regeringen.se/remisser/2026/07/remiss-av-"
     "europeiska-kommissionens-forslag-till-forordning-om-utveckling-av-moln-"
     "och-ai-kom2026502/"),
    # a trade body's skrivelse, attached as a bare /contentassets/ PDF
    ("case-extern-skrivelse.html", "https://www.regeringen.se/remisser/2026/03/"
     "remiss-av-skrivelse-forslag-till-forfattningsandringar-till-foljd-av-eus-"
     "reviderade-avloppsvattendirektiv/"),
])
def test_parse_case_flags_externally_authored_document(fixture, url):
    """A remiss whose document regeringen did not publish -- no
    /rattsliga-dokument/ landing page, just an attached PDF or an off-site link.
    Those answers comment on a document this corpus will never hold, so the case
    is flagged and its answers are never harvested."""
    r = download.parse_arende((FILES / fixture).read_text(), url)
    assert r.externt_dokument
    assert r.remitterat == []


def test_parse_case_no_genvagar_falls_back_to_title():
    """A real ärende page (SOU 2026:8) carries no "Genvägar" island at all --
    remitterat must still resolve from the "(SOU 2026:8)" named in the title."""
    html = (FILES / "case-no-genvagar.html").read_text()
    url = ("https://www.regeringen.se/remisser/2026/03/"
           "remiss-av-sou-20268-rattssaker-samhallsvard-for-barn-och-unga/")
    r = download.parse_arende(html, url)
    assert r.remitterat == [{"typ": "sou", "basefile": "2026:8"}]
    assert r.basefile == "sou/2026:8"
    assert r.dnr == "S2026/00236"
    assert len(r.svar) == 42
    assert r.sista_svarsdag == "2026-08-10"


def test_record_round_trips():
    r = _closed()
    back = Remiss.from_dict(r.to_dict())
    assert back.to_dict() == r.to_dict()
    assert isinstance(back.svar[0], Remissinstans)


LISTING_TWO = """
    <ul class="list--block">
      <li><div class="sortcompact"><a href="/remisser/2026/07/open-case/">Open</a>
        </div></li>
      <li><div class="sortcompact"><a href="/remisser/2026/04/closed-case/">Closed</a>
        </div></li>
    </ul>"""


class Resp:
    def __init__(self, text=None, content=None):
        self.text, self.content = text, content


def _fake_request(pages, cases, hits=None, total=2):
    """A stubbed `net.request`: `pages` is the listing HTML for page 1, `cases`
    maps a case-url tail to its HTML (or an exception instance to raise).
    `total` is the listing's TotalCount, which sync checks the walk against."""
    def fake_request(session, method, url, **kw):
        if hits is not None:
            hits.append(url)
        if "GetFilteredItems" in url:
            return {"Message": pages if "&page=1" in url else "",
                    "TotalCount": total}
        if url.endswith(".pdf"):
            return Resp(content=b"%PDF-1.4 fake")
        for tail, html in cases.items():
            if url.endswith(tail):
                if isinstance(html, Exception):
                    raise html
                return Resp(text=html)
        raise AssertionError("unexpected url %s" % url)
    return fake_request


def _drive(monkeypatch, tmp_path, request_fn):
    monkeypatch.setattr(download, "request", request_fn)
    monkeypatch.setattr(download, "make_session", lambda ua: object())
    monkeypatch.setattr(download.time, "sleep", lambda s: None)
    _redirect(tmp_path, monkeypatch)


def _seen(tmp_path):
    return json.loads((tmp_path / "downloaded" / ".seen.json").read_text())


def _write_seen(tmp_path, cases, dirty=False):
    path = tmp_path / "downloaded" / ".seen.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"dirty": dirty, "arenden": cases}))


def test_sync_two_passes(tmp_path, monkeypatch):
    """Both cases are keyed on the document they remit, so the record lands at
    ``<typ>/<id-slug>.json`` with its answer PDFs in the sibling directory --
    and the examined-index remembers which url slug produced which basefile."""
    _drive(monkeypatch, tmp_path, _fake_request(LISTING_TWO, {
        "/open-case/": (FILES / "case-open.html").read_text(),
        "/closed-case/": (FILES / "case-closed.html").read_text()}))

    summary = download.sync(delay=0)
    assert summary["new"] == 2
    assert summary["fetched"] == 17
    assert download.list_basefiles() == ["ds/2026:15", "sou/2026:14"]
    assert _seen(tmp_path)["arenden"] == {
        # `until` is the deadline plus GRACE_PERIOD -- the date each ärende stops
        # needing to be re-fetched
        "open-case": {"basefile": "ds/2026:15", "until": "2026-11-20"},
        "closed-case": {"basefile": "sou/2026:14", "until": "2026-07-21"}}
    assert not _seen(tmp_path)["dirty"]

    record = json.loads(compress.read_text(
        tmp_path / "downloaded" / "sou" / "2026-14.json"))
    assert record["basefile"] == "sou/2026:14"
    assert all(s["downloaded"] for s in record["svar"])
    pdfs = list((tmp_path / "downloaded" / "sou" / "2026-14").glob("*.pdf"))
    assert len(pdfs) == 17

    # a second run is incremental (no new ärenden) and re-fetches no PDF
    again = download.sync(delay=0)
    assert again["new"] == 0
    assert again["fetched"] == 0


def test_sync_retries_an_unreachable_case_without_stranding_it(tmp_path, monkeypatch):
    """An ärende page that HTTP-errors gets *no* index entry, so the next run
    examines it again. Nothing is written for it in the meantime: under the old
    slug keying a failure had to leave a stub record to stay visible, but a case
    with no readable page has no identity to file a record under."""
    broken = requests.HTTPError("500 Server Error")
    cases = {"/open-case/": (FILES / "case-open.html").read_text(),
             "/closed-case/": broken}
    _drive(monkeypatch, tmp_path, _fake_request(LISTING_TWO, cases))

    summary = download.sync(delay=0)
    assert summary["new"] == 1 and summary["failed"] == 1
    assert download.list_basefiles() == ["ds/2026:15"]
    assert "closed-case" not in _seen(tmp_path)["arenden"]
    # the failure leaves the index dirty, so the next walk covers the whole
    # archive instead of stopping on a run of already-examined cases
    assert _seen(tmp_path)["dirty"]

    cases["/closed-case/"] = (FILES / "case-closed.html").read_text()
    again = download.sync(delay=0)
    assert again["new"] == 1 and again["failed"] == 0
    assert download.list_basefiles() == ["ds/2026:15", "sou/2026:14"]
    assert again["fetched"] == 17
    assert not _seen(tmp_path)["dirty"]


def test_sync_retries_a_malformed_case(tmp_path, monkeypatch):
    """A 200 whose DOM `parse_arende` can't read (a bot-challenge interstitial, a
    truncated response) is retried exactly like an HTTP error, and the run
    continues to the other ärenden rather than aborting."""
    cases = {"/open-case/": (FILES / "case-open.html").read_text(),
             "/closed-case/": "<html><body>bot challenge</body></html>"}
    _drive(monkeypatch, tmp_path, _fake_request(LISTING_TWO, cases))

    summary = download.sync(delay=0)
    assert summary["new"] == 1 and summary["failed"] == 1
    assert download.list_basefiles() == ["ds/2026:15"]

    cases["/closed-case/"] = (FILES / "case-closed.html").read_text()
    again = download.sync(delay=0)
    assert again["new"] == 1 and again["failed"] == 0
    assert download.list_basefiles() == ["ds/2026:15", "sou/2026:14"]


def test_parse_case_raises_on_a_document_with_no_identity_rule(monkeypatch):
    """A remiss that sends out a regeringen.se document whose doctype has no
    identity rule must fail loudly rather than mint a stub basefile that no join
    could ever find -- the sweep records it as a per-ärende failure and retries it
    once the rule is added."""
    html = """<html><body>
      <h1 id="h1id">Remiss av något helt nytt</h1>
      <div><h2 class="h-underlined">Dokument som remitteras</h2>
        <ul><li><a href="/rattsliga-dokument/nagot-helt-nytt/2026/07/x/">Något
          helt nytt</a></li></ul></div>
    </body></html>"""
    with pytest.raises(ValueError, match="yields no basefile"):
        download.parse_arende(html, "https://www.regeringen.se/remisser/2026/04/x/")


def test_fetch_pending_rejects_duplicate_org_slugs(tmp_path):
    """Two answer PDFs sharing a basename would silently overwrite each other
    under downloaded/<typ>/<id-slug>/<org>.pdf and mis-join both basefiles to
    the first organisation -- _fetch_pending fails fast instead.

    This must be a ValueError, not an AssertionError: an `assert` here would
    be stripped out under `python -O`, silently letting the collision through
    to the overwrite it exists to prevent (rule:errors-drive-retry-use-raise)."""
    remiss = Remiss(
        basefile="sou/2026:14", titel="t", url="https://example.org/case/",
        svar=[Remissinstans(organisation="Ale kommun",
                            source_url="https://x/contentassets/aa/remissvar.pdf"),
              Remissinstans(organisation="Kammarkollegiet",
                            source_url="https://x/contentassets/bb/remissvar.pdf")])
    with pytest.raises(ValueError, match="duplicate org slugs"):
        download._fetch_pending(object(), remiss, 0)


def test_fetch_pending_refuses_an_empty_body(tmp_path, monkeypatch):
    """regeringen.se serves some attachments as 200 with Content-Length: 0 (a
    broken upload on their side -- one in the first 1781 answers). Writing that
    would mark the answer downloaded and hand the parse stage a zero-byte "PDF",
    where pdftohtml exits non-zero and fails the document. The answer stays
    unfetched instead, to be retried on the next poll."""
    logged = []
    _redirect(tmp_path, monkeypatch)
    monkeypatch.setattr(download.time, "sleep", lambda s: None)
    remiss = Remiss(
        basefile="sou/2026:14", titel="t", url="https://example.org/case/",
        svar=[Remissinstans(organisation="Tomma verket",
                            source_url="https://x/tomma-verket.pdf"),
              Remissinstans(organisation="Statskontoret",
                            source_url="https://x/statskontoret.pdf")])

    def fake_request(session, method, url, **kw):
        return Resp(content=b"" if "tomma-verket" in url else b"%PDF-1.4 fake")

    monkeypatch.setattr(download, "request", fake_request)
    assert download._fetch_pending(object(), remiss, 0, logged.append) == 1
    assert [i.downloaded for i in remiss.svar] == [False, True]
    assert not layout.remisser_answer("sou/2026:14", "tomma-verket").exists()
    assert any("served 0 bytes" in msg for msg in logged)


def test_fetch_pending_refetches_a_record_marked_downloaded_but_empty(
        tmp_path, monkeypatch):
    """`downloaded` alone doesn't settle it -- a record written before the
    empty-body guard existed points at a zero-byte file. Re-checking the bytes on
    disk is what repairs those without a manual data pass."""
    _redirect(tmp_path, monkeypatch)
    monkeypatch.setattr(download.time, "sleep", lambda s: None)
    path = layout.remisser_answer("sou/2026:14", "tomma-verket")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    remiss = Remiss(
        basefile="sou/2026:14", titel="t", url="https://example.org/case/",
        svar=[Remissinstans(organisation="Tomma verket",
                            source_url="https://x/tomma-verket.pdf",
                            downloaded=True)])

    monkeypatch.setattr(download, "request",
                        lambda s, m, u, **kw: Resp(content=b"%PDF-1.4 real"))
    assert download._fetch_pending(object(), remiss, 0) == 1
    assert path.read_bytes() == b"%PDF-1.4 real"


def test_sync_checkpoints_the_index_so_an_interrupted_walk_resumes(
        tmp_path, monkeypatch):
    """A sweep of the whole archive runs for hours; if the index were only
    written at the end, a kill would throw away every ärende examined. It is
    checkpointed once per listing page, so a run that dies on page two keeps
    page one's cases -- and stays dirty, so the next run re-walks rather than
    trusting a run of hits."""
    cases = {"/open-case/": (FILES / "case-open.html").read_text(),
             "/closed-case/": (FILES / "case-closed.html").read_text()}
    inner = _fake_request(LISTING_TWO, cases)

    def dies_on_page_two(session, method, url, **kw):
        if "GetFilteredItems" in url and "&page=2" in url:
            raise RuntimeError("killed mid-walk")
        return inner(session, method, url, **kw)

    _drive(monkeypatch, tmp_path, dies_on_page_two)

    with pytest.raises(RuntimeError):
        download.sync(delay=0)
    # both of page one's cases survive the kill, and the run is marked unfinished
    assert _seen(tmp_path)["arenden"]["open-case"]["basefile"] == "ds/2026:15"
    assert _seen(tmp_path)["arenden"]["closed-case"]["basefile"] == "sou/2026:14"
    assert _seen(tmp_path)["dirty"]

    # resuming re-walks the listing but re-fetches neither ärende page
    hits = []
    monkeypatch.setattr(download, "request", _fake_request(LISTING_TWO, cases, hits))
    monkeypatch.setattr(download, "date", _FixedDate("2027-01-01"))  # both closed
    download.sync(delay=0)
    assert not any("/open-case/" in url or "/closed-case/" in url for url in hits)
    assert not _seen(tmp_path)["dirty"]


def test_sync_skips_collision_case_without_aborting_sweep(tmp_path, monkeypatch):
    """A single case whose answers collide on org_slug must not blow up the
    whole sweep -- it is that one ärende's own data anomaly, so `sync` logs it,
    leaves that ärende's PDFs unfetched this run (retried next poll), and still
    polls/fetches every other ärende."""
    logged = []
    _redirect(tmp_path, monkeypatch)

    # both ärenden are already known and still open (no `until`), so the catch-up
    # pass polls each from the url its stored record carries
    collider = Remiss(
        basefile="sou/2026:14", titel="t", url="https://example.org/collider/",
        svar=[Remissinstans(organisation="Ale kommun",
                            source_url="https://x/contentassets/aa/remissvar.pdf"),
              Remissinstans(organisation="Kammarkollegiet",
                            source_url="https://x/contentassets/bb/remissvar.pdf")])
    healthy = Remiss(
        basefile="ds/2026:15", titel="t", url="https://example.org/healthy/",
        svar=[Remissinstans(organisation="Statskontoret",
                            source_url="https://x/statskontoret.pdf")])
    for remiss in (collider, healthy):
        path = layout.remisser_arende(remiss.basefile)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(remiss.to_dict(), ensure_ascii=False))
    _write_seen(tmp_path, {
        "collider": {"basefile": "sou/2026:14", "until": None},
        "healthy": {"basefile": "ds/2026:15", "until": None}})

    # the re-fetched pages add no answers, so each ärende keeps the svar it has
    empty_page = """<html><body><h1 id="h1id">Remiss av SOU 2026:14</h1>
      </body></html>"""
    monkeypatch.setattr(download, "request", _fake_request(
        "", {"/collider/": empty_page,
             "/healthy/": empty_page.replace("SOU 2026:14", "Ds 2026:15")},
        total=0))
    monkeypatch.setattr(download, "make_session", lambda ua: object())
    monkeypatch.setattr(download.time, "sleep", lambda s: None)

    summary = download.sync(delay=0, log=logged.append)

    assert any("sou/2026:14" in msg and "duplicate org slugs" in msg
               for msg in logged)
    assert summary["fetched"] == 1   # healthy's one PDF, despite the collision
    after = Remiss.from_dict(json.loads(
        compress.read_text(layout.remisser_arende("sou/2026:14"))))
    assert not any(inst.downloaded for inst in after.svar)
    healthy_after = Remiss.from_dict(json.loads(
        compress.read_text(layout.remisser_arende("ds/2026:15"))))
    assert all(inst.downloaded for inst in healthy_after.svar)


def test_sync_never_stores_or_fetches_an_external_case(tmp_path, monkeypatch):
    """The origin gate end to end: an ärende remitting a document regeringen did not
    publish has no document to be keyed on and is never stored -- it is recorded
    in the examined-index as a null entry so it is examined exactly once, and not
    one of its 93 listed answers is fetched."""
    hits = []
    _drive(monkeypatch, tmp_path, _fake_request(LISTING_TWO, {
        "/open-case/": (FILES / "case-extern-skrivelse.html").read_text(),
        "/closed-case/": (FILES / "case-closed.html").read_text()}, hits))

    summary = download.sync(delay=0)
    assert summary["new"] == 1 and summary["externt"] == 1
    assert download.list_basefiles() == ["sou/2026:14"]      # only the SOU case
    assert _seen(tmp_path)["arenden"]["open-case"]["basefile"] is None
    assert summary["fetched"] == 17          # the SOU case's answers, in full

    # a later run doesn't even re-fetch the external ärende page
    hits.clear()
    download.sync(delay=0)
    assert not any(url.endswith("/open-case/") for url in hits)


class _FixedDate:
    """Stands in for the `date` the downloader imported, pinning `today()` so a
    deadline-driven decision is testable without waiting for the calendar;
    `fromisoformat` still behaves."""
    fromisoformat = staticmethod(date.fromisoformat)

    def __init__(self, iso):
        self._today = date.fromisoformat(iso)

    def today(self):
        return self._today


def test_sync_repolls_an_open_case_and_drops_a_closed_one(tmp_path, monkeypatch):
    """Answers keep arriving for the whole remissperiod, so having examined a
    case is no reason to skip it -- only its closing date is. A case whose
    deadline (plus GRACE_PERIOD) has passed is never fetched again; one still
    inside it is re-fetched every run, and answers posted since last time are
    picked up."""
    hits = []
    open_html = (FILES / "case-open.html").read_text()      # deadline 2026-10-30
    closed_html = (FILES / "case-closed.html").read_text()  # deadline 2026-06-30
    _drive(monkeypatch, tmp_path, _fake_request(LISTING_TWO, {
        "/open-case/": open_html, "/closed-case/": closed_html}, hits))
    # a day inside the open case's window and past the closed one's grace period
    monkeypatch.setattr(download, "date", _FixedDate("2026-08-01"))

    download.sync(delay=0)
    assert download.list_basefiles() == ["ds/2026:15", "sou/2026:14"]
    # the SOU case's 17 answers arrived with the first poll
    assert len(list((tmp_path / "downloaded" / "sou" / "2026-14").glob("*.pdf"))) == 17

    hits.clear()
    summary = download.sync(delay=0)
    assert any(url.endswith("/open-case/") for url in hits)      # still collecting
    assert not any(url.endswith("/closed-case/") for url in hits)  # done
    assert summary["repolled"] == 1 and summary["open"] == 1


def test_sync_walks_past_an_examined_case_to_reach_a_gap(tmp_path, monkeypatch):
    """The incremental walk stops after STOP_AFTER *consecutive* examined cases,
    not at the first one -- so an ärende that failed on an earlier run is reached
    again even when newer cases above it have all succeeded."""
    monkeypatch.setattr(download, "STOP_AFTER", 1)
    cases = {"/open-case/": (FILES / "case-open.html").read_text(),
             "/closed-case/": requests.HTTPError("500 Server Error")}
    _drive(monkeypatch, tmp_path, _fake_request(LISTING_TWO, cases))

    assert download.sync(delay=0)["failed"] == 1
    assert list(_seen(tmp_path)["arenden"]) == ["open-case"]

    # open-case is examined and sits *above* the failed one; with a run-length
    # stop of 1 the walk still passes it and retries closed-case
    cases["/closed-case/"] = (FILES / "case-closed.html").read_text()
    assert download.sync(delay=0)["new"] == 1
    assert download.list_basefiles() == ["ds/2026:15", "sou/2026:14"]



def test_merge_keeps_both_answers_from_same_org(tmp_path):
    """Two distinct answers filed by the same organisation (different PDFs,
    different org_slugs -- e.g. a follow-up submission) must both survive a
    re-poll merge, exactly as the fresh-parse path keeps them both; deduping
    on the organisation's display name would silently drop the second one."""
    stored = Remiss(
        basefile="case", titel="t", url="https://example.org/case/",
        svar=[Remissinstans(organisation="Kammarkollegiet",
                            source_url="https://x/kammarkollegiet.pdf")])
    fresh = Remiss(
        basefile="case", titel="t", url="https://example.org/case/",
        svar=[Remissinstans(organisation="Kammarkollegiet",
                            source_url="https://x/kammarkollegiet.pdf"),
              Remissinstans(organisation="Kammarkollegiet",
                            source_url="https://x/kammarkollegiet-2.pdf")])
    changed = download._merge(stored, fresh)
    assert changed
    assert [inst.source_url for inst in stored.svar] == [
        "https://x/kammarkollegiet.pdf", "https://x/kammarkollegiet-2.pdf"]
