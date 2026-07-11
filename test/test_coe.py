"""Council of Europe Treaty Office web-service harvest and treaty parsing."""

import json
from pathlib import Path

from accommodanda.coe import download, parse
from accommodanda.lib import catalog, layout

FIXTURES = Path(__file__).parent / "files" / "coe"


def _records():
    """Stored records built from the frozen web-service fixtures (the search
    rows for ETS 005/009 plus the getLieux place lookup, verbatim)."""
    places = {int(place["Key"]): place["Value"]
              for place in json.loads((FIXTURES / "ws-lieux.json").read_text())}
    return {ws["Numero_traite"]: download.treaty_record(ws, places)
            for ws in json.loads((FIXTURES / "ws-search.json").read_text())}


def test_treaty_records_from_web_service():
    echr = _records()["005"]
    assert echr["title"] == \
        "Convention for the Protection of Human Rights and Fundamental Freedoms"
    assert echr["reference"] == "ETS No. 005"
    assert echr["opening_date"] == "1950-11-04"
    assert echr["opening_place"] == "Rome"
    assert echr["entry_into_force"] == "1953-09-03"
    assert echr["text_url"] == "https://rm.coe.int/1680a2353d"
    assert echr["source_url"] == download.DETAIL % "005"
    protocol = _records()["009"]
    assert protocol["reference"] == "ETS No. 009"
    assert protocol["opening_date"] == "1952-03-20"
    assert protocol["opening_place"] == "Paris"


def test_parse_pdf_body():
    """The PDF body path (pdftohtml -> page_paragraphs -> build_structure).
    009.pdf is synthetic (a hand-assembled Helvetica/Helvetica-Bold page):
    a bold chapter heading, two bold Article headings, wrapped lines that
    must reflow into one paragraph, and both marker generations -- Article 1
    uses the older dotted '1.' / parenthesised 'a)', Article 2 the bare
    '1' / 'a' of the current rm.coe.int layout -- plus a trailing unnumbered
    stycke."""
    paragraphs = parse.pdf_paragraphs(FIXTURES / "009.pdf")
    assert ("1. Everyone has the right to liberty and security of person.",
            False) in paragraphs
    structure = parse.build_structure(paragraphs)
    assert structure[0] == {"type": "rubrik", "level": 1,
                            "text": ["Chapter I - General provisions"]}
    articles = [node for node in structure if node["type"] == "artikel"]
    assert [node["id"] for node in articles] == ["A1", "A2"]
    article1, article2 = articles
    assert [child["id"] for child in article1["children"]] == ["A1P1", "A1P2"]
    assert article1["children"][0]["children"][0]["id"] == "A1P1La"
    assert [child["id"] for child in article2["children"]] == ["A2P1", "A2S1"]
    assert article2["children"][0]["children"][0]["id"] == "A2P1La"


def test_parse_treaty_artifact_and_sfs_bridge():
    record = _records()["005"]
    art = parse.parse_record(record, parse.pdf_paragraphs(FIXTURES / "009.pdf"))
    assert art["uri"] == "https://lagen.nu/ext/coe/005"
    assert art["identifier"] == "ETS No. 005"
    assert art["date"] == "1950-11-04"
    assert art["metadata"]["swedishImplementation"] == \
        "https://lagen.nu/1994:1219"
    assert art["references"][0]["predicate"] == "rdfs:seeAlso"
    assert any(node["type"] == "artikel" for node in art["structure"])


def test_coe_layout_and_catalog():
    uri = "https://lagen.nu/ext/coe/005"
    assert layout.relpath("coe", "005").as_posix() == "005"
    assert layout.page_relpath(uri) == "coe/005.html"
    assert layout.page_url(uri) == "/coe/005"
    assert layout.url_to_relpath("/coe/005") == "coe/005.html"
    art = {"uri": uri, "number": "005", "identifier": "ETS No. 005",
           "doctype": "treaty", "title": "European Convention"}
    row = catalog.coe_document(art, "005.json")
    assert row[1:5] == ("coe", "treaty", "ETS No. 005", "European Convention")
