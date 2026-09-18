# VALFS — Valmyndighetens författningssamling, Valmyndigheten

Verdict: DEFECT
Site list: 17 designations from 1 page (https://www.val.se/det-svenska-valsystemet/grunderna-i-det-svenska-valsystemet/lagar-och-regler)
We hold: 17 documents (valfs), newest VALFS 2026:2; site newest VALFS 2026:2
Missing: 0
Extra: 0
Repeal gaps: 0 (chain 2006:1→2008:1→2012:1/2013:1/2018:1→2019:1→2020:1/2021:1→2022:1→2023:1 all present)
Title defects: 0 (10+ compared; site titles carry only "pdf, N kB/MB" filesize suffix, which our titles correctly drop)
Consolidations: site no, we hold 0 (each PDF is either a grundförfattning or a full-text omtryck; no separate konsoliderad artifact exists on the site)
Inherited: none (assignment lists no predecessor series)
Issue: https://github.com/staffanm/ferenda/issues/107

## Evidence

### 1. Enumerate
Fetched the entry page once (1 request, ferenda.lib.net.request):

    from ferenda.lib.net import request
    import requests
    r = request(requests.Session(), 'GET',
        'https://www.val.se/det-svenska-valsystemet/grunderna-i-det-svenska-valsystemet/lagar-och-regler')

One flat page, no pagination. `a[href*="/download/"]` selector (BeautifulSoup)
found 17 PDF links, each labelled with its own "VALFS YYYY:N" designation.
This matches `ferenda/foreskrift/agencies.py`'s VALFS entry (`indexed_enumerate`
+ `resolve_direct`, `direct=True`).

### 2/3. Missing / extra
Site designations (17): 2006:1, 2006:2, 2008:1, 2008:2, 2009:1, 2012:1,
2013:1, 2018:1, 2019:1, 2020:1, 2021:1, 2022:1, 2023:1, 2024:1, 2025:1,
2026:1, 2026:2.

catalog.sqlite `kind='valfs'` (17 rows) has exactly the same set. No
missing, no extra.

### 4. Repeal marking
The site is a flat archive with no in-force/repealed marker at all (no
"upphävd" text, no strikethrough) — checked via a full-text scan of the
fetched HTML for "upphäv", "konsolider", "gäller"; the only "ändr" hits are
navigation chrome. So check 4 reduces to: does our internal `rpubl:upphaver`
chain hang together?

    select from_uri, predicate, to_uri from links
    where from_uri like 'https://lagen.nu/valfs/%' and predicate='rpubl:upphaver';

    valfs/2008:1 -> valfs/2006:1
    valfs/2012:1 -> valfs/2008:2
    valfs/2013:1 -> valfs/2006:1   <-- spurious, see below
    valfs/2018:1 -> valfs/2013:1
    valfs/2019:1 -> valfs/2018:1
    valfs/2020:1 -> valfs/2012:1
    valfs/2021:1 -> valfs/2019:1
    valfs/2022:1 -> valfs/2021:1
    valfs/2023:1 -> valfs/2022:1

Every link resolves to a document we hold, so no harvest gap. But
`valfs/2013:1 -> valfs/2006:1` is wrong: `valfs/2006:1` was already repealed
by `valfs/2008:1`, and `valfs/2013:1`'s own masthead says it is only an
"ändring i Valmyndighetens föreskrifter (VALFS 2008:1)".

`pdftotext` on `site/data/downloaded/foreskrift/valfs/valfs-2013-1-regulation.pdf`
shows why: the PDF is a full-text omtryck (it restates the whole amended
text of VALFS 2008:1). Its transitional-provisions section retains three
historical "Dessa föreskrifter … träder i kraft …" sentences end to end:

    Dessa föreskrifter träder i kraft den 1 januari 2009, då Valmyndighetens
    föreskrifter (VALFS 2006:1) ska upphöra att gälla.
    Dessa föreskrifter3 träder i kraft den 1 januari 2010.
    Dessa föreskrifter4 träder i kraft den 1 februari 2014

The first sentence (no footnote number) is VALFS 2008:1's own,
already-executed 2009 entry-into-force clause, reprinted verbatim. VALFS
2013:1's own clause is the last one (footnote 4, in force 2014-02-01) and
declares no repeal at all. `extract_metadata` in `ferenda/foreskrift/parse.py`
scans the whole document text for "ska upphöra att gälla" (`RE_SKALL_UPPHORA`
etc.) without excluding a reprinted, already-superseded transitional
sentence, so it credits VALFS 2013:1 with a repeal VALFS 2008:1 already made.
Confirmed by grepping every valfs PDF for "upphöra att gälla": every other
document has exactly one match and it is its own current clause; VALFS
2013:1 is the only one where the sole match is historical.

### 5. Titles
Compared all 17. Ours: `documents.title`. Theirs: the link text on the
entry page, which is the title plus " pdf, N kB/MB" that our extraction
correctly strips. Example:

    site:  "Valmyndighetens författningssamling VALFS 2006:1pdf, 192 kB."
    ours:  "Valmyndighetens författningssamling VALFS 2006:1"

VALFS 2026:2 is titled differently on the site itself ("Valmyndighetens
föreskrifter (VALFS 2026:2)") and our record matches that. No junk found:
no file names, no truncation, no HTML entities.

### 6. Consolidations
No "konsoliderad" text anywhere on the entry page or in its markup. Every
download record's `files.consolidation` is empty (checked all 17
`site/data/downloaded/foreskrift/valfs/valfs-*.json.br`). Matches: neither
side publishes separate consolidated versions.

### 7. Inherited series
None listed for this scope. No predecessor slug to check.

### 8. Freshness
Site's newest link: VALFS 2026:2 (dated 2026-04-22 in our catalog). We hold
VALFS 2026:2. Matches.

### #76 cross-check
No `valfs` record appears in issue #76's list of PDFs whose own text prints
a different designation than the one minted.
