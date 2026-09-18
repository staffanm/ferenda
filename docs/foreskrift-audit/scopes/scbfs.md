# SCBFS — SCB-FS, Statistiska centralbyrån
Verdict: DEFECT
Site list: 201 designations from 2 pages (https://www.scb.se/om-scb/scbs-verksamhet/regelverk-och-policyer/foreskrifter/, UL and KPI tabs)
We hold: 202 documents (scbfs), newest SCB-FS 2026:18; site newest SCB-FS 2026:18
Missing: 0
Extra: 1 — SCB-FS 2019:22 (Medlingsinstitutet notice; repealed by scbfs/2026:16, which we hold — legitimate, site drops repealed items from its register)
Repeal gaps: 4 — SCB-FS 2000:8, 2021:3, 2021:8, 2026:8 name another SCB-FS in a repeal clause but `upphaver` stays empty (see below; not the #41 hyphen bug)
Title defects: 1 minor — SCB-FS 2019:22 title runs two words together ("EUundersökningarna", "CostIndex"); no defects in a random sample of 15
Consolidations: site no, we hold 0 (OK)
Inherited: none (assignment says none; scbfs also carries Medlingsinstitutet notices by design, confirmed on scbfs/2019:22, not a predecessor slug)
Issue: https://github.com/staffanm/ferenda/issues/89

## Evidence

### 1. Enumeration (checks 1, 2, 3, 8)

Fetched both index tables with `ferenda.lib.net.request`:

    https://www.scb.se/.../foreskrifter/?currentpageId=98388&type=UL   -> 93 rows (92 designations)
    https://www.scb.se/.../foreskrifter/?currentpageId=98388&type=KPI  -> 110 rows (109 designations)

201 unique designations, all `SCB-FS YYYY:N` (leading-zero lopnummer on the site,
e.g. "SCB-FS 1996:08"; we store "SCB-FS 1996:8" — same document, cosmetic only,
normalised for comparison as int(year):int(lop)).

    sqlite3 catalog.sqlite: select label, date, expired from documents where kind='scbfs'
    -> 202 rows

Set difference after normalisation: missing = 0, extra = 1 (SCB-FS 2019:22).
2019:22 is a Medlingsinstitutet notice (title: "Medlingsinstitutets föreskrifter
om kompletterande uppgifter till statistik om bonus till EU-undersökningarna...").
`links` table: `from_uri='https://lagen.nu/scbfs/2026:16'` carries an
`rpubl:upphaver` row targeting it — repealed by a document we hold. The site's
register omits repealed items entirely, so this is the expected shape, not a
defect (per the repeal model in the briefing).

Newest on both sides: SCB-FS 2026:18.

### 2. Issue #76 — 19 scbfs records printing a different number

    gh issue view 76 --repo staffanm/ferenda   # scbfs: 19 records, all "live" origin

Read the stored regulation PDFs with `pdftotext -layout` for the 19 basefiles.
Three distinct causes, not one:

**(a) 16 of 19 — a ghost/overlay masthead.** SCB's PDF-generation template for
the "Tillkännagivande" / KPI announcements and several amendment/repeal notices
carries forward text from an older edition as an extra, overlapping layer on
page 1. `pdftotext` interleaves both layers. Example, `scbfs-2024-1-regulation.pdf`
(1 page):

    SCB-FS 2023:22
    SCB-FS 2024:1
    ...
    Föreskrifter
    om upphävande av Statistiska centralbyråns föreskrifter (SCB-FS
    2016:18)
    Tillkännagivande
    om skyldighet ... av uppgift om konsumentprisindex för december 2023;

Two unrelated documents' mastheads (SCB-FS 2023:22 and SCB-FS 2024:1) sit on
the same page. `scbfs/2022:10`, `2022:11`, `2022:14-16`, `2022:21`, `2022:22`,
`2022:24`, `2022:27`, `2022:28`, `2023:1`, `2024:1`, `2024:8`, `2024:13-15`
follow the same pattern (confirmed by reading the pages).

This is an SCB production defect, not a ferenda harvest/parse bug — the PDF we
store is the PDF SCB serves. But `ferenda/foreskrift/parse.py`'s
`_first_date(RE_BESLUTAD, text)` / `_first_date(RE_UTKOM, text)` (line 733-734)
take the *first* regex match in the extracted text, and the ghost layer's text
consistently sits ahead of the real layer. Result: wrong `beslutsdatum` /
`utkomFranTryck` on far more documents than the 19 #76 lists (#76 only checks
the designation number, not the dates):

    39 scbfs documents carry beslutsdatum = 2019-03-05 (SCB-FS 2019:6's ghost date)
    56 scbfs documents carry utkomFranTryck = 2019-03-06 (SCB-FS 2019:6's ghost date)

spanning identifiers from SCB-FS 2019:12 through SCB-FS 2023:1 — titles are
correct (e.g. SCB-FS 2021:1 = "Konsumentprisindex, december 2021") but the date
fields are all the stale 2019-03-05/06 pair. Verified against the PDF text for
`2020-1`, `2021-1`.

The same overlay also corrupts `upphaver`: `SCB-FS 2023:22` is the real
"upphävande av ... (SCB-FS 2016:18)" document (its own `upphaver` is correctly
`[scbfs/2016:18]`), but its ghost masthead text leaked into five later, unrelated
KPI documents built from the same template — `SCB-FS 2024:1`, `2024:8`,
`2024:13`, `2024:14`, `2024:15` — and our parser minted the same false
`upphaver: [scbfs/2016:18]` on all five.

**(b) 2 of 19 — false positive.** `scbfs/2009:21` and `scbfs/2010:6` print
"2001:99, 2001:100" — these are citations to "lagen (2001:99) och förordningen
(2001:100) om den officiella statistiken" in the body text, not an alternate
designation. Exactly the caveat #76 already names ("a document whose masthead
sits on page 3" etc.) — no action needed.

**(c) 1 of 19 — wrong file classified as the regulation.** `scbfs/2016:7`'s
download record stores `scb-fs-2016-7-variabelforteckning.pdf` (a "Variabelförteckning"
— a data-collection code list) as `files.regulation`. Page 1-2 of that PDF has
no SCB-FS designation and no regulation text at all; it is a bilaga document,
not the regulation. The landing page for this designation apparently exposes
only this one PDF link, and `classify_single` ("the landing hangs exactly one
/contentassets/ PDF") stores whatever single PDF it finds.

Filed as part of #89 (new — (a) and (c) are causes #76 does not name).

### 3. RE_FS_REF hyphen (issue #41, task 2)

    re.compile(r"\b([A-ZÅÄÖ]+(?:-| )?(?:FS|FA))\s*(\d{4}):(\d+)").findall("SCB-FS 2020:1")
    -> [('SCB-FS', '2020', '1')]

The hyphen form matches correctly — unlike RA-MS in #41 (which doesn't end in
FS/FA at all), SCB-FS does end in FS and the regex already allows an optional
hyphen/space before it. Scanned all 202 artifacts for a title/body mention of
another `SCB-FS YYYY:N` while `upphaver` and `andrar` are both empty:

    4 documents: SCB-FS 2000:8, 2021:3, 2021:8, 2026:8

None of these are the #41 hyphen bug. Two distinct, real gaps instead:

- `SCB-FS 2000:8`: a repeal-list table ("Statistiska centralbyrån föreskriver
  att följande föreskrifter skall upphöra att gälla.") whose ten rows print a
  bare two-digit year and number ("95:14", "96:4", "93:2", ...) in a column,
  without repeating "SCB-FS" on each row, and the introductory sentence ends
  in a period rather than ":"/"nämligen"/"enligt följande." — `RE_UPPHOR_LIST`
  needs one of those three to fire, and `RE_BARE_OWN_REF` expects a 4-digit
  year in parentheses, not a bare 2-digit "YY:N" table cell. Confirmed by
  `pdftotext -layout scbfs-2000-8-regulation.pdf`.
- `SCB-FS 2021:3`, `2021:8`, `2026:8`: an idiom none of `RE_UPPHOR_LIST`,
  `RE_UPPHAVS_LIST`, `RE_UPPHORA` cover — "Denna författning träder i kraft
  den <date>, då Statistiska centralbyråns föreskrifter (SCB-FS <ref>) om ...
  upphör att gälla." (a repeal folded into the entry-into-force sentence, not
  a "beslutar/föreskriver ... att X ska upphöra" decision or a "följande
  föreskrifter ... upphävs" list). Confirmed by `pdftotext -layout` on all
  three PDFs.

Filed as part of #89 — a new, un-named cause, not #41's.

### 4. Titles (check 5)

Random sample of 15 (seed 1) compared site Rubrik vs `documents.title`: all 15
matched exactly. The one title defect found is on the extra document,
SCB-FS 2019:22 (see above).

### 5. Consolidations (check 6)

    194 download records (8 are stored uncompressed .json, see below) with
    files.consolidation/amendment/memo/attachment all empty on every one.

The site's two tables are flat lists with no separate konsoliderad version;
holding zero is correct.

### 6. The 8 non-hyphenated "SCBFS" records — no regulation PDF at all

    select label, path from documents where kind='scbfs' and label like 'SCBFS%'
    -> SCBFS 2016:1, 2018:12, 2018:13, 2018:14, 2018:15, 2018:16, 2018:18, 2019:15

All 8 have `files.regulation: null` in the download record and no `structure`
key at all in the artifact — a metadata-only shell, no body text. Their landing
page (fetched live today, still true) links the PDF as a friendly slug that
does not end in ".pdf":

    /contentassets/2e8a8f058b344e8aa841a0d4a29c7333/tillkannagivande-av-uppgift-om-konsumentprisindex-mars-2018-pdf/

Fetched that URL directly: `200 application/pdf`, 189071 bytes — it is a real
PDF. `SCBFS.params["pdf_select"] = 'a[href*="/contentassets/"][href$=".pdf"]'`
requires the href to literally end in ".pdf", so this link is never picked up.
Reproducible today, not a stale snapshot.

Separately, `identifier = "%s %s:%s" % (agency.designation or agency.fs.upper(), ...)`
(`ferenda/foreskrift/harvest.py:477`) falls back to `agency.fs.upper()` =
"SCBFS" (no hyphen) when `agency.designation` is unset. These 8 records predate
the `designation="SCB-FS"` line in the current agency config and were never
revisited by a later incremental harvest (the watermark only walks forward),
so they carry both defects together: no body text, and a stale "SCBFS" label.

Filed as part of #89 — a new cause, current and reproducible.
