"""Statskontoret's STKFA and the predecessor ESVFA it took over from ESV.

Both series are published as one current *EA-regelverket* on
forum.statskontoret.se, and not as PDFs at all: a small tree of pages under
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

from bs4 import BeautifulSoup, NavigableString

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

# the outer heading the page prints over a chapter of its own text. Two shapes
# carry one: `h2.ea-kapitel` is a chapter ("2 kap. Allmänna bestämmelser om
# årsredovisning"), a plain h2 is the rubrik between two chapters ("Förordningens
# tillämpningsområde"). The typed section under it repeats the heading with a
# scope suffix ("… – föreskrifter till 2 kap. 1 § förordningen"), which is why
# the outer one is what the regulation prints -- the inner one is the repeat.
RE_EA_KAPITEL = re.compile(r"^\d+\s+kap\.")
# a typed section whose heading is only its type word ("Föreskrifter", "Allmänna
# råd") states no topic of its own; the content opens straight into its rubriker
RE_BARE_TYPE_HEADING = re.compile(
    r"^(?:Föreskrifter|Allmänna\s+råd)(?:\s+till\s+förordningen)?\s*$",
    re.IGNORECASE)
# where the page's publication history starts. Everything after this outer
# heading is the amendment register (the h3 per amending författning) and the
# amendment ledger, not text of the regulation -- the typed sections that
# survive it (the cutoff section, the bilagor) are what `_parse_ea` reads as
# amendment evidence, and they keep their own headings for that
RE_EA_OVERGANG = re.compile(r"^Övergångsbestämmelser")

# the issuing agency per samling. Not read from `agencies.SAMLINGAR`: that module
# imports this one to build the STKFA row, so reading it back here would be a
# cycle.
PUBLISHERS = {"esvfa": "Ekonomistyrningsverket", "stkfa": "Statskontoret"}

# The walk is bounded because it follows links the page itself names: a nav
# change that starts linking the whole site must fail loudly rather than crawl
# it. The live tree is 19 anchors off the root (2026-09), so this is two orders
# of headroom and no kind of routine limit.
PAGE_LIMIT = 500


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


def _drop_bare_type_headings(section):
    """Remove the section's headings that are only their type word ("Föreskrifter",
    "Allmänna råd", optionally "till förordningen"): they state no topic of
    their own, and the content opens straight into the section's own rubriker.
    """
    heading = section.find(["h2", "h3", "h4"])
    while heading is not None:
        text = " ".join(heading.get_text(" ", strip=True).split())
        if not RE_BARE_TYPE_HEADING.match(text):
            break
        heading.extract()
        heading = section.find(["h2", "h3", "h4"])


def _drop_repeated_heading(section, outer, next_outer):
    """Remove the typed section's first heading when it repeats a heading the
    page already prints around it, after the bare type word is dropped
    (:func:`_drop_bare_type_headings`).

    Two repeats occur in the markup. A chapter-marker heading ("1 kap. …") is
    the outer chapter heading's repeat when the page prints that same heading
    again as the next outer element (`next_outer`) -- the ingress section's
    shape -- and no outer heading follows it when the chapter lives in the
    section alone, which is why the equality is required. The scope-suffixed
    heading ("Kompensation – föreskrifter till 4 § förordningen") keeps its
    topic in the outer heading that opened it (`outer`). In each case the
    section's content opens at the place the kept heading stands, so the drop
    loses no boundary.
    """
    _drop_bare_type_headings(section)
    heading = section.find(["h2", "h3", "h4"])
    if heading is None:
        return
    text = " ".join(heading.get_text(" ", strip=True).split())
    if RE_EA_KAPITEL.match(text) and next_outer and text == next_outer:
        heading.extract()
        return
    if outer is None:
        return
    if text.startswith(outer):
        rest = text[len(outer):].lstrip()
        if not rest or rest[0] in ("\u2013", "-"):
            heading.extract()


def page_sections(box):
    """The text an EA leaf page carries, as an ordered list of section fragments.

    The walk is over the page's own top level, in document order. A typed
    section (``div.foreskrifter`` / ``div.allmanna-rad``) is kept, its repeated
    heading removed (:func:`_drop_repeated_heading`, or the bare type word alone
    after the Övergångsbestämmelser heading, where the section's heading is the
    amendment evidence). The outer headings are
    text of the regulation the page prints, so each is kept wrapped in a
    heading-only ``div.foreskrifter`` -- the shape ``_parse_ea`` reads as a
    rubrik block (a chapter marker stays a chapter, the way a PDF's sets it).
    Everything else the page hangs at its top level is dropped: the bare
    ``p``/``ul``/``ol`` runs between the headings are Forum's mirror of the
    förordning the föreskrifter advise on, which is the underlying act's text
    and not the regulation's, and the ``h3`` years after the Övergångsbestämmelser
    heading are the amendment register its typed sections already carry.

    ``box.find("body")`` is the live page's shape: Forum embeds the regelverk
    document as its own ``<html><body>`` inside the page shell, and the flat
    synthetic pages the tests write have no such wrapper.
    """
    content = box.find("body") or box
    elements = [el for el in content.children
                if not isinstance(el, NavigableString)]
    sections, outer, in_overgang, title_seen = [], None, False, False
    for i, el in enumerate(elements):
        if el.name in ("p", "ul", "ol"):
            continue
        if el.name in ("h2", "h3", "h4"):
            if in_overgang:
                continue
            text = " ".join(el.get_text(" ", strip=True).split())
            if not title_seen and RE_DESIGNATION.search(text):
                title_seen = True
                continue                      # the page's own title heading
            if RE_EA_OVERGANG.match(text):
                in_overgang = True
                continue                      # the publication history opens here
            outer = text
            sections.append('<div class="foreskrifter">%s</div>' % el)
            continue
        assert el.name == "div", (
            "unknown top-level element <%s class=%r> on a regelverk page"
            % (el.name, el.get("class")))
        assert el.get("class") and set(el["class"]) & {
            "foreskrifter", "allmanna-rad"}, (
            "an untyped top-level %s on a regelverk page: %s"
            % (el.get("class"), el.get_text(" ", strip=True)[:60]))
        if not in_overgang:
            next_el = elements[i + 1] if i + 1 < len(elements) else None
            next_outer = (" ".join(next_el.get_text(" ", strip=True).split())
                          if next_el is not None
                          and next_el.name in ("h2", "h3", "h4") else None)
            _drop_repeated_heading(el, outer, next_outer)
        else:
            # the amendment evidence lives in these sections' headings, so only
            # the bare type word is dropped
            _drop_bare_type_headings(el)
        sections.append(str(el))
    return sections


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
    sections = page_sections(box)
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
    text. The page is the consolidation and carries no separately published
    as-enacted text, so the record hangs no ``regulation`` file -- the shape
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
