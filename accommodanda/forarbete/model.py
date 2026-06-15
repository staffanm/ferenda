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
    kind: str                 # "rubrik" (heading) | "stycke" (paragraph)
    text: str
    page: int | None = None   # printed page number (the #sid{N} anchor)
    level: int | None = None  # heading depth = dotted segments ("4.1.2" -> 3)


@dataclass
class Forarbete:
    type: str                 # prop | sou | ds | dir | fm | skr | so | lr
    basefile: str             # the document's own id, e.g. "2025/26:161"
    identifier: str           # display form, e.g. "Prop. 2025/26:161"
    uri: str                  # https://lagen.nu/prop/2025/26:161
    title: str
    date: str | None = None
    body: list = field(default_factory=list)   # [Block], document order
