# Running accommodanda

How to go from a fresh checkout to a running `lagen all serve`. This is the
operator's guide: prerequisites, services, `config.yml`, the build pipeline,
and deployment. For *why* the system is shaped this way read
[`../../REWRITE.md`](../../REWRITE.md); for the module map read
[`../../accommodanda/README.md`](../../accommodanda/README.md).

## 1. Prerequisites

| Requirement | Why | Needed for |
|---|---|---|
| **Python 3.10+** | the codebase targets 3.10+ only | everything |
| **[uv](https://docs.astral.sh/uv/)** | dependency + venv management; `uv sync` installs all of `pyproject.toml` | everything |
| **OpenSearch 2.x** | full-text search index (`lib/search.py`) | `index`, search API |
| **poppler-utils** (`pdftohtml`, `pdftotext`) | PDF body extraction | eurlex/coe/forarbete/foreskrift/avg parse |
| **A JVM (OpenJDK 21) + POI jars** | reads binary `.doc`/`.docx` via Apache POI | **legacy DV Word path only** |
| **tesseract (+ swe), ocrmypdf** | OCR of scanned PDFs | forarbete re-OCR sidecars (optional) |
| **git** | the wiki/site content repo is git-backed; the inline editor commits to it | wiki/site parse, inline editing |

Everything except the DV Word path is pure Python. SFS, the citation engine,
the DV API path, search and the web service need no Java.

### Install Python dependencies and the `lagen` command

```sh
uv sync
```

This creates a virtual environment (`.venv/` in the repo root) and installs
everything into it, including `jpype1` (the JVM bridge). It also installs the
project itself, which registers a console script named **`lagen`** — the single
entry point for the whole pipeline (it maps to `accommodanda.build:main`).

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

### JVM + POI (only if you parse legacy DV Word documents)

```sh
sudo apt-get install -y openjdk-21-jdk-headless   # Ubuntu 24.04
./tools/fetch_poi.sh                               # POI 5.4.1 + deps into vendor/poi/ (gitignored, idempotent)
```

jpype auto-discovers `libjvm.so`; you normally do not need `JAVA_HOME`. The
`-headless` JDK is enough — POI's document reading needs no AWT. Skip this
entirely if you only run the API-backed DV path (the default).

### OpenSearch

Search (`lagen … index`, the `/api/v1/search` endpoint, and the ⌘K palette)
needs OpenSearch 2.x reachable at `opensearch_url` (default
`http://localhost:9200`). The repo ships a compose file that starts it:

```sh
docker compose up -d          # starts the `opensearch` service (no profile → always on)
```

The rest of the pipeline (download, parse, relate, generate, serve) works
without OpenSearch; only search-dependent features degrade.

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
wiki_root: ../lagen-wiki             # git-backed markdown content repo (begrepp/kommentar/site); default ../lagen-wiki
legacy_root: ../ferenda.old/data     # frozen legacy corpora, referenced in place; default ../ferenda.old/data

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

# --- ops dashboard (/ops) --------------------------------------------
ops_token: <random>                  # HTTP-Basic password for user `ops`; unset ⇒ /ops disabled (403)

# --- inline content editor (the only mutating surface) ---------------
editor_secret: <random hex>          # signs the session cookie; unset ⇒ editing off (403)
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
| `legacy_root` | `LEGACY_ROOT` | `<repo>/../ferenda.old/data` |
| `opensearch_url` | `OPENSEARCH_URL` | `http://localhost:9200` |
| `llm_model` | `BERGET_MODEL` | `openai/gpt-oss-120b` |
| `llm_base_url` | `LLM_BASE_URL` | `https://api.berget.ai/v1` |
| `llm_temperature` | `LLM_TEMPERATURE` | `0` |
| `llm_top_p` | `LLM_TOP_P` | unset (endpoint's default) |
| `compress` | `FERENDA_COMPRESS` | `true` |
| `compress_quality` | `FERENDA_COMPRESS_QUALITY` | `11` |
| `ops_token` | `OPS_TOKEN` | unset (dashboard disabled) |
| `editor_secret` | `EDITOR_SECRET` | unset (editing disabled) |
| `cookie_secure` | `EDITOR_COOKIE_SECURE` | `true` |
| `editors` | — (config only) | `{}` |

A present-but-invalid value raises `ConfigError` at startup rather than
silently falling back — a typo must never disable auth quietly.

### Content repo (wiki + site)

Commentary (`kommentar`), the concept glossary (`begrepp`), and the editorial
chrome (frontpage / om / sitenews) are **git-backed markdown** in a separate
repo checked out alongside this one:

```sh
git clone <lagen-wiki remote> ../lagen-wiki
```

`WIKI_ROOT`/`wiki_root` points at it; the default is the sibling
`../lagen-wiki`. Without it, the `begrepp`/`kommentar`/`site` sources have
nothing to parse (the rest of the pipeline is unaffected).

### Editor password hashes

Editors are a hand-curated registry; there is no self-signup. Mint a `pwhash`
(nothing is ever stored in the clear):

```sh
python -m accommodanda.api.auth hash '<the password>'   # prints the pbkdf2$… line
```

Paste the line into the editor's entry. A password change plus a restart
invalidates every outstanding session for that editor (the cookie embeds a
fingerprint of the current hash).

## 3. Verify the checkout

```sh
python -m pytest      # bare pytest collects exactly the new suites
```

`pyproject.toml` scopes collection to `test/test_*.py`, excluding the
`test/files/` fixture tree and the legacy unittest files, so a bare `pytest`
never touches code that doesn't import under modern Python.

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

### From fresh checkout to `serve` (dev)

```sh
uv sync                          # 1. deps (installs the `lagen` command into .venv)
source .venv/bin/activate        #    put `lagen` on PATH for this shell
docker compose up -d             # 2. OpenSearch
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

Beyond those standard actions, a source can define **source-specific actions**
that do something meaningful only for that source. Examples: `lagen sfs
versions` builds a statute's historical consolidations (only statutes have
those); `lagen foreskrift import-legacy skvfs` imports a frozen legacy corpus.
Run `lagen <source> --help` to see what a given source offers.

One recurring family is the **`ai-*` actions**. Any action whose name starts
with `ai-` works on a *single specified document* within a source: it sends that
document to a large language model together with a purpose-built prompt to
create *new* data — most often to discover connections between that document and
others — and writes the result as a `.ann` sidecar next to the artifact (a layer
kept separate from the parsed text, so it can be reviewed and corrected by hand).
These passes are **opt-in and never run automatically**: a normal
`download`/`parse`/`rebuild` never calls an LLM. They need `llm_model` set and a
Berget API key in the environment.

```sh
lagen eurlex ai-annotate 32016R0679       # author the editorial recital/article layer for one EU act
lagen kommentar ai-annotate <basefile>    # link an act's articles to external guidance documents
lagen remisser ai-analyze <case>/<org>    # map one remiss answer onto the referred förarbete's sections
```

These calls go to Berget by default, and are metered. Pointing them at a local,
vision-capable model instead (Qwen3.6-35B-A3B on llama.cpp, one 24 GB GPU) is a
matter of setting `llm_base_url` — unmetered and private, which is what makes bulk
passes over a whole corpus affordable. A local endpoint needs no API key. The
runbook, including the sampling keys it wants and the measured limits, is
[`../local-llm.md`](../local-llm.md).

The full per-source command reference (every source's exact arguments and
actions) is in
[`../../accommodanda/README.md`](../../accommodanda/README.md#running-the-pipelines).

Status and instrumentation:

```sh
lagen <source> status    # per-stage health for one source (writes the snapshot cell)
lagen all runs [N]        # recent runs from the ledger
```

## 6. Operations

`lib/runlog.py` writes three state files under `DATA/.build/`, consumed by the
`/ops` dashboard:

- `runs.ndjson` — append-only run ledger (one block per invocation).
- `errors.json` — per-document latest-outcome store, so a *failed* doc is
  distinguishable from one *never touched*.
- `status.json` — rolling per-source × per-stage health snapshot.

`/ops` is an HTML health dashboard mounted on the same FastAPI app (per-source
× per-stage matrix, failing-doc drill-downs with tracebacks, run timings). It
is gated by HTTP Basic auth (user `ops`, password = `ops_token`); leaving
`ops_token` unset disables it (every `/ops` route answers 403).

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

The authoritative runbook — host bootstrap, disk layout, secrets, CI, cron — is
[`../deploy-vps.md`](../deploy-vps.md). The shape of it:

The repo-root `docker-compose.yml` defines four services selected by a Compose
profile:

| invocation | services | use |
|---|---|---|
| `docker compose up -d` | `opensearch` only | dev — run `lagen all serve` from the working tree |
| `docker compose --profile prod up -d` | `opensearch` + `accommodanda` + `nginx` + `certbot` | prod |

`opensearch` carries no profile so it starts in both. The `accommodanda` image
is built on the box from the checkout and carries the full pipeline toolchain
(poppler, tesseract+swe, ocrmypdf, raptor2, a JRE + POI jars), so download and
rebuild run in the container against the read-write corpus mount:

```sh
docker compose exec accommodanda lagen all rebuild   # parse→relate→index→dump→generate
docker compose exec accommodanda lagen all all       # download too, then rebuild
```

One uvicorn process serves the static site + REST API (`lagen all serve`, the
image `CMD`); the `nginx` vhost reverse-proxies to it on `:8000`. The app
resolves lagen.nu's bare-URL grammar itself, so nginx needs no `try_files`
rules. TLS is issued once with `tools/vps/issue-cert.sh` and renewed by the
`certbot` sidecar.

**Continuous deploy + nightly sync.** Pushes to `modernization` trigger
`.github/workflows/deploy.yml` on a self-hosted runner on the VPS (update
checkout → build → `up -d` → `lagen all rebuild`); a `ferenda` crontab runs
`tools/vps/nightly.sh` (`lagen all all`) nightly.
