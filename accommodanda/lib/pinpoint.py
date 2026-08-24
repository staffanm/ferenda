"""Fragment id -> the human pinpoint a reader recognises ("K2P16S5" -> "2 kap.
16 § 5 st"), and that pinpoint -> the whole citation ("6 § räntelagen").

Factored out of ``lib/page`` so the serving layer can name a pinpoint without
importing the renderer: ``lib/pins`` labels a citation-resolved search hit with
the provision it points at, and pulling all of page.py (and markupsafe) into the
API's search path for one string transform is weight the endpoint should not
carry. The same split ``lib/coe_ids`` makes for the CoE article grammar. It
stays a leaf for that reason -- it imports nothing of ours, so no caller pays
for a name.

Four functions, in two layers. The first three answer *where in a document*,
from the fragment alone; the fourth adds *which document*:

  * ``human_fragment`` -- the Swedish, CoE and page-number fragment grammars:
    "K2P16S5" -> "2 kap. 16 § 5 st", "A6P1" -> "artikel 6 punkt 1",
    "sid39" -> "s. 39".
  * ``eu_article_label`` -- the EU act's own grammar, where an anchor is a bare
    number: "9.2.S2" -> "artikel 9.2 andra stycket".
  * ``pinpoint_label`` -- **the one to call.** It picks between the two by the
    fragment's shape. Calling ``human_fragment`` directly on an EU anchor reads
    the "S2" inside "9.2.S2" as a Swedish stycke segment and answers "2 st".
  * ``citation_label`` / ``citation`` -- the pinpoint plus the document's name,
    which is what a reader can actually look up: "Artikel 6 EKMR", not "A6".
"""

import re

FRAG_LABEL = {"K": "kap.", "P": "§", "O": "mom.", "S": "st", "N": "p", "M": "men."}
_FRAG_SEG = re.compile(r"([KPOSNM])([0-9a-zåäö]+)")
# an anchor writes an inserted paragraf's letter tight against its number
# ("P52u", "K6bP52c") because it is an id; a citation writes it apart -- "52 u §",
# "6 b kap.". Restoring the space is what makes the label citable.
_FRAG_LETTER = re.compile(r"^(\d+)([a-zåäö]+)$")


def _spaced(val):
    return _FRAG_LETTER.sub(r"\1 \2", val)


def human_fragment(frag):
    """A fragment id -> a human pinpoint: "K2P16S5" -> "2 kap. 16 § 5 st";
    "sid39" -> "s. 39"; change markers ("L1988:187") and unknowns -> ""."""
    if not frag:
        return ""
    if frag.startswith("sid"):
        return "s. " + frag[3:]
    coe = re.fullmatch(
        r"A((?:\d+[A-Za-z]?|[IVXLCDM]+)(?:\.\d+)?)(?:-(\d+))?"
        r"(?:P(\d+)(?:-(\d+))?)?(?:L([a-z])(?:-(\d+))?)?", frag)
    if coe:
        parts = ["artikel %s" % coe.group(1)]
        if coe.group(3):
            parts.append("punkt %s" % coe.group(3))
        if coe.group(5):
            parts.append("led %s" % coe.group(5))
        instance = coe.group(6) or coe.group(4) or coe.group(2)
        if instance:
            parts.append("variant %s" % instance)
        return " ".join(parts)
    segs = _FRAG_SEG.findall(frag)
    return " ".join("%s %s" % (_spaced(val), FRAG_LABEL[letter])
                    for letter, val in segs)


# an EU act anchors an article on its bare number ("32", "6.1", "6.1.c") -- a
# shape `human_fragment` deliberately does not type, since its callers in stats
# and eurlex/render handle those anchors themselves. A trailing ".S<n>" is a
# stycke (sub-paragraph) of that article: "9.2.S2" = artikel 9.2 andra stycket.
# A point never carries one -- the act names it by its paragraph whichever stycke
# holds it -- so the two tails are alternatives, not a sequence.
_EU_ARTICLE = re.compile(
    r"(\d+[a-z]?(?:\.\d+)*)(?:\.([a-z])|\.S(\d+))?", re.IGNORECASE)

# the Swedish ordinal a stycke is named by ("andra stycket"), which is how the
# acts themselves and everyone citing them write it -- never "stycke 2". Shared
# with `lib.page`, which renders the same prose for an SFS stycke pinpoint
# (rule:second-use-goes-to-lib); this is the leaf, so page imports it back.
STYCKE_ORDINAL = {1: "första", 2: "andra", 3: "tredje", 4: "fjärde",
                  5: "femte", 6: "sjätte", 7: "sjunde", 8: "åttonde",
                  9: "nionde", 10: "tionde", 11: "elfte", 12: "tolfte",
                  13: "trettonde", 14: "fjortonde", 15: "femtonde",
                  16: "sextonde", 17: "sjuttonde", 18: "artonde",
                  19: "nittonde", 20: "tjugonde"}


def eu_article_label(frag):
    """An EU act's article/sub-article anchor -> the pinpoint a reader
    recognises: "6.1" -> "artikel 6.1", "6.1.c" -> "artikel 6.1 c", and a stycke
    anchor "9.2.S2" -> "artikel 9.2 andra stycket". '' when `frag` is not an EU
    article anchor, or when its stycke runs past the ordinal table -- "21 stycket"
    is not Swedish, and reader-facing prose is the whole point of this module."""
    m = _EU_ARTICLE.fullmatch(frag or "")
    if not m:
        return ""
    article, punkt, stycke = m.groups()
    if punkt:
        return "artikel %s %s" % (article, punkt)
    if not stycke:
        return "artikel " + article
    ordinal = STYCKE_ORDINAL.get(int(stycke))
    return "artikel %s %s stycket" % (article, ordinal) if ordinal else ""


def pinpoint_label(frag):
    """A fragment id -> the pinpoint to *show a reader*, covering the bare EU
    article anchor as well as the Swedish and CoE forms `human_fragment` types.
    Returns '' for an id with no reader-facing form.

    An EU anchor is decided by shape and never falls through: it is all digits and
    dots, which no Swedish or CoE fragment is (those lead with K/P/O/S/N/M, "sid"
    or "A"), while `human_fragment`'s segment scan *does* match the "S2" inside
    "9.2.S2" and would read a stycke anchor as the bare "2 st"."""
    if _EU_ARTICLE.fullmatch(frag or ""):
        return eu_article_label(frag)
    return human_fragment(frag)


# the acronym a composed descriptive label carries at its end -- the inverse of
# `labels._named`, which writes "Europakonventionen (EKMR)" from a curated
# label and abbr. All caps and short, so an ordinary parenthetical ("lag om
# ändring i ...(1994:1219)") is not read as one.
_ACRONYM = re.compile(r".+\s\(([A-ZÅÄÖ][A-ZÅÄÖ0-9 .-]{1,15})\)$")


def short_name(descriptive):
    """The shortest name for a document that a reader still recognises: the
    acronym its descriptive label ends in, or the label itself.

    "Europakonventionen (EKMR)" -> "EKMR"; "räntelagen" -> "räntelagen"."""
    m = _ACRONYM.fullmatch(descriptive or "")
    return m.group(1) if m else (descriptive or "")


def citation_label(name, frag):
    """A cited provision written as a lawyer would cite it: "6 § räntelagen",
    "8 kap. 7 § regeringsformen", "Artikel 6 EKMR".

    The raw "1975:635#P6" a link carries is a machine address -- readable only
    if you already know which act 1975:635 is, which is the opposite of what a
    "most-cited paragraf" list, or the middle of a citation graph, is for.
    `name` is the document's own citing name (the catalog's `descriptive`
    column, or `short_name` of it where the space is tight) and
    `pinpoint_label` turns the anchor into the pinpoint.

    The first letter is raised, because this is a citation standing on its own
    -- a heading, a row in a table -- and not a phrase inside a sentence. A
    name that starts with its number ("6 § räntelagen") is unaffected."""
    where = pinpoint_label(frag)
    out = "%s %s" % (where, name) if where and name else (where or name or "")
    return out[:1].upper() + out[1:]


def citation(uri, descriptive):
    """`citation_label` for a whole uri and the catalog's `descriptive` name of
    the document it addresses: "https://lagen.nu/1975:635#P6" -> "6 §
    räntelagen".

    A document the catalog cannot name descriptively falls back to its own
    local path ("ext/coe/005"), never to an invented name: the reader is then
    shown an address, and knows it. The host is stripped here rather than
    against `catalog.BASE`, so this module keeps importing nothing."""
    root, _, frag = uri.partition("#")
    local = root.split("//", 1)[-1].partition("/")[2] if "//" in root else root
    return citation_label(descriptive or local, frag)


# the article part of a CoE fragment ("A6P1" -> "A6", "A5-2" kept whole so a
# repeated article stays distinct from its base). A bis-article's suffix
# letter ("A15A") is never followed by a digit or a lowercase letter --
# without the lookahead it would swallow a following segment opener
# ("A6P1" -> "A6P", and a lettered point "A3Lh" -> "A3L")
_COE_ARTICLE = re.compile(
    r"A(?:\d+(?:[A-Za-z](?![0-9a-z]))?|[IVXLCDM]+)(?:\.\d+)?(?:-\d+)?")

# an SFS change-marker anchor ("L1988:942"): the change-entry list's own id,
# not a provision a reader navigates
_CHANGE_MARKER = re.compile(r"L\d{4}(?:_\d+)?:\S+")


def is_change_marker(frag):
    """True for a change-marker anchor -- a citation graph over provisions
    drops these rather than drawing a node no reader can name."""
    return bool(_CHANGE_MARKER.fullmatch(frag or ""))


def unit_anchor(frag):
    """A fragment id -> the pinpointable *unit* it belongs to -- the node the
    citation graph draws. "K2P16S5" -> "K2P16" (the §), "A6P1" -> "A6" (the
    article), "9.2.S2" -> "9.2". A fragment with no finer typed tail (a page
    "sid39", a change marker "L1988:942") is its own unit."""
    m = _EU_ARTICLE.fullmatch(frag or "")
    if m:
        return m.group(1)
    m = _COE_ARTICLE.match(frag or "")
    if m:
        return m.group(0)
    segs = _FRAG_SEG.findall(frag or "")
    unit = "".join(letter + val for letter, val in segs
                   if letter in ("K", "P", "O"))
    return unit or frag
