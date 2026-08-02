"""Begreppssidan: the concept definition and what cites it.

Registered as this source's page renderer in `build.SOURCE_RENDERERS`;
`render` is the `(art, site) -> str` the generate driver calls.
"""

from markupsafe import Markup

from ..lib import catalog, tpl
from ..lib.page import Rail, Toc, doc_meta, page_context, render_node, render_toc

ENV = tpl.environment("accommodanda.wiki")


def render(art, site):
    """A concept definition; its inbound panel shows everything (laws, cases,
    förarbeten, commentary, other concepts) that references the concept."""
    title = art.get("title") or catalog.local(art["uri"])
    meta = [("Kategori", ", ".join(art.get("categories") or []))]
    toc = Toc()
    rail = Rail(site, art["uri"])
    nodes = art.get("body", [])
    structure = Markup("".join(
        render_node(b, site, art["uri"], toc, rail) for b in nodes))
    rail.add_document()
    # a synthesized stub (a defined term / nyckelord with no wiki page) has no
    # description -- its value is the aggregated inbound below (what defines and
    # tags it), so the template says so instead of showing a blank page
    return ENV.get_template("begrepp.html").render(page_context(
        title, "Begrepp", doc_meta(meta, art.get("source_url")),
        toc=render_toc(toc), eyebrow="Begrepp", island=rail.island(),
        structure=structure, has_description=bool(nodes)))
