"""Localized structural vocabulary for the non-Formex EU parsers (html, pdf).

Formex marks structure with tags, so its parser needs no language knowledge. The
HTML fallback and the PDF parser instead infer structure from text -- "Article N"
/ "Artikel N", "TITLE I" / "AVDELNING I", the enacting formula, the visa/recital
framing -- and every one of those is language-specific. Add a language by adding
a VOCAB entry; an unknown language falls back to English.

Out of scope here: reference *syntax* ("article 3(4)" vs "artikel 3.4"). That is
the citation engine's concern (lib.lagrum) -- the parsers only emit text, which
the engine then scans, so reference localization lives there, not here.
"""

import re

VOCAB = {
    "eng": {
        "article": "Article",
        "headings": ("TITLE", "CHAPTER", "PART", "SECTION", "SUBSECTION",
                     "ANNEX", "APPENDIX"),
        "enacting": r"HA(?:S|VE) (?:ADOPTED|DECIDED|DRAWN UP|AGREED)",
        "visa": ("having regard", "having seen"),
        "recital": ("whereas",),
    },
    "swe": {
        "article": "Artikel",
        "headings": ("AVDELNING", "KAPITEL", "DEL", "AVSNITT", "UNDERAVSNITT",
                     "BILAGA", "TILLÄGG"),
        "enacting": r"HAR (?:ANTAGIT|UTFÄRDAT|BESLUTAT|FÖRESKRIVIT|FATTAT)",
        "visa": ("med beaktande av",),
        "recital": ("av följande skäl", "med hänsyn till"),
    },
}

# language-neutral structural markers (parenthesised numbers/letters, numerals)
RE_RECITAL = re.compile(r"^\(\s*(\d+)\s*\)$")
RE_POINT = re.compile(r"^\(?\s*([a-z0-9]{1,4})\s*[.)]$", re.IGNORECASE)
_RE_ARTNUM = re.compile(r"(\d+[a-z]?)\s*$")
_RE_ROMAN = re.compile(r"[IVXLC]+\.?")
_RE_NUM = re.compile(r"\d+\.?")


def article_num(text):
    """The bare article number from a title ('Artikel 5' / 'Article 5' -> '5')."""
    match = _RE_ARTNUM.search(text)
    return match.group(1) if match else None


class Vocab:
    """The compiled structural patterns for one language."""

    def __init__(self, lang):
        spec = VOCAB.get(lang, VOCAB["eng"])
        self.article = re.compile(r"^%s\.?\s+(\d+\w*)" % spec["article"], re.I)
        self.heading = re.compile(r"^(?:%s)\b" % "|".join(spec["headings"]), re.I)
        self.enacting = re.compile(spec["enacting"], re.I)
        self._visa = tuple(spec["visa"])
        self._recital = tuple(spec["recital"])

    def is_marker(self, text):
        """A short left-cell that signals a structural table row (heading /
        recital / point), as opposed to a data cell."""
        return bool(text) and len(text) <= 16 and bool(
            RE_RECITAL.match(text) or self.article.match(text)
            or self.heading.match(text) or RE_POINT.match(text)
            or _RE_ROMAN.fullmatch(text) or _RE_NUM.fullmatch(text))

    def preamble_kind(self, text):
        """Classify a preamble line by its framing words: 'citation' (a visa),
        'recital', or 'preamble' (default)."""
        low = text.lower()
        if low.startswith(self._visa):
            return "citation"
        if low.startswith(self._recital) or low.startswith("whereas"):
            return "recital"
        return "preamble"


def vocab(lang):
    return Vocab(lang)
