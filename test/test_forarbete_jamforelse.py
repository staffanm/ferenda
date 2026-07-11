"""Tests for the jämförelsetabell extractor (forarbete.jamforelse): the layout
logic that turns a bilaga's two-column pages back into (left, right) rows,
against synthetic Line/Run geometry modelled on the two real table families
(OSL prop 2008/09:150 bilaga 7/8, socialtjänstlagen prop 2024/25:89 bilaga
16/17)."""

from accommodanda.forarbete import jamforelse as J
from accommodanda.lib.pdftext import Line, Run


def _line(top, *runs):
    return Line(text=" ".join(r[2] for r in runs), top=top, bold=False,
                lead_bold=False, italic=False,
                runs=[Run(left=l, right=r, text=t, bold=False, italic=False)
                      for l, r, t in runs])


def _page(*lines):
    return [_line(60 + 20 * i, *rs) for i, rs in enumerate(lines)]


def test_line_cells_gap_split_and_margin_filter():
    line = _line(60, (77, 210, "1 kap. 1 § första stycket"),
                 (309, 477, "1 kap. 1 § tredje stycket delvis"),
                 (533, 649, "Prop. 2008/09:150"))
    assert J._line_cells(line) == [
        (77, 210, "1 kap. 1 § första stycket"),
        (309, 477, "1 kap. 1 § tredje stycket delvis")]
    # fragment runs inside one cell (the modern PDFs split "1|kap. 1|§") merge
    frag = _line(60, (183, 189, "1"), (192, 308, "kap. 1 § första stycket"),
                 (428, 434, "2"), (438, 469, "kap. 1"), (472, 482, "§"))
    assert [c[2] for c in J._line_cells(frag)] == [
        "1 kap. 1 § första stycket", "2 kap. 1 §"]
    # a lone page-number line is dropped whole
    assert J._line_cells(_line(900, (300, 320, "515"))) == []


def test_line_cells_never_merges_across_column_start():
    # OSL bilaga 8's header sits 17pt from the boundary -- tighter than
    # CELL_GAP, so without the column hint the two cells would merge
    header = _line(126, (77, 286, "Sekretesslag (1980:100)"),
                   (303, 462, "Offentlighets- och"))
    assert len(J._line_cells(header)) == 1
    assert [c[2] for c in J._line_cells(header, cols=[77, 303])] == [
        "Sekretesslag (1980:100)", "Offentlighets- och"]


def test_columns_ignores_one_off_starts():
    cells = ([[(77, 210, "x"), (309, 400, "y")]] * 4
             + [[(150, 200, "wrapped fragment")]])
    assert J._columns(cells) == [77, 309]


def test_header_rejects_toc_and_page_number_lines():
    assert J._header([(77, 236, "Offentlighets- och"),
                      (309, 518, "Sekretesslag (1980:100)")])
    # prop-local shorthand headers (NML prop 2022/23:46) qualify too
    assert J._header([(77, 180, "Bestämmelse i NML"),
                      (298, 394, "Bestämmelse i ML")])
    assert not J._header([(77, 400, "Bilaga 7 Jämförelsetabell........."),
                          (500, 518, "513")])
    assert not J._header([(77, 236, "1 kap. 1 §"), (309, 400, "2 kap. 2 §")])


def test_tables_rows_fold_wrapped_cells(monkeypatch):
    title = _page(
        [(533, 649, "Prop. 2008/09:150")],
        [(77, 514, "Jämförelsetabell: Offentlighets- och sek-")],
        [(533, 587, "Bilaga 7")],
        [(77, 248, "retesslag (1980:100)")],
        [(77, 236, "Offentlighets- och"), (309, 518, "Sekretesslag (1980:100)")],
        [(77, 189, "sekretesslag")],                       # wrapped header tail
        [(77, 210, "1 kap. 1 § första stycket"),
         (309, 477, "1 kap. 1 § tredje stycket delvis")],
        [(77, 209, "1 kap. 1 § andra stycket"),
         (309, 494, "1 kap. 1 § första, andra och fjärde")],
        [(309, 360, "styckena")],                          # wrapped right cell
        [(77, 134, "1 kap. 2 §"), (309, 317, "-")])
    cont = _page(
        [(85, 139, "6 kap. 1 §"), (306, 431, "15 kap. 4 § andra stycket"),
         (533, 649, "Prop. 2008/09:150")],
        [(533, 595, "Bilaga 7")],
        [(85, 139, "6 kap. 2 §"), (306, 431, "15 kap. 6 § första stycket")],
        [(85, 139, "6 kap. 3 §"), (306, 431, "15 kap. 6 § andra stycket")])
    other = _page([(533, 595, "Bilaga 9")], [(77, 400, "Lagrådets yttrande")])
    monkeypatch.setattr(J, "pdf_pages",
                        lambda p: [(513, title), (514, cont), (540, other)])
    tabs = J.tables("dummy.pdf")
    assert len(tabs) == 1 and tabs[0]["bilaga"] == 7
    assert tabs[0]["header"] == ("Offentlighets- och", "Sekretesslag (1980:100)")
    assert "retesslag (1980:100)" in tabs[0]["text"]     # title prose collected
    assert tabs[0]["rows"] == [
        ("1 kap. 1 § första stycket", "1 kap. 1 § tredje stycket delvis", 513),
        ("1 kap. 1 § andra stycket",
         "1 kap. 1 § första, andra och fjärde styckena", 513),
        ("1 kap. 2 §", "-", 513),
        ("6 kap. 1 §", "15 kap. 4 § andra stycket", 514),
        ("6 kap. 2 §", "15 kap. 6 § första stycket", 514),
        ("6 kap. 3 §", "15 kap. 6 § andra stycket", 514)]


def test_tables_markerless_body_chapter(monkeypatch):
    # PBL prop 2009/10:170: the table is a numbered body chapter -- no
    # "Bilaga N" margin marker; continuation pages open by repeating the
    # header pair, and every page repeats it as a plain line too
    title = _page(
        [(533, 649, "Prop. 2009/10:170")],
        [(77, 97, "29"), (145, 283, "Jämförelsetabell")],
        [(77, 517, "I tabellen nedan används förkortningarna ...")],
        [(77, 255, "Nya plan- och bygglagen"),
         (300, 516, "Plan- och bygglagen (1987:10)")],
        [(77, 140, "1 kap. 1 §"), (300, 363, "1 kap. 1 §")],
        [(77, 140, "1 kap. 2 §"), (300, 363, "1 kap. 5 §")],
        [(77, 140, "1 kap. 3 §"), (300, 363, "1 kap. 6 §")])
    cont = _page(
        [(55, 233, "Nya plan- och bygglagen"),
         (279, 495, "Plan- och bygglagen (1987:10)"),
         (512, 628, "Prop. 2009/10:170")],
        [(55, 129, "2 kap. 11 §"), (279, 298, "ny")],
        [(55, 122, "3 kap. 2 §"), (279, 470, "delvis ny samt 1 kap. 3 § första")],
        [(279, 425, "stycket andra meningen")])
    unrelated = _page([(77, 300, "30 Författningskommentar")],
                      [(77, 500, "Förslaget till ny plan- och bygglag ...")])
    monkeypatch.setattr(J, "pdf_pages",
                        lambda p: [(519, title), (520, cont), (521, unrelated)])
    tabs = J.tables("dummy.pdf")
    assert len(tabs) == 1 and tabs[0]["bilaga"] is None
    assert tabs[0]["header"] == ("Nya plan- och bygglagen",
                                 "Plan- och bygglagen (1987:10)")
    assert tabs[0]["rows"] == [
        ("1 kap. 1 §", "1 kap. 1 §", 519),
        ("1 kap. 2 §", "1 kap. 5 §", 519),
        ("1 kap. 3 §", "1 kap. 6 §", 519),
        ("2 kap. 11 §", "ny", 520),
        ("3 kap. 2 §", "delvis ny samt 1 kap. 3 § första stycket andra meningen",
         520)]


def test_tables_paragrafnyckel_late_header_and_headerless(monkeypatch):
    # NLTS prop 2021/22:61 style: "Paragrafnyckel" title, header on the title
    # page, repeated atop the continuation page
    b5_title = _page(
        [(55, 163, "Prop. 2021/22:61")],
        [(183, 432, "Paragrafnyckel NLTS LTS")],
        [(55, 109, "Bilaga 5")],
        [(183, 628, "Paragrafnyckeln anger för varje paragraf i lagen "
                     "(1994:1563) om tobaksskatt ...")],
        [(183, 290, "Bestämmelse i NLTS"), (364, 464, "Bestämmelse i LTS")],
        [(183, 237, "1 kap. 1 §"), (364, 371, "–")],
        [(183, 237, "1 kap. 2 §"), (364, 384, "1 §")],
        [(183, 237, "1 kap. 3 §"), (364, 384, "2 §")])
    b5_cont = _page(
        [(77, 184, "Bestämmelse i NLTS"), (257, 357, "Bestämmelse i LTS"),
         (533, 641, "Prop. 2021/22:61")],
        [(533, 587, "Bilaga 5")],
        [(77, 137, "2 kap. 20 §"), (257, 277, "5 §")])
    # elcertifikat prop 2010/11:155 style: the header prints as one merged
    # run -- unrecoverable, but the rows must still come back
    b8 = _page(
        [(527, 643, "Prop. 2010/11:155")],
        [(71, 209, "Jämförelsetabell")],
        [(527, 581, "Bilaga 8")],
        [(71, 488, "Förslag till lag om elcertifikat Lag (2003:113) "
                    "om elcertifikat")],
        [(71, 128, "1 kap. 1 §"), (297, 354, "1 kap. 1 §")],
        [(71, 128, "1 kap. 5 §"), (297, 307, "–")],
        [(71, 128, "1 kap. 6 §"), (297, 354, "1 kap. 2 §")])
    monkeypatch.setattr(J, "pdf_pages",
                        lambda p: [(746, b5_title), (747, b5_cont), (900, b8)])
    tabs = J.tables("dummy.pdf")
    assert [t["bilaga"] for t in tabs] == [5, 8]
    nyckel, headerless = tabs
    assert nyckel["header"] == ("Bestämmelse i NLTS", "Bestämmelse i LTS")
    assert "(1994:1563)" in nyckel["text"]
    # the repeated header on page 747 must not fold into a row
    assert nyckel["rows"] == [("1 kap. 1 §", "–", 746),
                              ("1 kap. 2 §", "1 §", 746),
                              ("1 kap. 3 §", "2 §", 746),
                              ("2 kap. 20 §", "5 §", 747)]
    assert headerless["header"] is None
    assert "(2003:113)" in headerless["text"]
    assert headerless["rows"] == [("1 kap. 1 §", "1 kap. 1 §", 900),
                                  ("1 kap. 5 §", "–", 900),
                                  ("1 kap. 6 §", "1 kap. 2 §", 900)]
