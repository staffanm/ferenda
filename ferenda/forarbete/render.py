"""Förarbetessidan: the proposition/SOU/Ds body and the genomförande
margin.

Registered as this source's page renderer (the `render=` field of its
`build.py` registration);
`render` is the `(art, site) -> str` the generate driver calls.
"""
import re
from collections import Counter
from urllib.parse import quote

from markupsafe import Markup

from ..lib import labels, tpl
from ..lib.margins import directive_link
from ..lib.page import (
    NODES,
    Rail,
    Toc,
    doc_meta,
    page_context,
    plain,
    render_runs,
    render_toc,
)
from ..lib.text import drop_prefix, runs_text

ENV = tpl.environment("ferenda.forarbete")


FA_TYPE_LABEL = {"prop": "Proposition", "sou": "SOU", "ds": "Ds",
                 "pm": "Promemoria", "bet": "Betänkande",
                 "rskr": "Riksdagsskrivelse",
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


# A section heading that opens with its own number ("4.2 Remissförfarandet").
# A dotted number is unambiguous; a bare integer is not. The scanned older
# propositions carry running heads and table rows on `avsnitt` nodes -- "172
# Kungl. Maj:ts proposition nr 144 år 1970", "1562 Investeringar" -- and they
# look exactly like a numbered section. Four tests, each read off the document
# itself rather than off a list:
#
#   * the outline level: a "4.2" on level 2 puts "4" on level 1, so a number
#     counts only on the level its own document hangs that depth at;
#   * repetition: a title the document prints again and again is a running head,
#     the same test `lib.pdftext.strip_page_furniture` uses for page furniture.
#     Applied to bare integers only -- a real "6.2.1 Allmänt" repeats per chapter;
#   * the document's own ceiling: a bare integer above the highest number it
#     subdivides is a page or a table cell, not section 1562;
#   * the shape: a four-digit component means a date, not a section (_outline_shaped);
#   * a title with fewer than four letters is OCR debris ("3 J-", "1 W «").
#
# Measured over 300 sampled artifacts: 3,066 dotted and 701 bare numbers into the
# gutter, 2,627 rejected. What survives is a handful of scans whose body text the
# parser classified as a heading upstream.
_SECTION_NUMBER = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+(\S.*)$", re.DOTALL)
_MAX_LEVELS = 4          # "11.5.1" is three
_MAX_COMPONENT = 3       # digits; a component wider than that is a year
_MIN_TITLE_LETTERS = 4


def _outline_shaped(num):
    """True where `num` could be an outline number at all. An EU Official
    Journal running head reprinted inside a förarbete is dotted like a section
    number -- "27.6.2013 SV Europeiska unionens officiella tidning L 176/431" --
    and the date is the tell: no section number carries a four-digit component.
    Over 600 sampled artifacts this rejects 13 numbers, all of them dates or OCR
    debris, and no real section number."""
    parts = num.split(".")
    return len(parts) <= _MAX_LEVELS and all(len(p) <= _MAX_COMPONENT for p in parts)


def _section_rows(nodes):
    """Every `avsnitt` as `(level, number_match)`, in document order. Every
    `avsnitt` carries its own `level` (16,749 of 16,749 over 300 sampled
    artifacts), and the render walk reads the same field -- deriving one from the
    recursion depth instead would put the two on different scales."""
    rows = []

    def walk(ns):
        for n in ns:
            if n.get("type") == "avsnitt":
                rows.append((n["level"],
                             _SECTION_NUMBER.match(runs_text(n.get("text") or []).strip())))
            walk(n.get("children", []))

    walk(nodes)
    return rows


def _outline(nodes):
    """What this document's own numbering says about itself: the heading levels
    it hangs top-level numbers on, the highest number it subdivides, and how
    often each title is printed. Empty levels mean no decimal numbering at all --
    then no bare integer in a heading is a section number."""
    rows = _section_rows(nodes)
    dotted = [(lvl, m) for lvl, m in rows if m and "." in m.group(1)]
    heads = [int(m.group(1).split(".")[0]) for _, m in dotted
             if m.group(1).split(".")[0].isdigit()]
    return {"levels": {lvl - m.group(1).count(".") for lvl, m in dotted},
            "ceiling": (max(heads) if heads else 0) + 2,
            "titles": Counter(m.group(2).strip() for _, m in rows if m)}


def _numbered(node, level, outline):
    """`(number, title_runs)` for a numbered section heading, `(None, runs)`
    otherwise. The number leaves the title runs, since it moves to the gutter.

    The number is cut from the *flattened* text by character offset: it is often
    a styled run of its own (`{"style": "b", "text": "3.1"}`), so reading only a
    leading plain string missed half of them."""
    runs = list(node.get("text") or [])
    m = _SECTION_NUMBER.match(runs_text(runs).strip())
    if not m:
        return None, runs
    num, title = m.group(1), m.group(2).strip()
    if not title[:1].isupper() or not _outline_shaped(num):
        return None, runs
    if sum(c.isalpha() for c in title) < _MIN_TITLE_LETTERS:
        return None, runs
    if (level - num.count(".")) not in outline["levels"]:
        return None, runs
    if "." not in num and (outline["titles"][title] > 1
                           or int(num) > outline["ceiling"]):
        return None, runs
    # the match ran against the stripped text, so the cut has to add back the
    # leading whitespace the runs still carry
    flat = runs_text(runs)
    return num, drop_prefix(runs, len(flat) - len(flat.lstrip()) + m.start(2))


def render(art, site):
    lb = labels.document_labels("forarbete", art)
    title = lb.short_title or art["uri"]
    # the identifier is the eyebrow (below), so it needs no "Beteckning" dl row
    meta = [("Typ", FA_TYPE_LABEL.get(art.get("doctype"), art.get("doctype"))),
            ("Datum", art.get("date"))]
    parts = []
    toc = Toc()
    doc_uri = art["uri"]
    rail = Rail(site, doc_uri)
    state = {"page": None}
    # read once: the top-level numbers this document actually subdivides, which
    # is what tells a section number from a page number (_numbered)
    outline = _outline(art.get("structure") or [])

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
                num, title = _numbered(n, level, outline)
                parts.append(NODES.fa_avsnitt(
                    min(level + 1, 5), anchor,
                    nid if nid and nid in rail.data else None, num,
                    Markup(render_runs(title, site))))
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
        doc_uri=art["uri"], short_id=lb.short_id,
        description=site.snippet(art["uri"]),
        toc=render_toc(toc, lb.short_id), eyebrow=lb.short_id, island=rail.island(),
        implements=_implements_items(art, site),
        structure=Markup("".join(parts))))
