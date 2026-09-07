"""Tests for the förarbete downloader's parsing (network-free)."""

import json
from types import SimpleNamespace

import pytest
import requests

from ferenda.forarbete import download
from ferenda.forarbete.download import (
    basefile_slug,
    find_content_links,
    has_live_record,
    iter_listing,
    parse_listing,
)
from ferenda.lib import compress, layout, regeringen
from ferenda.lib.util import write_atomic

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


def test_parse_listing_skips_a_landing_the_site_cannot_serve():
    """A page regeringen.se lists but answers 500 for is dropped before any
    fetch: otherwise net.request climbs its full retry ladder (~62 s) and the
    per-document error leaves the watermark store dirty for every later run."""
    broken = "/rattsliga-dokument/skrivelse/2016/03/skr.-201516115"
    assert broken in regeringen.BROKEN_LANDINGS
    html = """
    <ul class="list--block">
      <li><div class="sortcompact">
        <a href="%s/">Verksamheten i Europeiska unionen under 2015,
          Skr. 2015/16:115</a>
        <time datetime="2016-03-10">10 mars 2016</time>
      </div></li>
      <li><div class="sortcompact">
        <a href="/rattsliga-dokument/skrivelse/2016/03/skr.-201516116/">
          En annan skrivelse, Skr. 2015/16:116</a>
        <time datetime="2016-03-11">11 mars 2016</time>
      </div></li>
    </ul>""" % broken
    items, raw = parse_listing(html, "skr")
    assert raw == 2                        # still counted: the listing served it
    assert [i["basefile"] for i in items] == ["2015/16:116"]


def test_resolve_identity_so_authoritative_from_vignette():
    item = {"basefile": None, "identifier": None, "title": "x"}
    landing = '<span class="h1-vignette">SÖ 1980:72</span><h1>x</h1>'
    assert download.resolve_identity("so", item, landing) == ("1980:72", "SÖ 1980:72")


def test_resolve_identity_so_rejects_non_so_landing():
    # an item under the SÖ index whose vignette is not a real SÖ number
    item = {"basefile": None, "identifier": None, "title": "x"}
    landing = '<span class="h1-vignette">Pressmeddelande</span><h1>x</h1>'
    assert download.resolve_identity("so", item, landing) is None


def test_resolve_identity_reads_a_numbered_type_off_its_landing_page():
    """prop. 2025/26:223's landing page has no vignette; the link to its PDF is
    the only place the number is printed."""
    item = {"basefile": None, "identifier": None,
            "title": "En ny konsumentkreditlag"}
    landing = ('<h1>En ny konsumentkreditlag</h1>'
               '<a href="/contentassets/abc/en-ny-konsumentkreditlag">'
               'En ny konsumentkreditlag, Prop. 2025/2026:223 (pdf 3 MB)</a>')
    assert download.resolve_identity("prop", item, landing) \
        == ("2025/26:223", "Prop. 2025/26:223")


def test_resolve_identity_prefers_the_vignette_over_a_mistyped_file_link():
    """prop. 2025/26:50's file link says "Prop. 2025/26:0"; its vignette is
    right."""
    item = {"basefile": None, "identifier": None, "title": "x"}
    landing = ('<span class="h1-vignette">prop. 2025/26:50</span>'
               '<a href="/contentassets/abc/x">x, Prop. 2025/26:0 (pdf)</a>')
    assert download.resolve_identity("prop", item, landing) \
        == ("2025/26:50", "Prop. 2025/26:50")


def test_resolve_identity_rejects_an_item_no_page_of_which_names_a_number():
    """An uppdrag filed under the kommittedirektiv index carries no Dir. number
    anywhere, and is not a document of this series."""
    item = {"basefile": None, "identifier": None, "title": "Uppdrag att ..."}
    landing = '<h1>Uppdrag att föreslå obligatorisk förskola</h1>'
    assert download.resolve_identity("dir", item, landing) is None


def test_parse_listing_unhandled_type_raises(monkeypatch):
    # the final else is a hard error, never a silent slug fallback
    monkeypatch.setitem(download.TYPES, "zz", ("zztype", 9999, None))
    html = LISTING_SLUG.replace("lagradsremiss/2026/06/andrade-regler-om-avdrag",
                                "zztype/2026/06/whatever")
    with pytest.raises(ValueError, match="no identifier rule"):
        parse_listing(html, "zz")


def test_a_listing_item_without_a_number_rides_on_for_the_landing_to_settle():
    """regeringen.se lists the odd proposition under its title alone -- prop.
    2025/26:223 is "En ny konsumentkreditlag" and nothing else. Skipping it left
    the document unheld while every neighbour was harvested, so it rides on with
    no basefile and `resolve_identity` reads the number off the landing page."""
    html = LISTING.replace(", Prop. 2025/26:279", "")
    items, raw = parse_listing(html, "prop")
    assert raw == 2
    assert [i["basefile"] for i in items] == [None, "2025/26:276"]
    assert items[0]["title"] == "Personalförsörjning av det militära försvaret"


def test_parse_listing_reads_the_number_however_regeringen_spelled_it():
    """The house style is "Prop. 2025/26:279", but the same listing carries
    "prop. …", "Prop 2025/26:169", "Prop.  2025/26:7" and the long riksmote
    "2025/2026:223". All of them name the same series."""
    for spelling, basefile in (("prop. 2025/26:279", "2025/26:279"),
                               ("Prop 2025/26:279", "2025/26:279"),
                               ("Prop.  2025/26:279", "2025/26:279"),
                               ("Prop.2025/26:279", "2025/26:279"),
                               ("Prop. 2025/2026:279", "2025/26:279")):
        html = LISTING.replace("Prop. 2025/26:279", spelling)
        items, _raw = parse_listing(html, "prop")
        assert items[0]["basefile"] == basefile, spelling


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
    # a genuine live-harvest record (no `source`, with a body) blocks
    # re-download / stops the walk
    write_atomic(layout.fa_record_file(tmp_path, "prop", "2020/21:1"),
                 json.dumps({"type": "prop", "files": []}))
    assert has_live_record(tmp_path, "prop", "2020/21:1") is True
    # a frozen import record (carries `source`, §7g) is treated as absent, so the
    # live downloader fetches its better copy AND it never trips the incremental stop
    write_atomic(layout.fa_record_file(tmp_path, "prop", "1997/98:45"),
                 json.dumps({"type": "prop", "source": "proptrips", "legacy_files": []}))
    assert has_live_record(tmp_path, "prop", "1997/98:45") is False
    assert has_live_record(tmp_path, "prop", "1867:23") is False   # truly absent


def test_needs_harvest_for_a_body_less_record(tmp_path):
    """14 038 of 97 213 records carry `files: []`, and their stored landing
    pages mostly do link a document -- they are missed downloads. Counting them
    as harvested is what put them out of reach: the incremental walk stopped
    above them and `--full` skipped them, so no run of the downloader could
    repair one.

    `has_live_record` still reports them present: it is shared with the
    riksdagen walk, where a body-less record is a modelled state (a planned
    betänkande) rather than a gap."""
    write_atomic(layout.fa_record_file(tmp_path, "prop", "2019/20:7"),
                 json.dumps({"type": "prop", "files": []}))
    assert has_live_record(tmp_path, "prop", "2019/20:7") is True
    assert download.needs_harvest(tmp_path, "prop", "2019/20:7") is True
    # a record with a body needs nothing; an absent one always does
    write_atomic(layout.fa_record_file(tmp_path, "prop", "2019/20:8"),
                 json.dumps({"type": "prop", "files": ["2019-20-8.pdf"]}))
    assert download.needs_harvest(tmp_path, "prop", "2019/20:8") is False
    assert download.needs_harvest(tmp_path, "prop", "1867:23") is True


def test_sync_incremental_skips_downloaded(tmp_path, monkeypatch):
    # 1. Setup mock functions
    items = [
        {"type": "prop", "basefile": "2025/26:279", "identifier": "Prop. 2025/26:279", "date": "2026-06-09", "url": "http://example.com/1"},
        {"type": "prop", "basefile": "2025/26:276", "identifier": "Prop. 2025/26:276", "date": "2026-05-20", "url": "http://example.com/2"},
    ]

    # Mock iter_listing
    monkeypatch.setattr(download, "iter_listing", lambda session, typ, delay, log=None: [(items, 2, 1)])

    # Mock download_document
    downloads = []
    def mock_download_document(session, root, item, delay, log=print):
        downloads.append(item["basefile"])
        # Create the live record so has_live_record is True next time
        write_atomic(layout.fa_record_file(root, "prop", item["basefile"]),
                     json.dumps({"type": "prop",
                                 "files": [basefile_slug(item["basefile"]) + ".pdf"]}))
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

    monkeypatch.setattr(download, "iter_listing", lambda session, typ, delay, log=None: [(items, 1, 1)])

    def mock_download_document_error(session, root, item, delay, log=print):
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
    def mock_download_document(session, root, item, delay, log=print):
        downloads.append(item["basefile"])
        write_atomic(layout.fa_record_file(root, "prop", item["basefile"]),
                     json.dumps({"type": "prop",
                                 "files": [basefile_slug(item["basefile"]) + ".pdf"]}))
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
    monkeypatch.setattr(download, "iter_listing", lambda session, typ, delay, log=None: [(items, 2, 1)])

    def mock_download_document(session, root, item, delay, log=print):
        write_atomic(layout.fa_record_file(root, "prop", item["basefile"]),
                     json.dumps({"type": "prop",
                                 "files": [basefile_slug(item["basefile"]) + ".pdf"]}))
        return {"basefile": item["basefile"]}
    monkeypatch.setattr(download, "download_document", mock_download_document)

    totals = download.sync(tmp_path, types=["prop"], delay=0, limit=1)
    assert totals["prop"][1] == 1                    # truncated at the cap
    state = json.loads((tmp_path / "prop" / ".watermark.json").read_text())
    assert state["dirty"] is True                    # backlog below the cap remains


def _one_prop(basefile="2025/26:279", date="2026-06-09"):
    return {"type": "prop", "basefile": basefile, "date": date,
            "identifier": "Prop. " + basefile, "url": "http://example.com/1"}


def test_sync_only_that_names_no_document_raises(tmp_path, monkeypatch):
    """An `--only` the listings do not carry is a typo or a document that has
    gone. Without the guard the run walks every listing to the bottom and
    reports a clean "0 new", which reads as "nothing to do"."""
    monkeypatch.setattr(download, "iter_listing",
                        lambda session, typ, delay, log=None: [([_one_prop()], 1, 1)])
    monkeypatch.setattr(download, "download_document",
                        lambda *a, **kw: pytest.fail("nothing should be fetched"))
    with pytest.raises(ValueError, match="--only prop/1999:1 names no document"):
        download.sync(tmp_path, types=["prop"], delay=0, only="prop/1999:1")


def test_sync_a_broken_listing_does_not_stop_the_next_doctype(tmp_path, monkeypatch):
    """`iter_listing` raises on a truncated listing (rule:fail-fast). That
    raise used to escape the per-doctype loop, so one broken listing skipped
    the eight doctypes below it; inside the walk it is a recorded Skip that
    leaves prop's store dirty and lets ds run."""
    def listings(session, typ, delay, log=None):
        if typ == "prop":
            raise ValueError("prop: listing page 1 is empty but TotalCount=4352")
        yield ([{"type": "ds", "basefile": "2026:5", "identifier": "Ds 2026:5",
                 "date": "2026-05-01", "url": "http://example.com/2"}], 1, 1)

    downloads = []

    def mock_download_document(session, root, item, delay, log=print):
        downloads.append(item["basefile"])
        write_atomic(layout.fa_record_file(root, item["type"], item["basefile"]),
                     json.dumps({"type": item["type"], "files": ["x.pdf"]}))
        return {"basefile": item["basefile"]}

    monkeypatch.setattr(download, "iter_listing", listings)
    monkeypatch.setattr(download, "download_document", mock_download_document)
    logged = []
    totals = download.sync(tmp_path, types=["prop", "ds"], delay=0,
                           log=logged.append)
    assert totals == {"prop": (0, 0), "ds": (1, 1)}
    assert downloads == ["2026:5"]
    assert any("TotalCount=4352" in line for line in logged)
    # the missed listing is retried: prop's store is left dirty
    assert json.loads(
        (tmp_path / "prop" / ".watermark.json").read_text())["dirty"] is True


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


def test_iter_listing_shortfall_under_one_page_is_upstream_bookkeeping(monkeypatch):
    # prop's listing serves 4 349 items under a TotalCount of 4 352. Counting
    # items the CMS then declines to serve is regeringen.se's business, and
    # refusing to harvest over it would strand every doctype walked after prop
    # in the same run. The discrepancy is reported, not raised.
    monkeypatch.setattr(download, "fetch", _fake_fetch({1: LISTING_1325}, 5))
    said = []
    pages = list(iter_listing(None, "ds", delay=0, log=said.append))
    assert [p for _, _, p in pages] == [1]
    assert "2 counted but not served" in said[0]


def test_sync_downloads_pm_doc_below_a_ds_only_page(tmp_path, monkeypatch):
    # end-to-end: a full pm sync whose first listing page is all-Ds still
    # reaches and downloads the promemoria on page 2
    monkeypatch.setattr(download, "fetch",
                        _fake_fetch({1: DS_ONLY_PAGE, 2: PM_ONLY_PAGE}, 3))
    downloads = []
    def mock_download_document(session, root, item, delay, log=print):
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


def test_refetch_bodies_recovers_from_the_stored_landing(tmp_path, monkeypatch):
    # a body-less record (finding 04's lr/SÖ gap): the stored landing carries
    # the content link, the asset now serves a real PDF -- the refetch stores
    # the body and updates the record; a record with a body is left alone
    docdir = layout.fa_dir(tmp_path, "lr", "2000/ungdomsmal")
    docdir.mkdir(parents=True)
    compress.write_download(docdir / "2000-ungdomsmal.html",
                            '<a href="/contentassets/abc/ungdomsmal/">pdf</a>')
    rec = {"type": "lr", "basefile": "2000/ungdomsmal",
           "identifier": "Ändringar i handläggningen av ungdomsmål",
           "url": "https://www.regeringen.se/x/", "files": []}
    write_atomic(layout.fa_record_file(tmp_path, "lr", "2000/ungdomsmal"),
                 json.dumps(rec).encode())

    fetched = []
    def fake_fetch(session, url, timeout=60):
        fetched.append(url)
        return SimpleNamespace(content=b"%PDF-1.4 fake", text="")
    monkeypatch.setattr(download, "fetch", fake_fetch)
    monkeypatch.setattr(download, "make_session", lambda ua: None)

    checked, recovered, errors = download.refetch_bodies(
        tmp_path, types=("lr",), delay=0, log=lambda *a: None)
    assert (checked, recovered, errors) == (1, 1, 0)
    assert fetched == ["https://www.regeringen.se/contentassets/abc/ungdomsmal/"]
    updated = json.loads(compress.read_text(
        layout.fa_record_file(tmp_path, "lr", "2000/ungdomsmal")))
    assert updated["files"] == ["2000-ungdomsmal.pdf"]
    assert compress.exists(docdir / "2000-ungdomsmal.pdf")
    # idempotent: the record now has a body, so a second run skips it
    assert download.refetch_bodies(tmp_path, types=("lr",), delay=0,
                                   log=lambda *a: None) == (0, 0, 0)


# --------------------------------------------------------------------------
# storing the documents: unchanged bytes must not become a new file
# --------------------------------------------------------------------------

def _store(tmp_path, monkeypatch, payloads):
    """Run store_documents over `payloads` (href -> bytes) and return the names."""
    monkeypatch.setattr(download, "fetch", lambda session, url, timeout=60:
                        SimpleNamespace(content=payloads[url.rsplit("/", 1)[-1]]))
    return download.store_documents(None, tmp_path, "2015-16-195",
                                    ["/contentassets/x/" + k for k in payloads], 0)


def test_unchanged_document_is_not_rewritten(tmp_path, monkeypatch):
    # the poppler conversion cache and the parse watermarks both key on the
    # PDF's mtime, so re-downloading identical bytes must leave the file alone
    names = _store(tmp_path, monkeypatch, {"a": b"%PDF-1.4 one"})
    assert names == ["2015-16-195.pdf"]
    before = compress.stat(tmp_path / "2015-16-195.pdf").st_mtime_ns

    assert _store(tmp_path, monkeypatch, {"a": b"%PDF-1.4 one"}) == names
    assert compress.stat(tmp_path / "2015-16-195.pdf").st_mtime_ns == before


def test_changed_document_is_written(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch, {"a": b"%PDF-1.4 one"})
    assert _store(tmp_path, monkeypatch, {"a": b"%PDF-1.4 two"}) == ["2015-16-195.pdf"]
    assert compress.read_bytes(tmp_path / "2015-16-195.pdf") == b"%PDF-1.4 two"


def test_reordered_links_keep_their_files(tmp_path, monkeypatch):
    # the names are positional, so a landing page that merely swapped its two
    # links would otherwise renumber both files -- and a renamed file is a new
    # file, costing both documents their conversion cache for no reason
    assert _store(tmp_path, monkeypatch, {"a": b"%PDF-1.4 one", "b": b"%PDF-1.4 two"}) \
        == ["2015-16-195.pdf", "2015-16-195-1.pdf"]
    mtimes = {n: compress.stat(tmp_path / n).st_mtime_ns
              for n in ("2015-16-195.pdf", "2015-16-195-1.pdf")}

    assert _store(tmp_path, monkeypatch, {"b": b"%PDF-1.4 two", "a": b"%PDF-1.4 one"}) \
        == ["2015-16-195-1.pdf", "2015-16-195.pdf"]
    assert {n: compress.stat(tmp_path / n).st_mtime_ns for n in mtimes} == mtimes


def test_a_new_document_takes_the_first_name_left_free(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch, {"a": b"%PDF-1.4 one", "b": b"%PDF-1.4 two"})
    # 'a' is gone and a third document appears: 'b' keeps -1, the newcomer
    # takes the freed base name rather than colliding with it
    assert _store(tmp_path, monkeypatch, {"b": b"%PDF-1.4 two", "c": b"%PDF-1.4 three"}) \
        == ["2015-16-195-1.pdf", "2015-16-195.pdf"]
    assert compress.read_bytes(tmp_path / "2015-16-195.pdf") == b"%PDF-1.4 three"


def test_a_link_that_is_not_a_document_is_skipped(tmp_path, monkeypatch):
    assert _store(tmp_path, monkeypatch,
                  {"a": b"<html>error</html>", "b": b"%PDF-1.4 one"}) \
        == ["2015-16-195.pdf"]


def test_a_word_bodied_record_is_not_a_live_record(tmp_path):
    # 260 propositions came from data.riksdagen.se with a .doc body and no
    # `source` key, so they read as live harvests and a --full walk passed over
    # them -- while regeringen.se lists the same documents as PDFs, which is
    # the only form the parser can recover chapter headings from
    write_atomic(layout.fa_record_file(tmp_path, "prop", "2006/07:128"),
                 json.dumps({"type": "prop", "files": ["2006-07-128.doc"],
                             "body_format": "doc"}).encode())
    assert has_live_record(tmp_path, "prop", "2006/07:128") is False

    write_atomic(layout.fa_record_file(tmp_path, "prop", "2015/16:195"),
                 json.dumps({"type": "prop", "files": ["2015-16-195.pdf"]}).encode())
    assert has_live_record(tmp_path, "prop", "2015/16:195") is True


# --------------------------------------------------------------------------
# the no-downgrade guard: a re-download must never trade a body for no body
# --------------------------------------------------------------------------

LANDING_NO_DOC = """<html><body><h1 id="h1id">Prop. 2001/02:82</h1>
  <p>Ingen bilaga har publicerats.</p></body></html>"""


def _item(basefile="2001/02:82"):
    return {"type": "prop", "basefile": basefile, "identifier": "Prop. " + basefile,
            "title": "En proposition", "date": "2002-03-14",
            "url": "https://www.regeringen.se/rattsliga-dokument/proposition/x/"}


def _stored(root, basefile, files, **extra):
    record = {"type": "prop", "basefile": basefile, "identifier": "Prop. " + basefile,
              "title": "En proposition", "date": "2002-03-14", "files": files, **extra}
    path = layout.fa_record_file(root, "prop", basefile)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, json.dumps(record, ensure_ascii=False))
    return record


def _no_doc_landing(monkeypatch):
    monkeypatch.setattr(download, "fetch",
                        lambda session, url, timeout=60: SimpleNamespace(
                            text=LANDING_NO_DOC, content=LANDING_NO_DOC.encode()))
    monkeypatch.setattr(download.time, "sleep", lambda *_: None)


def test_download_keeps_a_stored_body_when_the_landing_links_none(monkeypatch,
                                                                  tmp_path):
    """A regeringen landing page that links no document must not overwrite a
    record whose body came from elsewhere -- `files` is assigned, not merged, so
    the write would empty it and orphan bytes that stay on disk. This already
    happened to sou/1995:60 and sou/1999:78."""
    _no_doc_landing(monkeypatch)
    before = _stored(tmp_path, "2001/02:82", ["2001-02-82.doc"],
                     orig_url="http://193.188.157.111/prop?dok=P&post_id=1",
                     body_format="trips")
    logged = []
    record = download.download_document(None, tmp_path, _item(), delay=0,
                                        log=logged.append)
    assert record == before                       # returned untouched
    on_disk = json.loads(compress.read_text(
        layout.fa_record_file(tmp_path, "prop", "2001/02:82")))
    assert on_disk == before                      # and never rewritten
    assert on_disk["orig_url"].startswith("http://193.188.157.111")
    assert any("keeping the stored record" in m for m in logged)


def test_download_still_writes_a_record_that_had_no_body_either(monkeypatch,
                                                               tmp_path):
    """The guard is about losing a body, not about never rewriting: a stored
    record with `files: []` carries nothing to lose, so fresh metadata wins."""
    _no_doc_landing(monkeypatch)
    _stored(tmp_path, "2001/02:82", [], title="Stale title")
    record = download.download_document(None, tmp_path, _item(), delay=0)
    assert record["files"] == []
    assert record["title"] == "En proposition"    # refreshed from the listing
    assert record["url"].startswith("https://www.regeringen.se")


def test_download_writes_normally_when_the_landing_links_a_document(monkeypatch,
                                                                   tmp_path):
    """The guard must not block the ordinary upgrade path -- a landing that does
    link a document replaces a body-less record as before."""
    monkeypatch.setattr(download, "fetch",
                        lambda session, url, timeout=60: SimpleNamespace(
                            text='<a href="/contentassets/aa/prop">Prop (pdf)</a>',
                            content=b""))
    monkeypatch.setattr(download, "store_documents",
                        lambda *a, **kw: ["2001-02-82.pdf"])
    monkeypatch.setattr(download.time, "sleep", lambda *_: None)
    _stored(tmp_path, "2001/02:82", [])
    record = download.download_document(None, tmp_path, _item(), delay=0)
    assert record["files"] == ["2001-02-82.pdf"]


# ---- an item the listing did not name: --only, and the ownership guard ------

def _unnamed_listing(monkeypatch, items):
    monkeypatch.setattr(download, "iter_listing",
                        lambda session, typ, delay, log=None: [(items, len(items), 1)])


def test_only_reaches_a_document_the_listing_never_named(tmp_path, monkeypatch):
    """prop. 2025/26:223 is listed under its title alone, so the walk cannot
    match `--only` against the listing. It resolves such an item and matches the
    stored record's own basefile instead."""
    _unnamed_listing(monkeypatch, [
        {"type": "prop", "basefile": None, "identifier": None,
         "title": "Ett annat ärende", "date": "2026-04-01",
         "url": "http://example.com/annat"},
        {"type": "prop", "basefile": None, "identifier": None,
         "title": "En ny konsumentkreditlag", "date": "2026-03-30",
         "url": "http://example.com/223"},
    ])
    resolved = []

    def mock(session, root, item, delay, log=print):
        resolved.append(item["url"])
        if not item["url"].endswith("223"):
            return None                       # the landing named no number
        return {"basefile": "2025/26:223"}
    monkeypatch.setattr(download, "download_document", mock)
    assert download.sync(tmp_path, types=["prop"], delay=0,
                         only="2025/26:223") == {"prop": (2, 1)}
    assert resolved == ["http://example.com/annat", "http://example.com/223"]


def test_only_walks_past_an_unnamed_item_that_fails(tmp_path, monkeypatch):
    """One unrelated document failing must not end the run before it reaches
    the one asked for."""
    _unnamed_listing(monkeypatch, [
        {"type": "prop", "basefile": None, "identifier": None, "title": "x",
         "date": "2026-04-01", "url": "http://example.com/broken"},
        {"type": "prop", "basefile": None, "identifier": None, "title": "y",
         "date": "2026-03-30", "url": "http://example.com/223"},
    ])

    def mock(session, root, item, delay, log=print):
        if item["url"].endswith("broken"):
            raise requests.HTTPError("500 on the landing page")
        return {"basefile": "2025/26:223"}
    monkeypatch.setattr(download, "download_document", mock)
    assert download.sync(tmp_path, types=["prop"], delay=0,
                         only="2025/26:223") == {"prop": (2, 1)}


def test_an_unnamed_item_never_takes_over_another_pages_basefile(tmp_path,
                                                                 monkeypatch):
    """A number read off a page is a judgement, not a fact the listing stated --
    a regeringsuppdrag whose PDF link says "Dir. 2019:20" must not replace the
    real directive's record, nor drop its files into that directory."""
    real = layout.fa_record_file(tmp_path, "dir", "2019:20")
    real.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(real, json.dumps(
        {"type": "dir", "basefile": "2019:20", "identifier": "Dir. 2019:20",
         "title": "Det riktiga direktivet", "url": "http://example.com/dir",
         "files": ["2019-20.pdf"]}))
    landing = ('<h1>Uppdrag att utreda något</h1>'
               '<a href="/contentassets/x/y">Uppdraget, Dir. 2019:20 (pdf)</a>')
    monkeypatch.setattr(download, "fetch",
                        lambda session, url, timeout=60:
                        SimpleNamespace(text=landing))
    stored_files = []
    monkeypatch.setattr(download, "store_documents",
                        lambda *a, **kw: stored_files.append(a) or [])
    lines = []
    assert download.download_document(
        None, tmp_path,
        {"type": "dir", "basefile": None, "identifier": None,
         "title": "Uppdrag att utreda något", "date": "2026-01-01",
         "url": "http://example.com/uppdrag"},
        0, lines.append) is None
    assert stored_files == []                 # nothing written into its dir
    assert compress.read_json(real)["title"] == "Det riktiga direktivet"
    assert "already owns it" in "".join(lines)
