# KIFS — Kemikalieinspektionens föreskrifter, Kemikalieinspektionen

Verdict: DEFECT
Site list: 3 designations from 1 page (entry page), plus 3 more on a linked
archive page (2 pages total):
https://www.kemi.se/lagar-och-regler/lagstiftningar-inom-kemikalieomradet/kemikalieinspektionens-foreskrifter-kifs
We hold: 3 documents (kifs), newest KIFS 2022:3; site newest base regulation
also KIFS 2022:3 (no newer grundföreskrift on the main page).
Missing: 3 — KIFS 2005:7, KIFS 2008:3, KIFS 2020:3 (all on the agency's
"Föreskrifter som har upphört att gälla" archive page, never visited by the
harvest).
Extra: 0 — the 3 documents we hold match the 3 on the main index page exactly.
Repeal gaps: 0 confirmed as a parse defect. KIFS 2008:3 (missing, see above)
is described by kemi.se as replaced by KIFS 2022:3, but KIFS 2022:3's own
"Ikraftträdande- och övergångsbestämmelser" section carries no explicit
upphävande clause naming KIFS 2008:3 — the source text itself gives no
repeal evidence to parse, so `upphaver: []` on KIFS 2022:3 is correct given
its own text.
Title defects: 0 — only 3 documents held; all 3 titles match the site's own
headings exactly (e.g. site "KIFS 2022:3 om bekämpningsmedel" vs. our
"Kemikalieinspektionens föreskrifter (KIFS 2022:3) om bekämpningsmedel", the
same fuller form printed on the PDF cover).
Consolidations: site yes, we hold 3/3 (one per base regulation).
Inherited: none (assignment lists no predecessor samling).
Issue: https://github.com/staffanm/ferenda/issues/65

## Evidence

### 1. Our holdings
```
.venv/bin/python -c "
import sqlite3
con = sqlite3.connect('file:site/data/catalog.sqlite?mode=ro', uri=True)
print(con.execute(\"SELECT label, title, date FROM documents WHERE kind='kifs' ORDER BY label\").fetchall())
"
```
Result: KIFS 2017:7, KIFS 2017:8, KIFS 2022:3 — exactly 3 documents, each
with a `.grund.json` and a consolidated `.json` artifact and a `consolidation`
PDF in its download record (`site/data/downloaded/foreskrift/kifs/kifs-*-consolidation-*.pdf`).

### 2. Harvest config
`ferenda/foreskrift/agencies.py:145-155` — `KIFS` uses `indexed_enumerate`
with a single `index_url` (no `index_urls` list), so only the one entry page
is ever fetched. `enumerate=indexed_enumerate` + `resolve=resolve_landing`.

### 3. Entry page fetch
```
r = request(session, "GET",
  "https://www.kemi.se/lagar-och-regler/lagstiftningar-inom-kemikalieomradet/kemikalieinspektionens-foreskrifter-kifs")
```
200, 3 anchors match `link_select` (`kifs-20177`, `kifs-20178`, `kifs-20223`)
— matches our 3 held documents exactly, so the main page alone shows no gap.

### 4. The missed archive page
The entry page's own navigation menu links to a sub-page not covered by
`link_select`, `index_urls`, or any harvest visit:
`.../kemikalieinspektionens-foreskrifter-kifs/foreskrifter-som-har-upphort-att-galla`
("Regulations that have ceased to apply").

Fetched it directly (200, one page). It lists three grundföreskrifter, each
with its own landing page and PDF(s):
- KIFS 2005:7 (klassificering och märkning) — "upphävts ... den 1 maj 2022
  ... ersatts med CLP-förordningen." Landing:
  `.../foreskrifter-som-har-upphort-att-galla/kifs-20057`
- KIFS 2008:3 (bekämpningsmedel) — "upphävts ... den 1 maj 2022 ... ersattes
  då av KIFS 2022:3." Landing:
  `.../foreskrifter-som-har-upphort-att-galla/kifs-20083`
- KIFS 2020:3 (märkning och säkerhetsdatablad) — "har upphört att gälla".
  Landing: `.../foreskrifter-som-har-upphort-att-galla/kifs-20203`

None of these three basefiles exist under `site/data/artifact/foreskrift/kifs/`
or `site/data/downloaded/foreskrift/kifs/`. This is the same shape as #45
(AFS) and #51 (EIFS): an "upphävda föreskrifter" archive the harvest's index
URL list never visits.

### 5. Amendment PDFs are not a gap
KIFS 2017:7's landing page (`kifs-20177`) hangs 21 amendment PDFs (KIFS
2018:1 .. 2026:3) directly under its own "Ändringsföreskrifter" heading —
they have no separate landing pages of their own on kemi.se. `resolve_landing`
+ `classify_section` (`ferenda/foreskrift/harvest.py:181-198`) file these
as `files.amendment` references, and the parser turns them into the base's
`andradAv` metadata (`site/data/artifact/foreskrift/kifs/2017-7.json`:
21 entries) without minting separate `kifs/YYYY:N` documents — this matches
the vertical's documented, deliberate design (`ferenda/foreskrift/render.py:88`:
"an amendment that is also harvested as its own document [...] deliberately
not minted here"). Not filed as a defect.

### 6. Repeal-clause check
```
pdftotext site/data/downloaded/foreskrift/kifs/kifs-2022-3-consolidation-2022_3.pdf - \
  | grep -n -i "upphäv\|ikraftträd"
```
The "Ikraftträdande- och övergångsbestämmelser" section for KIFS 2022:3 only
says "Dessa föreskrifter träder i kraft den 1 maj 2022" — no clause names
KIFS 2008:3. `metadata.upphaver: []` in
`site/data/artifact/foreskrift/kifs/2022-3.json` is therefore consistent
with the source text; kemi.se's plain-language "ersattes då av" is editorial,
not a formal upphävande clause we could parse.

### 7. Requests used
3 requests to kemi.se (entry page, archive page, one landing page for
verification) — well under the 60-request budget. No JavaScript rendering
needed; no blocks encountered.
