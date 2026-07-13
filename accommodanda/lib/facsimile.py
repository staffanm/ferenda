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
"""

import math
import os
import re
import subprocess

from . import layout

DPI = 150


def page_count(pdf_path):
    """The number of pages in `pdf_path` (poppler's ``pdfinfo``). Raises
    CalledProcessError on a broken/absent PDF -- the caller knows the context."""
    out = subprocess.run(["pdfinfo", str(pdf_path)],
                         capture_output=True, check=True, text=True).stdout
    m = re.search(r"^Pages:\s*(\d+)", out, re.M)
    assert m, "pdfinfo emitted no page count for %s" % pdf_path
    return int(m.group(1))


def render_page(pdf_path, page, out_path):
    """Render 1-based `page` of `pdf_path` to `out_path` (a PNG at `DPI`) and
    return `out_path`. Renders to a sibling temp name and replaces, so a
    concurrent reader never sees a half-written file. Raises
    CalledProcessError when pdftoppm cannot render (no such page, broken
    PDF) -- the caller knows the request context and maps it to its own
    error."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # pid-unique temp root: two workers racing on the same page each write
    # their own file; the loser's replace is a harmless overwrite
    tmp_root = out_path.parent / ("%s.tmp%d" % (out_path.stem, os.getpid()))
    subprocess.run(
        ["pdftoppm", "-png", "-r", str(DPI), "-f", str(page), "-l", str(page),
         "-singlefile", str(pdf_path), str(tmp_root)],
        capture_output=True, check=True)
    (out_path.parent / (tmp_root.name + ".png")).replace(out_path)
    return out_path


def cached_page(source, basefile, pdf_path, page):
    """The facsimile PNG for one page of a document's source PDF, rendering on
    the first request and serving the cache thereafter."""
    out = layout.facsimile(source, basefile, page)
    if not out.exists():
        render_page(pdf_path, page, out)
    return out


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


def render_region(pdf_path, page, bbox, out_path):
    """Render just the `bbox` rectangle of 1-based `page` to `out_path` (a PNG
    at `DPI`) and return it. `bbox` is ``[x0, y0, x1, y1]`` in raw PDF points
    with a TOP-LEFT origin -- the representation the .graphics layer stores;
    poppler's ``-x/-y/-W/-H`` crop window is likewise top-left, in device
    pixels, so each point coordinate scales by `DPI`/72. Same atomic
    temp->replace and error contract as `render_page`."""
    assert valid_bbox(bbox), "invalid PDF crop bbox %r" % (bbox,)
    x0, y0, x1, y1 = bbox
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_root = out_path.parent / ("%s.tmp%d" % (out_path.stem, os.getpid()))
    subprocess.run(
        ["pdftoppm", "-png", "-r", str(DPI), "-f", str(page), "-l", str(page),
         "-x", str(round(x0 * DPI / 72)), "-y", str(round(y0 * DPI / 72)),
         "-W", str(round((x1 - x0) * DPI / 72)),
         "-H", str(round((y1 - y0) * DPI / 72)),
         "-singlefile", str(pdf_path), str(tmp_root)],
        capture_output=True, check=True)
    (out_path.parent / (tmp_root.name + ".png")).replace(out_path)
    return out_path


def png_size(data):
    """(width, height) of a PNG from its IHDR -- the big-endian ints at 16/20.
    Raises ValueError if `data` is not a PNG (a renderer that returned garbage)."""
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("facsimile renderer did not return a PNG")
    return (int.from_bytes(data[16:20], "big"),
            int.from_bytes(data[20:24], "big"))


def cached_region(source, basefile, pdf_path, page, bbox):
    """The cropped PNG for one `bbox` of a source PDF page, rendered on first
    request and served from the cache thereafter. `source`/`basefile` identify
    the *source* PDF (the amending SFS the region is cropped from), so crops of
    the same region are shared and a re-verified bbox lands on a fresh file."""
    out = layout.facsimile_crop(source, basefile, page, bbox)
    if not out.exists():
        render_region(pdf_path, page, bbox, out)
    return out
