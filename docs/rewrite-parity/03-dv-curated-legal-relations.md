# DV curated legal relations

**Status:** Implemented 2026-07-18 — full-corpus verification folds into
[finding 6](06-corpus-acceptance-and-verification.md) (requires the DV
re-parse + relate + full golden run)
**Priority:** P0

## Resolution

Curated metadata is normalized at parse time through the same citation
grammar the body uses, into inline-run lists stored beside the raw strings
(`metadata.{lagrum,forarbeten,related,litteratur}`, each entry
`{"text": raw, "runs": [...]}`), with typed predicates preserved
(`rpubl:lagrum`, `rpubl:forarbete`, `rpubl:rattsfallshanvisning`,
`dcterms:relation`). `lib/catalog.py:curated_links()` projects the resolved
runs into the links table; `render_dv` shows the four groups; unresolved
strings survive as plain runs. Two fallbacks recover what the grammar cannot
read: lagrumLista's `sfsNummer` (law-level link) and a hanvisning's
`gruppKorrelationsnummer` (authoritative join to the cited case's publication
group, resolved through the identity index — 13,307/13,307 grupp-carrying
entries resolve on the harvested corpus; ambiguous split groups are dropped,
not guessed). When the grammar and the grupp join both resolve but disagree,
the grammar's link stands and the conflict is recorded on the entry
(`grupp_konflikt`), so the acceptance pass can list exactly the edges that
may be wrong. Two discoveries against the original finding:
`europarattsligaAvgorandenLista` never holds citations — corpus-wide it takes
exactly three values ("EU-rätt" ×45, "Europarättsligt avgörande" ×43,
"Mänskliga rättigheter" ×10, across 98 records) — so it is retained as topic
labels (`metadata.europarattslig`, shown beside Rättsområde) but mints no
relation edge; `litteraturLista` (3,228 entries) was dropped entirely by the
parser and is now retained. `tools/golden_dv.py`
reports body-derived and curated recall separately plus their union. A
300-case oracle sample measures body 96.1%, curated 44.2%, union 96.4% —
the residual old-only refs are absent from both prose and API metadata
(old-pipeline context artifacts), not projection loss.

## Finding

The DV API's curated lagrum, preparatory-work and related-decision metadata is
preserved in the JSON artifact but is not projected into the catalog relation
graph. The current graph relies primarily on citations that occur literally in
the decision body.

This loses authoritative relations that the old pipeline deliberately
published and that cannot be recovered by improving the prose scanner.

## Evidence

`dv/parse.py:parse_api_record()` reads `lagrumLista`, `forarbeteLista`,
`hanvisadePubliceringarLista` and `europarattsligaAvgorandenLista`. Its artifact
projection stores the values under `metadata.lagrum`, `metadata.forarbeten` and
`metadata.related`.

`lib/catalog.py:artifact_links()` consumes inline links from structure/body,
top-level generic `references`, and a few other generic producers. It does not
read these DV metadata fields.

The old `ferenda/sources/legal/se/dv.py` intentionally parsed Lagrum, Rättsfall
and Litteratur metadata into linked mixed content with predicates such as
`rpubl:lagrum`, `rpubl:rattsfallshanvisning` and `dcterms:relation`.

The measured result in `REWRITE.md` is consistent with this omission: prose
extraction recalls 81.2% of `lagrumLista`, while the full golden comparison
recalls 95.6% of the old reference graph. The document calls much of the
shortfall “editor-derived”; that explains why it is absent from prose but is not
a reason to discard it.

## Impact

- Inbound and outbound legal-relation views omit curated lagrum and related
  authorities.
- Graph consumers get a weaker result than the old site even when the source
  API supplies the missing semantics explicitly.
- Scanner recall and metadata completeness are conflated, making golden results
  appear to be parser limitations rather than projection loss.

## Required work

1. Normalize curated lagrum, förarbete and related-case strings through the
   same citation grammar used for prose.
2. Project successfully resolved values into ordinary artifact link runs or an
   equally generic top-level relation representation.
3. Preserve meaningful predicates instead of flattening every relation to
   `dcterms:references`.
4. Retain unresolved source text explicitly so a failed normalization does not
   erase metadata.
5. Render the curated relation groups on decision pages.
6. Extend the DV golden tool to report body-derived and metadata-derived recall
   separately as well as their union.

## Acceptance criteria

- Every curated API relation is resolved, retained as unresolved text, or
  rejected by an explicit rule.
- Catalog links contain the union of body citations and curated metadata without
  duplicate edges.
- Representative old Lagrum, Rättsfall and Litteratur sections have equivalent
  new outbound links.
- The full DV golden run has no unexplained credible relation regressions.
