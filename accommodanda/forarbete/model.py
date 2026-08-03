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
                              # överlämnande block -- see parse.tag_frontmatter) |
                              # "ruta" (the ruled box a förarbete states its
                              # proposal or assessment in -- "Regeringens
                              # förslag:" in a proposition, "Förslag:"/
                              # "Bedömning:" in a SOU. The rule is a vector
                              # drawing pdftohtml discards; the box is read from
                              # the narrower measure it is set to) |
                              # "bild" (an illustration the PDF embeds, carried
                              # as the `bbox` the facsimile endpoint crops --
                              # see pdftext.pdf_figures)
    text: str
    page: int | None = None   # printed page number (the #sid{N} anchor)
    level: int | None = None  # heading depth = dotted segments ("4.1.2" -> 3)
    num: str | None = None     # chapter/§ number for kapitel/paragraf markers
    rows: list[tuple[str, ...]] | None = None  # tabell cell rows: a lydelse
                                               # pair, or a generic table's
                                               # N-column tuples
    th: bool = False           # tabell: row 0 is the column header pair
    bilaga: str | None = None  # the bilaga whose own numbering this page belongs
                               # to, where it restarted its count ("23" -> the
                               # #bilaga23-sid{N} anchor). Last of the fields
                               # a caller may pass positionally, which stop at
                               # `level` (after `page`); the geometry fields
                               # below are keyword-only in practice.
    # (start, end, "i"/"b"/"bi") over `text`: what the document emphasised,
    # carried from the PDF's font runs so the artifact keeps it
    spans: list[tuple[int, int, str]] = field(default_factory=list)
    # "bild": the figure's rectangle on its PDF page, in points from the page's
    # top-left -- what the facsimile endpoint crops. The pixels stay in the
    # source PDF and are rendered on demand, so no image is copied into the
    # corpus (lib/facsimile.cached_region)
    bbox: list[float] | None = None
    top: int | None = None    # y on the source page, used only while placing a
                              # figure among the paragraphs it was printed
                              # between. None where the block has no geometry to
                              # carry (a tabell is rebuilt from its cells), which
                              # the placement walk reads as "wherever its
                              # neighbours are" rather than as the top of the page


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
