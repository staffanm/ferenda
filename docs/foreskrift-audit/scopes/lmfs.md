# LMFS — Lantmäteriets författningssamling, Lantmäteriet

Verdict: DEFECT
Site list: 94 links from 1 page (https://www.lantmateriet.se/sv/om-lantmateriet/Rattsinformation/Foreskrifter/)
We hold: 93 documents (lmfs), newest LMFS 2026:3; site newest LMFS 2026:3
Missing: 1 — LMFS 2015:3 ("Lantmäteriets föreskrifter om avgifter för uppdrag")
Extra: 12 — the 12 lmvfs documents also minted under lmfs (1994:4, 1994:16,
  1995:3, 1995:9, 1995:10, 2000:2, 2003:3, 2004:1, 2006:1, 2007:3, 2007:4,
  2008:7). This is issue #50's shape (lmfs/lmvfs is one of the five named
  scopes). Not filed again.
Repeal gaps: 4 — LMFS 2019:2, 2020:6, 2021:5, 2022:3 each print an explicit
  "Vid ikraftträdandet upphör LMFS X (samt LMFS Y) att gälla" clause, but the
  parsed artifact's upphaver list is empty for all four.
Title defects: 0 in a 12-document sample. Titles match the site (or, for the
  12 lmvfs-under-lmfs duplicates, the site's LMVFS heading — #50's shape).
Consolidations: site no, we hold 0
Inherited: lmvfs (12 documents) — all 12 sit correctly under the lmvfs slug
  and match the site's LMVFS listing 1:1. They are additionally, and
  wrongly, minted under lmfs too (issue #50).
Issue: https://github.com/staffanm/ferenda/issues/69

## Evidence

### 1. Enumeration
Fetched the single entry page with `ferenda.lib.net.request` (2 requests total
to lantmateriet.se, well inside the 60-request budget: the index page plus one
confirmation fetch of LMFS 2015:3's PDF). No pagination; "Gällande
föreskrifter" and "Upphävda föreskrifter" sections sit on the same page.
94 `<a href*="/rattsinformation/foreskrifter/"][href$=".pdf"]>` links, parsed
by regex on link text + `<h3>` designation headers. All 94 resolve to 82
distinct LMFS + 12 distinct LMVFS designations (no site-side duplicates).

One link text reads "LMVFS 2008: 5" but its surrounding paragraph says
"Lantmäteriets föreskrifter" (not "Lantmäteriverkets"), and the filename is
`lmfs_20085.pdf`; the catalog's `LMFS 2008:5` title text
("...ändring i Lantmäteriverkets föreskrifter (LMVFS 1994: 16)...") matches
this record. Treated as a site-side typo, not our defect; folded into the
LMFS designation set.

### 2. Missing documents
    site lmfs designations − our lmfs designations = {2015:3}

Confirmed live:

    GET https://www.lantmateriet.se/globalassets/om-lantmateriet/rattsinformation/foreskrifter/lmfs153.pdf
    -> 200, application/pdf, 27014 bytes

The site's own "Upphävda föreskrifter" section lists it:
"LMFS 2015:3 — Lantmäteriets föreskrifter om avgifter för uppdrag" (repealed,
see below). Our corpus has no `lmfs/2015:3` document at all — a harvest gap,
not a parse issue on an existing record.

### 3. Extra documents
    our lmfs designations − site lmfs designations = the 12 lmvfs numbers

    sqlite3 site/data/catalog.sqlite (ro):
      select label from documents where kind='lmfs'  -> 93 rows
      select label from documents where kind='lmvfs' -> 12 rows
      overlap by year:lop = all 12 lmvfs rows

This is issue #50's documented shape for lmfs/lmvfs. Not a new defect.

### 4. Repeal marking
The site's "Upphävda föreskrifter" section names 41 repealed designations (one
more, `LMVFS 2006:1`, plus one `<h3>` entry that is a cookie-banner artifact,
not a document). Cross-checked each against
`links` rows with predicate `rpubl:upphaver` and `to_uri` equal to the
document's URI:

    29 OK  (a document we hold names it as a target)
    12 NO UPPHAVER LINK: LMFS 2022:2, 2021:5, 2021:3, 2021:2, 2021:1, 2020:6,
       2020:2, 2019:1(*), 2019:2, 2016:2, 2014:4, 2013:3, 2008:11

(*: 2019:1 was miscounted as "no link" in the first pass because it is itself
repealed by 2019:2, whose own upphaver list is empty — see below — not
because no repealer exists in our corpus.)

Of these, most are amendments to an annual fee schedule (their own text says
"i fråga om ikraftträdandebestämmelserna...", not a fresh repeal) and their
apparent "upphörande" rides on their base regulation's repeal — not a
separate defect. Four are base regulations whose own PDF text explicitly
repeals a named predecessor, but whose parsed artifact has `upphaver: []`:

| designation | PDF says (pdftotext) | artifact `upphaver` |
| --- | --- | --- |
| LMFS 2019:2 | "Vid ikraftträdandet upphör LMFS 2019:1 att gälla." | `[]` |
| LMFS 2020:6 | "...upphör LMFS 2019:2 samt LMFS 2020:5 att gälla." | `[]` |
| LMFS 2021:5 | "...upphör LMFS 2020:6, LMFS 2021:1 samt LMFS 2021:2 att gälla." | `[]` |
| LMFS 2022:3 | "...upphör LMFS 2021:5 samt LMFS 2022:2 att gälla." | `[]` |

Root cause traced by re-running the real parse pipeline
(`ferenda.foreskrift.parse.parse_body` + `extract_metadata`) against the
downloaded PDFs:

    .venv/bin/python -c "
    from ferenda.foreskrift import parse as P
    blocks, notes = P.parse_body(P._pages('site/data/downloaded/foreskrift/lmfs/lmfs-2021-5-regulation.pdf'), 'LMFS 2021:5')
    P._repair_ocr(blocks)
    text = P._repair_ocr_text('\n'.join([P._full_text(blocks)] + [t for _m,t in notes]))
    print('Vid ikraftträdandet' in text)   # False
    "

For all four documents, the text the metadata scan actually receives ends
partway through the "PANTSYSTEMET, BASTJÄNSTER" fee table (a bilaga table
whose rows repeat the document's own designation, e.g. "LMFS 2021:5 Begära
och erbjuda pantbrev"). The final ikraftträdandebestämmelser paragraph, which
carries the repeal clause, and the signatures after it, never reach
`extract_metadata`. Documents without that trailing pantsystemet table (e.g.
LMFS 2019:1, 2018:4, 2016:2, 2015:2), whose transitional clause sits right
after the last "normal" paragraf, parse correctly with the same sentence
shape ("Vid ikraftträdandet upphör LMFS X att gälla."). This is a parse
defect (`ferenda/foreskrift/parse.py`, `parse_body`/table handling), not a
regex-pattern gap: the regexes (`RE_ERSATTER`) match the sentence correctly
in isolation.

### 5. Titles
Compared 12 designations' `documents.title` against the site's own `<h3>` +
paragraph text. All match except cosmetic differences (a trailing "(pdf,
nytt fönster)" artifact of my own scrape, a stray space in "Lantmäteriet s").
No file-size fragments, PDF filenames, HTML entities, truncations, or
designation-in-title duplication found.

### 6. Consolidations
No "konsoliderad"/"grundförfattningen i dess lydelse" text anywhere on the
index page.

    for f in downloaded/foreskrift/lmfs/lmfs-*.json.br: files.consolidation
    -> 0 of 93 records carry a consolidation file

Matches: the site does not publish konsoliderade versions.

### 7. Inherited series (lmvfs)
    select label from documents where kind='lmvfs'  -> 12 rows, 1994:4 .. 2008:7

All 12 match the site's LMVFS-labelled links (1994:4, 1994:16, 1995:3,
1995:9, 1995:10, 2000:2, 2003:3, 2004:1, 2006:1, 2007:3, 2007:4, 2008:7).
Correctly kept under the `lmvfs` slug. The defect is that the same 12 are
*also* minted under `lmfs` (issue #50), not that they are missing or
misplaced under `lmvfs`.

### 8. Freshness
Newest we hold: LMFS 2026:3. Newest on site: LMFS 2026:3. No gap.
