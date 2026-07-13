"""In-memory model for a court decision (DV).

Deliberately flatter than the SFS model: a court decision has no rigid
nesting -- it is a sequence of section headings and paragraphs, plus
curated metadata. The body blocks are kept faithful (headings interleaved
with paragraphs in document order); grouping into sections, if wanted, is
a downstream projection rather than part of the model.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Rubrik:
    """A section heading (BAKGRUND, Skälen för avgörandet, DOMSLUT, …). `level`
    is the source HTML heading rank -- 1 for an `<h1>` instance name ("Svea
    hovrätt"), 2/3 for `<h2>/<h3>` sections; 0 for a heading inferred from a `<p>`
    by the heuristic (the legacy format carries no semantic heading tags)."""
    text: str
    level: int = 0


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
    avgorandedatum_lista: list[str] = field(default_factory=list)
    publiceringsform: str | None = None
    typ: str | None = None
    rattsomrade: list[str] = field(default_factory=list)
    nyckelord: list[str] = field(default_factory=list)
    lagrum: list[Lagrum] = field(default_factory=list)
    forarbeten: list[str] = field(default_factory=list)
    sammanfattning: str | None = None
    related: list[str] = field(default_factory=list)
    body: list[Rubrik | Stycke] = field(default_factory=list)
    footnotes: list[Fotnot] = field(default_factory=list)  # document end
    sources: list[str] = field(default_factory=list)  # provenance paths


@dataclass
class Fotnot:
    """An end-of-document footnote (HD started using these in 2023). `num` is
    the source marker digit; `text` is the footnote body (citation-linked
    downstream like any other body text)."""
    num: str
    text: str
