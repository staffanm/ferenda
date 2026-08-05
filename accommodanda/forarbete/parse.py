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

import re
import subprocess

from bs4 import BeautifulSoup

from ..lib import compress, layout

# font-aware extraction + paragraph reflow are shared across the PDF verticals
# (re-exported here so this module's existing import sites keep working)
from ..lib.errors import SkipDocument
from ..lib.lagrum import ALL_PARSE_TYPES, interleave, sfs_parser
from ..lib.pdftext import (
    FOOTNOTE_DROP,
    RE_KAP_MARK,
    RE_PARA_MARK,
    bilaga_labels,
    is_italic_subheading,
    line_body_size,
    page_number_candidates,
    page_paragraphs,
    pdf_figures,
    pdf_first_page_text,
    pdf_info,
    pdf_pages,
    points_from_pdftohtml,
    printed_pages,
)
from ..lib.util import basefile_slug
from . import legacy_formats, lydelse, tabell, volumes
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
            blocks.append(Block("fotnot", p.text, page, spans=p.spans, top=p.top))
        elif mk and (p.lead_bold or not p.text[mk.end():].strip()):
            # bold marker leading text, or a bare centered "2 kap." (the
            # page-centered chapter anchor over a lydelse table is not bold)
            blocks.append(Block("kapitel", p.text, page, num=mk.group(1),
                                spans=p.spans, top=p.top))
        elif mp and (p.lead_bold or not p.text[mp.end():].strip()):
            blocks.append(Block("paragraf", p.text, page,
                                num=re.sub(r"\s+", "", mp.group(1)),
                                spans=p.spans, top=p.top))
        elif mt and len(p.text) < 120 and heading_font:
            blocks.append(Block("rubrik", p.text, page,
                                mt.group(1).count(".") + 1, spans=p.spans, top=p.top))
        elif p.bold and len(p.text) < 120:
            blocks.append(Block("rubrik", p.text, page, 3,
                                spans=p.spans, top=p.top))   # unnumbered subhead
        elif is_italic_subheading(p.text, p.italic):
            # the italic, body-sized subheading a förarbete uses inside a
            # section ("Lagrådet", "Skälen för regeringens förslag"). The
            # italics are consumed as the heading signal, so they are not also
            # carried into the text -- the heading would render its whole self
            # emphasised, saying the same thing twice.
            blocks.append(Block("rubrik", p.text, page, 3, top=p.top))
        elif RE_HEADING_NUM.match(p.text):
            nxt = paras[i + 1].text if i + 1 < len(paras) else ""
            if (heading_font and nxt[:1].isupper()
                    and not RE_HEADING_NUM.match(nxt)):
                blocks.append(Block("rubrik", "%s %s" % (p.text, nxt), page,
                                    p.text.count(".") + 1, top=p.top))
                i += 2
                continue
        elif p.boxed:
            blocks.append(Block("ruta", p.text, page, spans=p.spans, top=p.top))
        else:
            blocks.append(Block("stycke", p.text, page, spans=p.spans, top=p.top))
        i += 1
    return blocks


def _pdf_probe(pdf_path):
    """`(pages, title, first page text)` for the volume rule -- the cheapest
    read that tells a rättelseblad from a betänkande. `pdfinfo` plus one page of
    `pdftotext` cost milliseconds; converting a 3000-page budget volume in full
    would not, and this runs for every file of every multi-PDF record."""
    info = pdf_info(pdf_path)
    return (int(info.get("Pages", 0)), info.get("Title", ""),
            pdf_first_page_text(pdf_path))


def parse_pdf(pdf_path, identifier, patch_key=None):
    """All body blocks of a förarbete PDF, page by page. The page a block
    carries is the *printed* page (the `#sid{N}` anchor citations resolve to):
    the marginal folio numbers are read off every page
    (`page_number_candidates`, resolved against the running numbering) and
    a *running* PDF-index ↔ printed-page offset is carried between detections
    (`printed_pages`) -- zero for modern regeringen.se PDFs numbered from the
    title page, negative where unnumbered cover matter precedes page 1
    (SOU 1989:67: printed 1 is PDF page 4), and shifting mid-document where
    the PDF omits blank printed leaves or binds in unnumbered dividers.
    `patch_key=(source, basefile)` patches the pdftohtml XML before extraction.
    Each page is first split around its nuvarande/föreslagen lydelse tables
    (lydelse.split_page); the normal segments reflow and classify as before,
    a table segment becomes one `tabell` block whose rows pair the aligned
    cell paragraphs (row 0 the column header pair)."""
    raw = list(pdf_pages(pdf_path, patch_key))
    figures = pdf_figures(pdf_path, patch_key)
    printed_map = printed_pages(
        {pageno: page_number_candidates(lines[:3] + lines[-3:], identifier)
         for pageno, lines in raw},
        [pageno for pageno, _lines in raw],
        bilaga_labels(raw, identifier))
    # (printed pageno, [("paras", [Para], None)
    #                   | ("tabell", header, rows)         (a lydelse table)
    #                   | ("gtabell", th, rows)])          (a generic table)
    pages = []
    for pageno, lines in raw:
        # unnumbered cover matter ahead of printed page 1 carries no anchor
        printed, bilaga = printed_map[pageno]
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
                             page_paragraphs(seg[1], identifier, printed,
                                             indent_breaks=True),
                             None))
                continue
            # generic tables (budget tables, bilaga listings) within the
            # non-lydelse lines; the rest reflows as prose
            for gkind, gdata, grows in tabell.split_generic(seg[1]):
                if gkind == "lines":
                    segs.append(("paras",
                                 page_paragraphs(gdata, identifier, printed,
                                                 indent_breaks=True),
                                 None))
                else:
                    segs.append(("gtabell", gdata, grows))
        pages.append(((printed, bilaga), segs))
    body = line_body_size([p for _pg, segs in pages
                           for kind, data, _x in segs if kind == "paras"
                           for p in data])
    # printed page -> the PDF page it came from, so a figure found by PDF page
    # lands on the block stream keyed by printed page
    pageno_of = {printed_map[pageno][0]: pageno for pageno, _ in raw
                 if printed_map[pageno][0] is not None}
    blocks = []
    for (printed, bilaga), segs in pages:
        on_page = []
        for kind, data, rows in segs:
            if kind == "paras":
                on_page += classify(data, printed, body)
            elif kind == "gtabell":
                on_page.append(Block("tabell", "", printed, rows=list(rows or []),
                                     th=bool(data)))
            else:
                header, cells = data, list(rows or [])
                if header is not None:      # the region's first chunk only
                    cells.insert(0, (header.runs[0].text, header.runs[1].text))
                on_page.append(Block("tabell", "", printed, rows=cells,
                                     th=header is not None))
        # the page's figures, placed where they were printed: an image carries
        # its own y, and so does every paragraph, so a figure goes after the
        # last block that begins above it. Appending them to the page instead
        # put the pyramid at the foot of prop. 2017/18:89 p. 40 rather than
        # under the sentence that introduces it.
        figs, pt_width, px_width = figures.get(pageno_of[printed], ((), 0, 0)) \
            if printed in pageno_of else ((), 0, 0)
        for fig in sorted(figs, key=lambda f: f.top, reverse=True):
            block = Block("bild", "", printed, top=fig.top,
                          bbox=points_from_pdftohtml(
                              px_width, pt_width,
                              (fig.left, fig.top, fig.width, fig.height)))
            on_page.insert(figure_index(on_page, fig.top), block)
        for block in on_page:               # a bilaga numbering its own pages
            block.bilaga = bilaga
        blocks += on_page
    return tabell.merge_continued(blocks)


def figure_index(on_page, fig_top):
    """Where in a page's block stream a figure printed at `fig_top` belongs:
    after the last block that begins above it.

    A block with no geometry of its own (a tabell, rebuilt from its cells rather
    than from lines) sits where the last block that had one did -- the page is
    already in reading order, so carrying that y forward keeps a figure printed
    below a table below it. Reading a missing top as 0 put it above instead, and
    the same sentinel put prop. 2017/18:89's pyramid at the foot of its page."""
    after, last = 0, None
    for i, block in enumerate(on_page):
        last = block.top if block.top is not None else last
        if last is not None and last < fig_top:
            after = i + 1
    return after


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
                     "bet-html": legacy_formats.riksdagen_bet_paras,
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


def _harvested_body(record, root):
    """The body of a harvested-form record whose body file(s) live in the raw
    `downloaded/<type>/` tree (`files`), read through `compress`.

    Every §7g frozen corpus is re-housed into this tree, so this is the one
    body route: a re-OCR sidecar
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
        # a multi-volume document (prop 2015/16:195 "del 1 av 4" ...) publishes
        # its body as several PDFs -- but `files` is every PDF the landing page
        # linked, errata and English summaries included, so `volumes.body_pdfs`
        # decides which are body and in what order. Patches are authored
        # against the first volume's XML (the only volume parsed before
        # multi-volume support), so only that volume takes the patch hook.
        docdir = layout.fa_dir(root, typ, basefile)
        # the landing page is only consulted for a record that actually has
        # several PDFs to choose between -- 485 of 97k, so reading and
        # decompressing it for every document would be pure waste
        labels = volumes.link_texts(docdir, record) if len(pdfs) > 1 else None
        body, _dropped = volumes.body_pdfs(record | {"_labels": labels},
                                           lambda name: _pdf_probe(docdir / name))
        if not body:
            # nothing in `files` is this document's text (a budget proposition,
            # or a record holding only errata and translations) -- an empty
            # artifact would look like a parsed document with no body
            raise SkipDocument("%s/%s: no body PDF among %d file(s)"
                               % (typ, basefile, len(pdfs)))
        blocks, ocr = _legacy_pdf_body(docdir / body[0], record["identifier"],
                                       patch_key)
        for extra in body[1:]:
            more, more_ocr = _legacy_pdf_body(docdir / extra,
                                              record["identifier"])
            blocks += more
            ocr = ocr or more_ocr
        return blocks, ocr
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
    """A downloaded record (the `<slug>.json`) -> a Forarbete. The body file(s)
    live beside the record in `root/<type>/` -- the downloader's, or re-housed
    frozen-corpus bytes (a `source`-carrying record; §7g) read identically. For
    rskr the stored HTML is the body. A record with no body yields metadata
    only (still a real catalog document at its URI)."""
    typ, basefile = record["type"], record["basefile"]
    ocr = False
    if typ == "rskr":
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


def _scan(text, parser, spans=()):
    """Citation-scan one text into an inline-run list, keeping the emphasis the
    document set (`spans`, from the PDF's font runs)."""
    return interleave(text, parser.parse_text(text, context={}), spans)


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
            # not every dict run is a link: `lagrum.interleave` also emits
            # {"text", "style"} runs for the emphasis the document set, and those
            # carry no uri. Reading it directly crashed the whole document
            # (sou/2002-99, KeyError: 'uri') as soon as one of them preceded a
            # citation in a scan old enough to reach this check. The default is
            # for the *absent* key only -- a link run always carries a uri
            # (`Ref.uri` is not optional), so a present-but-empty one would be a
            # bug upstream, and `or ""` would hide it.
            if isinstance(run, dict) and (m := RE_TARGET_YEAR.match(
                    run.get("uri", ""))):
                if (int(m.group(1)) > doc_year + 1
                        and m.group(1) in run["text"]):
                    suspects.append({"text": run["text"], "uri": run["uri"],
                                     "page": page})
                    runs[i] = run["text"]

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
    parser = sfs_parser("forarbete", PARSE_TYPES)   # fresh per-document state
    blocks = []
    for b in fa.body:
        block = ({"type": b.kind, "text": _scan(b.text, parser, b.spans)}
                 | ({"page": b.page} if b.page is not None else {})
                 | ({"bilaga": b.bilaga} if b.bilaga else {})
                 | ({"level": b.level} if b.level else {})
                 | ({"num": b.num} if b.num else {})
                 | ({"bbox": b.bbox} if b.bbox else {}))
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
