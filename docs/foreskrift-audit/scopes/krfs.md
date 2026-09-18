# KRFS — Statens kulturråd

Verdict: DEFECT
Site list: 24 designations from 1 page (https://www.kulturradet.se/om-oss/forfattningssamling/)
We hold: 24 documents (krfs), newest KRFS 2025:3; site newest KRFS 2025:3
Missing: 0
Extra: 0
Repeal gaps: 0 — all 6 upphävande documents (2003:4, 2018:1, 2018:2, 2019:1, 2025:2, 2025:3) carry correct upphaver targets; the 12 repealed targets (1995:1, 1997:1, 1998:2, 2002:2, 2010:1, 2011:1, 2012:1, 2012:3, 2013:1, 2015:1, 2020:1, 2022:2) are not on the site either, so their absence from our corpus is not a defect.
Title defects: 1 — KRFS 2022:1: we hold "KRFS 2022:01 Riksantikvarieämbetets föreskrifter om utmärkning av kulturegendom"; site prints "Riksantikvarieämbetets föreskrifter om utmärkning av kulturegendom". The designation is glued onto the title.
Consolidations: site no, we hold 0
Inherited: none (assignment lists no predecessor series)
Issue: https://github.com/staffanm/ferenda/issues/63

## Evidence

Enumeration (24/24 match, no missing/extra):

    .venv/bin/python -c "sqlite3 query on documents where kind='krfs'" -> 24 rows
    fetched https://www.kulturradet.se/om-oss/forfattningssamling/ (single static page,
    no pagination link found; searched for "arkiv"/"historik" links, found only
    unrelated nyhetsarkiv/publikationer-arkiv)
    BeautifulSoup select('a[href*="forfattningssamling-dokument"][href$=".pdf"]') -> 24 anchors
    Both sets normalize to the same 24 labels (KRFS 2003:4 .. KRFS 2025:3).

Repeal check: `links` table, predicate rpubl:upphaver, from_uri like krfs/%:

    krfs/2017:1 -> krfs/2015:1      krfs/2018:1 -> krfs/2012:1, krfs/2012:3
    krfs/2017:2 -> krfs/2002:2      krfs/2018:2 -> krfs/2010:1
    krfs/2019:1 -> krfs/2011:1      krfs/2022:1 -> krfs/1997:1
    krfs/2022:3 -> krfs/2020:1      krfs/2025:2 -> krfs/1995:1, krfs/1998:2
    krfs/2025:3 -> krfs/2013:1, krfs/2022:2

  All 6 `upphavande=1` documents (2003:4, 2018:1, 2018:2, 2019:1, 2025:2, 2025:3)
  match the site's own "Föreskrifter om upphävande av ..." titles, which name
  their targets in parentheses — no parse mismatch found. The 12 targets are
  older than the site's own listing horizon (site shows no "upphävda" archive,
  no strikethrough, and lists only 24 documents total), so their absence is
  a site-shape limitation, not a corpus gap.

Title comparison, all 24 documents (site anchor text minus its own "KRFS
YYYY:N" prefix, vs `documents.title`):

    23/24 identical (byte-for-byte, including embedded \xa0).
    KRFS 2022:1 differs:
      ours: 'KRFS 2022:01\xa0Riksantikvarieämbetets föreskrifter om utmärkning av kulturegendom'
      site: 'Riksantikvarieämbetets föreskrifter om utmärkning av kulturegendom'

  Root cause read from source: the artifact
  (site/data/artifact/foreskrift/krfs/2022-1.json.br) stores
  metadata.title == the uncut string above. `ferenda/foreskrift/parse.py`
  `clean_title(raw, identifier)` strips the raw harvest title's own leading
  designation with:

      t = re.sub(r"^%s\s*[-–—:]*\s*" % re.escape(identifier), "", t).strip()

  `identifier` is the *normalized* "KRFS 2022:1" (lopnummer int-cast,
  `ferenda/foreskrift/harvest.py` `ref()`), but the site's own anchor text
  reads "KRFS 2022:01" (lopnummer printed with a leading zero). The regex
  anchors on an exact literal match, so "^KRFS 2022:1" does not match
  "KRFS 2022:01" (next character is "0", not "1"), the substitution is a
  no-op, and the whole raw designation stays glued to the title.

Consolidations: no download record under krfs carries `files.consolidation`;
the site's page text has no occurrence of "konsoliderad".

    grep -io "konsoliderad[a-z]*" krfs_index.html   ->  (no output)

Freshness: site's newest anchor is "KRFS 2025:3 ..."; our newest held
document is KRFS 2025:3 (date 2025-06-05). Match.

Recurring-pattern checks: no "upphävda föreskrifter" archive page found (the
page's only "arkiv" links are unrelated: /om-oss/media/nyhetsarkiv/,
/om-oss/publikationer-arkiv/); the index is a single static page, not
paginated, so `indexed_enumerate` reading page 1 only is not a gap here.

Requests used: 1 GET to the entry page (well under the 60-request budget).
