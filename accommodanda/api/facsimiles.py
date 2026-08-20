"""The HTTP responder for a rendered source-PDF page or crop.

`lib/facsimile.py` renders and caches the pixels; this is the thin layer that
turns one of those renders into a response, and a client-supplied rectangle
into a validated bbox. It sits in its own module because three routers need it
and `app.py` cannot be one of their imports: `app` imports every router, so a
router importing `app` back would be a cycle.

The poppler exit-code discrimination in `png_response` is the part worth
keeping in one place. A missing page and a corrupt PDF both surface as a
`CalledProcessError`, and they are opposite answers -- 404 for the first, an
unhandled 500 for the second, because a corrupt source is a corpus fault that
must fail loudly rather than read to a caller as "no such page".
"""

import subprocess

from fastapi import HTTPException
from fastapi.responses import FileResponse

from ..lib import facsimile

# immutable: the PDF a facsimile renders from never changes in place (a
# re-download replaces the record wholesale), so clients may cache forever
FAX_HEADERS = {"Cache-Control": "public, max-age=31536000, immutable"}


def parse_bbox(raw):
    """A ``bbox=x0,y0,x1,y1`` query value as the float list the crop renderer
    takes, in PDF points from the page's top-left. A malformed or degenerate
    rectangle is client input, so it is a 400 rather than an assertion."""
    parts = raw.split(",")
    if len(parts) != 4:
        raise HTTPException(400, "bbox needs four comma-separated numbers")
    try:
        bbox = [float(p) for p in parts]
    except ValueError:
        raise HTTPException(400, "bbox coordinates must be numbers") from None
    if not facsimile.valid_bbox(bbox):
        raise HTTPException(400, "bbox must satisfy 0 <= x0 < x1, 0 <= y0 < y1")
    return bbox


def png_response(source, basefile, pdf, page, bbox, missing, *,
                 client_bbox=False, dpi):
    """The cached facsimile PNG of one source-PDF page (or `bbox` of it at `dpi`)
    as a response, rendering it on first request. `missing` is the 404 detail for
    a page the PDF does not have. `client_bbox` says the rectangle came from the
    query string: an off-page one is then the caller's mistake and a 400. Off a
    stored .graphics layer it is a corpus fault and stays a 500.

    `dpi` has no default on purpose: a crop's right resolution follows from what
    the page does with it, and the callers differ. A förarbete illustration is
    shown once at column width and nowhere else; a recovered SFS graphic is a
    thumbnail among hundreds that also opens full size; a crop under review is
    shown once, beside the page it was cut from."""
    try:
        png = facsimile.cached(source, basefile, pdf, page, bbox, dpi=dpi)
    except facsimile.OffPage:
        if not client_bbox:
            raise
        # the detail stays generic: the exception carries the server-side PDF
        # path, which is for the log and not for an anonymous caller
        raise HTTPException(400, "bbox does not lie on the page") from None
    except subprocess.CalledProcessError as exc:
        # poppler exit codes (see `man pdftoppm`): 1 is "error opening a PDF
        # file" -- the source is corrupt, a corpus data-integrity problem
        # that must fail loudly, not read as a client 404. 99 ("other
        # error") is what an out-of-range -f/-l page range produces -- a
        # genuinely missing page, so that alone is a 404.
        if exc.returncode == 1:
            raise
        raise HTTPException(404, missing) from None
    return FileResponse(png, media_type="image/png", headers=FAX_HEADERS)
