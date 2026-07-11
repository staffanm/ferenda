"""HUDOC metadata + converted Word HTML to :class:`HudocCase` artifacts."""

import json
import re
from datetime import datetime

from bs4 import BeautifulSoup

from ..lib import compress, patch
from ..lib.util import normalize_space
from .download import body_path, record_path
from .model import Block, HudocCase

RE_NUMBERED = re.compile(r"^(\d+)\.\s+(.*)$", re.DOTALL)
RE_HEADING_PREFIX = re.compile(r"^(?:[IVXLCDM]+|[A-Z]|\d+)\.\s+")
SKIP_EXACT = {
    "TABLE OF CONTENTS", "JUDGMENT", "DECISION", "STRASBOURG",
    "GRAND CHAMBER", "CHAMBER",
}


def _drop_contents(soup):
    """Remove a generated table-of-contents div, whose links duplicate headings."""
    for div in soup.find_all("div"):
        first = div.find("p")
        if first and normalize_space(first.get_text(" ", strip=True)).upper() == "TABLE OF CONTENTS":
            div.decompose()
            return


def _heading(paragraph, text):
    toc_anchor = paragraph.find("a", attrs={"name": re.compile(r"^_Toc")})
    styled = paragraph.find(["strong", "b"])
    uppercase = text == text.upper() and any(char.isalpha() for char in text)
    if not toc_anchor and not (styled and uppercase and len(text) < 180):
        return None
    if text.upper() in SKIP_EXACT:
        return None
    prefix = RE_HEADING_PREFIX.match(text)
    if prefix:
        marker = prefix.group(0).strip()
        if marker[0].isdigit():
            return 3
        if marker[0] in "IVXLCDM":
            return 1
        return 2
    return 1


def parse_body(html_text):
    soup = BeautifulSoup(html_text, "html.parser")
    for element in soup.find_all(["style", "script"]):
        element.decompose()
    _drop_contents(soup)
    blocks = []
    for paragraph in soup.find_all("p"):
        text = normalize_space(paragraph.get_text(" ", strip=True))
        if not text or text.upper() in SKIP_EXACT:
            continue
        footnote = paragraph.find_parent(id=re.compile(r"^_ftn\d+$"))
        if footnote:
            blocks.append(Block("note", text))
            continue
        level = _heading(paragraph, text)
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
        body=parse_body(html_text),
    )


def parse(basefile, root):
    record = json.loads(compress.read_text(record_path(root, basefile)))
    html = patch.apply("hudoc", basefile,
                       compress.read_text(body_path(root, basefile)))
    return parse_record(record, html).to_artifact()
