"""The per-user edit "cart" and the git commit engine behind the inline editor.

A logged-in user's edits accumulate as *drafts* -- one per region -- in a small
JSON store under ``DATA/.build/edits/<username>.json``, entirely separate from
the lagen-wiki working tree. Each user's cart is thus fully isolated -- they
draft, re-open and discard hunks without seeing or disturbing anyone else's --
and "how many hunks are in my cart" is just the length of that list. JSON (not
sqlite) because it is low-volume, human-inspectable, and matches the project's
"the file on disk is the source of truth, sqlite is derived" stance.

Checkout's stale-check -> write -> commit is **not** atomic across the whole
sequence: the ``base_sha`` conflict check (`commit`) guards against a region that
moved *before* the cart is applied, and git's ``index.lock`` serializes the
final commit, but two editors racing a checkout of the *same* region could both
pass the check and have the second overwrite the first between check and commit.
Under one uvicorn worker and hand-curated editors that race is not reachable in
practice; if concurrent editors ever are, hold a repo-level lock across the
whole ``commit`` body rather than trusting ``index.lock`` alone.

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
import os
import time
from pathlib import Path

from .. import config
from ..lib import git
from ..wiki import parse as wiki_parse
from . import editcontent

EDITS = config.DATA / ".build" / "edits"


# --------------------------------------------------------------------------
# the draft store
# --------------------------------------------------------------------------

def _store(username):
    return EDITS / (username + ".json")


def _load(username):
    path = _store(username)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def _save(username, drafts):
    EDITS.mkdir(parents=True, exist_ok=True)
    _store(username).write_text(json.dumps(drafts, ensure_ascii=False, indent=1),
                                encoding="utf-8")


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
    base = editcontent.read(region)
    drafts = [d for d in _load(username) if d["key"] != region.key]
    if new_text.rstrip("\n") != base["markdown"].rstrip("\n"):
        drafts.append({"key": region.key, "kind": region.kind, "ref": region.ref,
                       "anchor": region.anchor, "base_text": base["markdown"],
                       "base_sha": base["base_sha"],
                       "new_text": new_text.rstrip("\n") + "\n",
                       "updated": int(time.time())})
    _save(username, drafts)
    return len(drafts)


def discard(username, key):
    """Drop one draft from the cart; returns the resulting cart size."""
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
    """Apply the user's whole cart as one git commit authored by `editor`, clear
    the cart, and return `{sha, changes}` (`changes` drives the rebuild). Raises
    `Conflict` (nothing written) if any region moved under a draft, or ValueError
    on an empty cart / empty message."""
    drafts = _load(editor.username)
    if not drafts:
        raise ValueError("nothing to commit -- the cart is empty")
    if not message.strip():
        raise ValueError("a commit needs a message")

    stale = [d["key"] for d in drafts
             if editcontent.read(editcontent.region_of(d))["base_sha"]
             != d["base_sha"]]
    if stale:
        raise Conflict(stale)

    files, changes = [], []
    for d in drafts:
        info = editcontent.write(editcontent.region_of(d), d["new_text"])
        files.append(info["path"])
        changes.append({"kind": info["kind"], "basefile": info["basefile"]})
    # a brand-new commentary file changed the set of files on disk; the cached
    # frontmatter->path indexes must be rebuilt before the reparse reads them
    wiki_parse.kommentar_index.cache_clear()
    wiki_parse.begrepp_index.cache_clear()

    sha = _git_commit(files, editor, message)
    _save(editor.username, [])
    seen, deduped = set(), []
    for c in changes:                    # one rebuild per touched file, not per hunk
        key = (c["kind"], c["basefile"])
        if key not in seen:
            seen.add(key)
            deduped.append(c)
    return {"sha": sha, "changes": deduped}


def _git_commit(files, editor, message):
    """Stage exactly `files` and commit them authored *and* committed as `editor`
    -- both identities set, so `git log` attributes the web edit to the person
    who made it, indistinguishable from a local commit. Returns the new sha, or
    the unchanged HEAD when applying the drafts left the files byte-identical to
    disk (a rare no-op edit that would otherwise make `git commit` exit non-zero
    and 500)."""
    repo = config.WIKI_ROOT
    paths = [str(Path(f)) for f in files]
    git.run(repo, "add", "--", *paths)
    if not git.run(repo, "status", "--porcelain", "--", *paths, capture=True):
        return git.run(repo, "rev-parse", "HEAD", capture=True)   # nothing to commit
    env = {**os.environ,
           "GIT_AUTHOR_NAME": editor.name, "GIT_AUTHOR_EMAIL": editor.email,
           "GIT_COMMITTER_NAME": editor.name, "GIT_COMMITTER_EMAIL": editor.email}
    git.run(repo, "commit", "-m", message, "--", *paths, env=env)
    return git.run(repo, "rev-parse", "HEAD", capture=True)
