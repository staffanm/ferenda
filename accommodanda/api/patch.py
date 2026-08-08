"""The patch-file editor -- the write surface for authoring controlled fixes to a
document's *source material*: a correction of a real error in the downloaded
source, or an obfuscated redaction of personal data. (The commentary/concept editor
next door, `edit.py`, edits editorial markdown; this edits the source the parser
reads.)

Same posture as the rest of the write side: every route is gated by
`auth.require_editor` (401 anonymous / 403 editing-off), same-origin only. The
flow:

  * ``GET /patch/document`` returns a document's *intermediate source text* -- the
    best format to patch (plain text for sfs, innehåll HTML for dv, Formex XML for
    eurlex, via `patchsource`) -- with any existing patch already applied, plus a
    fingerprint of the pristine text.
  * the editor edits that text; ``POST /patch/save`` diffs it against the pristine
    intermediate, writes the *minimal* unified diff to ``patches/<source>/…``,
    commits it attributed to the logged-in editor, and force-reparses the document
    so the fix is live. A 409 if the source drifted under the edit.
  * ``GET /patch/edit`` is a small self-contained HTML page wrapping the two --
    the textarea shows the intermediate format and every save produces a minimal
    patch.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .. import config, patchsource
from ..lib import git, layout, tpl
from ..lib import patch as patchlib
from ..lib.errors import SkipDocument
from .auth import Editor, require_editor
from .db import base_sha

_EDITOR_PAGE = tpl.environment("accommodanda.api").get_template(
    "patch_edit.html")

router = APIRouter(prefix="/api/v1/patch", tags=["patch"])

# build injects the single-document reparse (build imports this package for
# `serve`, so we can't import build here -- the same injection the commentary
# editor uses for its rebuild). A save before it's wired 503s rather than
# silently leaving the artifact stale.
_reparse = None


def set_reparse(fn):
    """Called once by build.py at import to supply the post-save reparse."""
    global _reparse
    _reparse = fn


def _load(source, basefile):
    """``(pristine, current, label)`` for a document, or a 4xx: an unpatchable
    source is 400, a source with no readable content is 404."""
    if not patchsource.is_patchable(source):
        raise HTTPException(400, patchsource.unpatchable_message(source))
    try:
        return patchsource.pristine_and_current(source, basefile)
    except (FileNotFoundError, OSError, SkipDocument, ValueError) as exc:
        raise HTTPException(404, "no patchable source for %s/%s: %s"
                            % (source, basefile, exc)) from exc


class PatchView(BaseModel):
    source: str
    basefile: str
    format: str
    text: str                 # current: pristine with any existing patch applied
    has_patch: bool
    is_obfuscated: bool
    description: str | None
    base_sha: str             # fingerprint of the pristine text (concurrency guard)


class SaveBody(BaseModel):
    source: str
    basefile: str
    edited_text: str
    description: str = ""
    obfuscated: bool = False
    base_sha: str


@router.get("/document", response_model=PatchView)
def get_document(source: str = Query(...), basefile: str = Query(...),
                 editor: Editor = Depends(require_editor)):
    """A document's intermediate source text to edit, patch already applied."""
    pristine, current, label = _load(source, basefile)
    path, is_obfuscated = patchlib.find_patch(source, basefile)
    desc = patchlib.load_patchset(source, basefile)[1] if path else None
    return PatchView(source=source, basefile=basefile, format=label, text=current,
                     has_patch=path is not None, is_obfuscated=is_obfuscated,
                     description=desc, base_sha=base_sha(pristine))


@router.post("/save")
def save(body: SaveBody, editor: Editor = Depends(require_editor)):
    """Diff the edited text against the pristine intermediate, write + commit the
    minimal patch as this editor, and force-reparse the document. 409 if the
    source drifted since it was loaded; an edit identical to the pristine text
    removes the patch."""
    if _reparse is None:
        raise HTTPException(503, "reparse not wired -- the editor runs under "
                                 "`lagen serve`, which supplies it")
    pristine, _current, _label = _load(body.source, body.basefile)
    if base_sha(pristine) != body.base_sha:
        raise HTTPException(409, "the source changed since you loaded it; reload")
    path = patchlib.create_patch(body.source, body.basefile, pristine,
                                 body.edited_text, description=body.description,
                                 obfuscated=body.obfuscated)
    removed = path is None
    sha = _commit(body.source, body.basefile, editor, removed=removed,
                  obfuscated=body.obfuscated)
    _reparse(body.source, body.basefile)
    return {"removed": removed, "sha": sha,
            "path": None if removed else str(path.relative_to(config.REPO))}


def _commit(source, basefile, editor, removed, obfuscated):
    """Stage a document's patch files and commit them to the code repo as the
    logged-in editor (git identity = their name/email, exactly as a hand commit
    would attribute it). Returns the commit sha, or HEAD when nothing changed."""
    repo = config.REPO
    rels = [str(layout.patch(source, basefile, sfx).relative_to(repo))
            for sfx in (patchlib.PLAIN_SUFFIX, patchlib.ROT18_SUFFIX, ".desc")]
    # stage only the variants that exist on disk (a write) or are tracked (a
    # deletion) -- a pathspec matching neither aborts `git add` with a fatal
    tracked = set(git.run(repo, "ls-files", "-z", "--", *rels, capture=True).split("\0"))
    paths = sorted({r for r in rels if (repo / r).exists()} | (tracked - {""}))
    if not paths:
        return git.run(repo, "rev-parse", "HEAD", capture=True)
    verb = "Remove patch for" if removed else ("Redact" if obfuscated else "Patch")
    return git.commit_as(repo, paths, "%s %s %s" % (verb, source, basefile),
                         name=editor.name, email=editor.email)


# --------------------------------------------------------------------------
# a small self-contained editor page (templates/patch_edit.html; no
# build-time asset -- served on demand)
# --------------------------------------------------------------------------

@router.get("/edit", response_class=HTMLResponse)
def edit_page(source: str = Query(...), basefile: str = Query(...),
              editor: Editor = Depends(require_editor)):
    """A minimal self-contained editor page for one document's patch. Gated like
    every write route; the page's fetches carry the session cookie."""
    if not patchsource.is_patchable(source):
        raise HTTPException(400, patchsource.unpatchable_message(source))
    return _EDITOR_PAGE.render(
        source=source, basefile=basefile,
        format=patchsource.format_label(source))
