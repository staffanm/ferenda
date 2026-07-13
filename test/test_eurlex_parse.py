"""Tests for the EUR-Lex Formex parser."""

import zipfile
from xml.etree import ElementTree as ET

import pytest

from accommodanda.eurlex.parse import (
    _annex_anchor,
    content_file,
    doctype,
    flatten,
    load_formex,
    parse_document,
    parse_formex,
    to_artifact,
)
from accommodanda.lib.eu_structure import anchored_blocks
from accommodanda.lib.eu_structure import flatten as flatten_structure


def _flat(xml):
    return flatten(ET.fromstring(xml))


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


def test_parse_act_body_structure():
    doc = parse_formex(ET.fromstring(ACT_XML), "32022L2555", "swe")
    seen = [(b.kind, b.num, b.level, b.text) for b in doc.body]
    assert ("citation", None, None, "med beaktande av fördraget") in seen
    assert ("recital", "1", None, "Syftet med direktivet.") in seen
    assert ("preamble", None, None, "HÄRIGENOM FÖRESKRIVS FÖLJANDE.") in seen
    assert ("heading", None, 1, "KAPITEL I ALLMÄNNA BESTÄMMELSER") in seen
    # the article carries its number as the citation anchor
    article = next(b for b in doc.body if b.kind == "article")
    assert article.num == "1" and article.anchor == "1"
    assert article.text == "Artikel 1 – Innehåll"
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
    # nesting reconstructs to article.paragraph.point anchors
    anchors = {a for a, _ in anchored_blocks(to_artifact(doc)["structure"])}
    assert {"4.22", "4.22.a", "4.22.b"} <= anchors


JUDGMENT_XML = """<JUDGMENT>
  <BIB.JUDGMENT><NO.ECLI ECLI="ECLI:EU:C:2020:981">EU:C:2020:981</NO.ECLI></BIB.JUDGMENT>
  <TITLE><TI>Domstolens dom</TI></TITLE>
  <INTERMEDIATE><INDEX><KEYWORD>Frihet att tillhandahålla tjänster</KEYWORD></INDEX></INTERMEDIATE>
  <JUDGMENT.INIT><P>I mål <DATE ISO="20190321">C-311/19</DATE>,</P></JUDGMENT.INIT>
  <CONTENTS.JUDGMENT>
    <GR.SEQ LEVEL="1"><TITLE><TI>Bakgrund</TI></TITLE></GR.SEQ>
    <NP.ECR IDENTIFIER="NP0001"><TXT>Den nationella domstolen frågar.</TXT></NP.ECR>
  </CONTENTS.JUDGMENT>
  <JURISDICTION><INTRO>Domstolen beslutar:</INTRO>
    <NP><NO.P>1.</NO.P><TXT>Artikel 56 FEUF ska tolkas.</TXT></NP></JURISDICTION>
</JUDGMENT>"""


def test_parse_judgment():
    doc = parse_formex(ET.fromstring(JUDGMENT_XML), "62019CJ0311", "swe")
    assert doc.doctype == "judgment"
    assert doc.ecli == "ECLI:EU:C:2020:981"
    assert doc.date == "20190321"
    assert doc.title == "Domstolens dom"
    seen = [(b.kind, b.num, b.text) for b in doc.body]
    assert ("keyword", None, "Frihet att tillhandahålla tjänster") in seen
    assert ("heading", None, "Bakgrund") in [(b.kind, b.num, b.text) for b in doc.body]
    assert ("paragraph", "1", "Den nationella domstolen frågar.") in seen
    assert ("ruling", "1", "Artikel 56 FEUF ska tolkas.") in seen


def test_to_artifact_shape_and_runs():
    art = to_artifact(parse_formex(ET.fromstring(ACT_XML), "32022L2555", "swe"))
    assert art["uri"] == "https://lagen.nu/ext/celex/32022L2555"
    assert art["celex"] == "32022L2555" and art["oj"] == "L 333"
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
