# BFS — Boverket, Boverkets författningssamling

Verdict: DEFECT
Site list: 465 items (125 grundförfattning, 316 amendments, 24 allmänna råd
records) from 1 API call (https://api.boverket.se/forfattningssamling/v1/forfattningar)
We hold: 124 documents (bfs), newest BFS 2026:4 (2026-06-09); site newest
BFS 2026:9 (published 2026-08-25, not yet in force)
Missing: 1 — BFS 2026:9 (grundförfattning published 2026-08-25, 2.5 weeks
before this audit; a freshness gap, not a defect)
Extra: 0
Repeal gaps: 1 — BFS 2023:8 repeals 117 named regulations (2 BOFS, 115 BFS)
but its stored `upphaver` list is empty
Title defects: 0 confirmed (1 apparent mismatch on BFS 2007:5 turned out to
favor our title over the API's own inconsistent field; see Evidence)
Consolidations: site yes (6 grundförfattningar carry a Konsolidering
document), we hold 6 — match
Inherited: BOFS (Bostadsstyrelsen) — 2 grundförfattningar (BOFS 1984:8, BOFS
1986:72) exist in Boverket's own API and in our corpus, but our records
relabel both as "BFS 1984:8" / "BFS 1986:72" instead of their real BOFS
designation
Issue: https://github.com/staffanm/ferenda/issues/44

## Evidence

### 1. Enumeration
Fetched the whole register in one call, matching `bfs_enumerate()`'s own
comment that this API returns everything:

    from ferenda.lib.net import make_session, request, HARVESTER_UA
    session = make_session(HARVESTER_UA)
    items = request(session, "GET",
        "https://api.boverket.se/forfattningssamling/v1/forfattningar",
        parse_json=True, headers={"Accept": "application/json"})
    # 465 items total: Counter({'andringsforfattning': 316,
    #   'grundforfattning': 125, 'allmant_rad': 13, 'andrat_allmant_rad': 11})

Only `typ == "grundforfattning"` items become our documents (matches
`bfs_enumerate` in `ferenda/foreskrift/agencies.py`), so the comparison set
is 125 items.

### 2. Missing / freshness
Comparing the 125 API grundförfattning (year, lopnummer) keys against the
124 `documents` rows with `kind='bfs'` in `site/data/catalog.sqlite`: only
one key absent, `('2026', '9')` = BFS 2026:9, decided 2026-08-25, in force
from 2026-10-01. Our newest artifact (`2026-4.json.br`) has an mtime of
2026-09-13, so this reflects a harvest that ran before 2026-08-25, not a
broken harvest — normal cadence.

### 3. Extra
No catalog key is absent from the API's 125 grundförfattning keys — 0 extra
documents.

### 4. Repeal marking
BFS 2023:8 is a grundförfattning whose entire text is a bulk repeal:

    "Boverket föreskriver ... att följande författningar ska upphöra att
    gälla vid utgången av 2023." followed by a numbered list of 117 named
    regulations (2 "Bostadsstyrelsens föreskrifter (BOFS ...)" items, 115
    "Boverkets föreskrifter (BFS ...)" items).

The parser already extracted 116 `dcterms:references` links from that list
into the `links` table (`from_uri = 'https://lagen.nu/bfs/2023:8'`):

    sqlite3 site/data/catalog.sqlite
    select count(*) from links where from_uri='https://lagen.nu/bfs/2023:8';
    -- 116

But `site/data/artifact/foreskrift/bfs/2023-8.json`'s `metadata.upphaver`
is `[]`, and the `links` table has 0 `rpubl:upphaver` rows from
`bfs/2023:8` — compare with `bfs/2018:13` and `bfs/2022:5`, two other
"upphävande av vissa författningar" documents of the same shape, whose
`upphaver` lists are correctly populated (7 and 5 targets).

The likely cause: `RE_UPPHOR_LIST` in `ferenda/foreskrift/parse.py` caps
its list capture at `.{0,4000}?` characters before it must reach a
terminator (a blank line, "Dessa föreskrifter träder", or 5+ underscores).
BFS 2023:8's list has 117 items (versus 5 and 7 for the two working
examples) and very likely exceeds that 4000-character budget, so the whole
match fails and `upphaver` is never populated.

None of the 114 `bfs/`-namespaced repeal targets referenced by BFS 2023:8
are themselves documents in our corpus (they predate Boverket's own API,
which starts at 1988 for BFS numbers) — so this gap has no visible effect
on any document we currently show as in force. It is still a real defect:
the metadata field is objectively wrong compared to sibling documents of
the same shape.

### 5. Titles
Compared `documents.title` (124 rows) against the API's `titel` field,
keyed by label:

    diffs = 1 (of 124): BFS 2007:5 — ours says "...för certifiering av
    energiexpert", the API's grundförfattning record says "...om
    certifiering...". All 7 of BFS 2007:5's own amendment records in the
    API (2010:7 through 2026:11) consistently use "för", so our title is
    the one that agrees with the source's own repeated usage; the API's
    single grundförfattning-record field looks like Boverket's own typo.
    Not a corpus defect.

### 6. Consolidations
6 of 125 grundförfattningar carry a "Konsolidering" document in the API's
`ovrigaDokument`. Our download records
(`site/data/downloaded/foreskrift/bfs/bfs-*.json.br`) show
`files.consolidation` populated for exactly the same 6 basefiles
(2007-4, 2007-5, 2011-6, 2011-10, 2011-12, 2017-2). Match, no defect.

(Aside, not a defect: only 114 of 124 catalog documents have a matching
download record at all — 10 keys, e.g. 2018:12, 2020:8, 2026:4, have a
catalog row and an artifact but no `bfs-<year>-<lop>.json.br` file. Outside
this audit's scope since it does not affect what the site or the corpus
shows.)

### 7. Inherited series (BOFS / Bostadsstyrelsen)
No `bofs` agency is registered in `ferenda/foreskrift/agencies.py`. The
API's own register carries 2 grundförfattningar under the Bostadsstyrelsen
predecessor designation:

    BOFS 1986:72 "Bostadsstyrelsens föreskrifter om tidskoefficienter"
    BOFS 1984:8  "Bostadsstyrelsens föreskrifter och information till
                  förordningen (1983:1021) om tilläggslån för ombyggnad av
                  bostadshus m.m."

Both exist in our catalog, correctly under `publisher = "Bostadsstyrelsen"`,
but mislabeled:

    sqlite> select uri,label,publisher from documents
            where uri like '%1984:8' or uri like '%1986:72';
    https://lagen.nu/bfs/1984:8  | BFS 1984:8  | Bostadsstyrelsen
    https://lagen.nu/bfs/1986:72 | BFS 1986:72 | Bostadsstyrelsen

The artifact's own `identifier` field says "BFS 1984:8" / "BFS 1986:72",
not "BOFS ...". Root cause, in `bfs_enumerate()`
(`ferenda/foreskrift/agencies.py`):

    identifier="%s %s:%s" % (agency.fs.upper(), arsutgava, lopnummer)

`agency.fs.upper()` is hardcoded to `"BFS"` regardless of the item's own
`forfattning` field (which correctly says "BOFS 1986:72"). This fabricates
a designation Boverket never used for these two documents.

### 8. Freshness
Site newest: BFS 2026:9 (2026-08-25). We hold: BFS 2026:4 (2026-06-09).
About 2.5 months behind, consistent with the one missing document in
check 2 and the corpus's general staleness at the time of this audit — not
BFS-specific.
