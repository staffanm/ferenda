"""Patch files -- controlled, version-controlled fixes to a document's raw or
intermediate source content, applied at parse time before the text is tokenized
into the document model. Two uses, both carried over from the old pipeline:

  * *correction* -- a downloaded source that carries a real error (an OCR slip,
    a broken table, a mis-encoded character the publisher never fixed) is
    corrected once, by hand, so every re-parse produces the right document
    without re-editing.
  * *redaction* -- personal data that must not appear (a named party in a court
    decision, a personnummer) is removed. Such a patch is stored
    rot13-obfuscated so the removed text is not itself plain-text googleable in
    the committed patch.

A patch is an ordinary unified diff (``difflib`` / ``diff -u`` format) against
the document's *best intermediate format* -- the representation the parser
actually reads and that a human can meaningfully edit: plain text for SFS, the
innehåll HTML for DV, the Formex XML for eurlex. It lives at
``patches/<source>/<relpath>.patch`` (or ``.rot13.patch``), committed with the
pipeline code (``layout.patch``). A single-line description rides on the first
hunk's ``@@`` header; a multi-line one goes in a sibling ``.desc`` file.

This module is deliberately *mechanical* -- locate / read / apply / create a
patch over a text string it is handed. It knows nothing about any source (lib
never imports a vertical). Each vertical's parser calls
``patch_if_needed(source, basefile, text)`` at its intermediate-text choke
point; ``accommodanda.patchsource`` (which *may* import the verticals) knows how
to produce the pristine intermediate text for the ``mkpatch`` CLI and the web
editor.
"""

import codecs
import io
from difflib import unified_diff

from . import layout, util
from .patchit import PatchConflictError, PatchSet, PatchSyntaxError

PLAIN_SUFFIX = ".patch"
ROT13_SUFFIX = ".rot13.patch"


class PatchError(Exception):
    """A patch exists for a document but could not be read or applied. A
    conflict is deliberately fatal: it means the source drifted out from under
    the patch, so the patch must be regenerated -- never silently skipped."""


# --------------------------------------------------------------------------
# locate + read
# --------------------------------------------------------------------------

def find_patch(source, basefile):
    """The patch file for a document and whether it is rot13-obfuscated:
    ``(path, is_rot13)``, or ``(None, False)`` if none exists. The rot13 variant
    wins over a plain one (a redaction supersedes -- you would not keep both)."""
    rot13 = layout.patch(source, basefile, ROT13_SUFFIX)
    if rot13.exists():
        return rot13, True
    plain = layout.patch(source, basefile, PLAIN_SUFFIX)
    if plain.exists():
        return plain, False
    return None, False


def has_patch(source, basefile):
    """True iff a patch (plain or rot13) exists -- the cheap guard a parser uses
    to keep the common no-patch path byte-identical."""
    return find_patch(source, basefile)[0] is not None


def _read_patch_text(path, is_rot13):
    text = path.read_text(encoding="utf-8")
    if is_rot13:
        text = codecs.decode(text, "rot13")
    return text


def _description(patchset, source, basefile):
    hunk = patchset.patches[0].hunks[0]
    if hunk.comment:
        return hunk.comment
    descpath = layout.patch(source, basefile, ".desc")
    if descpath.exists():
        return descpath.read_text(encoding="utf-8").strip()
    return None


def load_patchset(source, basefile):
    """Parse a document's patch into a ``patchit.PatchSet`` (rot13-decoded if
    needed), returning ``(patchset, description)`` -- or ``(None, None)`` if
    there is no patch. Raises `PatchError` on a malformed patch."""
    path, is_rot13 = find_patch(source, basefile)
    if path is None:
        return None, None
    text = _read_patch_text(path, is_rot13)
    try:
        ps = PatchSet.from_stream(io.StringIO(text))
    except PatchSyntaxError as e:
        raise PatchError("%s/%s: malformed patch %s: %s"
                         % (source, basefile, path, e)) from e
    if len(ps.patches) != 1:
        raise PatchError("%s/%s: expected exactly one file-patch, got %d"
                         % (source, basefile, len(ps.patches)))
    return ps, _description(ps, source, basefile)


# --------------------------------------------------------------------------
# apply
# --------------------------------------------------------------------------

def patch_if_needed(source, basefile, text):
    """Apply the document's patch to `text`, returning ``(patched_text,
    description)``. With no patch it returns ``(text, None)`` unchanged, so a
    parser may call it unconditionally. Raises `PatchError` if a patch exists
    but does not apply."""
    ps, desc = load_patchset(source, basefile)
    if ps is None:
        return text, None
    lines = text.split("\n")
    try:
        ps.patches[0].adjust(lines)
        merged = list(ps.patches[0].merge(lines))
    except PatchConflictError as e:
        raise PatchError("%s/%s: patch does not apply (source drifted?): %s"
                         % (source, basefile, e)) from e
    return "\n".join(merged), desc


def apply(source, basefile, text):
    """`patch_if_needed` keeping only the patched text -- the common parser call
    at a source's intermediate-text choke point."""
    return patch_if_needed(source, basefile, text)[0]


# --------------------------------------------------------------------------
# create (the mkpatch CLI + the web editor)
# --------------------------------------------------------------------------

def _annotate(diff_lines, description):
    """Ride a single-line description on the first hunk's ``@@`` header -- the
    form patchit reads back as the hunk comment (mirrors the old mkpatch)."""
    out, done = [], False
    for line in diff_lines:
        if not done and line.startswith("@@ ") and line.rstrip("\n").endswith("@@"):
            line = line.rstrip("\n") + " " + description + "\n"
            done = True
        out.append(line)
    return out


def make_patch_text(original, edited, description=""):
    """The minimal unified diff turning `original` into `edited`, or ``""`` if
    they are identical. Lines are canonicalised by splitting on ``"\\n"`` -- the
    exact inverse of what `patch_if_needed` does when applying -- so a patch
    round-trips. A single-line `description` rides on the first hunk."""
    orig = [line + "\n" for line in original.split("\n")]
    new = [line + "\n" for line in edited.split("\n")]
    diff = list(unified_diff(orig, new, fromfile="original", tofile="edited"))
    if not diff:
        return ""
    if description and "\n" not in description:
        diff = _annotate(diff, description)
    return "".join(diff)


def create_patch(source, basefile, original, edited, description="", rot13=False):
    """Write the minimal patch turning `original` into `edited` to its canonical
    location and return that ``Path`` -- or ``None`` if there was no difference
    (in which case any existing patch for the document is removed). A `rot13`
    patch is stored obfuscated (redactions); a multi-line `description` goes to a
    sibling ``.desc`` file. Exactly one variant is kept, so `find_patch` is
    unambiguous."""
    content = make_patch_text(original, edited, description)
    if not content:
        remove_patch(source, basefile)
        return None
    suffix = ROT13_SUFFIX if rot13 else PLAIN_SUFFIX
    path = layout.patch(source, basefile, suffix)
    other = layout.patch(source, basefile, PLAIN_SUFFIX if rot13 else ROT13_SUFFIX)
    if other.exists():
        other.unlink()
    util.write_atomic(path, codecs.encode(content, "rot13") if rot13 else content)
    descpath = layout.patch(source, basefile, ".desc")
    if description and "\n" in description:
        util.write_atomic(descpath, description)
    elif descpath.exists():
        descpath.unlink()
    return path


def remove_patch(source, basefile):
    """Delete any patch (plain, rot13 and the ``.desc`` sidecar) for a document;
    return the list of paths removed."""
    removed = []
    for suffix in (PLAIN_SUFFIX, ROT13_SUFFIX, ".desc"):
        path = layout.patch(source, basefile, suffix)
        if path.exists():
            path.unlink()
            removed.append(path)
    return removed
