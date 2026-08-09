"""Europarådsfördragssidan: the treaty's provisions.

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
    provision_section,
    ref_list,
    render_node,
    render_toc,
)

ENV = tpl.environment("accommodanda.coe")


def _coe_label(node):
    """A provision's rail label: the Council of Europe numbers sections as well
    as articles."""
    return "%s %s" % ("Artikel" if node.get("type") == "artikel" else "Sektion",
                      node.get("ordinal") or "")


def render(art, site):
    md = art.get("metadata", {})
    implementation = md.get("swedishImplementation")
    lb = labels.document_labels("coe", art)
    meta = [
        # the treaty's authentic (English) title under the Swedish short-name h1
        ("Titel", lb.official_title if lb.official_title != lb.short_title else None),
        ("Referens", md.get("reference")),
        ("Öppnad för undertecknande", md.get("openingDate")),
        ("Ort", md.get("openingPlace")),
        ("Ikraftträdande", md.get("entryIntoForce")),
        ("Svensk lag", "SFS 1994:1219" if implementation else None),
    ]
    toc = Toc()
    rail = Rail(site, art["uri"])
    parts = []
    for node in art.get("structure", []):
        if node.get("type") in ("artikel", "sektion"):
            parts.append(provision_section(node, site, art["uri"], toc, rail,
                                              _coe_label(node)))
        else:
            parts.append(render_node(node, site, art["uri"], toc, rail))
    rail.add_document()
    return ENV.get_template("coe.html").render(page_context(
        lb.short_title or lb.official_title, "Europarådets fördrag",
        doc_meta(meta, art.get("source_url")), toc=render_toc(toc, lb.short_id),
        eyebrow=lb.short_id, island=rail.island(),
        implementation_link=Markup(
            ref_list(site, "Svensk inkorporering", [implementation])
            if implementation else ""),
        structure=Markup("".join(parts))))
