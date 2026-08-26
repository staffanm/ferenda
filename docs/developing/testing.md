# Testing and the golden corpus

What a bare `pytest` covers, the two kinds of correctness check, and what to run
after a parser change.

A bare `pytest` runs the whole suite (`pyproject.toml` scopes collection to
`test/test_*.py` and excludes the `test/files/` fixture tree). Two kinds of
check matter here:

- **Hand-authored fixtures** under `test/files/` are `input → expected output`
  pairs someone wrote by hand, so they are an **oracle** — the expected output
  is correct by construction. Example: `test/files/legalref/` drives the
  citation-engine tests; `test/files/sfs/parse/` drives the SFS structure
  tests. When you fix a parser bug, add a fixture that captures the correct
  output, so the bug can never silently return.

- **Reference ("golden") corpora** are the frozen output of the *previous
  generation* of the system (kept in a sibling `../ferenda.old` checkout — its
  original pipeline can no longer run, so its output is the spec). These are
  used as a **change-detector, not an oracle**: when the current parser and the
  reference disagree, it is *investigated*, not blindly accepted — the current
  parser is right a fair share of the time (the reference has its own stale and
  defective entries). The comparison tools live in `tools/corpus/golden_*.py`, and
  known-benign difference families are catalogued so a real regression stands
  out against them.

The practical rule for a parser change: run the relevant fixture suite (must
stay green — it's an oracle) and the golden comparison (investigate every new
difference; a genuine improvement over the reference is expected and fine, a
genuine regression is not).
