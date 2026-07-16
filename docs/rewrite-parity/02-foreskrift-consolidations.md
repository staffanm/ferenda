# Föreskrift consolidation publishing

**Status:** Open
**Priority:** P0

## Finding

Föreskrift consolidation PDFs are parsed and stored in JSON artifacts, but the
consolidated text is not carried through rendering, search, fragment indexing
or citation relation extraction.

A record with no base regulation PDF and only a consolidation can therefore
produce an artifact containing a body while its public page and search document
contain no regulation text.

## Evidence

`foreskrift/parse.py:parse_record()` parses every downloaded consolidation into
a `Consolidation.structure`. `foreskrift/model.py:to_artifact()` serializes the
result under the top-level `consolidations` array.

The downstream consumers do not walk that array:

- `lib/render.py:render_foreskrift()` renders only `art["structure"]`.
- `lib/text.py:document_text()` and `fragment_texts()` walk only the shared
  `structure` and `body` sections, plus amendment `content`.
- `lib/catalog.py:artifact_links()` scans top-level structure, body and SFS-style
  amendment content, but not consolidation structures.

The parser itself documents that some records intentionally have an empty base
`structure` and carry their only parsed body in `consolidations`.

## Impact

- Consolidated law text can be invisible on the public page.
- The most useful in-force text may be absent from full-text search and MCP
  retrieval.
- Paragraph fragments and inbound links from consolidated text are missing.
- Parsed content in the source-of-truth artifact is handled inconsistently by
  the derived layers.

## Required work

1. Define which text the main regulation page presents: normally the latest
   applicable consolidation, with the original regulation clearly available.
2. Render all available consolidation versions through an explicit version
   selector or equivalent stable URLs.
3. Include consolidation structures in full-document text, fragment indexing,
   citation extraction and bulk output semantics.
4. Keep fragment identities version-aware where two consolidations contain the
   same paragraph identifier.
5. Display the consolidation cutoff (`konsolideradTom`) and the amendments it
   incorporates.
6. Add fixtures for a consolidation-only record and a record with several
   consolidation versions.

## Acceptance criteria

- A consolidation-only artifact renders a non-empty, citable public document.
- The latest consolidation is searchable and retrievable through the API and
  MCP surfaces.
- Citations occurring only in consolidated text appear in the catalog graph.
- Multiple versions do not collide in generated paths or fragment URIs.
- Tests cover rendering, text extraction, fragments and relations rather than
  only artifact serialization.
