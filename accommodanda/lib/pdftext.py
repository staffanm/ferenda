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
import tempfile
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import NamedTuple

import brotli
from lxml import etree  # ty: ignore[unresolved-import]  # lxml ships no stubs

from . import layout, patch
from .util import normalize_space, write_atomic

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


def _converted(pdf_path, cache, args):
    """One poppler conversion of a PDF: served from `cache` when that entry is
    current, else produced by running `args` and cached.

    These subprocesses are the dominant cost of parsing a PDF-bodied document
    (`pdftohtml` is 53-91% of it, 11.8 s for one 4 MB born-digital SOU) and
    their input never changes -- a downloaded PDF is immutable, so every
    re-parse after a parser change was re-running them for nothing. An entry
    older than its PDF is stale: a re-download rewrites the PDF and moves its
    mtime past the entry's."""
    if cache is not None and cache.exists() and \
            cache.stat().st_mtime_ns >= Path(pdf_path).stat().st_mtime_ns:
        return brotli.decompress(cache.read_bytes())
    out = subprocess.run(args, capture_output=True, check=True).stdout
    if cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        # quality 5, not the corpus default 11: this write sits in the parse's
        # critical path, and at 11 brotli would cost more than the conversion it
        # is meant to save. The entry is rebuildable, so size is not precious.
        write_atomic(cache, brotli.compress(out, mode=brotli.MODE_TEXT,
                                            quality=5))
    return out


def pdftohtml_xml(pdf_path, hidden=False):
    """The raw ``pdftohtml -xml`` output for a PDF, as bytes. Verbose, but the
    one editable text representation of a PDF body -- so it is the patchable
    *intermediate format* of the PDF-bodied sources (förarbeten, föreskrifter,
    JO/ARN, remissvar). `pdf_pages` parses it; `patchsource` shows it for editing.
    ``hidden=True`` adds ``-hidden`` so invisible text is included -- the OCR layer
    ocrmypdf renders behind the page image is invisible, and pdftohtml drops it
    otherwise. Cached (see `_converted`)."""
    kind = "hidden.xml" if hidden else "xml"
    return _converted(pdf_path, layout.pdf_conversion(pdf_path, kind),
                      ["pdftohtml", "-xml", "-i", *(["-hidden"] if hidden else []),
                       "-nodrm", "-stdout", str(pdf_path)])


def ocr_pdf(path, lang):
    """OCR a scanned PDF (no recoverable text layer) into a cached hidden
    sidecar, returning its path. Cached beside the source as
    ``.<stem>.ocr.pdf`` so a re-parse is free.

    A missing ocrmypdf binary is a broken environment and propagates
    (rule:fail-fast); a per-document OCR failure (a corrupt scan, a missing
    language pack) raises CalledProcessError, caught at the build driver's
    per-document boundary and recorded there -- never swallowed into an empty
    artifact.

    Extract text from the result with ``hidden=True``: what ocrmypdf adds is an
    invisible text layer behind the page image, which pdftohtml drops otherwise."""
    cached = Path(path).with_name("." + Path(path).stem + ".ocr.pdf")
    if cached.exists():
        return cached
    # --force-ocr: rasterize and OCR every page, replacing the unrecoverable
    # (Identity-H, no ToUnicode) text layer these scans carry -- --skip-text
    # would see that broken layer as "already text" and skip the page.
    subprocess.run(["ocrmypdf", "--quiet", "--force-ocr", "-l", lang,
                    str(path), str(cached)], check=True, capture_output=True)
    return cached


def pdftotext_text(pdf_path):
    """A PDF's text as ``pdftotext`` reads it, with the U+000C page breaks it
    emits left in place.

    The route for the scanned corpora (soukb, propkb's scan-only props): they
    carry an OCR text layer that the font-aware `pdftohtml -xml` path renders
    empty -- and sometimes errors on -- while this reads it. Cached like the
    other conversion, because a scanned document pays for *both* (the font path
    runs first and finds nothing) and there are 5 807 of them."""
    return _converted(pdf_path, layout.pdf_conversion(pdf_path, "txt"),
                      ["pdftotext", str(pdf_path), "-"]).decode("utf-8", "replace")


def pdf_info(pdf_path):
    """poppler's ``pdfinfo`` fields as a dict ("Pages", "Title", …). Raises
    CalledProcessError on a broken or absent PDF: every caller is deciding
    something about the document from these values, and a silently empty dict
    would read as a 0-page, untitled file (rule:fail-fast)."""
    out = subprocess.run(["pdfinfo", str(pdf_path)], capture_output=True,
                         check=True, text=True).stdout
    return {k.strip(): v.strip()
            for k, _, v in (line.partition(":") for line in out.splitlines())
            if _}


def pdf_first_page_text(pdf_path):
    """The first page's text, whitespace-collapsed -- enough to tell a
    rättelseblad from a betänkande without converting the whole file."""
    out = subprocess.run(["pdftotext", "-f", "1", "-l", "1", str(pdf_path), "-"],
                         capture_output=True, check=True, text=True).stdout
    return normalize_space(out)


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


def pdf_images(pdf_path):
    """`(page, top, left, width, height)` for every embedded raster image, in
    page coordinates. The text path (`pdf_pages`) runs `pdftohtml -i`, which drops
    images; this runs it *without* `-i` to get their positions -- pdftohtml writes
    the extracted PNGs beside its output, so it runs into a throwaway temp dir and
    only the coordinates survive. Used to recover content encoded as bitmaps rather
    than text (DV verdicts print their paragraph numbers as tiny margin images)."""
    with tempfile.TemporaryDirectory() as tmp:
        out = str(Path(tmp) / "out")
        subprocess.run(["pdftohtml", "-xml", "-nodrm", str(pdf_path), out],
                       capture_output=True, check=True)
        xml = (Path(tmp) / "out.xml").read_bytes()
    root = etree.fromstring(xml, etree.XMLParser(recover=True, load_dtd=False,
                                                 no_network=True))
    return [(int(page.get("number")), int(im.get("top")), int(im.get("left")),
             int(im.get("width") or 0), int(im.get("height") or 0))
            for page in root.findall("page") for im in page.findall("image")]


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
# a number at the very start or very end of a margin line. The folio is often
# glued to the last footnote of a lagförslag page ("Senaste lydelse 2002:621.
# 115"), where nothing on the page is a line of digits alone. The lookbehind
# keeps the tail of an SFS number out ("2002:621" must not offer 621).
RE_LEADING_PAGENO = re.compile(r"^(\d{1,4})(?!\S)")
RE_TRAILING_PAGENO = re.compile(r"(?<![:\d])(\d{1,4})\s*$")


def _header_stripper(identifier):
    """A function removing the document's own running-header identifier from a
    margin line, so what remains can be read as a folio.

    Tolerant of the two ways the identifier is typeset in the corpus: in
    another case ("PROP. 2007/08:100 BILAGA 1" for identifier "Prop.
    2007/08:100" -- 611 pages of the survey sample) and letter-spaced ("PROP.
    2017/ 18: 100", "P R O P . 2 0 0 8 / 0 9 : 1", which the budget
    propositions typeset that way). Whitespace is therefore allowed between
    *every* character rather than only between tokens, and a trailing digit is
    guarded so "Ds 2004:13" does not match inside "Ds 2004:130"."""
    if not identifier:
        return lambda text: text
    pattern = r"\s*".join(re.escape(c) for c in identifier if not c.isspace())
    if identifier.rstrip()[-1:].isdigit():
        # a digit *directly* after the identifier continues its own number, so
        # "Ds 2004:13" must not match inside "Ds 2004:130". A digit after a
        # space is the folio -- the whole point of stripping the header -- so
        # the guard must not reach across whitespace.
        pattern += r"(?!\d)"
    rx = re.compile(pattern, re.I)
    return lambda text: rx.sub(" ", text)


class PageNumbers(NamedTuple):
    """What a page's margins could be saying its printed number is.

    `strong` -- margin lines that are nothing but digits once the document's
    own running header is stripped. Real evidence, and the only kind allowed to
    *establish* the numbering.

    `weak` -- a number at the very start or end of a line of prose. This is
    where the folio hides on a lagförslag page ("Senaste lydelse 2002:621.
    115"), but it is also where a copyright page's "Stockholm 2013" and an
    ISSN's trailing digits live. Weak numbers may only ever be *chosen between*
    once the numbering is already running; letting one bootstrap the count set
    Ds 2013:60 off by 2011 pages and then cost it every anchor it had.
    """
    strong: tuple
    weak: tuple


def page_number_candidates(lines, identifier):
    """Every printed-page number a page's marginal header/footer could be
    offering, split by how good the evidence is.

    Candidates rather than one answer, because a page in isolation cannot say
    which number is the folio: a lagförslag page carries footnote markers (a
    line reading just "2") *and* the real folio, and taking the first bare
    number read prop. 2003/04:67's page 115 as page 2. Which candidate is right
    follows from the numbering running through the document, so `printed_pages`
    -- which knows the running offset -- chooses."""
    strip = _header_stripper(identifier)
    strong, weak = [], []
    for line in lines:
        text = strip(line.text).strip()
        if RE_BARE_PAGENO.fullmatch(text):
            strong.append(int(text))
            continue
        for rx in (RE_LEADING_PAGENO, RE_TRAILING_PAGENO):
            m = rx.search(text)
            if m:
                weak.append(int(m.group(1)))
    return PageNumbers(tuple(dict.fromkeys(strong)),
                       tuple(dict.fromkeys(w for w in weak if w not in strong)))


# a printed-page offset shift within this many pages of the running offset is
# adopted from a single detection (omitted blank leaves, inserted unnumbered
# divider/plate pages); a larger shift needs corroboration
PAGE_SHIFT_TOL = 10

# "Bilaga 23" in a running header, tolerant of case and of the letter-spacing
# the budget propositions typeset it with ("Bila ga 2", "B ila ga 3"). Digits
# only: roman-numbered bilagor are absent from the corpus as running headers,
# and admitting them would match a stray "I" in prose.
RE_BILAGA = re.compile(r"\bb\s*i\s*l\s*a\s*g\s*a\s*(\d{1,3})\b", re.I)


class PagePosition(NamedTuple):
    """Where a pdf page sits in a document's printed numbering: its printed
    page, and the bilaga whose own numbering it belongs to (None for the body
    -- the ordinary case, and the only one that mints a plain `#sid` anchor)."""
    printed: int | None
    bilaga: str | None


def bilaga_labels(pages, identifier):
    """pdf page -> the bilaga its running header names, for the pages where
    that header actually runs.

    `pages` is the `(pageno, [Line])` stream. A bilaga is only accepted where
    the *same* label appears on an adjoining page, because a table-of-contents
    entry and a bilaga's own title line look exactly like a running header: on
    prop. 2015/16:195's bilaga volume the top line of one page reads "Bilaga 3
    - Definitioner av vissa tekniska specifikationer" while the running header
    on that very page says "Bilaga 23"."""
    strip = _header_stripper(identifier)
    seen = {}
    for pageno, lines in pages:
        for line in lines[:3] + lines[-3:]:
            m = RE_BILAGA.search(strip(line.text))
            if m:
                seen[pageno] = m.group(1)
                break
    return {pageno: label for pageno, label in seen.items()
            if seen.get(pageno - 1) == label or seen.get(pageno + 1) == label}


def _pick_pageno(marks, pageno, offset):
    """Which of a page's candidate folios to believe, given the offset the
    numbering is currently running at.

    Weak evidence may only ever *confirm* the numbering, never move it. So a
    weak number counts when it is exactly the page the count expects -- which
    is what lets the folio glued to a footnote beat the footnote marker beside
    it -- and is ignored otherwise. Granting weak numbers the same tolerance as
    strong ones let a lagrådsremiss reprinting an EU directive walk its own
    numbering away page by page: "L 96/119" and "29.3.2014" offer numbers, each
    lands within tolerance of the running count, and 27 became 28, then 33.

    Strong evidence -- a digits-only margin line -- keeps the tolerance, which
    is what absorbs an omitted blank leaf. Only strong evidence establishes the
    numbering in the first place, so a copyright page's "Stockholm 2013" cannot
    set the count, and only strong evidence may stand against it and be judged
    a shift or a restart by the caller."""
    if marks is None:
        return None
    if offset is None:
        return marks.strong[0] if marks.strong else None
    confirmed = [c for c in marks.strong + marks.weak if c - pageno == offset]
    if confirmed:
        return confirmed[0]
    near = [c for c in marks.strong
            if abs((c - pageno) - offset) <= PAGE_SHIFT_TOL]
    if near:
        return min(near, key=lambda c: abs((c - pageno) - offset))
    return marks.strong[0] if marks.strong else None


def _number_section(candidates, pagenos):
    """Number one run of pages carrying a single printed sequence: pdf page ->
    printed page (or None), plus the page where a numbering *restart* was
    confirmed and this run therefore ends (None if it ran to the end).

    The running-offset rule: a pdf page equals its printed page until a
    marginal number proves otherwise, and the offset that reading implies holds
    until the next trusted one changes it. The offset is piecewise by design --
    PDFs omit blank printed leaves between chapters and bind in unnumbered
    divider pages, so no single document-wide offset exists.

    Reading trust: the first reading establishes the offset outright and
    applies retroactively to the pages before it (unnumbered cover matter maps
    below printed 1 -> no anchor, never a duplicate of the real page 1). A
    later reading shifting the offset by at most PAGE_SHIFT_TOL pages is
    adopted at once. A larger *forward* shift is adopted only when the next
    reading agrees (one misread folio must not drag the rest of the document).
    Any *backward* shift is a section restarting its own numbering, however
    small: never adopted here -- the run ends at the first page of the new
    numbering and the caller decides what that section is. Size is not the
    signal, direction is. A forward shift is an omitted leaf and stays
    tolerance-bounded; a backward one of two pages is a four-page bilaga
    starting over, and adopting it mints the same `#sid` ids twice."""
    out = {}
    offset = None          # None until the first reading
    pending = None         # (implied offset,) awaiting corroboration ...
    pending_at = None      # ... first seen on this page
    first_offset = None
    for pageno in pagenos:
        detected = _pick_pageno(candidates.get(pageno), pageno, offset)
        if detected is not None:
            implied = detected - pageno
            if offset is None:
                offset = first_offset = implied
            elif implied != offset:
                if 0 < implied - offset <= PAGE_SHIFT_TOL:
                    offset = implied            # an omitted blank leaf
                elif pending == implied:
                    if implied > offset:        # corroborated large jump ahead
                        offset = implied
                    else:
                        # a corroborated step *back*: the numbering restarted,
                        # and it restarted on the page that first showed it --
                        # `pending_at`, not this one. That page was written
                        # with the old offset while waiting for corroboration,
                        # so drop it: it is the new section's first page, and
                        # leaving it behind both fabricates a body anchor and
                        # hides the section's own page 1.
                        out.pop(pending_at, None)
                        return (_retro(out, candidates, pagenos, first_offset),
                                pending_at)
                else:
                    # lone outlier: wait for a peer. The page itself keeps the
                    # running offset -- anchoring it to the outlier's own
                    # number would mint a misread or restart-duplicate #sid
                    pending, pending_at = implied, pageno
                    printed = pageno + offset
                    out[pageno] = printed if printed >= 1 else None
                    continue
            pending = pending_at = None
        printed = pageno + (offset if offset is not None else 0)
        out[pageno] = printed if printed >= 1 else None
    return _retro(out, candidates, pagenos, first_offset), None


def _retro(out, candidates, pagenos, first_offset):
    """Apply the run's first offset back over the pages before any reading --
    unnumbered cover matter maps below printed 1 and so carries no anchor."""
    if first_offset is None:
        return out
    for pageno in pagenos:
        marks = candidates.get(pageno)
        if marks is not None and marks.strong:
            break
        if pageno not in out:       # a page this run never reached is not ours
            continue
        printed = pageno + first_offset
        out[pageno] = printed if printed >= 1 else None
    return out


def printed_pages(candidates, pagenos, bilagor=None):
    """pdf page number -> `PagePosition(printed, bilaga)`.

    A document is numbered in one or more *sections*. The body is the first;
    a confirmed backward restart ends it, because from there neither numbering
    can mint a plain `#sid` anchor without duplicating or lying.

    What follows the restart depends on `bilagor` (pdf page -> the bilaga its
    running header names, from `bilaga_labels`). Where the pages say which
    bilaga they belong to, each bilaga is numbered as its own section and its
    pages become `bilaga23-sid42` -- addressable, and unable to collide with
    the body. Per bilaga, because in the documents that restart at all (vår-
    and budgetpropositioner) every bilaga restarts at 1: prop. 2021/22:100 has
    four separate printed page 1s.

    Where the pages do not say -- prop. 2008/09:1, whose utgiftsområden are
    separately paginated with no bilaga anywhere -- they keep no page number,
    exactly as before."""
    labels = bilagor or {}
    pagenos = list(pagenos)
    out = {}
    # the body is numbered once and stops at its first restart -- what follows
    # belongs to whatever section the pages name, not to the body
    body, restart_at = _number_section(candidates, pagenos)
    for pageno, printed in body.items():
        out[pageno] = PagePosition(printed, None)
    if restart_at is None:
        return out
    rest = pagenos[pagenos.index(restart_at):]
    i = 0
    while i < len(rest):
        label = labels.get(rest[i])
        if label is None:
            out[rest[i]] = PagePosition(None, None)
            i += 1
            continue
        j = i
        while j < len(rest) and labels.get(rest[j]) == label:
            j += 1
        _number_run(candidates, rest[i:j], label, out)
        i = j
    return out


def _number_run(candidates, pagenos, label, out):
    """Number one labelled run of pages into `out`, restarting a fresh count
    each time the run's own numbering restarts.

    A section can contain another: a bilaga volume holding several bilagor
    restarts once per bilaga, and a body can be followed by an unlabelled
    restart. Looping here rather than numbering the run once is what keeps
    every page in the map -- an unhandled inner restart used to leave the rest
    of the run absent, and the caller subscripts the map per page."""
    remaining = list(pagenos)
    while remaining:
        section, restart_at = _number_section(candidates, remaining)
        for pageno, printed in section.items():
            out[pageno] = PagePosition(printed, label)
        if restart_at is None:
            return
        cut = remaining.index(restart_at)
        if cut == 0:
            # the run restarts on its very first page, so this pass made no
            # progress; take that page unnumbered and go on, or we spin
            out[remaining[0]] = PagePosition(None, label)
            cut = 1
        remaining = remaining[cut:]


def dehyphenate(acc, line):
    if acc.endswith("-") and line[:1].islower():
        return acc[:-1] + line          # soft hyphen: "för-\nfogar" -> "förfogar"
    return (acc + " " + line) if acc else line


def _strip_header_runs(runs, header_re):
    """The line's text with its running-header fragments removed: a
    `header_re` match whose boundaries coincide with run boundaries is a
    margin header (the id alone, or split "Prop." + "2007/08:138" across
    fragments) and its runs go; a match inside a longer run is prose naming
    the identifier and stays whole."""
    starts, ends, pos = set(), set(), 0
    for r in runs:
        starts.add(pos)
        pos += len(r.text)
        ends.add(pos)
        pos += 1                                # the joining space
    joined = " ".join(r.text for r in runs)
    drop = [(m.start(), m.end()) for m in header_re.finditer(joined)
            if m.start() in starts and m.end() in ends]
    kept, pos = [], 0
    for r in runs:
        span = (pos, pos + len(r.text))
        pos += len(r.text) + 1
        if not any(s <= span[0] and span[1] <= e for s, e in drop):
            kept.append(r.text)
    return " ".join(kept)


def page_paragraphs(lines, identifier, pageno, force_break_tops=frozenset()):
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
        raw = l.text
        if header_re and header_re.search(raw):
            # A running header is its own text fragment(s) -- a standalone
            # margin line, or a margin id the baseline assembly merged onto a
            # body line -- so a match that covers whole runs is stripped. A
            # match *inside* a longer run is body text naming the identifier
            # ("Allmänna reklamationsnämnden gjorde följande bedömning",
            # "… (SOU 2008:97). I betänkandet …") and keeps it, which is what
            # the docstring always promised. Strictly conservative vs the old
            # strip-everywhere: everything dropped here was dropped before.
            if l.runs:
                raw = _strip_header_runs(l.runs, header_re)
            else:
                # no run geometry (OCR/legacy routes): strip only a line that
                # is nothing but the header and a page number/date
                residue = header_re.sub(" ", raw)
                if not re.search(r"[A-Za-zÅÄÖåäö]", residue):
                    raw = residue
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
        # a caller-forced break (DV's bitmap paragraph numbers, whose paragraphs
        # carry no extra vertical gap the heuristic below could see)
        starts = (cur is None or heading(l) or marker or l.top in force_break_tops
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
