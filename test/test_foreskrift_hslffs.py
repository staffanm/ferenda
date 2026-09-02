"""HSLF-FS: one författningssamling, six publishing sites.

Each scope's enumerator is checked against a trimmed copy of the index it
reads, so a site that changes shape fails here rather than in a corpus run.
The fixtures keep the typography the real pages print -- the split designation
"HSLF- FS 2020:19", the space after the colon in "HSLF-FS 2024: 21", the
zero-width joiners and non-breaking hyphens in Läkemedelsverkets feed -- because
that is what the number-reading has to survive.
"""

import json
import types
from pathlib import Path

import pytest

from ferenda.foreskrift import harvest, hslffs, parse
from ferenda.foreskrift.agencies import REGISTRY, SAMLINGAR
from ferenda.foreskrift.source import SOURCES
from ferenda.lib import compress
from ferenda.lib.util import approximate_date, record_path

FILES = Path(__file__).parent / "files" / "foreskrift"

SCOPES = ("hslffs-sos", "hslffs-fohm", "hslffs-ivo", "hslffs-lv",
          "hslffs-mfof", "hslffs-tlv")


class Saved:
    """A session serving a page per URL: a fixture file name, or the body
    itself for the one-off shapes a test writes inline."""

    def __init__(self, pages):
        self.pages = pages
        self.asked = []

    def request(self, method, url, **kwargs):
        self.asked.append(url)
        assert url in self.pages, "unexpected fetch of %s" % url
        page = self.pages[url]
        body = page if page.lstrip().startswith("<") \
            else (FILES / page).read_text("utf-8")
        return types.SimpleNamespace(text=body, content=body.encode("utf-8"),
                                     status_code=200, headers={}, url=url)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """Route both modules' `request` at the saved copies -- the enumerators
    fetch through `hslffs`, the shared body fetch through `harvest`."""
    serve = (lambda session, method, url, **kw:
             session.request(method, url, **kw))
    monkeypatch.setattr(hslffs, "request", serve)
    monkeypatch.setattr(harvest, "request", serve)
    monkeypatch.setattr(harvest.time, "sleep", lambda _s: None)


def enumerate_scope(scope, pages):
    agency = REGISTRY[scope]
    session = Saved(pages)
    return {ref.basefile: ref for ref in agency.enumerate(session, agency)}, session


# --------------------------------------------------------------------------
# the registry
# --------------------------------------------------------------------------

def test_six_scopes_publish_into_the_one_hslffs_samling():
    scoped = {scope: agency for scope, agency in REGISTRY.items()
              if agency.fs == "hslffs"}
    assert sorted(scoped) == sorted(SCOPES)
    for scope, agency in scoped.items():
        assert agency.scope == scope
        assert agency.designation == "HSLF-FS"
        assert agency.enumerate is not None and agency.resolve is not None
        # every scope may file into HSLF-FS, and into whichever closed
        # predecessor samling its own site still lists
        assert agency.params["samlingar"]["hslffs"] == "HSLF-FS"


def test_registry_keys_are_unique_and_default_to_the_fs_code():
    scopes = [agency.scope or agency.fs for agency in REGISTRY.values()]
    assert len(scopes) == len(set(scopes)) == len(REGISTRY)
    assert list(REGISTRY) == scopes
    # a samling one agency owns keeps the fs code as its scope
    assert REGISTRY["fffs"].scope is None and REGISTRY["fffs"].fs == "fffs"


def test_samlingar_is_keyed_by_samling_not_by_publisher():
    # SAMLINGAR is what everything keyed by *document identity* reads --
    # the printed designation, the parser's designation->slug table
    assert len(SAMLINGAR) == len(REGISTRY) - 5      # six scopes, one samling
    assert SAMLINGAR["hslffs"].designation == "HSLF-FS"
    assert SAMLINGAR["hslffs"].scope == "hslffs-sos"
    assert all(fs == agency.fs for fs, agency in SAMLINGAR.items())


def test_cli_help_names_every_hslffs_scope():
    notes = next(source for source in SOURCES if source.name == "foreskrift").notes
    for scope in SCOPES:
        assert scope in notes


# --------------------------------------------------------------------------
# reading a printed number off what the sites actually print
# --------------------------------------------------------------------------

@pytest.mark.parametrize("printed,expected", [
    ("HSLF-FS 2025:25 Socialstyrelsens kungörelse", ("HSLF-FS", "2025", "25")),
    ("HSLF-FS 2024: 21 Rättsmedicinalverkets", ("HSLF-FS", "2024", "21")),
    ("HSLF- FS 2020:19 Socialstyrelsens", ("HSLF-FS", "2020", "19")),
    ("HSLF‍-‍FS\xa02025:67", ("HSLF-FS", "2025", "67")),
    ("HSLF‑FS 2020:5 (omtryckt) pdf", ("HSLF-FS", "2020", "5")),
    ("HSLF-FS_2025_68 pdf, 656 kB.", ("HSLF-FS", "2025", "68")),
    ("Ändringsförfattning: HSLF-FS-2020:23 (PDF, 88 kB)",
     ("HSLF-FS", "2020", "23")),
    # a soft hyphen before the year, which only `util.normalize_hints` removes
    ("Föreskrifter (LVFS\xa0\xad2005:13) om ändring i", ("LVFS", "2005", "13")),
    ("SOSFS 2013:1 Socialstyrelsens allmänna råd", ("SOSFS", "2013", "1")),
    ("TLVFS 2009:03 pdf", ("TLVFS", "2009", "3")),      # leading zero dropped
    ("Register över författningar m.m. under år 2025", None),
])
def test_numbered_reads_the_typography_the_sites_print(printed, expected):
    assert hslffs.numbered(printed) == expected


def test_the_misprinted_designations_file_under_the_right_samling():
    # single documents whose designation the site typoed; the number is right,
    # and so is the file name the site links them by
    assert hslffs.samling("HSLS-FS") == "hslffs"     # Socialstyrelsen, 2017:25
    assert hslffs.samling("HFSL-FS") == "hslffs"     # Läkemedelsverket, 2017:69
    assert hslffs.samling("SLF-FS") == "hslffs"      # Folkhälsomyndigheten, 2016:97
    # everything else is the shared designation->slug rule
    assert hslffs.samling("FoHMFS") == harvest.fs_code("FoHMFS") == "fohmfs"


def test_a_samling_the_scope_does_not_publish_into_stops_the_harvest():
    # the same refusal wherever a designation is read -- an enumerated row and
    # an amendment register both mint their identifier through `printed`
    with pytest.raises(ValueError, match="hslffs-ivo lists 'KIFS'"):
        hslffs.file_docref(REGISTRY["hslffs-ivo"], "KIFS", "2020", "1",
                           "https://example.invalid/x.pdf", set())
    with pytest.raises(ValueError, match="hslffs-ivo lists 'KIFS'"):
        hslffs.printed("KIFS", REGISTRY["hslffs-ivo"])


# --------------------------------------------------------------------------
# Socialstyrelsen
# --------------------------------------------------------------------------

SOS_PAGES = {
    REGISTRY["hslffs-sos"].index_url: "sos-publikationer.html",
    REGISTRY["hslffs-sos"].params["konsoliderade_url"]: "sos-konsoliderade.html",
    "https://www.socialstyrelsen.se/kunskapsstod-och-regler/regler-och-riktlinjer/"
    "foreskrifter-och-allmanna-rad/konsoliderade-foreskrifter/20131-om-ekonomiskt-bistand/":
        "sos-konsoliderad-sosfs-2013-1.html",
    "https://www.socialstyrelsen.se/kunskapsstod-och-regler/regler-och-riktlinjer/"
    "foreskrifter-och-allmanna-rad/konsoliderade-foreskrifter/"
    "201515-om-vissa-atgarder-i-halso--och-sjukvarden-vid-dodsfall/":
        "sos-konsoliderad-sosfs-2015-15.html",
}


def test_sos_yields_one_docref_per_publication_and_filters_the_rest():
    refs, _ = enumerate_scope("hslffs-sos", SOS_PAGES)
    ref = refs["hslffs/2025:25"]
    assert ref.identifier == "HSLF-FS 2025:25"
    # DocRef.fs is set only where the document's samling differs from the
    # scope's own, the convention every föreskrift enumerator follows
    assert ref.fs is None and (ref.fs or REGISTRY["hslffs-sos"].fs) == "hslffs"
    assert ref.url == (
        "https://www.socialstyrelsen.se/publikationer/hslf-fs-202525-"
        "socialstyrelsens-kungorelse-om-andring-i-allmanna-raden-sosfs-20131-"
        "om-ekonomiskt-bistand-2025-6-9606/")
    assert ref.title.startswith("HSLF-FS 2025:25 Socialstyrelsens kungörelse")
    # the three entries whose designation the list misprints are still read
    assert {"hslffs/2024:21", "hslffs/2020:19", "hslffs/2017:25"} <= set(refs)
    # a SOSFS entry lands under the closed samling's own namespace
    assert refs["sosfs/2013:1"].fs == "sosfs"
    assert refs["sosfs/2013:1"].identifier == "SOSFS 2013:1"
    # registers, förteckningar, handböcker and meddelanden are not författningar
    assert not any("Register över" in (r.title or "") for r in refs.values())
    assert not any(r.title and r.title.startswith("Meddelande")
                   for r in refs.values())


def test_sos_attaches_the_konsoliderad_page_to_its_base_act():
    refs, _ = enumerate_scope("hslffs-sos", SOS_PAGES)
    assert refs["sosfs/2013:1"].extra["consolidations"] == [{
        "url": "https://www.socialstyrelsen.se/kunskapsstod-och-regler/"
               "regler-och-riktlinjer/foreskrifter-och-allmanna-rad/"
               "konsoliderade-foreskrifter/20131-om-ekonomiskt-bistand/"}]
    # HSLF-FS 2025:25 amends that act; it is a document of its own, not a
    # consolidation of one
    assert refs["hslffs/2025:25"].extra["consolidations"] == []


def test_sos_asks_the_page_which_samling_a_2015_base_belongs_to():
    # 2015 is the transition year: both SOSFS 2015:15 and HSLF-FS 2015:15
    # exist, so the join cannot settle it and the page's own heading does --
    # "Senaste version av HSLF-FS 2015:15 …"
    refs, session = enumerate_scope("hslffs-sos", SOS_PAGES)
    assert any("vid-dodsfall" in url for url in session.asked)
    assert len(refs["hslffs/2015:15"].extra["consolidations"]) == 1
    assert refs["sosfs/2015:15"].extra["consolidations"] == []
    # every other base is joined against the publication list, at no cost
    assert len(session.asked) == 3
    assert len(refs["hslffs/2022:39"].extra["consolidations"]) == 1


def test_sos_amendments_come_off_the_konsoliderad_page_register():
    agency = REGISTRY["hslffs-sos"]
    page = (FILES / "sos-konsoliderad-sosfs-2013-1.html").read_text("utf-8")
    ref = types.SimpleNamespace(basefile="sosfs/2013:1", identifier="SOSFS 2013:1")
    amendments = hslffs.sos_amendments(page, "https://www.socialstyrelsen.se/x/",
                                       agency, ref)
    assert [a["identifier"] for a in amendments] == [
        "SOSFS 2013:26", "HSLF-FS 2017:10", "HSLF-FS 2020:61",
        "HSLF-FS 2021:31", "HSLF-FS 2025:25"]
    # each amendment points at its own publication page
    assert amendments[-1]["url"].endswith("-2025-6-9606/")
    # the base act's own "Grundförfattning" row is not an amendment of itself
    assert "SOSFS 2013:1" not in [a["identifier"] for a in amendments]


def test_sos_konsoliderad_page_with_no_amendment_yet_is_not_an_error():
    # HSLF-FS 2024:5 is consolidated but not yet amended: its register names
    # the grundförfattning and a rättelse of it, and nothing else. An empty
    # amendment list is that answer, not a page this code failed to read.
    agency = REGISTRY["hslffs-sos"]
    page = (FILES / "sos-konsoliderad-hslffs-2024-5.html").read_text("utf-8")
    ref = types.SimpleNamespace(basefile="hslffs/2024:5",
                                identifier="HSLF-FS 2024:5")
    assert hslffs.sos_amendments(page, "https://www.socialstyrelsen.se/k/20245/",
                                 agency, ref) == []


def test_a_page_with_no_register_at_all_stops_the_harvest():
    agency = REGISTRY["hslffs-sos"]
    ref = types.SimpleNamespace(basefile="hslffs/2024:5",
                                identifier="HSLF-FS 2024:5")
    with pytest.raises(ValueError, match="carries no .Ladda ner. register"):
        hslffs.sos_amendments("<html><body>tom</body></html>",
                              "https://www.socialstyrelsen.se/k/20245/",
                              agency, ref)


def test_a_konsoliderad_row_with_no_number_stops_the_harvest():
    # the index's own entry is the one row that leads with no number; anything
    # else that does is the sub-navigation having changed shape
    agency = REGISTRY["hslffs-sos"]
    index = ('<html><body><a class="page-sub-navigation__link" href="%s">%s</a>'
             '</body></html>')
    pages = dict(SOS_PAGES)
    pages[agency.params["konsoliderade_url"]] = index % (
        "/kunskapsstod-och-regler/regler-och-riktlinjer/foreskrifter-och-"
        "allmanna-rad/konsoliderade-foreskrifter/om-ekonomiskt-bistand/",
        "Om ekonomiskt bistånd")
    with pytest.raises(ValueError, match="opens with no number"):
        enumerate_scope("hslffs-sos", pages)
    # the index's own row in that same sub-navigation is not a konsoliderad act
    pages[agency.params["konsoliderade_url"]] = index % (
        "/kunskapsstod-och-regler/regler-och-riktlinjer/foreskrifter-och-"
        "allmanna-rad/konsoliderade-foreskrifter/", "Konsoliderade föreskrifter")
    refs, _ = enumerate_scope("hslffs-sos", pages)
    assert refs["sosfs/2013:1"].extra["consolidations"] == []


def test_a_register_heading_whose_link_is_gone_stops_the_harvest():
    # a whole-document forward search would hand this amendment the *next*
    # member's publication page and file it against that document
    agency = REGISTRY["hslffs-sos"]
    page = ('<html><body><main>'
            '<div class="fileinformation__heading">Ändringsförfattning '
            'HSLF-FS 2021:31</div><div class="block-margin"></div>'
            '</main></body></html>')
    ref = types.SimpleNamespace(basefile="sosfs/2013:1",
                                identifier="SOSFS 2013:1")
    with pytest.raises(ValueError, match="links no publication page"):
        hslffs.sos_amendments(page, "https://www.socialstyrelsen.se/k/20131/",
                              agency, ref)


def test_sos_body_url_reads_the_publication_page_rather_than_guessing():
    # the artikelkatalog URL the frozen records were imported under now
    # redirects to the publication index, so the page is what is read
    body = REGISTRY["hslffs-sos"].params["body_url"]
    page = ('<a class="publication-wrapper__main-doc" '
            'href="/contentassets/3762e2/2025-6-9606.pdf">Öppna publikation</a>')
    assert body(page, "https://www.socialstyrelsen.se/publikationer/x/") == \
        "https://www.socialstyrelsen.se/contentassets/3762e2/2025-6-9606.pdf"
    with pytest.raises(ValueError, match="publication-wrapper__main-doc.* link on"):
        body("<p>ingen fil</p>", "https://www.socialstyrelsen.se/publikationer/x/")


# --------------------------------------------------------------------------
# Folkhälsomyndigheten
# --------------------------------------------------------------------------

FOHM_PAGES = {
    REGISTRY["hslffs-fohm"].params["page_url"].format(page=1):
        "fohm-publikationer-1.html",
    REGISTRY["hslffs-fohm"].params["page_url"].format(page=2):
        "fohm-publikationer-2.html",
    REGISTRY["hslffs-fohm"].params["page_url"].format(page=3):
        "fohm-publikationer-2.html",     # a repeated page ends the pager walk
}


def test_fohm_walks_the_pager_and_files_each_row_under_its_own_samling():
    refs, session = enumerate_scope("hslffs-fohm", FOHM_PAGES)
    assert len(session.asked) == 3           # two pages plus the repeat
    ref = refs["hslffs/2026:30"]
    assert ref.identifier == "HSLF-FS 2026:30"
    assert ref.url.endswith(
        "/publikationsarkiv/f/folkhalsomyndighetens-foreskrifter-hslf-fs-2026-30/")
    # the row title names the act it amends first and its own number last, so
    # the slug decides which document the row is
    assert "(HSLF-FS 2015:7)" in ref.title
    assert refs["fohmfs/2014:5"].identifier == "FoHMFS 2014:5"
    assert refs["fhifs/2013:12"].identifier == "FHIFS 2013:12"
    # one slug misprints the designation ("fohfs-2014-13"); the title has it
    assert refs["fohmfs/2014:13"].identifier == "FoHMFS 2014:13"


def test_fohm_attaches_a_konsoliderad_row_to_the_act_it_consolidates():
    refs, _ = enumerate_scope("hslffs-fohm", FOHM_PAGES)
    assert [c["url"].rsplit("/", 2)[-2]
            for c in refs["hslffs/2025:14"].extra["consolidations"]] == \
        ["konsoliderad-version-av-hslf-fs-2025-14-folkhalsomyndighetens-"
         "foreskrifter-och-allmanna-rad"]
    assert len(refs["fohmfs/2014:5"].extra["consolidations"]) == 1


def test_fohm_row_whose_slug_and_title_disagree_stops_the_harvest():
    # a one-off shape, served inline rather than written into the fixture tree
    agency = REGISTRY["hslffs-fohm"]
    page = ('<html><body><a class="headline--linked" href="/publikationsarkiv/f/'
            'folkhalsomyndighetens-foreskrifter-hslf-fs-2026-99/">'
            '<h3>Föreskrifter om ändring i (HSLF-FS 2015:7) HSLF-FS 2026:30</h3>'
            '</a></body></html>')
    pages = {agency.params["page_url"].format(page=n): page for n in (1, 2)}
    with pytest.raises(ValueError, match="which its title does not print"):
        enumerate_scope("hslffs-fohm", pages)


def test_fohm_konsoliderad_row_forwards_to_the_reader_that_holds_the_text():
    # a konsoliderad row publishes no PDF at all -- only a "Läs publikation"
    # button over a `?pub=<id>` view, and that view holds the text
    forward = REGISTRY["hslffs-fohm"].params["consolidation_page_url"]
    listed = ("https://www.folkhalsomyndigheten.se/publikationer-och-material/"
              "publikationsarkiv/k/konsoliderad-version-av-hslf-fs-2025-14-"
              "folkhalsomyndighetens-foreskrifter-och-allmanna-rad/")
    page = (FILES / "fohm-konsoliderad-hslffs-2025-14.html").read_text("utf-8")
    assert forward(page, listed) == listed + "?pub=157921"
    with pytest.raises(ValueError, match="no 'a.publication-open"):
        forward("<html><body>ingen läsare</body></html>", listed)


def test_fohm_amendments_come_off_the_readers_printed_pdf_register():
    agency = REGISTRY["hslffs-fohm"]
    page = (FILES / "fohm-reader-hslffs-2025-14.html").read_text("utf-8")
    url = ("https://www.folkhalsomyndigheten.se/publikationer-och-material/"
           "publikationsarkiv/k/x/?pub=157921")
    ref = types.SimpleNamespace(basefile="hslffs/2025:14",
                                identifier="HSLF-FS 2025:14")
    assert hslffs.fohm_amendments(page, url, agency, ref) == [{
        "identifier": "HSLF-FS 2026:13",
        "url": "https://www.folkhalsomyndigheten.se/contentassets/"
               "b1cc4ad150fb421796eac48154522fb4/hslf-fs-2026-13-"
               "andringsforfattning.pdf"}]
    # the "Grundförfattning:" row is the base act, not an amendment of itself
    with pytest.raises(ValueError, match="carries no .Tryckta versioner. register"):
        hslffs.fohm_amendments("<html><body>ingen läsare</body></html>", url,
                               agency, ref)


def test_fohm_body_url_takes_the_publication_page_pdf():
    body = REGISTRY["hslffs-fohm"].params["body_url"]
    page = (FILES / "fohm-publikation-hslf-fs-2026-30.html").read_text("utf-8")
    # the file name is Folkhälsomyndighetens own, not the document's number
    assert body(page, "https://www.folkhalsomyndigheten.se/p/x/").endswith(
        "/contentassets/702713fbed804472bde565d8c04d2d35/hslf-fs-2026-09.pdf")


# --------------------------------------------------------------------------
# IVO, MFoF (an index of files)
# --------------------------------------------------------------------------

def test_ivo_yields_one_docref_per_number_with_its_pdf():
    refs, _ = enumerate_scope(
        "hslffs-ivo", {REGISTRY["hslffs-ivo"].index_url: "ivo-foreskrifter.html"})
    ref = refs["hslffs/2026:8"]
    assert ref.identifier == "HSLF-FS 2026:8"
    assert ref.extra["regulation_url"] == ref.url == (
        "https://www.ivo.se/globalassets/dokument/publikationer/foreskrifter/"
        "hslf-fs-2026-8-foreskrifter-om-andring-i-foreskrifterna-om-anmalan-av-"
        "verksamhet-enligt-patientsakerhetslagen-2026-03-25.pdf")
    assert ref.extra["source_url"] == REGISTRY["hslffs-ivo"].index_url
    # "Konsoliderad version av HSLF-FS 2023:7" is not a number of its own
    assert refs["hslffs/2023:7"].extra["consolidations"] == [
        {"url": "https://www.ivo.se/globalassets/dokument/publikationer/"
                "foreskrifter/konsoliderad-version-av-hslf-fs-20237.pdf"}]
    assert refs["hslffs/2023:7"].extra["regulation_url"].endswith("hslf-fs-2023-7.pdf")
    # the förteckning över IVO:s föreskrifter names no number, so it is no document
    assert not any("forteckning" in r.url for r in refs.values())


def test_mfof_reads_the_number_off_the_row_the_chrome_interrupts():
    refs, _ = enumerate_scope(
        "hslffs-mfof", {REGISTRY["hslffs-mfof"].index_url: "mfof-foreskrifter.html"})
    assert sorted(refs) == ["hslffs/2017:51", "hslffs/2021:64", "hslffs/2022:18",
                            "hslffs/2022:25", "hslffs/2022:66", "hslffs/2023:3"]
    # MFoF splits this title across two anchors, so only the bare number
    # survives in one piece: "… (HSLF-FS pdf, 233.2 kB, … 2022:25) (pdf)"
    assert refs["hslffs/2022:25"].identifier == "HSLF-FS 2022:25"
    assert refs["hslffs/2023:3"].url.endswith("_20230502.pdf")
    # the konsoliderad file hangs on 2021:64, which the page also lists as
    # GRUNDFÖRFATTNING
    assert len(refs["hslffs/2021:64"].extra["consolidations"]) == 1
    assert "(inklusive%20bilagor).pdf" in refs["hslffs/2021:64"].extra["regulation_url"]


# --------------------------------------------------------------------------
# TLV (an accordion panel per base act)
# --------------------------------------------------------------------------

TLV_PAGES = {REGISTRY["hslffs-tlv"].index_url: "tlv-foreskrifter.html"}


def test_tlv_yields_the_base_act_and_every_amendment_in_its_panel():
    refs, _ = enumerate_scope("hslffs-tlv", TLV_PAGES)
    ref = refs["hslffs/2026:24"]
    assert ref.identifier == "HSLF-FS 2026:24"
    assert ref.url.endswith("/HSLF-FS_2026-24.pdf")
    assert refs["hslffs/2017:29"].title.startswith(
        "TLV:s föreskrifter (HSLF-FS 2017:29) om licensläkemedel")
    # the panels carry TLV's two closed predecessor samlingar as well
    assert refs["tlvfs/2009:3"].identifier == "TLVFS 2009:3"
    assert refs["lfnfs/2003:1"].identifier == "LFNFS 2003:1"


def test_a_tlv_file_under_no_role_heading_stops_the_harvest():
    # the role is the heading above the file *in its own panel*: a forward
    # search from the anchor reads across the panel boundary, and would give
    # the first file of a panel the last role of the one before
    agency = REGISTRY["hslffs-tlv"]
    page = ('<html><body>'
            '<div class="sv-collapsible-content"><h2>TLV:s föreskrifter '
            '(HSLF-FS 2026:23) om ansökan</h2><h3>Grundföreskrift:</h3>'
            '<a href="/download/18.a/HSLF-FS_2026-23.pdf">HSLF-FS 2026:23 pdf'
            '</a></div>'
            '<div class="sv-collapsible-content"><h2>TLV:s föreskrifter '
            '(TLVFS 2009:4) om prissättning</h2>'
            '<a href="/download/18.b/TLVFS_2009_4.pdf">TLVFS 2009:4 pdf</a>'
            '</div></body></html>')
    with pytest.raises(ValueError, match="sits under no role heading"):
        enumerate_scope("hslffs-tlv", {agency.index_url: page})


def test_a_tlv_panel_that_names_no_base_act_stops_the_harvest():
    agency = REGISTRY["hslffs-tlv"]
    page = ('<html><body><div class="sv-collapsible-content">'
            '<h3>Grundföreskrift:</h3>'
            '<a href="/download/18.a/HSLF-FS_2026-23.pdf">HSLF-FS 2026:23 pdf</a>'
            '</div></body></html>')
    with pytest.raises(ValueError, match="names no base act"):
        enumerate_scope("hslffs-tlv", {agency.index_url: page})


def test_tlv_files_a_konsoliderad_text_against_its_panels_grundforeskrift():
    refs, _ = enumerate_scope("hslffs-tlv", TLV_PAGES)
    # the oldest panel labels the konsoliderad text of LFNFS 2003:1
    # "TLVFS 2003:2", a number the samling never issued in its own right
    assert "tlvfs/2003:2" not in refs
    assert refs["lfnfs/2003:1"].extra["consolidations"] == [
        {"url": "https://www.tlv.se/download/18.2863d50f15f9b0b125bde2a6/"
                "1510819045985/tlvfs_2003_2_konsoliderad.pdf"}]
    # where the panel agrees with itself, the consolidation lands on the act
    assert len(refs["hslffs/2017:29"].extra["consolidations"]) == 1


# --------------------------------------------------------------------------
# Läkemedelsverket
# --------------------------------------------------------------------------

LV_PAGES = {REGISTRY["hslffs-lv"].params["feed_url"]: "lv-foreskrifter.atom"}


def test_lv_enumerates_the_atom_feed_the_page_offers():
    # the Angular JSON endpoint answers 200 with an empty body to every
    # plain-HTTP client; the feed the same page offers is served in full
    assert hslffs.LV_FEED_URL == \
        "https://www.lakemedelsverket.se/api/rss/Rss/?pageId=4741"
    refs, session = enumerate_scope("hslffs-lv", LV_PAGES)
    assert session.asked == [hslffs.LV_FEED_URL]
    ref = refs["hslffs/2025:67"]
    assert ref.identifier == "HSLF-FS 2025:67"
    assert ref.url.endswith("/foreskrifter/2025-67/")
    assert ref.extra["consolidations"] == [
        {"url": "https://www.lakemedelsverket.se/sv/lagar-och-regler/"
                "foreskrifter/2025-67-konsoliderad/"}]
    assert refs["lvfs/2011:10"].identifier == "LVFS 2011:10"
    # the register carries one SOSFS föreskrift from before the responsibility
    # for medicines moved to Läkemedelsverket
    assert refs["sosfs/1991:5"].identifier == "SOSFS 1991:5"
    # and one entry whose designation the feed typoes
    assert refs["hslffs/2017:69"].identifier == "HSLF-FS 2017:69"


def test_lv_reads_a_bare_number_entry_off_its_slug():
    refs, _ = enumerate_scope("hslffs-lv", LV_PAGES)
    assert refs["hslffs/2026:26"].title == "2026:26"
    assert refs["hslffs/2026:26"].url.endswith("/foreskrifter/2026-26/")
    # LVFS closed when HSLF-FS opened on 1 July 2015, so the årsutgåva decides
    assert hslffs.lv_slug_number("/sv/lagar-och-regler/foreskrifter/2011-10/", "") \
        == ("LVFS", "2011", "10")


def test_lv_body_url_reads_the_server_rendered_state_not_an_anchor():
    body = REGISTRY["hslffs-lv"].params["body_url"]
    page = (FILES / "lv-dokument-hslf-fs-2025-67.html").read_text("utf-8")
    url = "https://www.lakemedelsverket.se/sv/lagar-och-regler/foreskrifter/2025-67-konsoliderad/"
    assert body(page, url) == (
        "https://www.lakemedelsverket.se/globalassets/dokument/lagar-och-regler/"
        "hslf-fs/hslf-fs-2025-67-konsoliderad.pdf")
    with pytest.raises(ValueError, match="no server-rendered app-root state"):
        body("<html><body>tom</body></html>", url)


# --------------------------------------------------------------------------
# the issuing agency, over a masthead that is the samling's
# --------------------------------------------------------------------------

# every HSLF-FS masthead opens with the shared samling's own name and the
# Utgivare line of its editor at Socialstyrelsen, whichever agency issued the
# document -- so those two signals must not answer for the issuer
GEMENSAM = ("Gemensamma författningssamlingen avseende hälso- och sjukvård, "
            "socialtjänst, läkemedel, folkhälsa m.m. "
            "ISSN 2002-1054, Artikelnummer IVO 2026-22 "
            "Utgivare: Chefsjurist Pär Ödman, Socialstyrelsen ")


@pytest.mark.parametrize("body,expected", [
    ("Inspektionen för vård och omsorgs föreskrifter om ändring i "
     "föreskrifterna (HSLF-FS 2023:7) om anmälan av verksamhet",
     "Inspektionen för vård och omsorg"),
    ("Läkemedelsverkets föreskrifter om giltigheten av Europafarmakopén",
     "Läkemedelsverket"),
    # the second column drops its headers into the first column's line
    ("Föreskrifter om ändring i Tandvårds- och Utkom från trycket "
     "läkemedelsförmånsverkets föreskrifter den 25 juni 2026 (TLVFS 2003:2)",
     "Tandvårds- och läkemedelsförmånsverket"),
    # a kungörelse names no "<agency>s föreskrifter"; None sends the caller
    # back to the harvest label, which is the scope's own agency
    ("Socialstyrelsens kungörelse om ändring i allmänna råden (SOSFS 2013:1)",
     None),
])
def test_the_shared_samlings_masthead_does_not_speak_for_the_issuer(body, expected):
    assert parse.extract_publisher(GEMENSAM + body) == expected


def test_an_agencys_own_samling_still_reads_its_utgivare_line():
    # unchanged for the 70 samlingar one agency owns outright
    assert parse.extract_publisher(
        "Finansinspektionens författningssamling "
        "Utgivare: Finansinspektionen, Box 7821, 103 97 Stockholm") \
        == "Finansinspektionen"


# --------------------------------------------------------------------------
# resolving a page-indexed document
# --------------------------------------------------------------------------

def test_resolve_page_stores_the_pdf_and_the_konsoliderad_text(tmp_path, monkeypatch):
    agency = REGISTRY["hslffs-sos"]
    landing = ('<a class="publication-wrapper__main-doc" '
               'href="/contentassets/x/2013-3-13.pdf">Öppna publikation</a>')
    kons = (FILES / "sos-konsoliderad-sosfs-2013-1.html").read_text("utf-8")
    served = {"https://www.socialstyrelsen.se/publikationer/sosfs-20131/": landing,
              "https://www.socialstyrelsen.se/contentassets/x/2013-3-13.pdf":
                  "%PDF-1.6\nfixture",
              "https://www.socialstyrelsen.se/k/20131-om-ekonomiskt-bistand/": kons}

    def serve(session, method, url, **kw):
        body = served[url]
        return types.SimpleNamespace(
            text=body, content=body.encode("utf-8"), status_code=200)

    monkeypatch.setattr(hslffs, "request", serve)
    monkeypatch.setattr(harvest, "request", serve)
    monkeypatch.setattr(hslffs.time, "sleep", lambda _s: None)
    ref = hslffs.DocRef(
        basefile="sosfs/2013:1", fs="sosfs", identifier="SOSFS 2013:1",
        url="https://www.socialstyrelsen.se/publikationer/sosfs-20131/",
        title="SOSFS 2013:1 Socialstyrelsens allmänna råd om ekonomiskt bistånd",
        extra={"consolidations": [
            {"url": "https://www.socialstyrelsen.se/k/20131-om-ekonomiskt-bistand/"}]})
    record = hslffs.resolve_page(None, agency, ref, str(tmp_path), rejects=[])

    assert record["fs"] == "sosfs"
    assert record["publisher"] == "Socialstyrelsen"
    assert record["files"]["regulation"]["name"] == "sosfs-2013-1-regulation.pdf"
    # the konsoliderad page IS the consolidated text, so it is stored verbatim
    # as HTML -- what `parse.parse_consolidation_html` reads
    assert record["files"]["consolidation"] == [{
        "name": "sosfs-2013-1-consolidation-0.html",
        "url": "https://www.socialstyrelsen.se/k/20131-om-ekonomiskt-bistand/"}]
    assert [a["identifier"] for a in record["files"]["amendment"]] == [
        "SOSFS 2013:26", "HSLF-FS 2017:10", "HSLF-FS 2020:61",
        "HSLF-FS 2021:31", "HSLF-FS 2025:25"]
    stored = compress.read_json(record_path(str(tmp_path), "sosfs", "sosfs/2013:1"))
    assert stored == record
    assert "Ändrad: t.o.m. HSLF-FS 2025:25" in compress.read_text(
        tmp_path / "sosfs" / "sosfs-2013-1-consolidation-0.html")
    assert (tmp_path / "sosfs" / "sosfs-2013-1-regulation.pdf").read_bytes() \
        == b"%PDF-1.6\nfixture"


def test_resolve_page_rejects_a_body_that_is_not_a_pdf(tmp_path, monkeypatch):
    agency = REGISTRY["hslffs-sos"]
    served = {"https://x.invalid/p/": '<a class="publication-wrapper__main-doc" '
                                      'href="/f.pdf">Öppna</a>',
              "https://x.invalid/f.pdf": "<html>Access denied</html>"}
    serve = (lambda session, method, url, **kw: types.SimpleNamespace(
        text=served[url], content=served[url].encode("utf-8"), status_code=200))
    monkeypatch.setattr(hslffs, "request", serve)
    monkeypatch.setattr(harvest, "request", serve)
    rejects = []
    ref = hslffs.DocRef(basefile="hslffs/2026:1", fs="hslffs",
                        identifier="HSLF-FS 2026:1", url="https://x.invalid/p/",
                        extra={"consolidations": []})
    record = hslffs.resolve_page(None, agency, ref, str(tmp_path),
                                 log=lambda _m: None, rejects=rejects)
    assert record["files"]["regulation"] is None
    assert len(rejects) == 1 and "non-PDF" in rejects[0]


def test_resolve_page_forwards_to_the_reader_before_storing_it(tmp_path, monkeypatch):
    agency = REGISTRY["hslffs-fohm"]
    listed = ("https://www.folkhalsomyndigheten.se/publikationer-och-material/"
              "publikationsarkiv/k/konsoliderad-version-av-hslf-fs-2025-14-"
              "folkhalsomyndighetens-foreskrifter-och-allmanna-rad/")
    served = {
        "https://www.folkhalsomyndigheten.se/p/hslf-fs-2025-14/":
            '<a class="link file pdf" href="/contentassets/x/hslf-fs-2025-05.pdf">'
            'Folkhälsomyndighetens föreskrifter</a>',
        "https://www.folkhalsomyndigheten.se/contentassets/x/hslf-fs-2025-05.pdf":
            "%PDF-1.6\nfixture",
        listed: (FILES / "fohm-konsoliderad-hslffs-2025-14.html").read_text("utf-8"),
        listed + "?pub=157921":
            (FILES / "fohm-reader-hslffs-2025-14.html").read_text("utf-8"),
    }
    asked = []

    def serve(session, method, url, **kwargs):
        asked.append(url)
        body = served[url]
        return types.SimpleNamespace(text=body, content=body.encode("utf-8"),
                                     status_code=200)

    monkeypatch.setattr(hslffs, "request", serve)
    monkeypatch.setattr(harvest, "request", serve)
    monkeypatch.setattr(hslffs.time, "sleep", lambda _s: None)
    ref = hslffs.DocRef(
        basefile="hslffs/2025:14", fs="hslffs", identifier="HSLF-FS 2025:14",
        url="https://www.folkhalsomyndigheten.se/p/hslf-fs-2025-14/",
        title="Folkhälsomyndighetens föreskrifter och allmänna råd om "
              "gårdsförsäljning HSLF-FS 2025:14",
        extra={"consolidations": [{"url": listed}]})
    record = hslffs.resolve_page(None, agency, ref, str(tmp_path), rejects=[])

    assert asked[-2:] == [listed, listed + "?pub=157921"]
    # the base act's own PDF is what the publication page publishes; the
    # consolidation is the reader page, stored as HTML under the view it holds
    assert record["files"]["regulation"]["name"] == "hslffs-2025-14-regulation.pdf"
    assert record["files"]["consolidation"] == [{
        "name": "hslffs-2025-14-consolidation-0.html",
        "url": listed + "?pub=157921"}]
    assert [a["identifier"] for a in record["files"]["amendment"]] == \
        ["HSLF-FS 2026:13"]
    assert "Senaste lydelse gäller från och med" in compress.read_text(
        tmp_path / "hslffs" / "hslffs-2025-14-consolidation-0.html")


def test_the_stored_reader_parses_as_a_consolidation():
    # the same HTML route Socialstyrelsen's konsoliderad pages take: the
    # reader's "Senaste lydelse gäller …" line is the cutoff, and its closing
    # register of printed PDFs is not text of the act
    structure, notes, tom, refs = parse.parse_consolidation_html(
        FILES / "fohm-reader-hslffs-2025-14.html",
        parse.sfs_parser("foreskrift", parse.PARSE_TYPES,
                         written=approximate_date("2026")))
    assert tom == "https://lagen.nu/hslffs/2026:13"
    assert refs == [("HSLF-FS", "2026", "13")]
    assert notes == []
    assert [node["id"] for node in structure] == ["P1", "P2"]
    assert not any("Tryckta versioner" in str(node) for node in structure)
    assert not any("Detta är den senaste" in str(node) for node in structure)


# --------------------------------------------------------------------------
# the harvest keeps one watermark per scope
# --------------------------------------------------------------------------

def test_each_scope_keeps_its_own_watermark(tmp_path, monkeypatch):
    calls = []

    def fake_walk(items, **kwargs):
        calls.append(kwargs["scope"])
        return types.SimpleNamespace(seen=0, new=0, errors=0, skips=0)

    monkeypatch.setattr(harvest, "walk", fake_walk)
    for scope in ("hslffs-sos", "hslffs-ivo"):
        (tmp_path / "hslffs").mkdir(exist_ok=True)
        (tmp_path / "hslffs" / (".watermark-%s.json" % scope)).write_text(
            json.dumps({"last_harvest": "2026-01-01"}))
        harvest._harvest_session(REGISTRY[scope], str(tmp_path),
                                 types.SimpleNamespace(), False, None, None,
                                 0.0, lambda _m: None)
    assert calls == ["hslffs-sos", "hslffs-ivo"]
    # a samling one agency owns keeps the unsuffixed name it already has on disk
    assert not (tmp_path / "hslffs" / ".watermark.json").exists()
