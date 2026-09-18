# TVFS — Tillväxtverkets författningssamling, Tillväxtverket

Verdict: DEFECT
Site list: 11 designations from 1 page (https://tillvaxtverket.se/tillvaxtverket/omtillvaxtverket/varverksamhet/foreskrifter.595.html)
We hold: 11 documents (10 tvfs + 1 nutfs), newest TVFS 2025:3; site newest TVFS 2025:3
Missing: 4 confirmed — TVFS 2016:2, TVFS 2016:3, TVFS 2019:1, TVFS 2019:2 (each a distinct, dated
  ändringsföreskrift/omtryck with its own masthead number; PDF is already in our download
  records, filed under the wrong identifier). Also cited from elsewhere in the corpus but not
  on the current site page, so not counted here: TVFS 2016:1.
Extra: 0
Repeal gaps: 0 — TVFS 2013:1 (upphävd by 2015:1), TVFS 2017:1 (upphävd by 2025:3), NUTFS
  2001:1/2007:1 (upphävda via SFS 2022:96/2022:106, announced in TVFS 2025:1/2025:2) all
  check out against our upphaver links.
Title defects: 0 clear junk (no file sizes, truncation, filenames, entities); one style
  artifact noted below, not filed.
Consolidations: site no (single PDF per entry, no separate konsoliderad page), we hold 0 — matches.
Inherited: nutfs — we hold 1 (NUTFS 1996:4), correctly under the nutfs slug, matching the site.
  NUTFS 2001:1 and 2007:1 are mentioned in prose but carry no download link on the current
  site, so nothing to harvest for them; not a defect.
Issue: https://github.com/staffanm/ferenda/issues/99

## Evidence

### 1. Enumerate
Fetched the single entry page (1 request) with `ferenda.lib.net.request`. Extracted anchors
with `a[href*="/download/"]` (14 links). One is the "Årlig förteckning" list PDF, correctly
skipped by `skip_re`. Of the remaining 13, two pairs are the same document offered twice
("… (pdf, tillgänglig version)" is an accessible-format duplicate), leaving 11 distinct
designations: TVFS 2025:3, 2021:1, 2015:1, 2013:1, 2014:1, 2009:2, 2009:1, NUTFS 1996:4,
2025:1, 2025:2, 2017:1. This equals what we hold (11), and matches how our harvester's
`ref()` derives identity (first `FS YYYY:N` in the anchor text) — see the design issue below.

### 2/3. Missing / extra documents — the omtryck mislabeling
Four of the site's entries carry TWO designations in the anchor text: the base regulation's
number, then "omtryckt genom … TVFS <its own number>". Example:

    "TVFS 2015:1 omtryckt genom Tillväxtverket föreskrifter - stöd från regionala
    strukturfondsprogrammen och det nationella strukturfondsprogrammet TVFS 2019:1"

`ferenda/foreskrift/harvest.py:ref()` takes the FIRST `RE_FS_NUMBER` match as the document's
identity ("the (year, lopnummer) come from the first YYYY:N in the text"). For these four
entries that is the BASE regulation's number, not the linked PDF's own number. `pdftotext -f 1
-l 2` on the stored PDFs shows each one's real masthead:

    site/data/downloaded/foreskrift/tvfs/tvfs-2015-1-regulation.pdf  -> masthead "TVFS 2019:1",
        "Beslutade den 21 maj 2019", "Utkom från trycket den 18 juni 2019", tagged "Omtryck"
    site/data/downloaded/foreskrift/tvfs/tvfs-2009-1-regulation.pdf  -> masthead "TVFS 2019:2"
    site/data/downloaded/foreskrift/tvfs/tvfs-2009-2-regulation.pdf  -> masthead "TVFS 2016:2"
    site/data/downloaded/foreskrift/tvfs/tvfs-2014-1-regulation.pdf  -> masthead "TVFS 2016:3"

Our artifact `site/data/artifact/foreskrift/tvfs/2015-1.json` confirms the mismatch: its
`identifier` is "TVFS 2015:1", but `beslutsdatum` (2019-05-21), `ikrafttradandedatum`
(2019-08-01) and `utkomFranTryck` (2019-06-18) are all TVFS 2019:1's own dates, read straight
from the PDF. The document filed as "TVFS 2015:1" in our catalog IS TVFS 2019:1's content.

So TVFS 2016:2, 2016:3, 2019:1 and 2019:2 do not exist as their own documents in the corpus,
even though the PDFs for them are already sitting in our download records under a different
name. Other corpus documents cite them by their real number, and cannot resolve:

    from_uri                          text          to_uri (dangling)
    lagen.nu/skr/2020/21:210          TVFS 2016:2   lagen.nu/tvfs/2016:2
    lagen.nu/skr/2020/21:25           TVFS 2019:1   lagen.nu/tvfs/2019:1
    lagen.nu/skr/2021/22:248          TVFS 2019:1   lagen.nu/tvfs/2019:1
    lagen.nu/sou/2024:24              TVFS 2016:3   lagen.nu/tvfs/2016:3
    lagen.nu/tvfs/2009:1 (self)       TVFS 2019:2   lagen.nu/tvfs/2019:2
    lagen.nu/tvfs/2009:2 (self)       TVFS 2016:2   lagen.nu/tvfs/2016:2
    lagen.nu/tvfs/2014:1 (self)       TVFS 2016:3   lagen.nu/tvfs/2016:3

(query: `select * from links where to_uri like '%tvfs/2019%' or to_uri like '%tvfs/2016%'`).
A fifth designation, TVFS 2016:1, is cited the same way (skr 2020/21:25, sou 2018:70, sou
2024:24) but carries no download link on the current site page, so it is not counted in
"Missing" above — it cannot be re-fetched from this entry page.

Not in the corpus-wide pdftotext sweep (#76): these four rows print BOTH the base number
(in running prose, "Tillväxtverket föreskriver i fråga om … (TVFS 2015:1) …") and their own
number (in the masthead), so #76's "does our number appear on page 1-2" check found a match
and did not flag them — the defect is which of the two numbers we chose as the record's
identity, not a wrong-PDF swap.

### 4. Repeal marking
- Site prose: "[TVFS 2013:1] … har upphävts genom TVFS 2015:1." Our tvfs/2015:1 artifact
  (which, per above, is really TVFS 2019:1's reprint of 2015:1) carries
  `upphaver: ["https://lagen.nu/tvfs/2013:1"]`. Correct.
- Site groups TVFS 2017:1 under "Nyligen upphävda föreskrifter" (recently repealed). Our
  tvfs/2025:3 artifact carries `upphaver: ["https://lagen.nu/tvfs/2017:1"]`. Correct.
- Site prose: "Regeringen upphävde … NUTFS 2001:1 och NUTFS 2007:1 … bekantgjorda i andra
  hand … genom TVFS 2025:1 och TVFS 2025:2." Our artifacts: tvfs/2025:1 has
  `upphaver: ["nutfs/2001:1", "sfs/2022:96"]`, `andrar: ["nutfs/2001:1"]`; tvfs/2025:2 has
  `upphaver: ["nutfs/2007:1", "sfs/2022:106"]`, `andrar: ["nutfs/2007:1"]`. Both correct,
  including the SFS repeal ordinance.

### 5. Titles
Compared all 11 stored titles against the site's own link text / heading text; the four
plain designations (2025:1, 2025:2, 2025:3, 2021:1, 2017:1, 2013:1) match verbatim or are
pulled from the PDF (2025:1/2025:2 carry no link text on the site at all, so the title comes
from the PDF, and matches the repealed NUTFS regulation each announces). No file sizes, PDF
filenames, truncations, or HTML entities. One style note, not filed as a defect: the four
omtryck titles read "omtryckt genom …" with no stated subject, because the base designation
was stripped from the front of the site's link text — readable, if slightly odd on its own.

### 6. Consolidations
`select files.consolidation from all 10 tvfs + 1 nutfs download records` — every record has
`"consolidation": []`. The site does not publish a separate "konsoliderad version" page or
link either; each entry is a single PDF. Not a defect.

### 7. Inherited series (nutfs)
`select * from documents where kind='nutfs'` returns 1 row, NUTFS 1996:4, correctly filed
under the `nutfs` slug (not renumbered into tvfs), matching the site's own listing. NUTFS
2001:1 and 2007:1 are named in prose only ("Regeringen upphävde … NUTFS 2001:1 och NUTFS
2007:1") with no PDF link anywhere on the page, so there is nothing left to harvest for them.

### 8. Freshness
Site's highest lopnummer for the current year is TVFS 2025:3 (2025-01-16); we hold it. No
2026 regulation is listed (only the unrelated "Årlig förteckning 2026" list PDF).

### Commands used
    request(session, 'GET', <entry url>)                     # 1 HTTP request total
    pdftotext -f 1 -l 2 <regulation.pdf>                      # 4 PDFs read
    sqlite3 catalog.sqlite: documents (kind='tvfs'/'nutfs'), links (to_uri like tvfs/2016%/2019%)
    ferenda.lib.compress.read_text on artifact + downloaded JSON for tvfs and nutfs
