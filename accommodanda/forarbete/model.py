"""Typed model for a preparatory work (förarbete).

Like court decisions (and unlike statutes), förarbeten have no rigid nesting —
they are a flat sequence of headings and body paragraphs. What matters for the
link graph is the **printed page number** on each block: förarbete citations are
page-precise ("prop. 1997/98:45 s. 39" -> `prop/1997/98:45#sid39`), so every
block carries the page it sits on, and the artifact exposes `#sid{N}` anchors.
"""

from dataclasses import dataclass, field


@dataclass
class Block:
    kind: str                 # "rubrik" (numbered section heading) | "stycke"
                              # (paragraph) | "kapitel" / "paragraf" (a law's
                              # bold chapter/§ markers, recovered from font) |
                              # "fotnot" (small-print footnote, e.g. the
                              # lagtext "Senaste lydelse" provenance) |
                              # "tabell" (a nuvarande/föreslagen lydelse
                              # comparison, reconstructed from the two-column
                              # layout -- see lydelse.py) |
                              # "signatur" (a signer name in the prop/skr
                              # överlämnande block -- see parse.tag_frontmatter)
    text: str
    page: int | None = None   # printed page number (the #sid{N} anchor)
    level: int | None = None  # heading depth = dotted segments ("4.1.2" -> 3)
    num: str | None = None     # chapter/§ number for kapitel/paragraf markers
    rows: list[tuple[str, ...]] | None = None  # tabell cell rows: a lydelse
                                               # pair, or a generic table's
                                               # N-column tuples
    th: bool = False           # tabell: row 0 is the column header pair


@dataclass
class Forarbete:
    type: str                 # prop | sou | ds | pm | dir | fm | skr | so |
                              # lr | bet
    basefile: str             # the document's own id, e.g. "2025/26:161"
    identifier: str           # display form, e.g. "Prop. 2025/26:161"
    uri: str                  # https://lagen.nu/prop/2025/26:161
    title: str
    date: str | None = None
    ocr: bool = False         # body came through an OCR route (ABBYY xml or
                              # the pdftotext scan fallback) -- gates the
                              # future-citation sanity check at projection
    body: list[Block] = field(default_factory=list)   # document order
