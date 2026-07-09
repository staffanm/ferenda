"""Parse a preparatory work (förarbete) PDF into the Forarbete model and project
it to a JSON artifact.

Text is extracted with poppler's `pdftotext` (plain, reading-order mode -- it
isolates the running header and page number on their own lines, unlike
`-layout` which mashes them into the body in the alternating outer margin).
Each PDF page maps to one printed page (modern regeringen.se PDFs number from
the title page), so the PDF page index is the `#sid{N}` anchor förarbete
citations resolve to ("prop. 1997/98:45 s. 39" -> `prop/1997/98:45#sid39`).

The document URI is minted to the same form the FORARBETEN citation grammar
produces (`prop/{riksmöte}:{no}`, `sou/{year}:{no}`, …), so a citation to this
document and the document itself agree by construction -- the lesson from the DV
case-URI work. Body blocks are scanned for citations (SFS / other förarbeten /
case law) and carry inline links, like SFS and DV.
"""

import functools
import re
import subprocess
from pathlib import Path

from bs4 import BeautifulSoup

from .. import config
from ..lib import compress, layout
from ..lib.datasets import NAMEDLAWS as SFS_NAMEDLAWS
from ..lib.lagrum import (
    ALL_PARSE_TYPES,
    LagrumParser,
    interleave,
    load_abbreviations,
    load_namedlaws,
)

# font-aware extraction + paragraph reflow are shared across the PDF verticals
# (re-exported here so this module's existing import sites keep working)
from ..lib.pdftext import (
    FOOTNOTE_DROP,
    RE_KAP_MARK,
    RE_PARA_MARK,
    line_body_size,
    page_paragraphs,
    pdf_pages,
)
from ..lib.util import basefile_slug
from . import legacy_formats, lydelse
from .model import Block, Forarbete
from .structure import RE_TRAILING_PAREN, nest

# förarbeten cite across the whole spectrum, like court decisions
PARSE_TYPES = ALL_PARSE_TYPES

RE_HEADING_NUM = re.compile(r"^\d+(?:\.\d+)*$")       # "4" / "4.3.2" (own line)
RE_NUM_TITLE = re.compile(r"^(\d+(?:\.\d+)*)\s+\S")        # "15 Title" / "4.3 T"

# prop/skr front matter (the överlämnande on page 1): the handover sentence,
# the ort/datum line ("Stockholm den 20 maj 2021", occasionally Harpsund), and
# the ingress heading -- none of them bold, so the font-driven classifier reads
# them all as plain stycken (see tag_frontmatter)
RE_OVERLAMNAR = re.compile(r"^Regeringen (?:överlämnar|förelägger)\b")
RE_ORT_DATUM = re.compile(r"^\S+ den \d{1,2} \w+ \d{4}$")
RE_INNEHALL = re.compile(
    r"^(?:Propositionens|Skrivelsens) huvudsakliga innehåll$")


def mint_uri(typ, basefile):
    """https://lagen.nu/<type>/<basefile> -- the citation-target form (prop,
    sou, ds, dir, …), identical to what the FORARBETEN grammar mints."""
    return "https://lagen.nu/%s/%s" % (typ, basefile)


def classify(paras, page, body=0):
    """Paragraphs -> Blocks. Bold chapter/§ markers (recovered from font) become
    `kapitel`/`paragraf` blocks -- the structure that lets commentary be tied to
    a paragraf; other bold or numbered paragraphs are headings; the rest stycken.

    `body` is the document's body font size (see `line_body_size`); where the source
    carries font info it gates two misreads a bare "N Title" pattern invites:
    text clearly smaller than the body is a `fotnot` ("1 Senaste lydelse
    2008:1266." -- the lagtext provenance footnotes, previously read as level-1
    rubriks), and a numbered rubrik must be bold or larger than the body (a
    body-sized table row "22 år 25 000 …" is not a heading). Size-less paras
    (OCR/legacy) keep the permissive rules."""
    blocks = []
    i = 0
    while i < len(paras):
        p = paras[i]
        mk, mp, mt = (RE_KAP_MARK.match(p.text), RE_PARA_MARK.match(p.text),
                      RE_NUM_TITLE.match(p.text))
        heading_font = not p.size or not body or p.bold or p.size > body
        if body and p.size and p.size <= body - FOOTNOTE_DROP:
            blocks.append(Block("fotnot", p.text, page))
        elif mk and (p.lead_bold or not p.text[mk.end():].strip()):
            # bold marker leading text, or a bare centered "2 kap." (the
            # page-centered chapter anchor over a lydelse table is not bold)
            blocks.append(Block("kapitel", p.text, page, num=mk.group(1)))
        elif mp and (p.lead_bold or not p.text[mp.end():].strip()):
            blocks.append(Block("paragraf", p.text, page,
                                num=re.sub(r"\s+", "", mp.group(1))))
        elif mt and len(p.text) < 120 and heading_font:
            blocks.append(Block("rubrik", p.text, page,
                                mt.group(1).count(".") + 1))
        elif p.bold and len(p.text) < 120:
            blocks.append(Block("rubrik", p.text, page, 3))   # unnumbered subhead
        elif RE_HEADING_NUM.match(p.text):
            nxt = paras[i + 1].text if i + 1 < len(paras) else ""
            if (heading_font and nxt[:1].isupper()
                    and not RE_HEADING_NUM.match(nxt)):
                blocks.append(Block("rubrik", "%s %s" % (p.text, nxt), page,
                                    p.text.count(".") + 1))
                i += 2
                continue
        else:
            blocks.append(Block("stycke", p.text, page))
        i += 1
    return blocks


def parse_pdf(pdf_path, identifier, patch_key=None):
    """All body blocks of a förarbete PDF, page by page (page = pdf index).
    `patch_key=(source, basefile)` patches the pdftohtml XML before extraction.
    Each page is first split around its nuvarande/föreslagen lydelse tables
    (lydelse.split_page); the normal segments reflow and classify as before,
    a table segment becomes one `tabell` block whose rows pair the aligned
    cell paragraphs (row 0 the column header pair)."""
    # (pageno, [("paras", [Para], None) | ("tabell", header, rows)])
    pages = []
    for pageno, lines in pdf_pages(pdf_path, patch_key):
        segs = [("paras", page_paragraphs(seg[1], identifier, pageno), None)
                if seg[0] == "lines" else seg
                for seg in lydelse.split_page(lines)]
        pages.append((pageno, segs))
    body = line_body_size([p for _pg, segs in pages
                           for kind, data, _x in segs if kind == "paras"
                           for p in data])
    blocks = []
    for pageno, segs in pages:
        for kind, data, rows in segs:
            if kind == "paras":
                blocks += classify(data, pageno, body)
            else:
                header, cells = data, list(rows or [])
                if header is not None:      # the region's first chunk only
                    cells.insert(0, (header.runs[0].text, header.runs[1].text))
                blocks.append(Block("tabell", "", pageno, rows=cells,
                                    th=header is not None))
    return blocks


def _is_signer_name(text):
    """A signer line: 2-5 capitalized-ish words ("Stefan Löfven", "Gustaf von
    Essen"), optionally a trailing departement parenthetical ("Mikael Damberg
    (Justitiedepartementet)"). No digits, no sentence punctuation."""
    text = RE_TRAILING_PAREN.sub("", text)
    words = text.split()
    return (1 < len(words) <= 5 and len(text) < 60
            and text[:1].isupper() and not text.endswith(".")
            and all(w[:1].isalpha() for w in words)
            and not any(ch.isdigit() for ch in text))


def tag_frontmatter(blocks):
    """Retag the prop/skr front matter the classifier reads as plain stycken
    (nothing on the överlämnande page is bold): the "huvudsakliga innehåll"
    heading becomes a level-1 rubrik so the ingress nests into its own avsnitt,
    and the signer names after the ort/datum line become `signatur` blocks --
    the authors the sfs history-as-git export mines. Front matter ends at the
    first real rubrik ("1 Förslag till riksdagsbeslut"); signer tagging also
    requires the handover sentence, so bodies without the modern överlämnande
    (old riksdagen-format props) are left untouched."""
    end = next((i for i, b in enumerate(blocks) if b.kind == "rubrik"),
               len(blocks))
    front = blocks[:end]
    for b in front:
        if b.kind == "stycke" and RE_INNEHALL.match(b.text):
            b.kind, b.level = "rubrik", 1
    if any(b.kind == "stycke" and RE_OVERLAMNAR.match(b.text) for b in front):
        after_datum = False
        for b in front:
            if b.kind == "stycke" and RE_ORT_DATUM.match(b.text):
                after_datum = True
            elif after_datum and b.kind == "stycke" and _is_signer_name(b.text):
                b.kind = "signatur"
            else:
                after_datum = False
    return blocks


# html-body adapters by the record's `body_format` (stamped by the import verb,
# which probed the bytes -- parse never re-probes); each -> a Para stream
LEGACY_HTML_PARAS = {"text/tml": legacy_formats.riksdagen_html_paras,
                     "skanning2007": legacy_formats.riksdagen_mso_paras,
                     "trips": legacy_formats.trips_paras}


def _paged_body(pages):
    """A `(pageno, [Para])` stream -> Blocks, each page's paragraphs classified
    under its page number so `#sid{N}` anchors resolve. Shared by the ABBYY-XML
    and scanned-PDF (pdftotext) OCR routes; OCR noise rides along, but the
    citation scanner still lights up the references it can read."""
    pages = list(pages)
    body = line_body_size([p for _pageno, paras in pages for p in paras])
    return [b for pageno, paras in pages for b in classify(paras, pageno, body)]


def _legacy_pdf_body(pdf_path, identifier):
    """A frozen PDF body: the font-aware `pdf_pages` path for born-digital PDFs
    (regeringen-era, proptrips 2007+), falling back to a `pdftotext` OCR-text
    extraction for the scans (soukb, propkb's scan-only props) whose text layer
    `pdftohtml -xml` renders empty -- and sometimes errors on. Born-digital vs
    scan is decided by *result*, not by guessing the corpus: a born-digital PDF
    yields font blocks; a scan yields none there and its OCR text through the
    pdftotext fallback (page-anchored, so `#sid{N}` still resolves)."""
    try:
        blocks = parse_pdf(pdf_path, identifier)
    except subprocess.CalledProcessError:   # pdftohtml chokes on some KB scans
        blocks = []
    return blocks or _paged_body(legacy_formats.scanned_pdf_pages(pdf_path))


def _legacy_body(record):
    """The body of a frozen-import record (§7g), whose `legacy_files` reference
    the frozen bytes in place under LEGACY_ROOT (never copied).

    A re-OCR sidecar wins first: a modern-OCR'd PDF dropped at the record's
    `fa_ocr_pdf` path (a later `ocrmypdf` pass over the weaker embedded scan
    layers, run in prod) upgrades this document's parse without touching the
    import -- it is parsed instead of the legacy scan. Otherwise the first legacy
    PDF -> the shared PDF path (a pdf is listed only when the import's text-layer
    probe passed); an ABBYY `.xml` -> the page-anchored abbyy route; else an html
    body dispatched on the record's `body_format` -> paragraphs classified with no
    page (a page-less body carries `page=None`; `#sid{N}` anchors just don't
    apply). Else no body."""
    ocr = layout.fa_ocr_pdf(record["type"], record["basefile"])
    if ocr.exists():
        return _legacy_pdf_body(ocr, record["identifier"])
    files = [config.LEGACY_ROOT / f for f in record["legacy_files"]]
    pdfs = [f for f in files if f.suffix.lower() == ".pdf"]
    if pdfs:
        return _legacy_pdf_body(pdfs[0], record["identifier"])
    xmls = [f for f in files if f.suffix.lower() == ".xml"]
    if xmls:
        return _paged_body(legacy_formats.abbyy_pages(xmls[0]))
    htmls = [f for f in files if f.suffix.lower() == ".html"]
    if htmls:
        return classify(LEGACY_HTML_PARAS[record["body_format"]](
            htmls[0].read_text("utf-8")), None)
    return []


def rskr_body(html):
    """The API's own HTML rendering of a riksdagsskrivelse -> Blocks. The body
    is a handful of heading/paragraph elements in both feed generations (the
    modern Section1 layout and the plain pre-2000s one); everything after the
    ort/datum line ("Stockholm den 17 juni 2026") is a signer -- the talman,
    countersigned by a tjänsteman in the modern layout. Page-less by nature
    (no citation points into an rskr), so every block carries page=None."""
    soup = BeautifulSoup(html, "html.parser")
    texts = [re.sub(r"\s+", " ", el.get_text(" ", strip=True))
             for el in soup.find_all(["h1", "h2", "p"])]
    blocks = [Block("stycke", t) for t in texts if t]
    for i, b in enumerate(blocks):
        if RE_ORT_DATUM.match(b.text):
            for nxt in blocks[i + 1:]:
                nxt.kind = "signatur"
            break
    return blocks


def parse_record(record, root):
    """A downloaded record (the `<slug>.json`) -> a Forarbete. A live-harvest
    record uses the first PDF the downloader stored under `root/<type>/` (for
    rskr: the stored HTML body); a frozen
    import record (`legacy_files`) resolves its body under LEGACY_ROOT. A record
    with no body yields metadata only (still a real catalog document at its URI)."""
    typ, basefile = record["type"], record["basefile"]
    if "legacy_files" in record:
        body = _legacy_body(record)
    elif typ == "rskr":
        body = rskr_body(compress.read_text(
            Path(root) / typ / record["files"][0]))
    else:
        pdfs = [f for f in record.get("files", []) if f.lower().endswith(".pdf")]
        # the patch key carries the build-style basefile ("sou/2021-82" --
        # typ-qualified slug, what layout.relpath decomposes); the record's own
        # basefile ("2021:82") has no typ and is not filesystem-safe
        body = (parse_pdf(Path(root) / typ / pdfs[0], record["identifier"],
                          ("forarbete", "%s/%s" % (typ, basefile_slug(basefile))))
                if pdfs else [])
    if typ in ("prop", "skr"):
        body = tag_frontmatter(body)
    return Forarbete(type=typ, basefile=basefile,
                     identifier=record["identifier"], uri=mint_uri(typ, basefile),
                     title=record.get("title", ""), date=record.get("date"),
                     body=body)


@functools.cache
def _refparser():
    return LagrumParser(load_namedlaws(SFS_NAMEDLAWS), basefile="forarbete",
                        abbreviations=load_abbreviations(SFS_NAMEDLAWS),
                        parse_types=PARSE_TYPES)


def _scan(text, parser):
    """Citation-scan one text into an inline-run list."""
    return interleave(text, parser.parse_text(text, context={}))


def to_artifact(fa):
    """Project to JSON. Each block becomes an inline-run list (plain runs +
    {predicate,uri,text} link dicts), scanned with one parser threaded across the
    document so 'a. prop.'/'samma lag' state carries; the flat block run is then
    grouped into the nested `structure` tree by heading level (see structure.py).
    A `tabell` block projects to the shared table shape (`rad` children with
    `cells`, the same schema SFS uses -- catalog and render already speak it),
    row 0 flagged `th` (the nuvarande/föreslagen column header)."""
    parser = _refparser()
    parser.reset()                          # fresh per-document state
    blocks = []
    for b in fa.body:
        block = ({"type": b.kind, "text": _scan(b.text, parser)}
                 | ({"page": b.page} if b.page is not None else {})
                 | ({"level": b.level} if b.level else {})
                 | ({"num": b.num} if b.num else {}))
        if b.rows is not None:
            block["children"] = [
                {"type": "rad", "cells": [_scan(c, parser) for c in row]}
                | ({"th": True} if b.th and i == 0 else {})
                for i, row in enumerate(b.rows)]
        blocks.append(block)
    return {"uri": fa.uri, "type": fa.type, "identifier": fa.identifier,
            "basefile": fa.basefile, "title": fa.title, "date": fa.date,
            "structure": nest(blocks)}
