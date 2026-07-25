"""Directive lineage: the article<->article map a recast states in its own
jämförelsetabell annex.

The case law that matters for a Swedish statute is usually older than the
directive the statute transposes. LOU 3 kap. 11-16 §§ transpose article 12 of
2014/24, but Teckal (C-107/98), Stadt Halle (C-26/03) and Parking Brixen
(C-458/03) all cite 93/36 and 2004/18 -- generations the paragraf's genomförande
layer never names. A recast, though, publishes the mapping itself: 2014/24
bilaga XV pairs each of its articles with the 2004/18 article it replaced, and
2004/18 bilaga XII does the same against 93/37, 93/36 and 92/50. Chaining those
tables carries a paragraf's article set back through every generation, and the
EU-case-law rail then finds the old judgments without knowing lineage exists.

This is mechanical, not authored: the table is structured data in the parsed
act, so the lineage is *extracted semantics of the act itself* and belongs in
the act's own artifact (rule:artifact-is-truth), exactly like the förarbete
parser's `implements`. So `eurlex parse` calls `correspondence` on every act
and stores what it finds under the artifact's `correspondence` key; relate
reads it from there. There is no separate action to run and no authored layer
to keep in step -- an act's lineage is regenerated with its artifact.

(Contrast `sfs.correspond`, which *is* an annstore layer: that one is an LLM
reading a proposition's prose, expensive to regenerate and hand-correctable.
Here a misread table is bad *source*, and the source patch hook is the fix.)

Two properties of the source decide the shape of the reader:

* **Orientation varies.** The common layout puts the *repealed* act in column 1
  and "Detta direktiv" in column 2 (2017/2110, 2016/1076, most recasts); the
  procurement directives do the opposite. Neither is a majority everywhere, so
  the self column is found by wording, not by position.
* **The old side's links are wrong.** The citation engine resolves "Artikel 12"
  in any cell against the act being parsed, so an old-side cell links to the
  *new* act's article 12. Only the header's act-level citation ("Direktiv
  2004/18/EG") is trustworthy; every article number is therefore read from the
  cell *text*.
"""

import re

from ..lib import lagrum, layout
from ..lib.eu_structure import flatten

BASE = lagrum.CELEX_BASE

# the header cell naming the act we are reading; the other columns name the acts
# it replaced. Every phrasing the corpus actually uses: "Detta direktiv" and
# "Denna förordning" carry the bulk, "Den här förordningen" / "Det här
# direktivet" another 40 tables, and the singletons ("Föreliggande direktiv",
# "Denna delegerade förordning") cost nothing to admit. English is included for
# the eng manifestations, though no eng artifact in the corpus has a readable
# table today -- every one of them is a ">Plats för tabell<" placeholder.
SELF_COLUMN = re.compile(
    r"^(?:detta|denna|det\s+här|den\s+här|föreliggande|this|the\s+present)\s+"
    r"(?:\w+\s+)?"                    # "delegerade", "reviderade", …
    r"\w*(direktiv|förordning|beslut|directive|regulation|decision)\w*\b",
    re.I)                             # … or a compound: genomförandeförordning
# an act designation in a header cell the citation engine did not resolve to a
# link -- systematically the Euratom acts ("Direktiv 92/3/Euratom") and the
# "(EU, Euratom)" numbering, whose names carry no uri run
HEADER_ACT = re.compile(
    r"\b(\w*(?:direktiv|förordning|beslut)\w*)\b"
    r"(?:\s*\([^)]*\))?\s*(nr|No)?\s*"
    r"(\d{1,4})/(\d{1,4})\b", re.I)
# an article citation opening a cell: "Artikel 5", "Artiklarna 1 och 2",
# "Art 80.1", "Article 5"
ARTICLE_LEAD = re.compile(r"\b(artiklarna|artikel|articles|article|art)\b\.?\s*",
                          re.I)
# one pinpoint token in the dotted grammar the corpus uses: 5, 5a, 1.9, 12.2.3
PINPOINT = re.compile(r"\d+[a-z]?(?:\.\d+[a-z]?)*")
# what separates the members of an enumeration inside one cell
SEPARATOR = re.compile(r"\s*(?:,|och|and|samt|&)\s*|\s*[-–—]\s*", re.I)
RANGE = re.compile(r"[-–—]")
CELEX_URI = re.compile(re.escape(BASE) + r"([0-9][A-Z0-9()/._-]+)")


def _cells(node):
    """One `row` block's cells as `(text, [celex, ...])`.

    The parser joins a table row's cells with " | " *inside* the inline-run
    list, so a cell boundary can fall in the middle of a run sequence: the
    string runs are split on the separator and the link runs accumulate into
    whichever cell is open. Empty interior cells survive the join (see
    `parse._emit_table`), so a cell's index is its column."""
    cells = [([], [])]
    for run in node.get("text") or []:
        if isinstance(run, str):
            head, *rest = run.split(" | ")
            cells[-1][0].append(head)
            for part in rest:
                cells.append(([part], []))
        else:
            cells[-1][0].append(run.get("text", ""))
            if m := CELEX_URI.fullmatch(run.get("uri", "")):
                cells[-1][1].append(m.group(1))
    return [("".join(t).strip(), celexes) for t, celexes in cells]


def _articles(cell):
    """The article numbers a table cell cites, in order, deduplicated.

    A cell is prose around an enumeration -- "Artikel 1.1, 1.2, 1.4, 1.5 och
    1.6", "Artiklarna 71.5–71.8", "Artikel 1 a första delen av meningen" -- so
    the numbers are taken from the run of pinpoints following each article
    lead-in, and everything after the enumeration ends is prose. A plain
    integer range is filled ("Artiklarna 47–49" covers 48); a dotted range is
    not, since 71.5–71.8 lies inside one article anyway.

    Returns `[(article, pinpoint), ...]`: the bare article number the lineage
    joins on, and the fuller pinpoint the citing prose shows. Cells that cite
    no article at all -- "—", "Ny", "Bilaga IV", "Skäl 16 anpassat" -- yield
    nothing, which is exactly the "no counterpart" case."""
    found = []
    for lead in ARTICLE_LEAD.finditer(cell):
        pos, pins = lead.end(), []
        while m := PINPOINT.match(cell, pos):
            pins.append(m.group(0))
            sep = SEPARATOR.match(cell, m.end())
            nxt = PINPOINT.match(cell, sep.end()) if sep else None
            if not (sep and nxt):
                break
            if (RANGE.search(sep.group(0)) and pins[-1].isdigit()
                    and nxt.group(0).isdigit()):
                pins.extend(str(n) for n in
                            range(int(pins[-1]) + 1, int(nxt.group(0))))
            pos = sep.end()
        found.extend(pins)
    return list(dict.fromkeys((p.split(".")[0], p) for p in found).keys())


def _header_celex(text, celexes):
    """The CELEX a header column names: the citation engine's own resolution
    when it made one, else the act designation read out of the cell text.

    The fallback is needed because the engine does not resolve Euratom acts or
    the "(EU, Euratom)" numbering, so those header cells carry a name and no
    link -- and a table whose columns cannot be identified is a table we have
    to drop whole.

    The two numbers are ordered by the act's own numbering convention, which
    `lagrum.celex_uri` cannot settle alone here: for "Förordning (EG) nr
    63/2002" both 63 (as 1963) and 2002 are valid CELEX years, and the
    structurally-likeliest-first heuristic picks the wrong one. The "nr"/"No"
    that a pre-2015 regulation carries is exactly what marks the old
    number/year order, so it decides -- everything else is year/number."""
    if celexes:
        return celexes[0]
    m = HEADER_ACT.search(text)
    if not m:
        return None
    word = m.group(1).lower()
    akttyp = next((t for t in ("direktiv", "förordning", "beslut")
                   if t in word and not word.startswith("ram")), None)
    if not akttyp:
        return None                 # e.g. "rambeslut" -- CELEX sector 3 type F
    first, second = m.group(3), m.group(4)
    year, number = ((second, first) if m.group(2) and akttyp == "förordning"
                    else (first, second))
    try:
        uri = lagrum.celex_uri({"akttyp": akttyp, "ar": year,
                                "lopnummer": number}, base=layout.BASE)
    except lagrum.NoLink:
        return None                 # neither number can be a CELEX year
    return uri[len(BASE):]


def _tables(art):
    """Every correspondence table in one parsed act: `(self_column,
    {column: celex}, [row cells, ...])`.

    A table is located by its header row -- the row naming this act in one
    column and another act in the others -- rather than by the annex heading,
    which is unreliable: Formex splits "BILAGA XV" and "JÄMFÖRELSETABELL" into
    separate nodes and 2014/24's last "jämförelsetabell" mention is in article
    91, nowhere near the table. The table runs from the header to the first
    block that is not a row."""
    blocks, tables = flatten(art["structure"]), []
    for i, block in enumerate(blocks):
        if block.get("type") != "row":
            continue
        header = _cells(block)
        if len(header) < 2:
            continue
        selves = [c for c, (text, _) in enumerate(header)
                  if SELF_COLUMN.match(text)]
        if len(selves) != 1:
            continue                # not a header (or an unreadable one)
        others = {c: found for c, (text, celexes) in enumerate(header)
                  if c != selves[0]
                  and (found := _header_celex(text, celexes))}
        if not others:
            continue                # e.g. an "Andra rättsakter" free-text column
        rows = []
        for follower in blocks[i + 1:]:
            if follower.get("type") != "row":
                break
            rows.append(_cells(follower))
        tables.append((selves[0], others, rows))
    return tables


def _year(celex):
    """The four-digit year a CELEX carries, as an int."""
    return int(celex[1:5])


def correspondence(art):
    """The article<->article lineage a parsed act's own jämförelsetabell states,
    as `(edges, stats)`. Empty edges when the act has no readable table, which
    is the overwhelmingly common case -- 386 of 19 405 sector-3 acts carry one,
    and a judgment carries none at all -- so this runs on every act at parse
    time and costs nothing when there is nothing to find (0.0 ms on a judgment,
    a few ms on the largest correlation table in the corpus).

    Every column of every table that names an *earlier* act becomes edges from
    this act's articles to that act's. A column naming a later act is skipped
    rather than inverted: the successor states the same relation in its own
    table, and that is the copy to keep."""
    celex = art["celex"]
    tables = _tables(art)
    edges, stats = {}, {"tables": len(tables), "rows": 0, "empty": 0,
                        "forward": 0, "columns": 0, "short": 0}
    for self_column, others, rows in tables:
        stats["rows"] += len(rows)      # per row, not per (row, column) pair:
        backwards = {c: old for c, old in others.items()  # the printed count
                     if _year(old) <= _year(celex)}       # must be the table's
        stats["forward"] += len(others) - len(backwards)
        stats["columns"] += len(backwards)
        for column, old_celex in sorted(backwards.items()):
            old_uri = BASE + old_celex
            for cells in rows:
                if self_column >= len(cells) or column >= len(cells):
                    # a row that stops before this column: the trailing cells
                    # were empty in the source, so the column has no value
                    # here. Counted apart from `empty` because a *large* short
                    # count means the row and header widths disagree, and the
                    # caller reports it -- silently dropping most of a table
                    # would read as "this act has little lineage"
                    stats["short"] += 1
                    continue
                new_side = _articles(cells[self_column][0])
                old_side = _articles(cells[column][0])
                if not (new_side and old_side):
                    stats["empty"] += 1
                    continue
                for new_article, new_pin in new_side:
                    for old_article, old_pin in old_side:
                        edges.setdefault(
                            (new_article, old_uri, old_article),
                            {"newArticle": new_article,
                             "oldLaw": old_uri,
                             "oldArticle": old_article,
                             "oldUri": "%s#%s" % (old_uri, old_article),
                             "newPinpoint": new_pin,
                             "oldPinpoint": old_pin,
                             "quote": "%s — %s" % (cells[self_column][0],
                                                   cells[column][0])})
    stats["emitted"] = len(edges)
    stats["oldLaws"] = len({e["oldLaw"] for e in edges.values()})
    return list(edges.values()), stats
