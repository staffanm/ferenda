# Föreskrift consolidation publishing

**Status:** Implemented 2026-07-18 — the corpus-wide re-parse/relate/generate
folds into [finding 6](06-corpus-acceptance-and-verification.md)
**Priority:** P0

## Resolution

Measured first: of 9,056 föreskrift artifacts, 1,624 carry a consolidation
(62 of 86 series have none — agencies aren't required to publish them), and
only 4 carry more than one, of which 1 was the same PDF listed twice and just
3 are genuine two-version records. That killed the version-selector idea from
the original finding: the shipped model is *presented text + as-enacted view*,
not version navigation.

`lib/text.py:presented_consolidation()` picks the latest parsed consolidation
(by `konsolideradTom`); `body_sections()` lets it **replace** the base
`structure` as the document's presented body — walking both would double
every fragment id and index superseded text beside its replacement. Search
(document + fragment), the MCP pinpoint reader and
`catalog.py:artifact_links()` all read through `body_sections()`, so the
page, the index and the citation graph carry exactly the same text; dumps
export the artifact verbatim and already contained the consolidations.
`render_foreskrift` presents the consolidation under a banner naming the
cutoff amendment and stating that the compilation is inofficial, renders the
ändringsförfattningar register, and links the as-enacted base text at
`{uri}/grund` — a `.grund.json` sidecar the parse run emits beside the main
artifact whenever both texts exist (1,577 records), rendered as an
uncatalogued extra page exactly like the SFS lydelse artifacts. Fragment
identity is presented-version-only: `#P1` always means the text the canonical
page shows; the grund page repeats the anchors on its own URL and is
deliberately outside the catalog/search (citations target the current text).
Duplicate consolidation PDFs dedupe at parse. The 8 consolidations that
parsed to no structure are two failure shapes — image-only scans (TSFS
2024:10, 301 pages) and cover-sheet stub PDFs (RFFS/FKFS) — and now fall
back to the base text with the agency's own consolidated PDF linked from the
page (the consolidation `url` is retained in the artifact).

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
