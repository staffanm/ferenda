# MDFFS — Myndigheten för digital förvaltning (DIGG)

Verdict: DEFECT
Site list: 6 grundförfattningar from 1 page (https://www.digg.se/om-oss/forfattningssamling); no pagination markup found.
We hold: 6 documents (mdffs), newest MDFFS 2025:3; site newest grundförfattning MDFFS 2025:3, but site also
lists 5 ändringsförfattningar up to MDFFS 2026:4 that we do not hold at all.
Missing: 5 — MDFFS 2021:2, MDFFS 2026:1, MDFFS 2026:2, MDFFS 2026:3, MDFFS 2026:4 (all ändringsförfattningar,
each its own PDF-backed document on the site, none present as a document/artifact/download record in our corpus).
Extra: 0.
Repeal gaps: 0 — the site marks nothing upphävd for this scope; no repeal check applies.
Title defects: 3 — MDFFS 2025:1, MDFFS 2025:2, MDFFS 2025:3 store the bare designation as their title
("MDFFS 2025:1") instead of the real title, and carry no beslutsdatum, no bemyndigande. The 3 PDF-backed
documents (2019:1, 2019:2, 2021:1) have correct, non-junk titles.
Consolidations: site yes (5 of 6 grundförfattningar have a "Konsoliderad version" page), we hold 0.
Inherited: none (assignment lists no predecessor series).
Issue: https://github.com/staffanm/ferenda/issues/70

## Evidence

### Enumeration
Fetched https://www.digg.se/om-oss/forfattningssamling (1 request, 200, 84813 bytes). Parsed all `<a>` whose
text matches `MDFFS \d{4}:\d+`. No pagination link/element found in the HTML (grep for "sida N", "page=",
"nästa sida" returns nothing). Found 6 "Grundförfattning (MDFFS Y:N)" links and 5
"Ändringsförfattning (MDFFS Y:N)" links, plus 5 "Konsoliderad version" links:

    Grundförfattning (MDFFS 2019:1)  Ändringsförfattning (MDFFS 2026:1)  Konsoliderad version
    Grundförfattning (MDFFS 2019:2)  Ändringsförfattning (MDFFS 2021:2)  Konsoliderad version
    Grundförfattning (MDFFS 2021:1)  Ändringsförfattning (MDFFS 2026:2)  Konsoliderad version
    Grundförfattning (MDFFS 2025:1)  (no amendment yet)
    Grundförfattning (MDFFS 2025:2)  Ändringsförfattning (MDFFS 2026:3)  Konsoliderad version
    Grundförfattning (MDFFS 2025:3)  Ändringsförfattning (MDFFS 2026:4)  Konsoliderad version

### What we hold

    .venv/bin/python -c "
    import sqlite3
    con = sqlite3.connect('file:site/data/catalog.sqlite?mode=ro', uri=True)
    for row in con.execute(\"select label,title,date from documents where kind='mdffs' order by label\"):
        print(row)"

    MDFFS 2019:1  Myndigheten för digital förvaltnings föreskrifter om registrering i PEPPOL  2019-05-13
    MDFFS 2019:2  Myndigheten för digital förvaltnings föreskrifter om tillgänglighet ...      2019-05-15
    MDFFS 2021:1  Myndigheten för digital förvaltnings föreskrifter om hantering av ...        2021-02-04
    MDFFS 2025:1  MDFFS 2025:1                                                                 None
    MDFFS 2025:2  MDFFS 2025:2                                                                 None
    MDFFS 2025:3  MDFFS 2025:3                                                                 None

`site/data/artifact/foreskrift/mdffs/2025-1.json` etc. confirm empty metadata: title=None, beslutsdatum=None,
ikrafttradandedatum=None, bemyndigande=[]. `site/data/downloaded/foreskrift/mdffs/` has no
`mdffs-2025-{1,2,3}-regulation.pdf` — these three grundförfattningar are published by DIGG as inline HTML with
no PDF (`ferenda/foreskrift/agencies.py:1596-1601` documents this format split explicitly), and
`ferenda.foreskrift.parse.parse_record` (ferenda/foreskrift/parse.py:1587-1594) only reads a body when
`files.get("regulation")` is set — an HTML-only entry skips PDF parsing entirely and gets no fallback, so
title/date/bemyndigande stay empty.

Fetched the site pages directly to confirm real content exists:

    curl-equivalent via ferenda.lib.net.request:
    https://www.digg.se/.../foreskrifter-om-krav-pa-leverantorers-ansokan.../mdffs-20251  -> h1:
      "Föreskrifter om krav på leverantörers ansökan om anslutning till auktorisationssystem
       för tjänster för elektronisk identifiering och för digital post (MDFFS 2025:1)"
      body text: "Beslutade den 15 april 2025. Myndigheten för digital förvaltning föreskriver
      följande med stöd av 6 § förordningen (2023:709) ..."
    https://www.digg.se/.../mdffs-20252/...  -> h1 with real title, "Beslutade den 15 april 2025"

Both carry a real title, a real beslutsdatum and a real bemyndigande clause on the page; our artifact has none
of it.

### Missing ändringsförfattningar

`links` rows already in our catalog point `rinfoex:andradAv` at 5 URIs that do not exist as documents:

    select * from links where predicate='rinfoex:andradAv' and from_uri like '%mdffs%';
    mdffs/2019:1 -> mdffs/2026:1
    mdffs/2019:2 -> mdffs/2021:2
    mdffs/2021:1 -> mdffs/2026:2
    mdffs/2025:2 -> mdffs/2026:3
    mdffs/2025:3 -> mdffs/2026:4

None of `mdffs/2021:2`, `mdffs/2026:1..4` is in `documents`, has an artifact under
`site/data/artifact/foreskrift/mdffs/`, or a download record under `site/data/downloaded/foreskrift/mdffs/`.
Fetched three of the five amendment pages directly and confirmed each is a complete, independently dated
ändringsförfattning with its own PDF and own §§ text, not a stub:

    MDFFS 2026:1 (amends 2019:1): "beslutade den 16 januari 2026 ... att rubriken och 1 samt 2 §
      ska ha följande lydelse ... Denna författning träder i kraft den 23 januari 2026."
      links a 44 kB PDF.
    MDFFS 2021:2 (amends 2019:2): "beslutade den 1 november 2021 ... att 5 § ska ha följande
      lydelse ... träder i kraft den 1 januari 2022." Links a 69.4 kB PDF, and its own page cross-
      links back to the grundföreskrift MDFFS 2019:2.
    MDFFS 2026:2 (amends 2021:1): "beslutade ... 16 januari 2026 ... att 5, 6 och 10 §§ ska ha
      följande lydelse ... träder i kraft den 23 januari 2026." Links a 43.2 kB PDF.

Root cause: `mdffs_enumerate` (ferenda/foreskrift/agencies.py:1618-1656) only yields a `DocRef` for the
"Grundförfattning" anchor in each index group. "Ändringsförfattning" anchors are folded into the grund's
`extra["amendments"]` list (used only to mint `andradAv` links) and never enumerated as documents in their own
right, so no PDF or JSON is ever fetched for them. The same code comment
(ferenda/foreskrift/agencies.py:1596-1601) notes that MDFFS never has a downloadable PDF for a consolidation
either — this is the same gap: DIGG's HTML-only ändringsförfattning and konsoliderad-version pages are outside
what the harvester or parser reads at all.

### Consolidations

5 of 6 grundförfattningar (all but MDFFS 2025:1, too new to have been amended) have a "Konsoliderad version"
page on the site. `site/data/downloaded/foreskrift/mdffs/*.json.br` `files.consolidation` is empty for every
one of our 6 documents (checked by inspecting the artifact JSON above; none has a `consolidations` entry).

### Repeal check

No text on the index page matches "upphäv", "upphör" or a struck-through style; nothing on the site is marked
repealed for this scope, so check 4 does not apply.

### Freshness

Newest grundförfattning on both sides: MDFFS 2025:3. But the site's newest designation overall is MDFFS 2026:4
(an ändringsförfattning), which we do not hold in any form.

### Inherited series

The assignment lists no predecessor samling for mdffs, so check 7 does not apply.

### Requests used

10 HTTP requests total (1 index page + 3 ändringsförfattning pages + 2 HTML-only grundförfattning pages + 3
PDF-backed grundförfattning pages), all via `ferenda.lib.net.request`, well under the 60-request budget.
