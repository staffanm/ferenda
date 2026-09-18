# PFS — Pensionsmyndighetens föreskrifter, Pensionsmyndigheten
Verdict: DEFECT
Site list: 115 PFS designations from 1 page (https://www.pensionsmyndigheten.se/om-pensionsmyndigheten/allmanna-handlingar/lagar-och-regler)
We hold: 115 documents (pfs), newest PFS 2026:3; site newest PFS 2026:3
Missing: 0 PFS documents. But 7 predecessor (RFFS/FKFS) base regulations that
  the site still serves as PDFs are held under neither `rffs` nor `fkfs`.
Extra: 0
Repeal gaps: 9 of 14 `rpubl:upphaver` targets from pfs documents point at a
  URI absent from `documents` — 7 recoverable from this site, 2 unrecoverable
  from any source checked (see Evidence).
Title defects: 0 of 15 sampled
Consolidations: site no, we hold 0 (matches)
Inherited: RFFS/FKFS listed on the pfs entry page (predecessor
  Riksförsäkringsverket/Försäkringskassan regulations Pensionsmyndigheten
  still administers); no PPMFS series exists anywhere — PPM's own decisions
  were published under RFFS/FKFS numbers, not a separate PPMFS samling.
  7 base regulations from that inherited set are entirely missing from the
  corpus (see below); the rest (40 RFFS, 15 FKFS base regs sampled) are
  correctly excluded from pfs and held under rffs/fkfs.
Issue: https://github.com/staffanm/ferenda/issues/78

## Evidence

### 1-3. Enumeration, missing, extra (PFS itself)

Fetched the entry page once (`ferenda.lib.net.request`, GET, 200, 299525
bytes, no pagination markup found). Extracted every `.pdf` anchor matching
the harvester's own `link_select` pattern
(`a[href*="reskrifter/"][href$=".pdf"]`), split by which agency issued the
document (own text says "Pensionsmyndighetens föreskrifter" / "PFS" vs.
"Riksförsäkringsverket" / "Försäkringskassan" / "RFFS" / "FKFS" — the same
signal `PFS.params["skip_re"]` uses):

- 115 PFS-attributed PDF links, 115 unique `PFS YYYY:N` designations.
- `SELECT label FROM documents WHERE kind='pfs'` returns 115 rows.
- Set difference both ways: empty. 0 missing, 0 extra.

### 4. Repeal marking

```
SELECT from_uri, to_uri FROM links
WHERE predicate='rpubl:upphaver' AND from_uri LIKE '%/pfs/%';
```
returns 14 distinct target URIs. Checked each against `documents.uri`:

| target (repealed by a pfs document) | in `documents`? |
|---|---|
| fkfs/2006:2 | no |
| fkfs/2008:25 | no |
| fkfs/2009:14 | no |
| fkfs/2009:15 | no |
| fkfs/2009:16 | no |
| fkfs/2009:17 | no |
| rffs/2000:5 | no |
| rffs/2000:24 | no |
| rffs/2002:33 | no |
| (5 others) | yes |

For fkfs/2009:14..17 and rffs/2000:5, 2000:24, 2002:33 (7 of the 9): the pfs
entry page itself still serves the PDF, as a base regulation (not an
amendment — e.g. "Försäkringskassans föreskrifter (FKFS 2009:14) om
beräkning av bostadskostnad i ärenden om bostadstillägg till pensionärer
m.fl.", not "om ändring i ..."), for example:

```
/content/dam/.../upphävda-föreskrifter/2000/2000_05.pdf   (RFFS 2000:5)
/content/dam/.../upphävda-föreskrifter/2000/2000_24.pdf   (RFFS 2000:24)
/content/dam/.../upphävda-föreskrifter/2009/2009_14.pdf   (FKFS 2009:14)
```

Checked whether Försäkringskassan's own live register still lists them
(fetched `https://lagrummet.forsakringskassan.se/foreskrifter` once, parsed
the same embedded JSON the `fkfs` agency reads): RFFS 2000's `lopnummer`
sequence is `1,2,3,4,6,7,8,...` (5 absent) and `...,23,25` (24 absent); FKFS
2009's sequence omits 1, 11, 12, 14, 15, 16, 17, 22, 24. None of the 7
appear under any `forfattningssamling`/`arsutgava`/`lopnummer` combination in
that corpus — Försäkringskassan's register has dropped them entirely, most
likely because their subject (premium pension, benefit payment) transferred
to Pensionsmyndigheten in 2010. The `fkfs` agency (issue #49's scope) can
never find them because its one source no longer lists them. The `pfs`
agency's `skip_re` drops every RFFS/FKFS-labelled anchor unconditionally,
on the (here false) assumption that the fkfs agency is a complete source for
that material. Net effect: 7 base regulations exist as live, working PDFs
on Pensionsmyndigheten's own site and are held under neither `rffs` nor
`fkfs` — a harvest gap distinct from #49 (which is about amendment
documents FK's own register does list but the fkfs harvester skips).

fkfs/2006:2 and fkfs/2008:25 (the remaining 2 of 9): not found on the pfs
entry page under any designation, and not found in Försäkringskassan's
live register either. No recoverable source identified in this audit.

### 5. Titles

Sampled the first 15 PFS designations (2010:1 through 2011:5) and diffed
`documents.title` against the site's own link text (size suffix stripped).
All 15 match character-for-character. No junk, no truncation, no repeated
designation, no HTML entities.

### 6. Consolidations

No mention of "konsoliderad" or "sammanställd" text tied to föreskrifter on
the entry page. `site/data/downloaded/foreskrift/pfs/pfs-*.json.br`: 115
records, 0 carry `files.consolidation`. Site and corpus agree: no
consolidations published for PFS.

### 7. Inherited series

No PPMFS series exists. The entry page's own "Upphävda föreskrifter"
section states: "Föreskrifter som beslutats av PPM och Försäkringskassan
(inklusive Riksförsäkringsverket) gäller även efter Pensionsmyndighetens
inrättande 2010" — i.e. Premiepensionsmyndigheten's own decisions were
published under RFFS or FKFS numbers (e.g. "Premiepensionsmyndighetens
föreskrifter (RFFS 2000:5) om val och byte av fond...", "...(FKFS 2009:11)
om ändring i föreskrifterna (RFFS 2000:5)..."), never a distinct "PPMFS"
samling. `SELECT count(*) FROM documents WHERE kind='ppmfs'` returns 0, and
`ppmfs` is not a registered `fs` anywhere in `ferenda/foreskrift/agencies.py`
— correct, since the series never existed.

Extracted every RFFS/FKFS-attributed PDF anchor from the pfs entry page
(both the always-visible list and the already-expanded "Upphävda
föreskrifter" section — this is server-rendered HTML, no JS needed, so the
`indexed_enumerate`/`fkfs_enumerate` harvesters see the same content this
audit fetched): 48 RFFS + 28 FKFS distinct own-designations. Checked each
against `documents` under kind `rffs`/`fkfs`:

- RFFS: 40 of 48 present, 8 missing (5 amendment documents, out of scope
  per issue #49's design; 3 base regulations: 2000:5, 2000:24, 2002:33).
- FKFS: 15 of 28 present, 13 missing (9 amendment documents, out of scope
  per issue #49; 4 base regulations: 2009:14, 2009:15, 2009:16, 2009:17).

The 7 base regulations are the harvest gap filed above. Every other RFFS
designation held sits under `rffs`, never renumbered into `pfs` or `fkfs`.

### What passed

- Checks 1-3 (enumeration/missing/extra) for PFS itself: exact match.
- Check 5 (titles): 15/15 sampled match exactly.
- Check 6 (consolidations): site and corpus agree there are none.
- Check 8 (freshness): PFS 2026:3 (2026-09-10) is newest on both site and
  in the corpus.
- No PPMFS series exists to check for renumbering — confirmed a real
  negative, not an oversight.
