# KKVFS — Konkurrensverkets författningssamling, Konkurrensverket

Verdict: DEFECT
Site list: 11 designations with a PDF link, from 1 page (https://www.konkurrensverket.se/om-oss/forfattningssamling/). The same page also names 40+ older designations in prose (1993-2019) as repeal/replace history, with no PDF link.
We hold: 11 documents (kkvfs), newest KKVFS 2025:1; site newest KKVFS 2025:1 (2025-03-27). Match.
Missing: 0
Extra: 0
Repeal gaps: 0 — all 9 upphaver relations checked against the site's own "Upphävt genom / Ersatt av" text match our `upphaver` links. The #31 fix (KKVFS 2021:2 -> 2015:2) holds on the live site.
Title defects: 1 — KKVFS 2020:3: we hold "Föreskrifter om ändring i Konkurrensverkets **före- skrifter** (2020:2) …" (unrepaired PDF line-wrap hyphen); the site prints "Föreskrifter om ändring i Konkurrensverkets **föreskrifter** (2020:2) …". Side effect: the broken word stops `andrar_target`'s regex, so our record carries no `andrar` link to 2020:2, though the site is explicit that 2020:3 amends 2020:2.
Consolidations: site no (no "konsoliderad" text anywhere on the page), we hold 0. Not a defect.
Inherited: none (assignment lists no predecessor samling).
Issue: https://github.com/staffanm/ferenda/issues/103

## Evidence

### 1. Enumerate
Fetched the entry page over HTTP/2 (the site 403s HTTP/1.1):
```
.venv/bin/python -c "
from ferenda.lib.net import make_http2_session, request, BROWSER_UA
session = make_http2_session(BROWSER_UA)
r = request(session, 'GET', 'https://www.konkurrensverket.se/om-oss/forfattningssamling/')
print(r.status_code, len(r.text))"
# -> 200, 245703 bytes, single page, no pagination
```
`a[href*="/forfattningssamling/kkvfs"][href$=".pdf"]` -> 11 links: kkvfs_2025-1, 2020-1, 2020-2,
2020-3, 2017-3, 2021-1, 2021-2, 2017-4, 2007-2, 2007-3, 2006-2. These are exactly the 11
basefiles under `site/data/artifact/foreskrift/kkvfs/`.

### 2/3. Missing / extra
Set of 11 linked designations == set of 11 `documents.label` rows in the catalog (`kind='kkvfs'`).
No missing, no extra.

### 4. Repeal marking
Read every metadata.upphaver in our 11 artifacts (`ferenda.lib.compress.read_text` on each
`.json.br`) and compared against the page's own prose ("Upphävt genom …" / "Ersatt av …" /
"Ersätter …"):

| our repealer | our upphaver targets | site says |
|---|---|---|
| KKVFS 2021:2 | kkvfs/2015:2 | "2015:2 ... Upphävt genom KKVFS 2021:2" |
| KKVFS 2017:4 | kkvfs/2010:2 | "2010:2 ... Upphävt genom KKVFS 2017:4" |
| KKVFS 2017:3 | kkvfs/2009:1 | "2017:3 ... Ersätter KKVFS 2009:1" |
| KKVFS 2021:1 | kkvfs/2015:1 | "2021:1 ... Ersätter KKVFS 2015:1" |
| KKVFS 2025:1 | kkvfs/2010:3 | "2025:1 ... ersätter KKVFS 2010:3" |
| KKVFS 2007:3 | kkvfs/2000:6, kkvfs/2000:7 | "2000:6 ... Upphävt genom KKVFS 2007:3"; "2000:7 ... Upphävt genom KKVFS 2007:3" |
| KKVFS 2007:2 | kkvfs/1993:4, 1993:5, 1993:7 | "1993:4/5/7 ... Upphävt genom KKVFS 2007:2" (1993:6 correctly excluded: repealed by 1997:1, not 2007:2) |
| KKVFS 2006:2 | kkvfs/2000:2,3,4,5 | "2000:2/3/4/5 ... Upphävt genom KKVFS 2006:2" |

All 9 match exactly, including the case (1993:6) that must be excluded. The #31 fix is confirmed
live.

### 5. Titles
Compared all 11 `documents.title` values against the page's anchor text / surrounding `<p>`:
10 of 11 match verbatim, including KKVFS 2007:3's own junk ("Kungörelse - Förordning om
upphävande av vissa av Konkurrensverkets föreskrifter\xa0\xa0(142 kb)") — the site's own anchor
text carries that file-size suffix, so this is a faithful copy, not our defect.

KKVFS 2020:3 is the one defect. Its harvest anchor text on the site is bare ("KKVFS 2020:3", no
title), so the title comes from `title_from_masthead` reading the regulation PDF:
```
pdftotext -layout site/data/downloaded/foreskrift/kkvfs/kkvfs-2020-3-regulation.pdf -
```
```
Föreskrifter om ändring i Konkurrensverkets före-       Utkom från trycket
skrifter (2020:2) om betalning av avgifter enligt lagen
```
The PDF line-wraps "föreskrifter" with a hyphen. `title_from_masthead`
(ferenda/foreskrift/parse.py) joins masthead blocks with `" ".join(text.split())`, which
collapses whitespace but never closes a hyphenated line break — unlike body-paragraph reflow,
which calls `dehyphenate` (ferenda/lib/pdftext.py). Result: our title and metadata both read
"före- skrifter (2020:2)" verbatim, where the site prints "föreskrifter (2020:2)".

Confirmed side effect: `andrar_target()` (ferenda/foreskrift/parse.py) requires the literal
substring "föreskrifter" immediately before a bare "(YYYY:N)" (`RE_BARE_OWN_REF`) to record an
ändring relation. The hyphen break defeats that match, so:
```
.venv/bin/python -c "
from ferenda.lib import compress; import json
d = json.loads(compress.read_text('site/data/artifact/foreskrift/kkvfs/2020-3.json'))
print(d['metadata']['andrar'])"   # -> []
```
KKVFS 2020:3 carries no `andrar` link to KKVFS 2020:2, though the site's own text says
"Föreskrifter om ändring i Konkurrensverkets föreskrifter (2020:2) om betalning …" — an explicit
amendment declaration.

### 6. Consolidations
`grep -io "konsoliderad[a-z]*"` on the fetched page: no hits. Download records for all 11
basefiles: `files.consolidation` is empty for every one. Site publishes none, we hold none —
matches, not a defect.

### 7. Inherited series
Assignment states none. No predecessor slug to check.

### 8. Freshness
Site's own newest linked designation is KKVFS 2025:1 (2025-03-27); our newest catalog row is the
same. Match.

### Duplicate-class checks (do not re-file)
- #50 (predecessor doc also minted under successor slug): not applicable, no predecessor series.
- #52 (one PDF stored under two designations): all 11 `regulation.pdf` files are distinct
  (different sizes/content per pdftotext sample); not applicable.
- "upphävda föreskrifter" archive page missed by harvest: the KKV page has no separate archive —
  everything (current and historical) sits on the one page read above. Not applicable.
- Pagination missed by `indexed_enumerate`: page has no pagination controls; the whole samling is
  one page. Not applicable.
- #76 (pdftotext designation mismatch): kkvfs is not in the #76 list, and none of the printed
  headers we sampled (2020-3, 2007-3) disagree with the minted designation.
