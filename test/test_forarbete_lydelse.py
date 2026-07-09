"""Tests for the nuvarande/föreslagen lydelse two-column reconstruction.

Geometry taken from prop 2013/14:116 p. 5: left column at x 172-390, right at
402-626, boundary from the header's second run, centered kap/§ markers between
the columns, footnotes small-font at the page bottom.
"""

from accommodanda.forarbete.lydelse import split_page
from accommodanda.lib.pdftext import Line, Run


def _line(top, *runs, size=15, italic=False):
    rs = [Run(left, right, text, False, italic, size)
          for left, right, text in runs]
    return Line(" ".join(r.text for r in rs), top, False, False, italic,
                size, rs)


HEADER = _line(283, (172, 288, "Nuvarande lydelse"),
               (402, 519, "Föreslagen lydelse"), italic=True)


def test_no_header_no_table():
    lines = [_line(100, (172, 600, "Vanlig brödtext över hela sidbredden."))]
    assert split_page(lines) == [("lines", lines)]


def test_columns_split_and_rows_align():
    lines = [
        _line(100, (172, 620, "Härigenom föreskrivs att 2 kap. 28 § ska ha följande lydelse.")),
        HEADER,
        _line(421, (185, 391, "På ersättning till personer som"),
              (414, 620, "På ersättning till personer som")),
        _line(438, (172, 383, "vid årets ingång inte har fyllt 26"),
              (402, 612, "vid årets ingång inte har fyllt 23")),
        # new text on the right with no counterpart on the left
        _line(576, (414, 619, "På ersättning till personer som")),
        _line(593, (402, 619, "vid årets ingång har fyllt 23 men")),
    ]
    segs = split_page(lines)
    assert segs[0] == ("lines", [lines[0]])
    kind, header, rows = segs[1]
    assert kind == "tabell" and header is HEADER
    assert rows == [
        ("På ersättning till personer som vid årets ingång inte har fyllt 26",
         "På ersättning till personer som vid årets ingång inte har fyllt 23"),
        ("", "På ersättning till personer som vid årets ingång har fyllt 23 men"),
    ]


def test_centered_marker_splits_table_and_reenters_normal_path():
    marker = _line(318, (380, 423, "2 kap."))
    lines = [
        HEADER,
        _line(352, (172, 388, "Avgifter för unga"), (402, 618, "Avgifter för unga")),
        marker,
        _line(421, (172, 388, "vänster cell"), (402, 618, "höger cell")),
    ]
    segs = split_page(lines)
    kinds = [s[0] for s in segs]
    assert kinds == ["tabell", "lines", "tabell"]
    assert segs[1] == ("lines", [marker])
    # only the region's first chunk carries the header
    assert segs[0][1] is HEADER and segs[2][1] is None


def test_footnotes_end_the_region_and_superscripts_drop():
    lines = [
        HEADER,
        _line(421, (172, 388, "vänster cell"), (402, 618, "höger cell")),
        _line(367, (461, 466, "1"), size=10),          # stray superscript marker
        _line(897, (172, 313, "1 Senaste lydelse 2008:1266."), size=12),
        _line(911, (172, 313, "2 Senaste lydelse 2008:1266."), size=12),
    ]
    # body size must dominate: pad with body-sized prose before the header
    body = [_line(50 + 17 * i, (172, 600, "brödtext nummer %d i normal storlek" % i))
            for i in range(6)]
    segs = split_page(body + lines)
    kinds = [s[0] for s in segs]
    assert kinds == ["lines", "tabell", "lines"]
    _kind, _header, rows = segs[1]
    assert rows == [("vänster cell", "höger cell")]    # no superscript noise
    assert [l.text for l in segs[2][1]] == [
        "1 Senaste lydelse 2008:1266.", "2 Senaste lydelse 2008:1266."]


def test_full_width_prose_ends_the_region():
    lines = [
        HEADER,
        _line(352, (172, 388, "vänster cell"), (402, 618, "höger cell")),
        _line(420, (172, 619, "Denna lag träder i kraft den 1 juli 2014 och gäller.")),
    ]
    segs = split_page(lines)
    assert [s[0] for s in segs] == ["tabell", "lines"]
    assert segs[1][1][0].text.startswith("Denna lag träder i kraft")


def test_indent_starts_new_cell_paragraph():
    lines = [
        HEADER,
        _line(421, (185, 391, "Första stycket börjar indraget"),
              (414, 620, "Första stycket börjar indraget")),
        _line(438, (172, 383, "och fortsätter vid marginalen."),
              (402, 612, "och fortsätter vid marginalen.")),
        _line(455, (185, 391, "Andra stycket börjar indraget."),
              (414, 620, "Andra stycket börjar indraget.")),
    ]
    _kind, _header, rows = split_page(lines)[0]
    assert rows == [
        ("Första stycket börjar indraget och fortsätter vid marginalen.",
         "Första stycket börjar indraget och fortsätter vid marginalen."),
        ("Andra stycket börjar indraget.", "Andra stycket börjar indraget."),
    ]
