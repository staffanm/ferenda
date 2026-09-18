# UHRFS — Universitets- och högskolerådets författningssamling, Universitets- och högskolerådet

Verdict: DEFECT
Site list: 77 designations from 2 pages (gällande 42, upphävda archive 35) (https://www.uhr.se/publikationer/lagar-och-regler-for-hogre-utbildning/Universitets--och-hogskoleradets-forfattningssamling/gallande-foreskrifter-i-lopnummerordning/)
We hold: 42 documents (uhrfs), newest UHRFS 2026:4; site newest UHRFS 2026:4
Missing: 35 — UHRFS 2013:1, 2013:2, 2013:4, 2013:10, 2015:5, 2019:1, 2019:5, 2022:1, 2014:1, 2014:2 (+25 more, all on the never-visited "upphävda" archive)
Extra: 0
Repeal gaps: 0 — our 3 self-repeal notices (2019:3, 2023:5, 2023:6) parse correct `rpubl:upphaver` targets (2013:2, 2019:5, 2022:1); those targets are just missing documents (see above), not a parse defect
Title defects: 0 — spot-checked all 42 in-force documents against site link text
Consolidations: site yes (10 current + at least 2 more in the archive), we hold 0
Inherited: hsvfs (Högskoleverket) — not referenced on any UHR page fetched; we hold no hsvfs documents; matches the code's own comment ("no HSVFS predecessor appears"); no defect
Issue: https://github.com/staffanm/ferenda/issues/105

## Evidence

- Catalog: `select count(*) from documents where kind='uhrfs'` → 42, newest label UHRFS 2026:4.
- Fetched (via `ferenda.lib.net.request`, 3 requests total, well under the 60-request budget):
  1. `.../gallande-foreskrifter-i-lopnummerordning/` — 62 PDF links; after
     dropping konsekvensutredning/promemoria/rättelse/förteckning/remiss
     companions (the harvest's own skip regex), 42 distinct designations
     remain, an exact match to our 42 catalog rows.
  2. `.../upphavda-foreskrifter/` — linked from the same page's nav, never
     visited by `uhrfs_enumerate`. 42 PDF links, 35 distinct regulation
     designations after the same filtering, none present in our catalog.
  3. `.../konsoliderade-foreskrifter/` — 10 current consolidated PDFs.
     The archive page adds at least 2 more (for repealed base acts). Our 42
     `site/data/downloaded/foreskrift/uhrfs/*.json.br` records carry 0
     `files.consolidation` entries.
- `ferenda/foreskrift/agencies.py:1790-1832` — `uhrfs_enumerate` reads only
  `agency.index_url` (the gällande page); no second index URL for the
  archive.
- #76 cross-check: `uhrfs-2014-042.pdf` (minted as UHRFS 2014:42) prints
  "UHRfs 2014:4" on its own cover (`pdftotext` of
  `site/data/downloaded/foreskrift/uhrfs/uhrfs-2014-42-regulation.pdf`).
  Confirmed the discrepancy the briefing named; already tracked by #76, not
  refiled.
- No prior open issue for uhrfs (`gh issue list --search "foreskrift uhrfs"`
  returned only #76).
