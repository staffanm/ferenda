"""Tests for the KB proposition title reading (forarbete/kbtitles.py).

Hermetic: the OCR cases run against the trimmed ABBYY fixture under
``test/files/forarbete-legacy/`` and against paragraph lists written out in
full, never the frozen corpus. The paragraph shapes here are the ones the real
1867-1970 front pages take -- one title paragraph, a title the OCR broke in two,
and the budget proposition's display lines -- because the title this module
writes is the only title the catalog listing ever shows for these 19 066
documents.
"""

from pathlib import Path

from ferenda.forarbete import parse
from ferenda.forarbete.kbtitles import (
    MISSING,
    dehyphenate,
    title_from_paras,
    untuple,
)
from ferenda.forarbete.legacy_formats import abbyy_pages
from ferenda.lib import layout
from ferenda.lib.util import basefile_slug

FIXTURES = Path(__file__).parent / "files" / "forarbete-legacy"

XML = ("https://weburn.kb.se/riks/tvåkammarriksdagen/xml/1867/"
       "web_prop_1867____10/prop_1867____10.xml")

# the front page of prop 1952:64 as the ABBYY OCR reads it: running head, page
# number, document number, then the one paragraph that carries the title
FRONT = [
    "Kungl. Maj.ts proposition nr 6i.",
    "1",
    "Nr 64.",
    "Kungl. i\\laj:ts proposition till riksdagen angående fortsatt tullfrihet "
    "i vissa fall för Föreningen Rädda barnen, m. m.; ginen Stockholms slott "
    "den 15 februari 1952.",
    "Kungl. Maj :t vill härmed, under åberopande av bilagda utdrag av "
    "statsrådsprotokollet över finansärenden för denna dag, föreslå riksdagen "
    "att bifalla det förslag, om vars avlåtande till riksdagen föredragande "
    "departementschefen hemställt.",
]


def test_untuple_decodes_the_escaped_repr():
    """The old entry stored a 1-tuple's repr, which escaped every non-ASCII
    character inside it: slicing the wrapper off would leave "för\\xad slag" as
    four literal characters, so the string is decoded, not cut."""
    assert untuple("('med förslag till lag om dödande av förkommen handling',)") \
        == "med förslag till lag om dödande av förkommen handling"
    assert untuple("('med för\\xad slag till lag',)") == "med för\xad slag till lag"
    assert untuple("angående anslag till skolan") == "angående anslag till skolan"


def test_dehyphenate_joins_a_line_break_the_way_the_body_does():
    """The body parse already joins these lines; the entry title never did. A
    lowercase continuation is one word, anything else keeps a real hyphen."""
    assert dehyphenate("angående fortsatt dispo¬ sition av vissa äldre anslag") \
        == "angående fortsatt disposition av vissa äldre anslag"
    assert dehyphenate("från Malmö¬ hus län") == "från Malmöhus län"
    assert dehyphenate("med för\xad slag till lag") == "med förslag till lag"
    assert dehyphenate("i Sverige¬ Norge") == "i Sverige- Norge"


def test_title_from_the_fixture_front_page():
    """The trimmed 1867 fixture is a real KB front page: the title stands
    between the addressee and the "Gifven Stockholms Slott" dateline."""
    pages = abbyy_pages(FIXTURES / "abbyy_propkb.xml")
    assert title_from_paras([p.text for _pageno, paras in pages for p in paras]) \
        == ("om förändrad lydelse af 10 § » Kongl. Kungörelsen den 13 November "
            "1860, angående den allmänna Beväringen")


def test_title_survives_a_garbled_head_and_keeps_its_final_period():
    """Neither anchor is trusted to be spelled right -- this page reads
    "Kungl. i\\laj:ts" and "ginen" -- and the period of a title ending "m. m."
    is part of the title, so only the dateline and the punctuation before it
    are cut."""
    assert title_from_paras(FRONT) == ("angående fortsatt tullfrihet i vissa "
                                       "fall för Föreningen Rädda barnen, m. m.")


def test_title_broken_across_paragraphs_is_completed_from_the_next_one():
    """prop 1922:223's OCR ends one paragraph at "med förslag till lag om." and
    opens the next with the rest of the title. Reading only the paragraph that
    names the addressee would publish "med förslag till lag om"."""
    paras = ["Nr 223.",
             "Kungl. Maj:ts proposition till riksdagen med förslag till lag om.",
             "ändrad lydelse av 2 kap. 11 § strafflagen; given Stocka holms "
             "slott den 21 mars 1922."]
    assert title_from_paras(paras) == ("med förslag till lag om. ändrad lydelse "
                                       "av 2 kap. 11 § strafflagen")


def test_a_dateline_glued_to_the_title_still_ends_it():
    """prop 1963:137's OCR runs the dateline into the title with no separator
    at all -- "m. mgiven Stockholms slott". That is why the dateline pattern
    carries no left word boundary; putting one back loses the whole title."""
    assert title_from_paras([
        "Nr 137.",
        "Kungl. Maj:ts proposition till riksdagen med förslag till lag om "
        "ändrad lydelse av 15 kap. 29 § giftermålsbalken, m. mgiven Stockholms "
        "slott den 29 mars 1963."]) == ("med förslag till lag om ändrad lydelse "
                                        "av 15 kap. 29 § giftermålsbalken, m. m")


def test_the_budget_proposition_prints_its_title_as_display_lines():
    """The statsverksproposition (prop N:1 of each year) sets its head as
    separate lines, so no paragraph carries head and title together."""
    paras = ["KUNGL. MAJ:TS", "NÅDIGA", "PROPOSITION", "TILL", "Riksdagen",
             "angående statsverkets tillstånd och behov under budgetåret",
             "1923—1924.",
             "Bihang till riksdagens protokoll 1923. 1 samt."]
    assert title_from_paras(paras) == ("angående statsverkets tillstånd och "
                                       "behov under budgetåret 1923—1924.")


def test_no_title_on_the_page_yields_none():
    """19 of the 1 570 placeholders sit on a page whose OCR carries no printed
    title (a scan that starts mid-document). Nothing is invented for them."""
    assert title_from_paras(["50", "aldrig skickas i striden utan att i sina "
                             "led hafva upptagit den dithörande "
                             "krigsförstärkningen."]) is None


# --- what parse writes into the artifact ---------------------------------

def _record(basefile, title, files):
    return {"type": "prop", "basefile": basefile,
            "identifier": "Prop. %s" % basefile, "title": title,
            "orig_url": XML, "body_format": "abbyy", "files": list(files)}


def _stage(root, basefile, xml):
    """The record's body file where `_harvested_body` reads it: the slug is the
    filesystem-safe basefile, which for a riksmöte id is not a `:`→`-` swap
    ("2014/15:51" -> "2014-15-51")."""
    docdir = layout.fa_dir(root, "prop", basefile)
    docdir.mkdir(parents=True, exist_ok=True)
    (docdir / ("%s.xml" % basefile_slug(basefile))).write_bytes(xml)


def test_parse_reads_a_placeholder_title_off_the_body(tmp_path):
    """The end of the chain: the record keeps "Doc 1867:10" -- it is the
    harvested copy of what the old entry held -- and the artifact carries the
    title the document itself prints."""
    _stage(tmp_path, "1867:10", (FIXTURES / "abbyy_propkb.xml").read_bytes())
    doc = parse.parse_record(_record("1867:10", "Doc 1867:10", ["1867-10.xml"]),
                             tmp_path)
    assert doc.title.startswith("om förändrad lydelse af 10 §")


def test_parse_unwraps_a_tuple_title_and_joins_its_hyphens(tmp_path):
    """The two string defects through the same call. The title is written the
    way the entry holds it: the repr escaped the soft hyphen, so the four
    characters `\\xad` stand in the stored string and only a decode -- not a
    slice of the wrapper -- gives the word back."""
    _stage(tmp_path, "1957:142", (FIXTURES / "abbyy_propkb.xml").read_bytes())
    doc = parse.parse_record(
        _record("1957:142", "('med förslag till lag om ändring i förord\\xad ningen',)",
                ["1957-142.xml"]), tmp_path)
    assert doc.title == "med förslag till lag om ändring i förordningen"


def test_parse_leaves_an_intact_title_alone(tmp_path):
    """Every other förarbete record passes through character-for-character --
    the repair is gated on the defect, not on the corpus. The title carries a
    double space and a newline on purpose: 4 018 non-KB titles have whitespace
    like this, and collapsing it here would be a silent corpus-wide edit made
    by a KB repair."""
    _stage(tmp_path, "2018:16", (FIXTURES / "abbyy_propkb.xml").read_bytes())
    title = "Vägen till  självkörande fordon\noch introduktion "
    doc = parse.parse_record(_record("2018:16", title, ["2018-16.xml"]),
                             tmp_path)
    assert doc.title == title


def test_parse_gives_a_record_with_no_title_the_empty_string(tmp_path):
    """345 records (286 prop, 59 dir) carry `"title": null` -- the upstream
    published none. `Forarbete.title` is typed `str`, so the artifact gets ""
    rather than the None that used to reach it."""
    _stage(tmp_path, "2014/15:51", (FIXTURES / "abbyy_propkb.xml").read_bytes())
    doc = parse.parse_record(_record("2014/15:51", None, ["2014-15-51.xml"]),
                             tmp_path)
    assert doc.title == ""


def test_a_placeholder_with_no_printed_title_falls_back_to_the_marker(tmp_path):
    """The reader is told the title is missing rather than shown a basefile."""
    _stage(tmp_path, "1867:11",
           b'<?xml version="1.0"?><document xmlns="http://www.abbyy.com/'
           b'FineReader_xml/FineReader10-schema-v1.xml"><page><block '
           b'blockType="Text"><text><par><line><formatting>50</formatting>'
           b"</line></par></text></block></page></document>")
    doc = parse.parse_record(_record("1867:11", "Doc 1867:11", ["1867-11.xml"]),
                             tmp_path)
    assert doc.title == MISSING
