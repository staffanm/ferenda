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
import shutil
import subprocess
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

from . import cachesweep, layout, util

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
# What the lightbox shows: the `-stor.png` file `render.write_graphics` stores
# beside the thumbnail (and, for a page generated before the crops became stored
# files, the sfs-graphic endpoint's `stor=1`, which the crop-review UI also
# uses). The page
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


# a bare SFS number ("2007:90", "1949:105 s.1"'s löpnr letter included) -- the
# form a .graphics entry names its provenance act by, and the form the API's
# facsimile resolvers read an SFS document id in. One grammar, one home.
RE_SFS_BASEFILE = re.compile(r"^\d{4}:\d+[a-z]?$")


def graphics_region(entry, where):
    """The `(sfs, page, bbox)` of one `.graphics` layer entry, checked.

    The layer is hand-edited data, and two readers cut a crop from it -- the
    `sfs-graphic` endpoint and `generate`'s own stored-crop writer -- so the
    shape check lives here rather than in either of them: a mistyped bbox that
    the route refuses must not reach poppler from the build instead
    (rule:second-use-goes-to-lib). `where` names the entry in the message.

    An `assert`: both callers read an entry a human verified, so a bad one is a
    corpus fault, not input to answer 4xx for (rule:fail-fast)."""
    src, page, bbox = entry["sfs"], entry["page"], entry.get("bbox")
    assert isinstance(src, str) and RE_SFS_BASEFILE.fullmatch(src), \
        "%s: invalid graphics source %r" % (where, src)
    assert isinstance(page, int) and not isinstance(page, bool) and page > 0, \
        "%s: invalid graphics page %r" % (where, page)
    assert bbox is None or valid_bbox(bbox), \
        "%s: invalid graphics bbox %r" % (where, bbox)
    return src, page, bbox


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
    with render_slot():
        _run_pdftoppm(pdf_path, page, out_path, dpi, crop)
    _evict_if_low()
    return out_path


def _run_pdftoppm(pdf_path, page, out_path, dpi, crop):
    """The poppler call itself, inside a held render slot."""
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

# How many poppler processes this server runs at once. The endpoints are
# synchronous, so FastAPI gives each request a thread and every thread that
# asked for an uncached page used to start its own pdftoppm -- as many as the
# thread pool has threads. Four leaves the rest of production's six cores to
# serve everyone else, and a reader who arrives while four are running waits a
# moment instead of pushing the box into swap.
RENDER_WORKERS = 4
# What a caller waits for a slot before it is told to come back. Short on
# purpose: a reader opening a spread of pages should queue rather than see a
# broken image, and nothing beyond that is worth holding a connection for.
RENDER_WAIT = 10.0
# How many requests may be waiting for a slot at once. This is the important
# bound, not the wait: these endpoints are synchronous, so every waiting
# request holds one of AnyIO's worker threads (40 by default), and a cap on
# poppler alone would have moved the outage rather than prevented it -- the
# scrapers sustain about five requests a second, which fills the pool with
# threads doing nothing but waiting, and ordinary pages stop being served.
# Four running plus eight waiting leaves the pool most of its threads.
MAX_WAITING = 8
_render_slots = threading.BoundedSemaphore(RENDER_WORKERS)
_waiting = 0
_waiting_lock = threading.Lock()


@contextmanager
def render_slot():
    """Hold one of the `RENDER_WORKERS` poppler slots, or raise `RenderBusy`.

    Refuses immediately once `MAX_WAITING` requests are already queued, so the
    number of threads this can occupy is bounded whatever the arrival rate.
    """
    global _waiting
    with _waiting_lock:
        if _waiting >= MAX_WAITING:
            raise RenderBusy("%d faksimil-renderingar väntar redan"
                             % _waiting)
        _waiting += 1
    try:
        taken = _render_slots.acquire(timeout=RENDER_WAIT)
    finally:
        with _waiting_lock:
            _waiting -= 1
    if not taken:
        raise RenderBusy("alla %d faksimil-renderingar är upptagna"
                         % RENDER_WORKERS)
    try:
        yield
    finally:
        _render_slots.release()


class RenderBusy(RuntimeError):
    """Every facsimile render slot is taken. The API answers 503 with a
    Retry-After; nothing is cached and nothing is wrong."""


class RenderRefused(RuntimeError):
    """This caller may not pay for a render. The page is not in the cache and
    the request did not come from one of our own pages, so the API answers 403
    instead of starting poppler. A *cached* page is still served to everyone --
    this refuses the work, not the picture."""


# The cache is unbounded by construction: one page has more crop rectangles
# than anyone can enumerate, and nothing here can tell a reader's crop from a
# script's. Eviction used to be a cron job alone, which failed silently for
# months and left 658 GB of live cache on the production host. So the writer
# also evicts, and it evicts on *free space* rather than on a size cap: the
# cache shares a filesystem with the corpus, and what matters is that the
# corpus keeps room to write.
CACHE_MIN_FREE = 20 * 1024**3
# what a sweep frees, so it is not re-triggered by the next render
CACHE_FREE_TARGET = 40 * 1024**3
# a `shutil.disk_usage` per render is cheap but not free, and a sweep walks a
# large tree; both are worth doing rarely
RENDERS_BETWEEN_CHECKS = 200

_renders = 0
# separate locks on purpose: every render bumps the counter, and it must not
# queue behind a sweep that is walking a tree of hundreds of thousands of files
_renders_lock = threading.Lock()
_sweeping = threading.Lock()


def evict(root, target_free):
    """Delete the least recently used PNGs under `root` until the filesystem
    has `target_free` bytes free. The sweep itself is `lib/cachesweep`, shared
    with the PDF export cache; this names the files."""
    return cachesweep.sweep(root, root.rglob("*.png"), target_free=target_free)


def _evict_if_low():
    """Sweep the cache when the filesystem is running out, at most once every
    `RENDERS_BETWEEN_CHECKS` renders and never more than one sweep at a time.

    A render that arrives while a sweep is running returns at once rather than
    waiting for it: the sweep walks the whole cache tree, and the render it
    would block has nothing to do with how full the disk is."""
    global _renders
    with _renders_lock:
        _renders += 1
        due = _renders % RENDERS_BETWEEN_CHECKS == 0
    if not due:
        return
    root = layout.FACSIMILE
    if not root.exists() or shutil.disk_usage(root).free >= CACHE_MIN_FREE:
        return
    if not _sweeping.acquire(blocking=False):
        return
    # off the request thread. The sweep walks the whole cache tree and stats
    # every file -- on production that tree reached 658 GB -- so running it
    # inline would hold one of the API's worker threads for minutes and answer
    # that reader late. That is the very thing `render_slot` bounds, so doing
    # it here would put the condition back through the other door.
    try:
        threading.Thread(target=_sweep, args=(root,), daemon=True,
                         name="facsimile-evict").start()
    except RuntimeError:
        # no thread to be had -- the same overload the render slots exist to
        # survive. Give the lock back and skip this sweep: holding it would
        # stop every later one in this process, silently, which is the
        # cron-failed-for-months failure this eviction was added to replace.
        _sweeping.release()


def _sweep(root):
    """One eviction sweep, on its own thread. Holds `_sweeping` for its whole
    length, so the next `RENDERS_BETWEEN_CHECKS` renders start no second one."""
    try:
        freed = evict(root, CACHE_FREE_TARGET)
        print("facsimile cache: freed %.1f GB" % (freed / 1024**3), flush=True)
    finally:
        _sweeping.release()


def cached(source, basefile, pdf_path, page, bbox=None, *, dpi, may_render):
    """The facsimile PNG for one page of a document's source PDF -- or, with
    `bbox`, just that rectangle of the page at `dpi` -- rendered on the first
    request and served from the cache thereafter. `source`/`basefile` identify
    the *source* PDF (for a crop, the amending SFS the region comes from), so
    crops of the same region are shared and a re-verified bbox lands on a fresh
    file.

    `may_render=False` says this caller gets the cache or nothing: a cache miss
    raises `RenderRefused` rather than starting poppler. That is what the HTTP
    routes pass for a request that did not come from one of our own pages. A
    render is the one expensive thing this server does, and scrapers are what
    ask for it -- measured on prod over 30 minutes on 2026-09-05, 8795 of 8835
    `sidN.png` requests carried no `Referer` at all, from 5938 addresses, and
    275 of 300 sampled addresses never fetched a single HTML page.

    It has no default, here and in `api/facsimiles.png_path`: whether a caller
    may spend a second of poppler is not a question code should be able to
    reach this function without answering (rule:fail-fast).

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
    if not may_render:
        raise RenderRefused("%s/%s page %d is not rendered yet" %
                            (source, basefile, page))
    with _render_lock(str(out)):
        if out.exists():                 # rendered while we waited for the lock
            return out
        if bbox:
            render_region(pdf_path, page, bbox, out, dpi)
        else:
            render_page(pdf_path, page, out)
    return out
