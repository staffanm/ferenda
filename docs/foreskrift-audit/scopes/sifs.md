# SIFS — Spelinspektionens författningssamling, Spelinspektionen

Verdict: DEFECT
Site list: 52 designations from 2 register PDFs + 1 entry page (https://www.spelinspektionen.se/lagar-regler/foreskrifter/); the harvest's own JSON API returns 22 live items (1 page, no pagination gap)
We hold: 45 documents (25 sifs + 20 lifs) covering 37 distinct designations, newest SIFS 2026:3; site newest SIFS 2026:3
Missing: 2 confirmed actionable (SIFS 2023:2, SIFS 2024:4) — plus 14 pre-2018 LIFS designations no longer reachable anywhere on the live site (not actionable, see Evidence)
Extra: 8 — LIFS documents also minted under sifs (issue #50 shape: 6 byte-identical PDFs, 2 same text/different render)
Repeal gaps: 0 — every `upphaver` link checked against the agency's own register matches
Title defects: 6 — sifs/2018:8 (phantom document), sifs/2022:3, sifs/2023:1, sifs/2024:2, sifs/2024:3, sifs/2026:2 (title null; 4 of these never got a regulation PDF at all, one got the wrong PDF)
Consolidations: site yes (grundförfattning + tracked amendments; guidance text references an explicit "Konsoliderad version"), we hold 2 (sifs/2018:4, sifs/2020:2)
Inherited: lifs — 20 held vs 34 listed in the agency's own historical register; the 14 missing (2002-2016 era) are gone from the live site entirely (search confirmed), not renumbered into sifs. The 8 LIFS-under-sifs duplicates are the #50 shape, not a renumbering.
Issue: https://github.com/staffanm/ferenda/issues/88

## Evidence

### 1. Enumeration
- Entry page (https://www.spelinspektionen.se/lagar-regler/foreskrifter/) carries no inline
  document list. It links two PDFs and nothing else document-related:
  - "Register över Spelinspektionens författningssamling" (9 pages) — full historical
    register since 2002, 34 LIFS + 18 SIFS designations, with "Ersatt av / Upphävd genom /
    Ersätter" footnotes for every entry.
  - "Spelinspektionens föreskriftsförteckning" (6 pages) — the in-force set as of
    SIFS 2026:3: grundförfattningar plus their tracked amendments.
- The harvest (`ferenda/foreskrift/agencies.py` `SIFS` agency, `json_enumerate`) reads
  `https://www.spelinspektionen.se/api/regulationapi?query=&showAll=true&top=100&skip=0`.
  Verified live: `totalMatching: 22`, `len(items): 22` — one page, matches what
  `json_enumerate` collects. No pagination gap (unlike AgVFS #42).

### 2. Extra — issue #50 shape (confirmed, not refiled)
Of the API's 22 items, 8 carry an "LIFS" heading, not "SIFS": 2014:1, 2017:1, 2018:2,
2018:4, 2018:5, 2018:6, 2018:7, 2018:10. The SIFS harvest mints all 22 API items under the
`sifs` slug regardless of the printed prefix, so these 8 exist twice: once correctly under
`lifs/<n>` (from the historical migration) and once wrongly under `sifs/<n>`.

    for n in 2014-1 2017-1 2018-10 2018-2 2018-4 2018-5 2018-6 2018-7; do
      md5sum site/data/downloaded/foreskrift/sifs/sifs-$n-regulation.pdf \
             site/data/downloaded/foreskrift/lifs/lifs-$n-regulation.pdf
    done

    2017-1, 2018-2, 2018-4, 2018-5, 2018-6, 2018-7: byte-identical
    2014-1, 2018-10: different bytes, identical pdftotext (re-rendered/re-scanned copy
      of the same document — `diff <(pdftotext sifs..) <(pdftotext lifs..)` is empty for
      2014-1 bar one blank line; 2018-10 differs only in line-wrap)

This is the same shape as `sifs/2018:2` cited in issue #50 ("byte-identical PDF under
both... e.g. sifs/2018:2 whose title is literally 'LIFS 2018:2'"). Not refiled.

### 3. Missing
Register footnotes trace an "avgifter för tillsyn" (supervision fee) chain:
LIFS 2016:1 -> SIFS 2019:3 -> **SIFS 2023:2** -> **SIFS 2024:4** -> SIFS 2026:1.
We hold 2019:3 and 2026:1 but neither 2023:2 nor 2024:4 — both fully superseded, so the
live JSON API (which only lists 22 currently-referenced items) no longer carries them, and
their landing pages now return 404:

    https://www.spelinspektionen.se/lagar-regler/foreskrifter/sifs-20232/  -> 404
    https://www.spelinspektionen.se/lagar-regler/foreskrifter/sifs-20244/  -> 404

`links` also shows `sifs/2026:1 upphaver sifs/2024:4` — a repeal target we don't hold,
consistent with the repeal model (the target's absence is not itself a defect), but it
confirms the designation existed and its harvest window has closed.

14 pre-2018 LIFS designations in the register (2002:1, 2006:1, 2007:1, 2008:1, 2009:1,
2010:1, 2010:2, 2012:1, 2012:2, 2012:3, 2013:2, 2015:1, 2015:2, 2015:3) are also absent from
our corpus. A live full-text search for one of them found nothing:

    api/regulationapi?query=LIFS+2013&showAll=true -> LIFS 2017:1, 2018:x, SIFS 2022:3/2024:2
    (no LIFS 2013:1/2/3 designation-specific hits)

These are Lotteriinspektionen-era documents with no landing page and no live search hit;
nothing on spelinspektionen.se points at a fetchable copy. Reported, not filed — there is
no reachable source to harvest.

### 4. Repeal marking
Checked every `rpubl:upphaver` row against the register's own footnotes (LIFS 2014:1 ->
2002:1, LIFS 2016:1 -> 2015:2, LIFS 2017:1 -> 2015:3, LIFS 2018:11 -> 2017:2, LIFS 2018:12
-> 2016:1, LIFS 2018:2 -> 2013:1 & 2014:2, LIFS 2018:8 -> 2013:3, SIFS 2019:1 -> LIFS
2004:1, SIFS 2019:2 -> LIFS 2018:11, SIFS 2019:3 -> LIFS 2018:12, SIFS 2020:1 -> LIFS
2018:1, SIFS 2022:1 -> SIFS 2020:1, SIFS 2022:2 -> LIFS 2018:3, SIFS 2025:1 -> LIFS 2018:9,
SIFS 2026:1 -> SIFS 2024:4). All match. No gaps.

### 5. Title defects — root cause: `classify_href` link-role bug
`sqlite3` query on `documents` shows 6 sifs rows whose `title` equals their own `label`
(no real title) or is a phantom entry:

    sifs/2018:8   title "SIFS 2018:8"   source_url .../vagledning-for-tekniska-...-lifs-2018-8.pdf
    sifs/2022:3   title "SIFS 2022:3"   (has a PDF, but the wrong one, see below)
    sifs/2023:1   title "SIFS 2023:1"   (no PDF downloaded)
    sifs/2024:2   title "SIFS 2024:2"   (no PDF downloaded)
    sifs/2024:3   title "SIFS 2024:3"   (no PDF downloaded)
    sifs/2026:2   title "SIFS 2026:2"   (no PDF downloaded)

Two distinct bugs in `classify_href` (`ferenda/foreskrift/harvest.py:201-216`) cause this:

**(a) A guidance PDF's filename is misread as the regulation.** The `sifs-2022:3` landing
page hangs three PDFs: the actual regulation, a Swedish "Vägledning" (guidance), and an
English "Guidelines" translation — all three filenames embed `sifs-2022_3` because the
guidance documents are named after the regulation they explain:

    sifs-2022_3-spelinspektionens-foreskrifter...pdf                (the actual regulation)
    vagledning-for-sifs-2022_3-och-for-1-och-4-kap...pdf             (Swedish guidance)
    guidelines-for-sifs-2022_3-and-for-chapters-1-and-4...pdf        (English guidance)

`classify_href` only rejects a link containing "konsekvensutred"; it does not recognise
"vagledning"/"guidelines". All three match `RE_SLUG_NUMBER` on `2022`/`3`, so all three are
classified `role="regulation"`, and `resolve_landing` (`harvest.py:374-376`) overwrites
`files["regulation"]` unconditionally on every match — the last one in DOM order (the
English guidelines PDF) wins. Confirmed by reading the stored PDF:

    pdftotext -l 1 site/data/downloaded/foreskrift/sifs/sifs-2022-3-regulation.pdf -
    -> "Version 1.1  May 2025 ... Guidelines for SIFS 2022:3 ... on technical requirements..."

That is the English guidelines document, not the regulation. Because the parse stage reads
the wrong PDF, `metadata.title` comes back null. This document is not in issue #76's list
(that check only looks for the record's own number appearing on pages 1-2 — the guidance
PDF prints "SIFS 2022:3" repeatedly because it is *about* SIFS 2022:3, so the check passes
even though the stored file is wrong) and is not the #52 shape (that is one file stored
under two *different* designations; here the file matches its own designation's landing
page, just the wrong link on it).

**(b) An amending regulation's own PDF is classified as an amendment of itself.**
`classify_href` (`harvest.py:214-215`):

    if re.search(r"\bandring|\bändring", name):
        return ("amendment", ars, lop)

fires before the `regulation` vs `amendment` comparison on the next line, so any
regulation whose own filename contains "ändring" — which is normal: an amending
föreskrift is literally titled "Föreskrifter om ändring i ..." — is always classified
`role="amendment"`, even when `(ars, lop) == (base_ars, base_lop)`, i.e. even when the link
is the document's *own* body, not a reference to another document's amendment.
`amendment` is outside `DOWNLOAD_ROLES` (`{"regulation", "consolidation"}`), so the PDF is
recorded as a reference only and never fetched. This hits every SIFS ändringsföreskrift
whose landing page hangs only its own PDF:

    sifs-2023-1 landing page: one link, "SIFS 2023:1 - Ändring i SIFS 2020:2",
      filename …sifs-2023_1-foreskrift-om-andring-i-sifs-2020_2….pdf -> classified amendment
    sifs-2024-3 landing page: one link, filename …sifs-2024_3-...-andring-i-...sifs-2020_2.pdf
    sifs-2026-2 landing page: one link, filename …sifs-2026_2-...-andring-i-...sifs-20192….pdf

`sifs/2024:2`'s landing page hangs its own PDF (also "andring" in the filename, same bug)
plus the two SIFS-2022:3 guidance PDFs from bug (a) — either bug alone would drop its own
regulation.

Contrast with `sifs/2022:1` ("Föreskrifter om att upphäva...", no "ändring" in its
filename) and `sifs/2026:1` (a new fee schedule, not an amendment) — both classified and
downloaded correctly, both carry real titles in the catalog.

**(c) A phantom document: `sifs/2018:8`.** Its download record (`source: "myndfs-legacy"`)
stores the same Swedish "Vägledning" PDF as bug (a)'s guidance link
(`vagledning-for-tekniska-foreskrifter-og-allmanna-rad-lifs-2018-8.pdf`), read as if it
were a regulation "SIFS 2018:8". No such designation exists in the agency's register or
föreskriftsförteckning — the guidance document is filed under LIFS 2018:8's own materials,
not as a separate föreskrift. `pdftotext` on the stored PDF confirms it is a guidance text
("Vägledning för Lotteriinspektionens föreskrifter... (LIFS 2018:8)..."), not a regulation.

### 6. Consolidations
The site publishes at least one explicit "Konsoliderad version" (referenced in the
sifs/2022:3 guidance PDF text: "Konsoliderad version av LIFS 2018:4 inklusive ändring
enligt SIFS 2024:2"). We hold 2 consolidation files:

    site/data/downloaded/foreskrift/sifs/sifs-2018-4-consolidation-2018_4.pdf
    site/data/downloaded/foreskrift/sifs/sifs-2020-2-consolidation-2020_2.pdf

### 7. Inherited series (lifs)
20 held vs 34 in the register (see Missing). Every LIFS document we hold sits under the
`lifs` slug, not renumbered into `sifs` — the only cross-slug issue is the #50-shape
duplication in section 2, which is the opposite direction (predecessor doc *also* minted
under the successor slug, not moved).

### 8. Freshness
Newest in the register: SIFS 2026:3 (2026-04-23), matching `documents.date` for our
`sifs/2026:3`. We are current with the live site.
