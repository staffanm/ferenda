"""Typed model for the editorial site content (frontpage, /om about pages,
sitenews). Unlike the legal-document verticals this carries no citation graph --
it is hand-authored prose, links and lists -- so the model is a small block
tree, not a `Forfattning`/`Avgorande`-style domain structure.

A **block** is one of `Heading` / `Paragraph` / `Bullets` / `Code`; its `type`
field is the on-disk discriminator the renderer dispatches on (kept in Swedish
to match the other artifacts: `rubrik`/`stycke`/`lista`/`kod`). Inline content
is a list of *runs*: a bare `str` for plain text, or a dict `{"text", "uri"?,
"bold"?, "code"?}` for a link / emphasised / code span (`uri` resolved at parse
time via the shared `lib.markdown` link grammar, so the artifact is the source
of truth for structure and links).
"""

from dataclasses import dataclass

# an inline run: a bare str (plain text) or a dict {"text", "uri"?, "bold"?,
# "code"?} for a link / emphasised / code span (no run dataclass -- runs are the
# leaf serialised shape the renderer consumes directly)
Run = str | dict


@dataclass
class Heading:
    text: str
    level: int
    type: str = "rubrik"


@dataclass
class Paragraph:
    runs: list[Run]
    type: str = "stycke"


@dataclass
class Bullets:
    items: list[list[Run]]      # one run list per <li>
    type: str = "lista"


@dataclass
class Code:
    text: str
    type: str = "kod"


Block = Heading | Paragraph | Bullets | Code


@dataclass
class Frontpage:
    title: str
    blocks: list[Block]
    type: str = "frontpage"


@dataclass
class AboutPage:
    slug: str
    title: str
    blocks: list[Block]
    type: str = "om"


@dataclass
class NewsItem:
    id: str                     # anchor + Atom entry id, minted from the datetime
    published: str              # "2020-09-17 23:00:00" (naive local, as authored)
    title: str
    blocks: list[Block]


@dataclass
class Sitenews:
    title: str
    items: list[NewsItem]
    type: str = "sitenews"
