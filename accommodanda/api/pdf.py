"""The PDF export: a generated page re-rendered for paper (/api/v1/pdf).

WeasyPrint renders the same HTML the browser prints, through the same
style.css ``@media print`` rules, and adds the paged-media layer browsers
skip: running headers, "n (total)" folios, a PDF outline. On request it also
prints two things the screen page keeps in its side columns:

* ``toc`` inserts the page's own TOC as an "Innehåll" section whose entries
  carry the printed page number of their target (CSS ``target-counter()``,
  resolved after layout).
* ``kinds`` names which context kinds (the rail's section slugs: kommentar,
  dv, forarbete, ...) to print as an editorial apparatus under each provision
  or section that has any.

Both consume the page's own artifacts -- the ``nav.toc`` markup and the
``#lagen-context`` JSON island -- so the printed apparatus cannot drift from
what the rail shows on screen.

Subresources (the stylesheet, fonts, facsimile images) never leave the
process: /style.css and /fonts/* come straight from lib/assets, and every
other URL is answered by the running app itself through the ``subresource``
callable the route hands in (an in-process TestClient, the same idiom
browse.py uses for static browse-page generation).

Rendered PDFs are cached on disk (``cache/pdfexport/``): brottsbalken with
TOC and full context lays out for ~100 s, and the result only changes when
the page or the stylesheet does -- both of which are in the cache key, so
staleness is impossible and eviction is purely a size matter (LRU, capped).
"""

import hashlib
import json
import os
import re
from urllib.parse import unquote, urlsplit

import lxml.html
import weasyprint
from lxml import etree  # ty: ignore[unresolved-import]  # lxml ships no stubs
from weasyprint.urls import URLFetcherResponse

from .. import config
from ..lib import compress
from ..lib.catalog import BASE
from ..lib.page import RAIL_SECTION_ORDER
from ..lib.render import ASSETS

# the PDF result cache is bounded by size alone (the key carries every
# staleness input); 2 GiB holds a few hundred large exports
CACHE_MAX_BYTES = 2 * 1024**3


def parse_kinds(kontext: str) -> frozenset[str]:
    """The ``kontext`` query parameter as a set of rail section slugs.
    Empty means no context; ``alla`` means every kind; an unknown slug is the
    caller's error and raises ValueError naming the valid ones."""
    if not kontext:
        return frozenset()
    if kontext == "alla":
        return frozenset(RAIL_SECTION_ORDER)
    kinds = frozenset(k.strip() for k in kontext.split(",") if k.strip())
    unknown = kinds - frozenset(RAIL_SECTION_ORDER)
    if unknown:
        raise ValueError("okända kontextslag: %s (giltiga: %s, eller 'alla')"
                         % (", ".join(sorted(unknown)),
                            ", ".join(RAIL_SECTION_ORDER)))
    return kinds


def filename_for(path: str) -> str:
    """A safe ASCII download name from the page's public path:
    /prop/2020/21:22 -> prop-2020-21-22.pdf."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", unquote(path)).strip("-.")
    return (slug or "dokument") + ".pdf"


def _stylesheet() -> str:
    """What the site serves as /style.css, minus the editor layer (inert
    without a session, and WeasyPrint has nothing to log in with). Read per
    render, never memoized: an `--assets-only` publish must reach a running
    serve, and the stylesheet text is a cache-key input -- a pinned copy
    would key new pages against old CSS until restart."""
    return ((ASSETS / "fonts" / "fonts.css").read_text(encoding="utf-8")
            + (ASSETS / "style.css").read_text(encoding="utf-8"))


def _drop(doc, *classes):
    """Remove every <script> and every element carrying one of `classes` --
    the chrome the print CSS only hides; gone here, WeasyPrint neither lays
    it out nor fetches into it."""
    doomed = list(doc.iter("script"))
    doomed += [el for cls in classes for el in doc.find_class(cls)]
    for el in doomed:
        el.getparent().remove(el)


# a large statute's context island is a >10 MB text node; libxml2's default
# cap silently *empties* such a node, so the parser must be told up front
_PARSER = lxml.html.HTMLParser(huge_tree=True)


def _island(doc):
    """The rail's pre-rendered context panels, keyed by node id ('' is the
    document-level panel), or {} on a page without the island."""
    el = doc.get_element_by_id("lagen-context", None)
    return json.loads(el.text) if el is not None else {}


def _print_toc(doc):
    """The page's nav.toc rebuilt as a print TOC (nav.print-toc): a flat list
    keeping the lvl* indent classes; page numbers are CSS's job. None for a
    page without a TOC. The #top self-entry is dropped -- the document's
    title block is necessarily on page one."""
    entries = [a for lst in doc.find_class("toc-list") for a in lst.iter("a")
               if a.get("href") != "#top"]
    if not entries:
        return None
    nav = lxml.html.Element("nav", {"class": "print-toc"})
    h2 = etree.SubElement(nav, "h2")
    h2.text = "Innehåll"
    ol = etree.SubElement(nav, "ol")
    for a in entries:
        li = etree.SubElement(ol, "li")
        lvl = next((c for c in (a.get("class") or "").split()
                    if c.startswith("lvl")), "")
        if lvl:
            li.set("class", lvl)
        link = etree.SubElement(li, "a", {"href": a.get("href")})
        link.text = a.text_content()
    return nav


def _kontext_aside(panel_html, kinds):
    """One rail panel filtered to the requested kinds and recast for paper:
    the accordion rows (details.rail-sec / div.rail-sec-flat) become plain
    blocks with an h4 label, widgets gone. None if no requested kind is in
    the panel."""
    aside = lxml.html.fragment_fromstring(panel_html, create_parent="aside")
    aside.set("class", "print-kontext")
    kept = False
    for sec in aside.find_class("rail-sec"):
        if sec.get("data-sec") not in kinds:
            sec.getparent().remove(sec)
            continue
        kept = True
        # the accordion row chrome: the fold's summary line, or the flat
        # variant's inline label span
        for s in sec.findall("summary"):
            sec.remove(s)
        for s in sec.find_class("rail-sec-h"):
            if s.getparent() is sec:
                sec.remove(s)
        sec.tag = "div"
        h4 = lxml.html.Element("h4")
        h4.text = sec.get("data-label") or ""
        if (sec.get("data-n") or "0").isdigit() and int(sec.get("data-n")) > 1:
            n = etree.SubElement(h4, "span", {"class": "n"})
            n.text = "(%s)" % sec.get("data-n")
        sec.insert(0, h4)
    if not kept:
        return None
    # the rail caps its lists on purpose (PANEL_CAP and friends) and that
    # editorial judgment holds on paper too: a much-cited provision would
    # otherwise print pages of citers. The fold becomes its own summary
    # line ("+250 till") as plain text.
    for more in aside.find_class("more"):
        line = lxml.html.Element("p", {"class": "more print-more"})
        line.text = more.findtext("summary", "")
        more.getparent().replace(more, line)
    return aside


# the renderer is WeasyPrint *plus* this module's paper transform: bump on
# any change to _print_toc/_kontext_aside/_drop or the fold handling, or the
# cache serves the old transform's output for every unchanged page
# (the search index's INDEX_FORMAT is the same pattern)
PDF_FORMAT = 1


class SubresourceUnavailable(RuntimeError):
    """A same-origin subresource (image, font, stylesheet) failed to fetch.
    WeasyPrint renders on without the resource, so left alone this would
    quietly ship -- and cache -- a PDF with content missing; the export
    refuses instead (rule:fail-fast)."""


def _cache_key(stored: bytes, toc, kinds):
    """Everything the rendered bytes depend on: the page *content* (the
    stored variant's bytes -- so a regenerate after a source update or a
    patch-file change invalidates, while a deploy that only re-copies
    identical bytes still hits), the options, the stylesheet text and the
    renderer version (WeasyPrint and the transform). A stale entry is
    thereby unreachable, never served."""
    raw = repr((hashlib.sha256(stored).hexdigest(), toc, sorted(kinds),
                hashlib.sha256(_stylesheet().encode()).hexdigest(),
                weasyprint.VERSION, PDF_FORMAT))
    return hashlib.sha256(raw.encode()).hexdigest()


def _prune(cache, cap=CACHE_MAX_BYTES):
    """Drop the least-recently-used entries until the cache fits `cap`.
    Recency is mtime: a hit re-touches its entry (`export`)."""
    entries = []
    for p in cache.glob("*.pdf"):
        try:
            st = p.stat()
        except FileNotFoundError:           # a sibling worker pruned it
            continue
        entries.append((st.st_mtime_ns, st.st_size, p))
    entries.sort()
    total = sum(size for _, size, _ in entries)
    for _, size, p in entries:
        if total <= cap:
            break
        p.unlink(missing_ok=True)
        total -= size


def export(page, *, toc: bool, kinds: frozenset[str], subresource) -> bytes:
    """`render_pdf` behind the disk cache. `page` is the logical generated
    file (compress resolves the stored variant); FileNotFoundError if the
    page is not generated."""
    resolved = compress.resolve(page)
    if resolved is None:
        raise FileNotFoundError(str(page))
    cache = config.DATA / "cache" / "pdfexport"
    entry = cache / (_cache_key(resolved.read_bytes(), toc, kinds) + ".pdf")
    if entry.is_file():
        try:
            os.utime(entry)                 # LRU recency for _prune
            return entry.read_bytes()
        except FileNotFoundError:
            pass                            # a sibling worker pruned it: render
    data = render_pdf(compress.read_text(page), toc=toc, kinds=kinds,
                      subresource=subresource)
    cache.mkdir(parents=True, exist_ok=True)
    tmp = entry.with_name(entry.name + ".tmp-%d" % os.getpid())
    tmp.write_bytes(data)
    os.replace(tmp, entry)                  # concurrent renders: last wins
    _prune(cache)
    return data


def render_pdf(html_text: str, *, toc: bool, kinds: frozenset[str],
               subresource) -> bytes:
    """The page as PDF bytes. `subresource` answers an in-site path+query
    with (bytes, mime) -- the app answering for itself in-process."""
    doc = lxml.html.document_fromstring(html_text, parser=_PARSER)
    island = _island(doc) if kinds else {}
    # the TOC aside is harvested before the chrome it sits in is dropped
    nav = _print_toc(doc) if toc else None
    _drop(doc, "masthead", "toc-col", "rail", "mobile-bar")
    front = next(iter(doc.find_class("frontmatter")), None)
    if island and front is not None:
        # the document-level panel goes right after the frontmatter; the
        # TOC (inserted after, also with addnext) then lands between them
        if island.get(""):
            aside = _kontext_aside(island[""], kinds)
            if aside is not None:
                front.addnext(aside)
        for el in doc.xpath("//*[@data-rail]"):
            panel = island.get(el.get("data-rail"))
            aside = _kontext_aside(panel, kinds) if panel else None
            if aside is not None:
                el.addnext(aside)
    if nav is not None and front is not None:
        front.addnext(nav)
    # WeasyPrint renders a closed fold's content anyway -- declare every
    # fold open so the print CSS hides the summary widgets, not the text
    for d in doc.iter("details"):
        d.set("open", "open")
    failures = []
    data = weasyprint.HTML(
        string=lxml.html.tostring(doc, encoding="unicode"),
        base_url=BASE, url_fetcher=_fetcher(subresource, failures)).write_pdf()
    # WeasyPrint catches every fetcher exception and lays out without the
    # resource -- fine for its use, but here it would mean serving (and
    # caching, in export) a PDF that silently lacks an image or font
    if failures:
        raise SubresourceUnavailable("; ".join(failures))
    return data


def _fetcher(subresource, failures):
    """A WeasyPrint url_fetcher that never touches the network: assets from
    lib/assets, data: URIs decoded in place, everything else from the
    running app. *Every* failure -- including a URL outside the site's own
    origin, which would violate the "pages load no third-party resource"
    invariant -- is recorded in `failures` before the raise: WeasyPrint
    swallows the exception and renders without the resource, so the record
    is what lets render_pdf refuse the degraded result."""
    site = urlsplit(BASE)

    def fetch(url, timeout=None, ssl_context=None):
        if url.startswith("data:"):     # inline payload, nothing to fetch
            return weasyprint.default_url_fetcher(url)
        u = urlsplit(url)
        try:
            if (u.scheme, u.netloc) != (site.scheme, site.netloc):
                raise ValueError("extern URL hämtas inte: %s" % url)
            if u.path == "/style.css":
                return URLFetcherResponse(
                    url, body=_stylesheet(),
                    headers={"content-type": "text/css; charset=utf-8"})
            if u.path.startswith("/fonts/"):
                font = ASSETS / "fonts" / u.path.removeprefix("/fonts/")
                if font.parent != ASSETS / "fonts":  # rule:errors-drive-retry-use-raise
                    raise ValueError("ogiltig fontsökväg: %s" % u.path)
                return URLFetcherResponse(url, body=font.read_bytes(),
                                          headers={"content-type": "font/woff2"})
            body, mime = subresource(u.path + ("?" + u.query if u.query else ""))
            return URLFetcherResponse(url, body=body,
                                      headers={"content-type": mime})
        except Exception as exc:
            failures.append("%s (%s)" % (url, exc))
            raise
    return fetch
