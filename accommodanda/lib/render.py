"""Render parsed artifacts to a static, interlinked HTML site -- the `generate`
phase (REWRITE.md §6).

Two things make it the derived layer rather than a dumb pretty-printer:

  * outbound links are live -- every inline citation run becomes an <a> to the
    cited document's own page (and exact paragraph), so a case links into the
    statute it cites;
  * inbound links are annotated -- each statute paragraph's context (the cases
    and laws that cite *it*, queried from the catalog) is collected into a JSON
    island and shown in a right-hand rail that the client swaps as you scroll.
    That round-trip (case -> paragraph -> back to every case on that paragraph)
    is the signature lagen.nu feature.

The artifact JSON is the contract: a single generic node walk renders both the
SFS structure tree and the DV body, keyed on each node's `type`. Inbound links
are surfaced at two granularities: per *paragraph* (the scroll-driven context
rail, fed by `Rail`) and per *document* (a panel for citations to the whole law
or case -- the 27% of citations that carry no #fragment, and all case inbound).

A `Site` carries the catalog plus the set of document URIs that actually exist,
so a citation to a document we don't have (yet) renders as plain text rather
than a broken link.
"""

import json
import re
from dataclasses import dataclass
from html import escape
from pathlib import Path

from . import catalog
from . import layout
from .catalog import BASE
from .wikitext import begrepp_uri
from ..eurlex.model import doctype as eurlex_doctype


@dataclass
class Site:
    con: object
    known: set                          # document root uris present
    snippets: dict = None               # fragment uri -> tooltip text (lazy cache)

    @classmethod
    def from_catalog(cls, con):
        return cls(con, {u for (u,) in con.execute("SELECT uri FROM documents")},
                   {})

    def has(self, uri):
        return catalog.strip_fragment(uri) in self.known

    def snippet(self, uri):
        """Tooltip text for a link target (the target paragraph + its list
        items), cached per generate run; '' if the catalog has none."""
        if uri not in self.snippets:
            self.snippets[uri] = catalog.snippet(self.con, uri) or ""
        return self.snippets[uri]


# --------------------------------------------------------------------------
# uri -> local href / output path
# --------------------------------------------------------------------------

def split_uri(uri):
    base, _, frag = uri.partition("#")
    return catalog.local(base), frag


# the uri -> output-path / public-route rule now lives in lib.layout (the single
# home for on-disk and on-web location rules)
doc_relpath = layout.page_relpath


EXT = BASE + "ext/"                          # the "external reference" namespace
CELEX = BASE + "ext/celex/"
EURLEX = "https://eur-lex.europa.eu/legal-content/SV/TXT/?uri=CELEX:%s"


def is_external(uri):
    """A lagen.nu `ext/` URI identifies a document the site doesn't host
    (EU acts via CELEX, …) -- it resolves to an external service, not a page."""
    return uri.startswith(EXT)


def href(uri):
    if not uri.startswith(BASE):
        return uri  # already-absolute external
    _, frag = split_uri(uri)
    return "/" + doc_relpath(uri) + ("#" + frag if frag else "")


def external_href(uri):
    """Where an ``ext/`` reference we don't host resolves -- EUR-Lex for a
    CELEX (the EU act on the official site), else the uri itself."""
    if uri.startswith(CELEX):
        return EURLEX % catalog.local(uri)[len("ext/celex/"):].split("#")[0]
    return uri


# a minted fragment id decomposes into K(ap)/§/mom/stycke/punkt/mening segments
# (the FRAGMENT_LETTERS scheme); render it the way a lawyer would pinpoint it
FRAG_LABEL = {"K": "kap.", "P": "§", "O": "mom.", "S": "st", "N": "p", "M": "men."}
_FRAG_SEG = re.compile(r"([KPOSNM])([0-9a-zåäö]+)")


def human_fragment(frag):
    """A fragment id -> a human pinpoint: "K2P16S5" -> "2 kap. 16 § 5 st";
    "sid39" -> "s. 39"; change markers ("L1988:187") and unknowns -> ""."""
    if not frag:
        return ""
    if frag.startswith("sid"):
        return "s. " + frag[3:]
    segs = _FRAG_SEG.findall(frag)
    return " ".join("%s %s" % (val, FRAG_LABEL[letter]) for letter, val in segs)


def describe_citer(from_uri, anchor, label, title, source):
    """Human label for an inbound entry: the citing document's name plus the
    pinpoint where the citation sits -- "Skollag (2010:800) 2 kap. 16 § 5 st"
    for a statute, the referat/identifier for a case/förarbete. Commentary
    shows its author (the paragraph is the one being read, so no pinpoint)."""
    if source == "kommentar":
        # the anchor is the commented paragraph; showing it makes the many
        # sections of one commentary distinct (and useful) on a concept page
        pin = human_fragment(anchor)
        if pin:
            return "Kommentar " + pin
        return "Kommentar" + (" – %s" % title if title and title != "Kommentar"
                              else "")
    name = (title or label) if source == "sfs" else label
    pin = human_fragment(anchor) if source in ("sfs", "forarbete") else ""
    return name + (" " + pin if pin else "")


# inbound panel section order + heading; commentary first (it's the closest
# reading aid to a paragraph), then the machine-extracted sources, then concepts
INBOUND_GROUPS = [("kommentar", "Kommentar"), ("sfs", "Författningar"),
                  ("forarbete", "Förarbeten"), ("dv", "Rättsfall"),
                  ("begrepp", "Begrepp")]


# --------------------------------------------------------------------------
# table of contents (a sticky, scrollspy-driven outline of a document)
# --------------------------------------------------------------------------

class Toc:
    """Collects a document's headings as it is rendered, so the body's anchor
    ids and the TOC's links agree by construction. A heading without a node id
    (DV/förarbete) is given a generated, stable-per-page anchor."""

    def __init__(self):
        self.entries = []                # (anchor, text, level)
        self._n = 0

    def add(self, node_id, text, level):
        if not node_id:
            self._n += 1
            node_id = "sec%d" % self._n
        if text.strip():
            self.entries.append((node_id, text, level))
        return node_id


def plain(runs):
    """Heading text for the TOC: inline runs flattened to plain text."""
    return catalog.runs_text(runs).strip()


MIN_TOC = 3   # below this many headings a TOC adds clutter, not navigation


def render_toc(toc):
    if len(toc.entries) < MIN_TOC:
        return ""
    items = "".join('<a href="#%s" class="lvl%d">%s</a>'
                    % (escape(anchor), min(level, 3), escape(text))
                    for anchor, text, level in toc.entries)
    return ('<nav class="toc"><div class="toc-h">Innehåll</div>'
            '<div class="toc-list">%s</div></nav>' % items)


# --------------------------------------------------------------------------
# inline runs + inbound annotation
# --------------------------------------------------------------------------

INBOUND_CAP = 40   # max citing docs listed before "+N fler"


def render_runs(runs, site):
    if isinstance(runs, str):
        return escape(runs)
    out = []
    for run in runs:
        if isinstance(run, str):
            out.append(escape(run))
            continue
        uri = run["uri"]
        if site.has(uri):
            # a document we host (incl. EU acts we've parsed) -- local link;
            # hover shows the target paragraph (+ its list items). A "term" run
            # is an in-act use of a defined term: underlined, hover shows its
            # definition (the definition point's snippet).
            tip = site.snippet(uri)
            cls = ' class="term"' if run.get("kind") == "term" else ""
            out.append('<a%s href="%s"%s>%s</a>'
                       % (cls, escape(href(uri)),
                          (' title="%s"' % escape(tip)) if tip else "",
                          escape(run["text"])))
        elif is_external(uri):
            # an ext/ reference we don't host -- out to the external service
            # (EUR-Lex for a CELEX); becomes a local link once we parse it
            out.append('<a class="ext" href="%s" rel="external">%s</a>'
                       % (escape(external_href(uri)), escape(run["text"])))
        elif uri.startswith(BASE):
            # a lagen.nu document with no page yet -- show the text, not a
            # link that would 404. Becomes live once that doc is parsed.
            out.append('<span class="noref" title="%s">%s</span>'
                       % (escape(catalog.local(uri)), escape(run["text"])))
        else:
            out.append('<a class="ext" href="%s" rel="external">%s</a>'
                       % (escape(uri), escape(run["text"])))
    return "".join(out)


def _inbound_groups(site, uri):
    """Inbound entries grouped into per-source sections (Författningar /
    Förarbeten / Rättsfall), each a list of human-readable, pinpointed links.
    Returns the inner HTML, or None when nothing cites `uri`."""
    rows = catalog.inbound(site.con, uri, limit=INBOUND_CAP + 1)
    if not rows:
        return None
    shown, overflow = rows[:INBOUND_CAP], len(rows) > INBOUND_CAP
    bucket = {}
    for from_uri, anchor, label, title, source in shown:
        target = from_uri + ("#" + anchor if anchor else "")   # link to the pinpoint
        bucket.setdefault(source, []).append(
            '<li><a href="%s">%s</a></li>'
            % (escape(href(target)),
               escape(describe_citer(from_uri, anchor, label, title, source))))
    groups = [(src, heading) for src, heading in INBOUND_GROUPS if src in bucket]
    groups += [(s, s) for s in bucket if s not in dict(INBOUND_GROUPS)]
    html = "".join(
        '<div class="ingroup %s"><div class="ingroup-h">%s</div><ul>%s</ul></div>'
        % (src, escape(heading), "".join(bucket[src])) for src, heading in groups)
    if overflow:
        html += ('<div class="more">+%d fler</div>'
                 % (catalog.inbound_count(site.con, uri) - INBOUND_CAP))
    return html


def document_inbound(site, uri):
    """Document-level inbound: who cites the law/case/förarbete as a whole
    (the bare uri). Surfaces the citations no paragraph annotation shows."""
    groups = _inbound_groups(site, uri)
    return ('<section class="inbound-doc"><h2>Hänvisat till av</h2>%s</section>'
            % groups) if groups else ""


def genomfor_margin(site, sfs_uri, anchor):
    """Statute-paragraf margin: the EU directive article(s) this paragraf
    transposes (genomför), with the proposition as provenance (§7d). The mirror
    of the directive article's inbound, which shows this statute paragraf."""
    rows = catalog.genomfor_for(site.con, sfs_uri, anchor)
    if not rows:
        return ""
    items = []
    for directive, article, prop_uri, prop_label, pinpoint, partial in rows:
        celex = catalog.local(directive).rsplit("/", 1)[-1]
        dlabel = _doc_title(site, directive) or celex
        dlink = ('<a href="%s">%s</a>' % (escape(href(directive + "#" + article)),
                                          escape(dlabel))
                 if site.has(directive) else
                 '<a class="ext" href="%s" rel="external">%s</a>'
                 % (escape(external_href(directive)), escape(dlabel)))
        prov = ('<a href="%s">%s</a>' % (escape(href(prop_uri)), escape(prop_label))
                if prop_label and site.has(prop_uri) else escape(prop_label or ""))
        items.append('<li>genomför%s artikel %s i %s%s</li>'
                     % (" delvis" if partial else "", escape(pinpoint or article),
                        dlink, ' <span class="prov">(%s)</span>' % prov if prov else ""))
    return ('<aside class="genomfor"><div class="inbound-h">Genomför EU-rätt</div>'
            '<ul>%s</ul></aside>' % "".join(items))


class Rail:
    """Collects each paragraph's context panel (who cites it, and which EU
    article it transposes) as a document is rendered, keyed by the node's anchor
    id. Serialized to a JSON island the client swaps into the right rail as the
    reader scrolls -- the Gravitas "Kontext för …" rail. The link/href logic
    stays in Python; the client only moves pre-rendered HTML. A node carries a
    ``data-rail`` attribute (see `_rail_attr`) iff it has an entry here, so the
    scrollspy knows which elements drive the rail."""

    def __init__(self, site, doc_uri):
        self.site = site
        self.doc_uri = doc_uri
        self.data = {}

    def add(self, nid, pinpoint="", extra=""):
        """Record node `nid`'s rail panel if anything cites it, it transposes an
        EU article, or it carries an editorial `extra` section (the EU
        article<->recital links). Idempotent per id; no-op for context-less nodes."""
        if not nid or nid in self.data:
            return
        groups = _inbound_groups(self.site, self.doc_uri + "#" + nid)
        genomfor = genomfor_margin(self.site, self.doc_uri, nid)
        if not groups and not genomfor and not extra:
            return
        head = ('<div class="rail-h">Kontext%s</div>'
                % (' för <b>%s</b>' % escape(pinpoint) if pinpoint else ""))
        body = ('<div class="rail-sec"><div class="rail-sec-h">Hänvisat till av</div>'
                '%s</div>' % groups) if groups else ""
        self.data[nid] = head + body + extra + genomfor

    def island(self):
        """The ``<script type=application/json>`` island, or '' if no paragraph
        has context. ``</`` is escaped so the payload can't break out of the
        surrounding HTML."""
        if not self.data:
            return ""
        payload = json.dumps(self.data, ensure_ascii=False).replace("</", "<\\/")
        return ('<script type="application/json" id="lagen-context">%s</script>'
                % payload)


def _rail_attr(rail, nid):
    """`data-rail="id"` for a node the rail has context for, else ''."""
    return ' data-rail="%s"' % escape(nid) if nid and nid in rail.data else ""


# --------------------------------------------------------------------------
# generic node renderer (artifact type -> HTML)
# --------------------------------------------------------------------------

def _id_attr(nid):
    return ' id="%s"' % escape(nid) if nid else ""


def render_node(node, site, doc_uri, toc, rail, drop_marker=False):
    t = node.get("type")
    nid = node.get("id")

    if t == "tabell":
        rows = "".join(render_node(c, site, doc_uri, toc, rail)
                       for c in node.get("children", []))
        return "<table>%s</table>" % rows
    if t == "rad":
        cells = "".join("<td>%s</td>" % render_runs(c, site)
                        for c in node.get("cells", []))
        return "<tr>%s</tr>" % cells
    if t == "lista":
        items = "".join(render_node(c, site, doc_uri, toc, rail)
                        for c in node.get("children", []))
        return "<ul>%s</ul>" % items
    if t == "rubrik":
        text = node.get("text", [])
        anchor = toc.add(nid, plain(text), node.get("level") or 1)
        lvl = min(node.get("level") or 2, 5) + 1
        return '<h%d id="%s" class="rubrik">%s</h%d>' % (
            lvl, escape(anchor), render_runs(text, site), lvl)

    # the node's context (who cites it + which EU article it transposes) is
    # routed to the scroll-driven rail, not floated inline; the element is tagged
    # data-rail so the client knows it drives the rail. Leaf rubrik/tabell/rad/
    # lista nodes above carry no context.
    rail.add(nid, human_fragment(nid))
    ra = _rail_attr(rail, nid)

    if "text" in node:  # stycke/punkt/listelement/upphavd/moment (may nest)
        # the paragraf's own number now hangs in the gutter (drop_marker), so the
        # first stycke no longer repeats it inline; sub-stycken/punkter keep theirs
        marker = None if drop_marker else (node.get("beteckning") or node.get("ordinal"))
        num = '<span class="num">%s</span> ' % escape(str(marker)) if marker else ""
        tag = "li" if t in ("punkt", "listelement") else "p"
        body = "<%s%s%s>%s%s</%s>" % (tag, _id_attr(nid), ra, num,
                                      render_runs(node["text"], site), tag)
        # a stycke often introduces a list -- render its punkt/lista children
        # (previously dropped, so numbered lists vanished from the page)
        kids = node.get("children", [])
        if kids:
            inner = "".join(render_node(c, site, doc_uri, toc, rail) for c in kids)
            if any(c.get("type") == "punkt" for c in kids):
                inner = '<ol class="punkter">%s</ol>' % inner
            body += inner
        return body

    # container: paragraf, kapitel, avdelning, bilaga, overgangsbestammelse, ...
    if t in ("kapitel", "avdelning", "underavdelning"):
        label = {"kapitel": "kap.", "avdelning": "Avd.",
                 "underavdelning": "Avd."}[t]
        # the chapter number; its title is a rubrik child that already reads
        # "1 kap. Statsskickets grunder", so the chapter goes in the TOC via
        # that rubrik, not as a redundant bare-number entry here
        head_text = ("%s %s" % (node.get("ordinal", ""), label)).strip()
        children = "".join(render_node(c, site, doc_uri, toc, rail)
                           for c in node.get("children", []))
        head = '<h2%s class="kaprubrik">%s</h2>' % (_id_attr(nid),
                                                    escape(head_text))
        return '<section class="%s"%s>%s%s</section>' % (t, ra, head, children)

    if t == "paragraf":
        # hanging §-numeral in the gutter; the first stycke drops its inline number
        kids = node.get("children", [])
        children = "".join(
            render_node(c, site, doc_uri, toc, rail,
                        drop_marker=(i == 0 and c.get("type") == "stycke"))
            for i, c in enumerate(kids))
        gutter = ('<div class="paragraf-gutter"><span class="n">%s</span>'
                  '<a class="pilcrow" href="#%s" aria-label="Permalänk">§</a></div>'
                  % (escape(node.get("ordinal", "")), escape(nid or "")))
        return ('<section class="paragraf"%s%s>%s<div class="paragraf-body">%s</div>'
                '</section>' % (_id_attr(nid), ra, gutter, children))

    children = "".join(render_node(c, site, doc_uri, toc, rail)
                       for c in node.get("children", []))
    return '<section class="%s"%s%s>%s</section>' % (t or "node", _id_attr(nid),
                                                     ra, children)


# --------------------------------------------------------------------------
# page shells
# --------------------------------------------------------------------------

PAGE = """<!doctype html>
<html lang="sv"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%(title)s</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Source+Serif+4:ital,wght@0,400;0,500;0,600;1,400;1,500&display=swap">
<link rel="stylesheet" href="/style.css">
</head><body class="gr-root">
%(masthead)s
%(grid)s
%(island)s<script src="/scrollspy.js" defer></script>
</body></html>
"""

# masthead nav: label, browse route, the page kinds that mark it current
MAST_NAV = (("Lagar", "/sfs/", ("Författning",)),
            ("Rättsfall", "/dom/", ("Rättsfall",)),
            ("Förarbeten", "/forarbete/", ("Proposition", "SOU", "Ds",
             "Kommittédirektiv", "Förordningsmotiv", "Skrivelse", "Lagrådsremiss",
             "Sveriges internationella överenskommelser", "Förarbete")),
            ("EU-rätt", "/eurlex/", ("EU-förordning", "EU-direktiv", "EU-beslut",
             "EU-domstolen", "Fördrag", "EU-rättsakt")))


def _masthead(kind):
    links = "".join('<a href="%s"%s>%s</a>'
                    % (route, ' class="on"' if kind in act else "", label)
                    for label, route, act in MAST_NAV)
    return ('<header class="masthead">'
            '<a class="brand" href="/">lagen<em>.</em></a>'
            '<button class="search" type="button" data-search>'
            '<svg width="15" height="15" viewBox="0 0 16 16" fill="none" '
            'stroke="currentColor" stroke-width="1.5" aria-hidden="true">'
            '<circle cx="7" cy="7" r="5"></circle><path d="M11 11l4 4"></path></svg>'
            '<span>Sök lag, paragraf, rättsfall…</span>'
            '<span class="k">⌘K</span></button>'
            '<nav class="mast-nav">%s</nav></header>' % links)


def _source_link(source_url):
    """The document's authoritative-source ("Källa") link -- the publisher's own
    page for it, stamped onto the artifact by build.write_artifact. Absent for
    documents with no known source url."""
    return ('<p class="kalla"><a class="ext" href="%s" rel="external">Källa'
            '</a></p>' % escape(source_url)) if source_url else ""


def _frontmatter(eyebrow, title, subtitle, meta, source_url=None):
    eb = '<div class="eyebrow">%s</div>' % escape(eyebrow) if eyebrow else ""
    sub = '<p class="subtitle">%s</p>' % escape(subtitle) if subtitle else ""
    return ('<header class="frontmatter">%s<h1>%s</h1>%s%s%s</header>'
            % (eb, escape(title), sub, meta, _source_link(source_url)))


def page(title, kind, meta, body, toc="", eyebrow=None, subtitle=None,
         island="", solo=False, source_url=None):
    """Assemble a page. Document pages use the 3-column grid (TOC · reading
    column · context rail); `solo` pages (frontpage, browse indexes) drop the
    side columns for a single centered column."""
    front = _frontmatter(eyebrow, title, subtitle, meta, source_url)
    if solo:
        grid = ('<div class="gr-body solo"><main class="gr-main">%s%s</main></div>'
                % (front, body))
    else:
        grid = ('<div class="gr-body"><aside class="toc-col">%s</aside>'
                '<main class="gr-main">%s%s</main>'
                '<aside class="rail" id="rail" aria-live="polite"></aside></div>'
                % (toc, front, body))
    return PAGE % {"title": escape(title), "masthead": _masthead(kind),
                   "grid": grid, "island": island}


def render_sfs(art, site):
    props = art.get("metadata", {}).get("properties", {})
    title = props.get("dcterms:title") or ("SFS " + catalog.local(art["uri"]))
    meta = _meta_dl([
        ("Utfärdad", props.get("rpubl:utfardandedatum")),
        ("Ikraftträder", props.get("rpubl:ikrafttradandedatum")),
        ("Källa", props.get("dcterms:identifier")),
    ])
    toc = Toc()
    rail = Rail(site, art["uri"])
    body = document_inbound(site, art["uri"]) + "".join(
        render_node(n, site, art["uri"], toc, rail)
        for n in art.get("structure", []))
    return page(title, "Författning", meta, body, render_toc(toc),
                eyebrow="SFS " + catalog.local(art["uri"]), island=rail.island(),
                source_url=art.get("source_url"))


def render_dv(art, site):
    md = art.get("metadata", {})
    referat = art.get("referat") or []
    title = referat[0] if referat else art["uri"]
    meta = _meta_dl([
        ("Domstol", art.get("court_namn")),
        ("Avgörandedatum", art.get("avgorandedatum")),
        ("Målnummer", ", ".join(art.get("malnummer") or [])),
        ("Rättsområde", ", ".join(md.get("rattsomrade") or [])),
    ])
    summary = ('<p class="sammanfattning">%s</p>' % escape(md["sammanfattning"])
               if md.get("sammanfattning") else "")
    sokord = _keywords(md.get("nyckelord") or [], site)
    toc = Toc()
    rail = Rail(site, art["uri"])
    body = document_inbound(site, art["uri"]) + sokord + summary + "".join(
        render_node(b, site, art["uri"], toc, rail) for b in art.get("body", []))
    return page(title, "Rättsfall", meta, body, render_toc(toc),
                eyebrow=art.get("court_namn"), island=rail.island(),
                source_url=art.get("source_url"))


def _keywords(nyckelord, site):
    """Case keywords as links to their concept (begrepp) page where one
    exists -- the case→concept half of the keyword graph."""
    if not nyckelord:
        return ""
    items = []
    for n in nyckelord:
        uri = begrepp_uri(n)
        items.append('<a href="%s">%s</a>' % (escape(href(uri)), escape(n))
                     if site.has(uri) else escape(n))
    return '<p class="sokord"><span>Sökord</span> %s</p>' % " · ".join(items)


FA_TYPE_LABEL = {"prop": "Proposition", "sou": "SOU", "ds": "Ds",
                 "dir": "Kommittédirektiv", "fm": "Förordningsmotiv",
                 "skr": "Skrivelse", "lr": "Lagrådsremiss",
                 "so": "Sveriges internationella överenskommelser"}


def render_implements(art, site):
    """The genomför-direktiv statements pulled from a proposition's
    författningskommentar (§7d): which EU directive article each provision
    transposes. Each links to the directive -- its article on our EU page when we
    host it, else out to EUR-Lex."""
    recs = art.get("implements")
    if not recs:
        return ""
    items = []
    for r in recs:
        directive = r["directive"]
        celex = catalog.local(directive).rsplit("/", 1)[-1]
        label = _doc_title(site, directive) or celex
        target = r["uris"][0] if r.get("uris") else directive
        link = ('<a href="%s">%s</a>' % (escape(href(target)), escape(label))
                if site.has(directive) else
                '<a class="ext" href="%s" rel="external">%s</a>'
                % (escape(external_href(directive)), escape(label)))
        where = (("%s kap. %s § " % (r["chapter"], r["paragraf"]))
                 if r.get("chapter") and r.get("paragraf")
                 else ("%s § " % r["paragraf"]) if r.get("paragraf") else "")
        ref = ", ".join(r["pinpoints"] or r["articles"])
        items.append('<li>%sgenomför%s artikel %s i %s</li>'
                     % (escape(where), " delvis" if r.get("partial") else "",
                        escape(ref), link))
    return ('<section class="genomforande"><h2>Genomför EU-direktiv</h2>'
            '<ul>%s</ul></section>' % "".join(items))


def render_forarbete(art, site):
    title = art.get("title") or art.get("identifier") or art["uri"]
    meta = _meta_dl([("Beteckning", art.get("identifier")),
                     ("Typ", FA_TYPE_LABEL.get(art.get("type"), art.get("type"))),
                     ("Datum", art.get("date"))])
    parts = [document_inbound(site, art["uri"]), render_implements(art, site)]
    toc = Toc()
    rail = Rail(site, art["uri"])
    cur = None
    for b in art.get("body", []):
        if b.get("page") and b["page"] != cur:
            cur = b["page"]
            # page anchor (#sid{N} -- the förarbete citation target); the statute/
            # case paragraphs citing this very page drive the context rail
            key = "sid%d" % cur
            rail.add(key, "s. %d" % cur)
            parts.append('<span class="sid" id="%s"%s>%d</span>'
                         % (key, _rail_attr(rail, key), cur))
        runs = render_runs(b["text"], site)
        if b["type"] == "rubrik":
            level = b.get("level") or 1
            anchor = toc.add(None, plain(b["text"]), level)
            parts.append('<h%d id="%s" class="rubrik">%s</h%d>'
                         % (min(level + 1, 5), escape(anchor), runs,
                            min(level + 1, 5)))
        else:
            parts.append("<p>%s</p>" % runs)
    return page(title, "Förarbete", meta, "".join(parts), render_toc(toc),
                eyebrow=FA_TYPE_LABEL.get(art.get("type"), "Förarbete"),
                island=rail.island(), source_url=art.get("source_url"))


def _meta_dl(pairs):
    rows = "".join("<dt>%s</dt><dd>%s</dd>" % (escape(k), escape(str(v)))
                   for k, v in pairs if v)
    return '<dl class="meta">%s</dl>' % rows if rows else ""


def _doc_title(site, uri):
    row = site.con.execute("SELECT title FROM documents WHERE uri = ?",
                           (uri,)).fetchone()
    return row[0] if row else None


def render_kommentar(art, site):
    """Per-paragraph commentary: each section heading links to the statute
    paragraph it annotates; the whole page also links back to the law."""
    law = art.get("annotates")
    law_title = _doc_title(site, law) if law else None
    title = "Kommentar – " + (law_title or art.get("sfs") or "")
    meta = _meta_dl([("Författare", art.get("author")),
                     ("Avser", law_title or art.get("sfs"))])
    if law:
        meta = ('<p class="kommentar-law">Kommentar till <a href="%s">%s</a></p>'
                % (escape(href(law)), escape(law_title or art.get("sfs")))) + meta
    toc = Toc()
    rail = Rail(site, art["uri"])
    parts = [document_inbound(site, art["uri"])]
    for b in art.get("body", []):
        t = b.get("type")
        if t == "sektion":
            toc.add(b["id"], b.get("heading", b["id"]), 1)
            rail.add(b["id"], human_fragment(b["id"]))
            parts.append('<h2 id="%s" class="kaprubrik"%s>%s</h2>'
                         % (escape(b["id"]), _rail_attr(rail, b["id"]),
                            render_runs(b["text"], site)))
            parts += ["<p>%s</p>" % render_runs(c["text"], site)
                      for c in b.get("children", [])]
        elif t == "rubrik":
            parts.append('<h3 class="rubrik">%s</h3>'
                         % render_runs(b["text"], site))
        else:
            parts.append("<p>%s</p>" % render_runs(b["text"], site))
    return page(title, "Kommentar", meta, "".join(parts), render_toc(toc),
                eyebrow="Lagkommentar", island=rail.island(),
                source_url=art.get("source_url"))


def render_begrepp(art, site):
    """A concept definition; its inbound panel shows everything (laws, cases,
    förarbeten, commentary, other concepts) that references the concept."""
    title = art.get("title") or catalog.local(art["uri"])
    meta = _meta_dl([("Kategori", ", ".join(art.get("categories") or []))])
    toc = Toc()
    rail = Rail(site, art["uri"])
    body = document_inbound(site, art["uri"]) + "".join(
        render_node(b, site, art["uri"], toc, rail) for b in art.get("body", []))
    return page(title, "Begrepp", meta, body, render_toc(toc),
                eyebrow="Begrepp", island=rail.island(),
                source_url=art.get("source_url"))


EURLEX_KIND = {"regulation": "EU-förordning", "directive": "EU-direktiv",
               "decision": "EU-beslut", "judgment": "EU-domstolen",
               "treaty": "Fördrag", "act": "EU-rättsakt"}

# block type -> css class for the generic (paragraph-like) EU blocks
EURLEX_CLASS = {"recital": "recital", "citation": "visa", "preamble": "preamble",
                "point": "point", "ruling": "ruling", "note": "note", "row": "row"}


# --------------------------------------------------------------------------
# editorial layer (a sibling `.ann` file): thematic recital groups + the
# article<->recital cross-reference, folded into an EU act's page. Authored
# offline by `lagen eurlex ai-annotate`; absent for an unannotated act.
# --------------------------------------------------------------------------

class Editorial:
    """The `.ann` editorial layer for one EU act, mapping both directions of the
    preamble<->enacting-terms relation: an article (or sub-article like "4(5)")
    to the recitals that explain it, and a recital back to the articles it
    underpins plus the thematic group it belongs to."""

    def __init__(self, layer):
        self.a2r = layer.get("articleToRecitals", {})       # "4(5)" -> [recital n]
        self.groups = layer.get("recitalGroups", [])
        self.group_start = {}        # first recital n of a group -> group (heading)
        self.group_of = {}           # recital n -> its group
        for g in self.groups:
            lo, hi = g["range"]
            self.group_start[lo] = g
            for n in range(lo, hi + 1):
                self.group_of[n] = g
        articles = {}                # recital n -> set of article numbers citing it
        for key, recitals in self.a2r.items():
            art = key.split("(", 1)[0]                       # "6(2)(a)" -> "6"
            for n in recitals:
                articles.setdefault(n, set()).add(art)
        self.recital_articles = {n: sorted(a, key=_art_sort_key)
                                 for n, a in articles.items()}

    def recitals_for(self, key):
        return self.a2r.get(key)


def _art_sort_key(art):
    """Sort article numbers numerically where possible ('2' before '10')."""
    return (0, int(art)) if art.isdigit() else (1, art)


def _subarticle_key(t, num, cur_article, cur_parag):
    """The `.ann` key for a sub-article block, matching the LLM's "4(5)" /
    "6(2)(a)" grammar, from the block's running article/paragraph context."""
    if not (cur_article and num):
        return None
    if t == "paragraph":
        return "%s(%s)" % (cur_article, num)
    if t == "point":
        return ("%s(%s)(%s)" % (cur_article, cur_parag, num) if cur_parag
                else "%s(%s)" % (cur_article, num))
    return None


def _load_editorial(celex):
    path = layout.artifact("eurlex", celex).with_suffix(".ann")
    if not path.exists():
        return None
    layer = json.loads(path.read_text()).get("editorialLayer")
    return Editorial(layer) if layer else None


def _artlist(refs):
    """Article refs as links joined the Swedish way: "2", "2 och 6",
    "2, 6 och 28"."""
    links = ['<a href="#%s">%s</a>' % (escape(a), escape(a)) for a in refs]
    if len(links) <= 1:
        return "".join(links)
    return ", ".join(links[:-1]) + " och " + links[-1]


def _group_anchor(g):
    """The recital group's citation anchor -- its editorial `.ann` id, with a
    range-derived fallback if one is missing."""
    return g.get("id") or "rg%d" % g["range"][0]


def _recital_group_heading(g):
    """A compact, deliberately unofficial editorial label introducing a thematic
    recital group -- a single subdued line outdented into the left margin, since
    it is not part of the authentic act text. E.g. "Skäl 1–5: Bakgrund och syfte
    (jfr art 1)". Carries the group anchor so the TOC's Preambel section links to
    it."""
    lo, hi = g["range"]
    rng = "Skäl %d" % lo if lo == hi else "Skäl %d–%d" % (lo, hi)
    refs = g.get("articleRefs") or []
    jfr = ' <span class="jfr">(jfr art %s)</span>' % _artlist(refs) if refs else ""
    return ('<p id="%s" class="recital-group"><span class="rg-range">%s:</span> '
            '<b>%s</b>%s</p>' % (escape(_group_anchor(g)), escape(rng),
                                 escape(g["label"]), jfr))


def _recital_links_html(recitals):
    """Rail section for an article/sub-article: links to its relevant recitals."""
    links = "".join('<a href="#recital-%d">skäl %d</a>' % (n, n) for n in recitals)
    return ('<div class="rail-sec skal"><div class="rail-sec-h">Relevanta skäl'
            '</div><div class="skal-links">%s</div></div>' % links)


def _recital_context_html(editorial, n):
    """Rail panel for a recital: its thematic group and the articles it underpins
    (the back half of the article<->recital round-trip)."""
    parts = []
    g = editorial.group_of.get(n)
    if g:
        parts.append('<div class="rail-sec"><div class="rail-sec-h">Tematisk grupp'
                     '</div>%s</div>' % escape(g["label"]))
    articles = editorial.recital_articles.get(n)
    if articles:
        links = "".join('<a href="#%s">artikel %s</a>' % (escape(a), escape(a))
                        for a in articles)
        parts.append('<div class="rail-sec skal"><div class="rail-sec-h">Förklarar'
                     '</div><div class="skal-links">%s</div></div>' % links)
    return "".join(parts)


def _eurlex_pin(t, num, bid):
    """The rail's "Kontext för …" label for an EU block."""
    if t == "recital" and num:
        return "Skäl %s" % num
    if t == "article":
        return "Artikel %s" % (num or bid or "")
    if bid and "(" in bid:
        return "Artikel %s" % bid
    return human_fragment(bid)


def _render_eurlex_block(b, site, doc_uri, toc, rail, editorial=None,
                         cur_article=None, cur_parag=None):
    runs = render_runs(b["text"], site)
    bid = b.get("id")
    t = b["type"]
    num = b.get("num")
    if t == "heading":
        level = b.get("level") or 1
        anchor = toc.add(bid, plain(b["text"]), level)
        lvl = min(level + 1, 5)
        return '<h%d id="%s" class="rubrik">%s</h%d>' % (lvl, escape(anchor),
                                                         runs, lvl)
    if t == "keyword":
        return '<span class="sokord">%s</span>' % runs
    # editorial layer (.ann): wire this block into the article<->recital graph.
    # A recital gets a recital-N id and a back-link panel (its articles + group);
    # an article/sub-article (paragraph/point, keyed like the .ann's "4(5)") gets
    # a forward panel of its relevant recitals. Both ride the scroll-driven rail.
    extra = ""
    if editorial:
        if t == "recital" and num and num.isdigit():
            bid = "recital-%s" % num
            extra = _recital_context_html(editorial, int(num))
        else:
            key = (cur_article if t == "article"
                   else _subarticle_key(t, num, cur_article, cur_parag))
            recitals = editorial.recitals_for(key) if key else None
            if recitals:
                if t != "article":
                    bid = key          # synthesise the sub-article citation id
                extra = _recital_links_html(recitals)
    # the article is a citation target (id == its number); its inbound (incl.
    # implementing förarbeten) drives the rail, like an SFS paragraph
    pin = _eurlex_pin(t, num, bid)
    rail.add(bid, pin, extra)
    ra = _rail_attr(rail, bid)
    if t == "article":
        anchor = toc.add(bid, plain(b["text"]), 2)
        return '<h3 id="%s" class="artikel"%s>%s</h3>' % (escape(anchor), ra, runs)
    marker = '<span class="num">%s</span> ' % escape(num) if num else ""
    classes = [EURLEX_CLASS.get(t, "")]
    # a definitions-article point is a citation target (#<article>.<point>) and
    # the begrepp the act defines -- emit its id and emphasise the defined term
    defines = b.get("defines")
    if defines:
        classes.append("definition")
        runs = _emphasize_term(runs, defines)
    cls = " ".join(c for c in classes if c)
    return '<p%s%s%s>%s%s</p>' % (_id_attr(bid), ' class="%s"' % cls if cls else "",
                                  ra, marker, runs)


def _emphasize_term(runs_html, term):
    """Wrap a definition point's lead term (the plain text before its colon) in
    <dfn>, so the defined word stands out from its definition."""
    lead = escape(term)
    if runs_html.startswith(lead):
        return "<dfn>%s</dfn>%s" % (lead, runs_html[len(lead):])
    return runs_html


def render_eurlex(art, site):
    title = art.get("title") or catalog.local(art["uri"])
    meta = _meta_dl([
        ("CELEX", art.get("celex")),
        ("Typ", EURLEX_KIND.get(art.get("doctype"), art.get("doctype"))),
        ("Datum", art.get("date")),
        ("EUT", art.get("oj")),
        ("ECLI", art.get("ecli")),
    ])
    editorial = _load_editorial(art["celex"])
    toc = Toc()
    rail = Rail(site, art["uri"])
    parts = [document_inbound(site, art["uri"])]
    cur_article = cur_parag = None       # running context for sub-article keys
    preamble_in_toc = False              # the "Preambel" TOC parent is added once
    for b in art.get("body", []):
        t = b["type"]
        if editorial and t == "recital" and (b.get("num") or "").isdigit():
            group = editorial.group_start.get(int(b["num"]))
            if group:
                anchor = _group_anchor(group)
                if not preamble_in_toc:   # a Preambel section listing the groups
                    toc.add(anchor, "Preambel", 1)
                    preamble_in_toc = True
                toc.add(anchor, group.get("label", ""), 2)
                parts.append(_recital_group_heading(group))
        if t == "article":
            cur_article, cur_parag = b.get("id") or b.get("num"), None
        elif t == "paragraph":
            cur_parag = b.get("num")
        parts.append(_render_eurlex_block(b, site, art["uri"], toc, rail,
                                          editorial, cur_article, cur_parag))
    body = "".join(parts)
    kind = EURLEX_KIND.get(art.get("doctype"), "EU-rättsakt")
    return page(title, kind, meta, body, render_toc(toc),
                eyebrow=kind, island=rail.island(),
                source_url=art.get("source_url"))


def render_document(art, source, site):
    return {"sfs": render_sfs, "dv": render_dv, "forarbete": render_forarbete,
            "kommentar": render_kommentar, "begrepp": render_begrepp,
            "eurlex": render_eurlex}[source](art, site)


# --------------------------------------------------------------------------
# frontpage
# --------------------------------------------------------------------------

# the document types, in the order they appear on the frontpage, with their
# Swedish collection labels. dv's documents (and so its browse index) live under
# /dom/, lagen.nu's grammar; every other source browses under its own name.
SOURCE_ORDER = ("sfs", "dv", "forarbete", "eurlex", "kommentar", "begrepp")
SOURCE_LABEL = {"sfs": "Författningar", "dv": "Rättsfall",
                "forarbete": "Förarbeten", "eurlex": "EU-rättsakter",
                "kommentar": "Lagkommentarer", "begrepp": "Begrepp"}
BROWSE_DIR = {"dv": "dom"}


def _browse_dir(source):
    return BROWSE_DIR.get(source, source)


def _most_cited(con, source):
    """The 25 most-referenced documents of a source as ranked-list <li>s (the
    highlight reels on the frontpage), or '' if the source is empty."""
    rows = con.execute(
        "SELECT d.uri, COALESCE(d.title, d.label), COUNT(DISTINCT l.from_uri) c "
        "FROM links l JOIN documents d ON d.uri = l.to_root "
        "WHERE d.source = ? AND l.from_uri <> l.to_root "
        "GROUP BY l.to_root ORDER BY c DESC LIMIT 25", (source,)).fetchall()
    return "".join('<li><a href="%s">%s</a> <span class="c">%d</span></li>'
                   % (escape(href(u)), escape(t), c) for u, t, c in rows)


def render_index(con):
    n = catalog.counts(con)
    nav = "".join(
        '<li><a href="/%s/">%s</a> <span class="c">%d</span></li>'
        % (_browse_dir(s), escape(SOURCE_LABEL.get(s, s)), n[s])
        for s in SOURCE_ORDER if n.get(s))
    cols = []
    for source, heading in (("sfs", "Mest hänvisade författningar"),
                            ("dv", "Mest hänvisade rättsfall")):
        items = _most_cited(con, source)
        if items:
            cols.append('<section><h2>%s</h2><ol class="ranked">%s</ol></section>'
                        % (heading, items))
    body = ('<p class="lead">%d sammanlänkade dokument fördelade på %d '
            'dokumenttyper.</p>'
            '<nav class="browse counts"><ul>%s</ul></nav>'
            '<div class="cols">%s</div>'
            % (sum(n.values()), sum(1 for s in n if n[s]), nav, "".join(cols)))
    return page("lagen.nu", "Start", "", body,
                eyebrow="Sveriges lagar, med kontext", solo=True)


def _group_key(source, uri):
    """Browse grouping heading for a document: laws by year, cases by court,
    förarbeten by doctype, EU acts by act family, concepts/commentary by initial
    letter."""
    loc = catalog.local(uri)
    if source == "sfs":
        return loc.split(":")[0]                      # "1975:635" -> "1975"
    if source == "dv":
        parts = loc.split("/")                        # "dom/nja/2011s357" -> "NJA"
        return parts[1].upper() if len(parts) > 1 else "övriga"
    if source == "forarbete":
        typ = loc.split("/")[0]                        # "prop/2024/25:1" -> "prop"
        return FA_TYPE_LABEL.get(typ, typ.upper())
    if source == "eurlex":
        celex = loc[len("ext/celex/"):]               # "ext/celex/32016R0679" -> kind
        return EURLEX_KIND.get(eurlex_doctype(celex), "EU-rättsakt")
    return loc.split("/")[-1][:1].upper() or "övriga"  # kommentar/begrepp: A–Ö


def render_browse(con, source):
    """A complete, sectioned index of every document of one source, grouped by
    `_group_key` (laws by year newest-first, the rest A–Ö)."""
    rows = con.execute("SELECT uri, label FROM documents WHERE source = ? "
                       "ORDER BY uri", (source,)).fetchall()
    groups = {}
    for uri, label in rows:
        groups.setdefault(_group_key(source, uri), []).append((uri, label))
    order = sorted(groups, reverse=(source == "sfs"))
    label = SOURCE_LABEL.get(source, source)
    sections = []
    for key in order:
        items = "".join('<li><a href="%s">%s</a></li>'
                        % (escape(href(u)), escape(lbl or catalog.local(u)))
                        for u, lbl in groups[key])
        sections.append('<section class="browse-group"><h2>%s '
                        '<span class="c">%d</span></h2><ul>%s</ul></section>'
                        % (escape(key), len(groups[key]), items))
    body = ('<p class="lead">%d %s</p>%s'
            % (len(rows), label.lower(), "".join(sections)))
    return page("Alla " + label.lower(), "Bläddra", "", body, solo=True)


# --------------------------------------------------------------------------
# generate the whole site
# --------------------------------------------------------------------------

def generate_site(catalog_path, out_root, progress=None, fresh=None, record=None,
                  only=None):
    """Render every catalogued document to static HTML. `fresh(uri, out_path,
    art_path, dep_digest) -> bool` lets the caller skip a page whose inputs are
    unchanged (incremental generate); `record(uri, art_path, dep_digest)` is
    called after a page is (re)rendered so the caller can store its new
    signature. `art_path` is the page's own artifact (content-hashed by the
    caller); `dep_digest` captures its citation relationships (set-based).
    `only`, a set of artifact path strings, restricts the run to those documents
    (a targeted `lagen <source> generate <id>`) -- the corpus-wide aggregate
    pages are then left untouched. Returns (total_pages, rendered) -- rendered <
    total when pages were skipped."""
    out_root = Path(out_root)
    con = catalog.connect(catalog_path)
    site = Site.from_catalog(con)
    rows = con.execute(
        "SELECT uri, source, path FROM documents ORDER BY source, uri").fetchall()
    if only is not None:
        rows = [r for r in rows if r[2] in only]
    rendered = 0
    for i, (uri, source, path) in enumerate(rows):
        out = out_root / doc_relpath(uri)
        dep = catalog.page_dependency_digest(con, uri)
        if not (fresh and fresh(uri, out, path, dep)):
            art = json.loads(Path(path).read_bytes())
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(render_document(art, source, site))
            rendered += 1
            if record:
                record(uri, path, dep)
        if progress:
            progress(i + 1, len(rows), catalog.local(uri), rendered)
    if only is None:
        render_aggregates(con, out_root)
    if progress:
        progress(len(rows), len(rows), "", rendered)
    con.close()
    return len(rows), rendered


def render_aggregates(con, out_root):
    """Write the corpus-wide pages -- stylesheet, scripts, frontpage and the
    per-source browse indexes -- from the catalog. They depend on the whole
    document set (not on any single artifact), so they are cheap and always
    rebuilt; `lagen all generate --aggregates-only` runs just this, skipping the
    per-document render."""
    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "style.css").write_text(CSS)
    (out_root / "scrollspy.js").write_text(SCROLLSPY)
    (out_root / "index.html").write_text(render_index(con))
    for source in catalog.counts(con):
        browse_dir = out_root / _browse_dir(source)
        browse_dir.mkdir(parents=True, exist_ok=True)
        (browse_dir / "index.html").write_text(render_browse(con, source))


CSS = """
/* lagen 2026 -- Library / Gravitas. Sans body (Inter), serif headings &
   numerals (Source Serif 4) on warm paper, a single umber accent. A 3-column
   grid (TOC | reading column | context rail) that collapses under 64rem. */
:root {
  --bg:#f4f1ea; --surf:#faf8f3; --surf-2:#ede9df; --surf-3:#ddd8c8;
  --ink:#14181e; --ink-2:#3c4149; --ink-3:#6b6f76; --ink-4:#9da0a6;
  --rule:#d6d1c2; --rule-soft:#e6e2d4;
  --accent:#7a4a23; --case:#9a5a2a; --forarbete:#5b4a86; --kommentar:#9a3b5e;
  --serif:"Source Serif 4","Charter",Georgia,serif;
  --sans:"Inter","Helvetica Neue",system-ui,sans-serif;
  --col-toc:18rem; --col-rail:20rem;
}
* { box-sizing: border-box; }
html, body { margin: 0; }
body { font-family: var(--sans); font-size: 15px; line-height: 1.6;
       letter-spacing: -.005em; color: var(--ink); background: var(--bg);
       font-feature-settings: "kern"; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }

/* -- Masthead -- */
.masthead { position: sticky; top: 0; z-index: 10; display: flex;
            align-items: center; gap: 1.5rem; height: 4rem; padding: 0 1.75rem;
            background: var(--surf); border-bottom: 1px solid var(--rule); }
.masthead .brand { font-family: var(--serif); font-size: 1.5rem; font-weight: 500;
                   letter-spacing: -.015em; color: var(--ink); }
.masthead .brand em { color: var(--accent); font-style: italic; }
.masthead .brand:hover { text-decoration: none; }
.masthead .search { flex: 1; max-width: 36rem; display: flex; align-items: center;
                    gap: .6rem; padding: .55rem .9rem; cursor: text; text-align: left;
                    color: var(--ink-3); background: var(--surf-2);
                    border: 1px solid var(--rule); border-radius: 6px;
                    font-family: var(--serif); font-style: italic; font-size: .95rem; }
.masthead .search:hover { background: var(--surf-3); }
.masthead .search .k { margin-left: auto; font-family: var(--sans); font-style: normal;
                       font-size: .72rem; color: var(--ink-2); background: var(--surf);
                       border: 1px solid var(--rule); border-radius: 4px; padding: .1rem .5rem; }
.mast-nav { display: flex; gap: 1.4rem; font-size: .88rem; }
.mast-nav a { color: var(--ink-2); padding: .35rem 0;
              border-bottom: 1px solid transparent; }
.mast-nav a:hover { color: var(--ink); text-decoration: none;
                    border-bottom-color: var(--ink-3); }
.mast-nav a.on { color: var(--ink); font-weight: 500; border-bottom-color: var(--accent); }

/* -- Body grid -- */
.gr-body { display: grid; align-items: start;
           grid-template-columns: var(--col-toc) minmax(0,1fr) var(--col-rail); }
.gr-body.solo { display: block; max-width: 56rem; margin: 0 auto; }
.gr-main { min-width: 0; padding: 2.5rem clamp(1.5rem, 4vw, 4rem) 8rem; max-width: 56rem; }
.gr-body.solo .gr-main { max-width: none; }

/* -- TOC -- */
.toc-col { position: sticky; top: 4rem; max-height: calc(100vh - 4rem);
           overflow-y: auto; background: var(--surf); border-right: 1px solid var(--rule); }
nav.toc { padding: 1.75rem 0 5rem; font-size: .82rem; }
nav.toc .toc-h { padding: 0 1.5rem; font-family: var(--serif); font-style: italic;
                 font-size: .8rem; color: var(--ink-3); margin-bottom: .6rem; }
nav.toc a { display: block; color: var(--ink-2); line-height: 1.35;
            padding: .2rem 1.5rem .2rem 1.35rem; border-left: 2px solid transparent; }
nav.toc a:hover { color: var(--ink); background: var(--surf-2); text-decoration: none; }
nav.toc a.lvl2 { padding-left: 2.5rem; color: var(--ink-3); }
nav.toc a.lvl3 { padding-left: 3.4rem; color: var(--ink-4); font-size: .78rem; }
nav.toc a.active { color: var(--accent); border-left-color: var(--accent);
                   background: var(--surf-2); font-weight: 500; }

/* -- Frontmatter -- */
.frontmatter { margin-bottom: 3rem; padding-bottom: 1.75rem;
               border-bottom: 1px solid var(--rule); }
.eyebrow { font-family: var(--serif); font-style: italic; font-size: .95rem;
           color: var(--accent); }
.gr-main h1 { font-family: var(--serif); font-weight: 500; font-size: 2.6rem;
              line-height: 1.05; letter-spacing: -.028em; margin: .3rem 0 .5rem;
              text-wrap: balance; }
.gr-body.solo h1 { font-size: 2.1rem; }
.subtitle { font-family: var(--serif); font-style: italic; font-size: 1.05rem;
            color: var(--ink-2); line-height: 1.5; margin: 0; }
dl.meta { margin: 1.25rem 0 0; display: grid; grid-template-columns: max-content 1fr;
          column-gap: 1.25rem; row-gap: .25rem; font-family: var(--serif);
          font-style: italic; font-size: .9rem; color: var(--ink-3); }
dl.meta dt { font-weight: 500; font-style: normal; }
dl.meta dd { margin: 0; }
dl.meta a { color: var(--accent); }

/* -- Headings -- */
.kaprubrik { font-family: var(--serif); font-weight: 500; font-size: 1.7rem;
             letter-spacing: -.022em; line-height: 1.12; margin: 3rem 0 .25rem;
             padding-bottom: .75rem; border-bottom: 1px solid var(--rule);
             text-wrap: balance; scroll-margin-top: 5rem; }
.rubrik { font-family: var(--serif); font-weight: 600; font-size: 1.2rem;
          letter-spacing: -.015em; margin: 2.25rem 0 .5rem; scroll-margin-top: 5rem; }
.artikel { font-family: var(--serif); font-weight: 600; font-size: 1.15rem;
           margin: 2rem 0 .5rem; scroll-margin-top: 5rem; }

/* -- Paragraphs (SFS § with hanging numeral) -- */
section.paragraf { display: grid; grid-template-columns: 3.25rem minmax(0,1fr);
                   gap: 1rem; margin: 1.75rem 0; align-items: baseline;
                   scroll-margin-top: 5rem; }
.paragraf-gutter { text-align: right; font-family: var(--serif); line-height: 1.2;
                   font-variant-numeric: oldstyle-nums; }
.paragraf-gutter .n { font-size: 1.55rem; font-weight: 500; color: var(--accent); }
.paragraf-gutter .pilcrow { display: block; margin-top: .15rem; font-family: var(--sans);
                            font-size: .7rem; color: var(--ink-4); opacity: 0;
                            transition: opacity .15s; }
section.paragraf:hover .pilcrow { opacity: 1; }
.paragraf-gutter .pilcrow:hover { color: var(--accent); text-decoration: none; }
.paragraf-body { min-width: 0; font-size: 1.0rem; line-height: 1.7; }
.paragraf-body > p:first-child { margin-top: 0; }
p { margin: 0 0 .7rem; }
.num { font-family: var(--serif); font-weight: 600; color: var(--accent);
       font-variant-numeric: oldstyle-nums; }
section.paragraf.rail-active { background:
   linear-gradient(90deg, color-mix(in oklab, var(--accent) 6%, transparent), transparent 60%);
   border-radius: 4px; }

/* -- DV / förarbete extras -- */
.sammanfattning { font-family: var(--serif); font-style: italic; font-size: 1.05rem;
                  color: var(--ink-2); border-left: 2px solid var(--rule);
                  padding-left: 1rem; }
.sokord { font-size: .85rem; color: var(--ink-3); }
.sokord span { text-transform: uppercase; letter-spacing: .05em; font-size: .68rem;
               margin-right: .4rem; }
.kommentar-law { font-family: var(--serif); font-style: italic; font-size: 1rem;
                 color: var(--ink-2); border-left: 2px solid var(--kommentar);
                 padding: .4rem 0 .4rem 1rem; margin-bottom: 1rem; }
.genomforande { margin: 1.5rem 0; padding: 1rem 1.25rem; background: var(--surf);
                border: 1px solid var(--rule); border-radius: 6px; }
.genomforande h2 { font-family: var(--serif); font-size: 1rem; font-weight: 600;
                   margin: 0 0 .5rem; border: 0; }
.genomforande ul { margin: 0; padding-left: 1.1rem; font-size: .9rem; }
.sid { display: inline-block; font-family: var(--serif); font-variant-numeric: oldstyle-nums;
       color: var(--ink-4); font-size: .8rem; margin: 1.25rem 0 .25rem;
       border-top: 1px solid var(--rule-soft); padding-top: .25rem; scroll-margin-top: 5rem; }
.sid::before { content: "s. "; font-style: italic; }

/* -- Context rail (populated by the client from the JSON island) -- */
.rail { position: sticky; top: 4rem; max-height: calc(100vh - 4rem);
        overflow-y: auto; padding: 1.75rem 1.5rem 5rem;
        background: var(--surf); border-left: 1px solid var(--rule); font-size: .82rem; }
.rail-h { font-family: var(--serif); font-style: italic; font-size: .85rem;
          color: var(--ink-3); padding-bottom: .6rem; margin-bottom: .75rem;
          border-bottom: 1px solid var(--rule); }
.rail-h b { font-style: normal; font-weight: 500; color: var(--ink);
            font-variant-numeric: oldstyle-nums; }
.rail-empty { font-family: var(--serif); font-style: italic; color: var(--ink-3);
              line-height: 1.55; padding: 1rem .25rem; }
.rail-sec-h { font-family: var(--serif); font-style: italic; font-size: .78rem;
              color: var(--ink-3); margin-bottom: .35rem; }
.ingroup { margin-bottom: 1rem; }
.ingroup-h { font-family: var(--serif); font-style: italic; font-size: .8rem;
             color: var(--accent); font-weight: 500; margin: .35rem 0 .25rem; }
.ingroup ul { list-style: none; margin: 0; padding: 0; }
.ingroup li { margin: .15rem 0; line-height: 1.4; }
.ingroup.dv a { color: var(--case); }
.ingroup.forarbete a { color: var(--forarbete); }
.ingroup.kommentar a { color: var(--kommentar); font-weight: 500; }
.ingroup.begrepp a { color: var(--accent); }
.more { color: var(--ink-3); font-style: italic; font-size: .78rem; margin-top: .25rem; }
aside.genomfor { margin-top: 1rem; padding-top: .75rem; border-top: 1px solid var(--rule); }
aside.genomfor ul { list-style: none; margin: 0; padding: 0; font-size: .8rem; }
aside.genomfor li { margin: .2rem 0; line-height: 1.4; }
aside.genomfor .prov { color: var(--ink-3); }
.inbound-h { font-family: var(--serif); font-style: italic; font-size: .8rem;
             color: var(--accent); font-weight: 500; margin-bottom: .35rem; }

/* -- Document-level inbound panel (whole-doc citations) -- */
.inbound-doc { background: var(--surf); border: 1px solid var(--rule); border-radius: 6px;
               padding: .9rem 1.25rem; margin: 1.5rem 0 2.5rem; font-size: .85rem; }
.inbound-doc h2 { font-family: var(--serif); font-style: italic; font-size: .85rem;
                  color: var(--ink-3); font-weight: 400; margin: 0 0 .5rem; border: 0; }
.inbound-doc .ingroup { columns: 2; column-gap: 2rem; margin-bottom: .25rem; }
.inbound-doc .ingroup-h { column-span: all; }
.inbound-doc li { break-inside: avoid; }

/* -- Inline bits -- */
ol.punkter { list-style: none; margin: .4rem 0; padding-left: 1.5rem; }
ol.punkter > li { margin: .25rem 0; }
.noref { color: var(--ink-3); border-bottom: 1px dotted var(--ink-4); cursor: help; }
/* an in-act use of a defined term: underlined, hover shows the definition */
a.term { color: inherit; border-bottom: 1px dotted var(--ink-4); cursor: help; }
a.term:hover { color: var(--accent); border-bottom-color: var(--accent);
               text-decoration: none; }
/* the definition point itself (Article "Definitions") */
p.definition { scroll-margin-top: 5rem; }
p.definition dfn { font-style: normal; font-weight: 600; color: var(--accent); }

/* -- EU act editorial layer (.ann): thematic recital groups + the rail's
   article<->recital links. The group label is editorial, not part of the
   authentic text, so it reads as a subdued, compact aside outdented to the
   left rather than a heading competing with the act. -- */
.recital-group { margin: 1.75rem 0 .4rem -1.6rem; font-family: var(--serif);
                 font-style: italic; font-size: .8rem; line-height: 1.4;
                 color: var(--ink-3); scroll-margin-top: 5rem; }
.recital-group b { font-style: normal; font-weight: 600; color: var(--ink-2); }
.recital-group .rg-range { font-variant-numeric: oldstyle-nums; }
.recital-group .jfr { color: var(--ink-4); }
.recital-group a { color: var(--ink-3); }
p.recital { scroll-margin-top: 5rem; }
.rail-sec.skal .skal-links { display: flex; flex-wrap: wrap; gap: .2rem .6rem; }
.rail-sec.skal a { font-variant-numeric: oldstyle-nums; }

/* -- Context markers: every addressable unit that carries rail context wears a
   clickable 💬 in the right gutter (toward the rail), so context-bearing
   paragraphs are discoverable at a glance; clicking pulls that unit's panel into
   the rail and brings it into focus, and the unit in focus brightens its marker.
   The button is built client-side (scrollspy), so it stays global -- every
   source. Absolutely positioned, so it never disrupts the paragraf grid. -- */
[data-rail] { position: relative; }
.rail-dot { position: absolute; right: -1.7rem; top: .05rem; z-index: 2;
            border: 0; background: transparent; padding: .15rem; margin: 0;
            font-size: .95rem; line-height: 1; cursor: pointer; opacity: .5;
            filter: grayscale(.4);
            transition: opacity .15s, filter .15s, transform .1s; }
.rail-dot:hover, .rail-dot:focus-visible { opacity: 1; filter: none;
            transform: scale(1.2); outline: none; }
[data-rail].rail-active > .rail-dot { opacity: 1; filter: none; }
a.ext { color: var(--accent); }
a.ext::after { content: " \\2197"; font-size: .8em; color: var(--ink-4); }
table { border-collapse: collapse; margin: 1rem 0; width: 100%; font-size: .92rem; }
td { border-top: 1px solid var(--rule); padding: .35rem .7rem; vertical-align: top; }

/* -- Frontpage / browse -- */
.lead { font-family: var(--serif); font-size: 1.2rem; color: var(--ink-2); }
.cols { display: grid; grid-template-columns: 1fr 1fr; gap: 2.5rem; margin-top: 2rem; }
.cols h2, .browse-group h2 { font-family: var(--serif); font-weight: 500; font-size: 1.2rem;
              border-bottom: 1px solid var(--rule); padding-bottom: .3rem; }
ol.ranked { padding-left: 1.4rem; } ol.ranked .c { color: var(--ink-3); font-size: .8rem; }
.cols ul { list-style: none; padding: 0; } .cols li { margin: .3rem 0; }
.counts ul { list-style: none; padding: 0; margin: 1.5rem 0 0; display: flex;
             flex-wrap: wrap; gap: .5rem 1.75rem; }
.counts li { font-size: 1.05rem; }
.counts .c { color: var(--ink-3); font-size: .8rem; margin-left: .2rem; }
.browse-group { margin: 2rem 0; }
.browse-group h2 .c { color: var(--ink-3); font-size: .8rem; font-weight: 400; }
.browse-group ul { list-style: none; padding: 0; margin: .75rem 0 0;
                   columns: 18rem; column-gap: 2rem; font-size: .92rem; }
.browse-group li { margin: .18rem 0; break-inside: avoid; }

/* -- search palette (stub: backend deferred) -- */
.search-overlay { position: fixed; inset: 0; z-index: 100; display: flex;
                  justify-content: center; align-items: flex-start; padding-top: 8rem;
                  background: rgba(20,16,10,.32); }
.search-box { width: 37rem; max-width: 92vw; background: var(--surf);
              border: 1px solid var(--rule); border-radius: 8px;
              box-shadow: 0 30px 80px rgba(20,16,10,.3); overflow: hidden; }
.search-box input { width: 100%; border: 0; outline: 0; padding: 1.1rem 1.25rem;
                    background: transparent; font-family: var(--serif); font-size: 1.2rem;
                    color: var(--ink); }
.search-box .search-note { padding: .9rem 1.25rem; border-top: 1px solid var(--rule);
                           font-family: var(--serif); font-style: italic;
                           color: var(--ink-3); font-size: .9rem; }

/* -- Responsive: drop the side columns -- */
@media (max-width: 64rem) {
  .gr-body { display: block; }
  .toc-col, .rail { display: none; }
  .gr-main { max-width: 46rem; margin: 0 auto; }
  .masthead .search { display: none; }
  /* the rail is hidden here, so its gutter markers would point at nothing */
  .rail-dot { display: none; }
  .recital-group { margin-left: 0; }
}

/* -- Print -- */
@media print {
  .masthead, .toc-col, .rail { display: none; }
  .gr-body { display: block; }
  .gr-main { max-width: none; padding: 0; }
  .kaprubrik { border-color: #999; }
}
"""


# Client layer: a throttled scroll handler that (1) highlights the TOC entry for
# the section at the top of the viewport, and (2) swaps the context rail to the
# active paragraph's panel, read from the JSON island the renderer emitted. Plus
# a ⌘K palette stub (the search backend is deferred). Plain DOM, no deps.
SCROLLSPY = """
(function () {
  var island = {};
  var data = document.getElementById('lagen-context');
  if (data) { try { island = JSON.parse(data.textContent); } catch (e) {} }

  var toc = document.querySelector('nav.toc');
  var links = toc ? Array.prototype.slice.call(toc.querySelectorAll('a')) : [];
  var targets = links.map(function (a) {
    return document.getElementById(decodeURIComponent(a.getAttribute('href').slice(1)));
  });
  var rail = document.getElementById('rail');
  var marks = Array.prototype.slice.call(document.querySelectorAll('[data-rail]'));
  var EMPTY = '<div class="rail-empty">Ingen rättspraxis, förarbeten eller annan ' +
              'kontext har ännu knutits till denna del.</div>';
  if (rail) rail.innerHTML = EMPTY;

  var activeLink = -1, activeRail = '', activeMark = null, ticking = false;

  // swap the rail to a unit's panel and mark it active (idempotent per unit)
  function applyRail(best) {
    if (best === activeMark) return;
    var key = best ? best.getAttribute('data-rail') : '';
    activeRail = key;
    if (rail) rail.innerHTML = (key && island[key]) ? island[key] : EMPTY;
    if (activeMark) activeMark.classList.remove('rail-active');
    activeMark = best;
    if (best) best.classList.add('rail-active');
  }

  // a clickable 💬 in the right gutter of every context-bearing unit -- a
  // discoverable affordance that pulls that unit's panel into the rail and
  // brings the unit into focus. Built here (not in the artifact) so it is global
  // across every source without touching the per-source renderers.
  if (rail) marks.forEach(function (el) {
    // skip a container whose own context-bearing descendant carries the marker,
    // so nested units (SFS paragraf > stycke) show one dot, not two stacked
    if (el.querySelector('[data-rail]')) return;
    var dot = document.createElement('button');
    dot.type = 'button';
    dot.className = 'rail-dot';
    dot.textContent = '💬';
    dot.setAttribute('aria-label', 'Visa kontext för denna del');
    dot.addEventListener('click', function (e) {
      e.preventDefault();
      applyRail(el);
      el.scrollIntoView({ block: 'start', behavior: 'smooth' });
    });
    el.appendChild(dot);
  });

  function update() {
    ticking = false;
    var y = window.scrollY + 120;
    if (links.length) {
      var idx = 0;
      for (var i = 0; i < targets.length; i++) {
        if (targets[i] && targets[i].offsetTop <= y) idx = i;
      }
      if (idx !== activeLink) {
        if (links[activeLink]) links[activeLink].classList.remove('active');
        activeLink = idx;
        var a = links[idx];
        if (a) {
          a.classList.add('active');
          if (a.offsetTop < toc.scrollTop ||
              a.offsetTop > toc.scrollTop + toc.clientHeight - 30) {
            toc.scrollTop = a.offsetTop - toc.clientHeight / 2;
          }
        }
      }
    }
    if (rail && marks.length) {
      var best = null;
      for (var j = 0; j < marks.length; j++) {
        if (marks[j].offsetTop <= y) best = marks[j];
      }
      applyRail(best);
    }
  }
  window.addEventListener('scroll', function () {
    if (!ticking) { ticking = true; requestAnimationFrame(update); }
  }, { passive: true });
  update();

  // ⌘K palette -- visual stub; site-wide search is a deferred backend.
  var overlay = null;
  function open() {
    if (overlay) return;
    overlay = document.createElement('div');
    overlay.className = 'search-overlay';
    overlay.innerHTML = '<div class="search-box"><input autofocus ' +
      'placeholder="Sök lag, paragraf, rättsfall…"><div class="search-note">' +
      'Sökfunktionen är inte aktiverad ännu.</div></div>';
    document.body.appendChild(overlay);
    overlay.addEventListener('click', function (e) { if (e.target === overlay) close(); });
    overlay.querySelector('input').focus();
  }
  function close() { if (overlay) { overlay.remove(); overlay = null; } }
  document.addEventListener('keydown', function (e) {
    if ((e.metaKey || e.ctrlKey) && e.key === 'k') { e.preventDefault(); open(); }
    if (e.key === 'Escape') close();
  });
  document.addEventListener('click', function (e) {
    if (e.target.closest('[data-search]')) { e.preventDefault(); open(); }
  });
})();
"""
