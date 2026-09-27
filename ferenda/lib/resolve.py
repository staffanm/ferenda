"""Turn a ⌘K query into a precise, fragment-deep resource target.

Full-text search can't reach what people actually type into ⌘K: a law's
*nickname* ("avtalslagen") or abbreviation ("BrB") -- neither appears in the
statute's title or body -- a chapter:section pinpoint ("12:1"), an EU act's
short name ("GDPR"), or a case's nickname ("Instagrambilden"). This resolver
reads the query as a *citation* and returns the exact URI (with #fragment), so
⌘K can pin it as the first, Enter-to-go hit.

Three resolvers over the curated named-resource datasets (lib.datasets), tried
in order:

  * SFS -- reuses the citation engine's own LagrumParser, which already turns
    "12 kap. 1 § brottsbalken" / "BrB 12:1" into lagen.nu/<id>#<frag>. The one
    thing it doesn't do is the *law-first* order people type ("avtalslagen 36"):
    the grammar wants "36 § avtalslagen". So we peel a leading nickname/abbr off
    the front, normalise the terse pinpoint that follows ("36" -> "36 §", "12:1"
    -> "12 kap. 1 §"), and resolve it against that law's context.
  * EU  -- a named act ("GDPR", "IPRED") + an optional "art N"/"artikel N" tail
    -> celex/<CELEX>#<N>.
  * DV  -- a case nickname ("Instagrambilden") -> the published NJA case URI.

Other Swedish, EU and international citations use the shared citation engine
and document identity grammars. `data/citation_series.json` defines when an
absent citation is invalid (`absent_is_invalid`, called after catalog lookup).

Pure and catalog-free: it maps a string to a URI. Whether that URI is hosted
(and its title) is the caller's concern -- the /search endpoint confirms it
against the catalog before pinning it, so an alias for a not-yet-parsed document
simply doesn't surface.
"""

import functools
import json
import re
import threading
from datetime import date
from pathlib import Path

from . import courtids, datasets, markdown, text, treatyref
from .coe import treaty_uri
from .coe_ids import article_fragment
from .labels import treaty_names
from .lagrum import (
    ALL_PARSE_TYPES,
    CELEX_BASE,
    EURATTSFALL,
    FORESKRIFT,
    FS_SLUG,
    TREATIES,
    LagrumParser,
    lagrum_uri,
    load_abbreviations,
    load_named_spans,
    load_namedacts,
    load_namedlaws,
)

# --------------------------------------------------------------------------
# SFS -- nickname/abbr + chapter/§ pinpoint, in the order ⌘K users type
# --------------------------------------------------------------------------

_parsers = threading.local()


def _fresh_sfs_parser():
    """This thread's citation parser, with a clean per-query state. Construction
    (grammar + dataset loading) is the expensive part, so each thread builds one
    parser and keeps it. The parser is *stateful* and that state leaks across
    parse_text calls ("samma lag", learned aliases), so the resolver -- which
    treats each ⌘K query as an independent one-shot citation -- resets it before
    every query, the same reset-per-document pattern the verticals use.

    One parser per thread, not one shared cached parser: /search is a
    synchronous endpoint, so FastAPI runs it in a thread pool and two concurrent
    queries would otherwise reset each other's parse mid-flight."""
    parser = getattr(_parsers, "sfs", None)
    if parser is None:
        parser = _parsers.sfs = LagrumParser(
            load_namedlaws(datasets.NAMEDLAWS), basefile="query",
            abbreviations=load_abbreviations(datasets.NAMEDLAWS))
    parser.reset()
    return parser


@functools.cache
def _leading_laws():
    """Every law nickname and abbreviation as (alias, lawid) pairs, longest
    alias first -- so a leading law token in a query is matched greedily (the
    specific "brottsbalken" before any shorter alias that is also a prefix)."""
    pairs = list(load_namedlaws(datasets.NAMEDLAWS).items())
    pairs += list(load_abbreviations(datasets.NAMEDLAWS).items())
    return sorted(pairs, key=lambda p: len(p[0]), reverse=True)


@functools.cache
def _named_spans():
    """Every named span (`lagrum.NamedSpan`), keyed by its lower-cased name --
    "hyreslagen", "samtyckeslagen". Kept separate from `_leading_laws()`: a
    span's name is never fed to `LagrumParser` as a leading-law alias, since
    resolving a *pinpoint* after it ("hyreslagen 3 §") would need the parser to
    read "3 §" as relative to the span's own chapter, not to the whole act --
    real work this does not do. Only the bare name resolves here; a name with
    a pinpoint tail falls through to the ordinary resolvers below, same as any
    other unrecognised leading word."""
    return {name.lower(): span
            for name, span in load_named_spans(datasets.NAMEDLAWS).items()}


def _split_leading_law(q):
    """(lawid, remainder) when the query opens with a known law nickname or
    abbreviation (case-insensitively, at a word boundary), else None. The
    remainder is whatever pinpoint follows ("36", "12:1", "12 kap. 1 §")."""
    low = q.lower()
    for alias, lawid in _leading_laws():
        a = alias.lower()
        if low.startswith(a) and (len(low) == len(a) or not low[len(a)].isalnum()):
            return lawid, q[len(alias):].strip()
    return None


_SFSNR = re.compile(
    r"(?:sfs\s+)?((?:1[6-9]|20)\d{2}:(?:bih\.?\s?)?\d+(?:\s?s\.?\s?\d+)?)"
    r"(?:\s+(.*))?", re.IGNORECASE)


def _split_sfsnr(q):
    """(lawid, remainder) when the query opens with a bare SFS number --
    "2022:818", "SFS 1962:700 3:1", "1904:48 s.1" -- else None. The number-
    shaped probe is what API clients (and MCP callers) most naturally send;
    the id is kept in citation (space) form and lagrum_uri slugs it to the
    corpus basefile form ("1904:48 s.1" -> .../1904:48_s.1)."""
    m = _SFSNR.fullmatch(q.strip())
    if not m:
        return None
    return (re.sub(r"bih\.?\s?", "bih. ", m.group(1), flags=re.IGNORECASE),
            (m.group(2) or "").strip())


def _normalize_pinpoint(rem):
    """A terse, law-first pinpoint normalised to the form the grammar parses:
    "12:1" -> "12 kap. 1 §", a bare "36"/"36 a" -> "36 §". Anything already
    carrying a § or "kap" is left as-is (only a §-less, digit-bearing tail gets
    one appended, so "26 kap 9" still resolves)."""
    rem = rem.strip().rstrip(".").strip()
    m = re.fullmatch(r"(\d+):(\d+)\s*([a-z]?)", rem)
    if m:
        rem = "%s kap. %s %s§" % (m.group(1), m.group(2),
                                  m.group(3) + " " if m.group(3) else "")
    elif "§" not in rem and re.search(r"\d", rem):
        rem += " §"
    return rem


def resolve_sfs(q):
    """A statute URI for `q`, fragment-deep when it carries a pinpoint, else
    None. Handles the law-first order ⌘K users type ("avtalslagen 36", "BrB
    12:1") by peeling the leading law and resolving the pinpoint in its context;
    falls back to parsing the query as a plain citation ("12 kap. 1 § brottsbalken").
    A bare SFS number ("2022:818", "SFS 1962:700 3:1") is peeled the same way
    a nickname is. A named span ("hyreslagen") typed bare resolves straight to
    its own first node -- unlike a whole act's name, it never means the act's
    root."""
    span = _named_spans().get(q.strip().lower())
    if span:
        return lagrum_uri({"law": span.lawid}) + "#" + span.first
    parser = _fresh_sfs_parser()
    split = _split_leading_law(q) or _split_sfsnr(q)
    if split:
        lawid, rem = split
        if rem:
            refs = parser.parse_text(_normalize_pinpoint(rem),
                                     context={"law": lawid})
            frag = next((r.uri for r in refs if "#" in r.uri), None)
            if frag:
                return frag
        return lagrum_uri({"law": lawid})       # law named, no usable pinpoint
    # nobaseuri mode (context={}): the query has no resolvable base law, so a
    # relative citation ("3 § skadestånd") stays unlinked rather than minting a
    # sentinel URI under the "query" placeholder basefile. A citation that names
    # its law inline ("12 kap. 1 § brottsbalken") still resolves.
    refs = parser.parse_text(q, context={})
    frags = [r.uri for r in refs if "#" in r.uri]
    return frags[0] if frags else (refs[0].uri if refs else None)


# --------------------------------------------------------------------------
# EU -- named act + optional article
# --------------------------------------------------------------------------

_ART = re.compile(r"^art(?:ikel|icle)?\.?\s*(\d+)(?:[.\s]+(\d+))?", re.IGNORECASE)


@functools.cache
def _named_acts():
    """(alias, celex) pairs for every EU act, longest alias first, so a leading
    act name in a query is matched greedily. Reuses the citation engine's own
    loader (lagrum.load_namedacts) rather than re-reading namedacts.json here --
    one loader, one contract (the union of each act's `label` and `abbr`,
    lower-cased), no drift channel. resolve_eu lower-cases the query before
    matching, so the lower-cased aliases match directly."""
    return sorted(load_namedacts(datasets.NAMEDACTS).items(),
                  key=lambda p: len(p[0]), reverse=True)


# a bare article pinpoint after an act/treaty name -- the same terse law-first
# form the SFS path accepts ("avtalslagen 36"), so "GDPR 28" pins the way
# "GDPR art 28" does. Full-match only: a tail with trailing words is not a
# pinpoint and must not mint a wrong article.
_BARE_ART = re.compile(r"(\d+)(?:[.\s:]+(\d+))?")

# a recital pinpoint after the act name. The three forms are the ones the act
# and its readers use: the preamble's own "(83)", the Swedish "skäl 83" and the
# English "recital 83". The palette resolves "(83" against the page's anchors
# when the reader is already on the act; naming the act ("GDPR (83") must reach
# the same recital from anywhere.
_RECITAL = re.compile(r"\(\s*(\d+)\s*\)?|(?:sk[äa]l|recital)\.?\s*(\d+)",
                      re.IGNORECASE)


def resolve_eu(q):
    """An EU act URI for `q`, deep-linked to an article when the query names one
    ("GDPR art 32" or the terse "GDPR 32" -> .../32016R0679#32) or to a recital
    ("GDPR (83", "GDPR skäl 83" -> .../32016R0679#recital-83), else the act root
    ("IPRED"). None when no act short name leads the query."""
    low = q.lower()
    for label, celex in _named_acts():
        a = label.lower()
        if low.startswith(a) and (len(low) == len(a) or not low[len(a)].isalnum()):
            uri = CELEX_BASE + celex
            rest = q[len(label):].strip()
            recital = _RECITAL.fullmatch(rest)
            if recital:
                return uri + "#recital-" + (recital.group(1) or recital.group(2))
            m = _ART.match(rest) or _BARE_ART.fullmatch(rest)
            if m:
                uri += "#" + m.group(1) + ("." + m.group(2) if m.group(2) else "")
            return uri
    return None


# --------------------------------------------------------------------------
# CoE -- treaty short name + article ("EKMR 6" -> coe/005#A6)
# --------------------------------------------------------------------------

_TREATY_ART = re.compile(r"(?:art(?:ikel|icle)?\.?\s*)?(\d+)(?:[.\s:]+(\d+))?",
                         re.IGNORECASE)


@functools.cache
def _named_treaties():
    """(alias, treaty number) pairs from the curated CoE names, longest alias
    first -- the same greedy leading-name matching the SFS and EU resolvers use."""
    pairs = [(alias, number)
             for number, entry in treaty_names(datasets.COE_NAMES).items()
             for alias in (entry.get("abbr"), entry.get("label")) if alias]
    return sorted(pairs, key=lambda p: len(p[0]), reverse=True)


def resolve_treaty(q):
    """A CoE treaty article URI when the query is a treaty short name plus an
    article pinpoint ("EKMR 6", "Europakonventionen artikel 6.1" ->
    coe/005#A6 / #A6P1), else None. Only fires *with* a pinpoint: a bare
    treaty name keeps resolving to the Swedish incorporation act (the SFS
    resolver), but "EKMR 6" means the convention's article 6, not 6 § of lag
    (1994:1219) -- an anchor that does not even exist there (K4)."""
    low = q.lower()
    for alias, number in _named_treaties():
        a = alias.lower()
        if low.startswith(a) and len(low) > len(a) and not low[len(a)].isalnum():
            m = _TREATY_ART.fullmatch(q[len(alias):].strip())
            if m:
                return treaty_uri(number) + "#" + article_fragment(
                    m.group(1), m.group(2))
    return None


# --------------------------------------------------------------------------
# CJEU -- case number ("C-199/24" -> celex/62024CJ0199)
# --------------------------------------------------------------------------

_ecj_parsers = threading.local()


def _fresh_ecj_parser():
    """This thread's CJEU-case-number parser, reset before every query -- the
    same per-thread, reset-per-call pattern as `_fresh_sfs_parser`. A case
    number is self-contained and absolute (like a CELEX id), so the grammar
    needs no named-law or abbreviation vocabulary, only EURATTSFALL."""
    parser = getattr(_ecj_parsers, "ecj", None)
    if parser is None:
        parser = _ecj_parsers.ecj = LagrumParser(
            {}, basefile="query", parse_types=[EURATTSFALL])
    parser.reset()
    return parser


def resolve_ecj(q):
    """A CJEU judgment's CELEX URI for a case number ("C-199/24", "mål
    C-199/24", "Case C-199/24") -> celex/62024CJ0199, else None. Reuses the
    citation engine's own EURATTSFALL grammar -- the one that already parses
    this form inside EU document bodies (eurlex.parse) -- rather than a
    second, possibly drifting pattern."""
    refs = _fresh_ecj_parser().parse_text(q, context={})
    return refs[0].uri if refs else None


# --------------------------------------------------------------------------
# DV -- case nickname
# --------------------------------------------------------------------------

@functools.cache
def _named_cases():
    return datasets.load_namedcases()


def resolve_dv(q):
    """The published case URI for a known HD nickname ("Instagrambilden"), else
    None. Exact (case-insensitive) match only -- nicknames are distinctive, and
    a loose match would mis-pin an unrelated case."""
    return _named_cases().get(q.strip().lower())


# --------------------------------------------------------------------------
# unified
# --------------------------------------------------------------------------

@functools.cache
def _series():
    """Publication rules shipped as data. Restart the service after an edit.

    Reports and notices have separate coverage rules. Complete intervals are
    explicit corpus assumptions, never inferred from the current year.
    """
    return _citation_rules()["series"]


@functools.cache
def _citation_rules():
    return json.loads((Path(__file__).parent / "data" / "citation_series.json")
                      .read_text(encoding="utf-8"))


def citation_source(uri):
    """The source for a public citation URI, from configured namespaces."""
    for prefix, source in _citation_rules()["namespaces"].items():
        if uri.startswith("https://lagen.nu/" + prefix):
            return source
    if re.fullmatch(r"https://lagen\.nu/[0-9]{4}:[^/#]+(?:#.*)?", uri):
        return "sfs"
    if uri.removeprefix("https://lagen.nu/").split("/", 1)[0] in FS_SLUG.values():
        return "foreskrift"
    return None


def _resolve_page_citation(q):
    """Recognize standalone page citations, including impossible values.

    This input boundary accepts signed pages so a negative page can be
    reported as invalid. The document citation grammar only links unsigned
    values. Full matching avoids turning a range or trailing text into a
    different citation whose absence we would incorrectly certify.
    """
    for series in _series():
        if "citation" not in series:
            continue
        match = re.fullmatch(
            re.escape(series["citation"])
            + r"\s+([0-9]{4})\s*s\.?\s*(-?[0-9]+)\.?", q, re.IGNORECASE)
        if match:
            year, page = map(int, match.groups())
            return [{"uri": "%s%04ds%d" % (series["uri_prefix"], year, page),
                     "source": series["source"]}]
    return []


def absent_is_invalid(uri):
    """Whether a citation *absent from the catalog* is provably invalid.

    Call only after checking existence. A high page number is never proof;
    outside complete coverage, missing reports remain unconfirmed.
    """
    for series in _series():
        match = re.fullmatch(re.escape("https://lagen.nu/") + series["uri_pattern"], uri)
        if match:
            values = match.groupdict()
            if any(int(values[name]) <= 0 for name in series["positive"]):
                return True
            if values.get("number") and any(
                    start <= int(values["number"]) <= end
                    for start, end in series.get("complete_numbers", [])):
                return True
            if values.get("year"):
                year = int(values["year"])
                return (year <= 0 or year > date.today().year
                        or year < series.get("first_year", 0)
                        or year > series.get("last_year", date.today().year)
                        or any(start <= year <= end
                               for start, end in series["complete_years"]))
    return False


def citation_parser():
    """This thread's full citation parser, reset for a new query or document."""
    parser = getattr(_parsers, "all", None)
    if parser is None:
        parser = _parsers.all = LagrumParser(
            load_namedlaws(datasets.NAMEDLAWS), basefile="query",
            abbreviations=load_abbreviations(datasets.NAMEDLAWS),
            named_acts=load_namedacts(datasets.NAMEDACTS),
            parse_types=ALL_PARSE_TYPES)
        # submitted text discusses an act and cites its recitals bare
        # ("Under skäl 26 …"); the corpus parsers leave those unlinked
        parser.bare_recitals = True
    parser.reset()
    return parser


def clear_parser_state():
    """Release submitted text and learned context from this thread's parsers."""
    for cache in (_parsers, _ecj_parsers):
        for parser in vars(cache).values():
            parser.reset()


def resolve_regulation(q):
    """A registered regulation with a Swedish provision before or after it.

    The shared regulation grammar owns the identity, and the statute grammar
    owns the pinpoint. A standalone lookup supplies the context joining them.
    """
    parser = getattr(_parsers, "regulation", None)
    if parser is None:
        parser = _parsers.regulation = LagrumParser(
            {}, basefile="query", parse_types=[FORESKRIFT])
    parser.reset()
    refs = parser.parse_text(q, context={})
    if len(refs) != 1:
        return None
    ref = refs[0]
    before, after = q[:ref.start].strip(), q[ref.end:].strip()
    if before and after:
        return None
    pinpoint = re.sub(r"\s+i$", "", before) if before else after
    if pinpoint:
        pins = _fresh_sfs_parser().parse_text(
            _normalize_pinpoint(pinpoint), context={"law": "query"})
        if len(pins) == 1 and "#" in pins[0].uri:
            return ref.uri + "#" + pins[0].uri.split("#", 1)[1]
    return ref.uri


@functools.cache
def _instrument_names():
    names = list(TREATIES.items())
    names += [(name.lower(), entry["target"])
              for entry in treatyref.instruments().values()
              for name in entry["names"]]
    names += [(name.lower(), target) for pattern, target, name, context
              in treatyref.patterns() if name and context is None]
    return sorted(set(names), key=lambda item: -len(item[0]))


def _resolve_instrument(q):
    """Bare treaty names and name-first articles, in Swedish or English."""
    targets = ["https://lagen.nu/" + target for name, target in _instrument_names()
               if q.lower() == name]
    if targets:
        return targets
    for name, _target in _instrument_names():
        if q.lower().startswith(name + " "):
            tail = q[len(name):].strip()
            if _TREATY_ART.fullmatch(tail):
                # Both engines own their article grammars (EU, CoE, UN/IHL).
                # Let them build the fragment instead of guessing one here.
                number = re.sub(r"^art(?:ikel|icle)?\.?\s*", "", tail, flags=re.I)
                refs = citation_parser().parse_text(
                    "artikel %s i %s" % (number, name), context={})
                if refs:
                    return [ref.uri for ref in refs]
                return [ref["uri"] for ref in treatyref.references(
                    "article %s of the %s" % (number, name))]
    return []


def _resolve_general(q):
    """Citation engine families not handled by name-first shortcuts."""
    # Page citations have an exact input grammar above. Do not let the
    # scanning parser reinterpret a rejected range or decimal as its prefix.
    if any(re.match(re.escape(series["citation"]) + r"\s+[0-9]{4}\s*s\b", q, re.I)
           for series in _series() if "citation" in series):
        return []
    if uri := courtids.resolve(q):
        return [uri]
    if q.startswith("https://lagen.nu/") and citation_source(q):
        return [q]
    if re.fullmatch(r"(?:CELEX\s*:\s*)?[01356][0-9]{4}[A-Z][A-Z0-9/()_-]*", q, re.I):
        return [CELEX_BASE + re.sub(r"^CELEX\s*:\s*", "", q, flags=re.I).upper()]
    if match := re.fullmatch(r"(?:C?ETS\s*(?:No\.?\s*)?|CoE\s+)([0-9]+)", q, re.I):
        return [treaty_uri(match.group(1))]
    if re.fullmatch(r"(?:ICRC\s+)[0-9]+", q, re.I):
        return ["https://lagen.nu/icrc/" + str(int(q.split()[-1]))]
    if re.fullmatch(r"(?:UNTC\s+)[IV]+-[0-9]+", q, re.I):
        return ["https://lagen.nu/untc/" + q.split()[-1].upper()]
    if re.fullmatch(r"(?:HUDOC\s+)?001-[0-9]+", q, re.I):
        return ["https://lagen.nu/dom/echr/" + q.split()[-1]]
    instruments = _resolve_instrument(q)
    if instruments:
        return instruments
    return [ref.uri for ref in citation_parser().parse_text(q, context={})]


@functools.cache
def named_case_names() -> list[str]:
    """The popular names of cases ("Strukturen"), lower-cased."""
    return list(_named_cases())


def citation_names() -> list[str]:
    """Curated standalone names used by both lookup and document extraction."""
    return sorted({name for name, _ in (
        _leading_laws() + _named_acts() + _named_treaties() + _instrument_names())}
        | set(_named_cases()), key=lambda name: (-len(name), name))


def resolve(q):
    """Every resource the query resolves to as `{"uri", "source"}` (uri carries
    its #fragment), in priority order CoE treaty, SFS, EU, CJEU case, DV --
    usually 0 or 1. The treaty resolver leads because it only claims a query
    with an article pinpoint, and there it must outrank the SFS reading of the
    same alias.
    Pure: the caller confirms each uri against the catalog before surfacing it."""
    q = (q or "").strip()
    if not q:
        return []
    if q.startswith("https://lagen.nu/") and (source := citation_source(q)):
        return [{"uri": q, "source": source}]
    out = _resolve_page_citation(q)
    for source, fn in (("foreskrift", resolve_regulation),
                       ("coe", resolve_treaty), ("sfs", resolve_sfs),
                       ("eurlex", resolve_eu), ("eurlex", resolve_ecj),
                       ("dv", resolve_dv)):
        uri = fn(q)
        if uri and uri not in [o["uri"] for o in out]:
            hit = {"uri": uri, "source": citation_source(uri) or source}
            # a bare span name's own rationale ("cookielagen" -> why 9 kap.
            # 28 § LEK carries that name) is worth more to a reader than the
            # provision's own words, which is what a resolved hit shows by
            # default (pins.resolved_results) -- so it rides along here
            # rather than requiring a second lookup keyed on the same query.
            if span := _named_spans().get(q.lower()):
                # plain text, not the markdown `render_chapter`'s banner
                # renders it as: a search snippet is never rich HTML, and a
                # `[label](sfs:...)` cross-reference (mobiltelefonlagen's
                # disambiguation against skollagen) would otherwise show its
                # raw markdown brackets to the reader
                hit["reason"] = text.runs_text(markdown.to_runs(span.reason))
            out.append(hit)
    if not out:
        for uri in _resolve_general(q):
            source = citation_source(uri)
            if source and uri not in [hit["uri"] for hit in out]:
                out.append({"uri": uri, "source": source})
    return out
