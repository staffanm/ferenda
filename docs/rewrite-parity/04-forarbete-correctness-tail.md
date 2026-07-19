# Förarbete correctness tail

**Status:** Resolved 2026-07-19 (corpus-wide re-parse folds into
[finding 6](06-corpus-acceptance-and-verification.md))
**Priority:** P1

## Resolution

1. **lr/SÖ bodies** — the landings always carried the `/contentassets/`
   links; the assets served transient non-documents at harvest time, leaving
   1,523 lr + 1,214 SÖ records body-less. `lagen forarbete refetch-bodies`
   (download.py:`refetch_bodies`) re-reads each stored landing's content
   links and fetches them again. Full sweep run 2026-07-19: **2,732
   body-less records checked, 2,707 bodies recovered, 0 errors** — the
   corpus now stands at lr 2,738/2,744 with bodies, SÖ 3,505/3,524; the 25
   residual records genuinely serve no document today (re-tried by any
   later run). Spot-checked recovered bodies parse end-to-end (a 2000
   lagrådsremiss, 1919/1921 SÖ scans). Regression-locked in
   `test_forarbete_download.py`.
2. **Printed-page offsets** — `lib/pdftext.py` gains `printed_pageno` (the
   marginal folio, header-stripped) and `page_offset` (the constant offset
   from per-page evidence: mode with majority support; competing offsets
   raise — a wrong page anchor is silent citation corruption, so ambiguity
   fails visibly). `parse_pdf` stamps *printed* pages on every block;
   unnumbered cover matter gets no anchor. A 42-PDF era sweep: 37 at offset
   0, five real corrections (SOU 1989:67 was off by 3), zero ambiguous.
   The OCR/scan route keeps the page≈printed assumption (its `\f` split has
   no marginal geometry to read).
3. **General tables** — `forarbete/tabell.py`: a conservative geometric
   detector for *data* tables (aligned column starts, numeric-evidence gate,
   TOC/margin/lydelse exclusions) plus `merge_continued` joining a table
   across a page break with its repeated header dropped. Prose-celled
   listings stay out of this first cut by design — the legally significant
   lydelse/jämförelse tables have their own reconstructions, and the gate
   exists precisely so OCR-era fragmented prose can never shred into cell
   salad (SOU 1989:67: 30 genuine statistics tables, no prose loss).
4. **DOC/DOCX recovery** — landed earlier (commit ead96b82: `.docx` read as
   OOXML, `.doc` via antiword; ~1,308 props recovered). Re-scoped here: the
   296 remaining word-era docdirs are all `.wpd`-only. WordPerfect stays an
   explicit scope exclusion, **but** the "all covered elsewhere" premise was
   false: 82 of them (all 1995/96) have no parsed body in any corpus, and
   `soffice --convert-to docx` (libwpd) converts them cleanly — recovery is
   a scope decision, not a technical gap.
5. **FK bounds unified** — `fk_span` moved to `kommentar.py` (fk.py imports
   it); `kommentar.extract` now uses it instead of the level-1-bounded
   `find_kommentar` (which remains only for förordningsmotiv's level-3
   layout). Corpus-validated: implements edges 2,000 → 2,972, gains exactly
   the in-FK-pseudo-rubrik truncation class; the few removed edges sat in
   bilagor (Lagrådet quotes) — false authoritative-commentary edges.
6. **Truncated rubriks** — `join_dangling_rubriks` re-attaches the statute
   name dropped off "Förslag till lag om ändring i" rubriks (following
   stycke, mis-classified rubrik, all-caps era style, TOC dotted leaders,
   glued-onto-next-paragraph). 115 of 126 corpus-wide re-join; the residual
   11 are genuinely ambiguous (mid-word OCR truncation, 1902-era chancery
   prose). Fixture-locked in `test_forarbete_parse.py`.

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
