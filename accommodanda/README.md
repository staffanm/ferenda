# accommodanda — developer setup

The rebuilt ferenda pipeline: vertical source pipelines (SFS, DV) that go
from downloaded source files to a typed document model and a JSON artifact,
with the citation engine as a shared library. For *why* it's shaped this
way and what's done vs. pending, read [`../REWRITE.md`](../REWRITE.md);
this file is just how to get it running.

## Prerequisites

- **Python 3.10+** and **[uv](https://docs.astral.sh/uv/)**. `uv sync`
  installs everything in `pyproject.toml` (incl. `jpype1`).
- **A JVM — only for the legacy DV Word path** (`dv_word.py` reads binary
  `.doc`/`.docx` through Apache POI via jpype). Everything else (SFS, the
  citation engine, the DV API path) is pure Python and needs no Java.

  On Ubuntu 24.04:

  ```sh
  sudo apt-get install -y openjdk-21-jdk-headless
  ```

  jpype auto-discovers `libjvm.so`; you normally don't need `JAVA_HOME`.
  The `-headless` package is enough — POI's HWPF/XWPF reading needs no AWT.

- **The POI jar stack** (not committed — `vendor/poi/*.jar` is gitignored).
  Fetch once after checkout:

  ```sh
  ./tools/fetch_poi.sh
  ```

  Idempotent; pulls POI 5.4.1 + runtime deps from Maven Central into
  `vendor/poi/`.

## Quick start

```sh
uv sync                      # Python deps
./tools/fetch_poi.sh         # POI jars (legacy DV only)
uv run python -m pytest \
  test/test_lagrum.py test/test_sfs_parse.py test/test_sfs_register.py \
  test/test_dv_identity.py test/test_dv_parse.py test/test_dv_legacy.py
```

> Run the new suites by naming them explicitly. A bare `pytest test/`
> fails at collection: `test/` is a package and the legacy
> `integration*.py` files don't import under modern Python (out of scope).

## Module map

**SFS vertical**
| File | What |
|---|---|
| `extract.py` | body extraction from rkrattsbaser HTML (+ archival `<pre>`) |
| `reader.py` | `TextReader` — faithful port incl. autostrip blank-line semantics |
| `tokenizer.py` | recognizers → flat event stream |
| `assembler.py` | RANK-driven stack machine → document tree |
| `model.py` | typed dataclasses (`Forfattning`, `Kapitel`, `Paragraf`, …) |
| `nf.py` | tree → golden normal form (replicates old URI-minting quirks) |
| `register.py` | SFSR register page → amendments + change tuples |
| `__main__.py` | `parse` / `refs` / `validate` CLI |

**Citation engine (shared library)**
| File | What |
|---|---|
| `lagrum.py` | Lark/Earley engine; `LagrumParser(parse_types=…)` composes a grammar from LAGRUM / KORTLAGRUM / EULAGSTIFTNING / RATTSFALL / FORARBETEN / … |

**DV vertical (court decisions)**
| File | What |
|---|---|
| `download.py` | downloader for the rättspraxis API |
| `identity.py` | entity-resolution index (one canonical case ← many source records) |
| `model.py` | `Avgorande` model (metadata + ordered Rubrik/Stycke body + footnotes) |
| `parse.py` | **API path** — body from `innehall` HTML, metadata from curated fields |
| `structure.py` | instance/ruling segmenter (delmål → instans → betänkande/dom → domskäl/domslut) |
| `naming.py` | canonical case title — referat identity + HD's given names (`case_label`) |
| `namedcases.py` | harvester for HD's named-precedent list (`data/namedcases.json`) |
| `word.py` | **legacy path** — POI (HWPF/XWPF) → flat `(text, bold, in_table)` stream |
| `legacy.py` | legacy stream → head/body split → `Avgorande` |

## Running the pipelines

**SFS** (operates on the golden / downloaded trees under `site/data/sfs/`):

```sh
uv run python -m accommodanda.sfs parse site/data/sfs/downloaded/2018/585.json --basefile 2018:585
# golden = the old pipeline's parsed XHTML (site/data/sfs/parsed), normalized to NF on the fly
uv run python -m accommodanda.sfs validate site/data/sfs/parsed site/data/sfs/downloaded --sections structure,references
uv run python -m accommodanda.sfs refs FILE PARSED.xhtml  # citation diff for one doc
```

**DV** (operates on `site/data/domstol/` (API) and `site/data/dv/` (legacy)):

```sh
# download + build the identity index
uv run python -m accommodanda.dv.download site/data/domstol/downloaded   # [--full] [--no-bilagor] [--limit N]
uv run python -m accommodanda.dv.identity                       # -> site/data/dv/identity-index.json

# parse (API path is driver-owned; `[ids…]` parses just those, empty = all stale)
uv run python -m accommodanda.build dv parse                                       # API path, incremental
uv run python -m accommodanda.dv.legacy --index site/data/dv/identity-index.json   # legacy POI path, batch report
uv run python -m accommodanda.dv.legacy site/data/dv/downloaded/ADO/1993-100_1.doc # one Word file -> artifact
```

The DV parsers are driven by the identity index: each canonical case is
parsed from its single best source — the API record when present, the
legacy Word original otherwise (no cross-source merge; see REWRITE.md §4).

### Wiki content repo (begrepp + kommentar)

The hand-authored commentary (`kommentar`) and concept glossary (`begrepp`)
are **git-backed markdown** in a separate content repo (`lagen-wiki`),
checked out alongside this one and pointed at by `WIKI_ROOT`:

```sh
git clone <lagen-wiki remote> ../lagen-wiki    # or: git submodule update --init
uv run python -m accommodanda.build begrepp parse
uv run python -m accommodanda.build kommentar parse
```

`WIKI_ROOT` defaults to `../lagen-wiki` (a sibling of the repo); override it
with the `wiki_root` key in `config.yml` or the `WIKI_ROOT` env var. The
content layout is `concept/<Name>.md` (frontmatter `title:`) and
`commentary/<source>/<relpath>.md` (frontmatter `annotates:`) — the commentary
is filed under the source it annotates and that source's basefile→path rule, so
`SFS/1915:218` lives at `commentary/sfs/1915/218.md`. The parsed artifact mirrors
this — `kommentar/artifact/<host_source>/<host_relpath>.json` (e.g.
`kommentar/artifact/eurlex/2023/32023R2854.json`), reusing the host source's own
path transform (`layout.kommentar_host`) so commentaries on different sources can
never collide on one flat name. Concept links are
`[label](begrepp:Concept)`, external links are ordinary markdown
`[label](https://…)`, legal citations stay plain text (the citation engine links
them), and `aliases:` carries old names from MediaWiki redirects. The parser is
`lib/markdown.py`.

Each `## …` heading anchors the section to the host node it annotates, per host:

| heading | anchor | host |
|---|---|---|
| `## N §` | `#P{N}` | continuously-numbered SFS |
| `## N kap M §` | `#K{N}P{M}` | per-chapter SFS |
| `## Artikel N` | `#{N}` | EU act article |
| `## Artikel N.M` / `## Artikel N.M a` | `#{N}.{M}` / `#{N}.{M}.{a}` | EU sub-article (definition/list point) |
| `## Skäl N` or `## (N)` | `#recital-{N}` | EU recital |

`annotates:` is an SFS number (`2009:400`) or a CELEX (`32024R2847`); the host act
is resolved accordingly (`wiki.host_uri`). A section may carry prose **and** a
curated external-links list: a `## Externa länkar` bullet block attaches to the
section heading it sits under (per-article guidance, shown in that node's rail),
or to the act as a whole when it precedes any section heading (document-level,
shown in the "Om dokumentet" rail). Bullets are `- [label](https://…) — note`.

`lagen kommentar validate [basefiles…]` reports section anchors that match no node
in the annotated act (a mistyped `## Artikel 99` / amended-away `## 24 kap 2 §`);
the same check warns during `relate`.

`lagen kommentar ai-annotate <basefile>` (opt-in, LLM) is the AI guidance linker
(PRD Step 4). An annotation declares its external guidance documents by hand in a
`guidance:` frontmatter block — a list of `{title, url, pdf}` mappings, the `pdf:`
being the direct download link (a guidance doc is short-lived; the URL is not
derivable from the act):

```markdown
---
annotates: 32023R2854
guidance:
  - title: Frågor och svar om dataakten
    url: https://digital-strategy.ec.europa.eu/en/library/…-data-act
    pdf: https://ec.europa.eu/newsroom/dae/redirection/document/108144
---
## Externa länkar
- [Frågor och svar om dataakten (FAQ)](https://…) — Europeiska kommissionen
```

The action downloads + caches each PDF (under `kommentar/guidance/`), flattens it
to page-marked text, and asks the configured Berget model to map guidance sections
(FAQ questions) to the act's **fine-grained targets** — not just whole articles but
the sub-articles and recitals the act divides into: a single definition `2.21`, a
numbered paragraph `6.2`, a recital `recital-15` (the dotted sub-article / `recital-N`
anchor grammar `eurlex.structure` mints, shared with the renderer and the wiki
commentary headings, so a link lands on the exact node). A FAQ answer about two definitions links to exactly those two, not to
article 2 as a whole. The result is written as a **`.ann` sidecar** next to the
kommentar artifact — `{"guidanceLinks": {anchor: [{label, href, desc, section}]}}` —
the AI-created (then human-corrected) layer, kept separate from the hand-edited
markdown, mirroring eurlex's `.ann` editorial layer. `label` names the source and
its own section reference ("Frågor och svar om dataakten, question 8"), `desc` is
that section's title (the FAQ question), so the rail renders `link: question`. The
guidance document's own `section` (a FAQ question number) is the durable,
human-dereferenceable locator; the `#page=N` deep link is a convenience, located by
matching the section title back into the PDF (the model miscounts pages). Like every
`ai-*` action the LLM is called only here, never from a corpus-wide
parse/relate/generate. The `.ann` is woven into the annotated act's rail by
`render._kommentar_indexes` (it merges each kommentar `.ann`'s `guidanceLinks`
alongside the curated per-article guidance); a sub-article gets its citation anchor
+ rail only when something targets it, so a forced/full `generate` surfaces the AI
links on the right nodes.

A kommentar is a **separate source**: editing a `commentary/…md` file shows up on
the annotated act's page only after re-running the wiki pipeline and the catalog —
`lagen kommentar parse && lagen kommentar relate && lagen <host> generate
<basefile>` (e.g. `lagen eurlex generate 32024R2847`; the host's own
`parse`/`generate` stages never read the wiki).

The repo was seeded from the live MediaWiki SQLite DB, replaying the full
per-revision history as one git commit per revision:

```sh
uv run python tools/mediawiki_to_markdown.py path/to/lagen.sqlite ../lagen-wiki
uv run python tools/wiki_artifact_diff.py path/to/lagen.sqlite   # losslessness check
```

`wiki_artifact_diff.py` asserts the migration's safety property: for every
page, `markdown → artifact` is byte-identical to the old `wikitext →
artifact` (modulo two adjudicated, content-free normalisations — see the
script). `lib/wikitext.py` is retired from the pipeline and kept only as the
converter's/diff's reference.

## Data layout

The pipelines read large data trees that live under `site/data/` (not all
committed):

```
site/data/sfs/{downloaded,golden,register}/   # SFS source + frozen golden corpus
site/data/domstol/downloaded/                 # DV new-API harvest (per court)
site/data/dv/{downloaded,intermediate}/       # DV legacy feed (.doc/.docx + old XML)
site/data/dv/identity-index.json              # canonical case -> source records
```

## Production deployment (Docker)

The prod host runs one compose project that serves **both** sites behind the
legacy nginx: `lagen.nu` (the legacy stack) and `ferenda.lagen.nu` (this
rebuilt site). Which services start is chosen by a Compose **profile**:

| invocation | services | use |
|---|---|---|
| `docker compose up -d` | `opensearch` only | dev — run `lagen all serve` from the working tree |
| `docker compose --profile prod up -d` | full stack | prod — legacy stack + `accommodanda` + `opensearch` |

`opensearch` carries no profile, so it starts in both; everything else is
`profiles: [prod]`. On the prod host, set `COMPOSE_PROFILES=prod` in `.env` so
every command (`ps`, `logs`, `restart`, …) sees the whole stack without the flag.

**One-time host setup**

1. Tag the existing legacy app image so the `ferenda` service can reference it —
   it can't build from this branch (which deleted `requirements.txt`):
   `docker tag <old-ferenda-image> lagen-ferenda:legacy`.
2. Create the read-write data dirs the `accommodanda` service mounts:
   `/mnt/data/ferenda` (the corpus / `data_root`) and `/mnt/data/lagen-wiki`
   (a clone of the `lagen-wiki` markdown repo — `WIKI_ROOT`).
3. Point `ferenda.lagen.nu` DNS at the host and append ` ferenda.lagen.nu` to the
   nginx service's `DOMAIN` env so the ACME client reissues one SAN cert for both
   names (nginx boots either way; TLS just warns for the new name until then).

**Bring-up**

```sh
docker compose --profile prod up -d      # opensearch must report healthy first
```

`accommodanda` is a frozen image: the code is baked in, but it also carries the
full pipeline toolchain (poppler-utils, tesseract+swe, ocrmypdf, raptor2-utils,
a JRE + the POI jars), so **download + rebuild run in the container** against the
read-write corpus mount:

```sh
docker compose exec accommodanda lagen all download
docker compose exec accommodanda lagen all rebuild   # parse→relate→index→dump→generate
docker compose exec accommodanda lagen all index
# or, without the serving process running:
docker compose run --rm accommodanda lagen all rebuild
```

One uvicorn process serves the static site + REST API (`lagen all serve`, the
image `CMD`); the nginx `ferenda.lagen.nu` vhost reverse-proxies to it on `:8000`
(the app resolves lagen.nu's bare-URL grammar itself, so nginx needs no
`try_files` rules). The corpus mount is read-write and shared with serving; a
rebuild writes essentially per-file, but for strict isolation restart the
service afterwards (`docker compose restart accommodanda`).
