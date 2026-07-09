"""Parser for remiss (public referral) answers: one organisation's PDF ->
:class:`Remissvar`.

A remiss case (`Remiss`, harvested by `download.py`) accumulates one answer PDF
per organisation under `layout.REMISSER_DOWNLOADED`. This module reads one such
PDF through the shared font-aware extraction (`lib.pdftext`, the same
`pdf_pages` + `page_paragraphs` pipeline `avg/parse.py` uses for JO/ARN) and
flattens it to plain paragraph text -- no structural classification, since the
only downstream consumer is an LLM analysis reading prose, not a rendered page.

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

from ..lib import compress
from ..lib.pdftext import page_paragraphs, pdf_pages
from .model import Remiss, Remissvar, org_slug


def parse_record(basefile, root):
    """A remiss-answer basefile ("<case-slug>/<org-slug>") -> Remissvar. Reads
    the case record for its metadata + cross-refs and the org's answer PDF for
    the body text (both under one download `root`: ``<case>.json`` beside the
    ``<case>/`` PDF dir); asserts the pipeline invariant that a parse never runs
    ahead of the download (the matching instance exists and is marked downloaded)."""
    case_basefile, slug = basefile.split("/", 1)
    remiss = Remiss.from_dict(json.loads(
        compress.read_text(Path(root) / (case_basefile + ".json"))))
    inst = next((i for i in remiss.svar if org_slug(i.source_url) == slug),
               None)
    assert inst is not None, (
        "remiss %s has no answer instance matching org slug %r"
        % (case_basefile, slug))
    assert inst.downloaded, (
        "remiss %s answer %r has not been downloaded yet" % (case_basefile, slug))

    pdf_path = Path(root) / case_basefile / (slug + ".pdf")
    assert pdf_path.exists(), "no answer PDF at %s" % pdf_path

    paras = [p for pageno, lines in pdf_pages(str(pdf_path), ("remisser", basefile))
             for p in page_paragraphs(lines, None, pageno)]
    full_text = [p.text for p in paras if p.text]

    return Remissvar(
        basefile=basefile,
        case_basefile=case_basefile,
        organisation=inst.organisation,
        case_titel=remiss.titel,
        remitterat=remiss.remitterat,
        source_url=inst.source_url,
        full_text=full_text)
