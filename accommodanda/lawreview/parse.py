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

How the text arrives is data in `journals.py`, not a branch per journal: a
page-bodied article (`html_document`) is read off its stored page by the
reader its `page_reader` names, and a PDF-bodied one by `_pdf_body`, the way
`guidance.parse` reads its PDFs. What the record carries is the listing's own
statement -- title, author, coordinates -- and the document adds the text
plus whatever `sida_kalla` says the document states: the jp Särtryck's
footer ("footer"), the siplr article's `-- N --` hand ("footer"), the ft
table-of-contents line its first leaf prints ("head"), or nothing -- the nmt,
njel and urt listings already put the page in the record ("record"), and
svjt, euar and lod state no page at all.

A record that names no document (a print-only article the nmt, njel and urt
listings list, and the siplr heading that sets no article PDF) carries no
text to mine: its artifact's structure stays empty rather than the parse
reading a file that is not on disk.

That holds for the nine journal scopes. The source's tenth scope is the
lawpub platform, whose record states the platform's coordinates rather than a
journal's, so it keeps its own record shape, model and parse in `lawpub.py`
and `parse` hands a `lawpub/...` basefile straight there. The one branch is
on the scope, not on the journal.
"""

import re

from bs4 import BeautifulSoup

from ..lib import compress, markup, patch
from ..lib.harvest import page_path, pdf_path
from ..lib.lagrum import ALL_PARSE_TYPES, sfs_parser
from ..lib.pdftext import page_paragraphs, pdf_pages
from ..lib.util import (
    MONTHS,
    approximate_date,
    normalize_hints,
    normalize_space,
    record_path,
)
from . import lawpub
from .journals import BY_KOD
from .model import Artikel, Block

LAWREVIEW_PARSE_TYPES = ALL_PARSE_TYPES

# The Särtryck's page footer ("sida 37", "SIDA 105", "SIDA 4 SIDA 5" -- the
# double footer names the two pages a short article opens across).
RE_JP_FOOTER = re.compile(r"sida\s+(\d+)", re.I)

# The siplr article's page footer ("– 5 --", "– 1 2 --"): the page between two
# en dashes, the journal's digits apart in its older conversions. The footer
# sits in the page's last lines, its running head ("S T O C K H O L M ...")
# set after it, so the last four lines of a leaf are consulted.
RE_SIPLR_FOOTER = re.compile(r"–\s*(\d+(?:\s*\d+)*)\s*–")

# The ft table-of-contents line: a leader (… or a run of dots), then the
# article's page -- on its own line, where the conversion sets it there.
RE_FT_LEADER = re.compile(r"(?:…|\.{2,})\s*(\d{1,3})\s*$")
RE_FT_LEADER_END = re.compile(r"(?:…|\.{2,})\s*$")

# The lod article's issue line ("Utgave: 3/2022" over "2022-10-28"): the
# day is the issue's publication date, ISO on the page itself.
RE_LOD_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")

# The lod author line's name links: each author's name links the journal's
# forfattere page, and nothing else in the line does (an e-mail link is a
# mailto).
RE_LOD_FORFATTER = re.compile(r"/page/forfattere")

# The euar item's publication line, Swedish month name and day first
# ("Publicerad: juni 22, 2026").
RE_EUAR_PUBLISHED = re.compile(
    r"Publicerad:\s*([a-zåäö]+)\s+(\d{1,2})\s*,\s*(\d{4})", re.I)

# the shared Swedish month table (lib.util.MONTHS), keyed the way the euar
# line spells the month
_EUAR_MONTHS = {namn: "%02d" % i for namn, i in MONTHS.items()}


# --------------------------------------------------------------------------
# page bodies: the document is the article's own web page
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


def _euar_date(soup):
    """The item's publication day, the sidebar's "Publicerad" line widened
    to ISO (the Swedish month name is the line's own form). The line is on
    every item page, 1998 and all, so one that is missing is a layout change,
    not a variant to absorb."""
    for p in soup.find_all("p"):
        m = RE_EUAR_PUBLISHED.search(p.get_text(" ", strip=True))
        if m:
            month = _EUAR_MONTHS.get(m.group(1).lower())
            if month is None:
                raise ValueError("unknown euar month %r" % m.group(1))
            return "%s-%s-%02d" % (m.group(3), month, int(m.group(2)))
    raise ValueError("no 'Publicerad' line on the euar page")


def _euar_body(root, patch_key):
    """The item's paragraphs, its author and its publication day, read off
    the stored page. The item sets its running text in
    `div.post-single-content`, every era since 1998 (the older items set
    their paragraphs in wrapper divs, the newer ones as plain children -- a
    recursive `<p>` read takes both); the "Läs också" cross-links and the
    footer sit outside that container, so they never enter the text. The
    item's lead sentence is set bold, and so is its author's name, the
    last wholly-bold paragraph of the item -- the bullet the lead sets with
    is what keeps the two apart. The patch applies to the stored page, the
    way `_svjt_body` takes one."""
    source, basefile = patch_key
    html = patch.apply(source, basefile, markup.block_lines(
        compress.read_text(page_path(root, basefile))))
    soup = BeautifulSoup(html, "html.parser")
    content = soup.select_one("div.post-single-content")
    if content is None:
        raise ValueError("no post-single-content on the euar page -- "
                         "the layout moved")
    paras = []
    for p in content.find_all("p"):
        text = normalize_space(p.get_text(" ", strip=True))
        if text:
            paras.append((p, text))
    author_p = None
    for p, text in paras:
        if text.startswith("•"):
            continue
        strongs = p.find_all("strong")
        if len(strongs) == 1 and \
                normalize_space(strongs[0].get_text(" ", strip=True)) == text:
            author_p = p
    fattare = None
    blocks = []
    for p, text in paras:
        if p is author_p:
            fattare = text
            continue
        blocks.append(Block("stycke", text))
    return blocks, fattare, _euar_date(soup)


def _lod_body(root, patch_key):
    """The article's paragraphs, its author and the issue's publication
    day, read off the stored page. The article sets its running text in
    `section#maincolwidth`; the table of contents and the issue line sit
    outside it, so they never enter the text. The author line
    (`div.authorinfo`, "Av Tue Goldschmieding, partner i Gorrissen
    Federspiel") links each author's name to the journal's forfattere page,
    and the names are the author -- a line that links no name states none
    (the journal has printed "Av forfatter" with the name lost), and the
    record then carries no author, the way the model allows. The issue
    line (`div.issueinfo`) states the issue's publication day in ISO on
    the page itself. The patch applies to the stored page, the way
    `_svjt_body` takes one."""
    source, basefile = patch_key
    html = patch.apply(source, basefile, markup.block_lines(
        compress.read_text(page_path(root, basefile))))
    soup = BeautifulSoup(html, "html.parser")
    content = soup.select_one("section#maincolwidth")
    if content is None:
        raise ValueError("no maincolwidth section on the lod page -- "
                         "the layout moved")
    fattare = None
    author = content.select_one("div.authorinfo")
    if author is not None:
        names = [normalize_hints(a.get_text(" ", strip=True))
                 for a in author.find_all("a", href=RE_LOD_FORFATTER)]
        fattare = ", ".join(n for n in names if n) or None
        author.extract()
    blocks = []
    # normalize_hints, not normalize_space: Lovdata typesets its running
    # text with soft hyphens ("produkt\xadsikkerhed"), and one inside a
    # citation would hide it from the scanner
    for p in content.find_all("p"):
        text = normalize_hints(p.get_text(" ", strip=True))
        if text:
            blocks.append(Block("stycke", text))
    info = soup.select_one("div.issueinfo")
    if info is None:
        raise ValueError("no issue line on the lod page -- the layout moved")
    m = RE_LOD_DATE.search(info.get_text(" ", strip=True))
    if m is None:
        raise ValueError("no date in the lod issue line %r"
                         % info.get_text(" ", strip=True))
    return blocks, fattare, m.group(1)


# --------------------------------------------------------------------------
# PDF bodies: the document is the article's own PDF
# --------------------------------------------------------------------------

def _pdf_body(pages):
    """The article's paragraphs as blocks, read off the PDF's pages: the
    running text of every page, reflowed into paragraphs. Nothing is removed
    afterwards -- a cover leaf's and a running head's text states nothing the
    scanner needs, and a page mark the scanner does not read as a citation
    stays harmlessly in the text, the way `guidance.parse`'s reader leaves
    what its rules do not name. (The stored PDF is the parse's intermediate;
    the correction patch applies to its conversion in `pdf_pages`, the way
    the other PDF-bodied sources take one.)"""
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


def _siplr_start_page(pages):
    """The issue page the article opens on ("SIPLR 2025 s. 5"), read off the
    article's `-- N --` footer: the journal prints it on the article's first
    leaf in every issue on record, but it has published one scanned article
    that prints no footer at all, so a missing footer takes the article's
    place in the issue in the identifier, not a layout change."""
    for _pageno, lines in pages[:2]:
        for line in lines[-4:]:
            m = RE_SIPLR_FOOTER.search(line.text)
            if m:
                return m.group(1).replace(" ", "")
    return None


def _ft_start_page(pages):
    """The issue page the article opens on ("FT 2025 s. 23"), read off the
    first leaf's running table of contents: the issue's contents print across
    the top of the leaf, and the article's own line ends in a leader and its
    page (the conversion sets the page on its own line). A line that leads
    nowhere states no page -- the identifier takes its place in the issue
    instead -- and a first leaf that sets no table of contents at all is
    the same."""
    lines = pages[0][1]
    for i, line in enumerate(lines):
        text = line.text.strip()
        if not text:
            continue
        m = RE_FT_LEADER.search(text)
        if m:
            return m.group(1)
        if RE_FT_LEADER_END.search(text):
            for after in lines[i + 1:]:
                t = after.text.strip()
                if t:
                    return t if t.isdigit() and len(t) <= 3 else None
        return None
    return None


# --------------------------------------------------------------------------
# the entry point
# --------------------------------------------------------------------------

def parse(basefile, root):
    """One basefile ("svjt/2026-104", "jp/2025-01-03", "urt/2026-1-147",
    "lawpub/880") -> artifact dict, body citation-scanned. The lawpub scope
    is a platform, not a journal (its record shape carries the platform's
    coordinates), so its basefiles go to its own module's parse."""
    journal = basefile.split("/", 1)[0]
    if journal == "lawpub":
        return lawpub.parse(basefile, root)
    assert journal in BY_KOD, "no such journal %r" % journal
    conf = BY_KOD[journal]
    record = compress.read_json(record_path(root, journal, basefile))
    patch_key = ("lawreview", basefile)
    fattare = record.get("fattare")
    date = None
    sida = None
    if conf.html_document:
        if conf.page_reader == "svjt":
            body = _svjt_body(root, patch_key)
        elif conf.page_reader == "euar":
            body, fattare, date = _euar_body(root, patch_key)
        else:
            assert conf.page_reader == "lod", conf.page_reader
            body, fattare, date = _lod_body(root, patch_key)
    else:
        sida_kalla = conf.sida_kalla
        if record.get("document_url") is None:
            # a print-only article: the listing names it and no PDF exists,
            # so there is nothing to mine. The siplr heading that sets no
            # article PDF is the same: its page comes off a PDF it has, so
            # that reader is the one that may run without a document, the
            # article's place in the issue taking the page's turn
            if sida_kalla in ("footer", "head") and journal != "siplr":
                raise ValueError(
                    "%s %s names no document but its page must come from one"
                    % (journal, basefile))
            body = []
            # .get(): a siplr print-only record carries no sida key at all;
            # the "record" journals' records always carry one (may be None)
            sida = record.get("sida")
        else:
            pages = list(pdf_pages(pdf_path(root, patch_key[1]), patch_key))
            body = _pdf_body(pages)
            if sida_kalla == "footer":
                sida = (_siplr_start_page(pages) if journal == "siplr"
                        else _jp_start_page(pages))
            elif sida_kalla == "head":
                sida = _ft_start_page(pages)
            elif sida_kalla == "record":
                # the listing's own statement, nmt, njel and urt alike --
                # always written by their walkers (None where the listing
                # states no page), so a missing key is a walker regression
                sida = record["sida"]
    return Artikel(
        journal=journal, year=record["year"], issue=record["issue"],
        seq=record.get("seq"), kind=record.get("kind"),
        titel=record["titel"], fattare=fattare,
        sammanfattning=record.get("sammanfattning"),
        body=body, sida=sida, date=date,
        source_url=record.get("source_url"),
        document_url=record.get("document_url"),
        # the year is the one date the publisher states for every journal
        # but euar; approximate_date fills the middle of it
    ).to_artifact(sfs_parser(basefile, LAWREVIEW_PARSE_TYPES,
                             written=date or approximate_date(record["year"])))