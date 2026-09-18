"""STKFA/ESVFA: the EA-regelverket tree walk, and its typed HTML as a body.

Both fixtures are trimmed live captures (2026-09-13):
``statskontoret-ea-index.html`` is the root's own anchors, and
``statskontoret-ea-page.html`` is eight of the 245 sections of
``/ea-regelverket/redovisning/arsredovisning-och-budgetunderlag/`` -- the page
that carries ESVFA 2022:1 -- with the Övergångsbestämmelser section that names
the amendments folded in.
"""

import types
from pathlib import Path

import pytest

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

def parsed(tmp_path, fixture="statskontoret-ea-page.html"):
    refs, _ = walk({INDEX: fixture})
    record = statskontoret.resolve(None, REGISTRY["stkfa"], refs[0], tmp_path,
                                   delay=0)
    return parse.parse_record(record, str(tmp_path))


def nodes(items):
    for item in items:
        yield item
        yield from nodes(item.get("children", []))


def test_the_page_parses_as_the_regulations_consolidated_text(tmp_path):
    reg = parsed(tmp_path)
    assert reg.structure == []          # no separately published as-enacted text
    [cons] = reg.consolidations
    kinds = {n["type"] for n in nodes(cons.structure)}
    assert {"kapitel", "paragraf", "allmanna_rad"} <= kinds
    assert any(n["type"] == "paragraf" and n.get("ordinal") == "1"
               for n in nodes(cons.structure))


def test_shared_forum_chapter_headings_make_the_text_standalone(tmp_path):
    reg = parsed(tmp_path)
    [cons] = reg.consolidations
    chapters = [n for n in cons.structure if n["type"] == "kapitel"]

    assert [n["ordinal"] for n in chapters] == [str(n) for n in range(1, 12)]
    assert [str(n["children"][0]["text"][0]) for n in chapters] == [
        "1 kap. Inledande bestämmelser",
        "2 kap. Allmänna bestämmelser om årsredovisning",
        "3 kap. Resultatredovisning",
        "4 kap. Resultaträkning och balansräkning",
        "5 kap. Värderingsregler",
        "6 kap. Anslagsredovisning och finansieringsanalys",
        "7 kap. Tilläggsupplysningar",
        "8 kap. Delårsrapport",
        "9 kap. Budgetunderlag och underlag för fördjupad prövning",
        "10 kap. Särskilda bestämmelser för affärsverken",
        "11 kap. Tillämpningsföreskrifter",
    ]
    assert not any("Förordningens egen text" in str(n.get("text", ""))
                   for n in nodes(cons.structure))
    assert any(n["type"] == "rubrik" and n.get("text") == ["Undantag"]
               for n in nodes(chapters[-1]["children"]))
    assert not any(n["type"] == "rubrik" and n.get("text") == ["Föreskrifter"]
                   for n in nodes(cons.structure))


def test_shared_subject_heading_survives_without_forum_duplicates(tmp_path):
    reg = parsed(tmp_path, "statskontoret-ea-compensation.html")
    [cons] = reg.consolidations
    headings = [str(n["text"][0]) for n in nodes(cons.structure)
                if n["type"] == "rubrik"]

    assert "Tillämpningsområde" in headings
    assert headings.count("Kompensation – föreskrifter till 4 § förordningen") == 1
    assert "Undantag" in headings
    assert "Föreskrifter" not in headings
    assert "Tillämpningsföreskrifter" not in headings
    assert not any("Förordningens egen text" in str(n.get("text", ""))
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
