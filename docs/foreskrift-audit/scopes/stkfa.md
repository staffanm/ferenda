# STKFA — Statskontoret, with inherited ESVFA (Ekonomistyrningsverket)
Verdict: OK
Site list: 10 designations from 19 pages (https://forum.statskontoret.se/ea-regelverket/)
We hold: 10 documents (esvfa), newest ESVFA 2022:10; site newest ESVFA 2022:10. 0 documents under stkfa on both sides.
Missing: 0
Extra: 0
Repeal gaps: 0 — no document in this scope is repealed or shown as upphävd on the site
Title defects: 0 — all 10 titles match the live page heading exactly
Consolidations: site yes (each leaf page is itself the consolidated text), we hold 10 of 10
Inherited: esvfa — all 10 site designations sit under the esvfa slug, none renumbered into stkfa; matches the live tree exactly
Issue: none

## Evidence

### Setup
Read `ferenda/foreskrift/statskontoret.py` and `test/test_foreskrift_statskontoret.py`
before fetching. The module walks `/ea-regelverket/` (root + 5 section pages +
leaf pages) rather than reading a list of PDFs: a leaf page's
`div.regelverk-page__box` heading names the ESVFA/STKFA designation it carries,
and the page itself is the consolidated text (no separately published
as-enacted PDF exists for this scope).

### Live walk (19 requests via `ferenda.lib.net.request`, 2 s+ apart, robots.txt
Crawl-delay respected)
Ran `statskontoret.enumerate_regulations` live against
`REGISTRY["stkfa"]`. It visited 19 pages: the root, 5 section pages
(forvaltning, finansiering, redovisning, revision, relaterade-regler), and 13
leaf pages. Of the 13 leaves, 3 name only the underlying förordning with no
ESVFA/STKFA box match (verified directly — `overlatelse-av-statens-losa-egendom`,
`forordning-om-avgift-for-administration-av-vissa-statliga-lan`,
`underlag-for-arsredovisning-for-staten` each hang a `regelverk-page__box`
whose heading is only "Förordningen (NNNN:NNN) om …", no ESVFA/STKFA
designation — Statskontoret/ESV has issued no föreskrift under those
förordningar). The remaining 10 leaves each name one ESVFA designation,
2022:1 through 2022:10. No STKFA designation exists anywhere in the tree.

### Catalog and download-record comparison
```
sqlite3 (via python): select kind, count(*) from documents where kind in
('stkfa','esvfa') group by kind
  -> [('esvfa', 10)]   # 0 under stkfa
```
The 10 catalog rows are exactly ESVFA 2022:1 .. 2022:10 — the same 10 the live
walk returns, same basefiles, same URLs.

Compared each download record's `updated_at`
(`site/data/downloaded/foreskrift/esvfa/esvfa-2022-*.json.br`) against the
`last-modified` meta tag the live page states now: identical for all 10
(e.g. esvfa/2022:1 both read "2026-01-20 09:54:34", esvfa/2022:10 both read
"2026-09-01 08:43:20"). The harvest is caught up; no stale consolidation.

### Titles (all 10, not just a sample)
Built `{identifier: title}` from `documents` and from a fresh live walk, and
diffed by identifier: all 10 titles match byte-for-byte
(e.g. "Ekonomistyrningsverkets föreskrifter och allmänna råd (ESVFA 2022:1)
om årsredovisning och budgetunderlag").

### Repeal check
None of the 10 documents is marked `upphavande` or targeted by an
`rpubl:upphaver` link (checked `links` table — no rows with that predicate for
this scope). Fetched two live pages and grepped for "upphäv"/"upphör": one
hit is a paragraph-level "har upphävts genom ESVFA NNNN:N" inside the
Övergångsbestämmelser text (normal amendment history inside a living
consolidation), not a document-level "upphävd" marking. No document in this
scope is struck through or shown as repealed on the site.

### Consolidations
Every one of the 10 download records carries `files.consolidation` (one HTML
file) and `files.regulation = None` — this scope publishes no separate
as-enacted text at all, by design (see the module docstring). Matches what
the site itself does: a leaf page is the current consolidated text, nothing
else.

### Amendments are metadata, not separate documents
The consolidated artifact for esvfa/2022:1 lists four amendments (ESVFA
2023:2, 2023:6, 2024:1, 2025:1) in its `amendments` field and `links` rows of
predicate `dcterms:references` pointing at
`https://lagen.nu/esvfa/2023:2` etc. These targets are not, and cannot be,
separate catalog documents: the site never publishes them as their own pages
— they exist only as named changes folded into the one consolidated leaf. This
is the intended shape for this scope (confirmed against
`test_the_consolidation_cutoff_is_the_newest_amendment`), not an "extra" or
"missing" document, and not a citation-graph defect worth filing (compare
memory note "Unresolved citation targets" — dangling target URIs are expected
where a source never publishes the cited document separately).

### Verdict on "we hold 0 under stkfa"
Confirmed correct, not a harvest gap: the live site currently has zero
STKFA-numbered föreskrifter. Statskontoret has not yet re-issued or amended
any EA-regelverket regulation under its own STKFA number since taking over
ESV's rulemaking — the whole in-force text is still ESVFA-numbered, and the
harvest already picks up STKFA the moment the tree names one (the `stkfa`
registry key drives the same `enumerate_regulations`/`parse_page`, which
matches `RE_DESIGNATION` on either series).

No corpus defect found. No issue filed.
