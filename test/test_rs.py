"""rs vertical (myndigheternas rättsliga ställningstaganden): identity, the six
listing readers, page-1 header extraction, body classification, artifact
projection and the layout/catalog/facets wiring.

Hermetic: the fixtures under ``test/files/rs/`` are trimmed captures of the live
2026 pages and of ``pdf_first_page_text`` over the real PDFs, so the rules are
exercised against what the agencies actually publish without network or poppler.
"""

import json
from pathlib import Path

import pytest
import requests

from accommodanda.lib import catalog, compress, facets, labels, layout, util
from accommodanda.lib.pdftext import Para, classify_letterhead
from accommodanda.lib.util import record_path
from accommodanda.rs import download as rs_download
from accommodanda.rs import parse as rs_parse
from accommodanda.rs.agencies import BY_ORG, ORGS, REGISTRY, number_slug
from accommodanda.rs.model import Block, Stallningstagande, rs_identifier, rs_uri

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
])
def test_identity(org, nummer, uri, identifier):
    assert rs_uri(org, nummer) == uri
    assert rs_identifier(org, nummer) == identifier


def test_number_slug_leaves_the_agency_number_readable():
    assert number_slug("2025:01") == "2025:01"
    assert number_slug("7/16/Skusan") == "7-16-Skusan"


def test_registry_is_one_entry_per_agency():
    assert len(REGISTRY) == len(ORGS) == len(BY_ORG) == 6
    # every agency has a citable designation and a listing to walk
    for agency in REGISTRY:
        assert "%s" in agency.identifier
        assert agency.listing.startswith("https://")


# --------------------------------------------------------------------------
# the six listing readers
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
    uri, source, kind, label, title, path = catalog.document_row(
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
    assert art["structure"] == []
    assert art["metadata"]["status"] == "upphävt"
    assert art["identifier"] == "Konkurrensverkets ställningstagande 2023:3"
