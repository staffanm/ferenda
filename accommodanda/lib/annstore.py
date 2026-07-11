"""The store for LLM-authored layers -- the `.ann`/`.corr` files the ai-*
actions write (eurlex/kommentar ai-annotate, remisser ai-analyze, sfs
ai-correspond).

These layers live in the git-backed content repo (``WIKI_ROOT/ann/``), *not*
the artifact tree, because their lifecycle is not an artifact's: an LLM output
is derived like an artifact but expensive to regenerate, and it may be
hand-verified and hand-edited after the fact -- at which point it is curated
data, exactly as irreplaceable as the hand-written commentary markdown,
regardless of who wrote the first draft. The artifact tree's contract is
"wipeable, rebuildable, never hand-touched"; a hand-edited layer there is one
re-run away from silent loss. Git gives both states what they need: the
initial commit of a file is the raw model output, later commits are the human
corrections, and a regeneration of an unverified layer shows up as a
reviewable diff.

Every layer is an envelope: a reserved top-level ``meta`` key
(status/model/generated/inputs) beside the payload's own keys, so readers keep
indexing the payload as before and the file's state lives *in the file*, not
in which directory it happens to sit in.

  * ``status: "generated"`` -- an expensive cache; a re-run of the ai-* action
    overwrites it freely (the git diff is the review).
  * ``status: "verified"`` -- a human has checked (and possibly edited) it.
    `guard` refuses to regenerate it without --force; verification is flipping
    the field by hand in an editor, captured in git history.
  * stale is *derived*, never stored: a layer whose recorded input hashes no
    longer match the current artifacts has drifted and needs human re-review
    (`lagen ann status` reports it) -- it is never silently regenerated.

The store mirrors the artifact tree's layout (``ann/<source-dir>/<relpath>``),
so a layer's path is derivable from the same (source, basefile) identity and a
migration from the old next-to-the-artifact location is a plain move.
"""

import hashlib
import json
from datetime import date
from pathlib import Path

from .. import config
from . import compress, layout, util

ROOT = config.WIKI_ROOT / "ann"

GENERATED = "generated"
VERIFIED = "verified"


def tree(source):
    """The store subtree of one source: ``WIKI_ROOT/ann/<source-dir>``."""
    return ROOT / layout.SOURCE_DIR[source]


def for_artifact(art_path, suffix=".ann"):
    """The store path mirroring one artifact-tree path -- the artifact's
    ARTIFACT-relative location replayed under ROOT, `.json` swapped for
    `suffix`. Raises if `art_path` is not under the artifact tree: a foreign
    path has no mirrored layer and must surface, not map somewhere wrong."""
    rel = Path(art_path).relative_to(layout.ARTIFACT)
    return ROOT / rel.with_suffix(suffix)


def path(source, basefile, suffix=".ann"):
    """The store path of one document's layer, from its (source, basefile)
    identity -- the ai-* writers' and identity-keyed readers' entry point."""
    return for_artifact(layout.artifact(source, basefile), suffix)


def read_meta(p):
    """The envelope ``meta`` of a layer file, with the store's defaults and
    validation applied -- the one home of the status policy. A pre-envelope
    file (no ``meta`` -- e.g. one migrated from the old artifact-tree location)
    counts as VERIFIED: its provenance is unknown, and an expensive, possibly
    hand-edited output must never be clobbered on a guess. The status value is
    hand-edited data, so an unrecognized one raises (ValueError, not assert:
    a typo like "verifed" silently *disarming* the guard is exactly the loss
    this store exists to prevent, and -O strips asserts) rather than falling
    through to regenerable."""
    meta = json.loads(Path(p).read_text()).get("meta", {})
    st = meta.get("status", VERIFIED)
    if st not in (GENERATED, VERIFIED):
        raise ValueError("%s has unknown meta.status %r (expected %r or %r)"
                         % (p, st, GENERATED, VERIFIED))
    return {**meta, "status": st}


def status(p):
    """The status of a layer file (`read_meta`'s policy), or None if the file
    does not exist."""
    p = Path(p)
    return read_meta(p)["status"] if p.exists() else None


def guard(p, force=False):
    """Refuse to regenerate a verified layer unless `force`. Called by every
    ai-* action *before* the LLM spend and again inside `write` (the choke
    point). Raises ValueError (not assert, which -O strips; not sys.exit --
    process-exit policy is the CLI driver's, and the refusal must stay
    catchable for a future non-CLI caller): protecting hand curation from
    silent loss is load-bearing (rule:errors-drive-retry-use-raise)."""
    if not force and status(p) == VERIFIED:
        raise ValueError(
            "%s is verified (hand-curated) -- regenerating would discard the "
            "curation. Edit it by hand, or pass --force to overwrite and "
            "re-review." % p)


def input_hash(data):
    """The hash a layer records for one input (sha256 hex of its bytes)."""
    return hashlib.sha256(data).hexdigest()


def artifact_input(source, basefile):
    """One ``inputs`` entry for a parsed artifact the LLM read:
    ``{"artifact:<source>/<basefile>": <hash>}`` -- self-describing, so
    `drifted` can recompute it later without per-source knowledge."""
    return {"artifact:%s/%s" % (source, basefile): input_hash(
        compress.read_bytes(layout.artifact(source, basefile)))}


def download_input(relpath):
    """One ``inputs`` entry for a *downloaded* file a deriver read directly
    (the prop PDFs sfs table-correspond extracts its tables from -- often a
    bilaga volume the artifact parse never covers, so the artifact hash alone
    would miss their drift), keyed by its DOWNLOADED-relative path."""
    return {"download:%s" % relpath: input_hash(
        compress.read_bytes(layout.DOWNLOADED / relpath))}


def wiki_input(p, wiki_root):
    """One ``inputs`` entry for a content-repo file the LLM pass read (the
    kommentar markdown with its `guidance:` frontmatter), keyed by its
    WIKI_ROOT-relative path."""
    p = Path(p)
    return {"wiki:%s" % p.relative_to(wiki_root): input_hash(p.read_bytes())}


def _current_hash(label):
    """Recompute one recorded input's hash from the label's own description;
    None when the input no longer exists (itself a drift)."""
    kind, _, rest = label.partition(":")
    if kind == "artifact":
        source, _, basefile = rest.partition("/")
        p = layout.artifact(source, basefile)
        return input_hash(compress.read_bytes(p)) if compress.exists(p) else None
    if kind == "download":
        p = layout.DOWNLOADED / rest
        return input_hash(compress.read_bytes(p)) if compress.exists(p) else None
    if kind == "wiki":
        p = config.WIKI_ROOT / rest
        return input_hash(p.read_bytes()) if p.exists() else None
    raise ValueError("unknown input label %r" % label)


def drifted(inputs):
    """The recorded inputs whose current hash no longer matches -- the layer
    was authored against data that has since changed (or vanished). For a
    verified layer this means *stale*: queue it for human re-review; never
    regenerate it mechanically."""
    return sorted(label for label, recorded in inputs.items()
                  if _current_hash(label) != recorded)


def write(p, payload, inputs, force=False, model=None):
    """Write one authored layer as a fresh ``generated`` envelope: ``meta``
    (status, model, authored date, input hashes) beside the payload's own
    keys. ``model`` defaults to the configured LLM (the usual author); a
    mechanical deriver (sfs table-correspond) passes its own marker instead.
    Guards against clobbering a verified layer (the writer also guards before
    the LLM spend; this is the choke point) and writes atomically -- a costly
    one-shot output must never survive truncated."""
    assert "meta" not in payload, "payload must not carry its own `meta` key"
    guard(p, force)
    envelope = {"meta": {"status": GENERATED, "model": model or config.LLM_MODEL,
                         "generated": date.today().isoformat(),
                         "inputs": inputs}, **payload}
    util.write_atomic(p, json.dumps(envelope, ensure_ascii=False, indent=2))
    return p


def entries():
    """Every layer in the store, sorted -- the iteration companion to `path`,
    for `lagen ann status`."""
    return sorted(p for pattern in ("*.ann", "*.corr") for p in ROOT.rglob(pattern))
