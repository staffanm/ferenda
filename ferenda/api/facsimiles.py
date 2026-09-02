"""Rendered source-PDF pages and crops: which PDF a document uri names, and the
response (or the raw bytes) for one page or rectangle of it.

`lib/facsimile.py` renders and caches the pixels. This module is everything
between a request and that call: the per-source resolvers that turn a uri-local
path into a downloaded PDF, the two answers built on them (`facsimile_response`,
`sfs_graphic_response`), a client-supplied rectangle validated into a bbox, and
`subresource`, which serves the same two answers as bytes rather than as HTTP.

It sits in its own module because several routers need it and `app.py` cannot be
one of their imports: `app` imports every router, so a router importing `app`
back would be a cycle. That is also what `subresource` is for. The PDF export
has to fetch the facsimiles a page prints, and it used to do that through an
in-process `TestClient(app)` -- which meant the routes that start an export were
stranded in `app.py`, since only `app.py` can name `app`. Measured over three
real exports, the export asks for exactly two path families and nothing else
(the stylesheet and fonts `api/pdf.py` already serves off disk itself), and both
of them end at a `Path` from `facsimile.cached`. Reading that file costs 0.11 ms
against 2.34 ms through the ASGI stack (median of five runs of 80 fetches,
warm) -- 0.8 s of round trips on 2007:90, which prints 325 road signs -- so the
whole client is replaced by a lookup and the export routes go back where they
belong (`api/pdfjob.py`).

The poppler exit-code discrimination in `png_response` is the part worth
keeping in one place. A missing page and a corrupt PDF both surface as a
`CalledProcessError`, and they are opposite answers -- 404 for the first, an
unhandled 500 for the second, because a corrupt source is a corpus fault that
must fail loudly rather than read to a caller as "no such page".
"""

import json
import re
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from fastapi import HTTPException
from fastapi.responses import FileResponse

from ..lib import annstore, catalog, compress, facsimile, layout, regeringen
from ..lib.util import basefile_slug
from . import db

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


def png_path(source, basefile, pdf, page, bbox, missing, *,
             client_bbox=False, dpi):
    """The cached facsimile PNG of one source-PDF page (or `bbox` of it at `dpi`)
    as a path on disk, rendering it on first request. `missing` is the 404 detail
    for a page the PDF does not have. `client_bbox` says the rectangle came from the
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
    return png


def png_response(*args, **kwargs):
    """`png_path`'s answer as an HTTP response. The split is what lets the PDF
    export read the same pixels without an HTTP client (see `subresource`)."""
    return FileResponse(png_path(*args, **kwargs), media_type="image/png",
                        headers=FAX_HEADERS)


# a förarbete basefile as it appears in a uri path: "prop/2013/14:116" (the
# riksmöte types carry an extra slash), "sou/2021:82", "bet/2020/21:JuU25";
# the type whitelist is the harvest vocabulary + bet
_RE_FA_BASEFILE = re.compile(
    r"^(%s|bet)/(\d{4}(?:/\d{2,4})?:[A-Za-zÅÄÖ]*\d+[a-z]?)$"
    % "|".join(regeringen.TYPES))
# a föreskrift: "<fs>/<year>:<löpnr>" ("mcffs/2026:1")
_RE_FS_BASEFILE = re.compile(r"^([a-zåäö]+)/(\d{4}:\d+)$")
# an avgörande: "avg/<org>/<dnr>" ("avg/jo/2340-2025", "avg/jk/2024/8082")
_RE_AVG_BASEFILE = re.compile(r"^avg/([a-z]+)/([A-Za-z0-9/-]+)$")
# a rättsligt ställningstagande, the same {source}/{org}/{nummer} grammar; the
# number keeps the colon four of the six agencies write it with ("fk/2025:01")
_RE_RS_BASEFILE = re.compile(r"^rs/([a-z]+)/([A-Za-z0-9:/-]+)$")


def _fa_pdf(local):
    m = _RE_FA_BASEFILE.match(local)
    if not m:
        return None
    typ, num = m.group(1), m.group(2)
    basefile = "%s/%s" % (typ, basefile_slug(num))
    record_path = layout.fa_record(basefile)
    if not compress.exists(record_path):
        return None
    record = compress.read_json(record_path)
    pdfs = [layout.fa_dir(layout.FA_DOWNLOADED, typ, num) / f
            for f in record.get("files", []) if f.lower().endswith(".pdf")]
    if pdfs:
        return ("forarbete", basefile, pdfs[0])
    # no PDF body, but the document may still have a page-image scan beside its
    # record (the KB propkb facsimiles -- forarbete/propkb.py). Resolved by rule
    # + existence, like the mirrored SFS PDFs in `_sfs_pdf`: it is a facsimile
    # source, not a parse input, so it is deliberately not named in the record.
    scan = layout.fa_facsimile_pdf(typ, m.group(2))
    return ("forarbete", basefile, scan) if scan.exists() else None


def _foreskrift_pdf(local):
    m = _RE_FS_BASEFILE.match(local)
    if not m or m.group(1) in regeringen.TYPES:
        return None
    fs = m.group(1)
    record_path = (layout.FORESKRIFT_DOWNLOADED / fs
                   / (basefile_slug(local) + ".json"))
    if not compress.exists(record_path):
        return None
    # the page anchors come from the `regulation` PDF (the body foreskrift's
    # parse reads), so that is the one a facsimile must rasterize
    regulation = compress.read_json(record_path)["files"].get("regulation")
    if not regulation:
        return None
    return ("foreskrift", local, layout.FORESKRIFT_DOWNLOADED / fs
            / regulation["name"])


def _avg_pdf(local):
    m = _RE_AVG_BASEFILE.match(local)
    if not m:
        return None
    basefile = local[len("avg/"):]
    pdf = (layout.AVG_DOWNLOADED / m.group(1)
           / (basefile_slug(basefile) + ".pdf"))
    return ("avg", basefile, pdf) if pdf.exists() else None


def _rs_pdf(local):
    m = _RE_RS_BASEFILE.match(local)
    if not m:
        return None
    basefile = local[len("rs/"):]
    pdf = (layout.RS_DOWNLOADED / m.group(1)
           / (basefile_slug(basefile) + ".pdf"))
    return ("rs", basefile, pdf) if pdf.exists() else None


# an SFS: a bare "<year>:<löpnr>" ("2002:780"), no source prefix -- the
# officially published PDF the mirror fetched (pdfmirror), facsimile source for
# both a full published page and a sfs-graphic crop
_RE_SFS_BASEFILE = re.compile(r"^\d{4}:\d+[a-z]?$")


def _sfs_pdf(local):
    if not _RE_SFS_BASEFILE.match(local):
        return None
    pdf = layout.sfs_pdf(local)
    return ("sfs", local, pdf) if pdf.exists() else None


def _dv_pdf(local):
    # a raw verdict's own source PDF, for the inline page-facsimile buttons. Unlike
    # the other sources there is no layout rule from the uri to the PDF (it is a
    # court attachment keyed by an opaque uuid), so the path is read from the
    # artifact's stamped `facsimile_pdf`. Only raw verdicts carry it; a referat
    # renders from HTML and has no facsimile.
    if not local.startswith("dom/") or not db.catalog_ready():
        return None
    with db.connection() as con:
        row = catalog.document(con, catalog.BASE + local)
        art = catalog.artifact_for(con, row[5]) if row else {}
    ref = art.get("facsimile_pdf")
    if not ref:
        return None
    pdf = layout.DATA / ref
    return ("dv", basefile_slug(local), pdf) if pdf.exists() else None


_PDF_RESOLVERS = (_fa_pdf, _avg_pdf, _rs_pdf, _foreskrift_pdf, _sfs_pdf, _dv_pdf)


def facsimile_path(local, sid, bbox=None):
    """The facsimile PNG for page `sid` of the document at uri-local path
    `local` ("prop/2013/14:116"), rendering into the disk cache on first
    request. With `bbox`, just that rectangle of the page -- the same renderer
    and cache the SFS graphics layer crops its figures with, so a förarbete's
    illustration needs no extraction path of its own: the pixels are already
    in the source PDF and this reads them where they are."""
    if ".." in local or sid < 1:
        raise HTTPException(404, "no such document: %r" % local)
    resolved = next(filter(None, (r(local) for r in _PDF_RESOLVERS)), None)
    if resolved is None:
        raise HTTPException(404, "no PDF source downloaded for %r" % local)
    source, basefile, pdf = resolved
    # a förarbete's illustration is displayed once, at the measure of the text
    # column, and nothing opens it larger -- so it is rendered at the same
    # resolution as the page facsimile it is cut from
    return png_path(source, basefile, pdf, sid, bbox,
                    "%r has no page %d" % (local, sid),
                    client_bbox=bbox is not None, dpi=facsimile.DPI)


def facsimile_response(local, sid, bbox=None):
    """`facsimile_path`'s PNG as an HTTP response."""
    return FileResponse(facsimile_path(local, sid, bbox),
                        media_type="image/png", headers=FAX_HEADERS)


def sfs_source_pdf(src: str) -> Path:
    """The mirrored published PDF of SFS `src`, which is where an sfs-graphic
    region is cropped from -- a 404 when the mirror does not hold it."""
    pdf = layout.sfs_pdf(src)
    if not pdf.exists():
        raise HTTPException(404, "source SFS %s is not mirrored" % src)
    return pdf


# sfs-graphic: a crop of the graphic/formula/map the consolidated SFS text drops
# but the published PDF carries. Unlike a facsimile the client sends only the
# viewed statute + gap id; the reviewed .graphics layer holds the geometry AND
# the provenance -- which amending SFS's PDF the region is cropped from (the act
# that last set that wording), not the viewed statute's own PDF.
def sfs_graphic_path(local, node, dpi):
    """The cropped PNG for gap `node` of the SFS at uri-local `local`, its page,
    bbox and source PDF read from the statute's .graphics layer."""
    if ".." in local or not _RE_SFS_BASEFILE.match(local):
        raise HTTPException(404, "not an SFS document: %r" % local)
    layer = annstore.path("sfs", local, ".graphics")
    if not layer.exists():
        raise HTTPException(404, "no graphics layer for %r" % local)
    content = json.loads(layer.read_text())
    entry = content.get(node)
    if entry is None:
        raise HTTPException(404, "no graphic %r in %r" % (node, local))
    if not annstore.publishable(content.get("meta", {}), entry):
        raise HTTPException(404, "graphic %r in %r is not verified" % (node, local))
    # the amending SFS whose published PDF carries the region (provenance)
    src, page, bbox = entry["sfs"], entry["page"], entry.get("bbox")
    assert isinstance(src, str) and _RE_SFS_BASEFILE.fullmatch(src), \
        "%s/%s: invalid graphics source %r" % (local, node, src)
    assert isinstance(page, int) and not isinstance(page, bool) and page > 0, \
        "%s/%s: invalid graphics page %r" % (local, node, page)
    if bbox is not None:
        assert facsimile.valid_bbox(bbox), \
            "%s/%s: invalid graphics bbox %r" % (local, node, bbox)
    pdf = sfs_source_pdf(src)
    # an entry with no bbox *is* the whole page, which has one resolution: there
    # is no larger render to ask for, so `stor` cannot apply to it
    return png_path("sfs", src, pdf, page, bbox,
                    "SFS %s has no page %d" % (src, page),
                    dpi=dpi if bbox else facsimile.DPI)


def sfs_graphic_response(local, node, dpi):
    """`sfs_graphic_path`'s PNG as an HTTP response."""
    return FileResponse(sfs_graphic_path(local, node, dpi),
                        media_type="image/png", headers=FAX_HEADERS)


# --------------------------------------------------------------------------
# the same two answers as bytes, for the PDF export
# --------------------------------------------------------------------------

# what a generated page can ask the export to fetch. Measured, not guessed:
# instrumented over three real exports, WeasyPrint asks for `/api/v1/facsimile`
# (a förarbete's figure crops) and `/api/v1/sfs-graphic` (a statute's recovered
# graphics) and nothing else -- `api/pdf.py`'s own fetcher answers /style.css
# and /fonts/* off lib/assets and decodes data: URIs in place, and the page
# facsimile *buttons* are hidden by the print stylesheet, so they never load.
# A path outside this table is refused rather than guessed at: the fetcher
# records every failure and `pdf.render_document` then refuses the degraded
# PDF, so a renderer that starts emitting a new subresource fails loudly here
# instead of silently printing a page with a hole in it.
_SUBRESOURCE_PATHS = ("/api/v1/facsimile", "/api/v1/sfs-graphic")


def subresource(path_qs):
    """`(bytes, content_type)` for one in-site subresource path+query, read
    straight off disk -- the callable `api/pdf.py`'s WeasyPrint url_fetcher
    hands its misses to.

    Deliberately not an HTTP client. Both answers below end at a `Path` from
    `facsimile.cached`, so going through the app to read it cost a full ASGI
    round trip per image for nothing (see the module docstring). Errors stay
    exceptions: the fetcher catches them, records the url, and the export
    refuses to hand out a PDF that is missing a picture."""
    url = urlsplit(path_qs)
    if url.path not in _SUBRESOURCE_PATHS:
        raise ValueError("no subresource at %s" % url.path)
    query = parse_qs(url.query)
    local = catalog.uri_local(_one(query, "uri", url.path))
    if url.path == "/api/v1/facsimile":
        raw = query.get("bbox", [None])[0]
        png = facsimile_path(local, int(_one(query, "sid", url.path)),
                             parse_bbox(raw) if raw else None)
    else:
        png = sfs_graphic_path(
            local, _one(query, "node", url.path),
            facsimile.CROP_DPI_LARGE
            if query.get("stor", ["0"])[0] not in ("0", "")
            else facsimile.CROP_DPI)
    return png.read_bytes(), "image/png"


def _one(query, name, path):
    """The single value of a required query parameter, or a ValueError naming
    what was missing -- the fetcher turns that into a recorded failure."""
    values = query.get(name)
    if not values:
        raise ValueError("%s needs a %s parameter" % (path, name))
    return unquote(values[0])

