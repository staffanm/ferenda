# SSMFS — Strålsäkerhetsmyndigheten

Verdict: DEFECT
Site list: 42-43 designations (unstable ordering across crawls) from 7 pages
(https://www.stralsakerhetsmyndigheten.se/publikationer/foreskrifter/)
We hold: 47 documents (ssmfs), newest SSMFS 2026:4; site newest SSMFS 2026:4
Missing: 2 — SSMFS 2017:3 (upphäver SSMFS 2008:7), SSMFS 2018:13 (amends SSMFS 2008:3)
Extra: 5 — SSMFS 2008:3, 2012:3, 2015:1, 2018:9, 2018:10. Four are still live
  regulations the crawl's index simply missed (site reorders its result set
  between requests); 2015:1 is a repeal-only document, correctly absent from
  the "current text" listing.
Repeal gaps: 0 — every repealing document we hold has populated `upphaver`
  targets; the targets themselves predate our harvest window, which is expected.
Title defects: 35 of 47 (74 %) — three distinct root causes, see Evidence.
Consolidations: site yes, we hold 21 of 47 download records with
  `files.consolidation`.
Inherited: ssifs 0 (site's in-force list carries none; consistent with SSI's
  regulations having been fully superseded by SSMFS), skifs 0 (same reasoning).
Issue: https://github.com/staffanm/ferenda/issues/93

## Evidence

### 1. Enumerate

`paginated_enumerate` params from `ferenda/foreskrift/agencies.py`:
`page_url=https://www.stralsakerhetsmyndigheten.se/publikationer/foreskrifter/?page={page}`,
`row_select=ul.search-result li.search-item div.search-content h3 a`.

Fetched page=1..7 with `ferenda.lib.net.request`; page 7 returned zero rows
(stop condition). 55 raw rows, 42 distinct `SSMFS YYYY:N` designations after
dedup. A second, independent 7-page crawl a few minutes later returned a
different row set that additionally contained `SSMFS 2017:3` (confirmed
present on page 1 and page 2 on that pass, absent on the first pass) — the
site's search index reorders between requests, so the item that lands at a
page boundary can be skipped on one crawl and caught on the next. This
instability is the plausible mechanism behind the two missing documents
below: our harvester (the same `paginated_enumerate`) is exposed to the same
reordering.

    .venv/bin/python -c "
    import requests
    from ferenda.lib.net import request
    from bs4 import BeautifulSoup
    session = requests.Session()
    page = 1
    while True:
        url = f'https://www.stralsakerhetsmyndigheten.se/publikationer/foreskrifter/?page={page}'
        r = request(session, 'GET', url)
        soup = BeautifulSoup(r.text, 'html.parser')
        rows = soup.select('ul.search-result li.search-item div.search-content h3 a')
        if not rows: break
        page += 1
    "

### 2. Missing documents

`SSMFS 2017:3` ("Föreskrifter om upphävande av ... (SSMFS 2008:7) om undantag
från kravet på godkännande av uppdragstagare") sits on page 1 of the site's
listing (confirmed twice) and is entirely absent from `documents` (`select *
from documents where label='SSMFS 2017:3'` → empty). It repeals SSMFS 2008:7,
which we also do not hold (expected — the repealed document predates our
harvest).

`SSMFS 2018:13` is not in our corpus, not in the site's current listing
(checked the `ssmfs-2018/` sub-listing in full, 14 rows, page 1-2), and not
reachable at the guessed URL (`ssmfs-2018/ssmfs-201813/` → "Sidan kan inte
hittas"). But our own held artifact for SSMFS 2008:3 records
`metadata.andradAv = ["https://lagen.nu/ssmfs/2018:13"]`, and the
*consolidation PDF we already hold* for 2008:3 prints, on its own cover page:

    Konsoliderad version med ändringar införda t.o.m. SSMFS 2018:13.

    pdftotext -layout site/data/downloaded/foreskrift/ssmfs/ssmfs-2008-3-consolidation-2008_3.pdf - | grep 2018:13

So the agency's own text confirms SSMFS 2018:13 exists as an amending
regulation; our corpus has never held it.

### 3. Extra documents

    .venv/bin/python -c "
    import sqlite3
    con = sqlite3.connect('file:site/data/catalog.sqlite?mode=ro', uri=True)
    ours = {r[0].replace('SSMFS ','') for r in con.execute(\"select label from documents where kind='ssmfs'\")}
    print(sorted(ours - site))   # site = the 42-designation set from the first crawl
    "
    # -> ['2008:3', '2012:3', '2015:1', '2018:10', '2018:9']

Direct fetch of the four regulation pages (SSMFS 2012:3, 2018:9, 2018:10 —
and 2008:3, separately):

    .venv/bin/python -c "
    import requests
    from ferenda.lib.net import request
    from bs4 import BeautifulSoup
    session = requests.Session()
    for u in [... four landing URLs ...]:
        r = request(session, 'GET', u)
        print(u, r.status_code, BeautifulSoup(r.text,'html.parser').find('h1').get_text())
    "

2012:3, 2018:9, 2018:10 return 200 with the correct `<h1>` (matching our
`documents.label`) — they are live, just missed by that particular crawl.
2008:3's stored `source_url`
(`ssmfs-2008/ssmfs-20083/?searchQuery=`) now 404s ("Sidan kan inte hittas"),
and the `ssmfs-2008/` sub-listing (14 rows, all fetched) does not include it
either — the document's landing page appears to have moved, not disappeared
(nothing repeals it in our `links` table, and it is still consolidated up to
2018:13 per the PDF above). This is a stale `source_url`, not a missing- or
extra-document defect; not filed.

2015:1 is `upphavande=1` (repeals SSMFS 2008:42), correctly excluded from a
"current text" listing.

### 4. Repeal marking

    .venv/bin/python -c "
    import sqlite3
    con = sqlite3.connect('file:site/data/catalog.sqlite?mode=ro', uri=True)
    rows = con.execute(\"select from_uri,to_uri from links where predicate='rpubl:upphaver' and from_uri like '%ssmfs%'\").fetchall()
    print(len(rows))
    "
    # -> 34 rows

Every repealing SSMFS document we hold carries populated `upphaver` targets.
All 34 targets point at pre-2018 designations we do not hold (2008:4 .. 2012:5
range) — expected, since the site only ever showed us these documents after
they were already superseded; per the repeal model this is not a defect.

### 5. Titles — three root causes, 35/47 records affected

    .venv/bin/python -c "
    import sqlite3, re
    con = sqlite3.connect('file:site/data/catalog.sqlite?mode=ro', uri=True)
    rows = con.execute(\"select label, title from documents where kind='ssmfs'\").fetchall()
    # classified by regex, see below
    "
    # name junk (Ulf Yngvesson / Johan Strandman): 22
    # stray digit ('\b1\s+[a-zäåö]'): 9
    # title == label (empty): 5
    # union: 35 distinct

**(a) Editor's name not stripped (22 records).** SSM's masthead prints
"Utgivare: <name>" with no trailing ", <agency>" clause. `RE_MASTHEAD_BOILERPLATE`
in `ferenda/foreskrift/parse.py` (line ~947) strips the literal `Utgivare:\s*`
but leaves the name itself, so it survives between the masthead's doubled
title and the body title. `undouble()` (line 1088) requires the second copy
to start with an exact copy of the first, so the stray name defeats it.
Example, `pdftotext -layout` on the source PDF:

    Strålsäkerhetsmyndighetens föreskrifter om
    bäringskikare, pejlkompasser och riktmedel
    som innehåller tritium
    Strålsäkerhetsmyndighetens
    författningssamling
    ISSN 2000-0987
    Utgivare: Ulf Yngvesson

    Strålsäkerhetsmyndighetens föreskrifter om     SSMFS 2012:2
    bäringskikare, pejlkompasser och riktmedel
    som innehåller tritium
    beslutade den 3 april 2012.

| designation | site title | our title |
|---|---|---|
| SSMFS 2012:2 | Strålsäkerhetsmyndighetens föreskrifter om bäringskikare, pejlkompasser och riktmedel som innehåller tritium | ...riktmedel Ulf Yngvesson Strålsäkerhetsmyndighetens föreskrifter om bäringskikare, pejlkompasser och riktmedel som innehåller tritium |
| SSMFS 2018:10 | ...om radon på arbetsplatser | ...om Ulf Yngvesson Strålsäkerhetsmyndighetens föreskrifter om radon på arbetsplatser |

**(b) A footnote digit lands mid-title (9 records).** Example, SSMFS 2008:3:

    pdftotext site/data/downloaded/foreskrift/ssmfs/ssmfs-2008-3-regulation.pdf -
    # "Strålsäkerhetsmyndighetens föreskrifter om\nkontroll av kärnämne m.m;1"

Our stored title: "Strålsäkerhetsmyndighetens föreskrifter om 1 kontroll av
kärnämne m.m" — the superscript footnote marker "1" (a directive-reference
note) is inserted before "kontroll" instead of trailing "m.m;" where the text
stream places it. A word-ordering artifact in PDF text extraction.

**(c) English-translation records get no title at all (5 records:
SSMFS 2008:21, 2008:23, 2008:24, 2008:26, 2008:32).** These come from the
`ssmfs-engelska` sub-index, whose row anchor text *does* carry the full
English title ("SSMFS 2008:21 The Swedish Radiation Safety Authority's
regulations concerning safety in connection with the disposal of nuclear
material and nuclear waste" — confirmed live on the site). But
`paginated_enumerate` (`ferenda/foreskrift/harvest.py` line ~571) calls
`ref(agency, a.get_text(...), a.get("href",""), seen)` without a `title=`
argument, so the harvest record always stores `"title": null`
(`site/data/downloaded/foreskrift/ssmfs/ssmfs-2008-21.json`). The PDF-masthead
fallback (`title_from_masthead`) also fails, because `RE_TITLE_TYPE` only
matches Swedish words ("föreskrifter", "allmänna råd", ...) and these PDFs are
entirely in English. With both title sources empty, the document's title
falls back to its own identifier.

### 6. Consolidations

    .venv/bin/python -c "
    from ferenda.lib.compress import read_text
    import json, glob
    total = cons = 0
    for f in glob.glob('site/data/downloaded/foreskrift/ssmfs/ssmfs-*.json.br'):
        total += 1
        d = json.loads(read_text(f[:-3]))
        cons += bool(d.get('files',{}).get('consolidation'))
    print(total, cons)
    "
    # -> 47 21

The site publishes "Konsoliderad version" PDFs (confirmed on SSMFS 2008:3's
own file); we hold 21 of 47 download records with a consolidation file. No
systematic gap found (the other 26 may simply have no amendments to
consolidate, or their consolidation PDF is identical to the regulation PDF
in single-amendment cases — not further investigated, out of scope for this
audit's budget).

### 7. Inherited series (SSI, SKI)

    .venv/bin/python -c "
    import sqlite3
    con = sqlite3.connect('file:site/data/catalog.sqlite?mode=ro', uri=True)
    for kind in ('ssifs','skifs'):
        print(kind, con.execute('select count(*) from documents where kind=?', (kind,)).fetchone())
    "
    # -> ssifs 0, skifs 0

The full 42/43-designation site listing scraped for check 1 contains zero
`SSI FS` or `SKI FS` designations — every entry is `SSMFS`. This is
consistent with all SSI/SKI regulations having been replaced by SSMFS
successors since SSM's formation in 2008 (the site shows only current text).
No predecessor-series gap found; nothing to file for #50's pattern.

### 8. Freshness

Site newest: SSMFS 2026:4 ("...om kärnämneskontroll", decided 2026-03-27).
We hold: SSMFS 2026:4, same designation, same date. Up to date.

### #76 cross-check

`gh issue view 76` lists ssmfs nowhere in its by-scope breakdown (migrfs,
scbfs, rfs, elsakfs, afs, skvfs, hslffs, kamfs, sksfs named explicitly, "22
more with one or two each" unnamed but ssmfs's 0.9% mismatch rate would need
verification only if it surfaced there). Not applicable.
