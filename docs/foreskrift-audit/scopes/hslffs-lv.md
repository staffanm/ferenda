# HSLFFS-LV — HSLF-FS, Läkemedelsverket

Verdict: DEFECT
Site list: 655 designations from 1 page (Atom feed, https://www.lakemedelsverket.se/api/rss/Rss/?pageId=4741; entry page https://www.lakemedelsverket.se/sv/lagar-och-regler/foreskrifter renders no list to a plain client, per the docstring in `ferenda/foreskrift/hslffs.py`)
We hold: hslffs (publisher=Läkemedelsverket) 233, lvfs 420, sosfs (LV-issued) 1 — 654 total; newest HSLF-FS 2026:26, LVFS 2015:8; site newest HSLF-FS 2026:26, LVFS 2015:8
Missing: 2 — LVFS 2005:13; HSLF-FS 2023:25 (the real Läkemedelsverket document; see Extra)
Extra: 0 genuine. LVFS 2015:4 is held, but it duplicates HSLF-FS 2015:4 (byte-identical PDF, both filed) — already covered by #50.
Repeal gaps: 0 confirmed. All 5 spot-checked explicit "upphävande"-titled documents carry correct `upphaver` targets. The site's broader "Upphävd föreskrift" category (370 entries, mostly ändringsföreskrifter marked spent, not repealed by a named act) is a status label, not a discrete repeal — out of the repeal model's scope per the briefing.
Title defects: 1 — HSLF-FS 2023:25 shows title "(omtryckt)", publisher "Tandvårds- och läkemedelsförmåns- verket" (TLV), not Läkemedelsverket's real title. Also two LVFS records carry OCR-garbled publisher strings ("Låkemedelsverket", "Täkemedelsverket") baked into the metadata (LVFS 1994:15, LVFS 1999:10) — minor, not filed.
Consolidations: site yes (48 konsoliderade texts named in the feed), we hold 11 of 48 targets with `files.consolidation` populated. Looks like staleness (older records pre-date the consolidation-capture code — HSLF-FS 2021:54, harvested more recently, has it; LVFS 2011:9, LVFS 2004:10 do not) rather than a new code bug. Not filed.
Inherited: lvfs — 420 of 420 site designations sit under the `lvfs` slug and match; one (LVFS 2015:4) is also separately held under `hslffs/2015:4`, a case of #50, not a new defect.
Issue: https://github.com/staffanm/ferenda/issues/55

## Evidence

### Enumeration
`ferenda/foreskrift/hslffs.py`'s own docstring: Läkemedelsverket's `api/provisionlist` JSON endpoint answers 200 with an empty body to a plain HTTP client, so the harvest reads the Atom feed at `LV_FEED_URL` instead. Fetched once:

    from ferenda.lib.net import make_session, request, HARVESTER_UA
    session = make_session(HARVESTER_UA)
    r = request(session, "GET", "https://www.lakemedelsverket.se/api/rss/Rss/?pageId=4741")
    # 200, 472429 bytes, 707 <entry> elements

Parsed the feed with the module's own `plain()`/`numbered()`/`lv_slug_number()`/`samling()` helpers, replicating `lv_enumerate`'s dedup (`docref`'s `seen` set, konsoliderade entries tagged by category and attached separately):

- 707 entries total: 659 grundföreskrift/ändringsföreskrift entries, 48 konsoliderade attachments.
- After dedup (feed carries 4 rows twice — HSLF-FS 2022:41, 2021:92, 2021:75, LVFS 2006:11 — first occurrence, newest-first, kept, matching `docref`'s behaviour): 655 unique basefiles — 234 hslffs, 420 lvfs, 1 sosfs (SOSFS 1991:5, the one document Socialstyrelsen issued on medicines before the responsibility moved, named in `agencies.py`'s comment on `HSLFFS_LV`).

### Catalog comparison

    sqlite3.connect("file:site/data/catalog.sqlite?mode=ro", uri=True)
    select uri, kind, label, title, date, publisher from documents
      where kind in ('hslffs','lvfs','sosfs')

Matched by `<fs>/<year>:<lop>` parsed off `uri`, against the site set restricted to
`kind='hslffs' and publisher='Läkemedelsverket'`, all of `kind='lvfs'`, and
`sosfs/1991:5`: catalog has 653 matching rows for a site set of 655.

Diff:
- Missing (site has, catalog doesn't): `hslffs/2023:25`, `lvfs/2005:13`.
- Extra (catalog has, site doesn't as a distinct entry): `lvfs/2015:4`.

### HSLF-FS 2023:25 — real LV document displaced by a TLV mis-numbering

The feed's own entry for HSLF-FS 2023:25 (published 2023-06-21):
"Läkemedelsverkets föreskrifter (HSLF-‍‍FS 2023:25) om supplement 11.2 till
Europafarmakopén", linking `.../foreskrifter/2023-25/`.

But `site/data/artifact/foreskrift/hslffs/2023-25.json` holds a different
document entirely:

    identifier: "HSLF-FS 2023:25"
    publisher: "Tandvårds- och läkemedelsförmåns- verket"
    title: "(omtryckt)"
    source_url: "https://www.tlv.se/om-tlv/regelverk/foreskrifter.html"

and the download record `site/data/downloaded/foreskrift/hslffs/hslffs-2023-25.json`
fetches its regulation PDF from
`https://www.tlv.se/download/.../hslf_fs_2023_35.pdf` — the file name says
**2023_35**, not 2023:25. TLV's link text on its own föreskrifter page read
"HSLF-FS 2023:25 (omtryckt) pdf, 1 MB." (per the stored `title` field of that
download record), so either TLV's own page misprints the number, or the
enumeration read the wrong number off it; either way the slot `hslffs/2023:25`
in our corpus holds this document instead of Läkemedelsverket's real one, and
`hslffs/2023:35` does not exist in the corpus at all:

    select uri,label,title,publisher from documents where uri='https://lagen.nu/hslffs/2023:35'
    -- []

This is a `hslffs-tlv` numbering problem with a direct impact on `hslffs-lv`:
Läkemedelsverket's real HSLF-FS 2023:25 is entirely missing from the corpus,
displaced by another agency's document under the same basefile.

### LVFS 2005:13 — genuine harvest gap

Feed entry (published 2005-12-19): "Föreskrifter (LVFS ­2005:13) om ändring i
Läkemedelsverkets föreskrifter (LVFS 1993:2) om förbud och begränsningar för
vissa ämnen att ingå i kosmetiska eller hygieniska produkter". `numbered()`
reads it correctly as `('LVFS', '2005', '13')`. Not in the catalog under any
label or uri; the neighbours LVFS 2005:12 and LVFS 2005:14 are both present.
No trace of the document under any other slug.

### LVFS 2015:4 — duplicate of HSLF-FS 2015:4, already #50

The single feed entry at slug `.../foreskrifter/2015-4/` prints its own
number as "HSLF‍-‍FS 2015:4" (`numbered()` reads the *first* number in the
title, which the harvest treats as authoritative), and the harvest files it
as `hslffs/2015:4`. The catalog nonetheless also holds a `lvfs/2015:4` with
matching `beslutsdatum` (2015-05-12), `ikrafttradandedatum` (2015-06-09),
`utkomFranTryck` (2015-05-27) and `andrar` target. The two regulation PDFs are
byte-identical:

    md5sum site/data/downloaded/foreskrift/lvfs/lvfs-2015-4-regulation.pdf \
           site/data/downloaded/foreskrift/hslffs/hslffs-2015-4-regulation.pdf
    83a791ad0b130c09a7d0696bf3cd49ef  (both)

`hslffs`/`lvfs` is explicitly listed as affected by #50 ("a predecessor-series
document also minted under the successor slug"), so this is that pattern, not
a new issue.

### Repeal marking

Sampled the 26 feed entries whose *title* names an explicit "upphävande" act
(not just a status category) — these are the discrete repeal acts the corpus
model can represent. Checked 5:

    HSLF-FS 2022:31 om upphävande av LVFS 2001:7  -> upphaver: [lvfs/2001:7]  OK
    HSLF-FS 2021:26 om upphävande av HSLF-FS 2019:43 -> upphaver: [hslffs/2019:43]  OK
    LVFS 2014:13 om upphävande av LVFS 1996:10    -> upphaver: [lvfs/1996:10]  OK
    HSLF-FS 2022:30 om upphävande av LVFS 2001:5  -> upphaver: [lvfs/2001:5]  OK
    HSLF-FS 2022:32 om upphävande av LVFS 2003:11 -> upphaver: [lvfs/2003:11] OK

All correct. Separately, the feed's `<category>` tag "Upphävd föreskrift"
marks 370 entries (mostly ändringsföreskrifter and superseded
grundföreskrifter), of which a random sample of 15 showed only 6 with an
`rpubl:upphaver` link pointing at them. Per the briefing's repeal model, that
is not by itself a defect: an ändringsföreskrift naturally has no discrete
"repealing document" once its changes are absorbed, and the site's status
category does not correspond 1:1 with the corpus's `upphaver` relation. No
issue filed for this category.

### Titles

Scanned all 653 matched titles for junk patterns (empty, PDF filename, file
size, HTML entity, label-only, too-short) — 0 hits by pattern, but
`hslffs/2023:25`'s title "(omtryckt)" is junk by inspection (see above; it
belongs to a different document than the one Läkemedelsverket actually
published under that number).

Two LVFS artifacts carry OCR-garbled `publisher` values baked into the
metadata rather than the printed "Läkemedelsverket": LVFS 1994:15
("Låkemedelsverket"), LVFS 1999:10 ("Täkemedelsverket"). Not filed — minor,
and out of the eight checks' scope (title, not publisher), noted for
visibility only.

### Consolidations

    select files.consolidation from downloaded records, matched against
    the 48 base acts the feed names as having a konsoliderad text.

11 of 653 held records carry `files.consolidation`; cross-checked against the
48 feed targets specifically: only a handful match (e.g. HSLF-FS 2021:54 has
it, LVFS 2011:9 and LVFS 2004:10 -- both in the 48 -- do not). The newer
record has it and the older ones don't, consistent with staleness (a pending
re-harvest) rather than a code defect. Not filed.

### Freshness

Site newest: HSLF-FS 2026:26 (2026-06-25), LVFS 2015:8 (2015-06-15, LVFS
closed 2015-07-01). Catalog newest: HSLF-FS 2026:26, LVFS 2015:8. Match.

### Requests used

7 of the 60-request budget: 1 Atom feed fetch, 2 individual document pages
(`.../foreskrifter/2020-11/`, `.../foreskrifter/2015-3/`, both fetched to
check whether "Upphävd föreskrift" names a discrete repealing act on the page
itself — it does not; the flag lives only in the feed's `<category>`), plus a
handful of local artifact/download-record reads (no HTTP).
