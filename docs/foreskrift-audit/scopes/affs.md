# AFFS — Arbetsförmedlingens författningssamling, Arbetsförmedlingen
Verdict: OK
Site list: 11 designations from 1 page (https://arbetsformedlingen.se/om-oss/var-verksamhet/styrning-och-resultat/forfattningssamling-affs)
We hold: 11 documents (10 affs, 1 amsfs), newest AFFS 2025:2; site newest AFFS 2025:2
Missing: 0
Extra: 0
Repeal gaps: 0 — all 8 upphaver links in our corpus point outside the site's gällande list (older repealed AFFS/AMSFS numbers the site does not publish); expected, not a defect
Title defects: 0 — all 11 titles match the site's description field exactly
Consolidations: site no, we hold 0
Inherited: amsfs — 1 document (AMSFS 1996:7), correctly under its own `amsfs` slug and kind, matching the site's single amsfs entry
Issue: none

## Evidence

### 1. Enumerate
The AFFS entry page is a Sitevision app carrying its whole register as an
embedded JSON blob, not a paginated HTML list. One GET reads it all:

    .venv/bin/python3 -c '
    from ferenda.lib.net import make_session, request, BROWSER_UA
    session = make_session(BROWSER_UA)
    r = request(session, "GET", "https://arbetsformedlingen.se/om-oss/var-verksamhet/styrning-och-resultat/forfattningssamling-affs")
    print(r.status_code, len(r.text))'
    # 200 108333

Applying the same regex the harvester uses (`RE_AFFS_ENTRY` in
`ferenda/foreskrift/agencies.py`) to the saved page yields exactly 11 entries:
AFFS 2025:1, 2025:2, 2023:1, 2023:2, 2021:1, 2021:2, 2018:3, 2018:4, 2017:4,
2010:2, and AMSFS 1996:7. This is a single flat list with no separate
"upphävda" section and no status field — the site presents all 11 as
currently gällande.

### 2-3. Missing / extra
Catalog query:

    sqlite3.connect("file:site/data/catalog.sqlite?mode=ro", uri=True)
    select uri,label,title,date,publisher,source_url,path,expired,upphavande
    from documents where kind in ('affs','amsfs')

returned 10 affs + 1 amsfs rows whose labels are exactly the 11 site
designations, one-to-one. Zero missing, zero extra.

### 4. Repeal marking
The site marks nothing as upphävd (no strikethrough, no status field — see
above), so there is nothing on the site side to check against. Our own
`links` table carries 8 `rpubl:upphaver` rows from affs/amsfs documents:

    affs/2010:2 -> affs/2010:1, amsfs/2002:2
    affs/2021:1 -> affs/2020:1
    affs/2021:2 -> affs/2002:546, affs/2015:1, amsfs/2002:11
    affs/2023:2 -> affs/2010:5
    affs/2025:1 -> affs/2021:1
    amsfs/1996:7 -> amsfs/1988:8

All targets except affs/2021:1 are older repealed designations the agency's
site does not publish at all (it lists only in-force text) — expected, not a
defect. affs/2021:1 is itself one of the 11 documents we hold, so that
repeal chain is internally consistent.

One real-world curiosity, not a corpus defect: AFFS 2025:1's own text says
"Genom föreskrifterna upphävs ... (AFFS 2021:1)" with ikraftträdande
2025-10-01 (past, relative to today 2026-09-13), yet the agency's own site
still lists AFFS 2021:1 among its "gällande" entries with no repeal notice.
Confirmed by fetching both landing pages directly:

    affs-20251.html: "Dessa föreskrifter träder i kraft den 1 oktober 2025.
      Genom föreskrifterna upphävs Arbetsförmedlingens föreskrifter
      (AFFS 2021:1) om aktivitetsrapport..."
    affs-20211.html: no upphävd/upphört-att-gälla banner anywhere in the page.

This is the agency's own site being stale, not our data being wrong — our
`upphaver` link (affs/2025:1 -> affs/2021:1) is correct per the source PDF
text. Per the repeal model, we never mark the repealed document itself with a
status flag, so no corpus change is warranted.

### 5. Titles
Compared our `documents.title` against the site's `description` field for
all 11 documents (not just 10 — did all of them, since the list is small):
11/11 exact string matches. No truncation, no file-size suffix, no HTML
entities, no PDF filename leakage.

### 6. Consolidations
Every download record under
`site/data/downloaded/foreskrift/{affs,amsfs}/*.json.br` carries
`files.consolidation: 0`. The site's landing pages likewise expose only a
single "ursprunglig lydelse" / "fulltext" PDF per document, no separate
konsoliderad version. Consistent — no defect.

### 7. Inherited series
AMSFS 1996:7 sits under `kind='amsfs'`, `uri=https://lagen.nu/amsfs/1996:7`,
not renumbered into `affs`. `fs_from_designation=True` on the AFFS agency
config (ferenda/foreskrift/agencies.py:2210) is doing its job. The site's
own single amsfs entry matches ours exactly (title and designation).

### 8. Freshness
Site newest: AFFS 2025:2 (also AFFS 2025:1, same lastPublishDate window).
Our newest: AFFS 2025:2 (date 2025-05-20). Match.

### HTTP budget
3 requests total (index page + 2 landing pages for the repeal-currency
check), well under the 60-request budget. No blocking encountered.
