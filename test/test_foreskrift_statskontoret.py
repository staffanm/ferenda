"""STKFA/ESVFA: the EA-regelverket tree walk, and its typed HTML as a body.

Both fixtures are trimmed live captures (2026-09-18), in the page's own shape:
the page shell, the regelverk document embedded as its own ``<body>``, and
inside it the page's top level in order -- the title heading, the typed
``div.foreskrifter`` / ``div.allmanna-rad`` sections, the outer chapter and
rubrik headings the page prints over them, and the bare ``p`` runs in between,
which are Forum's mirror of the förordning the föreskrifter advise on.
``statskontoret-ea-index.html`` is the root's own anchors, and
``statskontoret-ea-page.html`` is the leaf page that carries ESVFA 2022:1
(``/ea-regelverket/redovisning/arsredovisning-och-budgetunderlag/``);
``statskontoret-ea-page-kompensation.html`` is the leaf page that carries
ESVFA 2022:7, whose sections repeat the page's own outer headings with a
scope suffix.
"""

import types
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from ferenda.foreskrift import harvest, parse, statskontoret
from ferenda.foreskrift.agencies import REGISTRY
from ferenda.lib import compress
from ferenda.lib.errors import UpstreamChanged
from ferenda.lib.util import record_path

FILES = Path(__file__).parent / "files" / "foreskrift"
INDEX = REGISTRY["stkfa"].index_url
PAGE = INDEX + "redovisning/arsredovisning-och-budgetunderlag/"


class Saved:
    """A session serving a page per URL: a fixture file name, or the body itself
    for the shapes a test writes inline. An unlisted URL answers an empty page
    rather than raising -- the walk follows the site's own nav, and the fixtures
    trim it, so most of what the root links is simply not part of a test."""

    def __init__(self, pages):
        self.pages = pages
        self.asked = []

    def request(self, method, url, **kwargs):
        self.asked.append(url)
        page = self.pages.get(url, "<html><body></body></html>")
        body = page if page.lstrip().startswith("<") \
            else (FILES / page).read_text("utf-8")
        return types.SimpleNamespace(text=body, content=body.encode("utf-8"),
                                     status_code=200, headers={}, url=url)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    serve = (lambda session, method, url, **kw:
             session.request(method, url, **kw))
    monkeypatch.setattr(statskontoret, "request", serve)
    monkeypatch.setattr(harvest, "request", serve)


def parse_page_of(fixture, url):
    return statskontoret.parse_page((FILES / fixture).read_text("utf-8"), url)


def walk(pages=None):
    session = Saved(pages if pages is not None
                    else {INDEX: "statskontoret-ea-index.html",
                          PAGE: "statskontoret-ea-page.html"})
    agency = REGISTRY["stkfa"]
    return list(statskontoret.enumerate_regulations(session, agency)), session


# a second leaf, served under a made-up tree path: the page that carries
# ESVFA 2022:7, whose sections repeat the page's own outer headings
PAGE2 = INDEX + "redovisning/avdrag-mervardesskatt/"


def parsed_of(tmp_path, fixture):
    pages = {INDEX: '<html><body><a href="%s">s</a></body></html>'
             % PAGE2[len(INDEX):],
             PAGE2: fixture}
    refs, _ = walk(pages)
    [ref] = refs
    record = statskontoret.resolve(None, REGISTRY["stkfa"], ref, tmp_path,
                                   delay=0)
    return parse.parse_record(record, str(tmp_path))


# --------------------------------------------------------------------------
# the registry
# --------------------------------------------------------------------------

def test_one_live_scope_serves_both_samlingar():
    # ESVFA issues nothing new, so it has no harvester of its own -- but its
    # documents are the EA-regelverket's current text, so they arrive through
    # STKFA and are filed under their own samling
    assert REGISTRY["stkfa"].enumerate is statskontoret.enumerate_regulations
    assert REGISTRY["esvfa"].enumerate is None
    assert REGISTRY["esvfa"].designation == "ESVFA"


# --------------------------------------------------------------------------
# the tree walk
# --------------------------------------------------------------------------

def test_the_walk_lists_the_regulation_a_leaf_page_carries():
    refs, session = walk()
    [ref] = refs
    assert ref.basefile == "esvfa/2022:1"
    assert ref.identifier == "ESVFA 2022:1"
    # the page is the document, so its own fs decides where the record is filed,
    # even though the walk is the stkfa scope's
    assert ref.fs == "esvfa"
    assert ref.url == PAGE
    assert ref.title == ("Ekonomistyrningsverkets föreskrifter och allmänna råd "
                         "(ESVFA 2022:1) om årsredovisning och budgetunderlag")
    # the page states when it last changed, which is what tells a later run that
    # a consolidation has moved on without taking a new number
    assert ref.extra["updated_at"] == "2026-01-20 09:54:34"
    # the root is walked, and each page once however many times it is linked
    assert session.asked[0] == INDEX
    assert len(session.asked) == len(set(session.asked))


def test_the_walk_follows_only_the_ea_tree():
    refs, session = walk({
        INDEX: """<html><body>
            <a href="/ea-regelverket/finansiering/">section</a>
            <a href="/ea-regelverket/finansiering/?p=1#x">the same page, adorned</a>
            <a href="/om-oss/">elsewhere on the site</a>
            <a href="https://www.regeringen.se/">another host</a>
            <a href="mailto:ea@statskontoret.se">not a page at all</a>
        </body></html>""",
        INDEX + "finansiering/": "statskontoret-ea-page.html",
    })
    assert [ref.basefile for ref in refs] == ["esvfa/2022:1"]
    assert session.asked == [INDEX, INDEX + "finansiering/"]


def test_a_page_that_names_no_regulation_is_no_document():
    # the root and the three section pages hang no regelverk-page__box
    assert parse_page_of("statskontoret-ea-index.html", INDEX) is None


def test_a_register_that_names_nothing_is_an_upstream_change():
    # the walk reaching zero regulations is the site having changed, not a
    # register that emptied: 10 documents do not go away at once
    with pytest.raises(UpstreamChanged, match="no page under"):
        walk({INDEX: "<html><body><p>Sidan kunde inte hittas</p></body></html>"})


def test_a_named_regulation_with_no_sections_is_an_upstream_change():
    with pytest.raises(UpstreamChanged, match="hangs no föreskrift section"):
        walk({INDEX: """<html><body><div class="regelverk-page__box">
            <h2>Statskontorets föreskrifter och allmänna råd (STKFA 2026:1)
                om något</h2></div></body></html>"""})


def test_the_walk_is_bounded():
    # the link filter follows what the page names; a nav change that starts
    # linking the whole site must fail loudly rather than crawl it
    pages = {INDEX: "".join(
        '<a href="/ea-regelverket/p%d/">%d</a>' % (n, n)
        for n in range(statskontoret.PAGE_LIMIT + 10))}
    pages[INDEX] = "<html><body>%s</body></html>" % pages[INDEX]
    with pytest.raises(AssertionError, match="passed 500 pages"):
        walk(pages)


# --------------------------------------------------------------------------
# storing the page
# --------------------------------------------------------------------------

def test_resolve_stores_the_page_as_the_current_consolidation(tmp_path):
    refs, _ = walk()
    agency = REGISTRY["stkfa"]

    record = statskontoret.resolve(None, agency, refs[0], tmp_path, delay=0)

    assert record["fs"] == "esvfa"
    assert record["publisher"] == "Ekonomistyrningsverket"
    assert record["updated_at"] == "2026-01-20 09:54:34"
    # the page is the consolidated text and there is no separately published
    # as-enacted version, so the record hangs no `regulation` file
    assert record["files"]["regulation"] is None
    [consolidation] = record["files"]["consolidation"]
    # named off the basefile like every other stored body (`save_single_pdf_record`)
    assert consolidation == {"name": "esvfa-2022-1-consolidation.html", "url": PAGE}
    stored = compress.read_text(tmp_path / "esvfa" / consolidation["name"])
    assert stored.startswith("<main>") and 'class="foreskrifter"' in stored
    assert compress.read_json(record_path(tmp_path, "esvfa", "esvfa/2022:1")) == record


def test_a_changed_page_timestamp_restages_a_stored_record(tmp_path):
    # a consolidation moves on without taking a new number, so "the record is on
    # disk" is not "the record is current" for this source
    [ref], _ = walk()
    agency = REGISTRY["stkfa"]
    statskontoret.resolve(None, agency, ref, tmp_path, delay=0)

    assert harvest.item_key(agency, tmp_path, ref).is_downloaded is True

    ref.extra["updated_at"] = "2026-02-01 00:00:00"
    assert harvest.item_key(agency, tmp_path, ref).is_downloaded is False


# --------------------------------------------------------------------------
# the page as a body
# --------------------------------------------------------------------------

def parsed(tmp_path):
    refs, _ = walk()
    record = statskontoret.resolve(None, REGISTRY["stkfa"], refs[0], tmp_path,
                                   delay=0)
    return parse.parse_record(record, str(tmp_path))


def nodes(items):
    for item in items:
        yield item
        yield from nodes(item.get("children", []))


def chapter_name(chapter):
    # a chapter's title is carried by its first rubrik child, the way the
    # chapter-marker heading lands; a chapter opened by a marker of its own
    # also keeps that marker as its first rubrik
    if chapter.get("text"):
        return chapter["text"][0]
    [first] = chapter["children"][:1]
    assert first["type"] == "rubrik"
    return first["text"][0]


# a heading that says no topic of its own: the type word, with or without the
# "till förordningen" suffix the page prints
BARE_TYPE = {"föreskrifter", "föreskrifter till förordningen",
             "allmänna råd", "allmänna råd till förordningen"}


def test_the_page_parses_as_the_regulations_consolidated_text(tmp_path):
    reg = parsed(tmp_path)
    assert reg.structure == []          # no separately published as-enacted text
    [cons] = reg.consolidations
    kinds = {n["type"] for n in nodes(cons.structure)}
    assert {"kapitel", "paragraf", "allmanna_rad"} <= kinds
    assert any(n["type"] == "paragraf" and n.get("ordinal") == "1"
               for n in nodes(cons.structure))


# --------------------------------------------------------------------------
# the page's own headings, kept and de-duplicated
# --------------------------------------------------------------------------

def test_the_pages_outer_headings_are_text_of_the_regulation(tmp_path):
    # the page prints each chapter as its own heading, over the typed sections
    # that carry it. A parser that kept only the typed sections dropped every
    # chapter the regulation's text has no section of its own for -- the page
    # is the document, and its outer headings are the document's words.
    reg = parsed(tmp_path)
    [cons] = reg.consolidations
    chapters = {chapter_name(c) for c in cons.structure
                if c["type"] == "kapitel"}
    assert "1 kap. Inledande bestämmelser" in chapters
    assert "2 kap. Allmänna bestämmelser om årsredovisning" in chapters
    assert "9 kap. Budgetunderlag och underlag för fördjupad prövning" in chapters
    rubriker = {n["text"][0] for n in nodes(cons.structure)
                if n["type"] == "rubrik"}
    assert "Förordningens tillämpningsområde" in rubriker
    assert "Årsredovisningens avlämnande" in rubriker


def test_the_mirrored_forordning_text_is_not_the_regulations(tmp_path):
    # between the page's headings, Forum prints the förordning the
    # föreskrifter advise on, as bare paragraphs. That is the underlying
    # act's text: keeping it published the act twice, once as the agency's
    # own words.
    reg = parsed(tmp_path)
    [cons] = reg.consolidations
    text = " ".join(str(n["text"][0])
                    for n in nodes(cons.structure) if n.get("text"))
    assert "gäller för myndigheter som lyder omedelbart under regeringen" \
        not in text
    assert "skall senast den 22 februari" not in text


def test_a_bare_type_word_is_not_published_as_a_heading(tmp_path):
    # the page wraps groups of provisions in sections whose heading is only
    # the type word, "Föreskrifter". Published, that word stood as a rubrik
    # over the provisions -- a heading that says what every one of them is.
    for fixture in ("statskontoret-ea-page.html",
                    "statskontoret-ea-page-kompensation.html"):
        reg = parsed_of(tmp_path, fixture)
        [cons] = reg.consolidations
        rubriker = {n["text"][0] for n in nodes(cons.structure)
                    if n["type"] == "rubrik"}
        assert not {t for t in rubriker
                    if t.rstrip(".").lower() in BARE_TYPE}


def test_an_outer_heading_is_not_repeated_by_the_section_under_it(tmp_path):
    # the typed section repeats the outer heading over it with a scope suffix
    # ("Kompensation – föreskrifter till 4 § förordningen" under the page's
    # own "Kompensation"). The outer heading is kept, so the repeat must not
    # stand too: once published, the regulation had two Kompensation headings
    # and no Tillämpningsområde of its own.
    reg = parsed_of(tmp_path, "statskontoret-ea-page-kompensation.html")
    [cons] = reg.consolidations
    rubriker = [n["text"][0] for n in nodes(cons.structure)
                if n["type"] == "rubrik"]
    assert rubriker.count("Kompensation") == 1
    assert "Tillämpningsområde" in rubriker
    assert not any(t.startswith("Kompensation –") for t in rubriker)
    # the de-duplicated section still carries its provisions
    assert any(n["type"] == "paragraf" and n.get("ordinal") == "6"
               for n in nodes(cons.structure))


def test_an_allmanna_rad_section_keeps_its_own_heading(tmp_path):
    reg = parsed(tmp_path)
    [cons] = reg.consolidations
    rad = [n for n in nodes(cons.structure) if n["type"] == "allmanna_rad"]
    assert rad, "the page's allmanna-rad sections did not survive"
    # the heading names the provision the advice explains, which is what links
    # the two -- and the advice is never a further stycke of the binding §
    assert any("Allmänna råd till" in str(n["text"][0]) for n in rad)


def test_a_table_in_its_layout_wrapper_survives(tmp_path):
    # the page wraps its tables in `div.inner-content-table`. Read as prose, the
    # whole of "Tabell 1 Översikt över verksamhetens finansiering" runs into one
    # line of running text; skipped as an unknown element, it is lost outright.
    reg = parsed(tmp_path)
    [cons] = reg.consolidations
    [table] = [n for n in nodes(cons.structure) if n["type"] == "tabell"]
    # a tabell's rows are `rad` children whose `cells` are run lists
    rows = [[" ".join(str(r) for r in cell) for cell in rad["cells"]]
            for rad in table["children"]]
    assert rows[0][1] == "År -1 Utfall" and table["children"][0]["th"] is True
    assert any(row[0] == "Anslag" for row in rows)


def test_an_unknown_element_is_not_dropped_silently(tmp_path):
    refs, _ = walk({INDEX: """<html><body>
        <meta content="2026-01-20 09:54:34" name="last-modified"/>
        <div class="regelverk-page__box">
        <h2>Statskontorets föreskrifter och allmänna råd (STKFA 2026:1) om x</h2>
        <div class="foreskrifter"><p><strong>1 §</strong> Text.</p>
        <blockquote>A shape this parser has not seen.</blockquote>
        </div></div></body></html>"""})
    record = statskontoret.resolve(None, REGISTRY["stkfa"], refs[0], tmp_path,
                                   delay=0)
    with pytest.raises(AssertionError, match="unknown EA-regelverket element"):
        parse.parse_record(record, str(tmp_path))


def test_the_consolidation_cutoff_is_the_newest_amendment(tmp_path):
    # the page's Övergångsbestämmelser section names ESVFA 2022:1 (the base
    # itself) and the four amendments folded in
    reg = parsed(tmp_path)
    [cons] = reg.consolidations
    assert cons.konsolideradTom == "https://lagen.nu/esvfa/2025:1"
    assert [a.identifier for a in reg.amendments] == [
        "ESVFA 2023:2", "ESVFA 2023:6", "ESVFA 2024:1", "ESVFA 2025:1"]


def test_the_cutoff_names_the_amending_series_not_the_records(tmp_path):
    # Statskontoret issues the series now, so an ESVFA regulation is amended by
    # STKFA. Read as the record's own samling, the cutoff would point at
    # esvfa/2026:1 -- a document that does not exist.
    refs, _ = walk({INDEX: """<html><body>
        <meta content="2026-01-20 09:54:34" name="last-modified"/>
        <div class="regelverk-page__box">
        <h2>Ekonomistyrningsverkets föreskrifter och allmänna råd (ESVFA 2022:1)
            om årsredovisning</h2>
        <div class="foreskrifter"><p><strong>1 §</strong> Text.</p></div>
        <div class="foreskrifter"><h2>Övergångsbestämmelser till föreskrifter</h2>
        <h3>ESVFA 2022:1</h3><p>Träder i kraft den 1 januari 2023.</p>
        <h3>STKFA 2026:1</h3><p>Träder i kraft den 1 januari 2027.</p>
        </div></div></body></html>"""})
    record = statskontoret.resolve(None, REGISTRY["stkfa"], refs[0], tmp_path,
                                   delay=0)
    reg = parse.parse_record(record, str(tmp_path))

    [cons] = reg.consolidations
    assert cons.konsolideradTom == "https://lagen.nu/stkfa/2026:1"
    assert [a.uri for a in reg.amendments] == ["https://lagen.nu/stkfa/2026:1"]

WRAPPED = """<html><body><div class="regelverk-page__box">
    <h2>Ekonomistyrningsverkets föreskrifter och allmänna råd (ESVFA 2022:8)
      till förordningen (2007:603) om intern styrning och kontroll</h2>
    <h2 class="ea-kapitel">Riskanalys</h2>
    <p>3 § En riskanalys ska göras.</p>
    <div class="grey"><div class="esv-comment esv-comment__blue">
      <div class="foreskrifter"><h2>Riskanalys - föreskrifter till 3 §
        förordningen</h2><p><strong>2 §</strong> Myndigheten ska vid behov
        uppdatera riskanalysen.</p></div>
    </div></div>
    <div><div class="allmanna-rad"><h2>Allmänna råd till 6 §
      förordningen</h2><p>En sammanställning bör finnas.</p></div></div>
    %s
    </div></body></html>"""


def test_a_typed_section_in_a_presentation_wrapper_is_read():
    # Forum's September 2026 layout: the typed sections sit inside untyped
    # div.grey > div.esv-comment boxes, or a bare div
    # (ea-regelverket/forvaltning/intern-styrning-och-kontroll)
    ref = statskontoret.parse_page(WRAPPED % "", INDEX)
    texts = [" ".join(BeautifulSoup(section, "html.parser")
                      .get_text(" ", strip=True).split())
             for section in ref.extra["sections"]]
    assert texts == [
        "Riskanalys",
        "2 § Myndigheten ska vid behov uppdatera riskanalysen.",
        "Allmänna råd till 6 § förordningen En sammanställning bör finnas."]


def test_text_directly_in_a_wrapper_is_not_dropped_silently():
    with pytest.raises(AssertionError, match="inside a presentation wrapper"):
        statskontoret.parse_page(
            WRAPPED % "<div class='grey'><p>Ny text.</p></div>", INDEX)
