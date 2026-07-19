"""Tests for the non-Formex EU parsers: the OJ HTML/XHTML parser and the shared
localized vocabulary (eng + swe), including the recital-table heuristic and the
old-flavour text-structure fallback."""

from accommodanda.eurlex import lang as L
from accommodanda.eurlex.parse_html import parse_html


def kinds(doc):
    return [b.kind for b in doc.body]


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


def test_vocab_is_localized():
    eng, swe = L.vocab("eng"), L.vocab("swe")
    assert eng.article.match("Article 5") and not eng.article.match("Artikel 5")
    assert swe.article.match("Artikel 5") and not swe.article.match("Article 5")
    assert eng.heading.match("CHAPTER 2") and swe.heading.match("KAPITEL 2")
    assert not eng.heading.match("KAPITEL 2")
    assert L.vocab("xx").article.pattern == eng.article.pattern   # fallback = eng
