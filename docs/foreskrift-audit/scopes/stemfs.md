# STEMFS — Statens energimyndighets författningssamling, Energimyndigheten
Verdict: DEFECT
Site list: 42 designations from 1 page (gällande) + 48 designations (44 STEMFS,
4 NUTFS) from 1 more page (upphävda), https://www.energimyndigheten.se/om-oss/foreskrifter/
We hold: 27 documents (stemfs), newest STEMFS 2026:2; site newest STEMFS 2026:2
Missing: 16 (gällande page, silently dropped) + 44 (upphävda page, never
  visited) + 7 (amendment designations only named in the "Ändrad" column) —
  see Evidence for full lists
Extra: 1 — STEMFS 2023:1, repealed by STEMFS 2026:2 in our own links (correct
  shape, not a defect)
Repeal gaps: 0 found among documents we hold; cannot check repeal chains for
  the 44 archived documents we never harvested
Title defects: 0 — "elcertifkat" (STEMFS 2011:4) looks like a typo but is the
  agency's own spelling, verified against the source PDF
Consolidations: site yes (at least 3: STEMFS 2011:4, 2016:1, 2018:4), we hold 0
Inherited: none (per assignment) — note NUTFS (Närings- och teknikutveck-
  lingsverket, STEMFS's real predecessor) appears on the upphävda page; not in
  the assignment's "inherited series", so only noted, not scored
Issue: https://github.com/staffanm/ferenda/issues/95

## Evidence

### 1. Catalog holdings
    sqlite3 file:site/data/catalog.sqlite?mode=ro "select label from documents
    where kind='stemfs'"
27 rows, STEMFS 2011:4 .. STEMFS 2026:2. Download records:
`site/data/downloaded/foreskrift/stemfs/*.json.br` — also 27, none with
`files.consolidation`.

### 2. Entry page (gällande föreskrifter)
Fetched https://www.energimyndigheten.se/om-oss/foreskrifter/ with
`requests.get` + `BROWSER_UA` (one request). `div.fake-td[data-headline="Nummer"] a`
(the agency's own `link_select`, `ferenda/foreskrift/agencies.py:362`) yields 42
unique designations after resolving split anchors (`STEMFS` + `2025:7` in two
cells) with a tolerant `(\d{4})\D{0,3}:\D{0,3}(\d+)` regex.

Comparing the 42 to the 27 we hold (minus the repealed STEMFS 2023:1, which the
site correctly omits): 16 are missing —
2024:2, 2024:3, 2024:4, 2024:5, 2024:6, 2024:7, 2024:8, 2024:9, 2024:10,
2024:11, 2025:1, 2025:2, 2025:3, 2025:4, 2025:5, 2026:1.

Root cause: the agency's own markup inserts an invisible character between the
colon and the lopnummer for these 16 rows — a zero-width space (U+200B) in 15
of them, a soft hyphen (U+00AD) before the colon in STEMFS 2025:1:

    'STEMFS 2024:​5'
    'STEMFS 2025\xad:1'

`ferenda/foreskrift/harvest.py`'s generic `ref()` (used by `indexed_enumerate`)
matches the designation with `RE_FS_NUMBER = re.compile(r"\b([A-ZÅÄÖ-]+FS)\s*(\d{4}):(\d+)")`
and, as a fallback, `RE_COLON_NUMBER = re.compile(r"(\d{4}):(\d+)")` — both
require a digit immediately after the colon, so a break-hint character there
makes the match fail. `ref()` then returns `None` and the row is dropped with
no `Skip`, no error, no log line: the walk finishes clean (`.watermark.json`
says `dirty: false`) while quietly never enumerating these 16 documents.

The library already has the fix ingredient, unused at this call site:
`ferenda/lib/util.py:1178`, `RE_BREAK_HINT = re.compile("[­​-‏⁠﻿]")`,
wired up as `normalize_hints()` and used by `element_text()` elsewhere, but
`indexed_enumerate` (`ferenda/foreskrift/harvest.py:561`) passes
`a.get_text(" ", strip=True)` straight into `ref()` without normalizing.

Verified each of the 16 has a working landing page and PDF, e.g. STEMFS
2024:5's landing page (fetched once) links
`GetTemplateResource/121?...&fn=STEMFS 2024_5webb.pdf`, a real 2-page,
864 kB regulation — not a placeholder or blocked resource.

### 3. Ändrad/Konsoliderad column — never enumerated at all
The same page's rows carry a second column, `data-headline="Ändrad/konsoliderad"`,
naming amending and consolidated documents inline, e.g. for STEMFS 2018:4:

    <strong>Ändrad:</strong> STEMFS 2021:4, STEMFS 2019:1
    <strong>Konsoliderad:</strong> stemfs-4-2018-konsoliderad.pdf

`agency.params["link_select"]` only selects
`div.fake-td[data-headline="Nummer"] a`, so this column's links are never
enumerated. Extracted from the column directly: 8 amendment references, 7 of
which never appear in the Nummer column as a current standalone entry (so
never even attempted by the harvest): STEMFS 2016:2, 2017:1, 2018:1, 2018:5,
2019:1, 2021:4, 2023:5. None of the 7 are in our catalog. 3 konsoliderad PDFs
are named (STEMFS 2011:4, 2016:1, 2018:4) — none are in our download records
(`files.consolidation` is empty for all 27).

### 4. Upphävda föreskrifter — archive page never visited
The gällande page itself links it:
`<a href="/om-oss/foreskrifter/upphavda-foreskrifter/">upphävda föreskrifter</a>`.
`STEMFS.index_url` (`ferenda/foreskrift/agencies.py:365`) is only the gällande
page; there is no second `index_urls` entry for the archive, so
`indexed_enumerate` never fetches it.

Fetched https://www.energimyndigheten.se/om-oss/foreskrifter/upphavda-foreskrifter/
(one request). Its table uses the same `fake-td` markup but without
`data-headline`, so even a corrected `index_urls` list would need its own
`link_select` (`div.fake-table div.fake-tr div.fake-td:first-child a`, matched
against 50 rows). It names 44 STEMFS and 4 NUTFS (the real predecessor
samling, before the STEMFS code existed) designations, none of which are in
our catalog except STEMFS 2023:1 (already held, correctly marked repealed by
STEMFS 2026:2). Examples: STEMFS 2001:2, 2003:4, 2005:4..2005:13 (a full run
of ten), 2006:1, 2008:2, 2009:1, 2009:4, 2010:2, 2010:3, 2010:5, 2011:2,
2012:1, 2012:3, 2012:4, 2015:2, 2016:4, 2016:5, 2017:2, 2017:3, 2018:2,
2020:2..2020:15 (nine of them), 2021:1, 2021:3, 2021:7, 2021:11; NUTFS
1998:3, 1998:4, 1998:5, 2000:5.

This is the same pattern already confirmed in AFS (#45), EIFS (#51) and seven
other scopes.

### 5. Titles
Compared the 26 site-listed, non-repealed titles against `documents.title` —
all match except cosmetic differences (our records add "(STEMFS YYYY:N)"
inline, the site's own text sometimes ends with ";" or carries break-hint
characters we don't). One apparent typo, STEMFS 2011:4 "elcertifkat" (missing
"i"), is the agency's own spelling — confirmed with
`pdftotext -f 1 -l 1 stemfs-2011-4-regulation.pdf`, which prints "elcertifkat"
and "Defnitioner" throughout the source PDF. Not a corpus defect.

### 6. Consolidations
Site publishes them (see #3 above): at least 3 named. We hold 0
(`files.consolidation` empty in all 27 `site/data/downloaded/foreskrift/stemfs/*.json.br`).

### 7. Inherited series
Assignment says none. NUTFS documents surface on the upphävda page as
STEMFS's real historical predecessor (Närings- och teknikutvecklingsverket),
but that is outside the assignment's inherited-series list, so it is reported
here only as context for the upphävda-page gap, not scored as its own defect.

### 8. Freshness
Site's newest Nummer-column entry: STEMFS 2026:2. Our newest: STEMFS 2026:2.
Matches — the newest single document happens to have a clean designation
string, so it survived the break-hint bug that ate the 16 mid-2024–2025
entries below it.

### #76 cross-check
`gh issue view 76` body has no STEMFS entries.
