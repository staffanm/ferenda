"""Bookmarkable multi-document PDF collections.

The browser owns the editable collection.  It sends one complete manifest when
the reader asks for a PDF; the server validates it, reads the current generated
pages and caches only the derived PDF.  No collection record is stored.

Each source page first goes through :mod:`ferenda.api.pdf`'s existing paper
transform.  The transformed reading columns are then namespaced and assembled
in one HTML document.  One WeasyPrint layout is important: ``start="direct"``
means that the next document must use space left on the current page, which a
PDF concatenator cannot recover after documents have been laid out separately.
"""

import hashlib
import json
import os
import re
from datetime import date
from html import unescape
from pathlib import Path
from typing import Literal
from urllib.parse import unquote

import lxml.html
from lxml import etree  # ty: ignore[unresolved-import]  # lxml ships no stubs
from pydantic import BaseModel, ConfigDict, Field

from ..lib import compress, tpl, util
from . import pdf

MAX_DOCUMENTS = 1000
MAX_SECTIONS_PER_DOCUMENT = 500
COLLECTION_FORMAT = 1

StartMode = Literal["direct", "page", "recto"]


class CollectionItem(BaseModel):
    """One generated document and its non-global paper choices."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=2, max_length=300)
    start: StartMode = "direct"
    amendments: bool = True
    preamble: bool = True
    sections: list[str] = Field(default_factory=list,
                                max_length=MAX_SECTIONS_PER_DOCUMENT)


class CollectionManifest(BaseModel):
    """The complete, stateless request sent by ``/samling``."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    title: str = Field(default="Författningssamling", max_length=200)
    subtitle: str = Field(default="", max_length=400)
    cover: bool = True
    toc: bool = True
    columns: Literal[1, 2] = 1
    context: list[str] = Field(default_factory=list, max_length=50)
    items: list[CollectionItem] = Field(min_length=1, max_length=MAX_DOCUMENTS)


class InspectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paths: list[str] = Field(min_length=1, max_length=MAX_DOCUMENTS)


def _canonical_path(path: str) -> str:
    """One public document path, with no query or fragment."""
    if not path.startswith("/") or "?" in path or "#" in path or "\\" in path:
        raise ValueError("ogiltig dokumentsökväg: %r" % path)
    page = pdf.generated_page(path)
    if page is None:
        raise FileNotFoundError(path)
    return path


def _page_identity(path: str) -> Path:
    page = pdf.generated_page(_canonical_path(path))
    assert page is not None, "validated collection path disappeared"
    resolved = compress.resolve(page)
    assert resolved is not None, "validated collection page disappeared"
    return resolved


def validate(manifest: CollectionManifest) -> CollectionManifest:
    """Validate facts that span several Pydantic fields."""
    paths = [_canonical_path(item.path) for item in manifest.items]
    if len(paths) != len(set(_page_identity(path) for path in paths)):
        raise ValueError("samma dokument får inte förekomma flera gånger")
    pdf.parse_kinds(",".join(manifest.context))
    if manifest.columns == 2 and manifest.context:
        raise ValueError("två kolumner kan inte kombineras med kontext")
    return manifest


def _page(path: str) -> tuple[Path, str]:
    page = pdf.generated_page(_canonical_path(path))
    assert page is not None, "validated collection path has no generated page"
    return page, compress.read_text(page)


def _inside_class(el, class_name: str) -> bool:
    while el is not None:
        if class_name in pdf._classes(el):
            return True
        el = el.getparent()
    return False


def _outline(doc) -> list[dict]:
    """Selectable headings in the current rendered page.

    The rendered TOC is the contract because it names the exact anchors the
    PDF will contain.  Amendment-register headings are controlled by their own
    option and therefore do not appear as section choices.
    """
    entries = []
    seen = set()
    for toc in doc.find_class("toc-list"):
        for link in toc.iter("a"):
            href = link.get("href") or ""
            if not href.startswith("#") or href == "#top":
                continue
            anchor = unquote(href[1:])
            if anchor in seen:
                continue
            target = doc.get_element_by_id(anchor, None)
            if target is None or _inside_class(target, "andringar"):
                continue
            seen.add(anchor)
            level = (int(target.tag[1:]) - 1
                     if re.fullmatch(r"h[1-6]", target.tag) else 1)
            entries.append({"id": anchor,
                            "label": " ".join(link.text_content().split()),
                            "level": max(1, level)})
    return entries


def _context_kinds(doc) -> list[dict]:
    island = pdf._island(doc)
    seen = set()
    answer = []
    for panel in island.values():
        fragment = lxml.html.fragment_fromstring(panel, create_parent="div")
        for section in fragment.find_class("rail-sec"):
            key = section.get("data-sec")
            if not key or key in seen:
                continue
            seen.add(key)
            answer.append({"key": key,
                           "label": section.get("data-label") or key})
    return answer


def inspect(paths: list[str]) -> list[dict]:
    """Current labels, options and selectable headings for collection rows."""
    if len(paths) != len(set(_page_identity(path) for path in paths)):
        raise ValueError("samma dokument får inte förekomma flera gånger")
    answer = []
    for path in paths:
        _page_path, text = _page(path)
        doc = lxml.html.document_fromstring(text, parser=pdf._PARSER)
        front = next(iter(doc.find_class("frontmatter")), None)
        assert front is not None, (  # rule:fail-fast
            "generated collection document %r has no frontmatter" % path)
        heading = front.find("h1")
        assert heading is not None, (  # rule:fail-fast
            "generated collection document %r has no title" % path)
        eyebrow = next(iter(front.find_class("eyebrow")), None)
        answer.append({
            "path": path,
            "title": " ".join(heading.text_content().split()),
            "label": (" ".join(eyebrow.text_content().split())
                      if eyebrow is not None else path),
            "amendments": any(section.find_class("andring")
                              for section in doc.find_class("andringar")),
            "preamble": any(doc.find_class(name)
                            for name in ("visa", "recital", "preamble")),
            "outline": _outline(doc),
            "context": _context_kinds(doc),
        })
    return answer


def _content_root(doc, main):
    document = doc.get_element_by_id("dokument", None)
    return document if document is not None else main


def _select_sections(doc, selected: list[str]) -> None:
    """Keep the selected heading ranges and their descendants.

    Flat sources such as a proposition use sibling heading ranges.  SFS wraps
    each chapter below ``#dokument``; there the same rule keeps the complete
    chapter container.  The title and metadata stay outside this operation.
    """
    if not selected:
        return
    mains = doc.find_class("gr-main")
    assert len(mains) == 1, "generated page must have one reading column"
    main = mains[0]
    root = _content_root(doc, main)
    entries = _outline(doc)
    by_id = {entry["id"]: index for index, entry in enumerate(entries)}
    missing = [anchor for anchor in selected if anchor not in by_id]
    if missing:
        raise ValueError("okända eller inaktuella avsnitt i %r: %s"
                         % (doc.findtext("head/title", "dokumentet"),
                            ", ".join(missing)))

    nodes = list(root.iter())
    positions = {node: index for index, node in enumerate(nodes)}
    keep = set()
    for anchor in selected:
        entry_index = by_id[anchor]
        target = doc.get_element_by_id(anchor)
        assert target in positions, (  # rule:fail-fast
            "outline target %r sits outside the document body" % anchor)
        start = positions[target]
        end = len(nodes)
        level = entries[entry_index]["level"]
        for later in entries[entry_index + 1:]:
            if later["level"] > level:
                continue
            later_target = doc.get_element_by_id(later["id"])
            if later_target in positions and positions[later_target] > start:
                end = positions[later_target]
                break
        keep.update(nodes[start:end])

    for node in tuple(keep):
        parent = node.getparent()
        while parent is not None and parent is not root:
            keep.add(parent)
            parent = parent.getparent()
        keep.add(root)

    front = next(iter(doc.find_class("frontmatter")), None)
    if root is main and front is not None:
        keep.update(front.iter())
    for node in reversed(nodes[1:]):
        if node not in keep and node.getparent() is not None:
            node.getparent().remove(node)
    if root is not main:
        for child in list(main):
            if child is not front and child is not root:
                main.remove(child)


def _drop_preamble(doc) -> None:
    for name in ("visa", "recital", "preamble", "recital-group"):
        for element in list(doc.find_class(name)):
            parent = element.getparent()
            if parent is not None:
                parent.remove(element)


def _namespace(root, prefix: str) -> str:
    """Make one generated page's ids unique inside the combined document."""
    mapping = {}
    for element in root.iter():
        if old := element.get("id"):
            new = "%s-%s" % (prefix, old)
            mapping[old] = new
            element.set("id", new)
    for element in root.iter():
        href = element.get("href")
        if href and href.startswith("#") and href[1:] in mapping:
            element.set("href", "#" + mapping[href[1:]])
        for attr in ("for", "aria-controls", "aria-describedby"):
            value = element.get(attr)
            if value in mapping:
                element.set(attr, mapping[value])
    return mapping["top"]


_MONTHS = ("januari", "februari", "mars", "april", "maj", "juni", "juli",
           "augusti", "september", "oktober", "november", "december")


def _swedish_date(day: date) -> str:
    return "%d %s %d" % (day.day, _MONTHS[day.month - 1], day.year)


def assemble(manifest: CollectionManifest, generated: date):
    """Build the one paper DOM that WeasyPrint paginates."""
    validate(manifest)
    root = lxml.html.Element("html", {"lang": "sv"})
    if manifest.columns == 2:
        root.set("class", "pdf-two-columns")
    head = etree.SubElement(root, "head")
    etree.SubElement(head, "meta", {"charset": "utf-8"})
    etree.SubElement(head, "title").text = manifest.title
    etree.SubElement(head, "link", {"rel": "stylesheet", "href": "/style.css"})
    body_classes = "gr-root pdf-weasy pdf-collection"
    if manifest.cover:
        body_classes += " collection-has-cover"
    if manifest.toc:
        body_classes += " collection-has-toc"
    if manifest.columns == 2:
        body_classes += " pdf-two-columns"
    body = etree.SubElement(root, "body", {"class": body_classes})
    layout = etree.SubElement(body, "div", {"class": "gr-body"})
    main = etree.SubElement(layout, "main", {"class": "gr-main collection-main"})

    if manifest.cover:
        cover = etree.SubElement(main, "section", {"class": "collection-cover"})
        etree.SubElement(cover, "h1").text = manifest.title
        if manifest.subtitle:
            etree.SubElement(cover, "p", {"class": "collection-subtitle"}).text = \
                manifest.subtitle
        etree.SubElement(cover, "p", {"class": "collection-generated"}).text = \
            "Genererat från lagen.nu %s" % _swedish_date(generated)
        etree.SubElement(main, "div", {"class": "collection-cover-verso"})

    toc_entries = []
    kinds = frozenset(manifest.context)
    for number, item in enumerate(manifest.items, 1):
        _path, text = _page(item.path)
        raw = lxml.html.document_fromstring(text, parser=pdf._PARSER)
        _select_sections(raw, item.sections)
        if not item.preamble:
            _drop_preamble(raw)
        paper = pdf._paper_document(
            lxml.html.tostring(raw, encoding="unicode"), toc=False, kinds=kinds,
            amendments=item.amendments, columns=manifest.columns)
        source_body = paper.find("body")
        source_main = paper.find_class("gr-main")[0]
        wrapper_classes = ["collection-document", "start-" + item.start]
        if number == 1:
            wrapper_classes.append("collection-first-document")
        wrapper_classes.extend(c for c in pdf._classes(source_body)
                               if c not in ("gr-root", "pdf-weasy",
                                            "pdf-two-columns"))
        wrapper = etree.SubElement(main, "section", {
            "class": " ".join(wrapper_classes),
            "data-collection-number": str(number),
        })
        for child in list(source_main):
            wrapper.append(child)
        target = _namespace(wrapper, "d%d" % number)
        front = next(iter(wrapper.find_class("frontmatter")), None)
        assert front is not None, "paper document lost its title block"
        heading = front.find("h1")
        assert heading is not None, "paper document lost its title"
        eyebrow = next(iter(front.find_class("eyebrow")), None)
        toc_entries.append({
            "target": target,
            "label": (" ".join(eyebrow.text_content().split())
                      if eyebrow is not None else item.path),
            "title": " ".join(heading.text_content().split()),
        })

    if manifest.toc:
        nav = etree.Element("nav", {"class": "collection-toc print-toc"})
        etree.SubElement(nav, "h2").text = "Innehåll"
        listing = etree.SubElement(nav, "ol")
        for entry in toc_entries:
            row = etree.SubElement(listing, "li")
            link = etree.SubElement(row, "a", {"href": "#" + entry["target"]})
            etree.SubElement(link, "span", {"class": "collection-toc-id"}).text = \
                entry["label"]
            etree.SubElement(link, "span", {"class": "collection-toc-title"}).text = \
                entry["title"]
        first_document = next(iter(main.find_class("collection-first-document")))
        first_document.addprevious(nav)

    documents = main.find_class("collection-document")
    for entry, document in zip(toc_entries, documents, strict=True):
        previous = document.getprevious()
        if previous is None:
            continue
        etree.SubElement(previous, "div", {
            "class": "collection-running-next",
            "data-doc-id": entry["label"],
            "data-doc-title": entry["title"],
        })
    return root


def _manifest_bytes(manifest: CollectionManifest) -> bytes:
    return json.dumps(manifest.model_dump(), ensure_ascii=False,
                      sort_keys=True, separators=(",", ":")).encode()


def _cache_key(manifest: CollectionManifest, generated: date) -> str:
    inputs = []
    for item in manifest.items:
        page = pdf.generated_page(item.path)
        assert page is not None, "validated collection path disappeared"
        resolved = compress.resolve(page)
        assert resolved is not None, "validated collection page disappeared"
        inputs.append(hashlib.sha256(resolved.read_bytes()).hexdigest())
    raw = repr((hashlib.sha256(_manifest_bytes(manifest)).hexdigest(),
                generated.isoformat(), inputs,
                hashlib.sha256(pdf._stylesheet().encode()).hexdigest(),
                pdf.weasyprint.VERSION, pdf.PDF_FORMAT, COLLECTION_FORMAT))
    return hashlib.sha256(raw.encode()).hexdigest()


def cache_entry(manifest: CollectionManifest, generated: date) -> Path:
    validate(manifest)
    return pdf.cache_dir() / ("samling-" + _cache_key(manifest, generated)
                              + ".pdf")


_render_lock = util.KeyedLocks()


def export(manifest: CollectionManifest, *, subresource, generated: date,
           progress=None) -> bytes:
    """Render behind the shared disk cache."""
    entry = cache_entry(manifest, generated)
    entry.parent.mkdir(parents=True, exist_ok=True)
    with _render_lock(entry.name):
        if entry.is_file():
            try:
                os.utime(entry)
                return entry.read_bytes()
            except FileNotFoundError:
                pass
        data = pdf.render_document(assemble(manifest, generated),
                                   subresource=subresource, progress=progress)
        temporary = entry.with_name(entry.name + ".tmp-%d" % os.getpid())
        temporary.write_bytes(data)
        os.replace(temporary, entry)
    pdf._prune(entry.parent)
    return data


def filename(manifest: CollectionManifest) -> str:
    text = unescape(manifest.title).strip() or "forfattningssamling"
    return pdf.filename_for("/" + text)


TPL = tpl.environment("ferenda.api")


def collection_page() -> str:
    return TPL.get_template("pdf_collection.html").render()


def wait_page() -> str:
    return TPL.get_template("pdf_collection_wait.html").render()
