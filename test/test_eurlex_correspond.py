"""Tests for the EU-act lineage layer: reading a recast's jämförelsetabell
annex into article<->article correspondence, and the transitive walk the
statute rail resolves its case law through."""

import pytest

from ferenda.eurlex import correspond as C
from ferenda.lib import catalog

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


def test_correspond_reads_an_english_table():
    # an eng manifestation's correlation annex (e.g. the one 31993L0037's
    # source patch restores -- the only text CELLAR holds for it is English)
    # identifies its columns without the Swedish citation engine's help:
    # SELF_COLUMN already admitted "This Directive", and the header act
    # designation is read by HEADER_ACT alone since "Directive 71/305/EEC"
    # never gets a link run
    english = _act("31993L0037", [
        _row("Directive 71/305/EEC | This Directive"),
        _row("Article 10 | Article 10"),
        _row("Article 29 ( 1 ) | Article 30 ( 1 )"),
        _row("Article 31 | —"),
    ])
    edges, stats = C.correspondence(english)
    assert [(e["newArticle"], e["oldArticle"]) for e in edges] == [
        ("10", "10"), ("30", "29")]
    assert edges[0]["oldLaw"] == BASE + "31971L0305"
    assert stats["empty"] == 1


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


def test_predecessor_atoms_walks_three_generations_by_default(tmp_path):
    # three hops reaches the original 1971/1977 directives from a 2014 recast
    # (2014/24 -> 2004/18 -> 93/37 -> 71/305) -- the generation Dundalk and
    # SIAC Construction cite, which depth 2 silently cut off
    con = _lineage_catalog(tmp_path)
    got = catalog.predecessor_atoms(con, BASE + "32014L0024", "57")
    # ordered within a hop by (old_uri, old_pinpoint), so the walk is repeatable
    assert [(u.rsplit("/", 1)[1], a, hop) for u, a, hop in got] == [
        ("32004L0018", "45", 1), ("31992L0050", "29", 2),
        ("31993L0037", "24", 2), ("31971L0305", "23", 3)]
    # a narrower caller can still bound the walk
    shallow = catalog.predecessor_atoms(con, BASE + "32014L0024", "57",
                                        depth=2)
    assert (BASE + "31971L0305", "23", 3) not in shallow


def test_predecessor_atoms_keeps_the_table_s_pinpoint_precision(tmp_path):
    # 2014/24 annex XV itemizes artikel 58 punkt by punkt (58.1 -> 44.1,
    # 58.2 -> 46, 58.3 -> 47); the walk keeps whatever precision the table
    # offers per atom, and only falls back to bare article numbers when the
    # atom is finer than every row (58.4 here)
    con = catalog.connect(str(tmp_path / "c.sqlite"))
    con.executemany("INSERT INTO directive_correspondence VALUES (?,?,?,?,?,?)", [
        (BASE + "32014L0024", "58", BASE + "32004L0018", "44", "58.1", "44.1"),
        (BASE + "32014L0024", "58", BASE + "32004L0018", "46", "58.2", "46"),
        (BASE + "32014L0024", "58", BASE + "32004L0018", "47", "58.3", "47"),
    ])
    con.commit()
    new = BASE + "32014L0024"
    # a pinpointed atom takes exactly the row inside its claim
    assert catalog.predecessor_atoms(con, new, "58.3") == [
        (BASE + "32004L0018", "47", 1)]
    # the bare article takes every itemized row (they are all inside "58")
    assert catalog.predecessor_atoms(con, new, "58") == [
        (BASE + "32004L0018", "44.1", 1), (BASE + "32004L0018", "46", 1),
        (BASE + "32004L0018", "47", 1)]
    # an atom the table never itemizes degrades to the article numbers
    assert catalog.predecessor_atoms(con, new, "58.4") == [
        (BASE + "32004L0018", "44", 1), (BASE + "32004L0018", "46", 1),
        (BASE + "32004L0018", "47", 1)]


LOU, PROP = "https://lagen.nu/2016:1145", "https://lagen.nu/prop/2015-16-195"


def _genomfor(con, anchor, directive, article, pinpoint):
    con.execute("INSERT INTO genomforande VALUES (?,?,?,?,?,?,?,?,?)",
                (LOU, anchor, directive, article, PROP, "Prop. 2015/16:195",
                 pinpoint, 0, ""))


def _judgment(con, celex, to_fragment, act=None, date="2021-05-06"):
    uri = BASE + celex
    con.execute("INSERT INTO documents (uri, source, path, label, date) "
                "VALUES (?, 'eurlex', 'x.json', ?, ?)",
                (uri, "C-1/20", date))
    con.execute("INSERT INTO links (from_uri, from_anchor, predicate, to_uri, "
                "to_root, text) VALUES (?,?,?,?,?,?)",
                (uri, "p12", "dcterms:references",
                 (act or BASE + "32014L0024") + "#" + to_fragment,
                 act or BASE + "32014L0024", "artikel " + to_fragment))


def test_caselaw_anchored_attributes_a_lineage_case_to_its_hop(tmp_path):
    con = _lineage_catalog(tmp_path)
    _genomfor(con, "K13P1", BASE + "32014L0024", "57", "57")
    _judgment(con, "61987CJ0045", "23", act=BASE + "31971L0305")
    con.commit()
    got = catalog.caselaw_anchored(con, LOU)
    (row, provenance), = got["K13P1"]
    assert row[0] == BASE + "61987CJ0045"
    # the rail can say "om artikel 23 i 71/305/EEG, motsvarar artikel 57"
    assert provenance == {(BASE + "31971L0305", "23", "57", 3)}


def test_caselaw_anchored_keeps_a_directly_transposed_article_at_hop_zero(
        tmp_path):
    # a paragraf that transposes both a recast article and (via another
    # statement) the very article that recast replaced: the direct statement
    # wins, so the rail explains nothing it does not have to
    con = _lineage_catalog(tmp_path)
    _genomfor(con, "K13P1", BASE + "32014L0024", "57", "57")
    _genomfor(con, "K13P1", BASE + "32004L0018", "45", "45")
    _judgment(con, "62004CJ0226", "45", act=BASE + "32004L0018")
    con.commit()
    (_row, provenance), = catalog.caselaw_anchored(con, LOU)["K13P1"]
    assert provenance == {(BASE + "32004L0018", "45", "45", 0)}


def test_caselaw_anchored_routes_a_pinpoint_to_the_claiming_paragraf(tmp_path):
    # the LOU 13 kap. shape: seven paragrafer transpose punkter of artikel 57.
    # A case on 57.4 belongs next to 13 kap. 3 § (the deepest covering claim;
    # 13 kap. 5 § also claims 57.4 but 3 § comes first in statute order), a
    # case on 57.6 next to 13 kap. 5 § (its only claimant here), and a case
    # citing bare artikel 57 -- or a punkt nobody claims -- next to the first
    # paragraf of the article family, not next to all of them
    con = _lineage_catalog(tmp_path)
    _genomfor(con, "K13P1", BASE + "32014L0024", "57", "57.1")
    _genomfor(con, "K13P3", BASE + "32014L0024", "57", "57.4")
    _genomfor(con, "K13P5", BASE + "32014L0024", "57", "57.2, 57.4, 57.6")
    _judgment(con, "62018CJ0267", "57.4", date="2019-10-03")   # Delta Antrepriză
    _judgment(con, "62020CJ0210", "57.6", date="2021-06-03")   # Rad Service
    _judgment(con, "62016CJ0387", "57", date="2017-06-20")
    _judgment(con, "62016CJ0388", "57.9", date="2017-06-21")
    con.commit()
    got = catalog.caselaw_anchored(con, LOU)
    by_anchor = {anchor: {row[0].rsplit("/", 1)[1] for row, _p in cases}
                 for anchor, cases in got.items()}
    assert by_anchor == {
        "K13P1": {"62016CJ0387", "62016CJ0388"},
        "K13P3": {"62018CJ0267"},
        "K13P5": {"62020CJ0210"},
    }
    # the pinpoint the case actually cited survives into the provenance
    (_row, provenance), = got["K13P3"]
    assert provenance == {(BASE + "32014L0024", "57.4", "57.4", 0)}


def test_caselaw_anchored_cascades_past_an_anchor_the_law_no_longer_has(
        tmp_path):
    # LOU's genomförande layer follows prop 2015/16:195's numbering, where
    # tillsyn was 22 kap.; the 2021 restructuring renumbered it away, so the
    # rendered consolidation has no K22P1 to hang a rail on. Given the live
    # anchor set, the claim is skipped and the case lands on the article
    # family's first paragraf that still exists -- not in a dead anchor whose
    # rail no reader can ever see
    con = _lineage_catalog(tmp_path)
    _genomfor(con, "K22P1", BASE + "32014L0024", "83", "83")
    _genomfor(con, "K12P17", BASE + "32014L0024", "83", "83.6")
    _judgment(con, "62018CJ0496", "83.1")
    con.commit()
    unrestricted = catalog.caselaw_anchored(con, LOU)
    assert set(unrestricted) == {"K22P1"}
    got = catalog.caselaw_anchored(con, LOU, live={"K12P17", "K13P1"})
    assert set(got) == {"K12P17"}
    (_row, provenance), = got["K12P17"]
    assert provenance == {(BASE + "32014L0024", "83.1", "83.6", 0)}
