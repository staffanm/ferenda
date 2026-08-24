"""Föreskriftssidan: the regulation text, its ändringsföreskrifter
and consolidation banners.

Registered as this source's page renderer in `build.SOURCE_RENDERERS`;
`render` is the `(art, site) -> str` the generate driver calls.
"""
from html import escape

from markupsafe import Markup

from ..lib import catalog, labels, layout, tpl
from ..lib.page import (
    BANNERS,
    PANELS,
    Rail,
    Toc,
    doc_meta,
    footnote_items,
    page_context,
    ref_link,
    ref_list,
    render_node,
    render_toc,
)
from ..lib.text import presented_consolidation
from .model import printed_designation

ENV = tpl.environment("accommodanda.foreskrift")


def _upphavd_av(rows):
    """The regulations whose own text says they replace or repeal this one --
    the inbound mirror of the Upphäver group, from the catalog's typed
    rpubl:upphaver edges. Worded 'Upphävs eller ersätts av' because the source
    clause conflates the two; the claim comes from the *replacing* document's
    text, so this page's own status is not asserted beyond it."""
    return PANELS.ref_list("Upphävs eller ersätts av", [
        Markup('<a href="%s">%s</a>') % (
            layout.page_url(from_uri),
            "%s — %s" % (label, title) if title and title != label else label)
        for from_uri, label, title in rows])


def _andrad_genom(art, rows):
    """Every regulation known to have amended this one, as (uri, label) pairs:
    the harvest's own amendment register plus the inbound rpubl:andrar edges,
    which is what catches a base regulation whose agency never listed its
    amendments (SJÖFS 2005:25 is amended by 2006:39 and said nothing)."""
    seen, out = set(), []
    for am in art.get("amendments", []):
        if am.get("uri") and am["uri"] not in seen:
            seen.add(am["uri"])
            out.append((am["uri"], am.get("identifier")
                        or printed_designation(am["uri"])))
    for from_uri, label, _title in rows:
        if from_uri not in seen:
            seen.add(from_uri)
            out.append((from_uri, label or printed_designation(from_uri)))
    return sorted(out, key=lambda p: p[1] or "")


def _andrad_links(site, pairs):
    return Markup(", ").join(
        (Markup('<a href="%s">%s</a>') % (layout.page_url(uri), label)
         if site.has(uri) else Markup('<span class="noref">%s</span>') % label)
        for uri, label in pairs)


def _foreskrift_repealed_banner(rows):
    """The top-of-page callout when another regulation's text says it repeals
    or replaces this one: a repealed föreskrift must never read as in force.
    The evidence is the replacing documents' own repeal clauses (there is no
    authoritative status field in the agency registers), so the banner names
    them rather than asserting a repeal date of its own."""
    if not rows:
        return ""
    return BANNERS.foreskrift_repealed_banner(
        [{"url": layout.page_url(from_uri), "label": label}
         for from_uri, label, _title in rows])


def _amendment_label(am):
    """An ändringsförfattning's display: its designation linked to the agency's
    own page for it where the harvest captured one. The hosted-page link is
    deliberately not minted here: an amendment that is also harvested as its
    own record gets its page linked wherever its uri occurs in body text; this
    register lists the *source* documents."""
    ident = am.get("identifier") or ("ändringsförfattning utan läsbar "
                                     "beteckning")
    if am.get("url"):
        return Markup('<a class="ext" href="%s" rel="external">%s</a>') % (
            am["url"], ident)
    return Markup(escape(ident))


def _foreskrift_amendments(amendments, toc):
    """The bottom-of-page ändringsförfattningar register (the föreskrift
    counterpart of the SFS `_andringar` view, reduced to what the harvest
    knows: designation + the agency's own document link)."""
    if not amendments:
        return ""
    anchor = toc.add("L", "Ändringsförfattningar", 1)
    return PANELS.foreskrift_amendments(
        anchor, [_amendment_label(am) for am in amendments])


def _konsoliderad_banner(art, site, tom):
    """The callout on a föreskrift page that presents a konsoliderad version:
    which amendment cutoff it folds in, that the compilation is inofficial
    (the grundförfattning + ändringsförfattningar stay the authoritative
    texts), and the way to the as-enacted base text where we parsed it."""
    if tom:
        # the fallback is reached exactly when the cutoff amendment is missing
        # from the harvest register, so it must spell the designation the way
        # the rest of the page does: upcasing the slug writes SJOFS for SJÖFS
        ident = next((a["identifier"] for a in art.get("amendments", [])
                      if a.get("uri") == tom and a.get("identifier")),
                     printed_designation(tom))
        # a konsolideradTom is always <samling>/<nummer>, which is exactly what
        # printed_designation reads; a None here would print as the word "None"
        assert ident, "no designation for konsolideradTom %r" % tom
        cutoff = {"url": layout.page_url(tom) if site.has(tom) else None,
                  "label": ident}
    else:
        cutoff = None
    grund_url = (layout.page_url(art["uri"] + "/grund")
                 if art.get("structure") else None)
    return BANNERS.konsoliderad_banner(cutoff, grund_url)


def _unparsed_konsoliderad_note(consolidations):
    """The pointer shown when a record's konsoliderad PDF exists but yielded no
    parsed text (an image-only scan, or a cover sheet standing in for the
    document): the page presents the as-enacted base text, so at least link the
    agency's own consolidated PDF."""
    urls = [c["url"] for c in consolidations
            if not c.get("structure") and c.get("url")]
    if not urls:
        return ""
    return BANNERS.unparsed_konsoliderad_note(urls[0])


def _grund_banner(base_uri):
    """The callout on a föreskrift ``/grund`` page: the as-enacted base text,
    without later amendments, and the way back to the presented version."""
    return BANNERS.grund_banner(layout.page_url(base_uri))


def render(art, site):
    md = art.get("metadata", {})
    # a /grund sidecar (the as-enacted base text beside a presented
    # consolidation) renders the base structure with a way-back banner and --
    # like an SFS äldre lydelse -- no inbound panel: citations always target
    # the canonical page
    grund = art.get("version") == "grund"
    base_uri = art["uri"].removesuffix("/grund") if grund else art["uri"]
    cons = None if grund else presented_consolidation(art)
    structure = cons["structure"] if cons else art.get("structure", [])
    # the notes that belong to *that* body: a presented consolidation is its own
    # document, so listing the base regulation's notes under it would print
    # numbered notes about a text the reader is not looking at
    notes = cons.get("footnotes", []) if cons else art.get("footnotes", [])
    ident = art.get("identifier") or catalog.local(base_uri)
    lb = labels.document_labels("foreskrift", art)
    title = lb.short_title or ident
    andrad_rows = [] if grund else catalog.andrar_inbound(site.con, base_uri)
    andrad = _andrad_genom(art, andrad_rows)
    meta = [
        ("Titel", lb.official_title if lb.official_title != title else None),
        ("Utgivare", md.get("publisher")),
        ("Beslutad", md.get("beslutsdatum")),
        ("Ikraftträdande", md.get("ikrafttradandedatum")),
        # what has changed this regulation, in the header where the SFS pages
        # put it ("Ändring införd t.o.m. SFS 2022:836") -- a reader cannot tell
        # whether the text is current without it. The cutoff is only claimed
        # where a consolidation folds the amendments in; the register below
        # lists them either way.
        ("Ändrad t.o.m.", _andrad_links(site, [
            (cons["konsolideradTom"],
             next((lbl for u, lbl in andrad if u == cons["konsolideradTom"]),
                  printed_designation(cons["konsolideradTom"])))])
         if cons and cons.get("konsolideradTom") else None),
        ("Ändrad genom", _andrad_links(site, andrad)),
        # the repeal target belongs in the header, not only the refs section:
        # what this regulation replaces is identity-level metadata
        ("Upphäver", Markup(", ").join(
            ref_link(site, u, printed_designation)
            for u in md.get("upphaver") or [])),
    ]
    # outbound typed relations: what this regulation amends and replaces, the
    # empowering statute paragrafer (whose inbound mirror is the SFS paragraf's
    # "Föreskrifter meddelade med stöd av …" margin) and the EU directives it
    # transposes -- plus the inbound mirror of upphäver: who replaced *this*
    upphavd_rows = [] if grund else catalog.upphaver_inbound(site.con, base_uri)
    refs = (ref_list(site, "Ändrar", md.get("andrar"), printed_designation)
            + ref_list(site, "Upphäver", md.get("upphaver"), printed_designation)
            + ref_list(site, "Bemyndigande", md.get("bemyndigande"))
            + ref_list(site, "Genomför EU-direktiv", md.get("genomfor"))
            + _upphavd_av(upphavd_rows))
    toc = Toc()
    rail = Rail(site, art["uri"])
    banner = _foreskrift_repealed_banner(upphavd_rows) \
        + (_grund_banner(base_uri) if grund
           else _konsoliderad_banner(art, site, cons["konsolideradTom"])
           if cons
           else _unparsed_konsoliderad_note(art.get("consolidations", [])))
    body = Markup("".join(render_node(n, site, art["uri"], toc, rail)
                          for n in structure))
    # rendered before render_toc below: the amendment register adds its own
    # TOC entry as it renders
    amendments = Markup(_foreskrift_amendments(art.get("amendments", []), toc))
    # a grundföreskrift page shows the original wording, not the regulation as it
    # now reads, so citations to the regulation belong on the consolidation
    rail.add_document(inbound=not grund)
    return ENV.get_template("foreskrift.html").render(page_context(
        title, "Föreskrift", doc_meta(meta, art.get("source_url")),
        toc=render_toc(toc, ident),
        eyebrow=(ident + " · ursprunglig lydelse" if grund else ident),
        island=rail.island(), body_class=" inaktuell" if grund else "",
        banner=Markup(banner), refs=Markup(refs), structure=body,
        # poppler drops the superscript marker a föreskrift prints in its
        # ingress -- it shares a baseline with the prose -- so there is no
        # inline anchor to return to and the note lists its printed number
        footnotes=footnote_items(notes, site, backref=False),
        amendments=amendments))
