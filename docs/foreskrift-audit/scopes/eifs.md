# EIFS — Energimarknadsinspektionens författningssamling, Energimarknadsinspektionen
Verdict: DEFECT
Site list: 36 designations from 3 pages (el/fjärrvärme-och-fjärrkyla/naturgas), plus 32 more designations found only on a 4th sibling page we do not harvest (https://ei.se/om-oss/lagar-och-regler/foreskrifter/upphavda-foreskrifter)
We hold: 67 documents (eifs), newest EIFS 2026:10; site newest EIFS 2026:10 (both agree)
Missing: 32 — EIFS 2008:1, 2009:1, 2009:2, 2010:2..2010:6, 2011:1..2011:5, 2012:1, 2012:2, 2012:5, 2013:2, 2014:1, 2014:3, 2014:4, 2014:7, 2015:7, 2020:2, 2020:3, 2022:1, 2022:2, 2022:7, 2022:8, 2022:9, 2023:1, 2026:2, 2026:9
Extra: 31 — 19 covered by an upphaver row we hold; ~7 are amendments (no upphaver expected by design); EIFS 2019:1/2019:2/2019:4/2019:5 are repealed by EIFS 2022:7/2022:8/2022:9/2023:1, which fall in the Missing set above
Repeal gaps: 2 — EIFS 2012:4 upphaver points at eifs/1995:1 instead of nutfs/1995:1; EIFS 2013:7 upphaver is missing eifs/2011:5 (only holds eifs/2010:2)
Title defects: 0 of 8 checked
Consolidations: site yes (published for select regulations), we hold 4 (EIFS 2012:3, 2015:4, 2023:2, 2024:1)
Inherited: none assigned to this scope; STEMFS/NUTFS predecessor citations mostly route to their own fs slugs correctly, except the EIFS 2012:4 case above
Issue: https://github.com/staffanm/ferenda/issues/51

## Evidence

### 1. Enumerate

Fetched the three sibling index pages the EIFS agency config in
`ferenda/foreskrift/agencies.py` walks:

    https://ei.se/om-oss/lagar-och-regler/foreskrifter/foreskrifter---el                 (26 links)
    https://ei.se/om-oss/lagar-och-regler/foreskrifter/foreskrifter---fjarrvarme-och-fjarrkyla (4 links)
    https://ei.se/om-oss/lagar-och-regler/foreskrifter/foreskrifter---naturgas            (6 links)

36 unique designations total, parsed with
`soup.select('a[href*="/publikationer/foreskrifter-"]')` (the same selector
`agency.params["link_select"]` uses).

Each of the three pages also links a 4th sibling page, "Upphävda
föreskrifter" (`https://ei.se/om-oss/lagar-och-regler/foreskrifter/upphavda-foreskrifter`),
which is not in `EIFS.params["index_urls"]`. That page holds three HTML
tables (el / naturgas / fjärrvärme och fjärrkyla), each row a
(upphävd föreskrift, upphävande föreskrift) pair with a real download link
for both sides. It names 86 distinct EIFS designations. Two spot-checked
PDF links from that page both returned `200 application/pdf`:

    EIFS 2023:1 (347,731 bytes)
    EIFS 2026:9 (85,802 bytes)

### 2. Missing documents

67 designations on the current 36-link listing all already exist in our
catalog (`site/data/catalog.sqlite`, `select label from documents where
kind='eifs'`) — no gap there.

But 32 of the 86 designations named on the "Upphävda föreskrifter" page do
not exist under any `kind` in our catalog at all (checked
`select uri,kind,label from documents where label like 'EIFS <n>'` for each
— zero rows for all 32). These are documents the agency still hosts and
names on its own site, in a stable HTML table, that our harvest never
reaches because `EIFS.params["index_urls"]` omits that 4th page.

### 3. Extra documents

31 of our 67 documents are not linked from the 3 current-listing pages
(they are superseded). Cross-checked against `links` rows with predicate
`rpubl:upphaver` (`to_uri like '%/eifs/%'`):

- 19 are the target of an upphaver row we hold (legitimate — the site
  keeps only in-force text on its topic pages).
- ~7 are ändringsförfattningar (their own artifact carries `andrar`, not
  `upphaver` — expected, an amendment is not itself repealed).
- EIFS 2019:1, 2019:2, 2019:4, 2019:5 are repealed, per the site's own
  "Upphävda föreskrifter" table, by EIFS 2022:7, 2022:8, 2022:9 and 2023:1
  respectively — all four of which are in the Missing set (check 2). We
  cannot mark these repealed because we do not hold the repealing document.

### 4. Repeal marking — two parse defects

**a) EIFS 2012:4 → wrong repeal target.**

    .venv/bin/python3 -c "
    from ferenda.lib.compress import read_text
    import json
    d = json.loads(read_text('site/data/artifact/foreskrift/eifs/2012-4.json'))
    print(d['metadata']['upphaver'])"
    # ['https://lagen.nu/eifs/1995:1']

The document's own text (`structure[-1]`, the ikraftträdande section) says:

    "Genom dessa föreskrifter upphävs Närings- och
    teknikutvecklingsverkets föreskrifter och allmänna råd ( 1995:1 ) om
    redovisning av nätverksamhet (omtryck 1998:1)."

This names Närings- och teknikutvecklingsverket (NUTFS), not
Energimarknadsinspektionen. The correct target is `nutfs/1995:1`, not
`eifs/1995:1` (which does not exist — see check 2, our earliest EIFS
document is 2009:1, and there is no 1995 EIFS designation at all: EIFS was
not created until 2008).

Root cause: `ferenda/foreskrift/data/series.json`'s `"eifs"` entry has no
`"from"` year, and neither `"nutfs"` nor `"stemfs"` declares
`"successor": "eifs"`. `_series_for_year("eifs", 1995)` in
`ferenda/foreskrift/parse.py` therefore returns `"eifs"` unchanged instead
of walking back through the predecessor chain, so a bare (un-prefixed)
predecessor reference such as "(1995:1)" resolves to the citing document's
own series instead of the series that was actually in force in 1995.
Explicit-prefix citations ("STEMFS 2007:4", "NUTFS 1999:1") are unaffected
— they already route correctly to `stemfs`/`nutfs` (see e.g.
`eifs/2010:1 -> stemfs/2007:4`, `eifs/2013:8 -> nutfs/1999:1` in `links`).

**b) EIFS 2013:7 → missing repeal target.**

    upphaver: ['https://lagen.nu/eifs/2010:2']

The document's text says:

    "Genom dessa föreskrifter upphävs Energimarknadsinspektionens
    föreskrifter och allmänna råd (EIFS 2010:2) om ... och
    Energimarknadsinspektionens föreskrifter om ändring av
    Energimarknadsinspektionens föreskrifter och allmänna råd
    (EIFS 2011:5) om ..."

Two regulations are named, joined by "och": EIFS 2010:2 and EIFS 2011:5 (an
amendment to 2010:2). Only 2010:2 made it into `upphaver`. Root cause:
`_repeal_object()` in `ferenda/foreskrift/parse.py` cuts its segment at
`RE_ANDRING` ("ändring i/av") to strip a trailing "om ändring i X" tail off
a single target's own title. Here the SECOND target's own descriptive
title happens to contain "föreskrifter om ändring av …", so the cut lands
mid-sentence and discards EIFS 2011:5 entirely. Confirmed against the raw
PDF text as well as the parsed artifact.

No other eifs document showed the same double-target pattern (checked
every artifact whose text contains "upphäv" and 2+ FS-style references
within 700 characters of it; the only other two candidates, EIFS 2016:2 and
2019:4, were false positives — a single target mentioned twice, or two
already-correctly-captured targets).

EIFS 2016:1's `upphaver` correctly names `eifs/2010:6` even though 2016:1
is itself only an amendment to 2015:1 — this is the documented, intentional
omtryck-reprint handling in `_repeal_object` (a "då … upphör att gälla"
clause reprinted from the base regulation's own entry-into-force sentence),
not a defect.

### 5. Titles

Fetched 8 landing pages live and compared their PDF-link title text against
`documents.title`:

    EIFS 2026:10, 2025:2, 2023:2, 2022:11, 2022:12, 2018:2, 2015:4, 2019:3

All 8 matched exactly (mod the site's own trailing " EIFS <n>:<n>" repeat,
which we correctly do not carry into our title). No truncation, no PDF
filenames, no HTML entities, no missing titles.

### 6. Consolidations

    .venv/bin/python3 -c "
    from ferenda.lib import compress
    from pathlib import Path
    import json
    for p in sorted(compress.glob(Path('site/data/downloaded/foreskrift/eifs'), '*.json')):
        d = json.loads(compress.read_text(str(p)))
        if 'files' in d and d['files'].get('consolidation'):
            print(d['basefile'])"
    # eifs/2012:3, eifs/2015:4, eifs/2023:2, eifs/2024:1

4 of 67 download records carry a consolidation. The site publishes
"Konsoliderad" PDFs for some regulations (confirmed on the "Upphävda
föreskrifter" page and on individual landing pages), not systematically —
we hold every one we saw referenced. Not a defect.

### 7. Inherited series

The assignment lists no inherited series for eifs. In practice EIFS
absorbed STEMFS's (Statens energimyndighet) and NUTFS's (Närings- och
teknikutvecklingsverket) el/naturgas/fjärrvärme regulations. Citations that
name STEMFS/NUTFS explicitly route correctly to their own `stemfs`/`nutfs`
fs slugs (7 such links confirmed, e.g. `eifs/2022:12 -> stemfs/2006:3`).
The one bare (un-prefixed) predecessor citation we hold — EIFS 2012:4's
"(1995:1)" — misroutes to `eifs/1995:1` instead of `nutfs/1995:1` (check 4a
above).

### 8. Freshness

Site's newest linked designation (top of the "el" page): EIFS 2026:10
(2026-08-03). Our newest held EIFS document: EIFS 2026:10 (2026-08-03).
Freshness on the current-listing side matches. The freshness gap that
exists is entirely on the historical/upphävda side (check 2).

### Budget

14 HTTP requests to ei.se: 3 category-page fetches, 1 upphävda-page fetch,
8 landing-page fetches (titles), 2 PDF spot-checks. No JavaScript
rendering was needed; every page fetched fine as plain HTML.
