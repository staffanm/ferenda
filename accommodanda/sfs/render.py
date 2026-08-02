"""Författningssidan: the statute text, its ändringsregister, the lydelse
panel and the way-back banners.

Registered as this source's page renderer in `build.SOURCE_RENDERERS`;
`render` is the `(art, site) -> str` the generate driver calls.
"""
import functools
import json
import re
from datetime import date
from html import escape

from markupsafe import Markup

from ..lib import catalog, history, labels, lagrum, layout, tpl
from ..lib.catalog import BASE
from ..lib.page import (
    BANNERS,
    EURLEX,
    PANELS,
    Rail,
    Toc,
    citer_name,
    doc_meta,
    ext_link,
    forarb_sort_key,
    href,
    page_context,
    prop_link,
    register_anchor,
    render_node,
    render_toc,
)

ENV = tpl.environment("accommodanda.sfs")


# a shared FORARBETEN recognizer, built lazily per process, to turn a förarbete
# identifier ("Prop. 2017/18:89") into its document uri -- reusing the citation
# engine's minting instead of a second, drifting parser. namedlaws is irrelevant
# to förarbete numbers, so an empty map suffices.
_FORARB_PARSER = None


@functools.lru_cache(maxsize=None)
def forarbete_identifier_uri(identifier):
    """The document uri a förarbete identifier mints to (prop/sou/ds), or None
    for a form the engine does not host (betänkanden, riksdagsskrivelser).
    The lru_cache is what bounds the reuse: the lazily-built parser is reset and
    run at most once per distinct identifier, never on every call."""
    global _FORARB_PARSER
    if _FORARB_PARSER is None:
        _FORARB_PARSER = lagrum.LagrumParser(
            {}, basefile="0000:000", parse_types=[lagrum.FORARBETEN])
    _FORARB_PARSER.reset()
    refs = _FORARB_PARSER.parse_text(identifier, context={})
    return refs[0].uri if refs else None


def forarbeten_section(site, art):
    """The statute's own preparatory works, top-billed above the citation panel.
    Every förarbete of the grundförfattning and every ändringsförfattning is
    listed once (prop→sou→ds→lagrådsremiss→bet, oldest-first): the ones we host
    link to their page under the preferred full-title label, the rest (a
    betänkande/riksdagsskrivelse we do not host) show as their bare identifier.

    Returns `(html, own_uris)` -- `own_uris` are the hosted förarbete uris,
    excluded from the citation panel so a creating proposition reads as a
    preparatory work here, not as a generic inbound reference below."""
    idents, seen = [], set()
    for amendment in art.get("amendments", []):
        for ident in amendment.get("forarbeten", []):
            if ident not in seen:
                seen.add(ident)
                idents.append(ident)
    entries, own_uris = [], set()
    for ident in idents:
        uri = forarbete_identifier_uri(ident)
        meta = catalog.document_meta(site.con, uri) if uri else None
        if meta and site.has(uri):
            kind, label, title, dt = meta
            own_uris.add(uri)
            html = '<a href="%s">%s</a>' % (
                escape(href(uri)), escape(citer_name("forarbete", kind, label, title)))
        else:                       # unhosted (bet./rskr.) -> bare identifier
            # kind from the identifier prefix ("Bet. …" -> bet) so it still sorts
            # into its precedence block; date unknown, so it trails its dated peers
            kind, label, dt, html = (ident.split(" ")[0].rstrip(".").lower(),
                                     ident, None, escape(ident))
        entries.append((forarb_sort_key(kind, dt, label), html, kind))
    entries.sort(key=lambda e: e[0])
    # the SFSR "Förarbeten" row carries prop/bet/rskr (SOU/Ds/lagrådsremiss are
    # not registered there); bet./rskr. are procedural riksdag documents that add
    # little for a reader, so drop them -- in practice this leaves the
    # propositions (T2). `own_uris` still holds every hosted förarbete so none
    # reappear in the generic citation panel.
    shown = [html for _, html, kind in entries if kind not in ("bet", "rskr")]
    if not shown:
        return "", own_uris
    # a long list collapses: first five always visible, the rest behind a
    # "+N fler" disclosure (no extra JS -- a native <details>)
    return PANELS.forarbeten_section([Markup(h) for h in shown], 5), own_uris


def _expired_banner(props):
    """The repeal callout for a statute whose repeal has taken effect: the repeal
    date and, when known, a link to the repealing act. Paired with the
    `body.expired` treatment (subdued reading column + a fixed 'Upphävd
    författning' watermark) so the status stays visible even when an anchor link
    jumps deep past the heading."""
    av = props.get("rinfoex:upphavdAv")
    return BANNERS.expired_banner(
        props.get("rpubl:upphavandedatum"),
        {"url": layout.page_url(av), "label": catalog.local(av)} if av
        else None)


def _version_banner(base_id, version):
    """The callout on a historical-consolidation ("lydelse") page: which
    cutoff it shows, and the way back to the law as it reads today."""
    return BANNERS.version_banner(version, layout.page_url(BASE + base_id))


def _version_notes(art):
    """version id -> "i kraft <date> · Prop. …" annotation for the compare
    panel, from the amendment register (lib.history's join, reduced to the
    display string)."""
    return {v: " · ".join(
                n for n in ((("i kraft %s" % ikraft) if ikraft else None),
                            next((f for f in forarbeten
                                  if f.startswith("Prop")), None)) if n)
            for v, (ikraft, forarbeten) in history.amendment_info(art).items()}


# the compare status line (the h2.lydelser-status in sources/sfs.html,
# populated by versions.js the instant a lydelse is picked) names what is
# being compared, so the reader sees the page change even when the diff fetch
# returns near-instantly (T1). It heads the text it annotates, so it stays in
# the reading column while the <select> that drives it sits up in dl.meta
# (S1); the server-composed diff-note inside #dokument still carries the
# detail.
def _versions_panel(art, base_id, own_version, versions):
    """The compare panel (the old pipeline's docversions dropdown): the
    <select> that versions.js turns into the on-demand diff view
    (?diff=<version>, served by /api/v1/document/diff), annotated with each
    consolidation's ikraft date + proposition where the register knows them.
    Point-in-time links live in the andringar view (see _andringar); here is
    only the comparison affordance. Empty when this very consolidation is the
    only one known.

    Rendered as a `dl.meta` value, not a banner of its own: it belongs beside
    the consolidation cutoff it compares against ("Ändring införd t.o.m. SFS
    2026:880 · Jämför lydelser"), and a full-width box above the text pushed the
    statute itself below the fold for no gain (S1)."""
    versions = [(v, u) for v, u in versions if v != own_version]
    if not versions:
        return ""
    notes = _version_notes(art)
    return PANELS.versions_panel(
        [{"value": v, "note": notes.get(v, "")}
         for v, _vuri in reversed(versions)],          # newest first
        "denna" if own_version else "aktuell",
        BASE + base_id, own_version or "")


def _act_source_links(nr):
    """The change act's own authoritative publication, by era (the old
    registerpost rules): print PDFs at rkrattsdb.gov.se for SFS 1998:306 --
    2018:159, the official svenskforfattningssamling.se version from 2018:160
    on, nothing digitized before that."""
    year, _, lop = nr.partition(":")
    if not (year.isdigit() and lop.isdigit()):
        return ""
    y, n = int(year), int(lop)
    if (y, n) >= (2018, 160):
        return ('<li><a class="ext" rel="external" href="https://'
                'svenskforfattningssamling.se/doc/%d%d.html">'
                'Officiell autentisk version</a></li>' % (y, n))
    if (y, n) >= (1998, 306):
        return ('<li><a class="ext" rel="external" href="https://'
                'rkrattsdb.gov.se/SFSdoc/%02d/%02d%04d.PDF">'
                'Tryckt format (PDF)</a></li>' % (y % 100, y % 100, n))
    return ""


# how a rail line names what the register's Omfattning predicate did to the
# provision, keyed by the artifact's amendment property
AMENDMENT_VERB = {"rpubl:inforsI": "Införd", "rpubl:ersatter": "Ändrad",
                  "rpubl:upphaver": "Upphävd"}


def amendment_index(art):
    """anchor -> [(verb, SFS nr, prop identifiers)] over the artifact's SFSR
    register, in register (chronological) order: which change acts introduced,
    changed or repealed each provision. The per-provision inverse of the
    register's per-act Omfattning parse, feeding the rail's Ändringar section
    (Rail._andringar). Rows whose Omfattning names no fragment (a whole-act
    repeal, an omtryck) stay out -- they are the whole register's story, not
    one provision's."""
    index = {}
    for am in art.get("amendments") or []:
        p = am.get("properties", {})
        ident = p.get("dcterms:identifier", "")
        if not ident.startswith("SFS "):
            continue
        props = [f for f in am.get("forarbeten", [])
                 if f.startswith("Prop. ")]
        for key, verb in AMENDMENT_VERB.items():
            # always lists in the artifact (sfs/register.py ALWAYS_LIST)
            for uri in p.get(key, []):
                if "#" in uri:
                    index.setdefault(uri.partition("#")[2], []).append(
                        (verb, ident[4:], props))
    return index


def _andringar(art, base_id, own_version, versions, site, toc, rail):
    """The bottom-of-page register view (the old pipeline's div.andringar):
    one section per register post -- the base act first, then every change
    act -- with the act's own publication links, the point-in-time
    "Konsoliderad version med ändringar införda till och med SFS X" link where
    that consolidation is parsed, a diff link against the previous available
    consolidation (the amendment as a single change), its
    övergångsbestämmelser, and the register details (förarbeten, omfattning,
    CELEX, ikraftträdande)."""
    amendments = art.get("amendments") or []
    if not amendments:
        return ""
    have = dict(versions)                       # version id -> lydelse uri
    order = [v for v, _ in versions]            # oldest first
    # the current consolidation's own cutoff: its amendment's diff target is
    # the *current* page (that snapshot is not archived -- it is the document)
    m = re.search(r" i lydelse enligt SFS (.+)$",
                  art.get("metadata", {}).get("properties", {})
                     .get("dcterms:identifier", ""))
    cutoff = m.group(1) if m else None
    doc_uri = art["uri"]
    posts = []
    for i, am in enumerate(amendments):
        p = am.get("properties", {})
        ident = p.get("dcterms:identifier", "")
        nr = ident[4:] if ident.startswith("SFS ") else None
        heading = ("Ändring, %s" % ident) if i and ident else (ident or "Ändring")
        links = []
        prev = view_url = None
        if nr:
            links.append(_act_source_links(nr))
            if nr in have:
                view_url = layout.page_url(have[nr])
                if nr != own_version:
                    links.append('<li><a href="%s">Konsoliderad version med '
                                 'ändringar införda till och med SFS %s</a></li>'
                                 % (escape(view_url), escape(nr)))
                idx = order.index(nr)
                prev = order[idx - 1] if idx else None
            elif nr == cutoff and order:
                # the newest amendment: its consolidation is the current text
                view_url = layout.page_url(BASE + base_id)
                prev = order[-1]
        if prev and view_url:
            links.append(Markup('<li><a href="%s?diff=%s">Visa ändringarna '
                                '(jämfört med lydelsen enligt SFS %s)</a></li>')
                         % (view_url, prev, prev))
        content = "".join(render_node(c, site, doc_uri, toc, rail)
                          for c in am.get("content", []))
        celex = p.get("rpubl:celexNummer", [])
        rows = [
            ("Förarbeten", Markup(", ").join(prop_link(site, f)
                                             for f in am.get("forarbeten", []))),
            ("Omfattning", p.get("rpubl:andrar", "")),
            ("CELEX-nr", Markup(" ").join(
                ext_link(EURLEX % c, c)
                for c in ([celex] if isinstance(celex, str) else celex))),
            ("Ikraftträder", p.get("rpubl:ikrafttradandedatum", "")),
        ]
        # the anchor: the övergångsbestämmelse node already mints L{nr}; the
        # wrapper carries it only when no child does (no duplicate DOM ids)
        child_ids = {c.get("id") for c in am.get("content", [])}
        wrapper_id = register_anchor(nr) if nr else None
        posts.append({
            "id": (wrapper_id if wrapper_id and wrapper_id not in child_ids
                   else None),
            "heading": heading,
            "links": [Markup(link) for link in links if link],
            "content": Markup(content),
            "details": [(k, v) for k, v in rows if v]})
    anchor = toc.add("L", "Ändringar och övergångsbestämmelser", 1)
    return PANELS.andringar(anchor, posts)


@functools.lru_cache(maxsize=1)
def _sfs_fetched():
    """The download's {basefile: "YYYY-MM-DD"} last-fetch map (layout.sfs_fetched),
    read live at generate time -- an unchanged refetch bumps it without reparsing
    the artifact, so it, not the artifact, is the source of truth for the date."""
    path = layout.sfs_fetched()
    return json.loads(path.read_text()) if path.exists() else {}


def _node_id_set(nodes):
    """Every node id in an artifact structure -- the anchors the rendered page
    will actually carry, which is what `catalog.caselaw_anchored` needs to
    keep a case out of a paragraf that no longer exists."""
    ids = set()
    stack = list(nodes)
    while stack:
        node = stack.pop()
        if node.get("id"):
            ids.add(node["id"])
        stack.extend(node.get("children", []))
    return ids


def render(art, site):
    props = art.get("metadata", {}).get("properties", {})
    local_id = catalog.local(art["uri"])
    # a historical consolidation ("lydelse") carries its cutoff in `version`
    # and a /konsolidering/ uri; its page gets a way-back banner instead of
    # the inbound panel (citations always target the current consolidation)
    base_id, _, _ = local_id.partition("/konsolidering/")
    version = art.get("version")
    lb = labels.document_labels("sfs", art)
    # h1 is the friendly short name ("Säkerhetsskyddslagen"); the full official
    # title ("Säkerhetsskyddslag (2018:585)") moves into dl.meta "Titel" (C2)
    title = lb.short_title
    # a repeal that has taken effect (a future repeal date is still in force):
    # mark the whole page as upphävd
    upphavd = props.get("rpubl:upphavandedatum")
    expired = (bool(upphavd) and upphavd <= date.today().isoformat()
               and not version)
    # the latest amendment the consolidation carries, from the identifier
    # ("SFS 2018:585 i lydelse enligt SFS 2026:764"); omitted when unamended
    amended = re.search(r"i lydelse enligt SFS (\S+)",
                        props.get("dcterms:identifier") or "")
    versions = history.versions(base_id)
    # the compare affordance rides the cutoff row it compares against (S1); with
    # no cutoff to hang it on (an unamended act that still has consolidations)
    # it earns a row of its own
    lydelser = _versions_panel(art, base_id, version, versions)
    meta = [
        # the full official title ("Säkerhetsskyddslag (2018:585)"); the h1 is the
        # short name, so it does not repeat here unless the two coincide
        ("Titel", lb.official_title if lb.official_title != title else None),
        ("Ikraftträder", props.get("rpubl:ikrafttradandedatum")),
        ("Upphävd", upphavd),
        ("Ändring införd t.o.m.",
         Markup("SFS %s") % amended.group(1) + lydelser
         if amended else None),
        ("Lydelser", lydelser if lydelser and not amended else None),
        ("Senast hämtad", _sfs_fetched().get(base_id)),
    ]
    toc = Toc()
    rail = Rail(site, art["uri"])
    # each provision's own change history, shown beside it (S1); the anchors
    # link into the register section _andringar renders further down
    rail.amendment_index = amendment_index(art)
    # assign the statute's EU case-law rail up front, telling the join which
    # anchors this consolidation actually has -- a genomförande claim following
    # the proposition's original numbering (a since-renumbered kapitel) must
    # cascade to a live paragraf, not swallow its cases into a dead anchor
    site.caselaw_memo.clear()
    site.caselaw_memo[art["uri"]] = catalog.caselaw_anchored(
        site.con, art["uri"], live=_node_id_set(art.get("structure", [])))
    parallel_appendix = any(
        child.get("type") == "konventionsbilaga"
        for node in art.get("structure", []) if node.get("type") == "bilaga"
        for child in node.get("children", []))
    structure = Markup("".join(
        render_node(n, site, art["uri"], toc, rail)
        for n in art.get("structure", [])))
    # the register view renders after the structure so its TOC entry and
    # rail hooks come last, but the OB anchors (#L{nr}) sit inside it
    andringar = Markup(_andringar(art, base_id, version, versions, site, toc,
                                  rail))
    # the statute's own preparatory works get top billing; their hosted uris are
    # then excluded from the generic citation panel below
    forarbeten, own_forarbeten = ("", set()) if version \
        else forarbeten_section(site, art)
    banner = (_version_banner(base_id, version) if version
              else (_expired_banner(props) if expired else ""))
    # external links, law-level commentary and who cites the act as a whole --
    # the rail's default panel. A lydelse page is not the citable document
    # (citations always target the current consolidation), so it shows none.
    rail.add_document(own_forarbeten, inbound=not version)
    body_classes = []
    if version:
        body_classes.append("inaktuell")
    elif expired:
        body_classes.append("expired")
    if parallel_appendix:
        body_classes.append("parallel-appendix")
    return ENV.get_template("sfs.html").render(page_context(
        title, "Författning", doc_meta(meta, art.get("source_url")),
        toc=render_toc(toc),
        eyebrow=("%s · äldre lydelse" % lb.short_id if version
                 else lb.short_id),
        island=rail.island(),
        body_class="".join(" " + name for name in body_classes),
        banner=Markup(banner), forarbeten=Markup(forarbeten),
        has_lydelser=bool(lydelser), structure=structure,
        andringar=andringar))
