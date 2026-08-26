"""Reconstruct a förarbete's two-column *nuvarande/föreslagen lydelse* tables.

The lagtext chapter of an amending proposition sets the current wording and
the proposed wording side by side under an italic "Nuvarande lydelse /
Föreslagen lydelse" header. Text-order extraction interleaves the columns
line by line ("Denna lag gäller utöver person- Denna lag gäller utöver
brottsdata…"), mangling both. The per-run horizontal extents pdftext keeps on
every :class:`Line` let us undo that: the header's second run gives the
column boundary, each following line's runs sort into the left or right cell,
and the page-centered "2 kap." / "28 §" markers that apply to both columns
come back as ordinary marker lines.

`split_page` is the whole interface: a page's lines -> segments, each either
("lines", [Line]) for the normal reflow/classify path or
("tabell", header_line, rows) where rows pair the aligned cell paragraphs
[(nuvarande, föreslagen)] ('' for a cell with no counterpart -- entirely new
or deleted text). Full-width prose *inside* the region -- the paragraph a
statute sets across both columns before its enumeration diverges
("Bestämmelserna i 8 kap. 3-8 §§ … varumärkeslagen (2010:1877) ska tillämpas
vid intrång i den rätt till en beteckning som följer av") -- comes back as its
own ("lines", …) chunk and the columns continue after it. The region ends at
full-width prose with no two-column row below it, at a footnote-sized line, or
at the page end; a continuation page repeats the header, so detection is per
page."""

import re

from ..lib.pdftext import FOOTNOTE_DROP, PARA_GAP, dehyphenate, line_body_size

RE_LYDELSE = re.compile(r"\blydelse\b", re.IGNORECASE)

COL_TOL = 8           # cell runs may protrude this far past the boundary
CENTER_MARGIN = 60    # a crossing line starting this near the left margin is
                      # full-width prose, not a centered kap/§ marker
ROW_TOL = 10          # cell paragraphs starting within this many y-units of
                      # each other are the two sides of one row
INDENT = 8            # a first-line indent this deep starts a cell paragraph


def _header(line):
    """The column boundary x if `line` is a lydelse-table header ("Nuvarande
    lydelse" / "Föreslagen lydelse" as two runs), else None."""
    if (len(line.runs) == 2 and line.italic
            and all(RE_LYDELSE.search(r.text) for r in line.runs)):
        return line.runs[1].left - COL_TOL
    return None


def split_page(lines):
    """A page's [Line] -> [("lines", [Line]) | ("tabell", header, rows)]
    segments in document order (see module docstring)."""
    body = line_body_size(lines)
    margin = min((r.left for l in lines for r in l.runs), default=0)
    out = []
    plain = []          # lines destined for the normal reflow path
    i = 0
    while i < len(lines):
        split = _header(lines[i])
        if split is None:
            plain.append(lines[i])
            i += 1
            continue
        if plain:
            out.append(("lines", plain))
            plain = []
        segs, i = _table_region(lines, i + 1, lines[i], split, body, margin)
        out += segs
    if plain:
        out.append(("lines", plain))
    return out


def _table_region(lines, i, header, split, body, margin):
    """Consume the table region opening at `lines[i]` (the line after its
    header): cell lines sort by the boundary; a page-centered "2 kap."/"28 §"
    marker flushes the rows gathered so far and re-enters the normal path as
    its own line; so does a run of full-width prose lines, but only while a
    two-column row still follows below them (`_kinds_below`). Full-width
    prose with nothing two-column under it, or a footnote-sized line, ends the
    region (that line is left for the caller). Returns (segments, next_i);
    only the region's first table segment carries the header line (the PDF
    prints it once, not before every marker-separated chunk)."""
    segs, left, right = [], [], []
    pending_header = [header]
    full = []            # the run of full-width lines currently open, if any

    def flush():
        if left or right:
            segs.append(("tabell",
                         pending_header.pop() if pending_header else None,
                         _rows(left, right)))
            left.clear()
            right.clear()

    def close_full():
        # one chunk, not one per line: these are the lines of a single
        # paragraph, and reflow can only rejoin them inside one segment
        if full:
            segs.append(("lines", list(full)))
            full.clear()

    def small(size):
        return body and size and size <= body - FOOTNOTE_DROP

    while i < len(lines):
        l = lines[i]
        # footnote-sized runs never join a cell: a superscript marker ("1")
        # rides its own baseline as a tiny line -- dropped; the footnote text
        # itself ("1 Senaste lydelse …") ends the table (it opens the page's
        # footnote block, which the caller classifies as `fotnot` blocks)
        runs = [r for r in l.runs if not small(r.size)]
        if not runs:
            if len(l.text) > 3:
                break                       # footnote text: region over
            i += 1                          # stray superscript marker: drop
            continue
        lruns = [r for r in runs if r.right <= split]
        rruns = [r for r in runs if r.left >= split]
        if len(lruns) + len(rruns) == len(runs):
            # A line wholly left of the boundary under open full-width prose is
            # ambiguous: it is that paragraph's own short line ("följer av",
            # "a) anställning,") or the nuvarande column's first item, which is
            # equally free to be short. Two things answer it, and each catches
            # what the other misses. A cell opens indented, the way a statute
            # opens a stycke, so a line at the margin is the prose wrapping
            # ("följer av"). An indented one is read from what comes next: more
            # full-width prose, or the columns resuming.
            if full and not rruns and (
                    min(r.left for r in runs) <= margin + INDENT
                    or _prose_continues(lines, i + 1, split, margin, small)):
                full.append(l)
                i += 1
                continue
            close_full()
            left += [(l.top, r) for r in lruns]
            right += [(l.top, r) for r in rruns]
            i += 1
            continue
        # a run crossing the boundary. A centered "2 kap."/"28 §" marker is its
        # own full-width line (both columns' anchor) and the table continues
        # after it.
        if min(r.left for r in runs) > margin + CENTER_MARGIN:
            close_full()
            flush()
            segs.append(("lines", [l]))
            i += 1
            continue
        # full-width prose. Whether it opens the statute's paragraph or closes
        # the table is decided by what is below, not by the line itself: the
        # region goes on while a real two-column row still follows.
        if "row" not in _kinds_below(lines, i + 1, split, margin, small):
            break
        flush()
        full.append(l)
        i += 1
    close_full()
    flush()
    return segs, i


def _kinds_below(lines, i, split, margin, small):
    """What each line of `lines[i:]` is, relative to the column boundary:
    "row" (runs clear of it on both sides), "left"/"right" (wholly one side of
    it), "prose" (crossing it from the left margin) or "marker" (crossing it,
    page-centered). The page's footnote block and a second table's header both
    end the sequence -- what follows them is not this region's.

    Read twice, for the two questions a line's own geometry cannot answer."""
    for l in lines[i:]:
        if _header(l) is not None:
            return
        runs = [r for r in l.runs if not small(r.size)]
        if not runs:
            if len(l.text) > 3:
                return
            continue
        lruns = [r for r in runs if r.right <= split]
        rruns = [r for r in runs if r.left >= split]
        if len(lruns) + len(rruns) == len(runs):
            yield "row" if lruns and rruns else ("left" if lruns else "right")
        elif min(r.left for r in runs) <= margin + CENTER_MARGIN:
            yield "prose"
        else:
            yield "marker"


def _prose_continues(lines, i, split, margin, small):
    """Whether the full-width paragraph open above `lines[i]` runs on, i.e.
    whether the next line that reaches the boundary at all is more full-width
    prose rather than the columns resuming.

    This is what tells a lettered sub-item of a full-width enumeration from the
    nuvarande column's first item. Prop. 2025/26:207 p. 15 sets
    "a) anställning," between two full-width lines of one enumeration -- read as
    a cell it became a one-cell table that also stole the region's header --
    while prop. 2025/26:77 p. 9 sets "1. Europaparlamentets och" as the left
    column's first item, with a two-column row under it."""
    return next((k for k in _kinds_below(lines, i, split, margin, small)
                 if k != "left"), None) == "prose"


def _paras(cells):
    """One column's [(top, Run)] -> [(top, text)] paragraphs: a paragraph
    starts at a vertical gap larger than the column's own line-height or at a
    first-line indent (how a statute's stycke opens)."""
    if not cells:
        return []
    col_left = min(r.left for _top, r in cells)
    # per-line: (top, left, text), lines in reading order
    rows = {}
    for top, r in cells:
        rows.setdefault(top, []).append(r)
    lines = [(top, min(r.left for r in runs),
              " ".join(r.text for r in sorted(runs, key=lambda r: r.left)))
             for top, runs in sorted(rows.items())]
    gaps = sorted(b[0] - a[0] for a, b in zip(lines, lines[1:], strict=False)
                  if b[0] > a[0])
    gap = gaps[len(gaps) // 2] if gaps else 0
    paras = []
    for top, lft, text in lines:
        starts = (not paras or lft > col_left + INDENT
                  or (gap and top - paras[-1][1] > PARA_GAP * gap))
        if starts:
            paras.append((top, top, text))
        else:
            start, _prev, acc = paras[-1]
            paras[-1] = (start, top, dehyphenate(acc, text))
    return [(start, text) for start, _last, text in paras]


def _rows(left, right):
    """Pair the two columns' paragraphs into aligned rows [(nuvarande,
    föreslagen)]: paragraphs starting at (nearly) the same height are the two
    sides of one row; one without a counterpart ('' on the other side) is
    text that is entirely new or entirely dropped."""
    lp, rp = _paras(left), _paras(right)
    rows, i, j = [], 0, 0
    while i < len(lp) or j < len(rp):
        if (i < len(lp) and j < len(rp)
                and abs(lp[i][0] - rp[j][0]) <= ROW_TOL):
            rows.append((lp[i][1], rp[j][1]))
            i, j = i + 1, j + 1
        elif j >= len(rp) or (i < len(lp) and lp[i][0] < rp[j][0]):
            rows.append((lp[i][1], ""))
            i += 1
        else:
            rows.append(("", rp[j][1]))
            j += 1
    return rows
