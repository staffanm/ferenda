"""General table detection for förarbete PDFs (rewrite-parity finding 04).

`lydelse.py` reconstructs the nuvarande/föreslagen two-column comparison
tables; everything else tabular -- budget tables, bilaga listings, the
multi-column enumerations scattered through props and SOUs -- used to flow
line-by-line through the prose reflow and flatten into stycken, losing rows
and columns. This module detects those *generic* tabular regions from the
same Line/run geometry pdftohtml gives us, and `merge_continued` joins a
table that continues across a page break (its repeated header dropped).

Deliberately conservative: a region must hold at least MIN_ROWS consecutive
multi-cell lines agreeing on at least two column start positions before it is
believed. Prose runs abut (their gaps are kerning, far below CELL_GAP), TOC
dot-leader lines and page-margin markers are excluded, so a false table over
running text costs more evidence than a page ever offers.
"""

import re

CELL_GAP = 40   # a horizontal gap this wide between runs splits a line into cells
COL_TOL = 20    # cell starts within this many x-units are the same column
MIN_ROWS = 3    # consecutive multi-cell lines needed to believe a table

# TOC dotted leaders -- old letterpress TOCs space the dots (". . . .")
RE_DOTS = re.compile(r"(?:\.\s?){4,}")
# a TOC row: a *dotted* section number opens the first cell ("1.3.1 Kunskaps-
# yrkenas ...") -- a bare leading number ("22 år") is a data row, not a TOC
RE_TOC_SECTION = re.compile(r"\d+\.\d+(?:\.\d+)*(?:\s|$)|Bilaga\b|Kapitel\b")
RE_TOC_PAGENO = re.compile(r"\d{1,4}")
# page-margin marker runs (running header / bilaga stamp), never table cells
RE_MARGIN = re.compile(
    r"^(?:Prop\. \d{4}(?:/\d{2,4})?:\d+|Bilaga \d+|SOU \d{4}:\d+|Ds \d{4}:\d+)$")


def line_cells(line):
    """One Line -> [(left, right, text)] cells: runs gap-split at CELL_GAP,
    margin-marker runs dropped. A lone page number is no cell line at all."""
    if line.text.strip().isdigit():
        return []
    out = []
    for r in sorted(line.runs, key=lambda r: r.left):
        if RE_MARGIN.match(r.text.strip()):
            continue
        if out and r.left - out[-1][1] <= CELL_GAP:
            out[-1] = (out[-1][0], r.right, "%s %s" % (out[-1][2], r.text))
        else:
            out.append((r.left, r.right, r.text))
    return out


def _columns(cell_lines):
    """The region's column start positions: cell start xs clustered within
    COL_TOL, keeping clusters that recur on at least two lines."""
    xs = sorted(c[0] for cs in cell_lines for c in cs)
    clusters = []
    for x in xs:
        if clusters and x - clusters[-1][-1] <= COL_TOL:
            clusters[-1].append(x)
        else:
            clusters.append([x])
    return [min(c) for c in clusters if len(c) >= 2]


def _col_of(cell, cols):
    return min(range(len(cols)), key=lambda i: abs(cols[i] - cell[0]))


# a data cell: a number, amount, percentage, year span ("25 000", "8,4",
# "12,9 14,3", "1985", "+", "-")
RE_NUMERIC_CELL = re.compile(r"[\d+–—-][\d\s.,:%+–—/-]*")
# a nuvarande/föreslagen lydelse column header: that layout belongs to
# lydelse.py's paired-paragraph reconstruction -- where lydelse declined the
# region, two-column statute text must stay prose, never become cell salad
RE_LYDELSE_HEADER = re.compile(r"\blydelse\b", re.IGNORECASE)


def _consistent(cell_lines, cols):
    """Whether the region genuinely reads as a *data table* over `cols`:
    nearly all cells start at a column (>= 80%), at least MIN_ROWS lines carry
    cells in two or more distinct columns, and the non-leading columns hold
    mostly numeric cells (amounts, years, rates). Old typeset/OCR-era PDFs
    fragment prose into runs with wide kerning gaps -- their fragments fail
    the numeric test and stay prose instead of shredding into cell salad.
    (Prose-celled tables -- multi-column listings of text -- are deliberately
    out of this first cut; the legally significant lydelse/jämförelse tables
    have their own reconstructions.)"""
    if any(RE_LYDELSE_HEADER.search(c[2]) for c in (cell_lines[0] or [])):
        return False
    # a spaced-dot-era table of contents: section-numbered rows trailing off
    # in the page number -- numeric enough to fool the data gate below. The
    # ratio is over the multi-cell lines: OCR reflow interleaves the entries
    # with single-cell wrapped fragments that must not dilute it.
    multi = [cells for cells in cell_lines if len(cells) >= 2]
    toc_rows = sum(1 for cells in multi
                   if RE_TOC_SECTION.match(cells[0][2].strip())
                   and cells[-1][2].split()[-1].isdigit())
    if multi and toc_rows >= 0.5 * len(multi):
        return False
    total = aligned = rows2 = data = tail = 0
    for row, cells in enumerate(cell_lines):
        hit = set()
        for c in cells:
            total += 1
            i = _col_of(c, cols)
            if abs(cols[i] - c[0]) <= COL_TOL:
                aligned += 1
                hit.add(i)
            # the numeric-evidence tally: non-leading columns of the body
            # rows (the header row's labels and a wrapped continuation
            # line's overflow text are not data cells)
            if i > 0 and row > 0 and len(cells) >= 2:
                tail += 1
                if RE_NUMERIC_CELL.fullmatch(c[2].strip()):
                    data += 1
        if len(hit) >= 2:
            rows2 += 1
    return (total and aligned >= 0.8 * total and rows2 >= MIN_ROWS
            and data >= 2 and data >= 0.6 * tail)


def _rows(cell_lines, cols):
    """Cell lines -> row tuples over the region's columns. A line whose first
    column is empty is a wrapped cell: its cells append to the previous row."""
    rows = []
    for cells in cell_lines:
        placed = [""] * len(cols)
        for c in cells:
            i = _col_of(c, cols)
            placed[i] = ("%s %s" % (placed[i], c[2])).strip()
        if rows and not placed[0]:
            rows[-1] = tuple(
                ("%s %s" % (a, b)).strip() for a, b in zip(rows[-1], placed,
                                                           strict=True))
        else:
            rows.append(tuple(placed))
    return rows


def split_generic(lines):
    """A page segment's [Line] -> [("lines", [Line], None) |
    ("tabell", th, rows)] in document order. `th` is True when the region's
    first line is bold (a column-header row); rows are tuples over the
    detected columns."""
    out, plain = [], []
    i = 0
    while i < len(lines):
        # a candidate region: consecutive lines that split into >= 2 cells.
        # An *indented* single-cell line between them is a wrapped cell (its
        # column assignment happens in _rows) -- but only with another
        # multi-cell line still ahead, so trailing indented prose is never
        # absorbed.
        j = i
        left = None
        while j < len(lines) and not RE_DOTS.search(lines[j].text):
            cells = line_cells(lines[j])
            if len(cells) >= 2:
                left = cells[0][0] if left is None else min(left, cells[0][0])
                j += 1
            elif (len(cells) == 1 and left is not None
                  and cells[0][0] > left + CELL_GAP
                  and j + 1 < len(lines)
                  and len(line_cells(lines[j + 1])) >= 2):
                j += 1                              # wrapped cell mid-table
            else:
                break
        region = lines[i:j]
        cell_lines = [line_cells(l) for l in region]
        cols = _columns(cell_lines) if len(region) >= MIN_ROWS else []
        if len(cols) >= 2 and _consistent(cell_lines, cols):
            if plain:
                out.append(("lines", plain, None))
                plain = []
            out.append(("tabell", region[0].bold, _rows(cell_lines, cols)))
            i = j
        else:
            plain.append(lines[i])
            i += 1
    if plain:
        out.append(("lines", plain, None))
    return out


def merge_continued(blocks):
    """Join a table continuing across a page break: two adjacent `tabell`
    blocks on consecutive pages with the same column count are one table --
    the continuation's repeated header row (identical to the table's first
    row) is dropped. Mutates nothing; returns a new block list."""
    out = []
    for b in blocks:
        prev = out[-1] if out else None
        if (b.kind == "tabell" and prev is not None and prev.kind == "tabell"
                and b.rows and prev.rows
                and b.page is not None and prev.page is not None
                and 0 <= b.page - prev.page <= 1
                and len(b.rows[0]) == len(prev.rows[0])):
            rows = list(b.rows)
            if rows and rows[0] == prev.rows[0]:
                rows = rows[1:]                     # repeated header
            prev.rows = list(prev.rows) + rows
            continue
        out.append(b)
    return out
