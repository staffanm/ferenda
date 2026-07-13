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

RE_TREATY_NUMBER = re.compile(r"^(\d{1,3})([A-Z]?)$")
RE_HUDOC_ARTICLE = re.compile(
    r"^(?:P(?P<protocol>\d+)-)?(?P<article>\d+)(?:-(?P<paragraph>\d+))?"
    r"(?:-(?P<letter>[a-z]))?$",
    re.IGNORECASE,
)


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
