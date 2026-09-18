# FFS — Försvarsmakten

Verdict: DEFECT
Site list: 131 unique designations from 4 API-backed listing pages (entry: https://www.forsvarsmakten.se/om-forsvarsmakten/myndighetsinformation/dokument/)
We hold: 177 documents (ffs), newest FFS 2025:3; site newest FFS 2026:2
Missing: 1 — FFS 2026:2 (dated 2026-08-14 on the site; our last ffs download run was 2026-07-19, so this is a freshness gap, not a code defect)
Extra: 47 — all pre-2021 documents no longer on the "gällande" listings; 28/47 covered by an `rpubl:upphaver` link in our corpus, 19/47 not (see Repeal gaps)
Repeal gaps: 4 confirmed content-mismatch records (not a missing-flag issue): FFS 1987:24, FFS 1992:22, FFS 1994:32 hold the WRONG PDF (a 2017 repeal notice, not the original 1980s/1990s text); FFS 1999:1 holds an out-of-scope FIB document's PDF
Title defects: 2 — FFS 1982:20 and FFS 1999:1 have no title (title falls back to the bare designation); both are legacy-import records
Consolidations: site yes (1 confirmed: FFS 2019:3), we hold 0 in `files.consolidation` — and for FFS 2019:3 itself we captured the konsoliderad PDF under `files.regulation`, losing the as-published original entirely
Inherited: none (per assignment)
Issue: https://github.com/staffanm/ferenda/issues/48

## Evidence

### Harvester shape
`grep -n "FFS_API\|FFS_PAGE_IDS" -A20 ferenda/foreskrift/agencies.py`: `FFS_API` hits
`/api/episerver/v3.0/content/<pageId>?expand=PDFFilesArea`; `FFS_PAGE_IDS = ["2562",
"2563", "2564", "2565"]` (comment: 1978-94, 1995-2011, 2012-13, 2014-).

### 1. Enumerate
Fetched the entry page (1 request) with `ferenda.lib.net.request`. It lists exactly
four "Gällande FFS ..." categories (1978-1994, 1995-2011, 2012-2013, 2014-) plus a
separate "Gällande FAR" and "Gällande FIB" (both out of scope, confirmed by the
code's own comment) — the four PAGE_IDS still cover every FFS section the site
publishes; no missing category.

Fetched the four API pages (4 requests, `parse_json=True`):
  page 2562: 33 documentInfo entries
  page 2563: 41 entries
  page 2564: 3 entries
  page 2565: 57 entries
  total 134 raw rows, 0 FIB-named rows in the current listing, 133 match an
  "FFS YYYY:N" or bare "YYYY:N" pattern, 1 unmatched non-numeric row unrelated
  to FFS text — after normalizing to (year, lopnummer) there are 131 distinct
  designations. Budget used: 5 of 60 HTTP requests to the agency site.

### 2. Missing
`site_set - held` = {FFS 2026:2}. Confirmed real: API entry has
`date: 2026-08-14`, `preamble: "FFS 2026:2 Ändring av FFS 2019:3 om
grundläggande officersutbildning och tjänstegrader."`. Our downloaded/ tree for
ffs was last written 2026-07-19 (`stat` on `site/data/downloaded/foreskrift/ffs/*.json.br`),
before this document existed on the site — an ordinary incremental-harvest lag,
not a harvester code defect (the page-id list already covers page 2565 where
this document lives).

### 3. Extra
`held - site_set` = 47 documents, all with designations from 1982 to 2020. All
47 have `"source": "myndfs-legacy"` in their download record — a separate,
older import distinct from the live `ffs_enumerate` harvester. Cross-checked
each of the 47 against `links` rows with `predicate = 'rpubl:upphaver'` and
`to_uri like 'https://lagen.nu/ffs/%'` (71 such rows exist): 28/47 are the
target of a repeal link; 19/47 are not — see Repeal gaps.

### 4/Repeal gaps — confirmed content-mismatch, not a missing-flag defect
Filtered the 47 legacy records: does the PDF filename in the download record's
`url` actually contain the claimed (year, lopnummer)? 4 do not:

| basefile     | claimed content                                    | url filename (actual content)                    |
|--------------|-----------------------------------------------------|----------------------------------------------------|
| ffs/1987:24  | FFS 1987:24 (1987 Överbefälhavaren regulation)      | `ffs-2017-1.pdf` — is the *repeal notice* FFS 2017:1 ("Om upphävande av FFS 1987:24") |
| ffs/1994:32  | FFS 1994:32 (1994 regulation)                       | `ffs-2017-2.pdf` — is the *repeal notice* FFS 2017:2 ("Om upphävande av FFS 1994:32") |
| ffs/1992:22  | FFS 1992:22 (Försvarets radioanstalt, hörselundersökning) | `ffs--2017-5.pdf` — is the *repeal ordinance* FFS 2017:5, signed by Peter Hultqvist (confirmed: artifact structure is a bare signature block, no substantive text) |
| ffs/1999:1   | FFS 1999:1                                          | `fib-2017-6.pdf` — an FIB document, a series explicitly out of scope for this fs |

We separately hold FFS 2017:1, FFS 2017:2 and FFS 2017:5 correctly, with the
same repeal content and correct `upphaver` targets. So the *repealing*
documents are present and correctly parsed — the defect is that the legacy
import wrote the repealing document's own PDF a second time under the
repealed document's basefile, instead of the original 1980s/90s regulation
text (which is not present anywhere in the corpus), and in one case (1999:1)
under an out-of-scope FIB PDF entirely. Verified by reading
`site/data/downloaded/foreskrift/ffs/ffs-{1987-24,1994-32,1992-22,1999-1}.json`
and the matching artifact JSON (`site/data/artifact/foreskrift/ffs/*.json.br`).

The remaining 15/19 "uncovered" extras were not examined PDF-by-PDF (would
need fetching the agency site again); they may be legitimately time-limited
or superseded without an explicit repeal act. Not filed as a defect.

### 5. Titles
Compared `documents.title` (ours) against the API `preamble` for 10 sampled
designations (FFS 2019:3, 2021:2, 2013:3, 2017:5, 2011:1, 2011:2, 2006:2,
2012:4, 2023:15, 2025:3) — all 10 match, modulo our normal stripping of the
leading "FFS YYYY:N " designation. Two titles are junk: FFS 1982:20 and
FFS 1999:1 fall back to the bare designation because `metadata.title` is
`null` in the artifact — both are `myndfs-legacy` records (1999:1 is also
the FIB-content mismatch above).

### 6. Consolidations
`grep` over all 177 download records: `files.consolidation` and
`files.amendment` are empty on every single one (0/177). The current API
listing carries at least one explicit konsoliderad text: "FFS 2019:03 ...
- konsoliderad." (`ffs-2019-03-konsoliderad.pdf`), which precedes the plain
"FFS 2019:3" row (`ffs-2019-3.pdf`) in page 2565's JSON. Our `ffs_enumerate`
dedups by (year, lopnummer) and keeps whichever row it sees first — here the
konsoliderad row — so `ffs/2019:3` in our corpus holds the konsoliderad PDF
under `files.regulation`, and the as-published original is not held at all
under any basefile. Confirmed by reading
`site/data/downloaded/foreskrift/ffs/ffs-2019-3.json`.

A second instance of the same "first-wins" ordering flaw: the JSON also lists
"FFS 2021:2 rättelse" (`ffs-2021-02-1.pdf`) immediately before "FFS 2021:2"
(`ffs-2021-2.pdf`); our `ffs/2021:2` holds the rättelse (correction sheet),
whose own text says "Detta rättelseblad ersätter s. 8 av tidigare utgivna FFS
2021:2" — i.e. we hold the erratum, not the base regulation it corrects.
Confirmed by reading `site/data/artifact/foreskrift/ffs/2021-2.json`.

A third duplicate pair, FFS 2013:3 listed on both the 2012-2013 and 2014-
period pages, is benign — both point at plausibly the same still-current text
under different period folders; not examined further.

### 7. Inherited series
None per assignment. No check performed.

### 8. Freshness
Site newest: FFS 2026:2 (2026-08-14). We hold newest FFS 2025:3. Gap is
explained by the download tree's last-write date (2026-07-19), pre-dating
the new document — an ordinary incremental-harvest lag.
