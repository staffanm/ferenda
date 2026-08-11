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
from .dv.parse import record_intermediate as dv_record_intermediate
from .edpb.download import pdf_path as edpb_pdf_path
from .eurlex.parse import content_file, formex_intermediate, formex_members
from .foreskrift.parse import body_path as fs_body_path
from .lib import compress, layout, markup, patch, pdftext
from .lib.errors import SkipDocument
from .lib.util import document_extension, record_path
from .rs.agencies import BY_ORG
from .rs.download import body_path as rs_body_path
from .sfs.extract import extract_body


def _sfs_intermediate(basefile):
    """SFS's intermediate is the plain consolidated statute text -- straight from
    the beta-API JSON's ``forfattningstext`` when present, else recovered from
    the legacy SFST HTML exactly as the parser does (``sfs.extract.extract_body``)."""
    src = layout.sfs_source(basefile)
    if compress.exists(src):
        text = (compress.read_json(src).get("fulltext") or {}).get("forfattningstext")
        if text is None:
            raise SkipDocument("%s: no forfattningstext to patch" % basefile)
        return text.replace("\r", "")
    return extract_body(layout.sfs_sfst(basefile))


def _dv_intermediate(basefile):
    """DV's intermediate is whichever source its parse reads, and dv has three:
    the whole API record for a case the domstol API publishes -- body *and*
    metadata, so a redaction reaches the målnummer as well as the running text
    (`dv.parse.record_intermediate`); the court's own PDF, as pdftohtml XML, for
    a verdict published before its referat (no innehåll at all); and the frozen
    notis XML for a legacy-only case. A legacy Word referat is read through POI
    and has no editable text form to diff against, the same as avg's two Word
    documents."""
    # lazy: build imports this module (via api.patch), so a top-level
    # `from .build import` would close a build->api.patch->patchsource->build
    # cycle. The one sanctioned in-function import here (rule:no-infunction-imports).
    from .build import (  # noqa: PLC0415 -- breaks the build import cycle
        dv_record,
        dv_verdict_pdf,
    )
    path = dv_record(basefile)
    if path.suffix.lower() == ".xml":
        return path.read_text()
    if path.suffix.lower() != ".json":
        raise SkipDocument("%s: a legacy Word referat (%s) has no patchable "
                           "intermediate" % (basefile, path.name))
    record = compress.read_json(path)
    if not record.get("innehall"):
        # a verdict published before its referat has no innehåll at all -- parse
        # reads its body from the court's own PDF, so that PDF's pdftohtml XML
        # is what a patch targets (the PDF-bodied sources' intermediate). Some
        # ~290 cases have neither, which parse tolerates (they are metadata-only
        # entries): there is nothing to diff against.
        pdf = dv_verdict_pdf(basefile, record)
        if pdf is None:
            raise SkipDocument("%s: the record carries neither innehåll nor a "
                               "verdict PDF" % basefile)
        return _pdf_xml(pdf)
    return dv_record_intermediate(record)


def _eurlex_intermediate(basefile):
    """eurlex's intermediate is the main act's Formex XML (or the OJ HTML for the
    older acts that have no Formex manifestation), normalised to one element per
    line -- both manifestations ship as a single line, and `eurlex.parse`
    normalises identically before applying the patch."""
    path, _lang, route = content_file(layout.eurlex_dir(basefile))
    if path is None:
        raise SkipDocument("%s: no content file to patch" % basefile)
    if route == "fmx4":
        return formex_intermediate(formex_members(path)[0][1])
    if route == "html":
        return markup.block_lines(compress.read_bytes(path).decode("utf-8", "replace"))
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
    record = compress.read_json(layout.fa_record(basefile))
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
    record = compress.read_json(record_path(layout.AVG_DOWNLOADED, org, basefile))
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


def _edpb_intermediate(basefile):
    """An EDPB vägledning's PDF as pdftohtml XML. Every record names a document
    -- the harvest writes none without one -- so an absent file is a broken
    store, not a document-less entry."""
    return _pdf_xml(edpb_pdf_path(layout.EDPB_DOWNLOADED, basefile))


def _rs_intermediate(basefile):
    """A rättsligt ställningstagande's PDF as pdftohtml XML -- or, for the
    agency that publishes web pages rather than PDFs (Skatteverket), the page
    itself, normalised to one block element per line the way `rs.parse` does
    before applying the patch. Every agency publishes a document, except for the
    repealed Konkurrensverket entries that keep only their förteckning row --
    and those have no text to patch."""
    path = rs_body_path(layout.RS_DOWNLOADED, basefile)
    if not compress.exists(path):
        raise SkipDocument("%s: the agency published no document for it"
                           % basefile)
    if BY_ORG[basefile.split("/", 1)[0]].page_body:
        return markup.block_lines(compress.read_text(path))
    return _pdf_xml(path)


def _remisser_intermediate(basefile):
    """A remissvar's answer PDF as pdftohtml XML."""
    case, org = basefile.split("/", 1)
    return _pdf_xml(layout.remisser_answer(case, org))


# source -> (pristine-text provider, human label of the format being patched).
# Adding a source here (its parser must call patch.apply / pass a patch_key at
# its intermediate choke point) makes it patchable from the CLI and web editor.
_INTERMEDIATE = {
    "sfs": (_sfs_intermediate, "plain text"),
    "dv": (_dv_intermediate, "API record JSON (opublicerad dom: pdftohtml XML; "
                             "legacy notisfall: intermediate XML)"),
    "eurlex": (_eurlex_intermediate, "Formex XML (pre-Formex acts: the OJ HTML)"),
    "forarbete": (_forarbete_intermediate, "pdftohtml XML"),
    "foreskrift": (_foreskrift_intermediate, "pdftohtml XML"),
    "avg": (_avg_intermediate,
            "pdftohtml XML (jk, and kkv's pre-2006 documents: HTML)"),
    "rs": (_rs_intermediate,
           "pdftohtml XML (skv: the ställningstagande's own web page)"),
    "edpb": (_edpb_intermediate, "pdftohtml XML"),
    "remisser": (_remisser_intermediate, "pdftohtml XML"),
}


def patchable_sources():
    """The sources that currently support source-level patch files, sorted."""
    return sorted(_INTERMEDIATE)


def is_patchable(source):
    """Whether `source` has a text-patchable intermediate at all -- the check
    the CLI and the web editor make before offering to patch a document, so
    neither has to reach into the table itself."""
    return source in _INTERMEDIATE


def unpatchable_message(source):
    """The one wording for "this source cannot be patched", raised by
    `intermediate` and answered as a 400 by the web editor -- so a reader gets
    the same sentence and the same list wherever they hit it."""
    return ("source %r has no text-patchable intermediate; patchable sources "
            "are %s" % (source, ", ".join(patchable_sources())))


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
