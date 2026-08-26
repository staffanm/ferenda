"""The per-user edit "cart" and the git commit engine behind the inline editor.

A logged-in user's edits accumulate as *drafts* -- one per region -- in a small
JSON store under ``DATA/.build/edits/<username>.json``, entirely separate from
the lagen-wiki working tree. Each user's cart is thus fully isolated -- they
draft, re-open and discard hunks without seeing or disturbing anyone else's --
and "how many hunks are in my cart" is just the length of that list. JSON (not
sqlite) because it is low-volume, human-inspectable, and matches the project's
"the file on disk is the source of truth, sqlite is derived" stance.

The routes into this module are synchronous FastAPI endpoints, so one uvicorn
worker still serves several of them at once, in a thread pool. Two editors --
or one editor with two tabs -- therefore do reach the read-modify-write in the
draft store, and the stale-check -> write -> commit sequence, at the same time.
Both are serialized by `_LOCK`, one process-wide lock, and the store is written
through `util.write_atomic` so a reader never sees half a cart.
The lock covers the whole of `commit`, the ``index.lock`` step included: the
``base_sha`` conflict check must still hold when the write lands, which
``index.lock`` on its own does not give. It costs nothing -- a handful of
editors, and a checkout takes well under a second.

**Checkout** applies every draft to its markdown file and makes **one git commit
authored as that user** (`name`/`email` from ``config.EDITORS``) -- byte-for-byte
the history a `git clone` + edit + commit would produce, so future editors are
attributed exactly as if they had pushed. Before writing anything the commit
re-reads each region and aborts (no partial write) if one changed under a draft
since it was carted -- a `base_sha` mismatch is a conflict, surfaced for the user
to reconcile, never silently overwritten.

Regenerating the affected static pages is a separate step
(`build.rebuild_after_commit`), invoked by the router after a successful commit;
keeping it out of here leaves this module free of the build/render graph.
"""

import json
import threading
import time
from pathlib import Path

from .. import config
from ..lib import git, util
from ..wiki import parse as wiki_parse
from . import editcontent, graphicsedit

EDITS = config.DATA / ".build" / "edits"


# --------------------------------------------------------------------------
# kind dispatch: a draft's payload is whatever its kind's content module says
# --------------------------------------------------------------------------
# Every kind exposes the same three calls -- `region_of(draft)`,
# `read(region) -> {text, base_sha}`, `write(region, text) -> {kind, basefile,
# path}` -- so drafting, the conflict check and the write loop below carry any
# kind unchanged. Markdown regions are the original kind and keep
# `editcontent`'s own vocabulary ("markdown"); the adapter is the one place
# that translates, rather than renaming a field the editor JS also reads.
#
# Two places still know a kind by name, both deliberately: `commit` skips the
# wiki index invalidation for a graphics-only cart (there is no markdown to
# reindex), and `region_view` serves the markdown editor alone -- the crop
# reviewer has its own view (`api/graphics.py`), because a bbox has no textarea.


def _region_of(draft):
    """Rebuild the address a draft names, whichever kind it is."""
    if draft["kind"] == graphicsedit.KIND:
        return graphicsedit.region_of(draft)
    return editcontent.region_of(draft)


def _read(region):
    """The on-disk state a draft is based on, as `{text, base_sha}`."""
    if region.kind == graphicsedit.KIND:
        return graphicsedit.read(region)
    view = editcontent.read(region)
    return {"text": view["markdown"], "base_sha": view["base_sha"]}


def _write(region, new_text):
    """Apply one draft to its file; returns `{kind, basefile, path}`."""
    if region.kind == graphicsedit.KIND:
        return graphicsedit.write(region, new_text)
    return editcontent.write(region, new_text)

# One lock for both the draft store and checkout; see the module docstring.
_LOCK = threading.Lock()


# --------------------------------------------------------------------------
# the draft store
# --------------------------------------------------------------------------

def _store(username):
    return EDITS / (username + ".json")


def _load(username):
    path = _store(username)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def _save(username, drafts):
    """Write the cart atomically, so a concurrent `_load` reads either the old
    cart or the new one, never a truncated file."""
    util.write_atomic(_store(username),
                      json.dumps(drafts, ensure_ascii=False, indent=1))


def region_view(username, region):
    """The current markdown for a region, overlaid with the user's own pending
    draft if one is carted (so re-opening an edited hunk shows the unsaved text,
    not the on-disk version). `draft` flags which it is."""
    view = editcontent.read(region)
    draft = next((d for d in _load(username) if d["key"] == region.key), None)
    if draft:
        return {**view, "markdown": draft["new_text"], "draft": True}
    return {**view, "draft": False}


def upsert(username, region, new_text):
    """Add or replace the draft for `region`; returns the resulting cart size.
    An edit that matches the on-disk text is a no-op -- it *removes* any existing
    draft rather than carting a change that would commit nothing."""
    base = _read(region)
    with _LOCK:
        drafts = [d for d in _load(username) if d["key"] != region.key]
        if new_text.rstrip("\n") != base["text"].rstrip("\n"):
            drafts.append({"key": region.key, "kind": region.kind,
                           "ref": region.ref, "anchor": region.anchor,
                           "base_text": base["text"],
                           "base_sha": base["base_sha"],
                           "new_text": new_text.rstrip("\n") + "\n",
                           "updated": int(time.time())})
        _save(username, drafts)
        return len(drafts)


def discard(username, key):
    """Drop one draft from the cart; returns the resulting cart size."""
    with _LOCK:
        drafts = [d for d in _load(username) if d["key"] != key]
        _save(username, drafts)
        return len(drafts)


def cart(username):
    """The user's pending drafts, newest first -- what the checkout panel lists."""
    return sorted(_load(username), key=lambda d: d["updated"], reverse=True)


# --------------------------------------------------------------------------
# checkout: conflict check -> apply -> one attributed git commit
# --------------------------------------------------------------------------

class Conflict(Exception):
    """A carted region changed on disk since it was drafted; `keys` names the
    stale hunks. Raised instead of overwriting -- the router maps it to 409."""

    def __init__(self, keys):
        super().__init__("regions changed since drafted: %s" % ", ".join(keys))
        self.keys = keys


def commit(editor, message):
    """Apply the user's whole cart as one git commit authored by `editor`
    (`lib.git.commit_as`), clear the cart, and return `{sha, changes}`
    (`changes` drives the rebuild). Raises `Conflict` (nothing written) if any
    region moved under a draft, or ValueError on an empty cart / empty
    message."""
    # one lock for the whole sequence: the stale check must still hold when
    # the write lands, and the cart must clear with it (module docstring)
    with _LOCK:
        drafts = _load(editor.username)
        if not drafts:
            raise ValueError("nothing to commit -- the cart is empty")
        if not message.strip():
            raise ValueError("a commit needs a message")

        stale = [d["key"] for d in drafts
                 if _read(_region_of(d))["base_sha"] != d["base_sha"]]
        if stale:
            raise Conflict(stale)

        files, changes = [], []
        for d in drafts:
            info = _write(_region_of(d), d["new_text"])
            files.append(info["path"])
            changes.append({"kind": info["kind"], "basefile": info["basefile"]})
        # a brand-new commentary file changed the set of files on disk; the cached
        # frontmatter->path indexes must be rebuilt before the reparse reads them.
        # A graphics-only cart touches no markdown, so it skips the invalidation
        # rather than paying for a rebuild of indexes nothing read.
        if any(c["kind"] != graphicsedit.KIND for c in changes):
            wiki_parse.kommentar_index.cache_clear()
            wiki_parse.begrepp_index.cache_clear()

        sha = git.commit_as(config.WIKI_ROOT, [str(Path(f)) for f in files],
                            message, name=editor.name, email=editor.email)
        _save(editor.username, [])
        seen, deduped = set(), []
        for c in changes:                    # one rebuild per touched file, not per hunk
            key = (c["kind"], c["basefile"])
            if key not in seen:
                seen.add(key)
                deduped.append(c)
        return {"sha": sha, "changes": deduped}
