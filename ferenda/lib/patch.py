"""Patch files -- controlled, version-controlled fixes to a document's raw or
intermediate source content, applied at parse time before the text is tokenized
into the document model. It has two uses:

  * *correction* -- a downloaded source that carries a real error (an OCR slip,
    a broken table, a mis-encoded character the publisher never fixed) is
    corrected once, by hand, so every re-parse produces the right document
    without re-editing.
  * *redaction* -- personal data that must not appear (a named party in a court
    decision, a personnummer) is removed. Such a patch is stored *obfuscated*
    so the removed text is not itself plain-text googleable in the committed
    patch: letters rotate 13 and digits rotate 5 (ROT13 + ROT5, commonly
    "ROT18"). Plain ROT13 is not enough here and was the original bug -- it
    leaves every digit untouched, so a personnummer, an organisationsnummer or
    a telephone number, which is exactly what these patches remove, sat in the
    "obfuscated" file in the clear.

A patch is an ordinary unified diff (``difflib`` / ``diff -u`` format) against
the document's *best intermediate format* -- the representation the parser
actually reads and that a human can meaningfully edit: plain text for SFS, the
innehåll HTML for DV, the Formex XML for eurlex. It lives at
``patches/<source>/<relpath>.patch`` (or ``.rot18.patch``) in the git-backed
content repo, beside the commentaries and the annotation layers
(``layout.patch``). A single-line description rides on the first hunk's ``@@``
header; a multi-line one goes in a sibling ``.desc`` file.

This module is deliberately *mechanical* -- locate / read / apply / create a
patch over a text string it is handed. It knows nothing about any source (lib
never imports a vertical). Each vertical's parser calls
``patch_if_needed(source, basefile, text)`` at its intermediate-text choke
point, and declares the provider of its pristine intermediate text as
``Source.intermediate``; ``ferenda.patchsource`` reads that field back for the
``mkpatch`` CLI and the web editor.
"""

import io
import string
from difflib import unified_diff

from . import layout, util
from .patchit import PatchConflictError, PatchSet, PatchSyntaxError

PLAIN_SUFFIX = ".patch"
ROT18_SUFFIX = ".rot18.patch"

# ROT13 over the letters, ROT5 over the digits. Both halves are involutions --
# applying the table twice is the identity -- so one function both obfuscates
# and reads back, the way `codecs.decode(..., "rot13")` did before it.
_ROT18 = str.maketrans(
    string.ascii_lowercase + string.ascii_uppercase + string.digits,
    string.ascii_lowercase[13:] + string.ascii_lowercase[:13]
    + string.ascii_uppercase[13:] + string.ascii_uppercase[:13]
    + string.digits[5:] + string.digits[:5])


def obfuscate(text):
    """A redaction patch's stored form, and its own inverse. Deliberately not
    encryption: the point is only that the removed personal data is not
    plain-text searchable in the committed tree."""
    return text.translate(_ROT18)


class PatchError(Exception):
    """A patch exists for a document but could not be read or applied. A
    conflict is deliberately fatal: it means the source drifted out from under
    the patch, so the patch must be regenerated -- never silently skipped."""


# --------------------------------------------------------------------------
# locate + read
# --------------------------------------------------------------------------

def find_patch(source, basefile):
    """The patch file for a document and whether it is obfuscated:
    ``(path, is_obfuscated)``, or ``(None, False)`` if none exists. The
    obfuscated variant wins over a plain one (a redaction supersedes -- you
    would not keep both)."""
    obfuscated = layout.patch(source, basefile, ROT18_SUFFIX)
    if obfuscated.exists():
        return obfuscated, True
    plain = layout.patch(source, basefile, PLAIN_SUFFIX)
    if plain.exists():
        return plain, False
    return None, False


def has_patch(source, basefile):
    """True iff a patch (plain or obfuscated) exists -- the cheap guard a parser uses
    to keep the common no-patch path byte-identical."""
    return find_patch(source, basefile)[0] is not None


def _read_patch_text(path, is_obfuscated):
    text = path.read_text(encoding="utf-8")
    return obfuscate(text) if is_obfuscated else text


def _description(patchset, source, basefile):
    hunk = patchset.patches[0].hunks[0]
    if hunk.comment:
        return hunk.comment
    descpath = layout.patch(source, basefile, ".desc")
    if descpath.exists():
        return descpath.read_text(encoding="utf-8").strip()
    return None


def load_patchset(source, basefile):
    """Parse a document's patch into a ``patchit.PatchSet`` (de-obfuscated if
    needed), returning ``(patchset, description)`` -- or ``(None, None)`` if
    there is no patch. Raises `PatchError` on a malformed patch."""
    path, is_obfuscated = find_patch(source, basefile)
    if path is None:
        return None, None
    text = _read_patch_text(path, is_obfuscated)
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


def apply_if_fits(source, basefile, text):
    """`apply` for a *historical revision* of a document -- an archived SFS
    consolidation, where the same patch is offered to every superseded wording
    of the same statute. For a **correction** a conflict is the normal case,
    not a broken patch: an OCR slip or a lost blank line entered the source at
    some amendment and was corrected at another, so the patch fits the
    revisions in between and no others. Those revisions are published
    unpatched -- an uncorrected lydelse is still a true lydelse.

    A **redaction** is never skipped. Republishing the personal data a patch
    exists to remove, because a diff did not line up against an older wording,
    is precisely the harm; a conflict there stays fatal, and the versions
    stage records it as a skipped version rather than publishing one
    (`sfs.versions.build`). Which of the two a patch is, is exactly what
    `find_patch` already reports.

    Deliberately *not* the default: for the document a patch was authored
    against, a conflict always means the source drifted out from under it
    (`patch_if_needed`)."""
    path, is_obfuscated = find_patch(source, basefile)
    if path is None:
        return text
    if is_obfuscated:
        return apply(source, basefile, text)
    ps, _desc = load_patchset(source, basefile)
    lines = text.split("\n")
    try:
        ps.patches[0].adjust(lines)
        return "\n".join(ps.patches[0].merge(lines))
    except PatchConflictError:
        return text


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


def create_patch(source, basefile, original, edited, description="",
                 obfuscated=False):
    """Write the minimal patch turning `original` into `edited` to its canonical
    location and return that ``Path`` -- or ``None`` if there was no difference
    (in which case any existing patch for the document is removed). An
    `obfuscated` patch is stored ROT18-encoded (redactions); a multi-line
    `description` goes to a sibling ``.desc`` file. Exactly one variant is kept,
    so `find_patch` is unambiguous."""
    content = make_patch_text(original, edited, description)
    if not content:
        _remove_patch(source, basefile)
        return None
    suffix = ROT18_SUFFIX if obfuscated else PLAIN_SUFFIX
    path = layout.patch(source, basefile, suffix)
    other = layout.patch(source, basefile,
                         PLAIN_SUFFIX if obfuscated else ROT18_SUFFIX)
    if other.exists():
        other.unlink()
    util.write_atomic(path, obfuscate(content) if obfuscated else content)
    descpath = layout.patch(source, basefile, ".desc")
    if description and "\n" in description:
        util.write_atomic(descpath, description)
    elif descpath.exists():
        descpath.unlink()
    return path


def _remove_patch(source, basefile):
    """Delete any patch (plain, obfuscated and the ``.desc`` sidecar) for a
    document; return the list of paths removed."""
    removed = []
    for suffix in (PLAIN_SUFFIX, ROT18_SUFFIX, ".desc"):
        path = layout.patch(source, basefile, suffix)
        if path.exists():
            path.unlink()
            removed.append(path)
    return removed
