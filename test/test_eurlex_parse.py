"""Tests for the EUR-Lex Formex parser."""

import zipfile
from xml.etree import ElementTree as ET

import pytest

from accommodanda.eurlex.correspond import correspondence
from accommodanda.eurlex.parse import (
    _annex_anchor,
    content_file,
    doctype,
    flatten,
    load_formex,
    notice_work_date,
    parse_dir,
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
    assert ("heading", "AVDELNING I") in seen
    article = next(b for b in doc.body if b.kind == "article")
    assert article.num == "1" and article.text == "Artikel 1 – Definitioner"


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
