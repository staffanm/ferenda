"""The patch-file editor -- the write surface for authoring controlled fixes to a
document's *source material*: a correction of a real error in the downloaded
source, or a rot13 redaction of personal data. (The commentary/concept editor
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

import hashlib
import os
from html import escape

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .. import config, patchsource
from ..lib import git, layout
from ..lib import patch as patchlib
from ..lib.errors import SkipDocument
from .auth import Editor, require_editor

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


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load(source, basefile):
    """``(pristine, current, label)`` for a document, or a 4xx: an unpatchable
    source is 400, a source with no readable content is 404."""
    if source not in patchsource._INTERMEDIATE:
        raise HTTPException(400, "source %r has no patchable intermediate; "
                            "patchable: %s"
                            % (source, ", ".join(patchsource.patchable_sources())))
    try:
        pristine, label = patchsource.intermediate(source, basefile)
    except (FileNotFoundError, OSError, SkipDocument, ValueError) as exc:
        raise HTTPException(404, "no patchable source for %s/%s: %s"
                            % (source, basefile, exc)) from exc
    current, _desc = patchlib.patch_if_needed(source, basefile, pristine)
    return pristine, current, label


class PatchView(BaseModel):
    source: str
    basefile: str
    format: str
    text: str                 # current: pristine with any existing patch applied
    has_patch: bool
    is_rot13: bool
    description: str | None
    base_sha: str             # fingerprint of the pristine text (concurrency guard)


class SaveBody(BaseModel):
    source: str
    basefile: str
    edited_text: str
    description: str = ""
    rot13: bool = False
    base_sha: str


@router.get("/document", response_model=PatchView)
def get_document(source: str = Query(...), basefile: str = Query(...),
                 editor: Editor = Depends(require_editor)):
    """A document's intermediate source text to edit, patch already applied."""
    pristine, current, label = _load(source, basefile)
    path, is_rot13 = patchlib.find_patch(source, basefile)
    desc = patchlib.load_patchset(source, basefile)[1] if path else None
    return PatchView(source=source, basefile=basefile, format=label, text=current,
                     has_patch=path is not None, is_rot13=is_rot13,
                     description=desc, base_sha=_sha(pristine))


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
    if _sha(pristine) != body.base_sha:
        raise HTTPException(409, "the source changed since you loaded it; reload")
    path = patchlib.create_patch(body.source, body.basefile, pristine,
                                 body.edited_text, description=body.description,
                                 rot13=body.rot13)
    removed = path is None
    sha = _commit(body.source, body.basefile, editor, removed=removed,
                  rot13=body.rot13)
    _reparse(body.source, body.basefile)
    return {"removed": removed, "sha": sha,
            "path": None if removed else str(path.relative_to(config.REPO))}


def _commit(source, basefile, editor, removed, rot13):
    """Stage a document's patch files and commit them to the code repo as the
    logged-in editor (git identity = their name/email, exactly as a hand commit
    would attribute it). Returns the commit sha, or HEAD when nothing changed."""
    repo = config.REPO
    rels = [str(layout.patch(source, basefile, sfx).relative_to(repo))
            for sfx in (patchlib.PLAIN_SUFFIX, patchlib.ROT13_SUFFIX, ".desc")]
    # stage only the variants that exist on disk (a write) or are tracked (a
    # deletion) -- a pathspec matching neither aborts `git add` with a fatal
    tracked = set(git.run(repo, "ls-files", "-z", "--", *rels, capture=True).split("\0"))
    paths = sorted({r for r in rels if (repo / r).exists()} | (tracked - {""}))
    if not paths:
        return git.run(repo, "rev-parse", "HEAD", capture=True)
    git.run(repo, "add", "-A", "--", *paths)
    if not git.run(repo, "status", "--porcelain", "--", *paths, capture=True):
        return git.run(repo, "rev-parse", "HEAD", capture=True)
    verb = "Remove patch for" if removed else ("Redact" if rot13 else "Patch")
    env = {**os.environ,
           "GIT_AUTHOR_NAME": editor.name, "GIT_AUTHOR_EMAIL": editor.email,
           "GIT_COMMITTER_NAME": editor.name, "GIT_COMMITTER_EMAIL": editor.email}
    git.run(repo, "commit", "-m", "%s %s %s" % (verb, source, basefile),
            "--", *paths, env=env)
    return git.run(repo, "rev-parse", "HEAD", capture=True)


# --------------------------------------------------------------------------
# a small self-contained editor page (no build-time asset; served on demand)
# --------------------------------------------------------------------------

_EDITOR_HTML = """<!doctype html>
<html lang="sv"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Patch %(source)s %(basefile)s</title>
<style>
 body{font:14px/1.5 system-ui,sans-serif;margin:0;padding:1.5rem;max-width:70rem}
 h1{font-size:1.1rem} .fmt{color:#666;font-weight:normal}
 textarea{width:100%%;height:60vh;font:13px/1.4 ui-monospace,monospace;
   box-sizing:border-box;padding:.5rem;white-space:pre}
 .row{margin:.6rem 0;display:flex;gap:1rem;align-items:center;flex-wrap:wrap}
 input[type=text]{flex:1;min-width:12rem;padding:.35rem}
 button{padding:.45rem 1rem;font-size:1rem;cursor:pointer}
 #msg{margin-left:auto} .ok{color:#137333} .err{color:#c5221f}
 .hint{color:#666;font-size:.85rem}
</style></head><body>
<h1>Patch <code>%(source)s</code> <code>%(basefile)s</code>
 <span class="fmt">— intermediate format: %(format)s</span></h1>
<p class="hint">Edit the source text below to the desired final text; saving stores
 the <em>minimal</em> diff. An edit identical to the original removes the patch.
 Use <b>rot13</b> to obfuscate a redaction of personal data.</p>
<textarea id="text" spellcheck="false"></textarea>
<div class="row">
 <input type="text" id="desc" placeholder="Short description (e.g. 'Rättad OCR-felaktighet')">
 <label><input type="checkbox" id="rot13"> rot13 (redaction)</label>
 <button id="save">Save patch</button>
 <span id="msg"></span>
</div>
<script>
const q=new URLSearchParams(location.search), source=q.get("source"), basefile=q.get("basefile");
let base_sha="";
const msg=document.getElementById("msg");
function show(t,ok){msg.textContent=t;msg.className=ok?"ok":"err";}
async function load(){
 const r=await fetch("/api/v1/patch/document?source="+encodeURIComponent(source)+
   "&basefile="+encodeURIComponent(basefile),{credentials:"same-origin"});
 if(!r.ok){show("Load failed: "+r.status+" "+await r.text(),false);return;}
 const d=await r.json(); base_sha=d.base_sha;
 document.getElementById("text").value=d.text;
 if(d.description) document.getElementById("desc").value=d.description;
 document.getElementById("rot13").checked=d.is_rot13;
 show(d.has_patch?"Existing patch applied.":"No patch yet.",true);
}
document.getElementById("save").onclick=async()=>{
 const body={source,basefile,edited_text:document.getElementById("text").value,
   description:document.getElementById("desc").value,
   rot13:document.getElementById("rot13").checked,base_sha};
 const r=await fetch("/api/v1/patch/save",{method:"POST",credentials:"same-origin",
   headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
 if(!r.ok){show("Save failed: "+r.status+" "+await r.text(),false);return;}
 const d=await r.json();
 show(d.removed?"Patch removed and reparsed.":("Saved "+d.path+" ("+d.sha.slice(0,8)+"), reparsed."),true);
 load();
};
load();
</script></body></html>"""


@router.get("/edit", response_class=HTMLResponse)
def edit_page(source: str = Query(...), basefile: str = Query(...),
              editor: Editor = Depends(require_editor)):
    """A minimal self-contained editor page for one document's patch. Gated like
    every write route; the page's fetches carry the session cookie."""
    if source not in patchsource._INTERMEDIATE:
        raise HTTPException(400, "source %r is not patchable" % source)
    return _EDITOR_HTML % {"source": escape(source), "basefile": escape(basefile),
                           "format": escape(patchsource.format_label(source) or "")}
