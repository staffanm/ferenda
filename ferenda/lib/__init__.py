"""Shared horizontal libraries — cross-source machinery that the verticals
call into, but which never calls back into a source.

- ``lagrum``  — the Lark citation engine (LAGRUM / KORTLAGRUM / FORARBETEN /
  EULAGSTIFTNING / RATTSFALL / …), parameterized by parse-type set.
- ``util``    — small generic helpers (whitespace normalization, …).
- ``errors``  — pipeline control signals (``SkipDocument``).
"""
