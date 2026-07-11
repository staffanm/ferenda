"""HUDOC JSON harvesting, body parsing, CoE article identity and wiring."""

import json
from pathlib import Path

import pytest

from accommodanda.hudoc import download, parse
from accommodanda.lib import catalog, coe, facets, layout, render
from accommodanda.lib.errors import SkipDocument

FIXTURES = Path(__file__).parent / "files" / "hudoc"


def test_hudoc_article_codes_map_to_treaty_office_uris():
    assert coe.hudoc_article("8") == "https://lagen.nu/ext/coe/005#A8"
    assert coe.hudoc_article("6-3-d") == "https://lagen.nu/ext/coe/005#A6P3Ld"
    assert coe.hudoc_article("P1-1") == "https://lagen.nu/ext/coe/009#A1"
    assert coe.hudoc_article("P7-4") == "https://lagen.nu/ext/coe/117#A4"
    assert coe.hudoc_article("P99-1") is None
    assert coe.hudoc_articles("14+3") == [
        "https://lagen.nu/ext/coe/005#A14",
        "https://lagen.nu/ext/coe/005#A3",
    ]


def test_query_is_chamber_judgments_and_language_bounded():
    query = download.query_for(("ENG", "FRE"))
    assert 'documentcollectionid2:"CASELAW"' in query
    assert 'documentcollectionid2:"JUDGMENTS"' in query
    assert 'documentcollectionid2:"GRANDCHAMBER"' in query
    assert 'documentcollectionid2:"CHAMBER"' in query
    assert 'languageisocode:"ENG"' in query
    assert 'languageisocode:"FRE"' in query
    assert download.query_for(itemid="001-123456") == 'itemid:"001-123456"'


def test_parse_hudoc_fixture_to_artifact():
    record = json.loads((FIXTURES / "001-123456.json").read_text())
    html = (FIXTURES / "001-123456.html").read_text()
    art = parse.parse_record(record, html).to_artifact()
    assert art["uri"] == "https://lagen.nu/dom/echr/001-123456"
    assert art["doctype"] == "judgment"
    assert art["date"] == "2024-03-12"
    assert art["ecli"].startswith("ECLI:CE:ECHR:")
    assert art["metadata"]["applicationNumber"] == ["12345/20", "67890/21"]
    assert [node["id"] for node in art["structure"] if node["type"] == "stycke"][:3] \
        == ["S1", "P1", "P2"]
    headings = ["".join(node["text"]) for node in art["structure"]
                if node["type"] == "rubrik"]
    assert headings == ["THE FACTS", "THE LAW"]
    targets = {ref["uri"] for ref in art["references"]}
    assert "https://lagen.nu/ext/coe/005#A8" in targets
    assert "https://lagen.nu/ext/coe/117#A4" in targets


def test_toc_entries_are_removed_without_dropping_the_judgment_and_css_headings():
    html = """
    <style>
      .main { page-break-after: avoid; font-size: 14pt }
      .bold { font-weight: bold }
    </style>
    <div>
      <p>Table of Contents</p>
      <p><a href="#_Toc1">THE FACTS</a></p>
      <p class="main"><a name="_Toc1"></a>THE FACTS</p>
      <p>1. The application was lodged.</p>
      <p><span class="bold">A. Admissibility</span></p>
      <p>2. The complaint is admissible.</p>
    </div>
    """
    blocks = parse.parse_body(html)
    assert [(block.kind, block.text, block.level) for block in blocks] == [
        ("rubrik", "THE FACTS", 1),
        ("stycke", "The application was lodged.", 1),
        ("rubrik", "A. Admissibility", 2),
        ("stycke", "The complaint is admissible.", 1),
    ]


def test_restarted_judgment_numbering_gets_unique_stable_ids():
    record = json.loads((FIXTURES / "001-123456.json").read_text())
    html = """
      <p>THE FACTS</p><p>1. Facts.</p><p>2. More facts.</p>
      <p>FOR THESE REASONS, THE COURT</p><p>1. Declares.</p><p>2. Holds.</p>
    """
    art = parse.parse_record(record, html).to_artifact()
    paragraphs = [node for node in art["structure"] if node["type"] == "stycke"]
    assert [node["id"] for node in paragraphs] == ["P1", "P2", "P1-2", "P2-2"]
    assert [node["ordinal"] for node in paragraphs] == ["1", "2", "1", "2"]


def test_unusable_hudoc_body_is_deliberately_skipped():
    record = json.loads((FIXTURES / "001-123456.json").read_text())
    html = "<p>The text of this judgment is available in French only.</p>"
    with pytest.raises(SkipDocument, match="contains no numbered paragraphs"):
        parse.parse_record(record, html)


def test_hudoc_layout_and_catalog():
    uri = "https://lagen.nu/dom/echr/001-123456"
    assert layout.relpath("hudoc", "001-123456").as_posix() == "001-123456"
    assert layout.page_url(uri) == "/dom/echr/001-123456"
    assert layout.url_to_relpath("/dom/echr/001-123456") == \
        "dom/dom_echr_001_123456.html"
    assert render.human_fragment("A6P3Ld") == "artikel 6 punkt 3 led d"
    assert render.human_fragment("A25P1-2") == "artikel 25 punkt 1 variant 2"
    assert render.human_fragment("AII.1") == "artikel II.1"
    art = {"uri": uri, "itemid": "001-123456", "doctype": "judgment",
           "title": "CASE OF EXAMPLE v. SWEDEN"}
    row = catalog.hudoc_document(art, "case.json")
    assert row[1:5] == ("hudoc", "judgment", "001-123456",
                        "CASE OF EXAMPLE v. SWEDEN")


def test_metadata_references_join_generic_graph():
    target = "https://lagen.nu/ext/coe/005#A8"
    art = {"uri": "https://lagen.nu/dom/echr/001-123456",
           "references": [{"uri": target, "predicate": "dcterms:references",
                           "text": "8"}]}
    assert catalog.artifact_links(art) == [
        (None, {"uri": target, "predicate": "dcterms:references", "text": "8"})]


def test_hudoc_case_is_inbound_on_treaty_article(tmp_path):
    target = "https://lagen.nu/ext/coe/005#A8"
    treaty = {"uri": "https://lagen.nu/ext/coe/005", "number": "005",
              "identifier": "ETS No. 005", "doctype": "treaty",
              "title": "European Convention on Human Rights",
              "date": "1950-11-04",
              "structure": [{"type": "artikel", "id": "A8", "ordinal": "8",
                             "text": ["Article 8"], "children": []}]}
    case = {"uri": "https://lagen.nu/dom/echr/001-123456",
            "itemid": "001-123456", "doctype": "judgment",
            "title": "CASE OF EXAMPLE v. SWEDEN",
            "date": "2024-03-12",
            "references": [{"uri": target, "predicate": "dcterms:references",
                            "text": "8"}], "structure": []}
    treaty_path, case_path = tmp_path / "005.json", tmp_path / "case.json"
    treaty_path.write_text(json.dumps(treaty))
    case_path.write_text(json.dumps(case))
    database = str(tmp_path / "catalog.sqlite")
    catalog.rebuild(database, "coe", [treaty_path])
    catalog.rebuild(database, "hudoc", [case_path])
    con = catalog.connect(database)
    assert catalog.inbound(con, target) == [
        (case["uri"], None, "001-123456", case["title"], "hudoc")]
    assert set(facets.group(con, "hudoc")) == {("judgment", "2024")}
    assert set(facets.group(con, "coe")) == {("treaty", "1950")}
    site = render.Site(con, {treaty["uri"], case["uri"]})
    html = render.render_coe(treaty, site)
    assert "Europadomstolens praxis" in html
    assert "CASE OF EXAMPLE v. SWEDEN" in html
    assert 'id="A8"' in html
