# RPSFS — Rikspolisstyrelsens författningssamling, Rikspolisstyrelsen (Polismyndigheten)
Verdict: DEFECT
Site list: 103 in-force families (4 pages, 31 RPSFS) + 88 archived repeal acts (4 pages, 74 name an RPSFS target), entry https://polisen.se/lagar-och-regler/polismyndighetens-forfattningssamling/
We hold: 92 documents (rpsfs), newest RPSFS 2014:8 (2014-07-07); site newest in-force RPSFS 2012:30 (consistent — RPSFS is a closed series since 2015)
Missing: 0 — every RPSFS designation on the current in-force listing is in our records
Extra: 15 — 13 correctly repealed by a PMFS act we hold (legitimate); RPSFS 1999:2 and RPSFS 2011:12 have no repeal link and aren't mentioned on any fetched page (shape only, not filed, same as PMFS report)
Repeal gaps: 30 confirmed — all covered by #77 (repealer is one of the 38 missing archive acts, e.g. PMFS 2026:21 → RPSFS 2014:6). New, filed: 3 repeal acts we DO hold (RPSFS 2000:68, 2008:8, 2009:17) parse with title=None and upphaver=[], so the regulations they repeal aren't marked repealed
Title defects: 6 designation-as-title (RPSFS 1988:9, 1989:11, 1991:7, 2000:68, 2008:8, 2009:17) confirmed independently, already flagged by the PMFS auditor, not refiled. New, filed: 17 titles splice the PDF's own margin "FAP nnn-n" code into the running text, 4 of them breaking a word at a hyphen (e.g. RPSFS 2000:53 "stråldo- FAP 206-2 ser" for "stråldoser")
Consolidations: site yes (3 RPSFS families marked "Sammanställd"), we hold 3 — no gap
Inherited: n/a — RPSFS is the predecessor series itself; no cross-slug misfiling (`kind='pmfs' and label like 'RPSFS%'` and the reverse both return 0 rows)
Issue: https://github.com/staffanm/ferenda/issues/86 (cites #77 for the 30 repeal gaps and the PMFS auditor's 6 junk titles)

## Evidence

Budget: 9 HTTP requests to polisen.se this session (in-force pages 2-4, archive
pages 1-5), via `ferenda.lib.net.request`, 2s between navigations. Page 1 of
the in-force listing was reused from the PMFS auditor's earlier fetch in the
same scratchpad (cached HTML, no RPSFS entries on it — RPSFS starts on page 2).

**Enumerate.** `pmfs_enumerate` (`ferenda/foreskrift/agencies.py:420`) reads
`.../polismyndighetens-forfattningssamling/{1..4}/`: 27+40+31+5 = 103
in-force families, 31 carrying an RPSFS `.c-regulation__label`. All 31 are
NOLABEL-free (the second in-force template bug from #77 only hit PMFS
entries on the pages sampled — grepped the NOLABEL `<li>` text on
rpsfs_page2.html, all 15 were "PMFS ...", none "RPSFS ..."). The shared
archive `.../polismyndighetens-forfattningssamling---upphavda/{,2,3,4,5}/`:
25+25+25+13+0 = 88 repeal acts, matching the PMFS report.

**Missing.** Diffed the 31 in-force RPSFS labels against `documents` (kind
rpsfs): all 31 present.

**Extra.** Built the set of every RPSFS designation named anywhere on the
9 fetched pages (as a family's own label, or quoted inside a repeal act's
title): 115 total. 92 held minus 115-union overlap leaves 15 held-but-
unmentioned; checked each against `links` for an incoming `rpubl:upphaver`
row:

    select from_uri from links where predicate='rpubl:upphaver' and to_uri='https://lagen.nu/rpsfs/<label>'

13 of 15 have one (RPSFS 1988:9, 1989:11, 2000:26, 2000:30, 2005:12, 2008:1,
2009:6, 2010:2, 2012:13, 2012:15, 2012:18, 2012:23, 2014:8 — repealed by a
PMFS act we hold). RPSFS 1999:2 and RPSFS 2011:12 have none and are not
named on any page — reported as shape, not filed (matches the PMFS
auditor's decision on the analogous 21-document bucket).

The reverse set — 37 RPSFS designations named only inside a repeal act's
title, never independently held by us (e.g. RPSFS 2000:33, 2008:9, 2012:8)
— is not a "missing from harvest" defect: the site does not host a PDF for
any of them (already repealed before our first harvest of this closed
series), only a title mention inside the act that repealed them. Nothing to
fetch, so not reported as missing.

**Repeal gaps.** For each of the 74 archive entries naming an RPSFS target,
checked whether the repealer exists in `documents` and, if held, whether it
carries the `rpubl:upphaver` link:

- 30 name a repealer absent from `documents` entirely — all 30 are inside
  #77's 38 missing archive acts (verified `PMFS 2026:21`, `2026:20`,
  `2026:19`, `2026:06`, `2026:03`, `2026:02`, `2025:10`, `2025:06`, `2025:01`
  do not exist as `documents` rows). Not refiled.
- 3 repealers (RPSFS 2000:68, 2008:8, 2009:17) ARE in `documents`, but their
  artifact metadata has `title: null` and `upphaver: []`
  (`site/data/artifact/foreskrift/rpsfs/{2000-68,2008-8,2009-17}.json`), so
  `documents.upphavande` is unset and the repealed target carries no
  incoming link. New, filed as issue #86.

**Titles.** Sampled all 92 titles for junk shapes. Confirmed the PMFS
auditor's 6 (`documents.title == documents.label`). Regex-scanned for a
spliced FAP reference (`FAP\s?\d{2,3}[-_]\d+` inside the title, not at the
very end in parentheses): 18 hits, 17 confirmed as genuine splices against
either the site's own archive-listing text for that designation or plain
word-break evidence (a hyphenated Swedish word split across the FAP code);
excluded RPSFS 2001:4 as a plausible genuine parenthetical reference at the
title's end. Verified 4 of the 17 clip mid-word:

    RPSFS 2000:53 | "...Dokumentation av stråldo- FAP 206-2 ser"
    RPSFS 2000:55 | "...Överfallslarm vid allvar- FAP 206-4 ligt brottsligt angrepp"
    RPSFS 2005:10 | "...avseende snat- FAP 400-3 teribrott"
    RPSFS 2006:1  | "...bruk av narkoti- FAP 420-3 ka och drograttfylleri"

Full table and reproduction commands are in issue #86.

**Consolidations.** `site/data/downloaded/foreskrift/rpsfs/*.json.br`: 3 of
87 download records carry `files.consolidation` (RPSFS 2005:9, 2009:13,
2014:8) — matches the 3 "Sammanställd" families seen in the in-force
listing.

**Inherited.**

    select label from documents where kind='pmfs' and label like 'RPSFS%'   -- 0 rows
    select label from documents where kind='rpsfs' and label like 'PMFS%'   -- 0 rows

**Freshness.** Newest we hold: RPSFS 2014:8 (2014-07-07). RPSFS is a closed
series — Rikspolisstyrelsen became Polismyndigheten in 2015 and PMFS
replaced it — so no newer RPSFS designation can exist. The newest RPSFS
still in force on today's site is RPSFS 2012:30; the gap between it and our
newest (2014:8) reflects later RPSFS documents being repeal acts or already
superseded, not a freshness defect.

**#76 check.** No RPSFS designation appears in issue #76's 108-record
pdftotext-mismatch list.
