# AAFS — ÅFS, Åklagarmyndigheten
Verdict: OK
Site list: 213 designations from 1 page (https://www.aklagare.se/om-oss/dokument/forfattningssamling)
We hold: 213 documents (aafs), newest ÅFS 2026:2; site newest ÅFS 2026:2
Missing: 0
Extra: 0
Repeal gaps: 0 — all 19 site-marked "Upphävd:" documents have a matching rpubl:upphaver target in our links table
Title defects: 0 — 15 sampled titles match the site's text exactly (checked full text, not truncated)
Consolidations: site yes (a separate "konsoliderade" asset folder exists, not linked from this listing page), we hold 0 — matches the agencies.py code comment: "Consolidated in-force texts live on a separate page and are not harvested." Known, deliberate, not a new defect.
Inherited: raafs (RÅFS) — site lists 0 RÅFS entries on this page (all 213 items are ÅFS); we hold 0. Matches.
Issue: none

## Evidence

Entry page fetch (1 HTTP GET via ferenda.lib.net.request, robots.txt-paced):

    .venv/bin/python -c "
    from ferenda.lib.net import make_session, request, BROWSER_UA
    session = make_session(BROWSER_UA)
    r = request(session, 'GET', 'https://www.aklagare.se/om-oss/dokument/forfattningssamling')
    "
    -> 200, saved to scratchpad/fs-audit/aafs.html

The page is a single Sitevision listing (no pagination), server-rendering every
item. It shows "Alla (214)" total items: 213 numbered ÅFS/RÅFS entries plus one
"Regelförteckning20260303" index PDF that carries no ÅFS/RÅFS designation and
is correctly excluded by `aafs_enumerate`'s `RE_AAFS_DESIG` regex
(ferenda/foreskrift/agencies.py:821-844). Parsing the page with the same regex
gives exactly 213 designations, all "ÅFS", none "RÅFS".

Enumeration and diff against the catalog:

    site = {designation extracted per aafs_enumerate's own regex} -> 213, all ÅFS
    ours = SELECT label FROM documents WHERE kind='aafs'                -> 213
    site - ours -> [] (0 missing)
    ours - site -> [] (0 extra)

Repeal check: the site prints "Upphävd: <date>" inline on 19 of the 213 entries
(no strike-through markup; this listing uses a metadata line instead). Example
raw text: "ÅFS 2022:01 ... Upphävd: 2 januari 2024 ...". Extracted all 19:

    ÅFS 2005:24, 2010:2, 2011:1, 2011:7, 2012:5, 2013:5, 2014:4, 2014:9,
    2014:10, 2015:4, 2015:5, 2016:6, 2017:5, 2017:6, 2018:4, 2019:7,
    2020:4, 2021:5, 2022:1

Checked each against `ferenda.lib.catalog.upphaver_targets(con)`:

    .venv/bin/python -c "
    from ferenda.lib.catalog import upphaver_targets
    ... upphaver_targets(con) ..."

All 19 target URIs (https://lagen.nu/aafs/<year>:<lop>) are present in the
returned set, i.e. some document we hold carries an `rpubl:upphaver` link to
each of them. No repeal gap.

Title sample (Python `random.seed(1)`, 15 of 213 labels), site `<p>` text
compared verbatim against `documents.title`:

    ÅFS 2006:12, 2017:7, 2025:2, 2023:6, 2005:24, 2008:4, 2005:9, 2015:5,
    2023:5, 2014:3, 2014:8, 2019:8, 2013:1, 2024:5, 2007:17

All 15 matched character for character, including odd multi-space runs inside
titles (e.g. "föreskrifter       (ÅFS 2005:2)" in ÅFS 2019:8/2019:9) — these
are the agency's own formatting, correctly preserved, not junk.

Consolidations: download records under
`site/data/downloaded/foreskrift/aafs/*.json.br` (213 files) were checked for
`files.consolidation`:

    .venv/bin/python -c "
    from ferenda.lib.compress import read_text
    import glob, json
    ... files.get('consolidation') ..."

0 of 213 carry a consolidation file. The PDF URLs on the listing page sit under
`/globalassets/dokument/afs-forfattningssamling-alla-utom-konsoliderade/...`
("all except consolidated") — the folder name implies a sibling
"konsoliderade" collection, but no link to it appears anywhere on the entry
page or in `sitemap.xml` (fetched, checked, 0 hits for "konsoliderad"). This
matches the existing code comment in agencies.py (line 818): "Consolidated
in-force texts live on a separate page and are not harvested." Treated as an
existing, deliberate design decision, not a new finding.

Freshness: site newest is ÅFS 2026:2 (Publicerad-less, matches
"afs-20262.pdf"); our newest by (year, lop) sort is also ÅFS 2026:2. No gap.

Inherited series (raafs / RÅFS): the RE_AAFS_DESIG regex matches both "ÅFS"
and "RÅFS" prefixes; 0 of the 213 items on the current listing page match
"RÅFS". We hold 0 raafs documents, consistent with the site showing none. No
predecessor-series renumbering to check for.

HTTP budget used: 2 requests (entry page, sitemap.xml), well under the 60 cap.
No blocks encountered.
