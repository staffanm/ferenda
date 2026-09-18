# SISFS — SiSFS, Statens institutionsstyrelse
Verdict: DEFECT
Site list: 33 designations (30 SiSFS + 3 SiSUVFS) from 1 page (https://www.stat-inst.se/om-sis/lagar-forordningar-forfattningar/sis-forfattningssamling/)
We hold: 30 documents (sisfs) + 3 documents (sisuvfs), newest SiSFS 2025:2 / SiSUVFS 2025:1; site newest the same
Missing: 0
Extra: 0
Repeal gaps: 0 (site shows no repeal markers; no SiSFS/SiSUVFS document cites another for repeal)
Title defects: 0 — 33/33 catalog titles match the site's link text exactly
Consolidations: site yes (1, SiSUVFS 2024:1), we hold 0 — DEFECT, see issue
Inherited: none registered. `sisuvfs` is not a predecessor-agency series; it is a second, current samling
  from the same agency (SIS), published on the same page, correctly routed to its own slug by
  `fs_from_designation`. No registry gap.
Issue: https://github.com/staffanm/ferenda/issues/87

Also confirmed (not filed, cites #54): `RE_FS_REF` in `ferenda/foreskrift/parse.py` is case-sensitive
and misses the mixed-case "SiSUVFS" designation. 1 confirmed instance: SiSUVFS 2024:2's masthead cites
"(SiSUVFS 2024:1)" as the act it amends, but `metadata.andrar` is empty and no `rpubl:andrar` link
exists in the catalog. Same root cause as #54 (FoHMFS), not a new issue.

## Evidence

### Enumeration
One static HTML page, 49 `<a href$=".pdf">` links, fetched via
`ferenda.lib.net.request`/`make_session`. 33 carry a "SiSFS ####:#" or
"SiSUVFS ####:#" designation in the link text; 1 is the "Förteckning" index
PDF (dropped by `skip_re`); 1 is "Konsoliderad version av SiSUVFS 2024:1"
(dropped by dedup, see issue #87); 14 are undesignated "Övriga vårdavgifter"
tables for 2020-2026 (dropped by `ref()`, no number to key on — matches the
code comment in `agencies.py` above `SISFS`).

### Missing / extra (catalog query)
```
sqlite3.connect("file:site/data/catalog.sqlite?mode=ro", uri=True)
select kind, count(*), min(label), max(label) from documents
  where kind in ('sisfs','sisuvfs') group by kind
-> sisfs 30 'SiSFS 2012:1' 'SiSFS 2025:2'
-> sisuvfs 3 'SiSUVFS 2024:1' 'SiSUVFS 2025:1'
```
All 33 site designations map 1:1 onto these 33 catalog rows by label; no
extras on either side.

### Titles
Parsed the 33 site link texts into `{designation: title}` and diffed against
`documents.title` for every sisfs/sisuvfs row. 0 mismatches.

### Repeal marking
The site page carries no "upphävd" / "upphört att gälla" / strikethrough
markup anywhere (`grep -i` over the fetched HTML: 0 hits). Read every
artifact's extracted text for `upphäv`/`upphör`/a cross-designation
reference: only one document — SiSFS 2025:1 — carries a self-declared sunset
line ("Upphävs den 1 januari 2026") with no named repealing act, which is not
a corpus defect under the repeal model (no other document is expected to
carry an `upphaver` link for it). No SiSFS or SiSUVFS document has
`documents.upphavande=1`.

### Consolidations (issue #87)
```
.venv/bin/python -c "
from ferenda.lib.compress import read_text
import json
d = json.loads(read_text('site/data/downloaded/foreskrift/sisuvfs/sisuvfs-2024-1.json'))
print(d['files'])
"
-> {'regulation': {...sis-foreskrifter-om-anvisning-av-plats-for-unga...},
    'consolidation': [], 'amendment': [], 'memo': [], 'attachment': []}
```
The site's distinct "Konsoliderad version av SiSUVFS 2024:1" PDF
(a different URL) is never fetched. Root cause: `indexed_enumerate` +
`resolve_direct`'s `ref()` helper dedups purely on the parsed
`(fs, year, lopnummer)` basefile; the "Konsoliderad version av SiSUVFS 2024:1"
link text parses to the same basefile as the base regulation link listed
above it, so it is dropped as a duplicate rather than routed to
`files.consolidation`. The 30 SiSFS regulations have no konsoliderad PDFs on
the site at all, so their empty `files.consolidation` is correct.

### RE_FS_REF case-sensitivity (cites #54)
```
.venv/bin/python -c "
from ferenda.foreskrift import parse
print(parse.RE_FS_REF.findall('... föreskrifter (SiSUVFS 2024:1) om anvisning ...'))
"
-> []
```
`pdftotext -f 1 -l 1 -layout site/data/downloaded/foreskrift/sisuvfs/sisuvfs-2024-2-regulation.pdf -`
shows the clean masthead: "om ändring i Statens institutionsstyrelses
föreskrifter (SiSUVFS 2024:1) om anvisning ...". The artifact's
`metadata.andrar` is `[]`, and the catalog has no `rpubl:andrar` row from
`sisuvfs/2024:2`:
```
con.execute("select predicate, to_uri from links where from_uri=?",
            ("https://lagen.nu/sisuvfs/2024:2",)).fetchall()
-> [('dcterms:references', '.../1990:52#P15bS4') x3,
    ('rpubl:bemyndigande', '.../1998:641#P11'),
    ('rpubl:bemyndigande', '.../2001:937#K8P5a')]
```
No `rpubl:andrar` row at all — the general in-body citation linker also
misses "SiSUVFS", not only the metadata-extraction regex. SiSUVFS 2025:1
also amends 2024:1, but its stored PDF is a scan and the OCR text renders the
designation as "SiSUVES" (an unrelated OCR-quality problem), so that instance
is not independently confirmable and is not counted here.

### Freshness
Site newest: SiSFS 2025:2 (fee year 2026) and SiSUVFS 2025:1. Catalog
`max(label)` for both kinds matches exactly.

### Budget
7 HTTP requests total (well under the 60-request cap): 1 index page fetch,
2 `pdftotext` calls on already-downloaded local PDFs (no network), a handful
of local artifact/catalog reads. No blocking encountered.
