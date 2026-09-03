"""Swedish court case numbers -- the printed shape, and one spelling for it.

A decision carries a case number ("målnummer") from the day it is filed, and a
referat number only when it is published: "T 3-08" was decided 2009-11-03 and
came out as NJA 2009 s. 672 months later. Everything written in between cites
the case number, and law review articles keep doing so afterwards -- SvJT
2010 s. 94 names "Högsta domstolens dom 2009-11-03 T 3-08" and never the
referat. Both the search index (`lib/search.py`) and the citation engine
(`lib/lagrum.py`) therefore have to recognise the number, which is why the
shape lives here rather than in either of them (rule:second-use-goes-to-lib).

One number is printed several ways. Of the 24,995 case numbers the dv corpus
holds, 877 join the letter to the serial ("B732-08") where the rest separate it
("B 732-08"), and Arbetsdomstolen hyphenates it ("A-232-2013"). `normalize`
spells all three the same, and both sides call it -- the index on what it
stores, the query on what it is asked.
"""

import functools
import re

# `lagrum` is imported as a module, not `from .lagrum import Ref`: lagrum
# imports this module for its `spans`, so only the module form survives the
# cycle (the attribute is read at call time, when lagrum is fully loaded)
from . import datasets, lagrum, util

# The letter groups a case number may start with, over the 24,995 case numbers
# the dv corpus holds: B (3,943), Ö (3,912), T (2,100), A (2,051), M (1,307),
# UM (560), P (498), ÖH (303), ÖÄ (259) and 20 rarer ones. 9,466 numbers carry
# no letter at all (Regeringsrätten, Patentbesvärsrätten, the kammarrätter).
#
# A closed vocabulary, not "any short word before the number": the words that
# actually stand there in running text are "mål" and "nr", and letting those
# count would read "mål nr 4659-11" as the number "nr 4659-11" and find nothing.
COURT_LETTERS = ("B Ö T A M UM P ÖH ÖÄ F PMT Ä FT PMÖ PMÖÄ ÖM H PMÄ ÖP UMS ÖF "
                 "PMFT K PMB TVA ÖVA ÖÅ X").split()

# serial and year, with the letter group in front where a court prints one. The
# lookbehind and lookahead keep the number out of a longer run of digits, which
# is what tells "2009-11-03" (a date, no case number in it) from "T 3-08".
CASE_NUMBER = re.compile(
    r"(?<![\w-])(?:(%s)[ -]?)?(\d{1,5}-\d{2,4})(?![-\d])"
    % "|".join(sorted(COURT_LETTERS, key=len, reverse=True)), re.IGNORECASE)


def _canonical(match):
    letters, number = match.groups()
    return "%s %s" % (letters.upper(), number) if letters else number


def normalize(text):
    """Every case number in `text`, spelled one way: the letter group upper-case
    and one space in front of the serial. Text around a number is untouched, so
    it takes a bare case number ("B732-08" -> "B 732-08") and a sentence alike.
    """
    return CASE_NUMBER.sub(_canonical, text)


def find(text):
    """The case numbers printed in `text`, normalized. Empty for a text that
    holds none -- "brott 2009" and a bare year are not case numbers, and neither
    is the "2009-11" inside the date 2009-11-03."""
    return [_canonical(m) for m in CASE_NUMBER.finditer(text)]


def query_numbers(query):
    """The case numbers a *search query* asks for -- what `lib/search.py` builds
    its phrase clause from.

    Stricter than `find`, because a search box has no citation around the number
    to read. A letterless pair of numbers is a legitimate case number ("4659-11",
    9,466 of the 24,995 held numbers carry no court letter) and also the shape of
    a section range: the corpus holds decisions numbered 17-18, 17-19 and 18-19,
    so `find` alone turned the query "17 kap. 17-18 §§" into a hit on a
    Regeringsrätten decision it matches in no other way. So a bare number counts
    only when it is the whole query; inside a longer query it needs the court
    letter or the word "mål" in front of it.
    """
    return [_canonical(m) for m in CASE_NUMBER.finditer(query)
            if m.group(1) or RE_MAL.search(query[:m.start()])
            or m.group() == query.strip()]


# What a citation calls each court, mapped onto the court codes the dv corpus
# files decisions under (the casenumbers snapshot's "courts", datasets.CASENUMBERS). Editorial, and
# the reason it is not derived from those names: a citation writes "HD:s dom",
# never "Högsta domstolen"; and "Svea hovrätts beslut" covers two codes, since
# the court's hyresrättsliga avgöranden are a series of their own.
#
# A court that is not here links nothing, which is the point: a tingsrätt is
# cited by case number as often as an överrätt ("Södertörns tingsrätt mål nr B
# 4318-18"), the corpus holds no tingsrätt decisions, and its case numbers
# collide with the ones the corpus does hold.
COURT_PHRASES = {
    "högsta domstolen": ("HDO",),
    "hd": ("HDO",),
    "högsta förvaltningsdomstolen": ("HFD",),
    "hfd": ("HFD",),
    "regeringsrätten": ("REGR",),
    "regr": ("REGR",),
    "arbetsdomstolen": ("ADO",),
    "ad": ("ADO",),
    "mark- och miljööverdomstolen": ("MMOD", "MOD"),
    "miljööverdomstolen": ("MOD", "MMOD"),
    "möd": ("MMOD", "MOD"),
    "migrationsöverdomstolen": ("MIOD",),
    "mig": ("MIOD",),
    "marknadsdomstolen": ("MDO",),
    "patent- och marknadsöverdomstolen": ("PMOD",),
    "pmöd": ("PMOD",),
    "patentbesvärsrätten": ("PBR",),
    # Mark- och miljööverdomstolen and Patent- och marknadsöverdomstolen sit in
    # Svea hovrätt and print its name on their own letterhead ("SVEA HOVRÄTT
    # DOM ... M 6087-24"), so the phrase reaches all five series
    "svea hovrätt": ("HSV", "HYOD", "MMOD", "MOD", "PMOD"),
    "göta hovrätt": ("HGO",),
    "hovrätten över skåne och blekinge": ("HSB",),
    "hovrätten för västra sverige": ("HVS",),
    "hovrätten för nedre norrland": ("HNN",),
    "hovrätten för övre norrland": ("HON",),
    "kammarrätten i stockholm": ("KST",),
    "kammarrätten i göteborg": ("KGG",),
    "kammarrätten i jönköping": ("KJO",),
    "kammarrätten i sundsvall": ("KSU",),
    "rättshjälpsnämnden": ("RHN",),
}

# how far back of the number the citation's own apparatus reaches -- the court,
# and the date where it prints one. "Högsta domstolens dom den 8 februari 2013 i
# mål nr B 868-12" is 57 characters from court to number.
COURT_WINDOW = 90

# the phrase as a citation writes it: in the genitive ("Högsta domstolens dom",
# "HD:s dom") as often as not, the abbreviations with the Swedish colon
# genitive, and a court whose name ends in a definite article sometimes doubled
# ("Svea hovrättens beslut 2001-03-26 i mål ÖH 3895-00")
RE_COURT = re.compile(r"\b(%s)(?::?s|ens)?\b" % "|".join(
    sorted((re.escape(phrase) for phrase in COURT_PHRASES), key=len,
           reverse=True)), re.IGNORECASE)
# the word that says the number *is* a case number, right in front of it
RE_MAL = re.compile(r"\b(?:i\s+)?mål(?:et|en|nr|nummer)?\.?\s*(?:nr\.?\s*)?$",
                    re.IGNORECASE)
RE_ISO_DATE = re.compile(r"\b(?:19|20)\d\d-\d\d-\d\d\b")
# a court this table does not know, standing between the court it does know and
# the number. "HD prövade Södertörns tingsrätts dom i mål nr B 1-85" names two
# courts, and the number is the tingsrätt's -- linking it to the HD case the
# number happens to match is the false link COURT_PHRASES exists to prevent.
RE_OTHER_COURT = re.compile(
    r"\b\w*(?:tingsrätt|hovrätt|kammarrätt|förvaltningsrätt|domstol|nämnd)\w*",
    re.IGNORECASE)
# how close in front of the number a printed date has to stand to count as the
# *marker* that the number is a case number at all ("dom 2009-11-03 T 3-08").
# This bounds that role only: a date anywhere in the window still decides
# between two candidates further down in `_resolve` ("AD:s dom den 5 mars 2014 i
# mål A-232-2013" picks by a date 21 characters away). Both spellings count,
# which is why the distance is measured rather than the string compared.
DATE_GAP = 3


@functools.cache
def _index():
    """The snapshot (datasets.CASENUMBERS), read once. `spans` runs per text node -- once per
    block and once per table cell (`lib/artifact.py`) -- and the file is 1.3 MB,
    so re-reading it there tripled the cost of the whole citation scan.
    `lib/emdref.py` caches its own snapshot the same way."""
    return datasets.load_casenumbers()


def _court_named(before):
    """The court codes the text in front of the number names, or () -- the
    nearest recognised court, unless another court stands between it and the
    number and takes the number for its own."""
    named = list(RE_COURT.finditer(before))
    if not named:
        return ()
    return (() if RE_OTHER_COURT.search(before, named[-1].end())
            else COURT_PHRASES[named[-1].group(1).lower()])


def _date_named(before):
    """The date printed in front of the number, as (ISO, characters between it
    and the number), or None. Both spellings appear -- "dom 2009-11-03 T 3-08"
    and "dom den 8 februari 2013 i mål nr B 868-12" -- and the last one in the
    window is the citation's own."""
    iso = list(RE_ISO_DATE.finditer(before))
    swedish = list(util.SV_DATE.finditer(before))
    last = max(iso + swedish, key=lambda m: m.end(), default=None)
    if last is None:
        return None
    return (last.group() if last in iso
            else util.swedish_date(last.group()), len(before) - last.end())


def _resolve(number, before, snapshot):
    """The one held decision this citation means, or None.

    Three things have to agree, because a case number on its own does not
    identify a decision: the corpus must hold the number, the citation must name
    a court that holds it, and a printed date must match. Anything ambiguous
    stays unlinked (rule:fail-fast) -- 298 of the 24,411 held numbers name more
    than one decision, and the tingsrätt case numbers that fill the same texts
    would otherwise land on whichever överrätt case shares the number.
    """
    candidates = snapshot["numbers"].get(number)
    if not candidates:
        return None
    courts = _court_named(before)
    if not courts:
        return None
    # the marker that this number is a case number at all: "mål nr", a court
    # letter in the number itself, or the decision date standing right in front
    dated = _date_named(before)
    if not (RE_MAL.search(before) or " " in number
            or (dated and dated[1] <= DATE_GAP)):
        return None
    candidates = [c for c in candidates if c[0] in set(courts)]
    if dated:
        candidates = [c for c in candidates if c[1] == dated[0]] or candidates
    return candidates[0] if len(candidates) == 1 else None


def spans(text, base="https://lagen.nu/"):
    """Every case-number citation in `text` that resolves to a held decision, as
    (start, end, uri) over the number itself -- the identity the citation names,
    the way `lib/emdref.spans` returns the ECHR case name it matched. The caller
    merges these beside the grammar's refs (lagrum.yield_overlaps)."""
    snapshot = _index()
    out = []
    for m in CASE_NUMBER.finditer(text):
        found = _resolve(_canonical(m),
                         text[max(0, m.start() - COURT_WINDOW):m.start()],
                         snapshot)
        if found:
            out.append((m.start(), m.end(), base + found[2]))
    return out


def refs(text, base, predicate, orig):
    """The `spans` projection as inline `lagrum.Ref`s, the way `treatyref.refs`
    and `emdref.refs` hand their spans to the merge. Each link's own words are
    sliced from `orig` (see `lagrum.spans_as_refs`)."""
    return lagrum.spans_as_refs(spans(text, base), orig, predicate)
