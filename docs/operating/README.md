# Running Ferenda

How to go from a fresh checkout to a running `lagen all serve`. This is the
operator's guide: prerequisites, services, `config.yml`, the build pipeline,
and deployment. For the architecture and module map, read
[`../developing/README.md`](../developing/README.md).

## The rest of this guide

| Document | Covers |
|---|---|
| [`pipelines.md`](pipelines.md) | the per-source command reference |
| [`data-layout.md`](data-layout.md) | what lives under `site/data/` |
| [`operations.md`](operations.md) | the run ledger, the error stores, the `/ops` dashboard |
| [`editing.md`](editing.md) | the inline editor and the crop review UI |
| [`patches.md`](patches.md) | correcting and redacting source material |
| [`skvfs-harvest.md`](skvfs-harvest.md) | the SKVFS/MTFS bot-wall and its browser transport |
| [`cutover.md`](cutover.md) | moving lagen.nu from the legacy site to the rebuilt one |

## 1. Prerequisites

| Requirement | Why | Needed for |
|---|---|---|
| **Python 3.14+** | the codebase targets Python 3.14 or later | everything |
| **[uv](https://docs.astral.sh/uv/)** | dependency + venv management; `uv sync` installs all of `pyproject.toml` | everything |
| **OpenSearch 3.7** | full-text search index (`lib/search.py`) | `index`, search API |
| **poppler-utils** (`pdftohtml`, `pdftotext`) | PDF body extraction | eurlex/coe/forarbete/foreskrift/avg parse |
| **A JVM (OpenJDK 21) + POI jars** | reads binary `.doc`/`.docx` via Apache POI | DV Word inputs only |
| **tesseract (+ swe), ocrmypdf** | OCR of scanned PDFs | forarbete re-OCR sidecars (optional) |
| **git** | the wiki/site content repo is git-backed; the inline editor commits to it | wiki/site parse, inline editing |
| **antiword** | reads the Word 6/95 binaries POI refuses | förarbete `.doc` bodies |
| **Xvfb** | private framebuffer for the headful-Chrome transport | SKVFS/MTFS download on a headless host |

Everything except the DV Word path is pure Python. SFS, the citation engine,
the DV API path, search and the web service need no Java.

### Install Python dependencies and the `lagen` command

```sh
uv sync
```

This creates a virtual environment (`.venv/` in the repo root) and installs
everything into it, including `jpype1` (the JVM bridge). It also installs the
project itself, which registers a console script named **`lagen`** — the single
entry point for the whole pipeline (it maps to `ferenda.build:main`).

The `lagen` script lives at `.venv/bin/lagen`. To call it as just `lagen`,
**activate the environment** so `.venv/bin` is on your `PATH`:

```sh
source .venv/bin/activate     # once per shell session
lagen --help                   # now `lagen` resolves directly
```

The rest of this guide assumes an activated environment and writes `lagen …`
and `python …` directly. If you'd rather not activate, prefix any command with
`uv run` (e.g. `uv run lagen --help`, `uv run python -m pytest`) — `uv run`
resolves the command inside `.venv` without touching your shell's `PATH`. The
two are equivalent; use whichever you prefer.

### JVM + POI for DV Word documents

```sh
sudo apt-get install -y openjdk-21-jdk-headless   # Ubuntu 24.04
./tools/operations/fetch_poi.sh                               # POI 5.4.1 + deps into vendor/poi/ (gitignored, idempotent)
```

jpype auto-discovers `libjvm.so`; you normally do not need `JAVA_HOME`. The
`-headless` JDK is enough — POI's document reading needs no AWT. Skip this
entirely if you only run the API-backed DV path (the default).

### OpenSearch

Search (`lagen … index`, the `/api/v1/search` endpoint, and the ⌘K palette)
needs OpenSearch 3.7 reachable at `opensearch_url` (default
`http://localhost:9200`); `/api/v1/resolve` answers from the catalog alone and
does not. The repo ships a compose file that starts it:

```sh
docker compose -f docker-compose.dev.yml up -d
```

The rest of the pipeline (download, parse, relate, generate, serve) works
without OpenSearch; only search-dependent features degrade. A `lagen all
rebuild` with the cluster down fails its index step once, goes on to dump and
generate, and exits 1 with `index __cluster__` in the failure summary.

## 2. config.yml

A single **optional** `config.yml` at the repo root configures the runtime. It
is read with round-trip YAML, so a bad value is reported with its line number
(`data_root invalid at config.yml:43`). Its scope is deliberately narrow: it
locates the corpus and holds service secrets, nothing else. Every key has an
environment-variable override (listed below), and every key has a working
default — an absent `config.yml` runs a dev checkout out of the box.

```yaml
# --- corpus location -------------------------------------------------
data_root: /srv/lagen/data          # downloaded + artifact + generated trees; default <repo>/site/data
wiki_root: ../lagen-wiki             # git-backed content repo (begrepp/kommentar/site/ann/patches); default ../lagen-wiki

# --- services --------------------------------------------------------
opensearch_url: http://localhost:9200   # search cluster
llm_model: openai/gpt-oss-120b           # Berget chat model for opt-in ai-* passes
llm_base_url: https://api.berget.ai/v1   # OpenAI-compatible endpoint; point at a local
                                         # llama.cpp (http://127.0.0.1:8123/v1) to run the
                                         # ai-* passes on the workstation GPU (docs/local-llm.md)
llm_temperature: 0                       # sampling for the ai-* passes; raise for a model
llm_top_p: 0.95                          # whose thinking mode needs it (Qwen3.6: 1.0/0.95)

# --- on-disk storage -------------------------------------------------
compress: true                       # store artifact/ + generated/ as Brotli (.json.br/.html.br); default on
compress_quality: 11                 # Brotli quality 0-11; default 11 (lower for faster builds)

# --- inline content editor (mutating surface) + /ops dashboard -------
editor_secret: <random hex>          # signs the session cookie; unset ⇒ editing AND /ops off (403)
cookie_secure: true                  # Secure flag on the session cookie; off only for plain-http dev
editors:                             # hand-curated; there is no self-signup
  staffan:
    name: Staffan Malmgren           # → git author/committer name on this user's commits
    email: staffan@example.org
    pwhash: "pbkdf2$260000$…$…"        # never a plaintext password
```

| Key | Env override | Default |
|---|---|---|
| `data_root` | — | `<repo>/site/data` |
| `wiki_root` | `WIKI_ROOT` | `<repo>/../lagen-wiki` |
| `opensearch_url` | `OPENSEARCH_URL` | `http://localhost:9200` |
| `llm_model` | `BERGET_MODEL` | `openai/gpt-oss-120b` |
| `llm_base_url` | `LLM_BASE_URL` | `https://api.berget.ai/v1` |
| `llm_temperature` | `LLM_TEMPERATURE` | `0` |
| `llm_top_p` | `LLM_TOP_P` | unset (endpoint's default) |
| `compress` | `FERENDA_COMPRESS` | `true` |
| `compress_quality` | `FERENDA_COMPRESS_QUALITY` | `11` |
| `editor_secret` | `EDITOR_SECRET` | unset (editing + `/ops` disabled) |
| `cookie_secure` | `EDITOR_COOKIE_SECURE` | `true` |
| `editors` | — (config only) | `{}` |

A present-but-invalid value raises `ConfigError` at startup rather than
silently falling back — a typo must never disable auth quietly.

### Content repo (wiki + site + patches)

Commentary (`kommentar`), the concept glossary (`begrepp`), the editorial
chrome (frontpage / om / sitenews), the LLM annotation layers (`ann/`) and the
source patches (`patches/`) all live in a separate repo checked out alongside
this one — everything the running site writes, in one checkout:

```sh
git clone <lagen-wiki remote> ../lagen-wiki
```

`WIKI_ROOT`/`wiki_root` points at it; the default is the sibling
`../lagen-wiki`. Without it, the `begrepp`/`kommentar`/`site` sources have
nothing to parse. The patch tree is different: `layout.patch` **asserts**
`<wiki_root>/patches` exists, because an absent tree reads as "no document has
a patch" and would silently republish every redaction. So a parse of a
patchable source needs the checkout, not just the wiki sources.

### Editor password hashes

Editors are a hand-curated registry; there is no self-signup. Mint a `pwhash`
(nothing is ever stored in the clear):

```sh
python -m ferenda.api.auth hash '<the password>'   # prints the pbkdf2$… line
```

Paste the line into the editor's entry. A password change plus a restart
invalidates every outstanding session for that editor (the cookie embeds a
fingerprint of the current hash).

## 3. Verify the checkout

```sh
python -m pytest      # run the maintained suites
```

`pyproject.toml` scopes collection to `test/test_*.py`, excluding the
`test/files/` fixture tree.

## 4. The build pipeline

Everything runs through the `lagen` CLI, which always takes the shape
`lagen <source> <action> [basefile…]` — for example `lagen sfs parse`. Using
`all` in place of a source name runs the action for every source at once
(`lagen all parse`).

The pipeline is **incremental**, much like `make`: each action re-does only the
work that is actually out of date. It decides that by content, not timestamps —
a document is rebuilt when its input data changed, or when the code that
processes it changed, and is otherwise left alone. So re-running an action after
a small change is cheap; you don't have to track by hand what needs redoing.

A document flows through these stages, in order:

```
download → parse → relate → index → dump → generate
```

- **download** — fetch raw source material (bulk harvest with no basefile;
  targeted refetch with one). Incremental by default, `--full` re-walks.
- **parse** — raw → typed model → JSON artifact on disk (**the source of
  truth**). Per-document, incremental.
- **relate** — read every artifact into the SQLite catalog
  (`catalog.sqlite`): documents, the citation-link graph, fragment snippets.
  Corpus-level, rebuildable.
- **index** — push the corpus into OpenSearch for full-text search.
- **dump** — write the NDJSON bulk export (`dumps/<source>.ndjson.gz`).
- **generate** — render static, interlinked HTML into `generated/`.

Convenience verbs:

```sh
lagen all rebuild    # parse → relate → index → dump → generate (no download)
lagen all all        # download too, then rebuild — the full cron sweep
lagen all serve      # serve generated/ + the REST API on one uvicorn process
```

`rebuild`/`all` re-do only what changed; the first full build over the
~200K-document corpus is slow (see §6 for the rsync shortcut).

#### What a run shows while it works

Every run counts its work in *steps* — one source's parse, one source's
relate, the cross-document passes relate ends with, a generate. A run of two
or more steps draws a whole-invocation progress bar (current step, steps
remaining, ETA) above the per-document counter each step already shows:

```sh
lagen all all        # 90 steps: download, parse, relate, index, dump, generate
lagen all download   # 16 steps, one per source with something to fetch
lagen all relate     # 18 steps: one per source, plus the cross-document passes
lagen sfs rebuild    # 7 steps
```

A run of one step keeps the plain single line — the outer bar would read
"1/1" for the whole run and repeat what the step's own counter already says:

```sh
lagen all generate       # one step over the whole corpus
lagen eurlex parse 32016R0970   # one document, no counter worth drawing
lagen all status         # a report, done in seconds: never a bar
```

Before a step can run it has to work out what is already up to date, which on
a big source reads every artifact's size and mtime and can take tens of
seconds. That scan reports as `checking staleness` on the same line the step's
own counter uses. Download has no such scan — nothing on disk decides what it
fetches — so its line names the harvest watermark instead: `(from 2026-01-10)`,
or `(first harvest)` / `(full sweep)` when there is no boundary to work back to.

A run piped to a file or a cron log (`docker compose exec ferenda lagen all
rebuild >> log 2>&1`) keeps the plain per-document line only, since the bar
is for someone watching live and would otherwise write raw cursor-control
bytes into the log.

### From fresh checkout to `serve` (dev)

```sh
uv sync                          # 1. deps (installs the `lagen` command into .venv)
source .venv/bin/activate        #    put `lagen` on PATH for this shell
docker compose -f docker-compose.dev.yml up -d   # 2. OpenSearch
git clone <lagen-wiki> ../lagen-wiki   # 3. content repo (for wiki/site)
# 4. obtain a corpus — either harvest it, or rsync a prebuilt one (§6). To harvest:
lagen all download        #    fetch raw material (long)
lagen all rebuild         #    parse → relate → index → dump → generate (long)
lagen all serve           # 5. http://localhost:8000
```

A first-time harvest + full rebuild is a multi-hour operation. For a working
site fast, seed the corpus by rsync from an already-built host (§6).

## 5. Per-source pipelines

Every source supports the same general set of actions — `download`, `parse`,
and so on. The verb is the same across sources, but a source often accepts
**extra arguments** to narrow the work: pass a basefile (a document's id) to
download or parse just that one document, or a scope to fetch a subset. With no
argument, the action processes the whole source (a bulk harvest, or every stale
document).

```sh
lagen sfs download            # no argument → bulk harvest the whole source, incremental
lagen sfs download 2018:585   # one argument → (re)fetch just that document
lagen sfs parse               # no argument → parse every stale document
lagen sfs parse 2018:585      # one argument → parse just that document
lagen dv parse                # each source's parse has its own specifics (DV is driven by its identity index)
```

A scope names one sub-corpus to walk, and for most sources that is one
publisher. `lagen foreskrift download fffs` walks Finansinspektionens
författningssamling. HSLF-FS is the exception: seven agencies issue into that
one samling and each publishes on its own site, so it has six scopes named
after the publisher, not the samling.

```sh
lagen foreskrift download fffs         # one agency, one författningssamling
lagen foreskrift download hslffs-ivo   # one of HSLF-FS's six publishing sites
lagen foreskrift download hslffs-sos --only hslffs/2025:25    # one document
```

Every document any of the six yields is filed under `hslffs/` and identified
"HSLF-FS <år>:<nr>". Each site also still lists the closed samling its agency
took over, and those documents keep their own designation — Socialstyrelsen's
SOSFS, Folkhälsomyndighetens FoHMFS and FHIFS, Läkemedelsverkets LVFS, TLV:s
TLVFS and LFNFS. `lagen foreskrift -h` lists every scope.

Beyond those standard actions, a source can define **source-specific actions**
that do something meaningful only for that source. For example, `lagen sfs
versions` builds a statute's historical consolidations.
Run `lagen <source> --help` to see what a given source offers.

One recurring family is the **`ai-*` actions**. Any action whose name starts
with `ai-` works on a *single specified document* within a source (`sfs
ai-hierarki` is the exception: it takes a lag basefile but works over that
lag's whole chain component — the lag, its förordningar and föreskrifter, any
EU rung — since the ladder it authors spans documents): it sends the document
(or component) to a large language model together with a purpose-built prompt
to create *new* data — most often to discover connections between that
document and others — and writes the result as a `.ann` sidecar next to the
artifact (a layer kept separate from the parsed text, so it can be reviewed
and corrected by hand). These passes are **opt-in and never run
automatically**: a normal `download`/`parse`/`rebuild` never calls an LLM.
They need `llm_model` set and a Berget API key in the environment.

```sh
lagen eurlex ai-annotate 32016R0679       # author the editorial recital/article layer for one EU act
lagen kommentar ai-annotate <basefile>    # link an act's articles to external guidance documents
lagen remisser ai-analyze <case>/<org>    # map one remiss answer onto the referred förarbete's sections
lagen sfs ai-hierarki 2018:585            # author regleringshierarki rows for one lag's chain component
lagen sfs ai-hierarki --all               # every lag whose chain reaches a föreskrift
lagen sfs ai-correspond 2018:585 prop/2017-18-89   # old->new paragraf map of a restructured act (.corr)
lagen sfs ai-includegraphics 2007:90      # place the graphics the consolidated text drops (.graphics)
lagen forarbete ai-genomforande prop/2025-26-28    # directive->paragraf transposition map of a prop
lagen sfs cover-consolidation-gap --all            # no LLM: reconstruct missing archived consolidations from the amendment PDFs
```

All eight report the same way (`lib/aireport.py`): the live counter the
stages use, one persistent line per layer written, and a closing line --
`sfs ai-hierarki: 12 layer(s) written over 3 item(s), 400 skipped (layers
present 380, no graphic gaps 20), 1 failed in 2h05m`, the failed ids listed
after it since they are what to re-run. The run ledger (`lagen all runs`)
gets a segment with those counts, and a run that enumerated the whole
eligible set itself (`sfs ai-hierarki --all`) writes its coverage to
`status.json` under the action's name: how many of the ids it enumerated
carry a layer. A subset run (`--update`, `--matching`, named ids) writes no
cell.
A hand-verified layer is skipped and counted, never overwritten without
`--force`.

These calls go to Berget by default, and are metered. Pointing them at a local,
vision-capable model instead (Qwen3.6-35B-A3B on llama.cpp, one 24 GB GPU) is a
matter of setting `llm_base_url` — unmetered and private, which is what makes bulk
passes over a whole corpus affordable. A local endpoint needs no API key. The
runbook, including the sampling keys it wants and the measured limits, is
[`../local-llm.md`](../local-llm.md).

The full per-source command reference (every source's exact arguments and
actions) is in
[`pipelines.md`](pipelines.md).

Status and instrumentation:

```sh
lagen <source> status    # per-stage health for one source (writes the snapshot cell)
lagen all runs [N]        # recent runs from the ledger
```

A `rebuild`/`all` run that exits non-zero prints a closing summary naming
which step(s) failed and, where recorded, the per-basefile error — since that
detail otherwise scrolls off screen long before a multi-hour run ends. Full
tracebacks are still `/ops/failures` or `/ops/runs/{id}` (§6).

## 6. Operations

The detail — the fingerprint gates, the serving-side error ledger, every
`/ops` route — is in [`operations.md`](operations.md).

`lib/runlog.py` writes three state files under `DATA/.build/`, consumed by the
`/ops` dashboard:

- `runs.ndjson` — append-only run ledger (one block per invocation).
- `errors.json` — per-document latest-outcome store, so a *failed* doc is
  distinguishable from one *never touched*.
- `status.json` — rolling per-source × per-stage health snapshot.

`/ops` is an HTML health dashboard mounted on the same FastAPI app (a system
panel — deployed git revision, lagen-wiki push state, OpenSearch index size — a
per-source corpus inventory of documents + artifact size, the per-source ×
per-stage matrix, failing-doc drill-downs with tracebacks, run timings). It
is gated by the inline editor's session — any logged-in editor can view it,
sharing the edit routes' auth rather than a separate token. With no session it
answers 401; an unset `editor_secret` disables it entirely (every `/ops` route
answers 403).

### Seeding a new host by rsync (skip the from-scratch rebuild)

A full first `relate`/`generate` over the corpus is slow. The catalog stores
`data_root`-relative paths, so it is portable: rsync the `artifact/` tree,
`catalog.sqlite`, and `generated/` into the new host's `data_root`, then let it
update incrementally (`lagen all rebuild` re-does only what changed). Paths
resolve against the host's own `data_root`.

One caveat: **migrate the dev catalog before rsyncing.** An older catalog holds
absolute paths; `rebuild()` rewrites them to relative in place, but only on the
host where those absolute paths are valid. Run `lagen all relate` on dev once
(it relativises the whole catalog), then rsync.

## 7. Production deployment (Docker)

The prod host runs one compose project that starts **both** lagen.nu sites.
`docker-compose.yml` is that project. It is not the file to use on a
workstation — its OpenSearch volume binds an NFS path that exists on the prod
host only.

| invocation | services | use |
|---|---|---|
| `docker compose -f docker-compose.dev.yml up -d` | `opensearch` | dev — run `lagen all serve` from the working tree |
| `docker compose --profile prod up -d` | all nine | prod |

The prod project holds two applications. `ferenda` is the rebuilt site, built
on the box from this checkout. `ferenda-legacy` is the old application: its
code is the `legacy` branch, checked out at `~/wds/ferenda-legacy` and
bind-mounted in, and its image is a pre-built tag on the host. Beside them run
`fuseki` and `mediawiki` (legacy), `matomo` and `db` (analytics), and the
shared `nginx` and `certbot`.

Which hostname reaches which application is nginx's business, and it changes at
the September cutover — see [`cutover.md`](cutover.md).

The `ferenda` image
is built on the box from the checkout and carries the full pipeline toolchain
(poppler, tesseract+swe, ocrmypdf, raptor2, a JRE + POI jars), so download and
rebuild run in the container against the read-write corpus mount:

```sh
docker compose exec ferenda lagen all rebuild   # parse→relate→index→dump→generate
docker compose exec ferenda lagen all all       # download too, then rebuild
```

One uvicorn process serves the static site + REST API (`lagen all serve`, the
image `CMD`); the `nginx` vhost reverse-proxies to it on `:8000`. The app
resolves lagen.nu's bare-URL grammar itself, so nginx needs no `try_files`
rules. One SAN certificate covers both vhosts; the `certbot` sidecar renews it.

**Continuous deploy + nightly sync.** Pushes to `main` trigger
`.github/workflows/deploy.yml` on a self-hosted runner on the prod host (update
checkout → build → `up -d` → `lagen all rebuild`). `staffan`'s crontab runs the
pipeline as inlined `docker compose exec` lines: `lagen all all` nightly (which
now skips the browser-shielded föreskrift agencies skvfs/mtfs), plus a weekly
`lagen foreskrift browser-download` (Sundays) for those — the headful-Chrome
transport is too slow and serial for the nightly sweep.

`lagen rs browser-download` wants the same weekly slot, for the same reason and
one more. Skatteverkets 2,614 ställningstaganden are one browser navigation
each. The run paces them 20 seconds apart: at 5-second spacing the site's front
refuses everything after about 30 navigations, and keeps refusing for some 40
minutes. A weekly run costs the register plus what moved. The first run takes
~15 hours, so slice it with `--limit N` and let the next run resume. Nothing is
stranded — a run stores a record only once its page is on disk. Run both browser
jobs **one at a time**: they share the process-global `DISPLAY`.

### Evicting the facsimile cache

`data/cache/facsimile` holds the page PNGs `lib/facsimile` renders on demand.
It is a **pure cache**. Nothing else reads it. A deleted file is re-rendered on
the next request, in about half a second. Eviction is therefore a crontab line,
by publication age, not by source-specific code:

```sh
0 1 * * * find <data_root>/cache/facsimile -name "*.png" -mtime +15 -delete
```

Measured 2026-08-19: 245 PNGs use 34 MB. There is no pressure yet, so this command is documented but not installed.

Its siblings under `cache/` are not pure caches on the same terms.
`cache/pdfconv` (9.9 GB) holds the poppler conversions the parsers read. A lost
entry costs a re-conversion during a build, not during a request. Do not point
the same `find` at it.
