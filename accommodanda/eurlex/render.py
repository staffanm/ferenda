"""EU-rättsaktssidan: articles, recitals and the editorial annotation
layer.

Registered as this source's page renderer in `build.SOURCE_RENDERERS`;
`render` is the `(art, site) -> str` the generate driver calls.
"""
import json
import re
from datetime import date
from html import escape

from markupsafe import Markup

from ..lib import annstore, catalog, labels, tpl
from ..lib.eu_structure import Anchors, citable, first_stycke
from ..lib.eu_structure import flatten as eurlex_flatten
from ..lib.markdown import begrepp_uri
from ..lib.page import (
    BANNERS,
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
from ..lib.text import runs_text

ENV = tpl.environment("accommodanda.eurlex")


EURLEX_KIND = {"regulation": "EU-förordning", "directive": "EU-direktiv",
               "decision": "EU-beslut", "judgment": "EU-domstolen",
               "treaty": "Fördrag", "act": "EU-rättsakt"}

# the deepest point nesting the stylesheet grades: 63% of points sit at the first
# level, 23% at the second, 10% at the third and 4% deeper -- past `sub4` the
# indent saturates rather than walking the text off the page. Raising this needs a
# matching `p.point.subN` rule in lib/assets/style.css, which is what the classes
# below are for; test_site locks the pair together.
SUB_INDENT_MAX = 4

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


# The division designations the Swedish Formex sources print before a division
# title, always in capitals ("KAPITEL I ALLMÄNNA BESTÄMMELSER"). The set is the
# one the corpus shows -- BILAGA 354, KAPITEL 91, AVDELNING 26, DEL 23, AVSNITT
# 14 over 600 sampled acts. Only the token right after one of these words is a
# division numeral, which is why the label has to come off before the title is
# re-cased: "OSKÄLIGA AVTALSVILLKOR I SAMBAND MED" also holds a standalone "I",
# and it is the preposition.
_DIVISION_WORDS = ("BILAGA", "KAPITEL", "AVDELNING", "AVSNITT", "DEL")
_DIVISION_LABEL = re.compile(
    r"^(%s)\s+((?:[IVXLC]+|\d+[a-zA-Z]?|[A-Z]))(?=\s|$)" % "|".join(_DIVISION_WORDS))
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
# a dotted/slashed identifier read as one token: "ADR.OR.B", "ATM/ANS.AR.B",
# "96/9/EG", "META-SPC". Split into words it loses its capitals one component at
# a time -- "ADR.OR.B" came out "ADR.OR.b", because a one-letter component
# cannot be told from an ordinary capital.
_CODE = re.compile(r"[^\W_]+(?:[./\-][^\W_]+)+", re.UNICODE)
# the scan order matters: a code before the word it would otherwise be cut into
_TOKEN = re.compile(r"%s|[^\W\d_]+|." % _CODE.pattern, re.UNICODE | re.DOTALL)
# where the act's own prose lives -- the evidence _case_map reads. A heading is
# excluded (it may be the shouting one we are re-casing) and so is a table: a
# cell's text is joined with pipes, which made every cell look like a sentence
# start and put a capital "Artikel" in the map.
_PROSE = ("recital", "paragraph", "point", "stycke", "preamble", "citation")


def _shouts(text):
    """True where every letter in `text` is a capital -- how the sources set a
    division title, and never how they set running text."""
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and all(c.isupper() for c in letters)


def _case_map(blocks):
    """How this act writes each word in its own prose:
    `(capitalised, lowercase, shouted)` -- the capitalised spelling and its count
    per lowercased word, the lowercase counts, and the tokens the act sets in
    capitals inside ordinary text. An all-caps division title is re-set in sentence
    case for display, and this is what keeps the capitals the act itself uses --
    "direktiv 96/9/EG", not "direktiv 96/9/eg" -- with no list of proper nouns or
    acronyms to maintain anywhere.

    Only mid-sentence occurrences count: a word after a full stop is capitalised
    by position and says nothing about how the act writes it."""
    forms, low, shouted = {}, {}, set()
    for b in blocks:
        text = runs_text(b.get("text") or [])
        if _shouts(text):
            continue
        # the identifiers a regulation cites itself by, set in capitals inside
        # ordinary text ("ADR.OR.B", "ATM/ANS.AR.B", "META-SPC"). Read from every
        # block, tables included: that is where the codes live. Only dotted or
        # hyphenated codes count -- a bare capitalised token is as often an
        # ordinary word a line happens to shout ("KRAV", "OCH"), and the
        # capitalisation counts below already keep a real acronym like "EG".
        for code in _CODE.findall(text):
            if not code.isupper():
                continue
            # the act writes "ADR.OR.B.015" in prose and "ADR.OR.B" in the
            # heading, so every prefix of a known code is known too
            parts = re.split(r"([./\-])", code)
            shouted.update("".join(parts[:i]) for i in range(1, len(parts) + 1, 2))
        if b.get("type") not in _PROSE:
            continue
        for m in _WORD.finditer(text):
            before = text[:m.start()].rstrip()
            if not before or before[-1] in ".!?:;|":
                continue
            word = m.group(0)
            if word[:1].isupper():
                seen = forms.setdefault(word.lower(), {})
                seen[word] = seen.get(word, 0) + 1
            else:
                low[word.lower()] = low.get(word.lower(), 0) + 1
    # the spelling the act uses most often -- "Europeiska", never the heading's
    # own "EUROPEISKA", which is the shout we are undoing
    cap = {lower: max(seen.items(), key=lambda kv: kv[1])
           for lower, seen in forms.items()}
    return cap, low, shouted


def _sentence_case(text, casemap, capitalise=True):
    """`text` re-set in sentence case, and whether the next word still opens the
    sentence. A word keeps its capital only where the act capitalises it in prose
    *consistently* -- twice or more, and more than twice as often as it writes
    the same word lowercase -- and it then takes the act's own spelling, not the
    heading's. One stray capital is not evidence: it capitalised "Direktanspråk"
    in the middle of a chapter title off a single cross-reference. A token the
    act sets in capitals in ordinary text is left alone whatever the counts say:
    lowercasing "ADR.OR.B" to "ADR.or.b" rewrites the identifier the regulation
    cites itself by."""
    cap, low, shouted = casemap
    out = []
    for m in _TOKEN.finditer(text):
        token = m.group(0)
        if token in shouted:           # an identifier the act prints in capitals
            out.append(token)
            capitalise = False
            continue
        if _CODE.fullmatch(token):
            # a code shape the act does not print as one ("EFHU-GARANTIN",
            # "WTO-TULLKVOTER"): an acronym glued to an ordinary word. Each
            # component decides for itself, and a component the act never writes
            # in lower case anywhere is the acronym half.
            parts = re.split(r"([./\-])", token)
            for i, part in enumerate(parts):
                if i % 2:              # the separator
                    out.append(part)
                elif part.isupper() and part.lower() not in low and \
                        part.lower() not in cap:
                    out.append(part)
                    capitalise = False
                else:
                    word, capitalise = _sentence_case(part, casemap, capitalise)
                    out.append(word)
            continue
        if not _WORD.fullmatch(token):
            out.append(token)
            continue
        written, seen = cap.get(token.lower(), ("", 0))
        word = (written if seen >= 2 and seen > 2 * low.get(token.lower(), 0)
                else token.lower())
        if capitalise:
            word = word[:1].upper() + word[1:]
            capitalise = False
        out.append(word)
    return "".join(out), capitalise


def _cased_runs(runs, casemap):
    """`_sentence_case` across a run list, so the links inside a heading survive
    it. The opens-the-sentence state carries from run to run: without it every
    run capitalised its own first word, and a linked term mid-title came out
    "Datadelning mellan Företag"."""
    out, capitalise = [], True
    for run in runs:
        text = run if isinstance(run, str) else run.get("text", "")
        cased, capitalise = _sentence_case(text, casemap, capitalise)
        out.append(cased if isinstance(run, str) else dict(run, text=cased))
    return out


def _division_label(b, casemap):
    """A division heading's designation and its title runs, both ready to print.

    The artifact keeps them apart (Formex sets `TI` and `STI` as separate
    elements, and `eurlex/parse.py` no longer flattens the pair). The sources set
    both in capitals; the title is re-set in sentence case, the designation
    capitalised.

    A heading with no designation is left exactly as published, capitals and all.
    Those are the free-form ones -- "FÖRLAGA TILL INTYG OM ÖVERENSSTÄMMELSE
    GODKÄNT AV AMERIKAS FÖRENTA STATER", "FÖRTECKNING ÖVER FÖRETAG SOM AVSES I
    ARTIKEL 2.1 A" -- and they carry names the act never writes in prose, so
    lowercasing them would put words on the page the act never wrote. A
    designation, by contrast, always opens a common-noun title. 508 of 533
    all-caps headings in a 600-act sample carry one."""
    runs = b.get("text") or []
    label = b.get("label")
    if not label:
        return None, runs
    if _shouts(label):
        # only the designation word is a word: "KAPITEL IV" -> "Kapitel IV", and
        # the numeral after it stays exactly as the source set it
        word, _, rest = label.partition(" ")
        label = " ".join(x for x in (word.capitalize(), rest) if x)
    return label, _cased_runs(runs, casemap) if _shouts(runs_text(runs)) else runs


def _article_parts(b):
    """An article heading's `(word, number, title_runs)`, or `(None, None, None)`
    where it carries no designation of its own ("Enda artikel").

    The designation is the source's own TI.ART, kept apart from the STI.ART title
    by the parser -- most articles have no title at all."""
    label, num = b.get("label"), b.get("num")
    if not label or not num:
        return None, None, None
    m = re.match(r"(\S+)\s+%s(?=\W|$)" % re.escape(num), label)
    if not m:
        return None, None, None
    word = m.group(1)
    if word.isupper():                 # a legacy act shouting "ARTICLE 1"
        word = word.capitalize()
    return word, num, b.get("text") or []


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


def _render_eurlex_block(b, site, doc_uri, toc, rail, casemap,
                         editorial=None, key=None):
    bid = b.get("id")
    t = b["type"]
    num = b.get("num")
    # the heading and article paths render their own split-out runs, so the
    # whole-block render is left to the paths that use it
    if t == "heading":
        level = b.get("level") or 1
        label, title = _division_label(b, casemap)
        anchor = toc.add(bid, " ".join(x for x in (label, plain(title)) if x), level)
        return NODES.eu_heading(min(level + 1, 5), anchor, label,
                                Markup(render_runs(title, site)))
    if t == "keyword":
        return NODES.eu_keyword(Markup(render_runs(b["text"], site)))
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
        word, number, title = _article_parts(b)
        anchor = toc.add(bid, " ".join(x for x in (b.get("label"),
                                                   plain(b["text"])) if x), 2)
        # an article with no designation prints its label as the plain heading
        return NODES.eu_article(anchor, rail_id, word, number,
                                Markup(render_runs(title, site)) if number
                                else escape(b.get("label") or ""))
    runs = render_runs(b["text"], site)
    classes = [EURLEX_CLASS.get(t, "")]
    # a marked recital/paragraph/point hangs its marker in the left margin
    if num and t in ("recital", "paragraph", "point"):
        classes.append("hang")
    # a point nested inside another point (a definition's own sub-list) steps its
    # indent in, graded by the depth its anchor records -- and saturating, since
    # points nest to seven and the text would otherwise run off a narrow screen
    if t == "point" and (b.get("depth") or 1) > 1:
        classes.append("sub%d" % min(b["depth"], SUB_INDENT_MAX))
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
    # read once for the whole act: how it writes its own words, which is what
    # re-sets an all-caps division heading in sentence case (_case_map)
    # the artifact is a nested structure (divisions > articles > paragraphs >
    # points); render reads it in document order -- the heading levels and the
    # TOC already convey the hierarchy, so no nested <section> markup is needed
    blocks = list(eurlex_flatten(art.get("structure", [])))
    casemap = _case_map(blocks)
    for b in blocks:
        t = b["type"]
        key = anchors.key(t, b.get("num"), b.get("id"), b.get("depth"))
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
                                          casemap, editorial, key))
    rail.add_document()        # external links + commentary, the rail's default panel
    kind = EURLEX_KIND.get(art.get("doctype"), "EU-rättsakt")
    # an act whose repeal has taken effect is out of every listing, so a reader
    # only reaches it by following a citation -- the page has to say so itself.
    # A repeal dated ahead is not yet a repeal and gets no banner, matching the
    # `expired <= today` test the listings apply (catalog.expired_uris).
    expired = art.get("expired")
    if expired and expired > date.today().isoformat():
        expired = None
    return ENV.get_template("eurlex.html").render(page_context(
        title, kind, doc_meta(meta, art.get("source_url")),
        toc=render_toc(toc, lb.short_id), eyebrow=lb.short_id, island=rail.island(),
        opinion_href=_eurlex_opinion_href(art, site),
        banner=Markup(BANNERS.eurlex_expired_banner(expired) if expired else ""),
        body_class=" expired expired-eu" if expired else "",
        structure=Markup("".join(parts))))
