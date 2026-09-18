# BFNAR — allmänna råd, Bokföringsnämnden
Verdict: DEFECT
Site list: 73 designations from 1 page (https://www.bfn.se/redovisningsregler/allmanna-rad/)
We hold: 73 documents (bfnar), newest BFNAR 2026:1; site newest BFNAR 2026:1
Missing: 0
Extra: 0
Repeal gaps: 8 — every `documents.upphavande=1` document (2006:19, 2009:4, 2013:5, 2016:3, 2017:1, 2017:4, 2021:6, 2024:2) has `metadata.upphaver == []`, though its title names the repeal target as `(BFNAR YYYY:N)`.
Title defects: 0 — all 73 catalog titles match the site's link text exactly.
Consolidations: site yes (8: 2002:1, 2002:2, 2002:3, 2002:12, 2004:2, 2006:11, 2012:3, 2012:4), we hold 8 — matches.
Inherited: none (scope lists no predecessor samling).
Issue: https://github.com/staffanm/ferenda/issues/41

## Evidence

### 1. Enumerate
Ran `ferenda.foreskrift.agencies.bfnar_enumerate` (the same function the
corpus build uses) against the live entry page:

    .venv/bin/python -c "
    from ferenda.foreskrift.agencies import bfnar_enumerate, BFNAR
    import requests
    refs = list(bfnar_enumerate(requests.Session(), BFNAR))
    print(len(refs))"
    -> 73

One flat WordPress list, no pagination, 81 `<a>` tags matching
`a[href*="bfnar"][href$=".pdf"]` collapse to 73 distinct designations
(grund + `kons` files grouped per designation, matching the
`consolidations` count below).

### 2/3. Missing / extra
    sqlite3 catalog: select count(*) from documents where kind='bfnar' -> 73
Diffed the 73 site designations against the 73 catalog labels: identical
sets, 0 missing, 0 extra.

### 4. Repeal marking
    sqlite3: select uri from documents where kind='bfnar' and upphavande=1
    -> 8 rows (2006:19, 2009:4, 2013:5, 2016:3, 2017:1, 2017:4, 2021:6, 2024:2)

    for each: select * from links where from_uri=<uri> and predicate like '%upphaver%'
    -> 0 rows for all 8

Read the artifacts directly: all 8 have `metadata.upphaver == []`, e.g.
BFNAR 2006:19's title is "... upphävande av ... (BFNAR 2004:3)" but
`upphaver` is `[]`. Widened the check to every "ändring"-titled document
(the `andrar` field, same extraction path): 48 of 73 documents have a
title of the form "Ändring i ... (BFNAR YYYY:N) om ...", and every one
of the 48 has `metadata.andrar == []`. The catalog `links` table has zero
rows with predicate `rpubl:upphaver` for kind=bfnar, and no andrar-style
predicate rows either — only `dcterms:references` and
`rpubl:bemyndigande` appear.

Root cause read from `ferenda/foreskrift/parse.py`: `RE_FS_REF` (line
284) only matches a designation ending in "FS" or "FA"
(`[A-ZÅÄÖ]+(?:-| )?(?:FS|FA)`); "BFNAR" ends in "AR" and never matches.
`RE_BARE_OWN_REF` (line 311) matches a bare `(YYYY:N)` right after a
trigger word with no other characters in between; BFNAR titles always
write `(BFNAR YYYY:N)`, so the designation itself blocks that match too.
Both `andrar` and `upphaver` are built from these two regexes in
`extract_metadata` (lines 762-786), so both come out empty for every
bfnar document, 100% reproducible. `bemyndigande` is unaffected (it uses
a separate citation parser and is non-empty where declared, e.g. BFNAR
2016:9).

### 5. Titles
    Compared catalog.documents.title against the site's own <a> link text
    for all 73 designations (bfnar_enumerate title = a.get_text()).
    0 diffs.

### 6. Consolidations
    bfnar_enumerate: 8 designations carry a `kons`-suffixed PDF on the
    live site (2002:1, 2002:2, 2002:3, 2002:12, 2004:2, 2006:11, 2012:3,
    2012:4).
    Our download records: grep files.consolidation across
    site/data/downloaded/foreskrift/bfnar/*.json.br -> same 8, none
    others. Match.

### 7. Inherited
Assignment lists no predecessor samling for bfnar. Nothing to check.

### 8. Freshness
Site's newest link is BFNAR 2026:1 (top of the list); our newest
catalog row is also BFNAR 2026:1. Match.

## Filed issue
https://github.com/staffanm/ferenda/issues/41 — "foreskrift: bfnar
amendment and repeal links are always empty". Covers the repeal-gap
and the (broader) andrar-gap defect, with the root-cause regex read and
a suggested fix direction (a designation-aware match instead of the
FS/FA suffix heuristic). Did not open a pull request, did not attach a
patch.
