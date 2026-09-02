"""rs vertical (myndigheternas rättsliga ställningstaganden): identity, the seven
listing readers, page-1 header extraction, body classification, artifact
projection and the layout/catalog/facets wiring.

Hermetic: the fixtures under ``test/files/rs/`` are trimmed captures of the live
2026 pages and of ``pdf_first_page_text`` over the real PDFs, so the rules are
exercised against what the agencies actually publish without network or poppler.
"""

import functools
import json
import sqlite3
from pathlib import Path

import pytest
import requests

from ferenda.lib import (
    catalog,
    catalog_rows,
    compress,
    facets,
    labels,
    layout,
    page,
    util,
)
from ferenda.lib import browser, harvest
from ferenda.lib.pdftext import Para, classify_letterhead
from ferenda.lib.util import record_path
from ferenda.rs import download as rs_download
from ferenda.rs import parse as rs_parse
from ferenda.rs import source as rs_source
from ferenda.rs import render as rs_render
from ferenda.rs import skv
from ferenda.rs.agencies import (
    BROWSER_ORGS,
    BY_ORG,
    DEFAULT_ORGS,
    ORGS,
    REGISTRY,
    number_slug,
)
from ferenda.rs.model import (
    Block,
    Stallningstagande,
    rs_designation,
    rs_identifier,
    rs_uri,
)

FIXTURES = Path(__file__).parent / "files" / "rs"


def fixture(name):
    return (FIXTURES / name).read_text("utf-8")


# --------------------------------------------------------------------------
# identity -- the agency's own number names the document
# --------------------------------------------------------------------------

@pytest.mark.parametrize("org,nummer,uri,identifier", [
    ("imy", "2024:1", "https://lagen.nu/rs/imy/2024:1", "IMYRS 2024:1"),
    ("fk", "2025:01", "https://lagen.nu/rs/fk/2025:01", "FKRS 2025:01"),
    ("fi", "2026:1", "https://lagen.nu/rs/fi/2026:1",
     "FI:s rättsliga ställningstagande 2026:1"),
    ("kkv", "2025:1", "https://lagen.nu/rs/kkv/2025:1",
     "Konkurrensverkets ställningstagande 2025:1"),
    # the slash-separated numbers reduce to a path segment; the colon stays,
    # since the whole site already spells an author's number with one
    ("kfm", "1/23/VER", "https://lagen.nu/rs/kfm/1-23-VER",
     "Kronofogdens ställningstagande 1/23/VER"),
    ("migr", "RS/028/2021", "https://lagen.nu/rs/migr/RS-028-2021",
     "RS/028/2021"),
    # Skatteverket numbers no series, so its own designation is the dnr -- and
    # the pre-2020 dnr form reduces to a path segment the same way
    ("skv", "8-193984-2026", "https://lagen.nu/rs/skv/8-193984-2026",
     "Skatteverkets ställningstagande dnr 8-193984-2026"),
    ("skv", "131 297826-13/111", "https://lagen.nu/rs/skv/131-297826-13-111",
     "Skatteverkets ställningstagande dnr 131 297826-13/111"),
])
def test_identity(org, nummer, uri, identifier):
    assert rs_uri(org, nummer) == uri
    assert rs_identifier(org, nummer) == identifier


def test_number_slug_leaves_the_agency_number_readable():
    assert number_slug("2025:01") == "2025:01"
    assert number_slug("7/16/Skusan") == "7-16-Skusan"


def test_registry_is_one_entry_per_agency():
    assert len(REGISTRY) == len(ORGS) == len(BY_ORG) == 7
    # every agency has a citable designation and a listing to walk
    for agency in REGISTRY:
        assert "%s" in agency.identifier
        assert agency.listing.startswith("https://")


def test_skatteverket_is_kept_off_the_default_sweep():
    """Its transport is one headful Chrome on the process-global DISPLAY, so it
    cannot share a run with the HTTP agencies -- the föreskrift rule."""
    assert BROWSER_ORGS == ("skv",)
    assert set(DEFAULT_ORGS) | set(BROWSER_ORGS) == set(ORGS)
    assert "skv" not in DEFAULT_ORGS


@pytest.mark.parametrize("basefile,suffix", [
    ("fk/2025:01", "fk-2025-01.pdf"),
    # the one agency whose document is the page itself
    ("skv/8-193984-2026", "skv-8-193984-2026.html"),
])
def test_body_path_follows_what_the_agency_publishes(basefile, suffix):
    assert rs_download.body_path("/root", basefile).name == suffix


# --------------------------------------------------------------------------
# the seven listing readers
# --------------------------------------------------------------------------

def test_imy_listing():
    items = rs_download.imy_parse_listing(fixture("imy-listing.html"))
    assert [i["nummer"] for i in items] == ["2024:1", "2022:3"]
    assert items[0]["titel"] == "Klagomål mot söktjänster med utgivningsbevis"
    assert items[0]["url"].startswith("https://www.imy.se/link/")


def test_imy_page_summary_stops_at_the_series_boilerplate():
    page = rs_download.imy_parse_page(fixture("imy-page.html"),
                                      "https://www.imy.se/publikationer/x/")
    # IMY's own account of the statement, and nothing from the "Om IMY:s
    # rättsliga ställningstaganden" block that closes every publication page
    assert page["sammanfattning"].startswith("Hittills har IMY bedömt")
    assert "IMY:s rättsliga ställningstaganden innehåller" \
        not in page["sammanfattning"]
    assert page["dokument_url"].endswith(".pdf")


def test_fi_listing_reads_the_status_column():
    items = rs_download.fi_parse_listing(fixture("fi-listing.html"))
    assert [i["nummer"] for i in items] == ["2026:1", "2025:1", "2024:2",
                                            "2024:1", "2023:1", "2022:1",
                                            "2021:1"]
    assert items[0] == {"nummer": "2026:1",
                        "titel": "Tillståndsplikt vid garantigivning",
                        "beslutsdatum": "2026-06-25", "status": "gällande",
                        "dokument_url": "https://www.fi.se/contentassets/"
                        "323025d4553b41378cddb5ff9ea0cee7/rattsligt-"
                        "stallningstagande-tillstandsplikt-vid-garantigivning.pdf"}


def test_fk_listing_splits_number_from_title():
    items = rs_download.fk_parse_listing(fixture("fk-listing.html"))
    assert items[0]["arsgrupp"] == "2026"
    # the listing's own (mistyped) number; the PDF's Serienummer overrides it
    assert items[0]["nummer"] == "2026:03"
    assert items[0]["titel"] == ("Sjukpenninggrundande inkomst och statlig "
                                 "jobbpremie")
    # a single-digit löpnummer is padded, so 2025:9 and 2025:09 are one document
    assert "2025:09" in [i["nummer"] for i in items]


def test_kfm_listing_reports_what_it_cannot_file():
    items, unnumbered = rs_download.kfm_parse_listing(fixture("kfm-listing.html"))
    assert [i["nummer"] for i in items[:2]] == ["1/24", "1/23/VER"]
    assert items[0]["titel"] == "Buffert i skuldsaneringsärenden"
    # the year heading is the site's grouping, not the number's year
    assert items[1]["arsgrupp"] == "2023"
    # an entry with no number has no identity to be filed under
    assert unnumbered == ["Förslag på konkursförvaltare"]


def test_kkv_listing_lifts_the_fate_out_of_the_title():
    items = {i["nummer"]: i for i in
             rs_download.kkv_parse_listing(fixture("kkv-listing.html"))}
    assert items["2025:1"]["status"] == "gällande"
    assert items["2025:1"]["url"].startswith("https://www.konkurrensverket.se/")
    # "(upphävt 20 oktober 2025)" -- withdrawn, no successor named, no document
    assert items["2023:3"]["status"] == "upphävt"
    assert items["2023:3"]["upphavd"] == "20 oktober 2025"
    assert items["2023:3"]["url"] is None
    assert items["2023:3"]["titel"] == ("Ändringsbestämmelserna tillämplighet "
                                        "på otillåtet direktupphandlade avtal")
    # "(upphävt genom 2022:2)" -- and the successor's own "(ersätter 2019:1)"
    assert items["2019:1"]["ersatt_av"] == "2022:2"
    assert items["2022:2"]["ersatter"] == "2019:1"
    assert items["2022:2"]["titel"] == ("Maximal omfattning av ramavtal och "
                                        "öppenhetsprincipen")


def test_kkv_page():
    page = rs_download.kkv_parse_page(fixture("kkv-page.html"),
                                      "https://www.konkurrensverket.se/x/")
    assert page["beslutsdatum"] == "2025-05-28"
    # the ingress is the lead paragraph, not the "Konkurrensverkets
    # ställningstagande 2025:1" subtitle above it
    assert page["sammanfattning"].startswith("I januari 2024 fick")
    assert "/globalassets/" in page["dokument_url"]


def test_migr_results_deduplicate_the_double_linked_hits():
    ids = rs_download.migr_parse_results(fixture("lifos-results.html"))
    assert ids == ["45276", "44385", "50086", "50073"]


def test_migr_document():
    doc = rs_download.migr_parse_document(fixture("lifos-document.html"))
    assert doc["nummer"] == "RS/028/2021"
    assert doc["doktyp"] == "stallningstagande"
    assert doc["version"] == "3.0"
    # an in-place revision leaves only the date of the text it replaced
    assert doc["foregaende_version"] == "2021-02-28"
    assert doc["beslutsdatum"] == "2026-06-12"
    assert doc["dokumentnr"] == "50086"
    # the heading's framing is carried as fields, so the title is the subject
    assert doc["titel"].startswith("Hantering av återkallande")
    assert "RS/028/2021" not in doc["titel"]
    # the subject word the harvest itself filtered on is not a keyword
    assert doc["nyckelord"] == ["Verkställighet", "Praxis", "Avvisning",
                                "Lagstiftning", "Utlänningslag"]
    assert "documentAttachmentId=51321" in doc["dokument_url"]


def test_migr_document_without_a_number_in_the_index():
    """Two of the 104 Lifos entries state no RS/RK number; the page still says
    which kind of document it is, and `migr_sync` reads the number out of the
    PDF -- the Försäkringskassan route."""
    doc = rs_download.migr_parse_document(fixture("lifos-document-unnumbered.html"))
    assert doc["nummer"] is None
    assert doc["doktyp"] == "kommentar"
    assert doc["titel"] == "EU-domstolens dom C-19/21"
    assert doc["dokumentnr"] == "50079"
    # what the PDF prints is what the record is then filed under
    assert rs_download.migr_number(
        "RÄTTSLIG KOMMENTAR EU-domstolens dom C-19/21 RK/001/2023 Från och med"
    ) == "RK/001/2023"
    assert rs_download.migr_number("ingen beteckning här") is None


def test_migr_current_keeps_the_revision_not_the_superseded_entry():
    """Lifos keeps a superseded entry in its index beside the revision that
    replaced it, under the same number. One number is one document, so the later
    beslutsdatum wins -- the search is relevance-ordered, so taking whichever
    came last would pick between them at random."""
    superseded = {"nummer": "RS/021/2020", "version": "2.0",
                  "beslutsdatum": "2023-02-10", "dokumentnr": "47169"}
    revision = {"nummer": "RS/021/2020", "version": "4.0",
                "beslutsdatum": "2026-07-12", "dokumentnr": "50155"}
    other = {"nummer": "RS/028/2021", "version": "3.0",
             "beslutsdatum": "2026-06-12", "dokumentnr": "50086"}
    for order in ([superseded, revision, other], [revision, superseded, other]):
        current = {r["nummer"]: r for r in rs_download.migr_current(order)}
        assert len(current) == 2
        assert current["RS/021/2020"]["version"] == "4.0"


def test_fi_status_maps_onto_the_model_vocabulary():
    """FI's Status column is the only place a remote string decides whether a
    document reads as the agency's current position, so it is mapped rather than
    passed through -- and an unknown word stops the harvest instead of silently
    defaulting to "gällande", which would publish a withdrawn statement as
    current."""
    assert rs_download.fi_status("Gällande") == "gällande"
    assert rs_download.fi_status("Upphävt") == "upphävt"
    assert rs_download.fi_status("Upphävd") == "upphävt"
    with pytest.raises(ValueError, match="unknown Status"):
        rs_download.fi_status("Upphävt 2027-01-01")


def test_fi_listing_carries_a_withdrawal_through_to_the_model():
    """FI publishes no withdrawn ställningstagande today, so the repealed row is
    constructed from its own table -- but the path from the Status column to the
    banner and the citation label is what has to hold, and nothing else exercises
    it (the live fixture is seven Gällande rows)."""
    repealed = fixture("fi-listing.html").replace(
        "<td style=\"width: 8.7771%;\">Gällande</td>",
        "<td style=\"width: 8.7771%;\">Upphävt</td>", 1)
    items = rs_download.fi_parse_listing(repealed)
    assert items[0]["status"] == "upphävt"
    assert [i["status"] for i in items[1:]] == ["gällande"] * 6
    art = artifact(org="fi", nummer=items[0]["nummer"], titel=items[0]["titel"],
                   status=items[0]["status"])
    assert art["metadata"]["status"] == "upphävt"
    assert labels.document_labels("rs", art).descriptive_label.endswith("(upphävt)")


def test_swedish_date_comes_from_lib():
    # the rs vertical uses lib.util.swedish_date rather than a copy of its own
    assert not hasattr(rs_download, "SWEDISH_MONTHS")
    assert rs_download.swedish_date is util.swedish_date
    assert rs_download.swedish_date("28 maj 2025") == "2025-05-28"


# --------------------------------------------------------------------------
# Skatteverket -- the register, and the pages that are the documents
# --------------------------------------------------------------------------

def skv_register():
    return skv.parse_index(fixture("skv-register.html"))


def test_skv_register_states_identity_date_and_omraden():
    """The register carries everything but the text: the dnr that names the
    document, its own date (not the day rättslig vägledning published it), and
    the top-level områden its taxonomy path is filed under."""
    records, _unidentified = skv_register()
    live = next(r for r in records if r["basefile"] == "skv/8-492402")
    assert live["nummer"] == live["diarienummer"] == "8-492402"
    assert live["titel"].startswith("Befrielse från betalningsskyldigheten")
    # the register lists this one under 2020-10-05, the day it went up; the
    # ställningstagande's own date, which its page prints as Datum, is earlier
    assert live["beslutsdatum"] == "2020-09-29"
    assert live["source_url"].endswith("/385060.html?date=2020-10-05")
    assert live["nyckelord"] == ["Mervärdesskatt",
                                 "Skattebetalning & borgenärsarbete"]


def test_skv_register_states_the_withdrawal():
    """A closed validity window is the only place the register says a position
    no longer applies -- there is no status column."""
    records, _ = skv_register()
    withdrawn = next(r for r in records if r["basefile"] == "skv/8-1740076")
    assert withdrawn["status"] == "upphävt"
    assert withdrawn["upphavd"] == "2026-07-06"


def test_skv_a_withdrawal_notice_is_not_itself_withdrawn():
    """Skatteverket publishes "X ska inte längre tillämpas" as a document whose
    validity is the single day it was issued. Reading that zero-length window as
    a withdrawal would say the agency withdrew its own withdrawal notice."""
    records, _ = skv_register()
    notice = next(r for r in records if r["basefile"] == "skv/8-207888-2026")
    assert notice["titel"].endswith("ska inte längre tillämpas")
    assert notice["status"] == "gällande"
    assert notice["upphavd"] is None


@pytest.mark.parametrize("ref_id,nummer", [
    ("skatteverket 8-193984-2026", "8-193984-2026"),
    ("skatteverket 8-492402", "8-492402"),
    # the register mistypes a handful of ids, each still a readable dnr: a
    # capitalised issuer, a lost issuer, and an en dash for the year's hyphen
    ("Skatteverket 131 253470-14/111", "131 253470-14/111"),
    ("131 472246-16/111", "131 472246-16/111"),
    ("skatteverket 131 576809–13", "131 576809-13"),
    ("skatteverket 130 237238-5/111", "130 237238-5/111"),
    # and a few name no dnr at all
    ("skatteverket 1998-08-24", None),
    ("Test", None),
    ("", None),
])
def test_skv_dnr_is_read_where_there_is_one(ref_id, nummer):
    assert skv.dnr(ref_id) == nummer


def test_skv_register_reports_the_entries_it_cannot_file():
    """A pre-2000 RSV-skrivelse the register keys on its date, and a stray test
    page, have no identity to be filed under. Neither is invented, and neither
    is dropped silently."""
    records, unidentified = skv_register()
    assert len(records) == 8
    assert len(unidentified) == 2
    assert any("1998-08-24" in entry for entry in unidentified)
    assert any("Test" in entry for entry in unidentified)


def test_skv_register_refuses_two_entries_claiming_one_dnr():
    """The dnr is the identity, so a collision would file one document over the
    other with no trace."""
    doubled = fixture("skv-register.html").replace(
        '"refId": "skatteverket 8-492402"', '"refId": "skatteverket 8-1740076"')
    # a raise, not an assert: under `python -O` an assert would strip and one
    # document would quietly overwrite the other (rule:errors-drive-retry-use-raise)
    with pytest.raises(ValueError, match="8-1740076 twice"):
        skv.parse_index(doubled)


def test_skv_page_is_read_as_headings_paragraphs_and_notes():
    html = fixture("skv-page.html")
    assert skv.page_metadata(html) == {
        "Områden": "Mervärdesskatt, Skattebetalning & borgenärsarbete",
        "Datum": "2020-09-29", "Dnr": "8-492402"}
    blocks, notes = skv.page_body(html)
    assert blocks[0] == ("rubrik", "1 Sammanfattning", 1, [], False)
    assert [b[1] for b in blocks if b[0] == "rubrik"] == [
        "1 Sammanfattning", "2 Frågeställning", "3 Gällande rätt m.m.",
        "4 Bedömning", "Tillämpningsinformation"]
    # the notes are lifted out of the body under their own heading, each keeping
    # the marker the running text set as a superscript
    assert notes == [("1", "Bostad med särskild service för vuxna enligt lagen "
                      "(1993:387) om stöd och service till vissa "
                      "funktionshindrade.")]
    assert not any("Fotnot" in b[1] for b in blocks)
    # the rule the editor sets above the notes is a separator, not a stycke
    assert not any(b[1].startswith("___") for b in blocks)


def test_skv_page_names_what_it_replaced():
    assert skv.page_relations(fixture("skv-page.html")) == {
        "ersatt_av": None, "ersatter": "8-346749"}


def test_skv_page_names_what_replaced_it():
    """A withdrawn position carries Skatteverkets own dated note, which names
    the replacement with a marked-up reference to its dnr. The note stays in the
    body as well: it is published text about this position, and it says things
    no field carries."""
    html = fixture("skv-page-upphavd.html")
    assert skv.page_relations(html)["ersatt_av"] == "8-207888-2026"
    blocks, _notes = skv.page_body(html)
    assert blocks[0] == ("stycke", "Nytt: 2026-07-06", 1, [], False)
    assert blocks[1][1].startswith("Detta ställningstagande ska inte längre "
                                   "tillämpas.")


# --------------------------------------------------------------------------
# page-1 headers -- read by label anchor + value shape, over both flattenings
# --------------------------------------------------------------------------

@pytest.mark.parametrize("fixture_name,nummer", [
    # "Diarienummer Beslutsdatum Serienummer | FK 2026/004799 2026-04-02 2026:01"
    ("header-fk-3col.txt", "2026:01"),
    # "Beslutsdatum Vår beteckning | 2025-01-29 Dnr 2024/022038 | Serienummer …"
    ("header-fk-2col.txt", "2025:01"),
    ("header-fk-legacy.txt", "2016:09"),
])
def test_fk_serienummer_reads_both_column_flattenings(fixture_name, nummer):
    assert rs_download.fk_serienummer(fixture(fixture_name)) == nummer


def test_fk_serienummer_beats_a_mistyped_listing():
    # the listing calls this one 2026:03; the document names itself 2026:01
    assert rs_download.fk_serienummer(fixture("header-fk-3col.txt")) == "2026:01"


@pytest.mark.parametrize("org,fixture_name,expected", [
    ("fk", "header-fk-3col.txt",
     {"beslutsdatum": "2026-04-02", "diarienummer": "FK 2026/004799"}),
    ("fk", "header-fk-legacy.txt",
     {"beslutsdatum": "2016-10-07", "diarienummer": "52394-2016"}),
    ("fi", "header-fi.txt", {"diarienummer": "25-31002"}),
    ("imy", "header-imy.txt", {"beslutsdatum": "2024-05-14"}),
    # both Kronofogden templates state a Beslutsdatum, the older one below the
    # administrative block and beside a "Gäller från och med" date it is not
    ("kfm", "header-kfm-new.txt",
     {"beslutsdatum": "2024-05-06", "diarienummer": "KFM 5108-2024"}),
    ("kfm", "header-kfm-old.txt",
     {"beslutsdatum": "2023-10-23", "diarienummer": "KFM 21872-2022"}),
    ("kkv", "header-kkv.txt", {"diarienummer": "122/2024"}),
    # Lifos dates the record; the PDF dates the document, and they part company
    # whenever a ställningstagande is revised in place
    ("migr", "header-migr.txt", {"beslutsdatum": "2025-02-28"}),
])
def test_headers(org, fixture_name, expected):
    fields = rs_parse.READERS[org].header(fixture(fixture_name))
    assert {k: v for k, v in fields.items() if v} == expected


# --------------------------------------------------------------------------
# body classification
# --------------------------------------------------------------------------

def paras(*specs):
    return [Para(text=t, bold=b, size=s) for t, b, s in specs]


def test_letterhead_reads_headings_by_weight():
    blocks = classify_letterhead(paras(
        ("Diarienummer:", False, 14), ("IMY-2024-2904", False, 14),
        ("Sammanfattning", True, 18), ("Den granskade har brutit mot artikel 5.",
                                       False, 14),
        ("1 (7)", False, 14),
        ("Postadress: Box 8114 och prosan fortsätter", False, 14),
    ), rs_parse.RE_IMY_MARGIN, rs_parse.RE_IMY_MASTHEAD)
    assert blocks == [("rubrik", "Sammanfattning", 1),
                      ("stycke", "Den granskade har brutit mot artikel 5.", 0),
                      # the masthead is removed *in place*, so the body line it
                      # was glued onto survives
                      ("stycke", "och prosan fortsätter", 0)]


def test_letterhead_reads_headings_by_size_where_nothing_is_bold():
    # Finansinspektionen and Migrationsverket mark sections by size alone: the
    # body runs 18, the headings 24, and nothing in the document is bold
    specs = paras(("Rättsligt ställningstagande 2025:1", True, 18),
                  ("Sammanfattning", False, 24),
                  ("En sådan säkerhetsskyddschef som avses i 2 kap.", False, 18),
                  ("Ställningstagandet riktar sig till verksamhetsutövare.",
                   False, 18),
                  ("Bedömning", False, 24),
                  ("Att säkerhetsskyddschefen ska leda arbetet följer av lagen.",
                   False, 18))
    # read by weight, the size-24 headings are indistinguishable from prose
    assert [k for k, _t, _l in classify_letterhead(
        specs, rs_parse.RE_FI_MARGIN, rs_parse.RE_FI_MASTHEAD)] == ["stycke"] * 5
    assert classify_letterhead(specs, rs_parse.RE_FI_MARGIN,
                               rs_parse.RE_FI_MASTHEAD, by_size=True) == [
        ("rubrik", "Sammanfattning", 1),
        ("stycke", "En sådan säkerhetsskyddschef som avses i 2 kap.", 0),
        ("stycke", "Ställningstagandet riktar sig till verksamhetsutövare.", 0),
        ("rubrik", "Bedömning", 1),
        ("stycke", "Att säkerhetsskyddschefen ska leda arbetet följer av lagen.",
         0)]


@pytest.mark.parametrize("org,prose", [
    # every Reader's margin and masthead run against body prose that opens on,
    # or ends with, the words the letterhead uses -- the shape that silently
    # deletes a paragraph or a word from the published text
    ("imy", "Datum för publicering av beslutet saknar betydelse här."),
    ("fi", "Ställningstagandet riktar sig till verksamhetsutövare inom "
           "Finansinspektionen"),
    ("fk", "Beslutet i ärendet har fattats av Försäkringskassan"),
    ("kfm", "Beslutsdatum är inte avgörande för frågan om verkställighet."),
    ("migr", "Gäller för utlänningar som har fått avslag."),
    ("migr", "Bedömningen görs av Migrationsverket"),
    ("kkv", "Datum och diarienummer framgår av beslutet."),
])
def test_letterhead_patterns_leave_body_prose_intact(org, prose):
    reader = rs_parse.READERS[org]
    blocks = classify_letterhead(paras((prose, False, 14)),
                                 reader.margin, reader.masthead,
                                 by_size=reader.by_size)
    assert blocks == [("stycke", prose, 0)], (
        "%s's letterhead patterns altered body prose" % org)


@pytest.mark.parametrize("org,letterhead", [
    ("imy", ["Diarienummer:", "IMY-2024-2904", "Datum:", "2024-05-14"]),
    ("fi", ["Rättsligt ställningstagande 2025:1", "Datum", "2025-12-10",
            "FI dnr 25-31002"]),
    ("fk", ["RÄTTSLIGT STÄLLNINGSTAGANDE", "Beslutsdatum", "Serienummer",
            "2026:01", "2026-04-02", "FK 2026/004799"]),
    ("kfm", ["Kronofogdemyndighetens", "Infoklass: 1", "Nr", "1/23/VER",
             "Diarienummer:", "KFM 21872-2022", "2023-10-23"]),
    ("migr", ["Fastställelsebeslut: RA/058/2021 Version 1.0",
              "Beslutsdatum: 2025-02-28 Gäller för: hela myndigheten",
              "RS/028/2021"]),
    ("kkv", ["STÄLLNINGSTAGANDE 2025:1", "Dnr 122/2024", "2025-05-28"]),
])
def test_letterhead_patterns_remove_the_margin_block(org, letterhead):
    reader = rs_parse.READERS[org]
    blocks = classify_letterhead(
        paras(*[(line, False, 14) for line in letterhead]),
        reader.margin, reader.masthead, by_size=reader.by_size)
    assert blocks == [], "%s kept margin lines %s" % (
        org, [t for _k, t, _l in blocks])


def test_front_matter_drops_the_caption_and_the_repeated_title():
    blocks = [Block("stycke", "RÄTTSLIGT STÄLLNINGSTAGANDE"),
              Block("rubrik", "2025:1 Intern kontroll över säkerhetsskyddschefen"),
              Block("rubrik", "Sammanfattning"),
              Block("stycke", "Intern kontroll över säkerhetsskyddschefen "
                    "behandlas i avsnitt 3.")]
    kept = rs_parse.drop_front_matter(
        blocks, "Intern kontroll över säkerhetsskyddschefen")
    # only leading blocks go -- a later mention of the title is real prose
    assert [b.text for b in kept] == [
        "Sammanfattning",
        "Intern kontroll över säkerhetsskyddschefen behandlas i avsnitt 3."]


def test_front_matter_drops_a_truncated_title_echo():
    """Regression (RS/041/2021 and four more migr documents): the PDF's title
    line breaks early, so the printed copy is only the *start* of the title --
    the old ends-with test kept it and the page opened by repeating its own
    heading. The rule shared with edpb catches the truncated shape."""
    blocks = [Block("rubrik", "Kontroll av förvarsbesluts giltighet"),
              Block("stycke", "Ett beslut om förvar ska prövas på nytt.")]
    kept = rs_parse.drop_front_matter(
        blocks, "Kontroll av förvarsbesluts giltighetstid")
    assert [b.text for b in kept] == [
        "Ett beslut om förvar ska prövas på nytt."]


# --------------------------------------------------------------------------
# artifact projection
# --------------------------------------------------------------------------

class _Scanner:
    def parse_text(self, text, context):
        return []


def artifact(**kwargs):
    return Stallningstagande(**kwargs).to_artifact(_Scanner())


def test_artifact_shape():
    art = artifact(org="fk", nummer="2025:01", titel="Underhållsstöd",
                   beslutsdatum="2025-01-29", diarienummer="Dnr 2024/022038",
                   body=[Block("rubrik", "Bakgrund"),
                         Block("stycke", "Frågan är om …")])
    assert art["uri"] == "https://lagen.nu/rs/fk/2025:01"
    assert art["type"] == "stallningstagande"
    assert art["identifier"] == "FKRS 2025:01"
    assert art["metadata"]["publisher"] == "Försäkringskassan"
    assert art["metadata"]["status"] == "gällande"
    assert [n["type"] for n in art["structure"]] == ["rubrik", "stycke"]
    # every stycke is an anchor-bearing node, as in every other source
    assert art["structure"][1]["id"] == "S1"


def test_withdrawn_artifact_states_its_fate():
    art = artifact(org="kkv", nummer="2019:1", titel="Ramavtalsupphandlingar",
                   status="upphävt", ersatt_av="2022:2")
    assert art["metadata"]["status"] == "upphävt"
    assert art["metadata"]["ersattAv"] == "2022:2"
    # a withdrawn statement is still named by its own id wherever it is cited,
    # with the withdrawal said out loud
    assert labels.document_labels("rs", art).descriptive_label \
        == "Konkurrensverkets ställningstagande 2019:1 (upphävt)"


def test_absent_fields_stay_out_of_the_metadata():
    art = artifact(org="imy", nummer="2021:1", titel="Begreppet personuppgifter")
    assert set(art["metadata"]) == {"title", "publisher", "nummer", "status"}
    assert "sammanfattning" not in art
    assert "source_url" not in art


# --------------------------------------------------------------------------
# corpus wiring -- layout paths, catalog row, facet buckets
# --------------------------------------------------------------------------

@pytest.mark.parametrize("basefile,artifact_rel,page", [
    ("fk/2025:01", "fk/2025-01.json", "rs/fk_2025:01.html"),
    ("kfm/1-23-VER", "kfm/1-23-VER.json", "rs/kfm_1-23-VER.html"),
    ("migr/RS-028-2021", "migr/RS-028-2021.json", "rs/migr_RS-028-2021.html"),
])
def test_layout_grammar(basefile, artifact_rel, page):
    assert layout.artifact("rs", basefile) == layout.artifact_dir("rs") / artifact_rel
    uri = rs_uri(basefile.split("/")[0], basefile.split("/", 1)[1])
    assert layout.page_relpath(uri) == page
    assert layout.url_to_relpath(layout.page_url(uri)) == page


def test_catalog_row():
    art = artifact(org="migr", nummer="RS/028/2021", titel="Hantering av "
                   "återkallande")
    uri, source, kind, label, title, path = catalog_rows.document_row(
        art, "/x.json", "rs")
    assert (source, kind, label) == ("rs", "migr", "RS/028/2021")
    assert title == "Hantering av återkallande"


@pytest.mark.parametrize("local,date,year", [
    # the beslutsdatum wins where the document states one -- an in-place
    # revision belongs under the year its current version was fastställd
    ("rs/migr/RS-028-2021", "2026-06-12", "2026"),
    # else the agency's own number carries the year, in its own shape
    ("rs/migr/RS-028-2021", None, "2021"),
    ("rs/fk/2025-01", None, "2025"),
    ("rs/kfm/1-23-VER", None, "2023"),
    ("rs/kfm/1-24", None, "2024"),
])
def test_facet_year(local, date, year):
    row = facets.Row(uri="https://lagen.nu/" + local, local=local, kind="x",
                     label="x", title="x", display="x", date=date)
    assert facets.SCHEMES["rs"][1].key(row) == year


def test_facet_agencies_cover_the_registry():
    myndighet = facets.SCHEMES["rs"][0]
    for org in ORGS:
        row = facets.Row(uri="u", local="rs/%s/1" % org, kind=org, label="x",
                         title="x", display="x", date=None)
        # every agency has a curated label -- none falls back to its bare code
        assert myndighet.label(myndighet.key(row)) != org


# --------------------------------------------------------------------------
# end to end -- a stored record parses to the artifact the catalog files
# --------------------------------------------------------------------------

class _ServesHtml:
    """An agency serving an error page under a .pdf name -- what a CMS does
    when an asset moves."""

    def request(self, _method, _url, **_kwargs):
        response = requests.Response()
        response.status_code = 200
        response._content = b"<!doctype html><html><body>Sidan finns inte</body></html>"
        response.url = "https://example.invalid/x.pdf"
        return response


def test_a_failed_document_fetch_leaves_no_record(tmp_path, capsys):
    """A stored record is the assertion that its document is on disk -- that is
    what lets `parse.body` read an absent PDF as "the agency published none"
    rather than "the fetch broke". So a failed fetch writes nothing, reports it,
    and the next run retries."""
    record = {"basefile": "fi/2026:1", "org": "fi", "nummer": "2026:1",
              "titel": "Tillståndsplikt vid garantigivning", "status": "gällande",
              "dokument_url": "https://www.fi.se/contentassets/x.pdf"}
    seen, new = rs_download._walk(tmp_path, [record], _ServesHtml(), 0, False,
                                  None, "fi")
    assert (seen, new) == (1, 0)
    assert not compress.exists(record_path(tmp_path, "fi", "fi/2026:1"))
    # the shared walk names the document it could not store (it used to report a
    # bare count for the whole scope), so the retry is traceable to one entry
    out = capsys.readouterr().out
    assert "fi/2026:1" in out and "record left unwritten" in out


def test_a_vanished_pdf_re_resolves_its_record(tmp_path):
    """`item_key` reports "already current" as `record_unchanged(record, pdf)` --
    the record AND its document. A record matching byte for byte while its PDF
    has gone is not current: the shared walk must re-resolve it rather than skip
    it, or a lost document is never noticed again."""
    record = {"basefile": "fi/2026:2", "org": "fi", "nummer": "2026:2",
              "titel": "Kapitaltäckning", "status": "gällande",
              "dokument_url": "https://www.fi.se/contentassets/y.pdf"}

    class _ServesPdf:
        def request(self, _method, _url, **_kwargs):
            response = requests.Response()
            response.status_code = 200
            response._content = b"%PDF-1.4 minimal"
            response.url = "https://www.fi.se/contentassets/y.pdf"
            return response

    # first run stores both record and PDF; a second changes nothing
    assert rs_download._walk(tmp_path, [record], _ServesPdf(), 0, False, None,
                             "fi") == (1, 1)
    assert rs_download._walk(tmp_path, [record], _ServesPdf(), 0, False, None,
                             "fi") == (1, 0)

    # drop the PDF, leaving the record byte-identical: the entry is stale again,
    # so resolve runs and the document comes back. `new` stays 0 -- it counts
    # record *writes*, and the record itself never changed.
    compress.unlink(rs_download.pdf_path(tmp_path, "fi/2026:2"))
    assert rs_download._walk(tmp_path, [record], _ServesPdf(), 0, False, None,
                             "fi") == (1, 0)
    assert compress.exists(rs_download.pdf_path(tmp_path, "fi/2026:2"))


def test_migr_records_are_walked_with_fetching_on(monkeypatch):
    """Regression: `migr_sync` used to end in `_walk(..., fetch=False)`, the
    flag that means "every document was already fetched by
    `self_named_document`". That holds for Försäkringskassan, which routes
    *every* item through it, but Migrationsverket routes only the two entries
    whose index row states no RS/RK number. The other ~100 were therefore never
    fetched while their records were written anyway -- so 98 of 100 stored
    records asserted a PDF that was not on disk, and parse failed all of them.

    Behavioural, not a source grep: `_walk`'s own default is `fetch=True`, so
    asserting on the call is what still catches the bug if that default ever
    flips."""
    captured = {}
    monkeypatch.setattr(rs_download, "migr_session", lambda: object())
    monkeypatch.setattr(rs_download, "migr_listing", lambda session, delay: [])
    monkeypatch.setattr(rs_download, "_walk",
                        lambda *a, **kw: captured.update(kw) or (0, 0))
    rs_download.migr_sync("/nonexistent")
    assert captured.get("fetch", True) is not False, (
        "migr_sync must not disable fetching -- only 2 of its ~104 documents "
        "are fetched by self_named_document")


def test_a_record_naming_no_document_is_still_written(tmp_path):
    """The repealed Konkurrensverket entries keep their förteckning row and
    nothing else -- they are register entries, not failures."""
    record = {"basefile": "kkv/2019:1", "org": "kkv", "nummer": "2019:1",
              "titel": "Ramavtalsupphandlingar", "status": "upphävt"}
    seen, new = rs_download._walk(tmp_path, [record], _ServesHtml(), 0, False,
                                  None, "kkv")
    assert (seen, new) == (1, 1)
    assert compress.exists(record_path(tmp_path, "kkv", "kkv/2019:1"))


def test_parse_without_a_document(tmp_path):
    """A repealed Konkurrensverket entry keeps its förteckning row and nothing
    else: it parses to a register entry with an empty body rather than failing."""
    record = {"basefile": "kkv/2023:3", "org": "kkv", "nummer": "2023:3",
              "titel": "Ändringsbestämmelserna tillämplighet",
              "status": "upphävt", "upphavd": "20 oktober 2025",
              "source_url": "https://www.konkurrensverket.se/om-oss/"
              "stallningstaganden/"}
    path = record_path(tmp_path, "kkv", "kkv/2023:3")
    path.parent.mkdir(parents=True, exist_ok=True)
    compress.write_download(path, json.dumps(record, ensure_ascii=False))
    art = rs_parse.parse("kkv/2023:3", tmp_path)
    # an empty body writes no `structure` key at all (`lib.artifact.prune`)
    assert "structure" not in art
    assert art["metadata"]["status"] == "upphävt"
    assert art["identifier"] == "Konkurrensverkets ställningstagande 2023:3"


def store_skv(root, record, page_fixture):
    """One Skatteverket ställningstagande in the store: its register record and
    the page that is its text."""
    basefile = record["basefile"]
    compress.write_download(record_path(root, "skv", basefile),
                            json.dumps(record, ensure_ascii=False))
    compress.write_download(rs_download.body_path(root, basefile),
                            fixture(page_fixture))
    return basefile


def test_skv_parses_from_its_page(tmp_path):
    """End to end for the one agency with no PDF: the register record plus the
    stored page parse to the ordinary ställningstagande artifact, citations
    scanned, with the page's own chain fields folded in."""
    records, _ = skv_register()
    record = next(r for r in records if r["basefile"] == "skv/8-492402")
    art = rs_parse.parse(store_skv(tmp_path, record, "skv-page.html"), tmp_path)
    assert art["uri"] == "https://lagen.nu/rs/skv/8-492402"
    assert art["identifier"] == "Skatteverkets ställningstagande dnr 8-492402"
    assert art["metadata"]["publisher"] == "Skatteverket"
    assert art["metadata"]["beslutsdatum"] == "2020-09-29"
    assert art["metadata"]["diarienummer"] == "8-492402"
    assert art["metadata"]["nyckelord"] == ["Mervärdesskatt",
                                            "Skattebetalning & borgenärsarbete"]
    # what only the page says: the position this one replaced
    assert art["metadata"]["ersatter"] == "8-346749"
    assert art["structure"][0] == {"type": "rubrik", "level": 1,
                                   "text": ["1 Sammanfattning"]}
    assert art["footnotes"][0]["mark"] == "1"
    # the vertical's whole point: the body is on the rail of the paragraf it
    # reads, so the opening summary's citation resolves
    runs = art["structure"][1]["text"]
    assert any(isinstance(r, dict) and r.get("uri", "").endswith("2011:1244#K60P1")
               for r in runs), runs
    # no separate document: the page *is* the ställningstagande
    assert "document_url" not in art


def test_skv_withdrawn_page_carries_its_fate(tmp_path):
    """The register says a position stopped applying and when; the page says
    what replaced it. Both reach the artifact, which is what lets the page read
    as the historical statement it is."""
    records, _ = skv_register()
    record = next(r for r in records if r["basefile"] == "skv/8-1740076")
    art = rs_parse.parse(
        store_skv(tmp_path, record, "skv-page-upphavd.html"), tmp_path)
    assert art["metadata"]["status"] == "upphävt"
    assert art["metadata"]["upphavd"] == "2026-07-06"
    assert art["metadata"]["ersattAv"] == "8-207888-2026"
    assert labels.document_labels("rs", art).descriptive_label.endswith("(upphävt)")


def test_skv_page_is_the_patchable_intermediate(tmp_path, monkeypatch):
    """Skatteverkets page, not a PDF, is what a patch for this agency targets --
    normalised to one block element per line, so a hunk rewrites a paragraph
    rather than the whole document. `rs.parse` normalises identically before
    applying the patch, which is what keeps an authored patch applying."""
    records, _ = skv_register()
    record = next(r for r in records if r["basefile"] == "skv/8-492402")
    basefile = store_skv(tmp_path, record, "skv-page.html")
    monkeypatch.setattr(layout, "RS_DOWNLOADED", tmp_path)
    # the pair rs registers as `Source.intermediate`, which is what
    # `patchsource.intermediate` hands the CLI and the web editor
    provider, label = rs_source.SOURCES[0].intermediate
    text = provider(basefile)
    assert "web page" in label
    lines = text.split("\n")
    # every block on its own line, and a paragraph whole on one of them
    assert len(lines) == 51
    assert sum(1 for line in lines if line.startswith("<p ")) == 26
    assert any(line.startswith('<p class="normal">Möjligheten till befrielse')
               and line.endswith("</p>") for line in lines)


def test_skv_refuses_a_page_filed_under_the_wrong_dnr(tmp_path):
    """The dnr is this agency's identity and the page prints its own, so parse
    checks the two agree -- the store's proof that the page under a basefile is
    the document that basefile names."""
    records, _ = skv_register()
    record = next(r for r in records if r["basefile"] == "skv/8-1740076")
    # the register entry for one ställningstagande, the page of another
    basefile = store_skv(tmp_path, record, "skv-page.html")
    # a raise, not an assert: under `python -O` an assert would strip and the
    # wrong text would publish under a real identifier
    with pytest.raises(ValueError, match="wrong document is filed here"):
        rs_parse.parse(basefile, tmp_path)


# --------------------------------------------------------------------------
# render -- the withdrawal banner's sibling link
# --------------------------------------------------------------------------

def _site(known=()):
    # a real Site, not a stub: a hand-rolled one goes stale silently every time
    # the render context gains a field
    con = sqlite3.connect(":memory:")
    con.executescript(catalog.SCHEMA)
    return page.Site(con, set(known))


def test_a_sibling_is_linked_through_the_uri_its_number_mints():
    """The banner names the replacement the way the agency printed it and links
    it the way `model.rs_uri` addresses it -- which only agree by construction
    once the number is slugged, and Skatteverkets printed dnr is not a path
    segment."""
    own = "https://lagen.nu/rs/skv/131-253470-14-111"
    site = _site({"https://lagen.nu/rs/skv/8-2352573"})
    assert rs_render._sibling_rs(site, "8-2352573", own) == {
        "label": "8-2352573", "url": layout.page_url(
            "https://lagen.nu/rs/skv/8-2352573")}
    # the pre-2020 form: the label stays as printed, the url is the slug
    site = _site({"https://lagen.nu/rs/skv/131-297826-13-111"})
    sibling = rs_render._sibling_rs(site, "131 297826-13/111", own)
    assert sibling["label"] == "131 297826-13/111"
    assert sibling["url"] == "/rs/skv/131-297826-13-111"
    # a statement the corpus does not hold keeps the label and loses the link
    assert rs_render._sibling_rs(_site(), "2022:2",
                                 "https://lagen.nu/rs/kkv/2019:1")["url"] is None


def test_a_skv_page_renders(tmp_path):
    """The whole chain once, on the one agency whose body never went through a
    PDF: register record + stored page -> artifact -> the ställningstagandesida,
    with the withdrawal said in the banner."""
    records, _ = skv_register()
    record = next(r for r in records if r["basefile"] == "skv/8-1740076")
    art = rs_parse.parse(
        store_skv(tmp_path, record, "skv-page-upphavd.html"), tmp_path)
    html = rs_render.render(art, _site({art["uri"]}))
    assert "Jämkningsskyldighet vid överlåtelse av fastighet" in html
    assert "Skatteverkets ställningstagande dnr 8-1740076" in html
    assert "8-207888-2026" in html            # what replaced it, in the banner
    assert "1 Sammanfattning" in html


def test_skv_stops_walking_once_the_front_closes():
    """Skatteverkets front rejects everything for a while once it starts, so a
    run that meets a row of rejections stops instead of knocking through the
    remaining thousands. Nothing is stranded: a record is only ever stored once
    its page is, so the next run resumes at exactly this document."""
    rejected, said = [0], []

    def walk(source):
        return list(rs_download.until_blocked(source, lambda: rejected[0],
                                              limit=3, log=said.append))

    # a run the front answers throughout walks the whole listing
    assert walk(range(100)) == list(range(100))
    assert said == []

    # one that starts failing part way stops there -- and only after a *row* of
    # rejections, so a single bad page is still just one failed document
    def closes_after(n):
        for item in range(100):
            if item >= n:
                rejected[0] += 1
            yield item

    walked = walk(closes_after(10))
    assert walked == list(range(12)), walked
    assert "stopping" in said[0]


def test_skv_verify_rejects_a_page_that_is_not_a_stallningstagande():
    """The guard that decides whether a stored record has real text behind it.
    `browser.html` has already ruled out a WAF rejection; what is left is that
    the navigation landed on a document and not on some other page."""
    for name in ("skv-page.html", "skv-page-upphavd.html"):
        assert rs_download.skv_verify(fixture(name)) is None
    with pytest.raises(ValueError, match="no ställningstagande page"):
        rs_download.skv_verify(fixture("skv-register.html"))


def _artifact_from(fixture_name, root):
    """The artifact a stored page parses to, through the whole scanned
    projection -- so a node shape is checked as the catalog and renderer see it,
    not as the reader emits it. The record follows the page's own dnr, which is
    what `parse` checks the two agree on."""
    html = fixture(fixture_name)
    nummer = skv.dnr(skv.page_metadata(html)["Dnr"])
    basefile = "skv/" + number_slug(nummer)
    compress.write_download(
        record_path(root, "skv", basefile),
        json.dumps({"basefile": basefile, "org": "skv", "nummer": nummer,
                    "titel": "x", "status": "gällande"}, ensure_ascii=False))
    compress.write_download(rs_download.body_path(root, basefile), html)
    return rs_parse.parse(basefile, root)


def _with_trailing_element(markup):
    """A live page fixture with `markup` appended as the last block of the
    document's own content div -- where a note section sits. The withdrawn
    document is the base, because it is the one that publishes no notes of its
    own for a spliced section to run into."""
    page = fixture("skv-page-upphavd.html")
    closing = "</div></div>\n</main>"
    assert closing in page, "the fixture's content container moved"
    return page.replace(closing, markup + closing, 1)


def _with_body_element(markup):
    """The live page fixture with `markup` spliced in as the first block of the
    document's own content div."""
    page = fixture("skv-page.html")
    anchor = '<div class="body searchable-content">\n<div>'
    assert anchor in page, "the fixture's content container moved"
    return page.replace(anchor, anchor + markup, 1)


def test_skv_drops_the_empty_layout_tables():
    """Some pages set an empty 2x2 table as a layout scaffold. It carries no
    text, so it costs the document nothing."""
    blocks, _notes = skv.page_body(_with_body_element(
        "<table><tbody><tr><td width='50%'></td><td width='50%'></td></tr>"
        "</tbody></table>"))
    assert blocks[0] == ("rubrik", "1 Sammanfattning", 1, [], False)


def test_skv_reads_a_table_as_a_table():
    """206 tables across the register, and a first cut squashed each one into a
    single run-together stycke with its cells space-joined. They project onto
    the corpus-wide tabell/rad/cells node the renderer already draws."""
    blocks, _notes = skv.page_body(fixture("skv-page-tabell.html"))
    tables = [b for b in blocks if b[0] == "tabell"]
    assert len(tables) == 2
    _kind, _text, _level, rows, th = tables[0]
    assert th is True                       # this one marks its header row <th>
    assert rows[0] == ["Månad", "Dagar i Sverige", "Dagar i Danmark", "Summa"]
    assert len(rows) == 6


def test_skv_table_cells_are_citation_scanned(tmp_path):
    """A cell is text like any other, so a lagrum named inside a table still
    links -- which reading the table as one opaque block would lose."""
    art = _artifact_from("skv-page-tabell.html", tmp_path)
    tabell = next(n for n in art["structure"] if n["type"] == "tabell")
    assert [c for c in tabell["children"][0]["cells"]] == [
        ["Månad"], ["Dagar i Sverige"], ["Dagar i Danmark"], ["Summa"]]
    assert tabell["children"][0]["th"] is True


def test_skv_reads_a_quoted_passage_as_its_paragraphs():
    """47 blockquotes across the register -- a skatteavtal article, the OECD
    commentary on it. They flatten to stycken, as the `p.indented` Skatteverket
    quotes with elsewhere already does."""
    blocks, _notes = skv.page_body(fixture("skv-page-tabell.html"))
    quoted = [b[1] for b in blocks if b[1].startswith(
        "1. Om en person med hemvist i en avtalsslutande stat")]
    assert len(quoted) == 1, "the quoted treaty article is one stycke of its own"


def test_skv_body_text_in_an_unknown_element_is_reported():
    """The reader knows p, h1-h5, ul, ol, div.update, table and blockquote --
    the whole census over the register. Anything else stops the document rather
    than being squashed into one run-together stycke, which is how the tables
    were found in the first place."""
    with pytest.raises(ValueError, match="<pre>"):
        skv.page_body(_with_body_element("<pre>Ett kodblock</pre>"))


def test_skv_page_is_refetched_when_the_register_moves(tmp_path):
    """Skatteverket revises a page in place: it adds "ska inte längre tillämpas"
    to the page it withdraws, and the register entry closes its window at the
    same moment. Leaving the stored page alone because the file exists would
    publish the new date and currency over the superseded text."""
    root = tmp_path / "rs"
    record = {"basefile": "skv/8-1", "org": "skv", "nummer": "8-1",
              "titel": "En position", "status": "gällande"}
    served = [fixture("skv-page.html")]
    pending = [(record, lambda: served[0])]
    walk = functools.partial(
        harvest.walk_records, root, delay=0, scope="skv",
        document=harvest.page_path, verify=rs_download.skv_verify,
        refetch_when_changed=True)

    assert walk(pending) == (1, 1)
    assert walk(pending) == (1, 0)              # nothing moved, nothing fetched

    # the register closes the window: the record changes, so the page is refetched
    served[0] = fixture("skv-page-upphavd.html")
    moved = [({**record, "status": "upphävt", "upphavd": "2026-07-06"},
              lambda: served[0])]
    assert walk(moved) == (1, 1)
    stored = compress.read_text(harvest.page_path(root, "skv/8-1"))
    assert "ska inte längre tillämpas" in stored


class _ClosedFront:
    """A Skatteverket front that serves the register and then stops answering
    every document page, in one of the ways `lib.browser` distinguishes."""

    def __init__(self, error):
        self.error = error
        self.attempts = 0

    def __call__(self, _profile, settle=None):     # stands in for DetachedChrome
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def html(self, url, _marker, settle=None):
        if url == skv.INDEX_URL:
            return fixture("skv-register.html")
        self.attempts += 1
        raise self.error


@pytest.mark.parametrize("error", [
    browser.WafRejected("rejected"),
    # a front that holds its challenge open forever closes the site just as
    # surely as one that answers with a rejection page, and a stop watching only
    # for the rejection would spend fifteen hours failing every document
    browser.IncompleteNavigation("still a JavaScript challenge"),
])
def test_skv_stops_once_the_front_stops_answering(tmp_path, monkeypatch, capsys,
                                                  error):
    front = _ClosedFront(error)
    monkeypatch.setattr(rs_download, "DetachedChrome", front)
    seen, new = rs_download.skv_sync(tmp_path)
    assert new == 0
    # the register holds 8 filable entries; the run gives up after a row of
    # SKV_BLOCK_LIMIT and never reaches the rest
    assert front.attempts == rs_download.SKV_BLOCK_LIMIT
    assert seen < 8
    assert "front has closed for now" in capsys.readouterr().out
    # nothing is stored, so the next run resumes at the document it stopped on
    assert not list(tmp_path.glob("skv/*.json*"))


# --------------------------------------------------------------------------
# the notes, and where the note section ends
# --------------------------------------------------------------------------

def test_skv_notes_may_be_numbered_by_the_list_that_holds_them(tmp_path):
    """Skatteverket sets its notes two ways: paragraphs each opening with the
    marker the running text set as a superscript, or an ordered list whose
    numbering *is* the marker. Reading only the first failed 13 documents,
    because a list item carries no leading digit."""
    _blocks, notes = skv.page_body(fixture("skv-page-noter.html"))
    assert notes[0][0] == "1"
    assert notes[0][1].startswith("Med privatbostad avses ett småhus")


def test_skv_the_note_section_ends_at_the_next_heading():
    """Two documents close on a "Tillämpningsinformation" section *after* their
    notes. Reading the notes to the end of the document swallowed it -- a silent
    loss of body text, not only a crash."""
    blocks, _notes = skv.page_body(fixture("skv-page-noter.html"))
    headings = [b[1] for b in blocks if b[0] == "rubrik"]
    assert "Fotnoter" not in headings          # the heading itself is not body
    assert any(h.startswith("Tillämpningsinformation") for h in headings), \
        "the section after the notes is body, and must survive them"
    # and its paragraphs came through with it
    assert any(b[1].startswith("Detta ställningstagande ska tillämpas från")
               for b in blocks)


def test_skv_a_single_unnumbered_note_is_numbered_by_position():
    """One document in the register prints its only note with no marker at all
    -- one note needs no number. It takes its position, the same rule the
    ordered list follows."""
    _blocks, notes = skv.page_body(_with_trailing_element(
        "<h2>Fotnot</h2><p>Europaparlamentets och rådets förordning (EU) "
        "nr 952/2013.</p>"))
    assert notes == [("1", "Europaparlamentets och rådets förordning (EU) "
                      "nr 952/2013.")]


def test_skv_a_half_numbered_note_section_is_reported():
    """No document in the register mixes the two ways, so a section that does is
    far more likely to be a note running to a second paragraph -- which
    positional numbering would misnumber."""
    with pytest.raises(ValueError, match="numbers some notes and not others"):
        skv.page_body(_with_trailing_element(
            "<h2>Fotnot</h2><p>1 Den första noten.</p>"
            "<p>och dess andra stycke.</p>"))


# --------------------------------------------------------------------------
# how a ställningstagande names itself on a statute's context rail
# --------------------------------------------------------------------------

def test_the_compact_designation_drops_the_citation_frame():
    """`descriptive` is the *short* citing form ("räntelagen", not "Räntelag
    (1975:635)"). Four agencies have coined no designation, so `identifier`
    frames their bare number into a sentence that reads in prose -- and a rail
    row sits under a heading that has already said what these are, where
    thirteen rows each opening on the same 33 characters of frame says nothing.
    An agency whose citation form is already short keeps it."""
    assert rs_designation("skv", "8-140522-2026") == "8-140522-2026"
    assert rs_identifier("skv", "8-140522-2026") == \
        "Skatteverkets ställningstagande dnr 8-140522-2026"
    assert rs_designation("fk", "2025:01") == rs_identifier("fk", "2025:01")

    art = artifact(org="skv", nummer="8-140522-2026",
                   titel="Förutsättningar för att i folkbokföringen behandla "
                         "uppgifter om personer som aldrig har varit "
                         "folkbokförda i Sverige i vissa fall")
    lb = labels.document_labels("rs", art)
    assert lb.descriptive_label == "8-140522-2026"
    # the eyebrow and any prose citation keep the citable form
    assert lb.short_id == "Skatteverkets ställningstagande dnr 8-140522-2026"


def test_a_rail_row_carries_the_title_behind_the_bare_number():
    """A diarienummer names nothing on its own, so the document's own title
    rides along -- the treatment `foreskrift` already gets for "MCFFS 2026:8"."""
    assert "rs" in page.SUBTITLED_SOURCES
    row = page._citer_subtitle("rs", "8-140522-2026",
                               "Förutsättningar för att i folkbokföringen "
                               "behandla uppgifter")
    assert row == (' <span class="prov">Förutsättningar för att i '
                   'folkbokföringen behandla uppgifter</span>')


@pytest.mark.parametrize("status,upphavd,expired", [
    ("gällande", None, None),
    # the date the agency withdrew it -- what drops it off the rail
    ("upphävt", "2021-06-23", "2021-06-23"),
    # Konkurrensverket states two of its three withdrawals in prose and one not
    # at all; this column is compared against an ISO date, and all three of
    # those entries publish no document, so they carry no citation either way
    ("upphävt", "20 oktober 2025", None),
    ("upphävt", None, None),
])
def test_a_withdrawn_stallningstagande_declares_when_it_stopped(
        status, upphavd, expired):
    art = artifact(org="skv", nummer="8-1", titel="x", status=status,
                   upphavd=upphavd)
    assert catalog_rows._expired_date(art) == expired


def test_a_withdrawn_stallningstagande_leaves_the_context_rail(tmp_path):
    """A ställningstagande is on a paragraf's rail because it says how the
    agency reads that paragraf. A withdrawn one no longer says anything, so it
    drops -- the rule the rail already applies to a repealed act (I3). Reading
    folkbokföringslagen 1 § with thirteen ställningstaganden listed, twelve of
    them "(upphävt)", is what this is for."""
    con = catalog.connect(tmp_path / "catalog.sqlite")
    law = "https://lagen.nu/1991:481"
    rows = [(law, "sfs", "lag", "SFS 1991:481", None),
            ("https://lagen.nu/rs/skv/8-140522-2026", "rs", "skv",
             "8-140522-2026", None),
            ("https://lagen.nu/rs/skv/202-198967-18-111", "rs", "skv",
             "202 198967-18/111 (upphävt)", "2021-06-23")]
    for uri, source, kind, descriptive, expired in rows:
        con.execute(
            "INSERT INTO documents (uri, source, kind, label, title, "
            "descriptive, path, expired) VALUES (?,?,?,?,?,?,?,?)",
            (uri, source, kind, descriptive, "T", descriptive, "p", expired))
        if source == "rs":
            con.execute(
                "INSERT INTO links (from_uri, from_anchor, predicate, to_uri, "
                "to_root) VALUES (?,?,'dcterms:references',?,?)",
                (uri, "S1", law + "#P1", law))
    con.commit()
    site = page.Site(con, {r[0] for r in rows},
                     expired=catalog.expired_uris(con, "2026-08-12"))
    sections = page._inbound_groups(site, [law + "#P1"])
    rs_section = next(s for s in sections if s.key == "rs")
    assert rs_section.count == 1, "only the position still in force is listed"
    assert "8-140522-2026" in rs_section.html
    assert "upphävt" not in rs_section.html
    con.close()
