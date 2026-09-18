# HSLFFS-FOHM — HSLF-FS, Folkhälsomyndigheten

Verdict: DEFECT
Site list: 105 designations (+10 konsoliderade) from 5 pages (page 6 empty), https://www.folkhalsomyndigheten.se/publikationer-och-material/publikationer/?type%5B%5D=F%C3%B6reskrifter&sort%5B%5D=date
We hold: 105 documents (79 hslffs + 12 fohmfs + 14 fhifs), newest HSLF-FS 2026:30; site newest HSLF-FS 2026:30
Missing: 0
Extra: 0
Repeal gaps: 3 confirmed (parse defect) — HSLF-FS 2017:78, 2019:22, 2025:63 (repeal target is FoHMFS-designated, RE_FS_REF misses it); plus 11 amendment-link (andrar) gaps from the same cause
Title defects: 0 in a 12-document random sample
Consolidations: site yes (10), we hold 10 (matches exactly)
Inherited: fohmfs (12) and fhifs (14) both sit under their own slugs (not renumbered into hslffs); fhifs's own repeal chain (14 upphavande=1 docs) resolves 100% correctly
Issue: https://github.com/staffanm/ferenda/issues/54

## Evidence

### 1. Enumeration
`fohm_enumerate`'s own `page_url` (`?pn=N`, 25 rows/page) was fetched pages 1-6
(6 HTTP requests total, well under the 60-request budget). Pages 1-5 returned
25/25/25/25/15 rows, page 6 returned 0 (confirms the end). 115 rows total: 105
base documents (once grouped and deduped by `RE_FOHM_SLUG` + `RE_FS_NUMBER`,
matching `hslffs.fohm_enumerate`'s own logic) + 10 "Konsoliderad version av …"
rows.

### 2/3. Missing / extra
Built the site's designation set (`"<DESIGNATION> <year>:<nr>"`) the same way
`fohm_enumerate` does, and compared it against every catalog row where
`kind='hslffs' and publisher='Folkhälsomyndigheten'`, plus every row where
`kind in ('fohmfs','fhifs')` (105 rows total). The two sets are identical:
0 missing, 0 extra.

    site count 105
    our count 105
    missing (on site, not ours): 0
    extra (ours, not on site): 0

### 4. Repeal marking — DEFECT
14 documents in our corpus have `documents.upphavande=1` for kind hslffs,
publisher Folkhälsomyndigheten. 11 of them resolve `rpubl:upphaver` targets
correctly. 3 do not, and all 3 cite their target with the mixed-case
designation "FoHMFS":

| designation | title says | our upphaver |
|---|---|---|
| HSLF-FS 2017:78 | "... (FoHMFS 2015:2) om lagerdeklaration ..." | `[]` |
| HSLF-FS 2019:22 | "... allmänna råd (FoHMFS 2014:16) om radon inomhus" | `[]` |
| HSLF-FS 2025:63 | "Förordning om upphävande ... (FoHMFS 2014:11) ..." | `[]` |

The source PDFs confirm the target unambiguously (`pdftotext -layout`):

    2017:78: "... att Folkhälsomyndighetens föreskrifter (FoHMFS 2015:2) om
    lagerdeklaration av vin och druvmust ska upphöra att gälla den 15
    januari 2018."
    2019:22: "Folkhälsomyndigheten beslutar att Folkhälsomyndighetens
    allmänna råd (FoHMFS 2014:16) om radon inomhus ska upphöra att gälla
    den 1 november 2019."
    2025:63: "Regeringen föreskriver att Folkhälsomyndighetens föreskrifter
    (FoHMFS 2014:11) om tillstånd för användning av vissa bekämpningsmedel
    ska upphöra att gälla."

### Root cause
`ferenda/foreskrift/parse.py`'s `RE_FS_REF` (line 284):

    RE_FS_REF = re.compile(r"\b([A-ZÅÄÖ]+(?:-| )?(?:FS|FA))\s*(\d{4}):(\d+)")

has no `re.IGNORECASE`, and the prefix class `[A-ZÅÄÖ]+` requires every
letter before "FS" to be uppercase. Folkhälsomyndigheten's predecessor
designation is printed "FoHMFS" — lowercase "o" and "h" in the middle — so
this regex never matches it, anywhere it is cited. `extract_metadata`
(parse.py lines 762-786) uses this same regex to build both `upphaver` and
`andrar`, so every citation of a FoHMFS-designated act is invisible to both.

Confirmed directly:

    .venv/bin/python -c "
    from ferenda.foreskrift import parse
    print(parse.RE_FS_REF.findall('Folkhälsomyndighetens föreskrifter (FoHMFS 2015:2) om lagerdeklaration'))
    "
    # -> []  (matches nothing)

By contrast, the general citation/lagrum parser (used for in-body reference
links, unrelated to `RE_FS_REF`) correctly recognizes and links "FoHMFS
2015:2" in the body text of HSLF-FS 2017:78's own artifact -- so the corpus
already holds the fact that this text names that document; it is only this
one metadata-extraction regex that is blind to it.

This is a broader gap than the repeal check alone: 14 documents whose title
names a FoHMFS-designated act (either amending it or repealing it) all have
both `upphaver` and `andrar` empty:

    HSLF-FS 2016:47, 2017:78, 2019:10, 2019:22, 2019:27, 2019:28, 2020:49,
    2020:77, 2022:64, 2023:38, 2025:63, 2026:17, 2026:18, 2026:20

3 of these are repeal-only documents (the table above); the other 11 are
amendments whose `andrar` should point at the FoHMFS act they amend. Verified
against the catalog `links` table directly: zero `rpubl:upphaver` or
`rpubl:andrar` rows exist for any of the 14.

`FHIFS` (Statens folkhälsoinstitut's own predecessor series, all uppercase)
is unaffected: all 14 `fhifs` documents marked `upphavande=1` resolve their
`upphaver` target correctly.

This is the same function and the same downstream symptom as issue #41
(bfnar), but a distinct root cause within `RE_FS_REF`: #41 is a **suffix**
miss (`BFNAR` does not end in FS/FA at all); this is a **case-folding** miss
(`FoHMFS` does end in FS, but is not all-uppercase). Filed as a separate
issue since a suffix-list fix (the direction #41 suggests) would not by
itself catch this one.

### 5. Titles
Random sample of 12 designations (`random.seed(42)` over the site's 105),
comparing `documents.title` against the site's own link text. 0 diffs (exact
match on all 12, including one FHIFS and one FoHMFS entry).

### 6. Consolidations
Site: 10 "Konsoliderad version av …" rows (8 HSLF-FS, 2 FoHMFS). Our download
records: exactly 10 records under hslffs/fohmfs with publisher
Folkhälsomyndigheten carry a non-empty `files.consolidation`, matching the
same 10 basefiles one-for-one.

### 7. Inherited series
`fohmfs` (12 held) and `fhifs` (14 held) both sit under their own `kind`
slug in the catalog, not folded into `hslffs`. Their designations
("FoHMFS 2014:2", "FHIFS 2010:2", ...) match the site's own listing exactly
(they are part of the same 105-row comparison in checks 2/3, since the site
mixes all three samlingar in one list). `fhifs`'s own repeal chain (14
`upphavande=1` documents) resolves 100% correctly -- only `fohmfs`
citations, when made *from an hslffs document*, are affected by the
`RE_FS_REF` case bug.

### 8. Freshness
Newest row on page 1 of the site list: HSLF-FS 2026:30 (dated 2026-08-19 in
our catalog). Newest we hold: HSLF-FS 2026:30. Match.

### Budget
7 HTTP requests total to folkhalsomyndigheten.se (6 listing pages + 1 already
covered by the site's own robots pacing); well under the 60-request cap. No
blocks encountered.
