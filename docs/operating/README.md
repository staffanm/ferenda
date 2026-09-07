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
| **Camoufox** | the browser transport's own Firefox build (`python -m camoufox fetch`, ~1.2 GB) | skvfs/mtfs/skv/edps/icj/untc download |

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
python -m ferenda.api.auth hash    # prompts twice, prints the pbkdf2$… line
```

The command takes no argument: a password on the command line lands in the
shell history and in every `ps` listing. New hashes are minted at 600,000
pbkdf2-sha256 rounds; the cost travels inside the stored string, so an existing
`pbkdf2$260000$…` keeps working until it is re-minted.

`editor_secret` must be at least 32 characters — `openssl rand -hex 32` writes
64. A shorter one raises `ConfigError` at startup rather than signing sessions
with a guessable key. It can also come from a file named by `EDITOR_SECRET_FILE`
(how a Docker secret arrives), so the key is not in every process's environment.

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
  Documents new to the dump are appended as one more gzip member (a reader
  sees one stream); a document that changed in place or vanished, a code
  change or `--force` rewrite the file, so no uri appears twice. The
  artifacts a dump holds are recorded beside it (`.records`), and the
  rewrite reads artifacts across the run's jobs: forarbete's 97,274 took
  2151 s serially on 2026-09-07, 22 ms of NFS round trip each.
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
remaining, ETA) above the per-document counter each step already shows. The
ETA predicts each step from the wall time it took in earlier runs (the run
ledger, `site/data/.build/runs.ndjson`) and re-paces the rest of the plan on
how fast this run's finished steps ran against those predictions; the step in
flight counts toward it as it runs, up to its own prediction, so a long step
that overruns holds the ETA rather than pushing it out:

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
own counter uses. A parse or versions step first lists its documents
(`listing basefiles`, the walk that reads as a pause on a cold cache), then
scans them once, in chunks of 500 keys. With more than one job the chunks go
to a pool of scan workers. A worker reads its chunk's manifest entries in one
query and answers the file checks from one directory listing per directory
(`compress.dir_cache`). A document found up to date is booked on the spot; a
stale one goes to a build worker the moment its chunk lands, most expensive
first, so the counter moves seconds into the scan and a source with nothing
stale answers `up to date -- skipped` at the scan's end. On the production
NFS mount the per-document checks used to be round trips: one sqlite
transaction per document (four lock round trips) and 18 stats. Measured with
nothing stale and 16 jobs, 2026-09-06: eurlex parse 1172 s → 127 s,
forarbete parse 409 s → 13 s, hudoc parse 273 s → 15 s on production; on
the dev box eurlex parse 17 s end to end, forarbete parse 10 s. Most of
eurlex's 127 s is now the listing itself, one directory read per document.

`relate`, `dump` and `generate` open with the same kind of pass over every
artifact of a source (`freshness.stat_records`), and it runs across the same
number of processes: forarbete's 97,000 artifacts took 25–54 s serially on
production and take 5.5 s with 16 jobs. A relate whose fingerprint changed
then compares each artifact's size and mtime with the row's; only the ones
whose mark moved are read and hashed, `relate <source>  hashing rewritten
artifacts`, again across the pool — each read is an NFS round trip of 13–44
ms. After a sync that moves every artifact's mtime this is the whole source:
hudoc's 46,000 artifacts hashed in 40 s cold (66 s serially with a warm
cache, 207 s cold in the run before the change), forarbete's 97,000 in about
three minutes (2213 s before). The step then counts the source's links once
more for its summary line; on a cold catalog that count is tens of seconds
to minutes of index reads and is the same either way.

The cross-passes at the end of relate (`relate cross-passes`, ledger key
`relate __corr__`) took 5581 s in the same run. Almost all of it was
förarbete's hook: `fk.resolve` reads every proposition artifact (28,278) to
pin its författningskommentar entries, `genomforande.resolve` reads the
propositions with genomför-direktiv edges and then every statute a pinpoint
names, one artifact at a time. On a copy of the catalog with a cold cache and
a busy disk that hook ran for 86 minutes without finishing. Those reads now
go across the run's jobs too. The hook's opening query — which förarbeten
carry a genomför-direktiv link — had no index to use and fetched every
forarbete document's link rows to test the predicate, 20 minutes and more on
the same copy; relate now builds a partial index over those links
(`idx_links_genomfor`, one scan of `links` the first time) and the query is
a covering index scan.

The cross-passes also avoid repeated SQLite reads and writes:

- SFS reads its amendment artifacts across the run's jobs. Title matching
  caches each statute's effective date for one pass, including absent dates.
- Commentary and inbound-count updates write only changed rows. Concept
  lookups and the anchor audit read ranges of existing covering indexes.
- The norm hierarchy uses compact covering indexes for authority and repeal
  references (`idx_links_norm`) and rule metadata (`idx_docs_norm`). The
  delegation query starts at the clauses that confer authority. Inbound
  stamping reads `idx_docs_inbound` instead of full document rows, and runs
  before the hierarchy pass, while the links index is still warm.
- The regleringshierarki scan matches inherited terms across the run's jobs.
  Terms travel as strings and compile once per worker. A substring test per
  word (`begrepp.term_needles`) rejects nearly every (fragment, term) pair
  before the regex runs: 23 million pairs at 21 µs each was the whole pass.
  The curated-row merge and the anchor containment tests index their rows
  instead of scanning them pairwise.
- Each cross-pass prints its name and elapsed time. The batch connection
  uses a 64 MiB SQLite page cache and keeps temporary sorts in memory.

The passes are then bound by the catalog file's page layout. A full relate
leaves every table and index scattered one page at a time: on the production
catalog of 2026-09-07 the cited-by index held 399,000 pages in 294,000
separate runs, and the documents table 90,000 pages in 81,000 runs. The disk
under it streams at about 50 MB/s but seeks a few hundred times per second,
so a cold scan of that index took 468 s. `lagen all compact` rewrites the
catalog contiguously (`VACUUM INTO` a sibling file, then the same atomic
swap a full rebuild uses; about 8 minutes, one extra file's worth of disk,
readers unaffected). On the compacted file the same cold scan took 57 s and
the whole block 200 s. Incremental relates fragment the file slowly, so
production runs `lagen all compact` weekly from cron (Sunday 14:00, after
the nightly and the browser downloads are done); a full rebuild should be
followed by one by hand. The pipeline's writer lease keeps a compaction and
a relate from overlapping.

Cold timings on that compacted copy, 2026-09-07: 57 s inbound counts, 45 s
fk, 38 s regleringshierarki, 32 s genomförande, 11 s sfs, under 6 s each for
the rest; 200 s in all. The same block took 5581 s in the nightly before
these changes. The first run after deploying them also builds the three new
indexes, which on the fragmented production file took about 1300 s more.

The generate step has the same two costs on its own side. Its planning loop
asks, for every catalogued page, whether the output and the page's sidecar
layers exist, and looks the page up in the per-document manifest. On
2026-09-07 that loop ran 1 h 46 min over 458,674 pages with nothing to
render: one GETATTR per file on the NFS mount, and the manifest, a 488 MB
SQLite file, paged in over NFS at 400 reads per second. The loop now runs
under `compress.dir_cache`, one scandir per directory instead of a stat per
file, and the manifest lives beside the catalog under `catalog_root`
(`<catalog_root>/.build/manifest.sqlite`). The first run after that change
copies the manifest there from the data tree once and leaves the old file in
place; a machine that rsyncs the corpus copies the manifest with the catalog.

A run that changes nothing still paid about 900 s of staleness scans on
2026-09-07 (parse 461 s cold, then versions, relate, dump and generate's
gate re-walking the same trees warm) plus 790 s of `stats compute`. Three
rules now cut that:

- One stat pass per run. `freshness.stat_records` keeps every path's size
  and mtime for the run, so relate's walk serves dump, the cross-pass gate
  and generate's gate. A stage or hook that writes files drops the cache.
- `stats compute` is gated on the catalog's change stamp
  (`catalog.sqlite.stamp`, written by every relate that wrote rows) instead
  of running unconditionally. A run where relate skipped every source skips
  it too, and the dated snapshot is only taken when the corpus moved.
- A full generate whose only moved input is the catalog signature renders
  just the documents this run parsed and the pages that show them, both ends
  of every link, instead of checking all 458,674 pages. The run must prove
  that is the whole set: every published source's parse ran in this run, the
  cross-document layers (`.ann`, `.corr`), the repeal dates and the render
  code are unchanged, and the ledger shows the last run that parsed anything
  also completed a full generate. A changed `.versions.json` sidecar adds
  its own statute's page (the records of those sidecars live in
  `.build/generate-own-layers.records`).
  Otherwise the per-page scan runs as before. The aggregate pages and the
  gate record are written either way, so the next unchanged run skips.

Download has no such scan — nothing on disk decides what it fetches — so its
line names the harvest watermark instead: `(from 2026-01-10)`, or
`(first harvest)` / `(full sweep)` when there is no boundary to work back to.

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

### A forarbete listing item that names no number

regeringen.se lists the odd document under its title alone — prop. 2025/26:223
is "En ny konsumentkreditlag" and nothing else. `forarbete download` carries
such an item on with no basefile and reads its number off the landing page
instead (the vignette, then the text of the links to the document files), so
that document is harvested rather than silently skipped.

One consequence reaches `--only`. The walk cannot match an unnamed item
against the basefile you asked for without resolving it first, so
`lagen forarbete download prop --only 2025/26:223` also **stores** every unnamed
item it passes on the way down the listing. Those are documents the corpus
wants, but they are not the one you named.

An item that names no number **anywhere** is rejected. That is the ordinary
case for the kommittédirektiv index, which also holds regeringsuppdrag: 34 of
the newest 400 items (8.5%) carry no `Dir.` number at all. Nothing on disk
records a rejection, so each of those costs one landing-page GET on every run,
and each one resets the watermark's consecutive-hit stop — the walk then runs
to its date boundary instead of stopping after 20 known documents. Measured
2026-09-07 over the newest 400 items per type: prop 4 (1.0%), sou 1 (0.2%),
skr 14 (3.5%), fm 0, dir 34 (8.5%). If that cost ever matters, the fix is a
rejected-items index like the one `remisser` keeps (`layout.REMISSER_SEEN`),
not a tighter identity guess.

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
the September cutover — see [`cutover.md`](cutover.md). `matomo` is the one
container both applications share. Its **UI is at `https://lagen.nu/matomo/`**
(`docker/nginx/ferenda.lagen.nu.conf`); the legacy vhost's own `/matomo/` block
still answers at `old.lagen.nu/matomo/` and goes when that vhost does. The
reader-facing pages ping the tracker at the same `/matomo/` prefix, same-origin,
under the site id `lib/assets/matomo.js` maps their hostname to; the API and MCP
report server-side to their own site (`matomo_site_api`, `api/analytics.py`).

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

**Continuous deploy + nightly sync.** A push to `main` runs
`.github/workflows/deploy.yml`. Its first job is `checks`
(`.github/workflows/checks.yml`, a GitHub-hosted runner: pytest, ruff, ty, the
layer rule, `pip-audit`); the deploy job does not start unless it passes. The
deploy itself runs on the self-hosted runner on the prod host: update checkout
→ tag the outgoing image `lagen-ferenda:previous` → build → `up -d` → wait for
the container health check → smoke-test the public site through nginx →
publish the browser chrome. Any failure after the build puts
`lagen-ferenda:previous` back and restarts, so a release that does not come up
leaves the previous one serving.

The health check is `GET /healthz`: the app imported, `catalog.sqlite` answers
a query, and the generated tree is mounted. OpenSearch is reported beside those
three and never gates them — the site serves without search.

The deploy `reset --hard`s `~/wds/ferenda`, which holds the bind-mounted nginx
confs. It now saves the diff of a dirty tree to `~/wds/deploy-lost/<stamp>.patch`
first, so a host-side edit is recoverable. A recent git lock file stops the
deploy instead of being deleted; one older than an hour is removed as stale.

The same push does **not** fold in data. `staffan`'s crontab does that. `staffan`'s crontab runs the
pipeline as inlined `docker compose exec` lines: `lagen all all` nightly (which
now skips the browser-shielded föreskrift agencies skvfs/mtfs), plus a weekly
`lagen foreskrift browser-download` (Sundays) for those — the browser transport
runs one navigation at a time, so it stays off the nightly sweep.

`lagen rs browser-download` wants the same weekly slot, for the same reason and
one more. Skatteverkets 2,614 ställningstaganden are one browser navigation
each. The run paces them 20 seconds apart: at 2-second spacing the site's front
refuses navigation 31 and keeps refusing for minutes. That is a rate rule, not a
bot verdict — no browser gets around it. A weekly run costs the register plus
what moved. The first run takes ~15 hours, so slice it with `--limit N` and let
the next run resume. Nothing is stranded — a run stores a record only once its
page is on disk. Run both browser jobs **one at a time**: Playwright's sync API
is not built for one browser per thread.

### The facsimile render gate

A facsimile render holds a worker thread in poppler for about a second. On
2026-09-05 scrapers filled all 40 worker threads this way and every other route
on lagen.nu timed out. Measured over 30 minutes of that wedge: 8795 of 8835
`sidN.png` requests carried no `Referer` at all, from 5938 addresses, and 275
of 300 sampled addresses never fetched a single HTML page.

`/api/v1/facsimile`, `/api/v1/sfs-graphic` and the legacy `sidN.png` paths now
serve a cached PNG to any caller, but refuse to *start a render* unless the
request shows it came from a lagen.nu page — `Sec-Fetch-Site: same-origin`, or
a `Referer` on our own host (`auth.from_own_page`). No header change was
needed for that: the vhost's `Referrer-Policy: strict-origin-when-cross-origin`
already sends the full URL on a same-origin request, and trims it to the bare
origin cross-origin. A refused render is `403`; every
render slot busy (`facsimile.RENDER_WORKERS`, 4) is `503` with `Retry-After`.
This is not access control — an already-rendered page stays public, and both
headers can be typed by hand — only a floor under how much CPU a script
walking URLs can spend on the box.

### Evicting the facsimile cache

`data/cache/facsimile` holds the page PNGs `lib/facsimile` renders on demand.
It is a **pure cache**. Nothing else reads it. A deleted file is re-rendered on
the next request, in about half a second.

The renderer evicts it itself when the filesystem runs low: every 200 renders
it reads the free space, and under `facsimile.CACHE_MIN_FREE` (20 GB) it drops
the oldest PNGs until 40 GB are free. This is the floor, not the policy — a
cron line by age is still the right way to keep the cache small, because the
in-process sweep only fires when the disk is already nearly full:

```sh
0 1 * * * find <data_root>/cache/facsimile -name "*.png" -mtime +15 -delete
```

Measured 2026-08-19 on dev: 245 PNGs use 34 MB. Production is a different
story — the legacy facsimile cache reached 658 GB while a cron line was failing
silently, which is why the writer now evicts as well.

Its siblings under `cache/` are not pure caches on the same terms.
`cache/pdfconv` (9.9 GB) holds the poppler conversions the parsers read. A lost
entry costs a re-conversion during a build, not during a request. Do not point
the same `find` at it.
