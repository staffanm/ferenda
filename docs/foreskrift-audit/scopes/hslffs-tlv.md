# hslffs-tlv — HSLF-FS, Tandvårds- och läkemedelsförmånsverket

Verdict: DEFECT
Site list: 60 designations from 1 page, 12 accordion panels (https://www.tlv.se/om-tlv/regelverk/foreskrifter.html)
We hold: 59 documents with source_url = the TLV entry page (hslffs 32, tlvfs 25, lfnfs 2); newest HSLF-FS 2026:24 (2026-06-11); site newest also HSLF-FS 2026:24
Missing: 1 — HSLF-FS 2018:30 (TLV's own site prints its link text as "HSLF-FS 2018:3", one lopnummer short; the PDF's own masthead and Artikelnummer say 2018:30)
Extra: 0
Repeal gaps: 0 checked as "site marks X upphävd" (the site lists only current text, no struck-through entries) — one repeal chain inspected (HSLF-FS 2026:23 -> TLVFS 2008:2 -> LFNFS 2003:1) matches our links table
Title defects: 15 — see below (reprint-marker titles, masthead bleed, one wrong-subject title)
Consolidations: site yes (5 panels), we hold 5/5
Inherited: tlvfs (24 of 24 site designations held, correctly under kind=tlvfs); lfnfs (3 of 3 site designations held, correctly under kind=lfnfs; publisher label inconsistent, see below)
Issue: https://github.com/staffanm/ferenda/issues/59 (references #55 for the shared cross-agency collision)

## Evidence

### 1. Enumeration
Fetched the single entry page once (`ferenda.lib.net.request`). It has no
pagination; 12 `div.sv-collapsible-content` panels, one per base act. Read
every panel's anchors with `ferenda.foreskrift.hslffs.numbered()` /
`samling()` (the real production parser, not a hand-rolled regex) to build
the site's own list: 60 distinct (fs, year, lopnummer) triples -- 33
HSLF-FS, 25 TLVFS, 2 LFNFS.

### 2/3. Missing / extra
```
select kind, label, source_url from documents
 where source_url = 'https://www.tlv.se/om-tlv/regelverk/foreskrifter.html'
```
gives 59 rows. Diffed against the site's 60 by (fs, year, lopnummer): 1
missing, 0 extra.

The one missing designation is the base act TLV lists as "HSLF-FS 2018:3"
under the TLVFS 2014:9 panel ("Ändrad:", href
`.../HSLF_FS_2018_30.pdf`). Downloaded that PDF directly (2 requests total,
politely paced) and read it with `pdftotext`:

```
$ pdftotext downloaded/HSLF_FS_2018_30.pdf -
...
Artikelnummer 17318030HSLF
...
Föreskrifter om ändring i Tandvårds- och
läkemedelsförmånsverkets föreskrifter och
allmänna råd (TLVFS 2014:9) om prissättning av
vissa äldre läkemedel;
beslutade den 13 juni 2018.
```
The Artikelnummer and the filename both say 2018:30. TLV's own link text
says "2018:3" (one digit short) -- a genuine site typo. That number, 2018:3,
already belongs to a real, unrelated Läkemedelsverket document in our
corpus (`hslffs/2018:3`, "Föreskrifter om ändring i Läkemedelsverkets
föreskrifter (HSLF-FS 2016:34) ..."), so no collision happened -- HSLF-FS
2018:30 is simply absent from the corpus under any basefile.

A second, more serious instance of the same site typo did collide.
`enumerate_files`/`tlv_enumerate` in `ferenda/foreskrift/hslffs.py` files a
document under the number the site's own link text prints, with no check
against the PDF's own identifier. TLV's panel for TLVFS 2014:9 links a file
whose text reads "HSLF-FS 2023:25" but whose filename is
`hslf_fs_2023_35.pdf`:

```
$ pdftotext downloaded/hslf_fs_2023_35.pdf -
...
Artikelnummer 17323035HSLF
...
HSLF-FS
2023:35
Föreskrifter
om ändring i Tandvårds- och läkemedelsförmånsverkets föreskrifter ...
```
Our corpus stores this file's content under `hslffs/2023:25`:
```
sqlite> select uri,label,title,publisher from documents
        where uri='https://lagen.nu/hslffs/2023:25';
https://lagen.nu/hslffs/2023:25|HSLF-FS 2023:25|(omtryckt)|Tandvårds- och läkemedelsförmåns- verket
```
HSLF-FS 2023:25 is not TLV's number at all -- it is a real Läkemedelsverket
document. `hslffs/2025:67` (Läkemedelsverket, "om giltigheten av
Europafarmakopén i Sverige") lists it as one of nine prior editions it
repeals:
```
$ sqlite3 catalog.sqlite "select from_uri,to_uri from links
    where predicate like '%upphaver%' and to_uri='https://lagen.nu/hslffs/2023:25'"
https://lagen.nu/hslffs/2025:67|https://lagen.nu/hslffs/2023:25
```
and the repeal list's other eight members (2022:65, 2023:11, 2023:41,
2024:6, 2024:13, 2024:32, 2025:4, 2025:47) are all real Läkemedelsverket
documents, dated a few months apart -- 2023:25 fits that same rolling
series by date (2023-09-04). The genuine Läkemedelsverket HSLF-FS 2023:25
is missing from the corpus; TLV's HSLF-FS 2023:35 sits in its place under
the wrong label, the wrong title and the wrong publisher.

### 4. Repeal marking
TLV's page lists only current text; it never marks anything upphävd, so
there is nothing on the site to check a struck-through entry against.
Sampled one repeal chain in the links table instead: HSLF-FS 2026:23
(newest base act) upphäver TLVFS 2008:2, which upphäver LFNFS 2003:1 --
both repealing documents are in the corpus and their `upphaver` targets
resolve to documents we hold. No gap found in this chain.

### 5. Titles (10+ compared)
Reprint-marker text stored as the whole title (should fall back to the
PDF's own rubric per `parse.clean_title`'s docstring, "F7"): the harvest
title is literally "(omtryckt)" or "(Omtryckt" (TLV's own site drops the
closing paren on the newest one), which `clean_title`'s length-8 probe
wrongly accepts as real prose.

| designation | we hold (title) | the PDF's own rubric |
|---|---|---|
| HSLF-FS 2015:32 | `(omtryckt)` | "Föreskrifter om ändring i ... föreskrifter (TLVFS 2009:3) om handelsmarginal ..." |
| HSLF-FS 2016:90 | `(omtryckt)` | (same defect) |
| HSLF-FS 2017:63 | `(omtryckt)` | (same defect) |
| HSLF-FS 2020:5 | `(omtryckt)` | (same defect) |
| HSLF-FS 2023:25 | `(omtryckt)` | belongs to 2023:35 (see above) |
| HSLF-FS 2026:22 | `(Omtryckt` | (same defect) |
| TLVFS 2014:10 | `(omtryckt)` | (same defect) |
| TLVFS 2014:7 | `(omtryckt)` | (same defect) |

Masthead sidebar text ("Utkom från trycket den <day> <month> <year>")
bleeding into the middle of the title, verified against `pdftotext` of the
regulation PDF each stems from:

| designation | we hold (title) | root cause |
|---|---|---|
| TLVFS 2011:4 | "...och allmänna **den 12 sept 2011** råd (TLVFS 2009:4) ..." | "sept" is not in `ferenda.lib.util.MONTHS` (only full month names), so the masthead-date regexes in `ferenda/foreskrift/parse.py` (`RE_MASTHEAD_BOILERPLATE`, `RE_MASTHEAD_COLUMN`) never match it |
| TLVFS 2011:5 | "...och allmänna **den 20 nov 2011** råd ..." | same: "nov" is not in MONTHS |
| HSLF-FS 2015:17 | "...utbyte av läkemedel **1 2015** m.m" | the PDF's own footnote marker ("m.m.¹;") and the masthead's bare year interleave into the title |
| TLVFS 2012:3 | "...enligt lagen **2012** (2002:160) ..." and "(LFNFS **:2**)" (year dropped) | masthead date/number bleed; the base act's own year (2003) is lost from its parenthetical reference |
| TLVFS 2012:4 | "...och varor **2012** som förskrivs ..." | same masthead bleed |
| TLVFS 2012:5 | "...subvention för **2012** förbrukningsartiklar" and "(TLVFS **:3**)" (year dropped) | same masthead bleed |

One title names the wrong document entirely. `lfnfs/2003:1` (the
grundföreskrift LFNFS 2003:1, whose PDF's own rubric is "Läkemedelsförmånsnämndens
föreskrifter (LFNFS 2003:1) om ansökan och beslut hos
Läkemedelsförmånsnämnden ...") is stored with title "TLV:s föreskrifter
(TLVFS 2003:1) om receptfria läkemedel enligt lagen (2002:160) om
läkemedelsförmåner m.m. (ändras den 1 oktober 2026)" and publisher
"Tandvårds- och läkemedelsförmånsverket". That text is TLV's own panel
heading (`<h2>`), which names a different designation (TLVFS 2003:1, which
does not otherwise appear anywhere in that panel) than the grundföreskrift
anchor it sits over (LFNFS 2003:1). `tlv_enumerate` uses the panel heading
as the grund's title unconditionally, with no check that its own printed
designation (when it has one) agrees with the file it is attached to.

Publisher field carries a masthead line-wrap hyphen or misspelling on 4
documents (from `meta.pop("publisher")` in `parse.py`, not rejoined across
the PDF's own line break):
```
hslffs/2023:25  publisher = "Tandvårds- och läkemedelsförmåns- verket"
tlvfs/2013:8    publisher = "Tandvårds- och läkemedels- förmånsverket"
tlvfs/2014:4    publisher = "Tandvårds- och läkemedels- förmånsverket"
tlvfs/2009:4    publisher = "Tandsvårds- och läkemedelsförmånsverket"   (extra "s")
```

### 6. Consolidations
The site publishes 5 konsoliderad panels (TLVFS 2008:2, TLVFS 2009:3, TLVFS
2011:3, HSLF-FS 2017:29, and the LFNFS 2003:1/TLVFS 2003:2 family). Every
download record for those five base acts carries a non-empty
`files.consolidation`. 5/5, no defect.

### 7. Inherited series
tlvfs: 25/25 site designations present under `kind=tlvfs` (none renumbered
into hslffs). lfnfs: 2/2 site designations present under `kind=lfnfs`
(2003:1, and 2003:4; 2003:2 is filed as the konsolidering of 2003:1, not
its own number, matching the site). `lfnfs/2003:4`'s publisher is correctly
"Läkemedelsförmånsnämnden"; `lfnfs/2003:1`'s publisher is wrongly "Tandvårds-
och läkemedelsförmånsverket" (see the title defect above -- same root
cause, the panel heading text).

### 8. Freshness
Site's newest is HSLF-FS 2026:24 (2026-06-11, in the LFNFS 2003:1 panel's
"Ändrad" list) and HSLF-FS 2026:23 (2026-06-11, its own newest base act).
Both are in the corpus. No lag.

### Not filed again
No instance of #50 or #52 found in this scope (checked the 5 konsoliderad
PDFs' byte content against their grundföreskrift siblings; none matched by
hash).
