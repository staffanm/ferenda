# accommodanda — open findings (as of 2026-07-02)

Punch list of everything still open from the 2026-07-02 whole-package
conventions review (`docs/review-accommodanda-conventions-2026-07-02.md`).
The three CRITICALs from that review are **fixed** (see that doc); this
file tracks what's left, in the suggested order. Each item names its
rule slug from `docs/conventions.md`.

## HIGH — do next

1. **`lib/resolve.py:44-47`** — `@functools.cache` shares one *stateful*
   `LagrumParser` instance across all ⌘K queries: resolution is
   nondeterministic across queries/users (reproduced — one query teaches
   another an alias). Cache construction inputs, give each query a fresh
   `DocState`. rule:fail-fast.
2. **`lib/resolve.py:102`** — the placeholder `basefile="query"` mints
   sentinel URIs for relative citations (`"3 § skadestånd"` →
   `.../query#P3`, reproduced). Use the documented nobaseuri mode
   (`context={}`) instead. rule:fail-fast.
3. **`lib/markdown.py:94,106`** — load-bearing `assert` validating
   hand-authored frontmatter (untrusted input); under `-O` list items get
   filed under a `None` key with no signal. `raise ValueError`.
   rule:errors-drive-retry-use-raise.
4. **`sfs/correspond.py:163`** — load-bearing `assert fk` on a *data*
   condition (prop lacks an FK section) in the LLM correspondence
   pipeline; under `-O` writes a plausible zero-edge `.corr` sidecar.
   `raise ValueError`. rule:errors-drive-retry-use-raise.
5. **`wiki/annotate.py:77-80`** — `assert resp.content[:4] == b"%PDF"` on
   an untrusted remote response; under `-O` a WAF error page is cached as
   `.pdf` *forever* (`cached.exists()` short-circuit). `raise`, and
   switch the `write_bytes` at line 80/247 to `lib/util.write_atomic`.
   rule:errors-drive-retry-use-raise, rule:fail-fast,
   rule:second-use-goes-to-lib.
6. **`lib/llm.py:47-48`** — truncation check (`finish_reason == "length"`)
   is an `assert` on untrusted model output; same fix shape as 3-5.
   rule:errors-drive-retry-use-raise.
7. **`lib/catalog.py:390-416`** (`_index_document`) — re-indexing a
   changed artifact never deletes stale `fragments` rows (`INSERT OR
   REPLACE` only); ghost snippet rows accumulate forever. Pre-delete like
   `search.py` already does. Pairs naturally with the queued §2.1/§2.2
   incremental-relate work. rule:artifact-is-truth.
8. **`eurlex/download.py:430-441`** — wrapper-only Formex manifestations
   ship the `.doc.xml` manifest as the act's content: single-item
   manifestations skip wrapper disambiguation, and `(real or items)[0]`
   knowingly keeps a wrapper when all items are wrappers. Degrade to html
   like `bulk.py` already does for the identical case. rule:fail-fast.
9. **`eurlex/parse_pdf.py:48-52`** (`_ocr`) — a missing `ocrmypdf` binary
   (broken environment — confirmed present in `docker/accommodanda/
   Dockerfile`, so this only bites bare-metal dev) is treated like a bad
   document: prints, returns `None`, ships an empty artifact with the
   failure recorded nowhere. Assert the tool exists; only a genuine
   per-document `CalledProcessError` may become a recorded resilience
   point. rule:no-catch-log-continue, rule:fail-fast.
10. **`eurlex/parse_pdf.py:29-124`** — reimplements `lib/pdftext`
    (extraction, line grouping, gap reflow, byte-identical
    `_dehyphenate`) *including* the top-only span-grouping bug pdftext
    already fixed. Parameterize `lib/pdftext` for eurlex's real deltas
    (`-hidden`, cross-page offset flattening) instead of a third fork.
    rule:second-use-goes-to-lib.
11. **`avg/download.py` + `avg/parse.py:76`** — the §3.4 promotion
    trigger fired (avg is the fourth harvest-engine copy, sixth
    parser-state-reset copy) and produced copies instead of the planned
    `lib/harvest.py` / `LagrumParser.reset()` promotion. Do the promotion
    now, port avg onto it.
    rule:second-use-goes-to-lib.
12. **`lib/render.py:106`** (`_kommentar_indexes`) — `art.get("annotates")`
    + silent `continue`: wiki/parse.py guarantees the key on every
    kommentar artifact, so this only fires on corruption, and its effect
    is a whole commentary silently vanishing from every statute rail.
    Access the key directly. rule:fail-fast.

## Decision needed (not a mechanical fix)

- **`test_resolve.py::test_eu_extractable_label_pruned_abbr_still_resolves`**
  — commit d216ecc1 added Swedish labels to the GDPR `namedacts.json`
  entry without adjudicating this test, which pins the old
  pruned-labels contract. Either the pruning premise is obsolete (update
  the test, record the change) or ⌘K labels must stay pruned (give the
  parse engine its own alias channel). Fold into the `lib/resolve.py`
  work above — `resolve.py:114-128` also re-implements
  `lagrum.load_namedacts`, the two-loaders drift channel that let this
  slip. rule:second-use-goes-to-lib, rule:never-weaken-tests.
- **`test_sfs_parse.py::test_sfs_links[tricky-overlappande-tabellrader]`**
  — diagnosed cause: `sfs/nf.py:tabell_nf` ignores per-row
  `upphor`/`ikrafttrader` that the tokenizer already attributes, so both
  temporal variants of an overlapping table row emit citation tuples.
  Needs a fix + fixture, not a test edit. rule:lock-in-with-fixture.

## MEDIUM — batch opportunistically

**Fail-fast / dead fallbacks**
- `render.py:68,485,506,542,1192` — `or {}` on always-populated `Site`
  fields (dead fallback masking an impossible state).
- `render.py:1556` — dead 404 tolerance in `generate_browse`, with a
  comment describing a case that's already filtered upstream.
- `eurlex/annotate.py:33` — `.get("title", art["celex"])` is a dead
  default (title is always written); the real gap is an empty/`None`
  title, which slips through. Should be `.get("title") or art["celex"]`.
- `sfs/register.py:93-97` (`lookup_resource`) — unknown org/series labels
  ship straight into URI positions on the production path with no record.
- `wiki/parse.py:249,259` — duplicate `annotates:`/`title:` across two
  commentary files silently drops one of them.
- `lagrum.py:607` (`interleave`) — silently drops overlapping refs; live
  via eurlex's `cites + uses` splice.
- `lib/concepts.py:31,101` — the hand-edited alias override file lives
  under the frozen `lagen/` tree (rule:legacy-read-only violation) behind
  a silent `if exists() else {}` fallback.
- `build.py:1708-1714` (`report()`) — double-subtracts errored docs from
  the "skipped (fresh)" count; can go negative.

**Speculative surface**
- `render.py:785` — dead `DV_STRUCTURAL` constant.
- `layout.py:217` (`source_url`) — unread `metadata` parameter.
- `facets.py:345` (`browse_label`) — unread `source` parameter.
- `markdown.py:38` — dead `RE_WS`.
- `sfs/extract.py:16-35` — `keep_expired` param/branch has no caller.
- `sfs/download.py:255` (`list_basefiles`) — unused by the build driver
  its docstring claims to serve.
- `dv/structure.py:208-209` — dead second `close_to(2)` in `open_instans`.
- `foreskrift/harvest.py:483` (`_guarded_enumerate`) — unused `log` param.

**Duplication re-accreting (post-consolidation)**
- Browser-UA constants: `foreskrift/agencies.py:47`,
  `wiki/guidance_discover.py:44` (3rd/4th copies of `lib/net.BROWSER_UA`).
- `AVG_PARSE_TYPES` — 4th copy of the all-grammars list; export from
  `lib/lagrum`.
- `BASE = "https://lagen.nu/"` — declared ×4 (`avg/model.py`,
  `lib/catalog.py`, `sfs/nf.py`, `sfs/register.py`).
- `render.py` — directive-link construction duplicated ×3 intra-module
  (`genomfor_margin`, `render_implements`, `_ref_link`).
- `eurlex/parse_pdf.py:26` — date regex duplicates `parse_html._eu_date`.
- sfs source-dispatch (`json_path`/`html_path`/`register_path` from a
  suffix) triplicated: `__main__.py` ×2 + `_validate.py`.
- `wiki/annotate.py:80,247`, `guidance_discover.py:282` — plain
  `write_bytes`/`write_text` where `write_atomic` is the consolidated
  helper (line 80 doubles as part of finding 5 above).

**Typing (rule:own-typed-model)** — bare `list`/`object` fields with the
real type in a comment: `sfs/model.py` (throughout),
`sfs/tokenizer.py` event dataclasses (`upphor`/`ikrafttrader: object`),
`dv/model.py:52-53`, `lagrum.py:625` (`DocState.namedlaws`),
`render.py:46-54` (`Site`).

**Doc drift (rule:docs-follow-structure)**
- `accommodanda/__init__.py` docstring lists 2 of 7 verticals.
- avg missing from every API source enumeration + api/README (works,
  undocumented).
- `--only` CLI help claims forarbete-only; three consumers actually use it.
- dv: three stale `python -m` usage banners (pre-package module names).
- `foreskrift/harvest.py` docstring claims FFFS-only architecture (now 17
  agencies).
- `foreskrift/model.py` — duplicated docstring bullet.
- `foreskrift/parse.py:269` — promises a consolidation-PDF fallback that
  isn't implemented.
- avg docstring claims oldest-first backfill; harvest actually posts
  newest-first.

**Suppressions**
- `lib/search.py:295` — `ty: ignore` sits next to an unrelated comment
  (about mapping migration, not the suppression); needs its own
  rationale. The mechanical hook can't catch this one (a comment *is*
  present, just not about the right thing) — judgment-only.
- `foreskrift/structure.py:87` — bare, unlike its sibling at :83 (fixed
  this pass).

## Also flagged this session, outside the review's scope

- **`.gitignore` blocks `.claude/agents/` and `.claude/skills/`** (only
  `/.claude/hooks/` and `/.claude/settings.json` are excepted from the
  blanket `/.claude/*` ignore). The four review/guardrail agents
  (`plan-reviewer`, `conventions-enforcer`, `docs-sync`,
  `commit-planner`) and the `/wrapup` skill are on disk but **not
  trackable by git** as configured — they won't survive a fresh clone.
  Needs a decision: except them in `.gitignore` (adding
  `!/.claude/agents/` and `!/.claude/skills/`) or intentionally keep them
  local-only.
