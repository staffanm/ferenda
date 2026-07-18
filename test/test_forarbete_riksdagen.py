"""Tests for the riksdagen betänkande downloader (network-free).

The fixtures are truncated but otherwise real dokumentlista JSON pages
(`test/files/forarbete/bet_dokumentlista_page{1,2}.json`): page 1 mixes a
document with a PDF filbilaga (Bet. 2025/26:JuU47) with one that has none
(Bet. 2026/27:FiU8), and links to page 2 via `@nasta_sida`. The network layer
(`riksdagen.request`) is stubbed; nothing here touches data.riksdagen.se.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from accommodanda.forarbete import parse as fa_parse
from accommodanda.forarbete import riksdagen
from accommodanda.forarbete.parse import mint_uri
from accommodanda.lib import layout
from accommodanda.lib.util import basefile_slug, write_atomic

FIXTURES = Path(__file__).parent / "files" / "forarbete"
PAGE1 = json.loads((FIXTURES / "bet_dokumentlista_page1.json").read_text())
PAGE2 = json.loads((FIXTURES / "bet_dokumentlista_page2.json").read_text())

PDF_BYTES = b"%PDF-1.4 fake betankande body\n%%EOF\n"


def _entry(page, beteckning):
    return next(d for d in page["dokumentlista"]["dokument"]
                if d["beteckning"] == beteckning)


class FakeNet:
    """Stubs `riksdagen.request`: dokumentlista urls resolve against the two
    fixture pages (page 1's `@nasta_sida` -> page 2), every `/fil/` url yields a
    PDF. Records the urls fetched so a test can assert what was (not) requested."""

    def __init__(self):
        self.fetched = []
        self.bad_fil = set()   # fil urls served as a non-PDF (HTML error page, 200)
        page1_url = riksdagen.LISTING
        page2_url = riksdagen._https(PAGE1["dokumentlista"]["@nasta_sida"])
        self.pages = {page1_url: PAGE1, page2_url: PAGE2}

    def request(self, session, method, url, *, parse_json=False, **kwargs):
        self.fetched.append(url)
        if parse_json:
            assert url in self.pages, "unexpected listing url: %r" % url
            return self.pages[url]
        assert "/fil/" in url, "unexpected binary fetch: %r" % url
        if url in self.bad_fil:
            return SimpleNamespace(content=b"<html>tillfalligt fel</html>")
        return SimpleNamespace(content=PDF_BYTES)


def _patch(monkeypatch, net):
    monkeypatch.setattr(riksdagen, "request", net.request)
    monkeypatch.setattr(riksdagen, "make_session", lambda ua: None)
    monkeypatch.setattr(riksdagen.time, "sleep", lambda *_: None)


# --------------------------------------------------------------------------
# entry -> descriptor
# --------------------------------------------------------------------------

def test_descriptor_maps_entry_to_the_grammar_keyed_record():
    entry = _entry(PAGE1, "JuU47")
    d = riksdagen.descriptor(entry)
    assert d["type"] == "bet"
    assert d["basefile"] == "2025/26:JuU47"           # rm:beteckning
    assert d["identifier"] == "Bet. 2025/26:JuU47"    # matches the bet URI form
    assert d["organ"] == "JuU"
    assert d["dok_id"] == entry["dok_id"]
    assert d["date"] == entry["datum"]
    assert d["title"] == entry["titel"]
    assert d["url"].startswith("https://")            # protocol-relative -> https
    assert d["files"] == []


def test_descriptor_basefile_matches_forarbeten_grammar_uri():
    # the point of the feature: the minted URI equals what lib.lagrum mints for a
    # "bet. 2025/26:JuU47" citation, so the citation resolves to this document.
    d = riksdagen.descriptor(_entry(PAGE1, "JuU47"))
    assert mint_uri(d["type"], d["basefile"]) == "https://lagen.nu/bet/2025/26:JuU47"


def test_pdf_fil_present_and_absent():
    assert riksdagen.pdf_fil(_entry(PAGE1, "JuU47"))["typ"] == "pdf"
    assert riksdagen.pdf_fil(_entry(PAGE1, "FiU8")) is None    # filbilaga: null


# --------------------------------------------------------------------------
# pagination
# --------------------------------------------------------------------------

def test_iter_pages_follows_nasta_sida(monkeypatch):
    net = FakeNet()
    _patch(monkeypatch, net)
    pages = list(riksdagen.iter_pages(None, riksdagen.LISTING, delay=0))
    assert [p["@sida"] for p in pages] == ["1", "2"]   # both pages, then stops


def test_iter_pages_stops_when_sida_does_not_advance(monkeypatch):
    # the real API caps pagination and re-serves the capped page with a
    # forward-pointing @nasta_sida; the walk must not loop on it.
    capped = {"dokumentlista": {"@sida": "1", "@traffar": "1", "dokument": [],
                                "@nasta_sida": "http://x/loop"}}

    def request(session, method, url, *, parse_json=False, **kwargs):
        return capped

    monkeypatch.setattr(riksdagen, "request", request)
    monkeypatch.setattr(riksdagen.time, "sleep", lambda *_: None)
    pages = list(riksdagen.iter_pages(None, "http://x/start", delay=0))
    assert len(pages) == 1   # served once, then the sida guard stops the loop


# --------------------------------------------------------------------------
# storage
# --------------------------------------------------------------------------

def test_download_writes_record_and_pdf(monkeypatch, tmp_path):
    net = FakeNet()
    _patch(monkeypatch, net)
    record = riksdagen.download_document(None, tmp_path, _entry(PAGE1, "JuU47"), delay=0)
    assert record["files"] == ["2025-26-JuU47.pdf"]
    pdf = layout.fa_dir(tmp_path, "bet", "2025/26:JuU47") / "2025-26-JuU47.pdf"
    assert pdf.read_bytes() == PDF_BYTES
    on_disk = json.loads(layout.fa_record_file(tmp_path, "bet", "2025/26:JuU47").read_text())
    assert on_disk == record
    # exactly one binary fetch happened (the PDF), no HTML/XML body fetch
    assert sum("/fil/" in u for u in net.fetched) == 1
    assert not any(u.endswith(".html") or u.endswith(".xml") for u in net.fetched)


def test_download_metadata_only_for_null_filbilaga(monkeypatch, tmp_path):
    net = FakeNet()
    _patch(monkeypatch, net)
    record = riksdagen.download_document(None, tmp_path, _entry(PAGE1, "FiU8"), delay=0)
    assert record["files"] == []                       # metadata-only record
    assert net.fetched == []                           # no PDF, no body fetched
    assert layout.fa_record_file(tmp_path, "bet", "2026/27:FiU8").exists()
    assert not (layout.fa_dir(tmp_path, "bet", "2026/27:FiU8") / "2026-27-FiU8.pdf").exists()


def test_basefile_slug_round_trips_record_path():
    # fa_list keys a record by its type + on-disk stem; that stem must be the
    # slug of the record's own basefile, and layout.fa_record must find it back.
    slug = basefile_slug("2025/26:JuU47")
    assert slug == "2025-26-JuU47"
    path = layout.fa_record_file(layout.FA_DOWNLOADED, "bet", "2025/26:JuU47")
    assert path.name == slug + ".json"
    # fa_list would yield "bet/2025-26-JuU47"; layout.fa_record resolves it back
    assert layout.fa_record("bet/" + slug) == path


# --------------------------------------------------------------------------
# the riksmöte sequence (empirically verified against the API; see the comment
# block over riksdagen.riksmoten for the probe evidence)
# --------------------------------------------------------------------------

def test_riksmoten_sequence():
    seq = list(riksdagen.riksmoten(2026))
    assert seq[0] == "2026/27"                    # newest first
    assert seq[50] == "1976/77"
    assert seq[51] == "1975/76"                   # first split-year riksmöte
    assert seq[52] == "1975"                      # last single-year session
    assert seq[-1] == "1971"                       # first unicameral riksmöte
    assert len(seq) == 52 + 5                      # 2026/27..1975/76 + 1975..1971


# --------------------------------------------------------------------------
# sync: multi-riksmöte backfill, incremental, and the watermark rules
# --------------------------------------------------------------------------

RM_PAGE2_URL = "https://data.riksdagen.se/dokumentlista/rm-2025-26-page2"


def _watermark(tmp_path):
    """The saved last_harvest date, or None when no watermark exists."""
    path = tmp_path / "bet" / riksdagen.WATERMARK
    return json.loads(path.read_text())["last_harvest"] if path.exists() else None


def _dirty(tmp_path):
    """The persisted dirty flag of the watermark store."""
    path = tmp_path / "bet" / riksdagen.WATERMARK
    return json.loads(path.read_text()).get("dirty", False)


def _set_watermark(tmp_path, date_str):
    """Simulate an earlier clean harvest on `date_str` -- gives the gate's
    safety margin (14 days) room over the fixtures' fixed datums."""
    write_atomic(tmp_path / "bet" / riksdagen.WATERMARK,
                 json.dumps({"last_harvest": date_str}))


def _page(docs, sida="1", nasta=None):
    dl = {"@sida": sida, "@traffar": str(len(docs)), "dokument": docs}
    if nasta:
        dl["@nasta_sida"] = nasta
    return {"dokumentlista": dl}


def _backfill_net(monkeypatch):
    """A stubbed two-riksmöte corpus: rm=2026/27 holds the null-filbilaga FiU8,
    rm=2025/26 holds KU45 + JuU47 paged over two listing pages. The un-narrowed
    LISTING serves fixture page 1, from which newest_riksmote_year reads the
    newest rm (2026). riksmoten is narrowed to the two stubbed values -- the
    real 161-value sequence has its own test above."""
    net = FakeNet()
    net.pages[riksdagen.LISTING + "&rm=2026/27"] = _page([_entry(PAGE1, "FiU8")])
    net.pages[riksdagen.LISTING + "&rm=2025/26"] = _page(
        [_entry(PAGE1, "KU45")], nasta=RM_PAGE2_URL)
    net.pages[RM_PAGE2_URL] = _page([_entry(PAGE1, "JuU47")], sida="2")
    monkeypatch.setattr(riksdagen, "riksmoten",
                        lambda newest: iter(["%d/27" % newest, "2025/26"]))
    _patch(monkeypatch, net)
    return net


def test_sync_backfill_walks_riksmoten_and_saves_watermark(monkeypatch, tmp_path):
    net = _backfill_net(monkeypatch)
    seen, new = riksdagen.sync(tmp_path, delay=0)
    assert seen == 3 and new == 3          # FiU8 (metadata-only) + KU45 + JuU47
    # the saved date is the newest *published* entry's datum (KU45, 2026-06-16);
    # the planned FiU8's later datum (2026-06-30) must not win -- a planned
    # datum lies in the future and would erode the gate's safety margin
    assert _watermark(tmp_path) == "2026-06-16"
    assert _dirty(tmp_path) is False               # a clean run completes clean
    assert layout.fa_record_file(tmp_path, "bet", "2026/27:FiU8").exists()
    assert layout.fa_record_file(tmp_path, "bet", "2025/26:JuU47").exists()
    # the newest-riksmöte probe hits the un-narrowed listing once; every listing
    # fetch after it is an rm-narrowed walk (or a followed @nasta_sida)
    listing_urls = [u for u in net.fetched if "dokumentlista" in u]
    assert listing_urls == [riksdagen.LISTING,
                            riksdagen.LISTING + "&rm=2026/27",
                            riksdagen.LISTING + "&rm=2025/26",
                            RM_PAGE2_URL]


def test_sync_incremental_run_is_one_unnarrowed_walk(monkeypatch, tmp_path):
    _backfill_net(monkeypatch)
    riksdagen.sync(tmp_path, delay=0)                     # backfill, saves watermark
    _set_watermark(tmp_path, "2026-07-15")   # a later clean harvest has happened
    net2 = FakeNet()
    _patch(monkeypatch, net2)
    seen2, new2 = riksdagen.sync(tmp_path, delay=0)
    assert new2 == 0
    # watermark present -> single un-narrowed newest-first walk. The provisional
    # FiU8 (datum 2026-06-30 < the 2026-07-01 limit, but not final) reads as a
    # gap, then the final KU45 (2026-06-16, past the limit) stops the walk
    # conclusively -- page 2 never requested, no rm narrowing
    assert [u for u in net2.fetched if "dokumentlista" in u] == [riksdagen.LISTING]


def test_sync_backfill_skips_known_docs_but_keeps_walking(monkeypatch, tmp_path):
    # an already-on-disk doc must not stop a backfill walk early: KU45 (page 1
    # of rm=2025/26) is on disk with its PDF, yet JuU47 on page 2 is still fetched
    _backfill_net(monkeypatch)
    known = riksdagen.descriptor(_entry(PAGE1, "KU45"))
    known["files"] = ["2025-26-KU45.pdf"]        # a completed download is current
    write_atomic(layout.fa_record_file(tmp_path, "bet", known["basefile"]),
                 json.dumps(known))
    seen, new = riksdagen.sync(tmp_path, delay=0)
    assert seen == 3 and new == 2                # KU45 skipped, walk continued
    assert layout.fa_record_file(tmp_path, "bet", "2025/26:JuU47").exists()
    assert _watermark(tmp_path) == "2026-06-16"


def test_riksmote_narrowed_run_never_advances_watermark(monkeypatch, tmp_path):
    net = FakeNet()
    # narrowing appends the API's &rm= parameter; point that url at the fixtures
    narrowed_url = riksdagen.LISTING + "&rm=2025/26"
    net.pages[narrowed_url] = PAGE1
    net.pages[riksdagen._https(PAGE1["dokumentlista"]["@nasta_sida"])] = PAGE2
    _patch(monkeypatch, net)
    seen, new = riksdagen.sync(tmp_path, delay=0, riksmote="2025/26")
    assert seen == 4 and new == 4                       # it did download
    assert _watermark(tmp_path) is None       # a partial view never advances it


def test_full_rewalk_with_errors_leaves_store_dirty_and_next_run_heals(monkeypatch, tmp_path):
    # a --full re-walk that hits errors leaves a gap on disk; complete() still
    # advances the date (the date-conclusive stop bounds future walk depth) but
    # leaves the store dirty, so the next plain run re-walks and heals the gap
    _backfill_net(monkeypatch)
    riksdagen.sync(tmp_path, delay=0)                     # clean backfill
    assert _watermark(tmp_path) == "2026-06-16"
    # the JuU47 copy goes missing and its refetch now yields non-PDF bytes
    juu47 = _entry(PAGE1, "JuU47")
    layout.fa_record_file(tmp_path, "bet", "2025/26:JuU47").unlink()
    net2 = _backfill_net(monkeypatch)
    net2.bad_fil.add(riksdagen.pdf_fil(juu47)["url"])
    seen, new = riksdagen.sync(tmp_path, delay=0, full=True)
    assert new == 0                                       # the refetch failed
    assert _watermark(tmp_path) == "2026-06-16"           # newest published datum
    assert _dirty(tmp_path) is True                       # ... but not a clean run
    # the next plain (incremental) run reaches the gap -- the gate never stops
    # on a missing doc -- and, the transient failure gone, refetches it
    net3 = FakeNet()
    net3.pages[riksdagen.LISTING] = _page(
        [_entry(PAGE1, "FiU8"), _entry(PAGE1, "KU45"), juu47])
    _patch(monkeypatch, net3)
    seen3, new3 = riksdagen.sync(tmp_path, delay=0)
    assert new3 == 1                                      # exactly the stranded doc
    assert layout.fa_record_file(tmp_path, "bet", "2025/26:JuU47").exists()
    assert _dirty(tmp_path) is False                      # healed run completes clean


# --------------------------------------------------------------------------
# per-document failure handling: non-PDF bytes and the watermark invariant
# --------------------------------------------------------------------------

def test_download_rejects_non_pdf_bytes(monkeypatch, tmp_path):
    # an HTML error page served with 200 must not be stored as the PDF, and no
    # record may be written (else stop-at-known would never refetch the doc)
    net = FakeNet()
    entry = _entry(PAGE1, "JuU47")
    net.bad_fil.add(riksdagen.pdf_fil(entry)["url"])
    _patch(monkeypatch, net)
    with pytest.raises(ValueError, match="not a PDF"):
        riksdagen.download_document(None, tmp_path, entry, delay=0)
    assert not (layout.fa_dir(tmp_path, "bet", "2025/26:JuU47") / "2025-26-JuU47.pdf").exists()
    assert not layout.fa_record_file(tmp_path, "bet", "2025/26:JuU47").exists()


def test_incremental_error_leaves_store_dirty_and_next_run_heals(monkeypatch, tmp_path):
    # 1) clean backfill of the two-riksmöte corpus (JuU45 not in it yet)
    _backfill_net(monkeypatch)
    riksdagen.sync(tmp_path, delay=0)
    assert _watermark(tmp_path) == "2026-06-16"
    # 2) incremental run: a NEW doc (JuU45) tops the un-narrowed listing ahead
    #    of the known KU45, but its PDF fetch yields non-PDF bytes. complete()
    #    still advances the date, but the errored run leaves the store dirty --
    #    the failed doc is simply missing, a gap the gate never stops on, and
    #    the dirty flag makes the next run walk down to it regardless of any
    #    run of consecutive hits above.
    juu45 = _entry(PAGE2, "JuU45")
    net2 = FakeNet()
    net2.pages[riksdagen.LISTING] = _page([juu45, _entry(PAGE1, "KU45")])
    net2.bad_fil.add(riksdagen.pdf_fil(juu45)["url"])
    _patch(monkeypatch, net2)
    seen, new = riksdagen.sync(tmp_path, delay=0)
    assert new == 0                                       # the failure was counted, not raised
    assert not layout.fa_record_file(tmp_path, "bet", "2025/26:JuU45").exists()
    assert _watermark(tmp_path) == "2026-06-16"           # newest published datum
    assert _dirty(tmp_path) is True                       # errors withhold the clean save
    # 3) the next incremental run (still one un-narrowed walk, no rm backfill)
    #    reaches the gap and heals it with the transient failure gone
    net3 = FakeNet()
    net3.pages[riksdagen.LISTING] = _page([juu45, _entry(PAGE1, "KU45")])
    _patch(monkeypatch, net3)
    seen3, new3 = riksdagen.sync(tmp_path, delay=0)
    assert new3 == 1                                      # exactly the missed doc
    assert layout.fa_record_file(tmp_path, "bet", "2025/26:JuU45").exists()
    assert (layout.fa_dir(tmp_path, "bet", "2025/26:JuU45") / "2025-26-JuU45.pdf").read_bytes() == PDF_BYTES
    assert [u for u in net3.fetched if "dokumentlista" in u] == [riksdagen.LISTING]
    assert _dirty(tmp_path) is False                      # the healing run was clean


def test_malformed_feed_entries_are_recorded_and_skipped(monkeypatch, tmp_path):
    # one entry missing rm, one with a broken filbilaga (fil without typ/url),
    # one missing dokument_url_html: each becomes a recorded ValueError, never
    # a KeyError escaping the per-document catch -- one malformed entry must
    # not kill an hours-long backfill. The good document after them still
    # downloads, and the errors leave the store dirty.
    _set_watermark(tmp_path, "2026-07-15")
    no_rm = {k: v for k, v in _entry(PAGE2, "JuU45").items() if k != "rm"}
    bad_fil = dict(_entry(PAGE1, "JuU47"))
    bad_fil["filbilaga"] = {"fil": [{"namn": "x.pdf"}]}   # no typ, no url
    no_url = {k: v for k, v in _entry(PAGE2, "JuU45").items()
              if k != "dokument_url_html"}
    net = FakeNet()
    net.pages[riksdagen.LISTING] = _page(
        [no_rm, bad_fil, no_url, _entry(PAGE1, "KU45")])
    _patch(monkeypatch, net)
    messages = []
    seen, new = riksdagen.sync(tmp_path, delay=0, log=messages.append)
    assert (seen, new) == (4, 1)                   # only the good doc downloaded
    assert layout.fa_record_file(tmp_path, "bet", "2025/26:KU45").exists()
    assert sum("malformed" in m for m in messages) == 3
    assert _dirty(tmp_path) is True                # recorded errors keep it dirty


# --------------------------------------------------------------------------
# the pre-print upgrade cycle: a metadata-only record is provisional
# --------------------------------------------------------------------------

def _with_filbilaga(entry, url):
    """A copy of a filbilaga-less entry as the feed shows it once riksdagen has
    attached the printed PDF (status planerat -> Webbpublicering)."""
    upgraded = dict(entry)
    upgraded["filbilaga"] = {"fil": [{"typ": "pdf", "namn": "pub.pdf",
                                      "storlek": "1", "url": url}]}
    return upgraded


def test_incremental_upgrades_metadata_only_record_once_pdf_appears(monkeypatch,
                                                                    tmp_path):
    # a betänkande first harvested while "planerat" (filbilaga null) has a
    # metadata-only record; when the feed later shows its filbilaga, an
    # incremental run must re-download and upgrade it in place -- it must NOT
    # count as known forever (that would freeze it body-less permanently)
    _backfill_net(monkeypatch)
    riksdagen.sync(tmp_path, delay=0)                     # FiU8 stored metadata-only
    fiu8_record = layout.fa_record_file(tmp_path, "bet", "2026/27:FiU8")
    assert json.loads(fiu8_record.read_text())["files"] == []
    # the feed now shows FiU8 published, KU45 (current, has its PDF) below it
    fiu8_pub = _with_filbilaga(_entry(PAGE1, "FiU8"),
                               "https://data.riksdagen.se/fil/UPGRADE-FIU8")
    net2 = FakeNet()
    net2.pages[riksdagen.LISTING] = _page([fiu8_pub, _entry(PAGE1, "KU45")])
    _patch(monkeypatch, net2)
    seen, new = riksdagen.sync(tmp_path, delay=0)         # incremental
    assert new == 1                                       # the upgrade
    assert json.loads(fiu8_record.read_text())["files"] == ["2026-27-FiU8.pdf"]
    assert (layout.fa_dir(tmp_path, "bet", "2026/27:FiU8") / "2026-27-FiU8.pdf").read_bytes() == PDF_BYTES
    # the clean run saves the watermark; FiU8 now counts as published, so its
    # datum (the newest) is the new last_harvest
    assert _watermark(tmp_path) == "2026-06-30"


def test_incremental_stops_only_at_final_records(monkeypatch, tmp_path):
    # a current provisional (still filbilaga-less) record never feeds the
    # gate as "downloaded": a planned betänkande's datum can post-date
    # documents published after the last harvest, so the datum sort puts those
    # new docs *behind* the placeholder -- stopping at it would skip them
    # silently. Here the NEW JuU45 sorts below the FiU8 placeholder: the walk
    # must skip FiU8 (still current, no re-download, gate reads it as a gap),
    # fetch JuU45, and stop conclusively at the final KU45 (its datum is past
    # the safety margin) -- without following @nasta_sida past it (that url is
    # not served; this is also what keeps a wholly provisional old-corpus
    # region like rm=1990/91 from ever being re-walked: the walk stops at the
    # first final doc above it).
    _backfill_net(monkeypatch)
    riksdagen.sync(tmp_path, delay=0)                     # FiU8 stored metadata-only
    _set_watermark(tmp_path, "2026-07-15")   # a later clean harvest has happened
    net2 = FakeNet()
    net2.pages[riksdagen.LISTING] = _page(
        [_entry(PAGE1, "FiU8"), _entry(PAGE2, "JuU45"), _entry(PAGE1, "KU45")],
        nasta="https://data.riksdagen.se/never-served")
    _patch(monkeypatch, net2)
    seen, new = riksdagen.sync(tmp_path, delay=0)
    assert (seen, new) == (3, 1)          # FiU8 skipped, JuU45 fetched, stop at KU45
    assert layout.fa_record_file(tmp_path, "bet", "2025/26:JuU45").exists()
    assert [u for u in net2.fetched if "dokumentlista" in u] == [riksdagen.LISTING]


# --------------------------------------------------------------------------
# parse stage: what a bet record produces
# --------------------------------------------------------------------------

def test_metadata_only_record_parses_to_empty_body_artifact(tmp_path):
    # a filbilaga-less bet record (files: []) is a real catalog document: the
    # parse stage yields a valid artifact at the grammar-form URI with no body
    record = riksdagen.descriptor(_entry(PAGE1, "FiU8"))
    fa = fa_parse.parse_record(record, tmp_path)          # no files -> no disk reads
    art = fa_parse.to_artifact(fa)
    assert art["uri"] == "https://lagen.nu/bet/2026/27:FiU8"
    assert art["identifier"] == "Bet. 2026/27:FiU8"
    assert art["type"] == "bet"
    assert art["structure"] == []                         # empty body, still valid
