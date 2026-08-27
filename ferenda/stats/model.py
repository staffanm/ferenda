"""Typed model for one corpus measurement.

Every measurement is a `Measure`, and its `kind` is the on-disk discriminator the
renderer dispatches on -- the same contract the other verticals use between
artifact and page. The kinds are chosen by *what the data's job is*, which is
also what picks the chart form, so the renderer never has to guess:

  ``scalar``     one headline number (hero, no plot)
  ``toplist``    ranked named things -- horizontal bars, longest first
  ``series``     an ordered run over time -- line
  ``histogram``  a distribution over bins -- vertical bars
  ``bars``       unordered categories compared -- vertical bars
  ``profile``    every value drawn at its rank, largest first -- vertical
                 bars sampled over rank, so each bar is a real thing's own
                 size rather than a bucket count
  ``matrix``     two categorical axes with a magnitude -- heatmap
  ``sankey``     volume moving between the same categories -- flow diagram
  ``table``      rows that are read, not compared (no useful chart form)

A measurement has to admit what it cannot say -- which population it covers,
what it had to exclude, why a number is smaller than it looks. Two places carry
that, and neither is a footnote nobody reads: a `lede` that embeds measured
numbers (`computed_lede` in the page template) states the exclusions with their
counts, and the template's own `note=` renders under the figure for the caveat
that is prose rather than arithmetic. A measure whose caveat is invisible is a
measure that misleads.
"""

from dataclasses import asdict, dataclass, field


@dataclass
class Row:
    """One named value. `uri` makes the label a link when the thing measured is
    a document we host; `detail` is the secondary column a toplist may carry
    (a date, a count, the second half of a comparison)."""
    label: str
    value: float
    uri: str | None = None
    detail: str | None = None
    group: str | None = None    # the half of a two-ended measure this row is in
                                # ("Längst" / "Kortast"). Bars are scaled inside
                                # a group, never across: 55 313 and 24 on one
                                # scale draws the short end as nothing at all.
    steps: list["Row"] = field(default_factory=list)
                                # what the row's value counts, in order, shown
                                # in a foldout under the row. A row that says
                                # "23 steg from PFS 2025:2 to RFFS 2002:21" is
                                # not checkable without them: the reader cannot
                                # tell a chain of annual reissues from a chain
                                # of substance, and those are different facts
                                # about the law. Each step's `value` is its
                                # place in the chain, counting from 0.


@dataclass
class Point:
    """One (x, y) in a series or histogram. `x` is a label, not a number: the
    axes here are years, decades and bins, all of which are read as categories
    even when they happen to be numeric."""
    x: str
    y: float


@dataclass
class Tile:
    """One headline number in a scalar's KPI row: the figure as it should be
    read, and the caption saying what one unit of it is.

    A scalar that answers with several numbers at once ("så här många tecken,
    så här många ord, så här många dygns högläsning") cannot be one hero line
    -- three display-size figures set in a row of prose wrap into an unreadable
    block. Each number gets its own tile instead, and `caption` carries the unit
    at reading size, where a unit belongs."""
    value: str
    caption: str


@dataclass
class Cell:
    """One (row, col) magnitude. A matrix reads it as a grid position; a sankey
    reads the same pair as a flow from `row` to `col`."""
    row: str
    col: str
    value: float


@dataclass
class Measure:
    id: int
    group: str                  # "A".."G", the PRD's grouping
    title: str
    kind: str                   # scalar | toplist | series | histogram | bars
                                # | profile | matrix | sankey | table
    unit: str = ""              # what one unit of `value`/`y` is ("tecken")
    lede: str = ""              # the sentence that says what the number means
    lede_links: dict[str, str] = field(default_factory=dict)
                                # {text in the lede: uri}, linked at render
                                # time. A lede that names a document should let
                                # the reader go there; the lede itself stays
                                # plain text, because markup in a measurement
                                # is markup in the data
    value: float | None = None          # scalar
    display: str = ""                   # scalar: pre-formatted, when the raw
                                        # number is not what should be read
    tiles: list[Tile] = field(default_factory=list)      # scalar: a KPI row,
                                        # when the answer is several numbers
                                        # rather than one (`display` is then
                                        # the fallback for a reader with no CSS)
    rows: list[Row] = field(default_factory=list)        # toplist | table
    points: list[Point] = field(default_factory=list)    # series | histogram | bars
    cells: list[Cell] = field(default_factory=list)      # matrix | sankey
    xlabel: str = ""
    ylabel: str = ""


@dataclass
class Report:
    generated: str              # ISO date the measurements were taken
    measures: list[Measure] = field(default_factory=list)

    def to_artifact(self):
        """The on-disk shape: empty fields dropped, so a measure's JSON says only
        what that measure has. `asdict` would write every one of the twelve keys
        on all 53, which makes the artifact three times its size and makes a diff
        between two builds unreadable -- and the diff is the point of storing it."""
        return {"generated": self.generated,
                "measures": [_prune(asdict(m)) for m in self.measures]}


def _prune(d):
    return {k: v for k, v in d.items() if v not in (None, "", [], {})}
