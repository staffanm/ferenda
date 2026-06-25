"""Begreppsdefinitioner -- detecting defined terms in SFS text and minting the
``dcterms:subject`` link to each term's begrepp page. A faithful port of the old
``sfs.py`` ``find_definitions`` + ``_term_to_subject`` heuristics, off the
framework.

A *paragraf* enters a definition **mode** when its stycken announce one:

  normal           "I denna lag avses med ..."   (a term-list paragraf)
  brottsrubricering "... döms för mord till ..."  (a criminal offence)
  parantes          "... dödas (dödning)."        (a parenthesised coinage)
  loptext           "Med detaljhandel avses ..."  (an inline definition)

Each stycke / list item / table row in that paragraf then yields at most one
defined term, which becomes a ``dcterms:subject`` inline link over the term's
own span (``Ref`` with ``kind="term"``)."""

import re

from ..lib import util

# --- triggers: the opening of a paragraf (or stycke) announcing a mode ---
re_definitions = re.compile(
    r'^I (lagen|förordningen|balken|denna lag|denna förordning|denna balk'
    r'|denna paragraf|detta kapitel) (avses med|betyder|används följande)').match
re_brottsdef = re.compile(
    r'\b(döms|dömes)(?: han)?(?:,[\w\xa7 ]+,)? för ([\w ]{3,50}) till '
    r'(böter|fängelse)', re.UNICODE).search
re_brottsdef_alt = re.compile(
    r'[Ff]ör ([\w ]{3,50}) (döms|dömas) till (böter|fängelse)', re.UNICODE).search
re_parantesdef = re.compile(r'\(([\w ]{3,50})\)\.', re.UNICODE).search
re_loptextdef = re.compile(
    r'^Med ([\w ]{3,50}) (?:avses|förstås) i denna (förordning|lag|balk)',
    re.UNICODE).search

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


def term_to_subject(term):
    """The begrepp URI for a term (the old _term_to_subject): capitalised,
    spaces to underscores, under /begrepp/."""
    capitalized = term[0].upper() + term[1:]
    return 'https://lagen.nu/begrepp/%s' % capitalized.replace(' ', '_')


def paragraf_mode(stycke_texts):
    """The definition mode announced by a paragraf's stycken (the opening of
    the first stycke, with a re_definitions re-check across all of them), or
    None. Order mirrors the old sequential overwrite -- a later "I denna lag
    avses med" upgrades any earlier guess to "normal"."""
    first = stycke_texts[0] if stycke_texts else ""
    mode = None
    if re_definitions(first):
        mode = "normal"
    if re_brottsdef(first) or re_brottsdef_alt(first):
        mode = "brottsrubricering"
    if re_parantesdef(first):
        mode = "parantes"
    if re_loptextdef(first):
        mode = "loptext"
    if any(re_definitions(t) for t in stycke_texts):
        mode = "normal"
    return mode


def _stycke_term(text, mode):
    term = None
    # case 1: "antisladdsystem: ett tekniskt stödsystem" -- only in normal mode,
    # and not on the announcing stycke itself. The delimiter is usually ":", but
    # an embedded SFS number's colon or a " - " dash can mislead, so disambiguate.
    if mode == "normal" and not re_definitions(text):
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
    # cases 2-5: brottsrubricering / löptext, checked unconditionally
    for rx, group in ((re_brottsdef, 2), (re_brottsdef_alt, 1), (re_loptextdef, 1)):
        m = rx(text)
        if m:
            term = m.group(group)
    # parentes: a coinage ("dödas (dödning)") names the parenthetical, but a
    # prepositional *clarifier* ("Behandling (av personuppgifter)") names the head
    # noun before it -- not the parenthetical
    m = re_parantesdef(text)
    if m:
        paren = m.group(1).strip()
        if PREP_RE.match(paren):
            head = text[:m.start()].strip().split()
            term = ("%s %s" % (head[-1], paren)) if head else term
        else:
            term = paren
    return term


def defined_term(text, mode, kind):
    """The term defined by this node, or None. `kind` is 'stycke',
    'listelement' or 'tabellrad'; for a table row `text` is the first cell."""
    if kind == "tabellrad":
        # only the first cell can be a term, and not the column header
        term = (text if text not in ("Beteckning", "Begrepp")
                and not re_change_note.search(text) else None)
    elif kind == "listelement":
        for rx in re_list_prefixes:
            text = rx.sub('', text)
        term = text.split(":")[0]
    else:  # stycke
        term = _stycke_term(text, mode)
    if term:
        term = _strip_formula_prefix(util.normalize_space(term))
        # a term that still leads with a preposition is a mis-capture, not a
        # concept -- drop it rather than mint a bogus begrepp page
        if not PREP_RE.match(term) and 0 < len(term) < MAX_TERM_LEN:
            return term
    return None
