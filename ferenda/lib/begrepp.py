"""Begreppsdefinitioner -- detecting defined terms in författningstext and
minting nothing itself: the caller turns each term into a ``dcterms:subject``
inline link (``Ref`` with ``kind="term"``). A faithful port of the old
``sfs.py`` ``find_definitions`` heuristics, off the framework, moved here from
``sfs/begrepp.py`` when föreskrift became the second source to mark
definitions (rule:second-use-goes-to-lib).

A *paragraf* enters a definition **mode** when its stycken announce one:

  normal           "I denna lag avses med ..."   (a term-list paragraf)
  brottsrubricering "... döms för mord till ..."  (a criminal offence)
  parantes          "... dödas (dödning)."        (a parenthesised coinage)
  loptext           "Med detaljhandel avses ..."  (an inline definition)

Each list item and table row in that paragraf yields at most one defined term;
a stycke can yield several, since one sentence can coin two ("... bedriver
säkerhetskänslig verksamhet (verksamhetsutövare) ska utreda behovet av
säkerhetsskydd (säkerhetsskyddsanalys).").

The announcement phrasing and the noise profile differ per source -- a
föreskrift writes "I dessa föreskrifter avses med", and its PDF-extracted
text makes the coinage modes noisy -- so each source builds a :class:`Rules`
with its own scope phrases and its enabled mode set. The term-extraction
machinery below the class is shared."""

import re

from . import util

MODES = frozenset({"normal", "brottsrubricering", "parantes", "loptext"})

# --- triggers shared by every source (mode-gated per Rules) ---
re_brottsdef = re.compile(
    r'\b(döms|dömes)(?: han)?(?:,[\w\xa7 ]+,)? för ([\w ]{3,50}) till '
    r'(böter|fängelse)', re.UNICODE).search
re_brottsdef_alt = re.compile(
    r'[Ff]ör ([\w ]{3,50}) (döms|dömas) till (böter|fängelse)', re.UNICODE).search
# A parenthesised coinage names the thing just introduced. The pattern used to
# require the parenthesis to close the sentence (`\)\.`), so a sentence that
# coins two terms yielded only the second: "... bedriver säkerhetskänslig
# verksamhet (verksamhetsutövare) ska utreda behovet av säkerhetsskydd
# (säkerhetsskyddsanalys)." made säkerhetsskyddsanalys a defined term and
# verksamhetsutövare not. Both are legaldefinitioner.
#
# A parenthesis away from the sentence end is only a coinage when it *reads*
# like one, because the same brackets carry abbreviations and numbers. Measured
# over 1,500 acts, accepting every parenthesis mid-sentence minted 358 terms of
# which the bulk was noise -- "EEG", "EES", "COTIF", "nr 570", "1993" -- so a
# mid-sentence candidate must be a lowercase noun phrase with no digits.
# Swedish drafting writes a coined term that way ("(verksamhetsutövare)",
# "(dödning)") and writes an abbreviation in capitals.
#
# Two knock-on widenings, both inside the measurement below (916 new terms and
# 0 lost over 1,500 acts, against the previous code): a mid-sentence coinage now
# opens `parantes` mode for its whole paragraf, so a *sentence-final*
# parenthesis in a neighbouring stycke is read where the paragraf previously had
# no mode at all; and a stycke yields every sentence-final parenthesis it
# carries, where the old single `search` stopped at the first.
re_parantesdef = re.compile(r'\(([\w ]{3,50})\)\.', re.UNICODE).search
re_parantesdefs = re.compile(r'\(([\w ]{3,50})\)', re.UNICODE).finditer
# "Med detaljhandel avses i denna lag ..." -- and, just as often, without the
# "i denna lag": säkerhetsskyddslagen 1 kap. 2 § writes "Med
# säkerhetsskyddsklassificerade uppgifter avses uppgifter som rör
# säkerhetskänslig verksamhet ...", and drafting also says "i detta kapitel",
# "i det följande" or "vid tillämpning av 5 §". Requiring the one tail form lost
# 3 558 definitions in 1 427 acts, so the tail is not required at all.
#
# What that lets in is one shape: "Med" opening an adverbial rather than a
# definiendum ("Med undantag av de fordon som avses i 6 kap. 3 § ...", "Med
# hjälp av ett underhållssystem som avses i ..."). Those two heads are excluded
# by name -- 12 of the 3 558 -- rather than by a rule about prepositions, because
# "stöd till start av näringsverksamhet" is a defined term and reads the same.
re_loptextdef = re.compile(
    r'^Med (?!(?:undantag|hjälp) (?:av|för)\b)([\w ]{3,50}?)'
    r' (?:ska(?:ll)? )?(?:avses|förstås)\b',
    re.UNICODE).search
RE_DIGIT = re.compile(r"\d")
RE_LETTER = re.compile(r"[^\W\d_]")
# the roman numerals a list actually uses, as a set rather than as an alphabet:
# `^[ivxlcdm]+$` matched "civil", "mild" and "dill" too, dropping real terms to
# catch a marker
LIST_MARKERS = frozenset(("i", "ii", "iii", "iv", "v", "vi", "vii", "viii",
                          "ix", "x", "xi", "xii"))


def _is_coinage(paren):
    """Whether a parenthesis away from the sentence end reads as a coined term
    rather than an abbreviation, a number or a list marker. "(iii)" opening a
    lettered list is lowercase and digit-free, so it needs naming separately."""
    return (paren[:1].islower() and not RE_DIGIT.search(paren)
            and paren not in LIST_MARKERS)


def _has_coinage(text):
    """Whether any parenthesis in `text` reads as a coined term -- so a stycke
    that coins a term without closing a sentence on one still announces the
    mode. Without this a paragraf whose only definition is mid-sentence is never
    read at all."""
    return any(_is_coinage(m.group(1).strip()) for m in re_parantesdefs(text))


# Coined terms that are not concepts -- checked for every kind, not only the
# parenthesis path. Each is the act naming its own actor or its own drafting, so
# a page collecting "everywhere this is defined" would merge unrelated things
# under one heading. Kept as an explicit
# list with its evidence rather than a rule: "myndigheten" is noise and
# "spotmarknaden" is a real term, and no shape test separates them.
#
# A law short-name coined in parentheses ("marknadsmissbruksförordning") IS a
# term and stays -- it is what the act is called afterwards.
NOT_A_CONCEPT = frozenset({
    "myndigheten",         # "prövas av Verket för innovationssystem (myndigheten)"
    "tillsynsmyndigheten",  # each act's own supervisor, not a shared concept
    "motsvarande",         # "anställning hos allmän försäkringskassa (motsvarande)"
    "böte",                # a 1734 års lag verb form, not a term
    "institutet",          # the act's own shorthand for the body it just named
    "publ",                # "(publ)" marks a publikt aktiebolag, not a definition
    "chefen",              # "Med chefen förstås vid tillämpning av 5 §
                           # verksstadgan rådets ordförande" -- each
                           # myndighetsinstruktion naming its own head, 62 of them
    # the parenthesis that gives an act its short *title*, not a term: 170 acts
    # write "kompletterar Europaparlamentets och rådets förordning (EU) 2016/679
    # ... (allmän dataskyddsförordning)", and a concept page collecting them
    # lists 170 acts saying nothing about a concept
    "allmän dataskyddsförordning",
})

# --- helpers for term extraction ---
re_sfsid = re.compile(r'\((\d{4}:\d+)\)').search          # old re_SearchSfsId
re_change_note = re.compile(r'(Lag|Förordning) \(\d{4}:\d+\)\.?$')
re_list_prefixes = (re.compile(r'^(\-\-?|\x96) '),         # bullet
                    re.compile(r'^(\d+ ?\w?)\. '),        # dotted number
                    re.compile(r'^(\w)\) '))              # letter list

MAX_TERM_LEN = 68    # "Valutaväxling, betalningsöverföring och annan ..." cutoff

# a defined term never contains formula/path symbols nor leads with a preposition
# -- the two ways the heuristics mis-bound a *real* term (not noise): a colon-list
# definition sweeping a formula prefix into the span ("*/k/ utjämningsbelopp"), and
# a parenthetical clarifier captured instead of its head ("Behandling (av
# personuppgifter)" -> "av personuppgifter").
RE_FORMULA_TOKEN = re.compile(r"[*/=]")
PREP_RE = re.compile(
    r"(av|i|för|om|till|på|med|vid|mot|enligt|under|över|genom|från|åt|hos"
    r"|inom|utan|per|à)\b", re.IGNORECASE)


def _strip_formula_prefix(term):
    """Drop leading formula/path tokens a colon-list definition swept into the
    term span: '*/k/ utjämningsbelopp' -> 'utjämningsbelopp'."""
    words = term.split()
    while len(words) > 1 and RE_FORMULA_TOKEN.search(words[0]):
        words.pop(0)
    return " ".join(words)


# What a löptext definiendum carries that is not part of the term: the article
# in front of it ("Med ett träds grundyta avses ...", 186 of them) and the
# qualifier behind it naming where the definition applies ("Med dotterbolag
# enligt första stycket 3 avses ...", 94). Both would mint a begrepp page under
# a name no one looks up. Only this mode is trimmed -- a colon list and a
# parenthesised coinage write the bare term already.
RE_TERM_ARTICLE = re.compile(r"^(?:en|ett|den|det|de)\s+", re.I)
RE_TERM_QUALIFIER = re.compile(
    r"\s+(?:enligt|i denna|i detta|i första|i andra|i tredje|i fjärde)\s+.*$",
    re.I)


def _loptext_term(term):
    """A löptext definiendum with its article and its scope qualifier removed."""
    return RE_TERM_QUALIFIER.sub("", RE_TERM_ARTICLE.sub("", term)).strip()


class Rules:
    """One source's definition-detection rules: which scope phrases announce a
    term-list paragraf ("I denna lag ...", "I dessa föreskrifter ...") and
    which of the four modes the source's text supports at all. SFS enables all
    four; föreskrift enables only the announced modes ("normal", "loptext"),
    because the coinage modes were measured noisy on PDF-extracted text."""

    def __init__(self, scopes, modes=MODES):
        assert set(modes) <= MODES, "unknown mode in %r" % (modes,)
        self.modes = frozenset(modes)
        self.trigger = re.compile(
            r'^I (%s) (avses med|betyder|används följande)'
            % "|".join(re.escape(s) for s in scopes)).match

    def paragraf_mode(self, stycke_texts, in_appendix=False):
        """The definition mode announced by a paragraf's stycken (the opening
        of the first stycke, with a trigger re-check across all of them), or
        None. Order mirrors the old sequential overwrite -- a later "I denna
        lag avses med" upgrades any earlier guess to "normal".

        `in_appendix` is True inside a bilaga, a konventionsbilaga or
        övergångsbestämmelser, where a mid-sentence parenthesis does not
        announce a definition: an annex is a list of things, not drafting. See
        `defined_terms` for what it costs to ignore."""
        first = stycke_texts[0] if stycke_texts else ""
        mode = None
        if self.trigger(first):
            mode = "normal"
        if "brottsrubricering" in self.modes and (
                re_brottsdef(first) or re_brottsdef_alt(first)):
            mode = "brottsrubricering"
        if "parantes" in self.modes and re_parantesdef(first):
            mode = "parantes"
        if "loptext" in self.modes and re_loptextdef(first):
            mode = "loptext"
        if any(self.trigger(t) for t in stycke_texts):
            mode = "normal"
        # a paragraf that coins a term mid-sentence announces none of the four
        # triggers above, so it used to yield no terms at all. Added strictly
        # last and only when nothing else matched: letting it override an
        # announced mode turned term-list paragrafs into parentes ones and lost
        # their colon-list terms.
        if (mode is None and "parantes" in self.modes and not in_appendix
                and any(_has_coinage(t) for t in stycke_texts)):
            mode = "parantes"
        return mode

    def _stycke_terms(self, text, mode, in_appendix=False):
        """Every term this stycke defines: the sentence-final coinages first,
        then the mid-sentence ones -- not document order, which matters because
        `nf.inline` gives each span to the terms already taken.

        Precedence is the old sequential overwrite: a parenthesised coinage
        beats a löptext definition, which beats a brottsrubricering, which
        beats a colon list. Only the parenthesis case can yield more than one
        term."""
        term = None
        # case 1: "antisladdsystem: ett tekniskt stödsystem" -- only in normal
        # mode, and not on the announcing stycke itself. The delimiter is
        # usually ":", but an embedded SFS number's colon or a " - " dash can
        # mislead, so disambiguate.
        if mode == "normal" and not self.trigger(text):
            delimiter = ":"
            if " - " in text:
                if ":" in text and text.index(":") < text.index(" - "):
                    delimiter = ":"
                else:
                    delimiter = " - "
            m = re_sfsid(text)
            if delimiter == ":" and m and m.start() < text.index(":"):
                delimiter = " "
            if delimiter in text:
                term = text.split(delimiter)[0]
        # cases 2-5: brottsrubricering / löptext, checked whenever the source
        # enables the mode at all -- matching the old unconditional check
        checks = []
        if "brottsrubricering" in self.modes:
            checks += [(re_brottsdef, 2), (re_brottsdef_alt, 1)]
        if "loptext" in self.modes:
            checks.append((re_loptextdef, 1))
        for rx, group in checks:
            m = rx(text)
            if m:
                term = _loptext_term(m.group(group)) if rx is re_loptextdef \
                    else m.group(group)
        # parentes: a coinage ("dödas (dödning)") names the parenthetical, but a
        # prepositional *clarifier* ("Behandling (av personuppgifter)") names
        # the head noun before it -- not the parenthetical
        final, mid = [], []
        if "parantes" in self.modes:
            for m in re_parantesdefs(text):
                found = _paren_term(text, m)
                if not found:
                    continue
                # a parenthesis closing the sentence is a coinage whatever its
                # shape -- that is the long-standing rule, and it outranks a
                # colon-list term the same way it always did. One away from the
                # end has to look like a term.
                if text[m.end():m.end() + 1] == ".":
                    final.append(found)
                elif not in_appendix and _is_coinage(m.group(1).strip()):
                    mid.append(found)
        terms = final or ([term] if term else [])
        return terms + [t for t in mid if t not in terms]

    def defined_terms(self, text, mode, kind, in_appendix=False):
        """The terms defined by this node (possibly empty), sentence-final
        coinages before mid-sentence ones.
        `kind` is 'stycke', 'listelement' or 'tabellrad'; for a table row
        `text` is the first cell. Only a stycke can define more than one term
        -- a list item and a table row each name exactly one.

        `in_appendix` turns the mid-sentence coinage rule off. An annex is a
        list of things rather than drafting, and the same brackets there hold
        table cells and the English half of a dubbelbeskattningsavtal: of 1,238
        mid-sentence candidates over 1,500 acts, the 198 inside a bilaga, a
        table or övergångsbestämmelser held nearly all of the worst noise
        ("ton", "stat nr", "réseau", "alone or together with the whole
        enterprise"). A parenthesis that closes a sentence still counts there,
        exactly as it always has."""
        if kind == "tabellrad":
            # only the first cell can be a term, and not the column header; a
            # cell written "Naturvårdsbränning:" carries its delimiter, which
            # is not part of the term
            text = text.rstrip(": ")
            terms = ([text] if text not in ("Beteckning", "Begrepp")
                     and not re_change_note.search(text) else [])
        elif kind == "listelement":
            for rx in re_list_prefixes:
                text = rx.sub('', text)
            terms = [text.split(":")[0]]
        else:  # stycke
            terms = self._stycke_terms(text, mode, in_appendix)
        kept = []
        for term in terms:
            term = _strip_formula_prefix(util.normalize_space(term))
            # a term that still leads with a preposition is a mis-capture, not
            # a concept -- drop it rather than mint a bogus begrepp page. A
            # term with no letter ("2019:1", a numeric first column in a
            # föreskrift table) is a designation, not a term -- it crashed
            # relate's sentence-finder on elsakfs/2018:1 (2026-08-28)
            if (not PREP_RE.match(term) and 0 < len(term) < MAX_TERM_LEN
                    and RE_LETTER.search(term)
                    and term.lower() not in NOT_A_CONCEPT):
                kept.append(term)
        return kept


def _paren_term(text, m):
    """The term one parenthesis names, or None when it names nothing."""
    paren = m.group(1).strip()
    if not PREP_RE.match(paren):
        return paren
    head = text[:m.start()].strip().split()
    return ("%s %s" % (head[-1], paren)) if head else None
