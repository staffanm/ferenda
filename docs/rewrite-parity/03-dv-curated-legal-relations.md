# DV curated legal relations

**Status:** Open
**Priority:** P0

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
