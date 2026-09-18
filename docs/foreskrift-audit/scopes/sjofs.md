# SJOFS — SJÖFS, Sjöfartsverket

Verdict: DEFECT
Site list: 186 designations from 22 pages (1 entry page + 21 subject-category pages), https://www.sjofartsverket.se/sv/om-oss/lagrum/systematisk-forteckning-n/
We hold: 181 documents (sjofs), newest SJÖFS 2026:17; site newest SJÖFS 2026:25
Missing: 8 — SJÖFS 2026:18 to 2026:25 (freshness lag, not a parse/harvest defect; see Freshness)
Extra: 3 — SJÖFS 2006:36 (repealed by sjofs/2024:7 and tsfs/2021:90), SJÖFS 2023:4 (repealed by sjofs/2026:17), SJÖFS 2024:7 (a repeal notice itself, legitimately absent from the site's in-force list, but see Title defects — it should carry `upphavande=1` and does not, because its title never parsed)
Repeal gaps: 1 — SJÖFS 1980:8 is marked "Upphävd" on the site with no repealing document identified in our corpus (`upphaver` targets: none). Likely explained by the same root cause as the title defects below, but I could not pin the exact repealing document.
Title defects: 94 of 181 (52%) — two distinct causes, both parse defects:
  1. 68 documents (38%) are a "Bekantgörande av författning som ändrar/upphäver/ersätter en författning i Sjöfartsverkets författningssamling" type. Title is `null` for all 68, and `andrar` is empty for all 68 (0/68), even when the source PDF carries selectable text naming the target regulation (e.g. SJÖFS 2016:4 references "SJÖFS 1999:27" in its own body text, but `andrar` is `[]`). `upphaver` works only 6 of 68 times.
  2. 26 documents (14%) have the page's classification code ("SFH", sometimes "SFH 1.2.1.8") spliced into the middle of an otherwise correct title, e.g. SJÖFS 1996:6: "...säkerhet för SFH människoliv till sjöss" instead of "...säkerhet för människoliv till sjöss". Two of these — SJÖFS 1996:6 and SJÖFS 1999:12 — also appear in issue #76 (pdftotext prints a different designation); that is a separate defect, cited but not refiled.
Consolidations: site no, we hold 0 (matches — no defect)
Inherited: none registered for sjofs. Confirmed pre-2009 and pre-1970 SJÖFS still sit on Sjöfartsverket's own site under the sjofs slug (no relocation needed). Since 2009 many of the "Bekantgörande...ändrar" notices in our sjofs holdings are courtesy copies of Transportstyrelsen (TSFS) acts that amend a SJÖFS regulation; TSFS documents live under our separate `tsfs` slug (839 held), but a spot check of 4 referenced TSFS numbers found only 1 of 4 held there — a tsfs-scope observation, out of scope for this issue.
Issue: https://github.com/staffanm/ferenda/issues/90

## Evidence

### Enumeration
- Fetched the entry page; it links 21 subject-category subpages
  (`a[href*="/systematisk-forteckning-n/"]`, matching `sjofs_enumerate`'s
  `category_select`).
- Fetched all 21 category pages; collected every
  `a[href*="/globalassets/om-oss/lagrum/"][href$=".pdf"]` anchor.
- 207 distinct PDF links; anchor text is the bare designation ("1980:8") for
  regular documents, and 17 use a pre-1970 letter lopnummer ("1952:A9",
  deliberately skipped by `sjofs_enumerate`, see code comment in
  `ferenda/foreskrift/agencies.py`), plus 4 "Rättelseblad"/"Underbilaga"
  anchors that are not their own designation.
- 186 distinct plain "YYYY:N" designations remain — the site list.

### Missing / Extra
    site - ours = {2026:18 .. 2026:25}   (8)
    ours - site = {2006:36, 2023:4, 2024:7}   (3)

Checked `links` for `rpubl:upphaver` targeting each "extra" document's URI:

    sjofs/2006:36  <- rpubl:upphaver <- sjofs/2024:7, tsfs/2021:90
    sjofs/2023:4   <- rpubl:upphaver <- sjofs/2026:17
    sjofs/2024:7   <- rpubl:upphaver <- (none)

2006:36 and 2023:4 are legitimately off the site's in-force list (both
repealed). 2024:7's own artifact
(`site/data/artifact/foreskrift/sjofs/2024-7.json`) shows
`"upphaver": ["https://lagen.nu/sjofs/2006:36"]` — it is itself a repeal
notice, correctly linked as *repealer* of 2006:36, so it belongs off the
site's list too. But `documents.upphavande` is not set to 1 for it, unlike
two other sjofs upphävande-only documents (1996:9, 2006:2) that do carry the
flag. The download record's own PDF filename names the type explicitly:
`sjofartsverkets-foreskrifter-sjofs-2024-7-bekantgorande-som-upphaver-en-forfattning-i-sjofartsverkets-forfattningssamling-2006_36.pdf`.
The reason `upphavande` was not set is the same as the Title defects section:
`metadata.title` is `null`.

### Repeal gaps
Category page 1 ("Säkerheten på fartyg - allmänt") HTML text:

    2008:9  ... Upph 2 §, ändring 2,3 och 5 §§ ...
    2009:33 ... Bekantgörande - Upph
    1980:8  ... Upphävd

    sqlite> select from_uri from links where to_uri='https://lagen.nu/sjofs/2008:9' and predicate like '%upphav%';
    https://lagen.nu/sjofs/2009:33   -- matches the site annotation, no gap

    sqlite> select from_uri from links where to_uri='https://lagen.nu/sjofs/1980:8' and predicate like '%upphav%';
    (0 rows)

I grepped every locally held sjofs regulation PDF (pdftotext) for the string
"1980:8"; several documents cite it as a related/underlying text in a
footnote list (2003:12, 2003:5, 2006:13, ...) but none of the footnote
context reads as an actual repeal ("upphäver"/"ska upphöra att gälla"). I
could not identify which document, if any, formally repeals 1980:8. Given
that 62 of the 68 Bekantgörande documents (see below) have an empty
`andrar`/`upphaver` despite having real target text in the PDF, the
repealing document may well be among them, unresolved by the same defect.

### Title defects — Bekantgörande class (68 of 181)
    sqlite> select count(*) from documents where kind='sjofs' and title=label;
    68

Sample first-page text (pdftotext -layout), all follow one template:

    SJÖFS 2016:4
    Bekantgörande som ändrar en författning i
    Sjöfartsverkets författningssamling ...

For the ones whose PDF has a real text layer (not a raster scan of a TSFS
courtesy copy), the target designation is present in body text yet unused:

    site/data/artifact/foreskrift/sjofs/2016-4.json:
      "title": null, "andrar": [], "upphaver": []
    (PDF body text references "SJÖFS 1999:27" directly)

Across all 68:

    with andrar populated:   0 / 68
    with upphaver populated: 6 / 68

SJÖFS 2012:5 shows `upphaver` extracted correctly (footnote "SJÖFS 2005:7
kan makuleras.") while `title` is still `null` for the same document —
title extraction fails uniformly for this document type even when the
sentence is fully selectable text, e.g.:

    "Bekantgörande av författning som upphäver en författning i
     Sjöfartsverkets författningssamling"

Some of the 68 embed a raster scan of the referenced TSFS document with no
text layer at all (e.g. sjofs/2011:3, sjofs/2009:34 — confirmed visually,
rendered with pdftoppm); for those, `andrar` cannot be recovered from text
without OCR. Others have a full digital text layer and the target
designation appears in running text (e.g. sjofs/2016:4, sjofs/2018:3-2018:14,
sjofs/2017:11-2017:24) — for those, the empty `andrar` is a plain parser
gap, not a source-data limitation.

### Title defects — SFH classification code (26 of 181)
    sqlite> select count(*) from documents where kind='sjofs' and title like '%SFH%';
    26

Rendered SJÖFS 1996:6 page 1 (pdftoppm): the printed title is "...om
säkerhet för människoliv till sjöss" with a two-line classification stamp
"SFH / 1.2.1.8" printed in a side column next to it. Our stored title is:

    "Sjöfartsverkets kungörelse med föreskrifter om livräddningsredskap och
     anordningar på fartyg som inte omfattas av den internationella
     konventionen om säkerhet för SFH människoliv till sjöss"

— "SFH" injected mid-sentence from the side column. Several of the 26 are
further garbled by poor OCR on old scans (e.g. SJÖFS 1996:5, 1997:15,
1999:11), a separate, likely unfixable source-quality issue not raised here.

SJÖFS 1996:6 and SJÖFS 1999:12 (also in this list) are named in issue #76 as
regulation PDFs that print a different designation than the one they are
filed under — a distinct defect, cited here, not refiled.

### Consolidations
    grep -c "konsoliderad" across all 21 fetched category pages: 0
    with-consolidation download records: 0 / 181
No defect: neither the site nor our corpus carries konsoliderade versions.

### Inherited series
No predecessor slug is registered for sjofs. Confirmed the pre-1970
letter-lopnummer documents (1952:A9, 1953:A5, 1963:A10, 1968:A19, ...) still
sit on Sjöfartsverket's own site, not moved to a successor agency's page —
our sjofs slug placement is correct, no relocation needed. Spot-checked 4
TSFS numbers referenced by sjofs Bekantgörande notices against the `tsfs`
catalog (839 documents held):

    TSFS 2011:45  -> not held
    TSFS 2009:105 -> not held
    TSFS 2016:82  -> not held
    TSFS 2012:68  -> held ("Transportstyrelsens föreskrifter om upphävande
                     av Sjöfartsverkets föreskrifter (SJÖFS 2005:7) ...")

This is a tsfs-scope observation (not filed here).

### Freshness
Site's highest 2026 lopnummer is 25; we hold up to 17. All 8 missing
designations (2026:18-25) are a normal harvest-lag gap, not a parser or
harvest-code defect — the enumerate walk visits every category page and
would pick these up on a fresh run.

### Budget
HTTP requests to sjofartsverket.se: 1 (entry) + 21 (categories) + 1 (repeat
fetch of category 1, to inspect raw HTML for repeal annotations) = 23,
well under the 60 request budget. No blocks encountered.
