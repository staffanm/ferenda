"""Rättsfallssidan: the referat/dom body, its keywords and the ursprunglig
dom link.

Registered as this source's page renderer in `build.SOURCE_RENDERERS`;
`render` is the `(art, site) -> str` the generate driver calls.
"""
from urllib.parse import quote

from markupsafe import Markup

from ..lib import casenaming, labels, tpl
from ..lib.markdown import begrepp_uri
from ..lib.page import (
    NODES,
    Rail,
    Toc,
    doc_meta,
    footnote_items,
    href,
    page_context,
    render_node,
    render_runs,
    render_toc,
)

ENV = tpl.environment("accommodanda.dv")


DV_SHORT_COURT = {"Högsta domstolen": "HD",
                  "Högsta förvaltningsdomstolen": "HFD"}
DV_RULING_HEADING = {"betankande": "Föredragandens förslag till beslut",
                     "skiljaktig": "Skiljaktig mening", "tillagg": "Tillägg"}


def _dv_genitive(court):
    short = DV_SHORT_COURT.get(court)
    if short:
        return short + ":s"                       # HD:s, HFD:s
    return court + ("" if court.endswith("s") else "s")


def _dv_ruling_word(art):
    """The operative ruling's noun, from the målnummer prefix the court assigns:
    Ö-mål are beslut, B/T-mål are dom; otherwise the neutral "avgörande"."""
    mals = art.get("malnummer") or []
    pre = (mals[0][:1].upper() if mals else "")
    return {"Ö": "beslut", "B": "dom", "T": "dom"}.get(pre, "avgörande")


def _dv_page_marker(doc_uri, pg):
    """A förarbete-style facsimile page button: clicking loads that page of the
    raw verdict's source PDF (faksimil.js + /api/v1/facsimile). Emitted at each PDF
    page boundary of a verdict parsed from its PDF."""
    return NODES.dv_page_marker(
        pg, "/api/v1/facsimile?uri=%s&sid=%d" % (quote(doc_uri, safe=""), pg))


def _dv_numbered_paragraph(node, site):
    """A numbered domskäl paragraph: the number hangs in the gutter and is its own
    permalink anchor (#P{n}), so "punkt 42" is linkable."""
    return NODES.dv_numbered_paragraph(
        str(node["ordinal"]), Markup(render_runs(node.get("text", []), site)))


def _dv_walk(nodes, site, doc_uri, toc, rail, court=None, ruling="avgörande",
             state=None):
    """Render a DV structure level: court instances and the betänkande/dom split
    become titled sections (the föredragande's proposal muted), domskäl/domslut
    are transparent wrappers whose own `<h2>` leaves carry the section titles,
    and prose leaves render as ordinary paragraphs. `state` threads the running
    facsimile page across the recursion (a verdict parsed from its PDF tags each
    block with a page; a button is emitted at every page change)."""
    if state is None:
        state = {"page": None}
    sib = {n.get("type") for n in nodes}
    out = []
    for n in nodes:
        t = n.get("type")
        pg = n.get("page")
        if pg and pg != state["page"]:
            state["page"] = pg
            out.append(_dv_page_marker(doc_uri, pg))
        if t == "instans":
            c = n.get("court") or "Instans"
            anchor = toc.add(None, c, 1)
            inner = _dv_walk(n.get("children", []), site, doc_uri, toc, rail,
                             court=n.get("court"), ruling=ruling, state=state)
            out.append(NODES.dv_instans(anchor, c, Markup(inner)))
        elif t == "delmal":
            inner = _dv_walk(n.get("children", []), site, doc_uri, toc, rail,
                             court=court, ruling=ruling, state=state)
            out.append(NODES.dv_delmal(n.get("ordinal"), Markup(inner)))
        elif t in ("betankande", "skiljaktig", "tillagg"):
            label = DV_RULING_HEADING[t]
            anchor = toc.add(None, label, 2)
            inner = _dv_walk(n.get("children", []), site, doc_uri, toc, rail,
                             court=court, ruling=ruling, state=state)
            out.append(NODES.dv_ruling(t, anchor, label, Markup(inner)))
        elif t == "dom":
            inner = _dv_walk(n.get("children", []), site, doc_uri, toc, rail,
                             court=court, ruling=ruling, state=state)
            # title the court's own ruling only where a betänkande precedes it in
            # the same instance; otherwise the instans heading already names it
            label = anchor = None
            if "betankande" in sib and court:
                label = "%s %s" % (_dv_genitive(court), ruling)
                anchor = toc.add(None, label, 2)
            out.append(NODES.dv_dom(anchor or "", label, Markup(inner)))
        elif t in ("domskal", "domslut"):                # transparent wrappers
            out.append(_dv_walk(n.get("children", []), site, doc_uri, toc, rail,
                                court=court, ruling=ruling, state=state))
        elif t == "stycke" and str(n.get("ordinal") or "").isdigit():
            out.append(_dv_numbered_paragraph(n, site))   # hanging-indent, linkable
        else:
            out.append(render_node(n, site, doc_uri, toc, rail))
    return "".join(out)


def render(art, site):
    md = art.get("metadata", {})
    # a named case (incl. a pre-referat one) leads with its name in the h1 and its
    # referat/id in the eyebrow ("NJA 2025 s. 897" · "Meteoriten"); an unnamed case
    # has nothing to name, so the court fills the eyebrow and the id becomes the h1
    # ("Högsta förvaltningsdomstolen" · "HFD 2011 ref. 4") (C2)
    lb = labels.document_labels("dv", art)
    if lb.short_title:
        title, eyebrow = lb.short_title, lb.short_id
    else:
        title, eyebrow = lb.short_id, art.get("court_namn")
    meta = [
        ("Domstol", art.get("court_namn")),
        ("Avgörandedatum", art.get("avgorandedatum")),
        ("Målnummer", ", ".join(art.get("malnummer") or [])),
        ("Löpnummer", ", ".join(casenaming.lopnummer(art))),
        ("Rättsområde", ", ".join(md.get("rattsomrade") or [])),
        ("Europarätt", ", ".join(md.get("europarattslig") or [])),
    ]
    toc = Toc()
    rail = Rail(site, art["uri"])
    # a record with explicit instance structure (HD's modern <h1>-tagged form) is
    # walked as nested sections; a flat legacy record has no structural wrappers,
    # so the same walk renders it as a plain paragraph sequence
    structure = Markup(_dv_walk(art.get("structure", []), site, art["uri"],
                                toc, rail, ruling=_dv_ruling_word(art)))
    rail.add_document()
    return ENV.get_template("dv.html").render(page_context(
        title, "Rättsfall", doc_meta(meta, art.get("source_url")),
        toc=render_toc(toc), eyebrow=eyebrow,
        summary_text=md.get("sammanfattning"),
        island=rail.island(),
        ursprunglig=_dv_ursprunglig_dom(art),
        sokord=_keywords(md.get("nyckelord") or [], site),
        structure=structure,
        footnotes=footnote_items(art.get("footnotes", []), site),
        # the curated Lagrum/Förarbeten/Rättsfall/Litteratur lists are the
        # referat editor's apparatus -- shown for a published referat, but not
        # for a raw verdict, whose PDF carries no such section (R2)
        curated=_dv_curated(md, site) if art.get("referat") else []))


def _dv_ursprunglig_dom(art):
    """The "Ursprunglig dom" link items: the court's own pre-referat verdict PDF
    that this NJA referat later absorbed (R2). Present only on a referat that
    folded in a separate raw verdict record; the PDF is served by
    `/api/v1/dv-verdict`."""
    return [{"url": it["url"],
             "label": ", ".join(it.get("malnummer") or []) or "Dom (PDF)",
             "date": it.get("avgorandedatum")}
            for it in art.get("ursprunglig_dom") or []]


def _dv_curated(md, site):
    """The curated relation groups on a decision page -- the editor's Lagrum,
    Förarbeten, Rättsfall and Litteratur lists, each entry rendered from its
    normalized runs so a resolved citation links and unresolved text stays
    visible as the editor wrote it. An artifact parsed before the runs shape
    contributes nothing here (like the label fallback in the catalog)."""
    sections = []
    for key, label in (("lagrum", "Lagrum"), ("forarbeten", "Förarbeten"),
                       ("related", "Rättsfall"), ("litteratur", "Litteratur")):
        entries = [e for e in md.get(key) or []
                   if isinstance(e, dict) and e.get("runs")]
        if entries:
            sections.append({"key": key, "label": label,
                             "lines": [Markup(render_runs(e["runs"], site))
                                       for e in entries]})
    return sections


def _keywords(nyckelord, site):
    """Case keywords as sökord items linking to their concept (begrepp) page
    where one exists -- the case→concept half of the keyword graph."""
    out = []
    for n in nyckelord:
        uri = site.resolve(begrepp_uri(n))      # fold onto the canonical concept
        out.append({"href": href(uri) if site.has(uri) else None, "text": n})
    return out
