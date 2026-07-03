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

from .. import config
from ..lib import layout
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
    interleave,
    load_abbreviations,
    load_namedlaws,
)

# font-aware extraction + paragraph reflow are shared across the PDF verticals
# (re-exported here so this module's existing import sites keep working)
from ..lib.pdftext import (
    RE_KAP_MARK,
    RE_PARA_MARK,
    page_paragraphs,
    pdf_pages,
)
from . import legacy_formats
from .model import Block, Forarbete
from .structure import nest

# förarbeten cite across the whole spectrum, like court decisions
PARSE_TYPES = [LAGRUM, KORTLAGRUM, EULAGSTIFTNING, RATTSFALL, FORARBETEN,
               EURATTSFALL, MYNDIGHETSBESLUT]

RE_HEADING_NUM = re.compile(r"^\d+(?:\.\d+)*$")       # "4" / "4.3.2" (own line)
RE_HEADING_INLINE = re.compile(r"^(\d+(?:\.\d+)+)\s+\S")   # "4.3.2 Title"
RE_NUM_TITLE = re.compile(r"^(\d+(?:\.\d+)*)\s+\S")        # "15 Title" / "4.3 T"


def mint_uri(typ, basefile):
    """https://lagen.nu/<type>/<basefile> -- the citation-target form (prop,
    sou, ds, dir, …), identical to what the FORARBETEN grammar mints."""
    return "https://lagen.nu/%s/%s" % (typ, basefile)


def classify(paras, page):
    """Paragraphs -> Blocks. Bold chapter/§ markers (recovered from font) become
    `kapitel`/`paragraf` blocks -- the structure that lets commentary be tied to
    a paragraf; other bold or numbered paragraphs are headings; the rest stycken."""
    blocks = []
    i = 0
    while i < len(paras):
        p = paras[i]
        mk, mp, mt = (RE_KAP_MARK.match(p.text), RE_PARA_MARK.match(p.text),
                      RE_NUM_TITLE.match(p.text))
        if p.lead_bold and mk:
            blocks.append(Block("kapitel", p.text, page, num=mk.group(1)))
        elif p.lead_bold and mp:
            blocks.append(Block("paragraf", p.text, page,
                                num=re.sub(r"\s+", "", mp.group(1))))
        elif (p.bold or mt) and mt and len(p.text) < 120:
            blocks.append(Block("rubrik", p.text, page,
                                mt.group(1).count(".") + 1))
        elif p.bold and len(p.text) < 120:
            blocks.append(Block("rubrik", p.text, page, 3))   # unnumbered subhead
        elif RE_HEADING_NUM.match(p.text):
            nxt = paras[i + 1].text if i + 1 < len(paras) else ""
            if nxt[:1].isupper() and not RE_HEADING_NUM.match(nxt):
                blocks.append(Block("rubrik", "%s %s" % (p.text, nxt), page,
                                    p.text.count(".") + 1))
                i += 2
                continue
        else:
            blocks.append(Block("stycke", p.text, page))
        i += 1
    return blocks


def parse_pdf(pdf_path, identifier):
    """All body blocks of a förarbete PDF, page by page (page = pdf index)."""
    blocks = []
    for pageno, lines in pdf_pages(pdf_path):
        blocks += classify(page_paragraphs(lines, identifier, pageno), pageno)
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
    blocks = []
    for pageno, paras in pages:
        blocks += classify(paras, pageno)
    return blocks


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


def parse_record(record, root):
    """A downloaded record (the `<slug>.json`) -> a Forarbete. A live-harvest
    record uses the first PDF the downloader stored under `root/<type>/`; a frozen
    import record (`legacy_files`) resolves its body under LEGACY_ROOT. A record
    with no body yields metadata only (still a real catalog document at its URI)."""
    typ, basefile = record["type"], record["basefile"]
    if "legacy_files" in record:
        body = _legacy_body(record)
    else:
        pdfs = [f for f in record.get("files", []) if f.lower().endswith(".pdf")]
        body = (parse_pdf(Path(root) / typ / pdfs[0], record["identifier"])
                if pdfs else [])
    return Forarbete(type=typ, basefile=basefile,
                     identifier=record["identifier"], uri=mint_uri(typ, basefile),
                     title=record.get("title", ""), date=record.get("date"),
                     body=body)


@functools.cache
def _refparser():
    return LagrumParser(load_namedlaws(SFS_NAMEDLAWS), basefile="forarbete",
                        abbreviations=load_abbreviations(SFS_NAMEDLAWS),
                        parse_types=PARSE_TYPES)


def to_artifact(fa):
    """Project to JSON. Each block becomes an inline-run list (plain runs +
    {predicate,uri,text} link dicts), scanned with one parser threaded across the
    document so 'a. prop.'/'samma lag' state carries; the flat block run is then
    grouped into the nested `structure` tree by heading level (see structure.py)."""
    parser = _refparser()
    parser.state = type(parser.state)()      # fresh per-document state
    blocks = [{"type": b.kind,
               "text": interleave(b.text, parser.parse_text(b.text, context={}))}
              | ({"page": b.page} if b.page is not None else {})
              | ({"level": b.level} if b.level else {})
              | ({"num": b.num} if b.num else {})
              for b in fa.body]
    return {"uri": fa.uri, "type": fa.type, "identifier": fa.identifier,
            "basefile": fa.basefile, "title": fa.title, "date": fa.date,
            "structure": nest(blocks)}
