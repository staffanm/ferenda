"""A harvested lawreview record + its document -> :class:`Artikel` -> JSON
artifact.

The articles are not republished on the site: they are mined for the
references they make -- the statute they read, the förarbete they quote, the
rättsfall they apply -- and those references are what the article puts on the
context rails of those documents, next to everything else that reads them.
So the parse has exactly one job: deliver the article's whole text to the
citation scanner.

That says what the parse deliberately does not do. It classifies no headings,
removes no cover page and no running head, and splits no footnote from the
text -- the footnotes in particular stay, because that is where the SOU and
NJA references are densest, and a citation in a footnote is a citation.
What goes out is every paragraph, in order, each as an ordinary stycke.

The two journals hand the text over differently, and the difference is
exactly the one from `journals.py`: svjt sets the article as a web page
(`_svjt_body` reads its paragraphs off the stored page), and jp sets it as a
PDF (`_jp_body` reads the running text off the pages the way `guidance.parse`
does). What the record carries is the listing's own statement -- title,
author, abstract, year, issue -- and the document overwrites nothing of it.
"""

import re

from bs4 import BeautifulSoup

from ..lib import compress, markup, patch
from ..lib.harvest import page_path, pdf_path
from ..lib.lagrum import ALL_PARSE_TYPES, sfs_parser
from ..lib.pdftext import page_paragraphs, pdf_pages
from ..lib.util import (
    approximate_date,
    normalize_space,
    record_path,
)
from .journals import BY_KOD
from .model import Artikel, Block

LAWREVIEW_PARSE_TYPES = ALL_PARSE_TYPES

# The Särtryck's page footer ("sida 37", "SIDA 105", "SIDA 4 SIDA 5" -- the
# double footer names the two pages a short article opens across).
RE_JP_FOOTER = re.compile(r"sida\s+(\d+)", re.I)


# --------------------------------------------------------------------------
# svjt: the document is the article's own web page
# --------------------------------------------------------------------------

def _svjt_body(root, patch_key):
    """The article's paragraphs as blocks, read off the stored page: every
    `<p>` the page sets inside its body, the blank ones left out. The stored
    page is the parse's intermediate, so a correction patch applies here, the
    way rs's Skatteverket page takes one (`rs.parse.page_fields`) --
    normalised to one block element per line first, so a hunk rewrites a
    paragraph rather than the whole document (`patchsource` normalises
    identically)."""
    source, basefile = patch_key
    html = patch.apply(source, basefile, markup.block_lines(
        compress.read_text(page_path(root, basefile))))
    soup = BeautifulSoup(html, "html.parser")
    blocks = []
    for body in soup.select("div.body"):
        for p in body.find_all("p", recursive=False):
            text = normalize_space(p.get_text(" ", strip=True))
            if text:
                blocks.append(Block("stycke", text))
    return blocks


# --------------------------------------------------------------------------
# jp: the document is the issue's PDF
# --------------------------------------------------------------------------

def _jp_body(pages):
    """The article's paragraphs as blocks, read off the Särtryck's pages: the
    running text of every page, reflowed into paragraphs. Nothing is removed
    afterwards -- the Särtryck's cover page and the running heads state
    nothing the scanner needs, and a page mark the scanner does not read as a
    citation stays harmlessly in the text, the way `guidance.parse`'s reader
    leaves what its rules do not name. (The stored PDF is the parse's
    intermediate; the correction patch applies to its conversion in
    `pdf_pages`, the way the other PDF-bodied sources take one.)"""
    paras = [p for pageno, lines in pages
             for p in page_paragraphs(lines, None, pageno)]
    return [Block("stycke", p.text) for p in paras]


def _jp_start_page(pages):
    """The issue page the article opens on ("JP 2009 s. 37"), read off the
    Särtryck's page footer: the older issues print it on the article's first
    page, the newer ones on the page after their cover leaf, so the last line
    of the first two pages is consulted in order. Every Särtryck on record
    prints a footer there, so one that is missing is a layout change, not a
    variant to absorb."""
    for _pageno, lines in pages[:2]:
        m = RE_JP_FOOTER.search(lines[-1].text)
        if m:
            return m.group(1)
    raise ValueError("no 'sida N' footer on the jp Särtryck's first pages")


def parse(basefile, root):
    """One basefile ("svjt/2026-104", "jp/2025-01-03") -> artifact dict, body
    citation-scanned."""
    journal = basefile.split("/", 1)[0]
    assert journal in BY_KOD, "no such journal %r" % journal
    record = compress.read_json(record_path(root, journal, basefile))
    patch_key = ("lawreview", basefile)
    sida = None
    if BY_KOD[journal].html_document:
        body = _svjt_body(root, patch_key)
    else:
        pages = list(pdf_pages(pdf_path(root, patch_key[1]), patch_key))
        body = _jp_body(pages)
        sida = _jp_start_page(pages)
    return Artikel(
        journal=journal, year=record["year"], issue=record["issue"],
        seq=record.get("seq"), kind=record.get("kind"),
        titel=record["titel"], fattare=record.get("fattare"),
        sammanfattning=record.get("sammanfattning"),
        body=body, sida=sida,
        source_url=record.get("source_url"),
        document_url=record.get("document_url"),
        # the year is the one date the publisher states; approximate_date
        # fills the middle of it, the way rs dates its documents by year
    ).to_artifact(sfs_parser(basefile, LAWREVIEW_PARSE_TYPES,
                             written=approximate_date(record["year"])))