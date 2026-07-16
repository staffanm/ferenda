"""Court-decision identity: the canonical published URI a case is minted at, and
the display title a reader sees.

Both are cross-layer contracts, keyed on a case artifact's metadata, not on dv
internals -- so they live here in lib, read identically by the source that stamps
them at parse time, the catalog row that labels every listing and inbound citation,
and the renderer's page heading. They depend only on the shared citation engine
(`lib.lagrum`) and the URI/path grammar (`lib.layout`), never the reverse.

Two domain facts drive the naming:

  * a case's *identity* is the referat whose minted URI matches the document's --
    NJA's page form ("NJA 2025 s. 897"), never the löpnummer ("NJA 2025:58").
    The löpnummer is real metadata, but it is kept out of every identity string.
    A raw verdict that has no referat yet identifies by its målnummer ("Ö 3043-25").
  * Högsta domstolen names its precedents (the "namngivna rättsfall" list,
    `namedcases`). When a case is named, the nickname leads: "Meteoriten
    (NJA 2025 s. 897)", "Umgängesstödet (Ö 3043-25)". The nickname appears nowhere
    in the case text, so this is the only place it is attached.

`case_label` is the one display entry point; `case_uri` the one identity minter.
"""

import functools
import json

from .datasets import NAMEDCASES
from .lagrum import RATTSFALL, LagrumParser
from .layout import case_slug as slug

# The old published-verdict URI used the publisher's ``abbrSlug`` from
# ``lagen/nu/res/uri/swedishlegalsource.slugs.ttl``. Keep the mapping explicit:
# a court code is source vocabulary, while these values are a public URI
# contract (not a lowercasing rule -- notably MIOD -> mig and MMOD -> mmd).
COURT_URI_SLUG = {
    "ADO": "ad",
    "HDO": "hd",
    "HFD": "hfd",
    "HGO": "hgo",
    "HNN": "hnn",
    "HON": "hon",
    "HSB": "hsb",
    "HSV": "hsv",
    "HVS": "hvs",
    "HYOD": "hsv",  # Svea hovrätts hyresrättsliga avgöranden
    "KGG": "kgg",
    "KJO": "kjo",
    "KST": "kst",
    "KSU": "ksu",
    "MDO": "md",
    "MIOD": "mig",
    "MMOD": "mmd",
    "MOD": "mod",
    "PBR": "pbr",
    "PMOD": "pmod",
    "REGR": "regr",
    "RHN": "rhn",
}


@functools.cache
def _rattsfall_parser():
    return LagrumParser({}, basefile="dom", parse_types=[RATTSFALL])


def case_uri(cid):
    """The published document URI for a court decision.

    A referat case (the vast majority, and the only kind a citation can name)
    is minted by running its referat through the *same* RATTSFALL citation
    parser, so the document URI is byte-identical to what any reference to it
    produces -- the old pipeline's `dom/{serie}/{year}:{nr}` /
    `dom/nja/{year}s{page}` / `.../not/{n}` scheme. This is the published
    identifier the old site used; it must not change.

    This string-only helper retains a stable slug fallback when it receives no
    recognizable referat. Artifact projection has the court, målnummer and date
    needed to call :func:`verdict_uri` and restore the old non-referat scheme."""
    refs = _rattsfall_parser().parse_text(cid, context={})
    return refs[0].uri if refs else "https://lagen.nu/dom/" + slug(cid)


def verdict_uri(court, malnummer, avgorandedatum):
    """The old published URI for a verdict that has no referat.

    Mirrors the legacy COIN template
    ``/dom/{publisher}/{malnummer}/{avgorandedatum}`` and its space-removing
    slug transform. The old minter preserved uppercase Swedish målnummer
    prefixes (``Ö``, ``Ä``), so this does too.
    """
    assert court in COURT_URI_SLUG, "no published verdict URI slug for %s" % court
    assert malnummer, "a verdict URI requires a målnummer"
    assert avgorandedatum, "a verdict URI requires an avgörandedatum"
    malnummer = (malnummer.replace("ä", "ae").replace("å", "aa")
                 .replace("é", "e").replace("ö", "oe").replace(" ", ""))
    return "https://lagen.nu/dom/%s/%s/%s" % (
        COURT_URI_SLUG[court], malnummer, avgorandedatum)


@functools.cache
def _names():
    """(by_uri, by_malnr): the nickname maps from HD's named-rättsfall snapshot. A
    case with an assigned NJA page is keyed by its URI; one still at "NJA YYYY s.
    xxx" (page unassigned) is keyed by målnummer -- so a raw verdict is named before
    its referat exists."""
    assert NAMEDCASES.exists(), (
        "%s missing -- a broken checkout, not an unharvested snapshot; run "
        "`lagen dv namedcases` or restore the committed file" % NAMEDCASES)
    data = json.loads(NAMEDCASES.read_text(encoding="utf-8"))
    by_uri, by_malnr = {}, {}
    for c in data["cases"]:
        if c.get("uri"):
            by_uri[c["uri"]] = c["namn"]
        elif c.get("malnr"):
            by_malnr[c["malnr"]] = c["namn"]
    return by_uri, by_malnr


def canonical_referat(art):
    """The referat that identifies the case: the one whose minted URI matches the
    document's (the NJA page form, not the löpnummer). None for a raw verdict (no
    referat yet). Falls back to the first referat if none mints the exact URI."""
    referat = art.get("referat") or []
    for r in referat:
        if case_uri(r) == art["uri"]:
            return r
    return referat[0] if referat else None


def lopnummer(art):
    """The löpnummer referat(s) -- every referat that is not the canonical one
    ("NJA 2025:58" beside the canonical "NJA 2025 s. 897"). Kept as metadata, never
    part of an identity string."""
    canon = canonical_referat(art)
    return [r for r in (art.get("referat") or []) if r != canon]


def case_id(art):
    """The case's identity string: its canonical referat, or -- a raw verdict with
    no referat -- its målnummer. Never the löpnummer."""
    ref = canonical_referat(art)
    if ref:
        return ref
    malnr = art.get("malnummer") or []
    return malnr[0] if malnr else (art.get("court") or art["uri"])


def given_name(art):
    """HD's nickname for the case, if any -- by URI (assigned NJA page) or by
    målnummer (a raw verdict whose page isn't set yet)."""
    by_uri, by_malnr = _names()
    if art["uri"] in by_uri:
        return by_uri[art["uri"]]
    for m in art.get("malnummer") or []:
        if m in by_malnr:
            return by_malnr[m]
    return None


def case_label(art):
    """The case's display title: "Namn (Identitet)" when HD has named it, else the
    bare identity -- "Meteoriten (NJA 2025 s. 897)", "Umgängesstödet (Ö 3043-25)",
    "NJA 1981 s. 1"."""
    cid = case_id(art)
    name = given_name(art)
    return "%s (%s)" % (name, cid) if name else cid
