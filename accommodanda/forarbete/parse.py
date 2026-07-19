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
    page_offset,
    page_paragraphs,
    pdf_pages,
    printed_pageno,
)
from ..lib.util import basefile_slug
from . import legacy_formats, lydelse, tabell
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
    """All body blocks of a förarbete PDF, page by page. The page a block
    carries is the *printed* page (the `#sid{N}` anchor citations resolve to):
    the marginal folio numbers are read off every page (`printed_pageno`) and
    a constant PDF-index ↔ printed-page offset is derived (`page_offset`) --
    zero for modern regeringen.se PDFs numbered from the title page, negative
    where unnumbered cover matter precedes page 1 (SOU 1989:67: printed 1 is
    PDF page 4), and failing visibly when the evidence is ambiguous.
    `patch_key=(source, basefile)` patches the pdftohtml XML before extraction.
    Each page is first split around its nuvarande/föreslagen lydelse tables
    (lydelse.split_page); the normal segments reflow and classify as before,
    a table segment becomes one `tabell` block whose rows pair the aligned
    cell paragraphs (row 0 the column header pair)."""
    raw = list(pdf_pages(pdf_path, patch_key))
    offset = page_offset({pageno: n for pageno, lines in raw
                          if (n := printed_pageno(lines, identifier)) is not None})
    # (printed pageno, [("paras", [Para], None)
    #                   | ("tabell", header, rows)         (a lydelse table)
    #                   | ("gtabell", th, rows)])          (a generic table)
    pages = []
    for pageno, lines in raw:
        # unnumbered cover matter ahead of printed page 1 carries no anchor
        printed = pageno + offset if pageno + offset >= 1 else None
        lydelse_segs = lydelse.split_page(lines)
        # a page holding a lydelse table is a two-column statute page: its
        # leftover lines are statute text in columns, never a generic data
        # table -- the generic detector runs only on lydelse-free pages
        page_has_lydelse = any(s[0] == "tabell" for s in lydelse_segs)
        segs = []
        for seg in lydelse_segs:
            if seg[0] != "lines":
                segs.append(seg)
                continue
            if page_has_lydelse:
                segs.append(("paras",
                             page_paragraphs(seg[1], identifier, printed),
                             None))
                continue
            # generic tables (budget tables, bilaga listings) within the
            # non-lydelse lines; the rest reflows as prose
            for gkind, gdata, grows in tabell.split_generic(seg[1]):
                if gkind == "lines":
                    segs.append(("paras",
                                 page_paragraphs(gdata, identifier, printed),
                                 None))
                else:
                    segs.append(("gtabell", gdata, grows))
        pages.append((printed, segs))
    body = line_body_size([p for _pg, segs in pages
                           for kind, data, _x in segs if kind == "paras"
                           for p in data])
    blocks = []
    for pageno, segs in pages:
        for kind, data, rows in segs:
            if kind == "paras":
                blocks += classify(data, pageno, body)
            elif kind == "gtabell":
                blocks.append(Block("tabell", "", pageno, rows=list(rows or []),
                                    th=bool(data)))
            else:
                header, cells = data, list(rows or [])
                if header is not None:      # the region's first chunk only
                    cells.insert(0, (header.runs[0].text, header.runs[1].text))
                blocks.append(Block("tabell", "", pageno, rows=cells,
                                    th=header is not None))
    return tabell.merge_continued(blocks)


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
# the html bodies whose text came off print (riksdagen's 2007 OCR Word export,
# the keyed-in TRIPS databases) -- the chronology check applies to them like
# to the ABBYY/pdftotext routes; text/tml is the born-digital feed window
OCR_HTML_FORMATS = frozenset({"skanning2007", "trips"})


def _paged_body(pages):
    """A `(pageno, [Para])` stream -> Blocks, each page's paragraphs classified
    under its page number so `#sid{N}` anchors resolve. Shared by the ABBYY-XML
    and scanned-PDF (pdftotext) OCR routes; OCR noise rides along, but the
    citation scanner still lights up the references it can read."""
    pages = list(pages)
    body = line_body_size([p for _pageno, paras in pages for p in paras])
    return [b for pageno, paras in pages for b in classify(paras, pageno, body)]


def _legacy_pdf_body(pdf_path, identifier, patch_key=None):
    """A PDF body from a scanned-or-born-digital corpus: the font-aware
    `pdf_pages` path for born-digital PDFs (regeringen-era, proptrips 2007+),
    falling back to a `pdftotext` OCR-text extraction for the scans (soukb,
    propkb's scan-only props) whose text layer `pdftohtml -xml` renders empty --
    and sometimes errors on. Born-digital vs scan is decided by *result*, not by
    guessing the corpus: a born-digital PDF yields font blocks; a scan yields
    none there and its OCR text through the pdftotext fallback (page-anchored, so
    `#sid{N}` still resolves). `patch_key` threads the record's patch identity to
    `parse_pdf` (a re-housed prop is a normal harvested doc, patchable like any
    other). Returns (blocks, ocr) -- the route taken is the one fact that says
    whether the text is OCR output (the chronology check keys on it)."""
    try:
        blocks = parse_pdf(pdf_path, identifier, patch_key)
    except subprocess.CalledProcessError:   # pdftohtml chokes on some KB scans
        blocks = []
    if blocks:
        return blocks, False
    return _paged_body(legacy_formats.scanned_pdf_pages(pdf_path)), True


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
        return _paged_body(legacy_formats.abbyy_pages(xmls[0])), True
    htmls = [f for f in files if f.suffix.lower() == ".html"]
    if htmls:
        return (classify(LEGACY_HTML_PARAS[record["body_format"]](
            htmls[0].read_text("utf-8")), None),
            record["body_format"] in OCR_HTML_FORMATS)
    words = [f for f in files if f.suffix.lower() in (".doc", ".docx")]
    if words:
        return classify(legacy_formats.word_paras(words[0]), None), False
    return [], False


def _harvested_body(record, root):
    """The body of a harvested-form record whose body file(s) live in the raw
    `downloaded/<type>/` tree (`files`), read through `compress`.

    The live-harvest twin of `_legacy_body` (§7g re-housed): a re-OCR sidecar
    (`fa_ocr_pdf`) wins first, else the first PDF -> the shared PDF path (the
    born-digital-or-scan `_legacy_pdf_body`, so a re-housed propkb/soukb scan
    still reaches the pdftotext OCR fallback), an ABBYY `.xml` -> the page-
    anchored abbyy route (its bytes decompressed for the streaming parser, since
    the download tree brotli's the xml), else an html body dispatched on the
    record's `body_format`. Else no body. A plain regeringen/riksdagen PDF record
    (no `body_format`, one PDF) flows through the PDF branch unchanged."""
    typ, basefile = record["type"], record["basefile"]
    # the document's patch identity, shared by both PDF routes: a re-OCR sidecar
    # is still *this* document, so its parse must honour this document's patches
    # -- keying only the `files` branch would silently unpatch every document
    # someone re-OCRs, with the patch still on disk and the build still green
    patch_key = ("forarbete", "%s/%s" % (typ, basefile_slug(basefile)))
    ocr = layout.fa_ocr_pdf(typ, basefile)
    if ocr.exists():
        return _legacy_pdf_body(ocr, record["identifier"], patch_key)
    files = record.get("files", [])
    pdfs = [f for f in files if f.lower().endswith(".pdf")]
    if pdfs:
        return _legacy_pdf_body(layout.fa_dir(root, typ, basefile) / pdfs[0],
                                record["identifier"], patch_key)
    xmls = [f for f in files if f.lower().endswith(".xml")]
    if xmls:
        return _paged_body(legacy_formats.abbyy_pages(
            compress.read_bytes(layout.fa_dir(root, typ, basefile)
                                / xmls[0]))), True
    htmls = [f for f in files if f.lower().endswith(".html")]
    if htmls:
        return (classify(LEGACY_HTML_PARAS[record["body_format"]](
            compress.read_text(layout.fa_dir(root, typ, basefile)
                               / htmls[0])), None),
            record["body_format"] in OCR_HTML_FORMATS)
    words = [f for f in files if f.lower().endswith((".doc", ".docx"))]
    if words:
        # .doc/.docx are incompressible -> stored plain, so antiword/POI read the
        # path directly (unlike the brotli'd xml/html above)
        return classify(legacy_formats.word_paras(
            layout.fa_dir(root, typ, basefile) / words[0]), None), False
    return [], False


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


# a rubrik the flattened PDF cut mid-phrase -- "4.1 Förslag till lag om
# ändring i" with the statute name dropped to the next line (rewrite-parity
# finding 04: the truncated "lag om ändring i" rubriks)
RE_DANGLING_RUBRIK = re.compile(r"\bändring(?:ar)?\s+i\s*$", re.IGNORECASE)
# the orphaned continuation: a short lowercase-led line naming the statute
# ("sekretesslagen", "föreningsbankslagen (1987:620)"), possibly with a TOC
# dotted leader + page number after it. A real body stycke opens uppercase,
# so lowercase-led + short is the continuation signature. The line sometimes
# survives classification as a (fake) rubrik of its own, or -- in the all-caps
# heading style of older props -- as "UTSÖKNINGSLAGEN".
RE_TOC_LEADER = re.compile(r"[\s.]*\.{3,}[\s.\d]*$")
RE_CONTINUATION = re.compile(r"[a-zåäö][^.!?]{2,90}")
RE_UPPER_CONTINUATION = re.compile(r"[A-ZÅÄÖ][A-ZÅÄÖ\s\d:()-]{2,60}")
# reflow can glue the statute name straight onto the next paragraph's opening
# ("trafikskadelagen (1975:1410)14 § Från ett fordons..."): the name runs up
# to its SFS parenthesis, the glued remainder opens with a digit/uppercase
RE_GLUED_CONTINUATION = re.compile(
    r"([a-zåäö][a-zåäö\s-]{2,60}?\(\d{4}:\s?\d+\))\s*(?=[A-ZÅÄÖ0-9])")
RE_BILAGA_MARGIN = re.compile(r"^Bilaga \d+$")


def join_dangling_rubriks(body):
    """Re-attach the statute name a flattened PDF dropped off a "Förslag till
    lag om ändring i"-style rubrik: the following short statute-name line
    (a stycke or a mis-classified rubrik of its own; an interposed "Bilaga N"
    margin marker is skipped and stays in place) is folded into the rubrik
    text, with any TOC dotted leader stripped. A name glued onto the next
    paragraph's opening is split off it. The rubrik then resolves to its SFS
    number again (kommentar/genomförande key on the proposed-law name)."""
    drop = set()
    for i, b in enumerate(body):
        if b.kind != "rubrik" or not RE_DANGLING_RUBRIK.search(b.text):
            continue
        j = i + 1
        if (j < len(body) and body[j].kind == "stycke"
                and RE_BILAGA_MARGIN.match(body[j].text)):
            j += 1
        if j >= len(body) or body[j].kind not in ("stycke", "rubrik") \
                or j in drop:
            continue
        core = RE_TOC_LEADER.sub("", body[j].text).strip()
        if RE_CONTINUATION.fullmatch(core) or (
                b.text == b.text.upper()
                and RE_UPPER_CONTINUATION.fullmatch(core)):
            b.text = "%s %s" % (b.text, core)
            drop.add(j)
        elif body[j].kind == "stycke" and (m := RE_GLUED_CONTINUATION.match(core)):
            b.text = "%s %s" % (b.text, m.group(1))
            body[j].text = core[m.end():].lstrip()
    return [b for i, b in enumerate(body) if i not in drop]


def parse_record(record, root):
    """A downloaded record (the `<slug>.json`) -> a Forarbete. A live-harvest
    record uses the first PDF the downloader stored under `root/<type>/` (for
    rskr: the stored HTML body); a frozen
    import record (`legacy_files`) resolves its body under LEGACY_ROOT. A record
    with no body yields metadata only (still a real catalog document at its URI)."""
    typ, basefile = record["type"], record["basefile"]
    ocr = False
    if "legacy_files" in record:            # unmigrated frozen import (sou/dir/ds)
        body, ocr = _legacy_body(record)
    elif typ == "rskr":
        body = rskr_body(compress.read_text(
            layout.fa_dir(root, typ, basefile) / record["files"][0]))
    else:
        # live harvest + re-housed prop: body file(s) in downloaded/<type>/.
        # The patch key inside `_harvested_body` carries the build-style basefile
        # ("sou/2021-82" -- typ-qualified slug, what layout.relpath decomposes);
        # the record's own basefile ("2021:82") has no typ and is not filesystem-safe
        body, ocr = _harvested_body(record, root)
    body = join_dangling_rubriks(body)
    if typ in ("prop", "skr"):
        body = tag_frontmatter(body)
    return Forarbete(type=typ, basefile=basefile,
                     identifier=record["identifier"], uri=mint_uri(typ, basefile),
                     title=record.get("title", ""), date=record.get("date"),
                     ocr=ocr, body=body)


@functools.cache
def _refparser():
    return LagrumParser(load_namedlaws(SFS_NAMEDLAWS), basefile="forarbete",
                        abbreviations=load_abbreviations(SFS_NAMEDLAWS),
                        parse_types=PARSE_TYPES)


def _scan(text, parser):
    """Citation-scan one text into an inline-run list."""
    return interleave(text, parser.parse_text(text, context={}))


# the year a lagen.nu citation target carries in its uri: an SFS number
# (https://lagen.nu/1984:437#P3) or a förarbete id (…/prop/1992/93:100); other
# namespaces (dom/, avg/, ext/…) carry no comparable year and are never checked
RE_TARGET_YEAR = re.compile(
    r"^https://lagen\.nu/(?:(?:prop|sou|ds|dir|skr|bet|so|fm|pm|lr)/)?(\d{4})[:/]")


def censor_future_citations(blocks, doc_year):
    """The OCR chronology sanity check (rewrite-parity finding 05): a garbled
    citation must not point to legislation *newer* than the citing document
    (a 1971 prop whose OCR read '1934:437' as '1984:437'). Every link run
    whose target year exceeds ``doc_year + 1`` (the riksmöte spills into the
    next calendar year, so +1 is never suspect) *and* whose own text carries
    that year is demoted to its plain text -- the text is preserved verbatim,
    never rewritten; the link just is not minted -- and reported in the
    returned suspect list [{text, uri, page}]. The year-in-text condition is
    what scopes this to OCR digit garbling: a named-law reference
    ("kommunallagen" in a 1971 prop resolving to today's namesake) is a
    name-resolution question, not a scan error, and is left alone here.
    Mutates ``blocks`` in place (the flat pre-nest run lists)."""
    suspects = []

    def sweep(runs, page):
        for i, run in enumerate(runs):
            if isinstance(run, dict) and (m := RE_TARGET_YEAR.match(run["uri"])):
                if (int(m.group(1)) > doc_year + 1
                        and m.group(1) in (run.get("text") or "")):
                    suspects.append({"text": run.get("text"), "uri": run["uri"],
                                     "page": page})
                    runs[i] = run.get("text") or ""

    for b in blocks:
        sweep(b.get("text") or [], b.get("page"))
        for rad in b.get("children") or []:
            for cell in rad.get("cells") or []:
                sweep(cell, b.get("page"))
    return suspects


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
    art = {"uri": fa.uri, "type": fa.type, "identifier": fa.identifier,
           "basefile": fa.basefile, "title": fa.title, "date": fa.date}
    # OCR bodies get the chronology sanity check before the tree is built:
    # the basefile always leads with the riksmöte/calendar year, even when
    # `date` is missing (metadata-only era records)
    if fa.ocr and (m := re.match(r"\d{4}", fa.basefile)):
        if suspects := censor_future_citations(blocks, int(m.group(0))):
            art["suspect_citations"] = suspects
    art["structure"] = nest(blocks)
    return art
