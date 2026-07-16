# Förarbete correctness tail

**Status:** Open
**Priority:** P1

## Finding

The förarbete vertical is broadly implemented, but known body-coverage,
pagination and structural parsing gaps remain. These affect citation anchors
and the presentation of legally significant tables and
författningskommentarer, so they are replacement-parity work rather than
optional polish.

## Evidence

The closure checklist in `REWRITE.md` identifies all of the following as open:

- fetch the remaining lr/SÖ bodies;
- account for printed-page offsets instead of assuming PDF page equals printed
  page;
- parse general and continued tables;
- recover remaining legacy DOC/DOCX bodies;
- unify the two författningskommentar bounds; and
- repair known truncated proposed-law headings.

The current implementation confirms the gaps:

- `forarbete/parse.py` states that each PDF page maps directly to one printed
  page and uses that index for `#sidN`.
- `forarbete/legacy.py` treats `.doc`, `.docx` and `.wpd` bodies as metadata-only
  because no parse route exists. WordPerfect is deliberately outside closure,
  but DOC/DOCX are not.
- `forarbete/kommentar.py` records that one commentary finder can truncate on a
  misdetected chapter heading while another uses a more robust bound.
- The specialised jämförelsetabell parser does not provide a general table
  representation for all preparatory works.

## Impact

- Page citations can resolve to the wrong anchor when cover matter or numbering
  offsets exist.
- Some legacy documents have metadata but no accessible body despite the old
  corpus retaining one.
- Continued tables can be flattened or detached from their headers.
- Författningskommentar extraction can stop early or attach text to a truncated
  law title, weakening SFS correspondence derivation.

## Required work

1. Fetch and regression-lock the remaining lr/SÖ source bodies.
2. Detect printed page numbers from headers/footers and store a PDF-page ↔
   printed-page mapping; fail visibly when the mapping is ambiguous.
3. Add a general table model, including continuation across page boundaries and
   repeated or omitted headers.
4. Reuse the established office-document conversion path for recoverable DOC
   and DOCX files.
5. Replace the two commentary span algorithms with one tested implementation.
6. Repair the known truncated law headings and add each as a fixture.

WordPerfect bodies remain an explicit exclusion unless the closure scope is
changed.

## Acceptance criteria

- Printed-page anchors agree with a representative sample from every major
  source family and era.
- Every recoverable DOC/DOCX body is materialized or explicitly adjudicated.
- General and continued-table fixtures retain rows, columns and headings.
- Both commentary consumers use one span implementation.
- Known truncated headings and commentary cutoffs are fixture-locked.
- Full legacy inventory counts reconcile body-bearing and metadata-only records.
