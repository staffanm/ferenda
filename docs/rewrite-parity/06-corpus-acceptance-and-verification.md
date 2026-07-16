# Corpus acceptance and verification

**Status:** Open
**Priority:** P1; final release gate

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
