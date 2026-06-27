## Project overview

Ferenda is a Python framework for downloading, parsing and connecting large
repositories of interconnected documents. It is primarily used for Swedish
legal information (it powers lagen.nu).

The project is being **rebuilt**. The original framework was overengineered
in the wrong places — its central mistake was inheritance, with a
`DocumentRepository` / `SwedishLegalSource` god class exposing ~50
overridable hooks that entangled every source with the whole call graph.
The rewrite keeps two decades of accumulated domain knowledge (SFS/DV
formatting quirks, the citation grammar) and discards the framework.

- **Active code lives in `accommodanda/`.** This is where all new work
  happens. Target Python 3.10+ only.
- **`ferenda/` and `lagen/` are the legacy codebase** being replaced. Treat
  them as read-only reference — port knowledge out of them; don't extend
  them. (The Python 2/3 modernization of the legacy tree is *done*; there
  is nothing left to modernize there.)
- Implemented verticals: **SFS** (statutes), **DV** (court decisions),
  **forarbete** (legislative preparatory works), **eurlex** (EU law),
  **foreskrift** (agency regulations), **wiki** (begrepp/definitions).
  We are not at full parity with the old system, but new sources beyond the
  original scope (notably eurlex and foreskrift) are now handled.

Read [`REWRITE.md`](REWRITE.md) for *why* the new system is shaped this way
and what's done vs. pending. Read [`accommodanda/README.md`](accommodanda/README.md)
for how to run the pipelines and the module map. Keep both updated as
architecture and status change.

## Architecture

Three layers, realized in the `accommodanda/` package:

1. **Vertical source pipelines** (`accommodanda/{sfs,dv,eurlex,forarbete,foreskrift,wiki}/`)
   — each owns its full chain (download → parse → typed model → JSON
   artifact) and its *own* document model.
2. **Horizontal libraries** (`accommodanda/lib/`) — genuinely cross-source
   machinery: the citation engine (`lagrum.py`), catalog, search, render,
   layout, resolve, facets, datasets, the incremental build driver, etc.
3. **Corpus-wide derived layer** — the `relate`/index/dump phases that read
   published artifacts across all sources into SQLite + the search index
   and compute the inbound-link graph.

These boundaries are load-bearing rules, not suggestions:

- **Sources are programs; shared code is libraries.** A source may import
  from `lib/`; `lib/` must never import from, or branch on, a specific
  source. Shared code never calls back into a source.
- **No source base class, no inheritance for sources.** Share behaviour as
  small functions in `lib/`, configured by *data*, not by subclassing
  (e.g. `foreskrift/agencies.py` drives one harvest engine for 17 agencies
  rather than 17 bespoke pipelines). A source exposes only its artifacts
  plus a tiny orchestrator protocol.
- **Each source owns a typed model** — dataclasses using Swedish domain
  vocabulary (`Forfattning`, `Kapitel`, `Paragraf`, `Avgorande`, …). Don't
  force a universal `Document` type; any RDF / Akoma Ntoso mapping is a
  downstream projection, not the model.
- **The JSON artifact on disk is the source of truth** for all extracted
  semantics (structure, metadata, links). SQLite and the search index are
  derived and rebuildable — never the only home for authoritative data.

## Coding conventions

- Avoid fallback code in general — assert how the environment should be.
  Precondition `assert`s with a message are preferred over defensive
  branches that paper over a broken environment.
- Don't catch exceptions unless you know how to fix and recover from the
  root cause. Catching just to log (or worse, swallow) is useless.
- Only create temporary holding variables where the benefit is obvious —
  chaining expressions is usually clearer.
- Don't use in-function imports. All imports go at the top of the file,
  grouped stdlib, third-party, local.
- DRY and focused: consolidate helpers into `lib/`, keep functions small,
  avoid "just in case" complexity. When a second vertical needs the same
  thing, extract it to `lib/` rather than copying.
- When you fix a parser/extraction bug, lock it in with a regression
  fixture or golden check. Correctness is proven against the frozen golden
  corpus and the fixtures under `test/files/`, not by eyeballing.

## Testing

- Run the new suites by naming them explicitly; a bare `pytest test/` fails
  at collection because the legacy `integration*.py` files don't import
  under modern Python (out of scope). See `accommodanda/README.md`.
- Parser correctness is validated against the golden corpus and small
  regression fixtures; many tests are golden/adjudication checks rather
  than unit assertions.

## Commit conventions

- Subject is `scope: short lowercase summary`, no trailing period.
- `scope` is a vertical (`sfs`, `dv`, `eurlex`, `forarbete`, `foreskrift`,
  `wiki`) or a layer/concern (`lib`, `build`, `render`, `api`, `search`,
  `catalog`, `structure`, `golden`, `docs`, `chore`).
- Keep the subject to one line; use `,`/`;`/`—` to separate clauses when a
  commit touches several related things. Add a body explaining the *why*
  and the scope when the change is broad.

## AI Agent Behavior

- Act with integrity
- Ask, don't guess
- Questions are not orders
- No glazing
- Be critical
- Don't fix what's not broken
