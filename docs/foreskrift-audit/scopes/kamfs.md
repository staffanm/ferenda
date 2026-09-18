# KAMFS — Kammarkollegiets författningssamling, Kammarkollegiet
Verdict: DEFECT
Site list: 81 designations from 1 page (https://www.kammarkollegiet.se/om-oss/kammarkollegiets-forfattningssamling-kamfs)
We hold: 82 documents (kamfs), newest KAMFS 2026:4; site newest KAMFS 2026:4
Missing: 0
Extra: 1 — KAMFS 2019:2, repealed by KAMFS 2024:2 (link present and correct, legitimate)
Repeal gaps: 1 — KAMFS 2006:1 has an empty upphaver list despite its own PDF text repealing KAMFS 1998:1 (parse defect)
Title defects: 4 — KAMFS 2012:1, 2012:2, 2012:3, 2013:1 carry raw U+00A0 (nbsp) instead of spaces
Consolidations: site no, we hold 0
Inherited: none (per assignment); Förteckning shows Fastighetsmäklarinspektionen (predecessor FMN) and Trafikanalys (predecessor SIKAFS) have their own dual-numbered predecessor samlingar — out of this scope, not reported as a defect
Issue: https://github.com/staffanm/ferenda/issues/62

## Evidence

### 1. Enumerate (check 1)
Fetched the entry page once (1 HTTP request via `ferenda.lib.net.request`,
plain requests.Session). Ran the real `ferenda.foreskrift.agencies.kam_enumerate`
against the saved HTML (monkeypatching `request`) to get the exact harvest
logic's output: 81 DocRefs, one static page, no pagination, no landing pages
(matches the agency comment in agencies.py).

    site count 81
    ours count 82
    site - ours (missing from corpus): []
    ours - site (extra in corpus): ['2019:2']

### 2/3. Missing / Extra (checks 2, 3)
No designation on the live page is absent from our corpus. `kamfs/2019:2` is
the only document we hold that the live page no longer links — it was
repealed by `kamfs/2024:2` ("Kammarkollegiets föreskrifter om upphävande av
… (KAMFS 2019:2) …"), and the `rpubl:upphaver` link 2024:2 → 2019:2 is
present in `links`. Not a defect (matches the "extra but repealed" shape the
briefing calls out as often legitimate).

### 4. Repeal marking (check 4)
Fetched the agency's own "Förteckning över gällande författningar … (KAMFS)
2026" PDF (linked from the entry page, 1 more HTTP request, dated
2026-03-03, `sha` not recorded) and read it with `pdftotext -layout`. It
lists every KAMFS designation ever issued with its repeal chain. Cross-checked
every `rpubl:upphaver` link in our `links` table for `kamfs` against it:

    select from_uri, predicate, to_uri from links
    where predicate='rpubl:upphaver' and (from_uri like '%/kamfs/%' or to_uri like '%/kamfs/%')

All 21 rows for documents we hold on both ends match the Förteckning exactly
(2019:4→2023:3, 2020:2→2022:4→2024:1, 2001:1→2022:5, 2013:3→2021:1,
2016:1→2019:1, 2016:4→2021:2, 2019:1/2020:1→2021:3, 2020:4→2021:4, 2022:7→2023:4,
2019:2→2024:2, 2021:2→2025:1, 2023:3→2025:2, 1994:2→2015:5/2016:5, 2016:5→2019:4).

One gap found: `kamfs/2006:1`'s own PDF text
(`site/data/downloaded/foreskrift/kamfs/kamfs-2006-1-regulation.pdf`) says:

    Dessa föreskrifter träder i kraft den 1 juli 2006 då Kammarkollegiets
    föreskrifter (KAMFS 1998:1) till förordningen (1993:1138) om hantering
    av statliga fordringar upphävs.

but `site/data/artifact/foreskrift/kamfs/2006-1.json`'s
`metadata.upphaver` is `[]`. Root cause traced in `ferenda/foreskrift/parse.py`:
`RE_ERSATTER` matches the verb "upphävs" but only captures text *after* it;
here the target stands *before* the verb, and `RE_UPPHOR_BEFORE` (which
handles before-the-verb targets) only recognizes "upphör/upphöra att gälla",
not "upphävs". Confirmed by testing both regexes against the sentence in
isolation: `RE_ERSATTER` captures an empty string ("upphävs." with a 0-char
group), `RE_UPPHOR_BEFORE` does not match at all. Filed as issue #62.

The other 8 upphaver targets absent from our corpus (1994:1, 2001:1, 2011:3,
2011:4, 2012:4, 2016:1, 2016:4, 2020:4, 2022:4) are all documents the live
page no longer links (confirmed: `site - ours` above is empty, i.e. nothing
on today's page is unheld) — three of those (2011:3, 2011:4, 2012:4) are
Fastighetsmäklarnämnden-era "FMN" documents dual-numbered as KAMFS in the
Förteckning, out of this scope's remit. The rest (1994:1, 2001:1, 2016:1,
2016:4, 2020:4, 2022:1, 2022:2, 2022:3, 2022:4) are genuine KAMFS-only
designations that predate or were superseded before our harvest window and
have no PDF link anywhere on the current site — nothing to fetch, not an
actionable harvest defect today.

### 5. Titles (check 5)
Compared 15+ titles against the live page's own anchor text and the
Förteckning. All titles read off the PDF, not off the anchor text, and are
free of file sizes, truncation, or PDF filenames — except 4 that carry raw
non-breaking-space (U+00A0) characters from the site's own anchor markup:

    sqlite3 site/data/catalog.sqlite \
      "select label, title from documents where kind='kamfs'" \
      | grep -P '\xa0'
    KAMFS 2012:1 | …vägtrafik m.m.\xa0(KAMFS\xa02012:1,\xa0TRAFAFS\xa02012:1)
    KAMFS 2012:2 | …sjöfart m.m.\xa0(KAMFS\xa02012:2,\xa0TRAFAFS\xa02012:2)
    KAMFS 2012:3 | …bantrafik m.m.\xa0(KAMFS\xa02012:3,\xa0TRAFAFS\xa02012:3)
    KAMFS 2013:1 | …postverksamhet m.m.\xa0(KAMFS\xa02013:1,\xa0TRAFAFS\xa02013:1)

Sibling Trafikanalys titles (2014:1, 2014:4, 2015:3, 2020:3) use a plain
space in the same spot, so this is a fallback-to-raw-anchor-text bug, not the
agency's own style. Filed as part of issue #62.

### 6. Consolidations (check 6)
    for each of 82 downloaded/foreskrift/kamfs/*.json.br: files.consolidation == []
    count with consolidation: 0
The live page has no "konsoliderad version" links either (grepped the saved
HTML). Matches.

### 7. Inherited series (check 7)
Assignment says none. The Förteckning PDF (section 2 and 3) shows
Fastighetsmäklarinspektionen's predecessor Fastighetsmäklarnämnden ("FMN")
and Trafikanalys's predecessor Statens institut för kommunikationsanalys
("SIKAFS") each have their own historical samling, and Kammarkollegiet
internally dual-numbers those old documents with a KAMFS reference number
for its own index — but the documents were never actually published under a
KAMFS designation. This resembles the #50 predecessor/successor pattern, but
`fmn`/`sikafs` are not part of this scope's assignment (only `kamfs/2009:1`
touches `sikafs/2008:6` directly, and that upphaver link is already correct
in our corpus). Reporting for awareness, not filing.

### 8. Freshness (check 8)
Newest on the live page: KAMFS 2026:4 (Anmälan av reseverksamhet …,
2026-06-10). We hold it. Matches.

## Budget used
2 HTTP requests to kammarkollegiet.se (entry page + Förteckning PDF), well
under the 60-request budget.
