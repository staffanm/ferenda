"""Parser for remiss (public referral) answers: one organisation's PDF ->
:class:`Remissvar`.

A remiss ärende (`Remiss`, harvested by `download.py`) accumulates one answer PDF
per organisation under `layout.REMISSER_DOWNLOADED`. This module reads one such
PDF through the shared font-aware extraction (`lib.pdftext`, the same
`pdf_pages` + `page_paragraphs` pipeline `avg/parse.py` uses for JO/ARN) and
flattens it to plain paragraph text -- no structural classification, since the
only downstream consumer is an LLM analysis reading prose, not a rendered page.

Answers arrive as whatever each organisation's registrator produced, so the
extraction cannot assume a text layer: some are scans, and some carry text
poppler treats as invisible. `_pages` handles both -- hidden text always
included, ocrmypdf when a PDF still yields nothing.

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

from ..lib import compress, layout
from ..lib.pdftext import ocr_pdf, page_paragraphs, pdf_pages
from .model import Remiss, Remissvar, org_slug

OCR_LANG = "swe"        # remissvar are Swedish; tesseract+swe is a hard dependency


def _pages(pdf_path, patch_key):
    """(pageno, [Line]) per page, OCR'ing first when the PDF has no readable text.

    Two distinct failures look identical from here, and one fallback covers both
    (the same one `eurlex.parse_pdf.pdf_lines` uses): a scanned answer with no
    text layer at all, and one whose text layer poppler renders invisible --
    real among remissvar, and dropped entirely without ``hidden=True``. So every
    extraction asks for hidden text, and a PDF that still yields nothing goes
    through ocrmypdf.

    Emptiness is judged on *lines*, before `page_paragraphs`: a PDF that
    genuinely holds only a letterhead would OCR pointlessly if judged on the
    paragraphs left after stripping, and OCR is the expensive path."""
    pages = list(pdf_pages(str(pdf_path), patch_key, hidden=True))
    if any(lines for _pageno, lines in pages):
        return pages
    return list(pdf_pages(str(ocr_pdf(pdf_path, OCR_LANG)), patch_key, hidden=True))


def parse_record(basefile, root):
    """A remiss-answer basefile ("<typ>/<document id>/<org-slug>", e.g.
    ``sou/2026:14/kammarkollegiet``) -> Remissvar. Reads the ärende record for its
    metadata + cross-refs and the org's answer PDF for the body text (both under
    one download `root`: ``<typ>/<id-slug>.json`` beside the ``<typ>/<id-slug>/``
    PDF dir); asserts the pipeline invariant that a parse never runs ahead of the
    download (the matching instance exists and is marked downloaded).

    The org slug is the *last* segment, not the second: a document id may itself
    contain a slash (``pm/LI2026/01339``). Paths come from `layout.relpath`, the
    one rule the download tree and the artifact tree share, rebased onto `root`."""
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

    paras = [p for pageno, lines in _pages(pdf_path, ("remisser", basefile))
             for p in page_paragraphs(lines, None, pageno)]
    full_text = [p.text for p in paras if p.text]

    return Remissvar(
        basefile=basefile,
        arende_basefile=arende_basefile,
        organisation=inst.organisation,
        arende_titel=remiss.titel,
        remitterat=remiss.remitterat,
        source_url=inst.source_url,
        full_text=full_text)
