"""Structured parser for the trilingual ATMF appendix in SFS 2022:366."""

import re

from .convention import (
    LANGUAGES,
    Article,
    Instrument,
    align,
    legal_paragraphs,
    paragraphs,
)
from .convention import (
    split_appendix as split_convention_appendix,
)

NUMBER = "ATMF"
TITLES = {
    "fr": "Règles uniformes concernant l'admission technique de matériel ferroviaire utilisé en trafic international (ATMF, appendice G à la Convention)",
    "en": "Uniform Rules concerning the Technical Admission of Railway Material used in International Traffic (ATMF, appendix G to the Convention)",
    "sv": "Enhetliga rättsregler för tekniskt godkännande av järnvägsmateriel som används i internationell trafik (ATMF, bihang G till fördraget)",
}
RE_ARTICLE = {
    "en": re.compile(
        r"^(?:(/[^/]+/)\s+)?Article\s+(\d+[a-z]?)$", re.I),
    "fr": re.compile(
        r"^(?:(/[^/]+/)\s+)?Article\s+(premier|\d+[a-z]?)$", re.I),
    "sv": re.compile(
        r"^(?:(/[^/]+/)\s+)?Artikel\s+(\d+[a-z]?)$", re.I),
}
ORDINALS = (
    "1", "2", "3", "3a", "4", "5", "6", "6a", "6b", "7", "7a", "8",
    "9", "10", "10a", "10b", "11", "12", "13", "14", "15", "15a",
    "16", "17", "18", "19", "20", "21",
)
MISPLACED_SV_ARTICLE_11 = "a) all den information som anges i § 2,"


def split_appendix(text):
    return split_convention_appendix(text, "2022:366")


def _ordinal(language, match):
    raw = match.group(2).casefold()
    return "1" if language == "fr" and raw == "premier" else raw


def _language_blocks(items):
    # The three titles are printed together, followed by the complete French,
    # English and Swedish runs. The current Article 1 heading is therefore the
    # reliable boundary; the title itself is supplied from the header triplet.
    starts = [(i, language) for (i, item), language in zip(
        [(i, item) for i, item in enumerate(items)
         if item.startswith("/Upphör")
         and re.search(r"(?:Article (?:premier|1)|Artikel 1)$", item)],
        ("fr", "en", "sv"), strict=True)]
    ends = [starts[1][0], starts[2][0], len(items)]
    return {language: items[start:end]
            for (start, language), end in zip(starts, ends, strict=True)}


def _parse_language(language, items):
    children = []
    current = None
    accept = False
    i = 0
    while i < len(items):
        item = items[i]
        match = RE_ARTICLE[language].match(item)
        if match:
            ordinal = _ordinal(language, match)
            directive = match.group(1)
            accept = directive is None or directive.startswith("/Upphör")
            current = None
            assert i + 1 < len(items), "%s ATMF article lacks a title" % language
            i += 1
            if accept:
                heading = re.sub(r"^/[^/]+/\s+", "", item)
                current = Article(
                    ordinal, "%s - %s" % (heading, items[i]))
                children.append(current)
        elif accept and current is not None:
            current.text.append(item)
        i += 1
    assert tuple(child.ordinal for child in children) == ORDINALS, \
        "unexpected %s ATMF article sequence" % language
    for child in children:
        assert isinstance(child, Article)
        child.text = legal_paragraphs(child.text)
        if child.ordinal == "2" and language in ("en", "fr"):
            child.text[8:10] = [" ".join(child.text[8:10])]
        elif child.ordinal == "7" and language in ("en", "sv"):
            child.text[4:6] = [" ".join(child.text[4:6])]
        elif child.ordinal == "10" and language == "fr":
            child.text[11:13] = [" ".join(child.text[11:13])]
        elif child.ordinal == "10a" and language == "fr":
            # The French run in the incorporated source stops after § 3; the
            # English and Swedish runs also contain §§ 4–6. Preserve that
            # source difference as empty French cells in the aligned rows.
            child.text.extend([""] * 7)
        elif child.ordinal == "11" and language == "en":
            assert child.text.pop(19) == MISPLACED_SV_ARTICLE_11, \
                "unexpected misplaced Swedish ATMF paragraph"
        elif child.ordinal == "11" and language == "sv":
            child.text.insert(11, MISPLACED_SV_ARTICLE_11)
        elif child.ordinal == "13" and language in ("en", "sv"):
            child.text[:2] = [" ".join(child.text[:2])]
        elif child.ordinal == "21" and language in ("en", "sv"):
            child.text[3:5] = [" ".join(child.text[3:5])]
    return Instrument(NUMBER, None, None, TITLES[language], [], children)


def parse_appendix(text):
    items = paragraphs(text)
    assert items[:3] == [TITLES[language] for language in ("fr", "en", "sv")], \
        "unexpected ATMF appendix title triplet"
    blocks = _language_blocks(items)
    return align({language: [_parse_language(language, blocks[language])]
                  for language in LANGUAGES})
