# RAMS — RA-MS, Riksarkivet (myndighetsspecifika föreskrifter)
Verdict: DEFECT (same cause as #41 — no new issue filed)
Site list: 361 base designations (1 API query, `sokBlandGiltiga=true`, https://foreskrifter.riksarkivet.se/rams)
We hold: 366 documents (rams), newest RA-MS 2026:30; site newest RA-MS 2026:30
Missing: 0
Extra: 5 — RA-MS 2017:44, 2023:14, 2024:14, 2024:15, 2024:19 (all "beslut", not "föreskrifter"; no repeal link found; not a corpus defect, see Evidence)
Repeal gaps: 53 documents name another RA-MS in title or body, but `upphaver` is empty. Same root cause as #41 (`RE_FS_REF` in `ferenda/foreskrift/parse.py` matches only designations ending in FS/FA; "RA-MS" ends in MS). `andrar` shows no comparable gap — RA-MS amendment titles never use the "Ändring i/av ..." pattern that would trigger it.
Title defects: 0 in a random sample of 12 (11 exact matches; 1 sample fell in the "Extra" set and has no site title to compare)
Consolidations: site no (0 of 505 API items carry a typId=1/konsoliderad huvuddokument), we hold 0 — matches
Inherited: none (per assignment)
Issue: none — cites #41 (same regex defect), see below

## Evidence

### 1. Enumerate (site)
    .venv/bin/python -c "
    import requests
    from ferenda.lib.net import request
    session = requests.Session()
    url = 'https://foreskrifter.riksarkivet.se/api/rams/sok?nummer=&rubrik=&fulltext=&myndighet=&arkivbildare=&sokBlandGiltiga=true'
    data = request(session, 'GET', url, parse_json=True)
    items = data['traffLista']
    bases = [it for it in items if it.get('nummer') == it.get('grundforfattning')]
    print(len(items), len(bases))
    "
    # -> 505 total items (bases + amendments), 361 base regulations

The harvester (`ra_enumerate` in `ferenda/foreskrift/agencies.py`) uses this
same API call. `sokBlandGiltiga=true` is the only accepted value (`false`
gives HTTP 404, empty gives HTTP 400) — the API only exposes documents the
agency currently considers "Giltig" (its own `statusText` field).

### 2/3. Missing / extra
Compared 361 site base designations to 366 `documents.label` rows where
`kind='rams'`. 0 missing. 5 extra: RA-MS 2017:44, 2023:14, 2024:14, 2024:15,
2024:19. Querying the API by exact `nummer` for one of them under
`sokBlandGiltiga=true` returns zero hits, confirming the agency no longer
lists it as valid:

    url = ".../api/rams/sok?nummer=2023%3A14&...&sokBlandGiltiga=true"
    # -> {"traffLista": [], "antalTraffar": 0}

`links` has no `rpubl:upphaver` row targeting any of the 5, and no document
in our corpus names any of them in a title. All 5 are "Riksarkivets beslut om
gallring hos <agency>" (a decision, not a föreskrift). Each agency later got
a newer gallring document from Riksarkivet in most cases (e.g. Polismyndigheten:
2023:14 -> 2024:29/2024:37), consistent with the agency's practice of
superseding a gallring beslut administratively rather than issuing a formal
repeal instrument. Per the repeal model, there is nothing to link without a
repealing document — this is not a corpus defect.

### 4/repeal-gap defect (cites #41)
`ferenda/foreskrift/parse.py:284`:

    RE_FS_REF = re.compile(r"\b([A-ZÅÄÖ]+(?:-| )?(?:FS|FA))\s*(\d{4}):(\d+)")

requires the referenced designation to end in "FS" or "FA". "RA-MS" ends in
"MS", so it never matches, exactly as #41 found for "BFNAR" (ends in "AR").
RA-MS titles put the target designation in parentheses
("... (RA-MS 1995:56) ..."), so `RE_BARE_OWN_REF` (line 311, which requires
the parenthesised group to be bare `YYYY:NNNN` right after the trigger word)
does not match either.

Count (title or body names another RA-MS designation, cue word "upphäv"/
"upphör" in the title, `metadata.upphaver` empty):

    .venv/bin/python -c "
    import glob, json, re
    from ferenda.lib.compress import read_text
    files = sorted(glob.glob('site/data/artifact/foreskrift/rams/*.json.br'))
    pat = re.compile(r'RA-MS\s*\d{4}:\d+')
    n = 0
    for f in files:
        data = json.loads(read_text(f[:-3]))
        meta = data['metadata']
        title = meta.get('title') or ''
        struct = json.dumps(data.get('structure', []), ensure_ascii=False)
        mentions = set(pat.findall(title + ' ' + struct)) - {data.get('identifier')}
        if mentions and ('upphäv' in title.lower() or 'upphör' in title.lower()) and not meta.get('upphaver'):
            n += 1
    print(n)
    "
    # -> 53

Examples: RA-MS 1995:58 names RA-MS 1995:56; RA-MS 2000:9 names 82 targets
(a mass-repeal); RA-MS 2004:75 names RA-MS 1999:44. All have `upphaver == []`.

Corpuswide check: only 10 of 366 rams documents have any `upphaver` entry at
all, and all 10 target a non-RA-MS designation (`ffs`, `rafs`) or a bare
same-series year:number caught by `RE_BARE_OWN_REF`/an internal-reference
path — none target a full "RA-MS YYYY:NR" citation, because that citation
form can never match `RE_FS_REF`.

`andrar` shows 0 title-confirmed gaps: no RA-MS title in this corpus uses
"Ändring i ..."/"Ändring av ..." (the pattern that trips the same bug for
bfnar). RA-MS documents are almost always standalone new gallring/
överlämnande decisions per agency, not textual amendments, so this arm of
the bug is not exercised for this scope.

### 5. Titles
Random sample of 12 `rams` labels against the site API's `rubrik` field: 11
exact matches, 1 (RA-MS 2024:14) has no site entry (it's in the Extra set).
No junk found (no file sizes, no truncation, no PDF filenames, no repeated
designation-in-title beyond the legitimate cross-reference case above).

### 6. Consolidations
    .venv/bin/python -c "
    import glob, json
    from ferenda.lib.compress import read_text
    files = sorted(glob.glob('site/data/downloaded/foreskrift/rams/*.json.br'))
    print(len(files), sum(1 for f in files if json.loads(read_text(f[:-3])).get('files',{}).get('consolidation')))
    "
    # -> 365 download records, 0 with consolidation files
Checked the API directly: 0 of 505 items (across both series and
amendments) carry a typId=1 (konsoliderad) huvuddokument. The site
publishes no konsoliderade versions for rams; matches what we hold.

### 7. Inherited series
None listed for this assignment.

### 8. Freshness
Site newest: RA-MS 2026:30 (ikraftträdande 2026-06-29). We hold: RA-MS
2026:30 (date 2026-06-22 in `documents.date`, a beslutsdatum/ikrafttradande
difference, not a freshness gap). Matches.

## HTTP budget
6 requests to foreskrifter.riksarkivet.se (all via `ferenda.lib.net.request`,
paced per robots.txt). No blocks encountered.
