---
name: accommodanda-storage-layout
description: Target on-disk + URL layout conventions for accommodanda sources (the layout consolidation)
metadata:
  type: project
---

The `accommodanda` layout consolidation (centralised in `accommodanda/lib/layout.py`)
targets a uniform `DATA/<dir>/{downloaded,artifact}/<relpath>` storage scheme,
but the public URL grammar and a few source-specific shapes are deliberately
NOT uniform. Conventions confirmed by the user (staffan):

- **Internal storage** is uniform: each source has `<dir>/downloaded/` (raw) and
  `<dir>/artifact/` (parsed). Filenames preserved on migration so `hash_files`
  (name+content) keeps parse fresh — a move needs only a re-relate + dv reindex.
- **Public URLs follow lagen.nu's grammar, not the storage dir**: dv → `/dom/<court>/<id>`
  (e.g. `/dom/nja/2022s1136`); förarbeten → per-type top-level `/prop/...`, `/sou/...`
  (`layout.page_relpath` routes each förarbete type to its own segment, not a shared
  `/fa/`); sfs → root `/1962:700`; eurlex → `/ext/celex/<CELEX>`.
- **dv's canonical dir is `dom/`, not `dv/`**: API records (was `domstol/downloaded`)
  → `dom/downloaded/`; ALL parsed case-law artifacts → `dom/artifact/` regardless of
  source store. `dv/` holds ONLY the legacy raw store at `dv/downloaded/`; no other
  directories are ever created under `dv/`. The identity index unions the two raw
  stores: `reindex(dvdir=dv/downloaded [legacy], domstoldir=dom/downloaded [api])`.
- **sfs raw is flat**: the beta-API JSON lives at `sfs/downloaded/<year>/<nr>.json`
  (NOT a `source/` subdir — there is no live legacy HTML to need disambiguating).
  Superseded consolidations go in a sibling `sfs/downloaded/archive/<year>/<nr>/<version>.json`;
  any legacy SFST/SFSR HTML would sit in `sfs/downloaded/{sfst,sfsr}/`. Don't add a
  `source/` wrapper.
- **kommentar + begrepp share one raw dump** (`mediawiki/downloaded/`) — one raw
  source feeding two derived sources; their `artifact/` trees already conform.

Migration is staged: step 1 (done) centralised the *current* paths into `layout.py`
with byte-identical output; step 2 flips `relpath` to the uniform scheme + moves
files. Do the eurlex move only with the live CELLAR crawl stopped. See [[eurlex-caselaw-scope]].
