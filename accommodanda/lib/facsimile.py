"""On-demand facsimile rendering: one source-PDF page -> a cached PNG.

The reading view is the parsed artifact; the facsimile is the proof -- what
the printed page actually looks like, one click from the page anchor. Pages
are rendered lazily on first request with poppler's ``pdftoppm`` and cached
under ``layout.facsimile`` (a pure cache: an external process evicts, this
codebase only writes). The whole budget -- button press to pixels -- is under
a second, so the defaults are chosen for speed:

  * **150 DPI**: an A4 page becomes ~1240x1750 px -- 2x a ~620 px reading
    column, i.e. retina-sharp, while a born-digital page renders in ~0.5 s
    and compresses to ~350 KB (200 DPI costs ~0.8 s and ~500 KB for sharpness
    nobody can see at reading size).
  * plain PNG (pdftoppm's zlib): a post-pass optimizer would shave ~15% at
    2-3 s per page -- the wrong trade for an interactive endpoint.

Works identically for born-digital and scanned PDFs: pdftoppm rasterizes the
page as drawn (a scan's page image included), so the caller never needs to
know which kind it has.

The API endpoints that call this are synchronous, so FastAPI runs them in a
thread pool: several threads of *one* process render at the same time. Every
poppler call therefore carries a timeout, the temp file each render writes is
unique per call, and `cached` holds a per-cache-key lock so two readers asking
for the same page pay for one render. A poppler call that hits the timeout
raises subprocess.TimeoutExpired, which no caller handles: a wedged renderer is
a broken host, and a 500 is the honest answer to it.
"""

import math
import os
import re
import subprocess
import tempfile
from pathlib import Path

from . import layout, util

DPI = 150
# What a crop is rendered at for the page it is printed on. Twice the page DPI:
# a road sign is ~50 pt wide and sits inline at about 56 px, which 150 DPI
# already covers, but the crops are also the source for a retina display and for
# the lightbox's first paint. The cost is real and bounded. 2007:90 is the
# heaviest page in the corpus by image weight -- it prints 325 signs, of the 326
# it lists (Y2 is a sound signal the PDF draws nothing for) -- and those weigh
# 1.0 MB at 150 DPI against 2.1 MB here. Every thumbnail is `loading="lazy"`, so
# a reader pays for what they scroll past. Anything sharper belongs behind a
# click, not on the page.
CROP_DPI = 2 * DPI
# What the lightbox asks for (the sfs-graphic endpoint's `stor=1`). The page
# weight above is why this is a second render and not the inline one: it is one
# request for the graphic the reader actually opened, where CROP_DPI is paid
# hundreds of times over. Rendering rather than upscaling is what keeps it
# sharp -- these are vector drawings in the PDF, so there is always more detail
# to be had, and a browser stretching the thumbnail would only blur it.
CROP_DPI_LARGE = 4 * DPI

# No poppler call may hold a request thread for ever. The ceiling is deliberately
# generous: a born-digital A4 page renders in ~0.5 s and a scanned page in a few
# seconds, but the corpus holds SOU volumes of many hundred scanned pages where
# pdfinfo alone takes a while, and a page of dense vector maps is slow to
# rasterize. Five minutes is far past any of those, so only a wedged process
# reaches it.
TIMEOUT = 300

# How far past the page edge a crop may still reach. Both producers of a stored
# bbox bound it to the page and then convert poppler's pixel geometry to points
# (sfs/graphics.py against the page image, lib/pdftext._on_page against the page
# box), so what is left is rounding: half a point at most. One point is below a
# rendered pixel at any DPI, so absorbing it costs no visible area.
EDGE_SLOP = 1.0


class OffPage(ValueError):
    """A crop rectangle that does not lie on the page it names. For a bbox out
    of the query string this is client input and the route answers 400; for one
    read off a stored .graphics layer or artifact it is a corpus fault and must
    fail loudly."""


def _pdfinfo(pdf_path, *args):
    return subprocess.run(["pdfinfo", *args, str(pdf_path)], capture_output=True,
                          check=True, text=True, timeout=TIMEOUT).stdout


def page_count(pdf_path):
    """The number of pages in `pdf_path` (poppler's ``pdfinfo``). Raises
    CalledProcessError on a broken/absent PDF -- the caller knows the context."""
    m = re.search(r"^Pages:\s*(\d+)", _pdfinfo(pdf_path), re.M)
    assert m, "pdfinfo emitted no page count for %s" % pdf_path
    return int(m.group(1))


def page_size(pdf_path, page):
    """(width, height) of 1-based `page` of `pdf_path`, in PDF points. Raises
    CalledProcessError like `page_count`; a page the PDF does not have is
    poppler's exit 99, the same code an out-of-range ``pdftoppm`` range gives,
    so a caller that maps 99 to a 404 needs no extra case for it."""
    out = _pdfinfo(pdf_path, "-f", str(page), "-l", str(page))
    m = re.search(r"^Page\s+%d\s+size:\s*([\d.]+)\s*x\s*([\d.]+)\s*pts" % page,
                  out, re.M)
    assert m, "pdfinfo emitted no size for page %d of %s" % (page, pdf_path)
    return float(m.group(1)), float(m.group(2))


def _pdftoppm(pdf_path, page, out_path, dpi, *crop):
    """Run pdftoppm for one page of `pdf_path` at `dpi` (with `crop` as extra
    ``-x/-y/-W/-H`` arguments, in that same device space) and move the PNG onto
    `out_path`. `mkstemp` reserves the temp name with O_EXCL, so it is unique
    per *call* -- two threads rendering the same page cannot write each
    other's file."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fd, root = tempfile.mkstemp(prefix=out_path.stem + ".tmp",
                                dir=out_path.parent)
    os.close(fd)                      # the reservation, not the render target
    png = Path(root + ".png")         # what pdftoppm -singlefile appends
    try:
        subprocess.run(
            ["pdftoppm", "-png", "-r", str(dpi), "-f", str(page), "-l", str(page),
             *crop, "-singlefile", str(pdf_path), root],
            capture_output=True, check=True, timeout=TIMEOUT)
        png.replace(out_path)
    finally:
        Path(root).unlink(missing_ok=True)
        png.unlink(missing_ok=True)   # only left behind by a failed render
    return out_path


def render_page(pdf_path, page, out_path):
    """Render 1-based `page` of `pdf_path` to `out_path` (a PNG at `DPI`) and
    return `out_path`. Renders to a sibling temp name and replaces, so a
    concurrent reader never sees a half-written file. Raises
    CalledProcessError when pdftoppm cannot render (no such page, broken
    PDF) -- the caller knows the request context and maps it to its own
    error."""
    return _pdftoppm(pdf_path, page, out_path, DPI)


def valid_bbox(bbox):
    """True iff `bbox` is ``[x0, y0, x1, y1]`` of four finite (non-bool) numbers
    with positive, ordered bounds -- ``0 <= x0 < x1`` and ``0 <= y0 < y1``. The
    one shape check shared by the crop renderer, the .graphics validator and the
    sfs-graphic endpoint; each caller chooses `assert` (internal invariant) or a
    `raise`/404 (editor- or model-supplied input)."""
    if not (isinstance(bbox, list) and len(bbox) == 4
            and all(not isinstance(v, bool) and isinstance(v, (int, float))
                    and math.isfinite(v) for v in bbox)):
        return False
    x0, y0, x1, y1 = bbox
    return 0 <= x0 < x1 and 0 <= y0 < y1


def render_region(pdf_path, page, bbox, out_path, dpi):
    """Render just the `bbox` rectangle of 1-based `page` to `out_path` (a PNG
    at `DPI`) and return it. `bbox` is ``[x0, y0, x1, y1]`` in raw PDF points
    with a TOP-LEFT origin -- the representation the .graphics layer stores;
    poppler's ``-x/-y/-W/-H`` crop window is likewise top-left, in device
    pixels, so each point coordinate scales by `dpi`/72. Same atomic
    temp->replace and error contract as `render_page`, plus one check
    `valid_bbox` cannot make: the rectangle must lie on the page, give or take
    `EDGE_SLOP`. A rectangle past that is an `OffPage` -- it would render
    whitespace, which is never what a caller wants and never worth a cache
    entry. (It is not a bound on cache *growth*: the entry is keyed by the
    rounded bbox, so the in-page crops of one A4 page are already more than
    anyone can enumerate. Eviction is the cron job, see docs/operating.)"""
    assert valid_bbox(bbox), "invalid PDF crop bbox %r" % (bbox,)
    x0, y0, x1, y1 = bbox
    width, height = page_size(pdf_path, page)
    if x1 > width + EDGE_SLOP or y1 > height + EDGE_SLOP:
        raise OffPage("crop %r is outside the %g x %g pt page %d of %s"
                      % (bbox, width, height, page, pdf_path))
    return _pdftoppm(
        pdf_path, page, out_path, dpi,
        "-x", str(round(x0 * dpi / 72)), "-y", str(round(y0 * dpi / 72)),
        "-W", str(round((x1 - x0) * dpi / 72)),
        "-H", str(round((y1 - y0) * dpi / 72)))


def png_size(data):
    """(width, height) of a PNG from its IHDR -- the big-endian ints at 16/20.
    Raises ValueError if `data` is not a PNG (a renderer that returned garbage)."""
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("facsimile renderer did not return a PNG")
    return (int.from_bytes(data[16:20], "big"),
            int.from_bytes(data[20:24], "big"))


# One render per cache entry at a time: without it two readers opening the same
# facsimile each pay for the render.
_render_lock = util.KeyedLocks()


def cached(source, basefile, pdf_path, page, bbox=None, *, dpi):
    """The facsimile PNG for one page of a document's source PDF -- or, with
    `bbox`, just that rectangle of the page at `dpi` -- rendered on the first
    request and served from the cache thereafter. `source`/`basefile` identify
    the *source* PDF (for a crop, the amending SFS the region comes from), so
    crops of the same region are shared and a re-verified bbox lands on a fresh
    file.

    `dpi` is a *crop's* resolution. A whole page has exactly one, chosen for the
    reading view (see the module docstring), and its cache path carries no
    resolution to tell two apart -- so asking for a page at anything else is a
    caller error, not a request this quietly downgrades (rule:fail-fast)."""
    assert bbox or dpi == DPI, \
        "a whole page renders at DPI; %r is a crop resolution" % dpi
    out = (layout.facsimile_crop(source, basefile, page, bbox, dpi) if bbox
           else layout.facsimile(source, basefile, page))
    if out.exists():
        return out
    with _render_lock(str(out)):
        if out.exists():                 # rendered while we waited for the lock
            return out
        if bbox:
            render_region(pdf_path, page, bbox, out, dpi)
        else:
            render_page(pdf_path, page, out)
    return out
