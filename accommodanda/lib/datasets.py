"""Canonical filesystem paths of the curated *named-resource* datasets that ship
in the package source tree (config.py deliberately locates only the *corpus*, not
these). Each dataset is co-located with the vertical that owns and curates it:

  * ``NAMEDLAWS``  -- SFS named laws + abbreviations ("avtalslagen", "BrB" ->
    1915:218 / 1962:700). Hand-edited; also feeds the citation parser at parse
    time (every vertical's LagrumParser), not just ⌘K.
  * ``NAMEDACTS``  -- EU acts by short name ("GDPR", "IPRED" -> CELEX). Hand-edited.
  * ``NAMEDCASES`` -- HD cases by nickname ("Instagrambilden" -> NJA referat).
    Auto-harvested from Högsta domstolen's official list (dv.namedcases), with
    the harvested JSON committed as the shipped snapshot.

A single source of truth for these paths, so the ~7 parse-time callers and the
⌘K resolver agree without each re-deriving the location.
"""

from pathlib import Path

_PKG = Path(__file__).resolve().parent.parent

NAMEDLAWS = _PKG / "sfs" / "data" / "namedlaws.json"
NAMEDACTS = _PKG / "eurlex" / "data" / "namedacts.json"
NAMEDCASES = _PKG / "dv" / "data" / "namedcases.json"
