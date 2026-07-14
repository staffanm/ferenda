"""Document tree model for consolidated SFS texts.

Plain dataclasses; no RDF, no rendering. Field names use the Swedish
domain vocabulary (kapitel, paragraf, stycke...) since that is what the
source documents and all related tooling speak.

What each container's ``children`` may hold follows the assembler's
containment ranks (sfs/assembler.py RANK): opening an element of rank r
closes everything at rank >= r, so a container holds only deeper-ranked
elements (the ``*Innehall`` aliases at the bottom of this module).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class Forfattning:
    ikrafttrader: date | str | None = None
    children: list[ForfattningInnehall] = field(default_factory=list)


@dataclass
class Avdelning:
    ordinal: str
    rubrik: str
    underrubrik: str | None = None
    children: list[AvdelningInnehall] = field(default_factory=list)


@dataclass
class Underavdelning:
    ordinal: str
    rubrik: str
    children: list[UnderavdelningInnehall] = field(default_factory=list)


@dataclass
class Kapitel:
    ordinal: str
    rubrik: str
    upphor: date | str | None = None
    ikrafttrader: date | str | None = None
    children: list[KapitelInnehall] = field(default_factory=list)


@dataclass
class UpphavtKapitel:
    ordinal: str
    text: str


@dataclass
class Paragraf:
    ordinal: str
    moment: str | None = None
    upphor: date | str | None = None
    ikrafttrader: date | str | None = None
    children: list[Stycke] = field(default_factory=list)


@dataclass
class UpphavdParagraf:
    ordinal: str
    text: str


@dataclass
class Rubrik:
    text: str
    underrubrik: bool = False
    upphor: date | str | None = None
    ikrafttrader: date | str | None = None


@dataclass
class Stycke:
    text: str
    children: list[Lista | Tabell] = field(default_factory=list)


@dataclass
class Lista:
    kind: str  # "numrerad" | "bokstav" | "strecksats"
    children: list[Listelement] = field(default_factory=list)


@dataclass
class Listelement:
    ordinal: str
    text: str
    children: list[Lista] = field(default_factory=list)  # nested Lista


@dataclass
class Tabell:
    rows: list[Tabellrad] = field(default_factory=list)


@dataclass
class Tabellrad:
    cells: list[str] = field(default_factory=list)
    upphor: date | str | None = None
    ikrafttrader: date | str | None = None


@dataclass
class Overgangsbestammelser:
    rubrik: str
    children: list[Overgangsbestammelse] = field(default_factory=list)


@dataclass
class Overgangsbestammelse:
    sfsnr: str
    children: list[UnderavdelningInnehall] = field(default_factory=list)


@dataclass
class Bilaga:
    rubrik: str
    upphor: date | str | None = None
    ikrafttrader: date | str | None = None
    children: list[BilagaInnehall] = field(default_factory=list)


@dataclass
class Konventionsbilaga:
    """A multilingual convention appendix incorporated into an SFS statute.

    Unlike an ordinary statute appendix, this is a parallel corpus: each
    instrument, section, article and paragraph exists once, with text aligned
    across the two or three languages the appendix prints (``languages`` holds
    them in display order). Keeping that alignment in the source model prevents
    the renderer from having to infer legal structure from flat text runs.
    """

    instruments: list[Konventionsinstrument]
    languages: tuple[str, ...]


@dataclass
class Konventionsstycke:
    texter: dict[str, str] = field(default_factory=dict)


@dataclass
class Konventionsinstrument:
    # the protocol number ("4", "1" for the first/additional protocol) or None
    # for the base convention; the SFS projection turns it into the #B1/#B1P4
    # anchor and resolves the treaty URI via sfs/data/incorporates.json.
    protokoll: str | None
    rubriker: dict[str, str] = field(default_factory=dict)
    ingresser: list[Konventionsstycke] = field(default_factory=list)
    children: list[Konventionsavdelning | Konventionsartikel] = field(
        default_factory=list)


@dataclass
class Konventionsavdelning:
    ordinal: str
    rubriker: dict[str, str] = field(default_factory=dict)


@dataclass
class Konventionsartikel:
    ordinal: str
    rubriker: dict[str, str] = field(default_factory=dict)
    texter: list[Konventionsstycke] = field(default_factory=list)


# rank 4 containers (Kapitel) hold paragraf-level content and loose blocks
KapitelInnehall = Paragraf | UpphavdParagraf | Rubrik | Stycke | Lista | Tabell
# rank 3 containers (Underavdelning) and Overgangsbestammelse (rank 2) add
# kapitel-level content
UnderavdelningInnehall = Kapitel | UpphavtKapitel | KapitelInnehall
# rank 2 containers (Avdelning) add underavdelningar
AvdelningInnehall = Underavdelning | UnderavdelningInnehall
# rank 1 containers (Bilaga) add avdelningar
BilagaInnehall = Konventionsbilaga | Avdelning | AvdelningInnehall
# the document root holds everything, incl. the rank-1 trailing sections
ForfattningInnehall = Overgangsbestammelser | Bilaga | BilagaInnehall
