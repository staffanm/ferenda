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
    page_context,
    ref_link,
    ref_list,
    render_node,
    render_toc,
)
from ..lib.text import presented_consolidation

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
        ident = next((a["identifier"] for a in art.get("amendments", [])
                      if a.get("uri") == tom and a.get("identifier")),
                     catalog.local(tom).replace("/", " ").upper())
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
    ident = art.get("identifier") or catalog.local(base_uri)
    lb = labels.document_labels("foreskrift", art)
    title = lb.short_title or ident
    meta = [
        ("Titel", lb.official_title if lb.official_title != title else None),
        ("Utgivare", md.get("publisher")),
        ("Beslutad", md.get("beslutsdatum")),
        ("Ikraftträdande", md.get("ikrafttradandedatum")),
        # the repeal target belongs in the header, not only the refs section:
        # what this regulation replaces is identity-level metadata
        ("Upphäver", Markup(", ").join(
            ref_link(site, u) for u in md.get("upphaver") or [])),
    ]
    # outbound typed relations: what this regulation amends and replaces, the
    # empowering statute paragrafer (whose inbound mirror is the SFS paragraf's
    # "Föreskrifter meddelade med stöd av …" margin) and the EU directives it
    # transposes -- plus the inbound mirror of upphäver: who replaced *this*
    upphavd_rows = [] if grund else catalog.upphaver_inbound(site.con, base_uri)
    refs = (ref_list(site, "Ändrar", md.get("andrar"))
            + ref_list(site, "Upphäver", md.get("upphaver"))
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
        toc=render_toc(toc),
        eyebrow=(ident + " · ursprunglig lydelse" if grund else ident),
        island=rail.island(), body_class=" inaktuell" if grund else "",
        banner=Markup(banner), refs=Markup(refs), structure=body,
        amendments=amendments))
