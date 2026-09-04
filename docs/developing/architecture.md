# Architecture

How ferenda is put together: the layers, the source-and-stage model, and how to
add a source. The module-by-module map is [`source-map.md`](source-map.md); the
citable coding rules are [`../conventions.md`](../conventions.md).

## What ferenda is

ferenda is a pipeline that **downloads, parses and finds
references between documents in large repositories of Swedish legal
documents** — statutes, court decisions, preparatory works, EU law,
agency regulations, and more — and publishes them as a static,
cross-linked website plus a REST API and bulk data. It powers
https://lagen.nu/.

The value the system adds is **connection**: any link from document A
to document B is clickable, but can also be shown as an **inbound
link** when displaying document B. A statute paragraph shows
which court cases and preparatory works cite it, an EU directive links
to the national law that implements it. Producing those links reliably
across ~300K documents from a dozen inconsistent public sources, with
varying citation patterns, is a large part of the job.

The code is organised around one idea: **each legal source,
representing a type of document, is an independent program that
produces a JSON file per document, and everything else is derived from
those JSON files.** The sections below make that concrete.

## Principles

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
  `ferenda/<name>/`) may import from `lib/`; `lib/` must **never** import
  from, or branch on, a specific source. Shared code never calls back into a
  source. This keeps each source understandable on its own and keeps `lib/`
  reusable.
- **No source base class, no inheritance for sources.** There is deliberately no
  `Document` / `DocumentRepository` superclass a source subclasses. A source is
  plain functions wired into one small `Source` record (see §3). When two
  sources need the same behaviour, extract a **function** into `lib/` and
  configure it by *data*, not by adding a hook to a shared base class. (A single
  harvest engine driven by a per-agency data registry beats 76 bespoke
  agency pipelines — see `foreskrift/agencies.py`.)
- **Each source owns a typed model** — dataclasses using Swedish domain
  vocabulary (`Forfattning`, `Kapitel`, `Paragraf`, `Avgorande`, …) rather than
  a forced-universal document type. Different legal document kinds have genuinely
  different structure; don't flatten that away. Any RDF / Akoma Ntoso mapping is
  a downstream projection of the model, not the model itself.
- **Correctness is proven, not eyeballed.** Parsers are validated against frozen
  reference corpora and hand-authored fixtures (see §6), and every bug fix is
  locked in with a regression fixture so it can't silently come back.

Coding conventions worth internalising:

- **Fail fast.** Assert how the environment should be; a precondition `assert`
  with a message beats a defensive branch papering over a broken environment.
- **Don't catch just to log and continue.** Only catch an exception you can
  fix and recover from.
- **No in-function imports.** All imports at the top, grouped stdlib /
  third-party / local.
- **Second use goes to `lib/`.** When a second vertical needs the same thing,
  extract it to `lib/` rather than copying.
- **Lock in parser fixes with a fixture.** Correctness is proven against the
  golden corpus and the `test/files/` fixtures, not by eyeballing.

## The three layers

Realized in the `ferenda/` package:

1. **Vertical source pipelines** (`sfs/`, `dv/`, `forarbete/`, `eurlex/`,
   `foreskrift/`, `avg/`, `rs/`, `remisser/`, `guidance/`, `lawreview/`,
   `wiki/`, `hudoc/`, `coe/`, `icrc/`, `untc/`, `icc/`, `icj/`, `site/`,
   `stats/`) — each owns its full chain (download → parse → typed model → JSON
   artifact) and its own model.
2. **Horizontal libraries** (`lib/`) — genuinely cross-source machinery: the
   citation engine (`lagrum.py`), catalog, search, render, layout, resolve,
   facets, the incremental build driver, etc.
3. **Corpus-wide derived layer** — `relate`/`index`/`dump`/`generate`, reading
   published artifacts across all sources into SQLite + the search index and
   computing the inbound-link graph.

A vertical imports from `lib`; `lib` never imports a vertical; only `build.py`
(the orchestrator) imports across verticals. The `.claude/hooks/check-layers.py`
guardrail enforces the direction.

**One sanctioned inversion:** `site.browse` drives the REST API in-process (via a
FastAPI `TestClient`) to generate the corpus-wide *browse* pages, so the static
listings are byte-for-byte what the REST endpoint serves and cannot drift. A
vertical may not import `api`, so the checker carries one allowlist entry,
`("site/browse.py", "api.app")`. The dependency is one-way and confined to
aggregate-page generation.

## Sources and stages

Everything runs through the `lagen` CLI (`ferenda/build.py`, the
`ferenda.build:main` console script):

```
lagen <source> <action> [basefile…]
```

`build.py` knows nothing source-specific. Uniformity lives in the driver plus a
tiny protocol — two dataclasses and a registration dict.

The parts sit in four places: `lib/stage.py` holds the protocol (the
dataclasses, the `SOURCES` registry, the run-wide `RUN` options and the shared
shape helpers), `lib/freshness.py` holds the engine that decides what to run
(the manifest, the fingerprint gates, the per-document driver and its process
pool, the run ledger), `lib/corpus.py` holds the corpus verbs
(`relate`/`index`/`dump`/`generate`, the composites and the status verbs), and
`build.py` holds the CLI. Each source's registration is its own
`ferenda/<package>/source.py`; `build.py` imports them and fills the registry,
so adding a source means adding one file.

The corpus verbs know no source. Each takes the registry as its first argument
and reads what it needs off the `Source` record: which artifacts to relate,
which renderer to render a page with, what to add to the cross-document block.
Each source's `source.py` fills those fields in.

`build.py` still holds the few actions that read *two* sources at once —
`sfs ai-correspond`, `sfs table-correspond` and `sfs history-as-git` all read a
proposition, which is förarbete's job. A source may not import a sibling, so
they live in the orchestrator and are hung on sfs's registration as data. The
same rule sends `sfs.render.render_chapter` into `site.subdomains`
(`generate_aggregates`): the chapter subdomain pages render an SFS act, which
`site/` may not import.

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
    list_basefiles: Callable[[], list] | None = None   # override Source's own,
                                         # for a stage whose real unit of work
                                         # is finer than one basefile

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

    # what the corpus verbs ask of this source
    after: dict = field(default_factory=dict)   # verb name -> hooks run after it
    render: Callable | None = None      # this source's page renderer
    artifacts: Callable | None = None   # its published artifacts; None = none
    searchable: bool = True             # False: related but never indexed
    extra_pages: Callable | None = None # pages that are not catalog rows
    write_pages: Callable | None = None # a whole source of such pages
    owns_frontpage: Callable | None = None   # writes its own frontpage
    relate_cross: Callable | None = None     # its cross-document relate pass:
                                             # returns (counts, warnings)
    cross_code: tuple = ()              # the code behind relate_cross
    layers: Callable | None = None      # side files its pages/passes read
    registration: tuple = ()            # the source.py that declared it
```

`Stage.phase` names the corpus verb a rebuild runs the stage after. The default,
`"parse"`, keeps a stage in the rebuild's leading parse/versions loop.

A source's `source.py` exposes one attribute, `SOURCES: tuple[Source, ...]`
(one element for most; `wiki/source.py` registers both kommentar and begrepp).
`build.py` walks the modules at import time and mutates the `SOURCES: dict` of
`lib/stage.py`:

```python
# ferenda/wiki/source.py
_BEGREPP = Source(
    name="begrepp",
    list_basefiles=begrepp_list,
    stages={"parse": Stage(...)},
)
SOURCES: tuple[Source, ...] = (_KOMMENTAR, _BEGREPP)
```

The order `build.py` imports them in is the order `lagen all <verb>` walks the
corpus in, so it is data, not alphabetical tidiness.

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
- `versions` — sfs and eurlex only (a second per-doc stage: historical
  consolidations). Dispatched per archived/superseded version, not per
  document (`Stage.list_basefiles`, `"<basefile>@<version>"` keys), with a
  `Source.after["versions"]` hook that assembles each document's sidecar once
  its own versions are built. `lagen <src> versions <basefile>` expands the
  bare basefile to every key under it.

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

### Attaching work to a verb

A source hangs its own work off a standard verb in one of two ways. Neither is
a hook in a base class: both are fields on the registration, and the verb that
runs them knows no source name.

- **A per-document `Stage`.** Its `depends` names the stage that must run
  first; its `phase` names the corpus verb it runs after. `stats`'s `compute`
  stage sets `phase="dump"`: it measures the catalog `relate` rebuilt and the
  artifacts `parse` wrote, so it cannot ride the leading parse loop. A rebuild
  that names the source runs it after `dump` and before `generate`.
- **A corpus-level `Source.after[verb]` hook.** It runs once per source, after
  that verb's sweep over the source -- for a gated parse/versions stage only
  when the stage actually ran, and never under `-n`. `dv` registers
  `after={"parse": (_dv_after_parse,)}`: once a full dv parse is through, it
  reconciles the artifact tree to the canonical case set and refreshes the
  case-number snapshot. `lagen dv parse` runs the hook too, not only `lagen
  all rebuild`. sfs and eurlex hang their versions-sidecar assembly on
  `after["versions"]`; a targeted `lagen sfs versions 1999:1229`, and the
  versions prerequisite of a targeted generate, run it as well.

### Content-hash freshness

Freshness is content-based, never mtime-based for correctness decisions. Two
tiers:

The engine is `lib/freshness.py`; the paths below are its module constants.

1. **Per-document manifest** (`DATA/.build/manifest.json`, one entry per
   `source/stage/basefile`). A doc is fresh iff its output exists **and** the
   manifest records the same **input hash** *and* the same **recipe version**.
   - *Input hash* = SHA-256 over the stage's `inputs(basefile)` (decompressed
     content, so the fingerprint is stable across compression settings).
     The entry also records a size+mtime watermark of the same inputs
     (`inputs_wm`): an unchanged watermark reuses the recorded content hash
     without re-reading anything, a changed one re-hashes -- so a `.br`
     migration re-hashes once and finds nothing stale, while an ordinary run
     over 170,000 eurlex documents no longer decompresses every input.
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

One decision is mtime-based by design: the versions sidecar hooks skip a
statute or act whose `.versions.json` is newer than every archive file and
version artifact under it. The artifacts themselves are manifest-governed;
only the assembly of their index is gated by mtime. (sfs's mislabeled
explicit-key archives -- an archive file's own header names a cutoff other
than its filename -- fall through to the hook's direct-parse fallback
rather than the fan-out's predicted path, and are re-read only when that
gate opens.)

Relate's cross-document block (`__corr__`) has its **own recipe**: the lib
side is `CORR_CODE` (`lib/hierarki.py`), each source adds its own through
`Source.cross_code` (förarbete's `genomforande.py`/`fk.py`, sfs's
`register.py`/`correspond.py`). Both fold into the block's watermark
(`_corr_watermark`) beside every source's `layers` — the authored `.ann`/`.corr`
files, the versions sidecars and the uncatalogued artifacts each source names.
An edit to any of those re-runs only the cross passes (seconds), where an entry
in `RELATE_CODE` re-extracts every document of every source. Each source
contributes to the block through its `relate_cross` field — sfs loads the
`.corr` correspondence layers, förarbete pins its genomförande and
författningskommentar statements, kommentar audits its anchors — returning
`(counts, warnings)`, which `relate` prints. The same `layers` reopen
generate's coarse gate (`generate_fingerprint`). The corpus-wide passes
that follow run in a fixed order that is an invariant, not a convenience:
`canonicalize_concepts` before the concept-keyed `regleringshierarki` build
(rows store canonical uris), and `rebuild_norm_chain` — which DELETEs its
table — before `derive_delegation_edges` re-inserts the derived edges.

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
one bad doc aborting a 300K-doc run). A `SkipDocument` raised by an extractor
(expired/removed/empty doc) writes an empty artifact so the doc isn't retried
forever.

### write_artifact — the common envelope

Downloaders and parsers cooperate through one function,
`lib.stage.write_artifact(source, basefile, art, source_url=None)`:

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
PDF-bodied sources). Patches live under `patches/<source>/…` in the git-backed
content repo (`WIKI_ROOT`, the sibling `../lagen-wiki`), beside the commentaries
and the annotation layers. They are authored either from the CLI or through the
editor UI:

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
`lib/patchit.py`) and `patchsource.py`, which reads each source's pristine-text
provider off its registration — the `Source.intermediate` field, a
`(provider, format label)` pair the source's own `source.py` sets, so
`patchsource` itself imports no source. **A patch is a genuine parse input:**
every patchable source folds `_patch_input(source, basefile)` into its stage
`inputs`, so editing a patch re-stales exactly that document's `parse`.

## Adding a new source

Write, in a new `ferenda/<source>/` package:

1. **A typed model** (`model.py`) — dataclasses in Swedish domain vocabulary,
   with a `to_artifact()` (or an `nf.py` normal-form projection) that returns
   the JSON dict.
2. **A parser** (`parse.py`) — raw input → model → dict. The parser
   returns the dict; the Stage recipe (`stage.parse_stage`) hands it to
   `stage.write_artifact(source, basefile, art, source_url=…)`, which stamps
   `source_url` and writes it compressed. No parser calls `write_artifact`
   itself.
3. **A downloader** (`download.py`) — if the source is harvested. Reuse
   `lib/harvest.py` (the shared newest-first incremental walk +
   `HarvestWatermark`) and `lib/net.py` (the resilient HTTP session); state your
   own `lookahead_limit`/`safety_days` window at the call site.
4. **The registration** (`source.py`) — a `list_basefiles()`, an
   `artifact(basefile)`/`inputs(basefile)` pair, a `CODE` tuple naming every
   impl file relative to the package's own `HERE`, and the `SOURCES` tuple:

   ```python
   # ferenda/x/source.py
   HERE = Path(__file__).parent
   X_CODE = (HERE / "parse.py", HERE / "model.py",
             HERE.parent / "lib" / "lagrum.py", *CITATION_DATA)

   SOURCES: tuple[Source, ...] = (Source(
       name="x", list_basefiles=x_list,
       stages={"parse": Stage("parse", x_parse_run,
                              x_artifact, x_inputs, code=X_CODE)},
       harvest=x_harvest, origin="https://…"),)
   ```

   Then add the module to the import loop in `build.py`. That is the whole
   wiring: `build.py` gains one import and one name in the loop, nothing else.
   A source whose whole chain is the common shape (one bulk sync, a parse that
   reads the stored record in one call) writes `stage.simple_source` instead of
   the `Source(...)` above — `coe`, `icrc`, `untc`, `icc` and `icj` each fit in
   a 30-line `source.py`.
5. **To publish it in the derived layer** — set two more fields on that
   registration: `artifacts=functools.partial(layout.artifacts, "x")`, so
   `relate`/`index`/`dump`/`generate` pick the source up, and
   `render=x_render.render`, the page renderer `generate` renders each of its
   documents through. Add the name to `layout.CATALOGUED_SOURCES` too — a
   registration assert holds the two in step. A source that publishes no pages
   of its own (like `remisser` — it hangs off the referred förarbete's rail —
   or `site`) deliberately sets neither, so it is never relate'd or indexed.
6. **Tests** — a golden or fixture check locking in the parser contract
   (`test/test_<source>_*.py`). If it's a citation-bearing source, wire it into
   the catalog graph by minting the same `https://lagen.nu/<id>#<fragment>`
   URIs citations mint (that is what makes the inbound-link graph connect).

7. **To make the source patchable** (optional) — set
   `intermediate=(provider, "<format label>")` on the registration, where
   `provider(basefile)` returns the pristine intermediate text (a PDF-bodied
   source's is `lib/pdftext.pdf_intermediate`); apply the patch at the parser's
   intermediate choke point (`patch.apply`, or pass `patch_key=` to
   `lib/pdftext.pdf_pages` for a PDF body); and fold `_patch_input(source, bf)`
   into the source's freshness `inputs`. See *Patch files* in §3.

Then run `lagen x download && lagen x parse && lagen x relate && lagen x
generate` and check `lagen x status`.

### Per-source registries in `lib/`

`lib/` never imports a source, but several of its tables are keyed by source
name. A new source that publishes pages must be added to each of these, or the
generic form applies. Five of them refuse an unknown source; the rest fall
back silently, so check every row.

| Site | Decides | Unknown source |
|---|---|---|
| `lib/layout.py` `_relpath` | where the artifact lives on disk | `ValueError` |
| `lib/layout.py` `VERSIONED_SOURCES` + `versions_sidecar`/`version_artifact`/`version_key` | which sources keep version history | `ValueError` (only reached for a versioned source) |
| `lib/facets.py` `FLOW_GROUPS` | the browse flow group | `AssertionError` |
| `lib/catalog.py` `_LABELLED_KIND` | the catalog `kind` of a document | `KeyError` |
| `lib/catalog.py` `_document_snippet` | the opening words shown in cards | first prose paragraph |
| `lib/labels.py` `_DISPATCH` | the document labels | `_generic` |
| `lib/facets.py` `SCHEMES` | the facet scheme | no facets |
| `lib/facets.py` `SOURCE_LABELS` | the human name of the source (facets, feeds, the inbound rail) | `KeyError` in `feeds.py` and `page.py` |
| `lib/page.py` `CITER_STYLE` | how a citing document is named in the rail | `DEFAULT_CITER_STYLE` |
| `lib/render.py` `SOURCE_ORDER` | the order of sources on aggregate pages | not listed |
| `lib/render.py` `BROWSE_DIR` | the browse directory name | the source name |
| `lib/feeds.py` `DATASETS` | the bulk-data feed | no feed |

The design principle: **configure by data, not by subclassing.** `foreskrift`
drives one shared harvest engine for 76 agency scopes over 71
författningssamlingar from a data registry (`foreskrift/agencies.py`) rather
than 76 bespoke pipelines — that is the model to follow when sources are
similar.

## Adding a source-specific action

An action is a verb beyond the standard stages (`ai-annotate`, `import-legacy`,
`discover-guidance`, …). (`versions` looks like an action but is a real
Stage on sfs and eurlex — see §3.) It lives in the source's own `source.py`,
as an entry in the `actions` dict mapping a verb name to a callable taking the
raw `basefiles` list:

```python
# ferenda/x/source.py
def x_ai_annotate(basefiles):
    # validate args yourself; honor RUN.dry_run
    for basefile in basefiles:
        annotate.annotate(basefile, force=protocol.RUN.force)

SOURCES: tuple[Source, ...] = (Source(
    ..., actions={"ai-annotate": x_ai_annotate},
    notes="ai-annotate <id>   author the .ann editorial layer"),)
```

- The action function is the CLI half: it reads `protocol.RUN` and the
  arguments, and reports the outcome. The work itself belongs in the source's
  own module (`x/annotate.py`), where it can be tested without the CLI.
- The action callable does its own arg validation / usage-exit and honours
  `RUN.dry_run`.
- The `notes` string supplies the extra help `lagen x -h` prints.
- An action name must not collide with a stage name of the same source.
- An action that reads a *second* source is the exception: a source may not
  import a sibling (rule:lib-never-imports-vertical), so it lives in `build.py`
  and is hung on the registration there (`SOURCES["sfs"].actions.update(…)`).
  The three `sfs` actions that read a proposition are the live examples.

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

## Terms glossary

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
