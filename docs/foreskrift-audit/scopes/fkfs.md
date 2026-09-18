# FKFS — Försäkringskassans författningssamling, Försäkringskassan
Verdict: DEFECT
Site list: 543 base-regulation designations (161 FKFS, 382 RFFS) from 1 page —
the whole register loads as one embedded JSON corpus
(https://lagrummet.forsakringskassan.se/foreskrifter). The same corpus also
carries 605 more designations (182 FKFS, 423 RFFS) marked `isChangeDocument`
— standalone amending/repealing regulations with their own official number.
We hold: 161 fkfs + 382 rffs = 543 documents, matching the site's base-regulation
count exactly. Newest FKFS base regulation: FKFS 2025:3 (site and ours agree).
Site newest FKFS designation of any kind: FKFS 2026:8 (a change document).
Missing: 0 base regulations. But 605 change-document designations (identifier,
title, PDF URL, all present on the site) never enter the corpus in any form —
no basefile, no artifact, no download. See Repeal gaps and Evidence.
Extra: 0 — no document in our records is absent from the site's list.
Repeal gaps: 61 of 169 site-flagged revoked base regulations lack an
`rpubl:upphaver` edge. 53 of those name at least one change-document
designation as a candidate repeal (all 53 confirmed missing from the
corpus — a harvest defect). 8 name no change document at all (self-expiry
or an administrative-reform cause outside the repeal model — not a
defect). Examples:
| base regulation | site's repealing document | in our corpus? |
|---|---|---|
| FKFS 2011:9 | FKFS 2013:6 (new regulation, same subject) | no — never harvested |
| RFFS 2003:11 | FKFS 2012:1 "om upphävande av ... (RFFS 2003:11)" | no — never harvested |
| RFFS 1998:25 | RFFS 1998:43 "om ändring i ... (RFFS 1998:25)" | no — never harvested |
| FKFS 2022:1 | none — no change document recorded at all | n/a — not a defect |
Title defects: 0 of 12 sampled — every title matches the site's `titel`
field byte-for-byte (aside from a `\r\n` the site appends that we already trim).
Consolidations: site yes, we hold 107/160 fkfs and 374/382 rffs download
records with a `files.consolidation` entry. No defect.
Inherited: rffs — 382 RFFS documents sit under the `rffs` slug, none
renumbered as fkfs; site's own RFFS designation list matches ours exactly
(0 missing, 0 extra). The predecessor series is otherwise fine.
Issue: https://github.com/staffanm/ferenda/issues/49

## Evidence

Repo: `ferenda/foreskrift/agencies.py` lines 2237-2305 (`fkfs_enumerate`,
`fkfs_resolve`, `FKFS` Agency) and `ferenda/foreskrift/harvest.py`
`resolve_direct` (lines 390-427).

1. Fetch (1 request, ferenda's own `request()`/`make_session(BROWSER_UA)`):
   `GET https://lagrummet.forsakringskassan.se/foreskrifter` → 200, 2,086,458
   bytes. Extracted with the harvester's own regex
   `RE_FKFS_DOC = re.compile(r'\{"nummer":"[^"]*".*?"samling":[^{}]*\}')`:
   1691 raw JSON objects, all parse cleanly.
   - `isChangeDocument: false` (base regulations): 1086 raw, 543 unique after
     the harvester's own `(fs, arsutgava, lopnummer)` de-dupe — 322 FKFS /
     764 RFFS raw collapse to 161 FKFS / 382 RFFS unique (each duplicate is
     the identical object listed twice on the page, not two distinct
     documents sharing a number — verified: same `id` both times).
   - `isChangeDocument: true` (amendments/repeal notices): 605 unique raw
     documents — 182 FKFS / 423 RFFS. Confirmed these never coincide with a
     base-regulation key: `missing_entirely = {k for k in change_keys if k
     not in base_keys}` → 605 (all of them).

2. Catalog check (`site/data/catalog.sqlite`):
   ```
   select kind, count(*) from documents where kind in ('fkfs','rffs') group by kind
   -> ('fkfs', 161), ('rffs', 382)
   ```
   Diffing the 543 site base-regulation keys against
   `(kind, year, lopnummer)` from `documents` gives 0 missing, 0 extra —
   enumeration (checks 1-3) is exact.

3. For `FKFS 2012:1`, `FKFS 2013:6`, `RFFS 1998:43`, `RFFS 2004:32` (all
   `isChangeDocument: true` on the site, all with their own diarienummer,
   title, and PDF): `select uri, kind from documents where label=?` → `[]`
   for every one. `grep`-confirmed in `ferenda/foreskrift/harvest.py` that
   `resolve_direct` only calls `fetch()` (which downloads the PDF) for
   `regulation` and `consolidation`; the `amendment` list is built straight
   from `extra.get("amendments", [])` with no fetch call, so the PDF is
   never even downloaded — `ls site/data/downloaded/foreskrift/fkfs/ | grep
   ^fkfs-2011-9` shows `-regulation.pdf` and `-consolidation-0.pdf` but no
   amendment PDF, even though `fkfs-2011-9.json.br`'s
   `files.amendment` lists FKFS 2013:6's URL.

4. Repeal check, using `ferenda.lib.catalog.upphaver_targets(con)` (the
   documented, correct way to test repeal coverage): of 169 site-flagged
   `isRevoked: true` base regulations, 61 target URIs are absent from
   `upphaver_targets`. Each raw JSON entry with `isChangeDocument: true`
   already carries `baseDocumentNumber`, so the base-to-amendment mapping
   needs no extra requests — built straight from the one index-page fetch.
   Joining the 61 gaps against that map: 53 name at least one change-document
   designation (a real candidate repeal — one of the 605 from step 1,
   confirmed absent from the catalog per step 3); 8 name none at all (no
   change document was ever filed under that base regulation, so nothing
   could repeal it — these read as self-expiry or an administrative-reform
   cause, correctly outside the repeal model, not a defect). Fetched 5
   detail pages (`/foreskrifter/dokument?id=<id>`, 2 s apart, same
   `request()` helper) as a spot check of the mapping and to read two
   family JSONs' full amendment metadata; all 5 agreed with the index-page
   mapping.
   Total requests used: 1 (index) + 5 (detail pages) = 6 of the 60 budget.

5. Titles: 12 documents sampled at random (`random.seed(42)`), compared
   `documents.title` against the site's `titel` field — 12/12 match (the
   site appends a trailing `\r\n` to a few, already stripped in ours).

6. Consolidations: `json.loads(compress.read_text(...))["files"]["consolidation"]`
   over every fkfs/rffs download record: fkfs 107/160 non-empty, rffs
   374/382 non-empty.

7. Inherited series (rffs): all 382 RFFS site designations map 1:1 to
   `kind='rffs'` catalog rows; none appear under `kind='fkfs'`.

8. Freshness: newest FKFS *base* regulation on both site and in our corpus
   is FKFS 2025:3. The site's newest FKFS designation overall is FKFS
   2026:8, a change document — invisible to our harvest for the same
   structural reason as the repeal gaps above.
