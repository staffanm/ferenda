# Legal relations and source validation

**Status:** Resolved 2026-07-19 (all five sub-areas; corpus-wide re-parses
fold into [finding 6](06-corpus-acceptance-and-verification.md))
**Priority:** P1

## Finding

Several implemented verticals still have known semantic relations or
source-specific validation work that has not reached the derived graph and
public artifact contract. These are separate from bulk materialization: they
require code, fixtures or an explicit modelling decision.

## Föreskrift relations

**Resolved 2026-07-19.** `andrar` is extracted from the ändringsförfattning's
own harvest title (designated refs, chained titles, and the bare
"föreskrifter (2007:12)" form that implies the record's own series — an SFS
parenthesis never mints a target); the konsoliderad masthead's amendment list
folds into the register and the register's minted uris project as
`metadata.andradAv`. `catalog.relation_links` publishes `rpubl:andrar`,
`rpubl:upphaver`, `rpubl:genomforDirektiv` and `rinfoex:andradAv` as typed
edges (field-driven on metadata keys, excluded from the generic inbound
panel); render shows Ändrar/Upphäver outbound and "Upphävs eller ersätts av"
on the target via `catalog.upphaver_inbound`, and the directive page's
inbound panel now lists transposing föreskrifter. Outbound and inbound
mirrors are covered by `test_site.py` and `test_foreskrift_parse.py`; two
identity bugs found en route (ÅFS/RÅFS minting under the wrong samling, the
`bfnar`/`rams` slugs falling out of layout's föreskrift grammar) are fixed
and regression-locked. The corpus-wide re-parse/relate/generate folds into
[finding 6](06-corpus-acceptance-and-verification.md).

The original finding: the föreskrift model has fields for `bemyndigande`,
`upphaver`, `andrar` and `genomfor`. The parser extracts some of these, but
`andrar` remains empty and the catalog adds only `bemyndigande` as a
dedicated metadata edge. Required closure:

- extract `ändrar` from the amendment/consolidation evidence;
- publish `ändrar`, `upphäver` and `genomför` with stable typed predicates;
- render each relation group; and
- test both outbound edges and their inbound mirrors.

## EUR-Lex metadata validation

**Resolved 2026-07-19.** There is no legacy oracle (the old code never
supported EUR-Lex beyond an experimental module), so `tools/golden_eurlex.py`
validates the carried metadata fields — CELEX, date, title, OJ reference,
ECLI, doctype — against authoritative CELLAR metadata itself, frozen to the
retained snapshot `test/files/eurlex/cellar-snapshot.json` (parties and typed
legal relations were never modelled and are out of scope by decision). The
sample is deterministic and stratified over what the corpus holds: all 50
treaties, 120 regulations, 100 directives, 80 corrigenda, 150 judgments and
the 2 sector-5 strays; decisions and consolidated acts are absent by harvest
design (sector 3 is enumerated R/L only), not missing from the sample. The
compare is a change detector with a named adjudication-rule set plus a
per-document ledger (the golden_sfs pattern), and `--reparse` exercises the
current parser instead of the stored artifact tree — which is how it
distinguishes parser regressions from upstream drift against the frozen
snapshot. The run surfaced and drove four parser fixes: judgment artifacts
carried the *referral* date (the first DATE in JUDGMENT.INIT) instead of the
delivery date in TITLE; missing or calendar-impossible dates and corrigendum
dates now fall back to the notice.ttl work date already on disk; OJ numbers
are unpadded ("L 042" → "L 42"); and page-long misextracted titles (a
treaty's whole preamble or table of contents in title position) are rejected.
Final state: **zero unexplained differences** across all five fields.
Fixture-locked in `test_eurlex_parse.py`/`test_eurlex_html.py`; the corpus
re-parse (EURLEX_CODE changed) folds into
[finding 6](06-corpus-acceptance-and-verification.md).

The original finding: establish a representative frozen sample and compare
identifiers, dates, titles and doctypes with authoritative EUR-Lex metadata,
distinguishing parser regressions from upstream changes and retaining the
snapshot used for adjudication.

## JO and ARN validation

**Resolved 2026-07-19.** The inventory comparison (join on *any*
diarienummer, 2-digit years normalized, garbled identities adjudicated by the
frozen headnote's own "Diarienummer :" value): of 3,291 frozen cases, five
are genuinely absent from live jo.se — imported as `jo-legacy` records via
`avg/legacy.py:import_jo` (`lagen avg import-legacy jo …`), a one-time
importer since deleted (§7g teardown, 2026-07-19), with headnote-curated
titles and the frozen PDFs. The same import writes the
ämbetsberättelse map (1,619 citations, 1,774 dnr keys) from the distilled
RDFs; `parse_jo` grafts `official_report` onto live records too (modeled,
serialized as `metadata.officialReport`, rendered, and folded into the
search document so the citation form is findable). `classify_arn` strips the
live-PDF margin header and restated-summary front matter, anchored to the
referat's own änr; verified across all 140 live artifacts with a 25-case
frozen snapshot byte-identical, regression-locked in `test_avg.py`. The avg
relate ran; the corpus-wide re-parse (AVG_CODE changed) folds into
[finding 6](06-corpus-acceptance-and-verification.md).

The original finding: the live JO corpus has not been fully compared with
the frozen legacy corpus. That comparison is needed because the redesigned
upstream site may omit older decisions that lagen.nu previously carried.
The JO model also lacks the `official_report`/ämbetsberättelse citation
recorded in the closure checklist. ARN's classifier explicitly performs no
masthead filtering even though the known live-PDF margin header and repeated
bold summary can surface as leading body blocks. Required closure:

- compare complete live JO coverage with the frozen inventory and import genuine
  omissions;
- model, serialize, render and index JO `official_report`;
- remove known ARN masthead noise with regression fixtures; and
- run the complete JO/JK/ARN relation and generation path.

## Frozen-corpus semantics

**SOSFS konsolidering resolved 2026-07-19.** The decision: they are
consolidations of their base regulations (each self-titled "Senaste version
av SOSFS X:Y"), not independent artifacts — implemented exactly through the
föreskrift consolidation model as `files.consolidation` entries on the base
records. The documents are Socialstyrelsen HTML pages despite the frozen
`index.pdf` filenames (one real PDF among 87), so parse gained an
extension-routed `parse_consolidation_html` (same classify/nest text
pipeline; the "Ändrad: [t.o.m.] …" line yields the cutoff and register refs,
minted under each ref's own samling across the 2015 SOSFS→HSLF-FS
transition). 76 attached (5 duplicate fetches skipped), 2 missing bases
imported (sosfs/2011:9, hslffs/2018:54). Reviewed exclusions: 6 entries with
no document bytes on a base we already carry, plus sosfs/2014:7 (no bytes,
no base — nothing existed to import). Fixture-locked in
`test_foreskrift_parse.py`; corpus-wide relate/generate folds into
[finding 6](06-corpus-acceptance-and-verification.md).

The original finding: the frozen SOSFS `konsolidering/` tree is currently
skipped. Decide whether those documents are versions of their base
regulation, independent historical artifacts, or redundant copies, then
implement that decision consistently with the current föreskrift
consolidation model.

**OCR chronology resolved 2026-07-19.** Parse now records which route a body
came through (`Forarbete.ocr`: the pdftotext scan fallback, ABBYY XML, and
the skanning2007/trips HTML adapters); for OCR bodies,
`censor_future_citations` demotes any link whose target year exceeds the
basefile year + 1 *and* whose own cited text carries that year — preserving
the text verbatim, never rewriting, and reporting each demotion in the
artifact's `suspect_citations`. The year-in-text condition scopes the check
to digit garbling; the sweep separately surfaced a **named-law anachronism**
(historical documents' "kommunallagen"-style references resolve to the
modern namesake statute) that is deliberately out of scope here and recorded
in REWRITE §7g as its own issue. Fixture-locked in
`test_forarbete_parse.py`/`test_forarbete_legacy.py`; a 150-document sweep
over 1970s props and 1935–1975 SOUs found zero genuine future citations.

The original finding: OCR-era preparatory works also need a chronology
sanity check so a garbled citation cannot point to legislation newer than
the citing document. The check must report or preserve suspect text rather
than silently rewriting it.

## Acceptance criteria

- All four föreskrift relation fields have defined extraction and catalog
  semantics, with fixtures and inbound/outbound checks.
- The representative EUR-Lex oracle reports no unexplained metadata regressions.
- Live and frozen JO inventories reconcile, including explicit source
  precedence.
- JO official-report citations and ARN body cleanup are visible in artifacts and
  pages.
- Every skipped SOSFS consolidation is imported or covered by a reviewed
  exclusion decision.
- Impossible future citations are detected and reported without inventing a
  replacement target.
