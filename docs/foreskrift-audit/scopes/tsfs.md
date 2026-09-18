# TSFS — Transportstyrelsens föreskrifter, Transportstyrelsen

Verdict: DEFECT
Site list: 842 grundföreskrift designations from 34 read year pages (of 49 attempted, 1978-2026) (https://www.transportstyrelsen.se/sv/om-oss/dina-rattigheter-lagar-och-regler/forfattningssamling/ts-foreskrifter-i-nummerordning/)
We hold: 839 documents (tsfs), newest TSFS 2026:76; site newest TSFS 2026:84
Missing: 3 — TSFS 2026:82, TSFS 2026:83, TSFS 2026:84 (freshness gap, not a defect: routine harvest lag)
Extra: 0
Repeal gaps: 5 confirmed (jvsfs targets dropped by a case-sensitive regex) + 41 more masked by a filename-classify bug (see Evidence); 55 further non-repeal documents lose all content to the same bug
Title defects: 0 in 15+6 sampled documents (tsfs, jvsfs, lfs) — every title matched the site exactly
Consolidations: site yes, we hold 287 of 839 (34%) — not a defect
Inherited: jvsfs 20/20 match the site register; lfs 65/65 match; vvfs 1/1 matches on the TS register (VVFS 2008:418, duplicated as trvfs/2008:418 — issue #50), the other 10 vvfs documents come from Trafikverket's separate TRVFS register (out of this scope, also #50 territory)
Issue: https://github.com/staffanm/ferenda/issues/104

## Evidence

### 1. Enumeration
`ts_enumerate` in `ferenda/foreskrift/agencies.py` reads one page per year at
`<index_url><year>/`. I replicated it directly (not through a pipeline
command) for years 2026 down to 1978 (49 requests through `ferenda.lib.net.request`,
0.3s pacing): 34 years returned `li.tsfs-item` rows, 15 returned 404.
Script: `/tmp/.../scratchpad/fs-audit/enum_tsfs.py`, raw output:
`/tmp/.../scratchpad/fs-audit/tsfs_site_enum.json`.

Unique non-"om ändring" designations per prefix on that register:
tsfs 842, jvsfs 20, lfs 65, vvfs 1 (grund only; "om ändring" rows excluded,
matching how `ts_enumerate` reads the register).

### 2/3/8. Missing/extra/freshness
    site_tsfs - our_tsfs = {TSFS 2026:82, TSFS 2026:83, TSFS 2026:84}
    our_tsfs - site_tsfs = {}  (no extras)

Our newest is TSFS 2026:76; the site's newest is TSFS 2026:84. All numbering
gaps in between (71-75, 77-81) are also gaps on the site's own register, so
they are not missing documents — TSFS does not assign every integer to a
grundföreskrift. The 3-document gap is normal harvest lag versus a page read
today (2026-09-13), not a corpus defect.

### 4/7. Repeal marking and inherited series — the real defect

`catalog.sqlite` marks 208 tsfs documents `upphavande=1` (their whole content
is a repeal). Only 158 of the 208 carry an `rpubl:upphaver` link:

    SELECT count(*) FROM documents WHERE kind='tsfs' AND upphavande=1;   -- 208
    -- links row present for:                                            158
    -- links row absent for:                                              50

Root cause, confirmed by reading the actual artifacts and download records
(no corpus rebuild, files read directly):

**A. Filename-classify miss for a hyphen separator (41 of the 50, plus 55
more non-repeal documents).** `ferenda/foreskrift/agencies.py`:

    RE_TS_PDF = re.compile(r"([a-zåäö]+)[ _](\d{4})[_ ](\d+)(k)?\.pdf$", re.IGNORECASE)

only accepts `_` or a space between the year and the running number.
Transportstyrelsen's 2009 launch batch (and a handful of later documents)
publishes PDFs named `TSFS_YYYY-N.pdf` — a hyphen — which `ts_classify`
never matches, so `resolve_landing` never stores that PDF as `files.regulation`,
even though it is present on the page and matches `pdf_select`. Confirmed
live for three basefiles (2 s between requests):

    RuleNumber=2009:100  -> pdfs matching pdf_select: ['/TSFS/TSFS_2009-100.pdf']
    RuleNumber=2009:144  -> ['/TSFS/TSFS_2009-144.pdf']
    RuleNumber=2009:9    -> ['/TSFS/TSFS_2009-9.pdf']
    RuleNumber=2009:38   -> ['/TSFS/TSFS_2009-38.pdf']
    RuleNumber=2009:1    -> multiple PDFs incl. base 'TSFS_2009-1.pdf' (hyphen,
                            fails) and consolidation 'TSFS 2009_1k.pdf' (space,
                            matches -> consolidation IS captured, base is not)

Corpus-wide count (no network, local files only):

    839 tsfs download records, 96 have files.regulation == null
      89 of those are dated 2009, 7 are scattered 2010-2022
      41 are upphavande=1 repeal notices  -> empty upphaver[], the repeal gap
      55 are ordinary grundföreskrifter   -> empty structure[], no
         beslutsdatum/ikrafttradandedatum at all (e.g. tsfs/2009:1, a document
         later amended 8 times, has zero body text in our corpus)

**B. Case-sensitive designation regex drops `JvSFS` (5 of the 50).**

    RE_FS_REF = re.compile(r"\b([A-ZÅÄÖ]+(?:-| )?(?:FS|FA))\s*(\d{4}):(\d+)")

requires every letter in the prefix to be uppercase. Järnvägsstyrelsen's own
site (and Transportstyrelsen's titles, copied verbatim) write it "JvSFS"
(mixed case). `RE_FS_REF.findall("(JvSFS 2006:6)")` returns `[]`; the same
regex on `(SJÖFS 2008:16)` matches fine. Affected: TSFS 2014:124-128, each
titled "... upphävande av Järnvägsstyrelsens föreskrifter (JvSFS ...) ...".
All five named jvsfs targets exist in our corpus (JVSFS 2006:6, 2008:3,
2008:5, 2008:6, 2008:12) but show zero inbound `rpubl:upphaver` links:

    SELECT count(DISTINCT to_uri) FROM links
    WHERE predicate='rpubl:upphaver' AND to_uri LIKE 'https://lagen.nu/jvsfs/%';
    -- 0

So none of our 20 jvsfs documents show as repealed, though at least 5 are.

Inherited-series counts otherwise match the site exactly (jvsfs 20/20,
lfs 65/65). vvfs is 1/1 on this register (VVFS 2008:418); the other 10 vvfs
documents we hold come from Trafikverket's TRVFS register
(`source_url` = trvfs.ea.trafikverket.se/...), confirmed by reading
`documents.source_url` — that duplication is issue #50's territory, not a
new finding.

### 5. Titles
15 random tsfs titles + 3 jvsfs + 3 lfs titles, compared against the live
site text (`li.tsfs-item` link text, minus the trailing ", <DESIGNATION>"):
21 of 21 matched byte-for-byte, including the "upphävande av ..." repeal
titles. No truncation, no HTML entities, no file-size suffix. Titles are
sound.

### 6. Consolidations
Site publishes konsoliderad PDFs (the 'k'-suffixed filename). Of 839
download records, 287 (34%) carry a non-empty `files.consolidation`. Not a
defect — this is expected: only amended grundföreskrifter get one.

### #76 cross-check
Issue #76's full list includes `lfs/1983:4` and `lfs/1984:4` (both "live",
printing 1992:14/1993:42 on pages 1-2). Both are entries in Luftfartsverkets
"Bestämmelser för Civil Luftfart" (BCL-M) numbering series, which
cross-references older BCL-M revision numbers in its own masthead — plausible
per #76's own caveat about a document naming a related revision rather than
misidentifying itself. No tsfs, jvsfs, or vvfs basefile appears in #76's list.
I did not re-verify by hand beyond reading the printed context; flagging per
the briefing's instruction to describe, not re-file.

### Not filed again
- VVFS 2008:418 duplicated as trvfs/2008:418 — issue #50.
