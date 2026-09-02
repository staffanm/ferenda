"""Per-source *intermediate text* -- the representation a document's parser
reads and that a patch file (``lib.patch``) targets: plain text for SFS, the
innehåll HTML for DV, the Formex XML for eurlex. This is the one place that
answers "what is the best format to patch, and how do I recover it", for
whatever source is asked.

It knows no source. Each source declares its own provider and format label as
the ``intermediate`` field of its registration (``<package>/source.py``,
``lib.stage.Source``), beside every other thing a verb asks of it; this module
reads the table back out of ``stage.SOURCES`` at call time. ``build.py`` fills
that registry at import, and the API's entry point is ``build.cmd_serve``, so
the registry is filled before any caller here runs.

The split is deliberate: ``lib.patch`` is source-agnostic (lib never imports a
vertical), so the knowledge of *how to recover a source's pristine intermediate
text* -- which is the source's own -- stays in the source. The ``mkpatch`` CLI
(``build.py``) and the web editor (``api/patch.py``) both call ``intermediate``
/ ``current`` from here so there is exactly one reader of each source's
patchable format.

``intermediate(source, basefile)`` -> ``(text, format_label)`` is the pristine,
pre-patch text an editor shows; ``current(source, basefile)`` is the same with
any existing patch already applied (what the editor seeds its textarea with, so
successive edits compound rather than fight an applied patch)."""

from .lib import patch
from .lib import stage as protocol


def patchable_sources():
    """The sources that currently support source-level patch files, sorted."""
    return sorted(name for name, source in protocol.SOURCES.items()
                  if source.intermediate)


def is_patchable(source):
    """Whether `source` has a text-patchable intermediate at all -- the check
    the CLI and the web editor make before offering to patch a document, so
    neither has to reach into the registry itself."""
    return bool(_entry(source))


def _entry(source):
    """`source`'s ``(provider, format label)`` pair, or None -- including for a
    name no source registered at all."""
    registered = protocol.SOURCES.get(source)
    return registered.intermediate if registered else None


def unpatchable_message(source):
    """The one wording for "this source cannot be patched", raised by
    `intermediate` and answered as a 400 by the web editor -- so a reader gets
    the same sentence and the same list wherever they hit it."""
    return ("source %r has no text-patchable intermediate; patchable sources "
            "are %s" % (source, ", ".join(patchable_sources())))


def format_label(source):
    """The human label of `source`'s patchable intermediate format, or None."""
    entry = _entry(source)
    return entry[1] if entry else None


def intermediate(source, basefile):
    """``(text, format_label)``: the pristine (pre-patch) intermediate text a
    patch for this document targets. Raises `ValueError` for a source with no
    text-patchable intermediate (the PDF-bodied ones: forarbete, foreskrift,
    remisser, avg's JO/ARN -- their fix stage is post-extraction, not wired)."""
    entry = _entry(source)
    if entry is None:
        raise ValueError(unpatchable_message(source))
    provider, label = entry
    return provider(basefile), label


def pristine_and_current(source, basefile):
    """``(pristine, current, format_label)``: the pre-patch text *and* the same
    text with any existing patch applied. What the web editor needs in one read
    -- recovering an intermediate can mean running pdftohtml over a whole
    document, so it is done once, not once per form."""
    text, label = intermediate(source, basefile)
    return text, patch.patch_if_needed(source, basefile, text)[0], label


def current(source, basefile):
    """The intermediate with any existing patch already applied -- the editor's
    seed text, so a new edit is a diff against the *effective* current text."""
    _pristine, text, label = pristine_and_current(source, basefile)
    return text, label
