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
import sqlite3
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date
from html import escape
from pathlib import Path

from fastapi.testclient import TestClient

from ..api import app as api_service
from ..dv import naming as dv_naming
from ..eurlex.structure import flatten as eurlex_flatten
from ..eurlex.structure import subarticle_key
from . import catalog, layout
from .catalog import BASE
from .markdown import begrepp_uri


@dataclass
class Site:
    con: object
    known: set                          # document root uris present
    snippets: dict = field(default_factory=dict)               # fragment uri -> tooltip text (lazy cache)
    aliases: dict = field(default_factory=dict)                # variant begrepp uri -> canonical concept
    commentary: dict = field(default_factory=dict)             # (law_uri, anchor) -> [(author, prose)]
    guidance: dict = field(default_factory=dict)               # act uri -> [{label, href, note?}]
    article_guidance: dict = field(default_factory=dict)       # (law_uri, anchor) -> [{label, href, note?}]

    @classmethod
    def from_catalog(cls, con):
        commentary, guidance, article_guidance = _kommentar_indexes(con)
        return cls(con, {u for (u,) in con.execute("SELECT uri FROM documents")},
                   {}, catalog.concept_aliases(con),
                   commentary, guidance, article_guidance)

    def resolve(self, uri):
        """Fold a begrepp link baked into an artifact onto its canonical concept
        uri (inflected/variant forms merged at relate time); other uris (and a
        non-begrepp uri) pass through unchanged."""
        base, sep, frag = uri.partition("#")
        return (self.aliases or {}).get(base, base) + sep + frag

    def has(self, uri):
        return catalog.strip_fragment(uri) in self.known

    def snippet(self, uri):
        """Tooltip text for a link target (the target paragraph + its list
        items), cached per generate run; '' if the catalog has none."""
        if uri not in self.snippets:
            self.snippets[uri] = catalog.snippet(self.con, uri) or ""
        return self.snippets[uri]


def _kommentar_indexes(con):
    """Build the three rail indexes the wiki value-add feeds in **one pass** over
    the kommentar artifacts (each is read + parsed once, not three times):

      * ``commentary`` -- {(law_uri, anchor): [(author, [prose blocks])]}, the
        content the rail shows side-by-side with the paragraph. Commentary is an
        annotation layer (no page of its own); each `== N kap M § ==` section maps
        onto the host node's anchor (`K{N}P{M}`, an EU `5.2`, …). Leading blocks
        before the first section are commentary on the act as a whole, keyed
        (law, None) and shown in the rail by default.
      * ``guidance`` -- {act_uri: [{label, href, note?}]}, the document-level
        `## Externa länkar` block shown at the top of the act (PRD Step 2).
      * ``article_guidance`` -- {(law_uri, anchor): [{label, href, note?}]}, the
        external links attached to a single node's rail (PRD Steps 3-4), from two
        render-only sources keyed identically: the hand-curated per-section
        `## Externa länkar` block in the artifact body, and the AI guidance
        linker's `.ann` sidecar (`lagen kommentar ai-annotate`), kept separate from
        the hand-edited markdown but surfaced in the same rail.

    All three are render-only: external resources live outside the corpus, so they
    carry no inbound edge."""
    commentary, guidance, article_guidance = {}, {}, {}
    for (path,) in con.execute(
            "SELECT path FROM documents WHERE source = 'kommentar' AND path <> ''"):
        art = json.loads(Path(path).read_bytes())
        law = art.get("annotates")
        if not law:
            continue
        author, body = art.get("author"), art.get("body", [])
        # leading blocks before the first section heading are commentary on the
        # act as a whole -- keyed (law, None), shown in the rail by default
        preamble = []
        for b in body:
            if b.get("type") == "sektion":
                break
            preamble.append(b)
        if preamble:
            commentary.setdefault((law, None), []).append((author, preamble))
        for b in body:
            if b.get("type") != "sektion":
                continue
            if b.get("children"):
                commentary.setdefault((law, b["id"]), []).append((author, b["children"]))
            if b.get("guidance"):        # per-section `## Externa länkar` (Step 3)
                article_guidance.setdefault((law, b["id"]), []).extend(b["guidance"])
        if art.get("guidance"):          # document-level `## Externa länkar` (Step 2)
            guidance.setdefault(law, []).extend(art["guidance"])
        ann = Path(path).with_suffix(".ann")       # AI linker sidecar (Step 4)
        if ann.exists():
            links = json.loads(ann.read_bytes()).get("guidanceLinks", {})
            for anchor, items in links.items():
                article_guidance.setdefault((law, anchor), []).extend(items)
    return commentary, guidance, article_guidance


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
    return layout.page_url(uri) + ("#" + frag if frag else "")


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
INBOUND_GROUPS = [("sfs", "Författningar"), ("forarbete", "Förarbeten"),
                  ("foreskrift", "Myndighetsföreskrifter"),
                  ("dv", "Rättsfall"), ("begrepp", "Begrepp")]


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
        if run.get("kind") == "footnote":
            # an inline footnote marker -> superscript link to the endnote, with
            # a matching id the endnote's ↩ links back to
            n = escape(run["text"])
            out.append('<sup class="fnref" id="fnref-%s">'
                       '<a href="#fn-%s">%s</a></sup>' % (n, n, n))
            continue
        uri = site.resolve(run["uri"])     # fold a begrepp variant onto its canon
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


def bemyndigande_margin(site, uri):
    """Statute-paragraf margin: the agency föreskrifter issued (meddelade) with
    stöd av this paragraf -- the inbound side of the bemyndigande edge, mirror of
    each föreskrift's outbound 'Bemyndigande'. So the paragraf that delegates
    rule-making power lists the regulations made under it. The föreskrift links to
    its own page where present, else shows as text (an fs we have not parsed)."""
    rows = catalog.bemyndigande_inbound(site.con, uri)
    if not rows:
        return ""
    items = []
    for from_uri, label, title in rows:
        name = label or catalog.local(from_uri)
        link = ('<a href="%s">%s</a>' % (escape(href(from_uri)), escape(name))
                if site.has(from_uri) else '<span class="noref">%s</span>'
                % escape(name))
        sub = (' <span class="prov">%s</span>' % escape(title)
               if title and title != name else "")
        items.append("<li>%s%s</li>" % (link, sub))
    return ('<aside class="bemyndigande"><div class="inbound-h">Föreskrifter '
            'meddelade med stöd av denna paragraf</div><ul>%s</ul></aside>'
            % "".join(items))


def _law_title(site, base):
    """A law's display title from the catalog, whitespace-collapsed (SFS titles
    can carry a trailing CR/LF), falling back to its local id."""
    return " ".join((_doc_title(site, base) or catalog.local(base)).split())


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
    rows = catalog.correspondence_for_old(site.con, uri)
    if not rows:
        return ""
    items, seen = [], set()
    for new_uri, relation, scope, _prop in rows:
        if new_uri in seen:        # one line per successor paragraf, not per stycke
            continue
        seen.add(new_uri)
        base = new_uri.split("#")[0]
        label = ("%s %s" % (human_fragment(new_uri.partition("#")[2]),
                            _law_title(site, base))).strip()
        link = ('<a href="%s">%s</a>' % (escape(href(new_uri)), escape(label))
                if site.has(base) else escape(label))
        items.append('<li>Denna paragraf %s %s</li>'
                     % (_corr_phrase(relation, scope), link))
    return ('<aside class="motsvarighet"><div class="inbound-h">Motsvarighet'
            '</div><ul>%s</ul></aside>' % "".join(items))


def corresponding_cases_margin(site, uri):
    """New statute paragraf margin: the legal cases (rättsfall) that cite the old,
    repealed paragraf this one corresponds to -- under a heading naming that old
    paragraf, so a reader of the new law finds the case law decided under its
    predecessor. The correspondence is read from the `.corr` layer; the cases are
    the generic inbound on the old paragraf, filtered to case law."""
    out, seen = [], set()
    for old_uri, _rel, _scope, _prop in catalog.correspondence_for_new(
            site.con, uri):
        if old_uri in seen:        # one case section per old paragraf, not per stycke
            continue
        seen.add(old_uri)
        rows = [r for r in catalog.inbound(site.con, old_uri, limit=INBOUND_CAP + 1)
                if r[4] == "dv"]
        if not rows:
            continue
        base = old_uri.split("#")[0]
        old_label = "%s %s (numera upphävd)" % (
            human_fragment(old_uri.partition("#")[2]), _law_title(site, base))
        links = "".join(
            '<li><a href="%s">%s</a></li>'
            % (escape(href(from_uri + ("#" + a if a else ""))),
               escape(describe_citer(from_uri, a, label, title, source)))
            for from_uri, a, label, title, source in rows[:INBOUND_CAP])
        out.append('<div class="rail-sec"><div class="rail-sec-h">Rättsfall som '
                   'hänvisar till motsvarande %s</div><ul>%s</ul></div>'
                   % (escape(old_label), links))
    return "".join(out)


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
        """Record node `nid`'s rail panel if it has commentary, anything cites it,
        it transposes an EU article, or it carries an editorial `extra` section
        (the EU article<->recital links). Idempotent per id; no-op for
        context-less nodes."""
        if not nid or nid in self.data:
            return
        uri = self.doc_uri + "#" + nid
        commentary = self._commentary(nid)
        guidance = self._guidance_html(
            (self.site.article_guidance or {}).get((self.doc_uri, nid)))
        groups = _inbound_groups(self.site, uri)
        genomfor = genomfor_margin(self.site, self.doc_uri, nid)
        bemyndigande = bemyndigande_margin(self.site, uri)        # föreskrifter under it
        corr_cases = corresponding_cases_margin(self.site, uri)   # new-law side
        corresponds = corresponds_margin(self.site, uri)          # old-law side
        if not (commentary or guidance or groups or genomfor or bemyndigande
                or extra or corr_cases or corresponds):
            return
        head = ('<div class="rail-h">Kontext%s</div>'
                % (' för <b>%s</b>' % escape(pinpoint) if pinpoint else ""))
        body = ('<div class="rail-sec"><div class="rail-sec-h">Hänvisat till av</div>'
                '%s</div>' % groups) if groups else ""
        self.data[nid] = (head + commentary + guidance + body + corr_cases + extra
                          + genomfor + bemyndigande + corresponds)

    def add_document(self):
        """The document-level rail panel (key ''), shown when no single paragraph
        is in focus (at the top of the document): the act's curated external links
        (Externa länkar) plus any commentary on the document as a whole. Replaces
        the client's empty-rail placeholder."""
        panel = (self._guidance_html((self.site.guidance or {}).get(self.doc_uri))
                 + self._commentary(None))
        if panel:
            self.data[""] = '<div class="rail-h">Om dokumentet</div>' + panel

    def _guidance_html(self, items):
        """A list of curated external links -- the wiki annotation's `## Externa
        länkar` block (Commission FAQs, guidance PDFs, call-for-evidence pages, …) --
        as a rail section, used both for the act's document-level panel (Step 2) and
        for a single article's context panel (Step 3); '' for no items. Render-only:
        these resources live outside the corpus, so they carry no inbound edge. A
        lagen.nu-absolute href renders internal, any other an external link."""
        if not items:
            return ""
        out = []
        for g in items:
            ext = "" if g["href"].startswith(BASE) else ' rel="external"'
            # a guidance link carries either a `desc` (the guidance section's own
            # text, e.g. the FAQ question -- shown after the link as ": ...") or a
            # `note` (provenance for a hand-curated link -- shown as "— ...")
            if g.get("desc"):
                tail = ': <span class="q">%s</span>' % escape(g["desc"])
            elif g.get("note"):
                tail = ' <span class="prov">— %s</span>' % escape(g["note"])
            else:
                tail = ""
            out.append('<li><a href="%s"%s>%s</a>%s</li>'
                       % (escape(href(g["href"])), ext, escape(g["label"]), tail))
        return ('<div class="rail-sec vagledning"><div class="rail-sec-h">Externa '
                'länkar</div><ul>%s</ul></div>' % "".join(out))

    def _commentary(self, nid):
        """The wiki commentary for the paragraph `nid` (or `None` for the law as a
        whole), rendered as a rail section (its prose + author byline) -- shown
        side-by-side with what it comments on, in place of a separate kommentar
        page."""
        entries = (self.site.commentary or {}).get((self.doc_uri, nid))
        if not entries:
            return ""
        out = []
        for author, blocks in entries:
            prose = "".join("<p>%s</p>" % render_runs(c["text"], self.site)
                            for c in blocks if c.get("text"))
            by = '<div class="komm-by">— %s</div>' % escape(author) if author else ""
            out.append(prose + by)
        return ('<div class="rail-sec rail-komm"><div class="rail-sec-h">Kommentar'
                '</div>%s</div>' % "".join(out))

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
</head><body class="gr-root%(body_class)s">
%(masthead)s
%(grid)s
%(island)s<script src="/scrollspy.js" defer></script>
<script src="/search.js" defer></script>
</body></html>
"""

# masthead nav: label, browse route, the page kinds that mark it current
MAST_NAV = (("Lagar", "/sfs/", ("Författning",)),
            ("Rättsfall", "/dom/", ("Rättsfall",)),
            ("Förarbeten", "/forarbete/", ("Proposition", "SOU", "Ds",
             "Kommittédirektiv", "Förordningsmotiv", "Skrivelse", "Lagrådsremiss",
             "Sveriges internationella överenskommelser", "Förarbete")),
            ("Föreskrifter", "/foreskrift/", ("Föreskrift",)),
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
         island="", solo=False, source_url=None, body_class=""):
    """Assemble a page. Document pages use the 3-column grid (TOC · reading
    column · context rail); `solo` pages (frontpage, browse indexes) drop the
    side columns for a single centered column. `body_class` adds a modifier to
    the <body> (e.g. " expired" for a repealed statute -- subdued reading column
    + a fixed watermark)."""
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
                   "grid": grid, "island": island, "body_class": body_class}


def _expired_banner(props):
    """The repeal callout for a statute whose repeal has taken effect: the repeal
    date and, when known, a link to the repealing act. Paired with the
    `body.expired` treatment (subdued reading column + a fixed 'Upphävd
    författning' watermark) so the status stays visible even when an anchor link
    jumps deep past the heading."""
    when = props.get("rpubl:upphavandedatum")
    av = props.get("rinfoex:upphavdAv")
    detail = ("Upphörde att gälla %s" % escape(when)) if when else "Upphävd"
    if av:
        detail += ' genom <a href="%s">SFS %s</a>' % (
            escape(layout.page_url(av)), escape(catalog.local(av)))
    return ('<div class="expired-banner"><strong>Upphävd författning</strong>'
            '<span>%s.</span></div>' % detail)


def render_sfs(art, site):
    props = art.get("metadata", {}).get("properties", {})
    local_id = catalog.local(art["uri"])
    title = props.get("dcterms:title") or ("SFS " + local_id)
    # a repeal that has taken effect (a future repeal date is still in force):
    # mark the whole page as upphävd
    upphavd = props.get("rpubl:upphavandedatum")
    expired = bool(upphavd) and upphavd <= date.today().isoformat()
    meta = _meta_dl([
        ("Utfärdad", props.get("rpubl:utfardandedatum")),
        ("Ikraftträder", props.get("rpubl:ikrafttradandedatum")),
        ("Upphävd", upphavd),
        ("Källa", props.get("dcterms:identifier")),
    ])
    toc = Toc()
    rail = Rail(site, art["uri"])
    body = (_expired_banner(props) if expired else "") \
        + document_inbound(site, art["uri"]) + "".join(
            render_node(n, site, art["uri"], toc, rail)
            for n in art.get("structure", []))
    rail.add_document()        # external links + law-level commentary, default panel
    return page(title, "Författning", meta, body, render_toc(toc),
                eyebrow="SFS " + local_id, island=rail.island(),
                source_url=art.get("source_url"),
                body_class=" expired" if expired else "")


# the structural wrapper kinds in a DV decision tree (RANK in dv.structure); the
# renderer shows them as nested sections rather than flattening them away
DV_STRUCTURAL = {"delmal", "instans", "betankande", "dom",
                 "skiljaktig", "tillagg", "domskal", "domslut"}
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


def _dv_walk(nodes, site, doc_uri, toc, rail, court=None, ruling="avgörande"):
    """Render a DV structure level: court instances and the betänkande/dom split
    become titled sections (the föredragande's proposal muted), domskäl/domslut
    are transparent wrappers whose own `<h2>` leaves carry the section titles,
    and prose leaves render as ordinary paragraphs."""
    sib = {n.get("type") for n in nodes}
    out = []
    for n in nodes:
        t = n.get("type")
        if t == "instans":
            c = n.get("court") or "Instans"
            anchor = toc.add(None, c, 1)
            inner = _dv_walk(n.get("children", []), site, doc_uri, toc, rail,
                             court=n.get("court"), ruling=ruling)
            out.append('<section class="instans"><h2 id="%s" class="instans-rubrik">'
                       '%s</h2>%s</section>' % (escape(anchor), escape(c), inner))
        elif t == "delmal":
            inner = _dv_walk(n.get("children", []), site, doc_uri, toc, rail,
                             court=court, ruling=ruling)
            head = ('<h2 class="delmal-rubrik">%s</h2>' % escape(n["ordinal"])
                    if n.get("ordinal") else "")
            out.append('<section class="delmal">%s%s</section>' % (head, inner))
        elif t in ("betankande", "skiljaktig", "tillagg"):
            label = DV_RULING_HEADING[t]
            anchor = toc.add(None, label, 2)
            inner = _dv_walk(n.get("children", []), site, doc_uri, toc, rail,
                             court=court, ruling=ruling)
            out.append('<section class="%s"><h3 id="%s" class="instans-rubrik">%s'
                       '</h3>%s</section>' % (t, escape(anchor), escape(label), inner))
        elif t == "dom":
            inner = _dv_walk(n.get("children", []), site, doc_uri, toc, rail,
                             court=court, ruling=ruling)
            # title the court's own ruling only where a betänkande precedes it in
            # the same instance; otherwise the instans heading already names it
            head = ""
            if "betankande" in sib and court:
                label = "%s %s" % (_dv_genitive(court), ruling)
                anchor = toc.add(None, label, 2)
                head = ('<h3 id="%s" class="instans-rubrik">%s</h3>'
                        % (escape(anchor), escape(label)))
            out.append('<section class="dom">%s%s</section>' % (head, inner))
        elif t in ("domskal", "domslut"):                # transparent wrappers
            out.append(_dv_walk(n.get("children", []), site, doc_uri, toc, rail,
                                court=court, ruling=ruling))
        else:
            out.append(render_node(n, site, doc_uri, toc, rail))
    return "".join(out)


def _dv_footnotes(footnotes, site):
    """The end-of-document footnotes as an endnote list, each with a ↩ link back
    to its inline marker (#fnref-N)."""
    if not footnotes:
        return ""
    items = []
    for fn in footnotes:
        n = escape(str(fn["num"]))
        items.append('<li id="fn-%s">%s <a class="fn-back" href="#fnref-%s" '
                     'aria-label="Tillbaka till texten">↩</a></li>'
                     % (n, render_runs(fn["text"], site), n))
    return ('<section class="fotnoter"><h2>Fotnoter</h2><ol>%s</ol></section>'
            % "".join(items))


def render_dv(art, site):
    md = art.get("metadata", {})
    # heading by canonical identity + HD's given name (the stamped artifact label;
    # computed live for an artifact parsed before the field). The löpnummer
    # ("NJA 2025:58") stays metadata, never part of the identity string.
    title = art.get("label") or dv_naming.case_label(art)
    meta = _meta_dl([
        ("Domstol", art.get("court_namn")),
        ("Avgörandedatum", art.get("avgorandedatum")),
        ("Målnummer", ", ".join(art.get("malnummer") or [])),
        ("Löpnummer", ", ".join(dv_naming.lopnummer(art))),
        ("Rättsområde", ", ".join(md.get("rattsomrade") or [])),
    ])
    summary = ('<p class="sammanfattning">%s</p>' % escape(md["sammanfattning"])
               if md.get("sammanfattning") else "")
    sokord = _keywords(md.get("nyckelord") or [], site)
    toc = Toc()
    rail = Rail(site, art["uri"])
    # a record with explicit instance structure (HD's modern <h1>-tagged form) is
    # walked as nested sections; a flat legacy record has no structural wrappers,
    # so the same walk renders it as a plain paragraph sequence
    body = (document_inbound(site, art["uri"]) + sokord + summary
            + _dv_walk(art.get("structure", []), site, art["uri"], toc, rail,
                       ruling=_dv_ruling_word(art))
            + _dv_footnotes(art.get("footnotes", []), site))
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
        uri = site.resolve(begrepp_uri(n))      # fold onto the canonical concept
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
    state = {"page": None}

    def emit_page(node):
        # page anchor (#sid{N} -- the förarbete citation target, unchanged by the
        # hierarchy); the statute/case paragraphs citing this page drive the rail
        pg = node.get("page")
        if pg and pg != state["page"]:
            state["page"] = pg
            key = "sid%d" % pg
            rail.add(key, "s. %d" % pg)
            parts.append('<span class="sid" id="%s"%s>%d</span>'
                         % (key, _rail_attr(rail, key), pg))

    def walk(nodes):
        for n in nodes:
            emit_page(n)
            if n.get("type") == "avsnitt":
                level = n.get("level") or 1
                anchor = toc.add(n.get("id"), plain(n["text"]), level)
                parts.append('<h%d id="%s" class="rubrik">%s</h%d>'
                             % (min(level + 1, 5), escape(anchor),
                                render_runs(n["text"], site), min(level + 1, 5)))
                walk(n.get("children", []))
            else:
                parts.append("<p>%s</p>" % render_runs(n["text"], site))

    walk(art.get("structure", []))
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


def render_begrepp(art, site):
    """A concept definition; its inbound panel shows everything (laws, cases,
    förarbeten, commentary, other concepts) that references the concept."""
    title = art.get("title") or catalog.local(art["uri"])
    meta = _meta_dl([("Kategori", ", ".join(art.get("categories") or []))])
    toc = Toc()
    rail = Rail(site, art["uri"])
    nodes = art.get("body", [])
    # a synthesized stub (a defined term / nyckelord with no wiki page) has no
    # description -- its value is the aggregated inbound below (what defines and
    # tags it), so say so instead of showing a blank page
    note = ("" if nodes else
            '<p class="stub-note">Det här begreppet har ännu ingen beskrivning. '
            'Nedan visas var det definieras och används.</p>')
    body = note + document_inbound(site, art["uri"]) + "".join(
        render_node(b, site, art["uri"], toc, rail) for b in nodes)
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

def _sub_to_dot(key):
    """Normalise a sub-article ref to the canonical dotted id grammar --
    "6(2)(a)" -> "6.2.a" -- tolerating the legacy parenthesised form an older
    `.ann` may still carry (new ones are authored dotted)."""
    return re.sub(r"\(([^)]+)\)", r".\1", key)


class Editorial:
    """The `.ann` editorial layer for one EU act, mapping both directions of the
    preamble<->enacting-terms relation: an article (or sub-article like "4.5")
    to the recitals that explain it, and a recital back to the articles it
    underpins plus the thematic group it belongs to."""

    def __init__(self, layer):
        # keys are normalised to the dotted sub-article grammar the renderer mints,
        # so recitals_for(subarticle_key(...)) matches regardless of the on-disk form
        self.a2r = {_sub_to_dot(k): v
                    for k, v in layer.get("articleToRecitals", {}).items()}
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
            art = key.split(".", 1)[0]                       # "6.2.a" -> "6"
            for n in recitals:
                articles.setdefault(n, set()).add(art)
        self.recital_articles = {n: sorted(a, key=_art_sort_key)
                                 for n, a in articles.items()}

    def recitals_for(self, key):
        return self.a2r.get(key)


def _art_sort_key(art):
    """Sort article numbers numerically where possible ('2' before '10')."""
    return (0, int(art)) if art.isdigit() else (1, art)


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
    if bid and "." in bid:            # a dotted sub-article id ("5.2", "6.2.a")
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
    # a numbered recital is a citation target in its own right (`#recital-N`), so
    # it can be cited, commented on and ride the rail even with no editorial layer.
    if t == "recital" and num and num.isdigit():
        bid = "recital-%s" % num
    # editorial layer (.ann): wire this block into the article<->recital graph.
    # A recital gets a back-link panel (its articles + group); an article/
    # sub-article (paragraph/point, keyed like the .ann's "4.5") gets a forward
    # panel of its relevant recitals. Both ride the scroll-driven rail.
    extra = ""
    if t == "recital" and num and num.isdigit():
        if editorial:
            extra = _recital_context_html(editorial, int(num))
    else:
        # an article's key is its own id; a sub-article's is the dotted form. The
        # editorial layer gives a block a forward panel of its relevant recitals.
        # A sub-article (paragraph/point) carries no structural id of its own, so
        # it becomes a citation target -- gets an anchor + rides the rail -- only
        # when something targets it: the editorial recital links, or a per-node
        # guidance/commentary link (the linker's fine-grained "2.21" keys).
        key = (cur_article if t == "article"
               else subarticle_key(t, num, cur_article, cur_parag))
        if key:
            recitals = editorial.recitals_for(key) if editorial else None
            if t != "article" and (
                    recitals or (site.article_guidance or {}).get((doc_uri, key))):
                bid = key              # synthesise the sub-article citation id
            if recitals:
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
    # the heading is the act's short name (curated or extracted, stamped onto the
    # artifact at parse) plus its citing acronym -- "Cyberresiliensförordningen
    # (CRA)"; the full official title moves into the metadata list. With no short
    # name the heading is the full title, so it is not repeated in the metadata.
    # display_title is the single definition of this, shared with search/listings.
    title = catalog.display_title(art, art.get("title") or catalog.local(art["uri"]))
    meta = _meta_dl([
        ("Titel", art.get("title") if art.get("shortname") else None),
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
    # the artifact is a nested structure (divisions > articles > paragraphs >
    # points); render reads it in document order -- the heading levels and the
    # TOC already convey the hierarchy, so no nested <section> markup is needed
    for b in eurlex_flatten(art.get("structure", [])):
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
    rail.add_document()        # external links + commentary, the rail's default panel
    body = "".join(parts)
    kind = EURLEX_KIND.get(art.get("doctype"), "EU-rättsakt")
    return page(title, kind, meta, body, render_toc(toc),
                eyebrow=kind, island=rail.island(),
                source_url=art.get("source_url"))


def _ref_link(site, uri):
    """A link to a referenced document for a föreskrift's outbound metadata
    (bemyndigande -> SFS paragraf, genomför -> EU directive): the statute
    paragraf pinpointed and named, or the CELEX out to EUR-Lex; a plain span
    for an SFS we have not parsed."""
    if is_external(uri):
        return ('<a class="ext" href="%s" rel="external">%s</a>'
                % (escape(external_href(uri)),
                   escape(catalog.local(uri).rsplit("/", 1)[-1])))
    base, _, frag = uri.partition("#")
    pin = human_fragment(frag)
    name = _law_title(site, base)
    label = ("%s %s" % (pin, name)).strip() if pin else name
    return ('<a href="%s">%s</a>' % (escape(href(uri)), escape(label))
            if site.has(base) else '<span class="noref">%s</span>' % escape(label))


def _ref_list(site, heading, uris):
    if not uris:
        return ""
    items = "".join("<li>%s</li>" % _ref_link(site, u) for u in uris)
    return ('<section class="refs"><h2>%s</h2><ul>%s</ul></section>'
            % (escape(heading), items))


def render_foreskrift(art, site):
    md = art.get("metadata", {})
    ident = art.get("identifier") or catalog.local(art["uri"])
    title = md.get("title") or ident
    meta = _meta_dl([
        ("Utgivare", md.get("publisher")),
        ("Beslutad", md.get("beslutsdatum")),
        ("Ikraftträdande", md.get("ikrafttradandedatum")),
        ("Utkom från trycket", md.get("utkomFranTryck")),
    ])
    # outbound: the empowering statute paragrafer (the inbound mirror of which is
    # the SFS paragraf's "Föreskrifter meddelade med stöd av …" margin) + EU dir
    refs = (_ref_list(site, "Bemyndigande", md.get("bemyndigande"))
            + _ref_list(site, "Genomför EU-direktiv", md.get("genomfor")))
    toc = Toc()
    rail = Rail(site, art["uri"])
    body = document_inbound(site, art["uri"]) + refs + "".join(
        render_node(n, site, art["uri"], toc, rail)
        for n in art.get("structure", []))
    return page(title, "Föreskrift", meta, body, render_toc(toc),
                eyebrow=ident, island=rail.island(),
                source_url=art.get("source_url"))


def render_avg(art, site):
    md = art.get("metadata", {})
    ident = art.get("identifier") or catalog.local(art["uri"])
    title = md.get("title") or ident
    meta = _meta_dl([
        ("Myndighet", md.get("publisher")),
        ("Beslutsdatum", md.get("beslutsdatum")),
        ("Diarienummer", ", ".join(md.get("diarienummer", []))),
        ("Avgjord av", md.get("avgjordAv")),
        ("Sakområde", ", ".join(md.get("nyckelord", [])) or None),
    ])
    summary = ('<p class="sammanfattning">%s</p>'
               % escape(art["sammanfattning"])
               if art.get("sammanfattning") else "")
    toc = Toc()
    rail = Rail(site, art["uri"])
    body = document_inbound(site, art["uri"]) + summary + "".join(
        render_node(n, site, art["uri"], toc, rail)
        for n in art.get("structure", []))
    section = {"jo": "JO-beslut", "jk": "JK-beslut"}.get(art.get("org"),
                                                         "Myndighetsavgörande")
    return page(title, section, meta, body, render_toc(toc),
                eyebrow=ident, island=rail.island(),
                source_url=art.get("source_url"))


def render_document(art, source, site):
    # kommentar is not here -- it is an annotation rendered into statute rails
    # (generate_site skips it), not a page of its own
    return {"sfs": render_sfs, "dv": render_dv, "forarbete": render_forarbete,
            "begrepp": render_begrepp, "eurlex": render_eurlex,
            "foreskrift": render_foreskrift, "avg": render_avg}[source](art, site)


# --------------------------------------------------------------------------
# frontpage
# --------------------------------------------------------------------------

# the document types, in the order they appear on the frontpage, with their
# Swedish collection labels. dv's documents (and so its browse index) live under
# /dom/, lagen.nu's grammar; every other source browses under its own name.
# kommentar is an annotation layer shown in the rail (no page tree), so it is
# not a browsable source on the frontpage
SOURCE_ORDER = ("sfs", "dv", "forarbete", "foreskrift", "avg", "eurlex",
                "begrepp")
SOURCE_LABEL = {"sfs": "Författningar", "dv": "Rättsfall",
                "forarbete": "Förarbeten", "foreskrift": "Myndighetsföreskrifter",
                "avg": "JO- och JK-beslut", "eurlex": "EU-rättsakter",
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
    n = {s: c for s, c in catalog.counts(con).items() if s != "kommentar"}
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


# --------------------------------------------------------------------------
# faceted browse. A whole source is too large for one flat listing, so it is
# sliced into one or two facets (a law's subject initial, a case's court + year).
# The generator is a *client of the REST API*: it reads the browse model from
# GET /api/v1/browse (the navigator + each leaf bucket's ordered, labelled
# documents) and writes static HTML -- it never touches the catalog directly.
# Every leaf bucket becomes its own page ("Författningar som börjar på A",
# "NJA – Högsta domstolen 2024") with a navigator linking the sibling buckets,
# so the site is browsable with no JS.
# --------------------------------------------------------------------------

def _browse_client(catalog_path):
    """An in-process API client bound to `catalog_path` -- the generator consumes
    the same REST endpoints a network client would, with no running server. The
    get_con override is cleared by the caller (render_aggregates)."""
    def _con():
        con = sqlite3.connect("file:%s?mode=ro" % catalog_path, uri=True)
        try:
            yield con
        finally:
            con.close()
    api_service.app.dependency_overrides[api_service.get_con] = _con
    return TestClient(api_service.app)


def _browse_url(source, slugs):
    """Absolute URL of a browse bucket page (a directory, trailing slash)."""
    return "/" + "/".join([_browse_dir(source), *slugs]) + "/"


def _browse_item(doc):
    # a statute carries a split title: the designation/number prefix is shown
    # subdued, the sort subject emphasised, so the eye lands on where it files.
    # data-name/data-year drive the client-side filter. A non-statute (förordning,
    # kungörelse, …) dims the whole entry.
    if doc.get("key") is not None:
        cls = ' class="subdued"' if doc.get("subdued") else ""
        name = (doc.get("pre") or "") + doc["key"]
        label = ('<span class="pre">%s</span>%s'
                 % (escape(doc.get("pre") or ""), escape(doc["key"])))
        return ('<li%s data-name="%s" data-year="%s"><a href="%s">%s</a></li>'
                % (cls, escape(name.lower()), escape(doc.get("year") or ""),
                   escape(doc["url"]), label))
    return ('<li><a href="%s">%s</a></li>'
            % (escape(doc["url"]), escape(doc["display"])))


def _facet_links(source, buckets, parent_slugs, active_keys, depth):
    items = []
    for b in buckets:
        url = _browse_url(source, parent_slugs + [b["slug"]])
        cur = (' aria-current="page"' if depth < len(active_keys)
               and active_keys[depth] == b["key"] else "")
        items.append('<li><a href="%s"%s>%s <span class="c">%d</span></a></li>'
                     % (escape(url), cur, escape(b["label"]), b["count"]))
    return '<ul class="facet-list">%s</ul>' % "".join(items)


def _facet_nav(source, view, active_keys):
    """The navigator: the primary buckets as links, plus -- under the active
    primary -- its secondary buckets (the year/… within a court/type)."""
    levels, buckets = view["levels"], view["buckets"]
    parts = ['<h2 class="facet-axis">%s</h2>' % escape(levels[0]),
             _facet_links(source, buckets, [], active_keys, 0)]
    if len(levels) > 1:
        cur = next((b for b in buckets if b["key"] == active_keys[0]), None)
        if cur and cur["children"]:
            parts.append('<h2 class="facet-axis">%s</h2>' % escape(levels[1]))
            parts.append(_facet_links(source, cur["children"], [cur["slug"]],
                                      active_keys, 1))
    return '<nav class="facets">%s</nav>' % "".join(parts)


def _bucket_heading(source, levels, nodes):
    """The reading heading for a leaf bucket -- 'Författningar som börjar på A',
    'NJA – Högsta domstolen 2024', 'Förordningar 2016'."""
    if len(levels) == 1:
        return "%s som börjar på %s" % (SOURCE_LABEL.get(source, source), nodes[0]["key"])
    return "%s %s" % (nodes[0]["label"], nodes[1]["key"])


# client-side filter for a statute listing: narrows this letter's entries by name
# substring, or -- when the query is all digits -- by year prefix. data-name/
# data-year live on each <li>; the running match count updates .browse-shown.
BROWSE_FILTER = ('<input type="search" class="browse-filter" '
                 'placeholder="Filtrera på namn eller år…" '
                 'aria-label="Filtrera författningar">')

BROWSE_FILTER_JS = """<script>
(function(){
  var box=document.querySelector('.browse-filter'),
      list=document.querySelector('.browse-list');
  if(!box||!list)return;
  var items=Array.prototype.slice.call(list.children),
      shown=document.querySelector('.browse-shown');
  box.addEventListener('input',function(){
    var q=box.value.trim().toLowerCase(), byYear=/^[0-9]+$/.test(q), n=0;
    items.forEach(function(li){
      var ok=!q||(byYear
        ?(li.getAttribute('data-year')||'').indexOf(q)===0
        :(li.getAttribute('data-name')||'').indexOf(q)!==-1);
      li.hidden=!ok; if(ok)n++;
    });
    if(shown)shown.textContent=n;
  });
})();
</script>"""


def render_facet_page(source, view, nodes):
    """A single browse bucket page: the navigator + this leaf bucket's document
    list. `nodes` is the bucket-node path (one per level); the leaf carries its
    `documents` (from the API, already ordered and labelled). A statute listing
    also gets a client-side name/year filter over the letter's entries."""
    heading = _bucket_heading(source, view["levels"], nodes)
    docs = nodes[-1].get("documents") or []
    listing = ('<ul class="browse-list">%s</ul>' % "".join(_browse_item(d) for d in docs)
               if docs else '<p class="empty">Inga dokument.</p>')
    filtered = source == "sfs" and bool(docs)
    body = ('%s<section class="browse-group"><h1>%s '
            '<span class="c"><span class="browse-shown">%d</span></span></h1>%s%s%s</section>'
            % (_facet_nav(source, view, [n["key"] for n in nodes]),
               escape(heading), len(docs),
               BROWSE_FILTER if filtered else "", listing,
               BROWSE_FILTER_JS if filtered else ""))
    return page(heading, "Bläddra", "", body, solo=True)


def _write_browse(out_root, source, slugs, html):
    target = Path(out_root).joinpath(_browse_dir(source), *slugs)
    target.mkdir(parents=True, exist_ok=True)
    (target / "index.html").write_text(html)


def generate_browse(client, source, out_root):
    """Write every leaf-bucket page of one source from the API's browse model,
    plus the landing copies: a primary bucket's directory shows its first
    (default) child, and the source root shows the overall default bucket -- so
    /dom/, /dom/nja/ and /dom/nja/2025/ all resolve without a redirect or JS.
    A source the API does not facet (kommentar) is skipped."""
    resp = client.get("/api/v1/browse", params={"source": source})
    if resp.status_code == 404:
        return
    view = resp.json()
    root_html = None
    for prim in view["buckets"]:
        leaves = [[prim, sec] for sec in prim["children"]] if prim["children"] \
            else [[prim]]
        for i, nodes in enumerate(leaves):
            slugs = [n["slug"] for n in nodes]
            html = render_facet_page(source, view, nodes)
            _write_browse(out_root, source, slugs, html)
            if len(nodes) > 1 and i == 0:        # primary landing = first child
                _write_browse(out_root, source, slugs[:1], html)
            if root_html is None:                # overall default = first leaf
                root_html = html
    _write_browse(out_root, source, [], root_html)


# --------------------------------------------------------------------------
# generate the whole site
# --------------------------------------------------------------------------

# per-worker render state, set once per process by _render_init -- the catalog
# connection and Site can't cross the ProcessPool fork, so each worker builds its
# own once and renders many pages against it (mirrors build.run_action's pattern)
_RENDER: dict = {}


def _render_init(catalog_path, out_root):
    con = catalog.connect(catalog_path)
    _RENDER.update(con=con, site=Site.from_catalog(con), out_root=Path(out_root))


def _write_page(uri, source, path, title, site, out_root):
    """Render one document to its HTML file. A synthesized concept stub has no
    artifact on disk (empty path) and renders a shell whose content is its
    aggregated inbound (what defines/tags the concept); everything else loads its
    artifact."""
    art = (json.loads(Path(path).read_bytes()) if path
           else {"uri": uri, "type": source, "title": title})
    out = Path(out_root) / doc_relpath(uri)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_document(art, source, site))


def _render_one(job):
    """ProcessPool entry point: render `job` (uri, source, path, title) against
    this worker's prebuilt Site, returning the uri rendered."""
    _write_page(*job, _RENDER["site"], _RENDER["out_root"])  # ty: ignore[too-many-positional-arguments]
    return job[0]


def generate_site(catalog_path, out_root, progress=None, fresh=None, record=None,
                  only=None, source=None, jobs=1):
    """Render every catalogued document to static HTML. `fresh(uri, out_path,
    art_path, dep_digest) -> bool` lets the caller skip a page whose inputs are
    unchanged (incremental generate); `record(uri, art_path, dep_digest)` is
    called after a page is (re)rendered so the caller can store its new
    signature. `art_path` is the page's own artifact (content-hashed by the
    caller); `dep_digest` captures its citation relationships (set-based).
    `only`, a set of artifact path strings, restricts the run to those documents
    (a targeted `lagen <source> generate <id>`) -- the corpus-wide aggregate
    pages are then left untouched. `jobs>1` renders the stale pages across a
    process pool. Returns (total_pages, rendered) -- rendered < total when pages
    were skipped."""
    out_root = Path(out_root)
    con = catalog.connect(catalog_path)
    site = Site.from_catalog(con)
    rows = con.execute(
        "SELECT uri, source, path, title FROM documents "
        "ORDER BY source, uri").fetchall()
    if source is not None:                       # whole-source scope (incl. stubs)
        rows = [r for r in rows if r[1] == source]
    elif only is not None:                       # specific-document scope
        rows = [r for r in rows if r[2] in only]
    # commentary is an annotation rendered into statute rails, not a page of its own
    rows = [r for r in rows if r[1] != "kommentar"]

    # Freshness planning is single-threaded: it reads the catalog + manifest and
    # hashes inputs (the manifest lives here in the parent). Fresh pages advance
    # the counter at once; stale ones go to `plan` to be rendered (in parallel).
    total = len(rows)
    done = rendered = 0
    plan = []                       # (uri, source, path, title, dep) needing render
    for (uri, src, path, title) in rows:
        out = out_root / doc_relpath(uri)
        dep = catalog.page_dependency_digest(con, uri)
        if fresh and fresh(uri, out, path, dep):
            done += 1
            if progress and done % 500 == 0:
                progress(done, total, catalog.local(uri), rendered)
        else:
            plan.append((uri, src, path, title, dep))

    def finish(uri, path, dep):
        nonlocal done, rendered
        done += 1
        rendered += 1
        if record:
            record(uri, path, dep)
        if progress:
            progress(done, total, catalog.local(uri), rendered)

    if jobs > 1 and len(plan) > 1:
        with ProcessPoolExecutor(max_workers=jobs, initializer=_render_init,
                                 initargs=(catalog_path, out_root)) as pool:
            futures = {pool.submit(_render_one, job[:4]): job for job in plan}
            for fut in as_completed(futures):
                fut.result()                 # propagate a render error (abort)
                uri, src, path, title, dep = futures[fut]
                finish(uri, path, dep)
    else:
        for (uri, src, path, title, dep) in plan:
            _write_page(uri, src, path, title, site, out_root)
            finish(uri, path, dep)

    if only is None and source is None:          # corpus-wide pages on a full run
        render_aggregates(con, out_root, catalog_path)
    if progress:
        progress(total, total, "", rendered)
    con.close()
    return total, rendered


def render_aggregates(con, out_root, catalog_path):
    """Write the corpus-wide pages -- stylesheet, scripts, frontpage and the
    per-source faceted browse -- from the catalog. They depend on the whole
    document set (not on any single artifact), so they are cheap and always
    rebuilt; `lagen all generate --aggregates-only` runs just this, skipping the
    per-document render. The browse pages are written through the REST API (an
    in-process client over `catalog_path`), the frontpage from the catalog."""
    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "style.css").write_text(CSS)
    (out_root / "scrollspy.js").write_text(SCROLLSPY)
    (out_root / "search.js").write_text(SEARCH)
    (out_root / "index.html").write_text(render_index(con))
    client = _browse_client(catalog_path)
    try:
        for source in catalog.counts(con):
            if source == "kommentar":      # an annotation layer, not a source
                continue
            generate_browse(client, source, out_root)
    finally:
        api_service.app.dependency_overrides.pop(api_service.get_con, None)


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
/* scrollspy collapses every branch but the active section's ancestor path, so
   only top-level entries (plus the open branch) show; no-JS keeps all visible */
nav.toc a.toc-collapsed { display: none; }

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

/* -- Upphävd (repealed) författning -- a clear repeal callout, a subdued reading
   column, and a fixed watermark that stays in view at any scroll depth (so an
   anchor link deep into the text still reads as repealed). -- */
.expired-banner { display: flex; flex-wrap: wrap; align-items: baseline; gap: .55rem;
                  margin: 0 0 2.25rem; padding: .7rem 1rem; border: 1px solid var(--rule);
                  border-left: 3px solid var(--accent); border-radius: 5px;
                  background: var(--surf-2); font-size: .92rem; color: var(--ink-2); }
.expired-banner strong { font-family: var(--serif); font-weight: 600; color: var(--accent);
                         text-transform: uppercase; letter-spacing: .04em; font-size: .82rem; }
.expired-banner a { color: var(--accent); }
body.expired .gr-main { color: var(--ink-3); }
body.expired .gr-main h1, body.expired .kaprubrik, body.expired .rubrik,
body.expired .artikel { color: var(--ink-2); }
body.expired::before { content: "Upphävd författning"; position: fixed;
                       top: 50%; left: 50%; transform: translate(-50%, -50%) rotate(-24deg);
                       font-family: var(--serif); font-weight: 600; white-space: nowrap;
                       font-size: min(11vw, 8rem); color: var(--accent); opacity: .08;
                       pointer-events: none; user-select: none; z-index: 1; }

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
/* DV instance/ruling structure */
.instans { margin: 1.75rem 0 0; }
.instans-rubrik { font-family: var(--serif); font-weight: 600; font-size: 1.45rem;
                  margin: 0 0 .6rem; padding-bottom: .2rem;
                  border-bottom: 1px solid var(--rule); }
.betankande > .instans-rubrik, .dom > .instans-rubrik,
.skiljaktig > .instans-rubrik, .tillagg > .instans-rubrik {
    font-size: 1.1rem; border-bottom: 0; color: var(--ink-2); }
.delmal-rubrik { font-family: var(--serif); font-weight: 600; font-size: 1.6rem;
                 margin: 1.5rem 0 .6rem; }
/* the föredragande's proposal HD decides on -- subdued, set apart from the ruling */
.betankande { border-left: 2px solid var(--rule); padding-left: 1rem;
              margin: 1rem 0; color: var(--ink-3); }
.betankande .instans-rubrik { color: var(--ink-3); font-style: italic; }
/* footnotes (HD 2023+): inline superscript markers + the endnote list */
sup.fnref { font-size: .7em; line-height: 0; }
sup.fnref a { text-decoration: none; padding: 0 .1em; }
.fotnoter { margin-top: 2.5rem; border-top: 1px solid var(--rule);
            padding-top: 1rem; font-size: .9rem; color: var(--ink-2); }
.fotnoter h2 { font-family: var(--serif); font-size: 1rem; font-weight: 600;
               border: 0; margin: 0 0 .5rem; }
.fotnoter ol { margin: 0; padding-left: 1.5rem; }
.fotnoter li { margin: .2rem 0; }
.fn-back { text-decoration: none; color: var(--ink-4); margin-left: .25rem; }
.fn-back:hover { color: var(--accent); }
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
/* commentary shown side-by-side in the rail (in place of a kommentar page) */
.rail-komm { margin-bottom: 1rem; }
.rail-komm p { font-family: var(--serif); font-size: .9rem; line-height: 1.5;
               margin: 0 0 .5rem; color: var(--ink); }
.komm-by { font-family: var(--serif); font-style: italic; font-size: .78rem;
           color: var(--ink-3); }
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
aside.genomfor, aside.motsvarighet { margin-top: 1rem; padding-top: .75rem; border-top: 1px solid var(--rule); }
aside.genomfor ul, aside.motsvarighet ul { list-style: none; margin: 0; padding: 0; font-size: .8rem; }
aside.genomfor li, aside.motsvarighet li { margin: .2rem 0; line-height: 1.4; }
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
/* curated external links (the act's "Externa länkar" rail section) */
.rail-sec.vagledning { margin-bottom: 1rem; }
.rail-sec.vagledning ul { list-style: none; margin: 0; padding: 0; font-size: .85rem; }
.rail-sec.vagledning li { margin: .3rem 0; line-height: 1.4; }
.rail-sec.vagledning .prov { color: var(--ink-3); font-style: italic; }

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
.browse-group { margin: 1.5rem 0; }
.browse-group h1 { font-family: var(--serif); font-weight: 500; font-size: 1.5rem;
                   border-bottom: 1px solid var(--rule); padding-bottom: .3rem; }
.browse-group h1 .c, .browse-group h2 .c { color: var(--ink-3); font-size: .8rem;
                   font-weight: 400; }
.browse-list, .browse-group ul { list-style: none; padding: 0; margin: .75rem 0 0;
                   columns: 18rem; column-gap: 2rem; font-size: .92rem; }
.browse-list li, .browse-group li { margin: .18rem 0; break-inside: avoid; }
/* statute listing: the dropped designation/number prefix is subdued so the eye
   lands on the sort subject; secondary instruments (förordning, …) dim wholesale */
.browse-list .pre { color: var(--ink-4); }
.browse-list li.subdued a { color: var(--ink-3); }
.browse-list li.subdued .pre { color: var(--ink-4); }
.browse-filter { width: 100%; max-width: 24rem; margin: .1rem 0 .9rem;
                 padding: .45rem .7rem; font-family: var(--sans); font-size: .9rem;
                 color: var(--ink); background: var(--surf-2);
                 border: 1px solid var(--rule); border-radius: 6px; }
.browse-filter:focus { outline: none; border-color: var(--accent);
                       background: var(--surf); }
.empty { color: var(--ink-3); font-style: italic; }

/* -- faceted browse navigator -- */
.facets { margin: 0 0 2rem; }
.facet-axis { font-family: var(--sans); text-transform: uppercase;
              letter-spacing: .06em; font-size: .72rem; font-weight: 600;
              color: var(--ink-3); margin: 1.1rem 0 .4rem; }
.facet-list { list-style: none; padding: 0; margin: 0; display: flex;
              flex-wrap: wrap; gap: .35rem .7rem; }
.facet-list a { display: inline-block; padding: .12rem .55rem; border-radius: 4px;
                background: var(--surf-2); color: var(--ink-2); font-size: .92rem;
                text-decoration: none; }
.facet-list a:hover { background: var(--surf-3); }
.facet-list a[aria-current] { background: var(--accent); color: #fff; }
.facet-list .c { color: var(--ink-4); font-size: .72rem; margin-left: .25rem; }
.facet-list a[aria-current] .c { color: rgba(255,255,255,.75); }

/* -- search palette (live full-text search via the REST API) -- */
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
.search-results:not(:empty) { border-top: 1px solid var(--rule);
                              max-height: 60vh; overflow-y: auto; }
.search-hit { display: block; padding: .7rem 1.25rem; border-bottom: 1px solid var(--rule);
              text-decoration: none; color: var(--ink); }
.search-hit:last-child { border-bottom: 0; }
.search-hit:hover, .search-hit.sel { background: var(--surf-2); }
.search-hit.sel { box-shadow: inset 3px 0 0 var(--accent); }
.search-hit .hit-title { display: block; font-family: var(--sans); font-weight: 600;
                         font-size: .95rem; }
.search-hit .hit-sub { display: block; font-family: var(--serif); color: var(--ink-2);
                       font-size: .9rem; }
.search-hit .hit-snip { display: block; font-family: var(--serif); color: var(--ink-3);
                        font-size: .82rem; margin-top: .15rem; }
.search-hit .hit-snip em { font-style: normal; background: var(--mark, #fdf2b8);
                           border-radius: 2px; padding: 0 1px; }

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
# active paragraph's panel, read from the JSON island the renderer emitted. The
# ⌘K search palette is a separate script (SEARCH, below). Plain DOM, no deps.
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

  // The TOC is a flat list whose nesting lives only in the lvlN class. Recover
  // each entry's parent (the nearest preceding entry at a shallower level, -1 for
  // a top-level entry) so the scrollspy can collapse the outline to just the
  // active section's ancestor path.
  var levels = links.map(function (a) {
    var m = a.className.match(/lvl(\\d)/);
    return m ? +m[1] : 1;
  });
  var parents = (function () {
    var par = [], stack = [];      // stack[level] = last index seen at that level
    for (var i = 0; i < levels.length; i++) {
      var lv = levels[i];
      par[i] = -1;
      for (var p = lv - 1; p >= 1; p--) {
        if (stack[p] != null) { par[i] = stack[p]; break; }
      }
      stack[lv] = i;
      for (var d = lv + 1; d < stack.length; d++) stack[d] = null;  // deeper resets
    }
    return par;
  })();

  // Show top-level entries always, plus the active entry, its ancestors, and the
  // direct children of any node on that path -- every other branch is hidden.
  function collapse(active) {
    var expanded = {};             // nodes whose children should stay visible
    for (var i = active; i >= 0; i = parents[i]) expanded[i] = true;
    for (var j = 0; j < links.length; j++) {
      var show = parents[j] < 0 || expanded[parents[j]];
      links[j].classList.toggle('toc-collapsed', !show);
    }
  }
  var rail = document.getElementById('rail');
  var marks = Array.prototype.slice.call(document.querySelectorAll('[data-rail]'));
  var EMPTY = '<div class="rail-empty">Ingen rättspraxis, förarbeten eller annan ' +
              'kontext har ännu knutits till denna del.</div>';
  // the document-level panel (commentary on the statute as a whole), keyed '' --
  // shown when no single paragraph is in focus (at the top of the document)
  var DEFAULT = island[''] || EMPTY;
  if (rail) rail.innerHTML = DEFAULT;

  var activeLink = -1, activeRail = '', activeMark = null, ticking = false;

  // swap the rail to a unit's panel and mark it active (idempotent per unit)
  function applyRail(best) {
    if (best === activeMark) return;
    var key = best ? best.getAttribute('data-rail') : '';
    activeRail = key;
    if (rail) rail.innerHTML = (key && island[key]) ? island[key] : DEFAULT;
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
    // the focus line, 120px below the viewport top. getBoundingClientRect().top is
    // viewport-relative, so it is correct regardless of a node's offsetParent -- a
    // [data-rail] ancestor is position:relative, which makes a nested node's
    // offsetTop reset per-section (the "rail stuck on the section's last paragraf"
    // bug once chapter sections carry commentary).
    var LINE = 120;
    if (links.length) {
      var idx = 0;
      for (var i = 0; i < targets.length; i++) {
        if (targets[i] && targets[i].getBoundingClientRect().top <= LINE) idx = i;
      }
      if (idx !== activeLink) {
        if (links[activeLink]) links[activeLink].classList.remove('active');
        activeLink = idx;
        var a = links[idx];
        if (a) {
          a.classList.add('active');
          collapse(idx);          // open only this section's branch (offsets after)
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
        if (marks[j].getBoundingClientRect().top <= LINE) best = marks[j];
      }
      applyRail(best);
    }
  }
  window.addEventListener('scroll', function () {
    if (!ticking) { ticking = true; requestAnimationFrame(update); }
  }, { passive: true });
  update();
})();
"""


# The ⌘K command palette -- live full-text search against the REST API. Its own
# script (search.js): the search UI is unrelated to the TOC scrollspy and is
# global to every page, so it does not ride along in scrollspy.js. The API is
# always same-origin (the site and the API are served by one process, lagen
# serve), so requests are relative ('/api/v1/...') -- never a baked absolute base,
# which can only go stale and point a cached page at the wrong/dead port.
# Debounced; renders the top hits as links to each document's matching paragraph.
SEARCH = """
(function () {
  var overlay = null, results = null, timer = null, seq = 0, sel = 0;

  // the API returns raw field values (correct for an API); the indexed text is
  // parsed remote content, so everything interpolated into innerHTML is escaped
  // here. The highlight fragment is the one exception with markup: OpenSearch
  // html-encodes the body (search.py HIGHLIGHT encoder) and only injects <em>.
  function esc(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function hits() {
    return results ? Array.prototype.slice.call(
      results.querySelectorAll('.search-hit')) : [];
  }
  function select(i) {
    var hs = hits();
    if (!hs.length) return;
    sel = (i + hs.length) % hs.length;
    hs.forEach(function (h, n) { h.classList.toggle('sel', n === sel); });
    hs[sel].scrollIntoView({ block: 'nearest' });
  }
  function render(items, q) {
    if (!results) return;
    if (!items.length) {
      results.innerHTML = '<div class="search-note">Inga träffar för ' +
        '\\u201d' + esc(q) + '\\u201d.</div>';
      return;
    }
    results.innerHTML = items.map(function (r) {
      // r.url is the hosted page path (server-computed via layout.page_relpath);
      // a fragment hit deep-links to its paragraph anchor (the node id == pinpoint)
      var frag = r.fragments && r.fragments[0];
      var hl = (frag && frag.highlight[0]) || (r.highlight && r.highlight[0]) || '';
      var target = (r.url || '#') + (frag && frag.pinpoint ? '#' + frag.pinpoint : '');
      // lead with the page title (display: short name + acronym where the act
      // has them, else the full title -- the same heading the document page
      // shows), and carry the citation id (CELEX / "SFS 2018:218") as the sub,
      // shown only when it differs from the title (DV's label == its title)
      var primary = r.display || r.title || r.identifier || r.uri;
      return '<a class="search-hit" href="' + esc(target) + '">' +
        '<span class="hit-title">' + esc(primary) + '</span>' +
        (r.identifier && r.identifier !== primary ?
          '<span class="hit-sub">' + esc(r.identifier) + '</span>' : '') +
        (hl ? '<span class="hit-snip">' + hl + '</span>' : '') + '</a>';
    }).join('');
    // the first hit is the resolved target for a citation-shaped query
    // ("avtalslagen 36" -> §36); selecting it means Enter goes straight there
    select(0);
  }
  function go() {
    // navigate to the selected hit (the first by default == the resolved target)
    var hs = hits();
    if (!hs.length) return false;
    window.location.href = hs[sel].getAttribute('href');
    return true;
  }
  function run(q, andGo) {
    var mine = ++seq;
    if (!q.trim()) { if (results) results.innerHTML = ''; return; }
    fetch('/api/v1/search?limit=8&q=' + encodeURIComponent(q))
      .then(function (r) { return r.json(); })
      .then(function (d) { if (mine === seq) { render(d.results || [], q); if (andGo) go(); } })
      .catch(function () {
        if (mine === seq && results)
          results.innerHTML = '<div class="search-note">Sökningen kunde inte ' +
            'nås.</div>';
      });
  }
  function open() {
    if (overlay) return;
    overlay = document.createElement('div');
    overlay.className = 'search-overlay';
    overlay.innerHTML = '<div class="search-box"><input autofocus ' +
      'placeholder="Sök lag, paragraf, rättsfall…">' +
      '<div class="search-results"></div></div>';
    document.body.appendChild(overlay);
    overlay.addEventListener('click', function (e) { if (e.target === overlay) close(); });
    var input = overlay.querySelector('input');
    results = overlay.querySelector('.search-results');
    input.addEventListener('input', function () {
      clearTimeout(timer);
      var q = input.value;
      timer = setTimeout(function () { run(q); }, 180);
    });
    input.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown') { e.preventDefault(); select(sel + 1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); select(sel - 1); }
      else if (e.key === 'Enter') {
        // Enter goes to the selected hit -- the first by default, which for a
        // citation-shaped query is the resolved §/article. If the debounced
        // results aren't in yet, fetch now and jump to the first hit.
        e.preventDefault();
        clearTimeout(timer);
        if (!go()) run(input.value, true);
      }
    });
    input.focus();
  }
  function close() { if (overlay) { overlay.remove(); overlay = null; results = null; } }
  document.addEventListener('keydown', function (e) {
    if ((e.metaKey || e.ctrlKey) && e.key === 'k') { e.preventDefault(); open(); }
    if (e.key === 'Escape') close();
  });
  document.addEventListener('click', function (e) {
    if (e.target.closest('[data-search]')) { e.preventDefault(); open(); }
  });
})();
"""
