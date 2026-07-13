"""Shared typed alignment for multilingual convention appendices.

The individual convention modules own their printed headings and source quirks;
this module owns only the common intermediate shape and the invariant that the
same instruments, divisions, articles and legal paragraphs line up across the
English, French and Swedish runs.
"""

import re
from dataclasses import dataclass, field

from ..lib.util import normalize_space
from .model import (
    Konventionsartikel,
    Konventionsavdelning,
    Konventionsbilaga,
    Konventionsinstrument,
    Konventionsstycke,
)

LANGUAGES = ("en", "fr", "sv")
RE_APPENDIX = re.compile(r"\n[ \t]*\nBilaga[ \t]*\n[ \t]*\n")
RE_LEGAL_MARKER = re.compile(
    r"\s+(?=(?:\((?:[a-z]|ii|iii|iv|vi|vii|viii|ix|x)\)|[a-z]\))\s)")


@dataclass
class Article:
    ordinal: str
    rubrik: str
    text: list[str] = field(default_factory=list)


@dataclass
class Division:
    ordinal: str
    rubrik: str


@dataclass
class Instrument:
    nummer: str
    protokoll: str | None
    uri: str | None
    rubrik: str
    ingress: list[str] = field(default_factory=list)
    children: list[Division | Article] = field(default_factory=list)


def split_appendix(text, basefile):
    """Return ordinary statute text and the body after its sole Bilaga heading."""
    parts = RE_APPENDIX.split(text.replace("\r", ""))
    assert len(parts) == 2, \
        "SFS %s must contain exactly one Bilaga heading" % basefile
    return parts[0].rstrip() + "\n", parts[1].strip()


def paragraphs(text):
    return [normalize_space(part) for part in re.split(r"\n\s*\n", text)
            if part.strip()]


def legal_paragraphs(items):
    """Split source paragraphs that contain several numbered/list provisions."""
    return [part for item in items for part in RE_LEGAL_MARKER.split(item)]


def language_blocks(items, matches, label):
    """Slice the sequential per-language runs out of a flat paragraph list.

    ``matches(language, item)`` decides where each language's run begins; the
    runs must appear in ``LANGUAGES`` order. Each block runs from its own start
    heading up to the next language's. The starts are strictly increasing by
    construction (each search resumes past the previous one), so order needs no
    separate check.
    """
    starts = []
    after = 0
    for language in LANGUAGES:
        start = next((i for i in range(after, len(items))
                      if matches(language, items[i])), None)
        assert start is not None, "missing %s %s language version" % (language, label)
        starts.append(start)
        after = start + 1
    ends = starts[1:] + [len(items)]
    return {language: items[start:end]
            for language, start, end in zip(LANGUAGES, starts, ends, strict=True)}


def align_paragraphs(by_language, context):
    counts = {language: len(by_language[language]) for language in LANGUAGES}
    assert len(set(counts.values())) == 1, \
        "%s paragraphs do not align: %r" % (context, counts)
    return [Konventionsstycke(dict(zip(LANGUAGES, row, strict=True)))
            for row in zip(*(by_language[language]
                             for language in LANGUAGES), strict=True)]


def _markers(instrument):
    return [("division" if isinstance(child, Division) else "article",
             child.ordinal)
            for child in instrument.children]


def align(parsed):
    """Merge three parsed language runs into the source model."""
    keys = [(item.nummer, item.protokoll) for item in parsed["en"]]
    for language in LANGUAGES[1:]:
        assert [(item.nummer, item.protokoll)
                for item in parsed[language]] == keys, \
            "%s convention instruments do not align with English" % language

    result = []
    by_language = {
        language: {(item.nummer, item.protokoll): item
                   for item in parsed[language]}
        for language in LANGUAGES
    }
    for key in keys:
        versions = {language: by_language[language][key]
                    for language in LANGUAGES}
        markers = _markers(versions["en"])
        for language in LANGUAGES[1:]:
            assert _markers(versions[language]) == markers, \
                "%s convention provisions in %s do not align" % (
                    key[0], language)
        children = []
        for pos, (kind, ordinal) in enumerate(markers):
            aligned = {language: versions[language].children[pos]
                       for language in LANGUAGES}
            if kind == "division":
                children.append(Konventionsavdelning(
                    ordinal, {language: aligned[language].rubrik
                              for language in LANGUAGES}))
            else:
                children.append(Konventionsartikel(
                    ordinal,
                    {language: aligned[language].rubrik
                     for language in LANGUAGES},
                    align_paragraphs(
                        {language: aligned[language].text
                         for language in LANGUAGES},
                        "%s article %s" % (key[0], ordinal))))
        result.append(Konventionsinstrument(
            nummer=key[0],
            protokoll=key[1],
            uri=versions["en"].uri,
            rubriker={language: versions[language].rubrik
                      for language in LANGUAGES},
            ingresser=align_paragraphs(
                {language: versions[language].ingress
                 for language in LANGUAGES},
                "%s ingress" % key[0]),
            children=children))
    return Konventionsbilaga(result)
