"""Council of Europe Treaty Office web-service harvest and treaty parsing."""

import json
from pathlib import Path

from accommodanda.coe import download, parse
from accommodanda.lib import catalog, coe, layout, render

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


def test_significant_title_splits_off_the_instrument_designation():
    # the SFS listing convention: a subdued designation prefix, the emphasised
    # subject it files (and sorts) under
    assert coe.significant_title(
        "Convention for the Protection of Human Rights and Fundamental Freedoms") \
        == ("Convention for the ", "Protection of Human Rights and Fundamental Freedoms")
    assert coe.significant_title("European Convention on Extradition") \
        == ("European Convention on ", "Extradition")
    assert coe.significant_title("Convention on Cybercrime") \
        == ("Convention on ", "Cybercrime")
    # no instrument-plus-connector head: only a bare leading "European" is dropped
    assert coe.significant_title("European Social Charter") \
        == ("European ", "Social Charter")


def test_protocol_reference_names_the_amended_instrument():
    # the parent name a protocol appends its own qualifiers to (matched by prefix)
    assert coe.protocol_reference(
        "Protocol No. 4 to the Convention for the Protection of Human Rights "
        "and Fundamental Freedoms, securing certain rights and freedoms") \
        .startswith("Convention for the Protection of Human Rights")
    assert coe.protocol_reference(
        "Protocol No. 15 amending the Convention for the Protection of Human "
        "Rights and Fundamental Freedoms") \
        .startswith("Convention for the Protection of Human Rights")
    # a plain convention is not a protocol and references nothing
    assert coe.protocol_reference("European Convention on Extradition") is None


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


def _flatten(nodes):
    for node in nodes:
        yield node
        yield from _flatten(node.get("children", []))


def test_roman_and_compound_article_designations():
    structure = parse.build_structure([
        ("Section I – Definitions", True),
        ("Article I", True),
        ("Definitions apply.", False),
        ("Article II.1", True),
        ("1 First rule.", False),
    ])
    articles = [node for node in structure if node["type"] == "artikel"]
    assert [(node["id"], node["ordinal"]) for node in articles] == [
        ("AI", "I"), ("AII.1", "II.1")]
    assert articles[1]["children"][0]["id"] == "AII.1P1"


def test_section_only_amending_protocol_has_provision_structure():
    structure = parse.build_structure([
        ("Preamble", True),
        ("Have agreed as follows:", False),
        ("Section I", True),
        ("Article 1, paragraph 1, shall read:", False),
        ("Replacement text.", False),
        ("Section II", True),
        ("Final clauses apply.", False),
    ])
    sections = [node for node in structure if node["type"] == "sektion"]
    assert [(node["id"], node["ordinal"]) for node in sections] == [
        ("SecI", "I"), ("SecII", "II")]
    assert "Article 1, paragraph 1" in sections[0]["children"][0]["text"][0]


def test_repeated_coe_designators_get_contextual_unique_ids():
    structure = parse.build_structure([
        ("Article 1", True),
        ("1 First paragraph.", False),
        ("a First list.", False),
        ("a Second list with the same printed marker.", False),
        ("1 Text amended in accordance with Protocol No. 1.", False),
        ("1 Replacement paragraph.", False),
        ("Article 1", True),
        ("1 Appended provision.", False),
    ])
    nodes = list(_flatten(structure))
    ids = [node["id"] for node in nodes if node.get("id")]
    assert len(ids) == len(set(ids))
    assert {"A1", "A1P1", "A1P1La", "A1P1La-2", "A1S1", "A1P1-2",
            "A1-2", "A1-2P1"} <= set(ids)


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


def _treaty(number, title, doctype="treaty"):
    return {"uri": "https://lagen.nu/ext/coe/" + number, "number": number,
            "identifier": "ETS No. " + number, "doctype": doctype,
            "title": title, "date": "19%s-01-01" % number[:2], "structure": []}


def test_folkratt_lists_coe_alphabetically_with_nested_protocols(tmp_path):
    # a named central convention (EKMR, in names.json), one of its protocols, and
    # an unrelated treaty that sorts by its significant title, not the year
    echr = _treaty("005",
                   "Convention for the Protection of Human Rights and Fundamental Freedoms")
    protocol = _treaty("046",
                       "Protocol No. 4 to the Convention for the Protection of Human "
                       "Rights and Fundamental Freedoms, securing certain rights",
                       doctype="protocol")
    extradition = _treaty("024", "European Convention on Extradition")
    paths = []
    for art in (echr, protocol, extradition):
        p = tmp_path / (art["number"] + ".json")
        p.write_text(json.dumps(art))
        paths.append(p)
    database = str(tmp_path / "catalog.sqlite")
    catalog.rebuild(database, "coe", paths)
    con = catalog.connect(database)
    html = render.render_folkratt(con)

    # the shared top-level Dokumenttyp selector carries a Fördrag bucket (the CoE
    # treaties), marked current on the landing
    assert '<h2 class="facet-axis">Dokumenttyp</h2>' in html
    assert '<a href="/folkratt/" aria-current="page">Fördrag' in html
    # EKMR is a central treaty (surfaced first); its protocol nests beneath it,
    # not as a sibling top-level entry
    assert "Centrala fördrag" in html and "Övriga fördrag" in html
    assert 'class="folkratt-protocols"' in html
    assert html.index("Centrala fördrag") < html.index("Övriga fördrag")
    # the significant title is emphasised, the designation subdued, and the gloss
    # folds the informal name, acronym and reference into one parenthetical
    assert '<span class="pre">Convention for the </span>Protection of Human Rights' \
        in html
    assert 'class="ref">(Europakonventionen, EKMR, ETS No. 005)</span>' in html
    # links resolve to the treaty's own page address, not a /folkratt/ path
    assert 'href="/coe/005"' in html
    # the nested protocol shows its full title and its own reference
    assert "Protocol No. 4 to the Convention" in html
    assert 'class="ref">(ETS No. 046)</span>' in html
    # Extradition (name 'E') sits under Övriga, after the nested protocol block
    assert "Extradition" in html
    assert html.index("Protocol No. 4") < html.index("Extradition")
