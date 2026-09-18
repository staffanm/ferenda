# SKSFS — Skogsstyrelsens författningssamling, Skogsstyrelsen
Verdict: OK
Site list: 43 designations from 1 page (https://www.skogsstyrelsen.se/lag-och-tillsyn/forfattningar/)
We hold: 56 documents (sksfs), newest SKSFS 2025:4; site newest SKSFS 2025:4
Missing: 0
Extra: 13 — SKSFS 2008:3, 2008:6, 2008:7, 2009:1, 2009:2, 2010:3, 2012:1, 2012:3,
  2012:4, 2014:8, 2014:9, 2014:10, 2015:1. All explained: 4 base regulations
  repealed by a repeal document we hold (2008:3<-2014:8, 2008:6<-2014:10,
  2008:7<-2014:9, 2012:1<-2015:1), the 4 repeal notices themselves (which the
  site does not list as in-force text), and 5 amendments to those now-repealed
  base regulations (2009:1, 2009:2, 2010:3, 2012:3, 2012:4).
Repeal gaps: 0. SKSFS 1999:1 and 2001:2 print "(upphävd)" on the site; both
  are repealed in our corpus by SKSFS 2014:11.
Title defects: 0 of 15 checked. Titles read "SKSFS 1991 3 Förordning om..."
  (space, not colon) because that is exactly how Skogsstyrelsen's own PDF
  file names and anchor text write the designation. We strip the trailing
  ".pdf - 0,35 MB" the site appends; no truncation, entity, or duplicate
  designation found.
Consolidations: site says "yes, sometimes" in prose (no example PDF linked
  on this page), we hold 0. No mismatch — no consolidation exists to miss.
Inherited: none (per assignment).
Issue: none — see #76 note below.

## Evidence

Catalog query:
    .venv/bin/python -c "... select ... from documents where kind='sksfs' ..."
  -> 56 rows.

Entry page fetch (ferenda.lib.net.request, 1 GET):
    request(session, 'GET', 'https://www.skogsstyrelsen.se/lag-och-tillsyn/forfattningar/')
  -> 200, single page, no pagination links found (`sida`/`page` selectors: none).
  94 `<a href$=".pdf">` anchors; 46 match the ferenda `RE_SKSFS_FILE` pattern
  (excluding `-bilaga` annexes), 43 unique after de-duplicating same PDF
  listed under two subject categories (SKSFS 2016:1, 2016:2, 2020:2).

Diff (site unique 43 vs corpus 56 labels): missing = [] ; extra = the 13
  listed above (script inline, set difference).

Repeal check (links table, predicate rpubl:upphaver):
    2008:3  <- 2014:8
    2008:6  <- 2014:10
    2008:7  <- 2014:9
    2012:1  <- 2015:1
    1999:1  <- 2014:11
    2001:2  <- 2014:11
  All targets resolve to documents present in our corpus.

Consolidation check:
    grep -io "konsoliderad[a]\?" sksfs_index.html   -> 3 hits, all inside one
      generic paragraph explaining the practice ("Ibland gör vi en
      konsoliderad version ..."), no href contains "konsolider".
    56/56 download records checked via ferenda.lib.compress.read_text +
      json.loads; files.consolidation is empty on every one.

Issue #76 (`gh issue view 76`) lists three sksfs records with origin "live":
  sksfs/2011:3, sksfs/2014:7, sksfs/2022:1. Fetched the stored regulation
  PDFs and ran pdftotext -layout on each:

    sksfs-2011-3-regulation.pdf:
      "Föreskrifter om ändring i föreskrifter (SKSFS 1998:2) ... SKSFS 2011 :3"
      -- the amendment's own number (2011:3, printed with a stray space) sits
      on the same header line as the base regulation it amends (1998:2). The
      #76 scan picked up the base regulation's designation.

    sksfs-2014-7-regulation.pdf and sksfs-2022-1-regulation.pdf:
      both PDFs print "SKSFS 2011:7" on the cover page. Both documents are
      Skogsstyrelsen's periodic omtryck (full reprint incorporating all
      amendments) of the standing regulation SKSFS 2011:7. Skogsstyrelsen
      reissues the omtryck under a new number on its website (2014:7,
      2022:1 -- matching the URL/filename and our minted labels) but keeps
      printing the original base regulation's number on the cover.

  This is the same convention already documented in
  ferenda/foreskrift/agencies.py (skogs_enumerate docstring: "the link
  text's colon-reference names the amended base, not the document's own
  number") -- which is exactly why this harvester keys off the filename
  slug instead of in-PDF or in-link text. Our minted labels (2011:3, 2014:7,
  2022:1) are correct; the mismatch #76 found is Skogsstyrelsen's own PDF
  authoring habit, not a harvest or parse defect. No new issue filed; cites
  #76 per the briefing.
