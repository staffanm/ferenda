"""Extract an EU act's defined terms and interlink their in-act uses.

Modern EU acts gather their definitions in a dedicated article ("Article N --
Definitions"): an intro paragraph ("For the purposes of this Directive, the
following definitions apply:") followed by a numbered list of points, each
shaped ``term: definition`` (Swedish and English alike). We read each such point
as a definition of the lead term (the text before the first colon) and anchor it
as ``<article>.<point>`` -- the very fragment the citation engine mints for
"artikel 6.15 i ..." (lib.lagrum.celex_uri) -- so a pinpoint citation and the
definition it points at agree by construction.

A definition is valid only within its act (cross-act reuse goes through explicit
references), so occurrences are interlinked act-locally: every later use of a
defined term becomes a link to that act's own definition point, whose text the
hover preview (popover.js) shows. Matching is suffix-tolerant -- Swedish
inflects, so "sårbarhet" defined matches "sårbarheter" used -- and longest-term
first, so "storskalig cybersäkerhetsincident" wins over the "cybersäkerhet"
nested inside it. The point defining a term never links that term to itself.

Scope: only the dedicated definitions-article pattern (which covers NIS2 and the
bulk of modern acts). Definitions stated inline in running prose ("'X' means
...") are not yet detected.
"""

import re

from ..lib.lagrum import Ref

# the relation a use-of-a-defined-term run carries; internal to the act, so the
# catalog's self-citation filter keeps these out of inbound panels
TERM_PRED = "dcterms:references"

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
SUFFIXES = {
    "swe": ("ernas", "arnas", "ornas", "erna", "arna", "orna", "ens", "ets",
            "er", "ar", "or", "en", "et", "na", "ns", "s"),
    "eng": ("es", "s"),
}

_COLON = re.compile(r"\s*:\s*")
_TERM_MAX = 80   # a definition's lead term is short; a long head means the colon
                 # sits mid-prose, not at a definition boundary


def _term_of(point_text):
    """The defined term of a ``term: definition`` point -- the lead phrase before
    the first colon -- or None when the point is not so shaped."""
    if not _COLON.search(point_text):
        return None
    head = _COLON.split(point_text, 1)[0].strip()
    if 2 <= len(head) <= _TERM_MAX and any(c.isalpha() for c in head):
        return head
    return None


def _is_definitions_article(article, intro, lang):
    spec = DEFN_VOCAB.get(lang, DEFN_VOCAB["eng"])
    if any(t in (article.text or "").lower() for t in spec["titles"]):
        return True
    return any(p in (intro or "").lower() for p in spec["intro"])


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
                i += 1
                while i < n and body[i].kind not in ("article", "heading"):
                    point = body[i]
                    # a definitions entry is the article's own enumeration: a
                    # numbered `paragraph` when it sits directly under the article
                    # (GDPR art. 4), a `point` when the list is one paragraph deep
                    # (NIS2 art. 6). Its lettered sub-points are never definitions.
                    term = (_term_of(point.text)
                            if point.kind in ("paragraph", "point") else None)
                    if term and point.num:
                        point.anchor = "%s.%s" % (art_num, point.num)
                        point.defines = term
                        terms.setdefault(term, point.anchor)
                    i += 1
                continue
        i += 1
    return terms


def _term_regex(term, suffixes):
    """A pattern matching `term` (whitespace-flexible) with an optional
    inflectional ending on its final word."""
    body = r"\s+".join(re.escape(tok) for tok in term.split())
    return r"%s(?:%s)?" % (body, "|".join(suffixes))


def build_matcher(terms, lang):
    """Compile a single combined matcher for all defined `terms` and a
    group-name -> anchor index. Terms are tried longest-first so a phrase wins
    over a term nested inside it. Returns (None, {}) when there are no terms."""
    if not terms:
        return None, {}
    suffixes = SUFFIXES.get(lang, SUFFIXES["eng"])
    parts, index = [], {}
    for i, term in enumerate(sorted(terms, key=len, reverse=True)):
        group = "t%d" % i
        parts.append("(?P<%s>%s)" % (group, _term_regex(term, suffixes)))
        index[group] = terms[term]
    return re.compile(r"\b(?:%s)\b" % "|".join(parts), re.IGNORECASE), index


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
