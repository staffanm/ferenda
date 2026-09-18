# LIVSFS — Livsmedelsverkets författningssamling, Livsmedelsverket

Verdict: DEFECT

Site list: 650 rows read from 31 year pages (2026 down to 1996), plus the
entry page (https://www.livsmedelsverket.se/om-oss/lagstiftning1/foreskrifter-i-nummerordning/).
388 distinct LIVSFS designations (2002-2026), 248 distinct SLVFS designations
(1996-2002).

We hold: 34 documents (livsfs), all from 2022-2026. Newest LIVSFS 2026:3.
Site newest is also LIVSFS 2026:3 (freshness OK for the recent window).
0 documents under slvfs.

Missing: 354 LIVSFS designations (2002-2021) and all 248 SLVFS designations
(1996-2002) are on the site but absent from the corpus. Examples:
LIVSFS 2021:8, LIVSFS 2020:1, LIVSFS 2014:1, LIVSFS 2010:5, LIVSFS 2002:1,
SLVFS 2001:1, SLVFS 1998:8, SLVFS 1996:1. Even inside the held window
(2022-2026), 14 of 15 LIVSFS 2022 designations are missing (2022:1-3,
2022:5-15); only the "Rättelseblad LIVSFS 2022:4" corrigendum is held.
This matches a corpus that has downloaded only what appeared in the last
~4 years, not the full year-page range the harvest code already lists.

One concrete, reproducible cause found: the site's year page for 2018 sits
at a mismatched slug -- `foreskrifter-i-nummerordning-20172/` (H1: "2018")
instead of the expected `foreskrifter-i-nummerordning-2018/` (404). The
harvest's `index_urls` build the per-year URL as `"...-%d/" % year`, so the
2018 request always 404s; `optional_pages: True` treats that as "no
regulations this year" and silently skips it. This permanently drops
LIVSFS 2018:1 through 2018:9 (9 designations) from every run, independent
of any backfill.

Extra: 0 — every document we hold has a matching designation on the site.

Repeal gaps: 0 checked and correct. LIVSFS 2025:7 upphäver LIVSFS 2022:2
(`rpubl:upphaver` -> https://lagen.nu/livsfs/2022:2); LIVSFS 2026:1
upphäver SLVFS 1998:8 (`rpubl:upphaver` -> https://lagen.nu/slvfs/1998:8).
Both targets are outside the held set, which is expected under the repeal
model (the repealing document exists and links correctly; the repealed
target simply falls in the missing-documents gap above).

Title defects: 0 in a 3-document sample (LIVSFS 2024:2, LIVSFS 2022:4,
plus the general index listing). LIVSFS 2024:2's stored title
("Livsmedelsverkets föreskrifter om snus, snusliknande produkter och
tuggtobak") matches the PDF's own cover text exactly. LIVSFS 2022:4's
stored title ("Rättelseblad LIVSFS 2022:4") matches the PDF's own
metadata Title field ("LIVSFS 2022:4 Rättelseblad") -- word order differs,
content does not. Issue #32 (the dropped `td:first-child` selector) holds:
every year page from 1996 through 2026 (2018 excepted, see above) returns
rows with the fixed selector.

Consolidations: site yes (e.g. "Livsmedelsverkets föreskrifter LIVSFS
2021:8 (konsoliderad version)" hangs off the LIVSFS 2021:8 landing page),
we hold 0. This traces to the same root cause as Missing: the base
regulation LIVSFS 2021:8 was never enumerated in this corpus, so its
landing page (which is where `classify_livsfs` reads the consolidation
link) was never visited.

Inherited: slvfs -- the site still serves 248 SLVFS designations
(1996-2002) under Livsmedelsverket's own numbered-order pages, correctly
filed by `fs_from_designation`, but the corpus holds 0 of them. No
renumbering into livsfs was observed (no SLVFS PDF found minted under a
livsfs designation) -- the predecessor series is entirely absent rather
than mis-filed.

Issue: https://github.com/staffanm/ferenda/issues/67

## Evidence

Fetched all 31 year index pages plus the entry page with ferenda's own
`request()` (via `ferenda.lib.net.make_session`/`BROWSER_UA`), one request
per page, no extra load: script at
`/tmp/claude-1000/-home-staffan-repos-ferenda/71497249-8a1a-4631-91b8-faeae967da12/scratchpad/fs-audit/fetch_livsfs.py`,
raw results in `livsfs_raw.json` in the same directory. 32 HTTP requests
total to livsmedelsverket.se, well under the 60-request budget. One extra
request confirmed the entry page's own link list includes
`foreskrifter-i-nummerordning-20172/` alongside the regular
`-1995-` through `-2026-` slugs; two more requests confirmed the H1 text
on `-2017-` ("2018" is not there) and `-20172-` ("2018" is there, with
LIVSFS 2018:1-9). Two further requests fetched the LIVSFS 2024:2 landing
page and the LIVSFS 2021:8 landing page (which shows the konsoliderad
link). Total requests: 37.

Catalog queries (`site/data/catalog.sqlite`, read-only):

    select count(*) from documents where kind='livsfs'   -> 34
    select count(*) from documents where kind='slvfs'    -> 0
    select label,title,... from documents where kind='livsfs' order by label

    select * from links where from_uri like '%livsfs/2025:7'
      -> rpubl:upphaver -> https://lagen.nu/livsfs/2022:2
    select * from links where from_uri like '%livsfs/2026:1'
      -> rpubl:upphaver -> https://lagen.nu/slvfs/1998:8

Download records: `ls site/data/downloaded/foreskrift/livsfs/` lists only
`livsfs-2022-*` through `livsfs-2026-*` (68 files, 34 documents); no
`site/data/downloaded/foreskrift/slvfs/` directory exists at all.
`files.consolidation` is empty in all 34 `.json.br` download records.

`pdftotext`/`pdfinfo` on `livsfs-2024-2-regulation.pdf` and
`livsfs-2022-4-regulation.pdf` confirm both stored titles are taken
verbatim (bar word order) from the PDF's own text/metadata.

`git log --oneline -i --grep=livsfs` shows commit d5c499e5 ("foreskrift:
read LIVSFS rows from the first cell, and own the 612 documents that
opens (#36)") and 7c2774df ("foreskrift: file Livsmedelsverket's pre-2002
rows under SLVFS...") already landed in this branch. The corpus's current
34-document count is far short of that "612" figure, consistent with the
historical backfill never having completed in this data root -- this
report does not claim that gap is itself a code bug beyond the confirmed
2018 slug mismatch, since no pipeline was run to test it.
