"""Typed model for an EU legal document parsed from Formex.

Formex marks up two document families we care about -- legislation/treaties
(`ACT`) and case law (`JUDGMENT`) -- with deep, faithful structure (parts,
titles, chapters, articles, paragraphs, points; recitals; judgment paragraphs
and the ruling). We flatten that into an ordered list of typed `Block`s rather
than a nesting tree (like the DV and forarbete models): each block carries its
structural number and a citation `anchor`, which is enough to render the
document and to resolve pinpoint citations ("Article 5 of Directive ...") to a
fragment, without the bookkeeping of a tree.

The document URI is the language-neutral CELEX URI the citation engine mints
for EU references (`https://lagen.nu/ext/celex/{CELEX}`), so a citation to an
act and the act itself agree by construction.
"""

from dataclasses import dataclass, field

# the language-neutral CELEX URI the citation engine mints for EU references, so
# a citation to an act and the act itself agree by construction
BASE = "https://lagen.nu/ext/celex/%s"


def doctype(celex):
    """The document family from the CELEX sector digit (+ the act descriptor)."""
    if celex.startswith("6"):
        return "judgment"
    if celex.startswith("1"):
        return "treaty"
    if celex.startswith("3") and len(celex) > 5:
        return {"R": "regulation", "L": "directive",
                "D": "decision"}.get(celex[5], "act")
    return "act"


@dataclass
class Block:
    kind: str                  # see KINDS below
    text: str
    num: str | None = None     # structural marker: recital "(1)", article "1",
                               # paragraph "2", point "a"
    level: int | None = None   # heading/division depth (1 = outermost)
    anchor: str | None = None  # citation-target fragment (e.g. article "5")
    defines: str | None = None # a definitions-article point: the term it defines


# block kinds, in rough document order of where they occur:
#   title       the document title (ACT) / case title (JUDGMENT)
#   keyword     a subject keyword (JUDGMENT index)
#   citation    a preamble "having regard to ..." visa
#   recital     a numbered preamble recital (CONSID)
#   preamble    preamble framing text (PREAMBLE.INIT / .FINAL)
#   heading     a division/section/chapter title in the body
#   article     an article title (TI.ART)
#   paragraph   a body paragraph (numbered PARAG, ALINEA, or judgment NP)
#   point       a list item (LIST/ITEM)
#   ruling      the operative part of a judgment (JURISDICTION/DISPOSITIF)
#   signature   place/date/signatories


@dataclass
class EurlexDoc:
    celex: str
    uri: str                   # https://lagen.nu/ext/celex/{CELEX}
    doctype: str               # regulation | directive | decision | judgment
                               # | treaty | act
    lang: str                  # 3-letter code (swe, eng)
    title: str = ""
    date: str | None = None    # ISO date of the document
    ecli: str | None = None    # case law
    oj: str | None = None      # Official Journal reference (e.g. "L 333")
    body: list = field(default_factory=list)   # [Block], document order
