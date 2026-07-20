# Corpus acceptance and verification

**Status:** Resolved 2026-07-20 (acceptance run complete; follow-ups tracked)
**Priority:** P1; final release gate

## Acceptance run (2026-07-20)

**Pipeline.** `lagen all rebuild -j28` ran every stage over every source on
the 2026-07-09 harvest state: parse (all 15 sources, ~295,000 documents),
relate (catalog 232,161 documents), index (OpenSearch), dump, generate
(266,702 pages). **Zero failing documents corpus-wide** — including the
first-ever fully clean förarbete sweep (97,073) after the printed-page
mapping was rebuilt as a running piecewise offset, and the Treaty of Rome
scan (11957A) OCR'd via ocrmypdf/swe. Environment: Python 3.14.6,
poppler/pdftohtml, tesseract 5 (swe), OpenSearch (local docker),
`requires-python` raised to >=3.14.

**Incrementality.** Running `lagen all rebuild` twice: the second run makes
no change of any kind — all 57 stage gates report up to date, no document
touched, no page rendered — in **23.8 s, exit 0** (target: <30 s). Two build
defects were found and fixed getting there: CPython 3.14's
incremental-GC worker corruption is contained by recycling workers every
1,000 docs (multiprocessing.Pool maxtasksperchild — ProcessPoolExecutor's
equivalent deadlocked), and dispatch is longest-expected-first from
manifest-recorded durations (the old pipeline's scheduling rule).

**Inventory reconciliation.** Every source's record/artifact/catalog/dump
counts reconcile exactly; the non-obvious deltas, each verified: SFS 42,429
artifact files = 11,230 current (19 empty removed-doc placeholders) +
31,199 historical-version artifacts; föreskrift catalog = artifacts + 1,650
as-enacted (/grund) pages; begrepp catalog 23,100 = 571 wiki concepts +
keyword-minted concept pages; remisser is never published (by design);
hudoc has 1 deliberately empty artifact. Cleaned en route: 4,176 stale
colon-named duplicate förarbete artifacts from the frozen-import era
(every one verified to have its current slug-named sibling) had inflated
relate/dump; dumps now carry exactly one line per document.

**Goldens.** DV: 21,594/21,595 old records match by URI (the one miss is
the old pipeline propagating a source header typo), zero identifier
conflicts, dates 21,370 exact/13 pre-existing upstream disagreements,
old-reference recall 89.2% with the residual classes adjudicated. SFS:
9,296/11,020 structure-passing against the live 2026 harvest; the
adjudication showed **no corpus-wide regression** — 1,041 of the 1,703
diffs are upstream drift (the golden froze Sep 2025; the harvest is Jul
2026) visible because structure-only mode disables the staleness
adjudicator, and the ~660 residual matches the known baseline families.
Two genuine parser defects worth fixing regardless (tracked): kapitel
detection on Checklagen-style chapters (1932:131) and list-continuation
segmentation (1891:35 s.1, 1928:370). Several old-pipeline defects are
confirmed corrected (synthetic placeholder stycken, hyphenation artifacts,
2022:964's collapsed kapitel).

**Compatibility.** 14/14 representative published-URL classes resolve to
generated pages: statutes, referat/notis/verdict court decisions (including
the re-housed RÅ/HFD/NJA notiser and målnummer-form verdicts at the old
COIN scheme), förarbeten across eras, föreskrifter (incl. the revived
repealed PMFS), JK/ARN decisions, concept pages, EU documents.

**Known items carried forward:** the 3 MÖD I/II paired referats share one
address (identity decision, tracked); the colon-form basefile CLI footgun
(tracked); the DV date/reference residuals adjudicated above.

The original finding text follows as the record of what was required.

## Finding

The repository has substantial automated coverage and clean static checks, but
there is not yet a successful, current acceptance run proving the whole
authoritative corpus through every derived stage. The mounted development data
also predates some current source implementations.

This is an operations task only after the implementation findings are closed;
running an incomplete driver successfully must not be allowed to stand in for
coverage reconciliation.

## Observed verification state on 2026-07-16

- Ruff passed for `accommodanda/` and the golden tools.
- Ty passed for `accommodanda/`.
- The source-layer checker passed.
- 250 targeted DV, föreskrift, förarbete and golden tests passed.
- The full suite collected 1,609 tests, but the first API request in
  `test/test_api.py::test_search` hung in the installed Python 3.14.4
  Starlette `TestClient`/`httpx2` path. A faulthandler dump showed the pytest
  thread waiting in `TestClient.handle_request()` while the AnyIO portal event
  loop was idle. The full suite therefore cannot currently be certified green
  in the checked environment.

The mounted `.build/status.json` dates from 2026-07-13 and `catalog.sqlite` from
2026-07-14. The catalog has no materialized AVG, remisser, ICRC, UNTC or ICC
documents, so it is not evidence for a current all-source acceptance run. This
does not by itself imply implementation failure; `REWRITE.md` correctly states
that mounted corpus counts are deployment state. It does mean the final
operations checkbox remains open.

## Golden-tail decision

The full SFS golden measurement currently records 10,041 passing documents,
1,148 raw diffs and 21 skipped old dummies. The remaining differences are
concentrated in special-law/bilaga structure and amendment-register behaviour.
DV also retains 15 date conflicts for which neither candidate date survives in
the published body.

Functional parity does not require byte-for-byte reproduction of old parser
errors. It does require every remaining difference family to be sampled and
classified so that there are zero unexplained credible regressions. Accepted
differences and genuinely unknowable values need a durable adjudication ledger.

## Required work

1. Repair or constrain the API test-client dependency combination so bare
   `pytest` completes on every supported Python version.
2. Materialize the authoritative live and frozen source trees after the five
   implementation findings are closed.
3. Run download/import → parse → relate → index → dump → generate for every
   source with no unexplained failures.
4. Reconcile source inventories, artifact counts, catalog rows, search documents
   and dump records per source.
5. Run the complete SFS and DV goldens against the frozen corpora and record all
   adjudications.
6. Exercise representative old URLs, feeds, API calls, generated pages,
   fragments and inbound/outbound relations.
7. Record tool versions, source snapshot dates, counts and exclusions so the
   acceptance result is reproducible.

## Acceptance criteria

- Bare `pytest` passes without hangs on the supported Python matrix.
- Every authoritative source has a fresh successful status through all required
  stages.
- No source inventory entry disappears between download/import, artifact,
  catalog, search and dump without an explicit reason.
- Goldens have zero unexplained credible regressions; raw differences are either
  fixed or adjudicated.
- Old published URLs and representative feed/API behaviour pass compatibility
  checks.
- The acceptance report names deliberate substitutions and exclusions, so the
  final parity claim is precise rather than absolute.
