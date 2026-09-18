# MTFS — Myndigheten för tillväxtpolitiska utvärderingar och analyser (Tillväxtanalys)

Verdict: DEFECT
Site list: 16 designations from 1 page (https://www.tillvaxtanalys.se/statistik/tillvaxtanalysforeskrifter.125740.html)
We hold: 16 documents (mtfs), newest MTFS 2023:3; site newest MTFS 2023:3
Missing: 0
Extra: 0
Repeal gaps: 0 — all 5 repealed base regulations (2009:1..2009:4, 2010:1) have a
  repealing document in our corpus with a correct `rpubl:upphaver` target
Title defects: 0 — all 16 titles match the site's link text exactly
Consolidations: site no, we hold 0
Inherited: none (per assignment; note ITPS predecessor context below)
Issue: https://github.com/staffanm/ferenda/issues/73

## Evidence

### Access

The site is behind an F5/Shape challenge. Plain playwright chromium headless
was tried first and got the CAPTCHA page ("What code is in the image?").
Camoufox (`ferenda.lib.browser.CamoufoxBrowser`, as `ferenda/foreskrift/mtfs.py`
already uses) cleared it on the first navigation. Camoufox's browser binary
was not yet fetched in this environment; ran `python -m camoufox fetch` once
(one-time ~660 MB download, not a repo write) before use.
Total agency-site requests: 1 (the index page only).

### 1. Enumerate

```
CamoufoxBrowser(profile, timeout=60.0).html(mtfs.INDEX_URL, "Tillväxtanalys föreskrifter")
mtfs.parse_index(html)
```
Returned 16 `DocRef`s from the one-page register (no pagination). Matched
1:1 against `select label from documents where kind='mtfs'` (16 rows) — set
difference both ways is empty.

### 2/3. Missing / Extra

None. The 16 designations on the site and the 16 in `documents` are the same
set: MTFS 2009:1..2009:4, 2010:1, 2011:1, 2011:2, 2012:1, 2016:1..2016:3,
2018:1, 2018:2, 2023:1..2023:3.

### 4. Repeal marking

The site's "Upphävda föreskrifter" section lists 8 headings: the repealed
base regulations 2009:1, 2009:2, 2009:3, 2009:4, 2010:1, plus their now-moot
amendments 2011:1, 2012:1, 2016:3 (grouped there because they amend a
regulation that is itself gone, not because they are separately repealed).

For each of the 5 repealed bases, our corpus holds a repealing document with
the correct `rpubl:upphaver` target:

| Repealed base | Repealing document | `upphaver` target |
|---|---|---|
| mtfs/2009:1 | mtfs/2023:1 | mtfs/2009:1 |
| mtfs/2009:2 | mtfs/2018:1 (also mtfs/2018:2) | mtfs/2009:2 |
| mtfs/2009:3 | mtfs/2016:1 | mtfs/2009:3 |
| mtfs/2009:4 | mtfs/2011:2 | mtfs/2009:4 |
| mtfs/2010:1 | mtfs/2023:2 (also mtfs/2023:3) | mtfs/2010:1 |

No gaps.

### 5. Titles

All 16 titles in `documents.title` match the site's link text verbatim
(diffed programmatically, 0 mismatches). No junk (file sizes, PDF names,
truncation, HTML entities) found.

### 6. Consolidations

Site: no "konsoliderad" text anywhere on the index page. We hold: 0 of 16
download records carry `files.consolidation`. Consistent, no defect.

### 7. Inherited series

Assignment states none for mtfs. Note for context only: `links` shows
mtfs/2009:1..2009:4 each carry `rpubl:upphaver` to `itpsfs/*` designations
(ITPS = Institutet för tillväxtpolitiska studier, Tillväxtanalys's actual
predecessor agency). itpsfs is presumably a separate audit scope; not
checked here.

### 8. Freshness

Site newest: MTFS 2023:3 (Beslutad 2023-03-29). We hold: mtfs/2023:3, same
date. The agency has issued nothing since. In sync.

### Confirmed defect: publisher truncated to "Tillväxtanaly"

`documents.publisher` (and the artifact `metadata.publisher`) for
**mtfs/2023:1** and **mtfs/2023:2** is `"Tillväxtanaly"`. Every other
mtfs document (14 of 16, including 2023:3, decided the same day) carries
the full `"Myndigheten för tillväxtpolitiska utvärderingar och analyser"`.

Root cause, traced in `ferenda/foreskrift/parse.py`:

```
.venv/bin/python -c "
from ferenda.foreskrift import parse as P
blocks, notes = P.parse_body(P._pages('site/data/downloaded/foreskrift/mtfs/mtfs-2023-1-regulation.pdf', None), 'MTFS 2023:1')
P._repair_ocr(blocks)
start = P._body_start(blocks)
masthead = P._repair_ocr_text(P._full_text(blocks[:start]))
print(P.extract_publisher(masthead))
"
# -> Tillväxtanaly
```

`extract_publisher` tries `RE_UTGIVARE`, then `RE_FS_SERIES`, then
`RE_FS_TITLE` in order. The agency's full name, "Myndigheten för
tillväxtpolitiska utvärderingar och analyser", is 59 characters after its
leading capital — longer than the `{2,55}` capture-group bound both
`RE_FS_SERIES` and `RE_FS_TITLE` allow. So neither pattern can match the
masthead's own "Myndigheten för tillväxtpolitiska utvärderingar och
analysers författningssamling"/"...analysers föreskrifter" occurrences.
`RE_FS_TITLE` (a `re.search`, leftmost successful match wins) then matches
much later in the text, in the Ikraftträdande clause: "... då
**Tillväxtanalys** föreskrifter MTFS 2009:1 upphör att gälla" — the
regex's mandatory trailing "s" before "föreskrifter" eats the name's own
final "s", leaving the captured group `"Tillväxtanaly"`.

For 14 of the 16 documents this same failure mode also occurs (I checked
2023:3 directly: `extract_publisher` returns `None` there too, from the
same 59-vs-55 character overflow), but `parse_record` falls back to the
harvest-time label when `extract_publisher` returns `None`
(`publisher = meta.pop("publisher", None) or record.get("publisher")`,
`ferenda/foreskrift/parse.py:1600`), and that label is the correct full
name. 2023:1 and 2023:2 are the two documents where a short-name mention
happens to appear later in the running text in a shape `RE_FS_TITLE` can
still match, so the fallback is skipped and the truncated capture wins.

This is a parser defect (`{2,55}` cap too small for a 59-character agency
name), not a site or harvest problem. Both the site and our own harvest
label give the correct full name.
