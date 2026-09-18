# KBVFS — Kustbevakningens författningssamling, Kustbevakningen

Verdict: DEFECT
Site list: 4 designations from 1 page (https://www.kustbevakningen.se/om-oss/kustbevakningens-forfattningssamling/), cross-checked against the agency's own register PDF (2 pages)
We hold: 4 documents (kbvfs), newest KBVFS 2025:1; site newest KBVFS 2025:1
Missing: 0
Extra: 0
Repeal gaps: 0 — all three repeal links in our corpus match the agency's own register
Title defects: 1 — KBVFS 2025:1: we hold "Kustbevakningens föreskrifter och allmänna råd om avgifter vid uppdragsverksamhet Beslutade den 24 november 2025. Träder i kraft den 1 januari 2026. 115 kb 2025-12-01"; the site's title is "Kustbevakningens föreskrifter och allmänna råd om avgifter vid uppdragsverksamhet"
Consolidations: site no, we hold 0
Inherited: none (no predecessor series for this scope)
Issue: https://github.com/staffanm/ferenda/issues/61

## Evidence

### 1. Enumerate

Fetched the entry page with `ferenda.lib.net.request` (one GET, 200, 149912
bytes). The page states it lists "Kustbevakningens gällande föreskrifter och
allmänna råd (KBVFS)" — in-force documents only. Its table (`[data-href]`
rows) names exactly 4 designations:

    KBVFS 2021:3, KBVFS 2021:4, KBVFS 2024:2, KBVFS 2025:1

Confirmed against a second source: the page links a PDF register,
`.../register/register-over-kustbevakningens-forfattningssamling.pdf`
(fetched, 200, 162789 bytes, dated 2026-01-01, dnr 2025-2396:2). Its section
1, "Förteckning över gällande grundförfattningar", lists the same 4
designations with the same beslutsdatum/ikraftträdande.

### 2 and 3. Missing / extra documents

    .venv/bin/python -c "import sqlite3; con = sqlite3.connect('file:site/data/catalog.sqlite?mode=ro', uri=True); print([r for r in con.execute(\"select label from documents where kind='kbvfs' order by label\")])"

Returns the same 4 labels as the site and the register. 0 missing, 0 extra.

### 4. Repeal marking

Register PDF section 2, "Förteckning över ändringsförfattningar":

    KBVFS 2021:4  Upphäver KBVFS 2019:1
    KBVFS 2024:2  Upphäver KBVFS 2024:1
    KBVFS 2025:1  Upphäver KBVFS 2024:3

Our artifacts (`site/data/artifact/foreskrift/kbvfs/<year>-<lop>.json.br`,
`metadata.upphaver`) carry the identical targets:

    2021-4.json  upphaver: ['https://lagen.nu/kbvfs/2019:1']
    2024-2.json  upphaver: ['https://lagen.nu/kbvfs/2024:1']
    2025-1.json  upphaver: ['https://lagen.nu/kbvfs/2024:3']

KBVFS 2019:1, 2024:1 and 2024:3 are not published anywhere on the agency's
site or in its register — a repealed base regulation is removed once
superseded. We correctly do not hold them; the repeal model treats this as
the expected shape, not a gap.

### 5. Titles — the defect

    .venv/bin/python -c "
    from ferenda.lib.compress import read_text
    import json
    d = json.loads(read_text('site/data/artifact/foreskrift/kbvfs/2025-1.json'))
    print(d['metadata']['title'])
    "
    # -> Kustbevakningens föreskrifter och allmänna råd om avgifter vid
    #    uppdragsverksamhet Beslutade den 24 november 2025. Träder i kraft
    #    den 1 januari 2026. 115 kb 2025-12-01

The site's own title (the row's title cell, size and date columns excluded)
is "Kustbevakningens föreskrifter och allmänna råd om avgifter vid
uppdragsverksamhet". Our stored title runs on into the row's "Beslutad(e)
den ..." clause plus its file-size and modified-date columns.

Root cause, read from `ferenda/foreskrift/parse.py`: `clean_title()` (line
987) strips trailing chrome with `RE_TITLE_CHROME` (line 977), which matches
from a literal `pdf` / `.pdf` token to the end of the string. The other 3
KBVFS rows carry a visible `.pdf` suffix in their title cell (the site
appends the file name there), so the chrome regex finds an anchor and cuts
everything after it. KBVFS 2025:1's title cell has no `.pdf` suffix (its
row uses a different file-type icon, `fa-file-excel` vs `fa-file-pdf`, and
the site's markup simply omits the suffix there), so the anchor never
matches and the trailing "Beslutad(e) den ... <n> kb <date>" chrome survives
straight into the stored title.

Verified against the raw HTML:

    KBVFS 2025:1 row text: 'KBVFS 2025:1 Kustbevakningens föreskrifter och
    allmänna råd om avgifter vid uppdragsverksamhet Beslutade den 24
    november 2025. Träder i kraft den 1 januari 2026. 115 kb 2025-12-01'
    (no ".pdf" anywhere)

    KBVFS 2024:2 row text: '... utbildning för säker drift av
    Kustbevakningens fartyg.pdf Beslutade den 25 juni 2024. ...'
    (".pdf" present -> chrome regex cuts here -> clean title)

The other 3 titles (2021:3, 2021:4, 2024:2) match the site exactly.

### 6. Consolidations

Register PDF lists only grundförfattningar and ändringsförfattningar, no
"konsoliderad version". All 4 download records
(`site/data/downloaded/foreskrift/kbvfs/kbvfs-*.json`) carry an empty
`files.consolidation`. Site and corpus agree: no consolidations.

### 7. Inherited series

None assigned for this scope.

### 8. Freshness

Site and register both show KBVFS 2025:1 as the newest (ikraftträdande
2026-01-01). We hold KBVFS 2025:1 as the newest document. No gap.

### Requests used

4 (index page, register PDF, and 2 politeness pauses — well under the
60-request budget). No blocking encountered.
