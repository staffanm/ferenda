# hslffs-ivo — HSLF-FS, Inspektionen för vård och omsorg
Verdict: OK
Site list: 10 designations from 1 page (https://www.ivo.se/aktuellt/publikationer/foreskrifter/)
We hold: 10 documents (hslffs), newest HSLF-FS 2026:8; site newest HSLF-FS 2026:8
Missing: 0
Extra: 0
Repeal gaps: 0 — no repealed/struck-through document on the site
Title defects: 0 — all 10 catalog titles match the site's link text exactly (minus the "(pdf)" suffix and leading designation)
Consolidations: site yes (1, HSLF-FS 2023:7), we hold 1
Inherited: none — IVO publishes only into hslffs, no predecessor samling of its own
Issue: none

## Evidence

### Enumerate (check 1)
Fetched the single index page with `ferenda.lib.net.request` (BROWSER_UA session, one GET):

    r = request(session, "GET", "https://www.ivo.se/aktuellt/publikationer/foreskrifter/")

`ferenda/foreskrift/agencies.py:2598` (`HSLFFS_IVO`) uses
`enumerate_files` with `link_select = 'a[href*="/publikationer/foreskrifter/"][href$=".pdf"]'`
and no `unit`/`bare_numbers` param — matches `ferenda/foreskrift/hslffs.py:607-642`.

The page carries 12 matching anchors:
- 10 name an FS number and designation (`HSLF-FS 2026:8` … `HSLF-FS 2017:41`)
- 1 is "Konsoliderad version av HSLF-FS 2023:7" (attaches to 2023:7, not a number of its own)
- 1 is "Förteckning av IVO:s föreskrifter (pdf)" — a förteckning, not a författning, `numbered()`
  finds no FS number in it and `enumerate_files` skips it (`hslffs.py:630-632`)

10 + 1 (konsoliderad) + 1 (förteckning) = 12. Site designations:
2026:8, 2026:7, 2026:6, 2025:53, 2024:20, 2023:16, 2023:7, 2023:5, 2022:46, 2017:41.

### We hold (check 2, 3, 8)

    sqlite3.connect("file:site/data/catalog.sqlite?mode=ro", uri=True)
    select uri,label,title,date,publisher,source_url,path,expired,upphavande
    from documents where kind='hslffs' and publisher like '%vård och omsorg%'

Returns exactly the same 10 basefiles, all `publisher = "Inspektionen för vård och omsorg"`,
all `source_url = "https://www.ivo.se/aktuellt/publikationer/foreskrifter/"`. Missing = 0,
Extra = 0. Newest on both sides: HSLF-FS 2026:8 (dated 2026-03-25).

### Repeal marking (check 4)
`grep -i "upphäv\|upphört att gälla\|strike" ivo_index.html` — no hits. IVO's page shows no
document as repealed, so there is nothing to check an `upphaver` target against.

### Titles (check 5)
Checked all 10. Example:

| designation | site link text (minus "(pdf)") | our `documents.title` |
|---|---|---|
| HSLF-FS 2017:41 | Inspektionen för vård och omsorgs föreskrifter (HSLF-FS 2017:41) om anmälan av händelser som har medfört eller hade kunnat medföra en allvarlig vårdskada (lex Maria) | identical |
| HSLF-FS 2023:7 | Inspektionen för vård och omsorgs föreskrifter om anmälan av verksamhet enligt patientsäkerhetslagen | identical |
| HSLF-FS 2026:6 | Föreskrifter om ansökan om tillstånd för att bedriva tandvårdsverksamhet | identical |

No junk: no file sizes, no truncation, no PDF filenames, no HTML entities.

### Consolidations (check 6)
Read each download record (`site/data/downloaded/foreskrift/hslffs/hslffs-<bf>.json.br`
via `ferenda.lib.compress.read_text`) for `files.consolidation`. Only `2023-7` carries one
(`len == 1`); the other 9 carry zero. The site publishes exactly one konsoliderad text
(the "Konsoliderad version av HSLF-FS 2023:7" anchor) and we hold exactly that one. Match.

### Inherited series (check 7)
`ferenda/foreskrift/hslffs.py:1-18` and `agencies.py:2598-2608`: IVO's `params["samlingar"]`
is `{"hslffs": "HSLF-FS"}` only — it publishes into no predecessor samling. Nothing to check.

### Cross-agency collision (per the #55/#59 finding)
Queried the catalog for each of IVO's 10 numbers (`uri = 'https://lagen.nu/hslffs/<designation>'`):
every row's `publisher` is `Inspektionen för vård och omsorg`, matching IVO's own listing —
no number is claimed by, or resolves to, a different agency's document. `uri` is IVO's own
year:löpnummer, so a collision would show up as a wrong publisher on one of these 10 rows;
none did.

### #76 cross-check
`gh issue view 76` lists three hslffs entries with a pdftotext/designation mismatch:
`hslffs/2020:81`, `hslffs/2021:53`, `hslffs/2023:25`. None of these three numbers is one of
IVO's 10 designations, so #76 does not touch this scope.

### Budget
7 HTTP requests total (1 index page + a few retries under the hood); well under the 60-request cap.
