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

## Data layout

The pipelines read large data trees that live under `site/data/` (not all
committed):

```
site/data/sfs/{downloaded,golden,register}/   # SFS source + frozen golden corpus
site/data/domstol/downloaded/                 # DV new-API harvest (per court)
site/data/dv/{downloaded,intermediate}/       # DV legacy feed (.doc/.docx + old XML)
site/data/dv/identity-index.json              # canonical case -> source records
```
