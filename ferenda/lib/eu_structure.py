"""The canonical walk over an EU act's artifact tree, and the one sub-article id
grammar every layer shares.

This is artifact-format machinery, not source code: it reads the published EU-act
artifact (the JSON source of truth) the way the layout module reads the URI/path
grammar. Three consumers must agree on the *same* anchors -- the eurlex parser
that mints them, the renderer that turns a node into an addressable heading, and
the wiki guidance layer that links commentary onto a point -- so the grammar lives
here, once, keyed on artifact node metadata (`rule:second-use-goes-to-lib`).

`nest` (the parse-time tree *builder*) stays in `eurlex/structure`: it is genuine
source parsing, run only by eurlex. `flatten` is its document-order inverse; the
eurlex parser imports these block-kind constants back from here so the producer
and the consumers share one vocabulary.
"""

import re

# block kinds that carry a citable anchor (the artifact node `type` values)
ARTICLE = "article"
PARAGRAPH = "paragraph"
POINT = "point"
STYCKE = "stycke"

# the segment marking a stycke (sub-paragraph) in an anchor: "9.2.S2" is the
# second stycke of article 9.2, the way SFS writes "P2S2". Letter-tagged so it
# cannot be read as a numbered point -- "4.1.1" is already point 1 of art. 4.1
STYCKE_SEG = "S"

# block kinds that close the article context the same way `eurlex.structure.nest`
# closes it: a division/annex heading, and the trailing matter after the enacting
# terms
_CLOSERS = ("heading", "ruling", "signature")


def flatten(structure):
    """The inverse of `eurlex.structure.nest`: the document-order flat block list (a
    container becomes its own block, sans `children`, followed by its flattened
    children)."""
    out = []
    for node in structure:
        if "children" in node:
            out.append({k: v for k, v in node.items() if k != "children"})
            out.extend(flatten(node["children"]))
        else:
            out.append(node)
    return out


def citable(num):
    """Whether a structural marker can carry a citation anchor. A typographic
    bullet ("—") is a list glyph, not something a pinpoint can name, so it marks
    its item on the page but anchors nothing ("1.2.—" was never a citable id)."""
    return bool(num) and any(c.isalnum() for c in num)


def subarticle_key(t, num, cur_article, cur_parag, cur_point=None):
    """The citation anchor for a sub-article block -- the **dotted** `4.5` / `6.2.a`
    grammar, from the block's running article/paragraph/point context. A paragraph
    is `article.paragraph`, a point `article.paragraph.point` (or `article.point`
    for a point sitting directly under the article, like a definitions-article
    entry), and a point nested inside another point appends to its parent's key
    (`1.1.f.ii`). None when the block cannot anchor (no article context or no
    number). The one canonical sub-article id grammar -- shared by the renderer
    (the node id it mints), the wiki commentary headings (`## Artikel 5.2 a` ->
    "5.2.a") and the guidance linker's `.ann` keys -- so every layer lands on the
    same node."""
    if not (cur_article and citable(num)):
        return None
    if t == PARAGRAPH:
        return "%s.%s" % (cur_article, num)
    if t == POINT:
        return ".".join(str(p) for p in (cur_article, cur_parag, cur_point, num)
                        if p)
    return None


def stycke_key(container, num):
    """The anchor for a stycke (sub-paragraph): its container's key plus the
    letter-tagged ordinal -- `9.2` -> `9.2.S2`, and an article's own stycken
    `8` -> `8.S2`. A stycke never enters a *point's* key: the act calls the point
    "artikel 9.2 a" whichever stycke it sits in, so the point chain stays
    article > paragraph > point."""
    return "%s.%s%s" % (container, STYCKE_SEG, num) if container and num else None


class Anchors:
    """The running structural context that turns an act's blocks, read in document
    order, into their citation anchors.

    Article > paragraph > point is not a fixed depth: a point nests inside another
    point whenever a definition carries its own sub-list, and two sibling sub-lists
    under one paragraph both start at "i)" -- so a nested point has to carry its
    parent point in the anchor ("1.1.f.ii") or the two collide on one id. The open
    points are kept as a stack, keyed on the `depth` the parser stamps on a nested
    point (absent or 1 = the first point level).

    One tracker for every consumer of the artifact tree, so the anchors the parser
    stamps, the ones the renderer emits and the ones the guidance layer links onto
    are the same by construction (rule:second-use-goes-to-lib)."""

    def __init__(self):
        self.article = None
        self.parag = None
        self.stycke = None        # open stycke, when past the paragraph's first
        self.points = []          # open point markers, outermost first

    def container(self):
        """The key a stycke of the open paragraph hangs off: the paragraph's own
        key (`9.2`), or the article's when its stycken sit directly under it."""
        return subarticle_key(PARAGRAPH, self.parag, self.article,
                              self.parag) or self.article

    def key(self, t, num, bid=None, depth=None):
        """The anchor for the next block in document order, or None when the block
        cannot anchor. `depth` is a point's nesting inside another point. Updates
        the context as a side effect, so this must be called for *every* block, not
        only the anchorable ones."""
        if t == ARTICLE:
            self.article, self.parag, self.stycke = bid or num, None, None
            self.points = []
            return self.article
        if t == PARAGRAPH:
            self.parag, self.stycke, self.points = num, None, []
            # a numbered paragraph also answers to `.S1` (`first_stycke`): its own
            # text is its first stycke. An *unnumbered* paragraph anchors nothing
            # -- it is prose outside the article outline (a signature block, annex
            # text); an article's real stycken arrive as `stycke` blocks
            return subarticle_key(t, num, self.article, self.parag)
        if t == STYCKE:
            # a stycke closes any open point and becomes the one the points that
            # follow hang off; the paragraph context stays, so the stycke's own
            # key is still the paragraph's plus its ordinal
            key = stycke_key(self.container(), num)
            self.stycke, self.points = num, []
            return key
        if t == POINT:
            slot = (depth or 1) - 1
            del self.points[slot:]                  # close this point's siblings
            # a level may be skipped (a lettered sub-list hanging off a numbered
            # *paragraph*, GDPR art. 4.22 a), so pad rather than shift: the stack
            # slot has to stay the block's own depth or the next sibling reads the
            # previous one as its parent
            self.points += [None] * (slot - len(self.points))
            # the stycke is deliberately *not* in a point's key: the act names a
            # point by its paragraph whichever stycke holds it ("artikel 11 a"),
            # and that is the only form anyone cites. Threading the stycke through
            # moved 96 point anchors in a 400-act sample off the id their
            # citations name, to disambiguate collisions that never occurred
            key = subarticle_key(t, num, self.article, self.parag,
                                 ".".join(p for p in self.points if p) or None)
            # an uncitable marker still holds its stack slot (so a deeper point
            # keeps its level) but never joins a child's key
            self.points.append(num if citable(num) else None)
            return key
        if t == "recital" and (num or "").isdigit():
            return "recital-%s" % num
        if t in _CLOSERS:
            # the enacting terms are over: an annex heading opens a region whose
            # own lettered list is not the last article's points ("BILAGA I ... a)"
            # anchored as "12.a", colliding with every other annex's first item)
            self.article = self.parag = self.stycke = None
            self.points = []
        return None


def first_stycke(t, num, key):
    """The `.S1` alias a *numbered* paragraph carries alongside its own key: its
    text is the paragraph's first stycke, so "artikel 9.2" and "artikel 9.2 första
    stycket" both name it and both must resolve (`9.2` and `9.2.S1`). Only the
    numbered form needs the alias -- an unnumbered paragraph is already keyed as
    the article's first stycke. None when the block has no second name."""
    return stycke_key(key, "1") if t == PARAGRAPH and citable(num) and key else None


def anchored_blocks(structure, aliases=True):
    """Walk the act in document order yielding `(anchor, block)` for every block
    a citation (or a guidance link) can target: an article (anchor = its id or
    number), a sub-article paragraph/point (the `subarticle_key` dotted form), a
    stycke (`9.2.S2`), and a numbered recital (`recital-N`). Blocks that cannot
    anchor are skipped.

    A numbered paragraph is yielded twice, under its own key and its first-stycke
    alias, so both citation forms resolve. Pass `aliases=False` where the walk
    enumerates *nodes* rather than the anchors that reach them -- the guidance
    prompt's target list would otherwise offer the model two indistinguishable
    names for the same run of text on every paragraph (a quarter of its lines)."""
    anchors = Anchors()
    for b in flatten(structure):
        t, num = b.get("type"), b.get("num")
        key = anchors.key(t, num, b.get("id"), b.get("depth"))
        if key:
            yield key, b
            alias = first_stycke(t, num, key) if aliases else None
            if alias:
                yield alias, b


# --------------------------------------------------------------------------
# CELEX document classification
# --------------------------------------------------------------------------
# A '(NN)' revision of a CELEX, and the CELEX it revises. Two shapes: an act's
# corrigendum appends 'R(NN)' to the act ('32016R0900R(01)' revises
# '32016R0900'), a treaty text's appends a bare '(NN)' ('12019W/TXT(01)' revises
# '12019W/TXT'). The optional R is the whole difference, and dropping only the
# parenthesis names a CELEX that does not exist ('32016R0900R').
#
# Shared because three places need the same answer and disagreed: the parse
# (a corrigendum takes its repeal date from the act it corrects), the build
# (which puts the act's notice in the corrigendum's freshness inputs), and the
# browse collapse. `facets._keep_latest_eu_revision` deliberately does NOT use
# this -- it groups on the bare-parenthesis split, so it collapses treaty
# revisions onto their base while leaving an act and its corrigenda as separate
# entries. Changing that changes which documents the browse lists, which is a
# separate decision from reading a repeal (rule:second-use-goes-to-lib).
RE_REVISION = re.compile(r"^(.+?)R?\(\d+\)$")


def revision_base(celex):
    """The CELEX a '(NN)' revision revises, or None when `celex` is not one."""
    m = RE_REVISION.match(celex)
    return m.group(1) if m else None


# Read off the CELEX number alone, so anything holding a CELEX can classify a
# document without the eurlex source: the parser stamps it onto the artifact,
# the renderer picks the page's kind label from it, and the corpus statistics
# scan separates acts from case law by it (rule:second-use-goes-to-lib).

def doctype(celex):
    """The document family from the CELEX sector digit (+ the act/case descriptor).
    Sector 6 (case law) is split by its two-letter document code -- CJ/TJ/FJ are
    judgments, CC an Advocate General's opinion (förslag till avgörande), CO/TO an
    order -- so an opinion is not listed as a judgment (E4)."""
    if celex.startswith("6"):
        return {"CC": "opinion", "CV": "opinion", "CP": "opinion",
                "CO": "order", "TO": "order", "FO": "order"}.get(
                    celex[5:7], "judgment")
    if celex.startswith("1"):
        return "treaty"
    if celex.startswith("3") and len(celex) > 5:
        return {"R": "regulation", "L": "directive",
                "D": "decision"}.get(celex[5], "act")
    return "act"


# the `doctype` values that `doctype` mints for sector 6. A case has no preamble
# (no visas, no recitals, no enacting terms), so the text-inferring parsers must
# not look for one -- a judgment quotes an act's "av följande skäl:" in passing,
# and reading that as the start of a recital list turns its whole reasoning into
# recitals.
CASELAW = ("judgment", "opinion", "order")
