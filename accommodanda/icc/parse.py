"""ICC stored records (Legal Tools metadata + decision PDF) to artifacts.

Metadata comes from the resolved Legal Tools record, with the ICC-listing scrape
as fallback; the article tree is the decision PDF's numbered paragraphs, with the
per-page court-record running header dropped. A record Legal Tools could not
resolve stays metadata-only (empty structure), like a status record.
"""

import json
import re

from ..lib import compress
from ..lib.pdftext import page_paragraphs, pdf_pages
from ..lib.util import normalize_space
from .download import _iso, body_path, record_path
from .model import RE_CASE, Block, Decision

# the running header the ICC stamps on every court-record page, e.g.
# "ICC-01/04-02/06-2659 08-03-2021 5/97 RH"
RE_HEADER = re.compile(r"^ICC-\S+\s+\d\d-\d\d-\d{4}\s+\d+/\d+\s+[A-Z]{1,3}\b")
RE_NUMBERED = re.compile(r"^(\d{1,4})\.\s+(.*)$", re.DOTALL)
RE_ROMAN_HEAD = re.compile(r"^[IVXLC]+\.\s+[A-Z]")
RE_CAPWORD = re.compile(r"[A-Z]{2,}")


def _is_heading(text):
    """A section heading: a roman-numeral head ("III. THE CHARGES") or an all-caps
    line of at least two real words -- the second guard keeps footnote debris
    ("DRC. 3") out of the tree."""
    if RE_ROMAN_HEAD.match(text):
        return True
    return (text == text.upper() and 8 <= len(text) <= 70
            and len(RE_CAPWORD.findall(text)) >= 2)


def _classify(texts):
    """Paragraph texts -> classified blocks: numbered paragraphs keep their number
    (the ICC citation unit), section headings become rubriker, the rest stycken;
    the per-page court-record running header is dropped."""
    blocks = []
    for raw in texts:
        text = normalize_space(raw)
        if not text or RE_HEADER.match(text):
            continue
        numbered = RE_NUMBERED.match(text)
        if numbered:
            blocks.append(Block("stycke", numbered.group(2),
                                number=numbered.group(1)))
        elif _is_heading(text):
            blocks.append(Block("rubrik", text))
        else:
            blocks.append(Block("stycke", text))
    return blocks


def _blocks(path):
    """The decision PDF's paragraphs, classified."""
    return _classify(para.text for page, lines in pdf_pages(str(path), None)
                     for para in page_paragraphs(lines, None, page))


def parse(basefile, root):
    record = json.loads(compress.read_text(record_path(root, basefile)))
    lt = record.get("lt") or {}          # lt is legitimately None (unresolved)
    icc = record["icc"]                  # always written by the downloader
    base = record["base"]
    body = body_path(root, basefile)
    case = RE_CASE.search(base)
    return Decision(
        doc_number=base,
        title=lt.get("title") or icc.get("title") or "Decision",
        case_name=lt.get("caseName") or icc.get("case_name") or base,
        case_number=lt.get("caseNumber") or (case.group(0) if case else base),
        decision_type=record["kind"],
        date=(lt.get("dateCreated") or "")[:10] or _iso(icc.get("date")),
        chamber=icc.get("chamber") or lt.get("source"),
        slug=lt.get("slug"),
        body=_blocks(body) if compress.exists(body) else [],
    ).to_artifact()
