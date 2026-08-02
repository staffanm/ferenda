"""Europadomstolssidan: the judgment body and its article metadata.

Registered as this source's page renderer in `build.SOURCE_RENDERERS`;
`render` is the `(art, site) -> str` the generate driver calls.
"""

from markupsafe import Markup

from ..lib import labels, tpl
from ..lib.page import (
    Rail,
    Toc,
    doc_meta,
    page_context,
    ref_list,
    render_node,
    render_toc,
)

ENV = tpl.environment("accommodanda.hudoc")


def render(art, site):
    md = art.get("metadata", {})
    lb = labels.document_labels("hudoc", art)
    # the application number is the eyebrow now, so it needs no dl row of its own
    meta = [
        ("Domstol", md.get("publisher")),
        ("Avgörandedatum", art.get("date")),
        ("ECLI", art.get("ecli")),
        ("Artiklar", ", ".join(md.get("articles", [])) or None),
    ]
    toc = Toc()
    rail = Rail(site, art["uri"])
    structure = Markup("".join(
        render_node(node, site, art["uri"], toc, rail)
        for node in art.get("structure", [])))
    rail.add_document()
    return ENV.get_template("hudoc.html").render(page_context(
        lb.short_title or art.get("itemid"), "Europadomstolen",
        doc_meta(meta, art.get("source_url")), toc=render_toc(toc),
        eyebrow=lb.short_id,
        summary_text=("; ".join(md["conclusions"])
                      if md.get("conclusions") else None),
        island=rail.island(),
        refs=Markup(ref_list(site, "Berörda konventionsartiklar",
                              [ref["uri"] for ref in art.get("references", [])])),
        structure=structure))
