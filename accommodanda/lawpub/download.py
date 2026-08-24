"""The lawpub download phase: the platform's open-access articles, mined as
PDFs.

LAWPUB is one platform that hosts the open-access articles of several
publishers (`publishers.py`). They are all in a single paginated listing
(`POST /sv/sections/getsectionpage`), which the platform sorts newest-first on
request (``sortby=0&sortdirection=1``) and ends with an ``EOF`` page; an item
is downloadable only when it carries the open-access mark (``<svg
class="icon open">``) -- a locked item (``<svg class="icon" title="Stängd">``)
is skipped. The walk runs newest-first and stops on the harvest watermark's
caught-up gate the way `lawreview`'s svjt scope does: once the newest listing
pages hold only articles that are already on disk, the run stops there and
never re-walks the platform's full depth.

The document is the article's own PDF. The platform serves it from
``/utils/downloadsection/<sectionid>``; a listing item whose handle is a
number carries that id outright, while one whose handle is a DOI keeps the id
private until its article page is read (``data-sectionid``), so a DOI item
costs that one extra fetch only when its PDF is being fetched. The basefile is
the platform's own handle, slugified -- a section number or a DOI -- so the two
forms coexist on disk (``880`` and ``10.53292-c42237cc.fe896fd9``).
"""

import re
import time
from pathlib import Path

from bs4 import BeautifulSoup

from ..lib import compress, net
from ..lib.harvest import (
    HarvestWatermark,
    document_item_key,
    resolve_document,
    verify_pdf,
    walk,
)
from ..lib.util import MONTHS, basefile_slug, normalize_space
from .publishers import BY_ICON, kod_from_icon

__all__ = ["sync", "list_basefiles", "record_path", "pdf_path", "ORIGIN"]

LAWPUB_BASE = "https://www.lawpub.se"
LISTING = LAWPUB_BASE + "/sv/sections/getsectionpage"
ORIGIN = LAWPUB_BASE


# the flat-source paths: one record and one PDF each, named for the item's own
# handle -- a section number or a DOI -- beside this source's store root, the
# way the other flat sources (coe, icrc) file a single number's record and text

def record_path(root, basefile):
    return Path(root) / (basefile_slug(basefile) + ".json")


def pdf_path(root, basefile):
    return Path(root) / (basefile_slug(basefile) + ".pdf")

# the article page's own section id, the platform's download key
RE_SECTION_ID = re.compile(r'data-sectionid="(\d+)"')
# the "Publicerad i" line's month-year, after its comma ("..., juli 2026"):
# the month alternation is lib.util.MONTHS itself, so a non-month word before
# a year ("hösten 2026") simply states no date rather than needing a branch
RE_PUBLISHED = re.compile(r",\s*(%s)\s+(\d{4})" % "|".join(MONTHS),
                          re.I)
# the same line's page span ("s. 347–356", "s. 347")
RE_PAGES = re.compile(r"s\.\s*(\d+)(?:\s*[-–]\s*(\d+))?")


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
    return [rec for it in soup.select("div.section-item")
            if it.select_one("svg.icon.open") is not None
            for rec in (_record(it),)]


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
    number or a DOI -- is its basefile; a number is also its download key, a
    DOI keeps that key private until its article page is read at download time.
    """
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
        "basefile": basefile_slug(handle),
        "source": "lawpub",
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
        record, record_path(root, record["basefile"]),
        pdf_path(root, record["basefile"]),
        lambda: _fetch_pdf(session, record, delay),
        verify_pdf, full=full)


# --------------------------------------------------------------------------
# the entry point
# --------------------------------------------------------------------------

def sync(root, full=False, only=None, limit=None, delay=0.5):
    """The platform's open-access articles, newest first, down to the watermark.
    `--only 880` names one article, which is then the only document the run
    stores, the watermark untouched."""
    session = net.make_session(net.BROWSER_UA)
    if only:
        record = _find_record(session, only, delay)
        written = _store(session, root, record, full, delay)
        return 1, int(written)
    watermark = HarvestWatermark(
        Path(root) / ".watermark.json",
        lookahead_limit=5, safety_days=30)

    def item_key(record):
        return document_item_key(
            record, record_path(root, record["basefile"]),
            pdf_path(root, record["basefile"]),
            # the month-year the listing states, widened to a day
            date=record.get("date"))

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


def list_basefiles(root):
    """Every lawpub article on disk, read from its record's file name -- the
    platform's own handle for it, a section number or a DOI. Flat store: the
    record and the PDF sit side by side under this source's root, the dotfiles
    (the watermark) excluded.
    """
    return compress.list_stems(root)