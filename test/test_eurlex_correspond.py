"""Tests for the EU-act lineage layer: reading a recast's jämförelsetabell
annex into article<->article correspondence, and the transitive walk the
statute rail resolves its case law through."""

import pytest

from accommodanda.eurlex import correspond as C
from accommodanda.lib import catalog

BASE = "https://lagen.nu/ext/celex/"


def _link(text, uri):
    return {"predicate": "dcterms:references", "text": text, "uri": uri}


def _row(*runs):
    return {"type": "row", "text": list(runs)}


def _act(celex, structure):
    return {"celex": celex, "uri": BASE + celex, "structure": structure}


# --- the cell reader ------------------------------------------------------

def test_articles_reads_enumerations_ranges_and_prose():
    # a cell is an enumeration wrapped in prose; the bare article number is what
    # the lineage joins on, the pinpoint is what the rail can show
    assert C._articles("Artikel 1.1, 1.2, 1.4, 1.5 och 1.6") == [
        ("1", "1.1"), ("1", "1.2"), ("1", "1.4"), ("1", "1.5"), ("1", "1.6")]
    assert C._articles("Artikel 1.9, andra stycket, led a") == [("1", "1.9")]
    assert C._articles("Art 80.1, andra stycket") == [("80", "80.1")]
    assert C._articles("Artikel 1 a första delen av meningen") == [("1", "1")]
    # a plain integer range is filled -- "Artiklarna 2-5" covers 3 and 4 ...
    assert C._articles("Artiklarna 2–5") == [("2", "2"), ("3", "3"),
                                             ("4", "4"), ("5", "5")]
    # ... a dotted one is not: 71.5-71.8 is inside article 71 either way
    assert C._articles("Artiklarna 71.5–71.8") == [("71", "71.5"),
                                                   ("71", "71.8")]


def test_articles_ignores_cells_that_cite_no_article():
    # the "no counterpart" vocabulary, and the non-article content a
    # correspondence table also carries
    for cell in ("—", "-", "Ny", "Ändrad", "", "Bilaga IV, a–f",
                 "Skäl 16 anpassat", "Bilaga XV"):
        assert C._articles(cell) == [], cell


def test_cells_splits_on_the_joined_runs_keeping_interior_gaps():
    node = _row(_link("Artikel 2.1", BASE + "32014L0024#2.1"),
                ", led 4 a | ", "—", " |  | Ny")
    assert [t for t, _ in C._cells(node)] == ["Artikel 2.1, led 4 a", "—",
                                              "", "Ny"]


# --- table location and orientation ---------------------------------------

FORWARD = _act("32014L0024", [
    {"type": "heading", "text": ["BILAGA XV"]},
    _row("Detta direktiv | Direktiv ",
         _link("2004/18/EG", BASE + "32004L0018")),
    _row("Artikel 57 | Artikel 45"),
    _row("Artikel 12 | —"),
    {"type": "paragraph", "text": ["Utfärdat i Bryssel."]},
    _row("Något helt annat | i en annan tabell"),
])

# the *common* layout is the other way round: the repealed act in column 1
REVERSED = _act("32017L2110", [
    {"type": "heading", "text": ["JÄMFÖRELSETABELL"]},
    _row("Direktiv ", _link("1999/35/EG", BASE + "31999L0035"),
         " | Detta direktiv"),
    _row("Artikel 3 | Artikel 1"),
])


def test_correspond_reads_a_table_whose_self_column_is_first():
    edges, stats = C.correspondence(FORWARD)
    assert [(e["newArticle"], e["oldArticle"]) for e in edges] == [("57", "45")]
    # article 12 (the in-house exemption) really has no 2004/18 counterpart --
    # a "—" row is a read row with no pair, not a parse failure
    assert stats["rows"] == 2 and stats["empty"] == 1
    assert edges[0]["oldUri"] == BASE + "32004L0018#45"
    # the table stops at the first non-row block: the later row is another table
    assert stats["tables"] == 1


def test_correspond_reads_a_reversed_table():
    edges, _stats = C.correspondence(REVERSED)
    edge = edges[0]
    # "this directive" is always the new side, whichever column it sits in
    assert (edge["newArticle"], edge["oldArticle"]) == ("1", "3")
    assert edge["oldLaw"] == BASE + "31999L0035"


@pytest.mark.parametrize("phrase", [
    "Detta direktiv", "Denna förordning", "Detta Direktiv",   # the common forms
    "Den här förordningen", "Det här direktivet",             # ~40 more tables
    "Föreliggande direktiv", "Denna delegerade förordning",
    "Denna genomförandeförordning",                           # a compound noun
])
def test_self_column_admits_every_phrasing_the_corpus_uses(phrase):
    assert C.SELF_COLUMN.match(phrase), phrase


@pytest.mark.parametrize("phrase", [
    "Direktiv 2004/18/EG", "Andra rättsakter", "Ändringsdirektiv",
    "Direktiv | Tidsfrist för införlivande",   # the transposition-deadline table
])
def test_self_column_rejects_a_column_that_names_another_act(phrase):
    assert not C.SELF_COLUMN.match(phrase), phrase


@pytest.mark.parametrize("text,celex", [
    # Euratom acts and "(EU, Euratom)" numbering are not resolved by the
    # citation engine, so the header cell carries a name and no link
    ("Direktiv 92/3/Euratom", "31992L0003"),
    ("Rådets direktiv 95/21/EG", "31995L0021"),
    # a pre-2015 regulation is numbered number/year -- and BOTH readings of
    # "63/2002" are valid CELEX years, so the "nr" is what settles it
    ("Förordning (EG) nr 63/2002 (ECB/2001/18)", "32002R0063"),
    ("Förordning (EEG) nr 752/93", "31993R0752"),
    # ... while the 2015 reform's form is year/number, with no "nr"
    ("Förordning (EU) 2016/679", "32016R0679"),
    ("Kommissionens genomförandeförordning (EU) nr 543/2011", "32011R0543"),
    # a framework decision is CELEX type F, which we do not mint
    ("Rådets rambeslut 2006/960/RIF", None),
    ("Andra rättsakter", None),
])
def test_header_celex_reads_an_unlinked_act_designation(text, celex):
    assert C._header_celex(text, []) == celex


def test_header_celex_prefers_the_citation_engine_s_own_resolution():
    assert C._header_celex("Direktiv 2004/18/EG", ["32004L0018"]) == "32004L0018"


def test_correspond_skips_a_column_naming_a_later_act():
    # a table can point forward, at the act that replaced this one; that copy of
    # the relation belongs to the successor's own layer, not to this one. The
    # message must say so -- the pairs read fine, they were deliberately dropped
    forward_only = _act("31999L0035", [
        _row("Detta direktiv | Direktiv ",
             _link("2017/2110", BASE + "32017L2110")),
        _row("Artikel 3 | Artikel 1"),
    ])
    # nothing to raise about at parse time: the act simply has no lineage of
    # its own, and the successor's artifact carries the same relation
    assert C.correspondence(forward_only)[0] == []


def test_correspond_is_silent_for_an_act_with_no_table():
    # the common case by far -- 386 of 19 405 sector-3 acts carry a table, and
    # a judgment none -- so this runs on every act and must simply find nothing
    edges, stats = C.correspondence(
        _act("32014L0023", [{"type": "paragraph", "text": ["Ingen tabell."]}]))
    assert edges == [] and stats["tables"] == 0


# --- the transitive walk --------------------------------------------------

def _lineage_catalog(tmp_path):
    con = catalog.connect(str(tmp_path / "c.sqlite"))
    # written per document by catalog._index_document at relate time; a test
    # fixture stands them up directly
    con.executemany("INSERT INTO directive_correspondence VALUES (?,?,?,?,?,?)", [
        (BASE + "32014L0024", "57", BASE + "32004L0018", "45", "57", "45"),
        (BASE + "32004L0018", "45", BASE + "31993L0037", "24", "45", "24"),
        (BASE + "32004L0018", "45", BASE + "31992L0050", "29", "45", "29"),
        (BASE + "31993L0037", "24", BASE + "31971L0305", "23", "24", "23"),
    ])
    con.commit()
    return con


def test_predecessor_articles_walks_two_generations_by_default(tmp_path):
    con = _lineage_catalog(tmp_path)
    got = catalog.predecessor_articles(con, BASE + "32014L0024", "57")
    # ordered within a hop by (old_uri, old_article), so the walk is repeatable
    assert [(u.rsplit("/", 1)[1], a, hop) for u, a, hop in got] == [
        ("32004L0018", "45", 1), ("31992L0050", "29", 2),
        ("31993L0037", "24", 2)]
    # the third generation is reachable, just not by default
    deep = catalog.predecessor_articles(con, BASE + "32014L0024", "57", depth=3)
    assert (BASE + "31971L0305", "23", 3) in deep


def test_genomfor_targets_carries_the_transposed_article_along(tmp_path):
    con = _lineage_catalog(tmp_path)
    con.execute("INSERT INTO genomforande VALUES (?,?,?,?,?,?,?,?,?)",
                ("https://lagen.nu/2016:1145", "K13P1", BASE + "32014L0024",
                 "57", "https://lagen.nu/prop/2015-16-195", "Prop. 2015/16:195",
                 "57", 0, ""))
    con.commit()
    got = catalog.genomfor_targets(con, "https://lagen.nu/2016:1145", "K13P1")
    assert [(u.rsplit("/", 1)[1], a, via, hop) for u, a, via, hop in got] == [
        ("32014L0024", "57", "57", 0),      # the paragraf's own statement
        ("32004L0018", "45", "57", 1),      # ... and what it inherits from
        ("31992L0050", "29", "57", 2),
        ("31993L0037", "24", "57", 2)]


def test_genomfor_targets_keeps_a_directly_transposed_article_at_hop_zero(
        tmp_path):
    # a paragraf that transposes both a recast article and (via another
    # statement) the very article that recast replaced: the direct statement
    # wins, so the rail explains nothing it does not have to
    con = _lineage_catalog(tmp_path)
    for directive, article in ((BASE + "32014L0024", "57"),
                               (BASE + "32004L0018", "45")):
        con.execute("INSERT INTO genomforande VALUES (?,?,?,?,?,?,?,?,?)",
                    ("https://lagen.nu/2016:1145", "K13P1", directive, article,
                     "https://lagen.nu/prop/2015-16-195", "Prop. 2015/16:195",
                     article, 0, ""))
    con.commit()
    got = catalog.genomfor_targets(con, "https://lagen.nu/2016:1145", "K13P1")
    # 57 and 45 are stated, not inherited. Depth is counted per transposed
    # article, so naming 2004/18 art 45 directly also opens *its* two
    # generations -- 71/305 art 23, which art 57 alone would not reach. 24 and
    # 29 are one hop from 45 and two from 57; the genomförande query is ordered,
    # so 45's walk always runs first and the shallower hop is the one recorded.
    assert {(a, hop) for _u, a, _via, hop in got} == {
        ("57", 0), ("45", 0), ("24", 1), ("29", 1), ("23", 2)}
