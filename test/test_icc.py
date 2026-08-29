"""ICC decision harvesting (facet scrape + Legal Tools resolve), parsing, and
folkrätt wiring. Runs off a committed stored-record fixture and small dicts --
no network, no PDF binary (the PDF path's classification is unit-tested pure)."""

import json
from pathlib import Path

from bs4 import BeautifulSoup

from ferenda.icc import download, parse, treaties
from ferenda.icc import parse as icc_parse
from ferenda.icc import render as icc_render
from ferenda.icc.model import (
    Block,
    Decision,
    decision_uri,
    doc_basefile,
    load_types,
)
from ferenda.lib import catalog, facets, layout, page, render
from ferenda.lib.pdftext import Line

FIXTURES = Path(__file__).parent / "files" / "icc"


def _ntaganda():
    return parse.parse("ICC-01_04-02_06-2359", FIXTURES)


# --------------------------------------------------------------------------
# model identity + curated types
# --------------------------------------------------------------------------

def test_identity_and_curated_types():
    assert doc_basefile("ICC-01/04-02/06-2359") == "ICC-01_04-02_06-2359"
    assert decision_uri("ICC-01/04-02/06-2359") == \
        "https://lagen.nu/icc/ICC-01_04-02_06-2359"
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


def test_a_page_break_fragment_is_not_a_roman_numeral():
    """The old head pattern was the set of letters a roman numeral is spelt
    from, not a numeral: it matches "ICC" too, so a sentence a page break
    dropped onto the court's abbreviation read as a section heading ("ICC. This
    made the investigators' job particularly delicate and it", in
    ICC-01/04-01/06-2842 and ICC-01/09-01/20-153)."""
    for head in ("I. Introduction", "II. The Law", "IV. Overview",
                 "IX. Disposition", "X. CONSCRIPTION AND ENLISTMENT",
                 "XII. DISPOSITION"):
        assert parse.RE_ROMAN_HEAD.match(head), head
    for prose in ("ICC. This made the investigators' job particularly delicate "
                  "and it", "ICC. The meeting ended when Mr Gicheru was calmed",
                  "CCC. X", "IC. X", "VV. X"):
        assert not parse.RE_ROMAN_HEAD.match(prose), prose
    assert not parse._is_heading("ICC. This made the investigators' job "
                                 "particularly delicate and it")


def test_an_exhibit_id_is_not_a_heading():
    """A scanned footnote leaves the evidence number behind as its own
    paragraph. It holds no lowercase letter, so the all-caps test read it as a
    heading (4 of them, in ICC-01/04-01/06-2842 and ICC-01/05-01/08-3343).
    Counting capitals as *words* rather than as runs of capitals keeps it out:
    "EVD-OTP-00570." is one token and not a word, where the old count read
    "EVD" and "OTP" as two words.

    A whole-line "a hyphen before a digit" guard would do it too, and costs more
    than it saves: over 35 documents it rejected no exhibit id the word count
    misses, and two real headings that name a year range ("B. FORCES PRESENT IN
    THE CAR DURING THE 2002-2003 CAR OPERATION")."""
    for exhibit in ("EVD-OTP-00570.", "EVD-T-OTP-00711/CAR-OTP-0017-0358.",
                    "1664 HIV/AIDS."):
        assert not parse._is_heading(exhibit), exhibit
    for heading in ("TRIAL CHAMBER VI", "SEPARATE OPINION OF JUDGE ADRIAN FULFORD",
                    "B. FORCES PRESENT IN THE CAR DURING THE 2002-2003 CAR OPERATION",
                    "C. PILLAGING"):     # the enumerator counts as a word
        assert parse._is_heading(heading), heading


def test_a_heading_that_swallowed_its_paragraphs_is_split():
    """The scans carry no bold or size signal, so `page_paragraphs` glues a
    heading to the paragraphs under it wherever the gap below it is small, and
    `_is_heading` then stamped the whole run a rubrik -- 1969 characters of it in
    ICC-01/04-01/10-1, with every citation anchor inside lost. The run is cut at
    the first paragraph number, so the numbered paragraph gets the anchor it is
    cited by."""
    blocks = parse._classify([
        "I. Introduction 1. This decision of Pre-Trial Chamber I "
        "(\"Chamber\") is with respect to the Prosecution's Application. "
        "2. On 6 September 2010, the Chamber issued a decision.",
    ])
    assert [(b.kind, b.number) for b in blocks] == \
        [("rubrik", None), ("stycke", "1")]
    assert blocks[0].text == "I. Introduction"
    assert blocks[1].text.startswith("This decision of Pre-Trial Chamber I")
    # the split paragraph is anchored like any other numbered paragraph
    structure = Decision(
        doc_number="ICC-01/04-01/10-1", title="Decision",
        case_name="The Prosecutor v. Callixte Mbarushimana",
        case_number="ICC-01/04-01/10", decision_type="warrant",
        body=blocks).to_artifact()["structure"]
    assert [n["type"] for n in structure] == ["rubrik", "stycke"]
    assert structure[1]["id"] == "P1" and structure[1]["ordinal"] == "1"


def test_a_year_in_prose_does_not_split_a_paragraph():
    """Only a run whose head is *itself* a heading is cut, which is what keeps
    "… since early 2009. The Prosecutor further requests …" one paragraph."""
    blocks = parse._classify([
        "The Chamber recalls that the crimes were committed in the Kivu "
        "Provinces since early 2009. The Prosecutor further requests the "
        "Chamber to issue a warrant of arrest.",
    ])
    assert len(blocks) == 1 and blocks[0].kind == "stycke"
    assert blocks[0].number is None


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
    assert art["uri"] == "https://lagen.nu/icc/ICC-01_04-02_06-2359"
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
    uri = "https://lagen.nu/icc/ICC-01_04-02_06-2359"
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


def test_a_sentence_split_by_a_page_break_is_rejoined(monkeypatch):
    """A court record breaks mid-sentence on every page, and the two halves
    arrived as two paragraphs. The running header has to be dropped *before* the
    join: it is the first paragraph of every page, so leaving it in place puts a
    filing stamp between the halves and nothing ever rejoins."""
    def line(text, top):
        return Line(text, top, False, False, False, 12)

    def fake(path, patch_key=None, lang="swe"):
        return [(1, [line("The witness was assumed to be from the", 10)]),
                (2, [line("ICC-01/04-01/06-2842 14-03-2012 1/593 EO T", 5),
                     line("office in Kinshasa. The Chamber notes that the", 300),
                     line("evidence supports this account of the meeting.", 315),
                     line("Nothing else in the record contradicts it.", 330)])]

    monkeypatch.setattr(icc_parse, "pages_with_ocr", fake)
    assert [b.text for b in icc_parse._blocks("/tmp/x.pdf", "ICC-01/04-01/06-2842")] \
        == ["The witness was assumed to be from the office in Kinshasa. The "
            "Chamber notes that the evidence supports this account of the "
            "meeting. Nothing else in the record contradicts it."]


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


def test_legal_tools_footer_is_furniture():
    """The Legal Tools download stamps "No: ICC-… 3/40 PURL: …" under every
    page -- 3,116 fragments sat in rendered body text across 92 decisions.
    Furniture needs two rules: a footer that is its own paragraph drops whole;
    one glued onto a footnote strips, leaving the footnote's own legal-tools
    citation (a plain url, no PURL stamp) alone."""
    assert icc_parse.RE_FOOTER.match(
        "No: ICC-02/11-01/11 OA 2 3/40 "
        "PURL: https://www.legal-tools.org/doc/649ff5/")
    assert icc_parse.RE_FOOTER.match(
        "PURL: https://www.legal-tools.org/doc/649ff5/")
    glued = ("^ ICC-02/11-01/11-153 <http://www.legal-tools.org/doc/829d3f/>. "
             "No: ICC-02/11-01/11 OA 2 4/40 "
             "PURL: https://www.legal-tools.org/doc/649ff5/")
    assert icc_parse.RE_FOOTER_EDGE.sub("", glued).strip() \
        == "^ ICC-02/11-01/11-153 <http://www.legal-tools.org/doc/829d3f/>."
    mid = ("paras 155-165. No: ICC-02/11-01/11 OA 2 34/40 "
           "PURL: https://www.legal-tools.org/doc/649ff5/ ^ ^ alleged violation")
    assert "PURL" not in icc_parse.RE_FOOTER_EDGE.sub("", mid)


def test_sibling_filing_citations_link_held_decisions(tmp_path):
    """An ICC decision cites its siblings by document number. A held sibling
    links (through the variant on disk where only a -Red is held), an unheld
    number and the decision's own number stay plain, and the treaty citations
    ride along in the same span list."""
    (tmp_path / "ICC-01_04-01_07-1788.json").write_text("{}")
    (tmp_path / "ICC-01_04-01_07-55-Red.json").write_text("{}")
    treaties._held.cache_clear()
    text = ("as decided in ICC-01/04-01/07-1788 and ICC-01/04-01/07-55, "
            "but not ICC-01/04-01/07-9999 nor ICC-01/04-01/07-2288, "
            "applying article 74 of the Statute")
    refs = treaties.refs(text, "ICC-01/04-01/07-2288", tmp_path)
    uris = [r.uri.replace("https://lagen.nu/", "") for r in refs]
    assert "icc/ICC-01_04-01_07-1788" in uris
    assert "icc/ICC-01_04-01_07-55-Red" in uris
    assert not any(u.endswith("9999") or u.endswith("2288") for u in uris)
    assert "icrc/585#A74" in uris
    treaties._held.cache_clear()
