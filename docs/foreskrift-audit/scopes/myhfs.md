# MYHFS — Myndigheten för yrkeshögskolan
Verdict: DEFECT
Site list: 80 designations from 2 pages (gällande 34 + upphävda 46) (https://www.myh.se/lag-och-ratt/foreskrifter-och-allmanna-rad/gallande-foreskrifter-och-allmanna-rad)
We hold: 60 documents (myhfs), newest MYHFS 2026:5; site newest MYHFS 2026:5
Missing: 19 confirmed absent (plus 9 more present only as wrong content under another label) — 2009:3, 2011:1, 2012:2, 2013:1, 2013:3, 2014:1, 2014:2, 2015:1, 2015:5, 2016:4
Extra: 8 — 2013:2, 2013:5, 2014:4, 2015:4, 2016:12, 2017:6 (legitimate: "Förteckning" funding lists, not föreskrifter); 2016:17, 2018:1 (legitimate: pure-repeal decisions, listed on neither site page)
Repeal gaps: 0 in the rpubl:upphaver sense — all repeal links resolve correctly relative to the (wrong) identifiers we hold; the real defect is the identifiers themselves (see Title defects)
Title defects: 17 — 10 mislabeled/wrong-content documents (identifier AND title swapped for the first item in the document's own repeal list), 7 with title=None falling back to the bare identifier
Consolidations: site no, we hold 0
Inherited: none (per assignment)
Issue: https://github.com/staffanm/ferenda/issues/74 (filed)

## Evidence

### Enumeration
- Fetched the entry page (gällande) and the sibling "Upphävda föreskrifter och allmänna råd" page
  (https://www.myh.se/lag-och-ratt/foreskrifter-och-allmanna-rad/upphavda-foreskrifter-och-allmanna-rad),
  linked from the entry page's own nav. The harvest's `index_url` in
  `ferenda/foreskrift/agencies.py:1699-1704` visits only the gällande page.
- Both pages carry an explicit "MYHFS-nummer: <year>:<lop>" annotation next to every PDF link — the
  page states each document's own designation in plain text, independent of the PDF filename.
- gällande: 34 links, all with a MYHFS-nummer match. upphävda: 46 links, all matched. No designation
  overlaps between the two pages. Union: 80 distinct designations.

### Catalog query
    .venv/bin/python -c "
    import sqlite3
    con = sqlite3.connect('file:site/data/catalog.sqlite?mode=ro', uri=True)
    print(len(con.execute(\"select * from documents where kind='myhfs'\").fetchall()))
    "
    -> 60

### Missing (site has, we hold nothing under that label)
19 designations exist only on the site: 2009:3, 2011:1, 2012:2, 2013:1, 2013:3, 2014:1, 2014:2,
2015:1, 2015:5, 2016:4, 2016:5, 2016:6, 2016:7, 2016:15, 2016:16, 2022:1, 2022:2, 2022:6, 2024:1.
17 of these 19 sit on the upphävda page only, unreachable by the current harvest (see Missing archive,
below). 2024:1 sits on the gällande page but its PDF filename carries no digits
(`myndigheten-for-yrkeshogskolans-foreskrifter-om-utbildningar-inom-yrkeshogskolan-med-inriktning-instrument-och-steriltekniker.pdf`),
so `RE_MYHFS_FILE` in `ferenda/foreskrift/agencies.py:1678` cannot key it and `myh_enumerate` skips it
by design (see the comment at line 1673: "those cannot be keyed to a basefile from the listing and are
skipped"). 2022:6 is on the gällande page too but was likewise skipped for the same reason.

### Missing archive (the recurring pattern from AFS #45, EIFS #51, IAFFS #57, KIFS #65, MCFFS #71)
The entry page's own top nav links to "Upphävda föreskrifter och allmänna råd", a second listing of 46
repealed instruments. `myh_enumerate` only reads `agency.index_url` (the gällande page) and never
visits this second page. Every document that appears solely on the upphävda page is invisible to the
harvest.

### Wrong content under 10 labels (identifier + title hijacked from the first item of the document's
own repeal list)
For 10 of our 60 documents, the identifier we store, and usually the title, actually belong to a
DIFFERENT, earlier document that the real document repeals — not to the PDF we hold. The PDF's own
masthead (verified with `pdftotext -layout`) always carries the true designation, e.g.:

    MYHFS 2014:3                                    <- masthead, right-aligned
    Myndigheten för yrkeshögskolans föreskrifter om
    upphävande av vissa föreskrifter som rör ansökan om
    utbildningar inom myndighetens ansvarsområde;
    ...
    1. Myndigheten för yrkeshögskolans föreskrifter MYHFS 2010:1 om
       ansökan om att en utbildning ska ingå i yrkeshögskolan ...

Our catalog stores this PDF under identifier "MYHFS 2010:1" — the FIRST repealed item's designation —
instead of "MYHFS 2014:3", its own. The title stored is likewise the first item's description, not the
document's real subject ("upphävande av vissa föreskrifter ...").

| label we store | real designation (site + PDF masthead) | our title vs. real title |
|---|---|---|
| MYHFS 2009:2 | MYHFS 2021:2 | ours: "...föreskrift (MYHFS 2009:2) om datum för ansökan..." / real: "Förordning om upphävande av Myndigheten för yrkeshögskolans föreskrift (MYHFS 2009:2) om datum för ansökan..." |
| MYHFS 2010:1 | MYHFS 2014:3 | ours: "...om upphävande av vissa föreskrifter..." / real (2010:1's own subject): "...om ansökan att en utbildning ska ingå i yrkeshögskolan med start 1 juli 2011–30 juni 2012" |
| MYHFS 2010:2 | MYHFS 2011:2 | ours: "...om upphävande av två föreskrifter" / real (2010:2's own subject): "...om kursplan för trafiklärareutbildning i yrkeshögskolan" |
| MYHFS 2012:3 | MYHFS 2021:3 | ours: "...om uppgiftsinsamling..." (this is 2012:3's own real subject, correct title, wrong identifier attached to the 2021:3 PDF) |
| MYHFS 2015:3 | MYHFS 2022:6 | ours: title=None -> falls back to "MYHFS 2015:3" / real (2022:6): "Upphävande av ... allmänna råd om lärande i arbete (MYHFS 2015:3)" |
| MYHFS 2016:1 | MYHFS 2021:4 | ours: "...om resultat av lärande..." (2016:1's own real subject, wrong identifier attached to the 2021:4 PDF) |
| MYHFS 2016:8 | MYHFS 2023:4 | ours: "...om studiedokumentation" (2016:8's own real subject, wrong identifier attached to the 2023:4 PDF) |
| MYHFS 2016:14 | MYHFS 2020:3 | ours: "...ska ha krav på särskilda förkunskaper" (2016:14's own real subject, wrong identifier attached to the 2020:3 PDF) |
| MYHFS 2017:3 | MYHFS 2018:3 | ours: "...om ansökan om behörighetsgivande förutbildning..." (2017:3's own real subject, wrong identifier attached to the 2018:3 PDF) |
| MYHFS 2022:5 | MYHFS 2023:5 | ours: "...för specialiserande yrkeshögskoleutbildningar för undersköterskor" / real (2022:5's own subject): "...för utbildningar inom yrkeshögskolan med inriktning specialistundersköterska inom vård och omsorg av äldre" |

All 10 download records (`site/data/downloaded/foreskrift/myhfs/myhfs-<label>.json`) carry
`"source": "myndfs-legacy"` — a marker left by a one-time legacy-corpus import (its script,
`accommodanda/foreskrift/legacy.py`, was later deleted; commit `34c428b8` added it, `8daa75c4`
"tear down the frozen-legacy import scaffolding" removed it). 37 of our 60 myhfs records carry this
marker; the other 27 come from the live `myh_enumerate` harvest and show no such mismatch. The bad
(basefile, url) pairing is frozen in `site/data/downloaded` and is never revisited by the live
pipeline, since `myh_enumerate` only adds new PDFs it hasn't seen and never re-checks existing
basefiles' URLs.

Net effect: our catalog has no correct record at all for MYHFS 2011:2, 2014:3, 2018:3, 2020:3, 2021:2,
2021:3, 2021:4, 2022:6 and 2023:4/2023:5 — their real content sits mislabeled under an earlier
document's number instead.

### Other title defects (title=None, falls back to the identifier string)
7 documents show `metadata.title: null` in the artifact JSON and the catalog falls back to the bare
identifier as the displayed title: MYHFS 2012:1, 2013:2, 2013:5, 2014:4, 2015:4, 2016:12, 2017:6.
Six of these (2013:2, 2013:5, 2014:4, 2015:4, 2016:12, 2017:6) are "Förteckning över utbildningar som
beviljats statligt stöd" administrative funding lists, correctly identified and legitimately absent
from the site's föreskrift listings (not real föreskrifter) — the missing title is a smaller, separate
parse defect. MYHFS 2012:1 is a genuine föreskrift with a correct identifier and URL but no extracted
title.

### Extra documents (legitimate)
- 2013:2, 2013:5, 2014:4, 2015:4, 2016:12, 2017:6: "Förteckning" funding-award lists, not föreskrifter
  proper — the site's föreskrift listings correctly omit them.
- 2016:17, 2018:1: pure-repeal decisions that repeal an earlier document and are not themselves
  repealed, so they appear on neither the gällande nor upphävda page. Legitimate.

### Repeal marking
Checked `links` rows with predicate `rpubl:upphaver` for myhfs. Every repealing document we hold has a
non-empty `upphaver` list, and none of the targets fail to resolve to a URI form — but because 10 of
our identifiers are wrong (above), the repeal graph inherits the same mislabeling (e.g. our
"MYHFS 2010:1" claims to repeal 2011:1/2012:2/2013:1/2013:3, but it is really MYHFS 2014:3's content,
and the genuine MYHFS 2010:1 document is entirely absent from our corpus). This is the same defect as
the identifier swap, not a separate repeal-parsing gap.

### Consolidations
No "konsoliderad" text found on either site page; no `files.consolidation` entries in any of our 60
download records. No defect.

### Freshness
Site newest: MYHFS 2026:5 (2026-05-26, gällande page, in force 2026-07-01). We hold MYHFS 2026:5 with
matching title and date. Up to date.

### Inherited series
None declared for this scope.
