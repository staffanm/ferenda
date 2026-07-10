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
    -> ext/celex/<CELEX>#<N>.
  * DV  -- a case nickname ("Instagrambilden") -> the published NJA case URI.

Pure and catalog-free: it maps a string to a URI. Whether that URI is hosted
(and its title) is the caller's concern -- the /search endpoint confirms it
against the catalog before pinning it, so an alias for a not-yet-parsed document
simply doesn't surface.
"""

import functools
import re

from . import datasets
from .lagrum import (
    LagrumParser,
    lagrum_uri,
    load_abbreviations,
    load_namedacts,
    load_namedlaws,
)

CELEX_BASE = "https://lagen.nu/ext/celex/"


# --------------------------------------------------------------------------
# SFS -- nickname/abbr + chapter/§ pinpoint, in the order ⌘K users type
# --------------------------------------------------------------------------

@functools.cache
def _sfs_parser():
    """The cached citation parser. Construction (grammar + dataset loading) is
    the expensive part, so it is cached; its *state* is stateful and leaks
    across parse_text calls ("samma lag", learned aliases), so every query must
    reset it first via `_fresh_sfs_parser` -- never call this directly."""
    return LagrumParser(load_namedlaws(datasets.NAMEDLAWS), basefile="query",
                        abbreviations=load_abbreviations(datasets.NAMEDLAWS))


def _fresh_sfs_parser():
    """The cached parser with a clean per-query state. The resolver treats each
    ⌘K query as an independent one-shot citation, so state accumulated by an
    earlier query (a named law for a later "samma lag", an alias taught by
    "lagen (1999:123) om ...") must not leak into the next -- the same
    reset-per-document pattern the verticals use before parsing a new document."""
    parser = _sfs_parser()
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
    a nickname is."""
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


def resolve_eu(q):
    """An EU act URI for `q`, deep-linked to an article when the query names one
    ("GDPR art 32" -> .../32016R0679#32), else the act root ("IPRED"). None when
    no act short name leads the query."""
    low = q.lower()
    for label, celex in _named_acts():
        a = label.lower()
        if low.startswith(a) and (len(low) == len(a) or not low[len(a)].isalnum()):
            uri = CELEX_BASE + celex
            m = _ART.match(q[len(label):].strip())
            if m:
                uri += "#" + m.group(1) + ("." + m.group(2) if m.group(2) else "")
            return uri
    return None


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

def resolve(q):
    """Every resource the query resolves to as `{"uri", "source"}` (uri carries
    its #fragment), in priority order SFS, EU, DV -- usually 0 or 1. Pure: the
    caller confirms each uri against the catalog before surfacing it."""
    q = (q or "").strip()
    if not q:
        return []
    out = []
    for source, fn in (("sfs", resolve_sfs), ("eurlex", resolve_eu),
                       ("dv", resolve_dv)):
        uri = fn(q)
        if uri and uri not in [o["uri"] for o in out]:
            out.append({"uri": uri, "source": source})
    return out
