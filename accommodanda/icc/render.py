"""ICC-sidan: the decision and its case metadata.

Registered as this source's page renderer in `build.SOURCE_RENDERERS`;
`render` is the `(art, site) -> str` the generate driver calls.
"""

from markupsafe import Markup

from ..lib import labels, tpl
from ..lib.page import Rail, Toc, doc_meta, page_context, render_node, render_toc

ENV = tpl.environment("accommodanda.icc")


def render(art, site):
    md = art.get("metadata", {})
    meta = [
        ("Domstol", md.get("publisher")),
        ("Mål", md.get("caseNumber")),
        ("Dokumentnummer", md.get("documentNumber")),
        ("Avgörandedatum", art.get("date")),
        ("Kammare", md.get("chamber")),
        ("Dokumenttyp", md.get("title")),
    ]
    toc = Toc()
    rail = Rail(site, art["uri"])
    structure = Markup("".join(
        render_node(node, site, art["uri"], toc, rail)
        for node in art.get("structure", [])))
    rail.add_document()
    lb = labels.document_labels("icc", art)
    return ENV.get_template("icc.html").render(page_context(
        lb.short_title or lb.short_id, "Internationella brottmålsdomstolen",
        doc_meta(meta, art.get("source_url")), toc=render_toc(toc),
        eyebrow=lb.short_id, island=rail.island(), structure=structure))
