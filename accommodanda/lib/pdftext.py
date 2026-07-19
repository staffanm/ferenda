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
from collections import Counter
from dataclasses import dataclass, field, replace

from lxml import etree  # ty: ignore[unresolved-import]  # lxml ships no stubs

from . import patch
from .util import normalize_space

RE_DOTS = re.compile(r"\.{4,}")                       # TOC dotted leaders
# "2 kap. ...", a bare centered "2 kap." and a lettered "2 a kap."
RE_KAP_MARK = re.compile(r"^(\d+(?:\s?[a-z])?)\s*kap\.(?:\s|$)")
RE_PARA_MARK = re.compile(r"^(\d+\s*[a-z]?)\s*§(?:\s|$)")  # "3 §" / "3 a §"

LINE_TOL = 4          # spans within this many y-units are the same visual line
PARA_GAP = 1.5        # a vertical gap > PARA_GAP x line-height starts a paragraph
HEAD_GAP = 1.6        # a wrapped heading's leading, in multiples of its font size
FOOTNOTE_DROP = 3     # a footnote sits >= this many size units below body size
PAGE_STRIDE = 100000  # per-page `top` offset used by flat_lines: far larger than
                      # any within-page gap, so a whole-document reflow never
                      # merges the foot of one page into the head of the next


@dataclass
class Run:
    """One font run inside a visual line, with its horizontal extent -- the
    signal a two-column layout (a prop's nuvarande/föreslagen lydelse table)
    is reconstructed from."""
    left: int
    right: int
    text: str
    bold: bool
    italic: bool
    size: int = 0


@dataclass
class Line:
    text: str
    top: int
    bold: bool          # the whole visual line is bold (an unnumbered heading)
    lead_bold: bool     # the leftmost run is bold (a bold §/chapter marker that
                        # leads regular statutory text on the same line)
    italic: bool
    size: int = 0       # dominant font size (pt, from the fontspec) -- 0 where
                        # the source carries no font info (OCR/legacy routes)
    runs: list[Run] = field(default_factory=list)


@dataclass
class Para:
    text: str
    bold: bool = False
    lead_bold: bool = False
    italic: bool = False
    size: int = 0       # font size of the opening line; 0 = unknown


def pdftohtml_xml(pdf_path, hidden=False):
    """The raw ``pdftohtml -xml`` output for a PDF, as bytes. Verbose, but the
    one editable text representation of a PDF body -- so it is the patchable
    *intermediate format* of the PDF-bodied sources (förarbeten, föreskrifter,
    JO/ARN, remissvar). `pdf_pages` parses it; `patchsource` shows it for editing.
    ``hidden=True`` adds ``-hidden`` so invisible text is included -- the OCR layer
    ocrmypdf renders behind the page image is invisible, and pdftohtml drops it
    otherwise."""
    args = ["pdftohtml", "-xml", "-i", *(["-hidden"] if hidden else []),
            "-nodrm", "-stdout", str(pdf_path)]
    return subprocess.run(args, capture_output=True, check=True).stdout


def pdf_pages(pdf_path, patch_key=None, hidden=False):
    """(pageno, [Line]) per page via `pdftohtml -xml`. Each <text> fragment is
    one font run carrying <b>/<i>; fragments on the same baseline are one visual
    line, bold/italic when all their runs are. `patch_key=(source, basefile)`
    applies that document's patch to the pdftohtml XML before parsing -- the
    PDF-bodied sources' patch hook (a correction, or a rot13 redaction).
    ``hidden=True`` adds ``-hidden`` so invisible text is included -- the OCR layer
    ocrmypdf renders behind the page image is invisible, and pdftohtml drops it
    otherwise."""
    xml = pdftohtml_xml(pdf_path, hidden)
    if patch_key is not None and patch.has_patch(*patch_key):
        source, basefile = patch_key
        xml = patch.apply(source, basefile,
                          xml.decode("utf-8", "replace")).encode("utf-8")
    # pdftohtml emits occasionally malformed XML (overlapping <b>/<i>, stray &),
    # so parse leniently rather than abort the document
    root = etree.fromstring(xml, etree.XMLParser(recover=True, load_dtd=False,
                                                 no_network=True))
    # font id -> point size, from the <fontspec> declarations (global ids)
    sizes = {f.get("id"): int(f.get("size") or 0)
             for f in root.iter("fontspec")}
    for page in root.findall("page"):
        spans = []
        for t in page.findall("text"):
            text = normalize_space("".join(t.itertext()))
            if text:
                top, height = int(t.get("top")), int(t.get("height") or 0)
                left = int(t.get("left"))
                spans.append((top, left, top + height, text,
                              t.find(".//b") is not None,
                              t.find(".//i") is not None,
                              left + int(t.get("width") or 0),
                              sizes.get(t.get("font"), 0)))
        yield int(page.get("number")), _lines(spans)


def _lines(spans):
    """Group spans sharing a text baseline (top + height) into visual lines, left
    to right. We group on the baseline, not the top, because one line may mix font
    sizes -- a large heading number beside its title ('9' + 'Författnings-
    kommentar'), a bold §-marker leading body text -- and such spans share a
    baseline while sitting at different tops; a top-only grouping would split them
    (and reflow e.g. '9 Författningskommentar' to 'Författningskommentar 9', which
    then fails heading detection). The line's `top` is the topmost of its spans;
    its `size` the largest run's (superscript footnote markers ride along without
    shrinking their line)."""
    grouped: list[tuple[int, list[Run], int]] = []
    for top, left, base, text, bold, italic, right, size in sorted(spans):
        run = Run(left, right, text, bold, italic, size)
        if grouped and abs(base - grouped[-1][0]) <= LINE_TOL:
            prev_base, runs, prev_top = grouped[-1]
            runs.append(run)
            grouped[-1] = (prev_base, runs, min(prev_top, top))
        else:
            grouped.append((base, [run], top))
    out = []
    for _base, runs, top in grouped:
        runs.sort(key=lambda r: r.left)
        out.append(Line(normalize_space(" ".join(r.text for r in runs)), top,
                        all(r.bold for r in runs), runs[0].bold,
                        all(r.italic for r in runs),
                        max(r.size for r in runs), runs))
    return out


def flat_lines(pdf_path, hidden=False):
    """Every visual line across every page as one top-ordered [Line], page breaks
    flattened into large vertical gaps via a per-page `top` offset (PAGE_STRIDE),
    so a reflow over the whole document -- rather than page by page -- never
    merges the foot of one page into the head of the next. For sources whose
    structure ignores page boundaries (an EU act's articles run continuously),
    where per-page `page_paragraphs` would fragment a run across the break."""
    return [replace(line, top=line.top + page * PAGE_STRIDE)
            for page, (_pageno, lines) in enumerate(pdf_pages(pdf_path, hidden=hidden))
            for line in lines]


RE_BARE_PAGENO = re.compile(r"\d{1,4}")


def printed_pageno(lines, identifier):
    """The printed page number a page's marginal header/footer carries, or
    None. Looks at the outermost lines (running header at the top, folio at the
    bottom), strips the running-header identifier, and takes a line whose
    remainder is a bare number. The evidence for the PDF-page ↔ printed-page
    mapping (`page_offset`): a document's printed numbering can start after
    unnumbered cover matter, or -- a multi-volume SOU -- continue from the
    previous volume."""
    header_re = (re.compile(r"\s*".join(re.escape(t) for t in identifier.split()))
                 if identifier else None)
    for l in lines[:3] + lines[-3:]:
        t = (header_re.sub(" ", l.text) if header_re else l.text).strip()
        if RE_BARE_PAGENO.fullmatch(t):
            return int(t)
    return None


def page_offset(detections):
    """The document-wide printed-page offset from per-page evidence:
    ``detections`` maps pdf page index -> detected printed number. The offset
    must be *constant* to be trusted: the mode of the per-page differences
    wins when it carries at least three pages and a clear majority (stray
    bare numbers in margins -- years, annex numbering -- are tolerated as a
    minority). Too little evidence -> 0, the PDF-index-equals-printed-page
    assumption. Two well-supported competing offsets are a genuinely
    ambiguous mapping and raise -- a wrong page anchor is a silent citation
    corruption, so ambiguity must fail visibly, not guess."""
    diffs = Counter(printed - pdfno for pdfno, printed in detections.items())
    if not diffs:
        return 0
    (offset, support), *rest = diffs.most_common()
    if support < 3 or support < 0.6 * sum(diffs.values()):
        return 0                      # sparse/noisy evidence: keep the default
    runner = rest[0][1] if rest else 0
    if runner >= 3 and runner >= 0.3 * sum(diffs.values()):
        raise ValueError(
            "ambiguous printed-page mapping: competing offsets %s"
            % dict(diffs.most_common(4)))
    return offset


def dehyphenate(acc, line):
    if acc.endswith("-") and line[:1].islower():
        return acc[:-1] + line          # soft hyphen: "för-\nfogar" -> "förfogar"
    return (acc + " " + line) if acc else line


def page_paragraphs(lines, identifier, pageno):
    """Reflow a page's lines into paragraphs, dropping the running header (the
    identifier, when one is known -- pass ``None``/``""`` where the source has no
    fixed header to strip, e.g. a letter whose sender's name is prose, not a
    repeated masthead; the substitution is skipped outright rather than built as
    an always-matching pattern, since a header is stripped only where it recurs
    as a header, never as an incidental substring inside body text), the
    page-number line and TOC dotted-leader lines. A bold line (heading or a
    §/chapter marker) always begins its own paragraph; otherwise a vertical gap
    larger than the body line-height does. A page dominated by dotted leaders is
    the table of contents -- skipped whole."""
    if sum(RE_DOTS.search(l.text) is not None for l in lines) >= 5:
        return []
    header_re = (re.compile(r"\s*".join(re.escape(t) for t in identifier.split()))
                 if identifier else None)
    kept = []
    for l in lines:
        raw = header_re.sub(" ", l.text) if header_re else l.text
        text = normalize_space(raw)
        if text and text != str(pageno) and not RE_DOTS.search(text):
            kept.append(replace(l, text=text))
    gaps = sorted(b.top - a.top
                  for a, b in zip(kept, kept[1:], strict=False) if b.top > a.top)
    body_gap = gaps[len(gaps) // 2] if gaps else 0      # median line-height
    body_size = line_body_size(kept)

    def heading(l):
        # heading-ness by font: bold, or larger than the page's body size --
        # a prop's numbered chapter headings are large but NOT bold
        return l.bold or (l.size and body_size and l.size > body_size)

    paras, cur, prev = [], None, None
    for l in kept:
        marker = l.lead_bold and (RE_KAP_MARK.match(l.text)
                                  or RE_PARA_MARK.match(l.text))
        starts = (cur is None or heading(l) or marker
                  or (prev and heading(prev))
                  or (body_gap and prev and l.top - prev.top > PARA_GAP * body_gap))
        if starts and _heading_wrap(prev, l, marker, heading):
            starts = False                # wrapped heading line: same paragraph
        if starts and cur is not None:
            paras.append(cur)
            cur = None
        if cur is None:
            cur = Para(l.text, l.bold, bool(marker), l.italic, l.size)
        else:
            cur.text = dehyphenate(cur.text, l.text)
            cur.italic = cur.italic and l.italic
        prev = l
    if cur is not None:
        paras.append(cur)
    return paras


def line_body_size(lines):
    """The dominant (body) font size of a line sequence, 0 when the source
    carries no font info. Computed over *lines* -- a sparse page's paragraphs
    are too few for a stable mode, its lines are not."""
    sizes = [l.size for l in lines if l.size]
    return Counter(sizes).most_common(1)[0][0] if sizes else 0


# a line opening its own numbered heading ("5.1 Offentligfinansiella …") is
# never the wrapped continuation of the heading above it
RE_NUM_LEAD = re.compile(r"^\d+(?:\.\d+)*\s")


def _heading_wrap(prev, l, marker, heading):
    """Whether line `l` continues a wrapped multi-line heading: the previous
    line and this one are both heading-fonted in the *same* size (a heading and
    its subsection differ in size, so they never fold), sit a heading's own
    leading apart (HEAD_GAP x the size -- known only when font info is), and
    this line neither opens a numbered heading of its own nor is a §/kap
    marker."""
    return bool(prev is not None and heading(prev) and heading(l) and not marker
                and l.size and l.size == prev.size
                and 0 < l.top - prev.top <= HEAD_GAP * l.size
                and not RE_NUM_LEAD.match(l.text))
