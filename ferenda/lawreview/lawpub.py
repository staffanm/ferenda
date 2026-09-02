"""The lawpub scope: the LAWPUB platform's open-access articles, mined as
PDFs -- lawreview's tenth scope, and the one that is a platform rather than
a journal.

LAWPUB (lawpub.se) hosts the open-access articles of several publishers
(`_PUBLISHERS` below). They are all in a single paginated listing
(``POST /sv/sections/getsectionpage``), which the platform sorts newest-first
on request (``sortby=0&sortdirection=1``) and ends with an ``EOF`` page; an
item is downloadable only when it carries the open-access mark (``<svg
class="icon open">``) -- a locked item (``<svg class="icon" title="Stängd">``)
is skipped. The walk runs newest-first and stops on the harvest watermark's
caught-up gate the way the svjt scope does: once the newest listing pages
hold only articles that are already on disk, the run stops there and never
re-walks the platform's full depth.

The scope is not a `journals.Journal`: its coordinates are the platform's
(a per-article publisher read off the item's icon, an edition name, a
month-year and a page span off the "Publicerad i" line), not a journal's
year/issue/sequence, so it keeps its own record shape, model and parse in
this module, and `parse.parse` hands a ``lawpub/…`` basefile here. Two of
the platform's publishers overlap journals this source already harvests on
their own hosts (Förvaltningsrättslig tidskrift, the `ft` scope, and
Stockholm IP Law Review, the `siplr` scope) -- the same underlying article
can arrive under two basefiles, and both lines then show on the shared
"Artiklar" rail row.

The document is the article's own PDF. The platform serves it from
``/utils/downloadsection/<sectionid>``; a listing item whose handle is a
number carries that id outright, while one whose handle is a DOI keeps the
id private until its article page is read (``data-sectionid``), so a DOI
item costs that one extra fetch only when its PDF is being fetched. The
basefile is the platform's own handle behind the scope prefix, slugified --
a section number or a DOI -- so the two forms coexist on disk
(``lawpub/880`` and ``lawpub/10.53292-c42237cc.fe896fd9``).

The identifier takes the article-citation form the "Publicerad i" line
carries -- the publisher's abbreviation, the year, and the opening page
(``FT 2015 s. 551``), the edition standing in for the page where the line
states none, and no year at all where the listing states no date. As in the
journal scopes, an article is a fixed historical publication, and the text
is mining text: every paragraph in order, no structure read off it, only
the citation scan to survive.
"""

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from bs4 import BeautifulSoup

from ..lib import compress, net
from ..lib.artifact import prune, scanned_nodes
from ..lib.harvest import (
    HarvestWatermark,
    document_item_key,
    pdf_path,
    resolve_document,
    verify_pdf,
    walk,
)
from ..lib.lagrum import ALL_PARSE_TYPES, sfs_parser
from ..lib.pdftext import pdf_paragraph_texts
from ..lib.util import MONTHS, basefile_slug, normalize_space, record_path
from .model import Block, lawreview_uri

__all__ = ["lawpub_sync", "parse", "Artikel", "BY_ICON", "kod_from_icon"]

LAWPUB_BASE = "https://www.lawpub.se"
LISTING = LAWPUB_BASE + "/sv/sections/getsectionpage"

# the article page's own section id, the platform's download key
RE_SECTION_ID = re.compile(r'data-sectionid="(\d+)"')
# the "Publicerad i" line's month-year, after its comma ("..., juli 2026"):
# the month alternation is lib.util.MONTHS itself, so a non-month word before
# a year ("hösten 2026") simply states no date rather than needing a branch
RE_PUBLISHED = re.compile(r",\s*(%s)\s+(\d{4})" % "|".join(MONTHS),
                          re.I)
# the same line's page span ("s. 347–356", "s. 347")
RE_PAGES = re.compile(r"s\.\s*(\d+)(?:\s*[-–]\s*(\d+))?")
# the stem the icon file carries ("ft-icon.svg" -> "ft", "siplr_icon.svg"
# -> "siplr"): everything before the first underscore or dash, lower-cased
RE_ICON_STEM = re.compile(r"[a-z][a-z0-9]*", re.I)


# --------------------------------------------------------------------------
# the publishers (utgivare) whose open articles the platform hosts, as data
# --------------------------------------------------------------------------

def kod_from_icon(icon):
    """The publisher's identifier abbreviation, off its icon file's stem
    (``ft-icon.svg`` -> ``FT``, ``siplr_icon.svg`` -> ``SIPLR``). The item
    carries the icon's full path, so the stem is read off the file name alone
    -- a path taken as a whole would name its first directory, not the icon."""
    assert isinstance(icon, str) and icon, "a publisher icon is a path: %r" % (icon,)
    name = icon.rsplit("/", 1)[-1]
    m = RE_ICON_STEM.search(name)
    assert m is not None, "no publisher stem in the icon's name: %r" % (icon,)
    return m.group(0).upper()


@dataclass(frozen=True)
class Publisher:
    kod: str          # the identifier's leading abbreviation ("FT", "SIPLR", ...)
    utgivare: str     # the platform's /utgivare/<n> number
    namn: str         # the publisher's full name
    icon: str         # /utils/media/<...>.svg, the item's publisher icon


_PUBLISHERS = (
    # nst -- Nordisk socialrättslig tidskrift, on the platform since 2010
    Publisher("NST", "3", "Nordisk socialrättslig tidskrift",
              "/utils/media/nst-icon.svg"),
    # ft -- Förvaltningsrättslig tidskrift; overlaps the `ft` scope
    # (forvaltningsrattslig.org), the same articles on two hosts
    Publisher("FT", "4", "Förvaltningsrättslig tidskrift",
              "/utils/media/ft-icon.svg"),
    # ert -- Europarättslig tidskrift
    Publisher("ERT", "6", "Europarättslig tidskrift",
              "/utils/media/ert-icon.svg"),
    # iri -- the Swedish Law and Informatics Research Institute
    Publisher("IRI", "7", "The Swedish Law and Informatics Research Institute",
              "/utils/media/iri-icon.svg"),
    # siplr -- Stockholm IP Law Review; overlaps the `siplr` scope
    # (stockholmiplawreview.com), the same articles on two hosts
    Publisher("SIPLR", "9", "Stockholm IP Law Review",
              "/utils/media/siplr_icon.svg"),
    # sjf -- Stiftelsen Juridisk Fakultetslitteratur (Dataskyddet and its
    # companion volumes)
    Publisher("SJF", "10", "Stiftelsen Juridisk Fakultetslitteratur",
              "/utils/media/sjf_icon.svg"),
    # sisl -- Scandinavian studies in law
    Publisher("SSIL", "11", "Scandinavian studies in law",
              "/utils/media/sisl-icon.svg"),
)

# keyed by the icon's stem, the form the item carries (its publisher icon src)
BY_ICON = {kod_from_icon(p.icon).lower(): p for p in _PUBLISHERS}


# --------------------------------------------------------------------------
# the listing
# --------------------------------------------------------------------------

def _listing_page(session, page_index, delay):
    """One listing page's raw HTML: the platform's open items, newest first
    (its ``sortby=0&sortdirection=1``), in blocks of 500."""
    html = net.request(session, "POST", LISTING, data={
        "pageIndex": str(page_index), "pageSize": "500",
        "sortby": "0", "sortdirection": "1"}).text
    time.sleep(delay)
    return html


def _open_records(html):
    """The page's open items as records: the ones marked ``<svg class="icon
    open">``, the locked ones (``title="Stängd"``) left out -- they have no
    PDF the platform will serve."""
    soup = BeautifulSoup(html, "html.parser")
    return [_record(it) for it in soup.select("div.section-item")
            if it.select_one("svg.icon.open") is not None]


def _records(session, delay):
    """The listing's open records, newest first, page by page, until the
    platform serves its ``EOF`` page. A lazy generator: a walk that stops
    short of the depth abandons it and never fetches the pages past the stop."""
    page_index = 0
    while True:
        html = _listing_page(session, page_index, delay)
        eof = "<span>EOF</span>" in html
        yield from _open_records(html)
        if eof:
            return
        page_index += 1


# --------------------------------------------------------------------------
# one listing item -> its harvest record
# --------------------------------------------------------------------------

def _authors(it):
    """The item's author names, birth years stripped (they sit in a
    ``<small>`` beside each name); one string, or None where the item names no
    author at all."""
    names = []
    for a in it.select("div.authors a"):
        small = a.find("small")
        if small is not None:
            small.extract()
        name = normalize_space(a.get_text(" ", strip=True))
        if name:
            names.append(name)
    return ", ".join(names) if names else None


def _edition_fields(it):
    """The "Publicerad i" line's three facts -- the edition's name, its
    month-year (widened to a representative day) and its page span -- read off
    the one bookinfo line that states a publication, not its keywords line."""
    utgava = date = sida = None
    for bi in it.select("p.bookinfo"):
        text = bi.get_text(" ", strip=True)
        if "Publicerad i" not in text:
            continue
        a = bi.find("a")
        if a is not None:
            utgava = normalize_space(a.get_text(" ", strip=True))
        dm = RE_PUBLISHED.search(text)
        if dm is not None:
            date = "%s-%02d-15" % (dm.group(2), MONTHS[dm.group(1).lower()])
        pm = RE_PAGES.search(text)
        if pm is not None:
            sida = pm.group(1) if not pm.group(2) \
                else "%s-%s" % (pm.group(1), pm.group(2))
        break
    return utgava, date, sida


def _record(it):
    """One open listing item -> its harvest record, the stable thing the store
    and the watermark key on. The platform's handle for the item -- a section
    number or a DOI -- is its basefile behind the scope prefix; a number is
    also its download key, a DOI keeps that key private until its article page
    is read at download time."""
    a = it.select_one("h2 a")
    assert a is not None and a.get("href"), \
        "a section item names no article: %r" % it
    href = a["href"]
    handle = href.split("/artikel/", 1)[1]
    icon = it.select_one("img.publisher-icon")
    assert icon is not None, \
        "a section item names no publisher: %r" % a.get_text(strip=True)
    pub = BY_ICON[kod_from_icon(icon["src"]).lower()]

    doi = None
    d = it.select_one("p.doi a")
    if d is not None and d.get("href"):
        doi = d["href"].split("https://doi.org/", 1)[1]
    if handle.isdigit():
        sectionid = int(handle)
        document_url = "%s/utils/downloadsection/%d" % (LAWPUB_BASE, sectionid)
    else:
        sectionid, document_url = None, None
    utgava, date, sida = _edition_fields(it)
    return {
        "basefile": "lawpub/%s" % basefile_slug(handle),
        "journal": "lawpub",
        "kind": "numeric" if sectionid is not None else "doi",
        "sectionid": sectionid,
        "doi": doi,
        "utgivare": pub.kod,
        "utgivare_namn": pub.namn,
        "utgava": utgava,
        "date": date,
        "sida": sida,
        "titel": normalize_space(a.get_text(" ", strip=True)),
        "fattare": _authors(it),
        "source_url": LAWPUB_BASE + href,
        "document_url": document_url,
        "open": True,
    }


# --------------------------------------------------------------------------
# the document: the article's own PDF
# --------------------------------------------------------------------------

def _fetch_pdf(session, record, delay):
    """The article's PDF bytes. A number-carrying item downloads straight from
    its section id; a DOI item's id lives on its article page, so that page is
    read first (and only when its PDF is actually being fetched)."""
    sectionid = record["sectionid"]
    if sectionid is None:
        page = net.request(session, "GET", record["source_url"]).text
        time.sleep(delay)
        m = RE_SECTION_ID.search(page)
        assert m is not None, "no section id on %s" % record["source_url"]
        sectionid = m.group(1)
    data = net.request(
        session, "GET", "%s/utils/downloadsection/%s" % (LAWPUB_BASE, sectionid)
    ).content
    time.sleep(delay)
    return data


def _store(session, root, record, full, delay):
    """Fetch the article's PDF (when it is not on disk or the run is a full
    re-verification) and store the record beside it. True when it wrote
    something new or changed. `_fetch_pdf` paces its own requests, so the
    shared resolve adds no delay of its own."""
    return resolve_document(
        record, record_path(root, "lawpub", record["basefile"]),
        pdf_path(root, record["basefile"]),
        lambda: _fetch_pdf(session, record, delay),
        verify_pdf, full=full)


# --------------------------------------------------------------------------
# the download entry point
# --------------------------------------------------------------------------

def lawpub_sync(root, full=False, only=None, limit=None, delay=0.5):
    """The platform's open-access articles, newest first, down to the
    watermark. `--only lawpub/880` names one article, which is then the only
    document the run stores, the watermark untouched."""
    session = net.make_session(net.BROWSER_UA)
    if only:
        record = _find_record(session, only, delay)
        written = _store(session, root, record, full, delay)
        return 1, int(written)
    watermark = HarvestWatermark(
        Path(root) / "lawpub" / ".watermark.json",
        lookahead_limit=5, safety_days=30)

    def item_key(record):
        return document_item_key(
            record, record_path(root, "lawpub", record["basefile"]),
            pdf_path(root, record["basefile"]),
            # the month-year the listing states, widened to a day
            date=record["date"])

    result = walk(
        _records(session, delay),
        resolve=lambda record: _store(session, root, record, full, delay),
        item_key=item_key, watermark=watermark,
        full=full, limit=limit, only=None, scope="lawpub")
    return result.seen, result.new


def _find_record(session, only, delay):
    """The one listing record `only` names, walking the listing until it is
    found. A name the listing carries no record for is a typo or an article
    that has gone, and the run says which rather than store nothing."""
    for record in _records(session, delay):
        if record["basefile"] == only:
            return record
    raise ValueError("the lawpub listing carries no article %s" % only)


# --------------------------------------------------------------------------
# the model and the parse
# --------------------------------------------------------------------------

def start_page(sida):
    """The article's opening page, off the page span the listing states
    ("551-582" -> "551"); None where the line names no page at all."""
    if not sida:
        return None
    m = re.match(r"(\d+)", sida.strip())
    return m.group(1) if m else None


@dataclass
class Artikel:
    basefile: str                      # "lawpub/880" or "lawpub/10.53292-..."
    titel: str
    utgivare: str                      # the publisher's kod ("FT", "SIPLR", ...)
    utgivare_namn: str                 # the publisher's full name
    utgava: str | None = None          # the edition's name, as the line states it
    fattare: str | None = None         # the author(s), as the listing states them
    date: str | None = None            # the month-year the line states, widened to a day
    sida: str | None = None            # the page span ("551-582") or a single page
    body: list[Block] = field(default_factory=list)   # the mining text
    source_url: str | None = None      # the platform's page for the article
    document_url: str | None = None    # the PDF the platform serves it as

    @property
    def uri(self):
        return lawreview_uri("lawpub", self.basefile.split("/", 1)[1])

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
        return self.utgivare_namn

    def to_artifact(self, scanner):
        """The JSON artifact: the shared node convention (`structure` of stycke
        nodes with inline-run text), every text scanned for citations -- which
        is what puts an article's references on the context rails of the
        documents it names, the way the journal scopes' articles do. The
        catalog's date projection expects a 10-char ISO date: the month-year
        the line states arrives already widened to a representative day (the
        download's "-15"), and an article the listing states no date for
        carries none -- the catalog lists undated documents last and prints
        no date, and an invented date would missort them. `journal` is the
        scope, the axis the catalog's lawreview kind projection reads."""
        return prune({
            "uri": self.uri, "type": "juridisk_artikel",
            "journal": "lawpub",
            "date": self.date,
            "identifier": self.identifier,
            "metadata": prune({"title": self.titel,
                               "publisher": self.publisher,
                               "utgivare": self.utgivare,
                               "utgava": self.utgava,
                               "fattare": self.fattare,
                               "sida": self.sida}),
            "structure": scanned_nodes(self.body, scanner),
            "source_url": self.source_url,
            "document_url": self.document_url})


def parse(basefile, root):
    """One basefile ("lawpub/880", "lawpub/10.53292-c42237cc.fe896fd9") ->
    artifact dict, body citation-scanned. `lawreview.parse.parse` dispatches
    the scope's basefiles here: the record shape is the platform's, not a
    journal's."""
    record = compress.read_json(record_path(root, "lawpub", basefile))
    pdf = pdf_path(root, basefile)
    body = [Block("stycke", text)
            for text in pdf_paragraph_texts(pdf, ("lawreview", basefile))]
    return Artikel(
        basefile=basefile,
        titel=record["titel"],
        utgivare=record["utgivare"],
        utgivare_namn=record["utgivare_namn"],
        utgava=record["utgava"],
        fattare=record["fattare"],
        date=record["date"],
        sida=record["sida"],
        body=body,
        source_url=record["source_url"],
        document_url=record["document_url"],
    ).to_artifact(sfs_parser(basefile, ALL_PARSE_TYPES,
                             # an undated article scans under today's law
                             # (sfs_parser's own default for no date)
                             written=record["date"]))
