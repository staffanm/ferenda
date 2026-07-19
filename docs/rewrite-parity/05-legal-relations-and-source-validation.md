# Legal relations and source validation

**Status:** Open
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

The EUR-Lex vertical has parser fixtures but lacks the representative
metadata cross-check required by `REWRITE.md`. Establish a frozen sample across
regulations, directives, decisions, corrigenda and consolidated acts and compare
identifiers, dates, titles, parties and legal relations with authoritative
EUR-Lex metadata.

The check must distinguish parser regressions from upstream metadata changes
and retain the source snapshot used for adjudication.

## JO and ARN validation

The live JO corpus has not been fully compared with the frozen legacy corpus.
That comparison is needed because the redesigned upstream site may omit older
decisions that lagen.nu previously carried.

The JO model also lacks the `official_report`/ämbetsberättelse citation recorded
in the closure checklist. ARN's classifier explicitly performs no masthead
filtering even though the known live-PDF margin header and repeated bold summary
can surface as leading body blocks.

Required closure:

- compare complete live JO coverage with the frozen inventory and import genuine
  omissions;
- model, serialize, render and index JO `official_report`;
- remove known ARN masthead noise with regression fixtures; and
- run the complete JO/JK/ARN relation and generation path.

## Frozen-corpus semantics

The frozen SOSFS `konsolidering/` tree is currently skipped. Decide whether
those documents are versions of their base regulation, independent historical
artifacts, or redundant copies, then implement that decision consistently with
the current föreskrift consolidation model.

OCR-era preparatory works also need a chronology sanity check so a garbled
citation cannot point to legislation newer than the citing document. The check
must report or preserve suspect text rather than silently rewriting it.

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
