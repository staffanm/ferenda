"""Extract an EU act's defined terms and interlink their in-act uses.

Modern EU acts gather their definitions in a dedicated article ("Article N --
Definitions"): an intro paragraph ("For the purposes of this Directive, the
following definitions apply:") followed by a numbered list of points, each
shaped ``term: definition`` (Swedish and English alike -- either written out that
way in the running text, or in Formex's explicit ``DLIST`` term/definition markup,
which the parser joins into the same shape). We read each such point as a
definition of the lead term (the text before the first colon) and anchor it with
the shared sub-article grammar (`lib.eu_structure.Anchors`) -- the very fragment
the citation engine mints for "artikel 6.15 i ..." (lib.lagrum.celex_uri) -- so a
pinpoint citation and the definition it points at agree by construction.

A definition is valid only within its act (cross-act reuse goes through explicit
references), so occurrences are interlinked act-locally: every later use of a
defined term becomes a link to that act's own definition point, whose text the
hover preview (popover.js) shows. Matching is suffix-tolerant -- Swedish
inflects, so "sårbarhet" defined matches "sårbarheter" used -- and longest-term
first, so "storskalig cybersäkerhetsincident" wins over the "cybersäkerhet"
nested inside it. The point defining a term never links that term to itself.

Scope: the dedicated definitions-article pattern (which covers NIS2 and the bulk
of modern acts), plus, for Swedish, a term an act names only in passing -- a
parenthesis closing the clause that describes it ("... (betydande incident)"),
read by `inline_definitions`.
"""

import re

from ..lib.begrepp import TERM_PRED
from ..lib.eu_structure import Anchors
from ..lib.lagrum import Ref

# The matcher over an act's own defined terms -- `build_matcher`, its suffix
# table and `TERM_PRED`, the relation a use-of-a-defined-term run carries --
# moved to lib.begrepp when SFS became the second source to interlink its own
# definitions. This module keeps the extraction, which is EU-act shaped.

# per-language cues for the dedicated definitions article: words that appear in
# its title, and phrases that frame its intro paragraph. Unknown language falls
# back to English.
DEFN_VOCAB = {
    "eng": {"titles": ("definition",),
            "intro": ("the following definitions apply",
                      "the following definitions shall apply",
                      "the following definitions are used")},
    "swe": {"titles": ("definition",),
            "intro": ("följande definitioner", "avses med",
                      "används följande")},
}

# inflectional endings tolerated on the final word of a term occurrence, so a
# defined noun matches its inflected uses (longest first when building the
# pattern, so the fullest surface form is captured)

_COLON = re.compile(r"\s*:\s*")
_TERM_MAX = 80   # a definition's lead term is short; a long head means the colon
                 # sits mid-prose, not at a definition boundary

# An amending act writes its instructions in exactly the shape a definition has:
# "Artikel 6 ska ersättas med följande: <the whole replacement article>". When
# such an act's article carries a definitions-looking heading -- 2014/48/EU
# article 1 is headed "Definitioner av vissa termer", because that is the
# heading of the article it *inserts* -- every instruction under it reads as a
# definition, and 2026/1183 art. 1.7 became a 47 kB "definition" of the concept
# "Artiklarna 67-112 ska ersättas med följande".
#
# A definiendum is a noun phrase; an instruction is a clause, and its verb is a
# ska-passive. The two separate on where the "ska" sits: a term carries one only
# inside a relative clause ("sammanlagt belopp som ska betalas av konsumenten",
# "kemikalie för vilken exportanmälan ska ske"), an instruction leads with it.
# Measured over the corpus's 27 289 defined terms: 268 contain "ska", the test
# rejects 250 and every one of them is an instruction; the 18 it keeps are real
# terms. No amending instruction reaches it without a "ska" -- the four that
# carry another amending verb ("betecknas") are genuine terms.
_SKA = re.compile(r"^(.*?)\bska\b", re.I | re.S)
_RELATIVE = re.compile(r"\b(som|vilken|vilket|vilka|där|när)\b", re.I)


def _is_intro(text, lang):
    """Whether `text` is the line *announcing* a definitions list ("I detta
    direktiv gäller följande definitioner:") rather than a definition itself."""
    spec = DEFN_VOCAB.get(lang, DEFN_VOCAB["eng"])
    return any(p in (text or "").lower() for p in spec["intro"])


def _is_amendment(head):
    """Whether `head` instructs a change to a text rather than naming a term --
    "Artikel 6 ska ersättas med följande", "Följande punkt ska läggas till"
    (see _SKA)."""
    m = _SKA.search(head)
    return bool(m) and not _RELATIVE.search(m.group(1))


# the quotation marks the OJ sets a defined term in ("”domstol”: en domstol
# …"). The term is the phrase, not the marks: kept, every later use of
# "domstol" stopped matching its own definition and 32006R1896 lost 167 of its
# 266 term links.
_QUOTES = "\u201c\u201d\u2018\u2019\u201e\u201a\u00ab\u00bb\"'"


def _bare_term(text):
    """A defined term with the quotation marks the OJ prints around it removed."""
    return text.strip(_QUOTES).strip()


def _term_of(point_text, lang):
    """The defined term of a ``term: definition`` point -- the lead phrase before
    the first colon, with the quotation marks the OJ sets it in removed -- or
    None when the point is not so shaped.

    The announcing line is tested for and rejected on the *head* alone: it ends in
    a colon like a definition does and is itself a numbered paragraph in some acts
    (2015/1535 art. 1.1), so it would otherwise be read as defining its whole self.
    Testing the head rather than the block keeps the same phrase inside a genuine
    definition ("tjänst: … I denna definition avses med …") harmless. An amending
    instruction is rejected on the head for the same reason."""
    if not _COLON.search(point_text):
        return None
    head = _bare_term(_COLON.split(point_text, 1)[0])
    if (2 <= len(head) <= _TERM_MAX and any(c.isalpha() for c in head)
            and not _is_intro(head, lang) and not _is_amendment(head)):
        return head
    return None


def _is_definitions_article(article, intro, lang):
    spec = DEFN_VOCAB.get(lang, DEFN_VOCAB["eng"])
    if any(t in (article.text or "").lower() for t in spec["titles"]):
        return True
    return _is_intro(intro, lang)


# --------------------------------------------------------------------------
# definitions named in passing: "... enligt punkt 3 (betydande incident)."
#
# The definitions article is not always the whole story. NIS2 defines
# "incident" in article 6(6) but never lists "betydande incident", the term its
# whole reporting regime turns on -- article 23(1) names it in a parenthesis at
# the end of the clause that describes it, and then uses it 39 times. The form
# is ordinary in Swedish drafting; for EU acts we had assumed the definitions
# article covers every important term, and for this family of acts it does not.
#
# The parenthesis is a weak signal on its own: measured over 770 Swedish acts,
# reading every naming parenthesis yields 93 candidates of which ~19% are
# definitions. The rest are the annotation habit of commodity and veterinary
# regulations -- species names ("Thunnus alalunga"), residue qualifiers ("gula
# och röda", "med balja"), tariff codes, party aliases ("nedan kallat
# Uralchem"). Three conditions carry the precision to ~75% and keep NIS2's
# term:
#
#   * enacting terms only -- the annotation habit lives in recitals;
#   * the term is a lower-case noun phrase carrying no digit -- a species,
#     product or code is not written that way;
#   * the act uses the term at least twice more -- a term worth naming is a
#     term the act goes on to use (NIS2: 39 further uses);
#   * and what the parenthesis names must be a *description*, not a list item.
#     This is what separates the two habits: a definition closes a clause that
#     describes the concept ("Statistik om statliga budgetanslag eller utgifter
#     för forskning och utveckling (statliga FoU-anslag)", and NIS2's whole
#     23(1) sentence), while the annotation sits right after the one word or
#     name it qualifies ("örtteer (blommor)", "Bemisia tabaci Genn.
#     (icke-europeiska populationer)", "OECD:s manual om patentstatistik
#     (statistik om patent)"). Measured on the four survivors of the first
#     three conditions: the rule keeps the one definition and drops all three
#     annotations, and keeps both of NIS2's terms.
#
# The survivors are marked `defines_inline`, so an inferred definition can be
# told from a listed one wherever it is read.
# --------------------------------------------------------------------------

# the naming parenthesis itself: short, and closing its clause
_PAREN = re.compile(r"\(([^()]{3,60})\)(?=[.,;:]|\s*$)")
# what a naming parenthesis never holds: an act number or OJ coordinate, a
# cross-reference, an alias introduction, an enumerator, a clause of its own
_NOT_A_TERM = re.compile(
    r"^(?:EU|EG|EEG|EC|EEC|Euratom|EUT|EGT|OJ|nr|No)\b"
    r"|^(?:se|jfr|nedan\s+kalla\w*|kalla\w*|artikel\w*|punkt\w*|bilag\w+|"
    r"kapitel|avsnitt|dvs|t\.ex|inbegripet|inklusive|utom|eller|och|i|av|om)\b"
    r"|\b(?:ska|skall|som|när|där|vilka|är|har|kan|bör|enligt|genom)\b",
    re.IGNORECASE)
# ... and the shape one does have: letters, spaces and hyphens, 1-4 words,
# opening lower case (a legal concept is not a proper name)
_TERMISH = re.compile(r"^[a-zåäöéèü][\w\s\-–]*$", re.UNICODE)
# how often the act must use the term beyond the naming itself
_INLINE_MIN_USES = 2
# how many words of description must precede the naming parenthesis, counted
# from the last clause boundary. An annotation follows its one item; a
# definition follows the words that describe it.
_INLINE_MIN_DESCRIPTION = 6
_CLAUSE_BOUNDARY = re.compile(r"[,;:.]")
# the block kinds an in-passing definition can sit in: the enacting terms'
# own prose. A recital argues, a `citat` is another act's text.
_ENACTING_KINDS = ("paragraph", "stycke", "point")


# A coordination is a list, not a name: "tomater (gula och röda)", "(säte och
# ratt)", "(x och y)" -- every coordinated candidate in the 770-act measurement
# was a false positive. The exception is the Swedish compound coordination,
# where the first half keeps its hyphen ("hälso- och sjukvård") and the phrase
# is one concept.
_COORDINATION = re.compile(r"(?:^|[^-])\s+(?:och|eller)\s+", re.IGNORECASE)


def _naming_terms(text):
    """The terms a block names in passing, in order -- the parentheses that
    pass every shape test above. Text-level only; the caller applies the
    use-count and the already-defined test."""
    out = []
    for m in _PAREN.finditer(text):
        inner = _bare_term(m.group(1))
        if (_NOT_A_TERM.search(inner) or not _TERMISH.match(inner)
                or _COORDINATION.search(inner)):
            continue
        if not 1 <= len(inner.split()) <= 4:
            continue
        # the clause the parenthesis closes, back to the last boundary
        lead = text[:m.start()]
        boundary = _CLAUSE_BOUNDARY.search(lead[::-1])
        described = lead[len(lead) - boundary.start():] if boundary else lead
        if len(described.split()) < _INLINE_MIN_DESCRIPTION:
            continue
        out.append(inner)
    return out


def inline_definitions(body, lang, known):
    """Terms the act names in passing -> ``{term: anchor}``, mutating the
    naming block to carry the `defines` term, its citation `anchor` and the
    `defines_inline` mark. `known` is what the definitions article already
    defined; a term listed there is not re-read here.

    The anchors come from the same running context the renderer mints
    (`lib.eu_structure.Anchors`), so the definition and a pinpoint citation to
    it agree exactly as they do for a definitions-article point.

    Swedish only: `_NOT_A_TERM`'s stopwords, `_TERMISH`'s letter class and
    `_COORDINATION`'s "och"/"eller" test are tuned against the 770-act Swedish
    measurement above, and untested against English prose -- a manifestation in
    another language reads no inline definitions rather than guessing at
    ~75% precision that was never measured for it."""
    if lang != "swe":
        return {}
    whole = " ".join(b.text or "" for b in body).lower()
    known_lower = {t.lower() for t in known}
    terms, anchors = {}, Anchors()
    for block in body:
        key = anchors.key(block.kind, block.num, block.anchor, block.depth)
        if block.kind not in _ENACTING_KINDS or not key or block.defines:
            continue
        for term in _naming_terms(block.text or ""):
            low = term.lower()
            if low in known_lower or low in terms:
                continue
            # the act must go on to use it: one occurrence beyond the naming
            if whole.count(low) - 1 < _INLINE_MIN_USES:
                continue
            block.anchor = key
            block.defines = term
            block.defines_inline = True
            terms[term] = key
            break        # one naming per block; the first is the clause's own
    return terms


def extract_definitions(body, lang):
    """Find the act's definitions article(s) and return a ``{term: anchor}`` map,
    mutating each defining point in `body` to carry its citation `anchor`
    (``<article>.<point>``) and the `defines` term. Empty when the act has no
    recognised definitions article."""
    terms = {}
    i, n = 0, len(body)
    while i < n:
        block = body[i]
        if block.kind == "article":
            art_num = block.anchor or block.num
            intro = body[i + 1].text if (i + 1 < n
                                         and body[i + 1].kind == "paragraph") else ""
            if art_num and _is_definitions_article(block, intro, lang):
                # the anchor a definition point gets must be the one the renderer
                # mints for it, so track the same running context: an entry may
                # sit directly under the article (GDPR art. 4), one numbered
                # paragraph deep (2015/1535 art. 1.1 -> "1.1.b") or inside another
                # definition (its roman sub-list -> "1.1.b.i")
                anchors = Anchors()
                anchors.key("article", art_num, art_num)
                i += 1
                while i < n and body[i].kind not in ("article", "heading"):
                    point = body[i]
                    key = anchors.key(point.kind, point.num, point.anchor,
                                      point.depth)
                    term = (_term_of(point.text, lang)
                            if point.kind in ("paragraph", "point") else None)
                    if term and key:
                        point.anchor = key
                        point.defines = term
                        terms.setdefault(term, key)
                    i += 1
                continue
        i += 1
    return terms


def term_refs(text, matcher, index, doc_uri, self_anchor):
    """Occurrences of any defined term in `text` as term-link Refs into the act's
    own definition points. The point defining a term skips its own term (by
    anchor), but still links the other terms it mentions."""
    if not matcher:
        return []
    refs = []
    for m in matcher.finditer(text):
        anchor = index[m.lastgroup]
        if anchor == self_anchor:
            continue
        refs.append(Ref(m.start(), m.end(), m.group(), TERM_PRED,
                        "%s#%s" % (doc_uri, anchor), kind="term"))
    return refs
