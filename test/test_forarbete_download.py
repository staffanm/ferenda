"""Tests for the förarbete downloader's parsing (network-free)."""

import json

from accommodanda.forarbete.download import (_has_live_record, basefile_slug,
                                             find_content_links, parse_listing)
from accommodanda.lib.util import record_path, write_atomic

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
    items = parse_listing(LISTING, "prop")
    assert len(items) == 2
    a = items[0]
    assert a["basefile"] == "2025/26:279"           # the document's own id
    assert a["identifier"] == "Prop. 2025/26:279"
    assert a["title"] == "Personalförsörjning av det militära försvaret"
    assert a["date"] == "2026-06-09"
    assert a["url"].endswith("/proposition/2026/06/prop.-202526279/")
    assert items[1]["basefile"] == "2025/26:276"


def test_parse_listing_slug_type_falls_back_to_slug():
    items = parse_listing(LISTING_SLUG, "lr")
    assert len(items) == 1
    # lagrådsremiss has no number on regeringen.se -> basefile is the slug
    assert items[0]["basefile"] == "andrade-regler-om-avdrag"
    assert items[0]["title"] == "Ändrade regler om avdrag"


def test_parse_listing_skips_items_without_the_types_identifier():
    # a stray link whose text lacks "Prop. N" must not be taken as a document
    html = LISTING.replace(", Prop. 2025/26:279", "")
    assert len(parse_listing(html, "prop")) == 1  # only the second item survives


def test_parse_listing_ds_takes_only_ds_numbered_items():
    # category 1325 mixes ds and pm; asked for "ds" only the Ds-numbered item
    # is a document -- the dnr and title-only promemorior are skipped.
    items = parse_listing(LISTING_1325, "ds")
    assert len(items) == 1
    assert items[0]["basefile"] == "2026:15"
    assert items[0]["identifier"] == "Ds 2026:15"
    assert items[0]["title"] == "Gäldenärens avtal i konkurs"


def test_parse_listing_pm_takes_the_non_ds_promemorior():
    # asked for "pm" the same page yields the dnr item and the title-only item,
    # and skips the Ds-numbered one (it belongs to ds).
    items = parse_listing(LISTING_1325, "pm")
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
    write_atomic(record_path(tmp_path, "prop", "2020/21:1"),
                 json.dumps({"type": "prop", "files": []}))
    assert _has_live_record(tmp_path, "prop", "2020/21:1") is True
    # a frozen import record (carries `source`, §7g) is treated as absent, so the
    # live downloader fetches its better copy AND it never trips the incremental stop
    write_atomic(record_path(tmp_path, "prop", "1997/98:45"),
                 json.dumps({"type": "prop", "source": "proptrips", "legacy_files": []}))
    assert _has_live_record(tmp_path, "prop", "1997/98:45") is False
    assert _has_live_record(tmp_path, "prop", "1867:23") is False   # truly absent
