# CSNFS — Centrala studiestödsnämnden

Verdict: DEFECT
Site list: 13 in-force base designations (Fulltext sections) + 47 regleringstal
notices + ~55 amendment/repeal instruments, all read from 1 page
(https://www.csn.se/om-csn/lag-och-ratt/forfattningssamling)
We hold: 13 documents (csnfs), newest CSNFS 2022:5; site newest base regulation
also CSNFS 2022:5 (newest designation of any kind: CSNFS 2025:6, a repeal
notice, out of our harvest scope by design)
Missing: 0 base regulations (our harvest scope matches the site's 13 Fulltext
entries exactly). Not counted as missing: ~47 "regleringstal" annual notices
(CSNFS 1978:27 .. 2025:5) and ~55 amendment/repeal instruments — these sit
outside a "Fulltext -" heading and are excluded by the harvester's documented
design (agencies.py comment), which folds every amendment into the one
consolidated fulltext PDF per base regulation.
Extra: 0
Repeal gaps: 1 — CSNFS 2017:1 (studiestartsstöd) is repealed by CSNFS 2025:6,
effective 2026-01-01, transitional relief through end of 2025 only (now
lapsed as of today, 2026-09-13). CSNFS 2025:6 is entirely absent from our
corpus (harvest defect). No `rpubl:upphaver` link exists anywhere in `links`
pointing at csnfs/2017:1.
Title defects: 0 — checked all 13 held titles against the site's own Fulltext
link text; all match verbatim (site itself repeats "(CSNFS nnnn:n)" inside
some titles, e.g. 2006:7, 2010:6, 2013:8, 2022:5 — that is the source's own
wording, not something we added).
Consolidations: site yes (each Fulltext PDF is "Grundförfattningen i dess
lydelse med införda ändringar"), we hold 13 — filed under
`files.regulation`, not `files.consolidation`, by documented design. The
consolidated text is present and current in every case we checked.
Inherited: none (assignment states no predecessor series for this scope).
Issue: https://github.com/staffanm/ferenda/issues/43

## Evidence

### Enumeration
Fetched the entry page once with `ferenda.lib.net.make_session` +
`request()` (HARVESTER_UA, 200 OK, 386428 bytes, one request). Parsed with
BeautifulSoup. Headings under "Författningar i fulltext och tryckt version"
give 10 topic groups, each with a "Fulltext - <ämne>" and "Tryckt version -
<ämne>" pair, plus "Regleringstal" and "Upphävanden" sub-sections nested
under "Återbetalning", and seven "Tryckta versioner utgivna <year>"
sections (2020-2026) listing every individual instrument by year.

Fulltext-section links (our harvester's own scope), 13 total, one per
topic except "Återbetalning" (3 sub-loan-types):

    1995:26, 1998:7, 2001:1, 2001:3, 2001:4, 2001:5, 2001:6, 2006:7,
    2010:6, 2013:8, 2017:1, 2018:5, 2022:5

This is the exact 13-document set our catalog holds:

    sqlite3 'file:site/data/catalog.sqlite?mode=ro' \
      "select label from documents where kind='csnfs' order by label"
    -> 13 rows, matching the list above exactly.

### The repeal gap
"Tryckta versioner utgivna 2025" lists:

    2025:6 Föreskrifter om upphävande av Centrala studiestödsnämndens
    föreskrifter och allmänna råd (CSNFS 2017:1) om beviljning av
    studiestartsstöd

Fetched that PDF
(https://www.csn.se/download/18.67536c7c19b23e409dfbd/1766394952561/...):

    Centrala studiestödsnämnden föreskriver ... att nämndens föreskrifter
    och allmänna råd (CSNFS 2017:1) om beviljning av studiestartsstöd ska
    upphöra att gälla.
    1. Dessa föreskrifter träder i kraft den 1 januari 2026.
    2. De upphävda föreskrifterna gäller dock fortfarande för
    studiestartsstöd som avser tid före utgången av 2025.

The fulltext PDF we already hold for CSNFS 2017:1
(`site/data/downloaded/foreskrift/csnfs/csnfs-2017-1-regulation.pdf`,
downloaded 2026-07-15, after the repeal) states this in its own front
matter (`pdftotext -layout`):

    CSNFS 2017:1
    ändrad               CSNFS 2019:6
                         CSNFS 2020:6
                         CSNFS 2022:3
                         CSNFS 2023:2
                         CSNFS 2023:7
    upphävd              CSNFS 2025:6

Our artifact's metadata has no trace of this:

    read_text('site/data/artifact/foreskrift/csnfs/2017-1.json')
    -> metadata.upphaver = [], metadata.andradAv = []

And the `links` table has no `rpubl:upphaver` row at all pointing at
csnfs/2017:1 (checked all 6 link rows touching that URI — all
`dcterms:references`, none `rpubl:upphaver`). CSNFS 2025:6 does not exist
under any `csnfs/2025-*` artifact or download path
(`ls site/data/artifact/foreskrift/csnfs/` — 13 files, no 2025-6).

Root cause: `csnfs_enumerate()` in `ferenda/foreskrift/agencies.py` only
follows links under a heading starting with "fulltext" (case-insensitive).
A repeal notice never gets its own Fulltext heading — it appears only
under "Tryckta versioner utgivna <year>" — so the harvester can never
discover it.

### Titles
Compared all 13 site Fulltext link texts (source of designation + title)
against `documents.title` for the same 13 labels — verbatim match in every
case once the site's own "Pdf, nnn kB[, öppnas i nytt fönster]" suffix is
stripped, which our extraction already does correctly.

### Consolidations
All 13 download records
(`site/data/downloaded/foreskrift/csnfs/csnfs-*.json.br`) show
`files.consolidation: []`, `files.amendment: []`; the fulltext PDF is
filed as `files.regulation`. This matches the source docstring in
agencies.py, which explains the Fulltext PDF already is the consolidated
text, so there is no separate consolidation artifact to fetch. Not a
defect on its own.

### Not filed as a defect
The ~47 "regleringstal" notices (CSNFS 1978:27 .. 2025:5, under the
"Regleringstal" h4 nested in "Återbetalning") and the ~55 individual
yearly amendment PDFs (listed under "Tryckta versioner utgivna <year>")
are real CSNFS-numbered instruments not held as separate documents in our
corpus. This looked at first like a "Missing documents" gap (check 2), but
the harvester's design explicitly scopes to in-force base regulations only
and folds every amendment into one consolidated fulltext per topic
(documented in the agencies.py comment above `csnfs_enumerate`). Widening
that scope is a design call, not a corpus defect, so it is reported here
for visibility only and not filed.
