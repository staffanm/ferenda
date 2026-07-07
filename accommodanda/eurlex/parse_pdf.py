"""Parse an EU document from its PDF manifestation (the last-resort format) via
`pdftohtml -xml`, into the EurlexDoc model.

PDF carries no structure, so we extract positioned text with poppler's
pdftohtml (the same tool the forarbete parser uses), reflow it into paragraphs,
and infer document structure from the text with the localized vocabulary in
`lang` -- the same fallback the old-flavour HTML uses (Article/Artikel headings,
TITLE/AVDELNING, the enacting formula, visa/recital framing). Lossier than html
and far lossier than Formex, but citation-scannable and article-anchored.
"""

import re
import subprocess
from pathlib import Path

from ..lib.pdftext import dehyphenate, flat_lines
from ..lib.util import normalize_space
from . import lang as L
from .model import BASE, Block, EurlexDoc, doctype
from .parse_html import eu_date

PARA_GAP = 1.6       # a vertical gap > this * body-line-height starts a paragraph
RE_OJ = re.compile(r"\b([LC])\s*(\d+)\s*/\s*\d+")


def _ocr(path, lang):
    """OCR a scanned PDF (no recoverable text layer) into a cached hidden
    sidecar, returning its path. Cached so a re-parse is free. A missing
    ocrmypdf binary is a broken environment and propagates (rule:fail-fast); a
    per-document OCR failure (a corrupt scan, a missing language pack) raises
    CalledProcessError, caught at the build driver's per-document boundary and
    recorded there -- never swallowed into an empty artifact."""
    cached = path.with_name("." + path.stem + ".ocr.pdf")
    if cached.exists():
        return cached
    # --force-ocr: rasterize and OCR every page, replacing the unrecoverable
    # (Identity-H, no ToUnicode) text layer these scans carry -- --skip-text
    # would see that broken layer as "already text" and skip the page.
    subprocess.run(["ocrmypdf", "--quiet", "--force-ocr", "-l", lang,
                    str(path), str(cached)], check=True, capture_output=True)
    return cached


def pdf_lines(path, lang="eng"):
    """Visual lines from a PDF via the shared font-aware extractor, flattened
    across pages; if it has no recoverable text layer (a scanned document), OCR
    it first with ocrmypdf and extract from that. `hidden=True` throughout: the
    OCR layer ocrmypdf adds is rendered invisibly behind the page image."""
    path = Path(path)
    lines = flat_lines(path, hidden=True)
    if not lines:
        lines = flat_lines(_ocr(path, lang), hidden=True)
    return lines


def _paragraphs(lines):
    """Reflow [Line] into (text, bold) paragraphs: a bold line or a vertical gap
    larger than the body line-height starts a new one. (EU acts run continuously
    across pages, so this reflows the whole document rather than page by page and
    keeps every line, unlike the header/TOC/page-number stripping page_paragraphs
    the Swedish sources use.)"""
    gaps = sorted(b.top - a.top for a, b in zip(lines, lines[1:], strict=False)
                  if 0 < b.top - a.top < 200)
    body = gaps[len(gaps) // 2] if gaps else 12        # median line height
    paras, cur, prev = [], [], None
    for line in lines:
        if cur and (line.bold or prev is None or line.top - prev > body * PARA_GAP):
            paras.append(cur)
            cur = []
        cur.append((line.text, line.bold))
        prev = line.top
    if cur:
        paras.append(cur)
    out = []
    for para in paras:
        text = ""
        for line, _ in para:
            text = dehyphenate(text, line)
        text = normalize_space(text)
        if text:
            out.append((text, all(b for _, b in para)))
    return out


def parse_pdf(path, celex, lang):
    """A PDF manifestation -> EurlexDoc (best-effort structure from text)."""
    voc = L.vocab(lang)
    doc = EurlexDoc(celex=celex, uri=BASE % celex, doctype=doctype(celex), lang=lang)
    paras = _paragraphs(pdf_lines(path, lang))

    # metadata: the OJ header line(s) near the top
    head = " ".join(t for t, _ in paras[:6])
    doc.date = eu_date(head)
    oj = RE_OJ.search(head)
    if oj:
        doc.oj = "%s %s" % (oj.group(1), oj.group(2))

    in_body = False
    for text, _bold in paras:
        if voc.article.match(text) and len(text) <= 60:
            num = L.article_num(text)
            doc.body.append(Block("article", text, num=num, anchor=num))
            in_body = True
        elif voc.heading.match(text) and (text.isupper() or len(text) <= 40):
            doc.body.append(Block("heading", text, level=1))
        elif (m := L.RE_RECITAL.match(text.split(" ", 1)[0])):
            num = m.group(1)
            body = text.split(" ", 1)[1] if " " in text else ""
            doc.body.append(Block("recital" if not in_body else "point", body, num=num))
        elif in_body:
            doc.body.append(Block("paragraph", text))
        else:
            doc.body.append(Block(voc.preamble_kind(text), text))
            if voc.enacting.search(text):
                in_body = True
    return doc
