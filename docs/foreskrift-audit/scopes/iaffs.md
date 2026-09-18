# IAFFS — Inspektionen för arbetslöshetsförsäkringen

Verdict: DEFECT
Site list: 7 designations from 1 page (in-force list, https://www.iaf.se/lag-ratt/foreskrifter/) plus 66 more designations on the archive page (https://www.iaf.se/lag-ratt/foreskrifter/upphavda-foreskrifter/) that the harvest never visits.
We hold: 7 documents (iaffs), newest IAFFS 2025:8; site newest IAFFS 2025:8 (both agree)
Missing: 66 — IAFFS 2004:1, 2004:2, 2004:3, 2004:4, 2005:1, 2005:3, 2005:4, 2005:6, 2005:7, 2006:1 (and 56 more, 2006–2025:2, see evidence)
Extra: 0
Repeal gaps: 5 — every repealing document we hold points at a target that is missing from the corpus (harvest defect, same root cause as Missing): 2025:3→2018:3, 2025:4→2018:2, 2025:5→2024:1, 2025:6→2023:1, 2025:8→2015:1
Title defects: 0 — the 7 titles we hold match the site's own anchor/heading text exactly, including the site's own trailing semicolon on 2025:4
Consolidations: site yes (2017:5, 2017:2, 2018:1, 2018:2, 2009:8 all show a "Konsoliderad version" link), we hold 1 (iaffs/2017:5) — the only base document we hold that the site also shows a consolidation for; the other four base documents are among the 66 missing, so the gap there is downstream of the harvest defect, not a separate one
Inherited: none (assignment lists no predecessor series for iaffs)
Issue: https://github.com/staffanm/ferenda/issues/57

## Evidence

Catalog query (7 rows):
```
.venv/bin/python -c "
import sqlite3
con = sqlite3.connect('file:site/data/catalog.sqlite?mode=ro', uri=True)
for row in con.execute(\"select label,title,date,upphavande,source_url from documents where kind='iaffs' order by label\"):
    print(row)
"
```
Result: iaffs/2017:5, 2025:3, 2025:4, 2025:5, 2025:6, 2025:7, 2025:8. The three
"upphävande" documents (2025:3, 2025:4, 2025:8) each carry an `rpubl:upphaver`
or `rinfoex:andradAv` link (checked in `links` table) whose target is not in
the `documents` table for kind='iaffs'.

Agency module: `ferenda/foreskrift/agencies.py` lines 1252–1293. `iaf_enumerate`
fetches only `agency.index_url` ("https://www.iaf.se/lag-ratt/foreskrifter/")
and extracts landing-page links matching `RE_IAF_BASE`
(`/foreskrifter/iaffs-(\d{4})(\d+)/?$`). It never visits
`/lag-ratt/foreskrifter/upphavda-foreskrifter/`.

Fetch of the entry page (1 request):
```
r = request(session, 'GET', 'https://www.iaf.se/lag-ratt/foreskrifter/')
```
7 landing-page links found, matching `RE_IAF_BASE` exactly: IAFFS 2017:5,
2025:3, 2025:4, 2025:5, 2025:6, 2025:7, 2025:8 — this is the in-force list
and it is fully represented in our 7 held documents.

Fetch of the archive page (1 request):
```
r = request(session, 'GET', 'https://www.iaf.se/lag-ratt/foreskrifter/upphavda-foreskrifter/')
```
This page (`h1: "Upphävda föreskrifter"`) lists direct PDF links (not landing
pages) for 66 distinct designations, extracted by regex `IAFFS\s*(\d{4})[:.](\d+)`
over the anchor text:

2004:1, 2004:2, 2004:3, 2004:4, 2005:1, 2005:3, 2005:4, 2005:6, 2005:7,
2006:1, 2006:3, 2006:5, 2006:6, 2006:7, 2006:8, 2007:1, 2007:3, 2008:1,
2008:2, 2008:3, 2009:1, 2009:2, 2009:3, 2009:5, 2009:8, 2010:3, 2010:4,
2010:5, 2010:8, 2010:9, 2011:3, 2011:4, 2012:1, 2012:2, 2013:1, 2014:1,
2014:2, 2014:3, 2014:4, 2014:5, 2014:6, 2014:7, 2015:1, 2015:2, 2015:3,
2015:4, 2015:5, 2015:6, 2016:1, 2016:3, 2016:4, 2017:2, 2017:3, 2017:4,
2017:6, 2018:1, 2018:2, 2018:3, 2019:1, 2020:2, 2021:1, 2021:2, 2023:1,
2024:1, 2025:1, 2025:2

None of these 66 overlap with the 7 designations we hold. Every one of the
five repeal-relation targets from check 4 (2018:3, 2018:2, 2024:1, 2023:1,
2015:1) is in this list, confirming the repeal gaps are downstream of the
same single harvest defect, not independent parse bugs.

Fetch of `/lag-ratt/foreskrifter/forfattningsregister/` (1 request) returned
only static register text and links to yearly PDF registers, no additional
designations by anchor text. No extra fetches beyond these 3 requests were
needed; well under the 60-request budget.

This matches the recurring pattern already named in the briefing for AFS
(#45) and EIFS (#51): an "upphävda föreskrifter" archive page that the
harvest's index URL list does not visit.

Duplicate check: `gh issue list --repo staffanm/ferenda --state open --search
"foreskrift iaffs"` and `--search "iaffs"` (all states) returned no results
before filing.

Checks passed: 1 (enumerate — done, single page + archive), 3 (no extra
documents), 5 (no title defects), 7 (n/a, no inherited series), 8 (freshness
matches: both site and corpus newest is IAFFS 2025:8).
Checks failed: 2 (missing documents), 4 (repeal gaps, same root cause), 6
(consolidations only for the one base document we do hold).
