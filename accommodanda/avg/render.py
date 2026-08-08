"""Myndighetsavgörandesidan: the JO/JK/ARN/IMY/KKV decision body.

Registered as this source's page renderer in `build.SOURCE_RENDERERS`;
`render` is the `(art, site) -> str` the generate driver calls.
"""

from ..lib import catalog, labels, tpl
from ..lib.page import (
    doc_meta,
    document_body,
    footnote_items,
    page_context,
    render_toc,
)

ENV = tpl.environment("accommodanda.avg")


def render(art, site):
    md = art.get("metadata", {})
    ident = art.get("identifier") or catalog.local(art["uri"])
    lb = labels.document_labels("avg", art)
    # an ARN referat's "title" is its preamble paragraph: the first sentence
    # heads the page (A4, via labels._avg) and the whole preamble reads in
    # full between the metadata and the referat text
    title = lb.short_title or ident
    summary = art.get("sammanfattning")
    if (art.get("org") == "arn" and md.get("title")
            and md["title"] != title):
        summary = md["title"]
    meta = [
        ("Myndighet", md.get("publisher")),
        ("Beslutsdatum", md.get("beslutsdatum")),
        ("Diarienummer", ", ".join(md.get("diarienummer", []))),
        ("Ämbetsberättelse", md.get("officialReport")),
        ("Sakområde", ", ".join(md.get("nyckelord", [])) or None),
        # KKV only: the diarium's counterparty -- a competition case is known by
        # who it was against as much as by what it was called -- and the curated
        # ärendelista's branch and kinds of beslut
        ("Motpart", md.get("motpart")),
        ("Bransch", ", ".join(md.get("bransch", [])) or None),
        ("Typ av beslut", ", ".join(md.get("beslutstyp", [])) or None),
        # IMY only: the fine, and the praxisbeslut page's curated fields --
        # which lagrum the decision turns on, and whether it still stands
        ("Sanktionsavgift", md.get("sanktionsavgift")),
        ("Lagrum", (md.get("praxis") or {}).get("lagrum")),
        ("Korrigerande åtgärd", (md.get("praxis") or {}).get("korrigerandeAtgard")),
        ("Överklagat", (md.get("praxis") or {}).get("overklagan")),
        ("Vunnit laga kraft", (md.get("praxis") or {}).get("lagakraft")),
    ]
    structure, toc, rail = document_body(art, site)
    section = {"jo": "JO-beslut", "jk": "JK-beslut", "arn": "ARN-beslut",
               "imy": "IMY-beslut",
               "kkv": "KKV-beslut"}.get(art.get("org"), "Myndighetsavgörande")
    return ENV.get_template("avg.html").render(page_context(
        title, section, doc_meta(meta, art.get("source_url")),
        toc=render_toc(toc), eyebrow=ident,
        summary_text=summary,
        footnotes=footnote_items(art.get("footnotes", []), site,
                                  backref=False),
        island=rail.island(), structure=structure))
