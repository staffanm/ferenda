"""Tests for the frozen-corpus förarbete format adapters (REWRITE.md §7g).

Hermetic: every case runs against a small trimmed fixture under
``test/files/forarbete-legacy/`` (real slices of the frozen ferenda.old trees),
never the frozen paths themselves. One adapter per section: dokumentstatus XML
metadata (two eras), the text/tml ``<br>``-plaintext body incl. the ej-utgiven
sentinel, ABBYY OCR-XML pages, and the TRIPS ``div.body-text`` body.
"""

from pathlib import Path

import pytest

from accommodanda.forarbete.legacy_formats import (
    abbyy_pages,
    dokumentstatus_meta,
    riksdagen_html_paras,
    riksdagen_mso_paras,
    trips_paras,
    word_paras,
)

FIXTURES = Path(__file__).parent / "files" / "forarbete-legacy"


# --- Adapter 1: dokumentstatus XML ---------------------------------------

def test_dokumentstatus_meta_minimal_1971():
    meta = dokumentstatus_meta((FIXTURES / "dokumentstatus_1971.xml").read_bytes())
    assert meta["basefile"] == "1971:1"
    assert meta["identifier"] == "Prop. 1971:1"
    assert meta["date"] == "1971-12-31"
    assert meta["dok_id"] == "FU031"
    assert meta["htmlformat"] == "skanning2007"
    assert meta["source_url"] == "http://data.riksdagen.se/dokument/FU031"
    assert meta["title"].startswith("Kungl. Maj:ts proposition till riksdagen")
    assert meta["bilagor"] == [
        {"filnamn": "prop_1971__1.pdf", "filtyp": "pdf",
         "fil_url": "http://data.riksdagen.se/fil/"
                    "0AF53A43-1100-49F6-BD9E-2B2BFB2168A2"}]


def test_dokumentstatus_meta_slash_riksmote_1993_94():
    meta = dokumentstatus_meta(
        (FIXTURES / "dokumentstatus_1993-94.xml").read_bytes())
    assert meta["basefile"] == "1993/94:1"          # slash riksmöte kept verbatim
    assert meta["identifier"] == "Prop. 1993/94:1"
    assert meta["date"] == "1994-01-01"
    assert meta["htmlformat"] == "text/tml"
    assert [b["filnamn"] for b in meta["bilagor"]] == ["prop_199394__1.pdf"]


def test_dokumentstatus_meta_placeholder_date_is_none():
    # a placeholder/impossible datum is rejected to None, empty tags to None
    xml = (b'<dokumentstatus><dokument><rm>1975/76</rm><beteckning>100</beteckning>'
           b'<datum>0000-00-00 00:00:00</datum><titel/></dokument></dokumentstatus>')
    meta = dokumentstatus_meta(xml)
    assert meta["basefile"] == "1975/76:100"
    assert meta["date"] is None
    assert meta["title"] is None
    assert meta["bilagor"] == []


def test_dokumentstatus_meta_missing_beteckning_fails_fast():
    xml = b'<dokumentstatus><dokument><rm>1971</rm></dokument></dokumentstatus>'
    with pytest.raises(ValueError, match="rm/beteckning"):
        dokumentstatus_meta(xml)


# --- Adapter 2: riksdagen text/tml HTML body -----------------------------

def test_riksdagen_html_paras_text_tml():
    paras = riksdagen_html_paras(
        (FIXTURES / "riksdagen_text_tml.html").read_text(encoding="utf-8"))
    texts = [p.text for p in paras]
    assert texts[0] == "Regeringens proposition 1995/96:80"   # two <br> lines join
    assert "Ändrade relationer mellan staten och Svenska kyrkan" in texts
    # a <br><br>-free wrap over two indented lines reflows to one collapsed line
    assert "Marita Ulvskog (Civildepartementet)" in texts
    assert "Propositionens huvudsakliga innehåll" in texts
    assert all(not p.bold and not p.lead_bold for p in paras)   # no bold signal


def test_riksdagen_html_paras_ej_utgiven_sentinel():
    body = (FIXTURES / "riksdagen_ej_utgiven.html").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="Propositionen ej utgiven"):
        riksdagen_html_paras(body)


# --- Adapter 2b: riksdagen skanning2007 Word-export HTML ------------------

def test_riksdagen_mso_paras_skanning2007():
    paras = riksdagen_mso_paras(
        (FIXTURES / "riksdagen_skanning2007.html").read_text(encoding="utf-8"))
    texts = [p.text for p in paras]
    # the scanning-disclaimer banner (div.brask, no <p>) never leaks in
    assert not any("Observera att dokumentet" in t for t in texts)
    # a fully-<b> paragraph carries the bold heading signal (and its bold lead)
    assert paras[0].text == "Kungl. Maj: ts proposition nr 30 år 1971 Prop. 1971:30"
    assert paras[0].bold and paras[0].lead_bold
    assert any(p.text == "Propositionens liuTndsakliga innehåll" and p.bold
               for p in paras)
    # a plain body paragraph is not bold, and its U+00AD OCR line-break hyphen
    # is removed ("depar\xadtementschefen" -> "departementschefen")
    body = next(p for p in paras if "föredragande" in p.text)
    assert not body.bold and "departementschefen" in body.text
    assert not any("\xad" in t for t in texts)
    assert all("  " not in t for t in texts)             # whitespace collapsed


# --- Adapter 3: ABBYY FineReader OCR-XML ---------------------------------

def test_abbyy_pages_mapping_dehyphenation_and_block_filter():
    pages = abbyy_pages(FIXTURES / "abbyy_propkb.xml")
    assert [pageno for pageno, _ in pages] == [1, 2]            # 1-based page order

    page1 = [p.text for _, paras in pages[:1] for p in paras]
    body = next(t for t in page1 if t.startswith("Kongl. Maj:ts nådiga Proposition"))
    assert "den 13 November 1860" in body        # 'Novem¬' + 'ber' de-hyphenated
    assert "Beväringen; Gifven Stockholms" in body   # 'Gif¬' + 'ven' de-hyphenated
    assert "¬" not in body
    assert not any("TABELL" in t for t in page1)     # non-Text (table) block skipped

    page2 = [p.text for p in pages[1][1]]
    assert "50" in page2                             # the page-number Text block
    assert any("dithörande krigsförstärkningen, hvilken" in t for t in page2)  # '-'

    assert all(not p.bold and not p.lead_bold
               for _, paras in pages for p in paras)     # ABBYY carries no bold


# --- Adapter 4: TRIPS plaintext-HTML -------------------------------------

def test_trips_paras_proptrips():
    paras = trips_paras(
        (FIXTURES / "proptrips_1993-94.html").read_text(encoding="utf-8"))
    texts = [p.text for p in paras]
    assert texts[0] == ("Regeringens proposition 1993/94:1 "
                        "om organisationen vid fastighetstaxeringen")
    assert "Propositionens huvudsakliga innehåll" in texts
    assert any("anpassas till förfarandet vid inkomsttaxeringen." in t
               for t in texts)
    assert all("  " not in t for t in texts)         # justified runs collapsed
    assert all(not p.bold for p in paras)


def test_trips_paras_dirtrips():
    paras = trips_paras(
        (FIXTURES / "dirtrips_1987.html").read_text(encoding="utf-8"))
    texts = [p.text for p in paras]
    assert texts[0] == "Dir. 1987:10"
    assert "Mitt förslag" in texts and "Bakgrund" in texts     # headings on own line
    assert any("utreda handeln med tjänster med Sydafrika" in t for t in texts)
    assert any(t.startswith("I prop. 1984/85:56") for t in texts)
    assert all(not p.bold for p in paras)


# --- Adapter 5: legacy Word bodies ---------------------------------------

def test_word_paras_word95_doc():
    """The proptrips era's dominant format: a Word 6/95 binary, which POI's HWPF
    refuses outright -- so `word_paras` routes .doc through antiword. Locks in
    that the old binary yields real text, not the one-line garbage Word6Extractor
    returned (rule:lock-in-with-fixture)."""
    paras = word_paras(FIXTURES / "proptrips_word95.doc")
    texts = [p.text for p in paras]
    assert texts[0] == ("Utdrag ur protokoll vid regeringssammanträde "
                        "den 6 april 1998")
    assert any("statsministern Persson, ordförande" in t for t in texts)
    assert all("  " not in t for t in texts)         # runs collapsed like TRIPS
    # no font survives antiword's plaintext, so classify must recover headings
    # from numbering rather than weight -- as for every text-inferred body
    assert all(not p.bold and p.size == 0 for p in paras)


def test_word_paras_docx():
    """A .docx rides the shared POI XWPF reader (lib/poi), one Para per
    paragraph -- the other half of the word route."""
    paras = word_paras(FIXTURES / "proptrips_word.docx")
    texts = [p.text for p in paras]
    assert texts[:3] == ["Regeringens proposition", "2011/12:94",
                         "Nya faktureringsregler för mervärdesskatt m.m."]
    assert all(t.strip() for t in texts)             # empty paragraphs dropped
