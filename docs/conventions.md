# Conventions catalog

The citable rulebook for work on `accommodanda/`. Each rule has a slug.
Cite slugs in code where an exception is exercised
(`# noqa: BLE001 — walk must survive one bad agency page (rule:no-catch-log-continue)`)
and in review findings. The catalog records *judgment*, not just style —
most rules exist because their violation was actually found and paid for
(see `docs/review-accommodanda-2026-07-01.md`).

Enforcement map: **[hook]** = blocked/injected by a `.claude/hooks/` hook,
**[ruff]** = a ruff rule fails the Stop check, **[checker]** = the
layer-boundary AST check in the Stop hook, **[agent]** = judgment-level,
reviewed by `conventions-enforcer` / `plan-reviewer`. Unmarked rules are
judgment-only.

---

## Architecture

### rule:lib-never-imports-vertical  [checker]

`lib/` must never import from, or special-case, a specific source; a
vertical must never import another vertical. Only `build.py` (the
orchestrator) composes across verticals. When `lib` code seems to need
something from a vertical, the thing it needs is *shared machinery living
in the wrong place* — move it to `lib/` (as `case_slug` moved from
`dv/parse` to `lib/layout`). Keying on artifact/catalog *metadata* values
(source names in data) is fine; importing source *code* is not.

### rule:sources-are-programs

A source owns its full chain (download → parse → typed model → JSON
artifact) and exposes only its artifacts plus the tiny orchestrator
protocol. No source base class, no inheritance, no framework hooks — the
old codebase died of ~50 overridable methods. Share behaviour as small
`lib/` functions configured by **data**, not by subclassing:
`foreskrift/agencies.py` drives one harvest engine for 17 agencies; that
is the template for variation between sources.

### rule:own-typed-model

Each source's model is its own dataclasses in Swedish domain vocabulary
(`Forfattning`, `Kapitel`, `Avgorande`, …). No universal `Document` type.
RDF / Akoma Ntoso / JSON-LD mappings are downstream *projections* of the
artifact, never the model. Type dataclass fields precisely
(`list[Paragraf]`, not `list` plus a comment).

### rule:second-use-goes-to-lib

Before writing a helper inside a vertical, check `lib/` (start with
`lib/util`, `lib/net`) — it probably exists. When a second vertical needs
a helper that lives in a vertical, *promote it to `lib/` and import it
back*; never copy. `write_atomic` reached **five** byte-identical copies
before the review consolidated it. Corollary: a vertical is the wrong
home for anything generic — the `foreskrift` harvest engine belongs in
`lib/harvest` for the next source to build on.

### rule:respect-politeness

Every action for any source that downloads more than a single resource
must respect `accommodanda.build.POLITENESS` (or accept a `delay` parameter
that defaults to it). Multi-item network operations — harvests, scan
downloads, index walks, and body refetches — must sleep between network
fetches to avoid overwhelming upstream servers or triggering rate limits.

### rule:legacy-read-only  [hook]

`ferenda/` and `lagen/` are frozen reference — port knowledge out of
them; never extend, fix, or modernize them. Edits there are blocked. If
legacy behaviour is wrong, the fix belongs in the `accommodanda/`
replacement (with the legacy quirk documented where it matters).

---

## Error handling

### rule:fail-fast

Internal invariants crash early: access the key directly, `assert`
preconditions with a message, let programming bugs propagate. No
defensive branches or fallback values papering over a broken environment
— a fallback that "handles" an impossible state hides the bug and ships
wrong data. Reserve validation-and-recover for genuinely untrusted input
(remote documents), and even there prefer *reject and record* over
*guess and continue*.

### rule:no-catch-log-continue  [ruff BLE001]

Don't catch what you can't fix; catching just to log (or swallow) turns a
crash into silent data corruption. Broad `except Exception` is allowed
only at the sanctioned per-item resilience points, where one bad document
must not kill a corpus run:

- the shared download walk (`lib/harvest.py`)
- the build driver's per-document boundary (`build.py`)
- the golden-validation harness (`sfs/_validate.py`)
- the legacy-stats CLI (`dv/legacy.py`)
- the versions stage's per-version boundary (`sfs/versions.py`) — one
  corrupt decades-old archive file becomes a recorded skip in the
  sidecar, not a permanently stale stage
- the history-as-git export's per-snapshot preflight (`sfs/asgit.py`) —
  every failed snapshot is recorded and reported before any fast-import
  starts; the export then refuses the incomplete corpus rather than writing
  a history that a later repair would append out of order

Each site carries `# noqa: BLE001 — <what it survives, and where the
failure is recorded>`. Adding a *new* resilience point means adding it to
this list in the same change, with the user's agreement.

### rule:errors-drive-retry-use-raise

Validation whose exception is *load-bearing* (drives an LLM
feed-back-and-retry loop, a fallback path, a recorded rejection) must
`raise ValueError`, never `assert` — asserts vanish under `python -O` and
the validation silently passes (found live in `eurlex/annotate.py` and
`wiki/annotate.py`). `assert` is only for invariants whose failure means
"this program is wrong", where dying loudly in debug builds is the point.

### rule:narrow-what-you-catch

Catch the specific exception the engine documents, not `Exception`.
`lagrum.try_parse` caught `Exception` around the Lark parse, so a genuine
engine bug became "no reference here" — silent under-linking in the one
module where it's hardest to notice. The fix (catch
`lark.exceptions.UnexpectedInput`) is the pattern.

---

## Data & artifacts

### rule:artifact-is-truth

The JSON artifact on disk is the source of truth for all extracted
semantics — structure, metadata, links. SQLite and the search index are
derived and rebuildable; never make a derived store the only home of
authoritative data, and never "fix" data in the catalog that should be
fixed in the artifact (or the parser that produced it).

### rule:relocate-dont-regenerate

When generated output sits at the wrong path but its content is correct,
*move the files* — never delete-and-rebuild. Regenerating discards
provenance (download timestamps, upstream copies that may have changed)
and costs hours; a move plus re-relate is minutes. Filenames are part of
the freshness contract (`hash_files` hashes name+content).

### rule:respect-source-temporality

Know whether a source's documents are **consolidated snapshots** (SFS:
the text *is* the in-force version, superseded versions get archived) or
**as-published immutables** (föreskrifter, avg: each document is a fixed
historical artifact; amendment = new document). Never carry one model's
currency/cutoff thinking into the other — modelling a föreskrift
consolidation as a "cutoff date" was wrong; the datum is "last amendment
incorporated".

---

## Correctness & testing

### rule:lock-in-with-fixture

A parser/extraction fix without a regression fixture will regress.
Every such fix lands with a fixture under `test/files/` or a golden
check; correctness is proven against the frozen golden corpus, not by
eyeballing output. If a fix can't be fixtured, say so explicitly in the
change and why. (Cautionary tale: `sfs/extract.sanitize_body` fixed
2010:110 *only in test fixtures* — the production path never ran it.)

### rule:never-weaken-tests

Never loosen an assertion, fixture, or golden expectation to make a
failure go away. A golden diff has exactly two legitimate outcomes:
adjudicate it as a *deliberate improvement* (recorded, with rationale) or
fix the regression. Skipping, widening tolerances, or deleting cases is
neither.

---

## Style

### rule:no-infunction-imports  [ruff PLC0415]

All imports at the top of the file, grouped stdlib / third-party / local.
The one sanctioned exception: `lib/poi.py`'s POI/jpype imports, which
must follow JVM start — cited inline.

### rule:no-speculative-code

No "just in case" parameters, lookup surfaces, or half-wired features.
Unused code is a maintenance tax and a false signal to the next reader
(`eurlex/download.Notice` carried a whole unused index surface;
`sitemap_enumerate` served an agency that was never wired). Build it when
the second caller exists — and delete dead code on sight rather than
around it.

### rule:chain-dont-hold

Temporary holding variables only where the benefit is obvious; chaining
expressions is usually clearer. Match the surrounding code's density and
idiom.

### rule:fix-dont-annotate  [hook]

Lint/type suppressions (`# noqa: …`, `# ty: ignore[…]`) require a
specific rationale naming the actual constraint — on the same line
(`# noqa: BLE001 — recorded in errors.jsonl, walk continues`) or, where
line length forbids, in a comment directly above the suppression. Generic
rationales ("intentional", "ok", "needed") are rejected — as is the bare
suppression. The default move is to fix the finding; the suppression is
for the irreducible cases (third-party stubs, untyped artifact dicts,
the sanctioned sites above).

### rule:one-line-progress

Long-running work reports through the shared progress renderer in
`lib/util`, never with a fresh `print`/`log` line per item. The three
entry points are one mechanism:

- **`util.status(done, total, message, *, prefix, tail)`** — the single
  live counter. One line on **stderr**, rewound with `\r` and cleared with
  `\033[K` so it overwrites in place, clipped to one terminal row, with an
  `ETA MM:SS` right-aligned at the edge whenever `total` is known (pass
  `total=None` for an unknown total — no ETA, rendered `?`). Used by every
  per-item build loop (parse, generate, relate, index, dump, bulk unpack).
- **`util.progress(seen, total, *, scope, page, stamp, elapsed, **counts)`**
  and **`util.Reporter`** — the downloader/harvest form over `status`:
  `Reporter.update(seen, total, scope=…, **counts)` rewrites the live line,
  `.done()` drops a newline so a finished segment (a year/sweep/doctype)
  persists above the next one, `.reset()` rebases the elapsed clock after a
  slow per-segment query. `counts` are `label=value` tallies shown in call
  order (`fetched=…, skipped=…`).

Progress is a live view, so it goes to **stderr**; the **stdout** stream
carries only the final one-shot summary (`print("<source> <action>: <n>
seen, <n> fetched")`), so a redirected stdout stays a clean record and the
progress noise does not pollute it. Compute the total up front when it is
knowable (the work-list length) so the ETA works — a downloader that can
enumerate its targets should, rather than counting up against an unknown
total. A per-100-items newline dump is the anti-pattern this rule exists to
stop (`forarbete/propkb.sync`, `untc/download.sync` predate it).

---

## Process

### rule:no-unrequested-git  [hook]

Never run a state-changing git command (`commit`, `add`, `push`,
`checkout`, `reset`, …) without an explicit instruction *for that
operation*. "Fix X" is not permission to commit X. Make the changes,
leave them in the working tree, let the user decide. The git-guard hook
turns state-changing git into an explicit confirmation.

### rule:docs-follow-structure

A change that alters architecture, module layout, or a vertical's status
updates `REWRITE.md` (status markers + progress log) and
`accommodanda/README.md` (module map) *in the same change* — both drift
otherwise (the README's test list went stale within weeks). The
`docs-sync` agent exists for exactly this; bug fixes and internal
refactors don't qualify.

### rule:commit-shape

Subject `scope: short lowercase summary`, no trailing period; scope is a
vertical or a layer/concern (see CLAUDE.md). Body explains the *why* when
the change is broad. One commit = one coherent change; the
`commit-planner` agent groups a messy tree into a plan and, only after
the user approves it, executes the commits (rule:no-unrequested-git —
the approval is the explicit instruction, and git-guard still confirms
each command).
