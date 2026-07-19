# DV legacy coverage and published identity

**Status:** Closed (2026-07-16; re-implemented 2026-07-19, see below)
**Priority:** P0

## Re-closure (2026-07-19)

The implementation this document's evidence describes was committed as
`8226f5eb` but survives only on `origin/modernization` — the local line
diverged before it and kept the documentation without the code, which is why
the 7g legacy-corpus audit rediscovered 6,418 uncovered frozen referats. The
coverage was re-implemented on the current line with a different mechanism:
notis bodies imported from the old pipeline's frozen intermediate XML and an
oracle identity sidecar distilled from the old distilled RDFs
(`lagen dv import-legacy`), rather than the shared-Word-bundle ledger below.
Three of `8226f5eb`'s validated identity rules were ported: oracle målnummer
is metadata, never a linkage key (AD 1993 nr 22 / AD 1994 nr 13 under
A 112-92); an M-bridge is refused between components with conflicting
referat identities (RH 2016:61/62); and referat spelling variants
(colon / "ref." / "nr") normalize to one identity. Final census: 23,938
canonical cases, 23,901 parsed with zero errors, 21,594/21,595 old distilled
RDFs matched by URI (the one miss is a source header typo the old pipeline
propagated). See REWRITE.md §7g and `tools/golden_dv.py` for the adjudicated
result. The evidence below is retained as the record of the earlier,
unmerged implementation.

## Finding

DV's production driver enumerates API and legacy identities, parses either API
JSON or original Word into the same `Avgorande` artifact projection, and
publishes the old referat/notis/verdict URI schemes. The bounded local corpus is
fully materialized, every excluded remote ambiguity has been adjudicated, and
identity, artifact, catalog, dump and generated-page populations reconcile.

## Evidence

The direct-file import copied exactly 1,638 selected remote files
(23,248,023 bytes). Together with seven pre-existing files, the local direct
tree contains 1,645 files: 793 non-empty Word originals and 852 zero-byte notis
placeholders. `legacy-index.json` hash-checks and records header-derived
referat, målnummer and date for every non-empty file, preventing opaque or
incorrect filenames from creating false cases.

Notis bodies come from 197 shared Word bundles (47,409,809 bytes), not the
placeholders. Their exact sidecar maps 5,936 historical placeholder identities
to headings actually present in the bundles. It excludes seven headings absent
from the old placeholder ledger; filename ranges are not trusted because 21
bundle names disagree with their contents.

The 2026-07-16 enriched census is:

- 17,325 API records and 7,608 legacy source records;
- 23,770 canonical cases: 267 in both stores, 17,052 API-only and 6,451
  legacy-only;
- the legacy-only population is 5,936 notis cases plus 515 direct Word cases;
- 31 same-referat `MOD`/`MMOD` pairs are the expected 2011 institutional
  succession; there are no unexpected cross-court unions.

A production parse over all 23,770 identities completed with zero errors. The
artifact audit found exactly 23,770 current JSON artifacts, no missing or stale
paths, no duplicate public URIs and no empty legacy structures. The 1,035 empty
structures are API-backed metadata/summary-only publications, not dropped
legacy bodies.

The reconciliation also found and fixed two over-linking rules before closure.
Legacy files are no longer fused merely because they share a målnummer: one
proceeding can produce several published decisions (23 previously collapsed AD
referat were restored). Attachment variants now require the old feed's shared
filename stem. A målnummer also cannot bridge API and legacy components that
already carry conflicting strong referat identifiers; this restores the
separate `RH 2016:61` and `RH 2016:62` publications. Regression tests cover
both cases. The legacy parser also restores the omitted `AD` series in the one
header that prints only `2016 nr 10`, preserving `/dom/ad/2016:10`.

All 57 withheld remote originals were staged for header parsing and individually
recorded in [`legacy-ambiguities.json`](../../accommodanda/dv/data/legacy-ambiguities.json).
Fifty-six match an API publication by exact referat and date. The remaining
`PMD/PMÖÄ8867-16_1.docx` has no legacy referat header, but matches the sole
same-date/målnummer API record (`PMÖD 2016:1`) and its 948-character editorial
summary exactly. All 57 are API duplicates, zero are unresolved, and none
needed permanent transfer.

The focused old-corpus golden in
[`legacy-golden.json`](../../accommodanda/dv/data/legacy-golden.json) checks a
direct Word referat plus HDO, REG/RÅ and modern HFD notis-bundle cases. All four
match the old public URI and reference set; metadata and the applicable
structure contract match as well. Run it with `python tools/golden_dv_legacy.py`.
The full old-RDF change detector matches 21,593 cases by public URI, reports
zero disjoint published identifiers, and retains 95.5% of the old reference
set. Its only two URI absences are errors in the old oracle: `T4823-16.rdf`
prints `NJA 2017 s. 1101` instead of the API's `NJA 2017 s. 1011`, and the
`2005-59.rdf` file identifies itself as `AD 2004 nr 59` instead of `AD 2005 nr
59`. Remaining citation/metadata differences are the separately documented
parser-correctness tail, not missing legacy identities.

The final derived run contains 23,770 distinct DV rows in `catalog.sqlite`,
23,770 distinct NDJSON dump records, and 23,770 generated DV pages. Every
identity-index entry maps to one artifact, catalog row, dump URI and expected
page path.

`lib/casenaming.py` now restores the old
`dom/{publisher}/{malnummer}/{avgorandedatum}` scheme for non-referat verdicts
using the old publisher-slug table. `dv reindex` prunes artifacts whose former
canonical IDs are no longer in the rebuilt index, so corrected identity rules
cannot leave duplicate documents for `relate`/dump.

## Impact

- The original P0 omission is closed for the bounded corpus: legacy-only cases
  participate in an ordinary build and failures are visible.
- The bounded transfer remains exactly 1,638 selected direct files; reviewing
  the 57 ambiguous candidates proved they were duplicates rather than silently
  widening that import.
- Remaining DV work concerns curated legal relations, tracked separately in
  [finding 03](03-dv-curated-legal-relations.md), not coverage or identity.

## Required work

None for this finding. Keep API-first body/metadata selection for the 267 linked
cases unless the separate curated-relations finding demonstrates a field that
must be merged.

## Acceptance criteria

- [x] Every current identity-index entry produces exactly one canonical
  artifact; stale superseded artifacts are pruned.
- [x] All 5,936 placeholder-ledger notis cases and all 515 proven direct
  legacy-only cases in the bounded import have non-empty artifacts.
- [x] Referat, notis and non-referat verdict artifacts use the old public URI
  grammars, with no URI collisions in the current corpus.
- [x] The 57 ambiguous remote candidates are individually included or excluded
  with reviewed reasons.
- [x] Representative newly covered cases pass old-corpus golden comparison.
- [x] DV counts reconcile identity index → artifacts → catalog → dump → pages.
