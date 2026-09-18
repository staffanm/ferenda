# PMFS — Polismyndighetens författningssamling, Polismyndigheten
Verdict: DEFECT
Site list: 103 in-force families (4 pages) + 88 archived (upphävda) documents (4 pages), entry https://polisen.se/lagar-och-regler/polismyndighetens-forfattningssamling/
We hold: 124 pmfs + 92 rpsfs = 216 documents; newest PMFS 2026:18; site newest PMFS 2026:18 (matches)
Missing: 53 — 38 upphävande (repeal) acts behind the unvisited "upphävda" archive page (PMFS 2026:21, 2026:20, 2026:19, 2026:15, 2026:13, 2026:12, 2026:9, 2026:7, 2026:6, 2026:3, ... full list in issue) + 15 in-force families in a second `<li>` template `pmfs_enumerate` does not recognize (PMFS 2024:10, 2026:8, 2026:16, 2023:12, 2023:11, 2022:13, 2023:18, 2023:14, 2023:13, 2018:2, ...)
Extra: 50 held, not mentioned on either site page today — 29 already correctly marked repealed by something we hold (legitimate, superseded historical versions); 21 not repealed and not mentioned (likely quietly superseded by a consolidation with no discrete repeal act; no evidence tying them to the missing documents above, reported as shape only, not filed)
Repeal gaps: 6 sampled, 6 confirmed — RPSFS 2014:6, RPSFS 2012:5, RPSFS 2000:66, PMFS 2018:15, PMFS 2023:2, RPSFS 2012:34 all have zero incoming rpubl:upphaver links because their repealer is one of the 38 missing acts
Title defects: 0 for PMFS (11/12 sampled titles match the site exactly; 1 differs only by a stripped "Polismyndighetens" prefix, not junk). RPSFS carries 6 designation-as-title entries (RPSFS 1988:9, 1989:11, 1991:7, 2000:68, 2008:8, 2009:17) — out of scope, belongs to the rpsfs auditor
Consolidations: site yes (9 of 103 in-force families show a "Sammanställd" version), we hold 8 pmfs + 3 rpsfs consolidation files — no gap
Inherited: rpsfs — correctly filed under its own slug, no cross-slug misrouting (`kind='pmfs' AND label LIKE 'RPSFS%'` and the reverse both return 0 rows)
Issue: https://github.com/staffanm/ferenda/issues/77

## Evidence

Budget used: 20 HTTP requests to polisen.se (well under the 60 cap), all via
`ferenda.lib.net.request`, 1.5s between navigations.

**Enumerate.** `ferenda/foreskrift/agencies.py:420` `pmfs_enumerate` walks
`.../polismyndighetens-forfattningssamling/{1,2,3,4}/` (page 5 empty; 27+40+31+5
items). It never visits
`.../polismyndighetens-forfattningssamling---upphavda/` — grepped
`agencies.py` for "upphavda"/"upphävda", zero hits. That archive page has 4
pages (25+25+25+13, page 5 empty), all 88 entries are repeal-only
"upphävande" acts.

**Missing (archive).** Matched all 88 archive designations against
`documents` (kind in pmfs/rpsfs): 50 present, 38 absent. Confirmed 6 of the
absent repealers' targets in `catalog.sqlite` have no incoming
`rpubl:upphaver` link (see issue body for the exact query and table).

**Missing (in-force template).** Of 103 in-force `<li>` families, 23 render
without any `.c-regulation__label` element — the selector `pmfs_enumerate`
uses to read the base designation — so `base` comes back `None` and the
family is skipped (`agencies.py:437-438`, `if base is None ... continue`).
Compared PMFS 2026:18 (full template, no amendments) against PMFS 2016:6
(flat template, no amendments) — same shape of family, different site
markup, ruling out "only single-version families get the flat template."
15 of the 23 flat-template designations are absent from `documents`; 8 are
present (harvested before the template split, per download-record mtimes).

**Extra.** Built the set of every FS designation appearing anywhere in the
text of either listing (in-force ∪ archive, including designations quoted
inside repeal-act titles): 258 unique. Diffed against our 216 held pmfs+rpsfs
labels: 50 held labels never appear on either page. Checked each against
`links` for an incoming upphaver row from a document we hold: 29 have one
(legitimate — a later regulation we hold already repeals them), 21 don't.
None of the 21 match a designation quoted in the titles of the 38 missing
archive acts, so they aren't explained by the archive-page gap.

**Titles.** Sampled 12 PMFS in-force titles (`documents.title` vs the site's
`.c-regulation__title` text): 11 exact matches, 1 differs by a
"Polismyndighetens" prefix our copy drops (PMFS 2026:14) — inconsistent with
sibling entries that keep the prefix, but not junk (no file size, no
truncation, no PDF filename, no repeated designation).

**Consolidations.** 9 of 103 in-force families carry an `h4` subtitle
containing "Sammanställd". `site/data/downloaded/foreskrift/{pmfs,rpsfs}/*.json.br`:
8 pmfs and 3 rpsfs records have a non-empty `files.consolidation`.

**Inherited (rpsfs).**

    select label from documents where kind='pmfs' and label like 'RPSFS%'   -- 0 rows
    select label from documents where kind='rpsfs' and label like 'PMFS%'   -- 0 rows

No cross-slug misfiling; `pmfs_enumerate`'s `keep_prefix` routing works as
designed.

**Freshness.** Newest we hold: PMFS 2026:18 (pmfs), RPSFS 2014:8 (rpsfs).
Site's top in-force entry: PMFS 2026:18, dated 2026-09-01. Matches — the
freshness gap only shows up in the specific designations dropped by the two
bugs above, not at the "what's newest" level.
