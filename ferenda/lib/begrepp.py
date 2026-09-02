"""Begrepp (defined legal terms) end to end, in the two stages the pipeline
runs them: **at parse** a source detects the terms a författning defines and
marks their later uses in the same act; **at relate** the corpus-wide term list
is de-inflected and clustered onto one canonical concept node.

Parse time -- detecting definitions
-----------------------------------
This half mints nothing itself: the caller turns each term into a
``dcterms:subject`` inline link (``Ref`` with ``kind="term"``). A faithful port
of the old ``sfs.py`` ``find_definitions`` heuristics, off the framework, moved
here from ``sfs/begrepp.py`` when föreskrift became the second source to mark
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
machinery below the class is shared.

Relate time -- normalisation and clustering
-------------------------------------------
Collapse the inflected surface forms of a legal term onto one canonical
concept, so SFS definitions, DV nyckelord, EU defined terms and the
hand-authored wiki pages all land on the same `begrepp/<Name>` node.

The vocabulary is bounded (defined legal terms), so this is a hand-rolled,
**corpus-aware** Swedish noun de-inflector, not a general lemmatizer:

  * `_bases(form)` proposes the plausible base (indefinite-singular) forms of a
    term by *reversing* each inflectional ending. Ambiguous endings yield several
    candidates -- notably `-arna`, the definite plural of both an `-are` agent
    noun (`näringsidkarna` → `näringsidkare`) and an `-ar` plural (`bilarna` →
    `bil`). A bare `-are` is NEVER stripped: it is the agent *base*, so `domare`
    does not reduce to `dom`.
  * `cluster(forms)` unions each form only with candidate bases that are
    *themselves observed forms* -- so the corpus decides which reading is real.
    The canonical display/URI form of a group is a wiki-authored form if present
    (the wiki uses base form by convention), else the most base-like member.

A hand-edited override file (`data/begrepp_aliases.json`) maps stubborn variants and
true synonyms onto a canonical, and lists forms to KEEP DISTINCT (blocking a
wrong auto-merge). De-inflection only touches a term's last word (the head in
this corpus's compounds and `X av/för Y` phrases); casing and whitespace are
folded so `på Internet` / `på internet` are one concept.
"""

import json
import re
from pathlib import Path

from . import util
from .lagrum import Ref
from .util import normalize_fold as _norm

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
    # closed-class words a definition heuristic picks up off a mangled list or
    # a stray colon -- an act does not define "och". These eight hold 33
    # `definitions` rows over 32 documents, every one an extraction slip, and
    # each costs one false link per occurrence once the uses are marked
    # (`mark_term_uses`): 1985:1101's "den" alone marked 30 spans, "träder i
    # kraft *den* 1 juli 1986" among them
    "den", "det", "de", "denna", "detta", "en", "som", "och",
})

# Terms that ARE definitions but whose *uses* cannot be told apart from an
# ordinary word: mervärdesskattelagen defines "vara" (goods), and every
# infinitive of *att vara* reads the same -- 112 spans in one sampled act,
# all of them wrong. The definition stands and its own span keeps its concept
# link; only the marking of later uses is withheld, because no test available
# here separates the two senses (a part-of-speech tagger would).
AMBIGUOUS_USE = frozenset({"vara"})


def _markable(term):
    """Whether a defined term's *later uses* can be marked safely.

    Two shapes cannot. A word in `AMBIGUOUS_USE` carries a second, ordinary
    meaning. And an abbreviation set in capitals is the same case one letter at
    a time: mervärdesskattelagen (2023:200) defines "EU", and marking its uses
    put 382 links in that one act -- on the union's name in every act name,
    every EU-institution mention, every directive number's tail. The definition
    itself stands either way; only the marking of later uses is withheld."""
    return (term.lower() not in AMBIGUOUS_USE
            and not (len(term) <= 4 and term.isupper()))

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


# --------------------------------------------------------------------------
# using a defined term elsewhere in the same act
# --------------------------------------------------------------------------
#
# An act that defines a term goes on using it, and the reader who meets
# "uppgiftssamlingar" in 3 kap. 1 § lagen (2021:1172) om behandling av
# personuppgifter vid Försvarets radioanstalt has to remember that 1 kap. 5 §
# said what one is. Marking every later use as a link into the defining
# provision hands the definition to the popover instead (Staffan, 2026-08-30:
# "sfs does not yet, cf. its non-markup of uppgiftssamling").
#
# eurlex has done this since its parse was written; these two functions are
# that code, moved here when SFS became the second source to want it
# (rule:second-use-goes-to-lib).

# The inflectional endings a Swedish noun takes -- the definite and plural
# forms and their genitives. Longest first, so a regex alternation over them
# prefers the longest ending that fits. One list, because the relate-time half
# below reads the same domain fact backwards (it *reverses* an ending to reach
# the base form) and two hand-kept copies drift (rule:second-use-goes-to-lib).
SWEDISH_NOUN_ENDINGS = ("ernas", "arnas", "ornas", "erna", "arna", "orna",
                        "ens", "ets", "er", "ar", "or", "en", "et", "na",
                        "ns", "n", "s", "t")

# what a term picks up where it is *used* -- "uppgiftssamling" ->
# "uppgiftssamlingar", "incident" -> "incidenten". The two single-letter
# endings the de-inflector needs are left out here: appending one to a term
# that does not take it reads as a different word ("avtal" -> "avtalt"), and
# the definite forms the matcher does need ("-en", "-et") are already listed.
SUFFIXES = {
    "swe": tuple(e for e in SWEDISH_NOUN_ENDINGS if e not in ("n", "t")),
    "eng": ("es", "s"),
}

# a use links to the definition with the same predicate an ordinary citation
# uses; `kind="term"` is what tells the renderer to draw it as a term link
TERM_PRED = "dcterms:references"


def _term_regex(term, suffixes):
    """One term as a regex: its words separated by any whitespace, with an
    optional inflectional ending on its final word.

    `\b` alone does not end a Swedish compound: it let "EU" match the first
    half of "EU-land" (269 of 3,597 links in a 400-artifact sample) and the
    tail of a directive number the citation parser had not claimed
    ("direktiv 2010/66/EU"). A use is a whole word: no word character, hyphen
    or slash on either side of it."""
    body = r"\s+".join(re.escape(tok) for tok in term.split())
    return r"(?<![\w/-])%s(?:%s)?(?![\w-])" % (body, "|".join(suffixes))


def build_matcher(terms, lang):
    """Compile a single combined matcher for all defined `terms` and a
    group-name -> anchor index. Terms are tried longest-first so a phrase wins
    over a term nested inside it, and a term whose uses cannot be told from
    ordinary text (`_markable`) is left out. Returns (None, {}) when nothing
    is left to match."""
    terms = {t: a for t, a in terms.items() if _markable(t)}
    if not terms:
        return None, {}
    suffixes = SUFFIXES[lang]
    parts, index = [], {}
    for i, term in enumerate(sorted(terms, key=len, reverse=True)):
        group = "t%d" % i
        parts.append("(?P<%s>%s)" % (group, _term_regex(term, suffixes)))
        index[group] = terms[term]
    return re.compile(r"\b(?:%s)\b" % "|".join(parts), re.IGNORECASE), index


def term_refs(text, matcher, index, doc_uri, self_anchor=None):
    """Occurrences of any defined term in `text` as term-link Refs into the
    document's own definition points. The point defining a term skips its own
    term (by anchor), but still links the other terms it mentions.

    The one matching rule both entry points read: a caller with the whole
    text merges these beside its citations with `lagrum.merge_refs` (eurlex),
    a caller with a built run list splices them through `mark_term_uses`
    (sfs)."""
    if not matcher:
        return []
    refs = []
    for m in matcher.finditer(text):
        anchor = index[m.lastgroup]
        if anchor == self_anchor:
            continue
        refs.append(Ref(m.start(), m.end(), m.group(), TERM_PRED,
                        "%s#%s" % (doc_uri, anchor), kind="term"))
    return refs


def mark_term_uses(runs, matcher, index, doc_uri, self_anchor=None):
    """One node's inline-run list with every use of a defined term linked to
    the provision defining it -- `term_refs` spliced into the runs a source
    has already built.

    Only *plain* runs are scanned: a use inside an existing link run is left
    alone, which is the same rule eurlex applies with `merge_refs` -- a
    citation is the stronger, cross-document link, and the defining occurrence
    is already a `dcterms:subject` link to the concept page. A term whose
    definition sits in this very node links nowhere (`self_anchor`): SFS gathers
    a whole term list in one stycke, so every sibling definition would otherwise
    link to the line it is written on. Returns `runs` itself when nothing
    matched, so an unchanged artifact stays byte-identical."""
    if not matcher:
        return runs
    out, hit = [], False
    for run in runs:
        if not isinstance(run, str):
            out.append(run)
            continue
        pos = 0
        for ref in term_refs(run, matcher, index, doc_uri, self_anchor):
            if ref.start > pos:
                out.append(run[pos:ref.start])
            out.append({"predicate": ref.predicate, "uri": ref.uri,
                        "text": ref.text, "kind": ref.kind})
            pos, hit = ref.end, True
        if pos < len(run):
            out.append(run[pos:])
    return out if hit else runs


# --------------------------------------------------------------------------
# relate-time: normalisation and clustering
# --------------------------------------------------------------------------
#
# The corpus-wide half. Everything above runs per document at parse; what
# follows reads the whole collected term list at relate and decides which
# surface forms are one concept.

RES = Path(__file__).resolve().parent / "data" / "begrepp_aliases.json"

# generic inflectional endings reversed to a base (definite singular, plural and
# definite plural). NOT -are (an agent base) and NOT derivational (-ning/-het/
# -else), which would merge unrelated words.
#
# The shared list minus its genitive members: `_bases` reverses the genitive
# -s first and de-inflects the plain form it leaves, so reversing "-ens" here
# too would cut a second ending off the same word.
_ENDINGS = tuple(e for e in SWEDISH_NOUN_ENDINGS if not e.endswith("s"))


def _bases(word):
    """Candidate base forms of a lower-cased Swedish noun, by reversing each
    inflectional ending (several when ambiguous); empty when it looks like a base.
    The corpus picks the real one (`cluster` keeps only observed candidates)."""
    out = set()
    forms = {word}
    if word.endswith("s") and len(word) > 4:     # genitive -> also the plain form
        forms.add(word[:-1])
    for w in forms:
        if w.endswith("arna") and len(w) > 6:    # -are agent noun, definite plural
            out.add(w[:-4] + "are")              #   näringsidkarna -> näringsidkare
        if w.endswith("aren") and len(w) > 5:    # -are agent noun, definite singular
            out.add(w[:-1])                      #   näringsidkaren -> näringsidkare
        for end in _ENDINGS:                     # generic plural / definite
            if w.endswith(end) and len(w) - len(end) >= 3:
                out.add(w[:-len(end)])
    out.discard(word)
    return out


def _last_word_bases(form):
    """Candidate base forms of a whole (lower-cased) term: its last word
    de-inflected, the rest kept (the head inflects in this corpus's terms)."""
    parts = form.split(" ")
    return {" ".join(parts[:-1] + [b]) for b in _bases(parts[-1])} if parts else set()


def _word_variants(word: str) -> set[str]:
    """The inflected surface forms one word of a term may take in text:
    itself, its candidate bases, the reversible ending classes forward from
    each, the genitive -s, the agent-noun forms, and the adjectival/weak "a"
    ("sakkunnig" -> "den sakkunniga") -- forward-only, so `cluster`'s
    reversing is never loosened (`_ENDINGS` stays as it is)."""
    variants = set()
    for stem in {word} | _bases(word):
        variants.add(stem)
        variants.add(stem + "s")
        if stem.endswith("are"):                 # agent noun: -aren, -arna
            variants.update((stem + "n", stem[:-1] + "na"))
        if len(stem) >= 3:
            for end in (*_ENDINGS, "a"):
                variants.add(stem + end)
                variants.add(stem + end + "s")
    return variants


def term_pattern(term):
    """A compiled regex matching `term` or an inflected surface form of it in
    `util.normalize_fold`ed running text, every word inflection-wide and
    word-bounded: "betydande incident" matches "betydande incidenten";
    "nationellt bedömningsstöd" matches "nationella bedömningsstöd" (the
    attributive adjective agrees with its noun, so a last-word-only pattern
    missed every plural-context use -- measured on the golden-ten bench,
    where it cost all five bedömningsstöd rungs). Never "incidentrapport" (a
    compound is a different word)."""
    parts = []
    for word in _norm(term).split(" "):
        alt = "|".join(re.escape(v) for v in
                       sorted(_word_variants(word), key=len, reverse=True))
        parts.append("(?:" + alt + ")")
    return re.compile(r"\b" + r"\s+".join(parts) + r"\b")




def _ucfirst(name):
    return name[0].upper() + name[1:] if name else name


def _base_score(form):
    """Lower is more base-like: shorter wins, a definite/plural ending is a
    tie-break penalty (so `Borgenär` beats `Borgenären`)."""
    inflected = bool(_bases(form.lower()))
    return (len(form), inflected, form)


def _canonical_form(forms):
    """The display/URI form for a group of surface variants: a wiki-authored form
    if the group has one, else the most base-like member."""
    wiki = [f for f in forms if f in _wiki_titles()]
    return _ucfirst(min(wiki or list(forms), key=_base_score))


# --------------------------------------------------------------------------
# overrides (hand-edited) + the wiki base-form registry
# --------------------------------------------------------------------------

_OVERRIDES = None
_WIKI = None


def _load():
    global _OVERRIDES
    if _OVERRIDES is None:
        data = json.loads(RES.read_text())
        _OVERRIDES = {"alias": {_norm(k): v for k, v in data.get("alias", {}).items()},
                      "distinct": [{_norm(x) for x in p}
                                   for p in data.get("keep_distinct", [])]}
    return _OVERRIDES


def _wiki_titles():
    return _WIKI or set()


def register_wiki(titles):
    """Tell the normalizer which display forms are wiki-authored (base form by
    convention), so they win canonical selection and never silently move."""
    global _WIKI
    _WIKI = set(titles)


# --------------------------------------------------------------------------
# clustering -- the corpus-wide canonicalisation
# --------------------------------------------------------------------------

def cluster(forms):
    """Group surface `forms` into concepts: `{canonical: sorted([variants])}`. A
    form unions with a candidate base only when that base is itself observed (or
    a hand-edited alias target); keep-distinct pairs are split back apart."""
    over = _load()
    by_norm = {}
    for f in forms:
        by_norm.setdefault(_norm(f), set()).add(f)
    parent = {k: k for k in by_norm}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        if a in parent and b in parent and find(a) != find(b):
            parent[find(a)] = find(b)

    for k in list(by_norm):
        target = over["alias"].get(k)
        if target:
            union(k, _norm(target))              # explicit alias wins
        for cand in _last_word_bases(k):
            if cand in by_norm:
                union(k, cand)

    comps = {}
    for k in by_norm:
        comps.setdefault(find(k), set()).update(by_norm[k])

    out = {}
    for members in comps.values():
        for sub in _split_distinct(members):
            # an explicit alias target is the canonical (a human decision); else
            # the wiki form, else the most base-like member
            targets = sorted(over["alias"][_norm(m)] for m in sub
                             if _norm(m) in over["alias"])
            out[_ucfirst(targets[0]) if targets else _canonical_form(sub)] = sorted(sub)
    return out


def _split_distinct(members):
    """Split a group so no keep-distinct pair shares it (a wrong auto-merge the
    override forbids). Members off every distinct list stay with the first part."""
    norms = {m: _norm(m) for m in members}
    pairs = [d for d in _load()["distinct"]
             if len(d & set(norms.values())) > 1]
    if not pairs:
        return [members]
    parts = [{m for m in members if norms[m] in d} for d in pairs]
    rest = {m for m in members if not any(norms[m] in d for d in pairs)}
    if parts:
        parts[0] |= rest
    return [p for p in parts if p]
