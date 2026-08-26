"""Extract a proposition's *jämförelsetabell* / *paragrafnyckel* tables -- the
two-column tables a re-enacting proposition ships to map every provision of
the new law onto the old one (and usually vice versa: OSL prop 2008/09:150
bilaga 7/8, the 2025 socialtjänstlag prop 2024/25:89 bilaga 16/17, NML prop
2022/23:46 bilaga 4/5, PBL prop 2009/10:170 avsnitt 29).

The tables live late in the PDF (sometimes a separate volume that the artifact
parse never reads), and text-order extraction interleaves the two columns
exactly like the lydelse tables, so this module is the jämförelsetabell
sibling of `lydelse.py`, working from the same per-run horizontal extents. The
geometry differs enough to need its own pass, and varies across props:

- the region is bounded by the bilaga's page-margin marker ("Bilaga 7") when
  the table is a bilaga; a table set as a body chapter (PBL's avsnitt 29) has
  no marker and instead repeats its column-header pair at the top of every
  continuation page;
- the column positions shift between the title page and the continuation
  pages, so each page's columns are re-derived by clustering cell starts;
- the column header may sit on the title page or the next one, repeat on
  every page, or (elcertifikat prop 2010/11:155) be printed as one merged run
  that cannot be split -- a table with rows but no recoverable header is still
  returned, headerless.

`tables(pdf_path)` is the whole interface: every table in the PDF ->
{"bilaga", "header": (left, right) | None, "rows": [(left, right, page)],
"text": the title page's prose (title + intro), for the caller to identify
which laws the table maps}. Cell texts stay raw (provision labels plus
qualifiers like "delvis" / "(ny)" / "-"). Reading the labels *as provisions
of the two laws* is the caller's job (sfs.correspond.table_correspond) --
this module knows print layout, not SFS identity."""

import re

from ..lib.pdftext import pdf_pages

CELL_GAP = 40   # a horizontal gap this wide between runs splits a line into cells
COL_TOL = 20    # cell starts within this many x-units are the same column
MIN_COL = 3     # a column must recur this often on a page to be believed

RE_MARGIN = re.compile(r"^(?:Prop\. \d{4}(?:/\d{2,4})?:\d+|Bilaga \d+)$")
RE_BILAGA = re.compile(r"^Bilaga (\d+)$")
RE_TITLE = re.compile(
    r"^(?:Jämförelsetabell|Jämförelse mellan|Paragrafnyckel|Paragrafregister)")
RE_PROV = re.compile(r"^\d+(?:\s?[a-z])?\s?(?:kap\.|§)")
# a per-law section rubrik inside a multi-law register bilaga ("1.1 Lagen
# (1962:381) om allmän försäkring", SFB prop 2008/09:200) -- each section is
# its own table against its own old law
RE_SECTION = re.compile(r"^\d+(?:\.\d+)*$")
RE_SFS_PAREN = re.compile(r"\(\d{4}:\d+\)")
# a column header of prop-local shorthands ("AFL" / "SFB")
RE_ABBREV = re.compile(r"^[A-ZÅÄÖ0-9 .]{2,12}$")
# a table row opens with a provision label or one of the non-§ units a
# jämförelsetabell also maps (the bilaga, transition provisions, a heading)
RE_ROWSTART = re.compile(
    r"^(?:\d+(?:\s?[a-z])?\s?(?:kap\.|§)|Bilagan\b|Övergångsbest|"
    r"Ikraftträdande|Rubrik)")
RE_DOTS = re.compile(r"\.{4,}")     # TOC dotted leaders


def _line_cells(line, cols=()):
    """One Line -> [(left, right, text)] cells: runs gap-split, never merging
    across a known column start (a header set tighter than CELL_GAP to the
    boundary -- "Sekretesslag (1980:100)" ending 17pt before "Offentlighets-
    och" -- must still split). The page-margin marker runs ("Prop.
    2008/09:150", "Bilaga 7") are dropped, a lone page-number line whole."""
    if line.text.strip().isdigit():
        return []
    out = []
    for r in sorted(line.runs, key=lambda r: r.left):
        if RE_MARGIN.match(r.text.strip()):
            continue
        crosses = out and any(out[-1][1] < c <= r.left + COL_TOL
                              for c in cols)
        if out and r.left - out[-1][1] <= CELL_GAP and not crosses:
            out[-1] = (out[-1][0], r.right, "%s %s" % (out[-1][2], r.text))
        else:
            out.append((r.left, r.right, r.text))
    return out


def _columns(page_cells, remembered=()):
    """The page's column start positions: cell start xs clustered within
    COL_TOL, keeping clusters that recur (>= MIN_COL cells) -- one-off starts
    (a wrapped header fragment, an indented note) are not columns. A cluster
    matching one of the table's `remembered` columns is kept regardless: a
    sparse final page (two rows) must not lose its right column. Falls back
    to all clusters when none qualifies (a nearly empty page)."""
    xs = sorted(c[0] for cs in page_cells for c in cs)
    clusters = []
    for x in xs:
        if clusters and x - clusters[-1][-1] <= COL_TOL:
            clusters[-1].append(x)
        else:
            clusters.append([x])
    solid = [c for c in clusters
             if len(c) >= MIN_COL
             or any(abs(min(c) - r) <= COL_TOL for r in remembered)]
    return [min(c) for c in solid or clusters]


def _col_of(cell, cols):
    """The index of the column this cell starts in (nearest column start)."""
    return min(range(len(cols)), key=lambda i: abs(cols[i] - cell[0]))


def _header(cells_line):
    """The (left, right) column header pair if this line can be a table's
    column header: exactly two cells, neither a provision label, a page
    number or a TOC leader line, at least one naming a law or a provision
    column ("Sekretesslag (1980:100)", "Bestämmelse i NML") -- or both being
    short shorthands ("AFL" / "SFB", the SFB register's per-law sections)."""
    if (len(cells_line) == 2
            and not any(RE_PROV.match(c[2]) for c in cells_line)
            and not any(c[2].strip().isdigit() for c in cells_line)
            and not any(RE_DOTS.search(c[2]) for c in cells_line)
            and (any(re.search(r"lag|bestämmelse", c[2], re.IGNORECASE)
                     for c in cells_line)
                 or all(RE_ABBREV.match(c[2].strip()) for c in cells_line))):
        return (cells_line[0][2], cells_line[1][2])
    return None


def _section_rubrik(cells_line):
    """The section text if this line opens a per-law section of a multi-law
    register: "1.1 | Lagen (1962:381) om allmän försäkring" as two cells, or
    the same set tight enough to gap-merge into one."""
    if (len(cells_line) == 2 and RE_SECTION.match(cells_line[0][2].strip())
            and RE_SFS_PAREN.search(cells_line[1][2])):
        return "%s %s" % (cells_line[0][2], cells_line[1][2])
    if (len(cells_line) == 1
            and re.match(r"\d+(?:\.\d+)+\s+\S", cells_line[0][2])
            and RE_SFS_PAREN.search(cells_line[0][2])):
        return cells_line[0][2]
    return None


def _titled(page_cells):
    """Whether the page carries a table title ("Jämförelsetabell …" /
    "Paragrafnyckel …") as a real heading -- a dotted TOC entry is not one."""
    return any(RE_TITLE.match(c[2].lstrip()) and not RE_DOTS.search(c[2])
               for cs in page_cells for c in cs)


def tables(pdf_path):
    """Every jämförelsetabell/paragrafnyckel in the PDF, in page order (see
    module docstring for the returned shape). A table starts on a page whose
    text carries the title; a bilaga table continues over every following
    page with the same "Bilaga N" margin marker, a markerless body-chapter
    table over every page that opens by repeating its header pair. Inside a
    region, a per-law section rubrik ("1.1 Lagen (1962:381) om …") or a
    different header pair starts a *sibling* table -- a multi-law register
    bilaga (SFB prop 2008/09:200) is one table per replaced law. A row opens
    where the left column holds a provision label (RE_ROWSTART); other cell
    lines fold into the open row (wrapped cells); pre-row prose collects as
    the table's `text`. A title page that never yields rows (a TOC page) is
    dropped."""
    tabs = []
    by_marker = {}      # bilaga number -> table dict
    chapter = None      # the active markerless (body-chapter) table
    for pageno, lines in pdf_pages(pdf_path):
        marker = _bilaga_marker(lines)
        page_cells = [cs for cs in (_line_cells(l) for l in lines) if cs]
        if not page_cells:
            continue
        titled = _titled(page_cells)
        if marker is not None:
            chapter = None
            if marker not in by_marker:
                if not titled:
                    continue                # some other bilaga's pages
                by_marker[marker] = _new_table(marker, tabs)
            table = by_marker[marker]
        elif titled:
            table = chapter = _new_table(None, tabs)
        elif chapter is not None and chapter["header"] \
                and page_cells[0][0][2] == chapter["header"][0]:
            table = chapter                 # page opens with the repeated header
        else:
            chapter = None
            continue
        cols = table["cols"] = _columns(page_cells, table["cols"])
        for line in lines:
            cs = _line_cells(line, cols)
            if not cs or any(RE_DOTS.search(c[2]) for c in cs):
                continue
            texts = tuple(c[2] for c in cs)
            if table["header"] is not None and texts == table["header"]:
                continue                    # per-page repeated header
            # a per-law section rubrik, or a *different* header pair, starts
            # a sibling table: a multi-law register bilaga (SFB prop
            # 2008/09:200) is one section per replaced law, each with its own
            # old law and its own "AFL | SFB"-style header
            section = _section_rubrik(cs)
            if section:
                if table["rows"] or table["text"]:
                    # a fresh section: the accumulated table (rows, or the
                    # register's intro prose -- whose text lists *every*
                    # section's law and must not identify any one section)
                    # is closed off
                    table = _new_table(marker, tabs)
                    table["cols"] = cols
                    if marker is not None:
                        by_marker[marker] = table
                    else:
                        chapter = table
                table["text"] = ("%s %s" % (table["text"], section)).strip()
                continue
            found = _header(cs)
            if found and table["header"] is not None and table["rows"]:
                sibling = _new_table(marker, tabs)
                sibling["cols"], sibling["header"] = cols, found
                table = sibling
                if marker is not None:
                    by_marker[marker] = table
                else:
                    chapter = table
                continue
            if table["header"] is None and found:
                table["header"] = found
                continue
            left = " ".join(c[2] for c in cs if _col_of(c, cols) == 0)
            right = " ".join(c[2] for c in cs if _col_of(c, cols) != 0)
            if RE_ROWSTART.match(left):
                table["rows"].append((left, right, pageno))
            elif table["rows"] and (left or right):
                pl, pr, pp = table["rows"][-1]
                table["rows"][-1] = (("%s %s" % (pl, left)).strip(),
                                     ("%s %s" % (pr, right)).strip(), pp)
            elif not table["rows"]:
                table["text"] = ("%s %s" % (table["text"], " ".join(texts))).strip()
    for t in tabs:
        del t["cols"]           # per-table layout memory, not part of the shape
    return [t for t in tabs if t["rows"]]


def _new_table(marker, tabs):
    table = {"bilaga": marker, "header": None, "rows": [], "text": "",
             "cols": ()}
    tabs.append(table)
    return table


def _bilaga_marker(lines):
    """The page's margin bilaga number, or None."""
    for l in lines:
        for r in l.runs:
            m = RE_BILAGA.match(r.text.strip())
            if m:
                return int(m.group(1))
    return None
