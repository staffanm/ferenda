"""Ställningstagandesidan: the agency's legal position and its siblings.

Registered as this source's page renderer in `build.SOURCE_RENDERERS`;
`render` is the `(art, site) -> str` the generate driver calls.
"""

from markupsafe import Markup

from ..lib import catalog, labels, layout, tpl
from ..lib.page import (
    BANNERS,
    doc_meta,
    document_body,
    footnote_items,
    page_context,
    render_toc,
)

ENV = tpl.environment("accommodanda.rs")


def render(art, site):
    """A myndighets rättsligt ställningstagande. The metadata carries the one
    thing that separates it from every other document on the site -- whether the
    agency still stands by it -- so a withdrawn one says so in a banner as well
    as in the dl, and reads as the historical statement it is."""
    md = art.get("metadata", {})
    ident = art.get("identifier") or catalog.local(art["uri"])
    lb = labels.document_labels("rs", art)
    upphavd = md.get("status") == "upphävt"
    meta = [
        ("Myndighet", md.get("publisher")),
        ("Beslutsdatum", md.get("beslutsdatum")),
        ("Diarienummer", md.get("diarienummer")),
        ("Version", md.get("version")),
        ("Föregående version", md.get("foregaendeVersion")),
        ("Upphävt", md.get("upphavd")),
        ("Ersatt av", md.get("ersattAv")),
        ("Ersätter", md.get("ersatter")),
        ("Ämnesord", ", ".join(md.get("nyckelord", [])) or None),
    ]
    structure, toc, rail = document_body(art, site)
    banner = BANNERS.rs_upphavd_banner(
        md.get("upphavd"),
        _sibling_rs(site, md.get("ersattAv"), art["uri"])) if upphavd else ""
    # deliberately *not* the `inaktuell` body class the historical lydelser use:
    # its watermark reads "Inaktuell författning", and a ställningstagande is
    # precisely not a författning -- that distinction is the whole reason this
    # vertical exists. The banner says the withdrawal; a styling of its own is
    # an open question rather than a wrong label.
    return ENV.get_template("rs.html").render(page_context(
        lb.short_title or ident, "Rättsligt ställningstagande",
        doc_meta(meta, art.get("source_url")),
        toc=render_toc(toc), eyebrow=ident,
        summary_text=art.get("sammanfattning"),
        banner=Markup(banner),
        footnotes=footnote_items(art.get("footnotes", []), site,
                                  backref=False),
        island=rail.island(), structure=structure))


def _sibling_rs(site, nummer, own_uri):
    """A ställningstagande an agency names by bare number ("upphävt genom
    2022:2"), as {label, url?}. The sibling is *the same agency's*: a number is
    unique only within one agency's series, so the number is resolved against
    this document's own URI prefix. The url is dropped when the corpus does not
    hold that document -- a förteckning names statements from before the harvest
    reaches -- and the label alone still says what replaced this one."""
    if not nummer:
        return None
    uri = own_uri.rsplit("/", 1)[0] + "/" + nummer
    return {"label": nummer,
            "url": layout.page_url(uri) if uri in site.known else None}
