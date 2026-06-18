# SOU archive recompression — findings

Investigation into recompressing the scanned SOU PDFs from <https://sou.kb.se/> so the
~500 GB / 6129-document collection becomes manageable on resource-constrained systems,
while staying **viewable** and **re-OCR-able** (the bundled OCR is poor and we intend to
replace it).

Test document: `urn-nbn-se-kb-digark-3885594.pdf` (SOU 1975:23, *JO-ämbetet*), 313 pages,
117.9 MB.

## Source characteristics

`pdfinfo` / `pdfimages -list`:

- One full-page image per page: **1780×2779 px, 300 ppi**.
- Stored as **RGB (ICC), 8-bit/channel, JPEG**, already **4:2:0 chroma-subsampled**.
- ~377 KB/page average; PDF 1.3, produced by Quartz PDFContext (2013).
- **Effectively grayscale**: mean R−G channel difference ≈ **0.007** (on 0–1). These are
  scans of cream paper with black text; only the covers carry any real colour, and that
  colour carries no information.

## Key findings

### 1. Converting RGB → grayscale saves almost nothing
Because colour is already 4:2:0-subsampled (the two chroma channels cost very little) and
the JPEGs are already moderately compressed, re-encoding to grayscale JPEG lands at
**100–118 %** of the original — sometimes *larger*, because re-encoding an existing JPEG
adds noise that costs bits. Grayscale is the honest representation, but it is not the lever.

### 2. The real lever is **bitonal (1-bit)**
"Bitonal" = 1 bit per pixel, pure black/white, no grey. Fax-style run-length encoding
(CCITT Group 4) or a symbol dictionary (JBIG2) then compresses it hard.

| Strategy | Size vs source |
|---|---|
| Grayscale re-JPEG (q60–85) | 100–118 % (no win) |
| Grayscale, downsample to 200 ppi, q75 | ~78 % |
| **Bitonal CCITT G4, 300 ppi (30-page chunk)** | **~28 %** |
| Bitonal G4, single clean text page | ~11 % |

### 3. Bitonal does not hurt OCR (on clean text)
Tesseract output on a bitonal page vs. the colour original was character-for-character
near-identical; remaining differences are noise-level errors present in both. Bitonal does
cost the occasional diacritic (e.g. `klagomål` → `klagomal`) — see §"grayscale-OCR option".

### 4. Resolution sweet spot
Page is exactly 300 ppi (1780 px / 5.93 in). OCR accuracy vs. resolution (token
disagreement against the 300-ppi-grayscale baseline, clean page):

| ppi | grayscale | bitonal |
|---|---|---|
| 300 | reference | small |
| ~250 | minimal loss | — |
| ~200 | minimal loss | moderate |
| 150 | mild loss | noticeable |

- **Grayscale OCR is flat from 300 down to 200 ppi**; even 150 is usable on clean type.
- **Bitonal degrades faster** below 300 ppi (binarising a low-res scan loses stroke detail).
- Recommendation: **keep 300 ppi if storing bitonal** (a 1-bit 300-ppi page is already tiny,
  and it protects OCR). If storing grayscale instead, **200 ppi** is the sweet spot.
- "Springing for more pixels" is *not* warranted — the scans are already 300 ppi and the
  OCR problems we hit were bugs, not resolution (see §Bugs).

### 5. Preprocessing (unpaper / deskew) is mostly a no-op *on this document*
Adaptive/local thresholding handles tone/contrast; it does **not** fix geometry. But on
this document the geometry is already good:

- Detected skew ≤ 0.3° on most pages (Tesseract tolerates a few degrees internally anyway).
- The "black borders" are **faint grey** edges/gutter shadow — a normal threshold maps them
  to white. A whole-document border scan found **zero** pages with a dark frame.
- Three pipelines (threshold-only / +deskew / +unpaper) gave **equal OCR confidence
  (~94 %)** and near-equal size; deskew shaved ~1 %, unpaper was byte-for-byte equal to
  threshold-only.

Conclusion for the collection (varying scan quality):
- **Adaptive threshold (Sauvola): always** — this is the contrast workhorse.
- **Deskew: keep, gated** (only rotate if detected angle > ~0.5°; harmless no-op otherwise).
- **Despeckle: cheap insurance** for noisier scans.
- **unpaper's full clean / border / mask scan: skip by default.** Its auto border/mask
  detection can **silently crop real content** — unacceptable for a legal archive. If ever
  used, disable auto-scan (`--no-mask-scan --no-border-scan`).

## Recommended pipeline

1. Extract page images (`pdfimages -j`).
2. Grayscale → **conditional deskew** (gate > 0.5°) → **adaptive/Sauvola threshold** to
   1-bit → **despeckle** → **CCITT G4** (or JBIG2 lossless).
3. Keep **page 0 (cover) as a small grayscale JPEG** (binarises poorly, no text value).
4. **Stamp 300 dpi** on every page image (critical — see Bug 1).
5. Re-OCR with Tesseract `-l swe`, embed a hidden text layer, assemble a searchable PDF.

`ocrmypdf` already does steps 4–5 (and JBIG2 optimisation) out of the box; it does **not**
do the colour→bitonal conversion (step 2), so that stays a pre-step.

## Bugs discovered (and fixes)

These were all in the proof-of-concept pipeline, not inherent to the approach.

1. **Scrambled reading order + wrong page size — missing DPI tag.** Without a DPI tag the
   G4 TIFFs default to ~70 dpi; Tesseract then builds the page mediabox at 1830×2858 pts
   (should be 427×667) and squashes the invisible-glyph coordinates, so `pdftotext`/
   copy-paste re-orders words (one word per line, jumbled). **This was the cause of the bad
   OCR, not resolution.** Fix: `convert ... -density 300 -units PixelsPerInch`. Restored
   correct page size *and* reading order.

2. **`-deskew` breaks G4 TIFF write.** ImageMagick `-deskew` leaves a negative virtual-canvas
   offset; the TIFF writer rejects it (`negative image positions unsupported`). Fix:
   `+repage` immediately after deskew.

3. **9 pages silently dropped (304/313) — transient convert failures.** Pages 22, 31, 39,
   72, 97, 106, 124, 133, 142 failed during the batch (the deskew angle-probe `convert`
   returned an empty angle, and the binarise `convert` errored). Re-running those exact
   pages in isolation **succeeds (exit 0)** — so they are *not* bad images; the failures are
   transient (resource contention in the probe+despeckle path). **Must fix for production:**
   retry failed pages, fold the deskew-angle detection into a single convert instead of a
   separate probe, and **assert output page count == source page count** so pages can never
   be silently lost from a legal document.

## Results (test document)

| | original | compressed (current artifact) |
|---|---|---|
| Size | 117.9 MB | **13.5 MB** (~11.5 %, **8.7× smaller**) |
| Pages | 313 | 304 ⚠️ (9 dropped — Bug 3, not yet fixed) |
| Page size | 427×667 pts | 427×667 pts ✓ |
| Text layer | poor bundled OCR | fresh Tesseract `swe`, correct reading order ✓ |

Artifact: `urn-nbn-se-kb-digark-3885594.compressed.pdf`. **Not yet production-correct** — it
is missing 9 pages pending the Bug 3 fix. Otherwise the recompression and OCR are sound.

## Production notes for the downloader rewrite

- **Use Leptonica's Sauvola for binarisation, not ImageMagick.** ImageMagick's local
  threshold (`-lat`) produced noisy text here (OCR confidence ~34 % vs ~93 % global). A
  global threshold worked for this well-behaved document but is illumination-sensitive and
  will fail across 6129 docs of varying quality. Leptonica's Sauvola (what Tesseract uses
  internally) is the right tool.
- **Install `jbig2enc`** (not currently installed) and run the bitonal layer through it in
  lossless mode for a further ~20–30 % saving. **Never use JBIG2 lossy mode** — its symbol
  substitution can silently swap digits, catastrophic for legal text.
- **grayscale-OCR option:** for maximum OCR accuracy, OCR the *grayscale* image (Tesseract
  binarises it optimally; recovers the diacritics bitonal loses) but store the *bitonal*
  image — decouple OCR input from storage format (e.g. via hOCR + an assembler, or
  `ocrmypdf`).
- **Integrity check:** assert page count and ideally a per-page non-blank check; do the
  recompression once at download time as proposed.

### Projected collection impact

- ~8.7× → **500 GB ≈ 60 GB**.
- With JBIG2 lossless (and 200 ppi where grayscale is acceptable): **~40–45 GB**.
- **Cost caveat:** adaptive binarisation is CPU-heavy (~0.5–0.7 s/page). ~6129 docs ×
  ~250 pages ≈ 1.5 M pages ⇒ hundreds of single-threaded CPU-hours. Parallelises trivially,
  but budget for it — another reason to recompress once, at download time.

## Tooling used

`pdfimages`, `pdfinfo`, `pdftotext` (poppler); `tesseract` 5.x (`swe`, `eng`);
ImageMagick `convert`; `unpaper` 15.2; `ocrmypdf` 15.2 (all installed). **`jbig2enc` is
NOT installed** — needed for the further JBIG2 saving above.
