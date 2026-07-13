"""Structured parser for the Montreal Convention in SFS 2010:510."""

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

NUMBER = "MC99"
TITLES = {
    "en": "CONVENTION FOR THE UNIFICATION OF CERTAIN RULES FOR INTERNATIONAL CARRIAGE BY AIR",
    "fr": "CONVENTION POUR L'UNIFICATION DE CERTAINES RÈGLES RELATIVES AU TRANSPORT AÉRIEN INTERNATIONAL",
    "sv": "KONVENTION OM VISSA ENHETLIGA REGLER FÖR INTERNATIONELLA LUFTTRANSPORTER",
}
RE_ARTICLE = {
    "en": re.compile(r"^Article\s+(\d+)$"),
    "fr": re.compile(r"^Article\s+(\d+)$"),
    "sv": re.compile(r"^Artikel\s+(\d+)$"),
}
RE_CHAPTER = {
    "en": re.compile(r"^CHAPTER\s+([IVX]+)$"),
    "fr": re.compile(r"^CHAPITRE\s+([IVX]+)$"),
    "sv": re.compile(r"^KAPITEL\s+([IVX]+)$"),
}
CLOSING_PREFIX = {
    "en": "IN WITNESS WHEREOF",
    "fr": "EN FOI DE QUOI",
    "sv": "TILL BEKRÄFTELSE AV DETTA",
}


def split_appendix(text):
    return split_convention_appendix(text, "2010:510")


def _language_blocks(items):
    return language_blocks(
        items, lambda language, item: item == TITLES[language],
        "Montreal Convention")


def _parse_language(language, items):
    ingress = []
    children = []
    current = None
    i = 1
    while i < len(items):
        item = items[i]
        chapter = RE_CHAPTER[language].match(item)
        article = RE_ARTICLE[language].match(item)
        if chapter:
            assert i + 1 < len(items), "%s chapter lacks a title" % language
            i += 1
            children.append(Division(
                chapter.group(1), "%s - %s" % (item, items[i])))
            current = None
        elif article:
            assert i + 1 < len(items), "%s article lacks a title" % language
            i += 1
            current = Article(
                article.group(1), "%s - %s" % (item, items[i]))
            children.append(current)
        elif item.startswith(CLOSING_PREFIX[language]):
            current = None
        elif current is None:
            # The second signature paragraph follows the first and is likewise
            # outside Article 57. It occurs after all seven chapters.
            if len([child for child in children
                    if isinstance(child, Article)]) < 57:
                ingress.append(item)
        else:
            current.text.append(item)
        i += 1
    assert [child.ordinal for child in children if isinstance(child, Article)] \
        == [str(number) for number in range(1, 58)], \
        "unexpected %s Montreal Convention article sequence" % language
    assert [child.ordinal for child in children if isinstance(child, Division)] \
        == ["I", "II", "III", "IV", "V", "VI", "VII"], \
        "unexpected %s Montreal Convention chapter sequence" % language
    for child in children:
        if isinstance(child, Article):
            if child.ordinal == "53" and language == "sv":
                assert child.text[4].endswith("ut-")
                child.text[4:6] = [child.text[4][:-1] + child.text[5]]
            if child.ordinal not in ("53", "55"):
                child.text = legal_paragraphs(child.text)
    return Instrument(NUMBER, None, None, items[0], ingress, children)


def parse_appendix(text):
    blocks = _language_blocks(paragraphs(text))
    return align({language: [_parse_language(language, blocks[language])]
                  for language in LANGUAGES})
