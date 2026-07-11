# Developing accommodanda

Everything a developer needs to work in the `accommodanda/` package: how the
pipeline is structured, how sources and stages work, and how to add a new source
or a source-specific action. This guide is self-contained; the two companion
references are [`../../accommodanda/README.md`](../../accommodanda/README.md)
(the module map — which file does what) and [`../conventions.md`](../conventions.md)
(the citable coding-rule catalog).

## 0. What accommodanda is

accommodanda is a pipeline that **downloads, parses and interlinks large
repositories of Swedish legal documents** — statutes, court decisions,
preparatory works, EU law, agency regulations, and more — and publishes them as
a static, cross-linked website plus a REST API and bulk data. It powers
lagen.nu.

The value the system adds is **connection**: a statute paragraph shows which
court cases and preparatory works cite it, a court case links to the exact
paragraph it invokes, an EU directive links to the national law that implements
it. Producing those links reliably across ~200K documents from a dozen
inconsistent public sources is the whole job.

The code is organised around one idea: **each legal source is an independent
program that produces a JSON file per document, and everything else is derived
from those JSON files.** The sections below make that concrete.

## 1. Principles

Five rules shape the whole codebase. They are load-bearing — the guardrails in
`.claude/hooks/` enforce several of them mechanically — not stylistic
preferences.

- **The JSON artifact on disk is the source of truth** for all extracted
  semantics (structure, metadata, links). The SQLite catalog, the search index
  and the bulk dumps are all *derived* from the artifacts and can be rebuilt
  from them at any time — they are never the only home of authoritative data. If
  you can't reconstruct it by re-reading the artifacts, it doesn't belong in a
  derived store.
- **Sources are programs; shared code is libraries.** A source (a vertical under
  `accommodanda/<name>/`) may import from `lib/`; `lib/` must **never** import
  from, or branch on, a specific source. Shared code never calls back into a
  source. This keeps each source understandable on its own and keeps `lib/`
  reusable.
- **No source base class, no inheritance for sources.** There is deliberately no
  `Document` / `DocumentRepository` superclass a source subclasses. A source is
  plain functions wired into one small `Source` record (see §3). When two
  sources need the same behaviour, extract a **function** into `lib/` and
  configure it by *data*, not by adding a hook to a shared base class. (A single
  harvest engine driven by a per-agency data registry beats seventeen bespoke
  agency pipelines — see `foreskrift/agencies.py`.)
- **Each source owns a typed model** — dataclasses using Swedish domain
  vocabulary (`Forfattning`, `Kapitel`, `Paragraf`, `Avgorande`, …) rather than
  a forced-universal document type. Different legal document kinds have genuinely
  different structure; don't flatten that away. Any RDF / Akoma Ntoso mapping is
  a downstream projection of the model, not the model itself.
- **Correctness is proven, not eyeballed.** Parsers are validated against frozen
  reference corpora and hand-authored fixtures (see §6), and every bug fix is
  locked in with a regression fixture so it can't silently come back.

Coding conventions worth internalising before your first PR:

- **Fail fast.** Assert how the environment should be; a precondition `assert`
  with a message beats a defensive branch papering over a broken environment.
- **Don't catch to log.** Only catch an exception you can fix and recover from.
- **No in-function imports.** All imports at the top, grouped stdlib /
  third-party / local.
- **Second use goes to `lib/`.** When a second vertical needs the same thing,
  extract it to `lib/` rather than copying.
- **Lock in parser fixes with a fixture.** Correctness is proven against the
  golden corpus and the `test/files/` fixtures, not by eyeballing.

## 2. The three layers

Realized in the `accommodanda/` package:

1. **Vertical source pipelines** (`sfs/`, `dv/`, `hudoc/`, `coe/`, `eurlex/`,
   `forarbete/`, `foreskrift/`, `avg/`, `remisser/`, `wiki/`, `site/`) — each owns its full
   chain (download → parse → typed model → JSON artifact) and its own model.
2. **Horizontal libraries** (`lib/`) — genuinely cross-source machinery: the
   citation engine (`lagrum.py`), catalog, search, render, layout, resolve,
   facets, the incremental build driver, etc.
3. **Corpus-wide derived layer** — `relate`/`index`/`dump`/`generate`, reading
   published artifacts across all sources into SQLite + the search index and
   computing the inbound-link graph.

A vertical imports from `lib`; `lib` never imports a vertical; only `build.py`
(the orchestrator) imports across verticals. The `.claude/hooks/check-layers.py`
guardrail enforces the direction.

**One sanctioned inversion:** `lib.render` drives the REST API in-process (via a
FastAPI `TestClient`) to generate the corpus-wide *browse* pages, so the static
listings are byte-for-byte what the REST endpoint serves and cannot drift. The
dependency is one-way and confined to aggregate-page generation.

## 3. Sources and stages

Everything runs through the `lagen` CLI (`accommodanda/build.py`, the
`accommodanda.build:main` console script):

```
lagen <source> <action> [basefile…]
```

`build.py` knows nothing source-specific. Uniformity lives in the driver plus a
tiny protocol — two dataclasses and a registration dict.

### The Stage and Source dataclasses

```python
@dataclass
class Stage:
    name: str
    run: Callable[[str], None]          # recipe: read inputs, produce output
    output: Callable[[str], Path]       # basefile -> the produced file
    inputs: Callable[[str], list[Path]] = lambda bf: []   # dependency files
    depends: str | None = None          # upstream stage name (make-style)
    code: tuple = ()                    # impl files; their hash = the recipe version

@dataclass
class Source:
    name: str
    list_basefiles: Callable[[], list]
    stages: dict                        # name -> Stage
    harvest: Callable[[list], None] | None = None   # bulk download (discovery)
    origin: str | None = None           # human base URL, shown when harvesting
    actions: dict = field(default_factory=dict)     # name -> source-specific verb
    scopes: frozenset = field(default_factory=frozenset)   # harvest sub-corpora
    notes: str = ""                     # extra text for `lagen <src> -h`
```

Registration is just mutating the module-level `SOURCES: dict` at import time:

```python
SOURCES["begrepp"] = Source(
    name="begrepp",
    list_basefiles=begrepp_list,
    stages={"parse": Stage(...)},
)
```

There is **no base class and no subclassing**. `begrepp` is the minimal example
(a lister + one `parse` stage); `sfs` is the fullest (three stages —
`download`/`parse`/`versions` — a `harvest`, an `origin`, a custom
`ai-correspond` action, and `notes`).

### The verb taxonomy

Two kinds of verb, handled on different code paths:

**Per-document Stages** — run through the freshness engine, per basefile,
parallelisable with `-j`:
- `download` — only for sources that register a download Stage. Overloaded:
  with no ids (or scope-only args) it triggers the source's *bulk* `harvest`;
  with ids it runs the per-doc download Stage (targeted refetch of known ids).
  **New documents enter only through `harvest`** — the per-doc download stage
  can only re-touch known ids, never discover new ones.
- `parse` — every source has one; raw → artifact.
- `versions` — SFS only (a second per-doc stage: historical consolidations).

**Corpus-level verbs** — not Stages, single functions over whole sources:
`relate` (build the SQLite catalog), `index` (OpenSearch), `dump` (NDJSON),
`generate` (static HTML), `status`, `serve`, `runs`, and the composites
`rebuild`/`all`.

**The two `all`s are orthogonal:**
- `all` as *source* fans a verb out across every registered source.
- `all` as *action* = `rebuild` + a leading download phase. `rebuild` is the
  offline core `parse → relate → index → dump → generate`; `all` prepends the
  network-bound download.
- So `lagen all rebuild` = offline rebuild of everything; `lagen all all` =
  full network sync then rebuild.

`generate` is special: a page's prerequisite set is **data-dependent** (its own
artifact plus the artifacts of every doc that cites it, from the catalog),
which the static `Stage.inputs` protocol can't express — so it's a corpus verb
with its own per-page freshness (`page_signature`), not a Stage.

### Content-hash freshness

Freshness is content-based, never mtime-based for correctness decisions. Two
tiers:

1. **Per-document manifest** (`DATA/.build/manifest.json`, one entry per
   `source/stage/basefile`). A doc is fresh iff its output exists **and** the
   manifest records the same **input hash** *and* the same **recipe version**.
   - *Input hash* = SHA-256 over the stage's `inputs(basefile)` (decompressed
     content, so the fingerprint is stable across compression settings).
   - *Recipe version* = a hash over the Stage's `code` tuple. **Editing any
     file listed in `code` re-stales every doc of that stage** without a blanket
     `--force`. `code` must list *every* first-party module whose edit changes
     output, not just the head module.
2. **Coarse watermark** (`DATA/.build/watermarks.json`) — a cheap size+mtime
   fingerprint of a source's inputs plus a code-version check, so a whole
   corpus-level step (or the per-doc stage gate) can be skipped without reading
   the big manifest at all. This is what makes a no-op `lagen all rebuild`
   cheap. A per-doc watermark is recorded only on a **clean sweep** — a failed
   doc leaves the source unmarked so the next run retries it.

Driver flags:
- `--force` — skip the freshness short-circuit for the named stage (not its
  recursive deps).
- `--no-deps` — don't recurse into `depends`.
- `-n` / `--dry-run` — record the plan, run nothing.
- `-j` / `--jobs` — parallel workers: a process pool for `parse`, a thread pool
  for `index` (relate is single-writer SQLite and always serial). Defaults to
  `os.cpu_count()`.
- `--ignore-code-changes` — pin the code-version check fresh; a dev convenience
  so editing a parser doesn't restale the corpus.
- `--rot13` — `mkpatch` only: store the authored patch rot13-obfuscated, so a
  PII redaction doesn't commit the raw personal data in the clear (see *Patch
  files* below).

Per-doc resilience: a per-document exception is caught into the run's error
list and the run continues (this is a *sanctioned* catch — the alternative is
one bad doc aborting a 200K-doc run). A `SkipDocument` raised by an extractor
(expired/removed/empty doc) writes an empty artifact so the doc isn't retried
forever.

### write_artifact — the common envelope

Downloaders and parsers cooperate through one function,
`build.write_artifact(source, basefile, art, source_url=None)`:

- It resolves **one uniform `source_url`** (the "Källa" link the renderer
  shows), in precedence order: (1) `art["source_url"]` set by the parser; (2)
  the `source_url` the downloader recorded and the parse run passed in; (3)
  `layout.source_url(...)` derived by rule from identity (e.g. an EU act's ELI
  from its CELEX). A doc with none carries no link.
- It serializes (`json.dumps(..., ensure_ascii=False, indent=2,
  sort_keys=True)`) and writes precompressed (`.json.br`) via `lib/compress`.

`write_artifact` imposes no schema beyond stamping `source_url`. The typed model
each source builds (its `to_artifact` / `nf.to_normalform`) defines the rest.

### Patch files — correcting source material

Some published source material is simply wrong (an OCR slip, a broken table) or
carries personal data that must be redacted. Rather than fork the parser with
per-document special cases, a **patch file** is a unified diff applied to a
document's *intermediate source text* before parsing — the plain text (SFS), the
innehåll HTML (DV), the Formex XML (eurlex), or the extracted PDF text (the
PDF-bodied sources). Patches live under `patches/<source>/…` and are authored
either from the CLI or through the editor UI:

- `lagen <source> patch-show <basefile>` prints the document's intermediate
  source text (existing patch already applied) — the text you patch against.
- `lagen <source> mkpatch <basefile> <edited-file> [description]` diffs your
  edited copy against the pristine intermediate and writes the *minimal* patch.
  `--rot13` stores it rot13-obfuscated, so a PII redaction doesn't commit the raw
  personal data in the clear.
- The inline editor's **"patch source"** button (`api/patch.py`, `/patch/edit`)
  is the same flow over HTTP: it commits the patch attributed to the logged-in
  editor and force-reparses the document so the fix goes live.

The machinery is in `lib/patch.py` (find/apply/create, over the vendored
`lib/patchit.py`) and `patchsource.py` (the per-source `_INTERMEDIATE` registry
mapping a source to the pristine-text provider it patches against). **A patch is
a genuine parse input:** every patchable source folds
`_patch_input(source, basefile)` into its stage `inputs`, so editing a patch
re-stales exactly that document's `parse`.

## 4. Adding a new source

Write, in a new `accommodanda/<source>/` package:

1. **A typed model** (`model.py`) — dataclasses in Swedish domain vocabulary,
   with a `to_artifact()` (or an `nf.py` normal-form projection) that returns
   the JSON dict.
2. **A parser** (`parse.py`) — raw input → model → dict, ending in
   `build.write_artifact(source, basefile, art, source_url=…)`.
3. **A downloader** (`download.py`) — if the source is harvested. Reuse
   `lib/harvest.py` (the shared newest-first incremental walk +
   `HarvestWatermark`) and `lib/net.py` (the resilient HTTP session); state your
   own `lookahead_limit`/`safety_days` window at the call site.
4. **The wiring in `build.py`** — a `list_basefiles()`, an
   `artifact(basefile)`/`inputs(basefile)` pair, a `CODE` tuple naming every
   impl file, and one line:

   ```python
   SOURCES["x"] = Source(name="x", list_basefiles=x_list,
                         stages={"parse": Stage("parse", x_parse_run,
                                                x_artifact, x_inputs, code=X_CODE)},
                         harvest=x_harvest, origin="https://…")
   ```
5. **To publish it in the derived layer** — add an `ARTIFACTS["x"]` lister so
   `relate`/`index`/`dump`/`generate` pick it up. A source that publishes no
   pages of its own (like `remisser` — it hangs off the referred förarbete's
   rail — or `site`) deliberately omits this, so it is never relate'd/indexed.
6. **Tests** — a golden or fixture check locking in the parser contract
   (`test/test_<source>_*.py`). If it's a citation-bearing source, wire it into
   the catalog graph by minting the same `https://lagen.nu/<id>#<fragment>`
   URIs citations mint (that is what makes the inbound-link graph connect).

7. **To make the source patchable** (optional) — register its pristine
   intermediate-text provider in `patchsource._INTERMEDIATE`, apply the patch at
   the parser's intermediate choke point (`patch.apply`, or pass `patch_key=` to
   `lib/pdftext.pdf_pages` for a PDF body), and fold `_patch_input(source, bf)`
   into the source's freshness `inputs`. See *Patch files* in §3.

Then run `lagen x download && lagen x parse && lagen x relate && lagen x
generate` and check `lagen x status`.

The design principle: **configure by data, not by subclassing.** `foreskrift`
drives one shared harvest engine for 17 agencies from a data registry
(`foreskrift/agencies.py`) rather than 17 bespoke pipelines — that is the model
to follow when sources are similar.

## 5. Adding a source-specific action

An action is a verb beyond the standard stages (`ai-annotate`, `import-legacy`,
`discover-guidance`, …). (`versions` looks like an action but is a real SFS
Stage — see §3.) Mechanism: add an entry to the source's
`actions` dict mapping a verb name to a callable taking the raw `basefiles`
list:

```python
def x_ai_annotate(basefiles):
    # validate args yourself; honor RUN.dry_run
    for basefile in basefiles:
        ...

SOURCES["x"] = Source(..., actions={"ai-annotate": x_ai_annotate},
                      notes="ai-annotate <id>   author the .ann editorial layer")
```

- The action callable does its own arg validation / usage-exit and honours
  `RUN.dry_run`.
- The `notes` string supplies the extra help `lagen x -h` prints.
- An action name must not collide with a stage name of the same source.

### The ai-* convention

Every LLM pass is an **opt-in, source-specific action**, never called from a
corpus-wide `parse`/`relate`/`generate`. It reads one document, calls the model
once, and writes a **`.ann` sidecar** next to the artifact — the AI-created
(then human-corrected) editorial layer, kept separate from the parsed artifact
and the hand-edited markdown. Examples: `eurlex ai-annotate`, `remisser
ai-analyze`, `kommentar ai-annotate`, `sfs ai-correspond` (which writes a
`.corr` sidecar). The shared LLM client and the validate/self-repair-retry loop
live in `lib/llm.py` (`complete`/`complete_thread`/`author`); the model is the
`llm_model` config knob.

This keeps the corpus pipeline deterministic and reproducible: a full rebuild
never calls an LLM, and the AI layers regenerate only when their action is
explicitly re-run.

## 6. Testing and correctness

A bare `pytest` runs exactly the new suites (`pyproject.toml` scopes collection
to `test/test_*.py` and excludes the `test/files/` fixture tree and the legacy
unittest files). Two kinds of check matter here:

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
  defective entries). The comparison tools live in `tools/golden_*.py`, and
  known-benign difference families are catalogued so a real regression stands
  out against them.

The practical rule for a parser change: run the relevant fixture suite (must
stay green — it's an oracle) and the golden comparison (investigate every new
difference; a genuine improvement over the reference is expected and fine, a
genuine regression is not).

## 7. Terms glossary

| Term | Meaning |
|---|---|
| **basefile** | a document's stable id within a source (SFS `2018:585`, prop `2020/21:22`, CELEX `32016R0679`); the key every stage is parameterized by |
| **artifact** | the parsed JSON on disk (`artifact/<source>/<...>.json`), the source of truth |
| **stage** | a per-document build step (download/parse/versions) run through the freshness engine |
| **harvest** | bulk download that *discovers* new documents (vs the per-doc download stage that refetches known ids) |
| **catalog** | the derived SQLite (`catalog.sqlite`): documents, the citation-link graph, fragment snippets |
| **inbound / outbound** | the two directions of the citation graph — inbound = every document citing this one, outbound = every document this one cites |
| **inline run** | a text node encoded as a list of `str` runs + `{predicate, uri, text}` link objects at exact positions |
| **`.ann` sidecar** | the AI-authored (human-corrected) editorial layer beside an artifact |
| **NF / normal form** | SFS's projection to the shape used for golden comparison (`nf.py`), reproducing the reference corpus's URI-minting quirks exactly so the two can be compared |
| **golden / reference corpus** | the previous system generation's frozen output (in `../ferenda.old`), used as a change-detector — not an oracle — for regressions (see §6) |
