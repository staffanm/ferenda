"""Tests for the förarbete downloader's parsing (network-free)."""

import json
from types import SimpleNamespace

import pytest
import requests

from accommodanda.forarbete import download
from accommodanda.forarbete.download import (
    basefile_slug,
    find_content_links,
    has_live_record,
    iter_listing,
    parse_listing,
)
from accommodanda.lib import layout
from accommodanda.lib.util import write_atomic

# the real regeringen.se listing-item shape: ul.list--block > li >
# div.sortcompact > a (link text = "Title, <Identifier>") + a <time>
LISTING = """
<ul class="list--block">
  <li><div class="sortcompact">
    <a href="/rattsliga-dokument/proposition/2026/06/prop.-202526279">
      Personalförsörjning av det militära försvaret, Prop. 2025/26:279</a>
    <div class="block--timeLinks"><p>Publicerad
      <time datetime="2026-06-09">09 juni 2026</time> ·
      <a href="/tx/1329">Proposition</a></p></div>
  </div></li>
  <li><div class="sortcompact">
    <a href="/rattsliga-dokument/proposition/2026/05/prop.-202526276">
      Ny ordning för asylsystemet, Prop. 2025/26:276</a>
    <time datetime="2026-05-20">20 maj 2026</time>
  </div></li>
</ul>
"""

LISTING_SLUG = """
<ul class="list--block">
  <li><div class="sortcompact">
    <a href="/rattsliga-dokument/lagradsremiss/2026/06/andrade-regler-om-avdrag">
      Ändrade regler om avdrag</a>
    <time datetime="2026-06-11">11 juni 2026</time>
  </div></li>
</ul>
"""

# a real category-1325 listing page (trimmed to three items). Category 1325 is
# "Departementsserien och promemorior": it mixes Ds-numbered items (-> type ds)
# with promemorior that carry only a diarienummer or only a title (-> type pm).
LISTING_1325 = """
<ul class="list--block">
  <li><div class="sortcompact">
    <a href="/rattsliga-dokument/departementsserien-och-promemorior/2026/07/skarpt-straffansvar-for-allvarliga-krankningar-av-gravfriden/">
      Skärpt straffansvar för allvarliga kränkningar av gravfriden, Ju2026/01691</a>
    <div class="block--timeLinks"><p>Publicerad
      <time datetime="2026-07-03">03 juli 2026</time> ·
      <a href="/tx/1325">Departementsserien och promemorior</a></p></div>
  </div></li>
  <li><div class="sortcompact">
    <a href="/rattsliga-dokument/departementsserien-och-promemorior/2026/07/ds-202615/">
      Gäldenärens avtal i konkurs, Ds 2026:15</a>
    <div class="block--timeLinks"><p>Publicerad
      <time datetime="2026-07-02">02 juli 2026</time> ·
      <a href="/tx/1325">Departementsserien och promemorior</a></p></div>
  </div></li>
  <li><div class="sortcompact">
    <a href="/rattsliga-dokument/departementsserien-och-promemorior/2026/07/andring-av-detaljplaner/">
      Ändring av detaljplaner</a>
    <div class="block--timeLinks"><p>Publicerad
      <time datetime="2026-07-02">02 juli 2026</time> ·
      <a href="/tx/1325">Departementsserien och promemorior</a></p></div>
  </div></li>
</ul>
"""

DOCPAGE = """
<div class="content">
  <ul class="list--Block--icons">
    <a href="/contentassets/abc/personalforsorjning-prop.-202526279.pdf">Hela dokumentet</a>
    <a href="/contentassets/abc/bilaga-1.pdf">Bilaga 1</a>
    <a href="/contentassets/abc/personalforsorjning-prop.-202526279.pdf">dup</a>
    <a href="/some/other/page/">Not a file</a>
  </ul>
</div>
"""


def test_parse_listing_numbered_type():
    items, raw = parse_listing(LISTING, "prop")
    assert raw == 2
    assert len(items) == 2
    a = items[0]
    assert a["basefile"] == "2025/26:279"           # the document's own id
    assert a["identifier"] == "Prop. 2025/26:279"
    assert a["title"] == "Personalförsörjning av det militära försvaret"
    assert a["date"] == "2026-06-09"
    assert a["url"].endswith("/proposition/2026/06/prop.-202526279/")
    assert items[1]["basefile"] == "2025/26:276"


def test_parse_listing_lagradsremiss_keys_on_year_and_title():
    items, raw = parse_listing(LISTING_SLUG, "lr")
    assert raw == 1
    assert len(items) == 1
    # a lagrådsremiss has no number, so its basefile is <year>/<title-slug>
    # (never the unreliable URL slug) -- settled from the listing text + date
    assert items[0]["basefile"] == "2026/andrade-regler-om-avdrag"
    assert items[0]["identifier"] == "Ändrade regler om avdrag"
    assert items[0]["title"] == "Ändrade regler om avdrag"


# a SÖ listing item: the own number is end-anchored, after a *cited* other SÖ
LISTING_SO = """
<ul class="list--block">
  <li><div class="sortcompact">
    <a href="/rattsliga-dokument/sveriges-internationella-overenskommelser/1979/06/so-198072/">
      Ändring i konventionen (SÖ 1974:41), Bonn den 22 juni 1979, SÖ 1980:72</a>
    <time datetime="1979-06-22">22 juni 1979</time>
  </div></li>
</ul>
"""


def test_parse_listing_so_takes_the_end_anchored_own_number():
    items, _ = parse_listing(LISTING_SO, "so")
    # best-effort key from the listing is the OWN (trailing) SÖ number, not the
    # cited SÖ 1974:41 earlier in the title
    assert items[0]["basefile"] == "1980:72"
    assert items[0]["identifier"] == "SÖ 1980:72"
    assert "SÖ 1980:72" not in items[0]["title"]


def test_parse_listing_skips_a_misleading_url():
    # the curated dual-published copy of SÖ 1980:72 is dropped entirely
    dup = LISTING_SO.replace("1979/06/so-198072/", "1994/01/so-198072-/")
    items, raw = parse_listing(dup, "so")
    assert raw == 1 and items == []


def test_resolve_identity_so_authoritative_from_vignette():
    item = {"basefile": None, "identifier": None, "title": "x"}
    landing = '<span class="h1-vignette">SÖ 1980:72</span><h1>x</h1>'
    assert download.resolve_identity("so", item, landing) == ("1980:72", "SÖ 1980:72")


def test_resolve_identity_so_rejects_non_so_landing():
    # an item under the SÖ index whose vignette is not a real SÖ number
    item = {"basefile": None, "identifier": None, "title": "x"}
    landing = '<span class="h1-vignette">Pressmeddelande</span><h1>x</h1>'
    assert download.resolve_identity("so", item, landing) is None


def test_parse_listing_unhandled_type_raises(monkeypatch):
    # the final else is a hard error, never a silent slug fallback
    monkeypatch.setitem(download.TYPES, "zz", ("zztype", 9999, None))
    html = LISTING_SLUG.replace("lagradsremiss/2026/06/andrade-regler-om-avdrag",
                                "zztype/2026/06/whatever")
    with pytest.raises(ValueError, match="no identifier rule"):
        parse_listing(html, "zz")


def test_parse_listing_skips_items_without_the_types_identifier():
    # a stray link whose text lacks "Prop. N" must not be taken as a document
    html = LISTING.replace(", Prop. 2025/26:279", "")
    items, raw = parse_listing(html, "prop")
    assert len(items) == 1        # only the second item survives the filter...
    assert raw == 2               # ...but the page was NOT raw-empty


def test_parse_listing_ds_takes_only_ds_numbered_items():
    # category 1325 mixes ds and pm; asked for "ds" only the Ds-numbered item
    # is a document -- the dnr and title-only promemorior are skipped.
    items, raw = parse_listing(LISTING_1325, "ds")
    assert raw == 3
    assert len(items) == 1
    assert items[0]["basefile"] == "2026:15"
    assert items[0]["identifier"] == "Ds 2026:15"
    assert items[0]["title"] == "Gäldenärens avtal i konkurs"


def test_parse_listing_pm_takes_the_non_ds_promemorior():
    # asked for "pm" the same page yields the dnr item and the title-only item,
    # and skips the Ds-numbered one (it belongs to ds).
    items, raw = parse_listing(LISTING_1325, "pm")
    assert raw == 3
    assert len(items) == 2
    dnr, title_only = items
    # dnr-keyed: basefile == identifier == the diarienummer, title stripped of it
    assert dnr["basefile"] == "Ju2026/01691"
    assert dnr["identifier"] == "Ju2026/01691"
    assert dnr["title"] == "Skärpt straffansvar för allvarliga kränkningar av gravfriden"
    assert dnr["date"] == "2026-07-03"
    # title-only: slug basefile, identifier is the title
    assert title_only["basefile"] == "andring-av-detaljplaner"
    assert title_only["identifier"] == "Ändring av detaljplaner"
    assert title_only["title"] == "Ändring av detaljplaner"


def test_find_content_links_dedupes_and_filters():
    links = find_content_links(DOCPAGE)
    assert links == ["/contentassets/abc/personalforsorjning-prop.-202526279.pdf",
                     "/contentassets/abc/bilaga-1.pdf"]


def test_basefile_slug():
    assert basefile_slug("2025/26:279") == "2025-26-279"
    assert basefile_slug("2020:1") == "2020-1"


def test_has_live_record_treats_import_as_absent(tmp_path):
    # a genuine live-harvest record (no `source`) blocks re-download / stops the walk
    write_atomic(layout.fa_record_file(tmp_path, "prop", "2020/21:1"),
                 json.dumps({"type": "prop", "files": []}))
    assert has_live_record(tmp_path, "prop", "2020/21:1") is True
    # a frozen import record (carries `source`, §7g) is treated as absent, so the
    # live downloader fetches its better copy AND it never trips the incremental stop
    write_atomic(layout.fa_record_file(tmp_path, "prop", "1997/98:45"),
                 json.dumps({"type": "prop", "source": "proptrips", "legacy_files": []}))
    assert has_live_record(tmp_path, "prop", "1997/98:45") is False
    assert has_live_record(tmp_path, "prop", "1867:23") is False   # truly absent


def test_sync_incremental_skips_downloaded(tmp_path, monkeypatch):
    # 1. Setup mock functions
    items = [
        {"type": "prop", "basefile": "2025/26:279", "identifier": "Prop. 2025/26:279", "date": "2026-06-09", "url": "http://example.com/1"},
        {"type": "prop", "basefile": "2025/26:276", "identifier": "Prop. 2025/26:276", "date": "2026-05-20", "url": "http://example.com/2"},
    ]

    # Mock iter_listing
    monkeypatch.setattr(download, "iter_listing", lambda session, typ, delay: [(items, 2, 1)])

    # Mock download_document
    downloads = []
    def mock_download_document(session, root, item, delay):
        downloads.append(item["basefile"])
        # Create the live record so has_live_record is True next time
        write_atomic(layout.fa_record_file(root, "prop", item["basefile"]),
                     json.dumps({"type": "prop", "files": []}))
        return {"basefile": item["basefile"]}
    monkeypatch.setattr(download, "download_document", mock_download_document)

    # 2. First run (backfill / no watermark)
    totals = download.sync(tmp_path, types=["prop"], delay=0)
    assert totals == {"prop": (2, 2)}
    assert downloads == ["2025/26:279", "2025/26:276"]

    # Verify watermark file was written
    watermark_path = tmp_path / "prop" / ".watermark.json"
    assert watermark_path.exists()

    # 3. Second run (incremental)
    downloads.clear()
    totals2 = download.sync(tmp_path, types=["prop"], delay=0)
    # Both seen, but 0 new downloads since both already downloaded
    assert totals2 == {"prop": (2, 0)}
    assert downloads == []


def test_sync_error_advances_date_but_leaves_store_dirty_and_retries(tmp_path, monkeypatch):
    # begin/complete lifecycle: a failed download still advances the watermark
    # date (bounded walk depth) but leaves the store dirty; the next run then
    # reaches down past the consecutive-hit stop and retries the failure.
    items = [
        {"type": "prop", "basefile": "2025/26:279", "identifier": "Prop. 2025/26:279", "date": "2026-06-09", "url": "http://example.com/1"},
    ]

    monkeypatch.setattr(download, "iter_listing", lambda session, typ, delay: [(items, 1, 1)])

    def mock_download_document_error(session, root, item, delay):
        raise requests.HTTPError("500 Server Error")

    monkeypatch.setattr(download, "download_document", mock_download_document_error)

    totals = download.sync(tmp_path, types=["prop"], delay=0, log=lambda msg: None)
    assert totals == {"prop": (1, 0)}

    watermark_path = tmp_path / "prop" / ".watermark.json"
    state = json.loads(watermark_path.read_text())
    assert state["last_harvest"] == "2026-06-09"     # the date still advances
    assert state["dirty"] is True                    # ... but the run was not clean

    # the next run (transient failure gone) retries the stranded doc and heals
    downloads = []
    def mock_download_document(session, root, item, delay):
        downloads.append(item["basefile"])
        write_atomic(layout.fa_record_file(root, "prop", item["basefile"]),
                     json.dumps({"type": "prop", "files": []}))
        return {"basefile": item["basefile"]}
    monkeypatch.setattr(download, "download_document", mock_download_document)
    totals2 = download.sync(tmp_path, types=["prop"], delay=0, log=lambda msg: None)
    assert totals2 == {"prop": (1, 1)}
    assert downloads == ["2025/26:279"]
    state2 = json.loads(watermark_path.read_text())
    assert state2["dirty"] is False                  # a clean run clears the flag


def test_sync_limit_truncation_leaves_store_dirty(tmp_path, monkeypatch):
    items = [
        {"type": "prop", "basefile": "2025/26:279", "identifier": "Prop. 2025/26:279", "date": "2026-06-09", "url": "http://example.com/1"},
        {"type": "prop", "basefile": "2025/26:276", "identifier": "Prop. 2025/26:276", "date": "2026-05-20", "url": "http://example.com/2"},
    ]
    monkeypatch.setattr(download, "iter_listing", lambda session, typ, delay: [(items, 2, 1)])

    def mock_download_document(session, root, item, delay):
        write_atomic(layout.fa_record_file(root, "prop", item["basefile"]),
                     json.dumps({"type": "prop", "files": []}))
        return {"basefile": item["basefile"]}
    monkeypatch.setattr(download, "download_document", mock_download_document)

    totals = download.sync(tmp_path, types=["prop"], delay=0, limit=1)
    assert totals["prop"][1] == 1                    # truncated at the cap
    state = json.loads((tmp_path / "prop" / ".watermark.json").read_text())
    assert state["dirty"] is True                    # backlog below the cap remains


# --------------------------------------------------------------------------
# walk termination keys on the RAW item count, not the type-filtered one:
# category 1325 mixes ds and pm, so a page consisting entirely of the sibling
# type's documents must NOT read as "listing exhausted" (that would permanently
# skip everything deeper, --full included).
# --------------------------------------------------------------------------

# one page of only Ds-numbered items, one page of only non-Ds promemorior,
# built from the real 1325 fixture markup above
DS_ONLY_PAGE = LISTING_1325.replace(
    """  <li><div class="sortcompact">
    <a href="/rattsliga-dokument/departementsserien-och-promemorior/2026/07/skarpt-straffansvar-for-allvarliga-krankningar-av-gravfriden/">
      Skärpt straffansvar för allvarliga kränkningar av gravfriden, Ju2026/01691</a>
    <div class="block--timeLinks"><p>Publicerad
      <time datetime="2026-07-03">03 juli 2026</time> ·
      <a href="/tx/1325">Departementsserien och promemorior</a></p></div>
  </div></li>
""", "").replace(
    """  <li><div class="sortcompact">
    <a href="/rattsliga-dokument/departementsserien-och-promemorior/2026/07/andring-av-detaljplaner/">
      Ändring av detaljplaner</a>
    <div class="block--timeLinks"><p>Publicerad
      <time datetime="2026-07-02">02 juli 2026</time> ·
      <a href="/tx/1325">Departementsserien och promemorior</a></p></div>
  </div></li>
""", "")
PM_ONLY_PAGE = LISTING_1325.replace(
    """  <li><div class="sortcompact">
    <a href="/rattsliga-dokument/departementsserien-och-promemorior/2026/07/ds-202615/">
      Gäldenärens avtal i konkurs, Ds 2026:15</a>
    <div class="block--timeLinks"><p>Publicerad
      <time datetime="2026-07-02">02 juli 2026</time> ·
      <a href="/tx/1325">Departementsserien och promemorior</a></p></div>
  </div></li>
""", "")
EMPTY_PAGE = '<ul class="list--block"></ul>'


def _fake_fetch(pages, total):
    """A download.fetch stub serving `pages[N]` (1-based listing pages) wrapped
    in the AJAX JSON envelope; pages past the dict are raw-empty."""
    def fetch(session, url, timeout=60):
        page = int(url.rsplit("page=", 1)[1])
        html = pages.get(page, EMPTY_PAGE)
        return SimpleNamespace(json=lambda: {"Message": html, "TotalCount": total})
    return fetch


def test_iter_listing_sibling_only_page_does_not_terminate_pm_walk(monkeypatch):
    # page 1 holds only Ds items; the pm walk must keep going to page 2
    monkeypatch.setattr(download, "fetch",
                        _fake_fetch({1: DS_ONLY_PAGE, 2: PM_ONLY_PAGE}, 3))
    pages = list(iter_listing(None, "pm", delay=0))
    assert [p for _, _, p in pages] == [1, 2]
    assert [i["basefile"] for items, _, _ in pages for i in items] == [
        "Ju2026/01691", "andring-av-detaljplaner"]


def test_iter_listing_sibling_only_page_does_not_terminate_ds_walk(monkeypatch):
    # the mirror image: page 1 holds only non-Ds promemorior; the ds walk must
    # keep going to page 2 where the Ds document sits
    monkeypatch.setattr(download, "fetch",
                        _fake_fetch({1: PM_ONLY_PAGE, 2: DS_ONLY_PAGE}, 3))
    pages = list(iter_listing(None, "ds", delay=0))
    assert [p for _, _, p in pages] == [1, 2]
    assert [i["basefile"] for items, _, _ in pages for i in items] == ["2026:15"]


def test_iter_listing_genuinely_exhausted_listing_terminates(monkeypatch):
    # a raw-empty page with all TotalCount items already seen is the clean end
    monkeypatch.setattr(download, "fetch", _fake_fetch({1: LISTING_1325}, 3))
    pages = list(iter_listing(None, "ds", delay=0))
    assert [p for _, _, p in pages] == [1]


def test_iter_listing_raw_empty_page_below_totalcount_is_an_error(monkeypatch):
    # a raw-empty page while TotalCount says more should exist is a truncated
    # or broken listing -- an error, never clean exhaustion
    monkeypatch.setattr(download, "fetch", _fake_fetch({1: LISTING_1325}, 40))
    with pytest.raises(ValueError, match="TotalCount"):
        list(iter_listing(None, "ds", delay=0))


def test_sync_downloads_pm_doc_below_a_ds_only_page(tmp_path, monkeypatch):
    # end-to-end: a full pm sync whose first listing page is all-Ds still
    # reaches and downloads the promemoria on page 2
    monkeypatch.setattr(download, "fetch",
                        _fake_fetch({1: DS_ONLY_PAGE, 2: PM_ONLY_PAGE}, 3))
    downloads = []
    def mock_download_document(session, root, item, delay):
        downloads.append(item["basefile"])
        write_atomic(layout.fa_record_file(root, "pm", item["basefile"]),
                     json.dumps({"type": "pm", "files": []}))
        return {"basefile": item["basefile"]}
    monkeypatch.setattr(download, "download_document", mock_download_document)
    totals = download.sync(tmp_path, types=["pm"], delay=0)
    assert totals == {"pm": (2, 2)}
    assert downloads == ["Ju2026/01691", "andring-av-detaljplaner"]
    state = json.loads((tmp_path / "pm" / ".watermark.json").read_text())
    assert state["dirty"] is False and state["last_harvest"] == "2026-07-03"
