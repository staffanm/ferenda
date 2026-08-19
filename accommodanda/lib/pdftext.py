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
     is mostly *not* shared: a förarbete's outline (numbered 14 -> 14.3) and a
     föreskrift's body (``N kap.`` / ``N §``) read different signals, so each
     vertical keeps its own classifier over the same :class:`Para` stream. The
     one reading several verticals do share -- the *letterhead* document, set as
     a narrow margin column beside a wide body and marking its structure by font
     alone -- is :func:`classify_letterhead` here.

The Swedish-legal markers a chapter/§ begins with (``RE_KAP_MARK`` /
``RE_PARA_MARK``) live here because step 2 needs them (a bold marker always opens
its own paragraph) and the classifiers reuse them.
"""

import hashlib
import os
import re
import subprocess
import tempfile
from collections import Counter
from dataclasses import dataclass, field, replace
from functools import lru_cache
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
INDENT_MIN = 8        # a first line this far right of the margin starts one too
BOX_INSET = 6         # a line inset this far at *both* margins is in a ruta
# Between the two populations it has to separate, measured on SOU 2025:115: the
# running head hangs into the outer margin at a left no other line shares (1 of a
# page's ~41 lines, 2%), while the body margin and a ruled box's own inset run
# 31% and 34% of p. 307. Anything from ~5% to ~30% would do; 0.2 is the middle.
MARGIN_SHARE = 0.2    # a line start this common is a margin, not an indent
BOX_MIN_MEASURE = 0.5 # a ruta fills at least this much of the body's measure
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
    # the run's typeface, folded to its family (`base_family`): the signal that
    # separates a chart's axis labels from the prose around them
    font: str = ""


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
    # (start, end, "i"/"b"/"bi") over `text`: which stretches the document
    # emphasised, kept per-span rather than collapsed to the whole-line flags
    spans: list[tuple[int, int, str]] = field(default_factory=list)


@dataclass
class Para:
    text: str
    bold: bool = False
    lead_bold: bool = False
    italic: bool = False
    size: int = 0       # font size of the opening line; 0 = unknown
    top: int = 0        # y of the first line, for placing a figure among the
                        # paragraphs it was printed between
    # style spans over `text`, carried across the lines it was reflowed from
    spans: list[tuple[int, int, str]] = field(default_factory=list)
    boxed: bool = False   # set to a narrower measure than the body: a ruta
    font: str = ""        # its typeface (`base_family`), 0-length when unknown


def command_digest(args):
    """A short, stable digest of the exact command a cache entry was produced
    by. Stored *inside* the entry rather than in its name: a PDF has exactly one
    current conversion per format, so a command change makes the stored one
    wrong, not an alternative -- naming the entry after the digest would leave
    the superseded file on disk forever and grow the cache by its whole size on
    every future poppler flag change. (The facsimile *crop* cache is the
    opposite case and rightly keys its bbox into the filename: many crops of one
    page are valid at once.)"""
    return hashlib.sha256(
        " ".join(str(a) for a in args).encode()).hexdigest()[:16]


def _run_conversion(args):
    """Run a poppler conversion and return its output bytes.

    A ``pdftohtml`` invocation that ends in an output base writes into a
    temporary directory and is read back from it; poppler puts the images it
    extracts beside that base, so the directory is what keeps them out of the
    corpus. Anything else (``pdftotext``, or a command already using
    ``-stdout``) is captured from stdout as before."""
    if args[0] != "pdftohtml":
        return subprocess.run(args, capture_output=True, check=True).stdout
    with tempfile.TemporaryDirectory() as tmp:
        base = str(Path(tmp) / "out")
        subprocess.run([*args, base], capture_output=True, check=True)
        return (Path(tmp) / "out.xml").read_bytes()


def _converted(pdf_path, cache, args):
    """One poppler conversion of a PDF: served from `cache` when that entry is
    current, else produced by running `args` and cached.

    These subprocesses are the dominant cost of parsing a PDF-bodied document
    (`pdftohtml` is 53-91% of it, 11.8 s for one 4 MB born-digital SOU) and
    their input never changes -- a downloaded PDF is immutable, so every
    re-parse after a parser change was re-running them for nothing.

    Two things make an entry stale, and both must, because either alone is a
    silent wrong answer rather than an error. The PDF is *newer* than the entry:
    a re-download rewrites the file and moves its mtime past the entry's. Or the
    entry was produced by a *different command*: a flag change alters the output
    while leaving the PDF untouched, so nothing about the file says so, and the
    digest recorded in the entry is the only witness. Dropping ``-i`` and still
    seeing no images was that second case."""
    digest = command_digest(args).encode()
    if cache is not None and cache.exists() and \
            cache.stat().st_mtime_ns >= Path(pdf_path).stat().st_mtime_ns:
        stored = brotli.decompress(cache.read_bytes())
        head, _, payload = stored.partition(b"\n")
        if head == digest:
            return payload
    out = _run_conversion(args)
    if cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        # quality 5, not the corpus default 11: this write sits in the parse's
        # critical path, and at 11 brotli would cost more than the conversion it
        # is meant to save. The entry is rebuildable, so size is not precious.
        write_atomic(cache, brotli.compress(digest + b"\n" + out,
                                            mode=brotli.MODE_TEXT, quality=5))
    return out


def pdftohtml_xml(pdf_path, hidden=False):
    """The raw ``pdftohtml -xml`` output for a PDF, as bytes. Verbose, but the
    one editable text representation of a PDF body -- so it is the patchable
    *intermediate format* of the PDF-bodied sources (förarbeten, föreskrifter,
    JO/ARN, remissvar). `pdf_pages` parses it; `patchsource` shows it for editing.
    ``hidden=True`` adds ``-hidden`` so invisible text is included -- the OCR layer
    ocrmypdf renders behind the page image is invisible, and pdftohtml drops it
    otherwise. Cached (see `_converted`).

    ``-i`` ("ignore images") is deliberately *not* passed: poppler reports every
    embedded raster as an ``<image>`` with its placement on the page, which is
    what a förarbete's figure is, and passing it discarded them before anything
    could see one.

    But without ``-i`` poppler also *extracts* every one of those rasters to a
    file, and it derives their path from the PDF's own -- writing them into the
    corpus beside the source, ``-stdout`` notwithstanding. On a scanned SOU that
    is one full-page JPEG per page: the first corpus-wide run wrote 1,064,761
    files totalling 350 GB into ``downloaded/forarbete/sou`` and filled the
    disk. So the conversion runs with its output base inside a temporary
    directory, which takes the extracted images with it when it goes. Only the
    geometry is wanted here; `facsimile.cached` renders the pixels on
    demand from the PDF itself."""
    args = ["pdftohtml", "-xml", *(["-hidden"] if hidden else []),
            "-nodrm", str(pdf_path)]
    kind = "hidden.xml" if hidden else "xml"
    return _converted(pdf_path, layout.pdf_conversion(pdf_path, kind), args)


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


# poppler's own words when a PDF's cross-reference table is unusable -- the one
# `pdftohtml` failure `repair_pdf` can do anything about. Matched against stderr
# so every other non-zero exit keeps propagating.
_XREF_FAILURE = re.compile(rb"Couldn't (find trailer dictionary|read xref table)")


def repair_pdf(path):
    """A PDF whose cross-reference table poppler cannot read, rewritten by
    ghostscript's object scan into a readable one. Cached beside the source as
    ``.<stem>.repaired.pdf``, the same shape as `ocr_pdf`.

    Some registrators serve a PDF whose ``startxref`` points at the blank
    placeholder a linearizer never filled in: the objects are all present, but
    with no trailer dictionary poppler refuses the whole file ("Couldn't find
    trailer dictionary") and `pdftohtml` exits non-zero. Ghostscript ignores the
    xref and reconstructs it by scanning for ``obj`` markers, which recovers the
    text layer intact -- this is a repairable file, not a lost one, so it is
    worth the extra process before giving up on it.

    ghostscript is not a new dependency: ocrmypdf already requires it. A missing
    binary is a broken environment and propagates (rule:fail-fast).

    gs writes to a temporary sibling that is renamed into place only on a clean
    exit. Writing straight to the cache path would leave a truncated PDF behind
    when the child dies mid-write -- and the `cached.exists()` short-circuit
    above would then trust that fragment forever, silently. That child dying is
    not hypothetical: `avg/KNOWN-GAPS.md` records ghostscript/tesseract being
    killed under a 32-way parallel parse."""
    cached = Path(path).with_name("." + Path(path).stem + ".repaired.pdf")
    # an entry older than its source is stale, the same contract `_converted`
    # keeps: a re-download rewrites the PDF and moves its mtime past the
    # sidecar's, and serving the previous document's text forever would be
    # silent and permanent
    if cached.exists() and \
            cached.stat().st_mtime_ns >= Path(path).stat().st_mtime_ns:
        return cached
    staged = cached.with_suffix(".pdf.part")
    subprocess.run(["gs", "-q", "-o", str(staged), "-sDEVICE=pdfwrite",
                    str(path)], check=True, capture_output=True)
    os.replace(staged, cached)
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
    PDF-bodied sources' patch hook (a correction, or an obfuscated redaction).
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
    # font id -> point size / typeface, from the <fontspec> declarations
    # (global ids, declared on the page each is first used on)
    sizes = {f.get("id"): int(f.get("size") or 0)
             for f in root.iter("fontspec")}
    fonts = {f.get("id"): base_family(f.get("family"))
             for f in root.iter("fontspec")}
    for page in root.findall("page"):
        spans = []
        for t in page.findall("text"):
            # internal whitespace collapsed but the run's *edge* spaces kept:
            # poppler splits a line at every font change, and whether the
            # printed line had a space at that seam is recorded only there --
            # the geometry cannot say, since a font change with a space
            # ("finns i " + "bilaga 2") and one without ("bilaga 2" + ".")
            # both leave the runs touching. `_join_runs` reads them; the Line's
            # own text is normalized after assembly, so no edge survives.
            text = re.sub(r"\s+", " ", "".join(t.itertext()))
            if text.strip():
                top, height = int(t.get("top")), int(t.get("height") or 0)
                left = int(t.get("left"))
                spans.append((top, left, top + height, text,
                              t.find(".//b") is not None,
                              t.find(".//i") is not None,
                              left + int(t.get("width") or 0),
                              sizes.get(t.get("font"), 0),
                              fonts.get(t.get("font"), "")))
        yield int(page.get("number")), _lines(spans)


RE_PAGE_SIZE = re.compile(r"^Page\s+\d+\s+size:\s+([\d.]+) x ([\d.]+)", re.M)


def _page_pt_width(pdf_path, pageno):
    """The width of one page in PDF points, which pdftohtml's XML does not
    carry: its geometry is in poppler's own pixel space, and the ratio of the
    two is what converts a figure's box for the crop renderer. One `pdfinfo`
    per page *that has a figure* -- roughly one page in three hundred."""
    out = subprocess.run(["pdfinfo", "-f", str(pageno), "-l", str(pageno),
                          str(pdf_path)],
                         capture_output=True, check=True, text=True).stdout
    m = RE_PAGE_SIZE.search(out)
    assert m, "pdfinfo reported no page size for %s p.%d" % (pdf_path, pageno)
    return float(m.group(1))


def pdf_figures(pdf_path, patch_key=None):
    """The images a PDF carries that are document content, as
    ``{page: ([Figure], page_pt_width, page_px_width)}`` -- the two page widths
    so a caller can convert a figure's geometry to the points the crop renderer
    takes (`points_from_pdftohtml`).

    Poppler reports every embedded raster, which is mostly furniture: bullet
    glyphs, hairline rules, samling logos, and for a scanned document the page
    image itself. `is_figure` keeps the ones set inside the text margins at the
    measure's own scale. Reads the same cached conversion `pdf_pages` does, so
    this costs a parse of already-converted XML, not a second poppler run."""
    xml = pdftohtml_xml(pdf_path)
    if patch_key is not None and patch.has_patch(*patch_key):
        source, basefile = patch_key
        xml = patch.apply(source, basefile,
                          xml.decode("utf-8", "replace")).encode("utf-8")
    root = etree.fromstring(xml, etree.XMLParser(recover=True, load_dtd=False,
                                                 no_network=True))
    out = {}
    for page in root.findall("page"):
        images = page.findall("image")
        if not images:
            continue
        pageno = int(page.get("number"))
        px_width = int(page.get("width") or 0)
        px_height = int(page.get("height") or 0)
        # the text margins of this page, which is what "inside the margins"
        # and the measure are both read from
        lefts = Counter(int(t.get("left")) for t in page.findall("text"))
        rights = Counter(int(t.get("left")) + int(t.get("width") or 0)
                         for t in page.findall("text"))
        left = lefts.most_common(1)[0][0] if lefts else None
        right = rights.most_common(1)[0][0] if rights else None
        figs = [f for f in
                (_on_page(Figure(pageno, int(im.get("left")), int(im.get("top")),
                                 int(im.get("width") or 0),
                                 int(im.get("height") or 0)), px_height)
                 for im in images)
                if is_figure(f, (left, right))]
        if figs:
            out[pageno] = (figs, _page_pt_width(pdf_path, pageno), px_width)
    return out


def pages_with_ocr(pdf_path, patch_key=None, lang="swe"):
    """(pageno, [Line]) per page, OCR'ing first when the PDF has no readable
    text.

    Two distinct failures look identical from here and one route covers both: a
    scan with no text layer at all, and one whose text layer poppler renders
    *invisible* -- which is what an already-OCR'd scan looks like, and what
    `pdf_pages` drops without ``hidden=True``. So every extraction asks for
    hidden text, and a PDF that still yields nothing goes through ocrmypdf.

    Emptiness is judged on *lines*, before `page_paragraphs`: a PDF that
    genuinely holds only a letterhead would OCR pointlessly if judged on the
    paragraphs left after stripping, and OCR is the expensive path.

    Shared by the three corpora that read pages this way and meet the same pair
    of failures: remissvar, the Konkurrensverket diarium's scanned decisions,
    and the handful of scanned rättsliga ställningstaganden (Försäkringskassans
    2018:05 has no text layer at all, Kronofogdens 5/14/TSM an invisible one).
    `eurlex.parse_pdf` meets them too but keeps its own copy -- it
    flattens across pages (`flat_lines`) rather than reading page by page, so
    it consumes a different shape and there is nothing here for it to reuse.

    A third failure joins them here because it arrives from the same
    registrators: a PDF poppler refuses outright over an unreadable
    cross-reference table, which `repair_pdf` rebuilds. Everything after the
    repair -- the emptiness test, the OCR route -- runs against the repaired
    copy, since that is the readable document from then on.

    Only *that* failure is repaired. `pdftohtml` exits non-zero for other
    reasons too -- an encrypted document, a child killed under a parallel parse
    -- and those must keep surfacing as the per-document error the build driver
    records, not be silently rerouted through ghostscript (rule:no-catch-log-
    continue). The xref failure identifies itself in poppler's stderr, so that
    string is the gate.
    """
    try:
        pages = list(pdf_pages(str(pdf_path), patch_key, hidden=True))
    except subprocess.CalledProcessError as exc:
        if not _XREF_FAILURE.search(exc.stderr or b""):
            raise
        pdf_path = repair_pdf(pdf_path)
        pages = list(pdf_pages(str(pdf_path), patch_key, hidden=True))
    if any(lines for _pageno, lines in pages):
        return pages
    return list(pdf_pages(str(ocr_pdf(pdf_path, lang)), patch_key, hidden=True))


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


SPACE_MIN = 3         # horizontal gap between runs that means a real space


# A figure is sized against the *text column*, not the paper: a förarbete sets
# its illustrations to the measure its prose is set to, and the paper carries
# margins the figure never uses. Prop. 2017/18:89's pyramid on page 40 is 145 px
# on a 701 px page but a 446 px column -- 21% of the paper, 33% of the measure --
# so a paper-relative floor rejects the one figure we know we want.
FIGURE_MIN = 0.25     # of the text measure, in width OR height


@dataclass
class Figure:
    """An image poppler reports on a page, in its own pixel geometry."""
    page: int
    left: int
    top: int
    width: int
    height: int


def _on_page(fig, px_height):
    """`fig` with its foot cut back to the page bottom. poppler reports an
    image's placed height, which for a figure set at the foot of a page can run
    past the paper -- the part that overhangs is not printed and not in the
    facsimile. `is_figure` bounds a figure horizontally against the text
    margins, so only the vertical side needs this. The crop renderer refuses a
    rectangle that leaves the page (`facsimile.OffPage`), and it is right to:
    the geometry is fixed here, where the page is known, not there."""
    if px_height and fig.top + fig.height > px_height:
        return replace(fig, height=max(px_height - fig.top, 0))
    return fig


def is_figure(fig, margins):
    """Whether an image is document content rather than page furniture.

    Deliberately conservative: it must sit *inside the text margins* -- which
    excludes the logos, rules and letterhead marks a samling prints outside them
    -- and be large in one dimension relative to the text measure, which
    excludes the bullet glyphs and hairline rules that make up the bulk of what
    poppler reports (2,798 of 3,637 distinct placements over 40 förarbeten are
    under 60 px). A full-page scan fails the margin test by covering them.

    A page whose text poppler could not place (a scan) has no margins, and so no
    measure to judge a figure against: nothing on it is content."""
    left, right = margins
    if left is None or right is None or right <= left:
        return False
    return (left <= fig.left and fig.left + fig.width <= right
            and max(fig.width, fig.height) >= FIGURE_MIN * (right - left))


def points_from_pdftohtml(page_px, page_pt, box):
    """A pdftohtml ``(left, top, width, height)`` as the ``[x0, y0, x1, y1]`` in
    PDF points that `lib.facsimile.render_region` crops with.

    poppler reports its XML geometry in its own pixel space, not in points --
    1.5 px per point at its default resolution, so prop. 2017/18:89's 467.76 pt
    page is 701 px wide. The scale is derived from the page's own two
    measurements rather than assumed, since it is a poppler default and nothing
    in the format promises it. Cropping with the raw numbers lands roughly a
    third of the way down and to the right of the figure, on body text."""
    scale = page_px / page_pt
    left, top, width, height = box
    return [left / scale, top / scale,
            (left + width) / scale, (top + height) / scale]


def run_style(run):
    """The emphasis a run carries, as a stable flag string ("i", "b", "bi") --
    empty for ordinary body text. Superscript is not a font attribute poppler
    reports; it is inferred from geometry by the footnote-marker pass, which
    tags its own runs."""
    return ("b" if run.bold else "") + ("i" if run.italic else "")


def _normalized_with_spans(text, spans):
    """`normalize_space(text)` with the style spans remapped onto the result.
    The offsets have to be carried through the same collapse that produces the
    text, since a span recorded against the raw join would slide by however many
    spaces the collapse removed before it."""
    out, index, prev_ws = [], [], True      # prev_ws: leading space is dropped
    for ch in text:
        if ch.isspace():
            if prev_ws:
                index.append(len(out))
                continue
            prev_ws = True
            index.append(len(out))
            out.append(" ")
            continue
        prev_ws = False
        index.append(len(out))
        out.append(ch)
    index.append(len(out))
    while out and out[-1] == " ":            # trailing space
        out.pop()
    end = len(out)
    remapped = [(min(index[a], end), min(index[b], end), style)
                for a, b, style in spans]
    return "".join(out), [(a, b, s) for a, b, s in remapped if a < b]


def _join_runs(runs):
    """One line's runs as `(text, style spans)` -- the one way to rebuild a
    line's text from its runs, so the text and the spans over it cannot get out
    of step. (dv drops a line's sub-body-size runs and rebuilds it; joining
    those on a space and keeping the original line's spans left them pointing
    past the end of the shorter text.)

    A run boundary is not a word
    boundary: poppler splits a line at every font change, so an italic phrase
    inside a sentence arrives as three runs and the punctuation that follows it
    starts its own ("En sammanfattning … finns i " / "bilaga 1" /
    ". Utredningens …"). Joining those on a space wrote "bilaga 1 ." -- a space
    before every period, comma and parenthesis that trails an italic or bold
    phrase, throughout the corpus.

    The runs carry their own spacing, so they are butted together and a space is
    added only where the geometry says one was printed: a horizontal gap with no
    whitespace already at either side of the seam.

    The spans are what the run boundary is *for*: which stretch of the line the
    document set in italics or bold, in the line's own text coordinates, so the
    emphasis survives into the artifact instead of being flattened to a
    whole-line flag."""
    if not runs:                 # every run was a header fragment, dropped
        return "", []
    out, spans, pos = [], [], 0
    for prev, run in zip([None] + runs[:-1], runs, strict=True):
        if (prev is not None and run.left - prev.right >= SPACE_MIN
                and not prev.text.endswith((" ", "\t"))
                and not run.text.startswith((" ", "\t"))):
            out.append(" ")
            pos += 1
        style = run_style(run)
        if style:
            spans.append((pos, pos + len(run.text), style))
        out.append(run.text)
        pos += len(run.text)
    return _normalized_with_spans("".join(out), spans)


# pdftohtml reports an embedded font by its PDF name, which carries a subset tag
# and a style suffix that vary with the weight: one document's running text is
# "BCDEEE+TimesNewRomanPSMT" and its headings "BCDFEE+TimesNewRomanPS". Both are
# Times; a chart's "BCDKEE+TradeGothic" is not, and that is the distinction
# worth keeping, so the tag and the style are folded away and the typeface kept.
RE_FONT_SUBSET = re.compile(r"^[A-Z]{6}\+")
# `identityh` is the CMap the font is encoded with, not a style, but poppler
# reports it inside the name ("EUAlbertina-Regu-Identity-H") and one annex
# reproducing an EU directive sets its prose and its headings in two spellings
# of the same face
_FONT_STYLE_TOKENS = ("bolditalicmt", "boldmt", "italicmt", "psmt", "mt", "ps",
                      "identityh", "bold", "italic", "oblique", "regular",
                      "regu")


def base_family(name):
    """A pdftohtml font name folded to its typeface, lowercased: the subset tag
    and style suffixes dropped. `""` where the source names no font."""
    fam = re.sub(r"[^a-z]", "", RE_FONT_SUBSET.sub("", name or "").lower())
    shrinking = True
    while shrinking:
        shrinking = False
        for token in _FONT_STYLE_TOKENS:
            if fam.endswith(token) and len(fam) > len(token):
                fam, shrinking = fam[:-len(token)], True
                break
    return fam


def _lines(spans):
    """Group spans sharing a text baseline (top + height) into visual lines, left
    to right. We group on the baseline, not the top, because one line may mix font
    sizes -- a large heading number beside its title ('9' + 'Författnings-
    kommentar'), a bold §-marker leading body text -- and such spans share a
    baseline while sitting at different tops; a top-only grouping would split them
    (and reflow e.g. '9 Författningskommentar' to 'Författningskommentar 9', which
    then fails heading detection). The line's `top` is the topmost of its spans;
    its `size` its dominant run's (see `line_from_runs`).

    The spans are walked in *baseline* order, not `top` order, for the same
    reason the grouping keys on it. Each span is only compared with the group
    most recently opened, so a fragment on some third baseline sorting between
    two that share one splits them: a proposition prints its running header in
    the left margin at a `top` between a chapter number and its title ("3" at
    top 65, "Ärendet och dess beredning" at 61, "Prop. 2017/18:89" at 63,
    baselines 85, 86 and 76). Sorted by top the header lands in the middle and
    the heading comes apart -- the title becomes a stycke and the number a
    paragraph of its own, on every numbered heading whose page prints the header
    at that height."""
    grouped: list[tuple[int, list[Run], int]] = []
    for top, left, base, text, bold, italic, right, size, font in sorted(
            spans, key=lambda s: (s[2], s[1], s[0])):
        run = Run(left, right, text, bold, italic, size, font)
        if grouped and abs(base - grouped[-1][0]) <= LINE_TOL:
            prev_base, runs, prev_top = grouped[-1]
            runs.append(run)
            grouped[-1] = (prev_base, runs, min(prev_top, top))
        else:
            grouped.append((base, [run], top))
    out = []
    for _base, runs, top in grouped:
        runs.sort(key=lambda r: r.left)
        out.append(line_from_runs(runs, top))
    return out


def line_from_runs(runs, top):
    """A `Line` built from its runs -- text, style spans *and* the whole-line
    style flags all derived from the same run set, so they cannot disagree.

    The one constructor, for the same reason `_join_runs` is the one way to
    rebuild the text: a caller that drops runs from a line (dv strips the
    sub-body-size marginalia its verdicts share a baseline with) and then
    patches only `text` leaves `bold`/`lead_bold`/`italic` describing the run
    set that no longer exists. That is not cosmetic -- `page_paragraphs.heading`
    and dv's `Rubrik` test both read those flags, so a bold heading that shared
    a baseline with a Dok.Id stamp shipped as a `Stycke`."""
    # a precondition, not validation of external data: `_lines`' groups are
    # non-empty by construction and a run-dropping caller filters first. Without
    # it the failure is a bare IndexError out of `runs[0].bold` (rule:fail-fast)
    assert runs, "line_from_runs needs at least one run"
    text, spans = _join_runs(runs)
    # The line's size is the size *most of its characters* are set in, not its
    # largest run's. Both readings keep a raised footnote marker from shrinking
    # the line it rides on -- the marker is one or two characters -- but only
    # this one keeps a marker from *growing* its note: a lagtext page sets the
    # note number at body size over text two sizes smaller ("8" at 15 leading
    # "Senaste lydelse 2021:173." at 12), and where the two share a baseline the
    # largest run made the whole line body-sized. The footnote gate in every
    # vertical's classifier reads this size, so the note came out a heading. A
    # tie goes to the larger size, which is what `max` said.
    weight = Counter()
    for run in runs:
        weight[run.size] += len(run.text)
    return Line(text, top,
                all(r.bold for r in runs), runs[0].bold,
                all(r.italic for r in runs),
                max(weight, key=lambda size: (weight[size], size)),
                runs, spans)


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
    """The line's runs with its running-header fragments removed: a `header_re`
    match whose boundaries coincide with run boundaries is a margin header (the
    id alone, or split "Prop." + "2007/08:138" across fragments) and its runs
    go; a match inside a longer run is prose naming the identifier and stays
    whole.

    The offsets are taken from `_join_runs`, which is what actually assembles
    the line: computing them here as "each run plus a joining space" was right
    only while that was the join. Once runs were butted together and spaced by
    geometry, every boundary was off by however many spaces the line did not
    have, no match lined up, and the header stopped being stripped at all --
    it appeared inside the body text of every page whose header shares a
    baseline with the first line of prose."""
    joined, _ = _join_runs(runs)
    starts, ends, pos = set(), set(), 0
    for run in runs:
        text, _ = _join_runs([run])
        offset = joined.find(text, pos)
        # every run's own normalisation is a substring of the line's, since
        # `_join_runs` collapses both the same way. If that ever stops holding
        # the header silently reappears inside body prose corpus-wide, which is
        # the bug this function was rewritten to fix -- so it fails loudly
        # instead (rule:fail-fast).
        assert offset >= 0, "run %r not found in joined line %r" % (text, joined)
        starts.add(offset)
        ends.add(offset + len(text))
        pos = offset + len(text)
    drop = [(m.start(), m.end()) for m in header_re.finditer(joined)
            if m.start() in starts and m.end() in ends]
    kept, pos = [], 0
    for run in runs:
        text, _ = _join_runs([run])
        offset = joined.find(text, pos)
        pos = offset + len(text)
        if not any(s <= offset and offset + len(text) <= e for s, e in drop):
            kept.append(run)
    return kept


HEAD_MARGIN_SHARE = 0.75   # of the page's width: where a margin header sits


def _strip_split_header(lines, identifier):
    """`lines` without a running-header identifier the typesetter set over two
    baselines in the outer margin.

    `_strip_header_runs` matches the identifier inside one line's text, so it
    cannot see a header broken across lines: prop. 2025/26:77 prints "Prop." at
    the height of its title's first line and "2025/26:77" at the second, both in
    the right margin. Neither line contains the whole identifier, so nothing was
    stripped and the title came out spliced -- "Anpassning av svensk rätt till
    EU:s nya Prop. förordning om skyddade beteckningar på 2025/26:77
    jordbruksprodukter och livsmedel".

    Geometry has to decide it, because the text alone no longer can, and three
    conditions together keep that off body prose. Every fragment stands as its
    own run and reads as exactly one token of the identifier the caller named --
    prose that mentions the document sets it inside a longer run. Every fragment
    sits in the outer `HEAD_MARGIN_SHARE` of the page's width. And the fragments
    joined are the identifier entire, in reading order: a lone "Prop." in the
    margin is not a header and is left alone.

    The single-line header stays with `_strip_header_runs`, which the reflow
    below depends on for more than the strip -- it is what tells that function's
    caller the page opened with a head."""
    tokens = identifier.split() if identifier else []
    if len(tokens) < 2:
        return lines
    edge = HEAD_MARGIN_SHARE * max((r.right for l in lines for r in l.runs),
                                   default=0)
    head = [[r for r in l.runs if r.left >= edge and r.text.strip() in tokens]
            for l in lines]
    if (sum(1 for rs in head if rs) < 2
            or [r.text.strip() for rs in head for r in rs] != tokens):
        return lines
    kept = []
    for l, rs in zip(lines, head, strict=True):
        body_runs = [r for r in l.runs if r not in rs]
        if not rs:
            kept.append(l)
        elif body_runs:
            kept.append(line_from_runs(body_runs, l.top))
        # a line that was nothing but header fragments is dropped whole
    return kept


# A whole line that is nothing but this page's number, in the forms Swedish
# agencies set it: the bare number, "3 (12)" / "3(12)", and "Sida 3" /
# "Sid 3 av 12". Every form is anchored to the page's *own* number, so a line
# of body text can never match one -- "1 (3)" is a plausible enough string that
# an unanchored pattern would eat real content.
_PAGE_NUMBER = r"(?:sid(?:a|an)?\.?\s*)?%d(?:\s*[(/]\s*\d+\s*\)?|\s+av\s+\d+)?"


@lru_cache(maxsize=None)
def _page_number_re(pageno):
    return re.compile(_PAGE_NUMBER % pageno, re.IGNORECASE)


def is_page_number(text, pageno):
    """Whether `text` is the whole of a page-number line for page `pageno`.

    The bare number was already dropped; the parenthesised form was not, so
    every föreskrift page carried its "1 (3)" into the body -- as a heading, in
    the innehåll panel, and splitting the sentence that ran across the page
    break (D7). A footnote is a different thing and stays: it is body the
    document wrote, marked by its smaller size (FOOTNOTE_DROP), not chrome the
    typesetter added.

    ``pageno`` is None for a page `printed_pages` could not place -- one past a
    numbering restart that names no bilaga (prop. 2008/09:1's separately
    paginated utgiftsområden). Such a page keeps no printed number, so nothing
    on it can be that number, and the whole force of these patterns is that they
    are anchored to the page's own: unanchored, "1 (3)" is a plausible enough
    string to eat real content."""
    if pageno is None:
        return False
    return bool(_page_number_re(pageno).fullmatch(text.strip()))


# --- running page furniture, discovered rather than named ---------------------
# `page_paragraphs` strips a header the caller can *name* (förarbete's document
# identifier, avg's court name). A source where every document carries a
# different masthead -- remisser, where each of ~90 organisations answers on its
# own letterhead -- has no such string, and passing the organisation's own name
# is worse than nothing: it recurs as ordinary self-reference in body prose
# ("Ale kommun välkomnar..."). What the furniture does have, whatever it says, is
# a shape: it repeats on most pages, sits in a margin, and is set small.

FURNITURE_MIN_PAGES = 2     # below this, repetition is not evidence of anything
FURNITURE_MIN_SHARE = 0.6   # of pages a line class must appear on
FURNITURE_MARGIN = 0.12     # top/bottom fraction of the page it must sit in
_RE_DIGITS = re.compile(r"\d+")


def _furniture_key(text):
    """The class a line belongs to for repetition counting: its text with digit
    runs masked and whitespace collapsed. Masking is what makes this work at all
    -- a running footer carries the page number and often the date, so it differs
    literally on every page ("... 4(5)", "... 5(5)") while being the same
    furniture. Returns None for a line too short to judge."""
    key = _RE_DIGITS.sub("#", normalize_space(text))
    return key if len(key) >= 4 else None


def strip_page_furniture(pages, min_pages=FURNITURE_MIN_PAGES,
                         min_share=FURNITURE_MIN_SHARE, margin=FURNITURE_MARGIN):
    """`[(pageno, [Line])]` -> the same, with running headers/footers dropped.

    A line is furniture when all three hold: its digit-masked text recurs on at
    least `min_share` of the pages, it sits within `margin` of the top or bottom
    of the page's text block, and it is no larger than the page's dominant body
    size. Requiring them together is deliberate -- deleting a real sentence is
    far worse than leaving a masthead in, and each signal alone has a plausible
    counter-example: a short answer may repeat a real sentence, a heading sits
    high on the page, and body text is body-sized by definition.

    Where the source carries no font sizes (the OCR and legacy routes) the size
    test cannot apply and repetition plus margin position decide alone. That is
    the opposite call from `drop_footnotes`, which refuses to act at all without
    sizes -- deliberately: a footnote is *only* identifiable by its size, whereas
    a line repeating in the margin of every page is already furniture whatever
    it is set in.

    Documents under `min_pages` pages are returned untouched -- a single page
    establishes nothing. The floor is two rather than three deliberately: a third
    of remissvar are one or two pages and at three were never stripped at all,
    while at two a line must appear on *both* pages, which is evidence. The digit
    masking is what makes "1(2)" and "2(2)" the same line.
    """
    pages = list(pages)
    if len(pages) < min_pages:
        return pages
    counts = Counter()
    for _pageno, lines in pages:
        # per page, not per line: a phrase repeated three times on one page is
        # not thereby a running header
        counts.update({k for k in (_furniture_key(l.text) for l in lines) if k})
    threshold = max(2, int(len(pages) * min_share))
    repeated = {k for k, n in counts.items() if n >= threshold}
    if not repeated:
        return pages

    out = []
    for pageno, lines in pages:
        tops = [l.top for l in lines]
        if not tops:
            out.append((pageno, lines))
            continue
        lo, hi = min(tops), max(tops)
        span = (hi - lo) or 1
        body_size = line_body_size(lines)
        kept = [l for l in lines
                if not (_furniture_key(l.text) in repeated
                        and ((l.top - lo) / span <= margin
                             or (hi - l.top) / span <= margin)
                        and (not body_size or not l.size or l.size <= body_size))]
        out.append((pageno, kept))
    return out


# --- footnotes, for a source that does not want them --------------------------
# förarbete and dv *keep* footnotes: for a court decision the endnote apparatus is
# where the citations live. A remissvar is the opposite case -- its footnotes are
# nearly always a source reference ("Se EU-kommissionens vägledning, kapitel
# 5.3"), never the sentence stating why the organisation objects, and poppler
# interleaves both the note and its superscript marker into the body text, so a
# sentence reads "...de som registreras 7 EU-kommissionens vägledning, C
# (2023)1392 slutlig tilldelas ett samordningsnummer". A reader gets nonsense and
# a quote spanning the splice matches nothing. Same size test förarbete already
# uses to *find* footnotes (`FOOTNOTE_DROP`), applied to drop them instead.
_RE_MARKER = re.compile(r"^[\s\[(]*\d{1,3}[\s\])]*$")


def _marker_runs(runs, body):
    """`runs` with superscript reference markers removed: a run set noticeably
    smaller than the body size whose whole text is a bare number. Size is the
    signal, not the digit -- "3" at body size is a paragraph number, an amount or
    a chapter, and dropping those would eat real text."""
    return [r for r in runs
            if not (r.size and r.size <= body - FOOTNOTE_DROP
                    and _RE_MARKER.fullmatch(r.text))]


def drop_marker_lines(lines, body):
    """`lines` without the superscript reference markers that stand as lines of
    their own -- the note text they point at is untouched.

    A raised reference has a baseline of its own, so `_lines` -- which groups on
    the baseline, and must -- hands back a line holding nothing but the digit.
    Left standing it does three kinds of damage downstream, all of them on
    SOU 2025:115 p. 84 (body 17, references 10): the reflow reads its size as the
    paragraph's, so `classify` files running text as `fotnot`; its `left` sits far
    right of the margin, so `indent_breaks` starts a paragraph at it and cuts a
    sentence in half; and because the raised baseline sorts *above* the line it
    belongs to, the digit comes out ahead of the text it follows -- "4 I strategin
    framhålls genomförandet av direktiv (EU) 2022/2555", then a break, then
    "5 (NIS 2-direktivet)".

    Dropped rather than merged back in, because a marker reads as a digit either
    way and glues itself to whatever it followed: merging by horizontal position
    turns "direktiv (EU) 2022/2555" into "direktiv (EU) 2022/25554" -- a citation
    the FORARBETEN grammar can no longer resolve. Nothing downstream can recover a
    reference from a bare digit in running text, so the marker is chrome, and this
    is the same judgement `drop_footnotes` already makes about the markers it
    finds *inside* a line.

    Only the markers that point *into* body text go. A footnote's own leading
    number is raised off its note the same way and is a line of its own for the
    same reason -- but there it is the note's label, and the note is not running
    text, so it stays and reflows into the note as before. The line a marker
    shares its `top` with is what tells the two apart. Sizeless sources
    (OCR/legacy) are returned untouched -- without sizes a bare number is as
    likely to be a list ordinal as a reference."""
    if not body:
        return lines
    small = [i for i, l in enumerate(lines)
             if l.size and l.size <= body - FOOTNOTE_DROP
             and _RE_MARKER.fullmatch(l.text)]
    drop = {i for i in small
            if i + 1 < len(lines) and abs(lines[i + 1].top - lines[i].top) <= LINE_TOL
            and lines[i + 1].size > body - FOOTNOTE_DROP}
    return [l for i, l in enumerate(lines) if i not in drop]


def drop_footnotes(pages):
    """`[(pageno, [Line])]` -> the same with footnote text and the superscript
    markers referencing it removed. A line whose own size sits `FOOTNOTE_DROP`
    below the page's body size is the note; a small bare-number run inside a
    body line is its marker. Pages carrying no font information (the OCR and
    legacy routes) are returned untouched -- without sizes there is no signal,
    and guessing from the digits alone would delete real numbers."""
    out = []
    for pageno, lines in pages:
        body = line_body_size(lines)
        if not body:
            out.append((pageno, lines))
            continue
        kept = []
        for line in lines:
            if line.size and line.size <= body - FOOTNOTE_DROP:
                continue                      # the note's own text
            if line.runs:
                runs = _marker_runs(line.runs, body)
                if len(runs) != len(line.runs):
                    text, spans = _join_runs(runs)
                    line = replace(line, text=text, spans=spans, runs=runs)
            kept.append(line)
        out.append((pageno, kept))
    return out


# --- the letter's addressing apparatus ---------------------------------------
# `strip_page_furniture` finds what *repeats*. A letter's masthead does not: the
# recipient department, the reference line ("Ert dnr Ju2021/00658") and the
# sender's contact block are printed once, on page one, and a third of these
# answers are too short for repetition to be evidence of anything at all.
# Measured over 400 reparsed answers, that left 48% still carrying a contact
# block and 44% a Datum/Dnr line.
#
# What the apparatus does have is composition: it is dense in tokens prose does
# not contain -- an address, a phone number, a postal code, an org number -- and
# it is not built of sentences. Both halves are needed. The tokens alone would
# eat "Utredningen (dnr Ju2021/00658) föreslår att ..."; the shape alone would
# eat a heading.
_ADDRESS_TOKEN = re.compile(r"""(?:
      \S+@\S+\.\w{2,}                            # e-postadress
    | (?:https?://|www\.)\S+                     # webbplats
    | (?:\+46|\b0)\d[\d\s()-]{6,}\d              # telefonnummer (svenskt: 0 / +46)
    | \b\d{3}\s?\d{2}\s+[A-ZÅÄÖ][a-zåäöA-ZÅÄÖ]+  # postnummer + ort
    | \bOrg\.?\s?nr\.?:?\s*[\d\s-]+              # organisationsnummer
    | \bOrganisationsnummer:?\s*[\d\s-]+
)""", re.X)
_APPARATUS_LABEL = re.compile(
    r"\b(?:Dnr|Diarienummer|Ärendenummer|Datum|Sida|Handläggare|Beteckning|"
    r"Postadress|Besöksadress|Gatuadress|Telefon|Telefax|E-post|Epost|Webbplats|"
    r"Hemsida|Box|Bankgiro|Plusgiro)\b", re.I)
# how much of a paragraph may be address tokens before it is apparatus rather
# than prose that happens to contain one
APPARATUS_SHARE = 0.25


def _is_prose(text):
    """Whether a paragraph reads as running text rather than as apparatus: enough
    words to be a sentence and a terminator, or long enough that it must be prose
    however it ends (PDF extraction loses plenty of full stops)."""
    words = text.split()
    return len(words) >= 12 or (len(words) >= 8 and text.rstrip()[-1:] in ".!?:")


def _is_apparatus(para, cleaned):
    """Whether a paragraph is the letter's addressing apparatus rather than what
    the organisation had to say. Judged on what removing the address tokens costs
    it: a contact block is mostly tokens and collapses, while a sentence that
    merely cites a diarienummer barely changes. A short line stacking two or more
    reference labels ("VÄSTMANLANDS TINGSRÄTT Datum Diarienummer") is the printed
    reference header, which carries no tokens at all."""
    if not para:
        return False
    if 1 - len(cleaned) / len(para) >= APPARATUS_SHARE:
        return True
    labels, words = len(_APPARATUS_LABEL.findall(para)), len(cleaned.split())
    if labels >= 2 and words < 14:
        return True
    # "Lantmäterimyndigheten 1 (1)", "Remissvar 1(2)" -- the printed page marker
    # with whatever the masthead sets beside it. On a one-page letter nothing
    # repeats, so `strip_page_furniture` cannot see it. Bounded by length: prose
    # cites "artikel 8 (3) i Infosoc-direktivet" and must survive.
    if RE_PAGEMARK.search(para) and not _is_prose(cleaned) and words < 10:
        return True
    # a single label is enough on a line too short to be a sentence: "Ert dnr
    # Ju2021/00658", "Yttrande Diarienummer 31 januari 2024". Prose that cites a
    # diarienummer runs far longer than this, so the length bound is what keeps
    # the two apart.
    return bool(labels and words < 10 and not _is_prose(cleaned))


def strip_addressing(paras):
    """Paragraph texts with the letter's addressing apparatus -- the masthead,
    the reference line, the contact footer -- dropped whole.

    A paragraph that survives is emitted **unchanged**. Removing the address
    tokens is how apparatus is *recognised* (a contact block collapses, a
    sentence citing a diarienummer barely changes), not what is stored: a
    remissvar that cites its source by URL ("Se vidare vägledningen på
    www.imy.se/...") would otherwise have that URL silently deleted from the
    artifact, and the artifact is the source of truth for the text
    (rule:artifact-is-truth). The cost is that a recipient's e-mail spliced into
    a sentence stays in the prose, which is noise a reader can skip -- unlike a
    missing citation, which they cannot recover."""
    return [para for para in paras
            if not _is_apparatus(para, normalize_space(_ADDRESS_TOKEN.sub(" ", para)))
            and re.search(r"[^\W\d_]", para)]


# a page break falls mid-sentence far more often than at one: `page_paragraphs`
# reflows a page at a time, so the tail of page 3 and the head of page 4 come
# back as two paragraphs even when they are one sentence. Stripping the
# furniture between them is only half the repair -- without the join, a verbatim
# quote spanning the break is still not a substring of any single paragraph.
_SENTENCE_END = re.compile(r"[.!?:;»”\"')\]]\s*$")
_RE_COORDINATION = re.compile(r"(?:och|eller|samt)\b")


def paragraph_texts(pages, drop=None):
    """`[(pageno, [Line])]` -> the reflowed paragraph text of each page, as the
    per-page lists `join_across_pages` consumes.

    Both courts' page-range parsers wanted the same five lines, so they live
    here (rule:second-use-goes-to-lib). `remisser/parse.py` reads pages the same
    way but keeps its own copy on purpose: it takes `Para.text` unnormalised,
    and routing it through here would change what a referral answer parses to.

    `drop` is an optional predicate over the normalized text, applied *before*
    the join. That order is load-bearing for both courts: their running header
    is the first paragraph of every page, so leaving it in place puts a filing
    stamp between the two halves of every sentence a page break split and
    nothing ever rejoins."""
    return [[text for text in (normalize_space(para.text)
                               for para in page_paragraphs(lines, None, pageno))
             if text and not (drop and drop(text))]
            for pageno, lines in pages]


def join_across_pages(per_page):
    """`[[str]]` (a page's paragraph texts, in order) -> one flat [str] with
    paragraphs that a page break split rejoined. A page's last paragraph and the
    next page's first are one paragraph when the former does not end a sentence
    and the latter does not begin one (no leading capital, digit or bullet) --
    the conservative reading, since wrongly joining two real paragraphs merely
    merges them while wrongly splitting one breaks every quote across it."""
    out = []
    for paras in per_page:
        for i, para in enumerate(paras):
            # A paragraph ending in a hyphen is never a real paragraph end: the
            # hyphen is a line break the reflow failed to close. That happens
            # wherever `page_paragraphs` could not group lines at all -- a
            # scanned answer's line spacing is irregular enough that every line
            # becomes its own paragraph, so `dehyphenate` (which fires only when
            # joining lines *into* a paragraph) never runs, and the text carries
            # "säkerhets- skydd" through to the rail.
            hyphenated = out and out[-1].rstrip().endswith("-")
            if (out and para[:1].islower()
                    and (hyphenated
                         or (i == 0 and not _SENTENCE_END.search(out[-1])))):
                # A *hanging* hyphen is correct Swedish and must survive the
                # join: "studie- och yrkesvägledare", "fri- och rättigheter".
                # The coordinating conjunction after it is what tells the two
                # apart -- closing one up would produce "studieoch".
                out[-1] = ("%s %s" % (out[-1].rstrip(), para)
                           if hyphenated and _RE_COORDINATION.match(para)
                           else dehyphenate(out[-1].rstrip(), para))
            else:
                out.append(para)
    return out


def page_paragraphs(lines, identifier, pageno, force_break_tops=frozenset(),
                    indent_breaks=False):
    """Reflow a page's lines into paragraphs, dropping the running header (the
    identifier, when one is known -- pass ``None``/``""`` where the source has no
    fixed header to strip, e.g. a letter whose sender's name is prose, not a
    repeated masthead; the substitution is skipped outright rather than built as
    an always-matching pattern, since a header is stripped only where it recurs
    as a header, never as an incidental substring inside body text), the
    page-number line and TOC dotted-leader lines. A bold line (heading or a
    §/chapter marker) always begins its own paragraph; otherwise a vertical gap
    larger than the body line-height does. A page dominated by dotted leaders is
    the table of contents -- skipped whole.

    ``indent_breaks`` additionally starts a paragraph at an indented first line.
    Off by default because it is a claim about a document's typography, not
    about PDFs: a förarbete sets its body flush at one margin and indents only
    to start a paragraph, so there the indent is the *only* signal where the
    leading does not change. Measured over the other PDF-bodied sources it moves
    paragraph counts by +8% (coe) to +50% (avg) -- much of it right (a masthead
    or a running footer stops being glued to the prose) and some of it wrong (a
    line of a quoted block, breaking a sentence). Neither is validated for them,
    so they keep the segmentation they had."""
    if sum(RE_DOTS.search(l.text) is not None for l in lines) >= 5:
        return []
    # first the header no single line holds the whole of (`_strip_split_header`),
    # then, per line, the one a line does
    lines = _strip_split_header(lines, identifier)
    header_re = (re.compile(r"\s*".join(re.escape(t) for t in identifier.split()))
                 if identifier else None)
    kept, first_headed = [], False
    for l in lines:
        raw, spans, stripped = l.text, l.spans, False
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
                # the line is rebuilt from the runs that survive, so its style
                # spans are re-derived against the stripped text rather than
                # left pointing at offsets the strip has moved -- but only when
                # the strip actually dropped a run. `header_re.search` hits on
                # prose that merely names the identifier too, and rebuilding
                # there discards anything a caller put in `text` that is not in
                # `runs`: dv prepends a numbered domskäl paragraph's ordinal
                # ("5. ") to the text, and every such paragraph whose first line
                # named the court silently lost its number.
                body_runs = _strip_header_runs(l.runs, header_re)
                if len(body_runs) != len(l.runs):
                    stripped = True
                    # Rebuilt through the one constructor, so that text, spans,
                    # geometry *and* the whole-line style flags all describe the
                    # runs that survive -- patching the text alone is the exact
                    # failure `line_from_runs` documents: `bold`/`lead_bold`/
                    # `size` would keep describing a run set that is gone, and
                    # both `heading()` below and dv's `Rubrik` test read them.
                    # The geometry matters twice over here: a line that kept the
                    # dropped runs reported the *header's* extent to the margin
                    # and measure (SOU 2025:115 sets its head across the full
                    # width, 638 against a body ending at 555 -- wide enough to
                    # call the page's inset lines a box), and its `size` stayed
                    # the largest run's, which is the identifier's wherever the
                    # head is set larger than the title beside it.
                    if body_runs:
                        l = line_from_runs(body_runs, l.top)
                        raw, spans = l.text, l.spans
                    else:
                        raw, spans = "", []     # the line was only the header
            else:
                # no run geometry (OCR/legacy routes): strip only a line that
                # is nothing but the header and a page number/date
                residue = header_re.sub(" ", raw)
                if not re.search(r"[A-Za-zÅÄÖåäö]", residue):
                    raw = residue
        text, spans = _normalized_with_spans(raw, spans)
        if text and not is_page_number(text, pageno) and not RE_DOTS.search(text):
            first_headed = first_headed or (stripped and not kept)
            kept.append(replace(l, text=text, spans=spans))
    page_body = line_body_size(kept)
    # The *other* half of the running head. Stripping the identifier off the top
    # line leaves whatever was set beside it -- in a förarbete the chapter title,
    # "SOU 2025:115 Sanktioner" -> "Sanktioner" -- and that residue is a real line
    # of real text, so it survived as a paragraph on nearly every page: 598 of
    # SOU 2025:115's, each classified `fotnot` for being set smaller than the
    # body. It says nothing the body does not (the heading it names is printed in
    # the text below it), so the whole line goes.
    #
    # Two things keep this off body text, and neither is the residue's size. The
    # identifier has to have stood as *its own run(s)* -- `_strip_header_runs`
    # leaves a match inside a longer run alone, which is what prose naming the
    # identifier looks like ("… (SOU 2008:97). I betänkandet …"), so this is
    # only ever true of a line that set it apart. And the line has to be the page's
    # first: a running head is, and the footnotes that cite the document's own
    # number are at the foot.
    #
    # Third, the residue has to be set in the head's own style rather than the
    # body's, which is what separates the *other half of the head* from a first
    # line of prose the margin identifier merely shares a baseline with -- there
    # the residue is the body text itself and deleting it costs a real sentence.
    # Differ*ing* from the body, not falling below it: a förarbete sets its
    # annexes smaller than its body but its running head at one size throughout,
    # so on SOU 2025:115's bilaga 2 the head is larger than the text under it, and
    # a `smaller than body` test merely moved the damage there -- from 138 pages
    # of "Bilaga 2" as a footnote to 138 of it as a heading, in the table of
    # contents where it is harder to miss.
    if first_headed and kept[0].size and page_body and kept[0].size != page_body:
        kept = kept[1:]
    # before any measurement: a raised reference is a line of its own, and every
    # statistic below (line-height, margin, measure) would otherwise be taken
    # over a population that includes single digits floating in the right margin
    kept = drop_marker_lines(kept, page_body)
    gaps = sorted(b.top - a.top
                  for a, b in zip(kept, kept[1:], strict=False) if b.top > a.top)
    body_gap = gaps[len(gaps) // 2] if gaps else 0      # median line-height
    body_size = line_body_size(kept)

    def heading(l):
        # heading-ness by font: bold, or larger than the page's body size --
        # a prop's numbered chapter headings are large but NOT bold
        return (l.bold or is_italic_subheading(l.text, l.italic)
                or (l.size and body_size and l.size > body_size))

    # Swedish government typography marks a new paragraph by indenting its first
    # line, not by leaving extra space above it: in a proposition the body sets
    # flush at one margin and a paragraph opens ~13 units to the right of it,
    # with the ordinary line-height between. A gap rule alone therefore ran
    # whole sections together -- every paragraph after the first, on nearly
    # every page. The margin is read off the page's own line starts, so a
    # document that indents differently (or not at all) needs no configuring.
    #
    # The *leftmost* start a real share of the page's lines agree on, rather than
    # the commonest one. Nothing sets further left than the body margin, but a
    # page can easily have more inset lines than flush ones -- a page given over
    # to a ruled box does (SOU 2025:115 p. 307: 12 lines start inside the box at
    # 96, 11 at the body's 85), and the commonest start is then the box's own,
    # which leaves the box not inset from anything and the body outdented past
    # it. The share is what keeps a one-off outdent from winning instead: these
    # documents hang the running head into the outer margin, at a left no other
    # line on the page shares.
    starts_at = Counter(l.runs[0].left for l in kept if l.runs)
    floor = MARGIN_SHARE * sum(starts_at.values())
    margin = min((x for x, n in starts_at.items() if n >= floor), default=None)

    # A förarbete sets its proposal and assessment statements in a ruled box --
    # "Regeringens förslag:" in a proposition, "Förslag:"/"Bedömning:" in a SOU
    # -- and the rule itself is a vector drawing pdftohtml discards. The box is
    # legible from the text anyway: it is set to a *narrower measure*, inset at
    # both margins, where an ordinary paragraph's indent moves its first line
    # only and leaves the right edge alone. Both edges are read per page, since
    # a förarbete mirrors its margins on facing pages (prop. 2017/18:89 sets
    # 77-523 on page 49 and 183-629 on page 50).
    #
    # The measure is read off the lines that start *at* the margin -- the body's
    # own -- and not off the page as a whole. A box is inset at the left by
    # definition, so its lines are exactly the ones that must not vote on where
    # the body ends: on a page given over to a large ruta they are the majority,
    # and the page-wide mode then returned the *box's* right edge. Every box line
    # failed `box_right` against it, `boxed` came back false, and -- because a
    # box's lines are all inset from the body margin -- `indented` fired on each
    # of them in turn, so the box came out as one paragraph per line instead of
    # one ruta (SOU 2025:115 p. 307: body 85-555, box 96-543, page-wide mode 539).
    # The *furthest* of them, not the commonest: a line at the margin ends where
    # its text ran out, and only a full one reaches the measure. Ragged-right
    # setting leaves no two rights alike (SOU 2025:115 p. 85 ends its 24 body
    # lines at 24 different x), so a mode picks whichever the counter happened to
    # see first -- there it was a paragraph's last line, and the box on that page
    # broke exactly as it did before any of this. Nothing at the margin overruns
    # the measure, so the maximum is the measure.
    ends_at = [l.runs[-1].right for l in kept
               if l.runs and l.runs[0].left == margin]
    measure_end = max(ends_at) if ends_at else None

    def box_left(l):
        return (margin is not None and l.runs
                and margin + BOX_INSET <= l.runs[0].left)

    def box_right(l):
        return (measure_end is not None and l.runs
                and l.runs[-1].right <= measure_end - BOX_INSET)

    def boxed(l):
        return box_left(l) and box_right(l)

    def ruled(l):
        # A *ruled* box, as against anything else set to a narrower measure. The
        # box carries the document's proposal and assessment statements -- running
        # text, set like running text -- while the other inset blocks are set
        # smaller: a block quotation from a directive, a table's cells and its
        # source note, a chart's axis labels. Geometry alone cannot tell them
        # apart (SOU 2025:115 p. 307 insets its förslag box by 11 and the recital
        # it quotes by 21, both at a narrower measure), and without the size test
        # a budget proposition's indicator tables came out as 125 spurious rutor.
        #
        # Only the *label* is gated. The narrower measure is real either way, so a
        # quotation still reflows against its own margin rather than the page's --
        # it is simply not a box.
        return not l.size or not body_size or l.size >= body_size

    def aligned(run):
        # ...whose lines actually share that margin. "Set to its own margin" is a
        # claim worth checking: a box holds every line to one left edge, give or
        # take a paragraph indent. A bar chart's labels are inset and consecutive
        # and pass every other test, but each fragment starts wherever poppler
        # put it -- SOU 2024:50 p. 325 runs 115, 127, 115, 167, 182 down a single
        # "box" -- so no edge carries the run. Strictly more than half, so the
        # test still says something about a run of two: a centred heading and the
        # centred title under it are inset at both margins and consecutive, and
        # each starts at its own left ("Artikel 24" at 359 over its title at 231,
        # in the EU regulation SOU 2025:115 reproduces as bilaga 2).
        lefts = Counter(l.runs[0].left for l in run)
        return lefts.most_common(1)[0][1] * 2 > len(run)

    def measured(run):
        # ...and set to a measure of its own worth the name. A ruta is a *little*
        # narrower than the body -- it is the same running text, indented off both
        # margins by a few units -- so it fills nearly the whole measure: 447 of
        # the body's 470 on SOU 2025:115 p. 307, 448 of 471 on p. 85. A column of
        # a bar chart's axis labels is inset at both margins too, and consecutive,
        # and so passes every test above, but it is 36 units wide against a body
        # measure of 472. SOU 2024:50's charts alone made 1,211 boxes that way,
        # once its annexes stopped being (just as wrongly) read as footnotes.
        # a run exists only where `box_left` and `box_right` both passed, and
        # neither can without the page's margin and measure (rule:fail-fast)
        assert margin is not None and measure_end is not None, (
            "a boxed line on a page with no margin or measure")
        return (max(l.runs[-1].right for l in run)
                - min(l.runs[0].left for l in run)
                >= BOX_MIN_MEASURE * (measure_end - margin))

    # A ruta is set to its own margin, so its paragraphs indent from *that*:
    # measuring their first lines against the page's would make every line of
    # the box a paragraph of its own, since all of them are inset from it.
    #
    # Per *box*, not per page: one page can inset two things by different
    # amounts, and a single page-wide inset then breaks the deeper one exactly
    # the way the page margin breaks a box. SOU 2025:115 p. 307 holds a förslag
    # box at 96 and a block quotation at 106; taking the page's commonest inset
    # gives 96, and the quotation -- 10 further in, past INDENT_MIN -- came out
    # as one paragraph per line. A box's lines are consecutive by construction,
    # so each run of them carries its own margin.
    #
    # The run is also what *is* the box: a lone inset short line is an ordinary
    # paragraph's first line, a list item's runover or a lead-in ("Strategins
    # huvudmål är:"), and nothing about it distinguishes a box until a second
    # line holds the same narrower measure. Membership here is therefore the
    # whole boxed test below -- with a run of one, a page's short lines each
    # became a one-line ruta *and* took two paragraph breaks (entering and
    # leaving) with them, at +30% paragraphs over SOU 2025:115.
    box_base, start = {}, None
    for i, l in enumerate(kept + [None]):
        if l is not None and boxed(l):
            start = i if start is None else start
            continue
        if start is not None:
            run = kept[start:i]
            if (len(run) >= 2 and aligned(run)      # one inset line is not a box
                    and measured(run)):
                box_base |= dict.fromkeys(
                    range(start, i),
                    Counter(x.runs[0].left for x in run).most_common(1)[0][0])
            start = None

    def indented(i, l):
        # A first line inset from the measure *its own block* is set to. Crossing
        # in or out of a box is a break in itself, whatever the two insets are:
        # the box is a block, and without this its opening line would run on from
        # the sentence that introduces it ("... anges bland annat följande:") and
        # its closing line into the prose that resumes after it.
        if not indent_breaks:
            return False
        base = box_base.get(i, margin)
        if i and base != box_base.get(i - 1, margin):
            return True
        return (base is not None and l.runs
                and base + INDENT_MIN <= l.runs[0].left)

    paras, cur, prev = [], None, None
    for i, l in enumerate(kept):
        marker = l.lead_bold and (RE_KAP_MARK.match(l.text)
                                  or RE_PARA_MARK.match(l.text))
        # a caller-forced break (DV's bitmap paragraph numbers, whose paragraphs
        # carry no extra vertical gap the heuristic below could see)
        starts = (cur is None or heading(l) or marker or l.top in force_break_tops
                  or (prev and heading(prev)) or indented(i, l)
                  or (body_gap and prev and l.top - prev.top > PARA_GAP * body_gap))
        if starts and _heading_wrap(prev, l, marker, heading):
            starts = False                # wrapped heading line: same paragraph
        if starts and cur is not None:
            paras.append(cur)
            cur = None
        if cur is None:
            cur = Para(l.text, l.bold, bool(marker), l.italic, l.size, l.top,
                       list(l.spans), i in box_base and ruled(l),
                       l.runs[0].font if l.runs else "")
        else:
            # the line's spans slide by wherever its text landed in the
            # paragraph -- one past the end for the joining space, one *before*
            # it where a soft hyphen was closed up ("för-" + "fogar")
            joined = dehyphenate(cur.text, l.text)
            offset = len(joined) - len(l.text)
            cur.spans += [(a + offset, b + offset, st) for a, b, st in l.spans]
            cur.text = joined
            cur.italic = cur.italic and l.italic
            # a ruta justifies *every* line to its own narrower measure, so a
            # paragraph is in one only while every line of it is
            cur.boxed = cur.boxed and i in box_base and ruled(l)
        prev = l
    if cur is not None:
        paras.append(cur)
    return paras


def line_body_support(lines):
    """`(dominant font size, how many lines are set in it)` for a line
    sequence, `(0, 0)` when the source carries no font info. Computed over
    *lines* -- a sparse page's paragraphs are too few for a stable mode, its
    lines are not.

    The count is what says whether the mode means anything: a page of running
    text settles on its size over forty lines, a part title "decides" it on
    three.

    A tie goes to the *smaller* size, because a heading is never more common
    than the body it heads. `Counter.most_common` broke ties by insertion order,
    which is the order poppler happened to emit the sizes in, and both outcomes
    of reading the larger one as body are damaging: it widens the footnote test
    until real body text is apparatus, and it hides every heading, since nothing
    is larger than the body any more. Prop. 2025/26:24 p. 10 sets two lines of a
    wrapped heading at 19 and two of body at 15 -- read as body 19, the heading
    stopped being a heading, and the enacting sentence under it reflowed into it
    ("om allmän löneavgift Härigenom föreskrivs att 6 § lagen (1994:1920) …" as a
    level-2 rubrik)."""
    sizes = Counter(l.size for l in lines if l.size)
    if not sizes:
        return 0, 0
    size = min(sizes, key=lambda s: (-sizes[s], s))
    return size, sizes[size]


def line_body_size(lines):
    """The dominant (body) font size of a line sequence, 0 when the source
    carries no font info."""
    return line_body_support(lines)[0]


# The unnumbered subheading a förarbete sets in *italics* at body size --
# "Lagrådet", "Skälen för regeringens förslag", "Remissinstanserna" -- rather
# than in bold. Nothing else about it says heading: it is neither bold nor
# larger than the body, so without this it read as the first line of the
# paragraph beneath it and the two ran together.
#
# The shape is what separates it from an italic phrase inside prose: a whole
# line italic, short, and not a sentence (a quoted or emphasised clause carries
# its terminator). Deliberately narrow -- italics do a lot of other work in a
# förarbete, marking quoted lagtext, betänkande titles and defined terms.
ITALIC_SUBHEAD_MAX = 60


def is_italic_subheading(text, italic):
    return bool(italic and text and len(text) <= ITALIC_SUBHEAD_MAX
                and not text.rstrip().endswith((".", ":", ",", ";")))


# a line opening its own numbered heading ("5.1 Offentligfinansiella …") is
# never the wrapped continuation of the heading above it
RE_NUM_LEAD = re.compile(r"^\d+(?:\.\d+)*\s")


# --------------------------------------------------------------------------
# the letterhead reading, shared by every vertical whose documents are agency
# letters rather than författningar (avg's IMY/KKV decisions, rs's rättsliga
# ställningstaganden). Extracted here on its second vertical
# (rule:second-use-goes-to-lib); it stays source-agnostic by emitting plain
# (kind, text, level) triples that each vertical maps onto its own Block type.
# --------------------------------------------------------------------------

# The running header every one of these letterhead templates sets: a "N (M)"
# page mark (plus the "Page N of M" the Word-authored ones carry). A page mark
# inside a long paragraph is prose, not a header, so the rule is length-bounded.
RE_PAGEMARK = re.compile(r"\d+\s*\(\s*\d+\s*\)|^Page \d+ of \d+$")
PAGEMARK_MAX = 150
MAX_HEADING_LEVEL = 4
# A heading broken across lines never breaks *at* a hyphen in these corpora -- a
# trailing hyphen is always part of the term ("VIS-förordningen", "Trygg-Hansa"),
# so `dehyphenate`'s soft-hyphen rule would corrupt it. The one other shape is
# the suspended hyphen of a coordinated list ("VIS-, SIS- samt
# dataskyddsförordningen"), which Swedish writes with a space after it and which
# this pattern recognises by the comma the earlier member left behind.
RE_SUSPENDED_HYPHEN = re.compile(r"-\s*,.*-$")


def _modal_size(paras):
    """The running-text font size: the commonest size among the non-bold
    paragraphs. It is the yardstick everything else in a letterhead document is
    read against -- smaller is a footnote or the masthead, bold-and-larger is a
    heading -- and it has to be measured per document, because the agencies'
    templates set the body at different sizes (14 and 17 in pdftohtml's units
    for IMY and Datainspektionen respectively)."""
    sizes = Counter(p.size for p in paras
                    if not p.bold and p.size and p.text.strip())
    return sizes.most_common(1)[0][0] if sizes else 0


def heading_levels(paras, body, by_size=False):
    """The font sizes that mark headings, largest first -- their *rank* is the
    heading level, which is how a template's own nesting survives without anyone
    having to name its point sizes.

    Most letterhead templates set a heading bold and at or above the running
    size. A few mark it by *size alone*, leaving the weight regular
    (Finansinspektionens ställningstaganden: body 18, headings 24, title 30, not
    a bold run in the document); for those, ``by_size`` reads a size strictly
    larger than the body as the heading signal instead. It has to be asked for
    rather than always allowed, because in a bold-marking template a paragraph
    that merely runs large is not a heading."""
    if by_size:
        return sorted({p.size for p in paras if p.size > body}, reverse=True)
    return sorted({p.size for p in paras if p.bold and p.size >= body},
                  reverse=True)


def _join_heading(head, tail):
    """Rejoin the next line of a heading broken across lines. A trailing hyphen
    is kept -- see :data:`RE_SUSPENDED_HYPHEN` -- and closes up against the line
    that continues the term, except where it is the suspended hyphen of a
    coordinated list, which takes the space Swedish writes after it."""
    if head.endswith("-"):
        return head + (" " if RE_SUSPENDED_HYPHEN.search(head) else "") + tail
    return head + " " + tail


def classify_letterhead(paras, margin, masthead, by_size=False):
    """:class:`Para`s -> ``[(kind, text, level)]``, ``kind`` being ``"rubrik"``
    or ``"stycke"``, read by font rather than position -- the reading an agency
    *letter* wants, since it is set as a narrow margin column beside a wide body
    and marks its structure no other way. Pure over the Para stream so the rules
    are testable without poppler.

    A paragraph set smaller than the running text is a footnote or the masthead
    and goes; one carrying a "N (M)" page mark is the running header and goes;
    the `margin` pattern names the margin column's own labels and bare values,
    and `masthead` the footer tokens that have to be removed *in place* because
    the column glues them onto body lines. A bold paragraph is a heading whose
    level is the rank of its size (or, under ``by_size``, any paragraph set
    larger than the body -- see :func:`heading_levels`), and consecutive headings
    of one level are one heading, which is how a title set across three lines
    arrives."""
    body = _modal_size(paras)
    levels = heading_levels(paras, body, by_size)
    blocks = []
    for p in paras:
        text = normalize_space(p.text)
        if not text or (body and p.size and p.size < body):
            continue
        if RE_PAGEMARK.search(text) and len(text) < PAGEMARK_MAX:
            continue
        text = normalize_space(masthead.sub(" ", text))
        if not text or margin.search(text):
            continue
        if not ((p.bold or by_size) and p.size in levels):
            blocks.append(("stycke", text, 0))
            continue
        level = min(levels.index(p.size) + 1, MAX_HEADING_LEVEL)
        if blocks and blocks[-1][0] == "rubrik" and blocks[-1][2] == level:
            blocks[-1] = ("rubrik", _join_heading(blocks[-1][1], text), level)
        else:
            blocks.append(("rubrik", text, level))
    return blocks


# a footnote opens with its own marker digit, which pdftohtml renders as an
# ordinary leading number ("16 Se yttrande 15/2011 …"). The marker is the
# footnote's identity, so it is split off rather than left in the prose.
RE_FOOTNOTE_MARK = re.compile(r"^(\d{1,3})[.)]?\s+(?=\S)")
# what a note must have left once its marker is split off: a letter, and more
# than a token of it. The test runs *after* the split, because the shortest
# notes are the ones that are nothing but a citation ("1 WP 248.", "12 Se
# ovan.") -- exactly the data this reader exists to recover -- and a floor
# applied to the whole string would drop them as furniture. A bare page number
# leaves nothing behind and goes on the emptiness test alone.
# ... and it has to read as prose rather than as a stray heading or a party
# name the template happened to set small: more than one word, or a one-word
# abbreviation closed with a period ("Ibid."). Without this the lowered floor
# admitted a bare "Antagna" (the footer with its page number in another para),
# rs section headings ("Bakgrund", "Praxis") and avg party names ("KLAGANDEN").
RE_FOOTNOTE_PROSE = re.compile(r"[^\W\d_].*(?:\s|\.$)")
FOOTNOTE_MIN = 4


def letterhead_footnotes(paras, margin, masthead):
    """The footnotes :func:`classify_letterhead` leaves behind, as
    ``[(mark, text)]`` in document order.

    A letterhead template sets its notes *below* the running size, which is the
    one signal that separates them from the body -- and is why the block
    classifier drops them: a note is not a paragraph of the document and must
    not read as one. But dropping them loses text that carries citations, and in
    one corpus carries the *identifying* ones: IMY grounds a guideline it has
    named in prose ("Europeiska dataskyddsstyrelsens riktlinjer om samtycke")
    with its number in the footnote below ("Riktlinjer 05/2020"), so a decision
    whose footnotes are discarded cites nothing a citation scan can resolve.

    So this reads the same Para stream a second time and returns what the
    classifier discarded, with the page furniture that shares the small size --
    the masthead, the running page mark, the margin column's own values, and
    anything too short to be prose -- taken out. A note's leading marker digit is
    split from its text; a note that carries none keeps ``""``.

    Additive on purpose: the block stream every caller already consumes is
    unchanged, so a vertical opts into footnotes by calling this as well."""
    body = _modal_size(paras)
    notes = []
    for p in paras:
        if not (body and p.size and p.size < body):
            continue
        text = normalize_space(masthead.sub(" ", normalize_space(p.text)))
        if not text or margin.search(text) or (RE_PAGEMARK.search(text)
                                               and len(text) < PAGEMARK_MAX):
            continue
        match = RE_FOOTNOTE_MARK.match(text)
        mark, body_text = ((match.group(1), text[match.end():]) if match
                           else ("", text))
        if len(body_text) < FOOTNOTE_MIN or not RE_FOOTNOTE_PROSE.search(body_text):
            continue
        notes.append((mark, body_text))
    return notes


def _heading_wrap(prev, l, marker, heading):
    """Whether line `l` continues a wrapped multi-line heading: the previous
    line and this one are both heading-fonted in the *same* size (a heading and
    its subsection differ in size, so they never fold) *and the same weight*,
    sit a heading's own leading apart (HEAD_GAP x the size -- known only when
    font info is), and this line neither opens a numbered heading of its own
    nor is a §/kap marker.

    Weight has to agree because size alone is not reliable evidence of a
    heading: a page whose opening paragraph is set a point or two above its
    dominant body size reads as heading-fonted, and a JO decision -- bold
    "Anmälan" on its own line, body following -- then folded the first
    paragraph into the heading, giving a 40-word rubrik and a table of
    contents of three paragraphs (D8). A heading that wraps keeps its weight
    across the break; a heading followed by body text does not."""
    return bool(prev is not None and heading(prev) and heading(l) and not marker
                and l.size and l.size == prev.size and l.bold == prev.bold
                and 0 < l.top - prev.top <= HEAD_GAP * l.size
                and not RE_NUM_LEAD.match(l.text))
