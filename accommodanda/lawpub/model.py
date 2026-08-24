"""Typed model for a lawpub article -- one open-access article the platform
hosts, from one of the publishers in `publishers.py`.

As in `lawreview`, an article is a fixed historical publication: it is issued
in one edition, it revises in place nowhere and it is withdrawn nowhere, so
the model is the thin end of the corpus -- identity, the edition's coordinates
(publisher, edition, date, opening page), the author, and the mined text. The
text is the mining text, not a republished edition: every paragraph in order,
no structure read off it, because the article is published as a PDF the
platform serves; the only thing the text must survive is the citation scan.

The coordinates the platform states are its own: the publisher (off the item's
icon), the edition's name and month-year (off its "Publicerad i" line) and the
page span (off the same line). The identifier takes the article-citation form
that form carries -- the publisher's abbreviation, the year, and the opening
page (`FT 2015 s. 551`), the edition standing in for the page where the line
states none. The basefile is the platform's own handle for the article: its
numeric section number where the listing carries one, its DOI otherwise
(`880`, `10.53292-c42237cc.fe896fd9`).
"""

import re
from dataclasses import dataclass, field

from ..lib.artifact import scanned_nodes
from ..lib.catalog import BASE

__all__ = ["Block", "Artikel", "lawpub_uri"]

# the page span the "Publicerad i" line sets ("s. 551–582", "s. 11–50",
# "s. 1–4"); the en-dash is the line's own separator
RE_PAGES = re.compile(r"(\d+)(?:\s*[-–]\s*(\d+))?")


def lawpub_uri(basefile):
    """The published document URI, minted from the platform's own handle for
    the article -- the address reproduces the handle by construction."""
    return "%slawpub/%s" % (BASE, basefile)


def start_page(sida):
    """The article's opening page, off the page span the listing states
    ("551-582" -> "551"); None where the line names no page at all."""
    if not sida:
        return None
    m = RE_PAGES.match(sida.strip())
    if not m:
        return None
    return m.group(1)


@dataclass
class Block:
    kind: str            # "stycke" -- no structure is read off the text
    text: str


@dataclass
class Artikel:
    basefile: str                      # "880" or "10.53292-..."
    titel: str
    utgivare: str                      # the publisher's kod ("FT", "SIPLR", ...)
    utgivare_namn: str | None = None   # the publisher's full name
    utgava: str | None = None          # the edition's name, as the line states it
    fattare: str | None = None         # the author(s), as the listing states them
    date: str | None = None            # the month-year the line states, widened to a day
    sida: str | None = None            # the page span ("551-582") or a single page
    sammanfattning: str | None = None  # kept for the artifact's optional field
    body: list[Block] = field(default_factory=list)   # the mining text
    source_url: str | None = None      # the platform's page for the article
    document_url: str | None = None    # the PDF the platform serves it as

    @property
    def uri(self):
        return lawpub_uri(self.basefile)

    @property
    def identifier(self):
        """The article-citation form: the publisher's abbreviation, the year,
        the opening page -- the edition standing in for the page where the line
        states none (`FT 2015 s. 551`, `SSIL 1957 s. 11`). An article the
        listing states no date for cites without the year (`FT s. 551`):
        the listing's own silence, never an invented year."""
        year = self.date[:4] if self.date else None
        page = start_page(self.sida)
        place = "s. %s" % page if page else self.utgava
        return " ".join(p for p in (self.utgivare, year, place) if p)

    @property
    def publisher(self):
        return self.utgivare_namn or self.utgivare

    def to_artifact(self, scanner):
        """The JSON artifact: the shared node convention (`structure` of stycke
        nodes with inline-run text), every text scanned for citations -- which
        is what puts an article's references on the context rails of the
        documents it names, the way `lawreview`'s articles do. The catalog's
        date projection expects a 10-char ISO date: the month-year the line
        states arrives already widened to a representative day (the
        download's "-15"), and an article the listing states no date for
        carries none -- the catalog lists undated documents last and prints
        no date, and an invented date would missort them."""
        structure = scanned_nodes(self.body, scanner)
        metadata = {"title": self.titel, "publisher": self.publisher,
                    "utgivare": self.utgivare}
        if self.utgava:
            metadata["utgava"] = self.utgava
        if self.fattare:
            metadata["fattare"] = self.fattare
        if self.sida:
            metadata["sida"] = self.sida
        art = {"uri": self.uri, "type": "juridisk_artikel",
               "utgivare": self.utgivare,
               "identifier": self.identifier,
               "metadata": metadata, "structure": structure}
        if self.date:
            art["date"] = self.date
        if self.sammanfattning:
            art["sammanfattning"] = self.sammanfattning
        if self.source_url:
            art["source_url"] = self.source_url
        if self.document_url:
            art["document_url"] = self.document_url
        return art