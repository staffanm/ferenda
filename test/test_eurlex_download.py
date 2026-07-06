"""Tests for the eurlex harvest's handling of metadata-only works -- a CELEX
with no Swedish/English manifestation (a pre-accession act never translated)
must not be left on disk as a bare notice, and prune_empty cleans up any such
dirs earlier runs created -- and its content-format fallback (a scanned TIFF
served under an fmx4 manifestation is rejected for the next text type)."""

from datetime import date, timedelta

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


def test_watermark_round_trip_and_legacy_format(tmp_path):
    # legacy plain-date file (pre run-recency): high only, no run date
    (tmp_path / ".watermark-treaties").write_text("2022-05-05")
    assert D.read_watermark(tmp_path, "treaties") == (date(2022, 5, 5), None)

    # new format round-trips both dates
    D.write_watermark(tmp_path, "treaties", "2022-05-05", run=date(2026, 7, 4))
    assert D.read_watermark(tmp_path, "treaties") == (date(2022, 5, 5),
                                                      date(2026, 7, 4))

    # the resume write (interrupted walk) carries no run date -- an unfinished
    # walk must not claim recency
    D.write_watermark(tmp_path, "treaties", "2023-01-01")
    assert D.read_watermark(tmp_path, "treaties") == (date(2023, 1, 1), None)

    # no file at all
    assert D.read_watermark(tmp_path, "acts") == (None, None)


def test_incremental_floor_advances_with_run_recency():
    # a quiet sector (treaties: nothing since 2022) must not pin the window to
    # its last document -- a recent run advances the floor to run - 183 days
    assert D.incremental_floor(date(2022, 5, 5), date(2026, 7, 4)) \
        == date(2026, 7, 4) - D.RECENCY_WINDOW

    # a dormant *harvester* (old run) must not skip the years it never saw
    assert D.incremental_floor(date(2022, 5, 5), date(2024, 1, 1)) \
        == date(2024, 1, 1) - D.RECENCY_WINDOW

    # legacy watermark (run unknown): behave exactly as before
    assert D.incremental_floor(date(2022, 5, 5), None) == date(2022, 5, 5)
    assert D.incremental_floor(None, None) is None


def test_incremental_floor_reaches_below_high_for_an_active_sector():
    # regression: the floor must reach BELOW high by the lag allowance, so a work
    # dated under high but indexed later (CELLAR indexes out of wdate order by up
    # to RECENCY_WINDOW) is re-enumerated, not lost. The old max(high, run-window)
    # pinned the floor at high for an active sector and buried such works forever.
    high, run = date(2026, 7, 1), date(2026, 7, 4)
    floor = D.incremental_floor(high, run)
    assert floor == run - D.RECENCY_WINDOW
    assert floor < high                       # the whole point: below high


def test_enum_years_caselaw_walks_from_first_year_ignoring_the_floor():
    # regression: a CJEU judgment's CELEX year is the CASE year, but its work
    # date is the DECISION date, years later. With a 2025 floor a 2020-case
    # judgment decided in 2025 (62020CJ...) must still be enumerated, so caselaw
    # walks every year from first_year -- the floor only prunes within a year.
    caselaw = D.SECTORS["caselaw"]
    years = list(D.enum_years(caselaw, date(2025, 1, 1)))
    assert years[0] == caselaw.first_year          # 1954, not 2025
    assert years[-1] == date.today().year


def test_enum_years_legislation_and_treaties_start_at_the_floor_year():
    # sector 3/1 CELEX year == work year (no case-vs-decision lag), so the walk
    # may skip the decades below the floor
    for name in ("acts", "treaties"):
        sector = D.SECTORS[name]
        assert list(D.enum_years(sector, date(2025, 3, 1)))[0] == 2025
        assert list(D.enum_years(sector, None))[0] == sector.first_year  # no floor
    # caselaw with no floor also starts at first_year (the walk is unbounded below)
    assert list(D.enum_years(D.SECTORS["caselaw"], None))[0] == \
        D.SECTORS["caselaw"].first_year


def test_enum_query_keeps_wdate_less_documents():
    # regression: work_date_document is OPTIONAL, so a wdate-less work leaves ?d
    # unbound; a bare `?d >= ...` evaluates error->false and silently drops it
    # from every incremental run. The filter must admit an unbound ?d.
    q = D._enum_query("62020CJ", date(2025, 1, 1))
    assert "!BOUND(?d)" in q
    assert '?d >= "2025-01-01"^^xsd:date' in q
    # no floor -> no date filter at all (nothing to admit or exclude)
    assert "!BOUND" not in D._enum_query("62020CJ", None)


def test_pending_sidecar_round_trip(tmp_path):
    assert D.read_pending(tmp_path, "caselaw") == []
    D.write_pending(tmp_path, "caselaw", {"62020CJ0100", "61993CC0425"})
    assert D.read_pending(tmp_path, "caselaw") == ["61993CC0425", "62020CJ0100"]


def test_worth_retrying_only_recent_or_undated_works():
    today = date(2026, 7, 6)
    recent = (today - D.RECENCY_WINDOW + timedelta(days=1)).isoformat()
    old = (today - D.RECENCY_WINDOW - timedelta(days=1)).isoformat()
    assert D.worth_retrying(recent, today=today)       # may still gain content
    assert not D.worth_retrying(old, today=today)       # permanent no-content act
    assert D.worth_retrying(None, today=today)          # undated: keep, don't lose


def _stub_session(monkeypatch):
    monkeypatch.setattr(D, "make_session", lambda ua: object())


def test_sync_retries_pending_no_content_work_and_clears_it(tmp_path, monkeypatch):
    # a CELEX earlier runs stored no content for sits on the sidecar; an
    # incremental run retries it *before* the walk and, now that content exists,
    # downloads it and drops it -- the floor never gets a chance to bury it
    D.write_pending(tmp_path, "caselaw", ["62020CJ0100"])
    _stub_session(monkeypatch)
    monkeypatch.setattr(D, "enumerate_celex", lambda *a, **k: iter(()))
    monkeypatch.setattr(D, "fetch_selection", lambda s, celexes, langs:
                        {"62020CJ0100": [("swe", [("xhtml", "u")])]})
    monkeypatch.setattr(D, "fetch_metadata", lambda s, celexes:
                        ({"62020CJ0100": "2025-06-01"}, {}))

    class Resp:
        content = b"<?xml version='1.0'?><html/>"
    monkeypatch.setattr(D, "request", lambda *a, **k: Resp())

    _seen, stored, _skipped = D.sync(tmp_path, "caselaw", delay=0)
    assert stored == 1
    assert D.is_downloaded(tmp_path, "62020CJ0100")
    assert D.read_pending(tmp_path, "caselaw") == []      # cleared on success


def test_sync_keeps_recent_pending_but_drops_aged_out(tmp_path, monkeypatch):
    # both stay contentless this run: the recent one is kept for another try, the
    # one now older than the window is a permanent no-content act and dropped, so
    # the sidecar cannot grow without bound
    D.write_pending(tmp_path, "caselaw", ["62020CJ0100", "61990CJ0001"])
    _stub_session(monkeypatch)
    monkeypatch.setattr(D, "enumerate_celex", lambda *a, **k: iter(()))
    monkeypatch.setattr(D, "fetch_selection", lambda s, c, l: {})   # no content
    today = date.today()
    recent = (today - D.RECENCY_WINDOW + timedelta(days=5)).isoformat()
    old = (today - D.RECENCY_WINDOW - timedelta(days=5)).isoformat()
    monkeypatch.setattr(D, "fetch_metadata", lambda s, c:
                        ({"62020CJ0100": recent, "61990CJ0001": old}, {}))

    D.sync(tmp_path, "caselaw", delay=0)
    assert D.read_pending(tmp_path, "caselaw") == ["62020CJ0100"]


def test_sync_records_a_recent_no_content_work_from_the_walk(tmp_path, monkeypatch):
    # a recent judgment enumerated in the walk but with no swe/eng content is
    # recorded for retry; without the sidecar the floor would bury it once its
    # work date ages past the window
    _stub_session(monkeypatch)
    recent = (date.today() - timedelta(days=10)).isoformat()
    monkeypatch.setattr(D, "enumerate_celex", lambda s, sec, since:
                        iter([(2025, [("62025CJ0009", recent)])]))
    monkeypatch.setattr(D, "fetch_selection", lambda s, c, l: {})    # no content
    monkeypatch.setattr(D, "fetch_metadata", lambda s, c: ({}, {}))

    D.sync(tmp_path, "caselaw", delay=0)
    assert D.read_pending(tmp_path, "caselaw") == ["62025CJ0009"]
