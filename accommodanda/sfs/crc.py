"""Structured parser for the trilingual child-convention appendix (CRC).

SFS 2018:1197 prints the complete English and French originals followed by the
Swedish translation.  A few headings and list points were lost into adjacent
paragraphs in the source conversion; those repairs are kept here, while the
cross-language model and alignment invariants live in :mod:`.convention`.
"""

import re

from .convention import (
    LANGUAGES,
    Article,
    Division,
    Instrument,
    align,
    language_blocks,
    legal_paragraphs,
    paragraphs,
)
from .convention import (
    split_appendix as split_convention_appendix,
)

NUMBER = "CRC"
TITLES = {
    "en": "Convention on the Rights of the Child",
    "fr": "Convention relative aux droits de l’enfant",
    "sv": "Konvention om barnets rättigheter",
}
RE_ARTICLE = {
    "en": re.compile(r"^Article\s+(\d+)$"),
    "fr": re.compile(r"^Article\s+(premier|\d+)$", re.I),
    "sv": re.compile(r"^Artikel\s+(I|\d+)$"),
}
RE_DIVISION = {
    "en": re.compile(r"^Part\s+([IVX]+)$", re.I),
    "sv": re.compile(r"^Del\s+([IVX]+)$", re.I),
}
FRENCH_DIVISIONS = {
    "première partie": "I",
    "deuxième partie": "II",
    "troisième partie": "III",
}
RE_EMBEDDED_ARTICLE = re.compile(r"\s+(?=Article\s+\d+\s*$)")


def split_appendix(text):
    return split_convention_appendix(text, "2018:1197")


def _language_blocks(items):
    # The first Swedish label names the appendix as a whole; ``(Översättning)``
    # marks the switch after French. Neither belongs to a language instrument.
    items = [item for item in items
             if item not in ("FN:s konvention om barnets rättigheter",
                             "(Översättning)")]
    return language_blocks(
        items, lambda language, item: item == TITLES[language], "CRC")


def _article_ordinal(language, heading):
    match = RE_ARTICLE[language].match(heading)
    if not match:
        return None
    return "1" if match.group(1).casefold() in ("premier", "i") \
        else match.group(1)


def _division_ordinal(language, heading):
    if language == "fr":
        return FRENCH_DIVISIONS.get(heading.casefold())
    match = RE_DIVISION[language].match(heading)
    return match.group(1).upper() if match else None


def _expand_embedded_articles(language, items):
    if language != "en":
        return items
    return [part for item in items
            for part in RE_EMBEDDED_ARTICLE.split(item)]


def _repair_ingress(language, items):
    if language != "sv":
        return items
    # Two distinct preamble clauses were concatenated in the Swedish source.
    repaired = []
    for item in items:
        repaired.extend(re.split(
            r"(?<=arbetar med barns välfärd,)\s+(?=som beaktar att [“”\"]barnet)",
            item))
    return repaired


def _parse_language(language, items):
    items = _expand_embedded_articles(language, items)
    title = items[0]
    ingress = []
    children = []
    current = None
    for item in items[1:]:
        division = _division_ordinal(language, item)
        article = _article_ordinal(language, item)
        if division:
            current = None
            children.append(Division(division, item))
        elif article:
            current = Article(article, item)
            children.append(current)
        elif current is None:
            ingress.append(item)
        else:
            current.text.append(item)
    assert [child.ordinal for child in children if isinstance(child, Article)] \
        == [str(number) for number in range(1, 55)], \
        "unexpected %s CRC article sequence" % language
    for child in children:
        if isinstance(child, Article):
            if language == "fr" and child.ordinal in ("3", "15"):
                child.text = [part for item in child.text for part in re.split(
                    r"\s+(?=\d+\.\s)", item)]
            child.text = legal_paragraphs(child.text)
            # French and Swedish retain a post-convention signature formula
            # after Article 54; it is not part of that article (and is absent
            # from the English run in this source conversion).
            if child.ordinal == "54":
                child.text = child.text[:1]
    return Instrument(
        nummer=NUMBER,
        protokoll=None,
        uri=None,
        rubrik=title,
        ingress=_repair_ingress(language, ingress),
        children=children)


def parse_appendix(text):
    """Parse and horizontally align the CRC appendix's language versions."""
    blocks = _language_blocks(paragraphs(text))
    return align({language: [_parse_language(language, blocks[language])]
                  for language in LANGUAGES})
