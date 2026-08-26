"""The crop-review editor -- the write surface for signing off the graphics the
vision pass placed.

`sfs ai-includegraphics` locates each graphic/table/formula the consolidated SFS
text drops to a page + rectangle of the published PDF, and writes it as a
``generated`` ``.graphics`` layer. `annstore.publishable` keeps a generated
entry out of the public render until a human approves it, so this editor is the
step between "the model placed it" and "the reader sees it".

Same posture as the rest of the write side: the routes live on the internal API
(`api/internal.py`) at `/internal-api/v1/graphics/…`, same-origin only, and each
is gated by `auth.require_editor` (401 anonymous / 403 editing-off). The
flow mirrors the commentary editor rather than the patch editor, because
approving crops is *batch* work -- twenty small decisions, one commit:

  * ``GET /graphics/queue`` lists every entry the site will not yet show.
  * ``GET /graphics/page`` and ``GET /graphics/crop`` render the source PDF
    page and the rectangle on it. Both deliberately bypass `publishable`,
    which is exactly what the public `/api/v1/sfs-graphic` refuses to do: an
    editor has to see the draft to judge it, and nobody else may.
  * ``POST /graphics/cart`` carts one decision -- approve as-is, approve a moved
    rectangle, or approve the whole page -- reusing `editcart`, so the existing
    cart widget, conflict check and attributed commit all carry it.
  * ``GET /graphics/review`` is a small self-contained page wrapping the four.

The reviewer judges three things at once, which is why the page shows the crop
*and* the whole source page with the rectangle drawn on it: a confident
placement on the wrong figure still returns a clean, plausible picture, and only
the full page reveals it.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from ..lib import facsimile, layout, tpl
from . import editcart, facsimiles, graphicsedit
from .auth import Editor, require_editor

_REVIEW_PAGE = tpl.environment("ferenda.api").get_template(
    "graphics_review.html")

router = APIRouter(prefix="/graphics", tags=["graphics"])


class Decision(BaseModel):
    """One reviewed crop. `bbox` null means the whole page; `verified` false
    carts an un-approval (the entry goes back to draft)."""
    ref: str
    anchor: str
    page: int
    bbox: list[float] | None = None
    verified: bool = True
    base_sha: str


def _region(ref, anchor):
    """The addressed region, or a 404 when the layer or the gap is gone -- a
    re-parse can retire a gap key under an open review tab."""
    region = graphicsedit.Region(ref, anchor)
    if graphicsedit.entry_of(region) is None:
        raise HTTPException(404, "no pending graphic %s in %s" % (anchor, ref))
    return region


@router.get("/queue")
def queue_endpoint(editor: Editor = Depends(require_editor)):
    """Every crop awaiting review, with the context the judgement needs: the
    anchor text it sits under, its alt text, and the provenance PDF and page."""
    return {"pending": graphicsedit.queue()}


def _source_pdf(src):
    pdf = layout.sfs_pdf(src)
    if not pdf.exists():
        raise HTTPException(404, "source SFS %s is not mirrored" % src)
    return pdf


@router.get("/page", response_class=FileResponse,
            responses={200: {"content": {"image/png": {}}}})
def page_endpoint(sfs: str = Query(..., description="provenance SFS basefile"),
                  page: int = Query(..., ge=1),
                  editor: Editor = Depends(require_editor)):
    """The whole source page, for drawing the rectangle on and for paging
    through when the model picked the wrong page. Rendered at the same DPI as
    a facsimile, so client coordinates convert with one scale factor."""
    return facsimiles.png_response("sfs", sfs, _source_pdf(sfs), page, None,
                                   "SFS %s has no page %d" % (sfs, page),
                                   dpi=facsimile.DPI)


@router.get("/pagesize")
def pagesize_endpoint(sfs: str = Query(...), page: int = Query(..., ge=1),
                      editor: Editor = Depends(require_editor)):
    """The page's size in PDF points. The browser drags in image pixels; a bbox
    is stored in points from the top-left, and this is the other half of that
    conversion (the rendered PNG's pixel size is the first)."""
    width, height = facsimile.page_size(_source_pdf(sfs), page)
    return {"width": width, "height": height, "dpi": facsimile.DPI}


@router.get("/crop", response_class=FileResponse,
            responses={200: {"content": {"image/png": {}}}})
def crop_endpoint(sfs: str = Query(...), page: int = Query(..., ge=1),
                  bbox: str = Query(..., description="x0,y0,x1,y1 in PDF points"),
                  editor: Editor = Depends(require_editor)):
    """The rectangle as the reader would see it. The bbox comes from the query
    string rather than the layer, so the reviewer sees a dragged rectangle
    before carting it."""
    return facsimiles.png_response(
        "sfs", sfs, _source_pdf(sfs), page, facsimiles.parse_bbox(bbox),
        "SFS %s has no page %d" % (sfs, page),
        client_bbox=True, dpi=facsimile.CROP_DPI)


@router.post("/cart")
def cart_endpoint(body: Decision, editor: Editor = Depends(require_editor)):
    """Cart one decision; returns the resulting cart size. A `base_sha` that no
    longer matches the entry is a 409 here rather than at commit, so a reviewer
    learns immediately that a re-run moved the crop under them."""
    region = _region(body.ref, body.anchor)
    current = graphicsedit.read(region)
    if body.base_sha != current["base_sha"]:
        raise HTTPException(409, "this crop changed since you opened it "
                                 "-- reload before deciding")
    payload = graphicsedit.canonical({"page": body.page, "bbox": body.bbox,
                                      "verified": body.verified})
    try:
        graphicsedit.parse(payload)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    return {"carted": editcart.upsert(editor.username, region, payload)}


@router.get("/review", response_class=HTMLResponse, include_in_schema=False)
def review_page(editor: Editor = Depends(require_editor)):
    """The review queue page itself -- served on demand behind the editor
    session, no build-time asset, no site chrome (the patch editor's pattern)."""
    return HTMLResponse(_REVIEW_PAGE.render(username=editor.username))
