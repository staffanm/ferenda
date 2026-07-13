"""Structured parser for the trilingual ECHR appendix in SFS 1994:1219.

The ordinary SFS tokenizer correctly sees this as one appendix but cannot know
that it contains three complete language runs of the same seven international
instruments.  This module owns that one source-specific format: it splits the
language runs, recognizes the base convention and additional protocols, and
merges matching sections, articles and legal paragraphs into one aligned typed
model.
"""

import re

from ..lib.coe import ECHR_PROTOCOLS, treaty_uri
from .convention import (
    LANGUAGES,
    align,
    language_blocks,
    paragraphs,
)
from .convention import (
    Article as _Article,
)
from .convention import (
    Division as _Section,
)
from .convention import (
    Instrument as _Instrument,
)
from .convention import (
    split_appendix as split_convention_appendix,
)

BASE_NUMBER = "005"

RE_ARTICLE = {
    "en": re.compile(r"^Article\s+(\d+)(?:\s*[-–]\s*(.*))?$", re.I),
    "fr": re.compile(r"^Article\s+(\d+)(?:\s*[-–]\s*(.*))?$", re.I),
    "sv": re.compile(r"^Artikel\s+(\d+)(?:\s*[-–]\s*(.*))?$", re.I),
}
RE_SECTION = {
    "en": re.compile(r"^SECTION\s+([IVXLCDM]+)\s*[-–]\s*(.*)$", re.I),
    "fr": re.compile(r"^TITRE\s+([IVXLCDM]+)\s*[-–]\s*(.*)$", re.I),
    "sv": re.compile(r"^AVDELNING\s+([IVXLCDM]+)\s*[-–]\s*(.*)$", re.I),
}
RE_PRINTED_DATE = re.compile(r"^\d{1,2}\.[IVXLCDM]+\.\d{4}\s+", re.I)
RE_PARAGRAPH_MARKER = re.compile(r"^(?:\d+\.|[a-z]\))\s", re.I)
RE_NUMBERED_PARAGRAPH = re.compile(r"^\d+\.\s")
RE_EMBEDDED_POINT = re.compile(r"(?<=:)\s+(?=[a-z]\)\s)", re.I)

BASE_PREFIX = {
    "en": "convention for the protection of human rights",
    "fr": "convention de sauvegarde des droits de l'homme",
    "sv": "europeiska konventionen om skydd för de mänskliga rättigheterna",
}
TITLE_CONTINUATIONS = {
    "en": {"of", "for", "to"},
    "fr": {"a", "à", "au", "aux", "de", "des", "du", "pour"},
    "sv": {"av", "för", "om", "till"},
}

def split_appendix(text):
    """Return the ordinary statute text and the ECHR appendix body.

    The exact heading is a source-format boundary.  A missing or duplicated
    boundary means the special case no longer describes the downloaded source
    and must fail visibly instead of silently falling back to the flat parser.
    """
    return split_convention_appendix(text, "1994:1219")


def _base_heading(language, paragraph):
    return paragraph.casefold().startswith(BASE_PREFIX[language])


def _language_blocks(paragraphs):
    return language_blocks(paragraphs, _base_heading, "ECHR")


def _protocol_number(language, paragraph):
    heading = RE_PRINTED_DATE.sub("", paragraph).strip()
    folded = heading.casefold()
    if language == "en":
        if not folded.startswith("protocol "):
            return None
        match = re.match(r"protocol\s+no\.?\s*(\d+)\b", folded)
        return match.group(1) if match else "1" if folded.startswith("protocol to ") else None
    if language == "fr":
        if not folded.startswith("protocole "):
            return None
        if folded.startswith("protocole additionnel"):
            return "1"
        match = re.match(r"protocole\s+n(?:o|°)?\s*(\d+)\b", folded)
        return match.group(1) if match else None
    if folded.startswith("tilläggsprotokoll "):
        return "1"
    if not folded.startswith("protokoll "):
        return None
    match = re.match(r"protokoll\s+nr\s*(\d+)\b", folded)
    return match.group(1) if match else None


def _full_heading(prefix, title):
    return prefix if not title else "%s - %s" % (prefix, title)


def _article_paragraphs(paragraphs):
    """Repair printed-layout splits into logical legal paragraphs.

    A blank line occasionally bisects a numbered paragraph, while Article 35's
    English point ``a)`` is embedded after paragraph 3's colon.  Markers are the
    semantic boundary: embedded points split; an unmarked fragment following a
    marked paragraph rejoins that paragraph.
    """
    out = []
    for paragraph in paragraphs:
        for part in RE_EMBEDDED_POINT.split(paragraph):
            if out and RE_NUMBERED_PARAGRAPH.match(out[-1]) \
                    and not RE_PARAGRAPH_MARKER.match(part):
                out[-1] += " " + part
            else:
                out.append(part)
    return out


def _protocol_16_ingress(language, paragraphs):
    paragraphs = [paragraph for paragraph in paragraphs
                  if paragraph.casefold() != "strasbourg,"
                  and not re.fullmatch(r"\d{1,2}\.[IVXLCDM]+\.\d{4}", paragraph,
                                       re.I)]
    if language != "sv":
        return paragraphs
    # The Swedish source collapses the five clauses (including the concluding
    # agreement) into one physical paragraph.  Its repeated ``som …`` grammar
    # is the same clause boundary the English/French blank lines express.
    assert len(paragraphs) == 2 and paragraphs[0] == "Preambel", \
        "unexpected Swedish Protocol 16 preamble shape"
    clauses = re.split(r"(?<=,)\s+(?=som (?:beaktar|anser)|har kommit överens)",
                       paragraphs[1])
    return [paragraphs[0], *clauses]


def _parse_instrument(language, paragraphs, protocol):
    title = RE_PRINTED_DATE.sub("", paragraphs[0]).strip()
    ingress = []
    children = []
    current = None
    i = 1
    while i < len(paragraphs):
        paragraph = paragraphs[i]
        section = RE_SECTION[language].match(paragraph)
        article = RE_ARTICLE[language].match(paragraph)
        if section:
            current = None
            children.append(_Section(
                section.group(1).upper(),
                _full_heading(paragraph[:section.start(2)].rstrip(" -–"),
                              section.group(2).strip())))
        elif article:
            heading = paragraph
            title_part = (article.group(2) or "").strip()
            if (title_part.casefold().rsplit(" ", 1)[-1]
                    in TITLE_CONTINUATIONS[language]
                    and i + 1 < len(paragraphs)
                    and not RE_ARTICLE[language].match(paragraphs[i + 1])
                    and not RE_SECTION[language].match(paragraphs[i + 1])):
                i += 1
                heading += " " + paragraphs[i]
            current = _Article(article.group(1), heading)
            children.append(current)
        elif current is None:
            ingress.append(paragraph)
        else:
            current.text.append(paragraph)
        i += 1
    assert any(isinstance(child, _Article) for child in children), \
        "%s ECHR instrument %s has no articles" % (language, protocol or "005")
    for child in children:
        if isinstance(child, _Article):
            child.text = _article_paragraphs(child.text)
    if protocol == "16":
        ingress = _protocol_16_ingress(language, ingress)
    number = ECHR_PROTOCOLS[protocol] if protocol else BASE_NUMBER
    return _Instrument(number, protocol, treaty_uri(number), title, ingress,
                       children)


def _parse_language(language, paragraphs):
    boundaries = [(0, None)]
    boundaries += [(i, number) for i, paragraph in enumerate(paragraphs[1:], 1)
                   if (number := _protocol_number(language, paragraph))]
    instruments = []
    for pos, (start, protocol) in enumerate(boundaries):
        end = boundaries[pos + 1][0] if pos + 1 < len(boundaries) else len(paragraphs)
        chunk = list(paragraphs[start:end])
        # Protocol 16 was pasted from a separately printed instrument; each
        # language repeats the convention title immediately before its heading.
        if len(chunk) > 1 and _base_heading(language, chunk[-1]):
            chunk.pop()
        instruments.append(_parse_instrument(language, chunk, protocol))
    keys = [(instrument.nummer, instrument.protokoll) for instrument in instruments]
    assert len(keys) == len(set(keys)), "%s ECHR instruments are duplicated" % language
    return instruments


def parse_appendix(text):
    """Parse and horizontally align the appendix's three language versions."""
    blocks = _language_blocks(paragraphs(text))
    parsed = {language: _parse_language(language, blocks[language])
              for language in LANGUAGES}
    return align(parsed)
