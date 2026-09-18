# ELSAKFS — ELSÄK-FS, Elsäkerhetsverket

Verdict: DEFECT
Site list: 20 designations from 1 page (https://www.elsakerhetsverket.se/om-oss/lag-och-ratt/foreskrifter-i-nummerordning/)
We hold: 37 documents (elsakfs), newest ELSÄK-FS 2022:3; site newest ELSÄK-FS 2022:3
Missing: 0
Extra: 17 — all amendments; explained by the repeal model (see Evidence)
Repeal gaps: 1 — ELSÄK-FS 2021:7 repeals ELSÄK-FS 2006:1 on its own PDF title, but our record's upphaver list is empty
Title defects: 14 of 37 — 4 hold another document's PDF and text entirely; 5 have no title; 1 has no title and a garbled title; 1 has a table of contents appended; 2 run the title into the enacting clause
Consolidations: site yes (5 konsoliderade versions), we hold 5 (identifiers match exactly)
Inherited: none (assignment lists no predecessor series)
Issue: https://github.com/staffanm/ferenda/issues/47

## Evidence

### 1. Enumeration

Fetched the entry page once with `ferenda.lib.net.make_session`/`request`
(BROWSER_UA). It lists 20 `div.list-item.regulation-item` anchors, static
HTML — confirmed identical after a Playwright chromium render with a 2 s
wait (no "load more", no pagination markup, no `page=`/`sida=` links in the
page). This is the agency's page of currently valid ("Gällande") base
regulations, 20 designations, oldest ELSÄK-FS 2003:3, newest ELSÄK-FS
2022:3.

### 2/3. Missing / extra

All 20 site designations exist among our 37 records — 0 missing.

Our 37 minus the 20 site entries leaves 17 "extra" documents:
2006:1, 2008:1, 2008:2, 2008:3, 2010:1, 2010:2, 2010:3, 2015:1, 2015:3,
2016:4, 2018:1, 2021:1..2021:6.

Checked each against the artifact's `andrar`/`upphaver` and the `links`
table:
- 2008:1, 2008:2, 2008:3, 2015:1, 2018:1 are upphävda by documents we hold
  (2022:1, 2022:2, 2022:3, 2019:1 respectively — confirmed via
  `metadata.upphaver` in the artifact JSON).
- 2010:1, 2010:2, 2010:3, 2015:3, 2016:4, 2021:1..2021:6 are amendments
  (`rpubl:andrar` / `rinfoex:andradAv` links to their base regulation or to
  the consolidated version) — folded into a `(konsoliderad version)` page
  the site does show, or into a base regulation that is itself now
  repealed. This matches the briefing's "site lists only in-force text"
  shape. Not a defect.

One exception surfaced during this check (see Repeal gaps).

### 4. Repeal gaps

`site/data/downloaded/foreskrift/elsakfs/elsakfs-2021-7-regulation.pdf`
(fetched live to confirm) reads:

    Elsäkerhetsverkets föreskrifter om upphävande av Elsäkerhetsverkets
    föreskrifter och allmänna råd (ELSÄK-FS 2006:1) om elsäkerhet vid
    arbete i yrkesmässig verksamhet.

So ELSÄK-FS 2021:7 repeals ELSÄK-FS 2006:1. We hold both documents. But:

    $ python -c "
    from ferenda.lib.compress import read_text
    import json
    print(json.loads(read_text('site/data/artifact/foreskrift/elsakfs/2021-7.json'))['metadata']['upphaver'])
    "
    []

`documents.upphavande` for elsakfs/2021:7 is also `None` in the catalog.
The repealing document exists in our corpus; its `upphaver` list should
contain `https://lagen.nu/elsakfs/2006:1` and does not. A parse defect
per the briefing's repeal-gap shape.

### 5. Titles

Sampled/verified 16 of 37 titles against the agency's own PDF or landing
page text (fetched with `ferenda.lib.net.request`, 2 s apart; a handful of
old `-20151-`/`-20181-` style URLs now redirect-loop on the agency's own
site — not checked, not counted as a defect).

**Wrong document entirely** (identifier says one designation, the stored
PDF/text/date is another's):

| designation | we hold (title/date) | actual PDF at that basefile |
|---|---|---|
| ELSÄK-FS 2008:1 | "...ändring i...(2008:1)...", 2015-09-23 | ELSÄK-FS 2015:3 (md5 3f0cf6c0) |
| ELSÄK-FS 2010:1 | same title/date as above | ELSÄK-FS 2015:3 (same md5 3f0cf6c0) |
| ELSÄK-FS 2008:2 | "...ändring i...(2008:2)...", 2010-04-15 | ELSÄK-FS 2010:2 (md5 cc0077d7) |
| ELSÄK-FS 2008:3 | "...ändring i...(2008:3)...", 2010-05-07 | ELSÄK-FS 2010:3 (md5 6ec266cd) |

Confirmed by md5 of the stored PDF
(`site/data/downloaded/foreskrift/elsakfs/elsakfs-<basefile>-regulation.pdf`)
against a fresh fetch of the *live* URL the agency currently serves for
2008:1/2008:2/2008:3/elsak-2010-1: the live PDFs are four genuinely
different, correct documents (2008:1 beslutad 2008-01-31; 2008:2 beslutad
2008-01-31; 2008:3 beslutad 2008-01-31; 2010:1 beslutad 2010-04-16), none
matching what we hold. Our stored 2008:1/2010:1 pair is a duplicate of
2015:3; our 2008:2 is a duplicate of 2010:2; our 2008:3 is a duplicate of
2010:3. `metadata.structure` for elsakfs/2008:1 is 2015:3's article text
("beslutade den 23 september 2015 ... 4 kap. 3 a §"), not 2008:1's. All
four records carry `"source": "myndfs-legacy"` in their download record —
an older import path, not the current `indexed_enumerate`/`resolve_landing`
engine.

**Missing title** (title = the bare designation, e.g. "ELSAKFS 2011:4"):
ELSÄK-FS 2011:4, 2012:1, 2016:3, 2017:3, 2017:4 (all `(konsoliderad
version)` landing pages) and 2021:7. Site text for 2011:4's landing page
gives the real title plainly: "Elsäkerhetsverkets föreskrifter om anmälan
av ibruktagande av en kontaktledning" — confirmed the same shape for
2012:1, 2016:3, 2017:3, 2017:4. `metadata.title` is `None` in every one of
these six artifacts.

**Garbled title**: ELSÄK-FS 2003:3. We hold "...om upphävande av verkets
föreskrifter (ELSAK-FS 1995:4) om **kom fånt det** elektrisk utrustning
avsedd för explosionsfarlig miljö" — "kom fånt det" is inserted mid
sentence and does not appear in the agency's own text ("...om upphävande
av verkets föreskrifter (ELSÄK-FS 1995:4) om elektrisk utrustning avsedd
för explosionsfarlig miljö.", confirmed on the live landing page).

**Table of contents appended**: ELSÄK-FS 2006:1. We hold "...om
elsäkerhet vid arbete i yrkesmässig verksamhet **Innehållsförteckning
Allmänna bestämmelser Tillämpningsområde God elsäkerhetsteknisk praxis**"
— the real title (confirmed from 2021:7's own repeal text, which quotes
it) ends at "...yrkesmässig verksamhet."; the rest is the document's own
table-of-contents headings, folded into the title.

**Title runs into the enacting clause**: ELSÄK-FS 2015:1 and 2016:4. The
PDF's own masthead reads (2015:1):

    Elsäkerhetsverkets föreskrifter om stickproppar och uttag för
    allmänbruk

    beslutade den 23 september 2015.

with no semicolon before "beslutade" — we hold the whole run-on sentence
including "Elsäkerhetsverket meddelar följande föreskrifter1 med stöd
av...". Same shape for 2016:4 (title runs through "...ska införas fyra
nya paragrafer, 5 kap. 7 a, 16 a, 19 a och 22 §§, av följande lydelse.").
`ferenda/foreskrift/parse.py`'s `title_from_masthead` says it stops "up to
the semicolon or the beslutade/utfärdad clause" — these two PDFs have
neither a semicolon nor triggered that stop.

### 6. Consolidations

The site's own nummerordning page lists five `(konsoliderad version)`
entries: 2011:4, 2012:1, 2016:3, 2017:3, 2017:4. We hold exactly five
download records with a non-empty `files.consolidation`, for the same
five identifiers (`ELSAKFS 2011:4`, `2012:1`, `2016:3`, `2017:3`,
`2017:4`). Match — no defect.

### 7. Inherited series

None listed for this scope.

### 8. Freshness

Site newest: ELSÄK-FS 2022:3 (top of the nummerordning listing, confirmed
no 2023+ designation anywhere in the fetched HTML). Our newest: ELSÄK-FS
2022:3. Match.

## Commands used

    from ferenda.lib.net import make_session, request, BROWSER_UA
    session = make_session(BROWSER_UA)
    request(session, "GET", <index/landing/pdf url>)   # ~14 HTTP requests total
    # + 1 Playwright chromium page.goto to confirm no JS-loaded pagination

    sqlite3.connect("file:site/data/catalog.sqlite?mode=ro", uri=True)
    select * from documents where kind='elsakfs'
    select from_uri, predicate from links where to_uri=?

    from ferenda.lib.compress import read_text
    read_text("site/data/artifact/foreskrift/elsakfs/<basefile>.json")
    read_text("site/data/downloaded/foreskrift/elsakfs/elsakfs-<basefile>.json")

    md5sum site/data/downloaded/foreskrift/elsakfs/elsakfs-<basefile>-regulation.pdf
    pdftotext -layout <pdf> -
