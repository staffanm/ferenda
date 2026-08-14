"""IHL-fördragssidan: the instrument's provisions.

Registered as this source's page renderer in `build.SOURCE_RENDERERS`;
`render` is the `(art, site) -> str` the generate driver calls.
"""

from markupsafe import Markup

from ..lib import labels, tpl
from ..lib.page import (
    BANNERS,
    Rail,
    Toc,
    article_label,
    doc_meta,
    page_context,
    provision_section,
    render_node,
    render_toc,
)

ENV = tpl.environment("accommodanda.icrc")


def render(art, site):
    md = art.get("metadata", {})
    lb = labels.document_labels("icrc", art)
    meta = [
        ("Titel", lb.official_title if lb.official_title != lb.short_title else None),
        ("ICRC-nummer", art.get("number")),
        ("Antagen", md.get("adoptionDate")),
        ("Ikraftträdande", md.get("entryIntoForce")),
        ("I kraft", {True: "Ja", False: "Nej"}.get(md.get("inForce"))),
        ("Depositarie", md.get("depositary")),
        ("Ämnen", ", ".join(md.get("topics") or []) or None),
        ("Autentiska språk", ", ".join(md.get("languages") or []) or None),
        ("Antal parter", str(md["statesParties"]) if md.get("statesParties") else None),
    ]
    toc = Toc()
    rail = Rail(site, art["uri"])
    parts = []
    for node in art.get("structure", []):
        if node.get("type") == "artikel":
            parts.append(provision_section(node, site, art["uri"], toc, rail,
                                              article_label(node)))
        else:
            parts.append(render_node(node, site, art["uri"], toc, rail))
    rail.add_document()
    # No treaty is metadata-only any more: the 32 that were are the undivided
    # 19th-century declarations, whose text the section allowlist dropped for
    # being labelled "empty" (model.TEXT_SECTIONS). The banner stays for the
    # case it was written for -- a record the source publishes without a text --
    # because a page of metadata with nothing said reads as a broken page.
    banner = "" if art.get("structure") else BANNERS.text_not_held(
        "traktaten", art.get("source_url"), "ICRC:s fördragsdatabas")
    return ENV.get_template("icrc.html").render(page_context(
        lb.short_title or lb.official_title, "Internationell humanitär rätt",
        doc_meta(meta, art.get("source_url")), toc=render_toc(toc, lb.short_id),
        eyebrow=lb.short_id, island=rail.island(),
        lead=art.get("summary"), structure=Markup("".join(parts)),
        banner=banner))
