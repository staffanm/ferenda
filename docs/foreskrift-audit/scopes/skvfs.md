# SKVFS — Skatteverkets författningssamling, Skatteverket

Verdict: DEFECT
Site list: 591 designations from 1 page (https://www4.skatteverket.se/rattsligvagledning/115.html?year=Alla), 556 skvfs + 35 rsfs
We hold: 547 skvfs documents, newest SKVFS 2026:8 (2026-06-08); site newest SKVFS 2026:12. We hold 31 rsfs, newest RSFS 2002:13; site newest RSFS 2003:29.
Missing: 13 — 4 recent freshness lag (skvfs 2026:9, 2026:10, 2026:11, 2026:12) + 9 real gaps: skvfs 2004:37, 2004:38, 2005:28, 2005:29, 2006:9; rsfs 1996:18, 1999:29, 2003:25, 2003:29
Extra: 0
Repeal gaps: 0 confirmed (sample SKVFS 2009:3 -> upphävts genom SKVFS 2014:10 checks out); 4 records (skvfs/2011:24, 2012:2, 2017:12, 2017:13) carry an empty `upphaver` list, but their own stored PDF (and the site's detail page) names no target either — not a parse defect, just noted
Title defects: 9 — 8 placeholder ("Doc <basefile>"), 1 missing "Förordning" prefix (SKVFS 2018:21), all from the `skvfs-legacy` import
Consolidations: site no, we hold 0 (site publishes only the as-issued text plus a one-line "ändrad genom / upphävts genom" note; no konsoliderad PDF exists)
Inherited: rsfs — sits under its own slug, no cross-slug bleed into skvfs (not an #50 case); 4 designations missing from harvest (see Missing)
Issue: https://github.com/staffanm/ferenda/issues/92

## Evidence

### Setup
- `ferenda/foreskrift/skvfs.py` and `ferenda/lib/browser.py` read first, as instructed.
- SKVFS uses `browser=True, browser_pace=20.0` (ferenda/foreskrift/agencies.py:2482-2485),
  matching the briefing's 20 s caution.
- Camoufox session against a scratch profile
  (`fs-audit/skvfs-profile`), 3 page loads total, all spaced >=20 s:
  1. `INDEX_URL` (the "year=Alla" register) — one page holds every SKVFS and RSFS row, 591 total.
  2. Detail page for skvfs/2011:24 (a "Bekantgörande" repeal notice).
  3. Detail page for skvfs/2009:3 (a document with a known amend/repeal chain).
  No block encountered.

### Enumerate + Missing/Extra
    .venv/bin/python fetch_index.py     # saves skvfs-index.html, parses with skvfs.parse_index
    .venv/bin/python diff.py            # diffs site refs against catalog.sqlite documents

    site total: 591  ours total: 578
    missing (13): rsfs/1996:18, rsfs/1999:29, rsfs/2003:25, rsfs/2003:29,
                  skvfs/2004:37, skvfs/2004:38, skvfs/2005:28, skvfs/2005:29,
                  skvfs/2006:9, skvfs/2026:9, skvfs/2026:10, skvfs/2026:11, skvfs/2026:12
    extra (0)

Confirmed the 9 non-freshness gaps are true absences (no row under any label
variant, no download file under any name) with:

    select count(*) from documents where label=?   -- 0 for all 9

Two of the 9 are cited as amendment targets by documents we DO hold, so the
gap is visible in our own corpus, not just on the site:
- SKVFS 2005:16's title says "ändring i ... (SKVFS 2004:38) ..." — 2004:38 is missing.
- SKVFS 2006:25's title says "ändring i ... (SKVFS 2005:28) ..." — 2005:28 is missing.

### Repeal marking
Sample: site's detail page for skvfs/2009:3 reads "Dessa föreskrifter ...
upphävts genom SKVFS 2014:10." Our links table has exactly that row:

    select from_uri, predicate, to_uri from links
      where from_uri like '%skvfs/2014:10%' and predicate like '%upphav%';
    -- https://lagen.nu/skvfs/2014:10 | rpubl:upphaver | https://lagen.nu/skvfs/2009:3

Four records (skvfs/2011:24, 2012:2, 2017:12, 2017:13) are titled "Bekantgörande
i andra hand av författning som upphäver en författning i
Skatteverkets/Riksskatteverkets författningssamling" (a notice that some
regulation repeals some other regulation) but carry `metadata.upphaver == []`.
Checked the stored PDF (`pdftotext -layout`) and the site's own detail page for
skvfs/2011:24: neither names a target document anywhere in the text — the PDF
is a one-page masthead with no body. This is a shape the source itself does
not resolve from the record alone, not a parse bug we can point at. Reported,
not filed.

### Titles
    select label, title from documents where kind in ('skvfs','rsfs')
      and title like 'Doc %';
    -- 8 rows: skvfs/2004:19, 2007:17, 2011:24, 2012:2, 2017:12, 2017:13, 2024:26, 2024:27

All 8 are from the `skvfs-legacy` import (checked `files` source field in the
download record). For the 7 that have a stored PDF, `pdftotext` reads the real
title straight off page 1, e.g.:

    skvfs/2024:26 PDF: "Förordning\nom upphävande av Skatteverkets föreskrifter
      (SKVFS 2005:14) om förenklad faktura enligt mervärdesskattelagen (1994:200)"
    skvfs/2011:24 PDF: "Bekantgörande i andra hand av författning som
      upphäver en författning i Skatteverkets författningssamling;"

skvfs/2004:19 has no stored PDF at all (`files.regulation: null`), so its
title cannot be recovered locally; the site's own register gives it
("Skatteverkets föreskrifter om vilka guldmynt som är att anse som
investeringsguld;").

A ninth record, SKVFS 2018:21, has a title with the leading word missing:

    ours: "om upphävande av Skatteverkets föreskrifter (SKVFS 2004:19) ..."
    PDF:  "Förordning\nom upphävande av Skatteverkets föreskrifter (SKVFS 2004:19) ..."

All three "Förordning ..." records found (2018:21, 2024:26, 2024:27) are
government-ordinance repeal notices, a different masthead shape from the
ordinary "Skatteverkets föreskrifter" documents; the four "Bekantgörande ..."
records share a shape too. Title extraction drops or fails on both shapes.

A broader title diff (site register text vs. `documents.title` for all 578
shared basefiles) found many small mismatches, but spot checks on the
substantive-looking ones (skvfs/2021:1, 2009:16) showed the site register text
itself was wrong or garbled and our stored title, verified against the PDF,
was correct. Only 2018:21 stood up as our error.

### Consolidations
    grep -io "konsolid[a-zöä]*" skvfs-index.html detail-2011-24.html detail-2009-3.html
    -- no hits
Skatteverket does not publish a konsoliderad text; the detail page for an
amended regulation adds one sentence naming the amending/repealing
designations instead. We correctly hold 0 consolidation files
(`files.consolidation` empty in all 547 skvfs download records).

### Issue #76 cross-check (caution 2)
Four skvfs records are flagged: skvfs/2013:8, 2013:18, 2013:19, 2013:20 (all
`skvfs-legacy`). Read each stored PDF locally with `pdftotext -f 1 -l 2`:
all four print their own correct number in the masthead, e.g.
"SKVFS 2013: 18" — with a space after the colon. The #76 script's regex
(`YYYY:N`, no space) does not match that spaced form, so it reported only the
*other* law citations on the page (e.g. "2011:1244" from
skatteförfarandelagen) as "numbers printed" and missed the correct one. False
positive, caused by the masthead's own spacing, not a corpus defect. No new
issue filed; noted against #76.
