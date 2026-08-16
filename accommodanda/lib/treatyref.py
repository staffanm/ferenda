"""Treaty citations in an international court's English text, as references.

The international courts read the same instruments and cite them the same way,
so one matcher serves them: `icj` names the treaty it applies, `icc` cites the
Rome Statute by article on nearly every page. It lives here because it is the
second use (rule:second-use-goes-to-lib), and it reads the curated names
through `data/treaty_names.json` rather than importing a source, so `lib` still
knows nothing about a vertical (rule:lib-never-imports-vertical).

Two kinds of reference come out, and the difference matters to a reader:

  * **article-level** -- "article 74 of the Statute" resolves to the provision,
    `ext/icrc/585#A74`. This is what the ICC's own text is made of: 13,887
    article citations across 244 of its 269 decisions.
  * **instrument-level** -- the decision names the treaty but not a provision,
    or the target holds no article anchors. `ext/icrc/585`.

A caller may add its own unambiguous short forms: inside an ICC decision "the
Statute" is the Rome Statute and nothing else, where the same words in an ICJ
judgment mean the Statute of the Court.
"""

import functools
import json
import re

from . import datasets
from .catalog import BASE
from .lagrum import Ref
from .treaty_ids import arabic as _arabic
from .treaty_ids import article_fragment

PREDICATE = "dcterms:references"
# "article 74", "articles 22 and 23", "article 8(2)(b)(i)", and the roman form
# the older conventions number in -- the Genocide Convention runs Article I to
# Article XIX and the ICJ cites it that way, so an Arabic-only pattern missed
# the corpus's single most-cited instrument. The number is what anchors; the
# sub-paragraph is not addressable in these artifacts.
# The word is case-insensitive, the numeral is not: a roman article number is
# always set in capitals, and matching it case-insensitively would read the
# "i" of "article i" -- and every stray lowercase letter -- as a numeral.
#
# A court enumerates, and each number in the enumeration is a citation:
# "articles 15, 53, 54, 58 and 61 (5) of the Statute" states five relations.
# 1 585 "and" lists, 157 ranges and 34 "or" lists run through the ICC and ICJ
# corpora, the longest naming 11 articles.
#
# A bare comma does not join a list. "Article 58, 10 February 2006" is an
# article and a date, and reading the comma as a separator files the day of the
# month as an article. So a list is only read where it closes with "and", "or"
# or "to" -- the comma items before that close are then unambiguous.
_ROMAN = r"(?=[IVXL])X{0,3}(?:IX|IV|V?I{0,3})"
_ITEM = r"(?:\d{1,3}(?!\d)|%s)(?:\s*\(\d+\))*" % _ROMAN
RE_ARTICLE = re.compile(
    r"\b[Aa]rticles?\s+(?P<list>%s(?:(?:\s*,\s*%s)*\s+(?P<join>and|or|to)\s+%s)?)"
    % (_ITEM, _ITEM, _ITEM))
# the sub-paragraph is not addressable in these artifacts, and "(5)" inside an
# enumeration is not a sixth article
RE_SUBPARAGRAPH = re.compile(r"\s*\(\d+\)")
RE_NUMBER = re.compile(r"\d{1,3}|%s" % _ROMAN)
# How wide a range is filled. "articles 6 to 8" cites article 7 too, and the
# corpus's ranges span 1 to 27 articles. A wider or descending span is a
# misread of the sentence, not a citation of 200 articles.
MAX_RANGE = 30
# How far after an instrument's name a citation may reach back for its article,
# and how far before it. Measured against the ICC corpus: "article 74 of the
# Statute" puts the number 14 characters ahead of the name, and "the Statute,
# article 74" puts it 10 behind. Beyond this the two are separate statements.
ARTICLE_WINDOW = 40
# "article 9 (3) of the International Covenant …" -- an article whose own
# instrument follows it names *that* one, whether or not the name is curated.
# Without this guard the article bound backwards to whatever was named before
# it: an ICC decision writes "Covenant of Civil and Political Rights" where the
# curated name says "on", and article 9 was filed as article 9 of the Rome
# Statute.
RE_OF_INSTRUMENT = re.compile(r"\s*(?:of|in|under)\s+(?:the\s+)?[A-Z]")
# How far around a *generic* name its anchoring context may sit. Every treaty
# family numbers its protocols -- the corpus holds a "Second Additional
# Protocol" to the European Conventions on extradition (coe/098), mutual
# assistance (coe/182) and cybercrime (coe/224) -- so an ordinal name binds to
# a Geneva instrument only where the family is named beside it: "Additional
# Protocol II to the Geneva Conventions" binds, "the Second Additional
# Protocol to this Convention" on a CoE page stays unlinked. 150 characters
# is about one sentence: it spans the full official citation, which puts the
# whole relating-to clause between the family name and the short form
# ("Protocol Additional to the Geneva Conventions of 12 August 1949, and
# relating to the Protection of Victims of International Armed Conflicts
# (Protocol I)"), while the family name is what carries the safety -- no
# other family's text names the Geneva Conventions beside its own protocols.
CONTEXT_WINDOW = 150


def treaty_uri(target):
    return "%sext/%s" % (BASE, target)


# _arabic / article_fragment live in the dependency-free `lib.treaty_ids`
# leaf, shared with the citation engine (see that module's docstring for the
# cycle this avoids).


def cited_articles(match):
    """Every article number one `RE_ARTICLE` match names.

    A range is filled: "articles 6 to 8" cites article 7 too, and dropping the
    interior lost 49 citations across the ICC corpus. A range that runs
    backwards, or wider than `MAX_RANGE`, keeps its two ends -- it is a
    sentence this reader misread, not a decision applying 200 articles."""
    return [number for number, _span in cited_article_spans(match)]


def cited_article_spans(match):
    """`cited_articles`, with each number's own (start, end) span in the text
    -- the anchor an *inline* link needs. A range interior ("7" in "articles 6
    to 8") appears in no text and carries None: it is a relation the document
    states, not a string a link can wrap."""
    liststr, base = match.group("list"), match.start("list")
    items = []
    for m in RE_NUMBER.finditer(liststr):
        # a number inside a "(5)" sub-paragraph is not a sixth article --
        # the positional twin of the RE_SUBPARAGRAPH strip cited_articles
        # used to run before matching
        if liststr[:m.start()].count("(") > liststr[:m.start()].count(")"):
            continue
        items.append((m.group(0), (base + m.start(), base + m.end())))
    if match.group("join") != "to" or len(items) < 2:
        return items
    first, last = _arabic(items[-2][0]), _arabic(items[-1][0])
    if not 0 < last - first <= MAX_RANGE:
        return items
    return items[:-1] \
        + [(str(number), None) for number in range(first + 1, last)] \
        + [items[-1]]


@functools.lru_cache(maxsize=1)
def _curated():
    return json.loads(datasets.TREATY_NAMES.read_text("utf-8"))


@functools.lru_cache(maxsize=1)
def instruments():
    """{target: entry} for every curated instrument."""
    return {entry["target"]: entry for entry in _curated()["instruments"]}


@functools.lru_cache(maxsize=8)
def patterns(extra=()):
    """(compiled pattern, target, label, context) for every curated name plus
    the caller's own, longest label first so a full title beats a short form
    that prefixes it. `context` is None for a name that binds on its own; a
    `generic_names` entry carries its instrument's compiled `generic_context`,
    which `_named` requires within `CONTEXT_WINDOW` of the match.

    `extra` is ((name, target), ...) -- a short form only that caller can read
    without ambiguity. A name given as a compiled pattern is used as written
    (its own guards included) instead of being escaped: hudoc reads "the
    Convention" as the ECHR only where no longer title continues it.
    """
    named = [(name, entry["target"], None)
             for entry in _curated()["instruments"] for name in entry["names"]]
    named += [(name, entry["target"],
               re.compile(entry["generic_context"], re.I))
              for entry in _curated()["instruments"]
              for name in entry.get("generic_names", ())]
    named += [(name, target, None) for name, target in extra]
    # a compiled extra carries no canonical label (label None); `_named` then
    # labels each of its matches with the matched text itself
    out = [(name, target, None, context)
           if isinstance(name, re.Pattern) else
           (re.compile(r"\b%s\b" % re.escape(name), re.I), target, name, context)
           for name, target, context in named]
    # an acronym is matched case-sensitively, because a court sets one in
    # capitals and the lower-cased form of a short acronym is often an ordinary
    # word -- which is also why "CAT" and "CRC" are deliberately not in the
    # table, where "UNCLOS" and "ICESCR" are safe
    out += [(re.compile(r"\b%s\b" % acronym), target, acronym, None)
            for acronym, target in _curated()["acronyms"].items()]
    return sorted(out, key=lambda entry: -len(entry[2] or entry[0].pattern))


def _named(text, extra):
    """Every instrument name in `text`, as (start, end, target, name).

    A longer name wins any span it overlaps, whether the shorter one sits
    inside it ("Rome Statute" beats the "Statute" within it) or only runs into
    it. Two names of the same instrument may start a word apart -- "the Hague
    Regulations concerning the Laws and Customs of War on Land" is both "Hague
    Regulations" [4:21] and "Regulations concerning the Laws and Customs of War
    on Land" [10:68] -- and so may a caller's short form and a curated title:
    "the Convention Against Torture" is "the Convention" to hudoc's reader and
    the CAT to the table. Neither pair nests, so a containment test kept both,
    which files a citation of the ECHR that the sentence never makes and hands
    `interleave` two spans it cannot splice (rule:fail-fast). 41 hudoc
    judgments failed to parse on that assertion.

    Two instruments sharing the *same* span do not compete: "the Geneva
    Conventions" names all four, and common article 3 is an article of each, so
    it resolves to four references rather than to a guess at which one was
    meant.
    """
    found = []
    for pattern, target, name, context in patterns(tuple(extra)):
        for match in pattern.finditer(text):
            # a generic name (an ordinal protocol) binds only beside its
            # family's own context -- see CONTEXT_WINDOW
            if context and not context.search(
                    text, max(0, match.start() - CONTEXT_WINDOW),
                    match.end() + CONTEXT_WINDOW):
                continue
            found.append((match.start(), match.end(), target,
                          name or match.group(0)))
    # longest first, so the fuller name is the one already held; `patterns`
    # cannot order this itself, because a caller's extra is a compiled pattern
    # whose source length says nothing about the words it matches
    kept = []
    for start, end, target, name in sorted(found,
                                           key=lambda e: (e[0] - e[1], e[0])):
        if any(start < held_end and held_start < end
               and (held_start, held_end) != (start, end)
               for held_start, held_end, _t, _n in kept):
            continue
        kept.append((start, end, target, name))
    return sorted(kept)


def _binding(text, named, match):
    """The instrument name(s) an `RE_ARTICLE` match binds to: the entries of
    `named` at the **nearest** distance, or [] where nothing claims it.

    The window is the *gap* between the citation and the name, not a span
    measured from the far edge of the citation: an enumeration is as long as
    the articles it names, and measuring from its start let a list of six push
    its own instrument out of range -- "articles 21, 25(3), 30, 61(7), 64, 67,
    69 and 70(1)(a) to (c) of the Statute" resolved to the Statute and not one
    of its articles.

    A name that *follows* the article wins over a nearer one before it,
    because the citation has a direction: "article 3 common to the Geneva
    Conventions" is an article of the Geneva Conventions however recently the
    Rome Statute was named. Only where nothing follows does a preceding name
    take it, which is the "the Statute, article 74" form. And an article
    followed by "of <Instrument>" belongs to whatever follows, so a name
    *before* it may not claim it.

    Only a curated instrument can take an article: the caller's own short
    forms may name a target this table knows nothing about, and there is no
    anchor to mint against it."""
    near = [entry for entry in named
            if entry[0] - match.end() <= ARTICLE_WINDOW
            and match.start() - entry[1] <= ARTICLE_WINDOW
            and entry[2] in instruments()
            and instruments()[entry[2]]["articles"]]
    if not near:
        return []
    if not any(entry[0] >= match.end() for entry in near) \
            and RE_OF_INSTRUMENT.match(text, match.end()):
        return []

    def distance(entry):
        if entry[0] >= match.end():
            return (0, entry[0] - match.end())
        return (1, match.start() - entry[1])
    closest = distance(min(near, key=distance))
    return [entry for entry in near if distance(entry) == closest]


def _article_uri(target, number):
    """The uri one article citation resolves to: the article's own anchor,
    or the bare instrument where the number is not a provision of it.

    Every curated entry carries `last_article` and `numerals` -- read
    directly, because a new entry that omits one would otherwise degrade in
    silence: every citation to it would fall to instrument level (1 > 0), or
    mint "#A1" against a treaty that only holds "#AI".

    An article the instrument does not have is a misbinding, not a provision:
    "the Additional Protocols, article 85" binds to both, but Protocol II ends
    at 28. Naming the instrument is the honest answer, where #A85 was a link
    to nothing. 97 references pointed at an absent anchor before this check
    (rule:fail-fast). No instrument has an article 0 either -- an ICJ order
    prints "Article 0 5" where the scan lost the 6 of article 65.

    `anchor` is the exception: only an instrument reproduced as an annex
    carries one (the Hague Regulations), so its absence is the ordinary case
    rather than a gap."""
    entry = instruments()[target]
    if not 1 <= _arabic(number) <= entry["last_article"]:
        return treaty_uri(target)
    return "%s#%s" % (treaty_uri(target),
                      article_fragment(number, entry.get("anchor", "A"),
                                       entry["numerals"]))


def references(text, extra=(), article_level=True):
    """Every curated instrument this text cites, as artifact `references`.

    An article binds to the **nearest** instrument named within
    `ARTICLE_WINDOW` (`_binding`), not to every one in range. Binding to all
    of them read "article 3 common to the Geneva Conventions" as article 3 of
    the Rome Statute, because the Statute happened to be named in the sentence
    before.

    One reference per (instrument, article): a decision that applies article 74
    twenty times states one relation to article 74, not twenty. An instrument
    named with no article of its own is referenced whole.
    """
    named = _named(text, extra)
    out, bound = {}, set()

    def add(uri, label):
        out.setdefault(uri, {"uri": uri, "predicate": PREDICATE, "text": label})

    if article_level:
        for match in RE_ARTICLE.finditer(text):
            winners = _binding(text, named, match)
            for number in cited_articles(match):
                for start, end, target, name in winners:
                    bound.add((start, end, target))
                    uri = _article_uri(target, number)
                    add(uri, name if "#" not in uri
                        else "%s, article %s" % (name, number))
    for start, end, target, name in named:
        if (start, end, target) not in bound:
            add(treaty_uri(target), name)
    return sorted(out.values(), key=lambda reference: reference["uri"])


def spans(text, extra=(), article_level=True):
    """The citations `references` finds, as inline-linkable (start, end, uri)
    spans in document order -- what a page renderer needs where the artifact
    relation is not enough.

    Three deliberate differences from the aggregate: a range interior
    ("articles 6 to 8" citing 7) appears in no text, so only the ends link; an
    ambiguous binding ("the Geneva Conventions" naming four instruments) links
    nothing rather than guess; and a bound instrument name links to the
    instrument itself, so "article 74 of the Statute" reads as two links --
    the provision and the instrument."""
    named = _named(text, extra)
    out = []
    if article_level:
        for match in RE_ARTICLE.finditer(text):
            winners = _binding(text, named, match)
            if len({target for _s, _e, target, _n in winners}) != 1:
                continue
            target = winners[0][2]
            first = True
            for number, span in cited_article_spans(match):
                if span is None:
                    continue
                # the first number folds in the leading "article(s)" word,
                # the way lagrum's emit_pages draws page-list boundaries
                start = match.start() if first else span[0]
                first = False
                out.append((start, span[1], _article_uri(target, number)))
    for start, end, target, _name in sorted(named):
        # a name shared by several instruments at the same span (the Geneva
        # Conventions) has no single link target -- skip it
        if sum(1 for s, e, _t, _n in named if (s, e) == (start, end)) > 1:
            continue
        out.append((start, end, treaty_uri(target)))
    return sorted(out)


def refs(text, extra=(), exclude=None):
    """The `spans` projection as inline `lagrum.Ref`s -- the shape every
    caller's `refs_for` scanner hands to `interleave`. `exclude` drops a
    document's own uri (and its fragments): a treaty naming itself is
    self-description, not a citation."""
    return [Ref(start, end, text[start:end], PREDICATE, uri)
            for start, end, uri in spans(text, extra=extra)
            if exclude is None
            or not (uri == exclude or uri.startswith(exclude + "#"))]
