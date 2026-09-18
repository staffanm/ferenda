# VRFS — Vetenskapsrådets författningssamling, Vetenskapsrådet
Verdict: OK
Site list: 8 designations from 1 page (https://www.vr.se/om-vetenskapsradet/styrande-dokument/vetenskapsradets-forfattningssamling.html)
We hold: 8 documents (vrfs), newest VRFS 2025:1; site newest VRFS 2025:1
Missing: 0
Extra: 0
Repeal gaps: 0 (site marks no gällande document as upphävd; three pre-2013 föreskrifter are named only as text history, with no downloadable PDF — see Evidence)
Title defects: 0 — all 8 titles match the site's link text verbatim, including the site's own "Förordning om ändring..." wording on VRFS 2025:1
Consolidations: site no, we hold 0
Inherited: none (assignment lists no predecessor slug)
Issue: none

## Evidence

### 1. Enumerate
Fetched the entry page with `ferenda.lib.net.request` (one page, no pagination).
Selector `a[href*="/download/"][href$=".pdf"]` found 8 links, each row text
carrying one or more "XXFS YYYY:N" designations (the harvest's
`last_designation_enumerate` keys on the last one per row, matching
`ferenda/foreskrift/agencies.py:1519-1525`):

    VRFS 2013:1, VRFS 2014:1, VRFS 2025:1, VRFS 2021:1, VRFS 2024:1,
    VRFS 2019:1, VRFS 2019:2, VRFS 2021:2

### 2/3. Missing / extra
    sqlite3.connect("file:site/data/catalog.sqlite?mode=ro", uri=True)
    select label from documents where kind='vrfs' order by label;
    -> VRFS 2013:1, 2014:1, 2019:1, 2019:2, 2021:1, 2021:2, 2024:1, 2025:1

Set equality with the site's 8 designations. Zero missing, zero extra.

### 4. Repeal marking
No document on the page is struck through or marked "upphävd" among the 8
gällande PDFs. The page does carry a separate "Upphävda föreskrifter" text
block naming VRFS 2012:1 (upphävd genom VRFS 2018:1), VRFS 2009:1 (upphävd
genom VRFS 2012:1), and VRFS 2004:1 (upphävd genom VRFS 2009:1) — plain
prose, no PDF link, no `/download/` href. None of 2004:1/2009:1/2012:1/2018:1
is a document our harvest could ever fetch, since the site offers no file
for any of them; this is not a harvest gap. `links` rows citing
`vrfs/2004:1` and `vrfs/2012:1` from SOU/Ds documents are unresolved
citation targets (expected — see `unresolved-citation-targets` memory),
not documents the site publishes today.

### 5. Titles
Compared all 8 (fewer than 10 exist). Site link text (minus the
" pdf, N kB." suffix BeautifulSoup appends from the icon's alt/sr text)
equals `documents.title` byte for byte for every one, e.g.:

    VRFS 2025:1 site: "Förordning om ändring i Vetenskapsrådets
      föreskrifter (VRFS 2013:1) om godkännande för forskningshuvudmän
      att ta emot gästforskare (VRFS 2025:1)"
    VRFS 2025:1 ours: identical

The odd "Förordning" (not "Föreskrifter") in the 2025:1 title is the
agency's own wording, confirmed by the site page; not a corpus defect.

### 6. Consolidations
Site offers only the base regulation and amendment PDFs, no separate
konsoliderad version links. Checked both download records:

    brotli.decompress(...vrfs-2013-1.json.br) -> files.consolidation == []
    brotli.decompress(...vrfs-2025-1.json.br) -> files.consolidation == []

All 8 download records show `files.consolidation: []`. Matches the site.

### 7. Inherited series
Assignment states "none". No predecessor slug to check.

### 8. Freshness
Site newest: VRFS 2025:1 (PDF timestamp path 1745938513645, dated
2025-03-27 per catalog). We hold VRFS 2025:1. Matches.

### #76 cross-check
`gh issue view 76` body has no vrfs entries. Independently ran
`pdftotext -l 1` on all 8 stored regulation PDFs and grepped for
"XXFS YYYY:N" patterns: every PDF's own designation (VRFS 2013:1, 2014:1,
2019:1, 2019:2, 2021:1, 2021:2, 2024:1, 2025:1) appears last in its own
first page, matching the minted designation. No mismatch.

Budget: 1 HTTP request to vr.se (well under the 60-request cap).
