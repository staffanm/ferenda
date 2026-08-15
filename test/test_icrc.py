"""ICRC IHL treaty parsing, artifact projection, and folkrätt integration.

Everything runs off the committed JSON:API fixture (a trimmed real Geneva
Convention I envelope) plus small synthetic dicts -- no network.
"""

import json
from pathlib import Path

from accommodanda.icrc import download, parse
from accommodanda.icrc import render as icrc_render
from accommodanda.icrc.model import Treaty, treaty_uri
from accommodanda.lib import catalog, compress, facets, layout, page, render

FIXTURES = Path(__file__).parent / "files" / "icrc"


def _gci():
    return parse.parse("365", FIXTURES)


# --------------------------------------------------------------------------
# model identity
# --------------------------------------------------------------------------

def test_treaty_uri_and_kind():
    assert treaty_uri("365") == "https://lagen.nu/ext/icrc/365"
    assert Treaty("365", "Geneva Convention (I) …",
                  treaty_type="geneva_conventions").kind == "treaty"
    assert Treaty("470", "Additional Protocol (I) to the Geneva Conventions",
                  treaty_type="additional_protocols").kind == "protocol"
    # a protocol is recognised from its title even without the type
    assert Treaty("999", "Protocol on Blinding Laser Weapons").kind == "protocol"
    assert Treaty("105", "Paris Declaration Respecting Maritime Law").kind \
        == "declaration"


# --------------------------------------------------------------------------
# parse: metadata + article tree + states parties
# --------------------------------------------------------------------------

def test_parse_treaty_metadata():
    art = _gci()
    assert art["uri"] == "https://lagen.nu/ext/icrc/365"
    assert art["type"] == "internationell-overenskommelse"
    assert art["doctype"] == "treaty"
    assert art["number"] == "365"
    assert art["date"] == "1949-08-12"
    assert art["identifier"].startswith("Geneva Convention (I)")
    md = art["metadata"]
    assert md["depositary"] == "Switzerland"      # not the UN -- outside the MTDSG
    assert md["entryIntoForce"] == "1950-10-21"
    assert md["inForce"] is True
    assert md["historical"] is False
    assert md["languages"] == ["English", "French"]
    assert md["topics"] == ["Victims of Armed Conflicts"]
    assert md["statesParties"] == 3               # the fixture keeps three
    assert art["source_url"] == \
        "https://ihl-databases.icrc.org/en/ihl-treaties/gci-1949"
    assert art["summary"].startswith("This Convention represents the fourth")


def test_parse_structure_has_stable_fragment_ids():
    art = _gci()
    articles = [n for n in art["structure"] if n["type"] == "artikel"]
    # articles carry A<n> fragments; the preamble its own stable id
    assert [n["id"] for n in articles] == ["Preamble", "A1", "A2", "A3"]
    # chapter divisions are headings, not provisions
    rubriker = [n["text"][0] for n in art["structure"] if n["type"] == "rubrik"]
    assert "Chapter I : General provisions" in rubriker
    assert "Title of the Convention" in rubriker
    # the article body splits into stycken with contextual ids; Common Article 3
    # (the famous seven-paragraph provision) survives intact
    a1 = next(n for n in articles if n["id"] == "A1")
    assert a1["ordinal"] == "1"
    assert a1["children"][0]["id"] == "A1S1"
    assert a1["children"][0]["text"][0].startswith(
        "The High Contracting Parties undertake to respect")
    a3 = next(n for n in articles if n["id"] == "A3")
    assert len(a3["children"]) == 7


def test_parse_states_parties_carry_action_and_reservations():
    parties = _gci()["parties"]
    assert len(parties) == 3
    assert parties[0] == {"country": "Barbados", "action": "accession",
                          "date": "1968-09-10"}
    # a declaration carries its reservation text; the plain actions do not
    declaration = next(p for p in parties if p["action"] == "declaration")
    assert declaration["reservation"].startswith("Declaration made upon succession")
    assert "reservation" not in parties[0]


# --------------------------------------------------------------------------
# download: identity + the change-stamp incremental logic (no network)
# --------------------------------------------------------------------------

def test_record_path_and_list_basefiles(tmp_path):
    compress.write_download(download.record_path(tmp_path, "365"), "{}")
    compress.write_download(tmp_path / ".watermark.json", "{}")
    assert download.list_basefiles(tmp_path) == ["365"]   # skips the watermark


def test_incremental_resolve_refetches_only_on_changed_stamp(tmp_path, monkeypatch):
    def envelope(changed):
        return {"data": [{"attributes": {"field_treaty_number": 365,
                                         "changed": changed}}]}
    calls = []

    def fake_fetch(session, number):
        calls.append(number)
        return envelope("2020-01-01T00:00:00+00:00")

    monkeypatch.setattr(download, "fetch_treaty", fake_fetch)
    record = {"number": "365", "date": "1949-08-12",
              "changed": "2020-01-01T00:00:00+00:00"}
    # first pass downloads; a second with the same stamp does not
    assert download.resolve(None, tmp_path, record) is True
    assert download.resolve(None, tmp_path, record) is False
    assert calls == ["365"]
    # an advanced stamp re-fetches; --full forces a re-fetch regardless
    assert download.resolve(None, tmp_path,
                            {**record, "changed": "2021-06-06T00:00:00+00:00"}) is True
    assert download.resolve(None, tmp_path, record, full=True) is True


def test_enumerate_dedupes_and_completes_across_page_boundaries(monkeypatch):
    # a treaty repeated at a page boundary (the anomaly offset paging over a
    # mutable sort key produced live, dropping other treaties) must not inflate
    # the count or shadow a distinct treaty; the stable-key paging + dedup by
    # number returns each treaty exactly once.
    def node(number):
        return {"attributes": {"field_treaty_number": number,
                               "field_treaty_date_of_adoption": "1949-08-12",
                               "changed": "2020-01-01T00:00:00+00:00"}}
    pages = {0: [node(365), node(370), node(375)],
             3: [node(375), node(380)]}          # 375 repeats at the boundary

    def fake_request(session, method, url, **kwargs):
        offset = kwargs["params"]["page[offset]"]
        return {"data": pages.get(offset, []), "meta": {"count": 5}}

    monkeypatch.setattr(download, "request", fake_request)
    monkeypatch.setattr(download, "PAGE_SIZE", 3)
    numbers = [r["number"] for r in download.enumerate_treaties(session=None)]
    assert sorted(numbers) == ["365", "370", "375", "380"]   # four distinct, no dup


# --------------------------------------------------------------------------
# layout + catalog wiring
# --------------------------------------------------------------------------

def test_icrc_layout_round_trips_and_catalog_row():
    uri = "https://lagen.nu/ext/icrc/365"
    assert layout.page_url(uri) == "/icrc/365"
    assert layout.page_relpath(uri) == "icrc/365.html"
    assert str(layout.url_to_relpath("/icrc/365")) == "icrc/365.html"
    assert layout.relpath("icrc", "365") == Path("365")
    assert "icrc" in facets.sources()
    art = _gci()
    row = catalog.icrc_document(art, "artifact/icrc/365.json")
    assert row[:3] == (uri, "icrc", "treaty")
    assert row[3].startswith("Geneva Convention (I)")     # label = identifier


# --------------------------------------------------------------------------
# folkrätt landing + treaty page
# --------------------------------------------------------------------------

def _other(number, title, topic, doctype="treaty"):
    return {"uri": treaty_uri(number), "number": number, "doctype": doctype,
            "type": "internationell-overenskommelse", "identifier": title,
            "title": title, "date": "1907-10-18",
            "metadata": {"statesParties": 0, "topics": [topic]},
            "references": [], "structure": []}


def test_folkratt_lists_icrc_central_then_topic_index(tmp_path):
    gci = _gci()                                          # 365, central (GK I)
    ap = _other("470", "Additional Protocol (I) to the Geneva Conventions",
                "Victims of Armed Conflicts", doctype="protocol")   # central (TP I)
    hague = _other("195", "Hague Convention (IV) on War on Land",
                   "Methods and Means of Warfare")        # -> Stridsmetoder group
    exotic = _other("300", "Some Instrument on an Unmapped Subject",
                    "A Topic Not In The Taxonomy")        # -> catch-all Övriga
    paths = []
    for art in (gci, ap, hague, exotic):
        p = tmp_path / (art["number"] + ".json")
        p.write_text(json.dumps(art, ensure_ascii=False))
        paths.append(p)
    database = str(tmp_path / "catalog.sqlite")
    catalog.rebuild(database, "icrc", paths)
    con = catalog.connect(database)
    html = render.render_folkratt(con)

    # the ICRC block leads with the curated central instruments, then a subject
    # index carved by the ICRC topic taxonomy
    assert "Internationell humanitär rätt (ICRC)" in html
    assert "Genèvekonventionerna och tilläggsprotokollen" in html
    assert "Stridsmetoder och stridsmedel" in html       # Hague files here by topic
    assert html.index("Genèvekonventionerna") < html.index("Stridsmetoder")
    # a central treaty shows its curated Swedish name + acronym + ICRC number
    assert "(Första Genèvekonventionen, GK I, ICRC 365)" in html
    assert 'href="/icrc/365"' in html
    # a treaty whose topic is outside the taxonomy lands in the catch-all group
    assert "Övriga fördrag" in html
    assert "(ICRC 300)" in html
    assert html.index("Stridsmetoder") < html.index("Övriga fördrag")
    # the shared Dokumenttyp selector gains an IHL-fördrag bucket
    assert "IHL-fördrag" in html


def test_render_treaty_page_highlights_folkratt_and_shows_metadata(tmp_path):
    art = _gci()
    p = tmp_path / "365.json"
    p.write_text(json.dumps(art, ensure_ascii=False))
    database = str(tmp_path / "catalog.sqlite")
    catalog.rebuild(database, "icrc", [p])
    con = catalog.connect(database)
    html = icrc_render.render(art, page.Site(con, {art["uri"]}))
    # the Folkrätt masthead entry is current for an IHL treaty page
    assert '<a href="/folkratt/" class="on">Folkrätt</a>' in html
    # the metadata block surfaces depositary + participation, the body the articles
    assert "Switzerland" in html and "Depositarie" in html
    assert "Article 3 - Conflicts not of an international character" in html
    assert art["source_url"] in html                     # the "Källa" link


def test_an_undivided_treaty_keeps_its_text():
    """"empty" is the ICRC's label for a treaty with no section divisions -- the
    19th-century declarations are one block of text -- not for a block with no
    content. Reading it as "nothing here" dropped the whole text of 32 treaties
    (the Paris Declaration of 1856, St Petersburg, the Hague declarations),
    leaving pages that were metadata and nothing else (V2)."""
    envelope = {"data": [{"attributes": {"field_treaty_number": "1",
                                         "title": "Paris Declaration, 1856"},
                          "relationships": {"field_treaty_content": {"data": [
                              {"type": "paragraph--treaty_content", "id": "p1"}]}}}],
                "included": [{"type": "paragraph--treaty_content", "id": "p1",
                              "attributes": {
                                  "field_treaty_content_section": "empty",
                                  "field_treaty_content_title": "Declaration",
                                  "field_treaty_content_pre_title": "Declaration",
                                  "field_treaty_content_content":
                                      "<p>Privateering is and remains abolished.</p>"}}]}
    treaty = parse.parse_envelope(envelope)
    [provision] = treaty.provisions
    # anchored by what the treaty calls its own text, never by the word "empty"
    assert provision.fragment == "Declaration"
    assert "Privateering is and remains abolished." in " ".join(provision.paragraphs)


def _empty_block(pid, title, content):
    return {"type": "paragraph--treaty_content", "id": pid,
            "attributes": {"field_treaty_content_section": "empty",
                           "field_treaty_content_title": title,
                           "field_treaty_content_pre_title": title,
                           "field_treaty_content_content": content}}


def _article(pid, ordinal):
    return {"type": "paragraph--treaty_content", "id": pid,
            "attributes": {"field_treaty_content_section": "Article",
                           "field_treaty_content_pre_title": "Article %s" % ordinal,
                           "field_treaty_content_title": "Heading %s" % ordinal,
                           "field_treaty_content_content": "<p>Text %s.</p>" % ordinal}}


def _envelope(ids, included):
    return {"data": [{"attributes": {"field_treaty_number": "2",
                                     "title": "Convention (I), 1949"},
                      "relationships": {"field_treaty_content": {"data": [
                          {"type": "paragraph--treaty_content", "id": pid}
                          for pid in ids]}}}],
            "included": included}


def test_an_unlabelled_block_with_text_is_kept():
    """A treaty that has articles still carries unlabelled *operative* parts --
    Final Protocol, Additional article, Technical Annex, 69 blocks in the
    corpus. So "the treaty has articles" cannot be the drop rule; carrying text
    is what makes an unlabelled block part of the treaty."""
    treaty = parse.parse_envelope(_envelope(
        ("front", "art", "final"),
        [_empty_block("front", "Acknowledgements and Abbreviations", None),
         _article("art", "1"),
         _empty_block("final", "Final Protocol", "<p>Done at Geneva.</p>")]))
    assert [p.fragment for p in treaty.provisions] == ["A1", "FinalProtocol"]


def test_a_textless_unlabelled_block_between_articles_is_a_heading():
    """Where a textless unlabelled block sits says what it is. "Final
    provisions" stands between article 30 and article 31 of the 1929 Geneva
    Convention and heads the articles after it, exactly as a `Chapter` block
    would -- dropping it would lose the division over the final articles. The
    other 17 textless blocks in the corpus sit outside the run of articles
    (Acknowledgements and Introduction before it, Sources and Conclusion
    after), and are commentary."""
    treaty = parse.parse_envelope(_envelope(
        ("intro", "a1", "final", "a2", "sources"),
        [_empty_block("intro", "Introduction", None),
         _article("a1", "1"),
         _empty_block("final", "Final provisions", None),
         _article("a2", "2"),
         _empty_block("sources", "Sources", None)]))
    assert [(p.kind, p.heading) for p in treaty.provisions] == [
        ("artikel", "Heading 1"),
        ("rubrik", "Final provisions"),
        ("artikel", "Heading 2")]


def test_cross_treaty_citations_link_and_self_is_excluded():
    """R4: AP II article 1 cites common article 3 and its sibling protocol by
    the full official citation. The family reference lands in `references`
    (four instruments, no inline guess); the parenthesised short form links
    inline because the family is named beside it (generic-name context rule)."""
    from accommodanda.icrc.model import Provision
    treaty = Treaty(
        number="475", title="Protocol Additional (II) …",
        treaty_type="additional_protocols",
        provisions=[Provision(
            "artikel", "Article", "Article 1", fragment="A1", ordinal="1",
            paragraphs=["This Protocol, which develops and supplements "
                        "Article 3 common to the Geneva Conventions of "
                        "12 August 1949; and the Protocol Additional to the "
                        "Geneva Conventions of 12 August 1949, and relating "
                        "to the Protection of Victims of International Armed "
                        "Conflicts (Protocol I) …"])])
    art = parse.artifact(treaty)
    # the first "Geneva Conventions" is bound by common article 3; the second
    # (inside the Protocol I citation) is unbound and references the family
    assert sorted(r["uri"].replace("https://lagen.nu/ext/", "")
                  for r in art["references"]) == \
        ["icrc/365", "icrc/365#A3", "icrc/370", "icrc/370#A3",
         "icrc/375", "icrc/375#A3", "icrc/380", "icrc/380#A3", "icrc/470"]
    runs = art["structure"][0]["children"][0]["text"]
    assert [(run["text"], run["uri"].replace("https://lagen.nu/ext/", ""))
            for run in runs if isinstance(run, dict)] == \
        [("Protocol I", "icrc/470")]
