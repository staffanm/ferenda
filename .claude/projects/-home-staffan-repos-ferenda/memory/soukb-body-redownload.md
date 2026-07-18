---
name: soukb-body-redownload
description: soukb-scans re-downloads KB SOU bodies (1922-99) from sou.kb.se index; PDF is the body, built not run
metadata:
  type: project
---

`accommodanda/forarbete/soukb.py` + verb `lagen forarbete soukb-scans`
re-downloads the KB-digitised SOUs (1922–1999). Built + tested (14 tests,
`test/test_forarbete_soukb.py`), verified end-to-end on one small doc (1922:1,
10.5 MB) into a scratch tree; **NOT run at corpus scale** — the real crawl is
hundreds of GB and needs an explicit go.

Unlike [[prop-frozen-to-harvested]]'s propkb-scans (facsimile-only, adds no
docs), here the **scanned OCR'd PDF *is* the body** (no XML sibling), so it
writes a fresh harvested record per basefile with the PDF(s) in `files`.

**Why / how it works:** the single HTML index at `https://sou.kb.se/` is the
source of truth (legacy `regina.kb.se` start URL is dead); it forgets the old
soukb records and rebuilds from the index. Basefile comes from the index
**label** (`basefile_of` broadens the legacy regex: `första serien`→`fs`,
letter suffixes lowercased, `1952:16/17`→`1952:16-17`). 5,814 distinct
basefiles; **128 are multi-volume** (label repeats across URNs, e.g. `1987:3` =
28 vols) → `files` is a list (`<slug>.pdf`, `<slug>-1.pdf`, …). Each part: URN
resolver page → single digark `.pdf` link → fetch → `%PDF` magic → store plain.
Resumable per part. Records are `.json.br` (read via `compress.read_text`).

**Pending / open:** whether to purge stale legacy soukb records whose basefile
the index no longer produces (a clean purge is defensible since "forget the
soukb records existed", but it's a destructive corpus mutation — confirm before
doing it).

**Year-segmentation DONE (2026-07-18):** the forarbete `downloaded/` + `artifact/`
trees are now `<typ>/<year>/<slug>` (see [[forarbete-tree-year-segmented]]), so a
future full soukb crawl writes straight into the segmented layout — no flat-dir
scaling concern.
