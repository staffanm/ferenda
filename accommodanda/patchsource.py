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
from pathlib import Path

from .avg.download import (
    arn_pdf_path,
    imy_pdf_path,
    jk_html_path,
    jo_dnrs,
    jo_pdf_path,
    kkv_body_path,
)
from .avg.parse import kkv_html_text
from .eurlex.parse import content_file, formex_members
from .foreskrift.parse import body_path as fs_body_path
from .lib import compress, layout, patch, pdftext
from .lib.errors import SkipDocument
from .lib.util import document_extension, record_path
from .sfs.extract import extract_body


def _sfs_intermediate(basefile):
    """SFS's intermediate is the plain consolidated statute text -- straight from
    the beta-API JSON's ``forfattningstext`` when present, else recovered from
    the legacy SFST HTML exactly as the parser does (``sfs.extract.extract_body``)."""
    src = layout.sfs_source(basefile)
    if compress.exists(src):
        text = (json.loads(compress.read_text(src)).get("fulltext") or {}).get("forfattningstext")
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
    record = json.loads(compress.read_text(dv_record(basefile)))
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
        return compress.read_bytes(path).decode("utf-8", "replace")
    raise ValueError("%s: the %s manifestation is not text-patchable "
                     "(PDF-only act)" % (basefile, route))


def _pdf_xml(pdf_path):
    """A PDF's ``pdftohtml -xml`` output as text -- the intermediate the
    PDF-bodied sources patch (`lib.pdftext.pdf_pages` reads the same XML)."""
    if not Path(pdf_path).exists():
        raise SkipDocument("no body PDF at %s" % pdf_path)
    return pdftext.pdftohtml_xml(pdf_path).decode("utf-8", "replace")


def _forarbete_intermediate(basefile):
    """A förarbete's live-harvest body PDF as pdftohtml XML (the same first PDF
    parse reads). Frozen legacy-import bodies carry non-XML formats and are not
    patched at source level."""
    record = json.loads(compress.read_text(layout.fa_record(basefile)))
    if "legacy_files" in record:
        raise ValueError("%s: frozen legacy-import body is not text-patchable "
                         "at source level" % basefile)
    pdfs = [f for f in record.get("files", []) if f.lower().endswith(".pdf")]
    if not pdfs:
        raise SkipDocument("%s: no body PDF" % basefile)
    return _pdf_xml(layout.fa_dir(layout.FA_DOWNLOADED, record["type"],
                                  record["basefile"]) / pdfs[0])


def _foreskrift_intermediate(basefile):
    """A föreskrift's base-regulation PDF as pdftohtml XML (konsoliderade versions
    are separate documents, not patched through this key)."""
    fs = basefile.split("/", 1)[0]
    record = json.loads(
        compress.read_text(record_path(layout.FORESKRIFT_DOWNLOADED, fs, basefile)))
    reg_file = (record.get("files") or {}).get("regulation")
    if not reg_file:
        raise SkipDocument("%s: no base-regulation PDF" % basefile)
    return _pdf_xml(fs_body_path(layout.FORESKRIFT_DOWNLOADED, fs, reg_file))


def _avg_intermediate(basefile):
    """The intermediate an avg decision's parse actually reads, dispatched on
    the org exactly as the parser does: pdftohtml XML for the PDF-bodied organs,
    a JK decision's landing-page HTML, and for KKV whichever of the two its
    document happens to be (the diarium published a third of the corpus as
    windows-1252 HTML).

    An IMY decision assembled from several documents has no single intermediate
    -- parse threads one patch through every part, so a patch authored against
    one part would be attempted against the next and fail the document. Those
    are refused here rather than offered a patch that cannot hold."""
    org = basefile.split("/", 1)[0]
    record = json.loads(compress.read_text(record_path(layout.AVG_DOWNLOADED, org, basefile)))
    if org == "jk":
        return compress.read_text(jk_html_path(layout.AVG_DOWNLOADED, basefile))
    if org == "jo":
        dnrs = jo_dnrs(record.get("diary_number"))
        if not dnrs:
            raise SkipDocument("%s: jo record carries no diarienummer" % basefile)
        return _pdf_xml(jo_pdf_path(layout.AVG_DOWNLOADED, "jo/" + dnrs[0]))
    if org == "imy":
        parts = [d for d in record["delar"] if d["sprak"] == "sv"]
        if len(parts) != 1:
            raise SkipDocument(
                "%s: assembled from %d documents, which one patch cannot span"
                % (basefile, len(parts)))
        return _pdf_xml(imy_pdf_path(layout.AVG_DOWNLOADED, parts[0]["fil"]))
    if org == "kkv":
        if "dokument" not in record:
            raise SkipDocument("%s: the diarium published no document for it"
                               % basefile)
        path = kkv_body_path(layout.AVG_DOWNLOADED, record["dokument"]["fil"])
        data = compress.read_bytes(path)
        if document_extension(data) == ".pdf":
            return _pdf_xml(path)
        if document_extension(data) in (".doc", ".docx"):
            # read through POI, which has no editable text intermediate the way
            # pdftohtml XML and HTML do (the two Word cases in the corpus)
            raise SkipDocument("%s: a Word document has no patchable "
                               "intermediate" % basefile)
        return kkv_html_text(data)
    return _pdf_xml(arn_pdf_path(layout.AVG_DOWNLOADED, "arn/" + record["diarienummer"]))


def _remisser_intermediate(basefile):
    """A remissvar's answer PDF as pdftohtml XML."""
    case, org = basefile.split("/", 1)
    return _pdf_xml(layout.remisser_answer(case, org))


# source -> (pristine-text provider, human label of the format being patched).
# Adding a source here (its parser must call patch.apply / pass a patch_key at
# its intermediate choke point) makes it patchable from the CLI and web editor.
_INTERMEDIATE = {
    "sfs": (_sfs_intermediate, "plain text"),
    "dv": (_dv_intermediate, "innehåll HTML"),
    "eurlex": (_eurlex_intermediate, "Formex XML"),
    "forarbete": (_forarbete_intermediate, "pdftohtml XML"),
    "foreskrift": (_foreskrift_intermediate, "pdftohtml XML"),
    "avg": (_avg_intermediate,
            "pdftohtml XML (jk, and kkv's pre-2006 documents: HTML)"),
    "remisser": (_remisser_intermediate, "pdftohtml XML"),
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
