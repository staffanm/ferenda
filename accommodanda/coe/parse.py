"""Parse official English Council of Europe treaty texts into article trees.

Every official English text the Treaty Office web service links is a PDF on
rm.coe.int (the downloader refuses anything else), so the body path is
pdftohtml -> page_paragraphs -> :func:`build_structure`.
"""

import json
import re

from ..lib import compress
from ..lib.coe import article_fragment
from ..lib.pdftext import page_paragraphs, pdf_pages
from ..lib.util import normalize_space
from .download import body_path, record_path
from .model import Treaty

RE_ARTICLE = re.compile(r"^Article\s+(\d+[A-Z]?)\s*(?:[–—-]\s*(.*))?$", re.I)
RE_DIVISION = re.compile(r"^(Chapter|Section|Part)\s+([IVXLCDM\d]+)\b(?:\s*[–—-]\s*)?(.*)$",
                         re.I)
# numbered paragraphs and lettered points open with '1.'/'(a)' in older
# texts and with the bare '1'/'a' of the current rm.coe.int layout
RE_PARAGRAPH = re.compile(r"^(\d{1,2})\.?\s+(.*)$", re.DOTALL)
RE_POINT = re.compile(r"^\(?([a-z])[.)]?\s+(.*)$", re.DOTALL)


def pdf_paragraphs(path, patch_key=None):
    return [(normalize_space(para.text), para.bold)
            for page, lines in pdf_pages(str(path), patch_key)
            for para in page_paragraphs(lines, None, page)
            if normalize_space(para.text)]


def _runs(text):
    return [text]


def build_structure(paragraphs):
    """Classified paragraphs to a tree with stable article/subarticle ids."""
    root = []
    article = paragraph = None
    article_children = paragraph_children = None
    loose = article_serial = 0
    for text, bold in paragraphs:
        division = RE_DIVISION.match(text)
        if division and (bold or text == text.upper()):
            root.append({"type": "rubrik", "level": 1, "text": _runs(text)})
            article = paragraph = None
            article_children = paragraph_children = None
            continue
        match = RE_ARTICLE.match(text)
        if match:
            number = match.group(1).lstrip("0")
            title = "Article %s" % number
            if match.group(2):
                title += " – " + match.group(2)
            article_children = []
            article = {"type": "artikel", "id": article_fragment(number),
                       "ordinal": number, "text": _runs(title),
                       "children": article_children}
            root.append(article)
            paragraph = None
            paragraph_children = None
            article_serial = 0
            continue
        numbered = RE_PARAGRAPH.match(text)
        if article and article_children is not None and numbered:
            number = numbered.group(1)
            paragraph_children = []
            paragraph = {"type": "stycke",
                         "id": article_fragment(article["ordinal"], number),
                         "ordinal": number, "text": _runs(numbered.group(2)),
                         "children": paragraph_children}
            article_children.append(paragraph)
            continue
        point = RE_POINT.match(text)
        if article and article_children is not None and point:
            letter = point.group(1)
            parent_number = paragraph.get("ordinal") if paragraph else None
            node = {"type": "punkt",
                    "id": article_fragment(article["ordinal"], parent_number, letter),
                    "ordinal": letter, "text": _runs(point.group(2))}
            (paragraph_children if paragraph_children is not None
             else article_children).append(node)
            continue
        node = {"type": "stycke", "text": _runs(text)}
        if article and article_children is not None:
            article_serial += 1
            node["id"] = "%sS%d" % (article["id"], article_serial)
            article_children.append(node)
        else:
            loose += 1
            node["id"] = "S%d" % loose
            root.append(node)
    if not any(node.get("type") == "artikel" for node in root):
        raise ValueError("official treaty text contains no Article headings")
    return root


def parse_record(record, paragraphs):
    treaty = Treaty(
        number=record["number"], title=record["title"],
        opening_date=record.get("opening_date"),
        opening_place=record.get("opening_place"),
        entry_into_force=record.get("entry_into_force"),
        reference=record.get("reference"), summary=record.get("summary"),
        source_url=record.get("source_url"),
        structure=build_structure(paragraphs),
    )
    return treaty.to_artifact()


def parse(basefile, root):
    record = json.loads(compress.read_text(record_path(root, basefile)))
    body = body_path(root, record)
    return parse_record(record, pdf_paragraphs(body, ("coe", basefile)))
