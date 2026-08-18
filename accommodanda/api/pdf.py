"""The PDF export: a generated page re-rendered for paper (/api/v1/pdf).

WeasyPrint renders the same HTML the browser prints, through the same
style.css ``@media print`` rules, and adds the paged-media layer browsers
skip: a running head in the page's corner boxes, its page number, a PDF
outline. On request it also prints two things the screen page keeps in its
side columns:

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

Rendered PDFs are cached on disk (``cache/pdfexport/``): the GDPR with TOC
and full context lays out for some 14 s here and about four times that on
the production host, and the result only changes when the page or the
stylesheet does -- both of which are in the cache key, so staleness is
impossible and eviction is purely a size matter (LRU, capped). A lock per
key means two readers of the same cold export share one render, and a large
one runs as a background job the reader follows (``api/pdfjob.py``).
"""

import hashlib
import json
import os
import re
import threading
from urllib.parse import unquote, urlsplit

import lxml.html
import weasyprint
from lxml import etree  # ty: ignore[unresolved-import]  # lxml ships no stubs
from weasyprint.urls import URLFetcherResponse

from .. import config
from ..lib import compress, layout
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


# How many items of a section a margin note lists before the rest collapse
# into its "+N fler" line. The rail caps at PANEL_CAP (20) for a screen
# column of 22 rem; the same twenty in a 37 mm margin fill a page and more,
# and a reader gets a spread whose reading column is empty beside a list of
# citers. The cap only decides what is *collapsed* -- the count in the
# disclosure line covers every item, exactly as it does on screen.
MARGIN_CAP = 5

_MORE_COUNT = re.compile(r"\d+")

# A citer line names its target the way the screen rail does -- "Prop.
# 2015/16:170: En uppdaterad fondlagstiftning (UCITS V), avsnitt 8.4" -- and
# in a margin column that title wraps over four lines to say what its number
# already said. In print the line keeps the identifier and the pinpoint and
# drops the title between them. The identifier is what a reader looks up.
_CITER_TITLE = re.compile(
    r"""^(?P<id>.+?)          # the printed identifier
         :\s                  # the rail's own separator
         .*?                  # the title, which the number already names
         (?P<pin>,\s(?:s\.|avsnitt|art\.|kap\.|p\.)\s[^,]+)?$""",
    re.X)
# an EU act, a court case, an agency's own numbered position: each names
# itself first and runs straight into its title, with no separator --
# "(EU) 2016/1629 Tekniska krav för fartyg ...", "IMYRS 2021:1 Innebörden
# av ...". A form that carries no such identifier is left whole: a line the
# reader cannot look up by number must keep the words that identify it.
_CITER_RUNON = re.compile(
    r"^(\((?:EU|EG|EEG|Euratom)\)\s*(?:nr\s*)?\d+/\d+"
    r"|[CT]-\d+/\d+"
    r"|[A-ZÅÄÖ]{2,}\s\d{4}:\d+"
    r"|[A-ZÅÄÖ]-\d[\d-]*)\s\S")


def _short_citer(text: str) -> str:
    """A citer line cut to its identifier and pinpoint."""
    runon = _CITER_RUNON.match(text)
    if runon:
        return runon.group(1)
    title = _CITER_TITLE.match(text)
    if title:
        return title.group("id") + (title.group("pin") or "")
    return text


def _shorten_citers(sec) -> None:
    """Replace every citer line in a rail section with its short form. The
    link is left alone -- only the text it carries changes -- so the PDF's
    outline and its internal links still point where they did."""
    for link in sec.iter("a"):
        if link.text and not len(link):
            link.text = _short_citer(link.text)


def _cap_section(sec, cap):
    """Collapse a rail section's list to `cap` items, and fold what the print
    drops into the same "+N fler" line the screen already carries."""
    lists = sec.findall("ul")
    more = next(iter(sec.find_class("more")), None)
    hidden = len(more.findall(".//li")) if more is not None else 0
    # the rail's own wording, which differs per section ("fler", "till"):
    # taken from the line it already wrote, never invented here
    label = more.findtext("summary", "") if more is not None else ""
    if lists and len(lists[0]) > cap:
        hidden += len(lists[0]) - cap
        for item in lists[0][cap:]:
            lists[0].remove(item)
    if more is not None:
        more.getparent().remove(more)
    if not hidden:
        return
    line = etree.SubElement(sec, "p", {"class": "more print-more"})
    line.text = (_MORE_COUNT.sub(str(hidden), label, count=1) if label
                 else "+%d fler" % hidden)


def _kontext_aside(panel_html, kinds):
    """One rail panel filtered to the requested kinds and recast for paper:
    the accordion rows (details.rail-sec / div.rail-sec-flat) become plain
    blocks with an h4 label, widgets gone, each list capped at MARGIN_CAP.
    None if no requested kind is in the panel."""
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
        # the rail caps its lists on purpose and that editorial judgment
        # holds on paper too -- harder, because the column is narrower
        _shorten_citers(sec)
        _cap_section(sec, MARGIN_CAP)
        h4 = lxml.html.Element("h4")
        h4.text = sec.get("data-label") or ""
        if (sec.get("data-n") or "0").isdigit() and int(sec.get("data-n")) > 1:
            n = etree.SubElement(h4, "span", {"class": "n"})
            n.text = "(%s)" % sec.get("data-n")
        sec.insert(0, h4)
    return aside if kept else None


_HEADINGS = frozenset(("h1", "h2", "h3", "h4", "h5", "h6"))


# The running head names the division a page opens on, and a top margin box
# has room for a label, not a title: "KAPITEL X Delegerade akter och
# genomförandeakter" crowded the document's own name out of the middle of the
# head. These are the printed labels a division heading opens with -- "4
# kap.", "KAPITEL II", "AVDELNING I" -- across SFS, EU acts and the courts'
# decisions alike; a heading that opens with none keeps the head to the § or
# article alone, which is the part a reader looks for anyway.
_DIVISION_LABEL = re.compile(
    r"^\s*(\d+\s*[a-zA-Z]?\s*kap\.|(?:KAPITEL|AVDELNING|AVD\.|DEL|BILAGA)"
    r"\s+[IVXLCDM\d]+[A-Za-z]?|[A-ZÅÄÖ]+\s+AVDELNINGEN)")
_DIVISION_MAX = 26


def _running_labels(doc) -> None:
    """Stamp each division heading with the short label the running head
    prints (`data-kort`), separator included so a document without divisions
    prints "3 §" rather than " · 3 §"."""
    # `.rubrik` is emitted at h2 through h6 (lib/templates/nodes.html), and
    # the stylesheet takes the running head's division from `h2.rubrik`
    # only -- stamping the rest would write an attribute no page prints
    for heading in (doc.find_class("kaprubrik") + doc.find_class("instans-rubrik")
                    + [h for h in doc.find_class("rubrik") if h.tag == "h2"]):
        text = " ".join(heading.text_content().split())
        label = _DIVISION_LABEL.match(text)
        short = label.group(1) if label else (text if len(text) <= _DIVISION_MAX
                                              else "")
        heading.set("data-kort", short + " · " if short else "")


def _column_block(el, main):
    """The element `el`'s own block in the reading column: the ancestor that
    is a child of ``.gr-main``, or `el` itself when it already is one.

    A note is hung there rather than on `el` directly, because a rail marker
    can sit deep inside one -- SFS marks each stycke of a §, eurlex marks a
    recital. Down there the block that reaches into the margin would be laid
    out against an indented content box, so the distance it must reach would
    differ per nesting depth. Every note therefore hangs off the one column
    that has the same two edges on every page. Several markers in one block
    share it, and their notes stack in document order."""
    while el is not None and el.getparent() is not main:
        el = el.getparent()
    return el


def _attach(el, asides):
    """Put `el`'s notes in the margin beside it, and return the block that
    now holds them both.

    The two are the cells of one table row, so the note stays level with what
    it annotates. Floating it instead lets the text flow past, which fills
    the page and decouples the columns: the GDPR's recitals carry three times
    the context their text can sit beside, and the backlog never drained."""
    classes = ["kontextblock"]
    if el.tag in _HEADINGS:
        # the break falls after the block, out of reach of the heading's own
        # break-after: avoid
        classes.append("rubrikblock")
    block = lxml.html.Element("div", {"class": " ".join(classes)})
    el.addprevious(block)
    block.tail, el.tail = el.tail, None
    row = etree.SubElement(block, "div", {"class": "kontextrad"})
    etree.SubElement(row, "div", {"class": "kontextsp"}).append(el)
    etree.SubElement(row, "div", {"class": "kontextnot"}).extend(asides)
    return block


# U+2011 NON-BREAKING HYPHEN and U+2010 HYPHEN are in the text of some
# sources ("C\u2011291/24" in an advocate-general's opinion) and in none of
# the self-hosted woff2 subsets, so WeasyPrint draws .notdef -- a box where
# the hyphen belongs. The same gap is why `hyphenate-character` is set to the
# ASCII hyphen. Paper gets the ASCII hyphen throughout, for the same reason.
_PAPER_HYPHENS = str.maketrans({"\u2010": "-", "\u2011": "-"})


def _paper_html(doc) -> str:
    """The transformed document as the string WeasyPrint lays out."""
    return lxml.html.tostring(doc, encoding="unicode").translate(_PAPER_HYPHENS)


# the renderer is WeasyPrint *plus* this module's paper transform: bump on
# any change to _print_toc/_kontext_aside/_attach/_drop or the fold handling,
# or the cache serves the old transform's output for every unchanged page
# (the search index's INDEX_FORMAT is the same pattern)
PDF_FORMAT = 2


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


def generated_page(path: str):
    """The generated file a public page path names -- ``/prop/2020/21:22`` ->
    ``DATA/generated/forarbete/prop/2020/21_22.html``. None when the path is
    not a page address, and equally when the site holds no such page: an
    export can do nothing with either."""
    rel = layout.url_to_relpath(path)
    if rel is None:
        return None
    page = config.DATA / "generated" / rel
    return page if compress.resolve(page) is not None else None


def cache_entry(page, *, toc: bool, kinds: frozenset[str]):
    """The cache file this export lands in -- present means the PDF is ready
    to serve. FileNotFoundError if `page` is not generated."""
    resolved = compress.resolve(page)
    if resolved is None:
        raise FileNotFoundError(str(page))
    return (config.DATA / "cache" / "pdfexport"
            / (_cache_key(resolved.read_bytes(), toc, kinds) + ".pdf"))


# One render per cache key at a time. Two readers asking for the same big
# export used to pay for it twice over, in parallel, on a host that has the
# cores for neither; the second now waits for the first and reads its bytes.
_renders: dict[str, threading.Lock] = {}
_renders_lock = threading.Lock()


def _render_lock(key: str) -> threading.Lock:
    with _renders_lock:
        if len(_renders) > 512:
            # a lock per export ever asked for would grow without end; the
            # idle ones are dropped. A key dropped between lookup and acquire
            # gets a second lock and so a second render -- the same race the
            # cache write has always tolerated, and it ends the same way
            for old, lock in list(_renders.items()):
                if not lock.locked():
                    del _renders[old]
        return _renders.setdefault(key, threading.Lock())


def export(page, *, toc: bool, kinds: frozenset[str], subresource,
           progress=None) -> bytes:
    """`render_pdf` behind the disk cache. `page` is the logical generated
    file (compress resolves the stored variant); FileNotFoundError if the
    page is not generated. `progress`, when given, is told the page estimate
    and then follows the render (api/pdfjob.Job)."""
    entry = cache_entry(page, toc=toc, kinds=kinds)
    entry.parent.mkdir(parents=True, exist_ok=True)
    with _render_lock(entry.name):
        if entry.is_file():
            try:
                os.utime(entry)             # LRU recency for _prune
                return entry.read_bytes()
            except FileNotFoundError:
                pass                        # a sibling worker pruned it: render
        data = render_pdf(compress.read_text(page), toc=toc, kinds=kinds,
                          subresource=subresource, progress=progress)
        tmp = entry.with_name(entry.name + ".tmp-%d" % os.getpid())
        tmp.write_bytes(data)
        os.replace(tmp, entry)              # concurrent processes: last wins
    _prune(entry.parent)
    return data


# A printed page holds about this much of a document. Two terms, because
# either alone is out by a factor of two: dense prose runs 2100 characters to
# the page (the GDPR), while a statute spends much of its page on the air
# around §§ and list items and holds 1270 (räntelagen). Fitted on six real
# exports of the three -- with and without context -- against the margins
# below; worst case 20 % out, which is why the waiting screen says "ca".
# Only the progress bar reads this. Nothing renders differently for the
# estimate being wrong, and layout replaces it with the true count as soon
# as the first pass over the pages ends.
CHARS_PER_PAGE = 1850
BLOCKS_PER_PAGE = 132
_BLOCK_TAGS = ("p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "dt", "dd",
               "blockquote", "figure")


def estimate_pages(doc) -> int:
    """How many A4 pages the transformed document will make. An estimate for
    the progress bar -- the true count only exists after layout."""
    blocks = sum(len(doc.findall(".//" + tag)) for tag in _BLOCK_TAGS)
    return max(1, round(len(doc.text_content()) / CHARS_PER_PAGE
                        + blocks / BLOCKS_PER_PAGE))


def render_pdf(html_text: str, *, toc: bool, kinds: frozenset[str],
               subresource, progress=None) -> bytes:
    """The page as PDF bytes. `subresource` answers an in-site path+query
    with (bytes, mime) -- the app answering for itself in-process."""
    doc = lxml.html.document_fromstring(html_text, parser=_PARSER)
    island = _island(doc) if kinds else {}
    # the TOC aside is harvested before the chrome it sits in is dropped
    nav = _print_toc(doc) if toc else None
    _drop(doc, "masthead", "toc-col", "rail", "mobile-bar")
    _running_labels(doc)
    front = next(iter(doc.find_class("frontmatter")), None)
    annotated = {}
    if island and front is not None:
        main = front.getparent()
        # the document-level panel annotates the frontmatter; the rest hang
        # off whatever carries the rail marker, hoisted to its own block in
        # the reading column
        if island.get(""):
            # not a margin note: even capped it runs to a page and a half,
            # and a note that outruns its page cannot stay in the outer
            # margin -- see `.print-kontext.bred`
            aside = _kontext_aside(island[""], kinds)
            if aside is not None:
                aside.set("class", "print-kontext bred")
                front.addnext(aside)
        for el in doc.xpath("//*[@data-rail]"):
            panel = island.get(el.get("data-rail"))
            aside = _kontext_aside(panel, kinds) if panel else None
            if aside is not None:
                block = _column_block(el, main)
                assert block is not None, (          # rule:fail-fast
                    "rail marker %r sits outside the reading column"
                    % el.get("data-rail"))
                annotated.setdefault(block, []).append(aside)
    blocks = {el: _attach(el, asides) for el, asides in annotated.items()}
    if nav is not None and front is not None:
        # after the frontmatter -- and after the last of the blocks it now
        # sits in, or the TOC's page break would cut the frontmatter's own
        # note in half and strand its continuation behind the contents
        blocks.get(front, front).addnext(nav)
    # WeasyPrint renders a closed fold's content anyway -- declare every
    # fold open so the print CSS hides the summary widgets, not the text
    for d in doc.iter("details"):
        d.set("open", "open")
    if progress is not None:
        progress.plan(estimate_pages(doc))
    failures = []
    data = weasyprint.HTML(
        string=_paper_html(doc), base_url=BASE,
        url_fetcher=_fetcher(subresource, failures)).write_pdf()
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
