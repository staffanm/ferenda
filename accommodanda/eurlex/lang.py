"""Localized structural vocabulary for the EU parsers, and for the one consumer
that must read structure back out of the text (annotate's annex trim).

Formex marks structure with tags, so its parser needs no language knowledge. The
HTML fallback and the PDF parser instead infer structure from text -- "Article N"
/ "Artikel N", "TITLE I" / "AVDELNING I", the enacting formula, the visa/recital
framing -- and every one of those is language-specific. Add a language by adding
a VOCAB entry; an unknown language falls back to English.

`enacting` is the formula that *closes* the preamble and opens the enacting
terms, which is not the same sentence in every language: English closes with
"HAS ADOPTED THIS DIRECTIVE:", while a Swedish act opens with "… HAR ANTAGIT
DETTA DIREKTIV" (before the visas, as Formex's PREAMBLE.INIT) and closes with
"HÄRIGENOM FÖRESKRIVS FÖLJANDE.". Keying Swedish on the opener ended the
preamble at its first line, so every visa and recital of every non-Formex
Swedish act was parsed as ordinary body text.

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
        "annex": ("ANNEX", "APPENDIX"),
        "enacting": r"HA(?:S|VE) (?:ADOPTED|DECIDED|DRAWN UP|AGREED)",
        "visa": ("having regard", "having seen"),
        "recital": ("whereas",),
        "recital_intro": ("whereas",),
    },
    "swe": {
        "article": "Artikel",
        "headings": ("AVDELNING", "KAPITEL", "DEL", "AVSNITT", "UNDERAVSNITT",
                     "BILAGA", "TILLÄGG"),
        "annex": ("BILAGA", "TILLÄGG"),
        "enacting": r"HÄR(?:IGENOM|MED) (?:FÖRESKRIVS|BESLUTAS|FATTAS|ANTAS)",
        "visa": ("med beaktande av",),
        "recital": ("av följande skäl", "med hänsyn till"),
        "recital_intro": ("med beaktande av följande", "av följande skäl"),
    },
}

# language-neutral structural markers (parenthesised numbers/letters, numerals)
RE_RECITAL = re.compile(r"^\(\s*(\d+)\s*\)$")
# a recital marker run into the start of its own text, as the pre-2000 "Avis
# juridique important" HTML writes it -- no marker cell to separate them. All
# three forms occur in the corpus: "(1) ", "1) " (31995L0046) and "1. "
# (31999L0037). Numbering is only trusted in sequence (see parse_html), so a
# recital that merely opens with a number is not mistaken for a marked one.
RE_RECITAL_MARKER = re.compile(r"^\(?(\d{1,3})\s*[).]\s+(\S.*)$")
RE_POINT = re.compile(r"^\(?\s*([a-z0-9]{1,4})\s*[.)]$", re.IGNORECASE)
# the number right after the article keyword ("Artikel 1 Räckvidd" -- the
# legacy txt_te HTML runs the heading into the marker line), or a bare trailing
# number ("Artikel 5", a table-cell marker)
_RE_ARTNUM = re.compile(r"^(?:artikel|article)\.?\s+(\d+[a-z]?)|(\d+[a-z]?)\s*$",
                        re.IGNORECASE)
_RE_ROMAN = re.compile(r"[IVXLC]+\.?")
_RE_NUM = re.compile(r"\d+\.?")


def article_num(text):
    """The bare article number from a title ('Artikel 5' / 'Article 5' -> '5')."""
    match = _RE_ARTNUM.search(text)
    return (match.group(1) or match.group(2)) if match else None


class Vocab:
    """The compiled structural patterns for one language."""

    def __init__(self, lang):
        spec = VOCAB.get(lang, VOCAB["eng"])
        self.article = re.compile(r"^%s\.?\s+(\d+\w*)" % spec["article"], re.I)
        self.heading = re.compile(r"^(?:%s)\b" % "|".join(spec["headings"]), re.I)
        self.annex = re.compile(r"^(?:%s)\b" % "|".join(spec["annex"]), re.I)
        self.enacting = re.compile(spec["enacting"], re.I)  # ty: ignore[no-matching-overload]  # VOCAB values are str|list; enacting is always str
        # the framing line that opens the recital list ("… och med beaktande av
        # följande:" / "Whereas:"); it is the *tail* of its line, because a
        # Swedish act runs it onto the last visa
        self.recital_intro = re.compile(
            r"(?:%s)\s*[:.,]?$" % "|".join(spec["recital_intro"]), re.I)
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
