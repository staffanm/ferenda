# SGUFS — SGU-FS, Sveriges geologiska undersökning

Verdict: OK
Site list: 6 grundföreskrifter (+2 ändringsföreskrifter, +2 konsoliderade versions) from 1 page (https://www.sgu.se/om-sgu/verksamhet/foreskrifter/)
We hold: 6 documents (sgufs), newest SGU-FS 2024:2; site newest base regulation SGU-FS 2024:2 (newest amendment SGU-FS 2024:3, correctly referenced, not a separate document)
Missing: 0
Extra: 0
Repeal gaps: 0 (no document on the site is shown as upphävd/struck through)
Title defects: 0 (checked all 6 held documents against the site's anchor text)
Consolidations: site yes (2), we hold 2
Inherited: none (no predecessor series assigned)
Issue: none

## Evidence

### 1. Enumerate

Fetched the single entry page (no pagination, no sub-pages):

    ferenda.lib.net.request(session, "GET", "https://www.sgu.se/om-sgu/verksamhet/foreskrifter/")
    -> 200, 75826 bytes

`div.main-content` holds 9 `<p>` elements. Grouping the PDF anchors the way
`ferenda/foreskrift/agencies.py:sgu_enumerate` does (base regulation vs.
"ändring" vs. "konsolider") gives exactly 6 grundföreskrifter:

    SGU-FS 2015:1  behörighet att utföra gruvmätning
    SGU-FS 2017:1  redovisning av förvaltningsplaner och åtgärdsprogram för grundvatten
    SGU-FS 2020:1  gruv- och borrhålskartor
    SGU-FS 2023:1  kartläggning, riskbedömning och klassificering av status för grundvatten
    SGU-FS 2023:2  miljökvalitetsnormer för grundvatten
    SGU-FS 2024:2  övervakning av grundvatten

plus 2 ändringsföreskrifter (SGU-FS 2024:1 amends 2023:1, SGU-FS 2024:3
amends 2015:1) and 2 konsoliderade versions (for 2015:1 and 2023:1).

Cross-checked against SGU's own "regelförteckning" (a PDF register of all
regulations in force, dated January 2025, explicitly excluding repealed
ones): same 6 base designations plus the same 2 amendments. No designation
appears on the site or in the register that we do not hold.

### 2/3. Missing / extra

    sqlite3.connect("file:site/data/catalog.sqlite?mode=ro", uri=True)
    select uri, label, title from documents where kind='sgufs'
    -> 6 rows: sgufs/2015:1, 2017:1, 2020:1, 2023:1, 2023:2, 2024:2

Every one of the 6 site designations has a matching catalog row, and every
catalog row has a matching site designation. The harvest reaches the whole
samling; the "only 6 documents" count from the assignment brief is simply
the true size of SGU's live samling, not a harvest gap.

SGU-FS 2024:1 and 2024:3 (the two ändringsföreskrifter) are not minted as
their own catalog documents. This is the documented design for
`resolve_direct` sources whose register does not present the amendment as a
full grundföreskrift-equivalent page (`ferenda/foreskrift/model.py:83-93`,
the `Amendment` dataclass: "Captured as a reference (identity + its own
PDF)"). Both amendments are recorded correctly as references on their base
document's `metadata.andradAv` / `amendments` list, with a synthetic
`https://lagen.nu/sgufs/2024:N` URI and the agency's own PDF URL:

    artifact/foreskrift/sgufs/2015-1.json -> amendments: [{"identifier":
      "SGU-FS 2024:3", "uri": "https://lagen.nu/sgufs/2024:3", "url":
      "https://resource.sgu.se/.../sgu-fs-2024-3.pdf"}]
    artifact/foreskrift/sgufs/2023-1.json -> amendments: [{"identifier":
      "SGU-FS 2024:1", ...}]

`ferenda/foreskrift/render.py:_andrad_links` checks `site.has(uri)` before
linking internally, so this renders as expected (no broken lagen.nu link).
Not a defect.

### 4. Repeal marking

The entry page lists only in-force text; no document is struck through or
marked upphävd. No "upphävda föreskrifter" archive sub-page exists (checked
every link on the page; none mentions "upphävd", "arkiv" or "äldre").

One open question, noted but not a corpus defect: SGU's own January 2025
regelförteckning — which by its own header text excludes repealed
regulations — omits SGU-FS 2023:2, while the live entry page (our harvest
source) still lists it as current. This is an inconsistency between two of
SGU's own publications, not evidence our harvest missed a repeal notice: no
repealing document for 2023:2 appears anywhere on the site, and a targeted
web search found no report that 2023:2 was repealed. Our harvest correctly
mirrors the entry page, which is the authoritative index per the agency
config.

### 5. Titles

Compared all 6 held titles against the site's anchor text (prefix
"SGU-FS <year>:<lop> " stripped by the parser, rest verbatim):

| designation | our title | site text | verdict |
|---|---|---|---|
| 2015:1 | "...om behörighet att utföra gruvmätning" | same | OK |
| 2017:1 | "...om redovisning av förvaltningsplaner och åtgärdsprogram för grundvatten" | same | OK |
| 2020:1 | "...om gruv- och borrhålskartor" | same | OK |
| 2023:1 | "...om kartläggning, riskbedömning och klassificering av status för grundvatten" | same | OK |
| 2023:2 | "...om miljökvalitetsnormer för grundvatten" | same | OK |
| 2024:2 | "...om övervakning av grundvatten" | same | OK |

No junk: no file sizes, no truncation, no PDF filenames, no HTML entities,
no designation repeated inside the title. Only 6 documents exist in the
scope, so 10 could not be sampled.

### 6. Consolidations

Site publishes 2 konsoliderade versions (2015:1 t.o.m. 2024:3; 2023:1
t.o.m. 2024:1). Our download records carry both:

    site/data/downloaded/foreskrift/sgufs/sgufs-2015-1-consolidation-0.pdf
    site/data/downloaded/foreskrift/sgufs/sgufs-2023-1-consolidation-0.pdf

Matches exactly. Not a defect.

### 7. Inherited series

None assigned for sgufs.

### 8. Freshness

Site's newest base regulation: SGU-FS 2024:2 (2024-12-02). We hold SGU-FS
2024:2 as our newest document. Matches. (The register/site's newest overall
designation, the ändringsföreskrift SGU-FS 2024:3, is correctly captured as
a reference per check 2/3.)

### #76 cross-check

`gh issue view 76 --repo staffanm/ferenda` body does not mention sgufs. No
overlap to verify.

### Requests used

2 requests to sgu.se (the entry page, the regelförteckning PDF), well under
the 60-request budget. No block encountered.
