"""The margin builders: one function per kind of cross-document context a
document page can carry in its rail.

Each takes the render `Site` plus the uri (and, where the context is per-node,
the anchor) and returns rail markup -- the genomförande of an EU directive, the
EU case law on a paragraf, the bemyndigande chain a föreskrift hangs from, the
regleringshierarki, the provisions that fill it out, the paragraf a repealed one
corresponds to and the case law that follows it there, and a remiss round's
verdict. `page.Rail` calls them; they are the "what does this node relate to"
half of the page kit, split off so `page.py` stays the node walk and the shell.

The pair imports both ways -- `page` calls these builders, and they call back
into the shared citer/link helpers (`page.href`, `page.RailSection`,
`page._citer_line`). Both sides import the *module*, never a name from it, and
neither reads the other's attributes at import time, so either import order
resolves.
"""

import re
from html import escape

from markupsafe import Markup

from . import catalog, hierarki, page
from .pinpoint import STYCKE_ORDINAL, human_fragment


def ext_link(url, label):
    """The `.ext` external-reference anchor markup, shared by every
    out-of-corpus link (EUR-Lex CELEX pages, guidance links, …)."""
    return Markup('<a class="ext" href="%s" rel="external">%s</a>') % (url,
                                                                       label)


def directive_link(site, directive, target=None):
    """Link to an EU act referenced by `directive`: our own hosted page (at
    `target`, defaulting to the act itself) when we've parsed it, else out to
    EUR-Lex via its CELEX. Shared by the genomför-EU margin (statute paragraf
    -> directive article) and the genomförande section (proposition ->
    directive article) -- both name the directive the same way (its catalogued
    title, falling back to the bare CELEX) and fall back to EUR-Lex
    identically."""
    target = target or directive
    celex = catalog.local(directive).rsplit("/", 1)[-1]
    # the reader-facing short heading ("NIS 2-direktivet"), as the act's own page
    # shows it, keeps the genomför margin compact; fall back to the full official
    # title (unparsed act: no stored heading) then the bare CELEX
    label = (catalog.document_display(site.con, directive)
             or page._doc_title(site, directive) or celex)
    if site.has(directive):
        return '<a href="%s">%s</a>' % (escape(page.href(target)), escape(label))
    return ext_link(page._external_href(directive), label)


# the stycke ordinal table lives in `lib.pinpoint`, the dependency-free leaf this
# module was factored into, so the EU and SFS pinpoint prose cannot drift apart
_STYCKE_ORDINAL = STYCKE_ORDINAL


def _stycke_label(sfs_pin):
    """A stycke/punkt element-id suffix ("S1", "S3N2") as citation prose
    ("första stycket", "tredje stycket 2"). The resolver only stores pinpoints
    it verified against the published law's minted ids, so the shape holds.

    Returns None past the ordinal table. This is reader-facing citation prose
    in a statute margin, and "21 stycket genomför artikel …" is not Swedish --
    dropping the pinpoint (the claim survives, unnarrowed) beats shipping
    something no lawyer would write. Twenty styckens is already far past any
    real paragraf."""
    m = re.fullmatch(r"S(\d+)(?:N(\d+[a-z]?))?", sfs_pin)
    assert m, "malformed sfs_pinpoint %r in the genomforande table" % sfs_pin
    ordinal = _STYCKE_ORDINAL.get(int(m.group(1)))
    if not ordinal:
        return None
    label = "%s stycket" % ordinal
    return "%s %s" % (label, m.group(2)) if m.group(2) else label


def genomfor_margin(site, sfs_uri, anchor):
    """Statute-paragraf margin: the EU directive article(s) this paragraf
    transposes (genomför), with the proposition as provenance (§7d). The mirror
    of the directive article's inbound, which shows this statute paragraf."""
    rows = catalog.genomfor_for(site.con, sfs_uri, anchor)
    if not rows:
        return []
    items = []
    for directive, article, prop_uri, prop_label, pinpoint, partial, sfs_pin in rows:
        dlink = directive_link(site, directive, directive + "#" + article)
        prov = ('<a href="%s">%s</a>' % (escape(page.href(prop_uri)), escape(prop_label))
                if prop_label and site.has(prop_uri) else escape(prop_label or ""))
        stycke = _stycke_label(sfs_pin) if sfs_pin else None
        items.append('<li>%sgenomför%s artikel %s i %s%s</li>'
                     % (escape(stycke + " ") if stycke else "",
                        " delvis" if partial else "", escape(pinpoint or article),
                        dlink, ' <span class="prov">(%s)</span>' % prov if prov else ""))
    return [page.RailSection("genomfor", "Genomför EU-rätt", len(items),
                        "<ul>%s</ul>" % "".join(items))]


EU_CASELAW_CAP = 5   # cases shown expanded in the paragraf rail; the rest
                     # are rendered too, collapsed behind "+N till"


def _act_short_id(site, uri):
    """An EU act's citable short form for a lineage attribution -- "2004/18/EG"
    from the catalog's stamped short id, the CELEX when the act is not in the
    corpus (a predecessor named by a correlation table but never downloaded)."""
    row = site.con.execute("SELECT short_id FROM documents WHERE uri = ?",
                           (uri,)).fetchone()
    return (row[0] if row and row[0] else catalog.local(uri))


def _caselaw_provenance(site, arts):
    """How the rail attributes a case to the citation(s) it was found through.

    A citation of a directly transposed pinpoint needs no explanation ("om
    artikel 57.4"); one reached through the lineage layer does, because the
    reader is looking at a paragraf that transposes 2014/24 and the case is
    about 92/50 -- so the hop is named ("om artikel 18 i 92/50/EEG, motsvarar
    artikel 12"). `arts` is the {(act uri, cited pinpoint, transposed atom,
    hops)} the case was assigned by (`catalog.caselaw_anchored`).

    Article numbers sort short-then-lexical, never by length alone: `key=len`
    leaves ties in set-iteration order, which varies with the interpreter's
    string hash seed, so two generate runs over an unchanged corpus would emit
    "artikel 12, 57" and "artikel 57, 12" and churn the page."""
    direct = sorted({cited for _u, cited, _t, hop in arts if not hop},
                    key=lambda a: (len(a), a))
    if direct:
        return "om artikel %s" % ", ".join(direct)
    act_uri, cited, transposed, _hop = sorted(arts)[0]
    return "om artikel %s i %s, motsvarar artikel %s" % (
        cited, _act_short_id(site, act_uri), transposed)


def eu_caselaw_margin(site, sfs_uri, anchor):
    """Statute-paragraf rail: the EU court judgments interpreting what this
    paragraf transposes -- the join the genomförande layer exists for (LOU
    13 kap. 3 § -> artikel 57.4 i 2014/24 -> the cases citing artikel 57.4).
    The whole statute is assigned in one `catalog.caselaw_anchored` pass so
    each citation lands on exactly the paragraf whose genomförande pinpoint
    matches it best; cases newest first. The memo is primed by `render_sfs`
    with the consolidation's live anchor set -- an unprimed document has no
    statute rail (only statutes have genomförande claims), and recomputing
    here without the anchor set would quietly reintroduce the dead-anchor
    case-swallowing the priming exists to prevent. Every case is rendered;
    those past EU_CASELAW_CAP start collapsed behind a disclosure."""
    ordered = site.caselaw_memo.get(sfs_uri, {}).get(anchor)
    if not ordered:
        return []
    items = []
    for (uri, label, descriptive, _date), arts in ordered:
        name = descriptive or label or uri.rsplit("/", 1)[-1]
        link = ('<a href="%s">%s</a>' % (escape(page.href(uri)), escape(name))
                if site.has(uri) else escape(name))
        items.append('<li>%s <span class="prov">(%s)</span></li>'
                     % (link, escape(_caselaw_provenance(site, arts))))
    return [page.RailSection("eu-caselaw", "EU-domstolens praxis", len(items),
                        page._capped_list(items, EU_CASELAW_CAP, "till"))]


def eu_corresponding_cases_margin(site, uri):
    """EU-act article margin: the CJ/TJ/FJ judgments decided under the
    predecessor(s) this article's own recast lineage traces back to (
    `catalog.predecessor_atoms`) -- one section per predecessor generation,
    headed "Äldre praxis om motsvarande bestämmelse (<the predecessor
    article, linked>)", the `corresponding_cases_margin` pattern carried over
    from sfs paragrafs to EU articles. Kept apart from the page's own
    "EU-domstolens praxis" section (`page._inbound_groups`), which shows only
    judgments citing *this* article: Lindqvist (C-101/01) cites 95/46/EG
    artikel 8, not GDPR artikel 9, so it belongs in this section, not that
    one, even though GDPR artikel 9 is where a reader looks for it.

    The lineage can come from an act's own jämförelsetabell (`correspondence`)
    or a hand-authored `.corr` layer (`eurlex.correspond.hand_rows`) for one
    that states none -- this margin does not care which. The predecessor's own
    citation (`directive_link`) falls back to EUR-Lex when we hold no page for
    it, the common case for a recast's older generations."""
    base, _, atom = uri.partition("#")
    if not atom:
        return []
    out = []
    for old_uri, old_atom, _hop in catalog.predecessor_atoms(site.con, base, atom):
        old_full = old_uri + "#" + old_atom
        rows = [r for r in catalog.inbound(site.con, old_full)
                if r[4] == "eurlex" and catalog.is_cj_judgment(r[0])]
        if not rows:
            continue
        old_label = "artikel %s %s" % (old_atom, _act_short_id(site, old_uri))
        cite = "artikel %s i %s" % (escape(old_atom), directive_link(site, old_uri, old_full))
        links = ['<li><a href="%s">%s</a></li>'
                 % (escape(page.href(from_uri + ("#" + a if a else ""))),
                    escape(page.describe_citer(from_uri, a, label, title, source)))
                 for from_uri, a, label, title, source in rows]
        out.append(page.RailSection(
            "eu-aldre-praxis",
            "Äldre praxis om motsvarande bestämmelse (%s)" % old_label,
            len(links), '<div class="rail-prov">%s</div>%s'
            % (cite, page._capped_list(links, EU_CASELAW_CAP, "till"))))
    return out


def _bemyndigande_margin(site, uri):
    """Statute-paragraf margin: the agency föreskrifter issued (meddelade) with
    stöd av this paragraf -- the inbound side of the bemyndigande edge, mirror of
    each föreskrift's outbound 'Bemyndigande'. So the paragraf that delegates
    rule-making power lists the regulations made under it. The föreskrift links to
    its own page where present, else shows as text (an fs we have not parsed)."""
    rows = catalog.bemyndigande_inbound(site.con, uri)
    if not rows:
        return []
    items = []
    for from_uri, label, title in rows:
        name = label or catalog.local(from_uri)
        link = ('<a href="%s">%s</a>' % (escape(page.href(from_uri)), escape(name))
                if site.has(from_uri) else '<span class="noref">%s</span>'
                % escape(name))
        sub = (' <span class="prov">%s</span>' % escape(title)
               if title and title != name else "")
        items.append("<li>%s%s</li>" % (link, sub))
    return [page.RailSection("bemyndigande",
                        "Föreskrifter meddelade med stöd av denna paragraf",
                        len(items), "<ul>%s</ul>" % "".join(items))]


def _law_title(site, base):
    """A law's display title from the catalog, whitespace-collapsed (SFS titles
    can carry a trailing CR/LF), falling back to its local id."""
    return " ".join((page._doc_title(site, base) or catalog.local(base)).split())


def _chain_up_index(con):
    """doc -> its distinct chain parents [(parent, parent level)], stated
    edges (norm_chain) and delegation-derived ones alike. Doc-level: the
    "Normkedja" row names documents, never provisions."""
    up = {}
    for lower, upper, ulvl in con.execute(
            "SELECT DISTINCT lower_uri, upper_uri, upper_level FROM norm_chain"):
        up.setdefault(lower, {})[upper] = ulvl
    for lower, upper in con.execute(
            "SELECT DISTINCT lower_uri, upper_uri FROM delegation_edge"):
        up.setdefault(lower, {}).setdefault(upper, 1)
    return {k: sorted(v.items(), key=lambda x: (x[1], x[0]))
            for k, v in up.items()}


def _chain_down_index(con):
    """doc -> {child level: sorted child uris}, the downward half of the
    Normkedja row. Children are kept by name, not count: a directive
    "implemented by 2 lagar" must say which two (Staffan, 2026-08-28)."""
    down = {}
    for upper, lower, llvl in con.execute(
            "SELECT DISTINCT upper_uri, lower_uri, lower_level "
            "FROM norm_chain"):
        down.setdefault(upper, {}).setdefault(llvl, set()).add(lower)
    for upper, lower in con.execute(
            "SELECT DISTINCT upper_uri, lower_uri FROM delegation_edge"):
        down.setdefault(upper, {}).setdefault(2, set()).add(lower)
    return {k: {lvl: sorted(docs) for lvl, docs in v.items()}
            for k, v in down.items()}




CHAIN_FAN_CAP = 5   # Normkedja entries named inline per level;
                    # the rest fold behind a +N fler disclosure


def chain_meta(site, doc_uri):
    """The document-level norm chain as one metadata row, the current
    document marked: "(EU) 2016/679 → Dataskyddslag (2018:218) →
    **IMYFS 2024:1**". A level with several documents names them all, side
    by side and uncapped -- "implemented by 2 lagar" must say which two
    (Staffan, 2026-08-28). Upward, the current document's immediate parents
    all show; further up the spine follows one deterministic path (lowest
    level, then uri). Downward, every direct child shows, grouped by rung.
    None off the chain."""
    if doc_uri not in site.chain_up and doc_uri not in site.chain_down:
        return None

    def name(uri):
        # the citable short id ("2018:218", "IMYFS 2024:1", "(EU) 2016/679"),
        # never a title -- the strip must fit on one line (Staffan 2026-08-28)
        row = site.con.execute(
            "SELECT short_id, label FROM documents WHERE uri = ?",
            (uri,)).fetchone()
        return (row and (row[0] or row[1])) or catalog.local(uri)

    def link(uri):
        return ('<a href="%s">%s</a>' % (escape(page.href(uri)), escape(name(uri)))
                if site.has(uri) else escape(name(uri)))

    def fan(uris):
        # every document on the level is named; past the cap they fold behind
        # the site's usual "+N fler" disclosure rather than truncating
        named = ", ".join(link(u) for u in uris[:CHAIN_FAN_CAP])
        if len(uris) > CHAIN_FAN_CAP:
            named += (' <details class="more inline"><summary>+%d fler'
                      '</summary>%s</details>'
                      % (len(uris) - CHAIN_FAN_CAP,
                         ", ".join(link(u) for u in uris[CHAIN_FAN_CAP:])))
        return named

    levels = []
    parents = site.chain_up.get(doc_uri, [])
    if parents:
        levels.append(fan([u for u, _l in parents]))
        node, seen = parents[0][0], {doc_uri, parents[0][0]}
        while node in site.chain_up and len(levels) < 4:
            up = site.chain_up[node]
            node = up[0][0]
            if node in seen:
                break
            seen.add(node)
            levels.append(link(node))
    parts = list(reversed(levels))
    parts.append('<strong class="here">%s</strong>' % escape(name(doc_uri)))
    for _lvl, uris in sorted(site.chain_down.get(doc_uri, {}).items()):
        parts.append(fan(uris))
    return Markup(" → ".join(parts))


def regleringshierarki_margin(site, doc_uri, anchors):
    """One rail line per concept whose regleringshierarki row sits on (or
    under) one of this panel's anchors (O5): "betydande incident", linking the
    ladder at its own anchor on the concept's page. Row anchors sit deeper
    than the panel node (a definition at K1P2S1N10 under the K1P2 panel), so
    the match is containment, over the Site's per-document list."""
    rows = site.hierarki.get(doc_uri)
    if not rows:
        return []
    items, seen = [], set()
    for row_anchor, concept, aid in rows:
        if concept in seen or not any(
                a and hierarki._anchor_within(row_anchor, a) for a in anchors):
            continue
        seen.add(concept)
        term = concept.rsplit("/", 1)[-1].replace("_", " ")
        items.append('<li><a href="%s#%s">%s</a></li>'
                     % (escape(page.href(concept)), escape(aid), escape(term)))
    if not items:
        return []
    return [page.RailSection("regleringshierarki", "Regleringshierarki",
                        len(items), "<ul>%s</ul>" % "".join(items))]


RE_KAPITEL_ANCHOR = re.compile(r"^K\d+[a-z]?$")


def fyller_ut_margin(site, doc_uri, anchors):
    """The chapter rail line on a föreskrift whose subject no concept anchors
    (PRD §8): "Dessa föreskrifter fyller ut 7 kap. 2 § fartygssäkerhetslagen,
    som genomför direktiv 2009/45/EG artikel ...". Document-level wording --
    the chain is known per document, and "Detta kapitel" would assert a
    chapter-level fact the data does not hold until the ai-* pass splits the
    pins (PRD phase 3). Shown once per chapter panel, only where the upward
    chain is unambiguous (`hierarki.fyller_ut_index`)."""
    entry = site.fyller_ut.get(doc_uri)
    if not entry or not any(a and RE_KAPITEL_ANCHOR.match(a) for a in anchors):
        return []
    lag, lag_pin = entry["lag"]
    target = "%s#%s" % (lag, lag_pin) if lag_pin else lag
    prose = 'Dessa föreskrifter fyller ut <a href="%s">%s%s</a>' % (
        escape(page.href(target)),
        escape(human_fragment(lag_pin) + " " if lag_pin else ""),
        escape(_law_title(site, lag)))
    tails = ["artikel %s i %s"
             % (escape(pinpoint or article),
                directive_link(site, directive, directive + "#" + article))
             for directive, article, pinpoint in entry["direktiv"]]
    if tails:
        prose += ", som genomför " + ", ".join(tails)
    return [page.RailSection("fyller-ut", "Regleringshierarki", 1,
                        "<ul><li>%s</li></ul>" % prose, flat=True)]


def _corr_phrase(relation, scope):
    """How an old paragraf's margin names its successor, from the correspondence's
    relation/scope: "motsvaras numera huvudsakligen av", "har förts över till"."""
    if relation == "overfort":
        return "har förts över till"
    return {"delvis": "motsvaras numera delvis av",
            "i_huvudsak": "motsvaras numera huvudsakligen av",
            "i_sak": "motsvaras numera i sak av"}.get(scope, "motsvaras numera av")


def corresponds_margin(site, uri):
    """Old (repealed) statute paragraf margin: the new-law paragraf that now
    corresponds to this one, from the `.corr` correspondence layer -- "Denna
    paragraf motsvaras numera huvudsakligen av <ny paragraf>". The new side does
    not show the mirror line: that the new paragraf corresponds to the old one is
    already plain from its författningskommentar."""
    # same-law renumbering ('betecknas') edges are not supersessions -- the
    # old beteckning is a live provision today; renumbered_refs_margin's job
    rows = [r for r in catalog.correspondence_for_old(site.con, uri)
            if r[1] != "betecknas"]
    if not rows:
        return []
    items, seen = [], set()
    for new_uri, relation, scope, _prop, _ikraft in rows:
        if new_uri in seen:        # one line per successor paragraf, not per stycke
            continue
        seen.add(new_uri)
        base = new_uri.split("#")[0]
        label = ("%s %s" % (human_fragment(new_uri.partition("#")[2]),
                            _law_title(site, base))).strip()
        link = ('<a href="%s">%s</a>' % (escape(page.href(new_uri)), escape(label))
                if site.has(base) else escape(label))
        items.append('<li>Denna paragraf %s %s</li>'
                     % (_corr_phrase(relation, scope), link))
    return [page.RailSection("motsvarighet", "Motsvarighet", len(items),
                        "<ul>%s</ul>" % "".join(items))]


CORR_DEPTH = 3      # how many re-enactments back the case-law margin reaches


def corresponding_cases_margin(site, uri):
    """New statute paragraf margin: the legal cases (rättsfall) that cite the
    old, repealed provisions this one corresponds to -- one section per
    predecessor, headed "Äldre rättsfall för motsvarande bestämmelse (<the
    predecessor provision, linked>)", so a reader of the new law finds the
    case law decided under it. The correspondence chain is walked
    *transitively* (socialtjänstlagen 2025:400 -> 2001:453 -> 1980:620,
    breadth-first, CORR_DEPTH re-enactments deep): each generation's case law
    cites its own generation's provision. The correspondences are read from
    the `.corr` layers; the cases are the generic inbound on each old
    paragraf, filtered to case law."""
    out, seen = [], set()
    frontier = [uri]
    for _hop in range(CORR_DEPTH):
        nxt = []
        for at in frontier:
            for old_uri, rel, _scope, _prop, _ikraft in \
                    catalog.correspondence_for_new(site.con, at):
                if rel == "betecknas":
                    # same-law renumbering: renumbered_refs_margin's job
                    continue
                if old_uri in seen:  # one section per old paragraf, not per stycke
                    continue
                seen.add(old_uri)
                nxt.append(old_uri)
                # unlimited: the LIMIT used to be applied before the dv filter,
                # so a paragraf whose first 41 citers were förarbete rendered no
                # cases at all even with hundreds in the catalog
                rows = [r for r in catalog.inbound(site.con, old_uri)
                        if r[4] == "dv"]
                if not rows:
                    continue
                base = old_uri.split("#")[0]
                old_label = ("%s %s" % (
                    human_fragment(old_uri.partition("#")[2]),
                    _law_title(site, base))).strip()
                cite = ('<a href="%s">%s</a>'
                        % (escape(page.href(old_uri)), escape(old_label))
                        if site.has(base) else escape(old_label))
                links = ['<li><a href="%s">%s</a></li>'
                         % (escape(page.href(from_uri + ("#" + a if a else ""))),
                            escape(page.describe_citer(from_uri, a, label, title,
                                                  source)))
                         for from_uri, a, label, title, source in rows]
                out.append(page.RailSection(
                    "aldre-rattsfall",
                    "Äldre rättsfall för motsvarande bestämmelse (%s)" % old_label,
                    len(links), '<div class="rail-prov">%s</div>%s'
                    % (cite, page._capped_list(links, page.INBOUND_CAP))))
        frontier = nxt
        if not frontier:
            break
    return out


def _reassigned_before(site, uri):
    """The date this anchor's beteckning last changed meaning: the newest
    same-law renumbering that gave the label to another provision (a
    'betecknas' edge FROM it). References dated earlier mean the *old*
    provision and must not appear in this anchor's own inbound panel -- they
    surface on the successor's renumbered_refs_margin instead. None when the
    label was never reassigned (or the register lacks the date)."""
    return max((ik for _new, rel, _s, _p, ik in
                catalog.correspondence_for_old(site.con, uri)
                if rel == "betecknas" and ik), default=None)


def renumbered_refs_margin(site, uri):
    """New-beteckning paragraf margin: the references made to this provision
    under its *previous* beteckning(ar), from the same-law 'betecknas'
    correspondence edges (SFSR omfattning): "Hänvisningar till tidigare
    beteckning 4 kap. 4 §" under RF 4 kap. 6 §. A reference to the old label
    counts only when its document predates the renumbering's entry into force
    (and postdates the label's previous reassignment, if any) -- later
    references to that label mean the provision now carrying it.

    Chains of renumberings compose, but each hop must stay on this
    provision's own lineage: the provision arrived at the current label via
    the *latest* 'betecknas' edge strictly before the hop's upper bound, and
    only that edge's old label is a previous beteckning of it. An edge at or
    after the bound describes the label's *next* occupant (RF 2010:1408 moves
    12 kap. -> 13 kap. and 13 kap. -> 15 kap. on the same date: from 15 kap.
    the 13->15 hop must not continue through 12->13, whose references belong
    on the 13 kap. pages). A dateless edge (old registers) cannot be
    interpreted and ends its chain."""
    out = []
    frontier = [(uri, None)]        # (anchor uri, upper date bound so far)
    for _hop in range(CORR_DEPTH):
        nxt = []
        for at, upper in frontier:
            edges = [(old_uri, ikraft) for old_uri, rel, _s, _p, ikraft in
                     catalog.correspondence_for_new(site.con, at)
                     if rel == "betecknas" and ikraft
                     and (upper is None or ikraft < upper)]
            if not edges:
                continue
            # the arrival at this label; ties are one renumbering event
            # mapping several old labels onto it
            arrival = max(ik for _o, ik in edges)
            for old_uri, ikraft in edges:
                if ikraft != arrival:
                    continue        # an earlier occupant's arrival, not ours
                # the label's previous reassignment opens the window
                lower = max((ik for _n, r2, _s2, _p2, ik in
                             catalog.correspondence_for_old(site.con, old_uri)
                             if r2 == "betecknas" and ik and ik < arrival),
                            default=None)
                nxt.append((old_uri, arrival))
                rows = [r for r in catalog.inbound_collapsed(site.con, [old_uri])
                        if r[5] and r[5] < arrival
                        and (not lower or r[5] >= lower)]
                if not rows:
                    continue
                label = human_fragment(old_uri.partition("#")[2])
                out.append(page.RailSection(
                    "tidigare-beteckning",
                    "Hänvisningar till tidigare beteckning %s (före %s)"
                    % (label, arrival), len(rows),
                    page._capped_list([page._citer_line(r) for r in rows])))
        frontier = nxt
        if not frontier:
            break
    return out


# Five levels rather than three, as geometric shapes: direction by the triangle's
# orientation, strength by whether it is filled, a diamond for neither. A lone
# "−" read as a dash separating the organisation from its quote rather than as a
# minus sign, which inverted the meaning of every critical entry at a glance.
# Text-presentation Unicode (Geometric Shapes), so it renders as a glyph in the
# page's own colour rather than as a colour emoji.
_SENTIMENT_LEVELS = (
    (-0.6, "sentiment-neg-strong", "▼", "avstyrker eller riktar stark kritik"),
    (-0.15, "sentiment-neg", "▽", "kritisk"),
    (0.15, "sentiment-neutral", "◇", "neutral eller blandad"),
    (0.6, "sentiment-pos", "△", "positiv"),
    (1.01, "sentiment-pos-strong", "▲", "tillstyrker helt"),
)


def _sentiment_level(sentiment):
    """The `_SENTIMENT_LEVELS` row a score falls in -- the one place the band
    edges are read.

    Read twice, from the table once: the mark beside an answer and the verdict
    over a section have to agree about where "neutral" ends, and while each
    carried its own copy of ±0.15 they did not. A score of exactly 0.15 drew the
    positive triangle and was counted as neither positive nor negative, so three
    answers at 0.15 showed three "△ positiv" marks over "fått blandat
    mottagande"."""
    row = next((r for r in _SENTIMENT_LEVELS if sentiment < r[0]), None)
    # ai_analyze validates the score into [-1, 1] and the top band closes above
    # it, so falling off the end means the layer was written by something that
    # did not (rule:fail-fast -- a bare StopIteration names none of that)
    assert row is not None, (
        "sentiment %r is outside the scale ai_analyze validates" % (sentiment,))
    return row


def _sentiment_span(sentiment):
    """A compact, self-contained sentiment indicator for the remiss rail: a glyph
    and a css class the stylesheet colours, plus a `title` naming the level in
    words -- the shape alone is a legend the reader does not have. `sentiment` is
    a validated numeric score from the `.ann` (ai_analyze enforces [-1, 1]), so it
    is not user-escaped HTML."""
    _bound, cls, glyph, label = _sentiment_level(sentiment)
    return '<span class="sentiment %s" title="%s">%s</span>' % (cls, label, glyph)


def _remiss_summary(items, subject):
    """`{subject, verdict}` summarising how a section was received, or None when
    too few answers addressed it to generalise. Returned in parts, not as a
    sentence: the verdict is emphasised and the emphasis belongs in the template
    (rule:markup-in-templates).

    Below three answers there is no "mottagande" to describe -- two critical
    answers are two opinions, not a pattern -- and a summary over one or two
    would read as a finding the evidence does not support."""
    if len(items) < 3:
        return None
    # counted by the band each score lands in, not by a second copy of its edges
    sides = [_sentiment_level(it["sentiment"])[1] for it in items]
    neg = sum(1 for c in sides if c.startswith("sentiment-neg"))
    pos = sum(1 for c in sides if c.startswith("sentiment-pos"))
    n = len(items)
    if neg == n:
        verdict = "genomgående kritiserats"
    elif pos == n:
        verdict = "genomgående tillstyrkts"
    elif neg >= 2 * n / 3:
        verdict = "övervägande kritiserats"
    elif pos >= 2 * n / 3:
        verdict = "övervägande tillstyrkts"
    else:
        verdict = "fått blandat mottagande"
    return {"subject": subject, "verdict": verdict}
