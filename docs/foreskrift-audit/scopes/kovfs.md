# KOVFS — Konsumentverkets författningssamling, Konsumentverket

Verdict: DEFECT

Site list: 26 designations from 1 page (https://publikationer-api.konsumentverket.se/api/products?search=KOVFS&pageIndex=1&pageSize=50&sort=title, `count=26`, `pageSize=50` so one page covers all). 25 are real regulations; 1 ("Samtliga publikationer i Konsumentverkets författningssamling (KOVFS)", id 269) is an index/finding-aid PDF, not a regulation.

We hold: 49 documents (kovfs), newest KOVFS 2025:4 (2025-06-23); site (live search) newest KOVFS 2025:4. Freshness OK.

Missing: 1 — the real KOVFS 2021:1, "... om information om enhetspriser för drivmedel". It exists (referenced by name in KOVFS 2022:2 and KOVFS 2025:3's own text) but is not in our corpus. Its identifier slot is occupied by a mis-harvested document (see Issue).

Extra: 24 — all 24 documents we hold that are not in the current 26-item search result are historical designations (1993:9 .. 2020:2) the shop has since delisted. All are older than the site's live catalog window; this is the source curating down to a "still relevant" subset, not a corpus defect (rule: extra can be legitimate).

Repeal gaps: 0 — checked every `(upphävd)`/`(upphävd och ersatt av …)` marked title we hold (1993:9, 2010:4, 2011:3, 2012:3) against `links.rpubl:upphaver`. Each has its correct repealing document and target in our corpus (1993:9←2011:3, 2011:3←2011:5, 2010:1←2012:3).

Title defects: 8 — see Issue. Examples:
- KOVFS 2009:10: ours is 1567 characters of running body text; theirs (register) is "Konsumentverkets föreskrifter om kriterier för EG:s miljömärkning av campingplatstjänster".
- KOVFS 2015:2: ours drops the start of the sentence and appends unrelated body text ("... beslutade den 2 december 2015 Allmänna reklamationsnämnden föreskriver att KOVFS 2009:1 ska upphöra att gälla vid utgången av 2015."); theirs is "Föreskrift om upphävande av Allmänna reklamationsnämndens föreskrifter (KOVFS 2009:1) om konsumenttvister som inte prövas av nämnden".

Consolidations: site no (KOVFS publishes no konsoliderad versions; "hasVersions" in the API means a companion beslutspromemoria, not a consolidated text), we hold 0 of 49 — consistent, not a defect.

Inherited: none (per assignment).

Issue: https://github.com/staffanm/ferenda/issues/66

## Evidence

### 1. Enumerate
```
GET https://publikationer-api.konsumentverket.se/api/products?search=KOVFS&pageIndex=1&pageSize=50&sort=title
-> {"pageIndex":1,"pageSize":50,"count":26,"data":[26 items]}
```
25 items carry a real "KOVFS YYYY:N ..." title; one (id 269) is titled "Samtliga publikationer i Konsumentverkets författningssamling (KOVFS)" and downloads
`https://publikationer.konsumentverket.se/downloads/kovfs-alla-publikationer-2021-01-konsumentverket.pdf` — a 29-page "Förteckning över alla publikationer i Konsumentverkets författningssamling (KOVFS)" dated 2021-01-29, i.e. an index of every KOVFS designation back to 1977, not a regulation.

Our corpus already holds this exact PDF, downloaded and mis-parsed as basefile `kovfs/2021:1`:
```
sqlite3 site/data/catalog.sqlite (ro): select label, title from documents where label='KOVFS 2021:1';
-> KOVFS 2021:1 | Samtliga publikationer i Konsumentverkets författningssamling (KOVFS)
site/data/downloaded/foreskrift/kovfs/kovfs-2021-1.json.br: files.regulation.url =
  https://publikationer.konsumentverket.se/downloads/kovfs-alla-publikationer-2021-01-konsumentverket.pdf
```

### 2. Root cause (harvest)
`ferenda/foreskrift/harvest.py: ref()` derives the designation for a `direct` agency from the
PDF filename when the title text carries no "KOVFS YYYY:N" (`RE_SLUG_NUMBER`, a bare
`YYYY[-_ ]NN` pattern). The filename `kovfs-alla-publikationer-2021-01-konsumentverket.pdf`
encodes the *publication date* of the index PDF ("2021-01" = January 2021), not a designation.
`RE_SLUG_NUMBER` matches it anyway and mints the identifier "KOVFS 2021:1" for a document that
carries no such designation at all.

### 3. Downstream damage (parse)
The artifact's `metadata.upphaver` for `kovfs/2021:1` lists 1979:10 and 1985:5:
```
sqlite3: select from_uri, to_uri from links where from_uri like '%kovfs/2021:1' and predicate='rpubl:upphaver';
-> kovfs/2021:1 -> kovfs/1979:10
-> kovfs/2021:1 -> kovfs/1985:5
```
Neither target exists in our corpus. The register PDF's own table shows why: both are ordinary
rows in the historical listing, each already repealed decades ago by an unrelated regulation:
```
1979:10   Riktlinjer för sugnappars ...   upphävd 2004-10-31
1985:5    Riktlinjer för beklädnads- ...  upphävd 2009-06-30
```
The parser read isolated designation mentions out of the index table's running text and recorded
them as repeal actions taken *by* this "document" — which is not a regulation and repeals nothing.

### 4. Missing document masked by the same identifier
`KOVFS 2022:2` (which we hold correctly) reads: "... ändring i Konsumentverkets föreskrifter
(KOVFS 2021:1) om information om enhetspriser för drivmedel". `KOVFS 2025:3`'s own product
description (`GET .../api/products/454`) confirms: "Konsumentverkets föreskrifter 2021:1 och
2022:2 upphör [2025-06-01] att gälla" for the drivmedel-price-labelling regulation. This real
KOVFS 2021:1 is not in our corpus at all — its basefile is occupied by the mis-identified index
PDF instead.

### 5. Title defects
```
sqlite3: select label, length(title) from documents where kind='kovfs' order by length(title) desc;
-> KOVFS 2009:15  1708
-> KOVFS 2009:10  1567
-> KOVFS 2009:11  1529
-> KOVFS 2009:12  1468
-> KOVFS 2009:9   1439
-> KOVFS 2009:14  1409
-> KOVFS 2009:13  1390
-> KOVFS 2015:2    227
```
`site/data/downloaded/foreskrift/kovfs/kovfs-2009-*.json.br` shows the body-text title was
already present in the *download* record (not introduced later at parse), i.e. `ref.title` was
already this long at harvest time. Sibling `KOVFS 2009:16`, harvested the same batch, same day,
same URL pattern, has a clean 103-character title, so the fault is not systemic to the batch —
these 8 records individually carry a body dump where a short title belongs.

### 6. Consolidations
```
for each of 49 download records: files.consolidation == [] (0 of 49 non-empty)
```
No evidence in the API ("hasVersions" only ever pairs a regulation with its own
"Beslutspromemoria", never a second in-force text) that Konsumentverket publishes konsoliderade
versions of KOVFS regulations.

### Requests used
~13 GETs to publikationer-api.konsumentverket.se / publikationer.konsumentverket.se, well under
the 60-request budget. www.konsumentverket.se/om-konsumentverket/lagar-och-regler/ returned 404
(page moved/renamed); the API's own search result already gave a complete, single-page listing,
and the index PDF supplied additionally covers the same 1977-2020 designations independently.
