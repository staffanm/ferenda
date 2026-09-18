# BOLFS — Bolagsverkets föreskrifter, Bolagsverket
Verdict: DEFECT
Site list: 36 designations from 1 page (https://bolagsverket.se/omoss/varverksamhet/styrochplaneringsdokument/bolagsverketsforeskrifterbolfs.2161.html)
We hold: 32 documents (bolfs), newest BOLFS 2025:1; site newest BOLFS 2025:1
Missing: 4 — BOLFS 2006:3, 2009:1, 2022:2, 2024:1 (all ändringsföreskrifter, recorded only as unfetched references by design; see Evidence)
Extra: 0
Repeal gaps: 0 — issue #30's title-based repeal fix holds (BOLFS 2022:3 -> 2006:1, 2022:4 -> 2006:2, both present in links and in the artifact metadata)
Title defects: 3 — BOLFS 2006:1 ("upp- gifter" for "uppgifter"), BOLFS 2006:2 ("fi lialregistret" for "filialregistret"), BOLFS 2004:3 (title is the bare designation, no real text)
Consolidations: site yes (5: 2004:4, 2007:1, 2011:1, 2017:2, 2018:1), we hold 5
Inherited: none (assignment lists no predecessor series)
Issue: https://github.com/staffanm/ferenda/issues/46

## Evidence

### 1. Enumerate (check 1)
Fetched the single entry page (1 request):

    ferenda.lib.net.request(session, "GET", <entry url>)  # 200, 194595 bytes

Parsed h2/h3/h4 headings + PDF `/download/...` links with the same walk as
`ferenda.foreskrift.agencies.bolfs_enumerate`. Found 3 sections:
- "Gällande föreskrifter": 13 grundföreskrifter (2025:1, 2023:1, 2004:4,
  2004:6, 2004:7, 2007:1, 2009:2, 2011:1, 2012:1, 2017:2, 2018:1, 2019:1,
  2022:1), each with its own "Ändringsföreskrifter till BOLFS X" and
  "Konsoliderad version" sub-heading.
- "Upphävda föreskrifter": 16 designations, each its own heading (2004:1,
  2004:2, 2004:3, 2004:5, 2006:1, 2006:2, 2006:4, 2006:5, 2008:1, 2008:2,
  2009:3, 2009:4, 2013:1, 2015:1, 2022:3, 2022:4).
- Ändringsföreskrifter linked under a "Gällande" base but with no heading
  of their own: 2006:3, 2009:1, 2019:2 (under 2004:4); 2017:1 (under
  2007:1); 2014:1 (under 2011:1); 2022:2 (under 2017:2); 2024:1 (under
  2018:1).

Total unique designations named on the page: 36.

### 2/3. Missing / extra (checks 2-3)

    catalog labels (kind='bolfs')            = 32 designations
    site designations                        = 36 designations
    site - catalog                           = {2006:3, 2009:1, 2022:2, 2024:1}
    catalog - site                           = {} (no extra documents)

All 4 missing designations are ändringsföreskrifter linked only under a
"Ändringsföreskrifter till BOLFS X" sub-heading, with no heading of their
own. `bolfs_enumerate` records these as `extra["amendments"]` references
(identifier + url) on the base regulation's family, and
`resolve_direct` deliberately does not fetch an amendment's own PDF as a
document (`ferenda/foreskrift/harvest.py:250-256`, "a later pass can fetch
an amendment body on demand"). The corpus already holds 3 other
ändringsföreskrifter as full documents (2014:1, 2017:1, 2019:2), but
their download records carry an old `polopoly_fs` source_url, from a
harvest against a now-retired site layout that gave each amendment its
own landing page. This is a scope/consistency gap, not a hard defect
under the current, deliberate architecture, so it is reported as a shape
rather than filed on its own.

Verified BOLFS 2024:1 is a real, addressable document (fetched its PDF,
1 request):

    https://bolagsverket.se/download/18.7e28353618c67d1aad643c/1706275713492/bolfs-2024-1.pdf
    -> "BOLFS 2024:1 Föreskrift om ändring i Bolagsverkets föreskrifter
       (BOLFS 2018:1) om elektronisk ingivning av årsredovisningshandlingar
       för aktiebolag", beslutad 2024-01-08, utkom 2024-01-26.

### The filed defect: amendment identifiers dropped by `_bolfs_amend_id`

`ferenda/foreskrift/agencies.py:1130-1134`'s `_bolfs_amend_id` reads an
amendment's own designation from the *last* of at least two `YYYY-N` or
`YYYY_N` pairs in the PDF filename (works for
`bolfs_2004_4_2006_3.pdf` -> "BOLFS 2006:3"). It requires `len(nums) >= 2`
and returns `None` otherwise. Two current PDF filenames encode only the
amendment's own single pair (no base-year prefix), so the function drops
a real, parseable identifier:

    bolfs-2022-2.pdf  (amends BOLFS 2017:2) -> None, should be "BOLFS 2022:2"
    bolfs-2024-1.pdf  (amends BOLFS 2018:1) -> None, should be "BOLFS 2024:1"

Confirmed in the stored download records:

    .venv/bin/python -c "
    from ferenda.lib.compress import read_text
    import json
    for f in ['bolfs-2018-1', 'bolfs-2017-2']:
        d = json.loads(read_text(f'site/data/downloaded/foreskrift/bolfs/{f}.json'))
        print(f, d['files']['amendment'])
    "
    # bolfs-2018-1 [{'identifier': None, 'url': '.../bolfs-2024-1.pdf'}]
    # bolfs-2017-2 [{'identifier': None, 'url': '.../bolfs-2022-2.pdf'}]

`render.py:_amendment_label` falls back to the literal string
"ändringsförfattning utan läsbar beteckning" whenever `identifier` is
`None`, so the rendered "Ändringsförfattningar" register on the BOLFS
2017:2 and BOLFS 2018:1 pages shows that placeholder instead of "BOLFS
2022:2" / "BOLFS 2024:1", even though both numbers are trivially
readable from the filename and confirmed by the PDF's own cover page.

### 4. Repeal marking (check 4) — issue #30 fix verified

    catalog links, predicate='rpubl:upphaver', bolfs:
      2022:3 -> 2006:1
      2022:4 -> 2006:2
      (plus 2006:1->2004:1, 2006:2->2004:2, 2008:1->{2004:5,2006:4},
       2008:2->2006:5, 2018:1->2008:2, 2022:1->2008:1)

    artifact metadata.upphaver for 2022-3.json / 2022-4.json:
      2022-3 -> ['https://lagen.nu/bolfs/2006:1']
      2022-4 -> ['https://lagen.nu/bolfs/2006:2']

Both repeal-only documents (`documents.upphavande = 1`) correctly name
their target. The fix from PR for issue #30 (commit 786c6c8a, the
`RE_UPPHAVANDE` title regex) holds against the live site: all 16
"Upphävda föreskrifter" headings have a repealing document in the
corpus, and every repeal-only document's `upphaver` list resolves.

### 5. Titles (check 5)

Compared 32 catalog titles against the site's own PDF link text (stripped
of the trailing "(NN kB)" file-size suffix). 3 diffs, all in the direction
of our stored text being worse than the site's:

| designation | site link text (trimmed) | we hold |
|---|---|---|
| BOLFS 2006:1 | "...om avgifter för bevis om uppgifter i..." | "...om upp- gifter i..." |
| BOLFS 2006:2 | "...aktiebolagsregistret och filialregistret samt..." | "...och fi lialregistret samt..." |
| BOLFS 2004:3 | (no PDF; page names it "Bolagsverkets föreskrifter om företagshypotek.") | "BOLFS 2004:3" |

Fetched the source PDFs directly (2 requests) and confirmed both defects
originate in our own title extraction, not the source:

    pdftotext (default mode) bolfs-2006-1.pdf -> "...om avgifter för bevis om uppgifter i..."   (correct)
    pdftotext -layout        bolfs-2006-1.pdf -> "...om upp-" / "gifter i..." (two lines; the source PDF hyphenates the line break, plain pdftotext rejoins it, our title extraction does not)

    pdftotext (default mode) bolfs-2006-2.pdf -> "...och filialregistret samt..."                (correct, one word)
    pdftotext -layout        bolfs-2006-2.pdf -> "...och" / "filialregistret samt..." (no hyphen in the source at all; our extraction still splits it)

BOLFS 2004:3 has no PDF on the site (its download record carries no
regulation file); the heading is followed by a plain paragraph naming it
"Bolagsverkets föreskrifter om företagshypotek." but the harvester only
reads titles off PDF link text, so the stored title falls back to the
bare designation.

One more diff was checked and dismissed: BOLFS 2017:1's stored title
reads "...kungörelser i Postoch Inrikes Tidningar" (missing a
space/hyphen) against the site's "...Post- och Inrikes Tidningar". Both
`pdftotext` and `pdftotext -layout` on the source PDF itself produce
"Postoch" — the defect is baked into the source PDF's text layer, not
introduced by our pipeline, so it is not counted as a corpus defect.

### 6. Consolidations (check 6)

Site publishes "Konsoliderad version" for 5 grundföreskrifter (2004:4,
2007:1, 2011:1, 2017:2, 2018:1). Our download records carry
`files.consolidation` for exactly the same 5, no more, no fewer.

### 7. Inherited series (check 7)

The assignment lists no predecessor series for bolfs. Two families
(BOLFS 2011:1, 2012:1, 2019:1) are Patentombudsnämnden's own
föreskrifter/allmänna råd, published inside the BOLFS samling because
Bolagsverket hosts Patentombudsnämnden administratively — the site lists
them under the same "Förteckning" as Bolagsverket's own regulations, so
filing them under `bolfs` (not a separate `pofs`) matches the source.

### 8. Freshness (check 8)

Site newest: BOLFS 2025:1 (2025-08-25). We hold BOLFS 2025:1. Match.
(BOLFS 2024:1 is chronologically newer than several base regs we hold
but is an unfetched amendment, see above — it does not change the
"newest base regulation" comparison.)
