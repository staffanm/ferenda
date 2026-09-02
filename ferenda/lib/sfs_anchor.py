"""The SFS paragraf anchor -- ``K2P3``, ``P3``, ``K1P5aS2`` -- in one place.

The fragment a statute provision is cited by is the join key of the whole
inbound-link graph: `sfs` mints it on the provision, the citation engine mints
it on the reference, and `catalog` joins the two strings. Five sites used to
spell it out for themselves -- the föreskrift nester, the SFS renumbering
layer, the författningskommentar extractor, the English-text pinpoint matcher
and the wiki commentary headings -- so a change to one spelling silently
unhooked the others.

The grammar, exactly as the sites already agreed on it:

* ``K<kapitel>P<paragraf>`` under a chapter, ``P<paragraf>`` in a flat law
* a paragraf's letter stays attached to its number ("7 a" -> ``P7a``)
* whitespace is removed from both ordinals, so the anchor survives a source
  that prints "4 a kap. 1 §" as two words
* a stycke, where the citing side pinpoints one, appends ``S<n>``

A whole chapter is ``K<kapitel>`` and needs no helper -- one caller mints it.
"""

import re

RE_SPACE = re.compile(r"\s+")


def paragraf_anchor(kapitel, paragraf, stycke=None):
    """The fragment id of one paragraf: ``K4P6``, ``P5b``, ``K5P2S3``.

    `kapitel` falsy means a flat (chapterless) law. Both ordinals may be
    written with spaces ("4 a") or be numbers; the anchor removes the
    whitespace and keeps the letter attached."""
    par = RE_SPACE.sub("", str(paragraf))
    anchor = ("K%sP%s" % (RE_SPACE.sub("", str(kapitel)), par)) if kapitel \
        else ("P%s" % par)
    return "%sS%s" % (anchor, stycke) if stycke else anchor


def unique_paragraf_anchor(kapitel, paragraf, seen):
    """:func:`paragraf_anchor`, with a ``-2`` / ``-3`` suffix breaking a clash
    against the anchors already minted in `seen` (which this adds to).

    A document normally cannot repeat a paragraf id, but a föreskrift that
    restarts its § numbering without opening a chapter does, and two nodes
    sharing a fragment would make the second uncitable."""
    base = paragraf_anchor(kapitel, paragraf)
    anchor, n = base, 2
    while anchor in seen:
        anchor, n = "%s-%d" % (base, n), n + 1
    seen.add(anchor)
    return anchor
