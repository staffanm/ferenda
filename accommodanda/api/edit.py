"""The write side of the service: the inline editor's REST surface.

Every route is gated by `auth.require_editor`, so an anonymous or expired session
gets a 401 and editing-disabled (no `editor_secret`) a 403 -- the same posture as
the ops dashboard, one gate, no per-route checks. The public read API stays
GET-only and CORS-open; these mutating routes are same-origin only (the editor
JS runs on the served site), and the session cookie is `SameSite=Lax`, so a
cross-site page can't drive them.

The flow mirrors the UI: `GET /edit/region` fills the inline textarea,
`POST /edit/region` carts a hunk (or un-carts a no-op), `GET /edit/cart` +
`POST /edit/discard` drive the cart widget, and `POST /edit/commit` turns the
whole cart into one attributed git commit and *synchronously* regenerates the
pages it touched (`build.rebuild_after_commit`) so the edit is live when the call
returns. A stale hunk fails the commit as a 409 with the offending keys.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from . import editcart, editcontent
from .auth import Editor, require_editor

router = APIRouter(prefix="/api/v1/edit", tags=["edit"])

# The post-commit page rebuild lives in build.py (it drives relate/generate). We
# must not import build here: build imports this package (for `serve`), so a
# top-level `from .. import build` would close an app<-edit<-build import cycle.
# Instead build injects its `rebuild_after_commit` via `set_rebuild` when it
# loads -- so the server (whose entry point is build) always wires it, and a
# commit before it is wired fails loudly rather than silently skipping the
# rebuild. Dependency injection, not a lazy in-function import (rule:no-infunction-imports).
_rebuild = None


def set_rebuild(fn):
    """Called once by build.py at import to supply the post-commit rebuild."""
    global _rebuild
    _rebuild = fn


def _region(kind, ref, anchor):
    """A validated Region, or a 400 -- a bad kind/anchor is user input, not a
    server fault (`editcontent.Region` raises ValueError; the resolver's missing
    concept/page raises it too)."""
    try:
        return editcontent.Region(kind=kind, ref=ref, anchor=anchor or None)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


class RegionView(BaseModel):
    markdown: str
    exists: bool
    base_sha: str
    draft: bool


class EditBody(BaseModel):
    kind: str
    ref: str
    anchor: str | None = None
    new_text: str


class DiscardBody(BaseModel):
    key: str


class CommitBody(BaseModel):
    message: str


@router.get("/region", response_model=RegionView)
def get_region(kind: str = Query(...), ref: str = Query(...),
               anchor: str | None = Query(None),
               editor: Editor = Depends(require_editor)):
    """The markdown to edit for one region -- the current on-disk text, or this
    user's pending draft if the hunk is already carted, or a seeded template for a
    node with no commentary yet."""
    try:
        return editcart.region_view(editor.username, _region(kind, ref, anchor))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/region")
def put_region(body: EditBody, editor: Editor = Depends(require_editor)):
    """Cart an edited hunk (or un-cart it when the text matches disk). Returns the
    new cart size for the badge."""
    try:
        n = editcart.upsert(editor.username,
                            _region(body.kind, body.ref, body.anchor),
                            body.new_text)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"cart": n}


@router.post("/discard")
def discard(body: DiscardBody, editor: Editor = Depends(require_editor)):
    """Drop one hunk from the cart."""
    return {"cart": editcart.discard(editor.username, body.key)}


@router.get("/cart")
def get_cart(editor: Editor = Depends(require_editor)):
    """The pending hunks for the checkout panel."""
    return {"drafts": editcart.cart(editor.username)}


@router.post("/commit")
def commit(body: CommitBody, editor: Editor = Depends(require_editor)):
    """Check out the cart: one attributed git commit, then a synchronous rebuild
    of the pages it touched. 409 if a hunk went stale (nothing written)."""
    if _rebuild is None:
        raise HTTPException(503, "rebuild not wired -- the editor runs under "
                                 "`lagen serve`, which supplies it")
    try:
        result = editcart.commit(editor, body.message)
    except editcart.Conflict as exc:
        raise HTTPException(409, {"conflicts": exc.keys}) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    rebuilt = _rebuild(result["changes"])
    return {"sha": result["sha"], "rebuilt": rebuilt}
