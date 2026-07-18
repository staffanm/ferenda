---
name: forarbete-tree-year-segmented
description: forarbete downloaded+artifact trees are <typ>/<year>/<slug>; layout.fa_dir/fa_year/fa_record_file; pm buckets under _
metadata:
  type: project
---

Done 2026-07-18. The förarbete `downloaded/` and `artifact/` trees are
**year-segmented**: `downloaded/forarbete/<typ>/<year>/<slug>.{json,pdf,xml,html}`
and `artifact/forarbete/<typ>/<year>/<slug>.json` (mirrors SFS `<year>/<nr>`).
Previously flat — prop ~62k, bet ~42k, rskr ~40k files in one dir.

**Code (all in `lib/layout.py`):** `fa_year(slug)` = `slug[:4]` if 4-digit else
`_` (the yearless `pm` docs, keyed by title-slug/diarienummer, bucket under `_`).
`fa_dir(root, typ, ident)` = `<root>/<typ>/<year>` — the dir a record and its body
files share, so bare `files` names still resolve. `fa_record_file(root, typ, ident)`
is the writer-side record path; `fa_record`, `fa_facsimile_pdf`, `fa_ocr_pdf`, and
`relpath("forarbete", …)` all route through the segment. Record glob is now
`<typ>/<year>/<slug>.json` (`*/*/*.json`); the `.watermark.json`/`.complete`
markers stay at `<typ>/` (a level above), so the 3-level glob skips them.

**Invariant:** the migration seg (`filename[:4]`) == the code seg
(`fa_year(basefile_slug)`) for every file, because filename == slug + suffix and
every slug is ≥4 chars.

**URLs + generated HTML UNCHANGED** — `page_relpath`/`page_url` derive from the
URI, not `relpath`; this was purely an on-disk downloaded/artifact reorg.

One-time in-place migration moved ~190k downloaded + ~97k artifact files
(scratchpad `migrate_fa_segment.py`, idempotent). Related: [[soukb-body-redownload]].
