# Conventions review: `accommodanda/` whole-package — 2026-07-02

First full run of the new guardrail system (docs/conventions.md +
conventions-enforcer methodology) over all ~21,100 lines / 76 files: the
mechanical layer package-wide plus eight parallel judgment-level area
reviews. Every CRITICAL and HIGH finding below was independently verified
against the code (or reproduced live) before inclusion; agent findings
that did not survive verification, or duplicated the 2026-07-01 review's
OPEN items, are not listed.

## Mechanical layer — green

- ruff (`F,I,B,BLE,PLC0415,S110,S112,RUF100`): clean.
- ty: clean.
- Layer-boundary checker: clean (8 allowlisted §3.1 imports, 0 new, 0 stale).
- Bare suppressions: 11 found package-wide, all **fixed in this pass**
  (rationales naming the constraint; mostly stub-less lxml + untyped
  artifact dicts).
- Tests: 804 passed; 2 pre-existing failures (see §Known failures).

## CRITICAL (verified) — all three **FIXED** same day

Each fix landed with a regression test: `test_api.py` (stub row served
with empty artifact), `test_eurlex_download.py` (all-rejected → no
notice, `is_downloaded` stays false; success → notice), and
`test_foreskrift_parse.py` (amendment uri minting incl. mixed-prefix +
unreadable-identifier cases, source url preserved).

Operational follow-ups: existing föreskrift artifacts still carry the
`uri: ""` amendments until a forced foreskrift re-parse (the freshness
contract hashes inputs, not parser code); any metadata-only eurlex dirs
already on disk still mask their CELEX until `prune_empty` sweeps them.

Diff review of the fix itself (conventions-enforcer, scoped to the 7
changed files) surfaced one more real bug sharing C1's root cause:
**`api/app.py:327` `documents_endpoint`** (the listing endpoint) computed
`updated` from `Path(path).stat().st_mtime` unguarded for `path=''` stub
rows — `Path('')` aliases to the server process's cwd, so `.exists()` is
`True` and every stub reported a plausible-but-meaningless `updated`
timestamp instead of `None`. **Fixed** alongside C1, with its own
regression test (`test_documents_begrepp_stub_has_no_updated_timestamp`).

- **C1 `api/app.py:345`** — `/api/v1/document` 500s (`IsADirectoryError`)
  for every synthesized begrepp stub: stubs are real catalog rows with
  `path=''` and `Path('')` is a directory. The adjacent `or b"{}"`
  fallback guards a state that cannot occur (SkipDocument placeholders
  never get catalog rows) while the documented, reachable stub case
  crashes. rule:fail-fast.
- **C2 `eurlex/download.py:508`** — `store_document` writes `notice.ttl`
  *before* any content fetch succeeds; if every candidate fails
  `_content_ok` the metadata-only dir the docstring forbids is created
  anyway, and `is_downloaded` (keyed on notice.ttl) permanently masks the
  work from every later incremental run. rule:fail-fast.
- **C3 `foreskrift/parse.py:302`** — every amendment in every föreskrift
  artifact ships `uri: ""`: no harvest path writes a `uri` key
  (`harvest.py:317` stores `{identifier, url}`), so the `.get("uri", "")`
  default always fires and the real `url` is dropped record→artifact.
  The `identifier` fallback is dead too (key always present, sometimes
  `None`, despite the `str` type). rule:fail-fast, rule:artifact-is-truth.

## HIGH (verified)

- **H1 `lib/resolve.py:44-47`** — `@functools.cache` shares one
  *stateful* `LagrumParser` instance across all ⌘K queries; resolution is
  nondeterministic across queries/users. Reproduced: after
  `12 kap. 1 § brottsbalken`, the query `5 § samma lag` resolves to
  `1962:700#P5`; `7 § hittepålagen (1999:123)` teaches the alias and a
  later `9 § hittepålagen` resolves to `1999:123#P9`. Cache construction,
  not instance state. rule:fail-fast.
- **H2 `lib/resolve.py:102`** — the placeholder `basefile="query"` mints
  sentinel URIs for relative citations: `resolve_sfs("3 § skadestånd")`
  → `https://lagen.nu/query#P3` (reproduced). Use the documented
  nobaseuri mode (`context={}`) instead. rule:fail-fast.
- **H3 `lib/catalog.py:390-416`** — re-indexing a changed artifact never
  deletes its stale `fragments` rows (`INSERT OR REPLACE` only); shed or
  renamed node ids leave ghost snippet rows forever and the catalog stops
  being faithfully rebuildable. search.py pre-deletes for exactly this
  reason; catalog needs the same. rule:artifact-is-truth.
- **H4 `lib/markdown.py:94,106`** — frontmatter validation of
  hand-authored input via `assert`; under `-O` list items get filed under
  a `None` key (wrong metadata, no signal). Same class as the §3.3 fix.
  rule:errors-drive-retry-use-raise.
- **H5 `sfs/correspond.py:163`** — load-bearing `assert fk` on a *data*
  condition in the LLM correspondence pipeline; under `-O` an empty
  `[[KOMMENTAR]]` burns the LLM call and writes a plausible zero-edge
  `.corr` sidecar. rule:errors-drive-retry-use-raise.
- **H6 `wiki/annotate.py:77-80`** — `assert resp.content[:4] == b"%PDF"`
  on an untrusted remote response; under `-O` a WAF error page is cached
  as `.pdf` and the `cached.exists()` short-circuit serves the poisoned
  file forever (compounded by plain `write_bytes` instead of
  `write_atomic`). rule:errors-drive-retry-use-raise, rule:fail-fast.
- **H7 `eurlex/download.py:430-441`** — wrapper-only Formex
  manifestations ship the `.doc.xml` manifest as the act's content:
  single-item manifestations never enter wrapper disambiguation, and
  `(real or items)[0]` knowingly keeps a wrapper when all items are
  wrappers. `bulk.py` handles the identical case correctly (degrade to
  html). rule:fail-fast.
- **H8 `eurlex/parse_pdf.py:48-52`** — `_ocr` treats a missing `ocrmypdf`
  binary (broken environment) like a bad document: prints to stderr,
  returns `None`, and the pipeline publishes an empty artifact with the
  failure recorded nowhere. Not a sanctioned resilience point.
  rule:no-catch-log-continue, rule:fail-fast.
- **H9 `eurlex/parse_pdf.py:29-124`** — reimplements `lib/pdftext`
  (subprocess extraction, line grouping, median-gap reflow, byte-identical
  `_dehyphenate`) *including* the top-only span-grouping bug pdftext
  documents as fixed. The real deltas (`-hidden`, offset flattening) are
  parameters for lib, not grounds for a fork. rule:second-use-goes-to-lib.
- **H10 `avg/download.py` + `avg/parse.py:76`** — the §3.4 promotion
  trigger fired and produced copies instead: avg is the **fourth** copy of
  the harvest-engine core (`.complete` marker, backfill gating, Reporter
  loop) and the **sixth** copy of the `parser.state = type(parser.state)()`
  reset (also dv ×2, eurlex, forarbete, foreskrift — belongs as
  `LagrumParser.reset()` in lib). rule:second-use-goes-to-lib.
- **H11 `lib/render.py:106`** — `art.get("annotates")` + silent
  `continue`: wiki/parse.py guarantees the key on every kommentar
  artifact, so the guard only fires on corruption — and its effect is an
  entire commentary silently vanishing from every statute rail.
  rule:fail-fast.

## Root cause: the known `test_resolve` failure

Commit d216ecc1 (2026-06-29) added Swedish `label`s to the GDPR entry in
`eurlex/data/namedacts.json` and rewrote the dataset's `_comment`
contract, updating `test_lagrum.py` but never adjudicating
`test_resolve.py::test_eu_extractable_label_pruned_abbr_still_resolves`,
which pins the *old* pruned-labels contract. Decide: the pruning premise
is obsolete (update the test, record the deliberate change) or ⌘K labels
must stay pruned (give the parse engine its own alias channel). Related:
`lib/resolve.py:114-128` re-implements `lagrum.load_namedacts` — two
loaders for one hand-edited file is the drift channel that let this slip.

## MEDIUM (selected; all agent-reported, spot-checked)

Fail-fast / dead fallbacks: `render.py:68,485,506,542,1192` (`or {}` on
always-populated Site fields); `render.py:1556` (dead 404 tolerance with
wrong comment); `eurlex/annotate.py:33` (`.get("title", …)` dead default
that misses the real `None`-title gap); `sfs/register.py:93-97`
(`lookup_resource` guesses unknown org labels into URI positions with no
record on the production path); `wiki/parse.py:249,259` (duplicate
`annotates:`/`title:` silently drops a whole file); `lagrum.py:607`
(interleave silently drops overlapping refs — live via eurlex
cites+uses); `lib/llm.py:47-48` (truncation check is an assert);
`lib/concepts.py:31,101` (hand-edited override file lives in the frozen
`lagen/` tree behind an `if exists()` fallback — rule:legacy-read-only);
`build.py:1708-1714` (report() double-subtracts errored docs; "skipped"
can go negative).

Speculative surface: `render.py:785` (`DV_STRUCTURAL` dead),
`layout.py:217` (`metadata` param unread), `facets.py:345` (`source`
param unread), `markdown.py:38` (`RE_WS` dead), `sfs/extract.py:16-35`
(`keep_expired` dead branch), `sfs/download.py:255` (`list_basefiles`
unused by the driver its docstring claims), `dv/structure.py:208-209`
(dead second `close_to(2)`), `foreskrift/harvest.py:483` (unused `log`
param).

Duplication re-accreting: browser-UA constants in
`foreskrift/agencies.py:47` and `wiki/guidance_discover.py:44` (3rd/4th
copies post-consolidation); `AVG_PARSE_TYPES` 4th copy of the grammar
list (export from lib/lagrum); `BASE = "https://lagen.nu/"` ×4;
`render.py` directive-link construction ×3 intra-module;
`eurlex/parse_pdf.py:26` date regex vs `parse_html._eu_date`; sfs
source-dispatch triplicated (`__main__.py` ×2 + `_validate.py`);
`wiki/annotate.py:80,247`, `guidance_discover.py:282` plain writes where
`write_atomic` is the consolidated helper.

Typing (rule:own-typed-model): bare `list`/`object` fields with the real
type in comments — `sfs/model.py` (throughout), `sfs/tokenizer.py`
event dataclasses (`uphor/ikrafttrader: object`), `dv/model.py:52-53`,
`lagrum.py:625` (`DocState.namedlaws`), `render.py:46-54` (`Site`).

Doc drift (rule:docs-follow-structure): `accommodanda/__init__.py`
docstring lists 2 of 7 verticals; avg missing from every API source
enumeration + api/README (works but undocumented); `--only` help claims
forarbete-only (3 consumers); dv stale `python -m` banners ×3;
`foreskrift/harvest.py` docstring claims FFFS-only architecture;
`foreskrift/model.py` duplicated bullet; `foreskrift/parse.py:269`
promises a consolidation fallback that doesn't exist; avg docstring
claims oldest-first backfill but posts newest-first.

Suppressions: `lib/search.py:295` ty-ignore with unrelated nearby comment
(substance, not presence — the hook can't catch this one);
`foreskrift/structure.py:87` (note: :83's sibling was fixed this pass).

## Addressed during this review pass

The 11 package-wide bare suppressions (incl. `search.py:349/358`,
`api/app.py:278`, `render.py:1603` that agents also flagged from their
pre-fix reads). Fixed, all checks re-verified green.

## Known failures (pre-existing, unchanged)

- `test_resolve.py::test_eu_extractable_label_pruned_abbr_still_resolves`
  — root-caused above; needs a contract decision, not a test edit.
- `test_sfs_parse.py::test_sfs_links[tricky-overlappande-tabellrader]` —
  sfs reviewer's diagnosis: `nf.py:tabell_nf` ignores per-row
  `upphor`/`ikrafttrader` that the tokenizer attributes, so both temporal
  variants of an overlapping row emit citation tuples.

## Suggested order

1. ~~C1–C3~~ (fixed) → H1/H2 (public resolver correctness) next, plus
   the C-fix operational follow-ups above.
2. The assert-class sweep H4–H6 (+ `lib/llm.py`) — same fix shape as §3.3.
3. H3 (catalog fragments pre-delete) — pairs with the §2.1/§2.2 work.
4. H7–H10 fold into the already-planned §3.4 harvest/lib promotions.
5. MEDIUMs opportunistically, typing/doc drift as a batch.
