"""Render parsed artifacts to a static, interlinked HTML site -- the `generate`
phase (REWRITE.md §6).

Two things make it the derived layer rather than a dumb pretty-printer:

  * outbound links are live -- every inline citation run becomes an <a> to the
    cited document's own page (and exact paragraph), so a case links into the
    statute it cites;
  * inbound links are annotated -- each statute paragraph carries, in its
    margin, the cases and laws that cite *it*, queried from the catalog. That
    round-trip (case -> paragraph -> back to every case on that paragraph) is
    the signature lagen.nu feature.

The artifact JSON is the contract: a single generic node walk renders both the
SFS structure tree and the DV body, keyed on each node's `type`. Inbound links
are surfaced at two granularities: per *paragraph* (margin annotation on any
id-bearing node) and per *document* (a panel for citations to the whole law or
case -- the 27% of citations that carry no #fragment, and all case inbound).

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
from .catalog import BASE
from .wikitext import begrepp_uri


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


# förarbete uri prefixes (prop/2025/26:161, sou/2020:1, …) -> the fa/ tree
FORARBETE = ("prop/", "sou/", "ds/", "dir/", "fm/", "skr/", "so/", "lr/", "bet/",
             "rskr/")


def doc_relpath(uri):
    """Output path (== href, sans fragment) for a document uri, routed by uri
    shape: dom/ -> cases, a förarbete prefix -> fa/, kommentar/ + begrepp/ to
    their own trees, else statutes."""
    loc, _ = split_uri(uri)
    if loc.startswith("dom/"):
        source = "dom"
    elif loc.startswith("kommentar/"):
        source = "kommentar"
    elif loc.startswith("begrepp/"):
        source = "begrepp"
    elif loc.startswith(FORARBETE):
        source = "fa"
    else:
        source = "sfs"
    return "%s/%s.html" % (source, _slug(loc))


def _slug(loc):
    return "".join(c if c.isalnum() else "_" for c in loc).strip("_")


EXT = BASE + "ext/"                          # the "external reference" namespace
CELEX = BASE + "ext/celex/"
EURLEX = "https://eur-lex.europa.eu/legal-content/SV/TXT/?uri=CELEX:%s"


def is_external(uri):
    """A lagen.nu `ext/` URI identifies a document the site doesn't host
    (EU acts via CELEX, …) -- it resolves to an external service, not a page."""
    return uri.startswith(EXT)


def href(uri):
    if uri.startswith(CELEX):
        return EURLEX % uri[len(CELEX):].split("#")[0]  # -> EUR-Lex
    if not uri.startswith(BASE):
        return uri  # already-absolute external
    _, frag = split_uri(uri)
    return "/" + doc_relpath(uri) + ("#" + frag if frag else "")


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
        elif is_external(run["uri"]):
            out.append('<a class="ext" href="%s" rel="external">%s</a>'
                       % (escape(href(run["uri"])), escape(run["text"])))
        elif run["uri"].startswith(BASE) and not site.has(run["uri"]):
            # we cite a document we don't have a page for -- show the text,
            # not a link that would 404. Becomes live once that doc is parsed.
            out.append('<span class="noref" title="%s">%s</span>'
                       % (escape(catalog.local(run["uri"])), escape(run["text"])))
        else:
            # live link; hover shows the target paragraph (+ its list items)
            tip = site.snippet(run["uri"])
            out.append('<a href="%s"%s>%s</a>'
                       % (escape(href(run["uri"])),
                          (' title="%s"' % escape(tip)) if tip else "",
                          escape(run["text"])))
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


def margin_inbound(site, uri):
    """Per-paragraph inbound, floated into the margin next to its node."""
    groups = _inbound_groups(site, uri)
    return ('<aside class="inbound"><div class="inbound-h">Hänvisat till av</div>'
            '%s</aside>' % groups) if groups else ""


def document_inbound(site, uri):
    """Document-level inbound: who cites the law/case/förarbete as a whole
    (the bare uri). Surfaces the citations no paragraph annotation shows."""
    groups = _inbound_groups(site, uri)
    return ('<section class="inbound-doc"><h2>Hänvisat till av</h2>%s</section>'
            % groups) if groups else ""


# --------------------------------------------------------------------------
# generic node renderer (artifact type -> HTML)
# --------------------------------------------------------------------------

def _id_attr(nid):
    return ' id="%s"' % escape(nid) if nid else ""


def render_node(node, site, doc_uri, toc):
    t = node.get("type")
    nid = node.get("id")
    anno = margin_inbound(site, doc_uri + "#" + nid) if nid else ""

    if t == "tabell":
        rows = "".join(render_node(c, site, doc_uri, toc)
                       for c in node.get("children", []))
        return "<table>%s</table>" % rows
    if t == "rad":
        cells = "".join("<td>%s</td>" % render_runs(c, site)
                        for c in node.get("cells", []))
        return "<tr>%s</tr>" % cells
    if t == "lista":
        items = "".join(render_node(c, site, doc_uri, toc)
                        for c in node.get("children", []))
        return "<ul>%s</ul>" % items
    if t == "rubrik":
        text = node.get("text", [])
        anchor = toc.add(nid, plain(text), node.get("level") or 1)
        lvl = min(node.get("level") or 2, 5) + 1
        return '<h%d id="%s" class="rubrik">%s</h%d>' % (
            lvl, escape(anchor), render_runs(text, site), lvl)

    if "text" in node:  # stycke/punkt/listelement/upphavd/moment (may nest)
        marker = node.get("beteckning") or node.get("ordinal")
        num = '<span class="num">%s</span> ' % escape(str(marker)) if marker else ""
        tag = "li" if t in ("punkt", "listelement") else "p"
        body = "<%s%s>%s%s</%s>" % (tag, _id_attr(nid), num,
                                    render_runs(node["text"], site), tag)
        # a stycke often introduces a list -- render its punkt/lista children
        # (previously dropped, so numbered lists vanished from the page)
        kids = node.get("children", [])
        if kids:
            inner = "".join(render_node(c, site, doc_uri, toc) for c in kids)
            if any(c.get("type") == "punkt" for c in kids):
                inner = '<ol class="punkter">%s</ol>' % inner
            body += inner
        return '<div class="block">%s%s</div>' % (anno, body) if anno else body

    # container: paragraf, kapitel, avdelning, bilaga, overgangsbestammelse, ...
    if t in ("kapitel", "avdelning", "underavdelning"):
        label = {"kapitel": "kap.", "avdelning": "Avd.",
                 "underavdelning": "Avd."}[t]
        # the chapter number; its title is a rubrik child that already reads
        # "1 kap. Statsskickets grunder", so the chapter goes in the TOC via
        # that rubrik, not as a redundant bare-number entry here
        head_text = ("%s %s" % (node.get("ordinal", ""), label)).strip()
        children = "".join(render_node(c, site, doc_uri, toc)
                           for c in node.get("children", []))
        head = '<h2%s class="kaprubrik">%s</h2>' % (_id_attr(nid),
                                                    escape(head_text))
        return '<section class="%s">%s%s</section>' % (t, head, children)
    children = "".join(render_node(c, site, doc_uri, toc)
                       for c in node.get("children", []))
    if t == "paragraf":
        return ('<section class="paragraf"%s>%s<div class="paragraf-body">%s</div>'
                '</section>' % (_id_attr(nid), anno, children))
    return '<section class="%s"%s>%s%s</section>' % (t or "node",
                                                     _id_attr(nid), anno, children)


# --------------------------------------------------------------------------
# page shells
# --------------------------------------------------------------------------

PAGE = """<!doctype html>
<html lang="sv"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%(title)s</title><link rel="stylesheet" href="/style.css">
</head><body>
<header><a href="/" class="home">lagen.nu</a> <span class="crumb">%(kind)s</span></header>
<div class="layout">%(toc)s<main><h1>%(title)s</h1>%(meta)s%(body)s</main></div>
<script src="/scrollspy.js" defer></script>
</body></html>
"""


def page(title, kind, meta, body, toc=""):
    return PAGE % {"title": escape(title), "kind": kind, "meta": meta,
                   "body": body, "toc": toc}


def render_sfs(art, site):
    props = art.get("metadata", {}).get("properties", {})
    title = props.get("dcterms:title") or ("SFS " + catalog.local(art["uri"]))
    meta = _meta_dl([
        ("Utfärdad", props.get("rpubl:utfardandedatum")),
        ("Ikraftträder", props.get("rpubl:ikrafttradandedatum")),
        ("Källa", props.get("dcterms:identifier")),
    ])
    toc = Toc()
    body = document_inbound(site, art["uri"]) + "".join(
        render_node(n, site, art["uri"], toc) for n in art.get("structure", []))
    return page(title, "Författning", meta, body, render_toc(toc))


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
    body = document_inbound(site, art["uri"]) + sokord + summary + "".join(
        render_node(b, site, art["uri"], toc) for b in art.get("body", []))
    return page(title, "Rättsfall", meta, body, render_toc(toc))


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


def render_forarbete(art, site):
    title = art.get("title") or art.get("identifier") or art["uri"]
    meta = _meta_dl([("Beteckning", art.get("identifier")),
                     ("Typ", FA_TYPE_LABEL.get(art.get("type"), art.get("type"))),
                     ("Datum", art.get("date"))])
    parts = [document_inbound(site, art["uri"])]
    toc = Toc()
    cur = None
    for b in art.get("body", []):
        if b.get("page") and b["page"] != cur:
            cur = b["page"]
            # page anchor (#sid{N} -- the förarbete citation target) + the
            # statute/case paragraphs citing this very page, in the margin
            parts.append('<span class="sid" id="sid%d">%d</span>%s'
                         % (cur, cur, margin_inbound(site, "%s#sid%d"
                                                     % (art["uri"], cur))))
        runs = render_runs(b["text"], site)
        if b["type"] == "rubrik":
            level = b.get("level") or 1
            anchor = toc.add(None, plain(b["text"]), level)
            parts.append('<h%d id="%s" class="rubrik">%s</h%d>'
                         % (min(level + 1, 5), escape(anchor), runs,
                            min(level + 1, 5)))
        else:
            parts.append("<p>%s</p>" % runs)
    return page(title, "Förarbete", meta, "".join(parts), render_toc(toc))


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
    parts = [document_inbound(site, art["uri"])]
    for b in art.get("body", []):
        t = b.get("type")
        if t == "sektion":
            toc.add(b["id"], b.get("heading", b["id"]), 1)
            parts.append('<h2 id="%s" class="kaprubrik">%s</h2>'
                         % (escape(b["id"]), render_runs(b["text"], site)))
            parts += ["<p>%s</p>" % render_runs(c["text"], site)
                      for c in b.get("children", [])]
        elif t == "rubrik":
            parts.append('<h3 class="rubrik">%s</h3>'
                         % render_runs(b["text"], site))
        else:
            parts.append("<p>%s</p>" % render_runs(b["text"], site))
    return page(title, "Kommentar", meta, "".join(parts), render_toc(toc))


def render_begrepp(art, site):
    """A concept definition; its inbound panel shows everything (laws, cases,
    förarbeten, commentary, other concepts) that references the concept."""
    title = art.get("title") or catalog.local(art["uri"])
    meta = _meta_dl([("Kategori", ", ".join(art.get("categories") or []))])
    toc = Toc()
    body = document_inbound(site, art["uri"]) + "".join(
        render_node(b, site, art["uri"], toc) for b in art.get("body", []))
    return page(title, "Begrepp", meta, body, render_toc(toc))


def render_document(art, source, site):
    return {"sfs": render_sfs, "dv": render_dv, "forarbete": render_forarbete,
            "kommentar": render_kommentar, "begrepp": render_begrepp}[source](
        art, site)


# --------------------------------------------------------------------------
# frontpage
# --------------------------------------------------------------------------

def render_index(con):
    n = catalog.counts(con)
    top = con.execute(
        "SELECT d.uri, d.title, COUNT(DISTINCT l.from_uri) c FROM links l "
        "JOIN documents d ON d.uri = l.to_root "
        "WHERE d.source = 'sfs' AND l.from_uri <> l.to_root GROUP BY l.to_root "
        "ORDER BY c DESC LIMIT 25").fetchall()
    topcases = con.execute(
        "SELECT d.uri, d.label, COUNT(DISTINCT l.from_uri) c FROM links l "
        "JOIN documents d ON d.uri = l.to_root "
        "WHERE d.source = 'dv' AND l.from_uri <> l.to_root GROUP BY l.to_root "
        "ORDER BY c DESC LIMIT 25").fetchall()
    most = "".join('<li><a href="%s">%s</a> <span class="c">%d</span></li>'
                   % (escape(href(u)), escape(t), c) for u, t, c in top)
    cases = "".join('<li><a href="%s">%s</a> <span class="c">%d</span></li>'
                    % (escape(href(u)), escape(l), c) for u, l, c in topcases)
    body = ('<p class="lead">%d författningar och %d rättsfall, '
            'sammanlänkade.</p>'
            '<p class="browse"><a href="/sfs/">Bläddra bland alla författningar</a>'
            ' · <a href="/dom/">bläddra bland alla rättsfall</a></p>'
            '<div class="cols"><section><h2>Mest hänvisade författningar</h2>'
            '<ol class="ranked">%s</ol></section>'
            '<section><h2>Mest hänvisade rättsfall</h2><ol class="ranked">%s</ol>'
            '</section></div>'
            % (n.get("sfs", 0), n.get("dv", 0), most, cases))
    return page("lagen.nu", "Start", "", body)


def _group_key(source, uri):
    """Browse grouping: laws by year, cases by court/series (from the uri)."""
    loc = catalog.local(uri)
    if source == "sfs":
        return loc.split(":")[0]                 # "1975:635" -> "1975"
    parts = loc.split("/")                        # "dom/nja/2011s357" -> "nja"
    return parts[1].upper() if len(parts) > 1 else "övriga"


def render_browse(con, source):
    """A complete, sectioned index of every document of one source: laws
    grouped by year (newest first), cases grouped by court (A–Ö)."""
    rows = con.execute("SELECT uri, label FROM documents WHERE source = ? "
                       "ORDER BY uri", (source,)).fetchall()
    groups = {}
    for uri, label in rows:
        groups.setdefault(_group_key(source, uri), []).append((uri, label))
    order = sorted(groups, reverse=(source == "sfs"))
    title, kind = (("Författningar", "Bläddra"), ("Rättsfall", "Bläddra"))[
        source == "dv"]
    sections = []
    for key in order:
        items = "".join('<li><a href="%s">%s</a></li>'
                        % (escape(href(u)), escape(lbl or catalog.local(u)))
                        for u, lbl in groups[key])
        sections.append('<section class="browse-group"><h2>%s '
                        '<span class="c">%d</span></h2><ul>%s</ul></section>'
                        % (escape(key), len(groups[key]), items))
    body = ('<p class="lead">%d %s</p>%s'
            % (len(rows), title.lower(), "".join(sections)))
    return page("Alla " + title.lower(), kind, "", body)


# --------------------------------------------------------------------------
# generate the whole site
# --------------------------------------------------------------------------

def generate_site(catalog_path, out_root, progress=None):
    out_root = Path(out_root)
    con = catalog.connect(catalog_path)
    site = Site.from_catalog(con)
    (out_root).mkdir(parents=True, exist_ok=True)
    (out_root / "style.css").write_text(CSS)
    (out_root / "scrollspy.js").write_text(SCROLLSPY)
    rows = con.execute(
        "SELECT uri, source, path FROM documents ORDER BY source, uri").fetchall()
    for i, (uri, source, path) in enumerate(rows):
        art = json.loads(Path(path).read_bytes())
        out = out_root / doc_relpath(uri)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_document(art, source, site))
        if progress and (i % 200 == 0):
            progress(i + 1, len(rows))
    (out_root / "index.html").write_text(render_index(con))
    for source in ("sfs", "dom"):
        (out_root / source).mkdir(parents=True, exist_ok=True)
        (out_root / source / "index.html").write_text(
            render_browse(con, "dv" if source == "dom" else source))
    if progress:
        progress(len(rows), len(rows))
    con.close()
    return len(rows)


CSS = """
:root { --ink:#1a1a1a; --muted:#6b6b6b; --rule:#e2e0d6; --paper:#fbfaf6;
        --link:#0b5; --case:#b50; --accent:#356; }
* { box-sizing: border-box; }
body { font: 16px/1.6 Georgia, 'Times New Roman', serif; color: var(--ink);
       background: var(--paper); margin: 0; }
header { border-bottom: 1px solid var(--rule); padding: .6rem 2rem;
         font-family: system-ui, sans-serif; font-size: .9rem; }
header .home { font-weight: 700; color: var(--accent); text-decoration: none; }
header .crumb { color: var(--muted); }
.layout { display: flex; gap: 2.5rem; max-width: 82rem; margin: 0 auto;
          align-items: flex-start; }
main { flex: 1 1 auto; min-width: 0; max-width: 64rem; padding: 1.5rem 2rem 6rem; }
nav.toc { flex: 0 0 16rem; position: sticky; top: 0; max-height: 100vh;
          overflow-y: auto; padding: 1.5rem 0 2rem 1rem;
          font-family: system-ui, sans-serif; font-size: .8rem; }
nav.toc .toc-h { text-transform: uppercase; letter-spacing: .04em;
                 font-size: .68rem; color: var(--muted); margin-bottom: .5rem; }
nav.toc a { display: block; color: var(--ink); text-decoration: none;
            padding: .15rem 0 .15rem .6rem; border-left: 2px solid transparent;
            line-height: 1.3; }
nav.toc a:hover { color: var(--accent); }
nav.toc a.lvl2 { padding-left: 1.5rem; color: #555; }
nav.toc a.lvl3 { padding-left: 2.4rem; color: var(--muted); font-size: .76rem; }
nav.toc a.active { border-left-color: var(--accent); color: var(--accent);
                   font-weight: 600; }
@media (max-width: 64rem) { nav.toc { display: none; } }
h1 { font-size: 1.8rem; line-height: 1.2; scroll-margin-top: 1rem; }
.rubrik, .kaprubrik, .sid { scroll-margin-top: 1rem; }
section.paragraf, .block { scroll-margin-top: 1rem; }
a { color: var(--link); text-decoration: none; }
a:hover { text-decoration: underline; }
dl.meta { font-family: system-ui, sans-serif; font-size: .85rem;
          color: var(--muted); border-left: 3px solid var(--rule);
          padding-left: 1rem; margin: 1rem 0 2rem; display: grid;
          grid-template-columns: max-content 1fr; column-gap: 1rem; row-gap: .2rem; }
dl.meta dt { font-weight: 600; }
dl.meta dd { margin: 0; }
.kaprubrik { font-size: 1.3rem; border-bottom: 1px solid var(--rule);
             padding-bottom: .2rem; margin-top: 2.5rem; }
.rubrik { font-size: 1.05rem; margin-top: 1.8rem; }
section.paragraf { margin: 1.1rem 0; }
.paragraf-body p { margin: .3rem 0; }
.num { font-weight: 700; color: var(--accent); font-family: system-ui, sans-serif;
       font-size: .9rem; }
.sammanfattning { font-style: italic; color: #333; border-left: 3px solid var(--accent);
                  padding-left: 1rem; }
.sokord { font-family: system-ui, sans-serif; font-size: .85rem; color: var(--muted); }
.sokord span { text-transform: uppercase; letter-spacing: .04em; font-size: .7rem;
               margin-right: .4rem; }
.kommentar-law { font-family: system-ui, sans-serif; font-size: .95rem;
                 background: #fbf3f6; border-left: 3px solid #a36; padding: .4rem 1rem; }
aside.inbound { float: right; clear: right; width: 15rem; margin: .2rem -1rem 1rem 1.5rem;
                font-family: system-ui, sans-serif; font-size: .76rem;
                background: #fff; border: 1px solid var(--rule); border-radius: 6px;
                padding: .5rem .7rem; line-height: 1.4; }
aside.inbound .inbound-h { text-transform: uppercase; letter-spacing: .04em;
                           color: var(--muted); font-size: .68rem; margin-bottom: .3rem; }
.ingroup { margin-top: .4rem; }
.ingroup-h { text-transform: uppercase; letter-spacing: .03em; font-size: .62rem;
             color: var(--accent); font-weight: 600; margin: .35rem 0 .1rem; }
.ingroup ul { list-style: none; margin: 0; padding: 0; }
.ingroup li { margin: .1rem 0; }
.ingroup.dv a { color: var(--case); }
.ingroup.forarbete a { color: #76408a; }
.ingroup.kommentar a { color: #a36; font-weight: 600; }
.ingroup.begrepp a { color: var(--accent); }
.inbound .more, .inbound-doc .more { color: var(--muted); font-style: italic;
                                     font-size: .72rem; margin-top: .3rem; }
.inbound-doc { background: #fff; border: 1px solid var(--rule); border-radius: 6px;
               padding: .6rem 1rem; margin: 1rem 0 2rem;
               font-family: system-ui, sans-serif; font-size: .82rem; }
.inbound-doc h2 { font-size: .72rem; text-transform: uppercase; letter-spacing: .04em;
                  color: var(--muted); margin: 0 0 .4rem; border: 0; }
.inbound-doc .ingroup { columns: 2; column-gap: 2rem; }
.inbound-doc .ingroup-h { column-span: all; }
.inbound-doc li { break-inside: avoid; }
ol.punkter { list-style: none; margin: .2rem 0; padding-left: 1.5rem; }
ol.punkter > li { margin: .2rem 0; }
.noref { color: #555; border-bottom: 1px dotted #bbb; cursor: help; }
.sid { display: block; float: right; clear: right; color: var(--muted);
       font-family: system-ui, sans-serif; font-size: .7rem; margin: .3rem 0;
       border: 1px solid var(--rule); border-radius: 3px; padding: 0 .3rem; }
.sid::before { content: "s. "; }
a.ext { color: var(--accent); }
a.ext::after { content: " \\2197"; font-size: .8em; color: var(--muted); }
table { border-collapse: collapse; margin: 1rem 0; width: 100%; }
td { border-top: 1px solid var(--rule); padding: .3rem .6rem; vertical-align: top; }
.lead { font-size: 1.15rem; color: #333; }
.cols { display: grid; grid-template-columns: 1fr 1fr; gap: 2.5rem; margin-top: 2rem; }
.cols h2 { font-size: 1.1rem; border-bottom: 1px solid var(--rule); padding-bottom: .2rem; }
ol.ranked { padding-left: 1.4rem; } ol.ranked .c { color: var(--muted); font-size: .8rem; }
.cols ul { list-style: none; padding: 0; } .cols li { margin: .25rem 0; }
.browse { font-family: system-ui, sans-serif; font-size: .95rem; }
.browse-group { margin: 1.5rem 0; }
.browse-group h2 { font-size: 1.05rem; border-bottom: 1px solid var(--rule);
                   padding-bottom: .2rem; }
.browse-group h2 .c { color: var(--muted); font-size: .8rem; font-weight: 400; }
.browse-group ul { list-style: none; padding: 0; margin: .5rem 0 0;
                   columns: 16rem; column-gap: 2rem; font-size: .9rem; }
.browse-group li { margin: .15rem 0; break-inside: avoid; }
"""


# Scrollspy: highlight the TOC entry for the section currently at the top of the
# viewport, and keep the active entry visible within a scrollable TOC. Plain DOM
# + a throttled scroll handler -- no dependencies, works on a static file.
SCROLLSPY = """
(function () {
  var toc = document.querySelector('nav.toc');
  if (!toc) return;
  var links = Array.prototype.slice.call(toc.querySelectorAll('a'));
  var targets = links.map(function (a) {
    return document.getElementById(decodeURIComponent(a.getAttribute('href').slice(1)));
  });
  var active = -1, ticking = false;
  function update() {
    ticking = false;
    var y = window.scrollY + 100, idx = 0;
    for (var i = 0; i < targets.length; i++) {
      if (targets[i] && targets[i].offsetTop <= y) idx = i;
    }
    if (idx === active) return;
    if (links[active]) links[active].classList.remove('active');
    active = idx;
    var a = links[active];
    if (!a) return;
    a.classList.add('active');
    if (a.offsetTop < toc.scrollTop ||
        a.offsetTop > toc.scrollTop + toc.clientHeight - 30) {
      toc.scrollTop = a.offsetTop - toc.clientHeight / 2;
    }
  }
  window.addEventListener('scroll', function () {
    if (!ticking) { ticking = true; requestAnimationFrame(update); }
  }, { passive: true });
  update();
})();
"""
