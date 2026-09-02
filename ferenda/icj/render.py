"""ICJ-sidan: the decision and its case metadata.

Registered as this source's page renderer (the `render=` field of its
`build.py` registration);
`render` is the `(art, site) -> str` the generate driver calls.
"""

from ..lib import facets, labels, tpl
from ..lib.page import BANNERS, doc_meta, document_body, page_context, render_toc
from .model import KIND_SV

ENV = tpl.environment("ferenda.icj")
# the one table of reader-facing kind names, shared with the facet axis and the
# folkrätt landing (rule:second-use-goes-to-lib)
_KIND_LABEL = facets.scheme_kind_labels("icj")


# How many repaired words mean the text really was read off a scan. Measured
# over the whole corpus against the page-image test: the 138 scans repair a
# median of 19 words and the 117 typeset decisions a median of 0, but 27 of the
# typeset ones repair 1 to 8 -- a genuine typo or a ligature the text layer
# carries. At five the banner reaches 130 of the 138 scans and misfires on one
# typeset decision, which is the right way round for a warning.
SCAN_REPAIRS = 5


def scan_banner(md):
    """The banner for a decision whose text was read off the printed Reports by
    OCR, or '' for one that was not.

    `ocrRepairs` counts the words `icj.ocr` had to repair in this document, so
    the count is evidence about this text rather than a guess from its date --
    which would be wrong either way, since the July 2004 Wall opinion is a scan
    and the December 2004 judgment in the same volume is typeset. The Court
    itself says the printed version is the official one; the banner passes that
    on and links to the Court's own PDF."""
    return (BANNERS.icj_ocr_banner(md.get("pdfUrl"))
            if (md.get("ocrRepairs") or 0) >= SCAN_REPAIRS else "")


def render(art, site):
    md = art.get("metadata", {})
    meta = [
        ("Domstol", md.get("publisher")),
        ("Mål", md.get("caseNumber")),
        ("Avgörandedatum", art.get("avgorandedatum")),
        ("Dokumenttyp", _KIND_LABEL.get(KIND_SV.get(md.get("decisionType")))),
        ("Fråga", md.get("procedure")),
        ("Domstolens PDF", md.get("pdfUrl")),
    ]
    structure, toc, rail = document_body(art, site)
    lb = labels.document_labels("icj", art)
    banner = scan_banner(md)
    return ENV.get_template("icj.html").render(page_context(
        lb.short_title or lb.short_id, "Internationella domstolen",
        doc_meta(meta, art.get("source_url")), doc_uri=art["uri"],
        toc=render_toc(toc, lb.short_id),
        eyebrow=lb.short_id, island=rail.island(), structure=structure,
        banner=banner))
