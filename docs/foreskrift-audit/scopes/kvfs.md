# KVFS — Kriminalvårdens författningssamling, Kriminalvården

Verdict: DEFECT
Site list: 118 designations, 1 page (JS "load more", 12 clicks) at
https://www.kriminalvarden.se/om-oss/styrning-och-uppfoljning/foreskrifter-och-allmanna-rad/foreskrifter-i-nummerordning/
(the assigned entry page, .../kriminalvardens-foreskrifter/, now lists only
12 current in-force texts + 1 register PDF, 1 page)
We hold: 81 documents (kvfs) + 11 (kvvfs), newest KVFS 2026:10; site newest KVFS 2026:14
Missing: 38 — KVFS 2019:2, 2019:3, 2019:4, 2023:2, 2024:1..2024:15 (all 15),
  2025:1, 2025:3, 2025:4, 2025:6, 2025:7, 2025:8, 2026:1..2026:9, 2026:11..2026:14
Extra: 12 — all legitimately superseded (repeal covers 8 of them via a
  document we hold; the rest amend a fully repealed base we never held, see
  Evidence)
Repeal gaps: 3 confirmed parse defects (false or duplicated `upphaver`
  targets), not missing-repealer gaps — KVFS 2011:1, KVFS 2011:7, KVFS 2020:3
Title defects: 10+ — the full running text of the föreskrift is stored as
  `documents.title` instead of a short heading (examples below); two titles
  have their words reordered/interleaved
Consolidations: site yes (12 families), we hold 0 in `files.consolidation`
  (stored instead under `files.regulation`, and drifting — see Evidence)
Inherited: kvvfs — 11 documents, all correctly under the `kvvfs` slug, none
  renumbered into `kvfs`; none of the 11 sit on the current site (all fully
  historical, matches the repeal model)
Issue: https://github.com/staffanm/ferenda/issues/68

## Evidence

### 1. Enumeration

The agency config's `index_url`
(https://www.kriminalvarden.se/om-oss/styrning-och-uppfoljning/kriminalvardens-foreskrifter/)
now lists only 12 direct PDF links (the current in-force "konsoliderad"
texts for each FARK family, plus one un-amended grundförfattning) and the
register PDF (`kvfs-regelforteckning.pdf`). No individual amendment PDF is
linked from that page any more:

    requests.get(index_url) -> 200, 13 PDF links total (12 texts + register)

The site's real, complete listing lives at a different URL entirely:

    https://www.kriminalvarden.se/om-oss/styrning-och-uppfoljning/foreskrifter-och-allmanna-rad/foreskrifter-i-nummerordning/

which is a Sitevision component that server-renders no PDF links at all
(0 in the initial HTML) and requires JavaScript: a "Visa fler" button loads
10 more rows per click, "Visar 10 av 119 föreskrifter" going up to
"Visar 119 av 119". Rendered with playwright chromium (12 clicks, 2 s apart),
119 anchors resolve to 118 unique PDFs / distinct KVFS designations
(1987-2026), the true full corpus. `agencies.py`'s `KVFS` entry never visits
this URL, so `indexed_enumerate` cannot see any of it.

### 2. Missing (38)

Extracted every `kvfs-YYYY[-_ ]N` filename+text pair from the 118 fully
rendered "nummerordning" rows and diffed against `catalog.sqlite`:

    ours = {label from documents where kind='kvfs'}   # 81
    site = {118 designations parsed from the rendered PDF links}
    site - ours = 38, e.g. KVFS 2024:1 .. KVFS 2024:15, KVFS 2025:1,
      KVFS 2025:3/4/6/7/8, KVFS 2026:1..9, KVFS 2026:11..14, KVFS 2023:2,
      KVFS 2019:2/3/4

Every 2024, 2025 and 2026 amendment except the three we already hold
(2025:2, 2025:5, 2026:10 — all three are current in-force "konsoliderad"
texts still linked from the old index page) is missing. The gap tracks
exactly when kriminalvarden.se stopped linking individual amendment PDFs
from the old index page.

### 3. Extra (12)

    KVFS 2006:12, 2009:7, 2011:3, 2012:5, 2013:1, 2013:2, 2013:3, 2013:6,
    2014:2, 2014:6, 2016:6, 2017:4

Checked each against `links` (`predicate = 'rpubl:upphaver'`):
- 2006:12, 2009:7, 2011:3, 2012:5 are each repealed by a document we hold
  (2025:2, 2019:11, 2019:1, 2019:10 respectively) — correct, legitimate.
- 2013:1/2/3, 2014:2 amend a base regulation, KVFS 2011:15 ("myndighetens
  lokala organisation"), that is itself fully repealed by KVFS 2014:7 (which
  we hold, `upphaver -> kvfs/2011:15`). KVFS 2011:15 is not in our corpus,
  but it is not on the live site or in the register PDF either — a fully
  repealed grundförfattning the site itself no longer lists. Not a defect.
- 2013:6, 2014:6, 2016:6, 2017:4 amend chains later replaced by a
  "konsoliderad" FARK family text (Strafftid, OC-spray). No individual
  upphaver row targets the amendment itself; that is expected under the
  repeal model — the repeal is recorded against the family's base document.

### 4. Repeal gaps — 3 confirmed parse defects

    sqlite3 site/data/catalog.sqlite \
      "select from_uri, to_uri from links where predicate='rpubl:upphaver'
       and from_uri like '%/kvfs/%'"

- `kvfs/2011:1` lists `upphaver` targets including `kvfs/2011:7` and
  `kvfs/2011:9` — its own *later amendments*. A grundförfattning from
  January 2011 cannot repeal amendments to itself from later in 2011.
  Root cause: our stored PDF for "KVFS 2011:1" is not the as-published 2011
  text but the perpetually-updated "konsoliderad" file at a fixed URL — its
  own front matter today reads "Ändringar införda t.o.m. KVFS 2026:11"
  (verified: `pdftotext -layout kvfs-2011-1-regulation.pdf`). The parser
  read the amendment-history list on that living document and mis-tagged
  the whole family's amendment chain as `upphaver` instead of `andrar`.
- `kvfs/2011:7` (one amendment in that same chain) independently carries
  almost the identical wrong `upphaver` list — the same mis-extraction
  applied a second time to a sibling document.
- `kvfs/2020:3` (FARK Ungdomsövervakning) lists `upphaver -> kvfs/2020:2`
  (FARK Transport) — two unrelated regulations. Its PDF explains why:
  "Obs! Detta rättelseblad ersätter tidigare utgivna KVFS 2020:2 som utkom
  den 18 december 2020. Rättelsen avser författningens löpnummer." This is
  a corrigendum: an earlier printing of *this same* Ungdomsövervakning
  regulation was mistakenly numbered "2020:2" before it collided with the
  already-published FARK Transport (which legitimately holds that number)
  and was reprinted as 2020:3. The parser read "ersätter ... KVFS 2020:2"
  and resolved it to the wrong document — the unrelated Transport regulation
  that happens to hold the same designation today.
- A fourth, weaker instance: `kvfs/2013:2 -> upphaver -> kvfs/2013:1`, two
  successive amendments in the same "lokala organisation" chain — the same
  class of mis-extraction, lower confidence.

### 5. Titles

Compared `documents.title` (our catalog) against the register PDF's own
`Rubrik:` line (`kvfs-regelforteckning.pdf`, `pdftotext -layout`) for 10
designations:

| designation | site Rubrik | our title |
|---|---|---|
| KVFS 2006:12 | "...om statsbidrag till vissa organisationer inom kriminalvårdens område" | full body dump, ~180 words incl. every `1§ .. 5§` |
| KVFS 2012:1 | "...ändring i ... (KVFS 2011:1) om fängelse" | full preamble + entire `Omfattning:` clause, ~230 words |
| KVFS 2011:10 | "...ändring i ... (KVFS 2011:2) för häkte" | full body incl. the amended paragraph's new wording |
| KVFS 2011:12 | "...ändring i ... (KVFS 2011:2) för häkte" | full multi-clause `Omfattning:`, ~150 words |
| KVFS 2011:14 | "...ändring i ... (KVFS 2011:6) ..." | full body |
| KVFS 2009:2 | (no Rubrik row; site's own short label) "Skjutvapen för kriminalvårdstjänstemän..." | short — OK |
| KVFS 2011:1 | "Fängelse (KVFS 2011:1, FARK Fängelse)" | short — OK |

At least 10 of the 81 documents carry the entire running text of the
föreskrift (every numbered paragraph) as `title` instead of the heading
sentence. Two further titles have their words reordered:

    KVFS 2022:4: "Föreskrifter om ändring i Kriminalvårdens KVFS 2022:4
    föreskrifter och allmänna råd (KVFS 2011:1) FARK Fängelse Utkom från
    trycket den om fängelse 7 oktober 2022"
    KVFS 2022:5: same pattern, FARK Häkte

### 6. Consolidations

The site publishes 12 "konsoliderad" (currently in force) PDFs, one per
FARK family. `files.consolidation` is empty for all 81 download records:

    grep -c consolidation.*\\[\\] -> 0 of 81 carry a populated consolidation list

Each of the 12 is instead stored as `files.regulation` under the family's
*original* identifier (e.g. "KVFS 2011:1"), and that stored PDF is a living
document the source keeps rewriting in place — see the repeal-gap evidence
above, where "KVFS 2011:1"'s own text already reflects amendments through
KVFS 2026:11, a document from five years after 2011:1 was decided.

### 7. Inherited: kvvfs

    select label, title, date from documents where kind='kvvfs'  -- 11 rows

All 11 sit under `kvvfs`, none duplicated or renumbered into `kvfs`. None of
the 11 appear on the current site or in the register PDF (all are old
"upphävande" notices for 1980s/1990s Kriminalvårdsverket regulations) —
consistent with the repeal model, not a defect. Several kvvfs titles carry
OCR noise from old scans ("(KV VF S 1987z3)", "7 a & förvaltningsprocess-
lagen (1971: 291)"), a source-scan-quality issue rather than an extraction
bug, noted but not filed.

### 8. Freshness

Site newest: KVFS 2026:14 (from the rendered "nummerordning" list).
We hold newest: KVFS 2026:10 (2026-06-08). Four designations behind, part of
the missing-38 harvest gap above.

### Commands used

    requests.get(index_url, headers={'User-Agent': BROWSER_UA})
    requests.get(kvfs-regelforteckning.pdf) ; pdftotext -layout
    playwright chromium headless, "foreskrifter-i-nummerordning", 12x
      "Visa fler" clicks, 2 s apart
    sqlite3 site/data/catalog.sqlite (documents, links tables, read-only)
    ferenda.lib.compress.read_text on artifact/downloaded .json.br files
    pdftotext -layout on kvfs-2011-1-regulation.pdf, kvfs-2020-3-regulation.pdf

HTTP requests to kriminalvarden.se: ~16 (index page, register PDF, plain
fetch + playwright fetch of nummerordning, 12 "Visa fler" clicks). Well
under the 60-request budget.
