"""Shared identity rules for Council of Europe treaties and HUDOC article codes.

The Treaty Office source publishes the provisions and HUDOC supplies the cases
that apply them.  They are separate verticals, so the URI grammar lives here:
both producers can mint the same treaty/article target without importing each
other.
"""

import re

from .catalog import BASE
from .coe_ids import article_fragment

# HUDOC's article facet writes Convention articles as ``8`` / ``6-3-d`` and
# protocol provisions as ``P7-4``.  Protocol numbers are human numbers, while
# the Treaty Office identifies the instruments by ETS/CETS number.
ECHR_PROTOCOLS = {
    "1": "009",
    "4": "046",
    "6": "114",
    "7": "117",
    "12": "177",
    "13": "187",
    "14": "194",
    "16": "214",
}

# The leading instrument phrase of a treaty title -- the designation dropped for
# alphabetical sorting, mirroring the SFS "Lag (yyyy:nn) om " prefix. A title
# opens with an optional "European", optional ordinal/qualifier words, an
# instrument noun, and a connector ("on"/"for"/"of"/"to"/"amending"); what
# follows is the subject the treaty files under ("Convention for the Protection
# of Human Rights and Fundamental Freedoms" -> "Protection of Human Rights and
# Fundamental Freedoms"). The stored title carries no ETS/CETS reference (the
# harvester strips it), so this splits the name alone.
RE_INSTRUMENT = re.compile(
    r"^(?:European\s+)?"
    r"(?:(?:General|Interim|Outline|Framework|Additional|Provisional|Revised|"
    r"Second|Third|Fourth|Fifth|Sixth|Seventh|Eighth|Ninth)\s+)*"
    r"(?:Convention|Agreement|Charter|Code|Protocol|Arrangement|Statute|"
    r"Understanding|Treaty|Act)"
    r"\s+(?:on the|on|for the|for|relating to|concerning|of the|of|"
    r"to the|to|amending the|amending)\s+",
    re.I)
# A protocol title names the instrument it amends after "Protocol ... to (the)"
# or "... amending (the)": "Protocol No. 4 to the Convention for the Protection
# of Human Rights and Fundamental Freedoms" -> "Convention for the Protection
# of Human Rights and Fundamental Freedoms".
RE_PROTOCOL_LEAD = re.compile(
    r"^.*?\bprotocol\b.*?\b(?:to the|to|amending the|amending)\s+", re.I)

RE_TREATY_NUMBER = re.compile(r"^(\d{1,3})([A-Z]?)$")
RE_HUDOC_ARTICLE = re.compile(
    r"^(?:P(?P<protocol>\d+)-)?(?P<article>\d+)(?:-(?P<paragraph>\d+))?"
    r"(?:-(?P<letter>[a-z]))?$",
    re.IGNORECASE,
)


def significant_title(title):
    """Split a treaty title into the leading instrument designation (dropped for
    sorting, shown subdued) and the subject it files under (emphasised), the
    SFS-listing convention applied to Council-of-Europe names. A title with no
    recognised instrument-plus-connector head keeps its subject whole, bar a bare
    leading 'European'."""
    match = RE_INSTRUMENT.match(title)
    if match:
        return title[:match.end()], title[match.end():].strip()
    if title[:9].lower() == "european ":
        return title[:9], title[9:].strip()
    return "", title


def protocol_reference(title):
    """For a protocol title, the parent-instrument name it amends -- the text
    after 'Protocol ... to (the)' / '... amending (the)' -- or None when the
    title is not a protocol or names no parent. The caller resolves this name to
    a treaty in the corpus (a protocol's title appends its own qualifiers, so the
    match is by prefix, not equality)."""
    if "protocol" not in title.lower():
        return None
    match = RE_PROTOCOL_LEAD.match(title)
    return title[match.end():].strip() if match else None


def treaty_number(value):
    """Canonical three-digit Treaty Office number (including rare A suffixes)."""
    match = RE_TREATY_NUMBER.fullmatch(str(value).strip().upper())
    if not match:
        raise ValueError("invalid ETS/CETS number %r" % value)
    return "%03d%s" % (int(match.group(1)), match.group(2))


def treaty_uri(number):
    return "%sext/coe/%s" % (BASE, treaty_number(number))


def article_uri(number, article, paragraph=None, letter=None):
    return "%s#%s" % (
        treaty_uri(number), article_fragment(article, paragraph, letter)
    )


def hudoc_article(code):
    """Map one HUDOC article-facet code to its canonical CoE provision URI.

    Unknown protocol numbers are returned as ``None``: inventing an ETS/CETS
    identity would create a citation target that no Treaty Office artifact can
    ever satisfy.  Unknown syntactic forms are likewise ignored by callers.
    """
    match = RE_HUDOC_ARTICLE.fullmatch((code or "").strip())
    if not match:
        return None
    protocol = match.group("protocol")
    number = ECHR_PROTOCOLS.get(protocol) if protocol else "005"
    if number is None:
        return None
    return article_uri(number, match.group("article"), match.group("paragraph"),
                       match.group("letter"))


def hudoc_articles(value):
    """Unique provision URIs from HUDOC's semicolon-delimited article field."""
    out = []
    for expression in (value or "").split(";"):
        # HUDOC writes a right applied in conjunction with another as ``14+3``.
        # Both are real cited provisions and must become graph edges.
        for code in expression.split("+"):
            uri = hudoc_article(code)
            if uri and uri not in out:
                out.append(uri)
    return out
