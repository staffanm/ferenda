"""The span of provisions a TOC entry names: "3–6 b §§" under a statute's
heading, "kap. 3–24" under an avdelning, "art. 24–43" under an EU division.

Every tree below is one the corpus produces -- 2010:900 chapter 3, 1962:700's
avdelningar, 1998:204's two heading levels, GDPR chapter IV."""

from ferenda.eurlex.render import _article_scopes
from ferenda.lib.eu_structure import flatten
from ferenda.lib.page import Toc, heading_scopes, render_toc


def _rubrik(text, level=2):
    return {"type": "rubrik", "level": level, "text": [text]}


def _paragraf(ordinal):
    return {"type": "paragraf", "ordinal": ordinal, "children": []}


def _kapitel(ordinal, *children):
    return {"type": "kapitel", "id": "K%s" % ordinal, "ordinal": ordinal,
            "children": [_rubrik("%s kap. Rubriken" % ordinal, 1), *children]}


def _scoped(nodes):
    """(heading text, span) for every node the scan gives a span."""
    scopes = heading_scopes(nodes)
    return [(node["text"][0] if node["type"] == "rubrik" else node["id"],
             scopes[id(node)])
            for node in _walk(nodes) if scopes.get(id(node))]


def _walk(nodes):
    for node in nodes:
        yield node
        yield from _walk(node.get("children", []))


# ---- a statute's headings -------------------------------------------------

def test_heading_names_the_paragrafer_under_it():
    # 2010:900 3 kap: a heading over one paragraf prints it alone
    assert _scoped([_kapitel(
        "3", _paragraf("1"),
        _rubrik("Översiktsplanens syfte"), _paragraf("2"),
        _rubrik("Översiktsplanens innehåll"),
        _paragraf("3"), _paragraf("6 b"))]) == [
        ("Översiktsplanens syfte", "2 §"),
        ("Översiktsplanens innehåll", "3–6 b §§")]


def test_the_chapter_itself_gets_no_span():
    # the chapter's TOC entry reads the *container's* scope, and a chapter has
    # none: its paragrafer always open at 1. Its adopted title rubrik collects
    # no span either -- the scan skips it, the way render_node does
    kapitel = _kapitel("3", _paragraf("1"), _rubrik("Rubriken"), _paragraf("2"))
    assert heading_scopes([kapitel]).get(id(kapitel)) is None


def test_a_heading_covers_what_its_subheadings_cover():
    # 1962:700 2 kap: the level-2 heading spans its two level-3 sections, and
    # each of those names its own paragraf
    assert _scoped([_kapitel(
        "2",
        _rubrik("Brott som har begåtts i Sverige"),
        _rubrik("Behörighetens omfattning", 3), _paragraf("1"),
        _rubrik("Krav på åtalsförordnande", 3), _paragraf("2"),
        _rubrik("Brott som har begåtts utanför Sverige"),
        _paragraf("3"), _paragraf("8"))]) == [
        ("Brott som har begåtts i Sverige", "1–2 §§"),
        ("Behörighetens omfattning", "1 §"),
        ("Krav på åtalsförordnande", "2 §"),
        ("Brott som har begåtts utanför Sverige", "3–8 §§")]


def test_a_stray_heading_level_does_not_silence_the_others():
    # AFS 2006:1 opens at level 2 and prints two level-1 headings late in the
    # document: measuring every heading against the shallowest one in the act
    # left its 25 level-2 headings bare
    assert _scoped([
        _rubrik("Tillämpningsområde"), _paragraf("1"),
        _rubrik("Tillstånd"), _paragraf("2"),
        _rubrik("Allmänna råd", 1), _paragraf("3")]) == [
        ("Tillämpningsområde", "1 §"),
        ("Tillstånd", "2 §"),
        ("Allmänna råd", "3 §")]


def test_an_avdelning_names_the_kapitel_under_it():
    # 1962:700: the avdelning entry says which chapters it holds
    assert _scoped([
        {"type": "avdelning", "id": "A1", "ordinal": "1",
         "children": [_rubrik("FÖRSTA AVDELNINGEN", 1),
                      _kapitel("1"), _kapitel("2")]}]) == [("A1", "kap. 1–2")]


def test_an_underavdelning_and_its_avdelning_each_name_their_kapitel():
    # 2010:110 avd. A: the chapters sit two levels down, under underavdelningar
    def under(uid, *kapitel):
        return {"type": "underavdelning", "id": uid,
                "children": [_rubrik("Underrubriken", 1), *kapitel]}
    assert _scoped([
        {"type": "avdelning", "id": "AA", "ordinal": "A",
         "children": [_rubrik("AVD. A", 1),
                      under("AAUI", _kapitel("1"), _kapitel("2")),
                      under("AAUII", _kapitel("3"))]}]) == [
        ("AA", "kap. 1–3"), ("AAUI", "kap. 1–2"), ("AAUII", "kap. 3")]


def test_a_span_that_does_not_ascend_prints_nothing():
    # 1971:235: a heading opened over a stretch the act renumbers, so the last
    # paragraf under it is numbered *below* the first. Printed, it read
    # "Nedsättning av undervisningsskyldigheten (45–29 §§)"
    assert _scoped([_rubrik("Nedsättning av undervisningsskyldigheten"),
                    _paragraf("45"), _paragraf("29")]) == []
    # ... and the same defect on an EU act: 31978R1562 quotes the amended
    # regulation's articles 4-20d, then closes with its own articles 2 and 3
    blocks = flatten([_heading("AVDELNING II", 1, _article("4"), _article("3"))])
    assert list(_article_scopes(blocks).values()) == [""]


def test_a_run_that_restarts_inside_prints_nothing():
    # 1987:1182 carries a tax treaty as a bilaga: every article numbers its
    # punkter from 1, so the bilaga heading ran 1,2,3,4,5,1,2,… Reading only the
    # two ends, the heading printed "1–3 §§" over the whole treaty
    assert _scoped([_rubrik("Bilaga 1"),
                    _paragraf("1"), _paragraf("2"), _paragraf("5"),
                    _paragraf("1"), _paragraf("3")]) == []
    # the same restart landing back on its opening number: "1 §" claimed one
    # provision where the heading covers five (2011:1244, 2014 lydelse)
    assert _scoped([_rubrik("När ska kontrolluppgifter lämnas?"),
                    _paragraf("1"), _paragraf("2"), _paragraf("1")]) == []


def test_a_span_reads_the_letter_a_provision_carries():
    # "6 b" follows "6", and 1997:238 21 a § follows 21 § -- a lettered end is a
    # real span, not a number the scan cannot compare
    assert _scoped([_rubrik("Rubriken"), _paragraf("21"), _paragraf("21 a")]) \
        == [("Rubriken", "21–21 a §§")]
    # a number this module cannot read at all closes no span
    assert _scoped([_rubrik("Rubriken"), _paragraf("I"), _paragraf("II")]) == []


def test_a_heading_with_no_paragrafer_gets_no_span():
    assert _scoped([_rubrik("Rubriken"), _rubrik("Nästa rubrik")]) == []


# ---- an EU act's divisions ------------------------------------------------

def _article(num):
    return {"type": "article", "id": num, "num": num, "label": "Artikel %s" % num,
            "text": []}


def _heading(label, level, *children):
    return {"type": "heading", "level": level, "label": label, "text": [],
            "children": list(children)}


def test_an_eu_division_names_the_articles_under_it():
    # GDPR chapter IV: the Kapitel and each Avsnitt inside it print their own
    # span, since both are how a reader finds an article
    blocks = flatten([_heading(
        "KAPITEL IV", 1,
        _heading("Avsnitt 1", 2, _article("24"), _article("31")),
        _heading("Avsnitt 2", 2, _article("32")))])
    scopes = _article_scopes(blocks)
    assert [(b["label"], scopes[id(b)])
            for b in blocks if b["type"] == "heading"] == [
        ("KAPITEL IV", "art. 24–32"),
        ("Avsnitt 1", "art. 24–31"),
        ("Avsnitt 2", "art. 32")]


def test_an_annex_heading_closes_the_last_chapters_span():
    blocks = flatten([_heading("KAPITEL XI", 1, _article("94"), _article("99")),
                      _heading("BILAGA", 1)])
    scopes = _article_scopes(blocks)
    assert [scopes[id(b)] for b in blocks if b["type"] == "heading"] \
        == ["art. 94–99", ""]


# ---- what the nav prints --------------------------------------------------

def test_the_span_prints_in_its_own_element():
    rubrik = _rubrik("Rubriken")
    toc = Toc({id(rubrik): "3–6 b §§"})
    toc.add("R1", "Rubriken", 2, rubrik)
    toc.add("R2", "Utan omfång", 2)
    toc.add("R3", "Också utan", 2, rubrik.copy())
    html = render_toc(toc, "SFS 2010:900")
    assert '<a href="#R1" class="lvl2">Rubriken ' \
        '<span class="toc-scope">(3–6 b §§)</span></a>' in html
    assert '<a href="#R2" class="lvl2">Utan omfång</a>' in html
    assert '<a href="#R3" class="lvl2">Också utan</a>' in html
