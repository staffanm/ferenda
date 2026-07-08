"""Per-source *intermediate text* -- the representation a document's parser
reads and that a patch file (``lib.patch``) targets: plain text for SFS, the
innehåll HTML for DV, the Formex XML for eurlex. This is the one place that maps
a source to "the best format to patch", and the only patch-related module that
imports the verticals.

The split is deliberate: ``lib.patch`` is source-agnostic (lib never imports a
vertical), so the knowledge of *how to recover a source's pristine intermediate
text* -- which needs the verticals -- lives here, one level above lib. The
``mkpatch`` CLI (``build.py``) and the web editor (``api/patch.py``) both call
``intermediate`` / ``current`` from here so there is exactly one definition of
each source's patchable format.

``intermediate(source, basefile)`` -> ``(text, format_label)`` is the pristine,
pre-patch text an editor shows; ``current(source, basefile)`` is the same with
any existing patch already applied (what the editor seeds its textarea with, so
successive edits compound rather than fight an applied patch)."""

import json

from .eurlex.parse import content_file, formex_members
from .lib import layout, patch
from .lib.errors import SkipDocument
from .sfs.extract import extract_body


def _sfs_intermediate(basefile):
    """SFS's intermediate is the plain consolidated statute text -- straight from
    the beta-API JSON's ``forfattningstext`` when present, else recovered from
    the legacy SFST HTML exactly as the parser does (``sfs.extract.extract_body``)."""
    src = layout.sfs_source(basefile)
    if src.exists():
        text = (json.loads(src.read_text()).get("fulltext") or {}).get("forfattningstext")
        if text is None:
            raise SkipDocument("%s: no forfattningstext to patch" % basefile)
        return text.replace("\r", "")
    return extract_body(layout.sfs_sfst(basefile))


def _dv_intermediate(basefile):
    """DV's intermediate is the API record's innehåll HTML."""
    # lazy: build imports this module (via api.patch), so a top-level
    # `from .build import` would close a build->api.patch->patchsource->build
    # cycle. The one sanctioned in-function import here (rule:no-infunction-imports).
    from .build import dv_record  # noqa: PLC0415 -- breaks the build import cycle
    record = json.loads(dv_record(basefile).read_text())
    return record.get("innehall") or ""


def _eurlex_intermediate(basefile):
    """eurlex's intermediate is the main act's Formex XML (or the OJ HTML for the
    older acts that have no Formex manifestation)."""
    path, _lang, route = content_file(layout.eurlex_dir(basefile))
    if path is None:
        raise SkipDocument("%s: no content file to patch" % basefile)
    if route == "fmx4":
        return formex_members(path)[0][1].decode("utf-8")
    if route == "html":
        return path.read_bytes().decode("utf-8", "replace")
    raise ValueError("%s: the %s manifestation is not text-patchable "
                     "(PDF-only act)" % (basefile, route))


# source -> (pristine-text provider, human label of the format being patched).
# Adding a source here (its parser must call patch.apply at its intermediate
# choke point) makes it patchable from the CLI and the web editor.
_INTERMEDIATE = {
    "sfs": (_sfs_intermediate, "plain text"),
    "dv": (_dv_intermediate, "innehåll HTML"),
    "eurlex": (_eurlex_intermediate, "Formex XML"),
}


def patchable_sources():
    """The sources that currently support source-level patch files, sorted."""
    return sorted(_INTERMEDIATE)


def format_label(source):
    """The human label of `source`'s patchable intermediate format, or None."""
    entry = _INTERMEDIATE.get(source)
    return entry[1] if entry else None


def intermediate(source, basefile):
    """``(text, format_label)``: the pristine (pre-patch) intermediate text a
    patch for this document targets. Raises `ValueError` for a source with no
    text-patchable intermediate (the PDF-bodied ones: forarbete, foreskrift,
    remisser, avg's JO/ARN -- their fix stage is post-extraction, not wired)."""
    entry = _INTERMEDIATE.get(source)
    if entry is None:
        raise ValueError(
            "source %r has no text-patchable intermediate; patchable sources are %s"
            % (source, ", ".join(patchable_sources())))
    provider, label = entry
    return provider(basefile), label


def current(source, basefile):
    """The intermediate with any existing patch already applied -- the editor's
    seed text, so a new edit is a diff against the *effective* current text."""
    text, label = intermediate(source, basefile)
    return patch.patch_if_needed(source, basefile, text)[0], label
