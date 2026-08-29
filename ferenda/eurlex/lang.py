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

`decision` is the other language-specific text cue: how a court document names
itself on its opening line ("DOMSTOLENS DOM", "FÖRSLAG TILL AVGÖRANDE AV
GENERALADVOKAT", "JUDGMENT OF THE GENERAL COURT"). The courts' own HTML marks
that line with no semantic class, so it is the only handle on a case's title --
see `parse_html.case_title`. The courts are enumerated rather than matched as
"<something>s dom": a judgment's opening prose is full of lines that begin
"Kommissionens beslut … av den …", which a shape test would take for the title.

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
        "signature": r"^Done at\b",
        "decision": ("Judgment of the Court of First Instance",
                     "Judgment of the Civil Service Tribunal",
                     "Judgment of the General Court", "Judgment of the Court",
                     "Order of the Court of First Instance",
                     "Order of the Civil Service Tribunal",
                     "Order of the General Court", "Order of the Court",
                     "Opinion of the Court",
                     "Opinion of Mr Advocate General",
                     "Opinion of Mrs Advocate General",
                     "Opinion of Ms Advocate General",
                     "Opinion of Advocate General",
                     "View of Advocate General",
                     "Report for the Hearing"),
        "decision_name": ("advocate general",),
        "decision_date": r"\b\d{1,2} \w+ \d{4}\b",
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
        "signature": r"^Utfärdat i\b",
        "decision": ("Förslag till avgörande av generaladvokaten",
                     "Förslag till avgörande av generaladvokat",
                     "Generaladvokatens förslag till avgörande",
                     "Förslag till avgörande",
                     "Yttrande av generaladvokat",
                     "Ställningstagande av generaladvokat",
                     "EU-domstolens dom", "EU-domstolens beslut",
                     "Domstolens dom", "Domstolens beslut",
                     "Domstolens yttrande",
                     "Tribunalens dom", "Tribunalens beslut",
                     "Förstainstansrättens dom", "Förstainstansrättens beslut",
                     "Personaldomstolens dom", "Personaldomstolens beslut",
                     "Förhandlingsrapport"),
        "decision_name": ("generaladvokat", "generaladvokaten"),
        "decision_date": r"\b(?:den )?\d{1,2} \w+ \d{4}\b",
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
# number ("Artikel 5", a table-cell marker). The suffix is `[a-z]*`, not `[a-z]?`:
# inserted articles carry Latin ordinals as well as single letters ("Artikel
# 6ter", "Artikel 6sexies" of the Paris Convention), and a one-letter suffix
# truncated those to "6t"/"6s" -- a wrong anchor pointing at no article at all.
_RE_ARTNUM = re.compile(r"^(?:artikel|article)\.?\s+(\d+[a-z]*)|(\d+[a-z]*)\s*$",
                        re.IGNORECASE)
_RE_ROMAN = re.compile(r"[IVXLC]+\.?")
_RE_NUM = re.compile(r"\d+\.?")
# The pre-2000 OJ printed an annex heading once for *every* language edition, on
# one line: "ANEXO I - BILAG I - ANHANG I - ΠΑΡΑΡΤΗΜΑ I - ANNEX I - ANNEXE I -
# ALLEGATO I - BIJLAGE I - ANEXO I - LIITE I - BILAGA I". `Vocab.heading` anchors
# on the *document's* language, so a Swedish act whose strip opens in Spanish
# matched nothing -- the annex stayed body text and the act's last article
# swallowed the signature block and every annex after it (31996L0054's article 4
# ran to 201 452 characters over 4 762 paragraphs).
#
# One segment of the strip is enough to name it, so the test is the shape of the
# whole line: three or more ' - '-joined segments, each of them an annex word
# with an optional numeral. Requiring *every* segment to qualify is what keeps
# ordinary prose out -- a sentence mentioning "bilaga" three times has only one
# segment and never matches (a looser word-count test flagged 1 418 such
# sentences against 244 genuine headings).
_ANNEX_WORDS = ("BILAGA", "BILAG", "ANNEX", "ANNEXE", "ANEXO", "ANEXA", "ANHANG",
                "ALLEGATO", "BIJLAGE", "LIITE", "TILLÄGG", "APPENDIX", "LISA",
                "PIELIKUMS", "PRIEDAS", "PRÍLOHA", "PŘÍLOHA", "PRILOGA",
                "MELLÉKLET", "ZAŁĄCZNIK", "ANNESS")
# The Greek segment is matched by shape, not by word: the pre-2000 pages store
# ΠΑΡΑΡΤΗΜΑ in a legacy encoding that decodes to mojibake, and to more than one
# spelling of it ("ÐÁÑÁÑÔÇÌÁ", "ÐAPAPTHMA"). So: a 6-12 letter run carrying at
# least one non-ASCII letter. The lookahead is what keeps a plain upper-case
# Latin word out, which matters because every segment must qualify -- without it
# a line like "FÖRSTA - ANDRA - TREDJE" would read as an annex heading.
_MOJIBAKE = r"(?=\w*[^\x00-\x7f])[^\W\d_]{6,12}"
_ANNEX_SEG = re.compile(
    r"^(?:%s|%s)(?:\s*[IVXLC]+|\s*\d+|\s*[A-ZÉ])?\.?$"
    % ("|".join(_ANNEX_WORDS), _MOJIBAKE), re.I)
_ANNEX_SPLIT = re.compile(r"\s+-\s+")
# The same strip also occurs with the separators lost, the words run straight
# together ("ANEXOBILAGANHANGΜΠΑΠΤΗΜΑANNEXANNEXEALLEGATOBIJLAGEANEXO",
# 31986L0465). There is no reliable way back to one language's segment -- Danish
# "BILAG" and German "ANHANG" abut as "BILAGANHANG", which also reads as Swedish
# "BILAGA" -- so the run stands as its own heading text. Recognising it is what
# matters: it closes the last article, which is otherwise left swallowing every
# annex in the act.
#
# Recognised by a linear scan, NOT by the obvious regex
# `^(?:(?:WORD|MOJIBAKE)(?:\s*[IVXLC]+|\s*\d+)?){3,}$`: that repetition over a
# 6-12-letter wildcard backtracks exponentially on a long all-letter line that
# almost matches -- 31987R2273's annex glues 1,243 characters of country names
# ("AlbaniaAlbanienAlbanía..."), and one such line cost an hour of CPU and
# read as a hung rebuild. The scan accepts the same language on every shape
# measured (3,510 cases, the glued forms included), narrowing only where the
# regex leaned on `$` before a trailing newline or on the lookahead reaching a
# non-ASCII character that *ends* the run -- both fail toward "not an annex
# strip", the pre-fix reading. At each
# reachable position, every annex word and every 6-12-letter mojibake run
# (with the same some-non-ASCII-ahead condition) advances, an optional
# roman/arabic numeral tail rides each unit, and the whole text must be
# consumed by three or more units.
_ANNEX_WORD_RES = [re.compile(re.escape(w), re.I) for w in _ANNEX_WORDS]
_LETTER = re.compile(r"[^\W\d_]")
_WORDCHAR = re.compile(r"\w")
_NUM_TAIL = re.compile(r"\s*([IVXLC]+|\d+)", re.I)


def _annex_run(text):
    """Whether `text` is a glued multilingual annex strip (three or more
    annex words / mojibake segments run together, each with an optional
    numeral tail, nothing else)."""
    n = len(text)
    # the MOJIBAKE lookahead `(?=\w*[^\x00-\x7f])`: from position i, the
    # unbroken \w run ahead must hold at least one non-ASCII character
    nonascii_ahead = [False] * (n + 1)
    run_has = False
    for i in range(n - 1, -1, -1):
        if _WORDCHAR.match(text, i):
            run_has = (ord(text[i]) > 0x7f) or run_has
        else:
            run_has = False
        nonascii_ahead[i] = run_has
    # units[i] = the most units consumable to reach position i, -1 unreachable
    units = [-1] * (n + 1)
    units[0] = 0
    for i in range(n):
        if units[i] < 0:
            continue
        ends = set()
        for pattern in _ANNEX_WORD_RES:
            m = pattern.match(text, i)
            if m:
                ends.add(m.end())
        if nonascii_ahead[i]:
            for length in range(6, 13):
                if i + length <= n and all(
                        _LETTER.match(text, j) for j in range(i, i + length)):
                    ends.add(i + length)
        for end in ends:
            tails = {end}
            m = _NUM_TAIL.match(text, end)
            if m:
                # the numeral run may be taken at any length, as the regex's
                # backtracking would ("ANNEXIVANNEX" splits IV, but I alone
                # must stay available)
                start = m.start(1)
                tails.update(range(start + 1, m.end(1) + 1))
            for j in tails:
                units[j] = max(units[j], units[i] + 1)
    return units[n] >= 3


def annex_strip(text, annex_words):
    """The document's own annex heading out of a multilingual OJ strip, or None
    if `text` is not one. `annex_words` is the reading language's own words
    (`Vocab.annex_words`); a strip printed before that language joined the Union
    carries no segment for it, and then the whole strip stands as the heading --
    that is what the source itself says."""
    segs = [s.strip() for s in _ANNEX_SPLIT.split(text)]
    if len(segs) >= 3 and all(_ANNEX_SEG.match(s) for s in segs):
        own = [s for s in segs if s.upper().startswith(annex_words)]
        return own[0] if own else text
    return text if len(segs) == 1 and _annex_run(text) else None


# how a rubric run onto an article heading line may open (`Vocab.article_heading`):
# an upper-case letter, a digit, a quote or a separating dash. Deliberately *not*
# a lower-case letter -- that is prose continuing an article reference.
_RUBRIC_OPEN = r"(?:[^\Wa-zà-ÿ\d_]|[\d\"'“«(–—-])"


def article_num(text):
    """The bare article number from a title ('Artikel 5' / 'Article 5' -> '5')."""
    match = _RE_ARTNUM.search(text)
    return (match.group(1) or match.group(2)) if match else None


class Vocab:
    """The compiled structural patterns for one language."""

    def __init__(self, lang):
        spec = VOCAB.get(lang, VOCAB["eng"])
        self.article = re.compile(r"^%s\.?\s+(\d+\w*)" % spec["article"], re.I)
        # `article` only asks that a line *opens* with an article designation,
        # which is what a table marker cell needs. A class-less body line needs
        # more: an amending act's prose opens the same way ("Artikel 9.2 skall
        # ersättas med följande:", "Artikel 8 skall utgå.") and, taken for a
        # heading, mints a phantom article whose number is the *amended* act's --
        # stealing the body of the real article it interrupts, which is then left
        # empty. A heading is the designation alone, or with its rubric run onto
        # the same line ("Artikel 1 Räckvidd"), so the discriminator is what
        # follows the number: a pinpoint ("9.2") or a lower-case continuation is
        # prose, never a rubric. The keyword and the bis/ter letter are matched
        # case-insensitively; the rubric test must not be, so the flag is scoped
        # rather than global.
        self.article_heading = re.compile(
            r"^(?i:%s)\.?\s+\d+(?i:[a-z])*(?:\s+%s.*)?$"
            % (spec["article"], _RUBRIC_OPEN))
        self.heading = re.compile(r"^(?:%s)\b" % "|".join(spec["headings"]), re.I)
        self.annex = re.compile(r"^(?:%s)\b" % "|".join(spec["annex"]), re.I)
        # the bare words, for `annex_strip`'s segment pick (a prefix test, not a
        # pattern match -- the strip's segments are already isolated)
        self.annex_words = tuple(spec["annex"])
        self.enacting = re.compile(spec["enacting"], re.I)  # ty: ignore[no-matching-overload]  # VOCAB values are str|list; enacting is always str
        # the closing formula of the enacting terms ("Done at Brussels, 14 July
        # 1986." / "Utfärdat i Bryssel den 30 juli 1996."). The class-ful OJ HTML
        # marks it `signatory`; the class-less legacy HTML marks nothing, so the
        # signature stayed an ordinary paragraph -- and since `structure.nest`
        # closes the open article on a `signature` block and on nothing else
        # here, the act's last article went on swallowing the signature, the
        # footnotes and every annex after them (31986L0465's article 3 ran to
        # 193 710 characters over 6 143 paragraphs).
        self.signature = re.compile(spec["signature"], re.I)  # ty: ignore[no-matching-overload]  # VOCAB values are str|list; signature is always str
        # how a court document opens, in the *canonical spelling* ("Domstolens
        # dom", "Opinion of Advocate General"), longest phrase first so
        # `decision_opener` prefers "Förslag till avgörande av generaladvokat"
        # over the "Förslag till avgörande" that is also in the list. Storing
        # the spelling rather than a pattern is what lets the title the courts
        # set in caps be rendered back in the Court's own case -- see
        # `parse_html.case_title`.
        self._decision = tuple(sorted(spec["decision"], key=len, reverse=True))
        # the openers a *name* follows -- the ones ending in the advocate-general
        # word. Only there is the rest of the title a person, so only there may
        # it be recased; a judgment's title continues into the parties, where
        # the same rule would have written the Greek utility DEI as "Dei".
        self._decision_name = tuple(spec["decision_name"])
        # the date phrase that closes such a title ("den 11 november 1997",
        # "17 September 2019")
        self.decision_date = re.compile(spec["decision_date"], re.I)  # ty: ignore[no-matching-overload]  # VOCAB values are str|list; decision_date is always str
        # the framing line that opens the recital list ("… och med beaktande av
        # följande:" / "Whereas:"); it is the *tail* of its line, because a
        # Swedish act runs it onto the last visa
        self.recital_intro = re.compile(
            r"(?:%s)\s*[:.,]?$" % "|".join(spec["recital_intro"]), re.I)
        self._visa = tuple(spec["visa"])
        self._recital = tuple(spec["recital"])

    def decision_opener(self, text):
        """The canonical spelling of the court-title phrase `text` opens with, or
        None -- "FÖRSLAG TILL AVGÖRANDE AV GENERALADVOKAT RIMVYDAS NORKUS" ->
        "Förslag till avgörande av generaladvokat". The match is
        case-insensitive because the courts set the same phrase in caps, in
        title case and in sentence case across the decades."""
        low = text.lower()
        return next((p for p in self._decision if low.startswith(p.lower())),
                    None)

    def names_follow(self, opener):
        """Whether what follows this opener is a person's name -- true for the
        advocate-general openers ("Förslag till avgörande av generaladvokat
        RIMVYDAS NORKUS"), false for a court's, whose title runs on into the
        parties."""
        return opener.lower().endswith(self._decision_name)

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
