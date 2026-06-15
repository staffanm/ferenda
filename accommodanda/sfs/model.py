"""Document tree model for consolidated SFS texts.

Plain dataclasses; no RDF, no rendering. Field names use the Swedish
domain vocabulary (kapitel, paragraf, stycke...) since that is what the
source documents and all related tooling speak.
"""

from dataclasses import dataclass, field
from datetime import date


@dataclass
class Forfattning:
    ikrafttrader: date | str | None = None
    children: list = field(default_factory=list)


@dataclass
class Avdelning:
    ordinal: str
    rubrik: str
    underrubrik: str | None = None
    children: list = field(default_factory=list)


@dataclass
class Underavdelning:
    ordinal: str
    rubrik: str
    children: list = field(default_factory=list)


@dataclass
class Kapitel:
    ordinal: str
    rubrik: str
    upphor: date | str | None = None
    ikrafttrader: date | str | None = None
    children: list = field(default_factory=list)


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
    children: list = field(default_factory=list)


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
    children: list = field(default_factory=list)  # lists and tables


@dataclass
class Lista:
    kind: str  # "numrerad" | "bokstav" | "strecksats"
    children: list = field(default_factory=list)


@dataclass
class Listelement:
    ordinal: str
    text: str
    children: list = field(default_factory=list)  # nested Lista


@dataclass
class Tabell:
    rows: list = field(default_factory=list)


@dataclass
class Tabellrad:
    cells: list = field(default_factory=list)
    upphor: date | str | None = None
    ikrafttrader: date | str | None = None


@dataclass
class Overgangsbestammelser:
    rubrik: str
    children: list = field(default_factory=list)


@dataclass
class Overgangsbestammelse:
    sfsnr: str
    children: list = field(default_factory=list)


@dataclass
class Bilaga:
    rubrik: str
    upphor: date | str | None = None
    ikrafttrader: date | str | None = None
    children: list = field(default_factory=list)
