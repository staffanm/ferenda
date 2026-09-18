# TPPVFS — Totalförsvarets plikt- och prövningsverks föreskrifter, Totalförsvarets plikt- och prövningsverk
Verdict: DEFECT
Site list: 4 designations from 1 page (in force) + 2 from 1 archive page (https://www.pliktverket.se/om-myndigheten/vart-uppdrag/lag-och-ratt/foreskrifter)
We hold: 4 documents (1 tppvfs + 3 trmfs), newest TPPVFS 2024:1; site newest TPPVFS 2024:1
Missing: 2 — TRMFS 2017:2, TRMFS 2017:3 (both on the "Upphävda föreskrifter" archive sub-page, never visited by the harvest)
Extra: 0
Repeal gaps: 0 for documents we hold. The repealing document (TPPVFS 2024:1) is in our corpus and its upphaver list correctly names both missing targets, but the targets themselves are absent (harvest gap, not a parse gap)
Title defects: 0 of 4 checked — all titles are the site's own link text verbatim (including the site's own quirks, e.g. the stray space in "TRMFS 2017:4 )")
Consolidations: site no, we hold 0 — consistent
Inherited: trmfs — 3 of 5 site documents held (2017:1, 2017:4, 2017:5, all correctly under the trmfs slug, not renumbered into tppvfs); 2017:2 and 2017:3 missing (see above)
Issue: https://github.com/staffanm/ferenda/issues/96

## Evidence

### Catalog counts
```
sqlite3.connect('file:site/data/catalog.sqlite?mode=ro', uri=True)
select ... from documents where kind='tppvfs'  -> 1 row: TPPVFS 2024:1
select ... from documents where kind='trmfs'   -> 3 rows: TRMFS 2017:1, 2017:4, 2017:5
```

### Entry page fetch
```
request(session, 'GET', 'https://www.pliktverket.se/om-myndigheten/vart-uppdrag/lag-och-ratt/foreskrifter')
-> 200, 65116 bytes
soup.select('a[href*="/download/"][href$=".pdf"]') -> 4 links:
  TRMFS 2017:1  (trmfs-2017_1.pdf)
  TPPVFS 2024:1 (TPPV Föreskrifter om förmåner_slutlig.pdf)
  TRMFS 2017:4  (trmfs-2017_4.pdf)
  TRMFS 2017:5  (trmfs-2017_5.pdf)
```
These 4 are exactly what we hold. The harvest DOES reach the samling; the low
count (1 under the tppvfs slug) is correct because the agency itself has only
issued one TPPVFS regulation so far — the rest of the page's documents are the
predecessor TRMFS, correctly split off by `fs_from_designation`
(ferenda/foreskrift/agencies.py:1502-1513).

### The missed archive page
The entry page's menu carries a link "Upphävda föreskrifter" ->
`/om-myndigheten/vart-uppdrag/lag-och-ratt/foreskrifter/upphavda-foreskrifter`.
`TPPVFS.index_url` never points here and `last_designation_enumerate` never
follows menu links, so this page is never visited.

```
request(session, 'GET', 'https://www.pliktverket.se/om-myndigheten/vart-uppdrag/lag-och-ratt/foreskrifter/upphavda-foreskrifter')
-> 200, 64808 bytes
soup.select('a[href*="/download/"][href$=".pdf"]') -> 2 links:
  'Totalförsvarets rekryteringsmyndighets föreskrifter och allmänna råd om
   förmåner till totalförsvarspliktiga under tjänstgöring'
   -> /download/.../UPPHÄVD_TRMFS 2017_2.pdf
  'Totalförsvarets rekryteringsmyndighets föreskrifter och allmänna råd om
   förmåner till totalförsvarspliktiga under mönstring eller annan utredning...'
   -> /download/.../UPPHÄVD_TRMFS 2017_3.pdf
```
The designation appears only in the filename, not the link text — a second,
independent reason `last_designation_enumerate` would not have picked these
up even if it visited this page, since it runs `RE_FS_NUMBER` against the
visible text, which carries no "TRMFS 2017:N" string here at all.

Neither TRMFS 2017:2 nor 2017:3 is in `documents`:
```
select uri from documents where uri like '%trmfs/2017:2%' or '%trmfs/2017:3%'
-> []
```

### The repeal is already recorded on the repealing side
```
read_text('site/data/artifact/foreskrift/tppvfs/2024-1.json') -> metadata.upphaver:
  ["https://lagen.nu/trmfs/2017:2", "https://lagen.nu/trmfs/2017:3"]
```
and the catalog's `links` table carries matching `rpubl:upphaver` rows from
`tppvfs/2024:1` to both targets. So the parser correctly read the repeal
notice inside TPPVFS 2024:1 (which names TRMFS 2017:2 and 2017:3 as
superseded); the gap is purely that those two target documents were never
downloaded, because the harvest's index list never reaches the archive page
that hosts them. This matches the recurring "upphävda föreskrifter archive
page not in the index list" pattern already seen at AFS (#45) and EIFS (#51).

### Titles (4 checked, 0 defects)
All four held titles reproduce the site's own link text verbatim, including
the site's own formatting quirks (e.g. the site itself prints
"(TRMFS 2017:4 )" with a stray space, and "verket(TRMFS 2017:5)" with no
space) — not something we introduced.

### Consolidations
No `files.consolidation` entries in any of the 4 download records, and the
site publishes only single PDFs (no "konsoliderad version" links found on
either page) — consistent, no defect.

### Pagination
No pagination controls found on the entry page
(`soup.select('.pagination, .sol-pagination')` -> `[]`); both pages read in
full.

### Requests used
6 HTTP requests total (well under the 60 budget): 1 entry page, 1 archive
page, plus earlier interactive checks.
