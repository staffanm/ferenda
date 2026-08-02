"""Riktlinjesidan: the EDPB/WP29 guidance body and its sections.

Registered as this source's page renderer in `build.SOURCE_RENDERERS`;
`render` is the `(art, site) -> str` the generate driver calls.
"""

from markupsafe import Markup

from ..lib import catalog, tpl
from ..lib.page import (
    BANNERS,
    Rail,
    Toc,
    doc_meta,
    footnote_items,
    page_context,
    render_node,
    render_toc,
)

ENV = tpl.environment("accommodanda.edpb")


EDPB_SECTION = {"riktlinjer": "Riktlinje", "rekommendationer": "Rekommendation",
                "wp": "Artikel 29-gruppens vägledning"}


def render(art, site):
    """An EDPB riktlinje/rekommendation, or an endorsed artikel 29-gruppens
    vägledning.

    Two things separate it from every other document on the site, and both are
    said in a banner rather than left to the metadata list. Which **version**
    this is: the EDPB adopts, consults on and re-adopts these, publishes both,
    and republishing one without saying which would misstate what the board
    says today -- which the EDPB's own reuse terms ("the original meaning or
    message of the documents is not distorted") make a condition of publishing
    it at all. And which **language**: three of them exist in no Swedish version,
    and a reader meeting English text on a Swedish site should be told why
    before they start reading rather than after."""
    md = art.get("metadata", {})
    ident = art.get("identifier") or catalog.local(art["uri"])
    meta = [
        ("Utgivare", md.get("publisher")),
        ("Antagen", md.get("antagen")),
        ("Version", md.get("version")),
        ("Revision", md.get("revision")),
        ("Språk", {"sv": "svenska", "en": "engelska"}.get(md.get("sprak"))),
        ("Ämnesord", ", ".join(md.get("amnesord", [])) or None),
    ]
    toc = Toc()
    rail = Rail(site, art["uri"])
    structure = Markup("".join(
        render_node(n, site, art["uri"], toc, rail)
        for n in art.get("structure", [])))
    rail.add_document()
    banner = Markup("").join(part for part in (
        BANNERS.edpb_language_banner(art.get("source_url"))
        if md.get("sprak") == "en" else "",
        BANNERS.edpb_version_banner(md["version"], md.get("konsultation"))
        if md.get("version") else "") if part)
    return ENV.get_template("edpb.html").render(page_context(
        md.get("title") or ident,
        EDPB_SECTION.get(art.get("serie"), "Vägledning"),
        doc_meta(meta, art.get("source_url")),
        toc=render_toc(toc), eyebrow=ident,
        banner=banner,
        footnotes=footnote_items(art.get("footnotes", []), site,
                                  key="mark", backref=False),
        island=rail.island(), structure=structure))
