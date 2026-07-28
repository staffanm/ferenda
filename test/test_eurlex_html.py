"""Tests for the non-Formex EU parsers: the OJ HTML/XHTML parser and the shared
localized vocabulary (eng + swe), including the recital-table heuristic and the
old-flavour text-structure fallback."""

from pathlib import Path

from accommodanda.eurlex import lang as L
from accommodanda.eurlex.parse_html import parse_html

FILES = Path(__file__).parent / "files/eurlex"


def kinds(doc):
    return [b.kind for b in doc.body]


def recitals(doc):
    return [b for b in doc.body if b.kind == "recital"]


def parse_fixture(name, celex, lang):
    return parse_html((FILES / name).read_text(encoding="utf-8"), celex, lang)


def test_oj_class_act_maps_to_blocks():
    html = """<body>
      <p class="hd-date">9.4.1968</p><p class="hd-oj">L 88/1</p>
      <p class="doc-ti">REGULATION No 1</p>
      <p class="normal">Having regard to the Treaty,</p>
      <p class="normal">Whereas something is needed,</p>
      <p class="normal">HAS ADOPTED THIS REGULATION:</p>
      <p class="ti-art">Article 1</p>
      <p class="normal">The first rule applies.</p>
      <p class="note">( 1 ) OJ No 152.</p>
    </body>"""
    doc = parse_html(html, "31968R0001", "eng")
    assert doc.date == "1968-04-09" and doc.oj == "L 88"
    assert doc.title == "REGULATION No 1"
    assert kinds(doc) == ["citation", "recital", "preamble",
                          "article", "paragraph", "note"]
    art = next(b for b in doc.body if b.kind == "article")
    assert art.num == "1" and art.anchor == "1"


def test_legacy_classless_html_recovers_title_from_header_shape():
    # the old "Avis juridique important" HTML has no semantic title class; the
    # title is the class-less header line (act type + number + date), which runs
    # straight into the OJ publication reference -- both recovered and trimmed
    html = """<body>
      <p>31990L0630</p>
      <p>Kommissionens direktiv 90/630/EEG av den 30 oktober 1990 om anpassning
         till den tekniska utvecklingen av rådets direktiv 77/649/EEG om
         siktfältet i motorfordon Europeiska gemenskapernas officiella tidning
         nr L 341 , 06/12/1990 s. 0020 - 0029</p>
      <p>med beaktande av rådets direktiv 77/649/EEG av den 27 september 1977 om
         tillnärmning av medlemsstaternas lagstiftning</p>
      <p>Artikel 1</p>
      <p>Bilaga 3 ändras.</p>
    </body>"""
    doc = parse_html(html, "31990L0630", "swe")
    assert doc.title == (
        "Kommissionens direktiv 90/630/EEG av den 30 oktober 1990 om anpassning "
        "till den tekniska utvecklingen av rådets direktiv 77/649/EEG om "
        "siktfältet i motorfordon")


def test_title_classed_non_title_content_is_rejected():
    # an old consolidated-treaty page marks its whole table of contents (or
    # even the full preamble) as doc-ti; a "title" past TITLE_MAX is
    # misextraction and no title beats a page-long one
    toc = " ".join('<p class="doc-ti">PROTOKOLL OM %d</p>' % i
                   for i in range(80))
    doc = parse_html("<body>%s</body>" % toc, "12010A/TXT", "swe")
    assert doc.title == ""


def test_giant_title_shaped_paragraph_is_rejected():
    # a treaty's txt_te HTML is one giant <p> that passes the act-title shape
    # test (it cites directives and dates somewhere in the running text)
    giant = ("TREATY ON EUROPEAN UNION HIS MAJESTY THE KING recalling directive "
             "83/349/EEC of 13 June 1983 " + "and further provisions " * 100)
    html = "<body><p>%s</p><p>Artikel 1</p></body>" % giant
    assert parse_html(html, "11992M/TXT", "eng").title == ""


def test_legacy_classless_html_does_not_take_a_recital_as_title():
    # with no title-shaped header line, the visa ("med beaktande av …") -- which
    # also cites an act by number+date -- must NOT be picked up as the title
    html = """<body>
      <p>med beaktande av rådets direktiv 77/649/EEG av den 27 september 1977 om
         tillnärmning av medlemsstaternas lagstiftning</p>
      <p>Artikel 1</p>
    </body>"""
    assert parse_html(html, "31990L0630", "swe").title == ""


def test_recital_and_point_tables_vs_data_table():
    html = """<body>
      <p class="ti-art">Article 1</p>
      <table><tr><td>(a)</td><td>first point</td></tr>
             <tr><td>(b)</td><td>second point</td></tr></table>
      <table><tr><td>Apples</td><td>3</td></tr>
             <tr><td>Pears</td><td>5</td></tr></table>
    </body>"""
    doc = parse_html(html, "31968R0001", "eng")
    # the (a)/(b) table is a point list; the Apples/Pears table is data -> rows
    assert kinds(doc) == ["article", "point", "point", "row", "row"]


def test_heading_table_marker():
    html = ('<body><table><tr><td>TITLE I</td><td>General provisions</td>'
            '</tr></table></body>')
    doc = parse_html(html, "11957E", "eng")
    assert kinds(doc) == ["heading"]
    assert "TITLE I" in doc.body[0].text


def test_old_flavour_swedish_text_structure():
    # no semantic classes: structure inferred from the (swedish) text
    html = """<body>
      <p>RÅDET HAR UTFÄRDAT DETTA DIREKTIV</p>
      <p>Artikel 1</p>
      <p>Medlemsstaterna skall genomföra detta.</p>
      <p>AVDELNING II</p>
      <p>Artikel 2</p>
    </body>"""
    doc = parse_html(html, "31964L0475", "swe")
    assert kinds(doc) == ["preamble", "article", "paragraph", "heading", "article"]
    assert [b.num for b in doc.body if b.kind == "article"] == ["1", "2"]


def test_old_flavour_article_line_with_run_in_heading_gets_num():
    # the legacy txt_te HTML runs the article heading into the marker line
    # ("Artikel 1 Räckvidd") -- the num must still be extracted (31998L0070
    # et al. produced article blocks with num=None, an empty inventory for
    # forarbete's directive_articles)
    html = """<body>
      <p>RÅDET HAR UTFÄRDAT DETTA DIREKTIV</p>
      <p>Artikel 1 Räckvidd </p>
      <p>I detta direktiv fastställs krav.</p>
      <p>Artikel 3 Ändring av direktiv 93/12/EEG</p>
    </body>"""
    doc = parse_html(html, "31998L0070", "swe")
    assert [b.num for b in doc.body if b.kind == "article"] == ["1", "3"]


# --- pre-2000 "Avis juridique important" preambles ---------------------------
# These four fixtures are preamble excerpts of real acts, one per shape the
# format uses. None of them has a single <table>: the recitals are flat
# paragraphs, so before the fix every one of them parsed as a body `paragraph`
# and the acts reported zero recitals (31995L0046, the corpus's most-cited act,
# among them).


def test_legacy_swedish_numbered_recitals():
    # 31995L0046: markers are "1)", with no opening parenthesis, run into the
    # recital's own text; the list opens on the tail of the last visa ("… och
    # med beaktande av följande:") and closes on "HÄRIGENOM FÖRESKRIVS FÖLJANDE."
    doc = parse_fixture("avis-swe-numbered.html", "31995L0046", "swe")
    assert [b.num for b in recitals(doc)] == ["1", "2", "3", "4"]
    assert recitals(doc)[1].text.startswith("Systemen för databehandling")
    # the framing line is preamble matter, not a recital of its own
    assert kinds(doc)[:7] == ["preamble", "preamble", "preamble", "citation",
                              "citation", "citation", "preamble"]
    # and the enacting terms still start where they always did
    assert [b.num for b in doc.body if b.kind == "article"] == ["1"]
    assert kinds(doc)[-1] == "paragraph"


def test_legacy_swedish_unnumbered_recitals():
    # 31976L0399 and most pre-1999 Swedish acts number no recital at all: the
    # list is delimited by its framing line and the enacting formula alone
    doc = parse_fixture("avis-swe-unnumbered.html", "31976L0399", "swe")
    assert len(recitals(doc)) == 4
    assert all(b.num is None for b in recitals(doc))
    assert recitals(doc)[0].text.startswith("Rådets direktiv av den 23 oktober 1962")
    assert recitals(doc)[-1].text.startswith("Nödvändiga förbud")
    assert kinds(doc).count("citation") == 4


def test_legacy_english_numbered_recitals():
    # 31995L0046 (EN) has no framing line: each recital names itself ("Whereas
    # …") and carries a "(N)" marker run into that text
    doc = parse_fixture("avis-eng-numbered.html", "31995L0046", "eng")
    assert [b.num for b in recitals(doc)] == ["1", "2", "3", "4"]
    assert recitals(doc)[0].text.startswith("Whereas the objectives")
    assert kinds(doc).count("citation") == 3
    assert [b.num for b in doc.body if b.kind == "article"] == ["1"]


def test_legacy_english_unnumbered_recitals():
    doc = parse_fixture("avis-eng-unnumbered.html", "31980L0778", "eng")
    assert len(recitals(doc)) == 4
    assert all(b.num is None for b in recitals(doc))
    assert kinds(doc).count("citation") == 4


def test_legacy_wrapper_paragraph_is_not_a_block():
    # `<p><TXT_TE><p>…` -- html.parser makes the act's paragraphs children of
    # that opening <p>, so emitting the wrapper duplicated the whole document as
    # one block (78 kB of the 156 kB 31995L0046 artifact) and, since that block
    # contains the enacting formula, closed the preamble on line one
    doc = parse_fixture("avis-swe-numbered.html", "31995L0046", "swe")
    assert max(len(b.text) for b in doc.body) < 400
    assert sum(1 for b in doc.body
               if b.text.startswith("EUROPAPARLAMENTET OCH EUROPEISKA")) == 1


def test_legacy_recital_number_is_trusted_only_in_sequence():
    # a recital that merely opens with a number keeps the recital kind but gets
    # no num -- an out-of-sequence marker is a year, a stray footnote or a
    # sub-list, never this recital's number
    html = """<body>
      <p>med beaktande av följande:</p>
      <p>1) Den första punkten gäller.</p>
      <p>1993 antog rådet direktiv 93/12/EEG om detta.</p>
      <p>7) En punkt vars nummer inte följer på det förra.</p>
      <p>2) Den andra punkten gäller.</p>
      <p>HÄRIGENOM FÖRESKRIVS FÖLJANDE.</p>
      <p>Artikel 1</p>
    </body>"""
    doc = parse_html(html, "31995L0046", "swe")
    assert [b.num for b in recitals(doc)] == ["1", None, None, "2"]
    assert recitals(doc)[1].text.startswith("1993 antog")   # text kept whole
    assert recitals(doc)[3].text == "Den andra punkten gäller."


def test_swedish_enacting_formula_is_the_closing_one():
    # a Swedish act *opens* with "… HAR ANTAGIT DENNA FÖRORDNING" (Formex's
    # PREAMBLE.INIT) and closes with "HÄRIGENOM FÖRESKRIVS FÖLJANDE."; keying
    # the preamble's end on the opener ended it at the first line, so every
    # visa and recital of every non-Formex Swedish act became body text
    swe, eng = L.vocab("swe"), L.vocab("eng")
    assert not swe.enacting.search("EUROPEISKA UNIONENS RÅD HAR ANTAGIT DENNA FÖRORDNING")
    assert swe.enacting.search("HÄRIGENOM FÖRESKRIVS FÖLJANDE.")
    assert swe.enacting.search("HÄRIGENOM BESLUTAS FÖLJANDE.")
    assert swe.enacting.search("HÄRMED FÖRESKRIVS FÖLJANDE.")
    assert eng.enacting.search("HAS ADOPTED THIS DIRECTIVE:")
    assert swe.recital_intro.search("i enlighet med artikel 189b (3), "
                                    "och med beaktande av följande:")
    assert swe.recital_intro.search("av följande skäl:")
    assert not swe.recital_intro.search("med beaktande av kommissionens förslag,")
    assert eng.recital_intro.search("Whereas:")


def test_case_law_html_has_no_preamble_to_look_for():
    # ~8% of the corpus is case law served as HTML. A case has no preamble, but
    # it quotes acts: the phrases the act parser keys on ("av följande skäl:",
    # "Whereas", "HÄRIGENOM FÖRESKRIVS") all turn up inside the reasoning, and
    # reading them as structure turned a judgment's whole text into recitals
    html = """<body>
      <p>DOMSTOLENS DOM den 6 oktober 2015</p>
      <p>I artikel 25 i direktiv 95/46 föreskrivs följande:</p>
      <p>Direktivet antogs av följande skäl:</p>
      <p>Den hänskjutande domstolen har därför beslutat att vilandeförklara målet.</p>
      <p>HÄRIGENOM FÖRESKRIVS FÖLJANDE.</p>
      <p>Mot denna bakgrund beslutar domstolen följande.</p>
    </body>"""
    doc = parse_html(html, "62014CJ0362", "swe")
    assert kinds(doc) == ["paragraph"] * 6
    # …while the same text under an act's CELEX is read as an act
    assert "recital" in kinds(parse_html(html, "31995L0046", "swe"))


def test_modern_table_recitals_survive_the_swedish_preamble_fix():
    # the 2-column-table path is untouched: with the preamble now open across
    # the visas, a Swedish recital table is still a recital table (it used to
    # fall through to `point`, since the preamble had already been closed)
    html = """<body>
      <p class="normal">EUROPEISKA UNIONENS RÅD HAR ANTAGIT DENNA FÖRORDNING</p>
      <p class="normal">med beaktande av fördraget,</p>
      <p class="normal">av följande skäl:</p>
      <table><tr><td>(1)</td><td>Det första skälet.</td></tr>
             <tr><td>(2)</td><td>Det andra skälet.</td></tr></table>
      <p class="normal">HÄRIGENOM FÖRESKRIVS FÖLJANDE.</p>
      <p class="ti-art">Artikel 1</p>
      <table><tr><td>a)</td><td>första punkten</td></tr></table>
    </body>"""
    doc = parse_html(html, "32006R1563", "swe")
    assert kinds(doc) == ["preamble", "citation", "preamble", "recital", "recital",
                          "preamble", "article", "point"]
    assert [b.num for b in recitals(doc)] == ["1", "2"]


def test_vocab_is_localized():
    eng, swe = L.vocab("eng"), L.vocab("swe")
    assert eng.article.match("Article 5") and not eng.article.match("Artikel 5")
    assert swe.article.match("Artikel 5") and not swe.article.match("Article 5")
    assert eng.heading.match("CHAPTER 2") and swe.heading.match("KAPITEL 2")
    assert not eng.heading.match("KAPITEL 2")
    assert L.vocab("xx").article.pattern == eng.article.pattern   # fallback = eng


def test_paragraph_wrapping_a_table_is_not_emitted_twice():
    # a judgment's `<P class="C06Alinea">` is a container for a <table>, not a
    # paragraph. Emitting it *and* the table repeats the operative part: the
    # domslut once as flattened paragraph text and again as row blocks. The
    # wrapper skip has to cover block-level content generally, not only the
    # legacy `TXT_TE` marker (62011TJ0366, ~187 documents of this shape).
    doc = parse_fixture("judgment-p-wraps-table.html", "62011TJ0366", "swe")
    texts = [b.text if isinstance(b.text, str) else "" for b in doc.body]
    assert not [t for t in set(texts) if t and texts.count(t) > 1], texts
    # the operative part survives exactly once, through the table's own emission
    # (the numbered domslut is a 2-column marker table, so its rows become
    # `point` blocks; the unnumbered "Saken" table is a data table -> `row`)
    assert sum("ogiltigförklaras" in t for t in texts) == 1
    assert [b.kind for b in doc.body] == [
        "paragraph", "row", "paragraph", "point", "point"]


def test_amending_prose_is_not_an_article_heading():
    # an amending act's body opens sentences with an article reference
    # ("Artikel 9.2 skall ersättas med följande:", "Artikel 8 skall utgå.").
    # The class-less legacy path used to take those for headings, minting a
    # phantom article numbered after the *amended* act -- which then stole the
    # body of the real article it interrupted, leaving that one empty
    # (31969L0060 emitted articles 1,2,3,4,5,9,6,10,7,15,8, with 5/6/7 empty).
    html = """<body>
      <p>RÅDET HAR UTFÄRDAT DETTA DIREKTIV</p>
      <p>Artikel 5</p>
      <p>Artikel 9.2 skall ersättas med följande:</p>
      <p>"2. Officiellt plomberade förpackningar får inte omplomberas."</p>
      <p>Artikel 6</p>
      <p>Artikel 10.1. b skall ersättas med följande:</p>
      <p>Artikel 7</p>
      <p>Artikel 8 skall utgå.</p>
    </body>"""
    doc = parse_html(html, "31969L0060", "swe")
    assert [b.num for b in doc.body if b.kind == "article"] == ["5", "6", "7"]
    assert kinds(doc) == ["preamble",
                          "article", "paragraph", "paragraph",
                          "article", "paragraph",
                          "article", "paragraph"]


def test_article_heading_shape_across_languages():
    for voc, line in ((L.vocab("swe"), "Artikel 5"),
                      (L.vocab("swe"), "Artikel 5a"),
                      (L.vocab("swe"), "Artikel 1 Räckvidd"),
                      (L.vocab("swe"), "Artikel 6ter"),
                      (L.vocab("swe"), "Artikel 6sexies"),
                      (L.vocab("swe"), 'Artikel 5 "Definitioner"'),
                      (L.vocab("eng"), "Article 1 – Objective"),
                      (L.vocab("eng"), "ARTICLE 12")):
        assert voc.article_heading.match(line), line
    for voc, line in ((L.vocab("swe"), "Artikel 9.2 skall ersättas med följande:"),
                      (L.vocab("swe"), "Artikel 10.1. b skall ersättas"),
                      (L.vocab("swe"), "Artikel 8 skall utgå."),
                      (L.vocab("swe"), "Artikel 4 och 5 skall utgå"),
                      (L.vocab("swe"), "Artikel 3 i direktiv 64/54/EEG ändras"),
                      (L.vocab("eng"), "Article 103 the dependent child allowance")):
        assert not voc.article_heading.match(line), line
        # `article` still matches -- it is the looser table-marker test
        assert voc.article.match(line), line


def test_latin_ordinal_article_keeps_its_whole_suffix():
    # inserted articles carry Latin ordinals, not just single letters. A
    # one-letter suffix truncated "Artikel 6ter" to num "6t" -- an anchor
    # pointing at no article at all (62006TJ0215 quotes the Paris Convention).
    assert L.article_num("Artikel 6ter") == "6ter"
    assert L.article_num("Article 6sexies") == "6sexies"
    assert L.article_num("Artikel 5a") == "5a"
    assert L.article_num("Artikel 1 Räckvidd") == "1"


def test_multilingual_annex_strip_becomes_a_heading():
    # the pre-2000 OJ printed the annex heading once per language edition on one
    # line, so `voc.heading` -- anchored on this document's language -- never saw
    # its own word at the front. The annex stayed body text and the act's last
    # article swallowed it (31996L0054's article 4: 4 762 paragraphs).
    strip = ("ANEXO I - BILAG I - ANHANG I - ÐÁÑÁÑÔÇÌÁ É - ANNEX I - ANNEXE I"
             " - ALLEGATO I - BIJLAGE I - ANEXO I - LIITE I - BILAGA I")
    html = """<body>
      <p>HÄRIGENOM FÖRESKRIVS FÖLJANDE.</p>
      <p>Artikel 1</p>
      <p>Denna förordning skall ändras.</p>
      <p>%s</p>
      <p>Tabellrad som hör till bilagan.</p>
    </body>""" % strip
    doc = parse_html(html, "31996L0054", "swe")
    assert kinds(doc) == ["preamble", "article", "paragraph", "heading", "paragraph"]
    # the reading language's own segment names it, not the whole strip
    assert [b.text for b in doc.body if b.kind == "heading"] == ["BILAGA I"]
    assert parse_html(html, "31996L0054", "eng").body[3].text == "ANNEX I"


def test_annex_strip_variants_and_non_matches():
    swe = L.vocab("swe").annex_words
    # separators lost, the words run together (31986L0465) -- no way back to one
    # language's segment, so the run stands as its own heading
    run = "ANEXOBILAGANHANGANNEXANNEXEALLEGATOBIJLAGEANEXO"
    assert L.annex_strip(run, swe) == run
    # printed before Swedish was an OJ language: no BILAGA segment to pick
    older = "ANNEXE - ANNEX - ANHANG - BIJLAGE - ALLEGATO"
    assert L.annex_strip(older, swe) == older
    # prose that merely mentions annexes, and an ordinary heading, are not strips
    assert L.annex_strip("Före 2000 skall bilaga II del B ändras", swe) is None
    assert L.annex_strip("BILAGA I", swe) is None
    assert L.annex_strip("FÖRSTA - ANDRA - TREDJE", swe) is None


def test_signature_closes_the_last_article():
    # the class-less legacy HTML marks no `signatory`, so the closing formula
    # stayed a paragraph -- and `structure.nest` closes an open article on a
    # `signature` block and nothing else, so the last article went on swallowing
    # the signature, the footnotes and every annex (31986L0465: 6 143 paragraphs)
    html = """<body>
      <p>HÄRIGENOM FÖRESKRIVS FÖLJANDE.</p>
      <p>Artikel 3</p>
      <p>Detta direktiv riktar sig till medlemsstaterna.</p>
      <p>Utfärdat i Bryssel den 14 juli 1986.</p>
      <p>På rådets vägnar</p>
    </body>"""
    assert kinds(parse_html(html, "31986L0465", "swe")) == [
        "preamble", "article", "paragraph", "signature", "paragraph"]
    eng = """<body>
      <p>HAS ADOPTED THIS DIRECTIVE:</p>
      <p>Article 3</p>
      <p>Done at Brussels, 14 July 1986.</p>
    </body>"""
    assert kinds(parse_html(eng, "31986L0465", "eng")) == [
        "preamble", "article", "signature"]


def test_a_judgment_has_no_inferred_articles():
    # a judgment reproduces the contested act's operative articles; taking a
    # quoted "Artikel 4" for a heading left it swallowing the rest of the
    # judgment (61989TJ0068's "article 4": 280 353 characters)
    html = """<body>
      <p>Artikel 2</p>
      <p>Företagen skall omedelbart upphöra med överträdelsen.</p>
      <p>Artikel 4</p>
      <p>Följande böter skall åläggas de företag som beslutet riktar sig till.</p>
    </body>"""
    doc = parse_html(html, "61989TJ0068", "swe")
    assert "article" not in kinds(doc)
    assert kinds(doc) == ["paragraph"] * 4
