"""Parsers for the two avg organs: a harvested record -> :class:`Beslut` ->
JSON artifact.

**JO**: the decision body is the PDF (fetched by the harvester), read through
the shared font-aware extraction (`lib.pdftext`) -- the legacy jo.py knowledge
carries over as *conventions*, not coordinates: the first-page masthead
(address block), the margin Dnr/Datum and the ``Sid N (M)`` page headers are
noise; a bold paragraph is a section heading; ``Beslutet i korthet:`` opens the
abstract. Metadata comes from the search record (authoritative -- the site
curates it), never re-derived from the PDF. When the PDF is missing the
record's own flat text extraction (``pdf_text``) is the fallback body.

**JK**: the decision *is* its landing page. ``div.content`` carries the prose;
the legacy jk.py section signals hold on the current site: a paragraph that is
entirely ``<strong>`` is a section heading ("Justitiekanslerns beslut",
"Ärendet"), entirely ``<em>`` a subsection ("Bakgrund"), ``h3`` a section.

Both bodies are citation-scanned with the shared engine (the DV parse-type
set), so a decision's lagrum/rättsfall/förarbete references join the corpus
graph -- and other documents' MYNDIGHETSBESLUT citations to a JO/JK decision
now resolve to these pages.
"""

import functools
import json
import re

from bs4 import BeautifulSoup

from ..lib.datasets import NAMEDLAWS as SFS_NAMEDLAWS
from ..lib.lagrum import (
    EULAGSTIFTNING,
    EURATTSFALL,
    FORARBETEN,
    KORTLAGRUM,
    LAGRUM,
    MYNDIGHETSBESLUT,
    RATTSFALL,
    LagrumParser,
    load_abbreviations,
    load_namedlaws,
)
from ..lib.pdftext import page_paragraphs, pdf_pages
from ..lib.util import record_path
from .download import jk_canonical, jk_html_path, jo_dnrs, jo_pdf_path
from .legacy import arn_pdf_path
from .model import ORG_NAME, Beslut, Block

AVG_PARSE_TYPES = [LAGRUM, KORTLAGRUM, EULAGSTIFTNING, RATTSFALL, FORARBETEN,
                   EURATTSFALL, MYNDIGHETSBESLUT]

ABSTRACT_PREFIX = "Beslutet i korthet:"

# JO PDF noise: the first-page masthead block, the margin Dnr/Datum column and
# the per-page "Sid N (M)" header ("Riksdagens ombudsmän" itself is stripped as
# the running header by page_paragraphs)
RE_JO_NOISE = re.compile(
    r"Sid \d+ \(\d+\)|Postadress:|Besöksadress:|Texttelefon:|Telefon:"
    r"|E-post:|justitieombudsmannen@jo\.se|www\.jo\.se"
    r"|^Dnr(\s|$)|^Datum(\s|$)|^BESLUT$|^\d+-\d{4}$|^\d{4}-\d{2}-\d{2}$")

JK_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "maj": 5, "jun": 6,
             "jul": 7, "aug": 8, "sep": 9, "okt": 10, "nov": 11, "dec": 12}


@functools.cache
def _refparser():
    return LagrumParser(load_namedlaws(SFS_NAMEDLAWS), basefile="avg",
                        abbreviations=load_abbreviations(SFS_NAMEDLAWS),
                        parse_types=AVG_PARSE_TYPES)


def _fresh_parser():
    """The shared parser with document-lifetime state reset (so one decision's
    'samma lag' / learned law names do not bleed into the next)."""
    parser = _refparser()
    parser.state = type(parser.state)()
    return parser


def _norm(s):
    return re.sub(r"\s+", " ", (s or "")).strip()


# --------------------------------------------------------------------------
# JO
# --------------------------------------------------------------------------

def classify_jo(paras, titel):
    """`lib.pdftext.Para`s -> (body blocks, sammanfattning). Pure over the Para
    stream so the rules are testable without poppler. The document's own title
    is dropped (it is the metadata title) -- the PDF sets it as a *sequence* of
    bold lines, each its own Para, so it is consumed as a running prefix of the
    known title, not matched whole."""
    blocks, abstract = [], None
    title_left = _norm(titel).lower()
    # everything before the title is front matter (the merged masthead line,
    # the deciding ombudsman's byline, margin Dnr/Datum) -- skip to the title's
    # first bold line; a PDF where the title is never found classifies whole
    start = next((i for i, p in enumerate(paras) if p.bold and title_left
                  and title_left.startswith(_norm(p.text).lower())), 0)
    for p in paras[start:]:
        text = _norm(p.text)
        if not text or RE_JO_NOISE.search(text):
            continue
        if title_left and p.bold \
                and title_left.startswith(text.lower()):
            title_left = title_left[len(text):].strip()
            continue
        title_left = ""      # first non-title para: stop consuming
        if text.startswith(ABSTRACT_PREFIX):
            abstract = text[len(ABSTRACT_PREFIX):].strip()
            continue
        if p.bold:
            blocks.append(Block("rubrik", text, 1))
        else:
            blocks.append(Block("stycke", text))
    return blocks, abstract


def jo_body(pdf_path, titel):
    paras = [p for pageno, lines in pdf_pages(str(pdf_path))
             for p in page_paragraphs(lines, "Riksdagens ombudsmän", pageno)]
    return classify_jo(paras, titel)


def parse_jo(record, root):
    """A harvested JO search record (+ its PDF under `root`) -> Beslut."""
    dnrs = jo_dnrs(record.get("diary_number"))
    assert dnrs, "jo record %s carries no diarienummer" % record.get("id")
    titel = _norm(BeautifulSoup(record.get("post_title") or "",
                                "html.parser").get_text(" ", strip=True))
    pdf = jo_pdf_path(root, "jo/" + dnrs[0])
    if pdf.exists():
        body, abstract = jo_body(pdf, titel)
    else:
        # no PDF on disk: the record's own flat extraction, one preformatted
        # block (paragraph structure is not recoverable from it)
        text = _norm(re.sub(r"^\[P\]\s*", "", record.get("pdf_text") or ""))
        body, abstract = ([Block("stycke", text)] if text else []), None
    summary = abstract or _norm(BeautifulSoup(
        record.get("post_content") or "", "html.parser").get_text(" ", strip=True))
    return Beslut(
        org="jo", diarienummer=dnrs, titel=titel,
        beslutsdatum=record.get("resolve_date") or None,
        sammanfattning=summary or None,
        avgjord_av=_norm(record.get("resolve_maker")) or None,
        nyckelord=list(record.get("matter_of_fact_names") or []),
        body=body, source_url=record.get("permalink"))


# --------------------------------------------------------------------------
# JK
# --------------------------------------------------------------------------

def jk_date(raw):
    """ISO date from jk.se's "20 apr 2026" display form, or None."""
    m = re.match(r"(\d{1,2})\s+([a-zåäö]{3})\w*\s+(\d{4})", (raw or "").strip(),
                 re.IGNORECASE)
    if not m or m.group(2).lower() not in JK_MONTHS:
        return None
    return "%04d-%02d-%02d" % (int(m.group(3)), JK_MONTHS[m.group(2).lower()],
                               int(m.group(1)))


def jk_dnrs(raw):
    """Every diarienummer a raw jk.se value names, canonicalized; first is the
    canonical one (multi-dnr decisions come ";"/","-separated)."""
    return [jk_canonical(part) for part in re.split(r"[;,]", raw or "")
            if part.strip()]


def _jk_block(el):
    text = _norm(el.get_text(" ", strip=True))
    if not text:
        return None
    if el.name == "h3":
        return Block("rubrik", text, 1)
    strong, em = el.find("strong"), el.find("em")
    if strong and _norm(strong.get_text(" ", strip=True)) == text:
        return Block("rubrik", text, 1)
    if em and _norm(em.get_text(" ", strip=True)) == text:
        return Block("rubrik", text, 2)
    return Block("stycke", text)


def jk_body(html_text):
    """The decision prose of a jk.se landing page as typed blocks. The content
    column is the div carrying the ``div.date`` metadata row; the date row, the
    ``h2`` title and any action toolbars are not body."""
    soup = BeautifulSoup(html_text, "html.parser")
    datediv = soup.find("div", class_="date")
    assert datediv is not None, "jk landing page has no div.date -- site changed?"
    content = datediv.parent
    assert content is not None, "div.date has no enclosing content column"
    for noise in content.find_all("div", class_=("date", "actions")):
        noise.decompose()
    h2 = content.find("h2")
    if h2:
        h2.decompose()
    blocks = []
    for el in content.find_all(["p", "h3"]):
        if el.find(["p", "h3"]):
            continue      # a wrapper around real blocks (jk.se nests <p><p>)
        block = _jk_block(el)
        if block:
            blocks.append(block)
    return blocks


def parse_jk(record, html_text):
    """A harvested JK record + its landing page -> Beslut."""
    dnrs = jk_dnrs(record["diarienummer_raw"])
    return Beslut(
        org="jk", diarienummer=dnrs, titel=_norm(record["title"]),
        beslutsdatum=jk_date(record.get("beslutsdatum_raw")),
        body=jk_body(html_text), source_url=record.get("url"))


# --------------------------------------------------------------------------
# ARN (frozen corpus imported by avg/legacy.py)
# --------------------------------------------------------------------------

def classify_arn(paras):
    """`lib.pdftext.Para`s -> body blocks (a bold paragraph is a heading, the
    rest running text). Pure over the Para stream so the rule is testable without
    poppler. ARN referat carry no known masthead noise, so nothing is filtered."""
    return [Block("rubrik", _norm(p.text), 1) if p.bold
            else Block("stycke", _norm(p.text))
            for p in paras if _norm(p.text)]


def parse_arn(record, root):
    """An ARN record (+ its decision PDF under `root`) -> Beslut. One path for
    both provenances: a frozen-corpus import (`avg/legacy.py`, no ``source_url``)
    and a live arn.se harvest (`avg/download.py`, carrying the referat's live PDF
    URL as ``source_url``). ARN referat have no real title -- the summary
    paragraph is the title (a frozen fragment's, sanitized at import time; a live
    listing's, the ARN-curated summary). The body is the decision PDF read through
    the shared font-aware extraction; the Avdelning is the one keyword."""
    dnr = record["diarienummer"]
    pdf = arn_pdf_path(root, "arn/" + dnr)
    assert pdf.exists(), "arn %s has no body PDF at %s" % (dnr, pdf)
    paras = [p for pageno, lines in pdf_pages(str(pdf))
             for p in page_paragraphs(lines, ORG_NAME["arn"], pageno)]
    return Beslut(
        org="arn", diarienummer=[dnr], titel=_norm(record["title"]),
        beslutsdatum=record.get("beslutsdatum") or None,
        nyckelord=[record["avdelning"]] if record.get("avdelning") else [],
        body=classify_arn(paras), source_url=record.get("source_url"))


# --------------------------------------------------------------------------
# entry point (the build driver's recipe)
# --------------------------------------------------------------------------

def parse_record(basefile, root):
    """One basefile ("jo/2340-2025" / "jk/2024-8082" / "arn/1992-3657") ->
    artifact dict, body citation-scanned."""
    org = basefile.split("/", 1)[0]
    record = json.loads(record_path(root, org, basefile).read_text())
    if org == "jo":
        beslut = parse_jo(record, root)
    elif org == "jk":
        beslut = parse_jk(record, jk_html_path(root, basefile).read_text())
    else:
        beslut = parse_arn(record, root)
    return beslut.to_artifact(_fresh_parser())
