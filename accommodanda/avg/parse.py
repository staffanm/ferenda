"""Parsers for the avg organs: a harvested record -> :class:`Beslut` ->
JSON artifact.

**JO**: the decision body is the PDF (fetched by the harvester), read through
the shared font-aware extraction (`lib.pdftext`) -- the legacy jo.py knowledge
carries over as *conventions*, not coordinates: the first-page masthead
(address block), the margin Dnr/Datum and the ``Sid N (M)`` page headers are
noise; a bold paragraph is a section heading; ``Beslutet i korthet:`` opens the
abstract. Metadata comes from the search record (authoritative -- the site
curates it), never re-derived from the PDF. When the PDF is missing the
record's own flat text extraction (``pdf_text``) is the fallback body.

**IMY**: the decision is one or more PDFs the harvester has already grouped
under the diarienummer that names it, so parse assembles the body from those
parts and takes every field of metadata from the record (the tillsyn page
curates them; nothing is re-derived from the PDF). The layout is a two-column
one across both eras -- a narrow margin carrying "Diarienummer:"/"Datum:" and
the masthead, a wide body -- and the reading rules are font-driven rather than
positional: a paragraph smaller than the body size is a footnote or the
masthead, a paragraph carrying a "N (M)" page mark is a running header, and a
bold paragraph is a heading whose *level* is the rank of its font size (IMY
sets four, down to bold-at-body-size). Consecutive headings of one level are
one heading -- that is how a title set across three lines arrives.

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

from ..lib import compress, patch, poi, util
from ..lib.lagrum import ALL_PARSE_TYPES, sfs_parser
from ..lib.pdftext import (
    classify_letterhead,
    letterhead_footnotes,
    page_paragraphs,
    pages_with_ocr,
    pdf_pages,
)
from ..lib.util import (
    approximate_date,
    document_extension,
    normalize_space,
    record_path,
)
from .download import (
    arn_pdf_path,
    imy_pdf_path,
    jk_canonical,
    jk_html_path,
    jo_dnrs,
    jo_officialreport_path,
    jo_pdf_path,
    kkv_body_path,
)
from .model import ORG_NAME, Beslut, Block, Fotnot

AVG_PARSE_TYPES = ALL_PARSE_TYPES

ABSTRACT_PREFIX = "Beslutet i korthet:"

# JO PDF noise: the first-page masthead block, the margin Dnr/Datum column and
# the per-page "Sid N (M)" header ("Riksdagens ombudsmän" itself is stripped as
# the running header by page_paragraphs)
RE_JO_NOISE = re.compile(
    r"Sid \d+ \(\d+\)|Postadress:|Besöksadress:|Texttelefon:|Telefon:"
    r"|E-post:|justitieombudsmannen@jo\.se|www\.jo\.se"
    r"|^Dnr(\s|$)|^Datum(\s|$)|^BESLUT$|^\d+-\d{4}$|^\d{4}-\d{2}-\d{2}$")

JK_MONTHS = {m[:3]: i for m, i in util.MONTHS.items()}

RE_IMY_MARGIN = re.compile(
    r"^(?:Diarienummer|Ert diarienummer|Datum|Diarienr|Beslut)\s*:?$"
    r"|^(?:DI|IMY)-\d{4}-[\dX]+$|^\d{4}-\d{2}-\d{2}$|^\d{3,5}-\d{4}$")
# ... and the footer masthead, removed *in place*: it is set in the margin
# column, so wherever a footer line shares a baseline with a body line the two
# arrive glued into one paragraph ("avser gallring i misstankeregistret.
# www.imy.se") and dropping the paragraph would take the prose with it. Same
# reasoning as `classify_arn`'s margin header. Every token here is one IMY or
# Datainspektionen prints only in that block -- a colon-terminated label, the
# authority's own address, its e-mail, its bare domain -- so none of them can
# be mistaken for the decisions' prose about other websites.
RE_IMY_MASTHEAD = re.compile(
    r"\s*(?:Postadress:|Besöksadress:|Webbplats:|E-post:|Telefon:|Fax:"
    r"|Box 8114|104 20 Stockholm|Drottninggatan \d+"
    r"|www\.(?:imy|datainspektionen)\.se(?:/\S*)?"
    r"|imy@imy\.se|datainspektionen@datainspektionen\.se)\s*")

# KKV PDF noise. The letterhead sets the recipient block at the *body* size, so
# the fonts cannot separate it -- `classify_kkv_pdf` skips to the first heading
# instead (the subject line, which every template sets bold), the `classify_jo`
# idiom. What is left for the patterns is the margin's bare values and the
# Ringvägen/Torsgatan footer, which the column glues onto body lines.
RE_KKV_MARGIN = re.compile(
    r"^(?:BESLUT|YTTRANDE|SKRIVELSE|PROTOKOLL|Dnr|Datum)\s*:?$"
    r"|^Dnr \d+/\d{4}$|^\d{4}-\d{2}-\d{2}$")
RE_KKV_MASTHEAD = re.compile(
    r"\s*(?:Adress|Besöksadress|Telefon|Fax|E-post|Webbplats)\s"
    r"|\s*103 85 Stockholm|\s*118 60 Stockholm|\s*Ringvägen 100"
    r"|\s*Torsgatan 11|\s*08-700 16 00|\s*konkurrensverket@kkv\.se"
    r"|\s*www\.konkurrensverket\.se")
# the diarium's HTML decisions declare their encoding and mean it
RE_KKV_CHARSET = re.compile(rb"charset=([\w-]+)", re.I)
# a diarium HTML line short enough to head a section, and the shapes that rule
# it out: a digit anywhere (the ministry reference numbers and party addresses
# these letters are full of), a leading list dash, terminal sentence punctuation
KKV_HEADING_MAX = 60
RE_KKV_NOT_HEADING = re.compile(r"\d|^[-–•]|[.:;,!?]$")
# where a diarium HTML letter's prose begins: the letterhead above it is a run
# of short lines (document type, date, dnr, page mark, the recipient's address)
KKV_PROSE_MIN = 40
RE_KKV_HTML_NOISE = re.compile(r"^\d+\s*\(\s*\d+\s*\)$|^Dnr\b|^_{3,}$"
                               r"|^Sid(?:a)? \d+")


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


def jo_body(pdf_path, titel, patch_key=None):
    paras = [p for pageno, lines in pdf_pages(str(pdf_path), patch_key)
             for p in page_paragraphs(lines, "Riksdagens ombudsmän", pageno)]
    return classify_jo(paras, titel)


@functools.cache
def _officialreport_map(root):
    """The frozen corpus's dnr -> ämbetsberättelse-citation map (written by
    legacy.import_jo), or {} before any import has run. Cached per root: the
    map is import-time data, stable across a parse batch."""
    path = jo_officialreport_path(root)
    return json.loads(path.read_text("utf-8")) if path.exists() else {}


def parse_jo(record, root, patch_key=None):
    """A harvested JO search record (+ its PDF under `root`) -> Beslut."""
    dnrs = jo_dnrs(record.get("diary_number"))
    assert dnrs, "jo record %s carries no diarienummer" % record.get("id")
    # the ämbetsberättelse citation exists only in the frozen corpus (jo.se
    # does not publish it) -- grafted onto any record whose dnr the map knows
    report = next((_officialreport_map(str(root))[d] for d in dnrs
                   if d in _officialreport_map(str(root))), None)
    titel = _norm(BeautifulSoup(record.get("post_title") or "",
                                "html.parser").get_text(" ", strip=True))
    pdf = jo_pdf_path(root, "jo/" + dnrs[0])
    if pdf.exists():
        body, abstract = jo_body(pdf, titel, patch_key)
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
        official_report=report,
        nyckelord=list(record.get("matter_of_fact_names") or []),
        body=body, source_url=record.get("permalink"))


# --------------------------------------------------------------------------
# JK
# --------------------------------------------------------------------------

def jk_date(raw):
    """ISO date from jk.se's "20 apr 2026" display form, or None. A jk-legacy
    record carries the date already in ISO form (from the frozen distilled
    RDF) -- passed through."""
    raw = (raw or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return raw
    m = re.match(r"(\d{1,2})\s+([a-zåäö]{3})\w*\s+(\d{4})", raw, re.IGNORECASE)
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


def _jk_body_legacy_skin(soup):
    """The decision prose of a pre-2016 jk.se page (the ASP.NET skin some
    jk-legacy imports froze): the body is the heading/paragraph run that
    follows the ``div.beslutmetadatacontainer`` metadata box."""
    meta = soup.find("div", class_="beslutmetadatacontainer")
    assert meta is not None, \
        "jk landing page has neither div.date nor beslutmetadatacontainer"
    blocks = []
    for el in meta.find_next_siblings(["p", "h1", "h2", "h3"]):
        if el.name in ("h1", "h2", "h3"):
            text = _norm(el.get_text(" ", strip=True))
            if text:
                blocks.append(Block("rubrik", text, 1))
        else:
            block = _jk_block(el)
            if block:
                blocks.append(block)
    return blocks


def jk_body(html_text):
    """The decision prose of a jk.se landing page as typed blocks. The content
    column is the div carrying the ``div.date`` metadata row; the date row, the
    ``h2`` title and any action toolbars are not body. A frozen pre-2016 page
    (jk-legacy import) has neither -- routed to its own skin reader."""
    soup = BeautifulSoup(html_text, "html.parser")
    datediv = soup.find("div", class_="date")
    if datediv is None:
        return _jk_body_legacy_skin(soup)
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

def classify_arn(paras, dnr):
    """`lib.pdftext.Para`s -> body blocks (a bold paragraph is a heading, the
    rest running text). Pure over the Para stream so the rules are testable
    without poppler. A live arn.se PDF carries two noise shapes, both anchored
    to the referat's *own* änr so real citations to other decisions are never
    touched: the margin header "<own änr> <date>", which interleaves wherever
    a column boundary falls (line start or mid-sentence) and is removed
    in-place; and the front matter -- the curated summary (already the record
    title) restated in bold -- which ends at the "Beslut <date>; <own änr>"
    marker, everything through it dropped (the marker can also sit mid-para
    when the extraction glued it onto the summary; the remainder is body). The
    frozen Digiforms bodies carry neither pattern and pass unchanged."""
    margin = re.compile(r"\s*%s\s+\d{4}-\d{2}-\d{2}\s*" % re.escape(dnr))
    marker = re.compile(r"(?:^|\s)Beslut(?:et)?\s+\d{4}-\d{2}-\d{2}\s*;\s*%s\s*"
                        % re.escape(dnr))
    texts = [margin.sub(" ", _norm(p.text)).strip() for p in paras]
    start, remainder = 0, None
    for i, t in enumerate(texts[:12]):
        if m := marker.search(t):
            start, remainder = i, t[m.end():].strip()
            break
    blocks = []
    for j, (p, text) in enumerate(zip(paras[start:], texts[start:],
                                      strict=True)):
        if remainder is not None and j == 0:
            if remainder:     # body prose glued after the marker: never a
                blocks.append(Block("stycke", remainder))  # heading
            continue
        if not text:
            continue
        blocks.append(Block("rubrik", text, 1) if p.bold
                      else Block("stycke", text))
    return blocks


def parse_arn(record, root, patch_key=None):
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
    paras = [p for pageno, lines in pdf_pages(str(pdf), patch_key)
             for p in page_paragraphs(lines, ORG_NAME["arn"], pageno)]
    return Beslut(
        org="arn", diarienummer=[dnr], titel=_norm(record["title"]),
        beslutsdatum=record.get("beslutsdatum") or None,
        nyckelord=[record["avdelning"]] if record.get("avdelning") else [],
        body=classify_arn(paras, dnr), source_url=record.get("source_url"))


# --------------------------------------------------------------------------
# IMY
# --------------------------------------------------------------------------

def _classify_font_driven(paras, margin, masthead):
    """`lib.pdftext.Para`s -> body blocks, by the shared letterhead reading
    (:func:`lib.pdftext.classify_letterhead`): font rather than position, which
    is what both IMY's and Konkurrensverket's decisions want since both are set
    as a narrow margin column beside a wide body and neither marks its structure
    any other way. The rules moved to lib when the rs vertical (rättsliga
    ställningstaganden, the same agency-letter shape) became their second reader
    (rule:second-use-goes-to-lib); the shared function emits source-agnostic
    (kind, text, level) triples, which this maps onto the vertical's own
    :class:`Block`."""
    return [Block("stycke", text) if kind == "stycke"
            else Block("rubrik", text, level)
            for kind, text, level in classify_letterhead(paras, margin, masthead)]


def _footnotes_font_driven(paras, margin, masthead):
    """The notes the block classifier drops, as :class:`Fotnot`s -- see
    `lib.pdftext.letterhead_footnotes`.

    Wired for **imy** only. JO's and JK's templates set no notes and ARN's
    decisions arrive as one unbroken run of prose, so neither has any to
    collect; KKV does, but its three document formats go through one dispatcher
    (`kkv_read_document`) that would have to grow a third return value --
    recorded as open in `avg/KNOWN-GAPS.md`."""
    return [Fotnot(mark, text)
            for mark, text in letterhead_footnotes(paras, margin, masthead)]


def classify_imy(paras):
    """An IMY decision PDF's Paras -> body blocks. The margin column carries the
    "Diarienummer:"/"Datum:" labels with their values and the footer masthead;
    everything else is :func:`_classify_font_driven`'s general reading."""
    return _classify_font_driven(paras, RE_IMY_MARGIN, RE_IMY_MASTHEAD)


def imy_body(record, root, patch_key=None):
    """The decision's body: every Swedish part the harvester filed under this
    diarienummer, read in record order. An English translation carries the same
    dnr as the decision it translates and is skipped -- it is the same decision
    twice, and shipping both would double the text a search hit is scored on.
    A decision published as several parts (a beslut plus the tillsynsskrivelse
    that opened the ärende, or two nämnders beslut under one dnr) gets each
    part under a level-1 rubrik naming it, so the seam is visible."""
    parts = [d for d in record["delar"] if d["sprak"] == "sv"]
    blocks, notes = [], []
    for part in parts:
        pdf = imy_pdf_path(root, part["fil"])
        assert pdf.exists(), \
            "imy %s: part %s missing at %s" % (record["diarienummer"],
                                               part["fil"], pdf)
        if len(parts) > 1:
            blocks.append(Block("rubrik", part["titel"], 1))
        paras = [p for pageno, lines in pdf_pages(str(pdf), patch_key)
                 for p in page_paragraphs(lines, None, pageno)]
        blocks.extend(classify_imy(paras))
        notes.extend(_footnotes_font_driven(paras, RE_IMY_MARGIN,
                                            RE_IMY_MASTHEAD))
    return blocks, notes


def parse_imy(record, root, patch_key=None):
    """A harvested IMY record (+ its document PDFs under `root`) -> Beslut. The
    metadata is the tillsyn page's, verbatim: IMY curates the heading, the
    ingress and -- in the status block -- its own summary of what the decision
    found, which is the closest thing these decisions have to a referatrubrik.
    The etiketter are the keywords; the praxisbeslut fields and the
    sanktionsavgift ride along as metadata for the reader and the facets."""
    body, fotnoter = imy_body(record, root, patch_key)
    return Beslut(
        org="imy", diarienummer=[record["diarienummer"]],
        titel=record["titel"], beslutsdatum=record.get("beslutsdatum"),
        sammanfattning=record.get("sammanfattning"),
        nyckelord=list(record.get("kategorier") or []),
        body=body, fotnoter=fotnoter,
        source_url=record["tillsyner"][0]["url"],
        delar=[{"titel": d["titel"], "url": d["url"], "sprak": d["sprak"]}
               for d in record["delar"]],
        tillsyner=record["tillsyner"], praxis=record.get("praxis"),
        sanktionsavgift=record.get("sanktionsavgift"))


# --------------------------------------------------------------------------
# KKV
# --------------------------------------------------------------------------

def classify_kkv_pdf(paras):
    """A KKV decision PDF's `lib.pdftext.Para`s -> body blocks, by the same
    font-driven reading as `classify_imy`, but starting at the subject line.

    Konkurrensverket sets the recipient's address block at the *body* size, so
    unlike IMY's margin column the fonts cannot tell that front matter from the
    decision -- what can is that every template opens the decision proper with
    the subject line set bold. So the letterhead is everything above the first
    bold paragraph (the `classify_jo` idiom), and dropping it first also keeps
    it out of the body-size measurement, which the letterhead would otherwise
    skew on a short decision. A document with no bold line anywhere has no
    letterhead to find and is read whole."""
    start = next((i for i, p in enumerate(paras) if p.bold and p.text.strip()), 0)
    return _classify_font_driven(paras[start:], RE_KKV_MARGIN, RE_KKV_MASTHEAD)


def kkv_html_text(data):
    """The decoded text of a diarium HTML decision. The pre-2006 documents are
    FrontPage output and nearly every one declares -- and needs -- windows-1252;
    decoding them as UTF-8 would fail outright, so the encoding is asserted from
    the document's own declaration rather than sniffed.

    One document (04-0468) declares ``us-ascii`` instead. That is accepted, but
    only after confirming the bytes really are ASCII: ASCII is a strict subset
    of cp1252, so such a document decodes identically either way and there is
    nothing to mojibake -- while a us-ascii declaration over high bytes would be
    a document lying about itself, which is exactly what this guard is for."""
    declared = RE_KKV_CHARSET.search(data[:2048])
    # load-bearing: under -O an assert would vanish and the cp1252 decode below
    # would silently mojibake a document that had changed encoding, rather than
    # refusing it (rule:errors-drive-retry-use-raise)
    if not declared or declared.group(1).lower() not in (b"windows-1252",
                                                         b"iso-8859-1",
                                                         b"us-ascii"):
        raise ValueError(
            "kkv html body declares %r, not the windows-1252 the diarium "
            "publishes" % (declared and declared.group(1)))
    if declared.group(1).lower() == b"us-ascii" and not data.isascii():
        raise ValueError(
            "kkv html body declares us-ascii but carries %d non-ASCII byte(s), "
            "so its real encoding is unknown"
            % sum(1 for b in data if b > 127))
    return data.decode("cp1252")


def classify_kkv_html(html_text):
    """A diarium HTML decision -> (body blocks, sammanfattning). Pure over the
    markup so the rules are testable without network.

    These are letters typeset as layout tables by three generations of
    FrontPage, so no element carries meaning: the prose is in ``<td>``s and
    ``<p>``s indifferently, and the anchors that mark the fields
    (``zDnr``/``zz_1Dnr``/``zTypAvDokument``) differ per generation. What is
    stable is the *shape* -- a run of short letterhead lines (document type,
    date, dnr, page mark, the recipient's address) and then prose -- so the body
    starts at the first paragraph long enough to be prose and the letterhead
    above it is dropped whole. The oldest generation also opens with a curated
    ``ÄRENDE:``/``SAMMANF:`` table, which is not body at all: it is the
    diarium's own abstract, lifted out as the sammanfattning.

    Inside the body a short unpunctuated line with no digits in it, followed by
    a full paragraph, is a heading ("Saken", "Skäl för beslutet", "Ärendet") --
    these documents mark them by nothing else, not even bold."""
    soup = BeautifulSoup(html_text, "html.parser")
    for noise in soup(["script", "style"]):
        noise.decompose()
    body = soup.find("body")
    if body is None:                  # a remote document, not an invariant
        raise ValueError("kkv html body has no <body>")
    summary = _kkv_html_abstract(body)
    texts = []
    for el in body.find_all(["p", "td", "h1", "h2", "h3", "li"]):
        if el.find(["p", "td", "h1", "h2", "h3", "li"]):
            continue                      # a layout cell wrapping real blocks
        text = normalize_space(el.get_text(" ", strip=True))
        # the same string twice running is one cell nested in another's markup
        if text and (not texts or texts[-1] != text):
            texts.append(text)
    start = next((i for i, t in enumerate(texts) if len(t) >= KKV_PROSE_MIN), 0)
    texts = [t for t in texts[start:] if not RE_KKV_HTML_NOISE.match(t)]
    blocks = []
    for i, text in enumerate(texts):
        following = texts[i + 1] if i + 1 < len(texts) else None
        blocks.append(Block("rubrik", text, 1)
                      if _kkv_html_is_heading(text, following)
                      else Block("stycke", text))
    return blocks, summary


def _kkv_html_is_heading(text, following):
    """Whether a diarium HTML line is a section heading: short, unpunctuated,
    with no digit in it (which is what tells a heading from the ministry
    reference numbers and party addresses these letters are full of --
    "Ku2000/1259/Me", "NCC Construction AB, org. nr 556613-4929"), and followed
    by a paragraph it can head."""
    return (len(text) <= KKV_HEADING_MAX
            and not RE_KKV_NOT_HEADING.search(text)
            and following is not None and len(following) > KKV_HEADING_MAX)


def _kkv_html_abstract(body):
    """The diarium's own abstract, from the oldest generation's
    ``ÄRENDE:``/``SAMMANF:`` header table -- removed from the tree, since it is
    metadata the diarium wrote about the letter, not part of it. None where the
    generation has no such table."""
    anchor = body.find("a", attrs={"name": "sammanfattning"})
    if anchor is None:
        return None
    table = anchor.find_parent("table")
    if table is None:                 # a remote document, not an invariant
        raise ValueError("kkv html SAMMANF anchor sits outside a table")
    cells = [normalize_space(td.get_text(" ", strip=True))
             for td in table.find_all("td")]
    table.decompose()
    return next((cells[i + 1] for i, c in enumerate(cells[:-1])
                 if c.startswith("SAMMANF")), None)


def kkv_summary(record, root):
    """The separate sammanfattning document a few cases publish beside the
    decision ("26-0558s.pdf" beside "26-0558.pdf"), as text. Konkurrensverket
    writes it for the long decisions, so where it exists it is a better abstract
    than anything derivable from the decision itself; None where the case has
    none, which is almost all of them."""
    if "sammanfattning_dokument" not in record:
        return None
    path = kkv_body_path(root, record["sammanfattning_dokument"]["fil"])
    # the same invariant `kkv_body` relies on: the harvester drops the key for
    # a document it could not store, so a named document is a present one
    assert compress.exists(path), \
        "kkv %s: sammanfattning missing at %s" % (record["diarienummer"], path)
    blocks, _abstract = kkv_read_document(path, None)
    return normalize_space(" ".join(b.text for b in blocks)) or None


def kkv_read_document(path, patch_key):
    """One case document -> (blocks, the diarium's own abstract), routed on the
    format its bytes actually are."""
    data = compress.read_bytes(path)
    if document_extension(data) == ".pdf":
        # a handful of the diarium's PDFs are scans whose OCR layer poppler
        # renders invisible, so the shared OCR-aware reader is the PDF route
        return classify_kkv_pdf(
            [p for pageno, lines in pages_with_ocr(path, patch_key)
             for p in page_paragraphs(lines, ORG_NAME["kkv"], pageno)]), None
    if document_extension(data) in (".doc", ".docx"):
        return [Block("rubrik", p.text, 1) if p.bold else Block("stycke", p.text)
                for p in poi.read(path) if p.text.strip()], None
    # the HTML route's patchable intermediate is the decoded markup, as JK's is
    html = kkv_html_text(data)
    if patch_key is not None:
        html = patch.apply(patch_key[0], patch_key[1], html)
    return classify_kkv_html(html)


def kkv_body(record, root, patch_key=None):
    """The decision's body and the diarium's abstract, read by whichever of the
    three formats the diarium published this case in -- PDF (two thirds of the
    corpus), the FrontPage-era HTML (a third, pre-2006) or Word (two cases). A
    case that publishes no document at all (31 of 10,097) has an empty body: its
    record is still the register entry, which is what it is."""
    if "dokument" not in record:
        return [], None
    path = kkv_body_path(root, record["dokument"]["fil"])
    assert compress.exists(path), \
        "kkv %s: document missing at %s" % (record["diarienummer"], path)
    return kkv_read_document(path, patch_key)


def kkv_referat_blocks(referat):
    """Konkurrensverkets own account of a case -> body blocks. Its sections are
    the page's own headings ("Vad ärendet rör", "Varför ärendet prioriterats",
    "Konkurrensverkets beslut", "Tingsrätten", "Marknadsdomstolen"), so the
    account reads as the case's history by instance -- which is the one thing
    the decision document cannot contain, since it predates the courts that
    later reviewed it. It therefore heads the body, and the decision's own text
    follows under its own heading."""
    blocks = []
    for section in referat["avsnitt"]:
        if section["rubrik"]:
            blocks.append(Block("rubrik", section["rubrik"], 1))
        blocks.extend(Block("stycke", text) for text in section["stycken"])
    return blocks


def parse_kkv(record, root, patch_key=None):
    """A harvested KKV record (+ its decision document under `root`) -> Beslut.

    Every field of metadata is Konkurrensverkets own: the diarium is
    authoritative for what a case is, who it is against and when it was decided,
    and the curated ärendelista for what it was about, which branch it belongs
    to and what kinds of beslut it produced. Nothing is re-derived from the
    document. Where the case is curated, its account heads the body and the
    curated case name is the title -- the diarium's ärendemening is bureaucratic
    ("Anmälan om företagskoncentration - fjärrvärmerör") where the curated one
    names the case as it is known."""
    body, summary = kkv_body(record, root, patch_key)
    referat = record.get("referat")
    if referat:
        body = kkv_referat_blocks(referat) + body
    keywords = [record["arendetyp"]] if record.get("arendetyp") else []
    return Beslut(
        org="kkv", diarienummer=[record["diarienummer"]],
        titel=(referat["namn"] if referat else record["titel"]),
        beslutsdatum=record.get("beslutsdatum"),
        sammanfattning=(kkv_summary(record, root)
                        or (referat["ingress"] if referat else None) or summary),
        nyckelord=keywords + (referat["bransch"] if referat else []),
        arendetyp=record.get("arendetyp"), motpart=record.get("motpart"),
        bransch=referat["bransch"] if referat else [],
        beslutstyp=referat["beslutstyp"] if referat else [],
        referat_url=referat["url"] if referat else None,
        artal=referat["artal"] if referat else None,
        body=body, source_url=record["url"])


# --------------------------------------------------------------------------
# entry point (the build driver's recipe)
# --------------------------------------------------------------------------

def parse(basefile, root):
    """One basefile ("jo/2340-2025" / "jk/2024-8082" / "arn/1992-3657" /
    "imy/IMY-2024-2904" / "kkv/558/2026") -> artifact dict, body
    citation-scanned."""
    org = basefile.split("/", 1)[0]
    record = compress.read_json(record_path(root, org, basefile))
    patch_key = ("avg", basefile)
    if org == "jo":
        beslut = parse_jo(record, root, patch_key)
    elif org == "jk":
        # jk's intermediate is its landing-page HTML, not a PDF; patch it here
        html = compress.read_text(jk_html_path(root, basefile))
        beslut = parse_jk(record, patch.apply(*patch_key, html))
    elif org == "imy":
        beslut = parse_imy(record, root, patch_key)
    elif org == "kkv":
        beslut = parse_kkv(record, root, patch_key)
    else:
        beslut = parse_arn(record, root, patch_key)
    # the decision's own date, so a bare law name resolves to the act in force
    # when it was written rather than to whatever replaced it since
    return beslut.to_artifact(sfs_parser("avg", AVG_PARSE_TYPES,
                                         written=approximate_date(beslut.beslutsdatum)))
