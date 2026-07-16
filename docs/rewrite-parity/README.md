# Rewrite parity findings

Status as reviewed against `REWRITE.md`, the current `accommodanda/` code and
the mounted development corpus on 2026-07-16.

These are the six findings that must be closed before claiming that
accommodanda provides functional parity with the old lagen.nu legal-information
pipelines and public product:

1. [DV legacy coverage and published identity](01-dv-legacy-coverage-and-identity.md) — **closed**
2. [Föreskrift consolidation publishing](02-foreskrift-consolidations.md)
3. [DV curated legal relations](03-dv-curated-legal-relations.md)
4. [Förarbete correctness tail](04-forarbete-correctness-tail.md)
5. [Legal relations and source validation](05-legal-relations-and-source-validation.md)
6. [Corpus acceptance and verification](06-corpus-acceptance-and-verification.md)

The intended claim is deliberately narrower than literal compatibility with
the entire Ferenda framework. `REWRITE.md` explicitly replaces RDF/Fuseki and
the old internal APIs with source-owned JSON artifacts, REST/OpenAPI, NDJSON,
MCP and OpenSearch. It also excludes PBR, WordPerfect bodies and other listed
deferred work. Those are accepted substitutions or exclusions, not findings in
this set.

A suitable claim after all six findings are closed is:

> Accommodanda is a feature-complete replacement for lagen.nu's
> legal-information pipelines and public product, with documented protocol
> substitutions and exclusions.

An unqualified claim that accommodanda “matches the old codebase” would also
imply compatibility with the retired generic Ferenda framework and its
publishing protocols, which is neither implemented nor an architectural goal.
