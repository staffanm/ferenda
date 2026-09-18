# UFS — Upphandlingsmyndighetens författningssamling, Upphandlingsmyndigheten
Verdict: DEFECT
Site list: 5 designations from 1 page (https://www.upphandlingsmyndigheten.se/om-oss/upphandlingsmyndighetens-forfattningssamling/)
We hold: 5 documents (ufs), newest UFS 2023:2; site newest UFS 2023:2
Missing: 0
Extra: 0
Repeal gaps: 0 — site shows no upphävd/upphört marker; our corpus holds no rpubl:upphaver link for ufs (consistent)
Title defects: 2 — UFS 2023:1 (Omtryck word spliced into the parenthesis, trailing footnote digit); UFS 2023:2 (unrejoined line-wrap hyphen)
Consolidations: site no, we hold 0
Inherited: kkvfs (Konkurrensverket) — entry page names no KKVFS document at all; our kkvfs documents stay under their own slug, none renumbered into ufs
Issue: https://github.com/staffanm/ferenda/issues/106

## Evidence

### 1. Enumerate
Fetched the entry page once with `ferenda.lib.net.request` (200, 157833 bytes).
`a[href$=".pdf"][href*="ufs_"]` (the harvest's own `link_select`) returns exactly
five links, matching `UFS = Agency(..., params={"link_select":
'a[href$=".pdf"][href*="ufs_"]', "direct": True})` in
`ferenda/foreskrift/agencies.py:1840`:

    UFS 2020:1, UFS 2020:2, UFS 2020:3, UFS 2023:1, UFS 2023:2

The page's own table ("Gällande föreskrifter") lists the same five, each with a
short subject line. One page, no pagination.

### 2/3. Missing / extra
`sqlite3.connect("file:site/data/catalog.sqlite?mode=ro", uri=True)`,
`SELECT label FROM documents WHERE kind='ufs'` returns the same five labels.
Site set == our set. No missing, no extra.

### 4. Repeal marking
Page text has no "upphäv" instance describing a document (the two hits are
boilerplate about a future possible repeal, and a generic explainer). No
"upphört". `SELECT * FROM links WHERE predicate='rpubl:upphaver' AND
(from_uri LIKE 'https://lagen.nu/ufs/%' OR to_uri LIKE 'https://lagen.nu/ufs/%')`
returns 0 rows. Consistent: nothing is repealed on either side.

(Aside, out of scope: `links` holds one row citing `https://lagen.nu/ufs/1997:15`
from NJA 1999 s. 485 — three years before Upphandlingsmyndigheten existed. That
is almost certainly the old Utsökningsverkets författningssamling, a different
agency that also used "UFS". We hold no such document under `ufs`. This is a
citation-resolution question, not a harvest defect for this scope, so no issue
filed here.)

### 5. Titles
Site's own short titles (from the "Gällande föreskrifter" table):

| designation | site says | we hold |
|---|---|---|
| UFS 2020:1 | Om insamling av uppgifter för statistikändamål (inkl Bilaga A) | Upphandlingsmyndighetens föreskrift (UFS 2020:1) om insamling av uppgifter för statistikändamål |
| UFS 2020:2 | Om upphandlings-ID | Upphandlingsmyndighetens föreskrift (UFS 2020:2) om upphandlings-ID |
| UFS 2020:3 | Om ändring i föreskrift UFS 2020:1 ... | Upphandlingsmyndighetens föreskrift (UFS 2020:3) om ändring i Upphandlingsmyndighetens föreskrift (UFS 2020:1) om insamling av uppgifter för statistikändamål |
| UFS 2023:1 | Om ändring av föreskrift UFS 2020:1 ... (omtryck) | Upphandlingsmyndighetens föreskrifter om ändring av Upphandlingsmyndighetens föreskrifter (**UFS Omtryck 2020:1**) om insamling av uppgifter för statistikändamål**1** |
| UFS 2023:2 | Om ändring av föreskrift UFS 2020:2 ... (omtryck) | Upphandlingsmyndighetens föreskrifter om ändring av Upphandlingsmyndighetens **före- skrifter** (UFS 2020:2) om upphandlings-ID |

Our titles read the full PDF masthead sentence (the right choice; the site's
own table titles are stripped-down subjects), but two of the five are
corrupted:

- UFS 2023:1: the PDF masthead is two columns — the title runs down the left
  column, "Utkom från trycket / den 27 september 2023 / Omtryck" runs down the
  right. `pdftotext -layout` shows "Omtryck" sits on the same visual line as
  "2020:1) om insamling", so reading-order extraction splices it into the
  parenthesis: "(UFS Omtryck 2020:1)" instead of "(UFS 2020:1)". The title
  also ends in a bare "1", the footnote marker glued onto "statistikändamål"
  by the same column layout.
  `ferenda/foreskrift/parse.py:1026` (`_strip_boilerplate`) already knows
  about this exact shape (see its own comment at line 955: "Jordbruksverket's
  register code ... and its 'Omtryck' stamp, printed beside the title and
  landing mid-sentence like the dates" — `RE_MASTHEAD_BOILERPLATE` at line 942
  strips `\bOmtryck\b`). But `_strip_boilerplate` holds parenthesized spans
  back from that general cleanup (line 1032-1039, `held`) and only reruns
  `RE_MASTHEAD_COLUMN` (line 971, dates and column headers only, no
  "Omtryck") inside them. When "Omtryck" lands *inside* the parenthesis, as it
  does here, neither pass removes it.
- This same corruption breaks a second field: `andrar_target()`
  (`ferenda/foreskrift/parse.py:1523`) matches `RE_FS_REF` right after the
  "ändring" phrase to find the amended base. "(UFS Omtryck 2020:1)" does not
  match `RE_FS_REF = r"\b([A-ZÅÄÖ]+(?:-| )?(?:FS|FA))\s*(\d{4}):(\d+)"`, so
  UFS 2023:1's `andrar` comes out `[]`. Its sibling UFS 2023:2, whose
  parenthesis is intact, correctly gets `andrar: ['https://lagen.nu/ufs/2020:2']`.
  Verified against
  `site/data/artifact/foreskrift/ufs/2023-1.json` and `2023-2.json`
  (`ferenda.lib.compress.read_text`).
- UFS 2023:2: `pdftotext -layout` shows the left column word-wraps
  "föreskrifter" across two lines as "före-\nskrifter". Nothing in
  `ferenda/foreskrift/parse.py` rejoins a hyphenated line-wrap (checked:
  no dehyphenation regex in the module), so the stored title keeps the
  literal "före- skrifter".

Both defects trace to the same PDF: an "Omtryck" (reprint) masthead, a layout
this samling apparently introduced only for its two 2023 amendments.

### 6. Consolidations
Page explains what a "konsoliderad version" is in general, and links
consolidated PDFs for two *cross-referenced* DIGG/MDFFS documents, but no UFS
document has one. `files.consolidation` is `0` in all five
`site/data/downloaded/foreskrift/ufs/ufs-*.json.br` records. Consistent, no
defect.

### 7. Inherited series (kkvfs)
The entry page text (13,515 chars, full extraction) contains zero instances of
"KKVFS" or "Konkurrensverk". Upphandlingsmyndigheten does not republish or
relist Konkurrensverket's old upphandlingsstatistik föreskrifter here.
`SELECT DISTINCT kind FROM documents WHERE kind LIKE '%kkv%'` returns
`kkv` and `kkvfs` — the predecessor series already lives under its own slug,
not renumbered into `ufs`. Nothing to reconcile.

### 8. Freshness
Site's newest is UFS 2023:2 (2023-10-23). Our newest (`ORDER BY label`) is
also UFS 2023:2. No newer designation on the site as of 2026-09-13.
