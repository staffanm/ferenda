"""ICC stored records (Legal Tools metadata + decision PDF) to artifacts.

Metadata comes from the resolved Legal Tools record, with the ICC-listing scrape
as fallback; the article tree is the decision PDF's numbered paragraphs, with the
per-page court-record running header dropped. A record Legal Tools could not
resolve stays metadata-only (empty structure), like a status record.
"""

import re

from ..lib import compress
from ..lib.pdftext import join_across_pages, page_paragraphs, pages_with_ocr
from ..lib.util import normalize_space
from .download import _iso, body_path, record_path
from .model import RE_CASE, Block, Decision

# the running header the ICC stamps on every court-record page, e.g.
# "ICC-01/04-02/06-2659 08-03-2021 5/97 RH"
RE_HEADER = re.compile(r"^ICC-\S+\s+\d\d-\d\d-\d{4}\s+\d+/\d+\s+[A-Z]{1,3}\b")
RE_NUMBERED = re.compile(r"^(\d{1,4})\.\s+(.*)$", re.DOTALL)
# A roman-numeral section head ("III. THE CHARGES"), written as the numeral
# grammar and not as the set of letters a numeral is spelt from: "[IVXLC]+"
# also matches "ICC", so every sentence a page break dropped onto the court's
# own abbreviation -- "ICC. This made the investigators' job particularly
# delicate and it" -- read as a section heading.
RE_ROMAN_HEAD = re.compile(r"^(?=[IVXLC])(?:X{0,3}(?:IX|IV|V?I{0,3}))\.\s+[A-Z]")
# a court paragraph number *inside* a block. The scans carry no bold or size
# signal, so `page_paragraphs` glues a heading to the paragraphs under it
# wherever the gap below the heading is small, and the whole run then reads as
# one heading (1969 characters of it in ICC-01/04-01/10-1) -- which loses every
# citation anchor inside it. The paragraph number is the seam to cut on.
RE_EMBEDDED_NUMBER = re.compile(r"\s(\d{1,4})\.\s+[A-Z]")
# an all-caps *word*: at least two capitals, no lowercase letter and no digit.
# Matched against one whitespace-separated token, never against the line -- an
# exhibit id ("EVD-OTP-00570.", "EVD-T-OTP-00711/CAR-OTP-0017-0358.") holds no
# lowercase letter either, and is not a word.
RE_CAPWORD = re.compile(r"^[^a-z\d]*[A-Z]{2,}[^a-z\d]*$")
# the enumerator the court sets a subsection under ("C. PILLAGING"). It counts
# as one of the heading's words: a subsection whose title is a single word reads
# as debris otherwise. "[IVXLC]+" used to admit these by accident, for the
# letters that happen to spell a numeral -- "C. PILLAGING" was a heading and
# "A. MURDER", four lines above it in ICC-01/05-01/08-3343, was not.
RE_ENUMERATOR = re.compile(r"^[A-Z]\.\s")
# The roman branch needs a length, or a run of prose the split could not cut
# reads as a heading ("I. In proceedings leading up to the confirmation of the
# charges against Mr. Katanga …", 1064 characters in ICC-01/04-01/07-573). The
# longest heading this lets through is 194 characters; raising it to 300 admits
# three more real headings and two tables of contents, so it stays here.
HEADING_MAX = 200
# A quotation mark the Legal Tools record types twice ('entitled ""Décision sur
# la demande de mise en liberté provisoire de Thomas Lubanga Dyilo"'). A
# doubled delimiter is a typing slip, not something the title says, so it is
# collapsed. The *unbalanced* quote 16 other titles carry is left alone: those
# are cut off at the source, which marks the cut itself ("… stay the
# prosecution [ ... ]"), and closing the quotation would assert a title
# boundary the record does not have.
RE_DOUBLED_QUOTE = re.compile(r'"{2,}')


def _capwords(text):
    """The all-caps words in a line, counted as tokens. Counting runs of
    capitals instead read one hyphenated identifier as several words:
    "EVD-OTP-00570." holds two runs ("EVD", "OTP") and neither is a word."""
    return sum(1 for token in text.split() if RE_CAPWORD.match(token))


def _is_heading(text):
    """A section heading: a roman-numeral head ("III. THE CHARGES") or an all-caps
    line of at least two real words (an enumerator counting as one) -- the second
    guard keeps the debris a scanned footnote leaves behind out of the tree,
    whether it is a note number ("DRC. 3") or an exhibit id ("EVD-OTP-00570.")."""
    if RE_ROMAN_HEAD.match(text):
        return len(text) <= HEADING_MAX
    return (text == text.upper() and 8 <= len(text) <= 70
            and _capwords(text) + bool(RE_ENUMERATOR.match(text)) >= 2)


def _split_heading(raw):
    """One reflowed paragraph as the blocks it holds: itself, normally -- but a
    heading `page_paragraphs` glued to the paragraphs under it is cut at the
    first paragraph number, so that the heading is a heading and the court's
    numbered paragraph is a numbered paragraph (with the anchor a citation
    needs) rather than prose inside a 1969-character rubrik.

    The cut is made only where the text before the number is *itself* a
    heading, which is what keeps a year in ordinary prose ("… in 2007. The
    Chamber …") from cutting a paragraph in half."""
    text = normalize_space(raw)
    embedded = RE_EMBEDDED_NUMBER.search(text)
    if (embedded and not RE_NUMBERED.match(text)
            and _is_heading(text[:embedded.start()])):
        return [text[:embedded.start()], text[embedded.start(1):]]
    return [text]


def _classify(texts):
    """Paragraph texts -> classified blocks: numbered paragraphs keep their number
    (the ICC citation unit), section headings become rubriker, the rest stycken;
    the per-page court-record running header is dropped."""
    blocks = []
    for raw in texts:
        for text in _split_heading(raw):
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


def _blocks(path, basefile):
    """The decision PDF's paragraphs, classified.

    Through `pages_with_ocr`, not `pdf_pages`: the court files its records as
    scans carrying an *invisible* OCR text layer, which poppler omits unless
    asked for it. Reading only the visible layer returned one line per page --
    the court's own "ICC-01/04-01/06-1432 11-07-2008 5/44" stamp -- so 118 of
    269 decisions parsed to nothing at all while their text sat in the PDF
    already on disk (2 132 331 bytes of it, for that one). English, so the OCR
    fallback for a scan with no layer at all runs in English.

    The two halves of a sentence a page break split are rejoined, as in a
    remissvar: a court record breaks mid-sentence on every page. The running
    header has to go *before* the join -- it is the first paragraph of every
    page, so leaving it in place puts a filing stamp between the two halves of
    every sentence and nothing ever rejoins."""
    return _classify(join_across_pages(
        [[text for text in (normalize_space(para.text)
                            for para in page_paragraphs(lines, None, page))
          if text and not RE_HEADER.match(text)]
         for page, lines in pages_with_ocr(str(path), ("icc", basefile),
                                           lang="eng")]))


def parse(basefile, root):
    record = compress.read_json(record_path(root, basefile))
    lt = record.get("lt") or {}          # lt is legitimately None (unresolved)
    icc = record["icc"]                  # always written by the downloader
    base = record["base"]
    body = body_path(root, basefile)
    case = RE_CASE.search(base)
    return Decision(
        doc_number=base,
        title=RE_DOUBLED_QUOTE.sub('"', lt.get("title")
                                   or icc.get("title") or "Decision"),
        case_name=lt.get("caseName") or icc.get("case_name") or base,
        case_number=lt.get("caseNumber") or (case.group(0) if case else base),
        decision_type=record["kind"],
        date=(lt.get("dateCreated") or "")[:10] or _iso(icc.get("date")),
        chamber=icc.get("chamber") or lt.get("source"),
        slug=lt.get("slug"),
        body=_blocks(body, basefile) if compress.exists(body) else [],
    ).to_artifact()
