"""Statskontoret's STKFA and the predecessor ESVFA it took over from ESV.

This source reads both series as one current *EA-regelverket* on
forum.statskontoret.se rather than from their separately published PDFs: a
small tree under
``/ea-regelverket/``, one leaf page per förordning, each carrying that
förordning's föreskrifter and allmänna råd as typed HTML sections
(``div.foreskrifter`` / ``div.allmanna-rad``, 245 of them on the largest page).
A leaf page *is* the consolidated regulation, and the heading inside its
``div.regelverk-page__box`` names which one ("… föreskrifter och allmänna råd
(ESVFA 2022:1) om årsredovisning och budgetunderlag").

So the page has to be fetched before its identity is known, and the section
pages between the root and the leaves name no regulation of their own. The
enumerate therefore walks the whole ``/ea-regelverket/`` tree and carries each
leaf's sections on the ``DocRef``; :func:`resolve` writes them without fetching
again. The tree is around twenty pages, walked through :func:`lib.net.request`
like every other agency's listing, so the host's robots.txt Crawl-delay and the
source's own pacing apply (rule:respect-politeness).

ESVFA closed when Statskontoret took over ESV's rulemaking, but its documents
are still *current* -- they are the EA-regelverket's own text -- so they arrive
through this one scope and are filed under their own samling (``DocRef.fs``).
"""

import re
from collections import deque
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from ..lib import compress
from ..lib.errors import UpstreamChanged
from ..lib.harvest import write_record
from ..lib.net import request
from ..lib.util import basefile_slug as slug
from ..lib.util import record_path
from .harvest import DocRef, newest_first

# the identifying heading's designation, either series. Printed in parentheses
# after the type words, the way a föreskrift's own title always prints it.
RE_DESIGNATION = re.compile(r"\b(ESVFA|STKFA)\s+(\d{4}):(\d+)\b")
RE_KAPITEL = re.compile(r"^\d+\s*(?:[a-z]\s*)?kap\.", re.I)
RE_OVERGANG = re.compile(r"^Övergångsbestämmelser")

# the issuing agency per samling. Not read from `agencies.SAMLINGAR`: that module
# imports this one to build the STKFA row, so reading it back here would be a
# cycle.
PUBLISHERS = {"esvfa": "Ekonomistyrningsverket", "stkfa": "Statskontoret"}

# The walk is bounded because it follows links the page itself names: a nav
# change that starts linking the whole site must fail loudly rather than crawl
# it. The live tree is 19 anchors off the root (2026-09), so this is two orders
# of headroom and no kind of routine limit.
PAGE_LIMIT = 500


def _standalone_sections(title_heading):
    """The rule blocks and the shared Forum headings they need.

    Forum interleaves a förordning with its föreskrifter. A heading such as
    ``2 kap. Allmänna bestämmelser`` is therefore a sibling of both texts, not
    part of ``div.foreskrifter``. Keep it when that section contains rules, but
    keep the förordning's own paragraphs out.

    An inner heading that starts with the shared heading already carries the
    standalone form (``Kompensation – föreskrifter till …``). The generic
    ``Föreskrifter`` needs no shared subject heading unless its next heading is
    a chapter marker; ESVFA 2022:1 uses that shape for chapter 11.
    """
    out, pending = [], None
    for element in title_heading.parent.find_all(recursive=False):
        classes = element.get("class", [])
        if element.name == "h2" and "ea-kapitel" in classes:
            pending = element
            continue
        if not (element.name == "div"
                and any(c in classes for c in ("foreskrifter", "allmanna-rad"))):
            continue
        headings = element.find_all(["h2", "h3", "h4"])
        first = " ".join(headings[0].get_text(" ", strip=True).split()) \
            if headings else ""
        if pending is not None and not RE_OVERGANG.match(first):
            outer = " ".join(pending.get_text(" ", strip=True).split())
            generic_before_chapter = (first == "Föreskrifter"
                                      and any(RE_KAPITEL.match(
                                          " ".join(h.get_text(" ", strip=True).split()))
                                              for h in headings[1:]))
            if (RE_KAPITEL.match(outer) or generic_before_chapter
                    or not (first == "Föreskrifter" or first.startswith(outer))):
                out.append(str(pending))
        pending = None
        out.append(str(element))
    return out


def ea_links(html, url, index_url):
    """Every ``/ea-regelverket/`` page `html` links to, absolute and without
    query or fragment, so one page is walked once however it is linked."""
    root = urlsplit(index_url)
    links = []
    for anchor in BeautifulSoup(html, "html.parser").select("a[href]"):
        href = anchor.get("href")
        assert isinstance(href, str)
        parts = urlsplit(urljoin(url, href))
        if (parts.scheme in ("http", "https") and parts.netloc == root.netloc
                and parts.path.startswith(root.path)):
            links.append(urlunsplit((parts.scheme, parts.netloc, parts.path, "", "")))
    return links


def parse_page(html, url):
    """One EA page -> the :class:`DocRef` for the regulation it carries, or None
    where it carries none (the root and its five section pages hang no
    ``regelverk-page__box``; the 13 leaves below them do).

    ``extra`` carries the page's own typed sections and the ``last-modified``
    the page states, which is what tells a later run that a consolidation has
    moved on without taking a new number (``harvest.item_key``)."""
    soup = BeautifulSoup(html, "html.parser")
    box = soup.select_one("div.regelverk-page__box")
    if box is None:
        return None
    for heading in box.find_all(["h1", "h2", "h3"]):
        title = " ".join(heading.get_text(" ", strip=True).split())
        if (m := RE_DESIGNATION.search(title)):
            break
    else:
        return None
    sections = _standalone_sections(heading)
    if not sections:
        raise UpstreamChanged(
            "%s names %s but hangs no föreskrift section" % (url, m.group(0)))
    series, year, number = m.groups()
    fs = series.lower()
    updated = soup.select_one('meta[name="last-modified"]')
    return DocRef(
        basefile="%s/%s:%d" % (fs, year, int(number)),
        identifier="%s %s:%d" % (series, year, int(number)),
        url=url,
        title=title,
        fs=fs,
        extra={"sections": sections,
               "updated_at": updated["content"] if updated else None})


def enumerate_regulations(session, agency):
    """Walk the EA-regelverket tree and list the regulations its pages carry."""
    index_url = agency.index_url
    pending, seen, refs = deque([index_url]), set(), []
    while pending:
        url = pending.popleft()
        if url in seen:
            continue
        seen.add(url)
        assert len(seen) <= PAGE_LIMIT, (
            "%s: the EA-regelverket walk passed %d pages -- the tree is ~20, so "
            "the link filter is following something new" % (agency.fs, PAGE_LIMIT))
        html = request(session, "GET", url).text
        if (ref := parse_page(html, url)) is not None:
            refs.append(ref)
        pending.extend(link for link in ea_links(html, url, index_url)
                       if link not in seen)
    if not refs:
        raise UpstreamChanged(
            "%s: no page under %s names an ESVFA/STKFA regulation"
            % (agency.fs, index_url))
    return newest_first(refs)


def resolve(session, agency, ref, root, delay=0.5, *, log=print, rejects=None):
    """Store the page's typed sections as the regulation's current consolidated
    text. This source does not collect the separately published as-enacted PDF,
    so the record hangs no ``regulation`` file -- the shape
    ``parse_record`` already reads for an agency that publishes only a
    consolidated version.

    The page was fetched by the enumerate -- it had to be, to learn its number --
    and travels on the ``DocRef``, so this makes no request of its own and the
    engine's ``session``/``delay`` go unused."""
    fs = ref.fs or agency.fs
    name = "%s-consolidation.html" % slug(ref.basefile)
    compress.write_download(Path(root) / fs / name,
                            "<main>\n%s\n</main>" % "\n".join(ref.extra["sections"]))
    record = {
        "fs": fs,
        "basefile": ref.basefile,
        "identifier": ref.identifier,
        "title": ref.title,
        "publisher": PUBLISHERS[fs],
        "updated_at": ref.extra["updated_at"],
        "url": ref.url,
        "files": {
            "regulation": None,
            "consolidation": [{"name": name, "url": ref.url}],
            "amendment": [],
            "memo": [],
            "attachment": [],
        },
    }
    write_record(record_path(root, fs, ref.basefile), record)
    return record
