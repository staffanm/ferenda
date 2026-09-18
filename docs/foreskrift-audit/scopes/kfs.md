# KFS — Kommerskollegiums författningssamling, Kommerskollegium

Verdict: OK
Site list: 4 designations from 1 page (https://www.kommerskollegium.se/uppdrag/forfattningssamling/)
We hold: 4 documents (kfs), newest KFS 2020:1; site newest KFS 2020:1
Missing: 0
Extra: 0
Repeal gaps: 0 (site shows no repealed/struck-through entries)
Title defects: 0 — all 4 titles match the site's link text exactly
Consolidations: site no, we hold 0
Inherited: none (no predecessor series assigned)
Issue: none

## Evidence

The entry page is a single static page. It lists exactly 4 PDF links matching
`a[href$=".pdf"][href*="kfs"]`, the same selector the harvester
(`ferenda/foreskrift/agencies.py` KFS agency, indexed_enumerate + resolve_direct)
uses. No pagination, no "upphävda föreskrifter" archive link, and no mention of
"upphäv", "upphör", "historik", "arkiv", or "konsoliderad" anywhere in the page
text.

Site list (fetched via `ferenda.lib.net.request`, one GET,
`kfs_index.html` saved to scratchpad):

| designation | PDF | title |
|---|---|---|
| KFS 2020:1 | /globalassets/foreskrifter-vara/kfs120.pdf | Kommerskollegiums föreskrifter (2020:1) om tekniska regler |
| KFS 2017:1 | /globalassets/foreskrifter-vara/forfattningssamling-kfs-2017-1.pdf | Kommerskollegiums föreskrifter om medling mellan betalningsförmedlare och avgiftsupptagare |
| KFS 1996:3 | /globalassets/foreskrifter-vara/forfattningssamling-kfs-1996-3.pdf | Kommerskollegiums föreskrifter om information om nationella åtgärder som avviker från principen om fri rörlighet för varor inom Europeiska gemenskapen |
| KFS 1991:10 | /contentassets/3e2ea3c6f2b7436fb7fca63adaef950a/forfattningssamling-kfs-1991-10.pdf | Kommerskollegiums föreskrifter om auktoriserad handelskammares verksamhet med vissa dokument för handelsändamål |

Catalog query:

```
sqlite3 (via python) select label, title, date, source_url, expired, upphavande
from documents where kind='kfs' order by label
```

Result: exactly the same 4 labels — KFS 1991:10, 1996:3, 2017:1, 2020:1 — with
titles identical to the site's link text. No `expired` or `upphavande` flags
set (none expected — the site marks none as repealed).

Download-record check (`site/data/downloaded/foreskrift/kfs/*.json.br`):
all 4 records carry a `regulation` entry whose `url` matches the exact PDF URL
seen on the live page today, and `consolidation`/`amendment`/`memo`/`attachment`
are all empty lists — consistent with the site publishing no konsoliderade
versions.

Cross-check on the reference graph: the `links` table has ~70 `to_uri` values
under `https://lagen.nu/kfs/*` older than 1991 (e.g. `kfs/1943:11` through
`kfs/1990:36`) cited by other documents (mostly older case law), that we do
not hold as `documents` rows. These are historical KFS numbers with no trace
on the agency's current site (which lists only today's 4 in-force texts, no
archive). This is not a missing-document defect under check 2 — the site
itself does not publish them, so there is nothing to harvest. Not filed.

Inherited series: the briefing lists none for kfs, so check 7 is not
applicable.

All eight checks pass. No corpus defect found; no issue filed.
