"""ICC decision harvesting (facet scrape + Legal Tools resolve), parsing, and
folkrätt wiring. Runs off a committed stored-record fixture and small dicts --
no network, no PDF binary (the PDF path's classification is unit-tested pure)."""

import json
from pathlib import Path

from bs4 import BeautifulSoup

from accommodanda.icc import download, parse
from accommodanda.icc import parse as icc_parse
from accommodanda.icc import render as icc_render
from accommodanda.icc.model import (
    Block,
    Decision,
    decision_uri,
    doc_basefile,
    load_types,
)
from accommodanda.lib import catalog, facets, layout, page, render
from accommodanda.lib.pdftext import Line

FIXTURES = Path(__file__).parent / "files" / "icc"


def _ntaganda():
    return parse.parse("ICC-01_04-02_06-2359", FIXTURES)


# --------------------------------------------------------------------------
# model identity + curated types
# --------------------------------------------------------------------------

def test_identity_and_curated_types():
    assert doc_basefile("ICC-01/04-02/06-2359") == "ICC-01_04-02_06-2359"
    assert decision_uri("ICC-01/04-02/06-2359") == \
        "https://lagen.nu/ext/icc/ICC-01_04-02_06-2359"
    types = load_types()
    assert types["793"]["kind"] == "judgment"        # the Art 74 verdict facet
    for facet, entry in types.items():
        assert entry["kind"] and entry["label"] and facet.isdigit()


# --------------------------------------------------------------------------
# PDF-paragraph classification (pure, no PDF binary) + artifact structure
# --------------------------------------------------------------------------

def test_classify_paragraphs():
    blocks = parse._classify([
        "ICC-01/04-02/06-2359 08-07-2019 5/97 RH",        # running header -> dropped
        "TRIAL CHAMBER VI",                                # all-caps heading
        "I. INTRODUCTION",                                 # roman-numeral heading
        "1. This case concerns the conduct of Mr Ntaganda.",
        "2. Ituri is a district in the DRC.",
        "The evidence establishes the following facts.",   # plain paragraph
        "DRC. 3",                                          # footnote debris -> NOT a heading
    ])
    kinds = [(b.kind, b.number) for b in blocks]
    assert ("rubrik", None) in kinds and kinds.count(("rubrik", None)) == 2
    assert ("stycke", "1") in kinds and ("stycke", "2") in kinds
    # the header line was dropped; the footnote fragment is a stycke, not a rubrik
    assert not any("08-07-2019" in b.text for b in blocks)
    debris = [b for b in blocks if b.text == "DRC. 3"]
    assert debris and debris[0].kind == "stycke"


def test_to_artifact_numbers_paragraphs_and_ids():
    decision = Decision(
        doc_number="ICC-01/04-02/06-2359", title="Judgment",
        case_name="The Prosecutor v. Bosco Ntaganda", case_number="ICC-01/04-02/06",
        decision_type="judgment", date="2019-07-08", chamber="Trial Chamber VI",
        body=[Block("rubrik", "I. BACKGROUND"),
              Block("stycke", "First paragraph.", number="1"),
              Block("stycke", "Second paragraph.", number="2"),
              Block("stycke", "An unnumbered closing line.")])
    structure = decision.to_artifact()["structure"]
    assert [n["type"] for n in structure] == ["rubrik", "stycke", "stycke", "stycke"]
    assert [n.get("id") for n in structure] == [None, "P1", "P2", "S3"]
    assert structure[1]["ordinal"] == "1"


# --------------------------------------------------------------------------
# parse: metadata (from the stored Legal Tools record; no PDF -> status only)
# --------------------------------------------------------------------------

def test_parse_metadata_without_body():
    art = _ntaganda()
    assert art["uri"] == "https://lagen.nu/ext/icc/ICC-01_04-02_06-2359"
    assert art["type"] == "avgorande" and art["court"] == "icc"
    assert art["doctype"] == "judgment"
    assert art["title"] == "The Prosecutor v. Bosco Ntaganda"
    assert art["identifier"] == "ICC-01/04-02/06 (Judgment)"
    assert art["avgorandedatum"] == "2019-07-08"
    md = art["metadata"]
    assert md["publisher"] == "International Criminal Court"
    assert md["caseNumber"] == "ICC-01/04-02/06"
    assert md["documentNumber"] == "ICC-01/04-02/06-2359"
    assert md["chamber"] == "Trial Chamber VI"
    assert art["structure"] == []                          # no PDF on disk -> metadata only
    assert art["source_url"] == \
        "https://www.icc-cpi.int/court-record/icc-01/04-02/06-2359"


# --------------------------------------------------------------------------
# download helpers (no network)
# --------------------------------------------------------------------------

def test_english_primary_prefers_non_translation():
    matches = [{"externalId": "ICC-02/04-01/15-1762-Red-tFRA", "slug": "fr"},
               {"externalId": "ICC-02/04-01/15-1762-Red", "slug": "en"}]
    primary = download._english_primary(matches, "ICC-02/04-01/15-1762")
    assert primary["slug"] == "en"                         # the -tFRA translation is dropped


def test_row_extracts_base_number_and_fallback():
    html = ('<div class="views-row">'
            '<span class="recordTitle">Trial Judgment</span>'
            '<span class="courtRecordcaseName">The Prosecutor v. Dominic Ongwen</span>'
            '<span class="datetime">4 February 2021</span>'
            '<span class="tags">Trial Chamber IX</span>'
            '<a href="/court-record/icc-02/04-01/15-1762-red">Trial Judgment</a></div>')
    row = download._row(BeautifulSoup(html, "html.parser").select_one(".views-row"))
    assert row["base"] == "ICC-02/04-01/15-1762"           # variant suffix stripped, upper-cased
    assert row["case_name"] == "The Prosecutor v. Dominic Ongwen"
    assert row["chamber"] == "Trial Chamber IX"


def test_iso_date():
    assert download._iso("8 July 2019") == "2019-07-08"
    assert download._iso("") is None


# --------------------------------------------------------------------------
# layout + catalog wiring
# --------------------------------------------------------------------------

def test_icc_layout_round_trips_and_catalog_row():
    uri = "https://lagen.nu/ext/icc/ICC-01_04-02_06-2359"
    assert layout.page_url(uri) == "/icc/ICC-01_04-02_06-2359"
    assert layout.page_relpath(uri) == "icc/ICC_01_04_02_06_2359.html"
    assert str(layout.url_to_relpath("/icc/ICC-01_04-02_06-2359")) == \
        "icc/ICC_01_04_02_06_2359.html"
    assert "icc" in facets.sources()
    row = catalog.icc_document(_ntaganda(), "artifact/icc/ICC-01_04-02_06-2359.json")
    assert row[:3] == (uri, "icc", "judgment")
    assert row[3] == "ICC-01/04-02/06-2359"                # label = document number


# --------------------------------------------------------------------------
# folkrätt landing + decision page
# --------------------------------------------------------------------------

def _stub(number, case, kind, date):
    return {"uri": decision_uri(number), "docnumber": number, "doctype": kind,
            "type": "avgorande", "court": "icc",
            "identifier": "%s (x)" % number, "title": case, "avgorandedatum": date,
            "metadata": {"documentNumber": number, "caseNumber": number}, "references": [],
            "structure": []}


def test_folkratt_lists_icc_grouped_by_decision_type(tmp_path):
    judgment = _stub("ICC-01/04-02/06-2359", "The Prosecutor v. Bosco Ntaganda",
                     "judgment", "2019-07-08")
    sentence = _stub("ICC-01/04-02/06-2442", "The Prosecutor v. Bosco Ntaganda",
                     "sentence", "2019-11-07")
    paths = []
    for art in (judgment, sentence):
        p = tmp_path / (doc_basefile(art["docnumber"]) + ".json")
        p.write_text(json.dumps(art, ensure_ascii=False))
        paths.append(p)
    database = str(tmp_path / "catalog.sqlite")
    catalog.rebuild(database, "icc", paths)
    con = catalog.connect(database)
    html = render.render_folkratt(con)

    assert "Internationella brottmålsdomstolen (ICC)" in html
    assert "Domar – fällande/friande (art. 74)" in html
    assert "Straffmätning (art. 76)" in html
    assert html.index("Domar") < html.index("Straffmätning")   # curated type order
    assert 'href="/icc/ICC-01_04-02_06-2359"' in html
    assert "The Prosecutor v. Bosco Ntaganda" in html
    assert "ICC-avgöranden" in html                        # the shared Dokumenttyp bucket


def test_render_decision_page_highlights_folkratt(tmp_path):
    art = _ntaganda()
    p = tmp_path / "d.json"
    p.write_text(json.dumps(art, ensure_ascii=False))
    database = str(tmp_path / "catalog.sqlite")
    catalog.rebuild(database, "icc", [p])
    con = catalog.connect(database)
    html = icc_render.render(art, page.Site(con, {art["uri"]}))
    assert '<a href="/folkratt/" class="on">Folkrätt</a>' in html
    assert "International Criminal Court" in html and "Trial Chamber VI" in html
    assert "Dokumentnummer" in html
    assert "icc-cpi.int/court-record" in html              # the Källa link


def test_frontpage_folds_icc_into_the_folkratt_row():
    # icc is a folkrätt-landing source: it must collapse into the single Folkrätt
    # frontpage row (no standalone /icc/ row -- there is no /icc/ index page), and
    # its count must be part of the combined folkrätt total
    rows = list(render._index_rows({"sfs": 5, "coe": 3, "icc": 269, "hudoc": 2}))
    routes = [route for route, _label, _count in rows]
    assert "/icc/" not in routes
    folkratt = [row for row in rows if row[0] == "/folkratt/"]
    assert len(folkratt) == 1 and folkratt[0][2] == 3 + 269 + 2


def test_blocks_reads_the_invisible_ocr_layer(monkeypatch):
    """The court files its records as scans carrying an *invisible* OCR text
    layer, which poppler omits unless asked. Reading only the visible layer
    returned one line per page -- the court's own filing stamp -- so 118 of 269
    decisions parsed to nothing while their text sat in the PDF on disk (V2).
    `pages_with_ocr` is the shared route for exactly that failure: it asks for
    the hidden layer, and OCRs a scan that still yields nothing."""
    seen = {}

    def fake(path, patch_key=None, lang="swe"):
        seen.update(path=str(path), patch_key=patch_key, lang=lang)
        return [(1, [Line("Privateering is and remains abolished.", 10,
                          False, False, False, 12)])]

    monkeypatch.setattr(icc_parse, "pages_with_ocr", fake)
    blocks = icc_parse._blocks("/tmp/x.pdf", "ICC-01/04-01/06-1432")
    assert seen["lang"] == "eng"                    # the court files in English
    assert seen["patch_key"] == ("icc", "ICC-01/04-01/06-1432")
    assert [b.text for b in blocks] == [
        "Privateering is and remains abolished."]


def test_a_doubled_quotation_mark_is_collapsed_but_a_cut_title_is_left_alone():
    """Legal Tools types the opening quote twice on one record ('entitled
    ""Décision sur la demande…'), which reached the page as a stammered
    delimiter (U3). A doubled delimiter is a slip, not something the title says.
    The unbalanced quote 16 other titles carry is a different thing: the source
    truncates them and marks the cut itself, so closing the quotation would
    assert a boundary the record does not have."""
    assert icc_parse.RE_DOUBLED_QUOTE.sub('"', 'entitled ""Décision sur x"') \
        == 'entitled "Décision sur x"'
    cut = 'entitled "Decision on the consequences of non-disclosure [ ... ]'
    assert icc_parse.RE_DOUBLED_QUOTE.sub('"', cut) == cut
