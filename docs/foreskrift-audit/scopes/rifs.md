# RIFS — Revisorsinspektionens föreskrifter, Revisorsinspektionen

Verdict: DEFECT
Site list: 39 designations from 1 page (https://www.revisorsinspektionen.se/regelverk/samtliga-foreskrifter/)
We hold: 14 rifs + 25 rnfs = 39 documents; newest RIFS 2025:2; site newest RIFS 2025:2
Missing: 0
Extra: 0
Repeal gaps: 2 — RIFS 2018:2 should upphäva RNFS 2001:2 (missing); RIFS 2018:3 should upphäva RNFS 2001:3 (missing)
Title defects: 4 — see table below
Consolidations: site yes (3 "senaste lydelsen" RNFS PDFs), we hold 3 (used as the `regulation` file, matches site's own design)
Inherited: rnfs — 25 documents sit under their own `rnfs` slug, not renumbered into rifs; site's own RNFS list (25 designations, 1996:1–2017:1) matches exactly what we hold
Issue: https://github.com/staffanm/ferenda/issues/85

## Evidence

### 1. Enumerate
The entry page is one page, no pagination. It lists three sections: "Revisorsinspektionens
föreskrifter (RIFS) Fr.o.m. 2018-07-01", "Senaste lydelserna av RNFS" (3 konsoliderade PDFs),
and "Samtliga RNFS inklusive ändringsföreskrifter" (the rest of RNFS 1996–2017).

    .venv/bin/python3 -c "
    from ferenda.lib.net import make_session, request, BROWSER_UA
    s = make_session(BROWSER_UA)
    r = request(s, 'GET', 'https://www.revisorsinspektionen.se/regelverk/samtliga-foreskrifter/')
    open('rifs_index.html','w').write(r.text)"
    # then BeautifulSoup with the harvest's own selector:
    # a[href*="/regelverk/"][href$=".pdf"], skip Förarbeten/Hemställan

This yields exactly 14 RIFS + 25 RNFS designations (the harvest's own `skip_re` drops
"Förarbeten"/"Hemställan" companion PDFs, which are not föreskrifter). Every one of these
39 designations matches a row in `documents` for kind in (rifs, rnfs).

    sqlite3.connect("file:site/data/catalog.sqlite?mode=ro", uri=True)
    select count(*) from documents where kind='rifs'  -- 14
    select count(*) from documents where kind='rnfs'  -- 25

### 2/3. Missing / extra
Set difference between the site's 39 designations and the 39 rows in `documents` is empty
both ways. No missing, no extra.

### 4. Repeal marking
The entry page is titled "Samtliga ändringsföreskrifter" — a list of every issued regulation,
not a gällande-only list. It never marks any document upphävd/struck through, so there is no
site-side repeal marker to check against. We instead checked our own `rpubl:upphaver` graph
for completeness against each PDF's own ikraftträdande clause, since RIFS explicitly repeals
three named RNFS predecessors when it takes over each area.

    select from_uri, to_uri from links where predicate='rpubl:upphaver'
      and (from_uri like '%/rifs/%' or from_uri like '%/rnfs/%');
    -- rifs/2018:1 -> rnfs/1996:1   (revisorsexamen)
    -- rifs/2019:1 -> rifs/2018:3   (auktorisation/godkännande, superseded within rifs)
    -- rnfs/2001:2 -> rnfs/1997:1

RIFS 2018:1, 2018:2 and 2018:3 each carry an identical closing clause naming the RNFS
regulation they replace:

    rifs/2018:1: "...när Revisorsnämndens föreskrifter (RNFS 1996:1) om utbildning och
                  prov ska upphöra att gälla."                        -> captured
    rifs/2018:2: "...när Revisorsnämndens föreskrifter (RNFS 2001:2) om villkor för
                  revisorers och registrerade revisionsbolags verksamhet ska upphöra
                  att gälla."                                          -> NOT captured
    rifs/2018:3: "...när Revisorsnämndens föreskrifter (RNFS 2001:3) om auktorisation,
                  godkännande och registrering ska upphöra att gälla." -> NOT captured

Root cause, traced in `ferenda/foreskrift/parse.py`'s `_repeal_object`/`RE_PROVISION`
guard: for 2018:2 and 2018:3 the captured repeal-object window (bounded by
`RE_UPPHOR_BEFORE`, 2500 chars) also contains an earlier, unrelated numbered paragraph
from the regulation's own body (rifs/2018:2's own "23 §", rifs/2018:3's own "5 §"), because
the underscore divider line before the closing "Dessa föreskrifter träder i kraft..."
sentence is not recognized as a sentence boundary. `RE_PROVISION` (`\d+\s*§|...`) then
matches that unrelated "N §" and the code treats the whole match as a provision-level
repeal ("N § ... ska upphöra att gälla") rather than a regulation-level one, and drops
the RNFS reference entirely — even though the same regulation reference is correctly
picked up and linked in `structure` (`dcterms:references` to rnfs/2001:2). Reproduced
directly against the regex chain:

    import ferenda.foreskrift.parse as P
    # captured object item for 2018:2, before the RE_PROVISION check:
    # '...23 § Revisorer och registrerade... (RNFS 2001:2) om villkor för...verksamhet '
    # RE_PROVISION.search(item[:first.start()]) is True because of the "23 §"
    # -> the whole item is skipped, upphaver stays empty

### 5. Titles
Ten titles sampled directly (all rifs, plus the six rnfs docs that show anomalies).
Two distinct defects surfaced:

| designation | PDF masthead / body says | we hold |
|---|---|---|
| RIFS 2024:2 | "...villkor för revisorers och registrerade re-\nvisionsbolags verksamhet" (line-wrap hyphen) | "...re- visionsbolags verksamhet" (hyphen+space kept, not rejoined) |
| RIFS 2025:2 | same line-wrap pattern | same defect |
| RNFS 2005:1 | body (OCR): "...Revisorsnärmndens föreskrifter om utbildning och prov (RNES 1996:1)" | "...Revisorsnämmdens föreskrifter om utbildning och prov (RNES 1996:" — truncated before ":1)" |
| RNFS 2005:2 | body (OCR, correctly linked): "...verksamhet (RNFS 2001:2) skall införas..." | "...verksamhet (" — truncated, drops the designation entirely |
| RNFS 2014:1 | ordinary ändringsföreskrift, one clean designation | "Föreskrifter om ändring i Revisorsnämndens föreskrifter om utbildning och prov den föreskrifter (RNFS 1996:1) om utbildning (RNFS 1996:1)" — duplicated/garbled phrase |

RNFS 2005:1 and 2005:2 are scanned image PDFs (`pdftotext` returns 0-1 bytes; `pdfinfo`
shows Creator "CanoScan 9900F"), so their title comes from OCR of the scan. The OCR itself
has recognition noise (RNES for RNFS, "$$" for "§§") which is expected for a 2005 flatbed
scan and not itself a corpus defect. The truncation is a separate, real defect: the title
cuts off mid-parenthesis even though the *same* designation is correctly recovered, with a
working link, in the document's own `structure` (`dcterms:references` -> rnfs/2001:2 for
2005:2). The title-extraction path and the body reference-extraction path disagree on the
same OCR text.

    from ferenda.lib.compress import read_text
    import json
    json.loads(read_text("site/data/artifact/foreskrift/rnfs/2005-2.json"))["metadata"]["title"]
    # 'Föreskrifter om ändring i Revisorsnämndens föreskrifter om villkor för revisorers
    #  och registrerade revisionsbolags verksamhet ('
    json.loads(read_text("site/data/artifact/foreskrift/rnfs/2005-2.json"))["structure"][0]["children"][1]["text"]
    # [...'Revisorspämndens föreskrifter om villkor...verksamhet (',
    #    {'predicate': 'dcterms:references', 'text': 'RNFS 2001:2', 'uri': '.../rnfs/2001:2'}, ...]

Other 8 titles sampled (RIFS 2018:1/2/3/4, 2019:1/2, 2020:1/2) read clean and match their
PDF mastheads exactly.

### 6. Consolidations
The site publishes three "Senaste lydelsen (konsoliderad version)" PDFs for the oldest base
RNFS regulations (1996:1, 2001:2, 2001:3) instead of the original as-enacted text — it does
not offer both. Our download records store that same konsoliderad PDF as the `regulation`
file for those three designations (`files.consolidation` stays empty; by design, per the
comment in `ferenda/foreskrift/agencies.py` above `RIFS`: "RNFS base regs are listed
konsoliderad-first, so dedup keeps the in-force text"). This matches what the site actually
offers — no defect.

### 7. Inherited series (RNFS)
The site's own listing groups 25 RNFS designations (1996:1 through 2017:1) as the
predecessor series, ending when RIFS took over on 2018-07-01. We hold exactly those 25 under
the `rnfs` fs, not renumbered into `rifs`. No RNFS/RIFS PDF pair is byte-identical
(`md5sum` across both directories has no collisions), so issue #50's "predecessor doc minted
under the successor slug" class and issue #52's "document stores another document's PDF"
class do not apply here.

### 8. Freshness
Site's newest RIFS entry is RIFS 2025:2 (12 September 2024... no: dated in masthead
"beslutade" with utkom 2025-12-xx). We hold RIFS 2025:2 as our newest, dated 2025-12-01.
Matches.

### #76 cross-check
`gh issue view 76` body does not mention any rifs/ or rnfs/ designation. Not applicable.
