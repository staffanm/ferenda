"""Tests for the eurlex harvest's handling of metadata-only works -- a CELEX
with no Swedish/English manifestation (a pre-accession act never translated)
must not be left on disk as a bare notice, and prune_empty cleans up any such
dirs earlier runs created -- and its content-format fallback (a scanned TIFF
served under an fmx4 manifestation is rejected for the next text type). Plus
the want-list the citation-driven backfill harvests from
(`catalog.dangling_targets`)."""

from datetime import date, timedelta

import pytest

from accommodanda.eurlex import download as D
from accommodanda.lib import cellar as C
from accommodanda.lib import catalog

TIFF = b"II*\x00\x12p\x00\x00"          # little-endian TIFF magic + noise


def test_store_document_skips_a_work_with_no_manifestation(tmp_path):
    # empty selection -> no swe/eng manifestation: nothing is written, not even a
    # notice (session is never touched, so None is fine)
    stored = C.store_document(None, tmp_path / "1965" / "31965R0163",
                              "31965R0163", "1965-11-25", [], [])
    assert stored == []
    assert not (tmp_path / "1965").exists()


def test_content_ok_rejects_image_under_a_text_type():
    assert not C._content_ok("fmx4", TIFF)          # scanned placeholder
    assert C._content_ok("fmx4", b"  <?xml ?>")     # real Formex
    assert C._content_ok("fmx4", C.ZIP_MAGIC + b"x")  # zipped Formex bundle
    assert not C._content_ok("xhtml", TIFF)
    assert C._content_ok("html", b"<!DOCTYPE html>")
    assert C._content_ok("pdf", b"%PDF-1.4")
    assert not C._content_ok("pdf", TIFF)


def test_ranked_types_orders_fmx4_xhtml_html_then_pdf():
    by_type = {"html": [1], "pdf": [2], "fmx4": [3], "xhtml": [4], "pdfa1a": [5]}
    assert C._ranked_types(by_type) == ["fmx4", "xhtml", "html", "pdf", "pdfa1a"]


def _fake_sparql(selection_rows, stream_rows):
    """A sparql_select stand-in routing by query shape: the item-scoped stream
    query mentions owl:sameAs, the selection query does not."""
    return lambda session, query: (stream_rows if "owl:sameAs" in query
                                   else selection_rows)


def _row(celex, lang, mtype, item):
    return {"celex": {"value": celex}, "lang": {"value": lang},
            "mtype": {"value": mtype}, "item": {"value": item}}


def test_fetch_selection_degrades_a_wrapper_only_fmx4_to_html(monkeypatch):
    # a wrapper-only work: its Formex manifestation's single item is the
    # .doc.xml manifest, not content. fetch_selection must drop the fmx4 type
    # entirely and degrade to the next type (as bulk._select_content does),
    # never ship the wrapper -- and single-item manifestations must enter
    # wrapper disambiguation for this to be seen at all
    monkeypatch.setattr(C, "sparql_select", _fake_sparql(
        [_row("32000L0001", "SWE", "fmx4", "u-doc"),
         _row("32000L0001", "SWE", "html", "u-html")],
        [{"item": {"value": "u-doc"},
          "stream": {"value": "http://x/L_2000001SV.doc.xml"}}]))
    out = C.fetch_selection(object(), ["32000L0001"], ["swe"])
    assert out["32000L0001"] == [("swe", [("html", "u-html", None)])]


def test_fetch_selection_keeps_the_real_item_beside_its_wrapper(monkeypatch):
    # the common case: a Formex manifestation carrying both the real .xml item
    # and its .doc.xml wrapper -- the real item wins, the wrapper is dropped
    monkeypatch.setattr(C, "sparql_select", _fake_sparql(
        [_row("32000L0001", "SWE", "fmx4", "u-doc"),
         _row("32000L0001", "SWE", "fmx4", "u-xml")],
        [{"item": {"value": "u-doc"},
          "stream": {"value": "http://x/L_2000001SV.doc.xml"}},
         {"item": {"value": "u-xml"},
          "stream": {"value": "http://x/L_2000001SV.01.xml"}}]))
    out = C.fetch_selection(object(), ["32000L0001"], ["swe"])
    assert out["32000L0001"] == [("swe", [("fmx4", "u-xml", None)])]


def test_fetch_selection_takes_a_multipart_formex_as_the_whole_manifestation(
        monkeypatch):
    # 2004/18 is published as twelve OJ files (main text + one per annex), each
    # its own Formex item beside its .doc.xml wrapper. Picking one item stores
    # an annex *as the directive* (it did: the stored 32004L0018 was "BILAGA I",
    # 0 articles). Multi-part Formex must be fetched as the whole manifestation
    # in one zip -- the .fmx4.zip bundle parse.formex_members reads in order.
    items = ["http://x/cellar/uuid.0011.04/DOC_%d" % n for n in (1, 2, 3)]
    monkeypatch.setattr(C, "sparql_select", _fake_sparql(
        [_row("32004L0018", "SWE", "fmx4", u) for u in items]
        + [_row("32004L0018", "SWE", "fmx4", "u-doc")],
        [{"item": {"value": "u-doc"},
          "stream": {"value": "http://x/L_2004134SV.01011401.doc.xml"}}]
        + [{"item": {"value": u},
            "stream": {"value": "http://x/L_2004134SV.0101140%d.xml" % i}}
           for i, u in enumerate(items)]))
    out = C.fetch_selection(object(), ["32004L0018"], ["swe"])
    assert out["32004L0018"] == [
        ("swe", [("fmx4", "http://x/cellar/uuid.0011.04", C.ZIP_ACCEPT)])]


def test_store_document_asks_for_zip_only_on_the_manifestation_candidate(
        tmp_path, monkeypatch):
    # the zip Accept travels with the candidate: a manifestation URL is fetched
    # as an archive, a plain item URL with the session default
    asked = []

    class Resp:
        content = C.ZIP_MAGIC + b"junk"

    def fake_request(session, method, url, **kw):
        asked.append((url, (kw.get("headers") or {}).get("Accept")))
        return Resp()

    monkeypatch.setattr(C, "request", fake_request)
    target = tmp_path / "2004" / "32004L0018"
    C.store_document(object(), target, "32004L0018", "2004-03-31",
                     [("swe", [("fmx4", "http://x/cellar/uuid.0011.04",
                                C.ZIP_ACCEPT)]),
                      ("eng", [("html", "u-html", None)])], [])
    assert asked == [("http://x/cellar/uuid.0011.04", C.ZIP_ACCEPT),
                     ("u-html", None)]
    assert (target / "swe.fmx4.zip").exists()   # zip-ness flagged in the name


def test_manifestation_url_rejects_a_non_item_url():
    assert (C.manifestation_url("http://x/cellar/uuid.0011.04/DOC_12")
            == "http://x/cellar/uuid.0011.04")
    # ValueError, not AssertionError: the check must survive `python -O`, or a
    # changed CELLAR convention would silently fetch the wrong resource
    with pytest.raises(ValueError, match="not a CELLAR item URL"):
        C.manifestation_url("http://x/cellar/uuid.0011.04")


# --- the backfill want-list ------------------------------------------------

CELEX = "https://lagen.nu/ext/celex/"


def _corpus(tmp_path):
    """A catalog holding one act, citing three others it does not hold."""
    con = catalog.connect(str(tmp_path / "c.sqlite"))
    con.execute(
        "INSERT INTO documents (uri, source, kind, path) VALUES (?,?,?,?)",
        (CELEX + "62018CJ0041", "eurlex", "case", "x.json"))
    con.executemany(
        "INSERT INTO links (from_uri, from_anchor, predicate, to_uri, to_root, "
        "text) VALUES (?,?,?,?,?,?)",
        [("https://lagen.nu/2016:1145", "K1P1", "dcterms:references",
          CELEX + "32004L0018#45", CELEX + "32004L0018", "artikel 45"),
         (CELEX + "62018CJ0041", None, "dcterms:references",
          CELEX + "32004L0018#2", CELEX + "32004L0018", "artikel 2"),
         (CELEX + "62018CJ0041", None, "dcterms:references",
          CELEX + "31992L0050", CELEX + "31992L0050", "92/50"),
         # a target the corpus *does* hold, and a non-sector-3 one
         ("https://lagen.nu/2016:1145", None, "dcterms:references",
          CELEX + "62018CJ0041", CELEX + "62018CJ0041", "C-41/18")])
    con.commit()
    return con


def test_dangling_targets_ranks_what_the_corpus_cites_but_lacks(tmp_path):
    con = _corpus(tmp_path)
    got = catalog.dangling_targets(con, CELEX + "3")
    # most-cited first, with the count of distinct citing documents
    assert got == [(CELEX + "32004L0018", 2, 2), (CELEX + "31992L0050", 1, 1)]


def test_dangling_targets_excludes_documents_the_corpus_holds(tmp_path):
    # the judgment is cited once but IS in `documents`, so it is not a gap --
    # this is the whole point of the query, and the reason a bulk-dump corpus
    # can name its own missing repealed acts
    con = _corpus(tmp_path)
    assert not [row for row in catalog.dangling_targets(con, CELEX)
                if row[0] == CELEX + "62018CJ0041"]


def test_dangling_targets_ties_break_on_uri_so_a_run_is_reproducible(tmp_path):
    # two targets cited once each: the order must come from the uri, not from
    # whatever the group-by happened to emit -- a backfill run is resumable
    con = _corpus(tmp_path)
    con.execute(
        "INSERT INTO links (from_uri, predicate, to_uri, to_root) "
        "VALUES (?,?,?,?)",
        ("https://lagen.nu/2016:1145", "dcterms:references",
         CELEX + "31993L0036", CELEX + "31993L0036"))
    con.commit()
    tail = [uri for uri, n, _d in catalog.dangling_targets(con, CELEX + "3")
            if n == 1]
    assert tail == [CELEX + "31992L0050", CELEX + "31993L0036"]


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

    monkeypatch.setattr(C, "request", fake_request)
    target = tmp_path / "1993" / "61993CC0425"
    selection = [("swe", [("fmx4", "u-fmx4", None),
                          ("xhtml", "u-xhtml", None)])]
    stored = C.store_document(object(), target, "61993CC0425", "1993-01-01",
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

    monkeypatch.setattr(C, "request", lambda *a, **kw: Resp())
    target = tmp_path / "1993" / "61993CC0425"
    selection = [("swe", [("fmx4", "u1", None), ("pdf", "u2", None)]),
                 ("eng", [("fmx4", "u3", None)])]
    stored = C.store_document(object(), target, "61993CC0425", "1993-01-01",
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


def test_enum_years_caselaw_reaches_a_bounded_lookback_below_the_floor():
    # regression: a CJEU judgment's CELEX year is the CASE year, but its work
    # date is the DECISION date, a few years later. With a 2025 floor a
    # 2020-case judgment decided in 2025 (62020CJ...) must still be enumerated,
    # so caselaw reaches CASELAW_DECISION_LAG_YEARS below the floor -- but NOT
    # all the way to first_year (which meant ~73 slow SPARQL queries per run).
    caselaw = D.SECTORS["caselaw"]
    years = list(D.enum_years(caselaw, date(2025, 1, 1)))
    assert years[0] == 2025 - D.CASELAW_DECISION_LAG_YEARS   # 2020, not 1954
    assert years[-1] == date.today().year
    # the lookback never underflows first_year
    assert list(D.enum_years(caselaw, date(1955, 1, 1)))[0] == caselaw.first_year


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


def test_enum_query_language_filter_is_caselaw_only():
    # caselaw (require_language_expression) restricts discovery to works that
    # carry a swe/eng expression, so procedural-language-only judgments are never
    # selected+discarded per run. A FILTER EXISTS keeps one row per CELEX.
    q = D._enum_query("62020CJ", None, ("swe", "eng"))
    assert "FILTER EXISTS" in q
    assert "cdm:expression_uses_language" in q
    # the full chain down to a downloadable item, not just an expression
    assert "cdm:item_belongs_to_manifestation" in q
    assert '"SWE", "ENG"' in q
    # treaties/acts pass no languages -> no expression join, query unchanged
    assert "expression_uses_language" not in D._enum_query("32020R", None)
    # the caselaw sector is the one wired to ask for it
    assert D.SECTORS["caselaw"].require_language_expression
    assert not D.SECTORS["acts"].require_language_expression


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
                        {"62020CJ0100": [("swe", [("xhtml", "u", None)])]})
    monkeypatch.setattr(D, "fetch_metadata", lambda s, celexes:
                        ({"62020CJ0100": "2025-06-01"}, {}))

    class Resp:
        content = b"<?xml version='1.0'?><html/>"
    monkeypatch.setattr(C, "request", lambda *a, **k: Resp())

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
    monkeypatch.setattr(D, "enumerate_celex", lambda s, sec, since, languages=None:
                        iter([(2025, [("62025CJ0009", recent)])]))
    monkeypatch.setattr(D, "fetch_selection", lambda s, c, l: {})    # no content
    monkeypatch.setattr(D, "fetch_metadata", lambda s, c: ({}, {}))

    D.sync(tmp_path, "caselaw", delay=0)
    assert D.read_pending(tmp_path, "caselaw") == ["62025CJ0009"]
