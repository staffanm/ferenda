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

import os
import subprocess

from . import layout

DPI = 150


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
