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

A single source of truth for these paths, so the ~7 parse-time callers and the
⌘K resolver agree without each re-deriving the location.
"""

import json
from pathlib import Path

_PKG = Path(__file__).resolve().parent.parent

NAMEDLAWS = _PKG / "sfs" / "data" / "namedlaws.json"
NAMEDACTS = _PKG / "eurlex" / "data" / "namedacts.json"
COE_NAMES = _PKG / "coe" / "data" / "names.json"
ICRC_NAMES = _PKG / "icrc" / "data" / "names.json"
UNTC_TREATIES = _PKG / "untc" / "data" / "treaties.json"
ICC_DECISION_TYPES = _PKG / "icc" / "data" / "decision_types.json"
NAMEDCASES = _PKG / "dv" / "data" / "namedcases.json"
NAMEDEUCASES = _PKG / "eurlex" / "data" / "casenames.json"


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
