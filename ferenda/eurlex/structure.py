"""Group an EU act's flat block sequence into its containment hierarchy.

Formex (and the OJ HTML behind it) is deeply nested -- an act is a preamble
(citations + recitals) followed by enacting terms divided into
parts > titles > chapters > sections, the articles within them, and each
article's paragraphs and points, with annexes after. The parser flattens that to
an ordered `Block` list that keeps the signals: a `heading` carries its division
`level`, an `article`/`paragraph`/`point` its `num` and citation `id` (anchor).
`nest` rebuilds the tree from those signals:

  * a `heading` opens an `avdelning` (division) nested under the nearest open
    division of lower `level` (parts > titles > chapters > sections);
  * an `article` is contained by the current deepest division (or the root) and
    holds the `paragraph`s that follow; a `paragraph` holds its further `stycke`n
    and its `point`s;
  * preamble matter (title/keyword/citation/recital/preamble) and trailing
    matter (ruling/signature) stay where they fall, as leaves.

Citation ids are untouched -- an article keeps its anchor `id` (`celex#5`), a
point its `celex#5.2.a` -- so nesting changes the shape, never the targets.

`nest` is the parse-time tree *builder*, run only by eurlex, so it stays here. Its
document-order inverse (`flatten`) and the shared sub-article anchor grammar
(`subarticle_key`, `anchored_blocks`) are the artifact-format contract the renderer
and the wiki guidance layer also read, so they live in `lib.eu_structure`; the
block-kind constants come back from there so producer and consumers share one
vocabulary.
"""

from ..lib.artifact import nest_by_level
from ..lib.eu_structure import ARTICLE as _ARTICLE
from ..lib.eu_structure import PARAGRAPH as _PARAGRAPH
from ..lib.eu_structure import POINT as _POINT
from ..lib.eu_structure import STYCKE as _STYCKE
from ..lib.formex import QUOTATION as _QUOTATION

# block kinds that contain others (everything else is a leaf)
_DIVISION = "heading"
# leaves that end an article's run (trailing matter), vs. preamble matter which
# simply precedes the first article
_CLOSERS = ("ruling", "signature")


def nest(blocks):
    """Flat EU-act block dicts -> a nested `structure` list."""
    article = parag = None    # current open article / paragraph

    def division(b):
        """A division opens a fresh run: the article it interrupts is closed."""
        nonlocal article, parag
        article = parag = None
        return {**b, "children": []}

    def content(b, siblings):
        """Everything below the division level, into `siblings` -- the open
        division's children, or the root."""
        nonlocal article, parag
        t = b.get("type")
        if t == _ARTICLE:
            article, parag = {**b, "children": []}, None
            siblings.append(article)
        elif t == _PARAGRAPH:
            parag = {**b, "children": []}
            (article["children"] if article else siblings).append(parag)
        elif t in (_STYCKE, _POINT):
            # a stycke is a further sub-paragraph of the open paragraph (or of
            # the article, when its stycken sit directly under it); it opens no
            # new paragraph, so the points that follow keep hanging off the same
            # one, and a point takes the same parent
            target = parag or article
            (target["children"] if target else siblings).append(dict(b))
        elif t == _QUOTATION:
            # an act quoted verbatim belongs to the paragraph that introduces
            # it ("I artikel 25 i detta direktiv föreskrevs följande:"), so it
            # hangs off that paragraph rather than off the section around it.
            # It opens nothing: the paragraph stays current, and the quoted
            # act's own points are already inside the quotation's own blocks
            (parag["children"] if parag else siblings).append(dict(b))
        else:
            siblings.append(dict(b))
            if t in _CLOSERS:
                article = parag = None

    return nest_by_level(
        blocks,
        lambda b: (b.get("level") or 1) if b.get("type") == _DIVISION else None,
        division, content)
