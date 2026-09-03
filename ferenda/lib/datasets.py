"""Canonical filesystem paths of the curated *named-resource* datasets that ship
in the package source tree (config.py deliberately locates only the *corpus*, not
these). Each dataset is co-located with the vertical that owns and curates it:

  * ``NAMEDLAWS``  -- SFS named laws + abbreviations ("avtalslagen", "BrB" ->
    1915:218 / 1962:700). Hand-edited; also feeds the citation parser at parse
    time (every vertical's LagrumParser), not just ⌘K.
  * ``NAMEDACTS``  -- EU acts by short name ("GDPR", "IPRED" -> CELEX). Hand-edited.
    Its sector-1 entries (the treaties + Charter) and ``COE_NAMES`` feed the
    always-on treaty linking (lib/lagrum.load_treaties), not the opt-in name path.
  * ``COE_NAMES``   -- Council-of-Europe treaties by name ("europakonventionen" ->
    ETS/CETS number). Hand-edited.
  * ``NAMEDCASES`` -- HD cases by nickname ("Instagrambilden" -> NJA referat).
    Auto-harvested from Högsta domstolen's official list (dv.namedcases), with
    the harvested JSON committed as the shipped snapshot.
  * ``NAMEDEUCASES`` -- EU cases by usual name ("Schrems II" -> CELEX). The Court
    assigns no such name as data, so it is auto-harvested from Wikidata
    (eurlex.casenames), with the harvested JSON committed as the shipped snapshot.
  * ``FS_SERIES``   -- the författningssamlingar by fs slug: printed designation
    ("aafs" -> "ÅFS"), official name ("Åklagarmyndighetens författningssamling")
    and, for a series whose agency was renamed or disbanded, the ``successor``
    slug that carries it on (difs -> imyfs). Hand-edited, against bilaga 1 to
    författningssamlingsförordningen (1976:725) and its historical lydelser.

A single source of truth for these paths, so the ~7 parse-time callers and the
⌘K resolver agree without each re-deriving the location.
"""

import json
from pathlib import Path

from .. import config

_PKG = Path(__file__).resolve().parent.parent

NAMEDLAWS = _PKG / "sfs" / "data" / "namedlaws.json"
NAMEDACTS = _PKG / "eurlex" / "data" / "namedacts.json"
EU_TREATIES = _PKG / "eurlex" / "data" / "treaties.json"
COE_NAMES = _PKG / "coe" / "data" / "names.json"
ICRC_NAMES = _PKG / "icrc" / "data" / "names.json"
UNTC_TREATIES = _PKG / "untc" / "data" / "treaties.json"
ICC_DECISION_TYPES = _PKG / "icc" / "data" / "decision_types.json"
# the English names an international court cites a treaty by, mapped onto the
# corpus document carrying its text. Lives in lib/data, not in a source's, because
# it spans three corpora (icrc/untc/coe) and two readers (icj/icc) -- no vertical
# owns it (rule:lib-never-imports-vertical)
TREATY_NAMES = _PKG / "lib" / "data" / "treaty_names.json"
NAMEDCASES = _PKG / "dv" / "data" / "namedcases.json"
NAMEDEUCASES = _PKG / "eurlex" / "data" / "casenames.json"
# ECHR cases by party names and application number, for the citation engine's
# "Osman mot Förenade kungariket" resolution. Auto-generated from the stored
# HUDOC records (hudoc.casenames), committed as the shipped snapshot.
EMD_CASES = _PKG / "hudoc" / "data" / "casenames.json"
# the Swedish names of the respondent states, mapped onto the snapshot's own
# normalized respondent keys ("Förenade kungariket" -> ["united kingdom"]).
# Hand-edited; a state whose English key shifted over the corpus's lifetime
# maps to every key it has borne ("Turkiet" -> ["turkey", "türkiye"]).
EMD_RESPONDENTS = _PKG / "hudoc" / "data" / "respondents_sv.json"
FS_SERIES = _PKG / "foreskrift" / "data" / "series.json"
# held court decisions by the case number they were filed under ("T 3-08" ->
# NJA 2009 s. 672), for the citation engine's "Högsta domstolens dom 2009-11-03
# T 3-08" resolution. The one path here that is NOT in the package: it is a
# derived index of the parsed dv artifacts (dv.casenumbers writes it) and lives
# in the data root beside the case-law identity index. The artifact tree is
# `layout`'s, but layout sits in catalog's import chain, which ends in
# `malnummer` -- the parse-time reader of this file -- so the one segment is
# spelled here; test_dv_casenumbers pins it to layout.DOM_INDEX's directory.
CASENUMBERS = config.DATA / "artifact" / "dom" / "casenumbers.json"
# JO ämbetsberättelse pages ("2005/06 s. 171") mapped onto the diarienummer
# that mints the decision's URI. Auto-generated from the JO artifacts
# (avg.arsberattelse), committed as the shipped snapshot.
JO_ARSBERATTELSE = _PKG / "avg" / "data" / "arsberattelse.json"


def load_emd_cases(path=EMD_CASES):
    """The ECHR case snapshot: {"cases": {"applicant|respondent|serial":
    [[kind, date, itemid], …]}, "appnos": {…}} -- candidates, not pre-picked
    winners, because only the citation's own context (a printed date) can tell
    a chamber judgment from the Grand Chamber's. Pure JSON load with no source
    dependency. Empty if not generated yet."""
    if not path.exists():
        return {"cases": {}, "appnos": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def load_emd_respondents(path=EMD_RESPONDENTS):
    """The Swedish respondent-state names, mapped onto the casenames
    snapshot's own normalized respondent keys ("Förenade kungariket" ->
    ["united kingdom"]). Pure JSON load with no source dependency."""
    return {sv: keys
            for sv, keys in json.loads(path.read_text(encoding="utf-8")).items()
            if not sv.startswith("_")}


def load_casenumbers(path=CASENUMBERS):
    """The case-number snapshot: {"numbers": {"T 3-08": [[court, date, local
    uri], …]}, "courts": {court code: its name}} -- candidates, not pre-picked
    winners, because 298 of the held numbers name more than one decision and
    only the citation's own court and date tell them apart. Pure JSON load with
    no source dependency. Unlike the shipped snapshots it is written into the
    data root by `lagen dv casenumbers` (see CASENUMBERS), so a missing file
    means that has not run on this data root, and raises rather than silently
    unlinking every case-number citation of a whole parse run (rule:fail-fast).
    An empty dict here would be doubly quiet: `build.hash_files` skips a missing
    path, so the parse recipe would not notice either, and `malnummer._index`
    caches, so the emptiness would outlive a snapshot written mid-run."""
    assert path.exists(), (
        "%s is missing -- run `lagen dv casenumbers` (or rsync the artifact "
        "tree) before parsing sources that resolve case numbers" % path)
    return json.loads(path.read_text(encoding="utf-8"))


def load_jo_arsberattelse(path=JO_ARSBERATTELSE):
    """The JO ämbetsberättelse snapshot: {"2005/06 s. 171": ["2042-2004"], …}
    (values are lists -- two decisions can start on one page). Pure JSON load
    with no source dependency. The snapshot is committed, so a missing file is
    a broken checkout and raises rather than silently unlinking every
    ämbetsberättelse citation (rule:fail-fast)."""
    return json.loads(path.read_text(encoding="utf-8"))


def load_fs_series(path=FS_SERIES):
    """The hand-edited författningssamling registry: {fs slug: {designation,
    title, successor?}}. A pure JSON load with no source dependency, so the
    facet scheme and the browse renderer read it straight from here."""
    return json.loads(path.read_text(encoding="utf-8"))


def load_namedcases(path=NAMEDCASES):
    """Map each lower-cased HD-case nickname to its resolvable case URI, from the
    committed snapshot -- only rows that carry a URI (a determinate referat). The
    snapshot is produced by `dv.namedcases` (the harvest owns the case-URI minting);
    reading it back is a pure JSON load with no source dependency, so the ⌘K
    resolver reads it straight from here. Empty if not harvested yet."""
    if not path.exists():
        return {}
    return {c["namn"].lower(): c["uri"]
            for c in json.loads(path.read_text(encoding="utf-8"))["cases"]
            if c.get("uri")}
