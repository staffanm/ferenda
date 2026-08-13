"""HUDOC JSON harvesting, body parsing, CoE article identity and wiring."""

import json
from pathlib import Path

import pytest

from accommodanda.coe import render as coe_render
from accommodanda.hudoc import download, parse, summaries, translations
from accommodanda.hudoc import render as hudoc_render
from accommodanda.lib import catalog, coe, facets, layout, page
from accommodanda.lib.errors import SkipDocument
from accommodanda.wiki import parse as wiki_parse

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


def test_decisions_are_their_own_collection_scope():
    query = download.query_for(("ENG",), collection="decisions")
    assert 'documentcollectionid2:"DECISIONS"' in query
    assert 'documentcollectionid2:"JUDGMENTS"' not in query
    assert download.watermark_path("/root", "decisions").name \
        != download.watermark_path("/root", "judgments").name


def test_year_slice_bounds_the_query():
    query = download.query_for(("ENG",), year=2011)
    assert "kpdate:[2011-01-01T00:00:00.0Z TO 2011-12-31T23:59:59.0Z]" in query
    assert "kpdate" not in download.query_for(("ENG",))


def _fake_hudoc(monkeypatch, by_year, total=None):
    """Stand in for the result endpoint over a `{year: [itemid, ...]}` corpus,
    paging two at a time so the slice logic has to page."""
    pages = []

    def search_page(session, start, languages=download.DEFAULT_LANGUAGES,
                    itemid=None, page_size=download.PAGE_SIZE,
                    collection="judgments", year=None):
        if year is None:                      # the collection-total probe
            return {"resultcount": str(sum(len(v) for v in by_year.values())
                                       if total is None else total),
                    "results": []}
        items = by_year.get(year, [])
        window = items[start:start + 2]
        pages.append((year, start))
        return {"resultcount": str(len(items)),
                "results": [{"columns": {"itemid": i}} for i in window]}

    monkeypatch.setattr(download, "search_page", search_page)
    return pages


def test_enumeration_walks_year_by_year_newest_first(monkeypatch):
    by_year = {2026: ["a", "b", "c"], 2025: [], 2024: ["d"]}
    pages = _fake_hudoc(monkeypatch, by_year)
    got = list(download.enumerate_records(None, delay=0, first_year=2024,
                                          last_year=2026))
    assert [r["itemid"] for r in got] == ["a", "b", "c", "d"]
    # years descend, and a year longer than one page is paged through
    assert pages == [(2026, 0), (2026, 2), (2025, 0), (2024, 0)]


def test_a_year_past_the_paging_cap_raises_instead_of_losing_its_tail(monkeypatch):
    """HUDOC answers past start=10000 with an empty page, not an error -- which
    is how the store came to hold judgments back to 2009 and no further."""
    monkeypatch.setattr(download, "search_page",
                        lambda *a, year=None, **kw: {
                            "resultcount": str(download.PAGING_CAP + 1),
                            "results": [{"columns": {"itemid": "x"}}]}
                        if year else {"resultcount": "1", "results": []})
    with pytest.raises(ValueError, match="page over"):
        list(download.enumerate_records(None, delay=0, first_year=2026,
                                        last_year=2026))


def test_a_document_outside_the_harvested_years_raises(monkeypatch):
    _fake_hudoc(monkeypatch, {2026: ["a"]}, total=2)
    with pytest.raises(ValueError, match="outside the harvested years"):
        list(download.enumerate_records(None, delay=0, first_year=2026,
                                        last_year=2026))


def _store(tmp_path, records):
    for record in records:
        download.record_path(tmp_path, record["itemid"]).write_text(
            json.dumps(record), encoding="utf-8")
    return tmp_path


def test_a_summary_joins_its_case_on_application_number_and_date(tmp_path):
    """HUDOC gives a Case-Law Information Note no pointer to the case it
    summarises -- no ECLI, no item id -- so the join is the case's own
    application numbers plus its date."""
    root = _store(tmp_path, [
        {"itemid": "001-1", "appno": "47143/06", "kpdate": "2015-12-04T00:00:00"},
        {"itemid": "001-2", "appno": "9154/10;17249/12",
         "kpdate": "2015-12-15T00:00:00"},
    ])
    matched, unmatched = summaries.resolve(root, [
        {"itemid": "002-a", "docname": "Roman Zakharov", "appno": "47143/06",
         "kpdate": "2015-12-04T00:00:00"},
        # one application of a multi-application case is enough to identify it
        {"itemid": "002-b", "docname": "Szafranski", "appno": "17249/12",
         "kpdate": "2015-12-15T00:00:00"},
        # right application, wrong date: a different case of the same applicant
        {"itemid": "002-c", "docname": "elsewhere", "appno": "47143/06",
         "kpdate": "2011-01-01T00:00:00"},
    ], log=lambda _: None)
    assert {b: r["itemid"] for b, r in matched.items()} == {"001-1": "002-a",
                                                            "001-2": "002-b"}
    assert unmatched == 1


def test_a_key_two_cases_claim_identifies_neither(tmp_path):
    """HUDOC stores some decisions twice and mints one ECLI for decisions taken
    together, so a key can reach two stored cases. It then identifies no case,
    and saying so beats attaching the Court's summary of one case to another.
    (Measured: 10 ECLIs and 121 application/date pairs over 39,046 records.)"""
    root = _store(tmp_path, [
        {"itemid": "001-1", "appno": "1/11", "kpdate": "2015-12-04T00:00:00",
         "languageisocode": "ENG", "ecli": "ECLI:SHARED"},
        {"itemid": "001-2", "appno": "1/11", "kpdate": "2015-12-04T00:00:00",
         "languageisocode": "ENG", "ecli": "ECLI:SHARED"},
        {"itemid": "001-3", "appno": "2/11", "kpdate": "2015-12-04T00:00:00",
         "languageisocode": "ENG", "ecli": "ECLI:OWN"},
    ])
    assert summaries.held_index(root, log=lambda _: None) == {
        ("2/11", "2015-12-04"): "001-3"}
    assert translations.held_by_ecli(root, log=lambda _: None) == {
        "ECLI:OWN": "001-3"}


def test_two_language_versions_of_one_case_refuse_to_index(tmp_path):
    """The other cause of a shared key: a store harvested with --lang ENG,FRE
    holds every case twice, and no join can tell the two apart."""
    root = _store(tmp_path, [
        {"itemid": "001-1", "appno": "1/11", "kpdate": "2015-12-04T00:00:00",
         "languageisocode": "ENG", "ecli": "ECLI:SAME"},
        {"itemid": "001-2", "appno": "1/11", "kpdate": "2015-12-04T00:00:00",
         "languageisocode": "FRE", "ecli": "ECLI:SAME"},
    ])
    with pytest.raises(ValueError, match="in different languages"):
        summaries.held_index(root, log=lambda _: None)
    with pytest.raises(ValueError, match="in different languages"):
        translations.held_by_ecli(root, log=lambda _: None)


def test_a_withdrawn_summary_takes_its_link_off_the_case(tmp_path):
    """A Note the Court withdraws, or re-matches to another case, would keep its
    link on the page forever if the sync only ever wrote."""
    one = {"itemid": "002-a", "docname": "A v. State"}
    two = {"itemid": "002-b", "docname": "B v. State"}
    quiet = {"log": lambda _: None}
    assert summaries.store(tmp_path, {"001-1": one, "001-2": two}, **quiet) == (2, 0)
    assert summaries.read_sidecar(tmp_path, "001-1") == one
    # the same match again writes nothing, so the cases' parses stay fresh
    assert summaries.store(tmp_path, {"001-1": one, "001-2": two}, **quiet) == (0, 0)
    # ... and a run that no longer matches the second takes its sidecar away
    assert summaries.store(tmp_path, {"001-1": one}, **quiet) == (0, 1)
    assert summaries.read_sidecar(tmp_path, "001-1") == one
    assert summaries.read_sidecar(tmp_path, "001-2") is None


def test_matching_nothing_at_all_refuses_to_reap_every_link(tmp_path):
    """The completeness guard cannot tell an empty answer from a real one, so
    the reap must: an endpoint that returns nothing would otherwise take the
    Court's summary off every case page at once."""
    summaries.store(tmp_path, {"001-1": {"itemid": "002-a", "docname": "A"}},
                    log=lambda _: None)
    with pytest.raises(ValueError, match="matched no summary at all"):
        summaries.store(tmp_path, {}, log=lambda _: None)
    assert summaries.read_sidecar(tmp_path, "001-1") is not None


def test_a_stored_summary_becomes_a_link_on_the_case(tmp_path):
    record = json.loads((FIXTURES / "001-123456.json").read_text())
    html = (FIXTURES / "001-123456.html").read_text()
    plain = parse.parse_record(record, html).to_artifact()
    assert "summary" not in plain
    linked = parse.parse_record(record, html, {
        "itemid": "002-10954", "docname": "Roman Zakharov v. Russia [GC]",
    }).to_artifact()
    assert linked["summary"] == {
        "itemid": "002-10954", "title": "Roman Zakharov v. Russia [GC]",
        "url": "https://hudoc.echr.coe.int/eng?i=002-10954"}
    # ... and reaches the page as an outbound link, not as body text
    assert "hudoc.echr.coe.int/eng?i=002-10954" in \
        hudoc_render._summary_link(linked["summary"])
    assert hudoc_render._summary_link(None) is None


def test_a_translation_annotates_the_judgment_it_translates():
    """The Swedish translation is commentary on the judgment, the way an English
    translation of a Swedish statute is commentary on the statute."""
    assert layout.kommentar_host("001-159324") == "hudoc"
    assert wiki_parse.host_uri("001-159324") == \
        "https://lagen.nu/dom/echr/001-159324"
    assert translations.commentary_path("/w", "001-159324") == \
        Path("/w/commentary/hudoc/001-159324.md")
    draft = translations.draft("001-159324", {"itemid": "001-167574"})
    assert "annotates: 001-159324" in draft
    assert "https://hudoc.echr.coe.int/eng?i=001-167574" in draft
    assert "Domstolsverket" in draft


def test_a_translation_of_an_unheld_case_is_reported_not_guessed(tmp_path,
                                                                 monkeypatch):
    root = _store(tmp_path, [{"itemid": "001-1", "ecli": "ECLI:HELD"}])
    monkeypatch.setattr(translations, "translation_records", lambda *a, **kw: [
        {"itemid": "001-swe", "ecli": "ECLI:HELD",
         "docname": "CASE OF A v. B - [Swedish Translation] by %s"
                    % translations.TRANSLATOR},
        {"itemid": "001-orphan", "ecli": "ECLI:MISSING",
         "docname": "CASE OF C v. D - [Swedish Translation] by %s"
                    % translations.TRANSLATOR},
    ])
    matched, unmatched, doubled = translations.proposals(None, root)
    assert [b for b, _ in matched] == ["001-1"]
    assert [r["itemid"] for r in unmatched] == ["001-orphan"]
    assert doubled == []


def test_an_unexpected_translator_stops_the_draft(tmp_path, monkeypatch):
    root = _store(tmp_path, [{"itemid": "001-1", "ecli": "ECLI:HELD"}])
    monkeypatch.setattr(translations, "translation_records", lambda *a, **kw: [
        {"itemid": "001-swe", "ecli": "ECLI:HELD",
         "docname": "CASE OF A v. B - [Swedish Translation] by Someone Else"},
    ])
    with pytest.raises(ValueError, match="names translator"):
        translations.proposals(None, root)


def test_parse_hudoc_fixture_to_artifact():
    record = json.loads((FIXTURES / "001-123456.json").read_text())
    html = (FIXTURES / "001-123456.html").read_text()
    art = parse.parse_record(record, html).to_artifact()
    assert art["uri"] == "https://lagen.nu/dom/echr/001-123456"
    assert art["doctype"] == "judgment"
    assert art["avgorandedatum"] == "2024-03-12"
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


def test_opinion_in_running_prose_is_not_a_heading():
    # the OPINION branch requires the paragraph to be all-caps: a separate
    # opinion's title is, while prose quoting "political or other opinion" or
    # citing "the opinion of the Ombudsperson" is not (X2, 001-188991)
    html = """
    <div>
      <p>THE FACTS</p>
      <p>1. It drew the Court's attention to the opinion of the Ombudsperson
      and to the medical experts' opinions dated the same day.</p>
      <p>JOINT DISSENTING OPINION OF JUDGES GROZEV AND O'LEARY</p>
      <p>2. The rights shall be secured without discrimination on any ground
      such as political or other opinion, national or social origin.</p>
    </div>
    """
    blocks = parse.parse_body(html)
    assert [(block.kind, block.level) for block in blocks] == [
        ("rubrik", 1),
        ("stycke", 1),
        ("rubrik", 1),
        ("stycke", 1),
    ]
    assert blocks[2].text == "JOINT DISSENTING OPINION OF JUDGES GROZEV AND O'LEARY"


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
    with pytest.raises(SkipDocument, match="neither a numbered paragraph nor"):
        parse.parse_record(record, html)


class _Answer:
    """The two answers the body endpoint gives: a 204 for an item HUDOC holds
    as metadata only, and a 200 whose payload is not markup."""

    def __init__(self, status_code, text):
        self.status_code, self.text = status_code, text


def test_an_item_hudoc_holds_no_text_for_is_stored_not_refetched(monkeypatch):
    """HUDOC answers 204 No Content for an item it holds as metadata only --
    GREECE v. THE UNITED KINGDOM (1956) and the other pre-1980 Commission
    decisions. The empty body is stored, because that is what makes the item
    count as downloaded; raising instead left 11 records with no body, refetched
    on every run and failing `lagen hudoc parse` forever."""
    answers = iter([_Answer(204, ""), _Answer(200, "not markup")])
    monkeypatch.setattr(download, "request", lambda *a, **kw: next(answers))
    assert download.fetch_body(None, "001-1", 0).text == ""
    # a 200 that is not markup is still a broken fetch, and still raises
    with pytest.raises(ValueError, match="empty HTML body"):
        download.fetch_body(None, "001-2", 0)


def test_an_empty_body_is_skipped_as_having_no_text(tmp_path):
    record = json.loads((FIXTURES / "001-123456.json").read_text())
    with pytest.raises(SkipDocument, match="holds no text for this item"):
        parse.parse_record(record, "")
    # ... and the stored empty body reaches parse as exactly that
    download.record_path(tmp_path, "001-1").write_text(json.dumps(record))
    download.body_path(tmp_path, "001-1").write_text("")
    with pytest.raises(SkipDocument, match="holds no text for this item"):
        parse.parse("001-1", tmp_path)


def test_an_unnumbered_decision_is_a_document_not_an_empty_artifact():
    """A decision states its facts and its reasoning under headings and numbers
    nothing (this one strikes an Article 3 complaint out of the list). The skip
    guard used to test for a numbered paragraph, which is a judgment's shape, so
    62% of the decisions collection parsed to a zero-byte artifact."""
    record = json.loads((FIXTURES / "001-212739.json").read_text())
    html = (FIXTURES / "001-212739.html").read_text()
    body = parse.parse_body(html)
    assert not any(block.number for block in body)   # nothing is numbered ...
    assert any(block.kind == "rubrik" for block in body)  # ... but it has structure

    art = parse.parse_record(record, html).to_artifact()
    assert art["uri"] == "https://lagen.nu/dom/echr/001-212739"
    assert art["doctype"] == "decision"
    assert art["title"] == "DOBRE v. ROMANIA"
    assert len(art["structure"]) == len(body)
    # the unnumbered blocks still get stable anchors to link and cite
    assert [n["id"] for n in art["structure"][1:4]] == ["S1", "S2", "S3"]
    assert "FOURTH SECTION" in art["structure"][0]["text"][0]


def test_hudoc_layout_and_catalog():
    uri = "https://lagen.nu/dom/echr/001-123456"
    assert layout.relpath("hudoc", "001-123456").as_posix() == "001-123456"
    assert layout.page_url(uri) == "/dom/echr/001-123456"
    assert layout.url_to_relpath("/dom/echr/001-123456") == \
        "dom/dom_echr_001_123456.html"
    assert page.human_fragment("A6P3Ld") == "artikel 6 punkt 3 led d"
    assert page.human_fragment("A25P1-2") == "artikel 25 punkt 1 variant 2"
    assert page.human_fragment("AII.1") == "artikel II.1"
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
    # (anchor, page, run) -- a metadata reference belongs to the document, so
    # it carries neither an anchor nor a printed page
    assert catalog.artifact_links(art) == [
        (None, None,
         {"uri": target, "predicate": "dcterms:references", "text": "8"})]


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
            "avgorandedatum": "2024-03-12",
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
    site = page.Site(con, {treaty["uri"], case["uri"]})
    html = coe_render.render(treaty, site)
    assert "Europadomstolens praxis" in html
    assert "CASE OF EXAMPLE v. SWEDEN" in html
    assert 'id="A8"' in html
    # the citing side names the article, never the raw fragment id: the treaty
    # lives under ext/ but is hosted here, so the reference links to our own
    # article anchor with the curated short name (X3)
    case_html = hudoc_render.render(case, site)
    assert "artikel 8 EKMR" in case_html
    assert ">005#A8<" not in case_html
    assert '<a href="/coe/005#A8">' in case_html
