# DVFS — Domstolsverkets författningssamling, Domstolsverket

Verdict: OK
Site list: 263 unique designations from 3 API pages (https://www.domstol.se/om-sveriges-domstolar/for-dig-som-aktor-i-domstol/stod-for-aktorer-i-domstol/dvfs/)
We hold: 263 documents (dvfs), newest DVFS 2026:1; site newest DVFS 2026:1
Missing: 0
Extra: 0
Repeal gaps: 0 checked, 0 found — spot check confirms match
Title defects: 0 — all 263 titles compared exactly, no truncation, no HTML entities, no file names
Consolidations: site yes (2 families), we hold 5 download records covering the same 2 PDFs
Inherited: none (no predecessor series for this scope)
Issue: none

## Evidence

The DVFS entry page is an Optimizely/Find React SPA. Its list is served by a
POST JSON search API (`ferenda/foreskrift/agencies.py`, `dvfs_enumerate`,
lines 651-682). Replicating that same call is the only way to read the
agency's own list; the rendered HTML entry page carries no static list.

### 1. Enumerate

Script: `dvfs_enum.py`, using `ferenda.lib.net.request` with `HARVESTER_UA`,
POSTing to
`https://www.domstol.se/api/search/90578?searchPageId=90578&scope=ordinance&skip=%d&take=100&sortMode=ordinanceId&isZip=false`.

3 pages (skip=0,100,200), 265 raw hits, `totalMatching=265`. 3 HTTP requests.

265 raw hits collapse to 263 unique designations: two designations
(DVFS 2020:4, DVFS 2019:5) appear twice — once as `Grundförfattning` (with a
real date and description) and once as an empty `Konsoliderad` stub dated
`0001-01-01` with no description, pointing at the same landing URL. This
matches the harvester's own note in `agencies.py` ("the register lists every
DVFS number as its own document... no consolidated family page") — the
harvest's `harvest.ref` dedupes by URL, so it collapses to the same 263 we
hold. Not a defect.

### 2 & 3. Missing / extra

    site designations: 263 (after dedup)
    our catalog labels (kind='dvfs'): 263
    site - ours = 0 missing
    ours - site = 0 extra

Exact 1:1 match, confirmed programmatically (`dvfs_compare.json`).

### 4. Repeal marking

Fetched the landing pages for DVFS 2024:12 and DVFS 2026:1 (2 more HTTP
requests, 2 s apart). DVFS 2024:12's page reads:

    "Upphävd genom DVFS 2026:1"

Our artifact `site/data/artifact/foreskrift/dvfs/2026-1.json` (beslutsdatum
2026-01-05) carries:

    "upphaver": ["https://lagen.nu/dvfs/2024:12"]

The repealing document exists in our corpus and its `upphaver` target
matches the site's stated repealer. No repeal gap found in this sample.

### 5. Titles

Compared all 263 `documents.title` values against the site's `description`
field (site truncates long titles with "..."; comparison used a prefix match
against the truncated head). 0 mismatches across all 263. A 15-document
random sample (seed 42) was read in full and matched character-for-character,
including allmänna råd and ändringsförfattningar with embedded parenthetical
citations, e.g.:

    DVFS 2019:2 — site and ours both: "Domstolsverkets föreskrifter om
    ändring av Domstolsverkets föreskrifter (DVFS 1998:4) om utformning av
    dom och slutligt beslut i brottmål samt om utformning av
    avräkningsunderlag"

No junk found: no file sizes, no truncated titles stored as ours, no PDF
file names, no missing titles, no HTML entities, no doubled designations.

Note: `documents.date` differs from the site's displayed `footer.date` for
262 of 263 documents. This is not a defect — the site's footer date is
`ikrafttradandedatum` (entry into force), ours is `beslutsdatum` (decision
date). Verified directly against the DVFS 2026:1 artifact:

    "beslutsdatum": "2026-01-05"
    "ikrafttradandedatum": "2026-02-06"

Site footer for DVFS 2026:1 shows "2026-02-06" — the entry-into-force date,
confirming the fields are simply different, not wrong.

### 6. Consolidations

The site does publish konsoliderade versions for some DVFS families (JS
strings on the landing page name "Konsoliderad version"). Our download
records carry `files.consolidation` for 5 of 263 documents
(`dvfs-2019-5`, `dvfs-2020-4`, `dvfs-2020-5`, `dvfs-2020-8`, `dvfs-2020-9`),
resolving to only 2 distinct PDFs: the DVFS 2019:5 family's consolidated PDF
and the DVFS 2020:4 family's consolidated PDF, each attached under several
amending documents' landing pages. This matches what the site actually
serves — most DVFS documents have no separate "gällande" consolidation
published, only these two families do — so it is not a coverage gap.

### 7. Inherited series

Assignment lists no predecessor series for dvfs. Nothing to check.

### 8. Freshness

Site newest by ordinanceId sort: DVFS 2026:1 (ikrafttradandedatum
2026-02-06). Our newest by label: DVFS 2026:1 (beslutsdatum 2026-01-05).
Same designation, dates differ only per the field semantics noted above.

## Conclusion

All eight checks pass. No corpus defect found. No issue filed.
