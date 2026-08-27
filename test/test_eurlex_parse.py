"""Tests for the EUR-Lex Formex parser."""

import zipfile
from xml.etree import ElementTree as ET

import pytest
from lxml import etree

from ferenda.eurlex import parse as P
from ferenda.eurlex.correspond import correspondence
from ferenda.lib.cellar import notice_ttl
from ferenda.eurlex.parse import (
    UNCARRIED,
    content_file,
    doctype,
    notice_repeal_date,
    notice_work_date,
    parse_dir,
    parse_document,
    parse_formex,
    to_artifact,
)
from ferenda.lib.formex import (
    XML_PARSER,
    _annex_anchor,
    flatten,
    formex_members,
    load_formex,
)
from ferenda.lib import catalog
from ferenda.lib.errors import SkipDocument
from ferenda.lib.eu_structure import anchored_blocks
from ferenda.lib.eu_structure import flatten as flatten_structure


def _flat(xml):
    return flatten(ET.fromstring(xml))


def _lxml(xml):
    """A tree from the same parser `load_formex` uses, for the cases where the
    element implementation itself is what is under test."""
    return etree.fromstring(xml.encode("utf-8"), XML_PARSER)


def test_flatten_keeps_inline_drops_footnotes():
    # HT/DATE are inline (no added separator); NOTE (footnote) is dropped but
    # its tail is kept
    xml = ('<TXT>See <HT TYPE="ITALIC">Directive</HT> of '
           '<DATE ISO="20160706">6 July 2016</DATE>'
           '<NOTE><P>OJ L 1</P></NOTE> on cybersecurity.</TXT>')
    assert _flat(xml) == "See Directive of 6 July 2016 on cybersecurity."


def test_flatten_separates_block_children():
    # adjacent block elements (P) must not glue together
    assert _flat("<TI><P>Directive 2022/2555</P><P>of 14 December</P></TI>") \
        == "Directive 2022/2555 of 14 December"


def test_doctype_from_celex():
    assert doctype("32022L2555") == "directive"
    assert doctype("32016R0679") == "regulation"
    assert doctype("32014D0001") == "decision"
    assert doctype("62019CJ0311") == "judgment"
    # sector-6 case law is split by the two-letter document code: an AG opinion
    # (CC) is not a judgment, an order (CO/TO) files with judgments (E4)
    assert doctype("61987CC0253") == "opinion"
    assert doctype("62019CO0311") == "order"
    assert doctype("12012E/TXT") == "treaty"


ACT_XML = """<ACT>
  <BIB.INSTANCE>
    <DOCUMENT.REF><COLL>L</COLL><NO.OJ>333</NO.OJ></DOCUMENT.REF>
    <DATE ISO="20221214">20221214</DATE>
  </BIB.INSTANCE>
  <TITLE><TI><P>Direktiv (EU) 2022/2555</P><P>om cybersäkerhet</P></TI></TITLE>
  <PREAMBLE>
    <GR.VISA><VISA>med beaktande av fördraget</VISA></GR.VISA>
    <GR.CONSID>
      <CONSID><NP><NO.P>(1)</NO.P><TXT>Syftet med direktivet.</TXT></NP></CONSID>
    </GR.CONSID>
    <PREAMBLE.FINAL>HÄRIGENOM FÖRESKRIVS FÖLJANDE.</PREAMBLE.FINAL>
  </PREAMBLE>
  <ENACTING.TERMS>
    <DIVISION>
      <TITLE><TI>KAPITEL I</TI><STI>ALLMÄNNA BESTÄMMELSER</STI></TITLE>
      <ARTICLE IDENTIFIER="001">
        <TI.ART>Artikel 1</TI.ART><STI.ART>Innehåll</STI.ART>
        <PARAG IDENTIFIER="001.001"><NO.PARAG>1.</NO.PARAG>
          <ALINEA>I detta direktiv fastställs åtgärder.</ALINEA></PARAG>
        <PARAG IDENTIFIER="001.002"><NO.PARAG>2.</NO.PARAG>
          <ALINEA><P>Följande fastställs:</P>
            <LIST TYPE="alpha">
              <ITEM><NP><NO.P>a)</NO.P><TXT>skyldigheter.</TXT></NP></ITEM>
            </LIST></ALINEA></PARAG>
      </ARTICLE>
    </DIVISION>
  </ENACTING.TERMS>
</ACT>"""


def test_parse_act_metadata_and_title():
    doc = parse_formex(ET.fromstring(ACT_XML), "32022L2555", "swe")
    assert doc.doctype == "directive"
    assert doc.uri == "https://lagen.nu/ext/celex/32022L2555"
    assert doc.date == "20221214"
    assert doc.oj == "L 333"
    assert doc.title == "Direktiv (EU) 2022/2555 om cybersäkerhet"


def test_parse_act_title_joins_ti_and_sti():
    # the OJ's newer act-by-act Formex (32025R0390) splits the title: TI holds
    # only the designation, STI the date and subject -- reading TI alone
    # published "Rådets förordning (EU) 2025/390" as the whole title
    xml = """<ACT>
      <TITLE><TI><P><HT TYPE="UC">Rådets förordning (EU) 2025/390</HT></P></TI>
      <STI><P>av den <DATE ISO="20250224">24 februari 2025</DATE></P>
      <P>om ändring av förordning (EU) nr 269/2014</P></STI></TITLE>
      <ENACTING.TERMS/>
    </ACT>"""
    doc = parse_formex(ET.fromstring(xml), "32025R0390", "swe")
    assert doc.title == ("Rådets förordning (EU) 2025/390 av den "
                         "24 februari 2025 om ändring av förordning "
                         "(EU) nr 269/2014")


def test_an_eea_relevance_marker_in_sti_is_not_the_title():
    """Five acts -- 32020R0697, 32020R0699, 32020R0873, 32020R1043 and
    32021R0557 -- set the EEA-relevance notice in STI, where the join glued it
    onto the act's name in the catalog, the listings and search. A parenthesis
    alone does not tell it from a subtitle: 32020H1366's English manifestation
    sets "(Migration Preparedness and Crisis Blueprint)" in STI, and that is
    the recommendation's own short name. The Swedish shape below is built from
    the English one, because the Swedish manifestation carries no STI at
    all -- it prints the short name inside TI."""
    xml = """<ACT>
      <TITLE><TI><P>Rådets förordning (EU) 2020/699 av den 25 maj 2020 om
      tillfälliga åtgärder avseende stämmor i europabolag</P></TI>
      <STI><P>(Text av betydelse för EES)</P></STI></TITLE>
      <ENACTING.TERMS/>
    </ACT>"""
    doc = parse_formex(ET.fromstring(xml), "32020R0699", "swe")
    assert doc.title.endswith("stämmor i europabolag")
    assert "EES" not in doc.title
    named = xml.replace("(Text av betydelse för EES)",
                        "(beredskaps- och krisplan för migration)")
    assert parse_formex(ET.fromstring(named), "32020H1366", "swe").title.endswith(
        "(beredskaps- och krisplan för migration)")


def test_parse_act_body_structure():
    doc = parse_formex(ET.fromstring(ACT_XML), "32022L2555", "swe")
    seen = [(b.kind, b.num, b.level, b.text) for b in doc.body]
    assert ("citation", None, None, "med beaktande av fördraget") in seen
    assert ("recital", "1", None, "Syftet med direktivet.") in seen
    assert ("preamble", None, None, "HÄRIGENOM FÖRESKRIVS FÖLJANDE.") in seen
    # a division's designation (TI) and its title (STI) stay separate fields
    heading = next(b for b in doc.body if b.kind == "heading")
    assert (heading.level, heading.label, heading.text) == (
        1, "KAPITEL I", "ALLMÄNNA BESTÄMMELSER")
    # the article carries its number as the citation anchor, its designation
    # (TI.ART) as the label and its title (STI.ART) as the text
    article = next(b for b in doc.body if b.kind == "article")
    assert article.num == "1" and article.anchor == "1"
    assert (article.label, article.text) == ("Artikel 1", "Innehåll")
    # numbered paragraph, then a lead paragraph + a list point
    assert ("paragraph", "1", None, "I detta direktiv fastställs åtgärder.") in seen
    assert ("paragraph", "2", None, "Följande fastställs:") in seen
    assert ("point", "a", None, "skyldigheter.") in seen


# an article whose body is a numbered enumeration sitting directly under the
# ALINEA (no numbered paragraph), one of whose entries carries a lettered
# sub-list -- the shape of GDPR art. 4 def. 22, whose sub-points were dropped
DEF_LIST_XML = """<ACT>
  <BIB.INSTANCE><DATE ISO="20160427">20160427</DATE></BIB.INSTANCE>
  <TITLE><TI><P>Test</P></TI></TITLE>
  <ENACTING.TERMS>
    <ARTICLE IDENTIFIER="004">
      <TI.ART>Artikel 4</TI.ART><STI.ART>Definitioner</STI.ART>
      <ALINEA>
        <P>I denna förordning avses med</P>
        <LIST TYPE="ARAB">
          <ITEM><NP><NO.P>1.</NO.P><TXT>uppgift: något.</TXT></NP></ITEM>
          <ITEM><NP><NO.P>22.</NO.P>
            <TXT>berörd myndighet: en myndighet på grund av att</TXT>
            <P><LIST TYPE="alpha">
              <ITEM><NP><NO.P>a)</NO.P><TXT>den ansvarige är etablerad,</TXT></NP></ITEM>
              <ITEM><NP><NO.P>b)</NO.P><TXT>registrerade påverkas, eller</TXT></NP></ITEM>
            </LIST></P></NP></ITEM>
        </LIST>
      </ALINEA>
    </ARTICLE>
  </ENACTING.TERMS>
</ACT>"""


def test_definition_list_entries_are_paragraphs_with_nested_points():
    doc = parse_formex(ET.fromstring(DEF_LIST_XML), "32016R0679", "swe")
    seen = [(b.kind, b.num, b.text) for b in doc.body]
    # the article's own numbered entries are paragraph-level ("22." not "22)")
    assert ("paragraph", "1", "uppgift: något.") in seen
    assert ("paragraph", "22", "berörd myndighet: en myndighet på grund av att") in seen
    # the lettered sub-list is captured as points (previously dropped entirely)
    assert ("point", "a", "den ansvarige är etablerad,") in seen
    assert ("point", "b", "registrerade påverkas, eller") in seen
    # they hang off a *paragraph*, so they are the first point level and carry no
    # nesting level -- an enumeration emitted as paragraphs is not a point level,
    # and counting it as one indented these a step past every other lettered point
    assert all(b.depth is None for b in doc.body if b.kind == "point")
    # nesting reconstructs to article.paragraph.point anchors
    anchors = {a for a, _ in anchored_blocks(to_artifact(doc)["structure"])}
    assert {"4.22", "4.22.a", "4.22.b"} <= anchors


# a definitions article written in Formex's explicit definition-list markup
# (DLIST: PREFIX + TERM + DEFINITION), the shape of 2015/1535 art. 1.1 -- with a
# definition that carries its own nested definition list (b) and one that carries
# a plain roman sub-list (c). Previously unhandled: DLIST fell through every
# branch, so the whole article flattened into one paragraph with no points, no
# anchors and no defined terms.
DLIST_XML = """<ACT>
  <BIB.INSTANCE><DATE ISO="20150909">20150909</DATE></BIB.INSTANCE>
  <TITLE><TI><P>Test</P></TI></TITLE>
  <ENACTING.TERMS>
    <ARTICLE IDENTIFIER="001">
      <TI.ART>Artikel 1</TI.ART>
      <PARAG IDENTIFIER="001.001"><NO.PARAG>1.</NO.PARAG><ALINEA>
        <P>I detta direktiv gäller följande definitioner:</P>
        <DLIST SEPARATOR=":">
          <DLIST.ITEM><PREFIX>a)</PREFIX>
            <TERM><HT TYPE="ITALIC">produkt</HT></TERM>
            <DEFINITION>alla industriellt framställda produkter,</DEFINITION></DLIST.ITEM>
          <DLIST.ITEM><PREFIX>b)</PREFIX>
            <TERM><HT TYPE="ITALIC">tjänst</HT></TERM>
            <DEFINITION><P>alla informationssamhällets tjänster.</P>
              <P>I denna definition avses med</P>
              <DLIST SEPARATOR=":">
                <DLIST.ITEM><PREFIX>i)</PREFIX><TERM>på distans</TERM>
                  <DEFINITION>tjänster utan samtidig närvaro,</DEFINITION></DLIST.ITEM>
                <DLIST.ITEM><PREFIX>ii)</PREFIX><TERM>på elektronisk väg</TERM>
                  <DEFINITION>en tjänst som sänds vid utgångspunkten.</DEFINITION></DLIST.ITEM>
              </DLIST></DEFINITION></DLIST.ITEM>
          <DLIST.ITEM><PREFIX>c)</PREFIX>
            <TERM><HT TYPE="ITALIC">föreskrift för tjänster</HT></TERM>
            <DEFINITION><P>ett krav av allmän art.</P>
              <LIST TYPE="roman">
                <ITEM><NP><NO.P>i)</NO.P><TXT>uttryckligen och riktat,</TXT></NP></ITEM>
                <ITEM><NP><NO.P>ii)</NO.P><TXT>indirekt eller accessoriskt.</TXT></NP></ITEM>
              </LIST></DEFINITION></DLIST.ITEM>
        </DLIST>
      </ALINEA></PARAG>
    </ARTICLE>
  </ENACTING.TERMS>
</ACT>"""


def test_definition_list_markup_becomes_points_with_terms():
    doc = parse_formex(ET.fromstring(DLIST_XML), "32015L1535", "swe")
    seen = [(b.kind, b.num, b.depth, b.text) for b in doc.body]
    # the announcing line stays the paragraph; each DLIST.ITEM is its own point,
    # its term and definition joined by the list's separator
    assert ("paragraph", "1", None,
            "I detta direktiv gäller följande definitioner:") in seen
    assert ("point", "a", None,
            "produkt: alla industriellt framställda produkter,") in seen
    # a definition holding a nested list keeps only its own lead text ...
    assert ("point", "b", None, "tjänst: alla informationssamhällets tjänster. "
            "I denna definition avses med") in seen
    # ... and the nested list becomes points one level deeper, whether it is
    # written as another DLIST (b) or as a plain LIST (c)
    assert ("point", "i", 2, "på distans: tjänster utan samtidig närvaro,") in seen
    assert ("point", "i", 2, "uttryckligen och riktat,") in seen


def test_definition_list_markup_anchors_and_defines():
    art = to_artifact(parse_formex(ET.fromstring(DLIST_XML), "32015L1535", "swe"))
    anchors = dict(anchored_blocks(art["structure"]))
    # a nested point hangs under its parent point, so the two sub-lists that both
    # start at "i)" do not collide on one id
    assert {"1.1", "1.1.a", "1.1.b", "1.1.b.i", "1.1.b.ii", "1.1.c",
            "1.1.c.i", "1.1.c.ii"} <= set(anchors)
    # each entry's term is extracted and anchored, the nested ones included; the
    # announcing paragraph defines nothing
    defines = {a: b["defines"] for a, b in anchors.items() if b.get("defines")}
    assert defines == {"1.1.a": "produkt", "1.1.b": "tjänst",
                       "1.1.b.i": "på distans", "1.1.b.ii": "på elektronisk väg",
                       "1.1.c": "föreskrift för tjänster"}


# a list item whose sub-list sits *inside* the item's own TXT rather than beside
# it -- the shape of 93/104 art. 18.1 b. Read with the plain skip, the sub-list
# lands in the item's lead text and again as its own points, printing it twice.
NESTED_IN_TXT_XML = """<ACT>
  <BIB.INSTANCE><DATE ISO="19931123">19931123</DATE></BIB.INSTANCE>
  <TITLE><TI><P>Test</P></TI></TITLE>
  <ENACTING.TERMS>
    <ARTICLE IDENTIFIER="018">
      <TI.ART>Artikel 18</TI.ART>
      <PARAG IDENTIFIER="018.001"><NO.PARAG>1.</NO.PARAG><ALINEA>
        <LIST TYPE="alpha">
          <ITEM><NP><NO.P>a)</NO.P><TXT>Medlemsstaterna skall anta de lagar
            som är nödvändiga.</TXT></NP></ITEM>
          <ITEM><NP><NO.P>b)</NO.P><TXT><LIST TYPE="roman">
            <ITEM><NP><NO.P>i)</NO.P><TXT>En medlemsstat får dock avstå,
              varvid den skall se till att</TXT>
              <P><LIST TYPE="DASH">
                <ITEM><P>ingen arbetsgivare kräver mer än 48 timmar,</P></ITEM>
              </LIST></P></NP></ITEM>
          </LIST></TXT></NP></ITEM>
        </LIST>
      </ALINEA></PARAG>
    </ARTICLE>
  </ENACTING.TERMS>
</ACT>"""


def test_sublist_inside_an_items_own_text_is_not_printed_twice():
    doc = parse_formex(ET.fromstring(NESTED_IN_TXT_XML), "31993L0104", "swe")
    seen = [(b.kind, b.num, b.depth, b.text) for b in doc.body]
    # point b holds only its sub-list: its own lead is empty, and the sub-list
    # appears once -- as points, not folded into b's text as well
    assert ("point", "b", None, "") in seen
    assert sum("En medlemsstat får dock avstå" in text for *_, text in seen) == 1
    assert ("point", "i", 2, "En medlemsstat får dock avstå, varvid den skall "
            "se till att") in seen
    # a dash bullet keeps the marker the source writes ...
    assert ("point", None, 3, "ingen arbetsgivare kräver mer än 48 timmar,") in seen
    # ... and the citable points anchor under their parent. The paragraph is
    # also its own first stycke ("artikel 18.1 första stycket")
    anchors = [a for a, _ in anchored_blocks(to_artifact(doc)["structure"])]
    assert anchors == ["18", "18.1", "18.1.S1", "18.1.a", "18.1.b", "18.1.b.i"]


# an item with no marker of its own whose lead is a P and whose sub-list sits in
# a second P -- the shape of 2009/68 annex II. The item has no TXT at all, so a
# `.//TXT` lead lookup reaches into the sub-list and reads its first item's text
# as the parent's, printing it on both blocks.
LEAD_BELOW_SUBLIST_XML = """<ACT>
  <BIB.INSTANCE><DATE ISO="20090713">20090713</DATE></BIB.INSTANCE>
  <TITLE><TI><P>Test</P></TI></TITLE>
  <ENACTING.TERMS>
    <ARTICLE IDENTIFIER="001">
      <TI.ART>Artikel 1</TI.ART>
      <ALINEA>
        <LIST TYPE="DASH">
          <ITEM>
            <P>Bestämmelserna gäller även för särskilda strålkastare, med
              följande ändringar:</P>
            <P><LIST TYPE="alpha">
              <ITEM><NP><NO.P>a)</NO.P><TXT>De lägsta belysningsvärden som
                fastställs minskas.</TXT></NP></ITEM>
            </LIST></P>
          </ITEM>
        </LIST>
      </ALINEA>
    </ARTICLE>
  </ENACTING.TERMS>
</ACT>"""


def test_item_lead_is_its_own_not_its_sublists_first_entry():
    doc = parse_formex(ET.fromstring(LEAD_BELOW_SUBLIST_XML), "32009L0068", "swe")
    seen = [(b.kind, b.num, b.depth, b.text) for b in doc.body]
    assert ("point", None, None, "Bestämmelserna gäller även för särskilda "
            "strålkastare, med följande ändringar:") in seen
    assert ("point", "a", 2,
            "De lägsta belysningsvärden som fastställs minskas.") in seen
    # the sub-item's text belongs to the sub-item alone
    assert sum("De lägsta belysningsvärden" in text for *_, text in seen) == 1


# a paragraph written as several ALINEAs -- Formex's sub-paragraphs (stycken).
# The shape of 2005/85 art. 9.2, whose two derogations from the rule its first
# stycke states were dropped outright: `parag.find("ALINEA")` read one and left
# the siblings unparsed (831 stycken across 600 sampled acts).
STYCKEN_XML = """<ACT>
  <BIB.INSTANCE><DATE ISO="20051201">20051201</DATE></BIB.INSTANCE>
  <TITLE><TI><P>Test</P></TI></TITLE>
  <ENACTING.TERMS>
    <ARTICLE IDENTIFIER="009">
      <TI.ART>Artikel 9</TI.ART>
      <PARAG IDENTIFIER="009.002"><NO.PARAG>2.</NO.PARAG>
        <ALINEA><P>När en ansökan avslås ska skälen anges i beslutet.</P>
          <LIST TYPE="alpha">
            <ITEM><NP><NO.P>a)</NO.P><TXT>de faktiska skälen,</TXT></NP></ITEM>
          </LIST></ALINEA>
        <ALINEA>Medlemsstaterna behöver inte uppge skälen.</ALINEA>
        <ALINEA>Dessutom behöver de inte lämna skriftlig information.</ALINEA>
      </PARAG>
    </ARTICLE>
    <ARTICLE IDENTIFIER="008">
      <TI.ART>Artikel 8</TI.ART>
      <ALINEA>Medlemsstaterna ska se till att ansökningar prövas.</ALINEA>
      <ALINEA>De ska då beakta samtliga omständigheter.</ALINEA>
    </ARTICLE>
  </ENACTING.TERMS>
</ACT>"""


def test_further_stycken_of_a_paragraph_are_kept():
    doc = parse_formex(ET.fromstring(STYCKEN_XML), "32005L0085", "swe")
    seen = [(b.kind, b.num, b.text) for b in doc.body]
    # the paragraph's own text is its first stycke; the rest follow as `stycke`
    assert ("paragraph", "2", "När en ansökan avslås ska skälen anges i beslutet.") in seen
    assert ("stycke", "2", "Medlemsstaterna behöver inte uppge skälen.") in seen
    assert ("stycke", "3", "Dessutom behöver de inte lämna skriftlig information.") in seen
    # an article whose stycken sit directly under it, with no numbered paragraph:
    # every one is a `stycke`, numbered from 1, so the unnumbered prose that
    # trails the enacting terms (a signature block) is not mistaken for one
    assert ("stycke", "1",
            "Medlemsstaterna ska se till att ansökningar prövas.") in seen
    assert ("stycke", "2", "De ska då beakta samtliga omständigheter.") in seen


# the other way Formex writes stycken: sibling <P>s inside a *single* ALINEA,
# about a quarter of the multi-stycke paragraphs (2009/1272 art. 40.2). No text
# was lost this way -- it was flattened into one paragraph -- but the citation
# grammar mints a #40.2.S2 the page then did not carry.
STYCKEN_IN_ONE_ALINEA_XML = """<ACT>
  <BIB.INSTANCE><DATE ISO="20091210">20091210</DATE></BIB.INSTANCE>
  <TITLE><TI><P>Test</P></TI></TITLE>
  <ENACTING.TERMS>
    <ARTICLE IDENTIFIER="040">
      <TI.ART>Artikel 40</TI.ART>
      <PARAG IDENTIFIER="040.002"><NO.PARAG>2.</NO.PARAG><ALINEA>
        <P>Anbudsförfarandet ska inledas enligt följande:</P>
        <LIST TYPE="alpha">
          <ITEM><NP><NO.P>a)</NO.P><TXT>genom ett meddelande,</TXT></NP></ITEM>
        </LIST>
        <P>Den första tidsfristen ska läggas tidigast sex dagar efter.</P>
        <P>En andra tidsfrist får fastställas.</P>
      </ALINEA></PARAG>
    </ARTICLE>
  </ENACTING.TERMS>
</ACT>"""


def test_stycken_written_as_sibling_paragraphs_in_one_alinea():
    # parsed with the *production* parser: lxml frees an element proxy as soon as
    # the last reference to it goes, so a split keyed on `id()` mis-splits there
    # while passing green on a stdlib-ElementTree tree, where identity holds
    doc = parse_formex(_lxml(STYCKEN_IN_ONE_ALINEA_XML), "32009R1272", "swe")
    seen = [(b.kind, b.num, b.text) for b in doc.body]
    # each <P> with prose of its own opens a stycke; the list between them belongs
    # to the stycke it follows, not to the one after
    assert ("paragraph", "2", "Anbudsförfarandet ska inledas enligt följande:") in seen
    assert ("point", "a", "genom ett meddelande,") in seen
    assert ("stycke", "2",
            "Den första tidsfristen ska läggas tidigast sex dagar efter.") in seen
    assert ("stycke", "3", "En andra tidsfrist får fastställas.") in seen
    anchors = [a for a, _ in anchored_blocks(to_artifact(doc)["structure"])]
    assert anchors == ["40", "40.2", "40.2.S1", "40.2.a", "40.2.S2", "40.2.S3"]


def test_stycke_anchors_follow_the_sfs_grammar():
    art = to_artifact(parse_formex(ET.fromstring(STYCKEN_XML), "32005L0085", "swe"))
    anchors = [a for a, _ in anchored_blocks(art["structure"])]
    # "artikel 9.2" and "artikel 9.2 första stycket" are distinct citations that
    # name the same run of text, so the paragraph answers to both. Document order:
    # the first stycke's own point precedes the stycken that follow it
    assert anchors[:6] == ["9", "9.2", "9.2.S1", "9.2.a", "9.2.S2", "9.2.S3"]
    # an article's own stycken hang off the article ("artikel 8 andra stycket")
    assert anchors[-3:] == ["8", "8.S1", "8.S2"]
    # a point is named the same whichever stycke holds it: the stycke never
    # enters its key, which stays article.paragraph.point
    assert "9.2.a" in anchors and "9.2.S1.a" not in anchors


# an amending act: article 1 has its own lettered enumeration, and one of its
# points quotes the replacement text -- a whole article of the *amended* act,
# with a lettered list of its own
AMENDING_XML = """<ACT>
  <BIB.INSTANCE><DATE ISO="20150420">20150420</DATE></BIB.INSTANCE>
  <TITLE><TI><P>Test</P></TI></TITLE>
  <ENACTING.TERMS>
    <ARTICLE IDENTIFIER="001">
      <TI.ART>Artikel 1</TI.ART>
      <ALINEA>
        <P>Förordning (EU) nr 1 ska ändras på följande sätt:</P>
        <LIST TYPE="alpha">
          <ITEM><NP><NO.P>a)</NO.P><TXT>Artikel 3 ska ersättas med följande:
            <QUOT.S><ARTICLE IDENTIFIER="003"><TI.ART>Artikel 3</TI.ART><ALINEA>
              <P>Med avvikelse gäller följande:</P>
              <LIST TYPE="alpha">
                <ITEM><NP><NO.P>a)</NO.P><TXT>ett citerat led,</TXT></NP></ITEM>
                <ITEM><NP><NO.P>b)</NO.P><TXT>ett citerat led till.</TXT></NP></ITEM>
              </LIST></ALINEA></ARTICLE></QUOT.S></TXT></NP></ITEM>
          <ITEM><NP><NO.P>b)</NO.P><TXT>Artikel 4 utgår.</TXT></NP></ITEM>
        </LIST>
      </ALINEA>
    </ARTICLE>
  </ENACTING.TERMS>
</ACT>"""


def test_quoted_amending_text_stays_prose():
    doc = parse_formex(ET.fromstring(AMENDING_XML), "32015R0613", "swe")
    art = to_artifact(doc)
    anchors = [a for a, _ in anchored_blocks(art["structure"])]
    # the amending act's own points anchor; the quoted act's lettered list is not
    # lifted into them (it would collide with them, "1.a" twice on one page).
    # The article's unnumbered lead is its first stycke ("artikel 1 första stycket")
    assert anchors == ["1", "1.S1", "1.a", "1.b"]
    # ... and it is not lost either -- the quotation is kept whole, as prose
    quoting = next(b for b in doc.body if b.num == "a")
    assert quoting.text.endswith("a) ett citerat led, b) ett citerat led till.")


# CELLAR serves an act published across several OJ files under a GENERAL root
# that wraps the act in CONTENTS -- 2004/18's divisions sit there directly,
# while the Charter of Fundamental Rights nests them one further level down in
# a GR.SEQ. Reading only ENACTING.TERMS left both with no articles at all.
GENERAL_XML = """<GENERAL>
  <BIB.INSTANCE><DATE ISO="20040331">20040331</DATE></BIB.INSTANCE>
  <TITLE><TI><P>Direktiv 2004/18/EG</P></TI></TITLE>
  <CONTENTS>
    <PREAMBLE>
      <GR.CONSID>
        <CONSID><NP><NO.P>(1)</NO.P><TXT>Vid tilldelning av kontrakt.</TXT></NP></CONSID>
      </GR.CONSID>
    </PREAMBLE>
    <TOC><TITLE><TI>INNEHÅLL</TI></TITLE></TOC>
    <DIVISION>
      <TITLE><TI>AVDELNING I</TI></TITLE>
      <ARTICLE IDENTIFIER="001">
        <TI.ART>Artikel 1</TI.ART><STI.ART>Definitioner</STI.ART>
        <ALINEA>I detta direktiv används följande beteckningar.</ALINEA>
      </ARTICLE>
    </DIVISION>
  </CONTENTS>
</GENERAL>"""

GR_SEQ_XML = """<GENERAL>
  <BIB.INSTANCE><DATE ISO="20161207">20161207</DATE></BIB.INSTANCE>
  <TITLE><TI><P>Europeiska unionens stadga om de grundläggande rättigheterna</P></TI></TITLE>
  <CONTENTS>
    <GR.SEQ LEVEL="1">
      <TITLE><TI><P>EUROPEISKA UNIONENS STADGA</P></TI></TITLE>
      <PREAMBLE.GEN>
        <TITLE><TI><P>Ingress</P></TI></TITLE>
        <P>Europas folk har skapat en allt fastare sammanslutning.</P>
      </PREAMBLE.GEN>
      <ENACTING.TERMS>
        <ARTICLE IDENTIFIER="001">
          <TI.ART>Artikel 1</TI.ART><STI.ART>Människans värdighet</STI.ART>
          <ALINEA>Människans värdighet är okränkbar.</ALINEA>
        </ARTICLE>
      </ENACTING.TERMS>
    </GR.SEQ>
  </CONTENTS>
</GENERAL>"""


def test_general_root_act_body_sits_under_contents():
    doc = parse_formex(ET.fromstring(GENERAL_XML), "32004L0018", "swe")
    assert doc.title == "Direktiv 2004/18/EG"
    assert doc.date == "20040331"
    seen = [(b.kind, b.text) for b in doc.body]
    assert ("recital", "Vid tilldelning av kontrakt.") in seen
    # a TITLE with no STI beside it is the title itself, not a designation
    heading = next(b for b in doc.body if b.kind == "heading")
    assert (heading.label, heading.text) == (None, "AVDELNING I")
    article = next(b for b in doc.body if b.kind == "article")
    assert article.num == "1"
    assert (article.label, article.text) == ("Artikel 1", "Definitioner")


def test_general_root_act_body_nested_in_a_gr_seq():
    doc = parse_formex(ET.fromstring(GR_SEQ_XML), "12016P/TXT", "swe")
    seen = [(b.kind, b.text) for b in doc.body]
    article = next(b for b in doc.body if b.kind == "article")
    assert article.num == "1" and article.anchor == "1"
    # the preamble prose is kept under its own heading ...
    assert ("heading", "Ingress") in seen
    assert ("paragraph",
            "Europas folk har skapat en allt fastare sammanslutning.") in seen
    # ... but the sequence's TITLE only restates the document title
    assert ("heading", "EUROPEISKA UNIONENS STADGA") not in seen


JUDGMENT_XML = """<JUDGMENT>
  <BIB.JUDGMENT><NO.ECLI ECLI="ECLI:EU:C:2020:981">EU:C:2020:981</NO.ECLI></BIB.JUDGMENT>
  <TITLE><TI><P>Domstolens dom</P>
    <P>den <DATE ISO="20201217">17 december 2020</DATE></P></TI></TITLE>
  <INTERMEDIATE><INDEX><KEYWORD>Frihet att tillhandahålla tjänster</KEYWORD></INDEX></INTERMEDIATE>
  <JUDGMENT.INIT><P>genom beslut av den <DATE ISO="20190321">21 mars
    2019</DATE>, i mål C-311/19,</P></JUDGMENT.INIT>
  <CONTENTS.JUDGMENT>
    <GR.SEQ LEVEL="1"><TITLE><TI>Bakgrund</TI></TITLE></GR.SEQ>
    <NP.ECR IDENTIFIER="NP0001"><TXT>Den nationella domstolen frågar.</TXT></NP.ECR>
  </CONTENTS.JUDGMENT>
  <JURISDICTION><INTRO>Domstolen beslutar:</INTRO>
    <NP><NO.P>1.</NO.P><TXT>Artikel 56 FEUF ska tolkas.</TXT></NP></JURISDICTION>
</JUDGMENT>"""


OPINION_XML = """<CONCLUSION>
  <BIB.JUDGMENT><NO.ECLI ECLI="ECLI:EU:C:1988:431">EU:C:1988:431</NO.ECLI></BIB.JUDGMENT>
  <TITLE><TI><P>Förslag till avgörande av generaladvokat Lenz</P>
    <P>den <DATE ISO="19881005">5 oktober 1988</DATE></P></TI></TITLE>
  <CONTENTS.CONCLUSION>
    <TITLE><TI><HT TYPE="BOLD">Herr ordförande, mina damer och herrar domare,</HT></TI></TITLE>
    <P>Detta mål gäller en tvist.</P>
    <GR.SEQ LEVEL="1"><TITLE><TI>A - Bakgrund</TI></TITLE>
      <NP><NO.P>1.</NO.P><TXT>Sökanden väckte talan.</TXT></NP>
      <NP><NO.P>2.</NO.P><TXT>Kommissionen bestred.</TXT></NP>
    </GR.SEQ>
  </CONTENTS.CONCLUSION>
</CONCLUSION>"""


def test_parse_opinion():
    # an AG opinion (Formex CONCLUSION) parses to prose + numbered paragraphs, not
    # its footnotes alone; doctype comes from the CC CELEX code (E4)
    doc = parse_formex(ET.fromstring(OPINION_XML), "61987CC0253", "swe")
    assert doc.doctype == "opinion"
    assert doc.ecli == "ECLI:EU:C:1988:431"
    assert doc.date == "19881005"
    assert doc.title == "Herr ordförande, mina damer och herrar domare,"
    seen = [(b.kind, b.num, b.text) for b in doc.body]
    assert ("paragraph", None, "Detta mål gäller en tvist.") in seen
    assert ("heading", None, "A - Bakgrund") in seen
    assert ("paragraph", "1", "Sökanden väckte talan.") in seen
    assert ("paragraph", "2", "Kommissionen bestred.") in seen


def test_parse_judgment():
    doc = parse_formex(ET.fromstring(JUDGMENT_XML), "62019CJ0311", "swe")
    assert doc.doctype == "judgment"
    assert doc.ecli == "ECLI:EU:C:2020:981"
    # the delivery date from TITLE -- never JUDGMENT.INIT's referral date (the
    # golden cross-check caught the artifact carrying the referral date)
    assert doc.date == "20201217"
    assert doc.title == "Domstolens dom den 17 december 2020"
    seen = [(b.kind, b.num, b.text) for b in doc.body]
    assert ("keyword", None, "Frihet att tillhandahålla tjänster") in seen
    assert ("heading", None, "Bakgrund") in [(b.kind, b.num, b.text) for b in doc.body]
    assert ("paragraph", "1", "Den nationella domstolen frågar.") in seen
    assert ("ruling", "1", "Artikel 56 FEUF ska tolkas.") in seen


# trimmed from the real C-513/99 (Concordia Bus) swe.fmx4: pre-2012 ECR Formex
# numbers its paragraphs with plain NP (NO.P + TXT), not NP.ECR, and nests the
# ruling's JURISDICTION inside CONTENTS.JUDGMENT rather than at the root
OLD_JUDGMENT_XML = """<JUDGMENT>
  <BIB.JUDGMENT><REF.CASE FILE="ECRCJ2002SVA.0800721301.case.xml"><NO.CASE>C-513/99</NO.CASE></REF.CASE><NO.CELEX>61999J0513</NO.CELEX></BIB.JUDGMENT>
  <CURR.TITLE><LEFT>DOM AV DEN <DATE ISO="20020917">17.9.2002</DATE> — MÅL C-513/99</LEFT></CURR.TITLE>
  <TITLE><TI><P><IE/></P></TI></TITLE>
  <CONTENTS.JUDGMENT>
    <GR.SEQ LEVEL="1"><TITLE><TI><P>Dom</P></TI></TITLE>
      <NP><NO.P>1</NO.P><TXT>Högsta förvaltningsdomstolen har ställt tre frågor om
        tolkningen av artikel 36.1 i rådets direktiv 92/50/EEG.</TXT></NP>
      <NP><NO.P>3</NO.P><TXT>I artikel 1 i direktiv 92/50 föreskrivs följande:</TXT>
        <P><QUOT.S LEVEL="1"><LIST TYPE="alpha"><ITEM>
          <NP><NO.P>a)</NO.P><TXT>offentliga tjänsteavtal: skriftliga avtal med
            ekonomiska villkor</TXT></NP>
        </ITEM></LIST></QUOT.S></P></NP>
    </GR.SEQ>
    <JURISDICTION><INTRO><P>På dessa grunder beslutar</P><P>DOMSTOLEN</P></INTRO>
      <LIST TYPE="ARAB"><ITEM><NP><NO.P>1)</NO.P><TXT>Artikel 36.1 a i direktiv
        92/50/EEG skall tolkas så.</TXT></NP></ITEM></LIST>
    </JURISDICTION>
  </CONTENTS.JUDGMENT>
</JUDGMENT>"""


def test_parse_old_judgment_np_paragraphs_and_nested_ruling():
    # two thirds of the judgment corpus (1965-ca 2012) uses NP, which the
    # judgment path ignored -- every such judgment parsed to its header and
    # preamble alone, so no article citations fed the EU case-law rail
    doc = parse_formex(ET.fromstring(OLD_JUDGMENT_XML), "61999CJ0513", "swe")
    assert doc.doctype == "judgment"
    assert doc.date == "20020917"
    seen = [(b.kind, b.num, b.text) for b in doc.body]
    assert ("heading", None, "Dom") in seen
    assert ("paragraph", "1", "Högsta förvaltningsdomstolen har ställt tre frågor"
            " om tolkningen av artikel 36.1 i rådets direktiv 92/50/EEG.") in seen
    # the paragraph keeps its own lead-in, and the act it quotes in the P
    # beside it becomes a `citat` block keeping that act's own marker
    assert ("paragraph", "3",
            "I artikel 1 i direktiv 92/50 föreskrivs följande:") in seen
    assert ("citat", "a", "offentliga tjänsteavtal: skriftliga avtal med "
            "ekonomiska villkor") in seen
    # a quoted act's own list items are not judgment paragraphs
    assert not any(b.kind == "paragraph" and b.num == "a" for b in doc.body)
    # the ruling is found even though JURISDICTION sits inside CONTENTS.JUDGMENT
    assert ("ruling", "1)",
            "Artikel 36.1 a i direktiv 92/50/EEG skall tolkas så.") in seen


# trimmed from the real C-311/18 (Schrems II) swe.fmx4: a numbered paragraph
# that introduces a verbatim quotation of another act, which is the shape of
# almost every one of its sections 3 to 42
QUOTING_JUDGMENT_XML = """<JUDGMENT>
  <BIB.JUDGMENT><NO.CELEX>62018J0311</NO.CELEX></BIB.JUDGMENT>
  <CURR.TITLE><LEFT>DOM AV DEN <DATE ISO="20200716">16.7.2020</DATE></LEFT></CURR.TITLE>
  <TITLE><TI><P>Domstolens dom</P></TI></TITLE>
  <INTERMEDIATE><KEYWORD>Direktiv 2002/58/EG</KEYWORD></INTERMEDIATE>
  <CONTENTS.JUDGMENT>
    <GR.SEQ LEVEL="1"><TITLE><TI><P>Dom</P></TI></TITLE>
      <NP.ECR IDENTIFIER="NP0001"><NO.P>1</NO.P>
        <TXT>Begäran avser tolkningen av artikel 3.2 och artikel 28.3 i
        direktiv 95/46/EG.</TXT></NP.ECR>
      <NP.ECR IDENTIFIER="NP0004"><NO.P>4</NO.P>
        <TXT>I artikel 25 i direktiv 95/46/EG föreskrevs följande:</TXT>
        <P><QUOT.S LEVEL="1">
          <PARAG IDENTIFIER="025.001"><NO.PARAG>1.</NO.PARAG>
            <ALINEA>Medlemsstaterna skall föreskriva att överföringen får ske
            om ifrågavarande tredje land säkerställer en adekvat
            skyddsnivå.</ALINEA></PARAG>
          <PARAG IDENTIFIER="025.002"><NO.PARAG>2.</NO.PARAG>
            <ALINEA>Bedömningen skall ske på grundval av alla de
            förhållanden som har samband med en överföring.</ALINEA>
            <ALINEA>Hänsyn skall särskilt tas till artikel 31.2.</ALINEA></PARAG>
          <ALINEA><P>Detta direktiv gäller inte för</P>
            <LIST TYPE="alpha"><ITEM><NP><NO.P>a)</NO.P>
              <TXT>verksamhet som inte omfattas av gemenskapsrätten</TXT>
            </NP></ITEM></LIST></ALINEA>
        </QUOT.S></P>
      </NP.ECR>
    </GR.SEQ>
  </CONTENTS.JUDGMENT>
</JUDGMENT>"""


def test_quoted_act_keeps_its_own_structure_and_numbering():
    # the parser published the lead-in ("I artikel 25 ... föreskrevs följande:")
    # and nothing after it, because it read only the paragraph's first TXT.
    # Folding the quotation into the paragraph instead runs the quoted act's
    # numbered paragraphs together and loses its "1." and "2.".
    doc = parse_formex(ET.fromstring(QUOTING_JUDGMENT_XML), "62018CJ0311", "swe")
    assert [(b.kind, b.num, b.depth, b.quoted, b.text[:34]) for b in doc.body] == [
        ("keyword", None, None, None, "Direktiv 2002/58/EG"),
        ("heading", None, None, None, "Dom"),
        ("paragraph", "1", None, None, "Begäran avser tolkningen av artike"),
        ("paragraph", "4", None, None, "I artikel 25 i direktiv 95/46/EG f"),
        ("citat", "1", None, "paragraph", "Medlemsstaterna skall föreskriva a"),
        ("citat", "2", None, "paragraph", "Bedömningen skall ske på grundval "),
        # the quoted paragraph's second sub-paragraph: the act's own marker
        # belongs to the first, and a stycke ordinal is an anchor a quotation
        # has no business minting
        ("citat", None, None, "paragraph", "Hänsyn skall särskilt tas till art"),
        ("citat", None, None, "paragraph", "Detta direktiv gäller inte för"),
        ("citat", "a", 1, "point", "verksamhet som inte omfattas av ge"),
    ]


def test_quotation_hangs_off_the_paragraph_that_introduces_it():
    art = to_artifact(parse_formex(ET.fromstring(QUOTING_JUDGMENT_XML),
                                   "62018CJ0311", "swe"))
    dom = next(n for n in art["structure"] if n.get("type") == "heading")
    parag = next(c for c in dom["children"] if c.get("num") == "4")
    assert parag["type"] == "paragraph"
    assert [c["type"] for c in parag["children"]] == ["citat"] * 5


QUOTED_ARTICLE_XML = """<JUDGMENT>
  <BIB.JUDGMENT><NO.CELEX>62018J0311</NO.CELEX></BIB.JUDGMENT>
  <CURR.TITLE><LEFT>DOM AV DEN <DATE ISO="20200716">16.7.2020</DATE></LEFT></CURR.TITLE>
  <CONTENTS.JUDGMENT>
    <GR.SEQ LEVEL="1"><TITLE><TI><P>Dom</P></TI></TITLE>
      <NP.ECR IDENTIFIER="NP0011"><NO.P>11</NO.P>
        <TXT>Artikel 23 i förordning (EU) 2016/679 lyder, i den lydelse som
        avses i beslutet:</TXT>
        <P><QUOT.S LEVEL="1"><ARTICLE IDENTIFIER="023">
          <TI.ART>Artikel 23</TI.ART><STI.ART>Begränsningar</STI.ART>
          <PARAG IDENTIFIER="023.001"><NO.PARAG>1.</NO.PARAG>
            <ALINEA>Det ska vara möjligt att införa en
            lagstiftningsåtgärd.</ALINEA>
            <ALINEA>Begränsningen ska ske med respekt för rättigheterna.</ALINEA>
          </PARAG>
          <PARAG IDENTIFIER="023.002"><NO.PARAG>2.</NO.PARAG>
            <ALINEA>Varje lagstiftningsåtgärd ska innehålla bestämmelser.</ALINEA>
          </PARAG>
        </ARTICLE></QUOT.S></P>
      </NP.ECR>
      <NP.ECR IDENTIFIER="NP0012"><NO.P>12</NO.P>
        <TXT>Domstolen erinrar om att uttrycket <QUOT.S>adekvat
        skyddsnivå</QUOT.S> ska förstås i vid mening.</TXT></NP.ECR>
    </GR.SEQ>
  </CONTENTS.JUDGMENT>
</JUDGMENT>"""


def test_a_quoted_article_keeps_the_acts_numbering_and_mints_no_ordinal():
    # `parse_article` numbers stycken, and "2."/"3." are ordinals the quoted
    # act never prints -- invented numbering attributed to another document
    doc = parse_formex(ET.fromstring(QUOTED_ARTICLE_XML), "62018CJ0311", "swe")
    quoted = [(b.num, b.quoted, b.text[:30]) for b in doc.body
              if b.kind == "citat"]
    assert quoted == [
        (None, "article", "Artikel 23 Begränsningar"),
        ("1", "paragraph", "Det ska vara möjligt att inför"),
        (None, "paragraph", "Begränsningen ska ske med resp"),
        ("2", "paragraph", "Varje lagstiftningsåtgärd ska "),
    ]


def test_a_quotation_inside_a_sentence_stays_in_the_paragraphs_text():
    # 3 480 of the 205 212 quotations inside a numbered paragraph sit in a
    # sentence rather than standing as their own block; lifting one out would
    # cut the sentence in half
    doc = parse_formex(ET.fromstring(QUOTED_ARTICLE_XML), "62018CJ0311", "swe")
    twelve = next(b for b in doc.body if b.num == "12")
    assert twelve.kind == "paragraph"
    assert twelve.text == ("Domstolen erinrar om att uttrycket adekvat "
                           "skyddsnivå ska förstås i vid mening.")


def test_a_keyword_does_not_put_an_act_in_anaphoric_focus():
    # a keyword is an index entry, not the judgment speaking. Schrems II's list
    # ends on the Privacy Shield decision, and its paragraph 1 -- which names
    # its own act only at the end of a coordination -- pinned that paragraph's
    # leading bare articles on the decision.
    art = to_artifact(parse_formex(ET.fromstring(QUOTING_JUDGMENT_XML),
                                   "62018CJ0311", "swe"))
    blocks = flatten_structure(art["structure"])
    body = [b for b in blocks if b.get("type") != "keyword"]
    assert not any("32002L0058" in run["uri"] for block in body
                   for run in block.get("text", []) if isinstance(run, dict))
    # the keyword's own phrase still links: it names the act, it just does not
    # leave it in focus
    assert any("32002L0058" in run["uri"] for block in blocks
               if block.get("type") == "keyword"
               for run in block.get("text", []) if isinstance(run, dict))


def test_a_quotations_bare_article_pins_to_the_act_it_quotes():
    # "Hänsyn skall särskilt tas till artikel 31.2" is directive 95/46's own
    # article 31.2, because that is the act the paragraph introducing the
    # quotation named -- not whatever act the judgment last happened to mention
    art = to_artifact(parse_formex(ET.fromstring(QUOTING_JUDGMENT_XML),
                                   "62018CJ0311", "swe"))
    uris = [run["uri"] for block in flatten_structure(art["structure"])
            if block.get("type") == "citat"
            for run in block.get("text", []) if isinstance(run, dict)]
    assert uris == ["https://lagen.nu/ext/celex/31995L0046#31.2"]
    # ... and the quotation's own act is not left in the judgment's focus
    # afterwards: the acts a quoted document names are its business, not the
    # court's


# trimmed from the real 6/64 (Costa mot E.N.E.L.) swe.fmx4: the oldest ECR
# judgments number nothing -- their sections hold plain P prose, and the section
# nesting is in the GR.SEQ LEVEL attribute
COSTA_XML = """<JUDGMENT>
  <BIB.JUDGMENT><REF.CASE FILE="ECRCJ1954SVA.0100021101.case.xml"><NO.CASE>6/64</NO.CASE></REF.CASE><NO.CELEX>61964J0006</NO.CELEX></BIB.JUDGMENT>
  <CURR.TITLE><LEFT>DOM AV DEN <DATE ISO="19640715">15.7.1964</DATE> — MÅL 6/64</LEFT><RIGHT>COSTA MOT E. N. E. L.</RIGHT></CURR.TITLE>
  <TITLE><TI><P>Domstolens dom</P><P>av den <DATE ISO="19640715">15 juli 1964</DATE></P></TI></TITLE>
  <CONTENTS.JUDGMENT>
    <GR.SEQ LEVEL="1"><TITLE><TI><P>dom</P></TI></TITLE>
      <GR.SEQ LEVEL="2"><TITLE><TI><P>DOMSKÄL</P></TI></TITLE>
        <P>Giudice Conciliatore i Milano har förklarat målet vilande.</P>
        <GR.SEQ LEVEL="3"><TITLE><TI><P>Tillämpningen av artikel 177</P></TI></TITLE>
          <GR.SEQ LEVEL="4"><TITLE><TI><P>Invändning på grund av frågans lydelse</P></TI></TITLE>
            <P>Invändning har gjorts mot frågan.</P>
          </GR.SEQ>
        </GR.SEQ>
      </GR.SEQ>
    </GR.SEQ>
    <JURISDICTION><INTRO><P>DOMSTOLEN förordnar följande:</P></INTRO>
      <LIST TYPE="ARAB"><ITEM><NP><NO.P>1)</NO.P><TXT>Artikel 102 innehåller inte
        några bestämmelser som kan ge upphov till rättigheter.</TXT></NP></ITEM></LIST>
    </JURISDICTION>
  </CONTENTS.JUDGMENT>
</JUDGMENT>"""


def test_judgment_body_keeps_document_order_and_unnumbered_prose():
    # the contents were read in three passes -- every heading, then every
    # NP.ECR, then every NP -- which put the whole body after the *last*
    # heading and dropped unnumbered P prose entirely. Costa mot E.N.E.L.
    # published five empty section headings and no text at all.
    doc = parse_formex(ET.fromstring(COSTA_XML), "61964CJ0006", "swe")
    assert [(b.kind, b.level, b.text) for b in doc.body] == [
        ("heading", 1, "dom"),
        ("heading", 2, "DOMSKÄL"),
        ("paragraph", None, "Giudice Conciliatore i Milano har förklarat målet vilande."),
        ("heading", 3, "Tillämpningen av artikel 177"),
        ("heading", 4, "Invändning på grund av frågans lydelse"),
        ("paragraph", None, "Invändning har gjorts mot frågan."),
        ("paragraph", None, "DOMSTOLEN förordnar följande:"),
        ("ruling", None, "Artikel 102 innehåller inte några bestämmelser som kan"
         " ge upphov till rättigheter."),
    ]
    # each heading nests under the one above it, not beside it
    tree = to_artifact(doc)["structure"]
    assert [n["text"] for n in tree] == [["dom"]]
    assert tree[0]["children"][0]["text"] == ["DOMSKÄL"]


# trimmed from the real 61987CJ0031 (Beentjes) eng.fmx4: for the oldest ECR
# cases the report for the hearing is the only text CELLAR holds, and it is
# English -- Swedish did not exist pre-accession
HEARING_XML = """<REPORT.HEARING>
  <BIB.REPORT.HEARING><REF.CASE FILE="ECRCJ1988ENA.0800463501.case.xml"><NO.CASE>31/87</NO.CASE></REF.CASE><NO.CELEX>61987J0031</NO.CELEX></BIB.REPORT.HEARING>
  <CURR.TITLE><LEFT>REPORT FOR THE HEARING — CASE 31/87</LEFT><RIGHT>BEENTJES v NETHERLANDS STATE</RIGHT></CURR.TITLE>
  <TITLE><TI><P><HT TYPE="UC">Report for the Hearing</HT></P><P>delivered in Case 31/87<NOTE NOTE.ID="E0001" NUMBERING="STAR"><P>Language of the Case: Dutch.</P></NOTE></P></TI></TITLE>
  <CONTENTS>
    <GR.SEQ LEVEL="1"><TITLE><TI><NP><NO.P>I —</NO.P><TXT>Relevant legislation</TXT></NP></TI></TITLE>
      <P>Council Directive 71/305/EEC of 26 July 1971 is intended to secure
freedom of establishment and freedom to provide services.</P>
      <P>Article 29 (5) of Directive 71/305/EEC provides for an examination
of abnormally low tenders.</P>
    </GR.SEQ>
  </CONTENTS>
</REPORT.HEARING>"""


def test_hearing_report_stands_in_for_the_oldest_judgments():
    # Beentjes parsed to a single footnote: REPORT.HEARING fell through to the
    # ACT branch, which found no enacting terms. The report's "Relevant
    # legislation" prose is where the case's act citations live, so it parses
    # through the opinion's prose walker -- and, being English, its citations
    # are scanned with the English grammar (doc.lang drives _refparser)
    doc = parse_formex(ET.fromstring(HEARING_XML), "61987CJ0031", "eng")
    assert doc.doctype == "judgment"
    texts = [(b.kind, b.text) for b in doc.body]
    assert ("heading", "I — Relevant legislation") in texts
    assert any(kind == "paragraph" and "71/305/EEC" in text
               for kind, text in texts)
    art = to_artifact(doc)
    uris = [run["uri"] for block in flatten_structure(art["structure"])
            for run in block.get("text", []) if isinstance(run, dict)]
    assert "https://lagen.nu/ext/celex/31971L0305" in uris
    assert "https://lagen.nu/ext/celex/31971L0305#29.5" in uris


def test_judgment_without_title_date_has_none_not_the_referral_date():
    # old ECR Formex: empty TITLE, only referral/protocol dates in
    # JUDGMENT.INIT -- those must never stand in for the delivery date
    # (parse_dir fills the date from the notice work date instead)
    xml = """<JUDGMENT><TITLE><TI><P><IE/></P></TI></TITLE>
      <JUDGMENT.INIT><P>REFERENCE under the Protocol of
        <DATE ISO="19710603">3 June 1971</DATE></P></JUDGMENT.INIT>
    </JUDGMENT>"""
    assert parse_formex(ET.fromstring(xml), "61981CJ0025", "eng").date is None


def test_act_oj_number_is_unpadded():
    # Formex zero-pads NO.OJ ("042"); the citable form is "L 42"
    xml = """<ACT><BIB.INSTANCE>
      <DOCUMENT.REF><COLL>L</COLL><NO.OJ>042</NO.OJ></DOCUMENT.REF>
      <DATE ISO="20060210">20060210</DATE></BIB.INSTANCE>
      <TITLE><TI><P>Test</P></TI></TITLE></ACT>"""
    assert parse_formex(ET.fromstring(xml), "32006R0249", "swe").oj == "L 42"


def test_to_artifact_shape_and_runs():
    art = to_artifact(parse_formex(ET.fromstring(ACT_XML), "32022L2555", "swe"))
    assert art["uri"] == "https://lagen.nu/ext/celex/32022L2555"
    assert art["celex"] == "32022L2555" and art["oj"] == "L 333"
    assert art["date"] == "2022-12-14"     # compact Formex DATE@ISO, dashed out
    # every block text is an inline-run list (plain strings / link dicts)
    blocks = flatten_structure(art["structure"])
    for block in blocks:
        assert isinstance(block["text"], list)
    article = next(b for b in blocks if b["type"] == "article")
    assert article["id"] == "1"     # citation anchor -> artifact id


ANNEX_XML = """<ANNEX>
  <TITLE><TI>BILAGA III</TI></TITLE>
  <CONTENTS>
    <P>Förteckning enligt artikel 3.</P>
    <TBL COLS="2"><CORPUS>
      <ROW TYPE="HEADER"><CELL>Sektor</CELL><CELL>Undersektor</CELL></ROW>
      <ROW><CELL>Energi</CELL><CELL>El</CELL></ROW>
    </CORPUS></TBL>
  </CONTENTS>
</ANNEX>"""


def test_parse_document_embeds_annex_as_single_doc():
    doc = parse_document([ET.fromstring(ACT_XML), ET.fromstring(ANNEX_XML)],
                         "32022L2555", "swe")
    # main-act content is still there ...
    assert any(b.kind == "article" for b in doc.body)
    # ... followed by the annex as a level-1 heading with a bilaga anchor ...
    head = next(b for b in doc.body if b.text == "BILAGA III")
    assert head.kind == "heading" and head.level == 1 and head.anchor == "bilaga-3"
    # ... and the annex table flattened to row blocks
    assert any(b.kind == "row" and "Energi" in b.text for b in doc.body)


# 2004/18's jämförelsetabell: a blank spacer column sits between the three
# repealed directives and the "Ny/Ändrad" column, and every data row runs to a
# trailing empty cell. Dropping empties wholesale slid "Ändrad" into the spacer's
# place and broke the column->act mapping eurlex/correspond.py depends on.
SPACER_TABLE_XML = """<ANNEX>
  <TITLE><TI><P>BILAGA XII</P></TI></TITLE>
  <CONTENTS>
    <TBL COLS="6"><CORPUS>
      <ROW TYPE="HEADER"><CELL>Detta direktiv</CELL><CELL>Direktiv 93/37/EEG</CELL>
        <CELL>Direktiv 92/50/EEG</CELL><CELL>Andra rättsakter</CELL><CELL/></ROW>
      <ROW><CELL>Artikel 1.2 a</CELL><CELL>Artikel 1 a</CELL><CELL>Artikel 1 a</CELL>
        <CELL/><CELL>Ändrad</CELL></ROW>
      <ROW><CELL>Artikel 1.2 b</CELL><CELL>Artikel 1 c</CELL><CELL>—</CELL>
        <CELL/><CELL/></ROW>
    </CORPUS></TBL>
  </CONTENTS>
</ANNEX>"""


def _rows(doc):
    return [b.text for b in doc.body if b.kind == "row"]


def test_table_rows_keep_interior_empty_cells_and_drop_trailing_ones():
    doc = parse_formex(ET.fromstring(SPACER_TABLE_XML), "32004L0018", "swe")
    assert _rows(doc) == [
        # header: the trailing empty cell goes, so the row ends at column 4
        "Detta direktiv | Direktiv 93/37/EEG | Direktiv 92/50/EEG | "
        "Andra rättsakter",
        # data: the *interior* blank survives, so "Ändrad" stays in column 6
        "Artikel 1.2 a | Artikel 1 a | Artikel 1 a |  | Ändrad",
        # both trailing blanks go
        "Artikel 1.2 b | Artikel 1 c | —"]


def test_parse_dir_stores_the_lineage_on_the_artifact(tmp_path):
    """The lineage is extracted by `parse`, not by a separate action: it is the
    act's own jämförelsetabell, so it belongs in the act's artifact like the
    förarbete parser's `implements` (rule:artifact-is-truth)."""
    d = tmp_path / "2004" / "32004L0018"
    d.mkdir(parents=True)
    (d / "swe.fmx4").write_text(SPACER_TABLE_XML)
    art = parse_dir(d, "32004L0018")
    assert {(e["newArticle"], e["oldArticle"], e["oldLaw"].rsplit("/", 1)[1])
            for e in art["correspondence"]} == {
        ("1", "1", "31993L0037"), ("1", "1", "31992L0050")}


def test_parse_dir_publishes_what_the_act_amends_and_implements(tmp_path):
    """The notice's relations reach the artifact under the same metadata keys
    lib/catalog.relation_links already mints typed edges from, so an EU
    amending act publishes the rpubl:andrar a Swedish ändringsföreskrift does
    -- and every measure over base acts can exclude it."""
    d = tmp_path / "2008" / "32008R0803"
    d.mkdir(parents=True)
    (d / "swe.fmx4").write_text(ACT_XML)
    (d / "notice.ttl").write_bytes(notice_ttl(
        "32008R0803", "2008-08-08", [], ("1", None),
        {"amends": ["32002R0881"], "implements": ["32001R2580"]}))
    art = parse_dir(d, "32008R0803")
    assert art["metadata"] == {
        "andrar": ["https://lagen.nu/ext/celex/32002R0881"],
        "genomfor_akt": ["https://lagen.nu/ext/celex/32001R2580"]}


def test_parse_dir_leaves_no_metadata_key_on_a_base_act(tmp_path):
    """A base act carries neither relation, and gets no key at all rather than
    two empty lists -- an act with an empty `andrar` would read as one the
    harvest asked about and found nothing for."""
    d = tmp_path / "2002" / "32002R0881"
    d.mkdir(parents=True)
    (d / "swe.fmx4").write_text(ACT_XML)
    (d / "notice.ttl").write_bytes(
        notice_ttl("32002R0881", "2002-05-27", [], ("1", None)))
    assert "metadata" not in parse_dir(d, "32002R0881")


def test_parse_dir_leaves_no_lineage_key_on_an_act_without_a_table(tmp_path):
    # the overwhelmingly common case: no key at all rather than an empty list
    d = tmp_path / "2022" / "32022L2555"
    d.mkdir(parents=True)
    (d / "swe.fmx4").write_text(ACT_XML)
    assert "correspondence" not in parse_dir(d, "32022L2555")


def test_correspond_reads_the_columns_the_parser_emits():
    """The seam: eurlex/correspond.py claims a cell's index is its column. Assert
    it end to end against the real parser rather than a hand-built run list --
    that claim is only true because of the empty-cell rule above."""
    doc = parse_formex(ET.fromstring(SPACER_TABLE_XML), "32004L0018", "swe")
    edges, stats = correspondence(to_artifact(doc))
    assert stats["columns"] == 2            # the two directive columns
    assert {(e["oldLaw"].rsplit("/", 1)[1], e["newArticle"], e["oldArticle"])
            for e in edges} == {("31993L0037", "1", "1"),
                                ("31992L0050", "1", "1")}


# an article whose text carries a footnote citing another act
ACT_WITH_NOTE = """<ACT>
  <TITLE><TI><P>Testdirektiv</P></TI></TITLE>
  <ENACTING.TERMS><ARTICLE IDENTIFIER="001"><TI.ART>Artikel 1</TI.ART>
    <PARAG IDENTIFIER="001.001"><NO.PARAG>1.</NO.PARAG>
      <ALINEA>Se den tidigare rättsakten<NOTE NOTE.ID="E1"><P>Europaparlamentets
        och rådets direktiv (EU) 2016/1148 av den 6 juli 2016 (EUT L 194, s. 1).
        </P></NOTE>.</ALINEA></PARAG></ARTICLE></ENACTING.TERMS>
</ACT>"""


def test_footnotes_become_blocks_and_yield_citations():
    doc = parse_document([ET.fromstring(ACT_WITH_NOTE)], "32016L9999", "swe")
    para = next(b for b in doc.body if b.kind == "paragraph")
    assert "EUT" not in para.text and "194" not in para.text   # footnote not in prose
    note = next(b for b in doc.body if b.kind == "note")
    assert note.num == "1" and "2016/1148" in note.text
    # the footnote's act reference mints a CELEX link in the artifact
    art = to_artifact(doc)
    note_runs = next(b for b in flatten_structure(art["structure"])
                     if b["type"] == "note")["text"]
    assert any(isinstance(r, dict) and r["uri"].endswith("32016L1148")
               for r in note_runs)


def test_annex_anchor():
    assert _annex_anchor("BILAGA III") == "bilaga-3"
    assert _annex_anchor("ANNEX 2") == "bilaga-2"
    assert _annex_anchor("BILAGA I") == "bilaga-1"
    assert _annex_anchor("BILAGA") is None      # no recognisable number


def test_content_file_prefers_swe_zip(tmp_path):
    (tmp_path / "eng.fmx4").write_bytes(b"x")
    (tmp_path / "swe.fmx4.zip").write_bytes(b"x")
    (tmp_path / "swe.fmx4").write_bytes(b"x")
    path, lang, route = content_file(tmp_path)
    assert lang == "swe" and path.name == "swe.fmx4.zip" and route == "fmx4"


def test_content_file_ignores_orphaned_tmp_partial(tmp_path):
    # a hard-killed write_atomic orphans its temp file; "swe.fmx4.tmp" contains
    # the token "fmx4" but is not content -- suffix matching must reject it
    (tmp_path / "swe.fmx4.tmp").write_bytes(b"x")
    (tmp_path / "swe.html").write_bytes(b"x")
    path, lang, route = content_file(tmp_path)
    assert path.name == "swe.html" and route == "html"
    (tmp_path / "swe.html").unlink()
    assert content_file(tmp_path) == (None, None, None)


def test_load_formex_rejects_zip_without_formex_member(tmp_path):
    # a bundle holding only the .doc.xml manifest wrapper has no act content;
    # that is remote-data validation, so it raises (not asserts)
    bundle = tmp_path / "swe.fmx4.zip"
    with zipfile.ZipFile(bundle, "w") as zf:
        zf.writestr("L_2016001SV.doc.xml", "<wrapper/>")
    with pytest.raises(ValueError, match="no Formex member"):
        load_formex(bundle)


def test_formex_members_promotes_the_main_act_over_a_leading_annex(tmp_path):
    # filename order is OJ page order, and in 8 documents (32015R0228 among
    # them) an annex prints on an earlier page than the act itself -- the sort
    # alone led the whole parse with the annex ("BILAGA VII" for a title)
    bundle = tmp_path / "swe.fmx4.zip"
    with zipfile.ZipFile(bundle, "w") as zf:
        zf.writestr("L_2015049SV.01000101.xml",
                    '<ANNEX><TITLE><TI><P>BILAGA VII</P></TI></TITLE></ANNEX>')
        zf.writestr("L_2015049SV.01000301.xml",
                    '<?xml version="1.0"?><!DOCTYPE ACT SYSTEM "formex.dtd">'
                    '<ACT><TITLE><TI><P>Förordning (EU) 2015/228</P></TI>'
                    '</TITLE><ENACTING.TERMS/></ACT>')
        zf.writestr("L_2015049SV.01001201.xml",
                    '<ANNEX><TITLE><TI><P>BILAGA I</P></TI></TITLE></ANNEX>')
    names = [name for name, _ in formex_members(bundle)]
    assert names == ["L_2015049SV.01000301.xml",      # the act leads ...
                     "L_2015049SV.01000101.xml",      # ... annexes keep
                     "L_2015049SV.01001201.xml"]      # page order
    doc = parse_document(load_formex(bundle), "32015R0228", "swe")
    assert doc.title == "Förordning (EU) 2015/228"


def test_formex_members_leaves_a_corrigendum_manifestation_alone(tmp_path):
    # a corrigendum manifestation leads with its CORR member by design; only
    # ANNEX members are ever demoted
    bundle = tmp_path / "swe.fmx4.zip"
    with zipfile.ZipFile(bundle, "w") as zf:
        zf.writestr("L_2004229SV.01003501.xml", "<CORR><CONTENTS/></CORR>")
        zf.writestr("L_2004229SV.01003601.xml", "<ACT><ENACTING.TERMS/></ACT>")
    assert [name for name, _ in formex_members(bundle)] \
        == ["L_2004229SV.01003501.xml", "L_2004229SV.01003601.xml"]


# both notice shapes: the live path's synthesized n-triples and the bulk
# unpacker's turtle subset
NOTICE_NT = (b'<http://publications.europa.eu/resource/celex/X> '
             b'<http://publications.europa.eu/ontology/cdm#work_date_document> '
             b'"2016-04-27"^^<http://www.w3.org/2001/XMLSchema#date> .\n')
NOTICE_TTL = (b'@prefix j.0: <http://publications.europa.eu/ontology/cdm#> .\n'
              b'<x> j.0:work_date_document "1982-03-31"^^xsd:date ;\n'
              b'  j.0:resource_legal_id_celex "61981CJ0025" .\n')


def test_notice_work_date_reads_both_notice_shapes(tmp_path):
    (tmp_path / "notice.ttl").write_bytes(NOTICE_NT)
    assert notice_work_date(tmp_path) == "2016-04-27"
    (tmp_path / "notice.ttl").write_bytes(NOTICE_TTL)
    assert notice_work_date(tmp_path) == "1982-03-31"
    (tmp_path / "notice.ttl").unlink()
    assert notice_work_date(tmp_path) is None


CDM = b"http://publications.europa.eu/ontology/cdm#"


def _validity_notice(in_force, *end_dates):
    lines = [b'<x> <' + CDM + b'resource_legal_in-force> "%b" .' % in_force]
    lines += [b'<x> <' + CDM + b'resource_legal_date_end-of-validity> '
              b'"%b"^^<http://www.w3.org/2001/XMLSchema#date> .' % d
              for d in end_dates]
    return b"\n".join(lines) + b"\n"


def test_notice_repeal_date_needs_the_in_force_flag(tmp_path):
    # 31995L0046: out of force, one end date -- the date the GDPR's article 94
    # repeal took effect
    (tmp_path / "notice.ttl").write_bytes(_validity_notice(b"0", b"2018-05-24"))
    assert notice_repeal_date(tmp_path) == "2018-05-24"
    # 32006L0040: in force *and* carrying a past end date. The date alone would
    # repeal an act that is not repealed
    (tmp_path / "notice.ttl").write_bytes(_validity_notice(b"1", b"2009-04-28"))
    assert notice_repeal_date(tmp_path) is None
    # a notice from before the validity pair was harvested says nothing
    (tmp_path / "notice.ttl").write_bytes(NOTICE_NT)
    assert notice_repeal_date(tmp_path) is None
    (tmp_path / "notice.ttl").unlink()
    assert notice_repeal_date(tmp_path) is None


def test_notice_repeal_date_takes_the_latest_real_end_date(tmp_path):
    # 31981L0576 carries two end dates; EUR-Lex prints the last one
    (tmp_path / "notice.ttl").write_bytes(
        _validity_notice(b"0", b"1996-08-05", b"2014-10-31"))
    assert notice_repeal_date(tmp_path) == "2014-10-31"
    # 9999-12-31 is CELLAR's "no end date" placeholder, never a date
    (tmp_path / "notice.ttl").write_bytes(
        _validity_notice(b"0", b"2014-10-31", b"9999-12-31"))
    assert notice_repeal_date(tmp_path) == "2014-10-31"


def test_notice_repeal_date_reads_the_bulk_turtle_shape(tmp_path):
    (tmp_path / "notice.ttl").write_bytes(
        b'@prefix j.0: <http://publications.europa.eu/ontology/cdm#> .\n'
        b'<x> j.0:resource_legal_in-force "0" ;\n'
        b'  j.0:resource_legal_date_end-of-validity "2018-05-24"^^xsd:date .\n')
    assert notice_repeal_date(tmp_path) == "2018-05-24"


def test_notice_repeal_date_reads_the_boolean_in_force_shape(tmp_path):
    # the older tree notices on disk write the flag as a turtle boolean rather
    # than a digit, and rapper renders the same rdf/xml as "false"^^xsd:boolean.
    # Reading only the digit form made an act out of force read as in force
    (tmp_path / "notice.ttl").write_bytes(
        b'@prefix j.0: <http://publications.europa.eu/ontology/cdm#> .\n'
        b'<x> j.0:in-force false ;\n'
        b'  j.0:resource_legal_in-force false ;\n'
        b'  j.0:resource_legal_date_end-of-validity "2018-05-24"^^xsd:date .\n')
    assert notice_repeal_date(tmp_path) == "2018-05-24"

    (tmp_path / "notice.ttl").write_bytes(
        b'<x> <http://publications.europa.eu/ontology/cdm#'
        b'resource_legal_in-force> "false"^^<http://www.w3.org/2001/'
        b'XMLSchema#boolean> .\n'
        b'<x> <http://publications.europa.eu/ontology/cdm#'
        b'resource_legal_date_end-of-validity> "2014-10-31"'
        b'^^<http://www.w3.org/2001/XMLSchema#date> .\n')
    assert notice_repeal_date(tmp_path) == "2014-10-31"

    # `true` is in force, whatever end dates it also carries
    (tmp_path / "notice.ttl").write_bytes(
        b'@prefix j.0: <http://publications.europa.eu/ontology/cdm#> .\n'
        b'<x> j.0:resource_legal_in-force true ;\n'
        b'  j.0:resource_legal_date_end-of-validity "2009-04-28"^^xsd:date .\n')
    assert notice_repeal_date(tmp_path) is None


def test_notice_repeal_date_is_none_when_no_end_date_is_given(tmp_path):
    # 12019W_TXT: CELLAR says out of force but carries only the 9999 placeholder.
    # There is no date to filter on, so it stays listed rather than being
    # repealed on an invented date
    (tmp_path / "notice.ttl").write_bytes(
        b'@prefix j.0: <http://publications.europa.eu/ontology/cdm#> .\n'
        b'<x> j.0:resource_legal_in-force false ;\n'
        b'  j.0:resource_legal_date_end-of-validity "9999-12-31"^^xsd:date .\n')
    assert notice_repeal_date(tmp_path) is None


def _doc_dir(tmp_path, xml, notice=NOTICE_TTL):
    (tmp_path / "swe.fmx4").write_bytes(xml.encode())
    if notice is not None:
        (tmp_path / "notice.ttl").write_bytes(notice)
    return tmp_path


def test_parse_dir_fills_missing_date_from_notice(tmp_path):
    xml = """<JUDGMENT><TITLE><TI><P><IE/></P></TI></TITLE>
      <JUDGMENT.INIT><P>REFERENCE under the Protocol of
        <DATE ISO="19710603">3 June 1971</DATE></P></JUDGMENT.INIT>
    </JUDGMENT>"""
    art = parse_dir(_doc_dir(tmp_path, xml), "61981CJ0025")
    assert art["date"] == "1982-03-31"


def test_parse_dir_replaces_impossible_date_from_notice(tmp_path):
    # 61981CJ0025's source carries DATE ISO="19820231" -- the 31st of February
    xml = """<JUDGMENT><TITLE><TI><P>Judgment of
      <DATE ISO="19820231">31 February 1982</DATE></P></TI></TITLE></JUDGMENT>"""
    art = parse_dir(_doc_dir(tmp_path, xml), "61981CJ0025")
    assert art["date"] == "1982-03-31"


def test_parse_dir_stamps_the_repeal_date_on_the_artifact(tmp_path):
    # the repeal reaches the catalog (documents.expired) through the artifact,
    # the way every other extracted fact does -- which is what drops the act out
    # of browse, search and the API's document enumeration
    xml = """<ACT><BIB.INSTANCE><DATE ISO="19951024">19951024</DATE>
      </BIB.INSTANCE><TITLE><TI><P>Direktiv 95/46/EG</P></TI></TITLE></ACT>"""
    notice = (b'<x> <http://publications.europa.eu/ontology/cdm#'
              b'resource_legal_in-force> "0" .\n'
              b'<x> <http://publications.europa.eu/ontology/cdm#'
              b'resource_legal_date_end-of-validity> "2018-05-24"'
              b'^^<http://www.w3.org/2001/XMLSchema#date> .\n')
    art = parse_dir(_doc_dir(tmp_path, xml, notice), "31995L0046")
    assert art["expired"] == "2018-05-24"
    assert catalog._expired_date(art) == "2018-05-24"
    # an act still in force carries no such key at all
    art = parse_dir(_doc_dir(tmp_path, xml, NOTICE_NT), "31995L0046")
    assert "expired" not in art
    assert catalog._expired_date(art) is None


@pytest.mark.parametrize("celex,base", [
    ("32016R0900R(01)", "32016R0900"),      # an act's corrigendum: 'R(NN)'
    ("12019W/TXT(01)", "12019W/TXT"),       # a treaty text's revision: '(NN)'
])
def test_a_revision_inherits_the_repeal_of_what_it_revises(
        celex, base, tmp_path, monkeypatch):
    # CELLAR flags the act, never its corrigenda -- of 537 held corrigenda whose
    # base act we hold, none carry the flag. Without inheritance a repealed act
    # leaves its corrigendum ranked in search, listed by /api/v1/documents and
    # standing on a rail; a treaty revision additionally survives in the browse,
    # where the collapse keeps it and drops the repealed base
    base_dir = tmp_path / "base"
    base_dir.mkdir()
    (base_dir / "notice.ttl").write_bytes(
        _validity_notice(b"0", b"2026-08-17"))
    monkeypatch.setattr(P.layout, "eurlex_dir",
                        lambda bf: base_dir if bf == base else tmp_path / bf)
    assert P.revision_repeal_date(celex) == "2026-08-17"
    # the act itself is not a revision of anything
    assert P.revision_repeal_date(base) is None

    xml = """<ACT><BIB.INSTANCE><DATE ISO="20160601">20160601</DATE>
      </BIB.INSTANCE><TITLE><TI><P>Rättelse</P></TI></TITLE></ACT>"""
    art = parse_dir(_doc_dir(tmp_path, xml, NOTICE_NT), celex)
    assert art["expired"] == "2026-08-17"


def test_parse_dir_corrigendum_takes_its_own_notice_date(tmp_path):
    # a corrigendum's Formex bib is dated by the *corrected act*; its notice
    # work date (the correcting OJ's publication) is the document's own date
    xml = """<ACT><BIB.INSTANCE><DATE ISO="20120615">20120615</DATE>
      </BIB.INSTANCE><TITLE><TI><P>Rättelse</P></TI></TITLE></ACT>"""
    notice = (b'@prefix j.0: <http://publications.europa.eu/ontology/cdm#> .\n'
              b'<x> j.0:work_date_document "2021-04-15"^^xsd:date .\n')
    art = parse_dir(_doc_dir(tmp_path, xml, notice), "32012R0509R(03)")
    assert art["date"] == "2021-04-15"
    # the same act under a non-corrigendum CELEX keeps its own bib date
    art = parse_dir(_doc_dir(tmp_path, xml, notice), "32012R0509")
    assert art["date"] == "2012-06-15"


def test_exactly_one_act_is_uncarried():
    """A per-document carry exception is the kind of table that becomes a dumping
    ground, so the *contents* are the invariant: adding a second entry has to be a
    deliberate edit here, with the measurement that meets the bar the table's own
    comment states. Asserting on the prose instead (a length floor, say) only
    enforces verbosity, and gets padded to keep green."""
    assert list(UNCARRIED) == ["32018R0688"]


def test_an_uncarried_act_is_skipped_before_its_source_is_opened(monkeypatch):
    """`SkipDocument`, not an error: the driver writes the empty artifact that
    marks a document built-and-not-retried, so the act costs one skip per run
    instead of failing the whole rebuild's exit code every time. Raised by the
    parser rather than the build driver, so a --force run honours it too (the
    golden harness in tools/ calls `parse_dir` with no handler and would abort on
    one -- no uncarried CELEX is in its snapshot, and the claim is not made here)."""
    monkeypatch.setitem(UNCARRIED, "32099R9999", "because")
    # the 97 MB source is the problem, so nothing may open it: a nonexistent dir
    # would return None rather than raise if the guard were not the first statement
    with pytest.raises(SkipDocument, match="32099R9999: because"):
        parse_dir("/nonexistent", "32099R9999")
