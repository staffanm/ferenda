"""EU-rättsaktssidan: articles, recitals and the editorial annotation
layer.

Registered as this source's page renderer in `build.SOURCE_RENDERERS`;
`render` is the `(art, site) -> str` the generate driver calls.
"""
import json
import re
from html import escape

from markupsafe import Markup

from ..lib import annstore, catalog, labels, tpl
from ..lib.eu_structure import Anchors, citable, first_stycke
from ..lib.eu_structure import flatten as eurlex_flatten
from ..lib.markdown import begrepp_uri
from ..lib.page import (
    NODES,
    Rail,
    RailSection,
    Toc,
    doc_meta,
    href,
    page_context,
    plain,
    render_runs,
    render_toc,
    swedish_join,
)
from ..lib.pinpoint import eu_article_label, human_fragment

ENV = tpl.environment("accommodanda.eurlex")


EURLEX_KIND = {"regulation": "EU-förordning", "directive": "EU-direktiv",
               "decision": "EU-beslut", "judgment": "EU-domstolen",
               "treaty": "Fördrag", "act": "EU-rättsakt"}

# block type -> css class for the generic (paragraph-like) EU blocks
EURLEX_CLASS = {"recital": "recital", "citation": "visa", "preamble": "preamble",
                "paragraph": "paragraph", "stycke": "stycke",
                "point": "point", "ruling": "ruling",
                "note": "note", "row": "row"}


# --------------------------------------------------------------------------
# editorial layer (a `.ann` file in the curated store, lib.annstore): thematic
# recital groups + the article<->recital cross-reference, folded into an EU
# act's page. Authored offline by `lagen eurlex ai-annotate`; absent for an
# unannotated act.
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
        # so recitals_for(Anchors().key(...)) matches regardless of the on-disk form
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
    path = annstore.path("eurlex", celex)
    if not path.exists():
        return None
    layer = json.loads(path.read_text()).get("editorialLayer")
    return Editorial(layer) if layer else None


def _artlist(refs):
    """Article refs as links joined the Swedish way: "2", "2 och 6",
    "2, 6 och 28"."""
    return swedish_join(['<a href="#%s">%s</a>' % (escape(a), escape(a))
                          for a in refs])


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
    return NODES.eu_recital_group(_group_anchor(g), rng, g["label"],
                                   Markup(_artlist(refs)) if refs else None)


def _recital_links_sections(recitals):
    """Rail section for an article/sub-article: links to its relevant recitals."""
    links = "".join('<a href="#recital-%d">skäl %d</a>' % (n, n) for n in recitals)
    return [RailSection("skal", "Relevanta skäl", len(recitals),
                        '<div class="skal-links">%s</div>' % links)]


def _recital_context_sections(editorial, n):
    """Rail panel for a recital: the articles it underpins (the back half of
    the article<->recital round-trip), inline rather than behind a fold --
    one or two links carry their own weight. The thematic group is *not*
    repeated here: the group heading inserted in the recital text already
    says it."""
    articles = editorial.recital_articles.get(n)
    if not articles:
        return []
    links = "".join('<a href="#%s">artikel %s</a>' % (escape(a), escape(a))
                    for a in articles)
    return [RailSection("skal",
                        "Relevanta artiklar" if len(articles) > 1
                        else "Relevant artikel",
                        len(articles),
                        '<div class="skal-links">%s</div>' % links,
                        flat=True)]


def _eurlex_marker(t, num):
    """Display form of an EU block's structural number. The artifact stores the
    bare token ("42", "1", "a") -- the surrounding punctuation is presentational:
    a recital is parenthesised ("(42)"), a numbered paragraph gets a full stop
    ("1."), a lettered/roman point the list-parenthesis ("a)", "i)"). A point
    marked with a typographic bullet rather than an enumerator keeps it bare --
    the parenthesis belongs to a letter or a numeral, not to "—". Other numbered
    kinds (ruling, note) keep the bare token."""
    if t == "recital":
        return "(%s)" % num
    if t == "point":
        return "%s)" % num if citable(num) else num
    if t == "paragraph":
        return "%s." % num
    if t == "stycke":
        # a stycke's ordinal names it in a citation but is not printed beside it,
        # in the act or on an SFS page: it is simply the next block of prose
        return None
    return num


def _eurlex_pin(t, num, bid):
    """The rail's "Kontext för …" label for an EU block."""
    if t == "recital" and num:
        return "Skäl %s" % num
    if t == "article":
        return "Artikel %s" % (num or bid or "")
    if bid and "." in bid:            # a dotted sub-article id ("5.2", "6.2.a")
        label = eu_article_label(bid) or "artikel %s" % bid
        # first character only: .capitalize() would lowercase the rest and fold an
        # uppercase list marker ("5.1.A") onto the lowercase point of the same article
        return label[:1].upper() + label[1:]
    return human_fragment(bid)


def _render_eurlex_block(b, site, doc_uri, toc, rail, editorial=None, key=None):
    runs = render_runs(b["text"], site)
    bid = b.get("id")
    t = b["type"]
    num = b.get("num")
    if t == "heading":
        level = b.get("level") or 1
        anchor = toc.add(bid, plain(b["text"]), level)
        return NODES.eu_heading(min(level + 1, 5), anchor, Markup(runs))
    if t == "keyword":
        return NODES.eu_keyword(Markup(runs))
    # editorial layer (.ann): wire this block into the article<->recital graph.
    # A recital gets a back-link panel (its articles + group); an article/
    # sub-article (paragraph/point, keyed like the .ann's "4.5") gets a forward
    # panel of its relevant recitals. Both ride the scroll-driven rail.
    extra = []
    if t == "recital":
        # a numbered recital is a citation target in its own right (`#recital-N`),
        # so it can be cited, commented on and ride the rail with no editorial layer
        if key:
            bid = key
            if editorial:
                extra = _recital_context_sections(editorial, int(num))
    elif key:
        # an article's key is its own id; a sub-article's is the dotted form. Every
        # numbered sub-article (paragraph/point) gets that id, so a reader can link
        # to it directly (`#4.22.a`) -- but it only *rides* the rail when it has
        # context to show (rail.add is a no-op otherwise, and the data-rail
        # marker is then omitted), so ubiquitous ids don't clutter the margin. The
        # editorial layer additionally gives a block a forward panel of its recitals.
        recitals = editorial.recitals_for(key) if editorial else None
        if t != "article":
            bid = bid or key           # synthesise the sub-article citation id
        if recitals:
            extra = _recital_links_sections(recitals)
    # the article is a citation target (id == its number); its inbound (incl.
    # implementing förarbeten) drives the rail, like an SFS paragraph
    pin = _eurlex_pin(t, num, bid)
    rail.add(bid, pin, extra)
    rail_id = bid if bid and bid in rail.data else None
    if t == "article":
        anchor = toc.add(bid, plain(b["text"]), 2)
        return NODES.eu_article(anchor, rail_id, Markup(runs))
    classes = [EURLEX_CLASS.get(t, "")]
    # a marked recital/paragraph/point hangs its marker in the left margin
    if num and t in ("recital", "paragraph", "point"):
        classes.append("hang")
    # a point nested inside another point (a definition's own sub-list) indents
    # a step further, so the depth the anchor records is also the depth the eye reads
    if t == "point" and (b.get("level") or 1) > 1:
        classes.append("sub")
    # a definitions-article point is a citation target (#<article>.<point>) and
    # the begrepp the act defines -- emit its id and emphasise the defined term
    defines = b.get("defines")
    if defines:
        classes.append("definition")
        runs = _emphasize_term(runs, defines, site)
    return NODES.eu_block(bid, " ".join(c for c in classes if c) or None,
                           rail_id,
                           bid if num and bid else None,
                           _eurlex_marker(t, num) if num else None,
                           Markup(runs),
                           first_stycke(t, num, key) or "")


def _emphasize_term(runs_html, term, site):
    """Wrap a definition point's lead term (the plain text before its colon) in
    <dfn>, so the defined word stands out from its definition -- and, when the
    corpus has a begrepp page for the term, link the <dfn> to it (the act's own
    definition of "personuppgifter" -> /begrepp/Personuppgift). The concept name
    is folded onto its canonical page the way case keywords resolve, so an
    inflected/variant term ("personuppgifter") still finds the page."""
    lead = escape(term)
    if not runs_html.startswith(lead):
        return runs_html
    uri = site.resolve(begrepp_uri(term))
    dfn = ('<a href="%s"><dfn>%s</dfn></a>' % (escape(href(uri)), lead)
           if site.has(uri) else "<dfn>%s</dfn>" % lead)
    return dfn + runs_html[len(lead):]


def _eurlex_opinion_href(art, site):
    """On a judgment, the href of its Advocate General's opinion (CELEX CJ ->
    CC) when the corpus holds it -- the opinion is reached from the judgment,
    not the index (E4). None otherwise."""
    celex = art.get("celex") or ""
    if art.get("doctype") != "judgment" or celex[5:7] != "CJ":
        return None
    opinion = catalog.BASE + "ext/celex/" + celex[:5] + "CC" + celex[7:]
    return href(opinion) if site.has(opinion) else None


def render(art, site):
    # the heading is the act's short name (curated or extracted, stamped onto the
    # artifact at parse) plus its citing acronym -- "Cyberresiliensförordningen
    # (CRA)"; the full official title moves into the metadata list. With no short
    # name the heading is the full title, so it is not repeated in the metadata.
    # display_title is the single definition of this, shared with search/listings.
    # eyebrow is the short id ("(EU) 2016/679" / "C-311/18"); h1 is the short name
    # ("dataskyddsförordningen (GDPR)"), or the full title when there is no short
    # name; the full official title moves into dl.meta "Titel" (C2). labels is the
    # single definition of this trio, shared with search/listings.
    lb = labels.document_labels("eurlex", art)
    title = lb.short_title or lb.official_title
    # the case number is the eyebrow, so it needs no dl row of its own
    meta = [
        # the full official title, shown only when the h1 is the short form (else
        # it would just repeat it)
        ("Titel", lb.official_title if lb.official_title != title else None),
        ("CELEX", art.get("celex")),
        ("Datum", art.get("date")),
        ("ECLI", art.get("ecli")),
    ]
    editorial = _load_editorial(art["celex"])
    toc = Toc()
    rail = Rail(site, art["uri"])
    parts = []
    anchors = Anchors()                  # running context for sub-article keys
    preamble_in_toc = False              # the "Preambel" TOC parent is added once
    # the artifact is a nested structure (divisions > articles > paragraphs >
    # points); render reads it in document order -- the heading levels and the
    # TOC already convey the hierarchy, so no nested <section> markup is needed
    for b in eurlex_flatten(art.get("structure", [])):
        t = b["type"]
        key = anchors.key(t, b.get("num"), b.get("id"), b.get("level"))
        if editorial and t == "recital" and (b.get("num") or "").isdigit():
            group = editorial.group_start.get(int(b["num"]))
            if group:
                anchor = _group_anchor(group)
                if not preamble_in_toc:   # a Preambel section listing the groups
                    toc.add(anchor, "Preambel", 1)
                    preamble_in_toc = True
                toc.add(anchor, group.get("label", ""), 2)
                parts.append(_recital_group_heading(group))
        parts.append(_render_eurlex_block(b, site, art["uri"], toc, rail,
                                          editorial, key))
    rail.add_document()        # external links + commentary, the rail's default panel
    kind = EURLEX_KIND.get(art.get("doctype"), "EU-rättsakt")
    return ENV.get_template("eurlex.html").render(page_context(
        title, kind, doc_meta(meta, art.get("source_url")),
        toc=render_toc(toc), eyebrow=lb.short_id, island=rail.island(),
        opinion_href=_eurlex_opinion_href(art, site),
        structure=Markup("".join(parts))))
