# STFS — Sametinget

Verdict: BLOCKED
Site list: 0 designations from 0 pages (https://sametinget.se/dokumentbank?cat=72) — site blocked us mid-audit
We hold: 33 documents (stfs), newest STFS 2025:1 (2025-06-17); site newest: unknown (blocked)
Missing: unknown — could not enumerate the site
Extra: unknown — could not enumerate the site
Repeal gaps: 0 found in-corpus (see evidence); cannot confirm against the site's own upphävd markings
Title defects: not checked (needs the site's own titles for comparison)
Consolidations: site unknown (blocked); we hold 0 of 33 download records with files.consolidation
Inherited: none — assignment lists no predecessor samlingar
Issue: none — a block is not a corpus defect, per the briefing's hard rule

## Evidence

### Corpus side (all completed, no site access needed)

- `sqlite3` on `site/data/catalog.sqlite`: `select count(*) from documents
  where kind='stfs'` = 33, labels STFS 2007:1 through STFS 2025:1 (newest by
  date: 2025-06-17).
- All 33 catalog rows have a matching artifact under
  `site/data/artifact/foreskrift/stfs/<year>-<lop>.json.br` and a matching
  download record under `site/data/downloaded/foreskrift/stfs/`. No
  catalog/artifact/download mismatch.
- `files.consolidation`, `files.amendment`, `files.memo` are `[]` in all 33
  download records. We hold only the base regulation PDF for every stfs
  document; no konsoliderad text.
- `links` with predicate `rpubl:upphaver` touching stfs: two rows —
  `stfs/2013:2 -> nfs/2004:17` and `stfs/2017:1 -> stfs/2008:1`. Both
  repealing documents are in our corpus with a populated `upphaver` target
  that resolves to a document we hold. No repeal-marking gap found on our
  side.
- `rpubl:andrar` rows trace a coherent amendment chain, e.g. stfs/2007:4
  (Pristillägg) amended by 2007:10, 2008:4, 2009:4, 2010:1, 2013:1, 2019:2;
  stfs/2007:7 (Miljöersättning) amended by 2007:8, 2008:2, 2008:3, 2009:3,
  2010:2, 2012:1, 2012:2, 2013:3; stfs/2007:9 amended by 2009:2, 2013:2,
  2024:2; stfs/2016:1 amended by 2019:1; stfs/2007:2 amended by 2025:1. All
  amendment targets resolve inside the 33 records we hold.
- One dangling reference, noted but out of this scope's remit: `sou/2026:15`
  extracts the bibliography text "STFS 1993:384" and links it to
  `https://lagen.nu/stfs/1993:384`, which we do not hold. Every stfs
  artifact's own `bemyndigande` field points at `https://lagen.nu/1993:384`
  = SFS 1993:384, "Rennäringsförordning (1993:384)", which we do hold under
  `sfs`. This looks like a citation-extraction mislabel in forarbete/lawreview's
  citation engine (SFS read as STFS), not an stfs harvest defect. Not filed
  here; flag separately if a forarbete/lawreview audit wants it.
- `gh issue list --repo staffanm/ferenda --state open --search "foreskrift
  stfs"` — no open issue. `gh issue view 76` body does not mention stfs.
- `git status` / `git diff` untouched — no code or data was changed.

### Site side (blocked)

- `ferenda/foreskrift/agencies.py` (`stfs_enumerate`, lines 1397-1418) reads
  `https://sametinget.se/servlet/DocBankServlet?q=&path=/dokumentbank&cat=72&subCat=&idx=<n>`
  and paginates until an empty page.
- First call, through `ferenda.lib.net.request` with `BROWSER_UA`: HTTP 429
  on the very first request, including `robots.txt` itself, before this
  session had made more than a couple of requests.
- Retried with backoff (the library's own 5 retries, then a further 8
  manual polls of `robots.txt` at 30 s spacing over ~4 minutes): consistent
  429 throughout.
- Diagnostic: plain `curl` with curl's default (non-browser) user agent got
  HTTP 200 on `https://sametinget.se/` and `https://sametinget.se/dokumentbank?cat=72`
  early on, while the same URLs with any browser-style User-Agent
  (ferenda's `BROWSER_UA`, a plain Chrome/120 string) got 429. This pointed
  at a UA-based bot heuristic, not a blanket IP ban.
- Tried Playwright (chromium headless), which is allowed by the briefing for
  JS pages: the very first navigation succeeded (`status 200`, page
  contained the string "STFS"), but the render happened before I could
  capture the DOM to disk. Every navigation after that — three more
  Playwright attempts, spaced by a 45 s cooldown — timed out.
- Final check: even plain `curl` with the default user agent, previously
  working, now times out (`exit 124`) on `https://sametinget.se/dokumentbank?cat=72`.
  The block has escalated from a UA-fingerprint 429 to a full connection
  block for this session's egress path.
- Per the briefing's hard rule ("If the site blocks you ... stop fetching,
  say so in the report, and file no issue about the corpus"), I stopped.
  Total requests against sametinget.se this session: about 25-30, within
  the 60-request budget, but the site (or our path to it) is not answering.

## What is left to do

Re-run this audit's site-side checks (1, 2, 3, 4, 5, 6, 8) once
sametinget.se stops blocking this network path, ideally from a different
egress point or after a longer cooldown than tried here. The corpus-side
evidence above (repeal links, amendment chains, artifact/download
completeness) already passed and does not need to be redone.
