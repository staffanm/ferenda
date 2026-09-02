"""ICC-sidan: the decision and its case metadata.

Registered as this source's page renderer (the `render=` field of its
`build.py` registration);
`render` is the `(art, site) -> str` the generate driver calls.
"""

from ..lib import labels, tpl
from ..lib.page import BANNERS, doc_meta, document_body, page_context, render_toc

ENV = tpl.environment("ferenda.icc")


def render(art, site):
    md = art.get("metadata", {})
    meta = [
        ("Domstol", md.get("publisher")),
        ("Mål", md.get("caseNumber")),
        ("Dokumentnummer", md.get("documentNumber")),
        ("Avgörandedatum", art.get("avgorandedatum")),
        ("Kammare", md.get("chamber")),
        ("Dokumenttyp", md.get("title")),
    ]
    structure, toc, rail = document_body(art, site)
    lb = labels.document_labels("icc", art)
    # Two records are identity and metadata only. (It was 119 until the text
    # extraction started asking poppler for the invisible OCR layer these scans
    # carry -- see `_blocks`.) What is left is one decision whose PDF the
    # downloader never fetched, and one that is a scan with no text layer at
    # all, whose per-page court stamp is enough text that `pages_with_ocr` does
    # not judge it empty and never reaches ocrmypdf. Say so rather than ship six
    # metadata rows that read as a page which failed to load.
    banner = "" if art.get("structure") else BANNERS.text_not_held(
        "avgörandet", art.get("source_url"), "Internationella brottmålsdomstolen")
    return ENV.get_template("icc.html").render(page_context(
        lb.short_title or lb.short_id, "Internationella brottmålsdomstolen",
        doc_meta(meta, art.get("source_url")), doc_uri=art["uri"], short_id=lb.short_id,
        description=site.snippet(art["uri"]),
        toc=render_toc(toc, lb.short_id),
        eyebrow=lb.short_id, island=rail.island(), structure=structure,
        banner=banner))
