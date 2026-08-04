"""Parser for remiss (public referral) answers: one organisation's PDF ->
:class:`Remissvar`.

A remiss ärende (`Remiss`, harvested by `download.py`) accumulates one answer PDF
per organisation under `layout.REMISSER_DOWNLOADED`. This module reads one such
PDF through the shared font-aware extraction (`lib.pdftext`, the same
`pdf_pages` + `page_paragraphs` pipeline `avg/parse.py` uses for JO/ARN) and
flattens it to plain paragraph text -- no structural classification, since the
only downstream consumer is an LLM analysis reading prose, not a rendered page.

Answers arrive as whatever each organisation's registrator produced, so the
extraction can assume neither a text layer nor even a PDF: some are scans, some
carry text poppler treats as invisible, some have an unreadable cross-reference
table, and four are Word documents stored under a `.pdf` name. `_body_text`
dispatches on the file's magic bytes and `_pages` covers the rest -- hidden text
always included, ghostscript repair when poppler refuses the file, ocrmypdf when
a PDF still yields nothing.

Unlike JO/ARN, there is no fixed running-header string to strip: each
organisation's PDF carries its own letterhead, so no `page_paragraphs`
identifier applies -- pass `None` (verified: an organisation's own name is a
*bad* substitute, not an inert one -- it recurs constantly as ordinary
self-reference in body prose, "Ale kommun välkomnar...", "Kammarkollegiet
har...", and `page_paragraphs` strips a matching substring anywhere in a
line, not just where it forms a whole running-header line, so using it as the
identifier silently deleted the organisation's name out of real sentences)."""

import json
from pathlib import Path

from ..lib import compress, layout, poi
from ..lib.errors import SkipDocument
from ..lib.pdftext import (
    drop_footnotes,
    join_across_pages,
    page_paragraphs,
    pages_with_ocr,
    strip_addressing,
    strip_page_furniture,
)
from ..lib.util import sniff_extension
from .model import Remiss, Remissvar, org_slug

OCR_LANG = "swe"        # remissvar are Swedish; tesseract+swe is a hard dependency

# Answer PDFs that arrived whole (`Content-Length` bytes and all) but are
# permanently corrupt: no reader opens them, so there is no body to parse and
# never will be. Verified by re-downloading -- regeringen.se hands back the same
# broken bytes, so this is their stored copy, not our fetch, and the OCR
# fallback in `_pages` cannot rescue it either (ocrmypdf fails on the same
# malformed page tree poppler does).
#
# The sibling list `download.BROKEN_ANSWERS` covers the url that serves *no*
# usable response at all and so is never requested; these do respond, which is
# why they end up on disk and have to be refused here instead. One entry per
# answer basefile with the evidence.
BROKEN_PDFS = {
    # El-Kretsen's answer on the SOU 2021:26 (producentansvar) remiss. 67 492
    # bytes with an intact trailer whose xref points at offsets 80 585, 81 130
    # and 83 629 -- some 16 kB of object data is simply missing from the middle,
    # so every font and all but the first page object are unresolvable.
    # pdftotext yields 3 bytes of whitespace across the 4 pages poppler claims,
    # and ocrmypdf exits 15 on a null page ("'NoneType' object has no attribute
    # 'MediaBox'").
    "sou/2021:26/el-kretsen": "trasig PDF hos regeringen.se",
}


def _pages(pdf_path, patch_key):
    """(pageno, [Line]) per page, OCR'ing first when the PDF has no readable
    text -- `lib.pdftext.pages_with_ocr`, which this was extracted into when the
    avg vertical's KKV scans needed the same handling."""
    return pages_with_ocr(pdf_path, patch_key, OCR_LANG)


def _body_text(path, patch_key):
    """An answer's paragraph texts, read by what the file *is* rather than what
    it is stored as.

    Every answer is stored under a ``.pdf`` name because that is the shape the
    tree assumes, but a handful of registrators uploaded the Word document
    itself: 4 of the 79 980 answers carry OLE2 (.doc) or OOXML (.docx) magic,
    all from 2019-2020 (`dataspelsbranschen`, `transportstyrelsen`,
    `forvaltningsratten-i-stockholm`, `hela-sverige-ska-leva`). Handing those to
    poppler is what produced the five per-build `pdftohtml` failures.

    Word bodies go through `lib.poi`, the same HWPF/XWPF reader DV and förarbete
    use for their legacy Word corpora -- extraction, not a conversion to PDF,
    because the only thing downstream (an LLM reading prose) wants is the text,
    and a Word->PDF round trip would add a dependency to lose fidelity.

    Bytes that are neither raise: `sniff_extension` returns None for an HTML
    error page stored under a document name, and filing that as an answer's
    prose would be worse than failing (rule:fail-fast). This is a recorded
    per-document rejection of untrusted remote bytes, so it raises ValueError
    rather than asserting -- under -O an assert would vanish and the HTML would
    be handed to poppler, then to the xref repair, then to ocrmypdf, failing
    three subprocesses deep instead of here (rule:errors-drive-retry-use-raise)."""
    kind = sniff_extension(path)
    if kind in (".doc", ".docx"):
        return [p.text for p in poi.read(path) if p.text]
    if kind != ".pdf":
        raise ValueError("%s: stored as a document but its bytes are %s"
                         % (path, kind or "not a document format we read"))
    # no fixed identifier to name (see the module docstring), so the furniture is
    # found by its shape instead -- and the two halves of a sentence a page break
    # split are rejoined, which the strip alone does not do. Footnotes go too:
    # in a remissvar they are source references, never the sentence saying why
    # the organisation objects, and poppler splices note and marker into the
    # middle of the body sentence that cites them.
    pages = drop_footnotes(strip_page_furniture(_pages(path, patch_key)))
    return strip_addressing(join_across_pages(
        [[p.text for p in page_paragraphs(lines, None, pageno) if p.text]
         for pageno, lines in pages]))


def parse_record(basefile, root):
    """A remiss-answer basefile ("<typ>/<document id>/<org-slug>", e.g.
    ``sou/2026:14/kammarkollegiet``) -> Remissvar. Reads the ärende record for its
    metadata + cross-refs and the org's answer PDF for the body text (both under
    one download `root`: ``<typ>/<id-slug>.json`` beside the ``<typ>/<id-slug>/``
    PDF dir); asserts the pipeline invariant that a parse never runs ahead of the
    download (the matching instance exists and is marked downloaded).

    The org slug is the *last* segment, not the second: a document id may itself
    contain a slash (``pm/LI2026/01339``). Paths come from `layout.relpath`, the
    one rule the download tree and the artifact tree share, rebased onto `root`.

    A `BROKEN_PDFS` answer raises SkipDocument: the PDF is on disk and will never
    be readable, so the driver's empty-artifact marker is the honest outcome --
    the alternative is a per-document error every single build, forever."""
    if basefile in BROKEN_PDFS:
        raise SkipDocument("%s: %s" % (basefile, BROKEN_PDFS[basefile]))
    arende_basefile, slug = basefile.rsplit("/", 1)
    rel = layout.relpath("remisser", basefile)          # <typ>/<id-slug>/<org>
    remiss = Remiss.from_dict(json.loads(compress.read_text(
        Path(root) / rel.parent.parent / (rel.parent.name + ".json"))))
    inst = next((i for i in remiss.svar if org_slug(i.source_url) == slug),
               None)
    assert inst is not None, (
        "remiss %s has no answer instance matching org slug %r"
        % (arende_basefile, slug))
    assert inst.downloaded, (
        "remiss %s answer %r has not been downloaded yet" % (arende_basefile, slug))

    pdf_path = Path(root) / rel.parent / (slug + ".pdf")
    assert pdf_path.exists(), "no answer PDF at %s" % pdf_path

    full_text = _body_text(pdf_path, ("remisser", basefile))

    return Remissvar(
        basefile=basefile,
        arende_basefile=arende_basefile,
        organisation=inst.organisation,
        arende_titel=remiss.titel,
        remitterat=remiss.remitterat,
        source_url=inst.source_url,
        full_text=full_text)
