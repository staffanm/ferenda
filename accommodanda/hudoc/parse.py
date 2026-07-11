"""HUDOC metadata + converted Word HTML to :class:`HudocCase` artifacts."""

import json
import re
from datetime import datetime

from bs4 import BeautifulSoup

from ..lib import compress, patch
from ..lib.errors import SkipDocument
from ..lib.util import normalize_space
from .download import body_path, record_path
from .model import Block, HudocCase

RE_NUMBERED = re.compile(r"^(\d+)\.\s+(.*)$", re.DOTALL)
RE_HEADING_PREFIX = re.compile(r"^(?:[IVXLCDM]+|[A-Z]|\d+)\.\s+")
RE_STYLE = re.compile(r"\.([\w-]+)\s*\{([^}]*)\}")
RE_INTERNAL_LINK = re.compile(r"^#")
SKIP_EXACT = {
    "TABLE OF CONTENTS", "JUDGMENT", "DECISION", "STRASBOURG",
    "GRAND CHAMBER", "CHAMBER", "FINAL",
}
HEADING_EXACT = {
    "PROCEDURE", "THE FACTS", "THE LAW", "AS TO THE LAW", "COMPLAINTS",
    "LEGAL FRAMEWORK", "RELEVANT LEGAL FRAMEWORK", "APPENDIX", "ANNEX",
}


def _styles(soup):
    """Generated Word HTML uses opaque classes instead of semantic tags."""
    return {
        name: declarations.lower()
        for style in soup.find_all("style")
        for name, declarations in RE_STYLE.findall(style.get_text())
    }


def _css(element, styles, descendants=False):
    nodes = [element]
    if descendants:
        nodes.extend(element.find_all(True))
    return " ".join(styles.get(name, "")
                    for node in nodes for name in node.get("class", []))


def _heading(paragraph, text, styles):
    toc_anchor = paragraph.find("a", attrs={"name": re.compile(r"^_Toc")})
    styled = paragraph.find(["strong", "b"])
    css = _css(paragraph, styles, descendants=True)
    paragraph_css = _css(paragraph, styles)
    bold = styled or re.search(r"font-weight\s*:\s*(?:bold|[6-9]00)", css)
    avoids_break = re.search(r"page-break-after\s*:\s*avoid", paragraph_css)
    uppercase = text == text.upper() and any(char.isalpha() for char in text)
    upper = text.upper()
    if upper in SKIP_EXACT:
        return None
    known = (upper in HEADING_EXACT or upper.startswith("FOR THESE REASONS")
             or " OPINION" in upper)
    prefix = RE_HEADING_PREFIX.match(text)
    if not (toc_anchor or known or (uppercase and avoids_break)
            or (bold and prefix and len(text) < 180)):
        return None
    if prefix:
        marker = prefix.group(0).strip()
        if marker[0].isdigit():
            return 3
        if marker[0] in "IVXLCDM":
            return 1
        return 2
    return 1


def _toc_entry(paragraph, text):
    link = paragraph.find("a", href=RE_INTERNAL_LINK)
    return bool(link and not link["href"].lower().startswith("#_ftn")
                and normalize_space(link.get_text(" ", strip=True)) == text)


def parse_body(html_text):
    soup = BeautifulSoup(html_text, "html.parser")
    styles = _styles(soup)
    for element in soup.find_all(["style", "script"]):
        element.decompose()
    blocks = []
    for paragraph in soup.find_all("p"):
        text = normalize_space(paragraph.get_text(" ", strip=True))
        # The generated TOC can share its enclosing div with the whole judgment;
        # remove only its linked entries, never the container.
        if (not text or text.upper() in SKIP_EXACT
                or _toc_entry(paragraph, text)):
            continue
        footnote = paragraph.find_parent(id=re.compile(r"^_ftn\d+$"))
        if footnote:
            blocks.append(Block("note", text))
            continue
        level = _heading(paragraph, text, styles)
        if level:
            blocks.append(Block("rubrik", text, level=level))
            continue
        numbered = RE_NUMBERED.match(text)
        if numbered:
            blocks.append(Block("stycke", numbered.group(2),
                                number=numbered.group(1)))
        else:
            blocks.append(Block("stycke", text))
    return blocks


def _date(record):
    for key in ("judgementdate", "decisiondate", "kpdate"):
        value = record.get(key) or ""
        if re.match(r"\d{4}-\d{2}-\d{2}", value):
            return value[:10]
        if value:
            return datetime.strptime(value[:10], "%d/%m/%Y").date().isoformat()
    return None


def _split(value):
    return [item.strip() for item in (value or "").split(";") if item.strip()]


def parse_record(record, html_text):
    body = parse_body(html_text)
    if not any(block.number for block in body):
        raise SkipDocument("%s: HUDOC judgment body contains no numbered paragraphs"
                           % record["itemid"])
    return HudocCase(
        itemid=record["itemid"],
        title=normalize_space(record.get("docname")),
        collection=record.get("documentcollectionid2") or "",
        language=record.get("languageisocode") or "",
        date=_date(record),
        application_numbers=_split(record.get("appno")),
        ecli=record.get("ecli") or None,
        respondent=record.get("respondent") or None,
        originating_body=record.get("originatingbody") or None,
        importance=record.get("importance") or None,
        article_codes=_split(record.get("article")),
        conclusions=_split(record.get("conclusion")),
        body=body,
    )


def parse(basefile, root):
    record = json.loads(compress.read_text(record_path(root, basefile)))
    html = patch.apply("hudoc", basefile,
                       compress.read_text(body_path(root, basefile)))
    return parse_record(record, html).to_artifact()
