# SJVFS — Statens jordbruksverk

Verdict: OK

Site list: 1474 designations from 31 search-API pages (https://jordbruksverket.se/om-jordbruksverket/forfattningar)
- sjvfs 1456, dfs 14, lsfs 3, lbs 1

We hold: sjvfs 1459, dfs 14, lsfs 3, lbs 1. Newest both sides: SJVFS 2026:14.

Missing: 0

Extra: 3, all sjvfs, all benign
- sjvfs/2008:52, sjvfs/2011:39 — "kungörelse om förekomst av flyghavre" (annual wild-oat
  occurrence notices). The site's own register drops this whole sub-series after
  2005:54; it is not a selective corpus gap.
- sjvfs/2019:80 — an amendment to SJVFS 2015:25 that the site's index no longer
  surfaces, while the base regulation is still listed. No repeal link points at it
  in our corpus either; it reads as the register aging out an old amendment, not a
  repeal we failed to record.

Repeal gaps: 0 of 5 sampled repeal-carrying titles checked
- sjvfs/2009:13 -> sjvfs/2022:19 (rpubl:upphaver present)
- sjvfs/2019:71 -> sjvfs/2022:17 (rpubl:upphaver present)
- sjvfs/2000:16 -> sjvfs/2022:17 (rpubl:upphaver present)
- sjvfs/2019:14 -> sjvfs/2022:13 (rpubl:upphaver present)
- sjvfs/2004:39 -> sjvfs/2022:18 (rpubl:upphaver present, documents.upphavande=1)

Title defects: 0 of 16 sampled (12 random sjvfs + dfs/2007:8, lsfs/1986:18,
lbs/1976:7, sjvfs/2026:14). All match the site's own title field. One
cosmetic difference: dfs/2007:8's corpus title drops the "DFS 2007:8 " prefix
and trailing ";" that the site's raw title carries — a parse-time cleanup, not
a defect.

Consolidations: site no, we hold 0. SJVFS's search hits are themselves the
regulation PDFs (resolve_direct, no landing page); no title in the full 1474-row
enumeration mentions "konsoliderad". Consistent on both sides.

Inherited: dfs 14/14 match site under `dfs`; lsfs 3/3 match under `lsfs`; lbs
1/1 matches under `lbs`. None renumbered into `sjvfs`.

Already filed, confirmed present, not refiled:
- #52: `lsfs/1980:8` stores the same PDF as `lsfs/1986:18`.
- #76: `sjvfs/2006:3` and `sjvfs/2007:59` are in the pdftotext-mismatch list.

Issue: none

## Evidence

Corpus counts (`.venv/bin/python` against `site/data/catalog.sqlite`):

    sjvfs 1459 newest SJVFS 2026:14 oldest SJVFS 1991:101
    dfs   14   newest DFS 2007:8   oldest DFS 2004:11
    lsfs  3    newest LSFS 1986:18 oldest LSFS 1980:8
    lbs   1    LBS 1976:7

(An initial `order by label desc limit 3` looked like a freshness gap
because label sorts lexicographically ("...:9" > "...:14"); a numeric check
against the full 2026 list showed 2026:14 present.)

Live enumeration, called the same way the harvester does (issue #33's fix
under test): a session GET for the portlet cookie, then the search POST
walked page-by-page until `pagination.next` is falsy:

    from ferenda.lib.net import make_session, BROWSER_UA
    from ferenda.foreskrift.agencies import sjvfs_enumerate, SJVFS
    session = make_session(BROWSER_UA)
    refs = list(sjvfs_enumerate(session, SJVFS))
    # -> 1474 refs, 31 pages, page size 50 (confirmed via a direct
    #    single-page POST: result["pagination"]["last"]["value"] == 31)

31 pages x 50/page fully covers the register; `#33`'s earlier bug (page-1-only
reads) is fixed and holds — the corpus/site designation sets differ by only
3 documents, all benign (see Extra, above).

Set comparison by `kind` (uri suffix vs `DocRef.basefile`):

    sjvfs: site=1456 corpus=1459 missing=0 extra=3 (2008:52, 2011:39, 2019:80)
    dfs:   site=14   corpus=14   missing=0 extra=0
    lsfs:  site=3    corpus=3    missing=0 extra=0
    lbs:   site=1    corpus=1    missing=0 extra=0

Repeal check: for each `(repealer, target)` pair read off a title's
"(upphävd genom SJVFS ...)" suffix, `links` carries `rpubl:upphaver` from
repealer to target (see Repeal gaps above; all 5 present, one repealer,
sjvfs/2022:17, correctly carries two `upphaver` targets).

Download-record scan (`site/data/downloaded/foreskrift/sjvfs/*.json.br`,
1454 records): `files.consolidation` and `files.amendment` are empty on
every record. No title in the 1474-row site enumeration mentions
"konsoliderad" (case-insensitive substring search).

HTTP requests used against jordbruksverket.se: 1 GET (session cookie) + 31
POST (full enumerate) + 1 GET + 1 POST (single-page pagination-size check) =
34, within the 60-request budget. No Playwright needed; the register is a
JSON search API.

#76 cross-check: `gh issue view 76 --json body -q .body | grep -i sjvfs`
lists `sjvfs/2006:3` and `sjvfs/2007:59` as pdftotext mismatches; both exist
in our records, no new issue needed.

#52 cross-check: `gh issue view 52` lists `lsfs/1980:8 = lsfs/1986:18` as a
byte-identical PDF pair; both are among our 3 lsfs documents, no new issue
needed.
