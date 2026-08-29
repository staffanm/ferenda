"""UN Treaty Collection (MTDSG) scraping, artifact projection, folkrätt wiring.

Runs off a committed synthetic MTDSG fixture (a trimmed Vienna Convention page)
and the depositary's own VCLT PDF, whole -- the article count is an invariant,
so a fixture missing the first three articles cannot stand in for the text.
Small dicts otherwise, no network.
"""

import json
from pathlib import Path

import pytest

from ferenda.lib import catalog, compress, facets, layout, page, render
from ferenda.untc import download, parse
from ferenda.untc import render as untc_render
from ferenda.untc import text as untc_text
from ferenda.untc.model import Provision, Treaty, load_treaties, treaty_uri

FIXTURES = Path(__file__).parent / "files" / "untc"


def _vclt():
    return parse.parse("I-18232", FIXTURES)


# --------------------------------------------------------------------------
# model + curated list
# --------------------------------------------------------------------------

def test_treaty_uri_and_kind():
    assert treaty_uri("I-18232") == "https://lagen.nu/untc/I-18232"
    assert Treaty("XXIII-1", "I-18232", "23",
                  "Vienna Convention on the Law of Treaties").kind \
        == "treaty"
    assert Treaty("V-5", "I-8791", "5",
                  "Protocol relating to the Status of Refugees").kind \
        == "protocol"


def test_curated_list_is_complete_and_well_formed():
    treaties = load_treaties()
    # the anchors the whole build hangs on
    assert treaties["I-18232"]["title"] == "Vienna Convention on the Law of Treaties"
    assert treaties["I-31363"]["title"] == \
        "United Nations Convention on the Law of the Sea"
    # every curated entry carries the fields the harvest/listing need
    for unts, entry in treaties.items():
        assert entry["unts"] == unts
        # every treaty names where its authentic text really lives: the MTDSG
        # carries status only, and the UNTS's own volumes are scans
        assert entry["text"]["reader"] in ("ohchr", "pdf")
        assert entry["text"]["url"].startswith("https://")
        assert entry["chapter"] and entry["title"] and entry["group"]


# --------------------------------------------------------------------------
# parse: metadata + participation
# --------------------------------------------------------------------------

def test_parse_metadata():
    art = _vclt()
    assert art["uri"] == "https://lagen.nu/untc/I-18232"
    assert art["type"] == "internationell-overenskommelse"
    assert art["doctype"] == "treaty"
    assert art["number"] == "I-18232"
    assert art["date"] == "1969-05-23"
    md = art["metadata"]
    assert md["conclusionPlace"] == "Vienna"
    assert md["conclusionDate"] == "1969-05-23"
    assert md["entryIntoForce"].startswith("27 January 1980")
    assert md["registration"] == "27 January 1980, No. 18232"
    assert md["depositary"] == "UN Secretary-General"      # not a state -- the UN SG
    # the MTDSG carries status and no text; the articles come from the
    # depositary's own publication, so a treaty page is the two halves joined
    articles = [n for n in art["structure"] if n.get("ordinal")]
    assert articles, "the treaty text should reach the artifact"
    assert art["structure"][0]["id"] == "Preamble"
    # the fixture PDF is the depositary's whole publication, so the count the
    # curated entry carries is checked here as it is on the real corpus
    assert [n["id"] for n in articles][:3] == ["A1", "A2", "A3"]
    assert len(articles) == load_treaties()["I-18232"]["articles"] == 85
    assert art["metadata"]["reference"] == "UNTS I-18232"
    assert art["metadata"]["mtdsg"] == "XXIII-1"
    assert art["source_url"] == (
        "https://treaties.un.org/pages/ViewDetailsIII.aspx"
        "?src=TREATY&mtdsg_no=XXIII-1&chapter=23&clang=_en")


def test_parse_participation_actions_and_footnotes():
    art = _vclt()
    parties = {p["country"]: p for p in art["parties"]}
    # the consent-to-be-bound markers each map to their form
    assert parties["Albania"]["action"] == "accession"        # a
    assert parties["Bosnia and Herzegovina"]["action"] == "succession"   # d
    assert parties["Argentina"]["action"] == "ratification"   # bare date
    assert parties["European Union"]["action"] == "formal confirmation"  # c
    assert parties["Albania"]["actionDate"] == "2001-06-27"
    # a footnote superscript is stripped from the state name; a declaring state
    # (wrapped in <a class="noteIndex">) keeps its name
    assert "Bosnia and Herzegovina" in parties and "3" not in "".join(parties)
    assert "Argentina" in parties
    # signature-only vs bound counts
    assert art["metadata"]["statesParties"] == 4             # Albania/Bosnia/Argentina/EU
    assert art["metadata"]["signatories"] == 2               # Afghanistan/Argentina
    assert parties["Afghanistan"] == {"country": "Afghanistan",
                                      "signature": "1969-05-23"}


def test_tblgrid_anchor_ignores_the_decoy_territorial_table():
    # the page opens its territorial-notification table with a 'Participant'
    # header too; anchoring on the grid's control id keeps that noise out
    countries = {p["country"] for p in _vclt()["parties"]}
    assert "United Kingdom" not in countries


def test_parse_fails_loudly_on_control_drift():
    # the conclusion date is load-bearing; if the control id it lives in drifts,
    # the scrape must reject the page, not ship a dateless artifact
    html = ('<html><body>'
            '<table id="x_tblgrid"><tr><td>Participant</td><td>Signature</td>'
            '<td>Ratification</td></tr>'
            '<tr><td>Sweden</td><td></td><td>5 Dec 1972</td></tr></table>'
            '</body></html>')                     # no rptTreaty_ctl00_tcText
    entry = {"mtdsg_no": "XXIII-1", "unts": "I-18232", "chapter": "23", "title": "…"}
    with pytest.raises(ValueError, match="no conclusion date"):
        parse.parse_page(entry, html)


# --------------------------------------------------------------------------
# download identity (no network)
# --------------------------------------------------------------------------

def test_page_path_and_list_basefiles(tmp_path):
    compress.write_download(download.page_path(tmp_path, "I-18232"), "<html></html>")
    # the text file sits beside the status page and must not read as a document
    compress.write_download(tmp_path / "I-18232.text.html", "<html></html>")
    assert download.list_basefiles(tmp_path) == ["I-18232"]


# --------------------------------------------------------------------------
# the authentic text: the half the MTDSG does not carry
# --------------------------------------------------------------------------

def test_the_three_article_heading_shapes():
    """The fourteen are published by three depositaries and write the rubric
    three ways. Each shape below is taken from a real page; missing one cost
    the CRPD its article 20 and the Refugee Protocol its article 11."""
    for line, want in (("Article 5", "A5"),
                       ("Article II", "AII"),
                       ("Article 12 bis", "A12BIS"),
                       ("Article 1 - Definition of the term \"refugee\"", "A1"),
                       ("Article 11. Deposit in the archives", "A11"),
                       ("Article 20 Personal mobility", "A20")):
        match = untc_text.RE_ARTICLE.match(line)
        assert match, line
        assert untc_text.fragment(match.group(1)) == want, line
    # and the prose of every treaty must not read as a heading
    for prose in ("Article 5 shall apply mutatis mutandis to the present Protocol",
                  "in accordance with article XIII",
                  "The Contracting Parties confirm that genocide is a crime"):
        assert not untc_text.RE_ARTICLE.match(prose), prose


def test_the_table_of_contents_is_not_the_treaty():
    """UNCLOS's PDF opens with 33 pages of contents, each entry an "Article N."
    line of its own. Counting them gave 885 articles against the Convention's
    320. The whole run of leader lines is cut, not each leader line: the
    contents set their "Article N." and "ANNEX I." lines with no leader of
    their own, and honouring those filed 444 provisions under Annex IX."""
    lines = ["CONTENTS",
             "Article 1.", "Use of terms . . . . . . . . . . . . . . . . . 3",
             "Article 2.", "Legal status of the territorial sea . . . . . 4",
             "Article 3.", "Breadth of the territorial sea . . . . . . . 12",
             "Article 4.", "Outer limit . . . . . . . . . . . . . . . . . 12",
             "ANNEX I.", "HIGHLY MIGRATORY SPECIES . . . . . . . . . . 143",
             "Article 3.", "The breadth of the territorial sea shall not exceed",
             "12 nautical miles."]
    provisions = untc_text.provisions(lines)
    assert [p[0] for p in provisions] == ["A3"]
    assert provisions[0][2] == ["The breadth of the territorial sea shall not exceed",
                                "12 nautical miles."]


def test_a_three_dot_ellipsis_is_prose_not_a_contents_entry():
    """A leader needs five dots. The Refugee Protocol sets a three-dot ellipsis
    inside its article 1(2), and reading that line as a contents entry dropped
    the paragraph that defines who the Protocol covers."""
    lines = ["Article 1", "Article 1", "2. the term \"refugee\" shall . . . mean any person"]
    assert untc_text.provisions(lines)[-1][2] == [
        "2. the term \"refugee\" shall . . . mean any person"]


def test_an_annex_restarts_the_numbering_and_is_scoped():
    """UNCLOS numbers an Article 1 in the body and again in each of nine
    annexes, so 159 of its anchors named more than one provision. The heading
    prints its title on the same line ("ANNEX I. HIGHLY MIGRATORY SPECIES");
    the prose that cites an annex is "Annex III, article 11.", and reading that
    as a heading would restart the scope mid-body."""
    lines = ["Article 1", "The body's first article.",
             "Annex III, article 11.", "Still the body.",
             "ANNEX I. HIGHLY MIGRATORY SPECIES", "1. Albacore tuna.",
             "ANNEX II. COMMISSION ON THE LIMITS", "Article 1",
             "The annex's first article."]
    provisions = untc_text.provisions(lines)
    assert [p[0] for p in provisions] == ["A1", "AnnexI", "AnnexII", "AnnexII_A1"]
    # an annex may carry text and no article at all, and it keeps that text
    assert provisions[1][2] == ["1. Albacore tuna."]


def test_a_second_contents_block_ends_the_treaty():
    """The UNCLOS PDF prints the Final Act of the conference after the
    Convention, with its own contents and its own Annexes I, II and VI. Those
    would claim the anchors of the Convention's own annexes."""
    # the two blocks must sit further apart than LEADER_GAP, as the real ones
    # do: 8 027 lines of Convention separate them -- and each must run at least
    # MIN_LEADER_LINES, as the real ones do at 500 and 7
    lines = (["Contents . . . . . . . . . . . . . . . . . . . . 1"] * 5
             + ["Article 1", "The Convention's own article."]
             + ["A line of the Convention."] * (untc_text.LEADER_GAP + 1)
             + ["Final Act of the Conference", "Page"]
             + ["Annex I . . . . . . . . . . . . . . . . . . . . 195"] * 5
             + ["ANNEX I", "RESOLUTION I", "Not the Convention."])
    provisions = untc_text.provisions(lines)
    assert [p[0] for p in provisions] == ["A1"]
    assert "Not the Convention." not in provisions[0][2]


def test_one_dotted_line_is_not_a_contents_block():
    """A contents block lists a document. One line of dots is a line of the
    treaty -- a schedule row, a tariff table, a signature page -- and cutting
    at it drops every article above it. The whole mechanism is calibrated on
    the one PDF in the corpus that has a contents block at all, so the
    fifteenth treaty is the one this protects."""
    lines = ["Article 1", "The first article.",
             "Article 2", "Tariff heading 24.02 . . . . . . . . . . . 15 %",
             "Article 3", "The third article."]
    assert [p[0] for p in untc_text.provisions(lines)] == ["A1", "A2", "A3"]


def test_an_article_heading_with_no_text_is_not_an_article():
    """A contents entry that reaches `provisions` would take the plain "#A5"
    anchor and leave the real article 5 with a `unique_id` suffix -- the anchor
    every citation to it mints, landing on a heading with nothing under it. An
    annex heading is empty on purpose: three of UNCLOS's nine print their title
    and go straight to their first article."""
    lines = ["Article 5", "Article 5", "The real article 5.",
             "ANNEX I. HIGHLY MIGRATORY SPECIES", "Article 1", "The annex's."]
    provisions = untc_text.provisions(lines)
    assert [p[0] for p in provisions] == ["A5", "AnnexI", "AnnexI_A1"]
    assert provisions[0][2] == ["The real article 5."]


def test_the_curated_article_count_is_the_invariant():
    """The authentic text of a concluded treaty does not gain or lose an
    article, so the count is curated with it. Every one of the fourteen carries
    it, and a parse that disagrees raises rather than publishing a treaty
    missing its first half."""
    for unts, entry in load_treaties().items():
        assert isinstance(entry["articles"], int) and entry["articles"] > 0, unts
    assert load_treaties()["I-31363"]["articles"] == 445    # 320 + nine annexes
    assert load_treaties()["I-8791"]["articles"] == 11
    # the ordinal grammar is `model.RE_ORDINAL`, the one the artifact reads a
    # provision's ordinal with, so an "Article 5 bis" counts as the article it is
    assert untc_text.article_count(
        [(None, "Preamble", ["..."]), ("A1", "Article 1", ["."]),
         ("AnnexI", "ANNEX I", ["."]), ("AnnexVI_A31", "Article 31", ["."]),
         ("A5BIS", "Article 5 bis", ["."])]) == 3


def test_a_lowercase_l_set_for_the_digit_one_still_anchors():
    """The UNCLOS PDF sets "Article 3l" and "Article 4l" with a lowercase L for
    the 1, which left Annex VI articles 31 and 41 with no anchor and printed
    their text under articles 30 and 40. A number of digits alone is never
    rewritten, and a run with no digit is not an article heading at all."""
    assert untc_text.fragment("3l") == "A31"
    assert untc_text.fragment("41") == "A41"
    assert untc_text.RE_ARTICLE.match("Article 3l")
    assert not untc_text.RE_ARTICLE.match("Article ll")


def test_every_anchor_is_unique(): 
    """Scoping names the annexes it can; `unique_id` closes what is left. An
    ambiguous anchor is an unreachable article."""
    treaty = Treaty("XXIII-1", "I-18232", "23", "T", provisions=[
        Provision("A1", "Article 1", ["Body."]),
        Provision("A1", "Article 1", ["An annex the reader could not name."]),
        Provision("A1", "Article 1", ["And a third."])])
    ids = [n["id"] for n in treaty.to_artifact()["structure"]]
    assert ids == ["A1", "A1-2", "A1-3"]
    assert len(set(ids)) == len(ids)


def test_a_status_page_without_its_text_raises():
    """The downloader writes the text before a treaty counts as downloaded, so
    this is a half-finished store -- not a treaty nobody publishes. Parsing it
    to an empty structure is what the source used to do, and it published six
    metadata rows with nothing to cite."""
    try:
        parse.read_provisions(load_treaties()["I-1021"], FIXTURES, "I-1021")
    except ValueError as exc:
        assert "stored without its text" in str(exc)
    else:
        raise AssertionError("a status page with no text must not parse")


# --------------------------------------------------------------------------
# layout + catalog wiring
# --------------------------------------------------------------------------

def test_untc_layout_round_trips_and_catalog_row():
    uri = "https://lagen.nu/untc/I-18232"
    assert layout.page_url(uri) == "/untc/I-18232"
    assert layout.page_relpath(uri) == "untc/I_18232.html"
    assert str(layout.url_to_relpath("/untc/I-18232")) == "untc/I_18232.html"
    assert layout.relpath("untc", "I-18232") == Path("I-18232")
    assert "untc" in facets.sources()
    row = catalog.untc_document(_vclt(), "artifact/untc/I-18232.json")
    assert row[:3] == (uri, "untc", "treaty")
    assert row[3] == "Vienna Convention on the Law of Treaties"


# --------------------------------------------------------------------------
# folkrätt landing + treaty page
# --------------------------------------------------------------------------

def _stub(unts, title, date):
    return {"uri": treaty_uri(unts), "number": unts, "doctype": "treaty",
            "type": "internationell-overenskommelse", "identifier": title,
            "title": title, "date": date,
            "metadata": {"statesParties": 0}, "references": [], "structure": [],
            "parties": []}


def test_folkratt_lists_untc_grouped_by_subject(tmp_path):
    vclt = _vclt()                                            # Traktaträtt och havsrätt
    iccpr = _stub("I-14668", "International Covenant on Civil and Political Rights",
                  "1966-12-16")                               # Mänskliga rättigheter
    refugees = _stub("I-2545", "Convention relating to the Status of Refugees",
                     "1951-07-28")                            # Flyktingrätt
    paths = []
    for art in (vclt, iccpr, refugees):
        p = tmp_path / (art["number"].replace("-", "_") + ".json")
        p.write_text(json.dumps(art, ensure_ascii=False))
        paths.append(p)
    database = str(tmp_path / "catalog.sqlite")
    catalog.rebuild(database, "untc", paths)
    con = catalog.connect(database)
    html = render.render_folkratt(con)

    assert "Förenta nationerna (FN)" in html
    # the curated subject groups appear in their display order
    assert "Traktaträtt och havsrätt" in html
    assert "Mänskliga rättigheter" in html
    assert "Flyktingrätt" in html
    assert (html.index("Traktaträtt") < html.index("Mänskliga rättigheter")
            < html.index("Flyktingrätt"))
    # the gloss folds the Swedish name, acronym and the UNTS registration the
    # treaty is cited under
    assert "(Wienkonventionen om traktaträtten, VCLT, UNTS I-18232)" in html
    assert 'href="/untc/I-18232"' in html
    # the shared Dokumenttyp selector gains an FN-fördrag bucket
    assert "FN-fördrag" in html


def test_render_treaty_page_shows_status_and_participation(tmp_path):
    art = _vclt()
    p = tmp_path / "XXIII-1.json"
    p.write_text(json.dumps(art, ensure_ascii=False))
    database = str(tmp_path / "catalog.sqlite")
    catalog.rebuild(database, "untc", [p])
    con = catalog.connect(database)
    html = untc_render.render(art, page.Site(con, {art["uri"]}))
    assert '<a href="/folkratt/" class="on">Folkrätt</a>' in html   # masthead current
    assert "UN Secretary-General" in html and "Depositarie" in html
    assert "Registrering" in html
    # the treaty text is the page's body: an article a citation names has to be
    # an anchor on the page, or the depositary half bought nothing
    assert 'id="A5"' in html and 'id="Preamble"' in html
    assert "Article 5" in html                               # its heading, as printed
    # the participation table renders each state's binding consent in Swedish
    assert "Bindande samtycke" in html
    assert "anslutning" in html                              # accession -> anslutning
    assert "Albania" in html
    # the "Källa" link points at the MTDSG page (& is html-escaped in the href)
    assert "treaties.un.org/pages/ViewDetailsIII.aspx" in html
    assert "mtdsg_no=XXIII-1" in html
