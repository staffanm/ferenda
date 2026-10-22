"""TSFS 2024:33 prints italic terms in a narrow, headerless definition table."""

from pathlib import Path

from ferenda.foreskrift import parse
from ferenda.lib import tabell
from ferenda.lib.lagrum import sfs_parser
from ferenda.lib.pdftext import Line, Run
from ferenda.lib.text import node_text, runs_text

# Official grundförfattning, fetched 2026-10-17:
# https://www.transportstyrelsen.se/TSFS/TSFS%202024_33.pdf
TSFS_PDF = Path(__file__).parent / "files/foreskrift/tsfs-2024-33.pdf"


def test_tsfs_2024_33_keeps_each_term_with_its_definition():
    tree, _meta, _notes = parse.parse_pdf(
        TSFS_PDF, "TSFS 2024:33", sfs_parser("foreskrift", parse.PARSE_TYPES))
    [chapter] = [n for n in tree if n.get("id") == "K1"]
    [para] = [n for n in chapter["children"] if n.get("id") == "K1P2"]
    tables = [n for n in para["children"] if n["type"] == "tabell"]
    rows = [tuple(runs_text(cell) for cell in row["cells"])
            for table in tables for row in table["children"]]

    assert [term for term, _definition in rows] == [
        "ackrediterad verkstad",
        "brist med bedömning två (2)",
        "brist med bedömning två (2) utmärkt med kryss (x)",
        "efterkontroll", "egen reparation", "fackmässigt utförd reparation",
        "gasinstallation", "kontrollrapport", "köldmediesystem", "provning"]
    assert rows[0][1] == (
        "fordonsverkstad som inom sin egen organisation inrymmer ett kontrollorgan "
        "som ackrediterats för kontroll av egna reparationer med stöd av "
        "Europaparlamentets och rådets förordning (EG) nr 765/2008 av den 9 juli "
        "2008 om krav för ackreditering och marknadskontroll i samband med "
        "saluföring av produkter och upphävande av förordning (EEG) nr 339/93 "
        "samt lagen (2011:791) om ackreditering och teknisk kontroll,")
    assert rows[1][1] == (
        "fordonet har försämrats i otillåten grad beträffande föreskrivna krav "
        "i fråga om beskaffenhet och utrustning och bristen är inte enkel ur "
        "miljö- eller trafiksäkerhetssynpunkt,")
    assert rows[2][1] == (
        "fordonet uppfyller inte föreskrivna krav men bristen är enkel ur miljö- "
        "eller trafiksäkerhetssynpunkt och föranleder inget krav på efterkontroll, "
        "men en skyldighet för fordonsägaren att skyndsamt åtgärda bristen,")
    assert rows[-1][1] == (
        "kontroll av att de brister som meddelats i samband med kontrollbesiktning, "
        "efterkontroll eller flygande inspektion är åtgärdade.")
    assert node_text(para["children"][0]) == "I dessa föreskrifter avses med"
    assert node_text(para["children"][-1]).startswith("I övrigt har termer som används")


def _row(top, *, italic=True, gutter=20):
    runs = [Run(85, 185, "ett begrepp", False, italic, 14),
            Run(185 + gutter, 500, "en definition", False, False, 14)]
    return Line("ett begrepp en definition", top, False, False, False, 14, runs)


def test_short_narrow_table_needs_italic_terms():
    assert [kind for kind, _value, _rows in tabell.split_two_column(
        [_row(100), _row(150)])] == ["tabell"]
    assert [kind for kind, _value, _rows in tabell.split_two_column(
        [_row(100, italic=False), _row(150, italic=False)])] == ["lines"]


def test_inline_emphasis_with_word_spacing_is_not_a_table():
    assert [kind for kind, _value, _rows in tabell.split_two_column(
        [_row(100, gutter=4), _row(150, gutter=4), _row(200, gutter=4)])] == ["lines"]


def test_one_italic_definition_is_not_enough_to_establish_columns():
    assert [kind for kind, _value, _rows in tabell.split_two_column(
        [_row(100)])] == ["lines"]
