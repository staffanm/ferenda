"""Förarbetessidan: the proposition/SOU/Ds body and the genomförande
margin.

Registered as this source's page renderer in `build.SOURCE_RENDERERS`;
`render` is the `(art, site) -> str` the generate driver calls.
"""
from urllib.parse import quote

from markupsafe import Markup

from ..lib import labels, tpl
from ..lib.page import (
    NODES,
    Rail,
    Toc,
    directive_link,
    doc_meta,
    page_context,
    plain,
    render_runs,
    render_toc,
)

ENV = tpl.environment("accommodanda.forarbete")


FA_TYPE_LABEL = {"prop": "Proposition", "sou": "SOU", "ds": "Ds",
                 "pm": "Promemoria", "bet": "Betänkande",
                 "dir": "Kommittédirektiv", "fm": "Förordningsmotiv",
                 "skr": "Skrivelse", "lr": "Lagrådsremiss",
                 "so": "Sveriges internationella överenskommelser"}


def _implements_items(art, site):
    """The genomför-direktiv statements pulled from a proposition's
    författningskommentar (§7d) as template items: which EU directive article
    each provision transposes. Each links to the directive -- its article on
    our EU page when we host it, else out to EUR-Lex."""
    items = []
    for r in art.get("implements") or []:
        directive = r["directive"]
        target = r["uris"][0] if r.get("uris") else directive
        where = (("%s kap. %s § " % (r["chapter"], r["paragraf"]))
                 if r.get("chapter") and r.get("paragraf")
                 else ("%s § " % r["paragraf"]) if r.get("paragraf") else "")
        items.append({"where": where, "partial": bool(r.get("partial")),
                      "ref": ", ".join(r["pinpoints"] or r["articles"]),
                      "link": Markup(directive_link(site, directive, target))})
    return items


def render(art, site):
    lb = labels.document_labels("forarbete", art)
    title = lb.short_title or art["uri"]
    # the identifier is the eyebrow (below), so it needs no "Beteckning" dl row
    meta = [("Typ", FA_TYPE_LABEL.get(art.get("type"), art.get("type"))),
            ("Datum", art.get("date"))]
    parts = []
    toc = Toc()
    doc_uri = art["uri"]
    rail = Rail(site, doc_uri)
    state = {"page": None}

    def emit_page(node):
        # page anchor (#sid{N} -- the förarbete citation target, unchanged by the
        # hierarchy); the statute/case paragraphs citing this page drive the rail.
        # A page belonging to a bilaga that restarted its own count is anchored
        # #bilaga{B}-sid{N} instead: its printed numbers repeat the body's, so a
        # plain #sid would be two different pages (prop. 2021/22:100 has four
        # printed page 1s). Nothing cites that form -- "prop. … s. 42" always
        # means the body -- but the page stays addressable and the body's own
        # anchors stay unambiguous.
        pg, bil = node.get("page"), node.get("bilaga")
        if pg and (pg, bil) != state["page"]:
            state["page"] = (pg, bil)
            key = "bilaga%s-sid%d" % (bil, pg) if bil else "sid%d" % pg
            rail.add(key, "bilaga %s s. %d" % (bil, pg) if bil else "s. %d" % pg)
            # the page number doubles as the facsimile button: a click loads
            # the source PDF page as a retina PNG (faksimil.js + the
            # /api/v1/facsimile endpoint, rendered on demand and disk-cached).
            # A bilaga page gets no button: the endpoint addresses a page by
            # its *printed* number, which here repeats a body page's, so the
            # button would confidently show the wrong image. The number still
            # renders and the anchor still works.
            rail_id = key if key in rail.data else None
            fax = (None if bil else "/api/v1/facsimile?uri=%s&sid=%d"
                   % (quote(doc_uri, safe=""), pg))
            parts.append(NODES.fa_sid(key, rail_id, pg, fax, bil))

    def close_komm():
        if state["komm"] is not None:
            parts.append("</div>")
            state["komm"] = None

    def walk(nodes):
        for n in nodes:
            emit_page(n)
            if n.get("type") == "avsnitt":
                close_komm()
                level = n.get("level") or 1
                anchor = toc.add(n.get("id"), plain(n["text"]), level)
                # wire the section to the scroll-driven rail (remiss feedback on
                # this avsnitt); a section with no context gets no data-rail
                rail.add(n.get("id"), plain(n["text"]))
                nid = n.get("id")
                parts.append(NODES.fa_avsnitt(
                    min(level + 1, 5), anchor,
                    nid if nid and nid in rail.data else None,
                    Markup(render_runs(n["text"], site))))
                walk(n.get("children", []))
            elif n.get("type") == "tabell":
                # a nuvarande/föreslagen lydelse comparison: two columns of
                # aligned cells, the `th` row the italic column header
                close_komm()
                parts.append(NODES.fa_lydelse_tabell(
                    [{"tag": "th" if r.get("th") else "td",
                      "cells": [Markup(render_runs(c, site))
                                for c in r.get("cells", [])]}
                     for r in n.get("children", [])]))
            elif n.get("type") == "bild" and n.get("bbox") and n.get("page"):
                # the pixels stay in the source PDF: the facsimile endpoint
                # crops the figure's rectangle on demand and caches the result,
                # the same renderer the page buttons above use
                parts.append(NODES.fa_bild(
                    "/api/v1/facsimile?uri=%s&sid=%d&bbox=%s"
                    % (quote(doc_uri, safe=""), n["page"],
                       ",".join("%.1f" % v for v in n["bbox"])),
                    "Illustration på sidan %d" % n["page"]))
            else:
                # författningskommentar blocks (`fk`, stamped per entry by
                # forarbete's extractor at parse time): one highlight box per
                # entry -- a new entry number closes the previous box
                if n.get("fk"):
                    if state["komm"] != n["fk"]:
                        close_komm()
                        parts.append('<div class="fk-komm">')
                        state["komm"] = n["fk"]
                else:
                    close_komm()
                kind = n.get("type") if n.get("type") in ("fotnot", "ruta") else ""
                parts.append(NODES.fa_p(kind,
                                        Markup(render_runs(n["text"], site))))

    state["komm"] = None
    walk(art.get("structure", []))
    close_komm()
    rail.add_document()        # document-level remiss "most interesting" overall panel
    return ENV.get_template("forarbete.html").render(page_context(
        title, "Förarbete", doc_meta(meta, art.get("source_url")),
        toc=render_toc(toc), eyebrow=lb.short_id, island=rail.island(),
        implements=_implements_items(art, site),
        structure=Markup("".join(parts))))
