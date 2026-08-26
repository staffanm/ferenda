"""Tests for the nuvarande/föreslagen lydelse two-column reconstruction.

Geometry taken from prop 2013/14:116 p. 5: left column at x 172-390, right at
402-626, boundary from the header's second run, centered kap/§ markers between
the columns, footnotes small-font at the page bottom.
"""

from ferenda.forarbete.lydelse import split_page
from ferenda.lib.pdftext import Line, Run


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
    # closing prose still ends the region -- nothing two-column follows it
    lines = [
        HEADER,
        _line(352, (172, 388, "vänster cell"), (402, 618, "höger cell")),
        _line(420, (172, 619, "Denna lag träder i kraft den 1 juli 2014 och gäller.")),
    ]
    segs = split_page(lines)
    assert [s[0] for s in segs] == ["tabell", "lines"]
    assert segs[1][1][0].text.startswith("Denna lag träder i kraft")


def test_a_full_width_lead_in_does_not_end_the_region():
    """Prop. 2025/26:77 p. 9: a statutory paragraph sets its lead-in sentence
    across both columns and only then diverges into two enumerations. The
    lead-in starts at the left margin and crosses the boundary, exactly like the
    ikraftträdande sentence that closes a table -- so reading it as the end of
    the region discarded the pending header and reflowed the whole remaining
    enumeration through the plain path, where the columns interleave line by
    line ("2. Europaparlamentets och rådets 1. Europaparlamentets och").

    The lead-in comes back as its own ("lines", …) chunk -- one chunk, so its
    lines still reflow into one paragraph -- and the columns resume under it."""
    lines = [
        HEADER,
        _line(318, (380, 423, "1 §")),                      # centered marker
        _line(340, (185, 626, "Bestämmelserna i 8 kap. 3–8 §§ varumärkeslagen")),
        _line(357, (172, 626, "(2010:1877) ska tillämpas vid intrång som")),
        _line(374, (172, 232, "följer av")),                # the lead-in's tail
        _line(391, (185, 390, "1. rådets förordning (EU)"),
              (414, 620, "1. rådets förordning (EU)")),
        _line(408, (172, 388, "2019/787 av den 17 april"),
              (402, 618, "2024/1143 av den 11 april")),
    ]
    segs = split_page(lines)
    assert [s[0] for s in segs] == ["lines", "lines", "tabell"]
    assert segs[0][1] == [lines[1]]                         # the "1 §" marker
    assert [l.text for l in segs[1][1]] == [
        "Bestämmelserna i 8 kap. 3–8 §§ varumärkeslagen",
        "(2010:1877) ska tillämpas vid intrång som",
        "följer av"]
    _kind, header, rows = segs[2]
    assert header is HEADER                    # the header survived the lead-in
    assert rows == [
        ("1. rådets förordning (EU) 2019/787 av den 17 april",
         "1. rådets förordning (EU) 2024/1143 av den 11 april")]


def test_a_short_sub_item_stays_with_the_prose_around_it():
    """Prop. 2025/26:207 p. 15 sets "a) anställning," between two full-width
    lines of one enumeration. It is short enough to fall wholly left of the
    boundary, so read as a left cell it became a one-cell table -- which also
    took the region's header off the real table below it. What follows decides:
    more full-width prose, or the columns resuming."""
    lines = [
        HEADER,
        _line(340, (185, 626, "Tiden ska minskas med hänsyn till")),
        _line(357, (185, 300, "a) anställning,")),          # left of the boundary
        _line(374, (185, 626, "b) arbetsmarknadspolitiskt program, och")),
        _line(391, (185, 390, "Tiden ska dessutom minskas"),
              (414, 620, "Tiden ska dessutom minskas")),
    ]
    segs = split_page(lines)
    assert [s[0] for s in segs] == ["lines", "tabell"]
    assert [l.text for l in segs[0][1]] == [
        "Tiden ska minskas med hänsyn till", "a) anställning,",
        "b) arbetsmarknadspolitiskt program, och"]
    assert segs[1][1] is HEADER
    assert segs[1][2] == [("Tiden ska dessutom minskas",
                           "Tiden ska dessutom minskas")]


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
