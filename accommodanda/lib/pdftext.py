"""Shared font-aware PDF text extraction for the PDF-bodied verticals
(förarbeten, myndighetsföreskrifter, …).

The pipeline is three steps, each a pure function over the previous so a vertical
can stop at whichever layer it needs:

  1. :func:`pdf_pages` -- poppler's ``pdftohtml -xml`` to ``(pageno, [Line])``.
     Each ``<text>`` fragment is one font run carrying ``<b>``/``<i>``; runs on a
     shared baseline are one visual :class:`Line`, bold/italic when all their runs
     are. Font is what survives a layout that text-order extraction mangles, and
     it is the only reliable signal for an *unnumbered* heading or a bold §-marker.
  2. :func:`page_paragraphs` -- reflow a page's lines into :class:`Para`s,
     dropping the running header (the document identifier), the page-number line
     and table-of-contents dotted-leader lines.
  3. the vertical's own ``classify`` -- :class:`Para`s to typed blocks. This part
     is *not* shared: a förarbete's outline (numbered 14 -> 14.3) and a
     föreskrift's body (``N kap.`` / ``N §``) read different signals, so each
     vertical keeps its own classifier over the same :class:`Para` stream.

The Swedish-legal markers a chapter/§ begins with (``RE_KAP_MARK`` /
``RE_PARA_MARK``) live here because step 2 needs them (a bold marker always opens
its own paragraph) and the classifiers reuse them.
"""

import re
import subprocess
from dataclasses import dataclass

from lxml import etree  # ty: ignore[unresolved-import]  # lxml ships no stubs

from .util import normalize_space

RE_DOTS = re.compile(r"\.{4,}")                       # TOC dotted leaders
RE_KAP_MARK = re.compile(r"^(\d+)\s*kap\.\s")             # "2 kap. ..."
RE_PARA_MARK = re.compile(r"^(\d+\s*[a-z]?)\s*§(?:\s|$)")  # "3 §" / "3 a §"

LINE_TOL = 4          # spans within this many y-units are the same visual line
PARA_GAP = 1.5        # a vertical gap > PARA_GAP x line-height starts a paragraph


@dataclass
class Line:
    text: str
    top: int
    bold: bool          # the whole visual line is bold (an unnumbered heading)
    lead_bold: bool     # the leftmost run is bold (a bold §/chapter marker that
                        # leads regular statutory text on the same line)
    italic: bool


@dataclass
class Para:
    text: str
    bold: bool = False
    lead_bold: bool = False
    italic: bool = False


def pdf_pages(pdf_path):
    """(pageno, [Line]) per page via `pdftohtml -xml`. Each <text> fragment is
    one font run carrying <b>/<i>; fragments on the same baseline are one visual
    line, bold/italic when all their runs are."""
    xml = subprocess.run(
        ["pdftohtml", "-xml", "-i", "-nodrm", "-stdout", str(pdf_path)],
        capture_output=True, check=True).stdout
    # pdftohtml emits occasionally malformed XML (overlapping <b>/<i>, stray &),
    # so parse leniently rather than abort the document
    root = etree.fromstring(xml, etree.XMLParser(recover=True, load_dtd=False,
                                                 no_network=True))
    for page in root.findall("page"):
        spans = []
        for t in page.findall("text"):
            text = normalize_space("".join(t.itertext()))
            if text:
                top, height = int(t.get("top")), int(t.get("height") or 0)
                spans.append((top, int(t.get("left")), top + height, text,
                              t.find(".//b") is not None,
                              t.find(".//i") is not None))
        yield int(page.get("number")), _lines(spans)


def _lines(spans):
    """Group spans sharing a text baseline (top + height) into visual lines, left
    to right. We group on the baseline, not the top, because one line may mix font
    sizes -- a large heading number beside its title ('9' + 'Författnings-
    kommentar'), a bold §-marker leading body text -- and such spans share a
    baseline while sitting at different tops; a top-only grouping would split them
    (and reflow e.g. '9 Författningskommentar' to 'Författningskommentar 9', which
    then fails heading detection). The line's `top` is the topmost of its spans."""
    lines = []
    for top, left, base, text, bold, italic in sorted(spans):
        if lines and abs(base - lines[-1][0]) <= LINE_TOL:
            lines[-1][1].append((left, text, bold, italic))
            lines[-1][2] = min(lines[-1][2], top)
        else:
            lines.append([base, [(left, text, bold, italic)], top])
    out = []
    for _base, runs, top in lines:
        runs.sort()
        # lines rows are untyped heterogeneous lists, so ty can't see r[1]: str
        out.append(Line(normalize_space(" ".join(r[1] for r in runs)), top,  # ty: ignore[invalid-argument-type]
                        all(r[2] for r in runs), runs[0][2],
                        all(r[3] for r in runs)))
    return out


def _dehyphenate(acc, line):
    if acc.endswith("-") and line[:1].islower():
        return acc[:-1] + line          # soft hyphen: "för-\nfogar" -> "förfogar"
    return (acc + " " + line) if acc else line


def page_paragraphs(lines, identifier, pageno):
    """Reflow a page's lines into paragraphs, dropping the running header (the
    identifier), the page-number line and TOC dotted-leader lines. A bold line
    (heading or a §/chapter marker) always begins its own paragraph; otherwise
    a vertical gap larger than the body line-height does. A page dominated by
    dotted leaders is the table of contents -- skipped whole."""
    if sum(RE_DOTS.search(l.text) is not None for l in lines) >= 5:
        return []
    header_re = re.compile(r"\s*".join(re.escape(t) for t in identifier.split()))
    kept = []
    for l in lines:
        text = normalize_space(header_re.sub(" ", l.text))
        if text and text != str(pageno) and not RE_DOTS.search(text):
            kept.append(Line(text, l.top, l.bold, l.lead_bold, l.italic))
    gaps = sorted(b.top - a.top
                  for a, b in zip(kept, kept[1:], strict=False) if b.top > a.top)
    body_gap = gaps[len(gaps) // 2] if gaps else 0      # median line-height
    paras, cur, prev = [], None, None
    for l in kept:
        marker = l.lead_bold and (RE_KAP_MARK.match(l.text)
                                  or RE_PARA_MARK.match(l.text))
        starts = (cur is None or l.bold or marker or (prev and prev.bold)
                  or (body_gap and prev and l.top - prev.top > PARA_GAP * body_gap))
        if starts and cur is not None:
            paras.append(cur)
            cur = None
        if cur is None:
            cur = Para(l.text, l.bold, bool(marker), l.italic)
        else:
            cur.text = _dehyphenate(cur.text, l.text)
            cur.italic = cur.italic and l.italic
        prev = l
    if cur is not None:
        paras.append(cur)
    return paras
