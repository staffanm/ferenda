"""In-memory model for a court decision (DV).

Deliberately flatter than the SFS model: a court decision has no rigid
nesting -- it is a sequence of section headings and paragraphs, plus
curated metadata. The body blocks are kept faithful (headings interleaved
with paragraphs in document order); grouping into sections, if wanted, is
a downstream projection rather than part of the model.
"""

from dataclasses import dataclass, field


@dataclass
class Rubrik:
    """A section heading (BAKGRUND, Skälen för avgörandet, DOMSLUT, …)."""
    text: str


@dataclass
class Stycke:
    """A body paragraph; ordinal is set for numbered paragraphs
    ('1. ...', as used in HFD/HD prejudikat)."""
    text: str
    ordinal: str | None = None


@dataclass
class Lagrum:
    referens: str               # curated citation string from the source
    sfsnummer: str | None = None


@dataclass
class Avgorande:
    court: str                  # canonical court code (HDO, HFD, MOD, …)
    court_namn: str
    malnummer: list[str] = field(default_factory=list)
    referat: list[str] = field(default_factory=list)
    avgorandedatum: str | None = None
    publiceringsform: str | None = None
    typ: str | None = None
    rattsomrade: list[str] = field(default_factory=list)
    nyckelord: list[str] = field(default_factory=list)
    lagrum: list[Lagrum] = field(default_factory=list)
    forarbeten: list[str] = field(default_factory=list)
    sammanfattning: str | None = None
    related: list[str] = field(default_factory=list)
    body: list = field(default_factory=list)        # list[Rubrik | Stycke]
    sources: list[str] = field(default_factory=list)  # provenance paths
