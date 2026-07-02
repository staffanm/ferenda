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
import sys
from pathlib import Path

from lxml import etree  # ty: ignore[unresolved-import]  # lxml ships no stubs

from ..lib.util import normalize_space
from . import lang as L
from .model import BASE, Block, EurlexDoc, doctype

LINE_TOL = 3         # px: spans within this vertical distance are one visual line
PARA_GAP = 1.6       # a vertical gap > this * body-line-height starts a paragraph
RE_OJ = re.compile(r"\b([LC])\s*(\d+)\s*/\s*\d+")
RE_DATE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")


def _dehyphenate(acc, line):
    if acc.endswith("-") and line[:1].islower():
        return acc[:-1] + line
    return (acc + " " + line) if acc else line


def _ocr(path, lang):
    """OCR a scanned PDF (no recoverable text layer) into a cached hidden
    sidecar, returning its path -- or None if OCR is unavailable or fails (a
    missing tesseract language pack, say). Cached so a re-parse is free."""
    cached = path.with_name("." + path.stem + ".ocr.pdf")
    if cached.exists():
        return cached
    try:
        # --force-ocr: rasterize and OCR every page, replacing the unrecoverable
        # (Identity-H, no ToUnicode) text layer these scans carry -- --skip-text
        # would see that broken layer as "already text" and skip the page.
        subprocess.run(["ocrmypdf", "--quiet", "--force-ocr", "-l", lang,
                        str(path), str(cached)], check=True, capture_output=True)
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        print("  OCR unavailable for %s: %s" % (path.name, exc),
              file=sys.stderr, flush=True)
        return None
    return cached


def _extract_lines(path):
    """Ordered (top, text, bold) visual lines across all pages (page breaks are
    just larger vertical gaps via a per-page offset)."""
    # -hidden: include invisible text -- the OCR layer ocrmypdf adds is rendered
    # invisibly behind the page image, and pdftohtml drops it without this.
    xml = subprocess.run(
        ["pdftohtml", "-xml", "-i", "-hidden", "-nodrm", "-stdout", str(path)],
        capture_output=True, check=True).stdout
    # pdftohtml emits occasionally malformed XML (overlapping <b>/<i>, stray &)
    root = etree.fromstring(xml, etree.XMLParser(recover=True, load_dtd=False,
                                                 no_network=True))
    out, offset = [], 0
    for page in root.findall("page"):
        spans = []
        for t in page.findall("text"):
            text = normalize_space("".join(t.itertext()))
            if text:
                spans.append((int(t.get("top")), int(t.get("left")), text,
                              t.find(".//b") is not None))
        lines = []
        for top, left, text, bold in sorted(spans):
            if lines and abs(top - lines[-1][0]) <= LINE_TOL:
                lines[-1][1].append((left, text, bold))
            else:
                lines.append((top, [(left, text, bold)]))
        for top, runs in lines:
            runs.sort()
            out.append((offset + top,
                        normalize_space(" ".join(r[1] for r in runs)),
                        all(r[2] for r in runs)))
        offset += int(page.get("height", "1200")) + 100
    return out


def pdf_lines(path, lang="eng"):
    """Visual lines from a PDF; if it has no recoverable text layer (a scanned
    document), OCR it first with ocrmypdf and extract from that."""
    path = Path(path)
    lines = _extract_lines(path)
    if not lines:
        ocr = _ocr(path, lang)
        if ocr is not None:
            lines = _extract_lines(ocr)
    return lines


def _paragraphs(lines):
    """Reflow lines into (text, bold) paragraphs: a bold line or a vertical gap
    larger than the body line-height starts a new one."""
    gaps = sorted(b[0] - a[0] for a, b in zip(lines, lines[1:], strict=False)
                  if 0 < b[0] - a[0] < 200)
    body = gaps[len(gaps) // 2] if gaps else 12        # median line height
    paras, cur, prev = [], [], None
    for top, text, bold in lines:
        if cur and (bold or prev is None or top - prev > body * PARA_GAP):
            paras.append(cur)
            cur = []
        cur.append((text, bold))
        prev = top
    if cur:
        paras.append(cur)
    out = []
    for para in paras:
        text = ""
        for line, _ in para:
            text = _dehyphenate(text, line)
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
    date = RE_DATE.search(head)
    if date:
        doc.date = "%s-%02d-%02d" % (date.group(3), int(date.group(2)),
                                     int(date.group(1)))
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
