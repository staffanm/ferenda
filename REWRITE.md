# Ferenda rewrite plan

Status of the effort to rebuild ferenda — the framework behind lagen.nu —
keeping its accumulated domain knowledge while discarding the framework
that wrapped it. Living document; update status markers as work lands.

Legend: ✅ done · 🚧 in progress · ⬜ not started · 💤 deliberately deferred

Corpus counts in this document are dated measurements from the run described,
not a promise about whichever development or production data tree is currently
mounted. Implementation status means the source can be built through the normal
driver; materializing and refreshing a particular deployment is an operations
task, tracked separately from parser/library completion.

---

## Rewrite closure checklist

This is the finite backlog for declaring the rewrite complete. Detailed
sections below explain each item and retain the historical measurements.

- ✅ **Golden baseline and triage:** full SFS and DV corpus measurements,
  conservative temporal adjudication, normative DV structure fixtures and
  representative legacy-skeleton sampling are complete. Credible sampled
  parser regressions are fixed and fixture-locked. The intentionally unaccepted
  tail is bounded: SFS special-law/bilaga and amendment-register improvements,
  plus 15 DV date conflicts for which neither date survives in the published
  body (§3d, §4).
- ✅ **DV coverage and published identity:** the recoverable NJA notisfall are
  ingested and the legacy verdict URI grammar for non-referat cases restored
  (§4, §6; closed 2026-07-16, re-implemented after a divergent-branch loss
  2026-07-19 — 6,418 frozen-era cases including all notis bodies, 23,901
  parsed with zero errors, 21,594/21,595 old RDFs matched, `docs/rewrite-parity/01`).
- ✅ **DV curated legal relations:** the API's curated lagrum/förarbete/
  rättsfall/litteratur metadata normalized through the citation grammar and
  projected as typed graph edges, with the grupp join as authoritative
  fallback (§4; implemented 2026-07-18, `docs/rewrite-parity/03`; full-corpus
  verification folds into the acceptance run).
- ✅ **Föreskrift consolidation publishing:** the parsed konsoliderad version
  is now the presented text — rendered, searched, fragment-indexed and
  citation-scanned as one body, with the as-enacted base text at `{uri}/grund`
  (§7e; implemented 2026-07-18, `docs/rewrite-parity/02`; the corpus-wide
  re-parse folds into the acceptance run).
- ✅ **Förarbete correctness tail (2026-07-19):** lr/SÖ bodies recovered by
  the `refetch-bodies` second-chance pass (the landings always carried the
  links; the assets served transient non-documents at harvest time);
  printed-page numbers derived from marginal folios (`printed_pages`, a
  running piecewise offset in the old pipeline's style — 2026-07-20: the
  first constant-offset cut rejected 13 documents whose numbering shifts
  mid-file, so the offset now follows detections, with misread folios
  quarantined and appendix numbering-restarts never adopted; SOU 1989:67's
  anchors were off by 3); a conservative generic
  data-table model with cross-page continuation (`forarbete/tabell.py`); the
  FK bounds unified onto `fk_span` (+972 genomför-direktiv edges, appendix
  false-edges dropped, validated corpus-wide); the truncated "lag om ändring
  i" rubriks re-joined (115/126 corpus-wide, fixture-locked). DOC recovery had
  landed earlier (ead96b82); `.wpd` stays excluded by scope — but note 82
  wpd-only props have *no* body anywhere and soffice converts them cleanly
  (§7a, §7d, §7g; `docs/rewrite-parity/04`).
- ✅ **Derived legal relations:** föreskrift `ändrar` extracted (title +
  konsoliderad-masthead evidence) and published with `upphäver`/`genomför`/
  `ändradAv` as typed graph edges, rendered both directions (§7e).
- ✅ **Source validation tail (2026-07-19):** the EUR-Lex metadata cross-check
  landed — `tools/golden_eurlex.py` validates the carried fields (CELEX, date,
  title, OJ ref, ECLI, doctype) of a 502-document stratified sample against a
  retained CELLAR snapshot with an adjudication ledger; zero unexplained
  differences after fixing four parser defects it caught (§7d). The JO/ARN
  half landed the same day: live-vs-frozen JO inventory reconciled (five
  genuine omissions imported), `official_report` modeled/rendered/searchable,
  ARN masthead noise stripped (§7f).
- ✅ **Frozen-corpus tail (2026-07-19):** the skipped SOSFS consolidations
  landed as `files.consolidation` entries on their base records, and the OCR
  chronology sanity check demotes-and-reports citations whose target year
  post-dates the citing document (§7e, §7g).
- ✅ **SFS omitted graphics:** the graphics/formulas/maps/road-signs the
  text-only SFST source omits are detected, vision-localized to the
  provenance-correct published PDF, cropped and rendered (§3d).
- ✅ **Corpus acceptance run (operations, 2026-07-20):** `lagen all rebuild
  -j28` ran parse → relate → index → dump → generate over all 15 sources
  (~295k documents) with **zero failing documents corpus-wide**, a clean
  <30 s no-op incremental re-run, exact inventory reconciliation, DV/SFS
  goldens adjudicated with no corpus-wide regression, and 14/14
  published-URL classes resolving. See
  `docs/rewrite-parity/06-corpus-acceptance-and-verification.md`. Counts are
  recorded per run, not hard-coded as code completion criteria.

Explicitly outside closure scope: new source families; PBR; WordPerfect bodies;
greenfield citation grammars with no active caller; optional wiki taxonomy and
reading-column commentary presentation. They remain possible product work, not
unfinished replacement infrastructure.

---

## 1. Why, and the shape of the replacement

The old codebase works but is overengineered in the wrong places. Its
central mistake is **inheritance**: `DocumentRepository` /
`SwedishLegalSource` expose ~50 overridable hook methods, so every source
is entangled with the framework's whole call graph and pays for every
other source's special cases. Understanding one source means
understanding everything.

Guiding decisions (settled over the course of this work):

- **Keep the domain knowledge, replace the framework.** Two decades of
  SFS/DV formatting quirks and citation grammar are the asset; the god
  class is not.
- **Sources are programs; shared code is libraries.** A source calls into
  shared code; shared code never calls back into a source.
- **The parsed artifact on disk is the source of truth** for *all*
  extracted semantics — structure, metadata, and links are one artifact,
  not separate concerns. SQLite/OpenSearch are derived and rebuildable.
- **Machine-readable publishing survives without RDF as the primary surface.**
  REST/OpenAPI + raw-artifact NDJSON dumps + an MCP server are implemented; no
  GraphQL. Fuseki is retired and OpenSearch replaces Elasticsearch.
- **The internal model is ours** — typed dataclasses with Swedish domain
  vocabulary, not tied to the dead rpubl/rinfoex vocabularies. Any
  Akoma Ntoso / RDF mapping is a downstream *projection*, not the model.
- **Native artifact format:** source-owned typed JSON, without a universal
  envelope or JSON-LD context. The raw artifact is also the bulk-dump record;
  RDF/Akoma Ntoso can be added later as downstream projections if a consumer
  requires them.
- **Split the codebase, not the repo:** data pipeline vs consuming apps
  (web is just one consumer), divided at the artifact boundary, same repo.

### Target architecture (three layers)

1. **Vertical source pipelines** — `accommodanda/sfs/`, `accommodanda/dv/`,
   `accommodanda/forarbete/`, … Each owns its full chain (fetch → extract →
   parse → typed model → artifact) and its *own* document model. No universal
   `Document` base class; share conventions as small libraries, not
   inheritance. Each exposes only its artifacts plus a tiny orchestrator
   protocol (`download()`, `parse(basefile)`, `list_basefiles()`).
2. **Horizontal libraries** — genuinely cross-source machinery: the
   citation engine (lagrum/förarbete/rättsfall recognition), the small pieces
   of identity/URI grammar genuinely shared by multiple consumers, fetch
   utilities, the make-like incremental build driver (a good idea from the old
   code — keep it, as a dumb orchestrator over file freshness, not as methods
   on a class), and the golden-corpus validation harness.
3. **Corpus-wide derived layer** — the reborn `relate` phase. Reads
   published artifacts across all sources into the SQLite catalog + OpenSearch;
   computes the inbound-link graph (case law annotated onto statute
   paragraphs, förarbeten linked from the paragraphs they comment on —
   lagen.nu's killer feature). Depends only on artifacts, never on source
   internals.

Sequencing principle: **don't design the horizontal layer from SFS alone**
(it's the most idiosyncratic source). Build a second vertical (DV) by
copying from the first, then extract what actually duplicated.

Current code layout (this three-layer split is now realized in the package):

```
accommodanda/
  browse.py composing layer: the faceted browse tree, generated as a client of the REST API
  lib/      shared horizontal libs (full map: accommodanda/README.md "Shared library (lib/)") — lagrum (citation engine), catalog, page (the shared page kit) + render (site assembly) + tpl and the Jinja templates/ their markup lives in, layout, net, markdown, util, errors, casenaming, eucasenaming, labels, eu_structure, datasets, search, facets, feeds, dump, pins, resolve, text, compress, facsimile, pdftext, llm, annstore, wikitext, runlog, patch·patchit, markup, git, harvest, regeringen, poi, concepts, diff, history, assets, coe, coe_ids, pinpoint
  config.py runtime config (config.yml / data_root / catalog_root / wiki_root)
  sfs/      acts vertical — download·graphics·redaktionell·pdfmirror·extract·reader·model·tokenizer·assembler·nf·parallelappendix·register·versions·correspond·asgit·begrepp·_validate (+ __main__)
  dv/       court-decisions vertical — download·identity·namedcases·casenumbers·model·parse·structure·legacy
  forarbete/ preparatory-works vertical — download·propkb·soukb·riksdagen·rskr·model·parse·volumes·structure·kommentar·genomforande·aigenomforande·fk·jamforelse·lydelse·tabell·legacy_formats
  eurlex/   EU vertical (EUR-Lex/CELLAR) — download·bulk·annotate·casenames·correspond·definitions·parse·parse_html·parse_pdf·structure·lang·model
  hudoc/    ECHR case-law vertical — download·model·parse·summaries·translations
  coe/      Council of Europe Treaty Office vertical — download·model·parse
  icrc/     ICRC international humanitarian law treaty vertical — download·model·parse
  untc/     UN Treaty Collection (MTDSG status) vertical — download·model·parse
  icc/      International Criminal Court case-law vertical — download·model·parse
  icj/      International Court of Justice case-law vertical — download·model·parse·ocr
  foreskrift/ agency-regulations vertical — agencies·harvest·download·model·parse·structure
  avg/      JO/JK/ARN/IMY/KKV-decisions vertical — download·model·parse
  rs/       rättsliga-ställningstaganden vertical (7 myndigheter) — agencies·download·skv·model·parse
  guidance/ EU soft-law source, 12 issuing bodies (EDPB·EBA·EASA·ACER·ESMA·ENISA·BEREC·EDPS·EIOPA·EUIPO·ECB·ESRB) — issuers·<body>_download·eurlex_download·model·parse·render
  remisser/ remiss (referral-response) vertical — model·download·parse·ai_analyze
  site/     editorial-chrome vertical (frontpage/om/sitenews) — model·parse·render (markdown content repo, WIKI_ROOT)
  stats/    corpus-measurement vertical (/statistik) — model·scan·compute·charts·render (reads the finished corpus; nothing to download or parse)
  wiki/     kommentar + begrepp sources — parse·annotate·guidance_discover (markdown content repo, WIKI_ROOT)
  api/      HTTP API — app (REST/OpenAPI + static site + legacy feeds), pdf·pdfjob·pdfcollection (single-document and collection paper exports), mcp (MCP server), ops (health dashboard), auth·edit·editcontent·editcart (inline content editor), graphicsedit·graphics (`.graphics` crop-review editor), patch (source-fix editor), facsimiles (the shared source-PDF page/crop responder, its own module because `app` imports every router)
  build.py  orchestrator — the `lagen` build driver, composes the verticals
```

A vertical imports from `lib`; `lib` never imports a vertical; only `build`
(the orchestrator) imports across verticals. The artifact-level helpers a source
once owned but the derived layer also reads live in `lib` keyed on artifact
metadata, not source code: `lib.casenaming` (a court decision's canonical URI +
display title, read identically by dv's parse-time stamp, the catalog row and the
page heading), `lib.eucasenaming` (its EU mirror — a CJEU case's court case
number, curated usual name and inbound-citation label, keyed on CELEX, read
identically by eurlex's parse-time stamp, the catalog row and the page heading),
`lib.eu_structure` (the one EU-act sub-article anchor grammar shared
by the eurlex parser, the renderer and the wiki guidance layer),
`lib.datasets` (the named-resource snapshot loaders), and `lib.labels`
(every source's four reader-facing name forms — `short_id`/`short_title`/
`official_title`/`descriptive_label` — dispatched per source over the
artifact's own parse-time stamps plus the curated datasets, read
identically by `render.py` and by `catalog.py`'s stamped `descriptive`
column).

**The browse generator drives the REST API in-process.** The corpus-wide
*browse* pages are generated by driving `api.app` through a FastAPI `TestClient`
over the catalog (`browse.generate_all` → `generate_browse`), rather than
re-deriving the listings in the renderer: it *guarantees* the static browse pages
are byte-for-byte the same listing the REST endpoint serves, so the two can never
drift. This used to be a sanctioned layer inversion, because the generator lived
in `lib/render.py` and `lib/` may not import `api`. It now lives in
`accommodanda/browse.py` — the composing layer beside `build.py`, where importing
both the API and the render layer is the ordinary direction — so the checker's
allowlist is empty and the rule holds without exception.

---

## 2. Phase 0 — Regression safety net ✅

Before touching anything, make the old pipeline's output reproducible so
the new one can be checked against it. The old pipeline can no longer run
(it depends on `pkg_resources`, dropped by modern setuptools), so its
final output *is* the spec.

- ✅ **The golden corpus *is* `../ferenda.old/data/sfs/parsed/`** (the old
  checkout, not `site/data/`) — the old pipeline's
  parsed XHTML+RDFa output (11,056 SFS documents; the 174 zero-byte files are
  old-pipeline dummies for removed/expired docs). There is **no separate frozen
  golden tree and no `freeze` step**: `tools/golden_sfs.py normalize` transforms a
  parsed `.xhtml` to normal form **on the fly**, and the corpus `validate`
  (`accommodanda/sfs validate <parseddir> <downloaddir>`) normalizes each parsed
  doc per comparison. So the golden is always exactly what the old pipeline
  emitted — nothing to re-bake when the normalizer changes.
- ✅ `tools/golden_sfs.py` — comparator: `normalize` (old XHTML+RDFa → NF),
  `compare A B --sections metadata,structure,references,amendments` (A/B each
  `.xhtml` or `.json`), plus the adjudication overlay (§3d).
- ✅ Methodology fixed: the golden corpus is a **change-detector, not an
  oracle**. When new and old differ, the new pipeline is right maybe ~5%
  of the time — so differences are investigated, not blindly accepted.
  Mechanical old-pipeline corruption (e.g. the `-_och_-` citation-escape
  leak, the `__s._` page-number slug doubling) is canonicalized away in
  the comparator rather than reproduced.
- ✅ **Second, oracle-grade asset: the hand-authored fixture corpora in
  `test/files/`.** Unlike the golden corpus, these are
  `input → desired output` pairs someone wrote by hand, so they *are* an
  oracle. Reused so far:
  - `test/files/legalref/{SFS,EGLag}` → `test/test_lagrum.py` (citations).
  - `test/files/sfs/parse/` (109 `plaintext → element-tree` pairs across
    basic/lists/table/temporal/definition/regression/tricky) →
    `test/test_sfs_parse.py`: maps each expected tree to the same
    normal-form JSON `nf.to_normalform` emits and reuses
    `golden_sfs.diff_nodelists` for structure; a second test
    (`test_sfs_links`, added with the inline-links work, §3d) checks the
    inlined reference links against the fixtures' `<LinkSubject>` leaves.
    For the structure diff, inline
    citation/begrepp links fold back into node text (so a fixture's references
    need not be reproduced for its structure to match); ids dropped from
    the comparison (the old *test* driver minted them with a continuous-§
    rule, `K > 1`, that conflicts with the production rule, `K >= 1`, the
    golden corpus uses — id-minting is validated whole-document instead);
    temporal suppression off (these test the parser, not the
    consolidation policy). Three fixtures the old parser listed as broken
    now pass and were promoted to guards.
  - Still available, unused until their verticals exist:
    `test/files/myndfskr/` (19 `txt → n3` pairs, myndighetsföreskrifter),
    `test/files/citation/`, `uriformat/`, `legaluri/` (sub-component
    oracles), `test/files/pdfreader/`, `wordreader/` (extraction fixtures
    — relevant to the DV Word/PDF path).

---

## 3. SFS vertical (first vertical) 🚧

### 3a. Structural parser ✅

`accommodanda/sfs/` — heuristics ported from the old `sfs_parser`, structure
redesigned, as a pipeline of small modules: `extract` (body from rkrattsbaser
HTML) → `reader` (`TextReader`) → `tokenizer` (flat event stream) → `assembler`
(RANK-driven stack machine) → typed `model` dataclasses → `nf` (projection to
golden normal form, **replicating the old URI-minting quirks exactly**:
continuous-§ numbering, content-equality dedup, temporal suppression,
skipfragments). CLI: `python -m accommodanda.sfs parse|validate`.

- **Status:** the initial frozen-corpus run matched **98.7%**
  (10,912/11,056). The later complete 11,210-document run uses stricter current
  normalization/adjudication and is recorded under §3d; those dated percentages
  are different measurements, not a parser-status regression.

### 3b. Citation recognition (legalref → Lark) ✅

`accommodanda/lib/lagrum.py` — Lark (Earley) port of the old `legalref.py`
LAGRUM + EULAGSTIFTNING grammars, trigger-regex scanning with longest-prefix
retry. Ported formatter semantics: relative-ref completion from structural
context, sticky-chapter, external-law combined link, in-document law-name
learning, direct URI minting (no COIN); fragment letters K/P/O/S/N/M/L. The old
`-_och_-` preprocessing corruption is gone by construction; the `FILTER_LAW`
pre-filter is deliberately reproduced. Wired into `nf.py` as **inline links**
(§3d), with per-link sub-spans recovered from the parse tree.
`test/test_lagrum.py` ports the old `integrationLegalRef` oracle (only the old
engine's own failures fail).

- **Status:** 2018:585 = 219/222 tuples, 0 extra. The corpus-wide reference
  diffs are now characterized per-family and largely adjudicated or fixed —
  see §3d. ("Leak" is reserved for its literal sense: the old pipeline's
  `lastlaw`/`namedlaws` law-context surviving past a document boundary — not a
  synonym for "the two pipelines disagree.")

### 3c. SFSR register / amendments / förarbeten / metadata ✅

`accommodanda/sfs/register.py` — parses the SFSR register into one amendment
entry per change act (port of the old `extract_metadata_register`). Covers:
property mapping to the golden's polished form (departement→org URI, publisher
constants, dates, CELEX→`genomforDirektiv`); **Omfattning → `L*` change tuples**
(`ersatter`/`upphaver`/`inforsI`, resolved against the base law); the
**övergångsbestämmelse join** (OB blocks → `L{sfsnr}` ids + `content`, fed to the
reference scan); **per-amendment Förarbeten** (FORARBETEN grammar); and
**document-level metadata** — the *konsolidering envelope* (identifier "i lydelse
enligt SFS …", `konsolideringsunderlag`, dates, the `/konsolidering/<cutoff>` URI),
with the responsible department from the authoritative SFST header. Run-date
fields and the selectively-emitted `rdfs:label` are canonicalized away.

- **Status:** amendments **97.5%**, förarbeten **99%** per-entry, metadata
  **94.8%**. Residual is mostly stale-golden / post-freeze drift (adjudicated,
  §3d) plus a faithful-reproduction gap in the övergångsbestämmelse `L`-id
  collision rule.

### 3d. Remaining SFS work ⬜ / 🚧

- ✅ **Downloader** (`download.py`) — harvests the beta rkrattsbaser ES
  passthrough; one JSON `_source` per consolidated act (body + register +
  amendments) replaces the old two-page SFST+SFSR scrape. `search_after`
  enumeration (past ES's 10k window), incremental/`--full`, atomic writes.
  **13,789 acts.** New JSON lives flat at `downloaded/sfs/{y}/{n}.json`; legacy
  HTML in `downloaded/sfs/sfst|sfsr/`; superseded consolidations archived to
  `downloaded/sfs/archive/{y}/{n}/.versions/{vy}/{vn}.json` (keyed on the
  `andringInford` legal version, not checksum). A backfill interrupted mid-sweep
  (no watermark written yet) restarts from page 1 unless resumed: on
  interruption it prints the ES `search_after` cursor for the last completed
  page, which `--resume-after JSON` feeds back in to skip the pages already
  fetched. `test/test_sfs_download.py`.
- ✅ **JSON-or-HTML parse selection** — `load_inputs` prefers the new JSON over the
  legacy HTML; `register_from_source`/`sfst_header_from_source` map it onto the
  same intermediates, so all register/amendment/metadata parsing is reused
  untouched. 2018:585 from JSON vs HTML = **0 field diffs** (only genuine freshness
  deltas).
- 🚧 **Parallel-text convention appendices** — `sfs/parallelappendix.py`
  parses a statute whose sole `Bilaga` is a treaty printed as parallel text
  (the same convention in two or three languages, side by side) into a
  `Konventionsbilaga`, with **no per-law knowledge**. Article structure locates
  language-copy boundaries, `langdetect` labels each complete block, and
  structural rules read instruments/protocols, divisions, articles and
  paragraphs. Sequential glued headings, division subtitles, omitted parallel
  division headings and SFS `/…/` directives are handled generically; ordered
  article sequences remain strict. It is wired into `_assemble` — a statute
  that is not a parallel corpus, or whose printed languages disagree
  (`AppendixMisaligned`), flat-parses instead. It aligns **95/107 (89%)** of the
  structurally detected corpus candidates, including ECHR, Montreal, the ~40
  tax-exchange agreements, CRC and ATMF. The remaining five parallel fallbacks
  are deliberate: three sources duplicate article sequences and two are
  multi-treaty COTIF bundles outside this module's shape.
  `test/test_parallelappendix.py` locks the three-language projection, the
  restored title/preamble and the CoE-link rendering with frozen fixtures. Each
  instrument keeps its title and preamble as ingress and a protocol number;
  the projection anchors it `#B1`/`#B1P4` and resolves the treaty it reproduces
  through the curated `sfs/data/incorporates.json` (`{sfs}#{fragment}` →
  `source/number`), so ECHR articles link to their `ext/coe/NNN` targets. Nicer
  ragged-column rendering remains a downstream improvement. Details and the
  reproducible tally are in `accommodanda/sfs/parallelappendix.md`.
  The renderer already derives its column set from the appendix's own
  `languages` list (two or three) via the `--n-languages` custom property.
- ✅ **Omitted graphics — detected, localized and rendered.** The consolidated
  SFST source drops graphics, formulas, maps, symbols and some tables.
  Detection is deterministic: `sfs/graphics.py` turns the source's omission
  markers into typed `grafik` nodes during the `nf.py` projection. Two formulas,
  slash-delimited or plain, each in many wordings — `... är inte med här`,
  `Bilagan inte med här`, `Bilagor finns inte med här`, `Tabellen ej med här`,
  and the older acts' `Bilagan är här utesluten` / `Tabellen utelämnad`. The
  marker may stand alone, trail a heading, or be the bilaga's own rubrik with
  the whole appendix dropped under it
  (a projection-time overlay, like reference links — no model dataclass),
  preserving `sort` and the governing amendment (`satt_av`), and also
  recognizes the otherwise unmarked road-sign designator cells in 2007:90.
  `sfs/pdfmirror.py`, exposed as `lagen sfs mirror-pdf [<sfs> ...] [--full]`
  and run as part of `lagen sfs download`, mirrors the official PDFs under
  `downloaded/sfs/pdf/`. Which source holds an act follows from its SFS number,
  and both boundaries are exact act numbers, not dates: `2018:160`– resolves
  through svenskforfattningssamling.se document pages (the authentic online
  series, from the 1 April 2018 switch), `1998:306`–`2018:159` through derivable
  rkrattsdb URLs, and anything before `1998:306` exists only in print — a named
  act there is an error, a swept one is skipped without a request. Fetched
  bodies are PDF-signature checked. What keeps a rerun cheap is entirely local:
  an act already mirrored is skipped from disk, and one an upstream answered it
  has no PDF for is skipped from `.mirror.json`'s `absent` set — the record that
  exists because a missing file cannot itself say whether an act was never
  fetched or has nothing to fetch, which is what made every such act cost a
  request on every run. Each act is asked about at most once; the price is that
  a negative is permanent, and only `--full` revisits one. (An earlier design
  also harvested the publisher's `/regulations` listing into a watermarked index
  of what the online series carries. It was dropped: it saved no fetches — the
  doc page has to be fetched anyway for every act that *has* a PDF, and `absent`
  already covers the rest — and its remaining value, each act's publication
  date, was only wanted for an SFST reconciliation nobody had asked for.)
  Localization is the opt-in, nondeterministic vision half:
  `lagen sfs ai-includegraphics <basefile> [...]` (mirroring any source PDF it
  still needs, so mirror-pdf need not have run first) resolves each gap's
  *provenance* — the amending SFS that last set
  that wording, deterministically (register-first for bilaga gaps, so
  2004:629's two independently-amended map appendices resolve to different
  source PDFs; changenote-then-base otherwise) — then asks the vision model
  (`VISION_MODEL`, `lib/llm.py`'s `images=`/`vision_content` support) to
  locate each gap's page + bbox in that PDF (`collect_gaps`, `provenance_sfs`,
  `localize_group`). The validator bounds every page/bbox to the images shown
  and refuses a partial final result. Each artifact node has a stable semantic
  `key` (hash of structural path + kind/code + normalized anchor + occurrence
  within its container); the `.graphics` layer is keyed by it and stores the
  unhashed identity beside the crop. Content duplicates share a key, while a
  changed identity cannot inherit an old verified crop. A *pending* temporal
  variant is the exception: when the source prints an announced amendment as a
  second copy of a bilaga (`/Träder i kraft I:.../` beside the in-force copy's
  `/Upphör att gälla U:.../`), the pending copy gets its own keys and its own
  provenance — the text's markers beat the register's dates, which may already
  know the entry-into-force day while the text is still split. So 2004:629
  renders the in-force bilaga 1 maps from 2018:200 and the pending copy's from
  2023:395, with no per-document code. The NF keeps each temporal variant's
  `upphor`/`ikrafttrader` (ISO date or the verbatim "den dag som regeringen
  bestämmer"), and the renderer prints them as a `temporal-status` banner so a
  reader can tell the copies apart, as the official source does. The pass writes the
  resulting layer in the curated store
  (`lib/annstore.py`) — a peer of `.ann`/`.corr`, with per-entry `"verified":
  true` surviving a rerun so a reviewer can sign off graphics one at a time
  without losing prior sign-offs. The layer
  stores raw PDF points (top-left origin) and is hand-editable; generated,
  unverified candidates stay out of the public render until a human signs them
  off — by hand, or at `GET /internal-api/v1/graphics/review` (`api/graphicsedit.py` +
  `api/graphics.py`, the crop-review editor), which shows the crop beside the
  whole source page with the rectangle drawn on it and carts an approve/move/
  whole-page decision through the same cart and attributed-commit machinery as
  the inline content editor (`editcart.py`'s draft-kind dispatch). Its
  page/crop routes deliberately bypass `annstore.publishable` for a logged-in
  editor — an editor has to see an unreviewed crop to judge it — while the
  public `GET /api/v1/sfs-graphic` still 404s it.
  **Road-sign statutes take a second, fully deterministic route.** 2007:90 lists
  326 signs, each a designator cell with no marker and no per-row change note,
  and neither question a vision model could answer well at that scale. Both are
  read off the published PDFs instead (`roadsign_boxes`, `roadsign_index`,
  `localize_roadsigns`): their text layer names each row by the same designator,
  so the sign is the ink in the Märke column between that row's caption and the
  next row's, and the act that prints a row *last* is the one whose graphic is in
  force. The register cannot answer the provenance question — an amendment
  reprints only the rows it changes, not the whole paragraf, so 2017:923 sets
  2 kap. 5 § but prints only A30–A41 — which is why the PDFs decide. The
  resulting layer is `status: "derived"`: mechanical, so it reaches the render
  without per-entry sign-off, and regenerated as freely as a generated one. A row
  the PDF draws nothing for (Y2 is a *sound* signal) keeps its placeholder. `lib/facsimile.py`
  crops the bbox (`facsimile.cached` with a `bbox`); `GET
  /api/v1/sfs-graphic?uri=&node=` serves the crop lazily, resolving the
  provenance-correct PDF from the `.graphics` layer; the renderer's `grafik`
  node emits a `<figure>`/`<img>` crop with source-SFS attribution when the
  layer has placed it, else an honest placeholder. Every crop is also a
  lightbox opener (`assets/grafik.js`): these print small -- a road sign is
  3.5rem in its table cell -- which is exactly where a sign's symbol or a
  formula's subscripts stop being legible, so clicking one opens it as large as
  the viewport allows. The two sizes are two *renders*, not one stretched: a
  crop is rasterized at twice the page DPI inline and four times it for the
  lightbox (`facsimile.CROP_DPI`/`CROP_DPI_LARGE`, the endpoint's `stor=1`),
  since 2007:90 puts 325 thumbnails on one page (of its 326 signs, all but Y2,
  a sound signal the PDF draws nothing for) but opens them one at a time. `golden_sfs.py`'s
  `grafik-node-replaces-marker` adjudication family accepts the new grafik
  nodes as new-is-right against the old pipeline's dropped-graphics golden.
  `test/test_sfs_graphics.py`, `test/test_sfs_pdfmirror.py`.
- ✅ **Editorial notes typed, not read as statute text** (`sfs/redaktionell.py`)
  — a projection-time overlay in the same family as `graphics.py`'s gaps: the
  consolidated SFST text database stores a publisher's editorial note as
  ordinary prose, one `<p>` like any other, so nothing downstream could tell it
  from the law's own wording (it showed up as a measurement bug — every one of
  the corpus statistics' "shortest laws" was an editorial note, not a short
  law). Two sorts, kept apart because they say opposite things about the
  corpus: `endast-tryckt` (`/Författningens text finns bara i tryckt
  version/` — *we* are missing the text, a couple of dozen acts) and `upphavd`
  (`Har upphävts genom lag (1982:1101)` — *nothing* is missing, the act is
  repealed and this notice is all the publisher still carries; ~300 stycken, a
  handful of them a base act's whole body and the rest single repealed
  paragrafer inside a live act). `nf.py`'s `retype_editorial` retypes the
  already-projected stycke node as a `redaktionell` node — id, inline runs and
  beteckning carried over untouched, only `type` and the note's own
  `sort`/`satt_av` change, so an existing anchor still resolves and a repeal
  notice keeps its link to the repealing SFS. The renderer gives it the same
  subdued treatment as a grafik placeholder (`p.redaktionell`).
  `test/test_sfs_redaktionell.py`.
- ✅ **A förordning's authority, read from its ingress** (`sfs/bemyndigande.py`,
  2026-08-03) — nothing in an SFS artifact says a förordning is subordinate to
  the lag it serves (`rdf:type` is `KonsolideradGrundforfattning` for both, and
  the register carries no instrument type), so the relation is read from the
  two fixed forms Swedish drafting states it in. The **bemyndigandeupplysning**
  ("Denna förordning är meddelad med stöd av 1. 1 kap. 8 § cybersäkerhetslagen
  (2025:1506) i fråga om 4 §, …") is recognised structurally, not by parsing its
  prose: a punkt's references *out* of the document are the delegation, its
  references *into* the document what it authorises, so the chain is walkable
  provision-to-provision; a punkt naming only regeringsformen (the government's
  own 8 kap. 7 § RF norm-giving power) yields no edge. The **kompletterar
  ingress** ("… innehåller kompletterande bestämmelser till
  säkerhetsskyddslagen (2018:585)") is document-level only. Of 8,179
  förordningar, 654 carry the first and ~50 the second — neither is universal,
  so neither decides *whether* a document is a förordning
  (`labels.sfs_is_statute` does that, from the title, with full coverage).
  `extract` runs at `nf.py` projection time, a peer of `graphics`/`redaktionell`;
  `catalog._sfs_authority_links` publishes both as typed edges
  (`rpubl:bemyndigande`, `rinfoex:kompletterar`) feeding the norm-hierarchy
  table (§6). `test/test_norm_chain.py`.
- ✅ **Version history / time travel / diff** (`sfs/versions.py`, `lib/diff.py`,
  the `versions` Stage) — the old archive machinery's user-facing features,
  rebuilt over artifacts. The `versions` stage parses every archived
  consolidation (the ~31k legacy HTML snapshots in `downloaded/sfs/archive/…/
  .versions/` — both rättsdatabaser generations — plus the new downloader's
  JSON) through the same extract→assemble→NF chain into
  `artifact/sfs/archive/…/.versions/{vy}/{vn}.json` + a per-statute
  `artifact/sfs/{y}/{n}.versions.json` sidecar. Version ids are consolidation
  cutoffs ("t.o.m. SFS 2003:466"); legacy counter-keyed archives ("11.html")
  get their real cutoff recovered from the header, duplicates skipped, junk
  recorded in the sidecar rather than retried forever. `generate` renders each
  lydelse at the old `/{sfsnr}/konsolidering/{version}` grammar (no layout
  changes needed — the uri rules already round-trip it) with a way-back
  banner and an "Inaktuell författning" watermark; the statute page gets a
  "Jämför lydelser" panel (dates + propositions joined from the register) and
  the old bottom-of-page **andringar view** (one registerpost per change act:
  tryckt/officiell publication links, "Konsoliderad version … t.o.m. SFS X"
  point-in-time link, a per-amendment diff link against the previous available
  lydelse, övergångsbestämmelser, förarbeten/omfattning/CELEX/ikraft). Diff is
  *computed on demand* by `GET /api/v1/document/diff` (block-align +
  word-level `<ins>`/`<del>` over the artifact structure — no vendored
  htmldiff; direction normalized oldest→newest, note composed server-side) and
  swapped in by `versions.js` (`?diff=<version>`, deep-linkable);
  `/api/v1/document/versions` lists the history as data.
  `test/test_sfs_versions.py`, `test/test_diff.py`.
- ✅ **`history-as-git`** (`sfs/asgit.py`, `lagen sfs history-as-git <repodir>
  [basefile...]`) — the whole corpus as a git repository, one file per statute,
  one commit per amendment event (grouped by proposition when several statutes
  share one prop). Author = the proposition's first signer (co-signers as
  `Co-authored-by:` trailers), committer = the riksdagsskrivelse's first signer
  (both read off the parsed förarbete artifacts via a `forarbete_meta` callable
  `build.py` composes in, like `ai-correspond`); commit body is the prop's own
  "huvudsakliga innehåll" ingress. Granularity is bounded by the download
  archive (a commit spans the delta between two *available* consolidations);
  dates fall back utfärdande→ikraftträdande→July 1 of the amendment year.
  Emitted as one `git fast-import` stream (minutes, not days) via a staging
  ref that atomically replaces `main` only on success. Idempotent via per-file
  `Lagen-Transition:` trailers (immutable transition id + plaintext and
  metadata hashes): a re-run appends only a strict extension of that ledger,
  while corrections, backfills, changed attribution, late proposition members
  and scope changes raise `RebuildRequired` — answered with
  `--rebuild-history`, which recreates `main` from the complete corpus (also
  the migration path for legacy `Lagen-Event:`-only repos). A full export
  requires every selected artifact and snapshot to be valid and a clean
  non-bare target with `main` checked out. Implements
  `docs/prd-sfs-history-as-git.md`. `test/test_sfs_asgit.py`
  (golden fast-import stream + git round-trip + real two-run export tests).
- 🚧 **Adjudication overlay** (`golden_sfs.adjudicate`, `test/test_golden_adjudicate.py`)
  — the "change-detector, not oracle" posture (§2) as code: a `PREDICATES` table where
  each rule forgives a whole *family* of diffs in which the new pipeline is right against
  a stale/defective golden, while still *reporting* them (a forgiven class that grows stays
  visible). `validate` reports `match + adjudicated = passing`, so **`diff` is the
  genuine-regression count** to drive to zero. Every diff line carries the source-node
  **`«clause»`** (both sides), the context several predicates key on. The forgiveness
  families: stale-golden drift (`post-freeze-amendment`, `stale-consolidation-drift`,
  `change-reference-staleness`), old-pipeline corruption the new mints correctly
  (`celex-correction` — scrambled sector-3 CELEX; `balk-basefile-correction` — the 1734
  års lag balkar; `golden-chapter-collapse` — the old TOC-collapse), and old-grammar gaps
  (`eller-enumeration`). All mirror-paired where applicable: an unpaired add/drop stays
  visible. Some new-pipeline bugs are *fixed in the parser, not adjudicated* (bare-kapitel
  source misattribution, unanchored self-links).
  - ✅ **Parser correctness fix: list-embedded-mid-clause continuation.** A sentence with a
    numbered list embedded mid-clause ("Den som … vållar [1. 2. 3.] döms för …", BrB 13:6 /
    13:5c): the text after the list is the sentence *continuing*, but the new pipeline
    opened it as its own stycke — shifting every later `S#`. A stycke cannot start
    mid-sentence; the old pipeline got this wrong two *different* ways (13:6 folded it into
    the last list item; 13:5c made it a new stycke). Fix (`assembler.py`): a **lowercase**
    block immediately after an **open list** folds back into the stycke that owns the list,
    so the following genuine (capitalised) stycke keeps its ordinal. Scoped to an open list
    so a definition paragraph's lowercase definienda ("konsument: …" under "I denna lag
    avses med", no list) stay their own stycken. Oracle fixtures:
    `regression-stycke-fortsattning-efter-lista` (new) + `regression-kort-stycke-med-
    nummerlista` (corrected — it had mis-authored a lowercase "samt" as a separate stycke).
  - ✅ **The fix is new-is-right, so it *raises* the raw diff count** (the golden was
    inconsistently wrong, so a consistently-correct parser diverges from it) — reconciled by
    two adjudication predicates, not by weakening the parser. Manual audit of 1962:700: every
    new reference "extra" is a mirror-paired `S#`-shift against the golden's stale ordinal
    (0 unpaired, no real misattribution), and the fix surfaces ~25 brottsrubricering begrepp
    the old pipeline missed because the crime name sat in a list continuation (Häleri,
    Kapning, Rån, Människohandel, …) — a genuine gain.
    - `stycke-pinpoint-drift` — a reference whose target is identical on both sides but read
      from a different stycke of the *same paragraf*; forgiven only as a mirror pair (keyed
      on `paragraf_of`, so bilaga offsets and bare-chapter relabels are out of scope, and an
      unpaired add/drop stays visible).
    - `brottsrubricering-begrepp` — a `begrepp: extra` whose defining clause is an
      offence definition ("… döms för X till böter/fängelse"); the begrepp diff now carries
      its «context» so the predicate can see the clause. Scoped to the offence pattern, so
      an ordinary added term or extractor noise is not blanket-forgiven.
    Separately, large bilaga `S#` offsets (e.g. 2001:911) are a *different* cause —
    post-freeze temporal variants, i.e. structure-staleness, below.
  - ✅ **Structure-staleness predicate implemented conservatively.** The
    adjudicator receives the candidate normal form and forgives an added or
    changed structure node only when (1) the amendments comparison independently
    contains an act newer than the golden horizon and (2) that exact candidate
    subtree carries a formal `Lag/Förordning (YYYY:N)` amendment note newer than
    the horizon. A missing node is forgiven only when a newer amendment's
    `rpubl:upphaver` names that exact fragment; other missing nodes and every
    order change remain unexplained. Fixture-locked in
    `test/test_golden_adjudicate.py`.
  - ✅ **The remaining SFS golden gap is measured and bounded (2026-07-12).**
    A structure-only run over 11,210 frozen documents produced **10,479 exact,
    710 diff, zero errors and 21 skipped old dummies (93.5% exact)**. The stricter
    structure+amendments run produced 10,010 exact + 31 wholly adjudicated =
    **10,041 passing (89.6%)**, 1,148 diff, zero errors and 21 skipped. It
    accepted 313 individual post-freeze structure changes and 142 added
    amendments. Of the 1,148 residual documents, 695 have a structure diff,
    453 only an amendment-register diff and 102 both. Structure is highly
    concentrated: 170 bilaga-dominated documents account for 43,214 of 56,911
    residual structure problems (75.9%); 399 of the 695 structure cases have at
    most ten problems. The dominant outliers are obsolete embedded
    treaty/tariff/rail annexes (`1959:467`, `1972:698`, `1987:1185`), large
    historical consolidations (`1981:774`) and known old TOC collapse/current
    parser edge cases (`2023:200`). They are an explicit special-law improvement
    backlog, not silently accepted parity.
- ✅ **begrepp / `find_definitions`** (`begrepp.py`) — term-definition heuristics
  (a paragraf *mode* — `normal`/`brottsrubricering`/`parantes`/`loptext` — + the five
  `defined_term` cases) → `dcterms:subject` `/begrepp/{Capitalised}` inline links
  (`Ref kind="term"`), threaded through the projection. Compared as a term-URI set
  (the source stycke drifts like any reference); **~97% recall** on definition-heavy
  laws. `test/test_sfs_begrepp.py`.
- ✅ **Named-law data** — `sfs.ttl` → hand-editable `namedlaws.json` (203 labels /
  120 abbrevs across 309 id-keyed entries; `load_namedlaws`/`load_abbreviations`/
  `register.abbreviations` read JSON, no rdflib). Complete for SFS's bare-citation
  class — all 12 balkar + the grundlagar are present (that is where "brottsbalken →
  1962:700" comes from). Within SFS the *full* citation form is the convention
  (resolved by SFS number or in-document learning), so the colloquial long tail
  (`avtalslagen`, …) is DV/förarbete work, not §3. `riksdagsordningen` de-staled to
  the current `2014:801`.
- ✅ **Named-law data is dated (2026-08-06)** — a name outlives the act holding it:
  the dataset mapped each name to *one* SFS id, always the current act, so a 2010
  decision citing "11 kap. 1 § socialtjänstlagen" resolved to 2025:400, a statute
  that didn't exist yet — found as a context rail on 11 kap. 1 § listing 5 rättsfall
  and 100+ myndighetsbeslut all older than the law. `namedlaws.json` stays keyed by
  SFS id, but a name may now span several ids, each with an optional `from`/`until`
  (ISO dates, `until` exclusive); 245 → 309 entries, 49 names dated, 64 predecessor
  rows added. `load_namedlaws`/`load_abbreviations` return a `NamedLaws`
  (`lib/lagrum.py`), a dict subclass that still *is* the flat name→SFS-id map of the
  current act — existing call sites and the grammar's NAMED_LAW terminal are
  unchanged — plus `.at(name, when)` for the act that carried the name on a given
  date. `LagrumParser(..., written=)`/`sfs_parser(..., written=)` set the document's
  own date; `reset(written=)` sets it per document for a cached parser. A law the
  document itself names ("lagen (2001:453) om …") still outranks the dated table.
  `tools/namedlaws_history.py` derives the `from`/`until` spans from the corpus
  itself, walking `rinfoex:upphavdAv` both ways from each named act with
  `rpubl:upphavandedatum` as the boundaries — through the införandelag where
  there is one, since that is what `upphavdAv` names for a major statute and
  following it literally stopped the chain at exactly the most-cited
  successions (skollagen, aktiebolagslagen, folkbokföringslagen,
  försäkringsrörelselagen); forward, a successor takes the name over only
  where it carries it (polisdatalagen 1998:622 → 2010:361), and where the
  replacement renamed the concept there is nothing to move because no act
  holds the old name today (firmalagen, skuldsaneringslagen) — a predecessor inherits the name only
  if its own title yields it (the chain alone is wrong: begravningslagen 1990:1144
  replaced "Lag (1963:537) om gravrätt m.m.", never cited as begravningslagen),
  which over the 245 curated entries takes 93 named laws with a repealed
  predecessor down to the 53 whose predecessor carried the same name. Re-runnable and
  idempotent; `--write` edits the dataset in place, default prints the diff. dv, avg,
  rs and foreskrift now pass `written=` off the document's own date (`lib/util.py`'s
  new `approximate_date` fills a partial one — a bare year, year-month or riksmöte —
  to the middle of the span it can mean); förarbete's `written_date` falls back to
  the basefile since 57% of that corpus (every kommittédirektiv) records no date.
  wiki is deliberately left undated — editorial commentary is written now, so
  today's law is the correct reading. **The corpus has not been reparsed for this**:
  the corrected links only reach the context rail once dv, avg, rs, foreskrift and
  forarbete are reparsed.
  `sfs/versions.py` needs no date: statute text always cites another act by
  SFS number except for the balkar and the grundlagar, and those are never
  repealed and re-enacted under the same name — verified against the dataset,
  where none of the 58 names that ever moved between acts is either (the one
  balk that was replaced, giftermålsbalken, became äktenskapsbalken).
- ✅ **Inline links / runs-spans** — every NF text node is a list of `str` runs +
  `{predicate,uri,text}` link objects at exact positions (per-link sub-spans recovered
  from the parse tree, with trailing-marker absorption reproducing the fixtures'
  boundaries); the flat top-level `references` is dropped. All node kinds are scanned,
  including headings/upphävd (a deliberate divergence — a heading self-links its own
  "12 kap."→#K12). `inline_references` reconstructs the old `(source,predicate,uri)`
  tuples for the oracle (`test_sfs_links`); 2018:585 = **219/222, 0 extra**.
- 💤 **Bold/italic runs — N/A for SFS** (investigated): no emphasis markup in the JSON
  source or any of the 11,056 golden XHTMLs. A formatting-bearing-source concern (the
  DV/POI `bold` flag, §4), already supported by `Ref.kind` where it occurs.
- ✅ **Context rail — Ändringar restored (2026-07-30).** The legacy pipeline's
  per-provision "Ändringar" accordion is back: each paragraf's rail lists the
  SFSR register posts whose Omfattning names it — "Ändrad: SFS 2011:864 (Prop.
  2010/11:158)" — link into the bottom-of-page register and, where hosted, the
  proposition (`sfs/render.py`'s `amendment_index` over the artifact's register,
  `Rail._andringar`). Unlike the legacy accordion it does not silently drop an
  amendment with no registered proposition. `test/test_site.py`
  (`test_paragraf_rail_shows_amendment_history`).

---

## 4. DV vertical (second vertical) ✅

Court decisions (vägledande avgöranden). Forces the two highest-value
horizontal pieces: KORTLAGRUM citations and the cross-source link graph.

- ✅ **Downloader** `accommodanda/dv/download.py` — harvests the new courts'
  publication service at `rattspraxis.etjanst.domstol.se` (open JSON API
  behind an Angular SPA): `POST /api/v1/sok` paginates the whole corpus,
  `GET /api/v1/bilagor/{id}` for PDFs. Records stored verbatim as
  `site/data/downloaded/dom/{domstolKod}/{uuid}.json` + attachments.
  Incremental (newest-first, through the shared `lib/harvest.walk`/
  `HarvestWatermark` loop — stops on a run of consecutive already-downloaded
  pages or one conclusively past the 365-day safety window) and `--full`
  (oldest-first) modes; idempotent, atomic writes, politeness delay. A crashed
  or `--limit`-truncated run leaves the watermark dirty, so the next run
  re-walks the backlog instead of trusting it; a periodic cron'd `--full`
  sweep is the backstop for record edits/late publication past the window.
- ✅ **Full harvest done:** 17,254 records across 22 courts (1981–today),
  656/657 PDFs (1 upstream glitch — registered attachment never
  uploaded). Mostly HTML `innehall`, not PDF — good for parsing. Keep
  current via cron'd plain (incremental) run.
  - Gotcha: the API's `sok` free-text `sokordLista` does **not** match on
    referatnummer (a known-present "AD 1993 nr 2" returns `total: 0`).
    Authoritative "does the API have X?" checks must go against the
    harvested corpus, not that endpoint.
- ✅ **Identity indexer** `accommodanda/dv/identity.py` — entity resolution
  (union-find over shared keys) reconciling the two raw stores into one
  canonical identity per real case, so the parser can merge instead of
  emitting duplicates. **Manufactures** the identity agreement the old
  `CompositeRepository` merely *assumed* (the sources cannot natively
  agree: UUIDs vs filename-derived ids, REG vs REGR). Keeps all source
  records per case (for field-level merge), unlike `CompositeRepository`'s
  winner-takes-all parse.
  - Court mapping: REG→REGR, MIG→MIOD, MÖD→MOD, MMD→MMOD, PMD→PMOD.
  - Keys: ("M", canonical_court, norm_malnr) and ("R", norm_referat). API
    records carry explicit mål/referatnummer; legacy identity comes from
    the filename — målnummer for almost every court, but ADO encodes the
    referat (`1993-100` → "AD 1993 nr 100") and HDO notisfall
    (`2003_not_1` → "NJA 2003 not 1") get reconstructed referat keys.
  - Error modes both reported: under-linking → duplicate (audited,
    negligible); over-linking → component spanning >1 court (zero found).
  - **Result on the real corpus: 18,728 canonical cases — 14,838 linked
    across both sources, 2,252 API-only (post-feed + 6 new courts), 1,638
    legacy-only** (825 NJA notisfall the API doesn't carry, 514 older AD
    referat, 231 HSV, …). Index at `site/data/artifact/dom/identity-index.json`.
  - `test/test_dv_identity.py` (linkage, reconstruction,
    court-scoping/no-over-link, attachment grouping).

#### Coverage: legacy feed vs new API ✅ (analysis)

The 1,638 legacy-only cases are **not a temporal cutoff** — for every
affected court the missing cases fall *inside* the API's year range. The
gaps are categorical, three themes covering 1,572 of them:

- **HD notisfall — 825 (HDO), confirmed.** "NJA YYYY not N" brief notices;
  the API publishes full NJA referat but carries zero notisfall.
- **Arbetsdomstolen referat 2006–2017 — 514 (ADO), confirmed.** The API
  covers those years with *other* AD referat yet is missing ~30–65 more
  per year that the old feed has (verified absent in the harvested
  corpus). The new API's AD coverage for that decade is partial.
- **Non-referat Svea hovrätt judgments — 231 (HSV).** Målnummer-only
  (0% referat), heavy on `ÖH` hyresmål. ~10–20 may be linkage artifacts
  from malformed legacy filenames (`B3689`, `T8372-08t`) — a cleanup pass
  on the legacy filename parser would confirm.

Tail (~66) scattered across MOD/REGR/HFD — individual non-referat
decisions. **Implication:** for these ~1,600 verdicts the legacy Word/OOXML
is the *only* source (no API record to fall back on), including the entire
HD notisfall series and a decade of AD referat — so the legacy-OOXML path
below is not optional polish, it's the only way they enter the corpus.
- ✅ **DV parser core** — `accommodanda/dv/model.py`, `parse.py` and
  `structure.py` emit metadata plus a content-bearing instance/ruling tree
  (instances, betänkande vs dom, domskäl/domslut, skiljaktig), with a flattening
  view for linear consumers. **API path:** body
  from `innehall` HTML (each `<p>` classified heading-vs-paragraph;
  numbered prejudikat paragraphs carry an ordinal; `<br>`/entities/`&nbsp;`
  handled, separators dropped), metadata from the curated fields,
  projected to a JSON artifact. Driven by the identity index (consumes
  the `domstol` member per case). **17,090 API-backed cases parse, 0
  failures**; the 966 empty bodies are exactly the records with no
  `innehall` (995 summary-only) — zero content dropped. `test/test_dv_parse.py`.
  Source/coverage increments:
  - ✅ **Legacy Word path (POI)** — `accommodanda/lib/poi.py` (moved from
    `dv/word.py`, 2026-07-17, once förarbete became its second caller —
    rule:second-use-goes-to-lib) reads the *original* binary `.doc` (POI
    **HWPF**) and `.docx` (POI **XWPF**) via jpype, **not** the antiword
    DocBook intermediate — a real DOM (paragraphs, table cells, bold runs)
    recovering the label/value structure antiword flattened. POI 5.4.1 jars
    vendored in `vendor/poi/`; OpenJDK 21 + `jpype1` deps; log4j-api pointed at
    SimpleLogger so its "no provider" notice stays off stdout. `accommodanda/
    dv/legacy.py` (`from ..lib import poi as word`) splits the flat
    `(text, bold)` stream into header / bold-label metadata / `REFERAT` body /
    `Sökord`/`Litteratur` footer → `Avgorande`, preferring the identity
    index's canonical referat/court. The whole referat is one Word table, so
    the body discriminator is the `REFERAT` marker, not table membership.
    **15,624 legacy docs parse, 0 empty bodies, 0 failures.**
    `test/test_dv_legacy.py` (14 JVM-free unit tests over synthetic streams).
    Förarbete's own proptrips-era `.doc` bodies are mostly Word 6/95 binaries
    POI's HWPF refuses, so that vertical routes `.doc` through `antiword`
    instead and reserves `lib/poi.py` for `.docx` (§7g).
  - ✅ **Field-level merge — investigated and rejected.** Measured the gaps
    a merge could fill for the 14,838 cases with both sources: body-fallback
    opportunity is **0** (all 965 API-empty bodies are summary-only nämnd
    records with no legacy original); the only fields legacy carries beyond
    identity are `Lagrum`/`Sökord`, filling API gaps on just ~10%/~7% of
    linked cases; `rättsområde`/`förarbeten`/`litteratur` are genuinely
    empty API-wide (not a parser bug) and absent from legacy too. So the
    architecture is **single-best-source per canonical case** (API when
    present, POI-legacy otherwise), not a merge.
  - ✅ **Notisfall coverage — closed as part of the frozen-referat coverage
    closure (2026-07-19, below).** `lagen dv import-legacy` imported the
    5,935 frozen notis bodies from the old pipeline's intermediate XML (the
    legacy feed itself shipped notiser as zero-byte Word files), and
    `dv/legacy.py` gained a notis parse route (TRIPS `<para>` / OOXML
    `<w:p>` flavors) rather than the `.docx`-splitting approach this bullet
    originally scoped. The importer itself was deleted once run (§7g
    teardown, 2026-07-19); the notis parse route remains.
  - ✅ **Citation extraction from body text** — KORTLAGRUM ported
    (`AbbrevLawNormalRef` "3 § MBL"/"MBL 3 §", `AbbrevLawShortRef`
    "JB 22:2"), law-abbrev terminal built from the 110 `dcterms:alternative`
    entries in `sfs.ttl` (longest-first so "MBL" ≠ "MB"+"L"). Both forms
    require structure, so a bare abbreviation in prose never links. Wired
    into `dv_parse` (`extract_references`): each body block scanned with
    `LagrumParser(parse_types=[LAGRUM, KORTLAGRUM, EULAGSTIFTNING])`,
    populating the artifact's `references`. `Short` fixtures promoted into
    `test/test_lagrum.py`. Corpus check
    (`dv_parse --references`): on a 500-case sample, 4,487 refs found,
    **81.2% `lagrumLista` recall** (the shortfall is editor-derived lagrum
    not cited verbatim, not scanner misses — a signal, per the oracle's
    change-detector posture).
  - ✅ **Curated legal relations projected as typed graph edges**
    (2026-07-18, `docs/rewrite-parity/03`). The API's curated
    lagrumLista/forarbeteLista/hanvisadePubliceringarLista/litteraturLista
    (and the legacy footer's Lagrum/Rättsfall/Litteratur) are normalized at
    parse time through the same citation grammar the body uses, into
    inline-run lists stored beside the raw strings
    (`metadata.{lagrum,forarbeten,related,litteratur}`), predicates preserved
    (`rpubl:lagrum`/`rpubl:forarbete`/`rpubl:rattsfallshanvisning`/
    `dcterms:relation`); unresolved strings survive as plain runs. Fallbacks
    where the grammar fails: lagrumLista's `sfsNummer` (law-level link) and a
    hanvisning's `gruppKorrelationsnummer` — an authoritative join to the
    cited case's publication group, resolved via the identity index
    (13,307/13,307 grupp-carrying entries resolve; ambiguous split groups
    dropped, not guessed). `europarattsligaAvgorandenLista` never holds
    citations (corpus-wide it takes exactly three topic-label values, on 98
    records) — kept as labels in `metadata.europarattslig` beside
    Rättsområde, minting no relation edge; `litteraturLista` — previously
    dropped entirely — is retained. `lib.catalog.curated_links` projects the
    runs into the links table (unanchored, so `inbound_collapsed` dedups
    against body pinpoints naturally); `dv/render.py`'s `render` shows the four groups.
    `golden_dv` now reports body/curated/union recall separately, resolving
    the 81%-vs-96% conflation: a 300-case oracle sample measures body 96.1%,
    curated 44.2%, union 96.4%, with the residual old-only refs absent from
    both prose and API metadata (old-pipeline context artifacts, not
    projection loss). Corpus re-parse + relate pending (the acceptance run).
  - Summary-only nämnd records (no `innehall`) get the sammanfattning as
    body downstream.
- ✅ **DV golden corpus (reference graph)** — `tools/golden_dv.py`. The old
  pipeline's distilled RDF (`../ferenda.old/data/dv/distilled/{COURT}/{id}.rdf`, 15,858
  files) is the frozen oracle: per case a document URI + its
  `dcterms:references` set. Cases match by URI (which now agree — the RDF shows
  `dom/rh/2009:37`, **independently confirming the case-URI re-minting**).
  Compares reference sets. The full 2026-07-12 run indexed 17,294 artifacts and
  matched 15,177 old RDF records by URI: **95.6% old-reference recall**
  (73,454/76,836), 65.8% exact + 15.2% superset. The
  residual misses are editor-derived lagrum not cited verbatim in the body
  (the same signal as the 81% lagrumLista recall) + the new scanner filling old
  all-or-nothing holes — change-detector posture, investigated not assumed.
  ✅ **The 2026-07-19 rerun (after the frozen-referat coverage closure, §7g)
  matches 21,594 of 21,595 old RDFs** — the single miss is a source header
  typo the old pipeline propagated (`AD 2004 nr 59` vs the published
  AD 2005 nr 59); identifier agreement is 16,624 exact + 4,970 new-superset
  with zero conflicts, referatrubrik 20,275 exact with zero new-missing
  (notis summaries now carry the oracle's published rubrik), avgörandedatum
  21,370 exact / 183 text-confirmed / 13 disjoint (all pre-existing
  old-feed-vs-API disagreements on API-backed cases). Whole-corpus
  old-reference recall over the doubled matched set is 89.2% — the newly
  covered legacy/notis population cites more sparsely through curated
  metadata than the API records, and the diff classes mirror the ones already
  adjudicated above. ✅ **Metadata
  comparison surface added:** the same corpus pass now
  reports exact/diff/old-missing/new-missing counts for identifier,
  referatrubrik/sammanfattning, avgörandedatum and målnummer, including one
  example per outcome family. Identifiers are compared through the actual
  citation/URI grammar, not display spelling: 10,207 are exact and all 4,970
  remaining cases are new supersets (normally canonical NJA page + editorial
  löpnummer), with **zero conflicting identifiers**. Date correctness no
  longer blindly trusts either metadata source: an unambiguous formal
  final-ruling sentence for the publishing court overrides API metadata only
  after calendar, future-date and referat/API-year checks. A corpus-wide dry run
  selected 218/17,325 API records and rejected the looser matcher's
  procedural-history false positives; all affected artifacts were reparsed.
  This fixes the API's `NJA 2018 s. 405` year typo (`2016-06-12` vs the text's
  12 June 2018), as well as cases where the old golden is stale/wrong. Eight
  referat contain several formally stated publishing-court decisions; their
  artifacts now preserve every date in `avgorandedatum_lista` and use the
  latest as the backward-compatible scalar date (`NJA 2001 s. 191`, for
  example, records both 20 March and 19 April). The refreshed 15,177-case date
  comparison has 14,955 exact, 182 `text-confirmed`, and 25 new supersets. The
  remaining 15 disjoint cases contain neither competing date in their published
  body, so choosing between old RDF and API metadata would be guessing. They
  remain explicitly unadjudicated: `NJA 1982 s. 124`, `NJA 1987 s. 175`,
  `RH 2000:65`, `RH 2003:9`, `RH 2005:11`, `RH 2007:94`, `RH 2010:159`,
  `RÅ 1994 ref. 104`, `HFD 2022 ref. 14`, `MIG 2009:23`, `MÖD 2004:6`,
  `MÖD 2016:2`, `PMÖD 2018:19`, `PMÖD 2018:37`, and `PMÖD 2019:30`.
  `test/test_golden_dv.py`.
- ✅ **DV structural golden (instance/ruling skeleton)** — `tools/golden_dv_structure.py`,
  a *second* DV oracle, complementing the reference-graph one above. The old
  pipeline's parsed XHTML+RDFa (`../ferenda.old/data/dv/parsed/{COURT}/{id}.xhtml`, which
  the distilled RDF does not capture) segmented each referat into its decision
  structure — instance stages (`div.instans`, `dcterms:creator` = court), the
  föredragande/revisionssekreterare **betänkande** as a sibling of the court's
  **dom** (so the proposal is separated from the ruling by construction), each
  with **domskäl**/**domslut**, plus **skiljaktig** (dissent), **tillagg**
  (concurrence) and **delmål** (split cases). `normalize()` reduces that to a
  coarse skeleton — the ordered tree of `(kind, court, ordinal)`, **no body
  text** (the old input is Word/OCR; text equality would be all noise — the
  contract is the segmentation). The diff reuses `golden_sfs.diff_nodelists`.
  - ✅ **Spec-first cut landed.** Normalizer + artifact-side reducer (the parser
    contract: a nested `structure` list of `{type, court?, ordinal?, children}`)
    + `compare`/`validate` CLI, all hermetically tested
    (`test/test_golden_dv_structure.py`). Verified on real referat (HFD 2011:26
    → 3 instances + dissent; NJA 2017 s. 55 → delmål I/II, HD's betänkande split
    from its dom). This **writes the target down**; it isn't a regression net yet.
  - ✅ **The parser work it specifies — done.** `dv/structure.py` ports the old
    `dv.py` FSM recognizers (`Instans`/`Betankande`/`Domslut`/`Skiljaktig`/…) into
    a RANK-driven stack machine; `nest()` now emits a **content-bearing**
    `structure` (the instance/ruling tree with the prose attached as leaves),
    which `to_artifact` ships in place of the flat body. The golden's
    `skeleton_from_artifact` drops the prose leaves, so `validate` compares the
    same skeleton it always did; the renderer flattens the tree back
    (`dv/structure.flatten`). Verified on real referat (AD 1993 nr 101 → an
    instans with dom/domskäl/domslut; `flatten` round-trips the body).
  - Posture: change-detector, not ground truth — the old FSM segmentation is
    heuristic, so diffs are investigated and the new parser may improve on it
    rather than assumed regressions. An oracle-grade hand-authored legacy-form
    fixture now covers delmål → tingsrätt → HD betänkande + dom + skiljaktig.
    Corpus sampling then exposed and regression-locked further concrete bugs:
    ordinary business “föredragning” no longer opens a judicial betänkande; an
    explicit HD föredragande proposal opens the HD instance rather than staying
    under the preceding hovrätt; appended `BILAGA` + lower-court judgments open
    a new instance; administrative Migrationsverket history is not a court dom;
    uppercase `DOMSKÄL`/`DOMSLUT` headings are recognized; and a disposition
    sentence immediately after its heading stays in the same domslut branch.
    `test/test_dv_parse.py`.
  - ✅ **Full-corpus structural result reviewed and bounded (2026-07-12).** Of
    15,177 URI-matched old/new pairs, 257 (1.7%) have the same exact wrapper
    tree. The secondary reduced sampling diagnostic matches 3,292 (21.7%); it
    stratifies review but does not accept or hide any exact-comparison failure.
    1,000 zero-byte old dummies are reported separately and 6,418 old paths have
    no artifact.
    Dominant differences remain the old XHTML adding an unnamed second instance
    (7,546) and the new parser recognizing explicit dom branches (5,481 first,
    3,560 second). Representative adjudication found old-golden defects as well
    as the new-parser bugs fixed above: for example old `AD 1997 nr 26` has a
    phantom empty second instance, old `AD 1993 nr 116` misses explicit domskäl,
    and old `NJA 2007 s. 382` loses an initial HD ruling when checking order.
    The legacy corpus is therefore **not safe as an automatic structural
    oracle**. The hand-authored fixtures are normative; corpus diffs remain a
    stratified sampling surface, and every sampled credible regression is now
    fixture-locked before repair.
  - ✅ **HD's modern (2023+) record format.** Newer API records carry real
    `<h1>`–`<h3>` headings and footnotes the legacy `<p>`-only path dropped or
    mis-segmented. `parse_body` now reads the heading tags (an `<h1>` court name
    drives the instans boundary directly, so the structure no longer depends on
    the appellant-action prose), lifts the end-of-document **footnote**
    definitions out of the block stream, and strips the inline `[N]` markers
    (undoing the OOXML `<sup>[N]</sup>N` doubled-digit artifact — which also
    repaired CJEU refs like `C-268/213` → `C-268/21`, so they mint the right
    CELEX and link to the internal copy). The renderer walks the instance/ruling
    tree (föredragande's betänkande shown muted, the court's own ruling titled)
    and prints the footnotes as back-linked endnotes. Locked by fixtures in
    `test/test_dv_parse.py`.
  - ✅ **EU acts cited by Swedish short name.** The citation engine
    (`lagrum.load_namedacts`, reading `eurlex/data/namedacts.json` the way it
    reads `namedlaws.json`) resolves "artikel 6 i dataskyddsförordningen" →
    `ext/celex/32016R0679#6`, with a leading determiner/adjective (den, EU:s,
    allmänna) absorbed by the grammar. Once an act is named, a definite generic
    "artikel N i förordningen" and a *bare* "artikel N" anaphora-pinpoint the same
    act. `celex_uri` mints CELEX for all four act-type letters it can appear
    behind (L directive, R förordning, H rekommendation, D beslut). Separately,
    a treaty/Charter/ECHR article rides on the *instrument's own* consolidated
    text, never mis-pinned onto whatever named secondary act is in focus:
    `lagrum.load_treaties` (always-on whenever EULAGSTIFTNING is active, not
    gated on caller-supplied acts) maps EU-treaty/Charter/ECHR names to the
    ext-relative path of their consolidated text — EU treaties/Charter from the
    sector-1 entries of `eurlex/data/namedacts.json` (`load_namedacts`
    deliberately skips those, so treaty names stay out of the opt-in named-act
    path), the ECHR from the new `coe/data/names.json` — and `TREATY_RULES`
    links `"artikel N i <treaty>"` (the "i" optional), coordinated lists and
    ranges, and the name-first `"<instrument>, särskilt artikel N"`
    construction. So "artikel 7 och 8.1 i EU:s rättighetsstadga" links each
    article to `ext/celex/12012P/TXT#7` / `#8.1`, and "artikel 6.1
    europakonventionen" links to `ext/coe/005#A6P1` — external EUR-Lex links
    for the EU treaties/Charter (no corpus page yet), the CoE article-fragment
    grammar for the ECHR. The named-act grammar extension itself is gated on
    the caller supplying acts (like KORTLAGRUM's LAW_ABBREV), so SFS/förarbete
    citation parsing — and the golden — are untouched; only the DV scanner
    opts in. `test/test_lagrum.py`.
  - ✅ **Recitals** (`skal_part`, 2026-08-02) — a recital is where an act states
    the reasoning its articles enact, and the guidance corpus cites it for
    exactly that: "i skäl 108 och artikel 46.1 i allmänna
    dataskyddsförordningen föreskrivs att …". `skäl 108 i <akt>`, the
    coordination Swedish writes by hanging one "i <akt>" off both halves, and
    the definite generic `skäl 108 i förordningen` all mint `#recital-N` — the
    anchor the eurlex renderer already gives each recital, so these join the
    citation graph beside the articles. English surface too (`recital N of …`).
    A recital that names no act does *not* anaphora-link, unlike a bare
    article: these documents number their own paragraphs the same way, and a
    bare number is likelier to be one of those. The act keeps its own link over
    its own words, which is also what keeps it the act in focus for the
    anaphora that follows. Measured on the 60 edpb artifacts: +409 links, 211
    of them recitals, none lost — and 71 links moved off a repealed directive
    onto the GDPR, the extra anchor points giving the anaphora chain more
    places to re-latch. `test/test_lagrum.py`.
  - ✅ **Canonical case naming + HD's given names** (`lib/casenaming.py`, with
    `case_uri`; moved out of `dv/` since the catalog + renderer read it too). One entry
    point, `case_label`, computes a case's display title so the renderer heading,
    its eyebrow and the catalog row label (which drives every listing and inbound
    citation) read identically. A case's *identity* is its **canonical referat** —
    the one whose minted URI matches the document's (NJA's page form "NJA 2025 s.
    897", never the löpnummer "NJA 2025:58"; the löpnummer is kept as metadata,
    out of every identity string); a raw verdict with no referat identifies by
    målnummer. On top, Högsta domstolen's *named precedents* (the harvested
    `namedcases` snapshot, `dv/data/namedcases.json`) lead with the nickname —
    "Meteoriten (NJA 2025 s. 897)", "Umgängesstödet (Ö 3043-25)" — keyed by URI or,
    for an un-paginated verdict, by målnummer. The label is **stamped onto the
    artifact at parse time** (`build.dv_parse_run`, the source owns its model) so
    the catalog stays a pure consumer. `test/test_dv_naming.py`.
  - ✅ **Identity collision regressions closed.** Identity indexing no longer
    merges two authoritative API decisions merely because a court reused the
    same målnummer (for example `AD 1993 nr 22` / `AD 1994 nr 13`), and NJA's
    shared editorial löpnummer no longer merges different page decisions
    (`NJA 2016 s. 341` / s. 346). Strong referat identity is resolved first;
    målnummer bridges one API and one legacy root only when unambiguous.
    Regression-locked in `test/test_dv_identity.py`.
  - ✅ **Pre-referat raw-verdict coverage (R2).** A HD/HFD decision is first
    published as a bare PDF verdict (no `innehall` HTML, no referat) and only
    gains its NJA/HFD referat months later — until then it was invisible to
    the parser. `dv/parse.parse_pdf_record` reads the PDF directly via
    `lib/pdftext`, recovering the domskäl paragraph numbers HD prints as
    unselectable left-margin bitmaps (counted, not OCR'd) and tagging every
    block with its source page for facsimile links. `dv/identity.py`'s R2
    merge folds that raw record into the referat component that later
    publishes the same målnummer, guarded to exactly one referat component per
    målnummer and a matching avgörandedatum. `dv/download.py` also now drops
    `PROVNINGSTILLSTAND`/`FORHANDSAVGORANDE` publications (leave-to-appeal
    notices and CJEU referral requests — neither a decision), purging any
    already-stored copy. Separately, `dv/legacy.py`'s `notis_summary` recovers
    a listing description from a notis's own first-paragraph summary line
    where the frozen oracle's `referatrubrik` carries none.

---

## 5. Horizontal libraries (extracted after DV) ✅

- ✅ **Configurable citation engine.** `accommodanda/lib/lagrum.py` remains one
  module: the planned `citations/` package split offered no capability or
  boundary improvement, so it is not a rewrite requirement. The useful part
  of the plan — parameterization by grammar set, context and pre-filter — is
  implemented while keeping the old `LegalRef(*parse_types)` configurability.
  - ✅ **Parse-type configurability built.** `LagrumParser(parse_types=…)`
    composes the grammar, `?ref` root alternatives and trigger regex from
    only the requested types (`ROOTS`/`RULES`/`TRIGGER_SRC` tables +
    `DEPENDS`). Roots come from the *requested* set, rules/triggers from
    the dependency-*expanded* set, so a dependency (KORTLAGRUM/ENKLALAGRUM
    → LAGRUM) lends productions without contributing its own roots. A new
    parse type = an entry in those tables plus its `fmt_*` formatter(s).
  - ✅ **All 8 old-engine grammars ported**, each validated against its
    `test/files/legalref/` oracle: LAGRUM, KORTLAGRUM, EULAGSTIFTNING (SFS
    + EU, earlier), plus RATTSFALL (`DV`, "NJA 1994 s. 12" → `dom/…`),
    FORARBETEN (`Regpubl`, prop/SOU/Ds/bet/celex + page lists + "a. prop."
    + avsnitt), EURATTSFALL (CJEU "mål C-176/09" → celex; hand-authored
    oracle since the `ECJ` fixtures are broken/encoding-mangled),
    MYNDIGHETSBESLUT (`Avg`, JO/JK/ARN by diarienummer, with the JK
    date-disambiguation), ENKLALAGRUM (`Simple`, the absolute-only LAGRUM
    subset). DV (`dv_parse`) now scans with all seven via `DV_PARSE_TYPES`.
  - 💤 **Never implemented in the old engine** (declared constants only, no
    ebnf branch): FORESKRIFTER, INTLLAGSTIFTNING, INTLRATTSFALL,
    DOMSTOLSAVGORANDEN — "porting" these means greenfield grammar design. One
    is now built: **FORESKRIFT** (§7e, 2026-08-03) recognises a
    myndighetsföreskrift by its författningssamling designation + number
    ("PMFS 2022:1"), the first of these written from scratch rather than
    ported. INTLLAGSTIFTNING, INTLRATTSFALL, DOMSTOLSAVGORANDEN remain
    deferred (user decision).
- ✅ **Identity / URI minting at the right seams.** There is deliberately no
  universal identity library: identity belongs to each source model. Pieces
  read by several consumers live in `lib.casenaming`, `lib.eucasenaming`,
  `lib.layout`, `lib.coe` and the citation formatter, so documents and
  citations mint the same published identifiers without a universal model.
  - ✅ **Display naming consolidated into `lib.labels`.** Every source had its
    own scattered rule for a document's four reader-facing name forms
    (eyebrow/h1/official-title/citing-form); `lib.labels.document_labels`
    dispatches per source (`sfs`/`eurlex`/`dv`/`forarbete`/`foreskrift`/
    `avg`/`hudoc`/`coe`/`icrc`/`untc`/`icc`, else a generic fallback) over the
    artifact's own parse-time stamps plus the curated datasets (named laws,
    CoE/ICRC/UNTC names, the new `eurlex/data/treaties.json`), imports no
    source code (rule:lib-never-imports-vertical), and is read identically by
    `render.py` (every per-document page) and `catalog.py` (the stamped
    `descriptive` column) — folded into the `relate`/`generate` recipe-version
    tuples so a labelling-rule edit re-stales both.
- ✅ **Artifact contract settled:** source-owned typed JSON, no universal
  envelope and no JSON-LD context (see §1). Shared consumers operate on the
  small artifact conventions they actually need; dumps preserve each raw
  artifact as one NDJSON record.
- ✅ **Incremental build driver (make-like freshness orchestration)** —
  `accommodanda/build.py`, the `lagen <source> <action> [basefile...]` CLI.
  Source-first verbs; sources register per-document `Stage`s, so the driver
  knows nothing source-specific — uniformity lives in the driver + a tiny
  protocol, not a base class. **Content-hash freshness** (manifest at
  `site/data/.build/manifest.sqlite` — per-entry rows, so a scoped run reads
  and writes single entries instead of a 133 MB JSON) keyed on input hash
  **+ recipe version**
  (a hash of the stage's own impl files, so editing the parser re-stales
  every doc without a blanket `--force`). A stage with no `inputs` and no
  `code` is judged fresh by default (nothing to version an existing output
  against — e.g. `download`, whose "input" is a remote service). `Stage.always`
  opts a stage *out* of that default for the opposite reason: its real inputs
  are the whole corpus, too large to hash, so the driver cannot answer "has
  anything changed" and must not pretend it can — every invocation runs,
  `--force` or not (`stats compute`, §7k). **Implicit deps** (a downstream
  action builds stale upstream first; `--no-deps` scopes). `--force`, `-j`
  (process pool), `-n`/`--dry-run`, `status`. `test/test_build.py`.
  - ✅ **`parse` stage wired for SFS + DV** — finally *persists* artifacts:
    `site/data/artifact/sfs/<y>/<n>.json` and `site/data/artifact/dom/<slug>.json`
    (DV driven by the identity index). This is Stage B (artifact corpus on
    disk) from §6.
  - ✅ **`download` wired for SFS + DV**, two modes split on whether a basefile
    is given (the old `download_single` vs `download_new`):
    - **Bare `lagen sfs download` / `lagen dv download` = the full bulk
      harvest** (`Source.harvest`), *not* a loop over `list_basefiles()` — that
      could only ever re-touch known ids, never *discover* new documents. SFS
      does a `search_after` sweep; DV paginates the courts' API. Incremental by
      default, `--force` = full re-walk. Self-logging per page, throttled.
    - **`lagen sfs download 2018:585` / `lagen dv download <case>` = per-doc**
      targeted (re)fetch (SFS by beteckning + archive superseded consolidation;
      DV by the uuid the index holds). inputs/code empty → an on-disk doc is
      "fresh" until `--force`. Politeness delay between fetches.
    Kept independent of `parse` (parse has the JSON-or-HTML fallback, so
    download is not a true build dependency — wiring it would force-migrate
    every legacy doc as a side effect of a bare `parse`). A DV harvest
    **auto-rebuilds the identity index** when records changed (`dv.identity.reindex`)
    so new cases are immediately parse-visible — one whole-corpus pass at the
    end (the index is a global union-find, not incrementally updatable; needs
    no parsing, keys come from raw fields + legacy filenames). Index lives at
    `site/data/artifact/dom/identity-index.json`.
  - ✅ **Driver progress logging** — `run_action` prints a throttled
    single-line `\r` counter to stderr (`parse 5400/11228  ran … err …`) every
    50 docs; the per-document loop was otherwise silent until the final report.
  - ✅ `relate` + `generate` landed as **corpus-level verbs** (not per-doc
    Stages — see §6): the catalog rebuild and the static-site render. The
    earlier "per-doc upsert" plan was revised once it was clear generate's
    prerequisite set is data-dependent (the inbound set), not a static
    per-basefile input list.
- ✅ **Golden comparison seam is shared at the useful level.** Normalization is
  source-specific; the common ordered-node differ (`golden_sfs.diff_nodelists`)
  is reused by the DV structural golden. A universal comparator would only
  hide the different oracle contracts and is not a rewrite requirement.
- ✅ **Shared harvest core extracted** (`accommodanda/lib/harvest.py`, 2026-07-06).
  The incremental-harvest loop independently reimplemented in four verticals
  (dv, forarbete, `forarbete/riksdagen.py`, `foreskrift/harvest.py`, avg/jo) —
  newest-first page walk, stop-at-first-on-disk, `--full`/backfill mode,
  atomic writes, politeness delay, `Reporter` progress — is now one shared
  mechanism: `HarvestWatermark` (the stop-decision gate) and `walk`/`Skip`/
  `ItemKey`/`guarded_enumerate` (the download loop itself), promoted out of
  `foreskrift/harvest.py`'s original engine. Also hardened in the promotion:
  a `begin()`/`complete()` lifecycle persists a `dirty` flag alongside the
  watermark date, so a crashed, `--limit`-truncated, or per-doc-error run
  leaves the store dirty — the next run disables the consecutive-hit stop
  (but keeps the date-conclusive one) and self-heals by walking back down to
  the safety boundary, rather than trusting fresh records that may sit above
  stranded backlog. `dv/download.py` and `foreskrift/harvest.py`/`avg/download.py`
  (jo) now run through `walk`; `forarbete/download.py` and
  `forarbete/riksdagen.py` adopt the `begin`/`complete` lifecycle directly.
  Each source states its own window (`lookahead_limit`/`safety_days`) at its
  call site — dv: 365-day safety window (annual cadence, coarse dates);
  forarbete/riksdagen/foreskrift/avg-jo: 14 days / 20 items.

## 6. Derived layer + publishing ✅

- ✅ **Render markup lives in Jinja templates** (2026-07-29). The whole
  generate-phase markup — page chrome, per-source page bodies, the node
  walk's leaf emissions, rail/panel/banner partials, every listing, plus the
  site/stats verticals' bodies and the api ops/patch pages — moved from
  `%`-format strings into templates (`lib/templates/`, per-vertical
  `templates/`, `lib/tpl.py` environment; autoescape retired ~166 manual
  `escape()` calls). Algorithmic emission stays Python by rule
  (`rule:markup-in-templates`): `render_runs`, citation prose, `diff`,
  charts SVG, Atom XML. Every phase was gated browser-equivalent against
  the pre-port output by `tools/render_equivalence.py` (HTML5-normalizing
  snapshot diff; 3,644-page sample, zero differences).

The reborn `relate` + `generate` phases. Corpus-wide verbs in `build.py`'s
CLI, special-cased outside the per-document `Stage` machinery — not because
the deps are unbounded but because they don't fit the static per-doc protocol:
`relate` writes shared catalog rows (not one output per basefile), and a doc's
HTML has a **data-dependent** prerequisite set — its own artifact plus the
artifacts of exactly the documents that cite it (its *inbound set*), which the
catalog already knows (`SELECT from_uri … WHERE to_root = X`; the old
pipeline's deps files). For now both rebuild whole; the inbound set is the key
to a future per-doc incremental generate.

- ✅ **SQLite catalog** (`accommodanda/lib/catalog.py`, `relate`). Derived,
  rebuildable from artifacts alone, never a source of truth. Three tables:
  `documents(uri, source, kind, label, title, path)`,
  `links(from_uri, from_anchor, predicate, to_uri, to_root, text)` (the core
  graph) and `genomforande` (the förarbete→EU-directive→SFS-paragraf *implements* relation,
  §7d). (A `fragments` table — per-node text snippets, for link tooltips —
  existed here until the popover redesign made hover previews fetch the
  target page's own rendered HTML instead; an existing `catalog.sqlite`
  keeps it as an orphaned, unwritten table until the next full rebuild.)
  One **generic walk** (`collect_links`) extracts edges from either source —
  works because citations are inline (`text`/`cells` run-lists) and both
  verticals mint the same `https://lagen.nu/<id>#<fragment>` URIs.
  `rebuild()` is per-source (drop + re-insert that source's rows),
  single-process and transactional (sidesteps multi-writer SQLite
  contention). `lagen all relate` → **catalog at `<catalog_root>/catalog.sqlite`**
  (`config.CATALOG_ROOT`, default `data_root`).
  `documents.path` is stored **`data_root`-relative**, never absolute — so a
  *colocated* catalog is *portable*: rsync a dev catalog to a deploy host with a
  different `data_root` and every artifact still resolves. Read sites resolve
  through `catalog.data_root(con)` / `catalog.artifact_path(root, stored)`;
  `rebuild()` migrates any pre-relative absolute rows in place
  (`_relativize_paths`) on the host that built them.
  **Storage decoupled from the corpus** (`catalog_root`, env `CATALOG_ROOT`): the
  latency-sensitive catalog can sit on fast local disk while the bulk artifact
  tree lives on NFS (SQLite's per-statement locking turns into synchronous
  round-trips there — ~8 ms per fresh connection vs ~0.2 ms local, paid many times
  per query-heavy page). The catalog is self-describing about which root its paths
  resolve against: a `meta` table records the absolute `data_root` **only** when
  the catalog lives outside it; a colocated catalog records nothing and falls back
  to its own file's directory, keeping the rsync-portability above (a *separated*
  catalog is pinned to its host's corpus path until that host re-relates).
  A **full rebuild** (missing catalog, or `--force` over the whole corpus) is
  built in a scratch file opened `locking_mode=EXCLUSIVE`/`journal_mode=OFF` (one
  lock for the whole build instead of per-statement, no journal/fsync — a crashed
  rebuild is discarded, not recovered) and **atomically swapped** over the live
  catalog on completion (`build._swap_catalog`: `catalog.quiesce_wal` folds+drops
  the old WAL so a stale `-wal` can't be misapplied onto the new file, then fsync
  file, `os.replace`, fsync dir); readers keep serving the old catalog until the
  rename. Incremental `relate` is unchanged — in-place, WAL.
- ✅ **Norm hierarchy table** (`catalog.rebuild_norm_chain`, `norm_chain`, run
  once per `relate` over the whole corpus — a chain crosses EU → lag →
  förordning → myndighetsföreskrift, so it cannot be scoped per source) — one
  row per typed authority relation (`rpubl:bemyndigande`/`rpubl:genomforDirektiv`/
  `rinfoex:kompletterar`), both ends resolved to a rung (`NORM_LEVEL`/
  `norm_level`) and dropped unless the citing end sits below the cited end (a
  plain cross-reference or a same-rung amendment is not authority). **No reader
  yet**: a first attempt rendered it as a context-rail section and was
  withdrawn — the display turned out to be the hard part, not the data (a
  genomförDirektiv rung duplicated the richer "Genomför EU-rätt" row, and the
  lag/förordning rung is present for only the ~700 förordningar stating one of
  the two authority formulas, with nothing on the page to explain the gap).
  Kept as data for a future editorial (`ai-*`) command to read, not a rendered
  feature. `test/test_norm_chain.py`.
- ✅ **Cross-source inbound-link graph** — the killer feature, working
  end-to-end. `catalog.inbound(con, uri)` = the distinct docs citing exactly
  that fragment uri. Verified on the partial corpus: **2,037 cases cite
  räntelagen § 6** (`1975:635#P6`); a case → law-paragraph → back-to-every-
  case-on-that-paragraph round-trip renders both directions.
  **Since 2026-08-07 the serving layer reads it from a derived tree, not from
  the catalog** (`lib/inbound.py`, written per page by `generate`). Two problems
  forced it, and they turned out to be one. *Cost*: the links table is keyed by
  the citing document, so "who cites brottsbalken and everything in it" gathers
  162,909 rows scattered across 2.1 GB — 231 MB of random reads, minutes on
  prod's ~100-IOPS disk, and no index closes it (the query needs five link
  columns *and* a join per citer). *Answer*: any caller sees a page of a panel
  far too big to return whole, and every cheap sort key was unrepresentative —
  by source name the first 100 citations of brottsbalken were 100 JK/JO/ARN
  decisions; by the citer's own authority, 100 statutes. Both need all the rows
  materialised first, which *is* the expensive read. Sorting once at build time
  in the order the site's context rail already uses settles both. The API
  contract changed with it (whole-law scope by default, `total`/`by_source`,
  10,000-row pages) while there are still no external consumers.
  **Since 2026-08-21 every row also carries the citing document's own
  `inbound_count`**, and `sort=citations` orders the whole scope by it — the
  "which of these matter" question, which the build-time order cannot answer
  because it is one fixed order for every reader. That count comes from
  `catalog.inbound_counts_for`, the targeted query the context rail already
  uses, on the `idx_links_to_root` covering index: 893 citers and 13 ms for
  avtalslagen 36 §, 11,693 and 578 ms for the whole of brottsbalken. `rail`
  stays the default and counts only the page. Both faces take it (REST
  `?sort=`, MCP `sort`), because ranking candidates is exactly what an AI host
  asking "the leading cases on this paragraf" needs and it had no signal for
  it. Same date,
  `catalog.DEP_INBOUND_COLUMNS` (what a page's freshness digest covers) widened
  from five link columns to ten, fixing a real staleness bug along the way: a
  citer re-parse that only moved *which* provision a pinpoint landed on (e.g. a
  lagrum-grammar fix) left the cited page's digest unchanged, so it kept serving
  the citation drawn in the old margin.
- ✅ **Static HTML site** (`accommodanda/lib/render.py`, `generate`). A single
  generic node renderer (keyed on artifact `type`) handles both the SFS
  structure tree and the DV body; **outbound** links are live `<a>`s to the
  cited doc's exact paragraph. **Inbound** links at two granularities: a
  per-paragraph margin annotation (id-bearing nodes) *and* document-level rail
  sections (`document_inbound`, folded into the "Om dokumentet" panel) for
  citations to the law/case as a whole — the
  **27% of citations that carry no `#fragment`** (and all case inbound) that
  no paragraph annotation surfaces. A `Site` holds the set of known document
  URIs, so a citation to a doc we don't have **renders as muted text, not a
  404** (`.noref`) — becomes live once that doc is parsed. Frontpage ranks
  laws by inbound count. `lagen all generate` →
  `site/data/generated/{index.html,style.css,sfs/*.html,dom/*.html}`;
  `lagen all serve [--port]` serves it. `test/test_site.py`.
- ✅ **2026 presentation redesign — the scroll-driven context rail.** The page
  shell was rebuilt (`page.page`): a sticky masthead with per-section nav, a
  three-column grid (TOC · reading column · context rail) that under 64rem
  becomes a single reading column with the side columns as drawers — the TOC an
  off-canvas left drawer, the rail a bottom sheet, opened from a fixed bottom
  toolbar (Innehåll · Sök · Kontext, the mobile bar in `lib/templates/
  page.html` + `lib/assets/drawers.js`) while the masthead wraps (horizontally
  scrollable nav; the search collapsed to an icon already at 80rem, before its
  label can wrap) and scrolls away — a serif/sans type system on warm paper, and SFS §-numerals
  hung in a gutter with a permalink pilcrow. The big structural change is that
  **inbound is no longer floated inline next to each paragraph** — a `Rail`
  collector gathers every id-bearing node's context (who cites it — split
  temporally when the label was renumbered — which EU article it transposes,
  correspondence/tidigare-beteckning margins, FK/kommentar/remiss and
  bemyndigande panels) into a single JSON island, and the client
  (`lib/assets/scrollspy.js`, `window.lagenScrollspy(root, island)` — one
  instance per reading surface, returning a destroy function; the page's own
  `.gr-body` gets one at load, each split-view pane gets its own, below) builds
  the rail as a column of one-line entries, each absolutely positioned beside
  the location it annotates (nodes that carry context carry `data-rail`); the
  entry at the top of the viewport expands in place into its full "Kontext
  för …" panel as you scroll, the rest staying collapsed to a summary line
  ("Rättsfall (5) + 2 ytterligare") — only the expanded panel's HTML is ever
  mounted, since a large statute's island is megabytes. All
  href/link logic stays in Python — the client only moves pre-rendered HTML. A
  ⌘K command palette closes the search loop (below) and grew local quick-jump
  + hover-popover navigation (below). The
  document-level inbound panel and the new genomför/term displays plug into the
  same shell. Render-only (regenerate, no relate).
- ✅ **Authoritative-source ("Källa") link.** Every artifact carries one uniform
  `source_url` — the publisher's own page for the document — resolved once, for
  all sources, by `build.write_artifact` in precedence order (parser-set on the
  artifact → the real fetched/landing location the downloader recorded → one
  `lib.layout` derives by rule from identity, e.g. an EU act's EUR-Lex URL from
  its CELEX, a case's domstol URL). `render` turns it into each page's "Källa"
  external link; a document with none simply omits it.
- ✅ **Case-law citation graph reconnected — DV document URI re-minted to the
  old scheme.** Was: the DV vertical published `dom/AD_1993_nr_100` (an ad-hoc
  referat-slug) while RATTSFALL citations mint the old rinfo canonical
  `dom/ad/1993:100` / `dom/nja/{year}s{page}` / `.../not/{n}` — so 42,281
  case→case edges pointed at URIs no document had. **User constraint: published
  case URLs / internal URI-shaped ids must NOT change from the old pipeline.**
  Fix (`lib/casenaming.py::case_uri`, formerly `dv/parse.py`): mint the document
  URI by running the case's referat through the **same RATTSFALL parser citations
  use**, so the document URI is byte-identical to any reference to it, by
  construction — the old published identifier, not a new one. **All 17,393 referat
  cases parse, 0 fall back** (verified across the whole index). `test/test_dv_parse.py`
  (`case_uri` + minting tests). Required a full DV re-parse → re-relate →
  re-generate (the `uri` lives inside each artifact).
  - ⬜ **Non-referat cases (~1,335, ~7%)** keep a stable slug URI for now.
    They are never citation targets (RATTSFALL only names referat/notis), so
    the graph doesn't need them; but the old pipeline published them under the
    *verdict* scheme `dom/{publisher_slug}/{malnummer}/{avgorandedatum}`
    (`swedishlegalsource.space.ttl`). Restoring that needs a verified DV-court
    → rinfo-org-slug map (HDO→hd, ADO→ad, … across every hovrätt/kammarrätt) —
    deferred rather than guessed, since the URI is a published identifier.
- ✅ **Per-doc incremental generate.** `generate` treats `relate` as its upstream
  dep and **auto-runs it** for any source whose artifacts are newer than the
  catalog (`stale_sources()`, make's target-older-than-prerequisite rule;
  `--force` re-relates all). Each page then re-renders **only when it actually
  changed**: its manifest-tracked freshness key (`page_signature`) is its own
  artifact hash **+** `catalog.page_dependency_digest` — a digest of its
  *data-dependent* prerequisite set, the inbound citers it annotates plus the
  hosted documents it links out to. So a page goes stale when a new case starts
  citing it, an old citer drops, or a link target appears/disappears — not when an
  unrelated artifact changes (the old pipeline's deps-file rule, as a catalog
  query). `relate` itself still rebuilds per-source whole (seconds); `parse` stays
  an explicit upstream step.
- ✅ **Bare lagen.nu page URLs — the published URI grammar, restored.** A document
  is now linked at its *bare* address (`/2018:585`, `/prop/2020/21:22`,
  `/dom/ad/1993:100`, `/celex/32016R0679`), not the flattened on-disk filename
  (`/sfs/1962_700.html`). `lib/layout` grew the split: `page_relpath` is the
  filesystem-safe HTML file, **`page_url`** the public address a link points at,
  and **`url_to_relpath`** the inverse the static server applies. A statute is a
  *top-level* page (`2018:585.html`, the SFS colon kept) served at `/2018:585`; EU
  acts collapse `ext/celex/` to `/celex/`. `page.href`, the API (`SearchResult`/
  `BrowseDoc.url`) and the browse model all emit `page_url`; `api.app.SiteFiles`
  rewrites a bare document URL back to its file on a static miss (nginx's
  `try_files`, in Starlette), so `lagen serve` answers the published URLs directly.
  `test/test_api.py`, `test/test_facets.py`, `test/test_site.py`.
- ✅ **Repealed (upphävd) statutes.** A statute whose `rpubl:upphavandedatum` has
  passed is marked **upphävd** end-to-end: the catalog carries an `expired` column
  (`catalog.expired_date`/`expired_uris`); the browse listings **omit** it
  (`facets._rows`, still reachable by direct link and search — the listing shows
  only law in force); and its page gets a repeal callout (with a link to the
  repealing act when known), a subdued reading column and a fixed "Upphävd
  författning" watermark that stays visible at any scroll depth (`sfs/render.py`'s `_expired_banner`
  + the `body.expired` treatment). A *future* repeal date is still in force.
  `test/test_site.py`.
- ✅ **Repealed EU acts.** The same treatment for eurlex, off CELLAR's own
  metadata rather than the citation graph: `cdm:resource_legal_in-force` (the
  flag EUR-Lex prints as "No longer in force") plus
  `cdm:resource_legal_date_end-of-validity`. Both are kept in the stored
  `notice.ttl` (`lib/cellar.py`'s `META_PREDICATES` / `notice_ttl`) and read back
  by `cellar.notice_repeal_date`, which `eurlex/parse.parse_dir` stamps on the
  artifact as `expired` — so the repeal reaches `documents.expired` through the
  artifact like every other extracted fact. Both triples are needed: 32006L0040
  carries an end date of 2009-04-28 and is still in force, and 31981L0576 carries
  two end dates (EUR-Lex prints the last). `9999-12-31` is CELLAR's "no end date"
  placeholder. The API's document enumeration now applies the rule browse and
  search already did (`catalog._doc_filter`'s `include_expired`, default false;
  `/api/v1/documents?include_expired=true` and the MCP `list_documents` argument
  put them back), and a repealed act's page carries its own callout and a
  "Gäller inte längre" watermark (`eurlex/render.py`, `BANNERS.eurlex_expired_banner`).
  A corpus harvested before this stores no validity pair, so
  `lagen eurlex refresh-metadata` re-reads CELLAR's metadata into every
  `notice.ttl` without refetching content; `parse` + `relate` then carry it
  through. `test/test_eurlex_parse.py`, `test/test_api.py`.
- ✅ **Keeping the repeal current without re-reading the corpus.** The flag sits
  on the *repealed* act and the discovery walk is bounded by work date, so it
  never returns to a 1995 directive repealed in 2018 — read naively, the status
  would need a periodic metadata sweep of all 64,037 documents. The repealing
  act is the way in: it carries
  `cdm:resource_legal_repeals_resource_legal` / `…_implicitly_repeals_…` while
  the old act carries no inverse edge at all. So `download` asks each year's
  newly stored acts what they repeal and re-reads the targets the corpus holds
  (`cellar.fetch_repeals` → `download.refresh_repeal_targets`), one extra
  batched query per year (1.3 s / 88 KB per 1,000 CELEX, measured).
  Coverage, measured over 600 random non-caselaw documents: 225 out of force,
  **145 (64%) named by a repeal edge**; the other 36% end by their own terms
  with no act repealing them, and only the `refresh-metadata` audit finds those.
  That audit skips documents already recorded as repealed — a repeal never
  lifts — so it shrinks each run instead of costing the corpus every time.
  The flag stays the gate rather than the date: checked against EUR-Lex's own
  pages, 32005R0145, 32006L0040, 32014R1198 and 31978L1020 all print "In force"
  while carrying a past end-of-validity (12 of 600), so a date-only rule would
  hide acts that still apply. `test/test_eurlex_download.py`.
- ✅ **Two ways the mark could still not land**, both closed:
  *(1) The notice was not a parse input.* The repeal date lives in `notice.ttl`
  and nowhere else, and a metadata refresh rewrites the notice while leaving the
  content file untouched — so `parse` judged such a document fresh and the
  repeal never reached the artifact. Verified before the fix: a notice rewritten
  to a different end-of-validity left `expired` on the old date with `parse`
  reporting "skipped (fresh) 1". `depends="download"` does not cover it (it
  recurses into the upstream stage but never hashes its output), so the notice
  is now in the parse stage's `inputs`.
  *(2) A corrigendum carried no repeal.* CELLAR flags the act and never its
  corrigenda — of 537 held corrigenda whose base act we hold, none carry the
  flag — so repealing an act expired the act's row and left its corrigenda
  listed, ranked and searchable. `parse.revision_repeal_date` inherits the base
  act's date, for both revision shapes (`32016R0900R(01)` → `32016R0900`,
  `12019W/TXT(01)` → `12019W/TXT`; the optional `R` is the difference, and
  getting it wrong points at a CELEX that does not exist). The base's notice
  joins the corrigendum's freshness inputs (`build.eurlex_parse_notices`).
  Four held acts with five corrigenda are in that position today, all repealed
  during 2026. `test/test_build.py`, `test/test_eurlex_parse.py`.
- ✅ **Statute browse listing — visual hierarchy.** An SFS entry is split
  into its dropped designation/number prefix (shown subdued) and the subject it
  sorts under (emphasised), so the eye lands on the sort key (`facets._sfs_split`);
  parliamentary primary law (a *lag*, a *balk*, or a grundlag) is shown at full
  weight while secondary instruments (förordning, kungörelse, …) are dimmed
  (`labels.sfs_is_statute`, moved out of `facets.py` 2026-08-03 so the catalog
  can read it too: an SFS row's `kind` is now `lag` or `forordning` rather than
  one uniform `law`, which is also the norm hierarchy's rung test, §6). The
  listing carries `pre`/`key`/`subdued` on each `BrowseDoc`; the kind split also
  renamed the browse's kind buckets ("Lagar"/"Förordningar m.m." replacing one
  "Författningar"). `test/test_facets.py`, `test/test_api.py`.
- ✅ **Publishing layer — search, REST/OpenAPI, bulk dumps** (replaces the
  retired Fuseki/RDF publishing). All three are **derived & rebuildable** from
  artifacts + catalog, never a source of truth, and slot in as **corpus-wide
  verbs** in `build.py` next to `relate`/`generate`/`serve`. Decided with the
  user: OpenSearch 2.x (not ES — Apache-2, `opensearch-py`); FastAPI + uvicorn
  (OpenAPI 3 + Swagger for free); parent-child indexing (doc + per-§ fragment);
  NDJSON bulk dumps (not JSON-LD — no `@context` modeling, dumps are the raw
  artifacts). Published `lagen.nu` URIs stay byte-identical (standing
  constraint) — API key, dump `uri`, ES `_id` are all that URI.
  - ✅ **Shared flattener** (`lib/text.py`) — one definition of "the text of a
    node / document / fragment" (runs = `str | {uri,text,…}` → join the `text`s,
    table `cells` joined by space, body sections + amendments concatenated),
    with `catalog`'s `runs_text` refactored onto it (re-exported, so the two
    `catalog.runs_text` callers are untouched). The DRY seam indexing and dumps
    share. `test/test_text.py`.
  - ✅ **OpenSearch indexing** (`lib/search.py`, `lagen <src> index`) — keeps the
    old `ferenda/fulltextindex.py:ElasticSearchIndex` domain knowledge (field
    boosts, paragraph-precise hits, `inbound_count` ranking) but **without a
    parent-child join** — at corpus scale (~1M+ units, more once the flat
    verticals gain structure) the join's global ordinals were the dominant heap
    consumer and kept tripping the parent circuit breaker. Instead every unit is a
    **standalone document carrying its parent's metadata**, and search
    **collapses by `doc_uri`** to one result per document: one whole-document unit
    (`is_doc`, carries the body text only when the doc has no fragments) + one unit
    per id-bearing fragment (its text + `pinpoint`, with the document's
    identity denormalised as *non-searchable* `doc_title`/`doc_label` so a title
    query collapses to the document, a body query to the matching paragraph).
    Ranking is relevance + `log1p(inbound_count)` (`catalog.document_inbound_count`,
    the whole-document "most-hänvisade" signal on *to_root*); a `cardinality` agg
    gives the distinct-doc total. Per-source whole rebuild (drop_source +
    `helpers.bulk`, 5 MB/chunk). Cluster endpoint from `config.yml`'s
    `opensearch_url` (env `OPENSEARCH_URL` overrides). **Verified live** against a
    real OpenSearch 2.18 (`docker-compose.yml`): the collapse round-trip + a real
    `kommentar` index (212 docs → 1913 units) return one result per document with
    paragraph pinpoints, no breaker. opensearch-py 3.x bugs the cluster surfaced
    and fixed along the way: client calls are keyword-only (`index=…`),
    `doc_actions` must not hardcode `_index`; index settings `number_of_replicas:0`
    + `refresh_interval:60s`. `test/test_search.py`.
  - ✅ **Search facets, prefix matching, a full `/sok` results page.** A `year`
    facet (`facets.document_year`, reusing browse's own per-source year
    extraction — SFS from its `YYYY:number` identifier, other sources from
    their existing browse `SCHEMES` "År" level) is indexed alongside
    `source`/`kind`; `query_body` runs the text query as a `post_filter` (hits
    narrow on the selected facets, but each facet's own aggregation still
    counts against the *other* selected facets, so there's always a way back
    out) and returns per-facet buckets (`SearchResponse.facets`) plus a `year`
    query param end-to-end (`/api/v1/search?year=`, `SearchIndex.search`).
    Every query also runs a second, prefix-matching branch (`prefix_query` —
    every ordinary word gets a trailing `*`, so `upphovsr` matches
    `upphovsrätt`) OR'd against the exact query. Because these are index-schema
    changes an artifact-hash-only freshness check can't see, `search.py` folds
    an `INDEX_FORMAT` version into each indexed unit's stored freshness key, so
    bumping it reindexes every affected unit on the next ordinary incremental
    pass. On the client, `render.render_search_page` renders a full result-list
    page with a facet sidebar at `/sok` (`fullsearch.js`), replacing the ⌘K
    palette's in-page dropdown for anyone who wants to page through / narrow a
    result set. `test/test_search.py`, `test/test_api.py`.
  - ✅ **REST / OpenAPI** (`accommodanda/api/app.py`, mounted on `lagen all serve`, FastAPI +
    uvicorn) over three read-only backends (catalog.sqlite · OpenSearch · artifact
    JSON). `/api/v1`: `search` (each hit carries its hosted-page `url` via
    `layout.page_relpath`), `documents` (filtered/paginated id+metadata index of
    the corpus — *not* search, which requires `q`; carries `updated` = artifact
    mtime and `source_url` denormalised into the catalog like `title`),
    `document?uri=…` (URI as query param — `lagen.nu` URIs carry `:`/`/`),
    `document/inbound` (the killer feature as data — `scope=tree` by default, so
    a law answers for itself *and every provision in it*, served from the
    per-document files `generate` writes rather than a live catalog query; see
    `lib/inbound.py`),
    `document/outbound` (`hosted` flag for un-parsed targets), `graph` (a node's
    neighborhood aggregated per neighbor document, grouped by `lib/facets.flow_group`
    — what the `/hanvisningar/` explorer draws), `sources`, `dumps`.
    Auto `/openapi.json` + `/docs`. CORS-open (read-only public data) so the
    static site reaches it cross-origin. **Two apps, two path namespaces**
    (`api/internal.py`): everything above is the public `/api/v1` and is all
    `/docs` shows, while the surface only the site itself calls — login, the
    commentary/patch/crop editors, the PDF export's background jobs — is a
    mounted app at `/internal-api/v1` with its own schema behind the editor
    session. Every internal route is same-origin only, reads included, and the
    `/ops` dashboard carries the same two gates (`auth.same_origin`). Verified live against the **real
    1.5 GB catalog**: Brottsbalk inbound 5,153, räntelagen §6 ← 2,783 citers.
    Closes the ⌘K loop — `lib/assets/search.js`'s palette now does a debounced
    `fetch` to `/api/v1/search` (API base baked into each page as
    `<meta name="lagen-api">`, overridable with `LAGEN_API`). Tested with
    FastAPI `TestClient` over a fixture catalog + faked search — no live cluster.
    `test/test_api.py`.
  - ✅ **Power-user navigation chrome — local quick-jump + hover popovers +
    split reading view.** Two additions on top of the ⌘K/search-API loop:
    (a) **instant local quick-jump** (`lib/assets/search.js`) — a lenient
    pinpoint grammar (`4`, `4 §`, `11:2`, `4:`, `kap 4`, `art 5.2`, `(42`,
    `skäl 42`, `bilaga III`) resolved against the *current page's own*
    anchors (`window.lagenDom.ownEl`), no network; a match shows the
    target's own text and Enter scrolls+flashes it. Hits appear as soon as
    the palette opens; if the remote `/api/v1/search` fetch then fails, the
    local hits stay and a "Sökningen kunde inte nås" note is added rather
    than the whole palette going empty. (b) **`lib/assets/popover.js`** —
    hover/focus previews on every internal link in the reading column and
    context rail, built from the *rendered target page* (same-origin
    `fetch` + `DOMParser`, cached per pathname; same-page targets read
    straight from the live DOM) — replacing the old title-attribute tooltip
    `render.py` used to emit from catalog snippets (the `fragments` table
    it read is gone, see §6). The popover's ↗ expands the target into a
    **split reading view**: stacked panes, each importing the fetched
    page's full `.gr-body` (TOC + reading column + context rail, its JSON
    island carried along) marked `[data-pane]`, with its own
    `lagenScrollspy` instance and a slim chrome bar (title link, move
    up/down, close); draggable dividers resize panes; closing the last
    import restores the normal single-document layout. Id collisions
    between panes (two statutes both minting `#P1`) are resolved by
    `lib/assets/dom.js`'s `window.lagenDom` — the shared own-document
    anchor lookup (`ownEl`/`sel`), landing-flash and JSON-island-parse
    helpers scrollspy/search/popover all build on, so "the page's own
    anchor" means the same thing everywhere once several documents share
    one DOM.
  - ✅ **Graph explorer** (`/hanvisningar/`, `lib/templates/hanvisningar.html` +
    `lib/assets/graf.js`, over `GET /api/v1/graph`) — the citation graph as a
    page a reader can walk rather than a number in a rail. `catalog.py`'s new
    `graph_*` queries answer per *neighbor document* (one row per citer/target
    with its link count) instead of per citation, grouped by the same
    `lib/facets.flow_group` map the stats sankey (§7k) uses, so both surfaces
    agree on what a node is. `graf.js` draws a force-directed canvas layout
    from that JSON: a degree stepper widens the walk outward, scrolling out
    past the current degree fetches the next one, a direction toggle
    (in/out/both) and a legend that doubles as a flow-group filter narrow the
    view. A fragment uri (`…#K4P7`, `…#A6`) switches to **pinpoint mode**:
    the neighborhood of that one provision, plus `internal` — the document's
    own §/article-to-§/article graph from `graph_internal`, unit ids
    collapsed by `pinpoint.unit_anchor` so a stycke-level citation lands on
    its § rather than fragmenting the view. Default center is
    `https://lagen.nu/ext/coe/005#A6`, ECHR article 6 — the corpus' most-cited
    single provision. `coe/parse.py` gained inline linking of bare "Article N"
    references to the instrument's own provisions (only ordinals it holds; an
    external treaty citation wins the overlap) so the ECHR's internal graph
    has edges to draw — 29 links recovered on that one treaty.
  - ✅ **NDJSON bulk dumps** (`lib/dump.py`, `lagen <src> dump`) — every
    `artifact/<source>/**.json` re-serialised one-per-line, gzipped, to
    `site/data/dumps/<source>.ndjson.gz`. Each line round-trips to its on-disk
    artifact; the citation graph is already inline, so a line is self-contained
    (no catalog read, no transform). Listed at `/api/v1/dumps` (a manifest —
    `source`/`file`/`size` — not a download route: the app's static mount
    covers `generated/` only). The files themselves are now served by the
    reverse proxy, not uvicorn — a `location /dumps/` block in
    `docker/nginx/ferenda.lagen.nu.conf` (autoindex on, gzip off, sendfile +
    byte ranges) over a read-only mount of `<data_root>/dumps`
    (`docker-compose.prod.yml`), since the set is ~4.5 GB (forarbete alone
    ~3.6 GB) — committed but **not yet deployed** to prod. Verified on the
    real `kommentar` source (212 lines). `test/test_dump.py`.
  - New deps: `opensearch-py`, `fastapi`, `uvicorn` (pyproject). ✅ **`lagen all
    index` run at corpus scale** against a provisioned OpenSearch — works.
    ✅ **Incremental relate + index** (content-hash diff, see 2026-06-26 log).
  - ✅ **MCP server** (`accommodanda/api/mcp.py`, mounted at `/mcp` via
    Streamable HTTP on the same `lagen all serve` FastAPI app) — the same
    read-only view reshaped as eight tools (`search`, `resolve_citation`,
    `get_document`, `fetch`, `list_documents`, `get_incoming_citations`,
    `get_outgoing_citations`, `list_sources`) for any MCP-capable AI host,
    public and unauthenticated like REST. The tools are thin wrappers over
    the same `lib` functions the REST endpoints use; `lib/pins.py` was
    extracted as the shared citation-shaped-query resolver (name+pinpoint →
    exact fragment target) behind both REST `/search` and the MCP
    `search`/`resolve_citation` tools. `test/test_mcp.py`, incl. end-to-end
    Streamable HTTP round-trips against a running app.
    `get_document` (and `fetch` through it) answers with the body rendered as
    **markdown** by default (`lib/mdtext.py` — headings, paragraph
    designations, lists, tables, citations as inline `[text](uri)` links;
    tuned for the SFS/eurlex/förarbete shapes, generic for the rest);
    `format="json"` returns the raw artifact tree instead. REST's
    `/api/v1/document` takes the same `format` parameter with the opposite
    default (`json`), swapping `artifact` for a `markdown` field — the
    envelope and metadata stay JSON either way. `test/test_mdtext.py`.
    The endpoint speaks protocol revision **2026-07-28** (SDK `mcp>=2.0`,
    `MCPServer`), the revision that deleted the protocol's session concept:
    no `initialize` handshake, no `Mcp-Session-Id`, every call a
    self-contained POST carrying the client's version and capabilities in
    `params._meta`, with `server/discover` in place of the handshake's
    capability exchange — so any request may land on any process and `/mcp`
    scales behind plain round-robin. The same endpoint still serves
    2025-11-25 and older clients, which handshake as before; both paths are
    tested against one running server. `tools/list`/`server/discover` are
    advertised cacheable for an hour and shareable (SEP-2549 `ttlMs`/
    `cacheScope`) since the tool table only changes at deploy.
    Note for anything proxying the app: 2026-07-28 requires `Mcp-Method` (and
    `Mcp-Name` on `tools/call`) on every POST so gateways can route without
    reading the body — the SDK rejects a missing or mismatched one with
    `-32020`. Our nginx vhost proxies straight through, but a proxy that
    strips unknown headers would silence the endpoint.
    `search`/`fetch` additionally satisfy the result contract OpenAI's hosts
    expect of a knowledge server (`{results: [{id, title, url}]}` and
    `{id, title, text, url, metadata}`, both as `structuredContent`) — met by
    *naming* fields, not by narrowing tools: the contract's fields are a subset
    of what the corpus already answers with, `search` gained only an `id` key
    (the fragment URI on a paragraph-deep hit, so a fetch reads the provision
    and not the whole statute), `fetch` is a thin wrapper over `get_document`
    with the unmapped corpus facts in `metadata`, and the citation-graph tools
    are untouched. Both declare `TypedDict` returns, which is what makes the
    SDK emit `structuredContent` at all (a bare `-> dict` yields neither schema
    nor structure). Deliberately *not* adopted as the model — it is a
    two-tool RAG shape with no expression for the citation graph, which is the
    point of the server — a downstream projection, never the model
    (rule:own-typed-model, one layer up).
    Operationally: a `_LoggedMCP` ASGI wrapper logs one line per JSON-RPC
    request (client IP, method, tool name + truncated arguments) since the
    uvicorn/nginx access log only sees `POST /mcp/ 200`; the MCP SDK's
    DNS-rebinding protection is explicitly disabled
    (`TransportSecuritySettings(enable_dns_rebinding_protection=False)`) —
    its localhost-only default would 421 all production traffic arriving
    through the nginx vhost. `serve()` now also calls
    `logging.basicConfig(INFO)` so these and other app-level log lines reach
    stdout alongside uvicorn's own access log.
  - ✅ **Operations/health dashboard** (`lib/runlog.py`, `api/ops.py`) — every
    `build.py` invocation now records a run in an append-only ledger
    (`DATA/.build/runs.ndjson`: run-start / per-(step,source) segment /
    run-end), folds per-doc failures into a keyed latest-outcome store
    (`errors.json`, so "failed" is distinguishable from "never tried") and,
    on full-source runs, updates a rolling per-source × per-stage snapshot
    (`status.json`). `lagen <source> status` writes the authoritative
    snapshot cell; `lagen all runs [N]` lists recent runs from the CLI. The
    dashboard itself is `/ops` on the FastAPI app (HTML, gated by the inline
    editor's session — any logged-in editor may view it; an unset
    `editor_secret` disables it, 403) with `/ops/runs`, `/ops/runs/{id}` and
    `/ops/failures` drill-downs, plus a system panel (deployed git revision baked
    at image build, lagen-wiki push state, OpenSearch index size) and a per-source
    corpus inventory (docs + artifact size). A successful login on `/admin/`
    redirects here. `test/test_runlog.py`, `test/test_ops.py`, `test/test_git.py`.
  - ✅ **Inline content editor** (`api/auth.py` + `api/edit.py` + `api/editcontent.py`
    + `api/editcart.py`; the write side of the service, first cut 2026-07-05) — a
    logged-in user edits the git-backed markdown (kommentar / begrepp / editorial
    site) *inline on the live site*: an ✎ on any §/article opens the commentary for
    that node (created from `fragment_heading` if none exists), a concept/editorial
    page edits its whole body, with a link toolbar that turns a search hit into an
    `sfs:`/`eurlex:`/`begrepp:` link. Edits accumulate in a per-user "cart"
    (`DATA/.build/edits/<user>.json`, isolated from the working tree); checkout is
    **one git commit authored as that user** (`name`/`email` from a new `editors`
    config registry — so history attributes each editor exactly as a clone+commit
    would), conflict-checked against on-disk `base_sha`, followed by a synchronous
    scoped rebuild (`build.rebuild_after_commit`: parse → relate → regenerate just
    the touched pages) so the edit is live when the call returns. Auth is a signed
    session cookie (stdlib HMAC over the `editor_secret` knob — unset disables
    editing, and the `/ops` dashboard that shares this session, 403); passwords
    are `pbkdf2$…` strings minted by
    `python -m accommodanda.api.auth hash`. The static site stays byte-identical for
    anonymous readers — the affordances are grafted client-side (`lib/assets/editor.js`,
    `editor.js`) after a `/auth/me` check, keyed off a `<meta name="lagen-doc">`
    render injects. The mutating routes are same-origin only (CORS stays GET-open).
    `test/test_editcontent.py`, `test/test_editcart.py`, `test/test_edit_api.py`.
- ✅ **Full corpus now catalogued.** `relate` runs over the whole set —
  `documents`: sfs 11,184 · dv 17,103 · forarbete 15,237 · eurlex 61,146
  (+ kommentar/begrepp) — so the cited law-roots that were dead targets in the
  first partial cut are now live. A full `lagen all generate` (~100k+ pages,
  EU-dominated) has been run and completes in acceptable wall-time. The
  document-specific parse errors were triaged (2026-06-27): 3 forarbete docs hit
  `KeyError: 'item'` in the citation grammar (the `itemnumeric_ref_id` "tredje
  punkten" form wasn't handled by `fmt_section_item_refs`); 149 eurlex judgments
  hit `ParseError: line 1, column 0` (CELLAR served scanned TIFFs under their
  fmx4 manifestation — now fixed by the downloader's content-format fallback, §7d).
  `cmd_all`'s parse step also now withholds the source watermark when any doc
  errored, so a quiet source with failures retries (and re-surfaces them) next run
  instead of being skipped wholesale.

## 7. Further verticals 🚧

### 7a. Förarbeten vertical (preparatory works) 🚧

The third leg of lagen.nu's killer feature — förarbeten (prop/SOU/Ds/dir + the
lesser types) annotated onto the statute paragraphs they comment on. ~31,700
förarbete citations currently render as dead `.noref` text; this vertical makes
them resolve.

- ✅ **Downloader** `accommodanda/forarbete/download.py` — harvests all nine
  regeringen.se types from `/rattsliga-dokument/`. Built from first principles
  off the live site (the old `Regeringen` downloader targeted the pre-rebuild
  site). **Enumeration** is the page's own AJAX filter endpoint
  `GET /Filter/GetFilteredItems?…&preFilteredCategories=<taxonomy-id>&page=N`
  (the visible `?p=N` links are inert), returning a JSON envelope
  `{"Message": <ul.list--block html>, "TotalCount": N}`. Each listing item
  carries the document's **own identifier** and a landing-page link; the landing
  page hangs the content PDF under `/contentassets/`. Types + taxonomy ids:
  prop 1329 (4,336 docs), sou 1331 (3,158), ds 1325, dir 1327 (2,432), fm 1326,
  skr 1330, so 1332, lr 2085.
  - **basefile = the document's own identifier** (prop "2025/26:279", sou
    "2020:1", …), per user requirement, so the same act from other sources
    (riksdagen/KB) for older periods reconciles by identity. The two types
    regeringen.se publishes untitled-by-number key on the document itself, never
    the unreliable URL slug: **SÖ** on the number in its landing-page vignette
    (`resolve_identity` reads the `h1-vignette`, rejecting a non-SÖ item that the
    SÖ index happened to list); **lagrådsremiss** on `<year>/<title-slug>`
    (`lr_identity`, the title carrying the identity since the vignette is only
    the bare word "Lagrådsremiss"). A curated `regeringen.is_misleading`
    skip-list drops the dual-published/mislabelled landing pages that would
    otherwise mint a wrong identity.
  - **`pm` (promemorior outside the Ds series)** shares category 1325
    ("Departementsserien och promemorior") with `ds`; `parse_listing`'s
    `EXCLUDE` map gives `ds` the items numbered `Ds YYYY:N` and `pm` the rest.
    A pm without a Ds number is keyed by its **diarienummer** (`Ju2026/01691`,
    `KN2026/01475`, …); one with neither Ds number nor dnr falls back to the
    landing-page slug. Same downloader, same parse pipeline.
  - Incremental (newest-first, through the shared `lib/harvest.walk`/
    `HarvestWatermark` begin/complete lifecycle — dv, §4) + `--full`; atomic
    writes; browser UA (regeringen.se 403s bots); politeness delay. Fixed
    (2026-07-06): `iter_listing` was terminating on the *type-filtered*
    descriptor count, so a raw page whose items all belonged to the sibling
    type (pm/ds share category 1325) read as "exhausted" and permanently
    truncated the listing below it; it now keys exhaustion on the raw
    per-page item count, cross-checked against the envelope's `TotalCount`
    (a truncated/broken listing now raises rather than silently stopping).
    Stores per doc:
    `<slug>.json` record + landing `<slug>.html` + content PDF(s) under
    `site/data/downloaded/forarbete/<type>/<year>/` (year-segmented since
    2026-07-18, `pm` bucketed under `_`). `test/test_forarbete_download.py`.
  - ✅ **Older-period sources imported from the frozen corpora** —
    propriksdagen, KB and the regeringen-era gap-fill trees use the same
    identifier-keyed records and precedence machinery; see §7g. A live
    replacement can claim the same basefiles later without changing identity.
  - ⬜ **lr/SÖ content links** — these expose an extensionless
    `/contentassets/<hash>/<slug>/` (HTML-rendered), not a `.pdf`; landing HTML
    is captured but no file pulled yet.
- ✅ **Parser** `accommodanda/forarbete/{model,parse}.py` (PDF → artifact). Text
  via poppler `pdftotext` (plain reading-order mode — isolates the running
  header + page number on their own lines, unlike `-layout` which mashes them
  into the alternating outer margin). **Page = PDF index = printed page** (modern
  PDFs number from the title page), so each block carries its `#sid{N}` anchor —
  the target förarbete citations resolve to (`prop. X s. 39` → `prop/X#sid39`).
  Reflows wrapped lines
  (de-hyphenates), strips the running header (substring, anywhere — it bleeds
  into body lines), skips TOC pages, detects numbered headings. **URI minted to
  the citation-target form** (`prop/{riksmöte}:{no}`, `sou/{year}:{no}`, …) so
  document and citation agree by construction (the DV-URI lesson). Body scanned
  for refs (same engine as DV) → inline links. Validated: prop 2025/26:161 →
  284 blocks, 464 links (sfs 320, prop 126, sou 7, bet 4, celex 3, rskr 3).
  `test/test_forarbete_parse.py`.
- ✅ **Hierarchy materialized** (`forarbete/structure.py`) — förarbeten carry a
  real numbered outline (14 → 14.3 → 14.3.4, the TOC depth), and the parser
  already tags each heading with a `level`; `nest` groups the flat block run into
  a nested `structure` tree (a `rubrik` opens an `avsnitt` under the nearest open
  section of lower level; other blocks are its content), replacing the flat `body`
  — so `render` shows true nested headings/TOC, `catalog` gets per-section
  `fragments`, and search indexes section units (prop 1999/2000:39: 1,499 blocks →
  4-level tree, **348 fragments where there were 0**). Section `id`s come from the
  heading number (`a14.3.4`) or a counter — TOC/search anchors, **not** citation
  targets: leaves keep their `page`, so the `#sid{N}` citation anchors are
  untouched. `flatten` is the inverse view for the linear consumer
  (`kommentar.py`'s författningskommentar walk). `test/test_forarbete_structure.py`;
  the first of the §7-wide "materialize the flat verticals' structure" effort
  (förarbete → eurlex → DV).
- ✅ **Wired through build + catalog + render**: `lagen forarbete parse`
  (Stage), `catalog.forarbete_document` (source `forarbete`), `forarbete/render.py`'s `render`
  (förarbete page with `#sid{N}` page anchors + page-level inbound margin notes),
  `doc_relpath` routes förarbete URIs to the `fa/` tree. So `relate`/`generate`
  light up the förarbete inbound graph — the ~31,700 dead förarbete citations
  resolve and each förarbete shows what cites it (and at which page).
- ✅ **Font-size-aware parsing + lydelse tables** (driven by prop 2013/14:116's
  misreads): `pdftext` now carries each run's fontspec size and horizontal
  extent. Wrapped multi-line headings fold into one logical rubrik ("5 Mer
  fokuserad nedsättning av / socialavgifterna för de yngsta" — heading lines of
  the same size a heading's own leading apart, numbered-continuation guard);
  a numbered rubrik must be bold or larger than the document body size (a
  body-sized table row "22 år 25 000 …" is not a heading) and clearly smaller
  text becomes `fotnot` blocks ("1 Senaste lydelse 2008:1266." — previously
  level-1 rubriks); bare centered "2 kap."/"28 §" markers classify as
  kapitel/paragraf. `lydelse.py` reconstructs the two-column
  *nuvarande/föreslagen lydelse* comparisons the text-order extraction used to
  interleave into garbage: the italic header line gives the column boundary,
  cells reflow per column (indent/gap paragraphs, superscript footnote markers
  dropped) and pair into aligned rows — a `tabell` block in the SFS
  `rad`/`cells` shape, rendered side by side; an empty cell marks text that is
  entirely new or dropped. Corpus sweep: 1,146 tables / 2,550 rows across the
  59 curated+sampled props, junk level-1 headings 861 → 31, FK extraction
  unchanged or better (162 gained 5 law sections). OCR/legacy routes carry no
  font info and keep the permissive rules. `test/test_forarbete_lydelse.py`,
  `test/test_pdftext.py`, `test/test_forarbete_parse.py`.
- ✅ **Front-matter tagging for prop/skr** (`parse.tag_frontmatter`) — the
  överlämnande page carries no bold, so the font-driven classifier had read it
  all as plain stycken. Now: the "Propositionens/Skrivelsens huvudsakliga
  innehåll" heading is promoted to a level-1 rubrik (so the ingress becomes its
  own avsnitt), and the signer names after the ort/datum line ("Stockholm den
  20 maj 2021") are retagged as a new `signatur` block kind (`model.Block`).
  `structure.signers()`/`structure.ingress()` read them back off the parsed
  artifact. This is the data `sfs/asgit.py`'s `history-as-git` export (§3d)
  mines for commit authorship and message body — reading a förarbete artifact
  stays förarbete's job, composed in by `build.py` like `ai-correspond`.
  `test/test_forarbete_parse.py`.
- ✅ **Ruled boxes, emphasis spans and embedded figures (2026-08-03).** A
  förarbete's stated proposal/assessment ("Regeringens förslag:", a SOU's
  "Förslag:"/"Bedömning:") is set inside a ruled box whose rule `pdftohtml`
  discards as a vector drawing; `classify` recovers it from the narrower
  measure the paragraph is set to (`Para.boxed`) and tags it a `ruta` block,
  rendered as a bordered box rather than an ordinary stycke. Separately,
  `pdftext.pdf_pages` now carries each line's bold/italic **spans** (not just a
  whole-line flag; superscript is not a font attribute poppler reports, and a
  footnote marker stays its own run kind), threaded through `classify`/`_scan`
  into the
  rendered inline styling (`lib/lagrum.interleave`'s new `styles=` parameter
  splits plain text where the emphasis changes and only styles a citation link
  where one emphasis covers the whole of it). And `pdf_figures`
  (`Figure`/`is_figure`) reports the images poppler embeds that are document
  content — inside the text margins, large relative to the measure, which
  excludes bullet glyphs, hairline rules and a scanned page's own full-page
  image — placed among the paragraphs they were printed between as `bild`
  blocks (`bbox` in PDF points via `points_from_pdftohtml`). No pixels are
  copied into the corpus: the API's `/api/v1/facsimile` endpoint gained a
  `bbox=` crop parameter (`lib/facsimile.cached`, the same renderer the
  SFS graphics layer crops with), and a `bild` block's `<figure>` renders that
  crop on demand. En route, `pdftohtml_xml` stopped extracting images to disk
  (`-i` dropped so poppler reports figure placement, but its unrequested image
  *files* — 1,064,761 of them, 350 GB, on the first corpus-wide run — now land
  in a temporary directory the conversion discards), and its cache entries
  gained a command digest so a future flag change re-converts instead of
  silently serving a stale one. `test/test_pdftext.py`.
- ⬜ lr/SÖ content, page-number offset for
  docs whose front matter shifts the printed sequence; general (non-lydelse)
  tables — the budget prop's statistics tables still flatten to stycken; a
  lydelse table continuing onto a page that does not repeat its header.
- ✅ **`bet` (utskottsbetänkanden) — a fourth harvest source**,
  `accommodanda/forarbete/riksdagen.py`. Committee reports are the missing
  prop→enacted-law link ("bet. 2025/26:JuU47 s. 12", already minted by the
  FORARBETEN grammar as `bet/<rm>:<beteckning>`); this downloader fills that
  citation target. Off `data.riksdagen.se`'s `dokumentlista` JSON feed
  (`doktyp=bet`), not regeringen.se. **basefile = `"<rm>:<beteckning>"`**
  (e.g. "2025/26:JuU47"), matching the citation grammar's URIs by
  construction. Bodies are **PDF-only** (the printed page is the citation
  anchor; riksdagen's HTML body carries no pages) — a betänkande without an
  attached filbilaga gets a metadata-only record, still a real catalog
  document. Incremental (newest-first, gated by the shared `HarvestWatermark`;
  only *final* records feed the gate, and the saved date is the newest
  *published* entry's datum — a planned betänkande's future datum would erode
  the safety margin) + `--full`;
  a full backfill iterates all **161 riksmöten** back to 1867, because the
  API caps a single query's pagination at ~10k docs, far below the ~75k-doc
  corpus. Wired into `build.py`'s `fa_harvest` as scope `"bet"` (its own
  sync call, alongside the regeringen.se scopes; `--only` is not supported
  for `bet`). No frozen legacy corpus (§7g) covers it.
  `test/test_forarbete_riksdagen.py`.
- ✅ **`rskr` (riksdagsskrivelser) — a fifth harvest source**, sharing the
  same engine. The bet-specific `_walk`/`sync` in `riksdagen.py` were
  generalized into a doctype-agnostic `harvest()` (bet stays its default
  driver, `_currency`/`_published` now take the full entry rather than a
  pre-picked `pdf_fil`), and `accommodanda/forarbete/rskr.py` drives it for
  riksdagsskrivelser — the chamber's decision letter to the government, the
  last hop of the prop→bet→rskr chain every SFS register cites per amendment
  ("rskr. 2007/08:159"), already minted by the FORARBETEN grammar as
  `rskr/<rm>:<beteckning>`. Same **basefile = `"<rm>:<beteckning>"`** shape.
  Unlike `bet`, the body is **not** the filbilaga PDF — an rskr is a few
  boilerplate sentences ending in the talman's (and, in the modern layout, a
  countersigning tjänsteman's) signature, all of it in the API's own small
  HTML rendering, so the downloader stores that HTML and skips the PDF
  entirely. Also no planned/published upgrade cycle: every feed entry is
  published and final (an rskr records a decision already taken), so the
  watermark runs with the default window. `parse.rskr_body()` turns the HTML
  into the ordinary block stream (everything after the ort/datum line tagged
  `signatur`), so `bet`/`rskr` parse through the same forarbete `parse.py`
  pipeline. Wired into `fa_harvest` as scope `"rskr"` alongside `bet` (neither
  supports `--only`; both support `--riksmote`). No frozen legacy corpus
  covers it. These signer names are what `sfs/asgit.py`'s `history-as-git`
  export uses for commit authorship (§3d).

### 7c. Wiki value-add — kommentar + begrepp ✅ (first cut)

The hand-authored MediaWiki content (the dump in
`site/data/downloaded/mediawiki/`) imported as **two ordinary sources**, proving
the manually-written value-add flows through the identical artifact → catalog →
inbound → render pipeline as the machine-extracted sources.

- ✅ **Shared wikitext parser** `accommodanda/lib/wikitext.py`: MediaWiki XML →
  blocks; each prose paragraph → inline runs combining `[[wikilinks]]` (→
  `begrepp/<Concept>`) **and** the citation engine's law/case/förarbete links,
  non-overlapping. Author byline + `[[Kategori:]]` extracted.
- ✅ **`kommentar` — an annotation layer, not a page source.** Wiki SFS
  commentary (`wiki/parse.py::kommentar_artifact`): each `== 21 kap 1 § ==`
  heading → a section keyed on the statute fragment (`K21P1`), prose
  citation-scanned with the commented law as the relative-reference base (so "7
  kap 3 §" resolves to the same law, "tryckfrihetsförordningen" / "NJA 1990 s.
  510" to their docs). **It has no page tree of its own** (no `/kommentar/`, not
  on the frontpage/browse, not an inbound citer — `render_kommentar` removed,
  `catalog.inbound` excludes it): instead the commentary prose is shown
  **side-by-side in the statute paragraph's context rail** when that paragraph is
  in focus. `page._kommentar_indexes` builds `{(law_uri, anchor) → prose}` from
  the kommentar artifacts; `Rail._commentary` renders it as the rail's top
  "Kommentar" section (with author byline). 212 commentaries. `test/test_site.py`
  (`test_commentary_shows_in_paragraph_rail_not_as_page`).
- ✅ **`begrepp` source** `::begrepp_artifact` — concept/keyword glossary,
  published at `begrepp/<Name>` (MediaWiki ucfirst). `[[wikilinks]]` weave the
  concept graph; the concept page's inbound shows everything (laws, cases,
  förarbeten, commentary, other concepts) that references it. 565 pages, **468
  concepts have inbound**. DV `nyckelord` render as links to their concept page
  where one exists (the case→concept half).
- Wired: `lagen {kommentar,begrepp} parse`; `catalog.{kommentar,begrepp}_document`;
  `render_{kommentar,begrepp}`; `doc_relpath` → `kommentar/` + `begrepp/` trees;
  inbound groups "Kommentar"/"Begrepp"; inbound entries now link to the citing
  *pinpoint* (`from_uri#anchor`). `test/test_wiki.py`.
- ✅ **Concept synthesis — the begrepp layer is now the union of extracted terms
  and wiki concepts.** Two relate-time additions (`catalog.subject_links` +
  `synthesize_concepts`, wired into `cmd_relate`):
  - **case↔concept edges**: a court decision's `nyckelord` (metadata, so the
    inline-link walk missed them) now emit `dcterms:subject` edges to
    `begrepp/<Name>`, so a concept page lists the cases tagged with it.
  - **stub concept nodes**: every concept the corpus *references* — an SFS defined
    term (`dcterms:subject`) or a nyckelord — that has no wiki page gets a stub
    `documents` row (empty `path`, rendered as a synthesized shell whose content
    is its aggregated inbound: what defines and tags it). So a defined term
    without a hand-written description is still a real node, links to it stop
    dangling, and DV nyckelord become live links. A `RE_CONCEPT` name filter drops
    the formula/parenthetical junk the SFS extractor emits (`*/k/ …`,
    `(av personuppgifter)`) — on the real catalog **~5,690 clean stubs vs 520
    rejected** (SFS-defined alone, before nyckelord). `wiki/render.py`'s `render`
    puts a description-less concept's occurrences in the **reading column**
    rather than the context rail (2026-08-10): the page is an index of where the
    term is used, not an article whose text is missing, so the rail would leave
    the column empty beside it. The lede counts the rättskällor
    (`EDITORIAL_KEYS` excludes our own commentary and other concept pages), the
    statute group is headed **Legaldefinitioner** there (`GROUP_LABEL` — the
    rail's "Lagrumshänvisningar hit" names the margin it was written for), and
    the rail's JSON island is suppressed so the same list is not printed twice.
    `generate_site` renders the path-less stub.
    `test/test_wiki.py`. **EU defined terms now promoted too**
    (`catalog.definition_links`): each Swedish EU act's definitions-article point
    that `defines` a term emits a `dcterms:subject` edge to `begrepp/<Name>`,
    anchored to the point — so an EU term joins the shared namespace (`ränta`,
    `royalties`) and the concept page shows which EU act defines it, while the
    act-local term-use interlinking (a use → the act's own definition point) is
    untouched. Swedish manifestation only (the namespace is Swedish); English acts
    excluded. Verified on 32003L0049 → Ränta/Royalties concepts with the act
    inbound.
  - **What each act says the term means** (`catalog.definition_sentences` -> the
    `definitions` table; `wiki/render._definitions`). The link alone tells a
    reader where to look, not what they would find, so relate stores the
    *defining sentence* beside the edge and the concept page prints it under the
    title and the curated description -- in the reading column, and the defining
    acts leave the rail (`DEFINING_KEYS`, `Rail.drop_document_sections`) so the
    same list never appears twice. Stored at relate because it already has the
    artifact open: a term defined in a hundred acts would otherwise open a
    hundred artifacts, on each of ~28,900 concept pages. **41 440 definitions
    over 5 140 acts** (14 401 SFS, 27 039 eurlex), on 22 133 concepts.
    - The two corpora state a definition in different places. An eurlex
      definitions-article point is the definition whole -- except where the body
      is a sub-list and the point's own text stops at the colon (NIS2 art. 6.1),
      where the sub-list is taken with it. An SFS node is a whole stycke and
      often holds more: brottsbalken 10 kap. 8 § 1 st runs "Fullgör man ej …
      dömes för fyndförseelse till böter. Underlåter man …", so the unit stored
      is the **sentence** carrying the term (`lib.text.sentences`, which already
      survives "10 kap. 8 §", "bl.a." and "m.m."). A definition written as a
      two-column table row is stored whole -- 336 of them, where the term cell
      alone states nothing.
    - A definition the source left empty ("total tillåten fångstmängd (TAC): ",
      32015R0104 art. 3 f) keeps its row with an empty sentence: the act does
      define the term and the page still has to list it, there is just nothing
      to quote. Dropping those rows instead hid 863 concepts' occurrences.
    - A definition folds onto the **canonical** concept with the link beside it
      (`canonicalize_concepts`). Left behind it strands on a page nobody
      renders: the wiki page *Risken* absorbs the form *Risk*, so *Risk*'s 31
      legaldefinitioner had no page while the page had none of them. 1 077 rows
      over 494 concepts were in that state.
  - **An amending instruction is not a defined term**
    (`eurlex.definitions._is_amendment`). An amending act writes its
    instructions in exactly a definition's shape — "Artikel 6 ska ersättas med
    följande: *&lt;the whole replacement article&gt;*" — and 2014/48/EU's article 1
    is headed "Definitioner av vissa termer", because that is the heading of the
    article it *inserts*. Every instruction under it read as a definition, and
    2026/1183 art. 1.7 became a 47 kB "definition" of the concept "Artiklarna
    67–112 ska ersättas med följande". A definiendum is a noun phrase and an
    instruction is a clause whose verb is a ska-passive, so the two separate on
    where the "ska" sits: a term carries one only inside a relative clause
    ("sammanlagt belopp som ska betalas av konsumenten", "kemikalie för vilken
    exportanmälan ska ske"). Measured over the corpus's 27 289 defined terms:
    **268 contain "ska", the test rejects 250 and every one is an instruction**;
    the 18 it keeps are real terms, and no instruction reaches it without a
    "ska" (the four heads carrying another amending verb are genuine terms). The
    reparse changed 36 of 64 038 acts. `test/test_eurlex_definitions.py`.
  - **"Med X avses Y" without "i denna lag"** (`sfs.begrepp.re_loptextdef`).
    The löptext trigger required the tail "i denna lag/förordning/balk", and
    drafting as often writes "i detta kapitel", "vid tillämpning av 5 §" or
    nothing at all -- säkerhetsskyddslagen 1 kap. 2 § states "Med
    säkerhetsskyddsklassificerade uppgifter avses uppgifter som rör
    säkerhetskänslig verksamhet …". Requiring the tail lost **3 558 definitions
    in 1 427 acts**. What dropping it lets in is one shape, "Med" opening an
    adverbial ("Med undantag av de fordon som avses i …"), excluded by naming
    its two heads -- 12 of the 3 558 -- rather than by a rule about
    prepositions, since "stöd till start av näringsverksamhet" is a defined term
    and reads the same. The definiendum is then trimmed of the article in front
    of it (186) and the scope qualifier behind it ("Med dotterbolag enligt
    första stycket 3 avses …", 94), both of which would mint a begrepp page
    under a name no one looks up. `test/test_sfs_begrepp.py`.
  - **Concept canonicalization** (`lib/concepts.py` + `catalog.canonicalize_concepts`):
    a hand-rolled, **corpus-aware** Swedish noun de-inflector collapses inflected
    surface forms onto one concept (`Näringsidkare/Näringsidkaren/Näringsidkarna`),
    so two laws defining the same term in different inflections no longer mint two
    nodes. It never strips a bare `-are` (an agent *base*, so `Domare` ≠ `Dom`,
    `Företagare` ≠ `Företag`) and merges only onto a base that is *itself observed*
    (resolving the `-arna` ambiguity). Canonical display = a wiki form (the wiki
    uses base form) else the most base-like member; casing/whitespace folded; a
    hand-edited `begrepp_aliases.json` forces synonym merges and blocks wrong ones
    (`keep_distinct`). The relate pass clusters all referenced concepts, **remaps
    the variant link targets** to the canonical and records the fold in a
    `concept_alias` table; `render` (`Site.resolve`) folds a variant uri baked into
    an artifact onto the canonical page. On the real catalog: **355 forms collapse
    into 347 concepts, 0 wiki URIs changed.** `test/test_concepts.py`,
    `test/test_wiki.py`.
  - **`find_definitions` span fixes** (`sfs/begrepp.py`): the two extractor
    mis-*bindings* (not noise) fixed at source — a colon-list definition sweeping a
    formula prefix (`*/k/ utjämningsbelopp` → `utjämningsbelopp`), and a
    parenthetical *clarifier* captured instead of its head (`Behandling
    (av personuppgifter)`: the head is the term, not the paren — distinguished by
    the paren starting with a preposition, so the `dödas (dödning)` coinage still
    works). A term never leads with a preposition or contains `*`/`/`; `RE_CONCEPT`
    is now just a thin backstop. `test/test_sfs_begrepp.py`.
- ✅ **Authoring layer:** the authenticated inline editor writes the git-backed
  kommentar/begrepp/site markdown through a per-user edit cart, commits with
  editor attribution and runs a scoped rebuild (§6).
- 💤 **Product follow-ups (not rewrite blockers):** defined-in-commentary
  resolution; optionally embed
  commentary prose in the reading column rather than only the context rail;
  topic taxonomy (`Lagar inom …`). These are value-add/product work, not
  missing rewrite infrastructure.

### 7d. EU vertical (EUR-Lex / CELLAR) ✅ (first cut)

The fourth vertical and the second cross-border leg of the killer feature — the
~30k CELEX citations §6 could only bounce to EUR-Lex as external links now
resolve to internal pages. EU treaties, regulations/directives, and CJEU case
law, keyed by **CELEX** (the basefile throughout).

- ✅ **Downloader** `accommodanda/eurlex/download.py` — harvests the Publications
  Office **CELLAR** repository (the one complete source: the bulk dumps cover only
  in-force sector 3, the Open Data portal only OJ from 2004). Three sectors by
  CELEX leading digit — 1 treaties, 3 secondary law (R regulations / L
  directives), 6 Court of Justice. **Discovery via the auth-free CELLAR SPARQL
  endpoint** (no 10k-result cap, unlike SOAP) — *which CELEX exist* is the hard
  part, so no number-guessing. Per document the best manifestation per language
  (**fmx4 > xhtml > html > pdf**) + its content-item URL. The per-document CDM
  tree-notice fetch (~10s each — the dominant harvest cost; a judgment's notice
  runs to 500k+ triples across 24 languages for the ~6 edges used) was replaced by
  **batched SPARQL selection queries** (work→expression→manifestation→item edges,
  one query per year-slice of CELEX; `notice.ttl` synthesized from a metadata
  query). Incremental (watermark + skip-on-disk) / `--force`; swe+eng default. A
  registered SOAP account (`EURLEX_USERNAME`/`EURLEX_PASSWORD`, env-only) gives a
  secondary `--source soap` enumerator as a cross-check for the unmetered but
  SLA-less SPARQL endpoint. `lagen eurlex download [treaties|acts|caselaw]
  [--since YYYY-MM-DD] [--lang swe,eng] [--source sparql|soap]`. **Content-format
  fallback** (2026-06-27): the richest *type* is not always the richest *content* —
  some scanned old judgments (CC/CJ/TJ, ~1993–2002) expose an `fmx4`-typed
  manifestation whose item is a TIFF *image*, not Formex XML. `store_document` now
  validates each fetched item against its declared format (`_content_ok`) and falls
  to the next candidate type (`fmx4 → xhtml → html → pdf`, ranked by `_ranked_types`),
  so the real text manifestation is stored. Recovered 149 judgments that previously
  died in parse with `ParseError: line 1, column 0` (ElementTree on TIFF bytes).
  **Multi-part Formex** (2026-07-24): an act published across several OJ files
  (the main text plus one file per annex) exposes *one item per part*, and no
  single item is the document — taking the first stored 2004/18 as its "BILAGA I"
  (14 kB, 0 articles) and the Charter of Fundamental Rights as its table of
  contents. Such a manifestation is now fetched **whole**, as one zip
  (`ZIP_ACCEPT` on the manifestation URL, `manifestation_url`), which is exactly
  the `.fmx4.zip` bundle the bulk importer produces and `parse.formex_members`
  already reads in order. Only Formex takes this route — a zip is not readable
  as xhtml/html/pdf content.
- ✅ **Bulk import** `accommodanda/eurlex/bulk.py` — `lagen eurlex unpack-bulk
  <dir|zip>` unpacks an official CELLAR bulk legislation dump (per-format zips:
  MTD metadata + EN/SV × FMX/HTML/PDF) into the *exact* per-CELEX layout the
  harvester produces, so `parse` treats the works as downloaded docs (no network).
  Keyed by the opaque cellar work UUID; the CELEX comes from the metadata rdf
  (`resource_legal_id_celex`). Keeps the single best manifestation per work +
  language (fmx4 > html > pdf, mirroring the live downloader). Latest cut keeps
  only sector-3 R/L (drops decisions + minor types, classified via
  `model.doctype`, filtered *before* the watermark so excluded acts don't advance
  it).
- ✅ **Parser** — `accommodanda/eurlex/{model,parse,parse_html,parse_pdf,lang}.py`.
  The parsers first produce ordered anchor-bearing `Block`s (parts/titles/
  chapters/articles/paragraphs/points + recitals + judgment paragraphs/ruling),
  then `eurlex/structure.py` materializes their containment hierarchy. Three
  format-precedence routes produce the **same artifact shape**:
  - `parse.py` — **Formex** (the richest manifestation), roots `ACT`
    (regs/dirs/decisions/treaties) + `JUDGMENT` (CJEU). Inline markup is
    flattened; footnotes become `note` blocks. A `.fmx4.zip` bundles annexes as
    separate files; they are embedded after the main act (lowest sequence).
    A `GENERAL` root carries the same act one or two levels deeper — the
    preamble and enacting terms wrapped in a `CONTENTS`, either directly
    (2004/18) or inside a further `GR.SEQ` (the Charter) — so `parse_act_body`
    descends through the wrappers; reading `ENACTING.TERMS` alone left both of
    those with no articles at all. A table row keeps its *interior* empty cells
    (trailing ones are dropped), because in a jämförelsetabell the column a
    value sits in is what identifies which act it belongs to.
  - `parse_html.py` — **OJ HTML/XHTML** for the many older docs with no Formex;
    the stable OJ CSS classes (`ti-art`, `sti-art`, `normal`, `note`, …) map onto
    the same Block kinds. Pre-OJ loose `<txt_te>` HTML falls back to
    text-inferred structure.
  - `parse_pdf.py` — **PDF** last resort via `pdftohtml -xml` (positioned text →
    reflow → structure inferred from text); an OCR sidecar handles scanned PDFs
    with no text layer.
  - `lang.py` — localized structural vocabulary (Article/Artikel, TITLE/AVDELNING,
    enacting formula, visa/recital) for the two text-inferring parsers; Formex
    needs none (tagged). Reference *syntax* stays in the citation engine.
- ✅ **URI minted to the citation-target form** (`model.BASE` =
  `https://lagen.nu/ext/celex/{CELEX}`) — the same language-neutral CELEX URI
  EULAGSTIFTNING/EURATTSFALL citations mint, so an EU act and any citation to it
  agree by construction (the DV/forarbete URI lesson, third application). Body
  scanned with the shared engine (EU-leg + CJEU) → inline links. CELEX minting in
  `lagrum.py` hardened alongside.
- ✅ **Wired through build + catalog + render**: `lagen eurlex
  {download,unpack-bulk,parse}` (a `Source` with a `harvest` discovery sweep +
  `unpack-bulk` action), `catalog.eurlex_document` (source `eurlex`, doctype kind),
  `eurlex/render.py`'s `render` (doctype-labelled CELEX page), `page_relpath` routes
  `ext/celex/…` → `eurlex/{celex}.html`. **The payoff:** a CELEX citation to an act
  we've now parsed renders as a **local** link (`site.has` wins over
  `is_external`); only *un-parsed* EU acts still fall back to the external EUR-Lex
  href — exactly the §6 "becomes live once parsed" promise, now for EU law.
- ✅ **Corpus on disk:** ~64k EU documents parsed to artifacts
  (`site/data/artifact/eurlex/`, 63,902 catalog rows as of 2026-07-26, after
  the repealed-acts backfill); mostly Formex, HTML fallback for older acts,
  PDF as last resort. `test/test_eurlex_parse.py` (Formex, 11 tests), `test/test_eurlex_html.py`
  (HTML/PDF fallback, 5).
- ✅ **Directive lineage — the EU-act correspondence layer**
  (`eurlex/correspond.py`, run by `eurlex parse`, 2026-07-24/25). The case law a Swedish paragraf needs is usually older than the
  directive it transposes: LOU 13 kap. 1 § transposes article 57 of 2014/24, but
  the practice on exclusion grounds is about 2004/18 art. 45 and, before that,
  92/50 art. 29. A recast publishes the mapping itself, in its jämförelsetabell
  annex, so this is **mechanical** — the eurlex counterpart of `sfs
  table-correspond`, no LLM — and because it is the act's own structured data,
  it is extracted **at parse time into the act's artifact** (`correspondence`
  key, rule:artifact-is-truth) rather than authored into a layer: no action to
  remember, nothing to fall out of step with a re-parse, and a no-op for ~98%
  of sector-3 acts and every judgment. Three properties of the source shaped
  the reader:
  the table is located by its **header row**, not the annex heading (only ~20%
  of these tables sit under a heading named *Jämförelsetabell*; 2014/24's is
  `BILAGA XV`, and the word appears 2 600 nodes away in article 90);
  **orientation varies** and reversed is the norm (424 of 456 tables put the
  repealed act in column 1), so the self column is found by wording, admitting
  all eight phrasings the corpus uses (`Detta direktiv`, `Den här
  förordningen`, `Föreliggande direktiv`, …); and **the old side's links are
  wrong** — the citation engine resolves "Artikel 12" in any cell against the
  act being parsed — so article numbers come from cell *text* only. A header
  column the engine left unresolved (Euratom acts, `(EU, Euratom)` numbering)
  is read from its designation via `lagrum.celex_uri`, with the pre-2015 "nr"
  settling number/year order. There is no authored layer and no `relate`-time
  load: `catalog._index_document` writes the pairs straight from the artifact's
  `correspondence` key into `directive_correspondence` as it indexes each act,
  so the table stays incremental with the artifact tree rather than a post-pass
  re-reading every file. `catalog.predecessor_atoms` walks it transitively
  (`LINEAGE_DEPTH = 3`, the procurement chain 2014/24 → 2004/18 → 92/50 &
  93/36-38 → 71/305 & 77/62, keeping the table's sub-article precision where
  it has any) under `catalog.caselaw_anchored`, which assigns each judgment
  citation to the paragraf whose genomförande pinpoint matches it best —
  each carrying the hop it came by, which `page.eu_caselaw_margin` names
  ("om artikel 15 i 92/50/EEG, motsvarar artikel 79"). **Measured:** 238 of the 310 LOU paragrafs with a
  genomförande statement now show older EU case law they did not have. Corpus
  potential: 386 acts (122 directives, 264 regulations) carry a readable table,
  ~27k article pairs. **Both figures are floors, measured against artifacts
  parsed before the `_emit_table` alignment fix**: a corpus-wide `lagen eurlex
  parse --force` has not run yet, so every multi-column table (2010/75,
  2009/138, 2006/112 …) still has rows whose cells slid left, and those rows
  are dropped rather than mismapped. Re-run the parse before treating the
  numbers as final. The chain stops at 2004/18 for procurement — 93/36 and
  93/37 do have jämförelsetabeller, but their only manifestation is pre-2003
  HTML whose tables are a literal `>Plats för tabell<` placeholder (42 acts
  corpus-wide are in that state), and 2004/17's likewise, so LUF gets one hop.
  Codified case law is a genuine limit, not a gap: 2014/24 art. 12 (in-house)
  maps to "—" because it had no predecessor article — Teckal was the source —
  so *that* relation can never come from a correlation table.
- ✅ **Citation-driven backfill** (`lagen eurlex backfill [<sector-digit>]
  [--limit N]`, `catalog.dangling_targets`, 2026-07-24) — the want-list the
  lineage layer above needs filled. The sector-3 stock came from a CELLAR bulk
  dump, which ships only acts *in force*; every act repealed since is missing
  while cited from everywhere (2004/18 alone: 6 979 references from 790
  documents we do hold). Re-running discovery over all of sector 3 to reach
  them would fetch a hundred thousand acts the corpus doesn't need; the
  citation graph already names exactly the ones that matter, so
  `dangling_targets` ranks every link target with no `documents` row by
  inbound reference count (the top 500 carry 76% of all dangling references),
  and `backfill` downloads down that list, most-cited first, through the same
  `download.download_document` an ordinary harvest uses. A CELEX with no
  Swedish/English manifestation (a pre-accession act never translated) is
  reported, not retried.
- ✅ **Defined-terms extraction + in-act interlinking** (`eurlex/definitions.py`).
  Modern EU acts gather their definitions in a dedicated "Definitions" article — an
  intro ("the following definitions apply") then a numbered list of `term:
  definition` points. Each such point is read as a definition of its lead term and
  **anchored `<article>.<point>`** — the very fragment `celex_uri` mints for
  "artikel 6.15 i …", so a pinpoint citation and the definition it points at agree
  by construction. A definition is act-local, so every later **use** of a defined
  term becomes a link to that act's own definition point (`lib/assets/popover.js`
  shows the definition point on hover, fetched from the act's own rendered page —
  §6): suffix-tolerant (Swedish inflects — "sårbarhet" defined matches
  "sårbarheter" used) and longest-term-first (a phrase wins over a term nested in
  it); a citation wins wherever a term-use overlaps it. The new link flavour rides
  a `kind="term"` field on `Ref`/the inline run (`lib.lagrum`), so the renderer can
  style it apart from a cross-document citation. Scope: the dedicated
  definitions-article pattern (covers NIS2 + the bulk of modern acts); inline "'X'
  means …" definitions in running prose not yet detected.
  `test/test_eurlex_definitions.py`.
- ✅ **EU case naming** (`lib/eucasenaming.py`, the EU mirror of DV's
  `lib/casenaming.py`). `case_number` derives the court's own case number from a
  caselaw CELEX (`62018CJ0311` → "C-311/18", also T-/F- courts, an AG opinion
  sharing its judgment's number); on top, a curated **usual name** (`given_name`,
  e.g. "Schrems II") sourced from a shipped snapshot, since neither EUR-Lex nor
  CELLAR carry one as data (only the full parties string) — the Court publishes
  no such name, so it is harvested from **Wikidata** (`eurlex/casenames.py`,
  property P476 CELEX → item label) into `eurlex/data/casenames.json`
  (`NAMEDEUCASES` in `lib/datasets.py`), analogous to `dv/data/namedcases.json`.
  Coverage is famous cases only (~245); every other case falls back to the bare
  case number. `case_name` (usual name or case number) is stamped onto a
  judgment artifact at parse time as its page heading — replacing the useless
  Formex "Domstolens dom (…) den …" title, which moves to a "Titel" metadata row
  — and `case_citation` ("C-311/18 (Schrems II)") labels it wherever it is cited
  from elsewhere, feeding the inbound panel (`page.INBOUND_GROUPS`) —
  since 2026-07-27 its own "EU-domstolens praxis"/"Generaladvokatens förslag
  till avgörande" groups (`page.INBOUND_KIND_GROUPS`, split off from the
  legislation-citing "EU-rätt" group by doctype). Refreshed via `lagen eurlex
  casenames`.
  `test/test_eucasenaming.py`, `test/test_eurlex_casenames.py`.
- ✅ **Advocate General opinions and orders classified apart from judgments.**
  `model.doctype` splits a sector-6 CELEX by its two-letter document code —
  CJ/TJ/FJ judgment, CC/CV/CP an AG opinion (*förslag till avgörande*),
  CO/TO/FO an order — instead of treating everything as a judgment; an
  opinion previously fell through the Formex `ACT` branch and rendered as
  footnotes alone. `parse.parse_opinion` reads the opinion's own `CONCLUSION`
  structure (opening prose, numbered `NP` opinion paragraphs, `GR.SEQ` section
  groupings — the same shape a judgment's contents take). The browse facets
  (`lib/facets.py`) file a court order with its judgments (a ruling, not
  separate prose) and list an opinion under its own "Generaladvokatens
  förslag" bucket only while no judgment for the same case exists yet
  (`_drop_opinions_with_judgment` — once the judgment lands, the opinion is
  reached from it and from search, not the index).
- ✅ **Fördrag (EU primary law) browse groups by treaty family, not year.**
  A treaty's CELEX year is the year of a later consolidated republication, not
  a reader's handle on it — `lib.facets._treaty_family` reads the CELEX
  document-type letter instead (E = TEU/TFEU, M = TEU-side, P = the Charter,
  A = Euratom, U/D/C/L amending treaties, the enlargement letters accession
  treaties, `ME` the combined consolidated publication), in a curated reading
  order (TEU/TFEU/Charter first). The curated Swedish names a founding/
  consolidated treaty needs (it carries no extractable short title of its
  own) now live in `eurlex/data/treaties.json`, read through the new
  `lib.labels` module (§5) rather than ad hoc in the renderer.
- ✅ **Genomför-direktiv edges wired** — `forarbete/kommentar.py`'s *implements*
  relations (a proposition's författningskommentar stating which EU directive
  article a provision transposes — "Paragrafen genomför artikel 21.1–21.3 i NIS
  2-direktivet") now flow through the whole derived layer. The förarbete parse
  stage attaches them to the artifact as a typed `implements` section (artifact =
  source of truth); `catalog.implements_links` emits one edge per transposed
  article (`rpubl:genomforDirektiv` → `ext/celex/{CELEX}#{article}`), anchored to
  the förarbete's `#sid{page}` so inbound pinpoints the page. **The payoff:** an EU
  directive article's page now shows which Swedish förarbete implements it (e.g.
  directive 2013/11/EU art. 18 ← prop. 2014/15:128 s. 56), and the proposition
  page renders a **"Genomför EU-direktiv"** panel linking each statement to the
  directive article. Verified end-to-end on the real corpus (prop 2014/15:128 → 7
  statements → directive articles light up). `test/test_site.py`.
  - ✅ **Extended to `fm` (förordningsmotiv).** The extraction guard was
    prop-only ("only the bill text is closest to the enacted law"); widened to
    `{"prop", "fm"}` because an fm is published *alongside* the förordning it
    enacts, so its "Förordningen genomför … direktivet" statement is just as
    authoritative. An fm writes its författningskommentar at heading level 3
    (unnumbered, prop props it at level 1) and names its förordning in the
    leading title rubriks rather than a prop-style "Förslaget till lag om
    ändring i…" level-2 heading, so `find_kommentar`/`fm_law` needed fm-aware
    section-location and law-context logic. Same pass fixed the alias-binding
    lookback: a directive alias used to resolve against a fixed 400-char
    window before the `(…direktivet)` parenthetical, which a long "senast
    ändrat genom <amendment list>" clause could push past the real subject
    directive; now scoped to the **defining sentence** (`_sentence_start`),
    which also corrected a real prop misparse, not just an fm-only edge case.
    `test/test_forarbete_kommentar.py`.
- ✅ **Genomför statements pinned to the SFS paragraf** — the cross-document join
  the parser couldn't make, resolved at *relate* time (`forarbete/genomforande.py`,
  a vertical module that reads the statute corpus through the shared catalog,
  never importing the SFS vertical). Each statement's författningskommentar rubrik
  resolves to an SFS law two ways: a **"lag om ändring i X (YYYY:NN)"** rubrik
  names the amended act directly; a **new law** (named by title only) is matched
  against the catalog's SFS title index, with ties — a new law replacing an older
  same-named one — broken by the SFS whose **ikraftträdande is the closest date
  after the proposition** (user rule). The commented paragraf becomes the SFS
  fragment (`K{kap}P{par}`/`P{par}`). Each resolved statement is stored in a
  `genomforande` table (provenance: the proposition) *and* as an
  sfs-paragraf → directive-article edge, so **the statute paragraf's margin shows
  which EU article it transposes** ("Genomför EU-rätt") and the **directive
  article's inbound now shows the implementing statute** (alongside the
  proposition). Conservative on a published identifier: exact normalized-title
  match, unique-or-tie-break-only, no fuzzy fallback. Verified end-to-end (prop
  2014/15:128 → "lag om alternativ tvistlösning…" → SFS 2015:671, 8 paragrafs
  pinned). `test/test_site.py` (Case 1 / Case 2 unique / Case 2 tie-break).
- ✅ **Per-paragraf författningskommentar in the statute rail** — the FK's
  commentary *text* (not just its genomför edges) extracted per paragraf
  (`forarbete/fk.py`): the chapter located by content (never rubrik levels,
  which in-FK "1 kap." pseudo-headings corrupt; the heading itself may be lost
  to a stycke — prop 2017/18:269), sliced per law (numbered/unnumbered/
  stycke-demoted law rubriks) and per paragraf (marker recovery incl. combined
  "9 och 10 §§" and mid-stycke markers), lagtext split from commentary by
  opener formula across the three FK styles (lagtext quoted / bare marker /
  marker inline), group comments ("I paragraferna finns …", "De ändringar som
  föreslås …") annexing their quoted run. Stored as the prop artifact's
  `kommentarer` section; `fk.resolve` pins entries to statute anchors at
  relate time (`fk_kommentar` table, law resolution shared with
  `genomforande`); the statute paragraf's rail shows each prop's comment
  ("Författningskommentar", newest first, `#sid`-pinpointed provenance) —
  including prop 2017/18:89, which the legacy metrics-driven CommentaryFinder
  misses entirely. On the **proposition's own page** the commentary is
  highlighted too: `extract(mark=True)` stamps each commentary block
  `fk: <entry-no>` in the artifact, and the renderer wraps each entry's run
  in an `.fk-komm` box (light blue background + border, one box per
  paragraf's commentary), leaving the quoted lagtext plain. Rules locked to the nine-prop curated corpus
  (`test/test_forarbete_fk.py`, `test/test_site.py`). Known limitation: a
  law-level comment spanning several chapters ("De ändringar … i lagen" over
  1 kap. + 2 kap. quotes) anchors only its own chapter's run.
- ✅ **Formex annex parsing:** multi-file manifestations embed each annex after
  the main act, with stable `bilaga-N` anchors; headings, paragraphs, lists and
  tables are retained and tested in `test/test_eurlex_parse.py`.
- ✅ **Metadata cross-check (2026-07-19):** there is no legacy EU oracle (the
  old code never supported EUR-Lex beyond an experimental module), so
  `tools/golden_eurlex.py` validates the carried fields — CELEX, date, title,
  OJ ref, ECLI, doctype — against authoritative CELLAR metadata itself,
  frozen to a retained snapshot (`test/files/eurlex/cellar-snapshot.json`,
  502 documents deterministically stratified over treaties / regulations /
  directives / corrigenda / judgments; decisions and consolidated acts are
  outside the corpus by harvest design). Change-detector + adjudication
  ledger (the golden_sfs pattern); `compare --reparse` exercises the current
  parser rather than the stored artifacts. The run drove four parser fixes —
  judgment dates were the *referral* date (JUDGMENT.INIT's first DATE) rather
  than the delivery date in TITLE; missing/impossible dates and corrigendum
  dates now come from the notice.ttl work date already on disk; OJ numbers
  unpadded ("L 042"→"L 42"); page-long misextracted titles rejected
  (`TITLE_MAX`) — and ends at zero unexplained differences. Artifact dates
  are now dashed ISO; the corpus re-parse (EURLEX_CODE changed) folds into
  the finding-6 acceptance run.
- ✅ **Truncated rubriks + FK-bound unification (2026-07-19, finding 04):**
  `join_dangling_rubriks` re-attaches the statute name a flattened PDF dropped
  off a "Förslag till lag om ändring i" rubrik (the following lowercase-led
  line, a mis-classified rubrik continuation, the all-caps era style, and a
  name glued onto the next paragraph are all handled; 115 of the 126
  corpus-wide re-join, fixture-locked). `kommentar.extract` now bounds on the
  unified `fk_span` (moved into kommentar.py; fk.py imports it): validated
  corpus-wide, the implements extraction grows from 2,000 to 2,972 edges —
  the gains exactly the in-FK-pseudo-rubrik truncation class (2012/13:155:
  5 → 122) — while the handful of "lost" edges were appendix-derived
  (Lagrådet's opinion in a bilaga), i.e. false authoritative-commentary edges
  the old level-1 bound overran into.
- ✅ **`ai-genomforande` — opt-in LLM directive→paragraf mapping (2026-07-23):**
  the mechanical `implements` extraction only catches the formulaic "Paragrafen
  genomför artikel N i direktivet" sentence; most transpositions are stated
  less rigidly ("Bestämmelsen motsvarar artikel 23.4", a whole-law "lagen
  genomför direktivet" naming no article) or not stated at all for purely
  national paragrafer. `forarbete/aigenomforande.py` (`lagen forarbete
  ai-genomforande <prop-basefile> [<CELEX> ...]`, directives defaulting to the
  prop's own mechanical `implements`) reads the per-paragraf `kommentarer`
  entries `fk.py` already stamped on the prop artifact, *one LLM call per
  proposed law* (huge FKs chunked at a char budget) — whole-law context at a
  fraction of per-paragraf calls. Paragraf identity is never asked of the
  model: each candidate entry (commentary mentioning "artikel"/"direktiv")
  gets a stable id the model returns, so it can only pick a real paragraf;
  each directive is tagged A, B, … with its real article inventory (read from
  the eurlex artifact), so a multi-directive prop (financial omnibus,
  NIS2+CER) is one pass. Every mapping is validated: known id, known tag,
  every cited article reduced via `kommentar.parse_articles` (bare numbers
  and dotted/lettered pinpoints alike — the base validated against the
  inventory, the pinpoints kept for the margin) and a non-empty supporting
  quote occurring in that entry's commentary; failing items are dropped, not
  stored — the mapping is many-to-many and partial by design, not 1:1.
  A mapping may carry an optional Swedish-side pinpoint (`"sfs": "S1"` /
  `"S3N2"`, the SFS element-id syntax, 2026-07-24) when the FK scopes the
  claim to a stycke/punkt: shape-checked at authoring (malformed values
  disregarded, never dropping the mapping), existence-checked at relate time
  against the published law's minted element ids (a "S5" on a two-stycke
  paragraf is disregarded, the paragraf-level reference stands), stored in the
  `genomforande` table's `sfs_pinpoint` column and rendered as citation prose
  ("första stycket genomför …") in the statute margin.
  Output is a `.ann`
  layer in the curated store (`lib/annstore.py`), a richer superset of the
  artifact's mechanical `implements`; `genomforande.resolve` now prefers an
  authored layer's edges over the mechanical ones per covered directive,
  keeping mechanical edges for any directive the layer didn't map
  (`genomforande_layers` globs the förarbete annstore subtree, joined to its
  prop by the recorded prop uri, same pattern as the `.corr` layers; the
  relate `__corr__` watermark now also covers these förarbete `.ann` layers).
  Own prompt file `genomforande_prompt.txt`; the LLM is called only from this
  action, never from parse/relate/generate — same discipline as `eurlex
  ai-annotate`/`sfs ai-correspond`. Correctness was benchmarked against an
  adjudicated golden corpus for the 2025/26 slice (`.ann.golden` layers
  beside the `.ann` files in the annstore, built by the six-model benchmark
  archived in `tools/aigenomforande-bench/`, scored on demand by its
  `evaluate.py` — bench data, not a test gate);
  `test/test_forarbete_aigenomforande.py` covers the non-LLM core and is
  self-contained (no annstore/golden dependence).

### 7e. Myndighetsföreskrifter vertical (agency regulations) 🚧

Binding regulations issued by ~100 agencies into their own författningssamling
(FFFS, AFS, NFS, …). The value-add: a föreskrift's **`bemyndigande`** points into
SFS at the empowering paragraf — a *new* edge type (statute → regulation) that
makes a law's page list the regulations issued under it — plus `genomforDirektiv`
(→ EU) and `upphaver`/`andrar` (the intra-fs amendment graph). A föreskrift is
now also a citation *target* in its own right (`FORESKRIFT`, below); the
FORESKRIFTER grammar was never implemented in the old engine (§5 💤), so this
is greenfield, not a port.

- **Landscape (poked, 2026):** no central API — lagrummet.se is a link directory,
  the old rinfo aggregation is dead — so harvest is irreducibly per-agency. But the
  *publishing architectures* are few, so an agency is **configuration over a shared
  engine**, not a bespoke pipeline (the explicit user constraint: ~100 sources can't
  be ~100 pipelines). Documents are **PDFs** everywhere (the förarbete parse pipeline
  applies); landing/listing HTML carries the reliable identity + amendment metadata.
  **No oracle** (user: the old myndfskr corpus + the `test/files` fixtures are
  low-quality) — model by judgment off the SFS + förarbete patterns, spot-checked.
- ✅ **First-class primitives** (`foreskrift/model.py`) — unlike SFS (where the text we
  handle *is* the consolidated in-force version), föreskrifter are **as-published,
  immutable documents**: a grundförfattning and each ändringsförfattning is a fixed
  artifact with no currency metadata. A base `Regulation` embeds its `Amendment`(s) and,
  for the minority that have one (108/1218), its `Consolidation`(s) — an *inofficial*
  compilation (the printed text stays officially valid; an official reprint is an
  *Omtryck*). A consolidation's one pinning fact is `konsolideradTom` — the most recent
  amendment folded in (a föreskrift uri), **not a date** (a "senast uppdaterad" date is
  just when the file was regenerated). URI `https://lagen.nu/{fs}/{year}:{lopnummer}`;
  `bemyndigande` → `https://lagen.nu/{sfs}#P{n}`. `structure` is the förarbete-style
  nested §§ tree (filled at parse).
- ✅ **Reusable harvest engine** (`foreskrift/harvest.py`) — the incremental
  newest-first loop itself (gated by `HarvestWatermark`, atomic writes,
  `Reporter`, politeness) was promoted out of here into `lib/harvest.py`
  (`walk`, shared with dv/forarbete/riksdagen/avg — see §5); `foreskrift/harvest.py`
  now just wires each agency's enumerate/resolve seam onto that shared loop,
  **architecture-agnostic**. An agency is config naming two seams over it:
  - **`enumerate`** — *how to list an agency's docs*, the variable axis. Three reusable
    enumerators cover the wild: `indexed_enumerate` (one static HTML page),
    `paginated_enumerate` (`?page=N`), `json_enumerate` (a search/REST API in one call);
    a genuinely idiosyncratic index is a small bespoke function (FFFS, BFS).
  - **`resolve`** — *item → stored files*. `resolve_landing` (most agencies) scrapes a
    landing page's PDFs and classifies each via a pluggable **classifier**:
    `classify_file` (link text), `classify_section` (the `<h2>` a file sits under),
    `classify_href` (the PDF filename) → regulation / consolidation / amendment / memo /
    attachment. `resolve_direct` handles **API-direct** sources whose listing already
    carries the file URLs (no landing page). Only **regulation + consolidation** (the
    in-force text) are downloaded; amendments/memos/attachments are recorded as
    references (identifier + href) — the full amendment graph without the fetch cost.
- ✅ **15 agencies harvested to completion** (`foreskrift/agencies.py`, the
  `REGISTRY` where the ~100 fs live as config) — a full corpus run: **1218 base
  regulations, 1203 (98.8 %) with downloaded text** (regulation/consolidation
  PDF), 0 crashes, 0 unhandled errors. The 15 without local text are genuine
  edge cases (repeal/amendment-only top-level entries, one memo-only landing,
  5 pre-1994 NFS `ar-YY-N` two-digit-year filenames). Per-fs: tfs 339, nfs 210,
  fffs 126, bfs 124, msbfs 97, lmfs 93, ssmfs 46, ptsfs 45, livsfs 32, kovfs 26,
  stemfs 26, sifs 22, elsakfs 20, rgkfs 9, kifs 3 (only 3 in-force base regs).
- ✅ **Registry grown to the full lagrummet.se government-agency list**
  (`foreskrift/agencies.py`) — from the 15-agency exemplar corpus above to **71
  registered författningssamlingar** (county `\d+FS` series excluded), **66 live**
  through the shared harvest engine + **5 closed-series stubs with no live
  harvester**: rsfs, sosfs/hslffs (backfilled once from a frozen corpus, §7g,
  since migrated to ordinary harvested records), sjvfs (SharePoint/Microsoft 365
  auth wall), and svkfs (no register left of its own — delegated to eifs). SKVFS
  and MTFS are live through
  a detached headful-Chrome transport: `Agency.browser` keeps Playwright/CDP
  absent while their F5/Shape challenge runs, while all other agencies retain
  requests/HTTP2. The SKVFS register also emits its closed RSFS predecessor, so
  RSFS needs no second browser sweep. Predecessor författningssamlingar route via
  `fs_from_designation`/`DocRef.fs` at harvest time with no registry entry of
  their own (the MCFFS precedent): fifs, difs, rnfs, trmfs, nutfs, mprtfs,
  mrtvfs, sisuvfs, amsfs, rffs, lfs, jvsfs, vvfs, trvtfs. KKVFS (Konkurrensverket)
  sits behind a Cloudflare front that 403s HTTP/1.1 and only serves HTTP/2; its
  `Agency.http2` flag routes it through a new `make_http2_session`
  (`lib/net.py`, the `httpx2[http2]` extra) instead of the default `requests`
  session. A full harvest at the new scale is in progress: **~6,750 base
  regulations** across the ordinary live fs (skolfs 2557, tsfs 925, fkfs 543, rams 366,
  rfs 274, dvfs 263 the largest), followed by a full `lagen foreskrift rebuild`.
- ✅ **Enumeration resilience** (`harvest.py`) — these agency indexes are flaky and
  badly maintained, so the harvest survives any single index page failing without
  losing the rest: `_guarded_enumerate` turns an enumerator that dies outright (a
  single-call API down, malformed JSON, 403) into a logged `Skip` and moves to the
  next agency (one bad source can't abort the whole run); multi-page enumerators
  (`indexed_enumerate` per-year, `paginated`, `sitemap`) yield a `Skip` for one
  unreachable page and keep walking the tail. A `Skip` is *logged* (never swallowed)
  and *withholds the watermark save* so the page is retried next run; an
  *expected* empty page (a year with no regulations — `optional_pages`) is silently
  skipped, not an error.
- ✅ **Per-agency parse-coverage fixes** surfaced by the full run: MSBFS 25→96/97
  PDFs (`classify_default_regulation` for old SÄI/SÄIFS predecessor texts whose
  designation prefix ≠ the agency fs, + `/siteassets/` ∪ `/contentassets/` selector);
  NFS 169→205/210 (filenames come both `nfs-2014-29.pdf` and underscored/zero-padded
  `nfs_2007_09.pdf`).
- ✅ **Five exemplar architectures** (the seam pressure-test; each agency is ~10 lines):
  - **FFFS** (Finansinspektionen) — static förteckning, bespoke enumerate (year+lopnr
    fused in the detail URL), landing + text-classify. ~1.8 s/base, ~335 bases.
  - **SSMFS** (Strålsäkerhetsmyndigheten) — `paginated_enumerate`, landing + text-classify
    (PDFs served *without* a `.pdf` suffix → byte-sniffed).
  - **NFS** (Naturvårdsverket) — `json_enumerate` (an Optimizely search API, `unwrap`
    a `searchModel` envelope), landing + `classify_href`.
  - **KIFS** (Kemikalieinspektionen) — `indexed_enumerate`, Sitevision `/download/` PDFs
    grouped under `<h2>` sections → `classify_section`.
  - **BFS** (Boverket) — **API-direct**: a key-less REST API returns the whole register
    with each PDF URL + amendment back-link inline → bespoke enumerate + `resolve_direct`,
    no landing page.
  Wired: `lagen foreskrift download [fs…]` (`--full` refreshes existing, `--only
  fs/year:num`); bare = all agencies.
- ✅ **SKVFS + SOSFS/HSLF-FS backfilled from the frozen legacy trees** (originally
  `foreskrift/legacy.py`, §7g pri 6) — both known-hard sources gained a baseline this
  way. Socialstyrelsen remains **without a live harvester** (no live
  enumerate/resolve; `download` is a logged no-op), while SKVFS layers a live
  browser harvest over its imported baseline. The one-time import walked the frozen
  `entries/`, routed each doc to its own fs by the authoritative basefile (SKVFS + the
  RSFS predecessor, SOSFS + the joint HSLF-FS — `hslffs` slug, "HSLF-FS" designation),
  and wrote a record pointing at the frozen regulation PDF in place. A
  `source: "<corpus>-legacy"` marker meant a future bot-evading harvester's record (no
  marker) was never clobbered. **skvfs: 540 imported (492 PDF-body, 48 html-only →
  metadata-only), 8 null stubs skipped; sosfs: 419 imported (417 PDF-body, 2
  metadata-only), 22 null, 77 konsolidering skipped** (a `konsolidering/{fs}/{year}:{n}`
  3-part namespace whose index.pdf is in fact HTML — outside the vertical's URI/layout,
  deferred to a future SOSFS harvester's native Consolidation). A normal SKVFS run
  skips every already-imported record and fills only later identifiers; the post-freeze
  2025:4–2026:8 gap was downloaded live as 34 exact official PDFs. Parse ran
  end-to-end over the frozen bytes (bemyndigande/§§ where the PDF has a text layer;
  older SOSFS scans have none → metadata-only, by design). **Superseded 2026-07**: all
  909 imported records were migrated into ordinary harvested form (PDFs copied into
  `FORESKRIFT_DOWNLOADED/<fs>/`, records repointed from `{"legacy": relpath}` to
  `{"name": ...}`), proven byte-identical; `foreskrift/legacy.py` and the
  `import-legacy` verb were then deleted.
- ✅ **Full legacy-corpus sweep (2026-07-19)** — `foreskrift/legacy.py` reborn for
  all ~30 frozen lagen.nu myndfs corpora: every legacy document the live corpus
  does not carry (as a base record or an amendment under one) imported as its own
  record — body PDF copied, title from the frozen distilled RDF, original source
  URL kept though it may now 404, `"source": "myndfs-legacy"` marking provenance
  for presentation disclaimers; live always wins, re-runs are idempotent.
  **2,177 imported** (4,402 legacy docs seen, 4,249 live-covered, 50 bodyless +
  15 non-PDF reported and left frozen): the agency-purged repealed regulations
  (248 pre-reform AFS, PMFS 2019:2, 197 HSLF-FS), whole predecessor series
  (RPSFS, LSFS, LMVFS, KBMFS, RTVFS), and the frozen SJVFS (900) / SVKFS /
  LIFS / LVFS samlingar. Entries the old pipeline downloaded but never parsed
  (`"basefile": null`) recover their id from the entry path. `lagen foreskrift
  import-legacy`, tested in `test_foreskrift_legacy.py`.
- ✅ **Repealed-föreskrift presentation (2026-07-19)** — a regulation some
  other regulation's text repeals must never read as in force, even though
  its own artifact carries no repeal field (the evidence is the *replacing*
  document's clause, an inbound rpubl:upphaver edge). Three surfaces, all
  catalog-derived at generate time: the top-of-page "Upphävd eller ersatt"
  banner naming the replacer(s); the samling browse listing keeps the
  repealed regulation findable (point-in-time law) but subdued
  (`catalog.upphaver_targets` → `facets.browse_doc`); and the replacing
  regulation's metadata header carries a linked "Upphäver" row. The
  extraction also learned the transitional-provision passive ("Genom
  föreskrifterna upphävs … (PMFS 2019:2)") scanning *all* clauses, not the
  first. Acceptance pair PMFS 2019:2 (myndfs-legacy import) / PMFS 2022:1
  verified end-to-end; locked in `test_site.py`/`test_foreskrift_parse.py`.
- ✅ **MTFS live through the same detached Chrome transport** — its Sitevision page
  maps authoritative `MTFS YYYY:N` headings directly to PDFs. All 16 regulations
  (2009:1–2023:3) downloaded end-to-end; five older filenames omit “MTFS”, so the
  enumerator never infers identity from the file slug.
- ✅ **Shared PDF parser** (`foreskrift/{parse,structure}.py`) — one parser for all 15 fs.
  The font-aware extraction + paragraph reflow it shares with the förarbete parser was
  promoted to `lib/pdftext.py` (the rewrite's "extract after the second instance" rule);
  förarbete re-imports it, its 20 tests unchanged. Föreskrift-specific layers:
  - `parse.classify` reads the `N kap.`/`N §` markers **from the text, not the font** — bold
    is reliable on a modern FFFS PDF but gone on a scanned 1984 BOFS one, while the textual
    convention holds corpus-wide; so the body classifies even when extraction is OCR-mangled.
  - `structure.nest` builds the statute-shaped `kapitel → paragraf → stycke` tree and mints
    the SFS `#K2P3` anchor on each paragraf — what makes a föreskrift paragraf a *citation
    target* (a statute's `bemyndigande`, or a cross-reference, resolves to `…#K2P3`).
  - `parse.extract_metadata` lifts the masthead facts best-effort: beslutsdatum,
    ikraftträdande, Utkom-från-trycket, the `bemyndigande` SFS paragrafer (the "med stöd av
    …" clause through the citation engine, deduped to paragraf-precision), the EU directive a
    "Jfr … direktiv …" footnote says it `genomför`, the regulations it `upphäver`.
  - Every step is best-effort: a scanned PDF (no text), a 600-page förteckning (no §§), a
    short declarative (no §§) all yield a document, never an error. **Full corpus parse: 0
    failures across all 15 fs.**
- ✅ **`konsolideradTom`** — a consolidated version's one pinning fact, the most recent
  amendment it folds in (`parse.konsoliderad_tom`: the highest fs-reference in the
  konsoliderad masthead, e.g. FFFS "Ändringar: … FFFS 2026:6" / NFS "ändringar till och med
  NFS 2026:5"), **not a date**. (Fixed `RE_FS_REF` to admit 3-letter codes NFS/TFS/BFS.)
- ✅ **The `bemyndigande` edge — statute → its föreskrifter — wired end-to-end.** A
  föreskrift is *meddelad* under one or more SFS paragrafer; `catalog.bemyndigande_links`
  emits that edge (`rpubl:bemyndigande`, föreskrift→SFS-paragraf) into the shared link
  graph (1247 edges, 570 empowering paragrafer across 260 statutes), and the SFS paragraf
  page grows a margin **"Föreskrifter meddelade med stöd av denna paragraf"**
  (`page._bemyndigande_margin`) listing them — the headline value-add (a statute now lists
  the regulations issued under it). The edge is a *typed* relation, kept out of the generic
  "Lagrumshänvisningar hit" panel (its own `_NOT_BEMYNDIGANDE` filter), and the föreskrift page
  shows the mirror outbound "Bemyndigande". Föreskrift is now a first-class rendered source
  (`foreskrift/render.py`'s `render`, lagen.nu's `/{fs}/{år}:{nr}` route, browse + frontpage), its
  `structure` reshaped to the shared statute node convention (`id`/`ordinal`, paragraf body
  in a `stycke` child) so it reuses `render_node` + the catalog fragment/link walkers. Shared
  PDF extraction lives in `lib/pdftext`.
- ✅ **`parse` stage wired into the build driver.** Föreskrift was the last vertical whose
  corpus was produced by a one-off batch script outside the driver; it now registers a real
  `parse` Stage (`build.foreskrift_parse_run`, inputs = the harvested record + its body PDFs,
  recipe = `FORESKRIFT_CODE`), so `lagen foreskrift parse` / `… rebuild` re-parse incrementally
  and a parser edit re-stales every doc the recipe-version way — like SFS/eurlex. No
  per-document `download` stage: the body PDFs arrive only through the bulk `foreskrift_harvest`
  sweep, so parse depends on no upstream stage and runs over whatever the harvest left on disk.
  relate/index/dump/generate already acted on the artifacts by source name, so they needed no
  change.
- ✅ **The build driver is the single parse entry point.** The standalone
  `cmd_one`/`cmd_batch`/`main` CLIs that each `{dv,eurlex,forarbete,wiki,foreskrift}/parse.py`
  carried (a pre-driver debugging path that duplicated artifact-writing and bypassed the
  manifest) were removed; every source now parses only through its driver `parse` Stage. The
  parse modules keep their library API (`parse_record`/`to_artifact`/… that `build.py` imports).
  (The legacy DV Word path, `dv/legacy.py`, keeps its CLI — it has no driver stage yet.)
- ✅ **OpenSearch indexing** is source-generic and already indexes föreskrift
  artifacts, including their id-bearing paragrafer.
- ✅ **Consolidations published as the presented text** (2026-07-18,
  `docs/rewrite-parity/02`). Measured: 1,624 of 9,056 records carry a
  konsoliderad version, only 3 genuinely carry two — so no version selector;
  `lib/text.presented_consolidation` picks the latest parsed one and
  `text.body_sections` lets it *replace* the base `structure` for render,
  search/fragments, the MCP reader and `catalog.artifact_links` alike (same
  §§ ids — walking both would double every anchor). The page banners the
  cutoff amendment and the compilation's inofficial status, renders the
  ändringsförfattningar register, and links the as-enacted text at
  `{uri}/grund` — a `.grund.json` sidecar the parse run emits when both
  texts exist (1,577 records), rendered as an uncatalogued extra page like
  the SFS lydelse artifacts. The 8 unparseable konsoliderad PDFs (image-only
  scans, cover-sheet stubs) fall back to the base text with the agency's own
  PDF linked.
- ✅ **Typed relation edges (2026-07-19):** `andrar` is extracted from an
  ändringsförfattning's own harvest title ("… om ändring i … (ÅFS 2005:5)",
  chained titles take the first ref, a bare "(2007:12)" implies the record's
  own series; 823 designated + most of 174 bare-ref titles resolve); the
  konsoliderad masthead's amendment list folds into the register (entries the
  landing page missed get minted uris), and the register's uris project as
  `metadata.andradAv`. `catalog.relation_links` publishes all four as typed
  edges (`rpubl:andrar`/`rpubl:upphaver`/`rpubl:genomforDirektiv`/
  `rinfoex:andradAv`), field-driven on metadata keys; they stay out of the
  generic inbound panel (`_NOT_TYPED`) while genomförDirektiv joins the
  directive page's inbound like the förarbete implements-edges. Render adds
  Ändrar/Upphäver outbound groups and the target's "Upphävs eller ersätts av"
  mirror (`catalog.upphaver_inbound`). En route, `_fs_key` now consults the
  registry's designation→slug rows — the naive transliteration minted 'ÅFS'
  amendments under Arbetsmiljöverkets `afs` (and 'RÅFS' under Riksarkivets
  `rafs`); and layout's föreskrift slug grammar gained the two non-`-fs`
  series (`bfnar`, `rams`), which had been silently falling through to the
  SFS page branch (locked by a registry↔grammar test).
- ✅ **Browse polish (2026-07-30).** A new hand-edited registry,
  `foreskrift/data/series.json` (`lib/datasets.FS_SERIES`/`load_fs_series`:
  designation, official title, an optional `successor` slug), drives what the
  browse shows instead of the raw fs slug: a samling heads by its official
  name + printed designation ("Åklagarmyndighetens författningssamling
  (ÅFS)") and orders Swedish-alphabetically (ÅFS after Z, `facets._fs_order`)
  rather than ASCII; a series whose agency was renamed or absorbed (DIFS →
  IMYFS, SRVFS → MSBFS → MCFFS, …) folds its documents under the successor,
  with a note naming the predecessor(s) (`facets.fs_predecessors`,
  `_fs_live_map` fails fast on a cyclic entry). Every ändringsförfattning now
  nests under its base regulation instead of listing separately
  (`catalog.andrar_edges`, `facets._fold_fs_amendments`); a samling under 200
  documents (amendments included) lists on one page, at or above it keeps
  per-year pages with the year selector as a banner atop the list rather than
  in the left nav (`browse.generate_browse`'s `FS_YEAR_SPLIT_MIN`). Separately,
  `parse.clean_title`/`title_from_body` fall back to the PDF body's own
  opening rubric as the title when the harvest title is link chrome ("pdf, 63
  kB") rather than prose, and `upphaver` targets now fold a designation
  through `_fs_key` instead of a bare `.lower()` (ÅFS mints `aafs/…`, not a
  dangling `åfs/…`). `test/test_foreskrift_parse.py`,
  `test/test_site.py` (`test_foreskrift_browse_nests_amendments_under_base`,
  `test_foreskrift_succeeded_series_folds_into_successor`,
  `test_foreskrift_small_series_gets_one_index_page`,
  `test_foreskrift_large_series_partitions_by_year_with_top_axis`).
- ✅ **Browse hygiene + search facet labels (2026-08-02).** A succeeded
  författningssamling's own slug now gets a page saying its föreskrifter list
  under the successor (`browse._write_succeeded_series`, following the
  whole chain — säifs → srvfs → msbfs → mcffs — via `facets.fs_live_series`);
  before this the folded series carried no page at all, though its old
  addresses stayed in circulation. `generate` also reaps any browse directory
  the run no longer writes (`browse._reap_browse`), so a folded-away samling's
  pages from an older build stop serving. A föreskrift with a konsoliderad
  version listed twice — the consolidated text and its as-enacted `/grund`
  sibling carry the same beteckning and title — so the listing now folds the
  base version out under the consolidated one, marked `consolidated`
  (`facets._fold_fs_versions`); the base stays reachable from the document
  page. Separately, the search facets' kind → label map (`facets.kind_labels`)
  is now derived from the same `SCHEMES` the browse pages use rather than a
  second table, fixing forarbete's "bet"/"pm"/"rskr" buckets reaching the
  search UI as raw catalog keys; `/api/v1/search` serves it as `kind_label` per
  result and `label` per facet bucket. `test/test_browse_generate.py`,
  `test/test_facets.py` (`test_kind_labels_name_every_forarbete_type`,
  `test_fold_fs_versions_drops_the_base_and_marks_the_consolidated`), `test/test_api.py`.
- ✅ **Föreskrifter become citation targets (2026-08-03).** A new `FORESKRIFT`
  parse type (`lib/lagrum.py`) recognises a myndighetsföreskrift by its printed
  författningssamling designation + number ("PMFS 2022:1", "ELSÄK-FS 2008:1", in
  running text or inside a name's parenthesis) — the corpus held **zero**
  inbound references to any of its 12,936 föreskrifter before this, a fact
  about the missing production, not about Swedish drafting. The designation
  terminal is built from the `foreskrift/data/series.json` registry
  (`FS_SLUG`/`FS_DESIGNATIONS`), so only a registered series mints a uri and the
  printed form maps to its real slug (ÅFS → `aafs`, not Arbetsmiljöverkets
  `afs`). Added to `ALL_PARSE_TYPES` (dv/forarbete/avg/wiki cite föreskrifter
  now) and to `foreskrift/parse.py`'s own `PARSE_TYPES`, since an agency's
  regulations constantly cross-refer each other in operative text ("Utöver
  denna föreskrift gäller MSBFS 2020:7") — the masthead-only `andrar`/
  `upphaver` edges never captured that. En route, a `generic_ref` naming a
  chapter now stays *sticky* for the bare sections that follow ("4 kap. 7 §
  samt 8 och 9 §§" resolves both trailing refs into chapter 4, not chapterless
  `#P8`/`#P9`) — the same chapter-continuation rule every other chaptered
  production already had. `test/test_lagrum.py`.
- ✅ **`lagen foreskrift reap` (2026-08-03)** — removes a harvested record an fs
  reassignment left behind under the *old* författningssamling. An agency
  taking over a renamed/absorbed agency's samling is read by
  `fs_from_designation`; turning that reading on for an agency the harvest had
  already walked without it re-files its whole back catalogue under the new fs
  while the old run's records stay on disk claiming the same landing pages
  (MCF's listing was first walked under `msbfs`, so every MCFFS/SÄIFS/SRVFS/
  KBMFS regulation on mcf.se also carries a stale `msbfs/…` record — MSB was
  renamed at the end of 2025, so "MSBFS 2026:8" does not exist — and both
  parse, publish and cite, doubling a rail row). `foreskrift/download.superseded`
  (`stored_series`/`superseded_files`) finds them positively: two records
  claiming one landing page, the landing slug naming the samling the site
  itself files it under, so the claim the slug corroborates is the real one and
  the other is the leftover — never a scoped guess, since the whole point is
  comparing across författningssamlingar. `reap` removes the loser's record,
  cached landing page and body PDFs (`--dry-run` lists without removing);
  `relate` then drops it from the catalog on the next run. Registered as a
  `foreskrift` action alongside `browser-download`.

### 7f. avg vertical — JO + JK + ARN + IMY + KKV myndighetsavgöranden ✅ (first cut)

`accommodanda/avg/` — vägledande avgöranden from Riksdagens ombudsmän (JO) and
Justitiekanslern (JK), ported from the legacy `jo.py`/`jk.py`. One vertical,
three per-organ configs (the foreskrift doctrine: sources sharing a model are
configuration over one engine, not two pipelines). The ~800 MYNDIGHETSBESLUT
citations the other verticals already scan (`dnr NNNN-YYYY` / `dnr NNNN-YY-TT`)
now have internal targets.

- **URI = citation-minted, by construction** (the DV lesson, fourth
  application): `model.beslut_uri` is `avg/{org}/{dnr}` — the exact string
  `lagrum.fmt_jo_refs`/`fmt_jk_refs` mint — so a decision and any citation to
  it agree byte-for-byte (locked by `test_uri_matches_citation_grammar`).
  Identifier forms kept from the old pipeline: "JO dnr 6356-2012" / "JK …".
- **Both sites were redesigned since the legacy code**, so the download layer
  is new; the *domain knowledge* carried over (dnr grammar, multi-dnr
  decisions, the JK dotted-ärendetyp quirk, decision-as-PDF vs -as-page):
  - **JO** (WordPress): the search UI's `admin-ajax.php` action
    (`get_jo_search_result`, page-embedded nonce) is a complete records API —
    dnr, beslutsdatum, title, summary, deciding ombudsman, sakområde/lagrum
    taxonomies, the decision **PDF url** and the site's own flat text
    extraction. **3,738 decisions back to 1979.** Newest-first incremental with
    the dv-style `HarvestWatermark` incremental gate; the PDF is fetched per decision.
  - **JK** (Umbraco): the listing still honours the legacy "broken pagination"
    hack — `POST page=9999` returns the whole corpus in one response
    (**1,427 decisions, publications 1998–**). The decision *is* its landing
    page (stored + record JSON). `jk_canonical` normalizes the site's raw dnr
    forms to the citation form: dotted ärendetyp `6098-19-4.4` → `6098-19-44`,
    `JK `-prefix dropped, multi-dnr `;`-lists → first names the document; the
    new-era `YYYY/NNNN` form passes through (not a citation target under the
    old grammar, but a stable published identity).
- **Parsers** (`avg/parse.py`): JO reads the PDF through the shared
  `lib/pdftext` (front matter before the title dropped, the title consumed as
  a bold-line prefix sequence, bold para → rubrik, `Beslutet i korthet:` → the
  abstract; the record's `pdf_text` is the no-PDF fallback body); JK classifies
  the landing `div.content` (all-`<strong>` p → section, all-`<em>` →
  subsection — the legacy jk.py signals, still valid). Both bodies scanned with
  the DV parse-type set, so JO/JK practice joins the corpus graph — verified on
  a live sample: 53 decisions → 1,038 outbound edges (RB, RF, förvaltningslagen
  top-cited), pages render with live links + rail.
- **Wired end-to-end**: `lagen avg download [jo|jk] [--only org/dnr]` (harvest)
  + `parse` Stage (recipe-versioned); `layout` (`avg/{org}/{dnr}` page grammar,
  storage relpath), avg's catalog row (the shared `catalog.document_row`), `avg/render.py`'s `render` (JO-beslut/JK-beslut
  page with sammanfattning + meta), facets (Organ → År browse), frontpage
  entry. relate/index/dump/generate act on the artifacts generically.
  `test/test_avg.py` (16 hermetic tests).
- ✅ **ARN as the third organ** (2026-07-02, §7g pri 1 + a live harvester):
  - **Frozen corpus imported** (`avg/legacy.py`, `lagen avg import-legacy arn
    <tree>`): 1,026 referat 1991–2022. Metadata from each case's
    `fragment.html` (Änr = dnr verbatim, `\d{4}-\d{4,}`, zero-padding
    preserved; Avgörande → beslutsdatum; Avdelning → nyckelord; the summary
    *is* the title — its trailing self-citation stripped by a corpus-validated
    regex, 802 strips / 0 over-strips, tolerant of the 2-digit-year and
    reversed variants the legacy regex missed). The body file is picked by
    **magic-byte sniff** (5 corrupt 2001 `index.pdf` error pages fall through
    to the valid `index.doc`) and materialized as PDF — doc/wpd/rtf converted
    via headless LibreOffice (a deliberate §7g point-at-bytes deviation: 96 MB
    corpus, ~80 % needs conversion anyway). One empty stub (blank summary +
    textless body) is detected generically and skipped, the legacy
    DocumentRemovedError case. `orig_url` from the entry is kept on the record
    as provenance but never rendered — ARN's Digiforms URLs are session-bound
    and dead (no stable source URL exists, `remote_url` returned None already
    in the legacy module).
  - **Live harvester** (`arn_sync`): arn.se's current site publishes the
    vägledande beslut as **one static page**
    (`/om-arn/vagledande-beslut/`, ~138 referat 2017– , the JK one-shot
    idiom; the old Digiforms database 404s). Records in the same parse shape
    + `source_url` (the decision PDF under `/globalassets/`). **Live wins**:
    the harvester overwrites a record carrying the import's `source:
    "arn-legacy"` marker (73 of 138 replaced their frozen twins on the first
    run, the same live-wins convention as förarbete/föreskrift), and the
    import never overwrites a marker-less record, even under `--force`;
    `imported_from` stays as pure provenance naming the frozen file the body
    came from.
  - Parsed corpus: 1,091 ARN artifacts (953 frozen + 138 live), 0 errors,
    0 empty bodies, 4,340 outbound citation links in 702 docs. Facets
    (Organ → År; `_avg_year` keys ARN on the organ — its year-*first* dnr
    collides with JO's year-last shape), "ARN-beslut" page label,
    `test_uri_matches_citation_grammar` extended to arn.
- ✅ **JO/ARN validation (2026-07-19):** the live-vs-frozen JO inventory
  reconciled — of 3,291 frozen cases, all but **five** join a live jo.se
  record on some diarienummer (after normalizing 2-digit years and two
  identities the old pipeline read off printed dnr *ranges*; the frozen
  headnote's own "Diarienummer :" value adjudicates those). The pruning
  hypothesis was essentially false. `avg/legacy.py:import_jo` imports the
  five as `jo-legacy` records (headnote-curated titles, frozen PDFs) and
  writes the **ämbetsberättelse map** (`jo/.officialreport.json`, 1,619
  citations keyed by 1,774 dnr) from the distilled RDFs'
  `dcterms:bibliographicCitation` — jo.se does not publish the citation, so
  `parse_jo` grafts it onto live records too (`Beslut.official_report` →
  `metadata.officialReport`, rendered as the Ämbetsberättelse row, folded
  into the search doc's text so "JO 1990/91 s. 70" finds the decision; the
  map is a parse input, so a rewrite re-stales JO parses).
  `classify_arn` now strips the live arn.se PDF noise, anchored to the
  referat's *own* änr (citations to other decisions untouched): the margin
  "änr + date" header wherever a column boundary drops it (line start,
  mid-sentence, glued onto other lines) and the restated-summary front
  matter ending at the "Beslut <date>; <änr>" marker — verified over all
  140 live artifacts with the 25-case frozen snapshot byte-identical.
  Running the full JO/JK harvest and relate is deployment materialization,
  not implementation status (note above §1).
- ✅ **JK frozen deltas (2026-07-19, the legacy-corpus sweep):** the same
  live-vs-frozen join for JK — dot-insensitive over every diarienummer a
  live record names (the frozen ids write the avdelning undotted,
  '859-97-21'; live jk.se writes '859-97-2.1') — reduced an apparent
  231-decision gap to **37 genuinely absent** (almost all 1997–1999, before
  jk.se's archive thins out). `avg/legacy.py:import_jk` imports them as
  `jk-legacy` records with the frozen jk.se landing pages (the live parse's
  own input format); titles/dates from the distilled RDFs. 19 of the pages
  froze the pre-2016 ASP.NET skin, so `jk_body` gained a
  `beslutmetadatacontainer`-anchored skin reader. All 37 parse clean;
  ARN needed nothing (0 missing after the join).
- ✅ **ARN preamble-as-title (2026-07-30).** An ARN referat's "title" is in
  fact its preamble paragraph, not a heading — the page now heads on its
  first sentence (`lib/labels.first_sentence`, Swedish-abbreviation-aware:
  "s.k.", "kap.", a bare number don't end the sentence) while the whole
  preamble still renders as the summary above the referat text
  (`avg/render.py`'s `render`). En route, `lib/pdftext.page_paragraphs`' running-header
  strip was over-matching: a body line that merely *contains* the referat's
  own identifier ("Allmänna reklamationsnämnden gjorde följande bedömning")
  was losing "Allmänna reklamationsnämnden" to the header pattern; it now
  strips only a line that *is* the header (identifier + at most a page
  number/date). `test/test_pdftext.py`, `test/test_site.py`
  (`test_arn_page_heads_with_first_sentence_and_keeps_preamble`,
  `test_avg_meta_drops_avgjord_av`).
- ✅ **`/myndigheter/` landing (2026-07-30, T1).** avgöranden had no path in
  from the chrome at all; the masthead's "Föreskrifter" entry is now
  "Myndigheter" (`lib/tpl.py`'s `MAST_NAV`) and lands on a new page
  (`render._render_myndigheter`) introducing föreskrifter and avgöranden side
  by side, each linking into its own browse tree. `test/test_site.py`
  (`test_myndigheter_landing_links_both_collections`).
- ✅ **IMY as the fourth organ (2026-07-30).** Integritetsskyddsmyndighetens
  tillsynsbeslut — the GDPR practice, which nothing in the corpus had — from
  `imy.se/tillsyner/` (~130 tillsyn pages, 2018–; the listing is a Vue app but
  the server still renders `?page=N`, so no API contract is reverse-engineered).
  - **The published unit is not the document.** A tillsyn page carries a
    heading, an ingress, the tillsyn's current step and IMY's own summary of
    the outcome, and *attaches* the decisions as PDFs — and the diarienummer
    that names each decision is printed only inside those PDFs. So the
    harvester reads them (`imy_diarienummer`, page-1 header, two template
    generations: the prefixed `IMY-2024-2904`/`DI-2019-3375`, and the pre-2018
    bare `2248-2017` that only its position after the "Diarienr" column head
    tells from a date or the form number printed left of it) and regroups the
    documents by the number. That regrouping (`imy_records`) is what resolves
    all three of the corpus's shapes at once: one page deciding several ärenden
    (seven "Grannbevakning" beslut, seven brottsbekämpande myndigheter — each
    becomes its own decision, its title disambiguated by the document heading),
    one ärende published as several documents (a beslut plus the
    tillsynsskrivelse that opened it, plus an English translation — one
    decision, several `delar`), and one document hanging off several tillsyner
    (the vårdgivar-vägledning off eight pages, the 1177-rapport off six — one
    decision, several `tillsyner`, one stored asset).
    **138 decisions from 172 document links on 129 pages.**
  - **Anonymously published decisions have no identity.** 13 documents print no
    readable number — redacted to `DI-2018-XXXX` (the seven Grannbevakning
    beslut), an "Avidentifierad version" with the number dropped, or a scan
    with no text layer. They are reported by name at the end of the run, not
    filed under an invented key.
  - **The two curated pages are an overlay, not a second corpus.** Every entry
    on `praxisbeslut` and `beslut-om-sanktionsavgift` points at a tillsyn the
    `/tillsyner/` listing already carries (checked exhaustively: 56 unique
    `/link/<guid>.aspx` targets, 0 additions), but both add metadata that
    exists nowhere else — praxis marks the decisions IMY considers precedential
    and states lagrum, nyckelord, korrigerande åtgärd, överklagan and laga
    kraft; sanktionsavgift states the fine. The GUID redirects resolve through
    the `/tillsyner/rss` feed's GUID↔url pairs in **one** request instead of 56.
  - **Parse** (`classify_imy`): font-driven over the two-column layout —
    smaller than the body size is a footnote or masthead, a "N (M)" page mark
    is a running header, a bold paragraph is a heading whose level is the rank
    of its font size (IMY sets four, down to bold-at-body-size). The masthead
    is stripped *in place* (the `classify_arn` idiom) because the margin column
    glues it onto body lines wherever a baseline coincides. A heading broken
    across lines is rejoined keeping its hyphen — in this corpus a trailing
    hyphen is always part of the term (`VIS-förordningen`, `Trygg-Hansa`),
    never a line-break hyphen, and the one other shape is the suspended hyphen
    of a coordinated list (`VIS-, SIS- samt …`), recognised by the comma the
    earlier member leaves behind. **138/138 parse clean, 12,649 nodes, 2,263
    headings, 0 empty bodies**, and the whole corpus scanned clean of masthead
    and header residue.
  - **Wired end-to-end**: `lagen avg download imy` (`--only` names a dnr and
    needs the decision already harvested — a decision has no page of its own,
    so the tillsyn page to refetch is looked up in the record), `avg_inputs`
    depends on each decision's parts (shared assets, so several decisions can
    depend on one PDF), facets (Organ → År, keyed on the decision date because
    an IMY number carries the year the *ärende* was opened), "IMY-beslut" page
    label, and the sanktionsavgift + praxis rows in `avg/render.py`'s `render` meta block.
    `test/test_avg.py` (+17 hermetic tests, fixtures under
    `test/files/avg/imy/`, including real page-1 headers of both
    diarienummer generations).
- ✅ **KKV as the fifth organ (2026-07-30).** Konkurrensverkets tillsynsbeslut —
  competition and public-procurement practice, joined from **two** of the
  agency's own sources on the diarienummer, because neither alone is the corpus.
  - **The status filter is not a scope filter.** The diarium's own
    "Avslutade ärenden" + "Publicerade beslut" is 10,097 cases, but status says
    nothing about what *kind* of ärende a case is: 3,675 of those are
    remissyttranden and much of the rest routine korrespondens — neither a
    förvaltningsbeslut mot enskild. So the harvest also applies the agency's own
    ärendetyp groups (`KKV_CASETYPES`): konkurrensbegränsande samarbete (346),
    missbruk av dominerande ställning (675), KOS (64), upphandlingsskadeavgift
    och domstolsärenden (706), otillbörliga handelsmetoder (39) — **1,830 cases,
    1998–**, both code generations (the pre-2018 "11 Missbruk dominerande
    ställning" and the current "3.2.2 Misstänkt missbruk…" both answer to 46).
    Företagskoncentrationer (49) are deliberately out: 2,068 largely one-page
    clearances that lämnas utan åtgärd, closer in character to the remisser.
  - **The curated ärendelista is the other half.**
    `/konkurrens/tillsyn-arenden-och-beslut/arendelista/` carries, for 329
    cases, what the diarium has no equivalent of: Konkurrensverkets own account
    of what the case was about, why it was prioritized, what it decided and
    **what the courts then did with the decision** — sectioned under the page's
    own headings ("Vad ärendet rör", "Konkurrensverkets beslut",
    "Tingsrätten", "Marknadsdomstolen") — plus the branch, the parties and the
    kinds of beslut (Avskrivning, Gryningsräd, Åtagande, Konkurrensskadeavgift,
    Förbud mot förvärv…). A fifth of the entries name several diarienummer (an
    ärende that became more than one case), so 329 cases resolve to **413
    diarienummer**, and the account belongs to each — the same
    one-entry-annotates-several shape as IMY's praxisbeslut. Only 67 of those
    413 fall inside the narrowed diarium set; the other **346 are stored from
    the account alone** — cases from 1993–97 that predate the diarium, and the
    hand-picked företagsförvärv that the bulk exclusion of ärendetyp 49 drops.
    That combination is the point: the notable mergers (including the two
    "Förbud mot förvärv") come in through the curated door while the 2,068
    routine clearances stay out. The corpus is **2,176 documents**.
    The account *heads* the parsed body and its case name is the title, because
    the decision document predates the courts that reviewed it and the diarium's
    ärendemening is bureaucratic ("Anmälan om företagskoncentration —
    fjärrvärmerör") where the curated name is how the case is known.
  - **Identity comes free.** Unlike IMY, the diarium *is* a register: the
    diarienummer is a listing field, so nothing has to be read out of a document
    to mint it. Its shape (`558/2026`) is JK's new-era shape, so it rides the
    storage and page grammar that already handles a slash in a dnr
    (`avg/kkv/558/2026` → `avg/kkv_558_2026.html`).
  - **Three transport facts** decided the harvester. konkurrensverket.se is
    behind the same Cloudflare front that made `foreskrift`'s KKVFS set
    `http2=True`, so this rides `lib/net.make_http2_session` too. The search
    page is server-rendered React, but `X-Requested-With` turns it into the bare
    result JSON — 500 bytes per case instead of 4 kB — and `Accept:
    application/json` does the same for the ärendedata and case pages. And the
    diarium's paging is *cumulative* (`page=2` re-sends page 1), so a group is
    taken whole with `take` rather than paged; the ärendelista's `page` is a
    true offset and is walked normally. The listing is authoritative for the
    case, so the per-case ärendedata request — which is what carries the
    *beslutsdatum*, as against the registration date the listing carries — is
    made only for a case that is new or has moved.
  - **Three body formats behind one parameter.** The file endpoint calls every
    format `pdf`: most are (read by the same `_classify_font_driven` core as
    IMY, extracted from `classify_imy` on this second use, but starting at the
    bold subject line — KKV sets the recipient block at the body size, so unlike
    IMY's margin column the fonts cannot separate the letterhead, and dropping
    it first also keeps it out of the body-size measurement); the pre-2006 ones
    are the **FrontPage-era HTML** the diarium published then, in three template
    generations whose anchors and field labels all differ, so the body is found
    by *shape* — the letterhead is the run of short lines above the first real
    paragraph — and the oldest generation's `ÄRENDE:`/`SAMMANF:` table is lifted
    out as the diarium's own abstract rather than read as body; two are Word,
    via the shared `lib/poi`. Every HTML document declares (and needs)
    windows-1252, so the encoding is asserted from that declaration rather than
    sniffed, and HTML under a `.pdf` name is an error page and is rejected. A
    handful of the PDFs are scans whose OCR layer poppler renders invisible —
    the third corpus to meet that, so `remisser.parse._pages` was promoted to
    `lib.pdftext.pages_with_ocr` and both now share it.
  - **Wired end-to-end**: `lagen avg download kkv`, facets (Organ → År, keyed on
    the decision date because a case number carries the year the case was
    *registered* — a long investigation is decided years later), "KKV-beslut"
    page label, and Motpart/Bransch/Typ av beslut in `avg/render.py`'s `render` meta
    block. Adding two organs also meant teaching `patchsource` their document
    routes -- an IMY decision assembled from several parts has no single
    patchable intermediate and is refused, as is a KKV case published as Word.
    `test/test_avg.py` (+21 hermetic tests, fixtures under
    `test/files/avg/kkv/`).

### 7l. rs vertical — myndigheternas rättsliga ställningstaganden ✅ (first cut)

`accommodanda/rs/` — the **third** kind of document a förvaltningsmyndighet
publishes about the law it administers, and the one lagen.nu has never carried.
A föreskrift is binding law issued under a bemyndigande; a beslut decides one
ärende; a *rättsligt ställningstagande* binds nobody outside the agency and
decides no case. It states, in advance and in general, how the agency reads a
rule it administers where the courts have not yet answered — and every one of
the agencies says so in nearly the same words ("styrande för vår
verksamhet", "inte bindande för till exempel domstolar"). That is exactly why
they are worth carrying: they are the published interpretation a reader of the
statute will actually meet, and the citation scan puts each of them on the rail
of the paragraf it interprets.

Six agencies in the first cut — **Försäkringskassan** (108, 2005–),
**Migrationsverket** (104, via Lifos), **Kronofogden** (22),
**Integritetsskyddsmyndigheten** (5), **Finansinspektionen** (7),
**Konkurrensverket** (13) — and **Skatteverket** (2,614 filed of 2,619
register entries, 2004–) added
afterwards, which is more than six times the other six together and is
described on its own below.

- **Identity is the agency's own number**, not a diarienummer — the one
  deliberate departure from `avg/model.py`, whose organs number nothing and
  where the dnr is all the identity there is. A ställningstagande is published
  *as* a numbered item in a series ("IMYRS 2024:1", "FKRS 2025:01",
  "RS/028/2021"), which is how the agency and everyone citing it names it. URI
  = `rs/{org}/{nummer}`, the avg grammar. Only FK and IMY have published a short
  designation for their series, so the other four are cited the way their own
  page names them; nothing is invented for an agency that has coined nothing.
  Skatteverket falls on the avg side of that line and for the avg reason: it
  numbers no series at all, and names its own positions "Skatteverkets
  ställningstagande 2026-07-06, dnr 8-207888-2026" — so there the dnr *is* the
  published designation, and following the rule means using it.
- **Currency is first-class.** Unlike a beslut, which is a fixed historical
  artifact, a ställningstagande is in force *until the agency withdraws it* —
  and four of the seven say so in the listing itself (FI's Status column,
  Konkurrensverkets "(upphävt 20 oktober 2025)", Migrationsverkets version
  numbering). So `status`/`upphavd`/`ersatt_av`/`ersatter` are modelled rather
  than dropped: a withdrawn statement still has to be readable — it governed
  what the agency did while it stood — but must not read as current law, so it
  renders subdued under a banner and is named "(upphävt)" wherever it is cited.
- **The document naming itself beats the listing retyping it.**
  Försäkringskassan is the one agency whose *identity* comes out of the
  document: its listing retypes the Serienummer, and at least once retypes it
  wrong (the 2026:01 PDF is listed as 2026:03). So the fetch happens in the
  sync and the number is read from the PDF; `stored_numbers` remembers which
  number a record was filed under, so a later run costs one listing request
  rather than 108 downloads (Lifos reuses the same memo).
- **One body reader, six configurations.** The first six publish the statement
  as a letterhead PDF, which is the shape `avg`'s IMY/KKV reading already knew. Its
  rules were promoted to `lib.pdftext.classify_letterhead` on this second
  reader (rule:second-use-goes-to-lib), emitting source-agnostic
  `(kind, text, level)` triples each vertical maps onto its own Block; `avg`
  now delegates to it. The one thing the shared reader had to learn is that
  **not every template marks a heading by weight** — Finansinspektionen and
  Migrationsverket set no bold section headings at all, only larger type
  (`heading_levels(..., by_size=True)`), and it is opt-in because in a
  bold-marking template a paragraph that merely runs large is not a heading.
  Everything else is per-agency data on a `Reader`: the margin column's labels
  and values, the footer masthead (removed *in place* — the column glues it
  onto body lines), and the page-1 fields the listing does not carry.
- **A stored record asserts that its document is on disk.** The harvest writes
  a record only once the PDF behind it is stored, and refuses to write one whose
  fetch failed; parse asserts the same invariant from the other side. That pair
  is what lets an absent PDF be read as "the agency published no document" — a
  repealed Konkurrensverket entry that kept only its förteckning row — instead
  of a broken fetch quietly publishing an empty page under a real identifier.
- **Currency is never inferred.** Finansinspektionens Status column is the only
  place in the vertical where a remote string decides whether a document reads
  as the agency's current position, so it is mapped onto the model's own two
  words and an unrecognised one stops the harvest (`fi_status`). Defaulting to
  "gällande" is the one mistake the field exists to prevent.
- **Nothing the listing states is re-derived from the PDF** (the avg rule). The
  PDF is read only where a field exists nowhere else — IMY's and Kronofogdens
  dates, four agencies' diarienummer, and Migrationsverkets own Beslutsdatum,
  which parts company with Lifos's Upphovsdat whenever a statement is revised
  in place. These headers are column tables that poppler flattens as either
  "label label value value" or "label value label value", so no label is
  reliably adjacent to its value: `labelled_value` anchors on the label and
  matches on the value's own shape, within a window that keeps the search
  inside the header.
- **A site that serves no intermediate certificates.** Lifos
  (lifos.migrationsverket.se) sends only its leaf and omits *both* certificates
  above it (Let's Encrypt's YR2 and the ISRG "Root YR" cross-signed into ISRG
  Root X1, which certifi does ship), so every requests/curl fetch fails with
  "unable to get local issuer certificate" while browsers, which chase the
  certificate's AIA pointers, load it fine. `lib/net.mount_aia_chain` does the
  same, for that host prefix alone.

  The care is in *what makes it safe*, because everything fetched here arrives
  over plain HTTP from a URL named by a certificate read on an unverified
  connection — so a first cut that simply appended the fetched bytes to
  certifi's bundle would have been no better than `verify=False`, and worse for
  hiding it: a `cafile` entry is a **trust anchor**, so an attacker's
  self-signed CA would have been trusted outright (this was caught in review
  and demonstrated before it was fixed). What the code does instead is verify
  every link before using it — `verify_directly_issued_by` proves each fetched
  certificate signed the one below it, and the walk stops only on a certificate
  a certifi root demonstrably signed. A forged certificate fails the signature
  check; a real one that chains nowhere trusted fails the terminator check.
- **Wired end-to-end**: `lagen rs download [org] [--only org/nummer]` + `parse`
  Stage (recipe-versioned); `layout` (`rs/{org}/{nummer}` page grammar, storage
  relpath), `catalog.rs_document`, `labels._rs`, `rs/render.py`'s `render` (with the
  withdrawal banner), facets (Myndighet → År, the year taken from the
  beslutsdatum where the document states one and from the agency's own number
  otherwise — a Migrationsverket RS/028/2021 currently in version 3.0 belongs
  under the year that version was fastställd), the `myndrs` legacy feed alias,
  the MCP source enum, `patchsource` (pdftohtml XML; for skv the page itself),
  frontpage entry and the
  `rs` inbound rail group. `/myndigheter/` now introduces all three — the rules
  a myndighet issues, the cases it decides and how it says it reads them.
  `test/test_rs.py` (94 hermetic tests, fixtures under `test/files/rs/`), plus
  the AIA-chain safety cases in `test/test_net.py` and the feed-index wiring
  guard in `test/test_feeds.py`.

**Skatteverket — the seventh agency, and the one that breaks every assumption
above.** `rs/skv.py` + `rs.download.skv_sync`, on its own command
`lagen rs browser-download`.

- **It publishes web pages, not PDFs.** The ställningstagande *is* its page, the
  way a JK-beslut is. So none of the letterhead machinery applies: `page_body`
  reads the page's own markup — h2–h5 headings, paragraphs, list items as
  stycken, the dated `div.update` notes Skatteverket sets at the head, and the
  notes under the closing "Fotnot" heading. `agencies.page_body` is the flag
  that routes it, so a second page-publishing agency is data rather than code.
  Its stored page is also its patchable intermediate, normalised to one block
  element per line in both `parse` and `patchsource` (the eurlex-HTML rule).
- **The register is JSON hiding inside a slow page.** 121.html lists 2,619
  entries and takes minutes to render, but Sitevision server-renders the whole
  list into the page as the app's initial state — so `parse_index` reads that
  payload rather than the rendered rows, located by shape (a `data.pages` list)
  because the portlet id changes per deploy. It carries more than the page
  shows: each entry's diarienummer, the document's *own* date (the rendered list
  shows the day rättslig vägledning published it, which differs for 1,592
  entries), the subject taxonomy ids, and the validity window.
- **Currency comes out of that window, carefully.** There is no status column;
  `latestVersion.endDate` is the only place the register says a position stopped
  applying, and 980 filed documents carry one. A further 273 carry an `endDate` *equal
  to* the start — those are the withdrawal notices themselves
  ("Ställningstagandet Verksamhetsöverlåtelse ska inte längre tillämpas"), which
  Skatteverket publishes with a single day's validity because their content is a
  one-time announcement. Reading a zero-length window as a withdrawal would say
  the agency withdrew its own withdrawal notice, so only a window that actually
  closed later counts. What replaced a withdrawn position, and what it replaced,
  come off the page: both are sentences in fixed words, and each names its
  counterpart with a marked-up reference carrying the other document's dnr.
- **Five entries name no dnr and are reported, not invented** — four pre-2000
  RSV-skrivelser the register keys on their date, and one stray test page. The
  reference id is otherwise hand-written and varies: 189 entries differ only in
  case or stray whitespace, one drops the issuer, and five write the number
  itself irregularly (an en dash for the year's hyphen, a space where a slash
  belongs, a two-digit unit, a one-digit year). All of those are read.
- **The pace is the design.** rattsligvagledning sits behind the F5/Shape
  challenge SKVFS sits behind. So every navigation goes through detached headful
  Chrome (`lib/browser`), one at a time. Measurement set the interval: ~30
  navigations at 5-second spacing trip the front's rate defence. The front then
  refuses every navigation for some 40 minutes, whatever profile asks — a fresh
  Chrome profile is refused on its first try. So a document waits 20 seconds,
  which held for 35 consecutive documents. A row of `SKV_BLOCK_LIMIT` refused or
  unfinished navigations ends the run. The next run resumes exactly there,
  because a record is only ever stored once its page is. A first harvest takes
  ~15 hours, sliceable with `--limit`; a weekly run costs the register plus what
  moved. That is why this agency has a command of its own rather than riding the
  nightly rs sweep. `lib.browser` grew `WafRejected` and `IncompleteNavigation`
  for it: a caller has to tell "the front said no" from "the page needed longer",
  because the two want opposite responses. Both were `assert`s, which `python -O`
  strips — and then the WAF's rejection page stores as the document.
- **Not done, deliberately**: there is no citation *grammar* for a
  ställningstagande yet — the outbound direction works (an rs body's lagrum
  citations put it on the statute's rail, which is the value), but a
  "FKRS 2025:01" written in another document does not yet resolve to its page.
  That belongs with the `MYNDIGHETSBESLUT` grammar in `lib/lagrum.py`.

### 7g. Frozen legacy corpora — imported, scaffolding torn down ✅ (plan 2026-07-01; teardown 2026-07-19)

**Status 2026-07-19: the migration is complete and the scaffolding is
removed — `accommodanda` + `site/data` are self-contained.** Every frozen
corpus's bytes now live natively in the store (soukb re-housed by
`soukb-scans`; the last 3,872 pointed-at bodies — dirtrips/dirasp html,
ds/dir/sou-regeringen PDFs — copied in and their 4,170 records rewritten to
the harvested `files` form, byte-identical parses verified per body route).
With nothing left to import or resolve externally, the one-time machinery is
deleted: the `import-legacy` verbs, `forarbete/legacy.py`,
`foreskrift/legacy.py`, `avg/legacy.py` (its runtime store-path helpers moved
to `avg/download.py`), `lib/legacy_import.py`, `dv/legacy.py`'s importers
(its Word/notis *parsers* remain runtime code), `config.LEGACY_ROOT` and the
`legacy_files` record field with both its parse and API consumers. The
corpora whose upstreams are dead (TRIPS retired 2016, the KB digitizations,
the pre-2016 jk.se skin …) simply have no downloader — archival data we are
glad to hold; everything still published flows through the live harvesters
(regeringen.se carries all new förarbeten). The section below is retained as
the record of the import design and its precedence rules.

The old pipeline downloaded several corpora whose *upstreams are dead or
historic* (TRIPS retired 2016, KB digitizations, defunct courts) — the corpus
is complete and will never update, so **the downloaders are not ported;
only a one-time import is built**. The raw trees live in `ferenda.old/data/`
(movable). Surveyed 2026-07-01 (data + legacy-module dossier):

| corpus | docs | coverage | raw format | value |
|---|---|---|---|---|
| `propkb` | 19,067 | **1867–1970** (two-chamber riksdag, KB) | ABBYY FineReader OCR-XML (full text), some PDF | high — a century of propositions |
| `propriksdagen` | 7,922 | 1971–2017 (data.riksdagen.se) | dokumentstatus XML + HTML + PDF | **highest value/effort** — born-digital, fills 1971→regeringen.se |
| `proptrips` | 4,556 | 1993/94–2016 (TRIPS) | plaintext-HTML + doc/docx/wpd/PDF | gap-filler only (era covered by the two above) |
| `soukb` | 5,807 | 1922–1999 (KB scans) | PDF **with text layer** (verified) + LIBRIS RDF; 371 GB | high — SOU citations resolve |
| `souregeringen`/`dsregeringen`/`dirregeringen` | 3,046/1,418/2,294 | ~1993–2025 | landing HTML + PDF | overlap with §7a's harvest — import missing basefiles only |
| `dirtrips`/`dirasp` | 5,096/1,826 | 1987–2016 | plaintext-HTML / PDF | moderate (dir is the least-cited type) |
| `arn` | 1,027 | 1992–2022 | decision file (pdf/doc/wpd) + `fragment.html` metadata | high, small — the avg vertical's third organ (`fmt_arn_refs` already mints `avg/arn/{dnr}`) |
| `skvfs`, `sosfs` (+ other myndfs trees) | — | varies | agency PDFs | fills the frozen baseline for hard föreskrift sources (§7e) — skvfs/sosfs from the frozen legacy tree; SKVFS now adds live records through `lib.browser.DetachedChrome`, and MTFS uses the same transport without a legacy baseline; sjvfs (SharePoint auth wall) and svkfs (no register left, delegated to eifs) remain frozen-only with no legacy corpus to import; kkvfs is live via `lib/net.make_http2_session` |
| `pbr` | ~12,300 | 1977–2016 (court dissolved) | case HTML + PDFs | skip — the old module was download-only, never parsed, no URIs minted |
| `keyword`/`myndprax`/`forarbeten`/`sitenews`/`mediawiki`/`eurlex*`/`sfs` | — | — | — | skip — facades, derived output, or superseded (wiki migration, CELLAR, golden) |

**Architecture: frozen corpora are alternate *sources* for existing verticals,
not new verticals.** Twice anticipated: §7a chose *basefile = the document's
own identifier* precisely so older-period sources reconcile by identity, and
`eurlex/bulk.py`'s `unpack-bulk` is the working pattern — a one-time import
verb that materializes a frozen tree into the vertical's own record layout,
after which the ordinary `parse` stage and the whole derived layer run
untouched.

- **Import verb per vertical**: `lagen forarbete import-legacy <corpus> <path>`
  walks the frozen `downloaded/` tree, derives `(type, basefile)` (the mapping
  quirks are known: PropKB's `1958:b23` b-series/urtima suffixes, SOUKB's 1922
  "första serien" restart, TRIPS' malformed-year sanitizers), and writes a
  record **only when no better source already holds that basefile**.
- **Precedence = the old composite's rule, made static**: live regeringen.se
  harvest → propriksdagen → proptrips → propkb (the old
  `get_preferred_instances` effectively said "anyone with a PDF beats an
  html-only copy"). Single best source per identity, no field merge — the DV
  lesson, and here identifiers already agree so no union-find is needed. A
  future harvester (data.riksdagen.se is still live; ARN publishes again) can
  claim the same basefiles later; the precedence rule absorbs that for free.
- **URIs agree by construction**: old and new mint the same
  `/prop/1975/76:100`, `/sou/1922:1`, `/dir/1994:111`, `/avg/arn/1992-1234`.
- **Point at the bytes, don't copy them** (410 GB soukb): move the frozen
  trees' `downloaded/` + `entries/` (the per-doc entry JSON carries the
  original landing URL → `source_url`) to a mount, add a `legacy_root` key in
  `config.yml`; import records reference body files in place. The old derived
  trees (`parsed/`, `distilled/`, `generated/`, `deps/`, most `intermediate/`)
  are replaced by this pipeline — droppable. Keep `soukb/intermediate/*.hocr*`
  (36 GB) until the PDFs' own text layer is confirmed good corpus-wide.
- **Format adapters, in effort order**: none for the regeringen-era trees (the
  förarbete PDF parser applies as-is); TRIPS plaintext-HTML is trivial
  (`div.body-text` → the text-inferred route); Riksdagen dokumentstatus
  XML/HTML is small; ABBYY-XML → a `pdftext.Para`-stream loader is one new
  format route (and buys 19k documents); `.doc`/`.docx` ride the DV POI path;
  `.wpd` (347 files) is dropped rather than chasing a WordPerfect converter.
  (Correction 2026-07-19: the "wpd all covered elsewhere" premise is false —
  82 of the 284 wpd-only proptrips docdirs, all 1995/96, have *no* parsed
  body in any corpus. `soffice --convert-to docx` (libwpd) converts them
  cleanly and `word_paras` parses the result, so recovery is a scope
  decision, not a technical gap.)
- **Priority**: (1) ARN into `avg` (smallest; the vertical is shaped for it);
  (2) propriksdagen (biggest citation-resolution payoff — förarbete citations
  in DV/SFS are dominated by 1971–1990s props that render as dead `.noref`
  text); (3) soukb + regeringen-era gap-fills; (4) propkb; (5)
  dirtrips/dirasp; (6) skvfs/sosfs backfill into föreskrift. PBR archived,
  not imported.

*Progress (2026-07-02):* priority 2 landed — `forarbete/legacy.py` imports the
propriksdagen corpus (`lagen forarbete import-legacy propriksdagen`) plus the
generic precedence machinery (`body_tier`/`SOURCE_RANK`/`should_write`) the later
frozen corpora reuse. Records point at the frozen bytes in place via `legacy_files`
(relative to `LEGACY_ROOT`), resolved at parse time. Body routing is data-driven,
not label-trusting: `index.pdf` is text-layer-probed at import (the skanning2007
*and* text/tml eras' pdfs are textless page scans — verified — while html-ec/2000s
pdfs are born-digital); a probed pdf → the shared PDF parse, else the `index.html`
body by `htmlformat` — `text/tml` `<br>`-plaintext or `skanning2007` OCR
Word-export html (`riksdagen_mso_paras`, bold headings survive) — stamped as
`body_format` on the record; html-ec/odd formats are positioned junk → metadata
only. Html bodies are page-less (`#sid` anchors simply absent; a page map is not
recoverable from the Word export). Verified on real data: born-digital PDFs (prop
2000/01:129, 133 `#sid` pages, 587 SFS links), text/tml (prop 1995/96:100, 115
links), skanning2007 (prop 1971:40, 122 SFS links incl. paragraf-anchored
1942:740; the 6 MB prop 1971:30 parses in ~30 s to 4.1k links), live
regeringen.se records never overwritten, idempotent re-runs. ARN (priority 1) is
a sibling in-flight.

*Progress (2026-07-02, live SKVFS added 2026-07-15):* priority 6 landed —
`foreskrift/legacy.py` imported the two harvest-blocked baselines (`lagen foreskrift
import-legacy {skvfs|sosfs}`). SOSFS/HSLF-FS remained frozen-only; SKVFS gained live
enumerate/resolve seams over the frozen baseline. Each had a `designation` for the printed prefix (HSLF-FS →
`hslffs` slug). Each frozen tree carried two fs series (skvfs+rsfs, sosfs+hslffs), routed
by each entry's authoritative basefile; records pointed at the frozen regulation PDF in place
(`files.regulation.legacy`, resolved by `parse.body_path` under LEGACY_ROOT) and carried a
`source: "<corpus>-legacy"` precedence marker (a future live harvester's record, no marker,
always wins; own re-import was idempotent, `--force` rewrote). null-basefile stubs and the
77 SOSFS `konsolidering/` texts were skipped with logged counts; html-only docs (no
regulation PDF) and text-less scanned PDFs became metadata-only records. Verified on real
data (`--limit` slices): skvfs 540 / sosfs 419 importable, parse end-to-end — hslffs 2015:15
→ 22 §§ + 4 paragraf-precise bemyndigande edges, skvfs 2012:1 → bemyndigande into SFS
1999:1229/2000:866; idempotent re-runs.
(13 hermetic tests, `test/test_foreskrift_legacy.py`). See §7e for the full note.
**Superseded 2026-07**: all 909 imported records were migrated into ordinary harvested
form (PDFs copied under `FORESKRIFT_DOWNLOADED/<fs>/`, records repointed from
`{"legacy": relpath}` to `{"name": ...}`, proven byte-identical); `foreskrift/legacy.py`,
the `import-legacy` verb and `test/test_foreskrift_legacy.py` were deleted — the one
surviving assertion (closed-series agencies registered with no live harvester) moved into
`test/test_foreskrift.py`.

*Progress (2026-07-02):* priorities 3–5 landed — `forarbete/legacy.py` now imports the
remaining eight frozen förarbete corpora as thin walkers over the propriksdagen
precedence machinery (`lagen forarbete import-legacy {souregeringen|dsregeringen|
dirregeringen|soukb|propkb|proptrips|dirtrips|dirasp}`). `SOURCE_RANK` gained the
sou (souregeringen>soukb) and dir (dirregeringen>dirasp>dirtrips) families beside
prop; a shared `_write_if_better`/`_preskip` core + `_record` back the per-corpus
walkers. **Two shapes:** the regeringen-era gap-fills + KB corpora (souregeringen/
dsregeringen/dirregeringen, soukb, propkb) are **entries-driven** — the authoritative
basefile is read from the entry JSON, the body located by the entry's path (soukb's
1922 `fs` suffix and propkb's `b`-series basefiles pass through verbatim; regeringen
multi-part PDFs are ordered main-first by the landing page's content links). The
**TRIPS family (proptrips/dirtrips/dirasp) is walked downloaded-first with the
basefile read from the path** — a deliberate deviation from the entries-driven plan:
the retired TRIPS scrape left ~half the entry JSONs null-basefile (proptrips 465 of
4,540, dirtrips 2,684 of 5,095, dirasp 1,442 of 1,826), yet those null-entry doc dirs
hold real bodies, so entries-driven would drop ~90% of proptrips; the `rm/year+nr`
path encodes the identity reliably and agrees with propriksdagen's basefile by
construction, the sibling entry supplying only `orig_url` provenance. **Body routing:**
`index.pdf` is text-probed (`pdftotext -l3`) then parsed font-aware via `pdftohtml`
— but the KB scans (soukb, propkb's scan-only props) carry an OCR text layer
`pdftohtml -xml` renders empty (and sometimes errors on) while `pdftotext` reads it,
so `parse` falls back to a page-anchored `pdftotext` extraction (`legacy_formats.
scanned_pdf_pages`) when the font path yields no blocks — decided by result, not by
guessing the corpus. propkb's ABBYY `index.xml` takes the page-anchored `abbyy` route;
proptrips/dirtrips html takes the `trips` route; `.doc`/`.docx`/`.wpd` are not listed
(metadata-only; a future POI/soffice route can revisit). **Provenance:** every record
keeps the entry's `orig_url`; it also flows to the rendered `url`/source_url only for
the corpora whose host still resolves (regeringen.se, urn.kb.se + weburn.kb.se — spot-
checked live), while the dead-IP TRIPS hosts keep `orig_url` as provenance-only
(`url=None`). **Re-OCR seam** (per the ocrmypdf plan): `parse._legacy_body` prefers a
sidecar PDF at `layout.fa_ocr_pdf(type, basefile)` (`forarbete/ocr/<type>/<slug>.pdf`)
over the frozen scan, and that path is a parse input so dropping a re-OCR'd PDF
re-stales the document; the OCR runner itself is not built (tesseract absent here).
The live regeringen.se downloader's skip test now treats a `source`-carrying import
record as absent, so live always wins and a legacy record never trips the
newest-first incremental stop. Verified on real data (`--limit 40` per corpus + parse
across routes): soukb 1945:1 → 636 blocks / 175 pages / 275 SFS links via the pdftotext
fallback; propkb 1867:23 ABBYY → page-anchored blocks; proptrips 2014/15:40 born-digital
PDF → 101 links, 1993/94:40 html → 71; dirasp 2007:23 → paragraf-anchored 1942:740 links;
souregeringen multi-part ordering. `test/test_forarbete_legacy.py` (+18 hermetic tests),
`test/test_forarbete_download.py` (skip-fix test). The unbounded full imports are not
run here.

*Full-corpus imports run (2026-07-02):* every §7g corpus is now materialized —
**avg/arn 1,026** (§7f note) + **foreskrift 959** (skvfs 540 incl. 31 RSFS,
sosfs 419 incl. 199 HSLF-FS) + **förarbete ≈36,260 records**: propkb 19,066
(17,295 ABBYY + 1,769 scan-pdf), propriksdagen 7,189 (1,478 pdf / 3,036
OCR-html / 2,177 metadata-only, 732 ceded to live), soukb 5,430 (3,161 pdf /
2,269 metadata-only — ~770 of those have PDFs that failed the text-layer
probe: the natural first targets for the re-OCR sidecar), dirtrips 2,411,
dsregeringen 1,260, proptrips 402 (2,712 ceded to better/equal propriksdagen
copies — the tier rule doing its job), dirasp 395, dirregeringen 63,
souregeringen 42 (the live regeringen.se harvest already held 2,968 sou).
One frozen entry stub is corrupt on disk (`dirtrips/entries/2006/72.json`,
doubled tail) — read as provenance-less, regression-locked. **soukb OCR
verdict** (user-adjudicated): the PDFs' embedded text layer is ABBYY
Recognition Server output and reads well across decades — it is used as-is;
no bulk re-OCR (the `forarbete/ocr/` sidecar seam remains for targeted
upgrades), and the old pipeline's 36 GB of Tesseract-3 `intermediate/*.hocr*`
can be dropped. Remaining ⬜: relate/generate at the new corpus scale. ✅ The
OCR chronology sanity check landed 2026-07-19: parse now knows which route a
body came through (`Forarbete.ocr` — the pdftotext scan fallback, ABBYY xml,
and the skanning2007/trips html adapters; text/tml and born-digital PDFs are
not OCR), and `censor_future_citations` demotes any link whose target year
exceeds the basefile year + 1 *and* whose own text carries that year — the
year-in-text condition scopes it to digit garbling, so a named-law reference
resolving to a modern namesake ("kommunallagen" in a 1971 prop →
lagen.nu/2017:725, a *name-resolution* defect the sweep surfaced, 231
instances in 18 sampled docs) is deliberately left to its own fix. The
suspect text is preserved verbatim (never rewritten), the link is simply not
minted, and each demotion is reported in the artifact's
`suspect_citations` [{text, uri, page}]. A 150-doc sweep over 1970s props +
1935–1975 SOUs found zero genuine future citations; the corpus-wide count
falls out of the full re-parse (finding 6). ✅ The
SOSFS `konsolidering/` texts landed 2026-07-19: they are consolidations of
their base regulations (self-titled "Senaste version av SOSFS X:Y"), served
by Socialstyrelsen as HTML pages despite the frozen `index.pdf` filenames
(one real PDF among 87 docs). Migrated into `files.consolidation` on their
base records — 76 attached (5 duplicate fetches skipped, 7 byteless entries
excluded incl. the wholly-absent sosfs/2014:7), 2 missing bases imported
(sosfs/2011:9, hslffs/2018:54; sosfs/2000:6 already existed as a plain-.json
record) — and parsed by the new `parse_consolidation_html` route (same
classify/nest text pipeline over the page's h2/h3/p blocks; the "Ändrad:
[t.o.m.] …" preamble line yields konsolideradTom + register refs, each under
its own printed samling — a SOSFS base consolidated t.o.m. an HSLF-FS
amendment is the 2015 series transition). All 76 present substantial
consolidated text; 41 carry a cutoff + register; 73 emit `/grund` sidecars. (`.doc/.docx`-only proptrips bodies landed 2026-07-17,
below — via `antiword`, not POI/soffice.) 💤 `.wpd` is deliberately dropped
rather than adding a WordPerfect converter; PBR is archived, not imported, and
outside the rewrite scope.

*Progress (2026-07-03):* the corpus-independent core each vertical had grown its
own copy of (`should_write` precedence, `rel` in-place LEGACY_ROOT-relative
references, the `iter_entries`/`docdir`/`read_record` walk primitives) is
extracted to `accommodanda/lib/legacy_import.py`; `forarbete/legacy.py`,
`foreskrift/legacy.py` and `avg/legacy.py` all call the shared module now,
with förarbete supplying its body-tier/source-rank comparison as the
`better()` tie-break callback. (**Superseded 2026-07**: `foreskrift/legacy.py`
was deleted once its imports were migrated to ordinary harvested records —
`forarbete/legacy.py` and `avg/legacy.py` still call the shared module.)

**Superseded 2026-07-17 (prop slice): prop is fully migrated frozen→harvested.**
All 28,288 `downloaded/forarbete/prop/*.json` records now carry `files`
(relative to `downloaded/prop/`) instead of `source`/`legacy_files`; zero
frozen prop records remain. This matters because `legacy_files` pointed at
`config.LEGACY_ROOT`, a dev-only mount — production held no such tree, so it
re-parsed and failed every frozen prop on every run. Final `body_format`
census: abbyy 17,295, none/pdf 7,052, skanning2007 2,334, text/tml 1,051,
trips 118, word 438; 2,236 records are metadata-only. `parse.parse_record`
stays additive rather than a rip-out: it branches on `legacy_files` present →
`_legacy_body` (still serving sou/dir/ds, whose frozen corpora are untouched
by this slice and remain under `LEGACY_ROOT`), else `_harvested_body` (the new
route, reading `files` under `downloaded/<type>/`). Removing
`forarbete/legacy.py` entirely is the last slice of §7g, after sou/dir/ds
migrate the same way.

Two library moves fell out of giving förarbete its own Word body: **POI moved
`accommodanda/dv/word.py` → `accommodanda/lib/poi.py`** (förarbete became its
second caller, so keeping it under `dv/` would have been a sibling-vertical
import — rule:second-use-goes-to-lib; `dv/legacy.py` now does
`from ..lib import poi as word`). `forarbete/legacy_formats.word_paras` adds
the new `.doc`/`.docx` body route, but **`.doc` is read with `antiword`, not
POI**: the proptrips-era `.doc` bodies are mostly Word 6/95 binaries that
POI's HWPF refuses (`OldWordFileFormatException`); POI (`lib/poi.py`) handles
only `.docx`. `antiword` is a new system dependency at parse time, alongside
`poppler-utils`, and has been added to `docker/accommodanda/Dockerfile`.

Also new: `accommodanda/forarbete/propkb.py`, a facsimile fetcher for the KB
two-chamber proposition scans (1867–1970). It adds no documents — the ABBYY
OCR text layer is already complete for all 19,066 propkb records — only a
facsimile "proof" view for the 17,295 that were fetched XML-only. No index
crawl: the scan-PDF url is derived mechanically from each record's stored
ABBYY xml `orig_url` (`/xml/`→`/pdf/web/`, `.xml`→`.pdf`), so the record set
*is* the work list and no basefile can be minted that the corpus lacks.

The scan lands at a **layout rule, `layout.fa_facsimile_pdf`, and is resolved
from disk by existence** (`api/app.py::_fa_pdf`) — the same bargain as the
mirrored SFS PDFs (`_sfs_pdf`). **No record is written**, for two independent
reasons: `parse._harvested_body` prefers a PDF over an xml, so naming the scan
in `files` would silently flip 17,295 bodies off KB's ABBYY OCR onto a
`pdftotext` of the scan; and the record is a *parse input*
(`build.fa_parse_inputs`, content-hashed by `build.hash_files`), so writing
*any* key into it — even one parse never reads — would re-stale all 17,295 prop
parses and re-run the ABBYY parse of the whole KB century for a set of images.
A record's `files` says what parse reads; the layout rule says what a facsimile
rasterizes, and the two are only sometimes the same file. Verified: the
parse-input hash is byte-identical with and without the scan present.

Exposed as its own verb, `lagen forarbete propkb-scans` (never part of
`harvest`), resumable from disk. **Built, not run**: the ~79 GB pass has not
been executed — only prop 1867:1 and 1937:141 were fetched, as end-to-end
verification, not a corpus pass.

Also new (2026-07-18): `accommodanda/forarbete/soukb.py`, a **body
re-downloader** for the KB-digitised SOUs (1922–1999). Unlike `propkb.py`,
there is no ABBYY XML sibling — the scanned, OCR'd PDF *is* the body — so
this adds real documents rather than a facsimile: it walks
`https://sou.kb.se/` as the sole source of truth (the old `regina.kb.se`
start URL is dead, so the legacy soukb records are forgotten entirely) and
writes a fresh harvested record per basefile, `files` pointing at the
fetched PDF(s). Basefile comes from the index label via a broadened port of
the legacy SOUKB regex (`1922:1 första serien`→`1922:1fs`, letter suffixes
lowercased, `/`-double-issues hyphenated); 5,814 distinct basefiles. 128 of
them are multi-volume (one label repeats across several URNs, e.g. `1987:3`
= 28 volumes of the Långtidsutredning), so `files` is a list in index order,
one record per basefile. Exposed as its own verb, `lagen forarbete
soukb-scans` (never part of `harvest`), resumable per part. **Built,
verified end-to-end on one small doc (1922:1, 10.5 MB) into a scratch
tree — not run at corpus scale**; the full pass is hundreds of GB.

**Legacy-corpus completeness audit (2026-07-19, the full sweep):** every
`ferenda.old/data/*/downloaded` corpus was diffed against the new corpus.
Imported: 2,177 föreskrift docs (see §7e), 37 JK decisions (§7f), 76 EUR-Lex
docs the harvest excludes by shape — 66 pre-1969 Swedish-HTML acts, 3 CP
"view" documents (the CELEX descriptor CASELAW_TYPES deliberately skips) and
7 non-`/TXT` treaty PDFs (the original Treaty of Rome among them; six parse,
11957A is a pure scan awaiting ocrmypdf). Proven already covered: ARN (0
missing after the dnr join), JO (§7f), SFS (golden-migrated;
`sfs-copy`/`sfs-copy2` are dev duplicates), mediawiki (876/876 files),
prop/sou/dir/ds (import-legacy records reference the frozen bytes in place
by design — the 371 GB soukb tree is pointed at, not copied). Excluded by
design: `keyword` (derived per-term subject aggregations the new catalog
recomputes), `pbr` (archived, outside closure), `sitenews` (empty).

✅ **DV frozen-referat coverage closed (2026-07-19).** The last real gap —
6,418 frozen-era DV cases (RÅ/HFD/NJA notiser, older NJA/AD/RÅ referats)
whose sources sit in `downloaded/dv/` but which the courts API never serves —
is materialized. Three pieces: (1) `lagen dv import-legacy` migrates the two
frozen facts the store lacked — the 5,935 notis *bodies* (the legacy feed
shipped notiser as zero-byte Word files; the text survives only in the old
pipeline's intermediate XML, copied in as parseable `.xml` beside them) and a
`legacy-identities.json` oracle sidecar distilled from the 21,595 old
distilled RDFs (referat/målnummer/date/referatrubrik per case). (2) The
identity index mints referat identities for frozen-only files: REG/HFD notis
filenames now yield their published identity like HDO's, and the oracle
sidecar attaches referats to målnummer-named files (unambiguous joins only —
målnummer is reused across years, so oracle målnummer is metadata, never a
linkage key, and an M-bridge is refused when the two components already
publish conflicting referats; colon vs "ref."/"nr" spellings normalize to one
identity). (3) `dv/legacy.py` gained the notis parse route (TRIPS `<para>`
and OOXML `<w:p>` flavors; header målnummer/date, Uppslagsord/Lagrum
sections, HD's month-compilation lead) and build.py routes any case without
an API record through the legacy parser (Word referat via POI, notis XML).
Notis summaries come from the oracle's published referatrubrik. Full-corpus
parse: **23,901 cases, zero errors**; golden: 21,594/21,595 old RDFs match an
artifact by URI — the single miss is old `ADO/2005-59.rdf` propagating a
source header typo (`AD 2004 nr 59`; decided 2005-06-01, mål B 134-2004,
API and filename agree on AD 2005 nr 59, where the artifact lives). All 13
avgörandedatum disjoints are pre-existing old-feed-vs-API metadata
disagreements on API-backed cases, none from the legacy route. A HWPF bug
was fixed en route (Word field-control characters `\x13\x14\x15` leaked into
extracted text; instruction segments now stripped, results kept).

The parallel closure commit was then merged (kept as history, its code
superseded) and its useful parts salvaged: the **withheld-originals
adjudication ledger** (`dv/data/legacy-ambiguities.json`, 57 legacy Word
files content-matched to the API publication each duplicates) is now applied
at identity scan time, hash-verified — before it, 54 of those files minted
duplicate målnummer-keyed cases beside their API referats; the ledger also
exposed that the old feed reused one filename stem for *distinct*
publications (MÖD/M5005-02.doc is MÖD 2003:112, its `_2` variant MÖD
2002:92), so attachment-variant fusion is now camp-wise by referat
compatibility. And **the old published verdict-URI scheme is restored**:
`casenaming.verdict_uri` mints `/dom/{publisher}/{malnummer}/{date}` (the
legacy COIN template, explicit abbrSlug map — MIOD→mig, MMOD→mmd) for a
non-referat case whose court, målnummer and date are all known; only a
fact-less stray keeps the slug fallback.

### 7h. remisser vertical — regeringen.se referral responses ✅ (first cut)

`accommodanda/remisser/` — remiss (public referral) ärenden from
regeringen.se/remisser/: a remiss sends a SOU/Ds out for consultation, and over
the referral period answers ("remissvar") accumulate from courts, agencies and
organisations. This corpus is **never published as its own pages** — it only
feeds an opt-in LLM pass whose output surfaces on the *referred* förarbete's
context rail, so it has no `relate`/`index`/`dump`/`generate` stage at all.

- **`model.py`**: `Remiss` — keyed on **the document it remits**, not the
  regeringen.se case-page slug: `basefile` is `"<typ>/<identifier>"` of the
  referred document (`sou/2026:14`, `pm/LI2026/01339`, `ds/2026:9`,
  `lr/2026/<title-slug>`), the case page's own URL kept separately in `url`.
  Title, dnr, deadline, cross-ref to the referred förarbete via `remitterat`,
  `externt_dokument` flagging an ärende whose remitted document regeringen itself
  never published, and `svar` — the `Remissinstans` list of organisations that
  have answered. `Remissvar` (one organisation's parsed answer) is basefiled
  `"<typ>/<document id>/<org-slug>"`. `org_slug` derives the filed-under-basename
  identity that `download.py`/`parse.py`/`build.py` all key on.
- **`download.py`**: harvests the `/remisser/` listing via the same AJAX
  filter endpoint the forarbete listings use (`?p=N` on the plain listing page
  always answers page one; an empty page mid-walk raises unless `TotalCount`
  confirms the archive is exhausted) plus each case page's metadata,
  "Remissinstanser" PDF and "Remissvar" list. The listing's own identity (a
  URL slug) is never the basefile — only the case page names the document
  remitted — so a separate **examined-ärende index**
  (`layout.REMISSER_SEEN`, `downloaded/remisser/.seen.json`:
  `{"dirty": bool, "arenden": {url-slug: {"basefile": str|null, "until":
  iso-date|null}}}`) is what the sweep's bookkeeping runs on in place of "is
  there a record on disk". `until` is the case's deadline plus `GRACE_PERIOD`
  — because answers accumulate for the whole remissperiod, "already examined"
  is *not* a reason to skip an ärende, only its closing date is; a `null`
  `basefile` marks an externally authored ärende, examined once and never
  fetched again. The "Dokument(et) som remitteras"/"Genvägar" island is
  matched (`_match_forarbete`) against `lib.regeringen.TYPES` to recover the
  referred förarbete's canonical basefile (falling back to the case title if
  the island is absent); the two doctypes regeringen.se publishes without a
  series number are resolved by the identity rules `lib.regeringen` shares with
  `forarbete/download.py` — a departementspromemoria (`pm_identity`) on the
  remiss's own diarienummer, minus any sub-ärende `–N` suffix
  (`SUBARENDE`), else the landing page's own slug; a lagrådsremiss
  (`lr_identity`) on `<year>/<title-slug>`. Whether the island links a
  `/rattsliga-dokument/` page decides `externt_dokument`: no such link means
  the remitted document was authored by an agency, an external party or the
  EU, not published by regeringen, so its answers are never fetched. `parse_arende`
  **raises** when an ärende remits a regeringen-published document but no
  basefile can be derived from it (an unrecognised doctype) — no stub identity
  is minted, since filing it under one would make the document unfindable by
  any later join. `sync` shares one `_poll` step (fetch the case page →
  classify its origin → merge onto any stored record → fetch pending answer
  PDFs → update the index entry) across two passes: the **listing walk**
  (newest-first, polling every case the index says still needs it, stopping
  after `STOP_AFTER` consecutive cases that need nothing — not the first one,
  so a case that failed last run leaves a gap the next walk falls into; a
  failed run leaves the index dirty, so the next run walks the whole archive)
  and a **catch-up pass** over index entries the walk stopped short of (an old
  ärende with a long remissperiod still open far below the frontier), re-polled
  from the url its own record carries. `_is_open` is gone, replaced by
  `_until`/`_needs_poll` reading the index entry alone. `sync_one`/`--only
  <url>` fetches one known ärende directly through the same `_poll` logic,
  subject to the same origin gate. `sync` returns `{"new", "failed",
  "externt", "repolled", "open", "fetched"}`. Verified live: of the current
  top 20 listing hits, 13 are stored (keyed sou/ds/pm) and 7 skipped as
  external; a second run re-polls the 12 still-open cases and does not
  re-fetch the closed one.
- **`parse.py`**: one answer PDF → `Remissvar`, via the shared
  `lib/pdftext` (`pdf_pages` + `page_paragraphs`) flattened to plain paragraph
  text — no structural classification, since the only downstream consumer is
  an LLM reading prose. Unlike JO/ARN/föreskrift there is no fixed running
  header to strip (each organisation's PDF carries its own letterhead), so
  `page_paragraphs` now accepts `identifier=None`/`""` and skips
  header-stripping outright rather than matching on a bad substitute.
- **Origin test** — an ärende whose page carries no remitted-document island is
  read as *external* unless its title names a series identifier; checked against
  every island-less page among the first 460 ärenden (agency rapport/framställan/
  hemställan, EU proposal, letter of questions). A `/rattsliga-dokument/` link
  with no derivable basefile stays a loud `parse_arende` raise — that is a
  missing identity rule, not an external document. `MARKUP_FIXES` corrects
  individual pages whose own markup defeats the parser (a heading misspelled
  "Gevägar"), curated per document rather than by loosening the rule.
- **`pm` cross-refs carry the landing `slug`** beside the dnr: forarbete keys a
  promemoria on its diarienummer only when its own listing text stated one, else
  on the landing slug, and the remiss page states neither — it has its *own* dnr,
  which usually but not always coincides (~30% of pm ärenden are slug-keyed).
  `layout.resolve_basefile` now takes `*alternates` and lets the tree settle it.
- **`ai_analyze.py`** — `lagen remisser ai-analyze <typ>/<document id>/<org-slug>`,
  the sole LLM pass over this corpus (never called from parse/relate/generate,
  the same doctrine as `kommentar ai-annotate`): maps one answer onto the
  specific sections of the referred SOU/Ds it discusses, with a per-section
  sentiment score and a verbatim quote plus an overall stance, validated
  strictly (every cited section id real, every quote a verbatim substring of
  the answer) and written as a `.ann` layer in the curated store
  (`lib/annstore.py`, `WIKI_ROOT/ann/remisser/…`, mirroring the answer
  artifact's relpath); joins to the forarbete tree through
  `layout.resolve_basefile` (below). Retries once as a real assistant/user
  follow-up turn on a malformed reply — since generalized into `lib.llm.author`
  (§5/§6/api, 2026-07-06), the shared validate/self-repair-retry loop
  eurlex/wiki annotate now use too.
- **Wired into `render.py`**: `_remiss_indexes` walks the remisser artifact
  tree directly (`layout.artifacts("remisser")`, not the catalog — this source
  is never `relate`d), picking up each answer's mirrored `.ann` layer from the
  curated store (`lib.annstore`), and builds
  `remiss_feedback`/`remiss_overall` on `Site`; `Rail._remiss_html` renders
  them as a "Remissvar" section — per-section on the cited `avsnitt`, and a
  document-level "most interesting feedback" panel via `Rail.add_document`,
  now wired into `forarbete/render.py`'s `render`.
- **`lib/regeringen.py`** (rule:second-use-goes-to-lib): the doctype table
  (`TYPES`), the listing-DOM walk (`listing_items`), and now also the
  **identity rules** for the two doctypes regeringen.se publishes without a
  series number — `pm_identity(dnr, slug)` (new) and `lr_identity` (moved out
  of `forarbete/download.py`, which now imports it). Both verticals must mint
  the same basefile for the same document from different pages, so the rules
  live in one place rather than each vertical guessing independently.
- **`lib/layout.py`**: `relpath`/`remisser_arende`/`remisser_answer` updated to
  the document-keyed grammar — `relpath("remisser", …)` splits the leading
  `<typ>` off the front of the basefile and the trailing `<org>` off the
  *back* (a document id may itself contain a slash, e.g. `pm/LI2026/01339`),
  landing an ärende record at `downloaded/remisser/<typ>/<id-slug>.json` beside
  its `<typ>/<id-slug>/<org>.pdf` answers and an artifact at
  `artifact/remisser/<typ>/<id-slug>/<org>.json`. New `resolve_basefile(source,
  basefile)` respells a cross-source basefile case-insensitively against the
  artifact tree when the two differ only in case — regeringen.se renders a
  diarienummer's department prefix inconsistently ("JU2026/01595" on the
  remiss vs "Ju2026/01595" on the promemoria's own listing) — used by
  `remisser/ai_analyze.py` and `render.py`'s `_remiss_indexes`.
- Wired end-to-end: `lagen remisser download [--only <url>] [--full]`
  (harvest) + `parse` Stage (recipe includes `lib/pdftext.py`); no
  `relate`/`index`/`dump`/`generate` — this source publishes nothing of its
  own. `test/test_remisser.py`, `test/test_remisser_parse.py`,
  `test/test_remisser_render.py`, `test/test_remisser_ai_analyze.py`,
  `test/test_pdftext.py` (118 tests, hermetic).

### 7i. site vertical — lagen.nu's editorial chrome ✅ (first cut)

`accommodanda/site/` carries the parts of lagen.nu that are hand-authored
prose, not extracted legal-document semantics: the curated frontpage law
list, the `/om/*` about pages, and the sitenews feed. Content is markdown in
the same `lagen-wiki` repo as `concept/`/`commentary/`, under a new `site/`
tree (`site/frontpage.md`, `site/sitenews.md`, `site/om/*.md`), populated
one-off by `tools/migrate_site_content.py` from the legacy MediaWiki
`Lagen.nu:Huvudsida` page, `lagen/nu/res/static/*.rst`, and `sitenews.txt` —
the markdown is the source of truth thereafter.

- **`model.py`**: a small block tree (`Heading`/`Paragraph`/`Bullets`/`Table`/
  `Code`/`Rule`, Swedish on-disk discriminators `rubrik`/`stycke`/`lista`/
  `tabell`/`kod`/`avdelare`) plus the three page shapes `Frontpage`,
  `AboutPage`, `Sitenews`/`NewsItem` — no `Forfattning`/`Avgorande`-style
  domain model, since there's no citation graph to hang one on. `Bullets`
  carries `ordered` (`<ol>` vs `<ul>`); a run gained `italic` alongside
  `bold`/`code`.
- **`parse.py`**: markdown → JSON artifact for three fixed basefiles
  (`frontpage`, `om/<slug>`, `sitenews`, the last split into dated
  `NewsItem`s on `## YYYY-MM-DD HH:MM:SS Title` heads). The block and inline
  layers are parsed by **markdown-it-py** (CommonMark + the GFM `table` rule,
  `html: False`) rather than a line scanner of our own — editorial content
  needs the whole ordinary markdown vocabulary (tables, ordered lists,
  emphasis), which `lib.markdown` deliberately doesn't have; `blocks(body,
  where)` walks the resulting `SyntaxTreeNode` onto this vertical's typed
  blocks and runs, raising `ValueError` naming the basefile for a construct
  with no block form (rule:errors-drive-retry-use-raise) rather than dropping
  the prose. Link *targets* still resolve through the shared
  `lib.markdown.target_uri` grammar, extended locally with site-relative
  `/…`/`#…` cross-links and `mailto:`. A generic, symmetric `sfs:`/`eurlex:`
  link scheme (`[FB](sfs:1949:381)`, `[GDPR](eurlex:32016R0679)`) was added to
  `lib.markdown.target_uri` for the frontpage's law links — the content names
  the source, never its URL shape.
- **`render.py`**: artifacts → static HTML + an Atom feed, one entry point
  `write_site(out_root)`. Registered in `build.py` as `SOURCES["site"]` with
  a `parse` Stage, but — like `remisser` — it is **absent from `ARTIFACTS`**,
  so it is never `relate`d/indexed/dumped. It *is* rendered during
  `generate`: `cmd_generate` calls `write_site` on a full run, on
  `--aggregates-only`, and on `lagen site generate`. The curated frontpage
  overwrites the generic corpus-stats `index.html` (`write_index=False`
  threaded through `render.generate_site`/`render_aggregates` when
  `has_frontpage()`); site artifacts are folded into `generate_fingerprint()`
  so an editorial edit reopens the generate gate.
- Served at `/` (frontpage), `/om/<slug>` + `/om/` hub, and
  `/dataset/sitenews/feed` (+ `.atom`) via the app's `SiteFiles` handler —
  no nginx change. New masthead entries "Om"/"Nyheter" in `lib/render.py`'s
  `MAST_NAV`.
- Wired end-to-end: `lagen site parse` (incremental) + `lagen site generate`.
  `test/test_site_content.py` (parse + render, hermetic).
- ✅ **Restored legacy per-repository feed surface** (`lib/feeds.py`) — beyond
  `sitenews`, the old Ferenda site's `/dataset/{sfs,dv,forarbeten,myndfs,
  myndprax,keyword,eurlex}/feed[.atom]` URLs (+ human-readable `/feed` twins)
  are back, with the old `rdf_type`/`rpubl_rattsfallspublikation`/
  `dcterms_publisher` query-parameter facets. `feeds.py` is one pure module —
  the legacy-alias→source map, the entry query and the Atom/HTML renderers —
  shared by static generation (`render.py` writes every dataset's feed during
  `generate`) and by two `api/app.py` endpoints that answer the same
  query-parameter URLs live off the catalog. `/dataset/sitenews` is the
  all-feeds directory page.

### 7j. HUDOC + Council of Europe treaties + ICRC IHL treaties + UN Treaty Collection + ICC and ICJ case law ✅ (first cut)

Six verticals sharing one folkrätt (international law) landing page:

- **`accommodanda/hudoc/`** harvests the public JSON endpoint used by HUDOC's
  own result UI (`/app/query/results`) and the selected document's converted
  Word HTML (`/app/conversion/docx/html/body`). Scope: two collections, each a
  download scope with its own watermark — Grand Chamber and Chamber
  **judgments** (21,672 English) and **decisions** (33,633). A decision is
  where the Court says why a complaint never reaches the merits, and it is
  where most of its Swedish output lives: 166 Swedish judgments against 922
  Swedish decisions. Committee judgments (7,541, none against Sweden, none
  carrying an importance level — settled law applied to repetitive
  violations), legal summaries, resolutions and communicated cases stay out;
  `--only <itemid>` can still fetch one deliberately. The bulk walk is
  newest-first and watermark-bounded, and **sliced by year**: HUDOC serves no
  result past `start=10000` while still reporting the true `resultcount`, so
  the original unsliced walk stopped dead at the 10,000th document and the
  store held 7,060 of 21,672 judgments, reaching back only to 2009-09-22
  (Handyside and Golder were unreachable). Years descend and each year's page
  descends by date, so the stream stays globally newest-first and the watermark
  stop is unchanged; the largest year is 1,623 documents, a year past the cap
  raises, and an exhausted enumeration checks its summed year counts against
  the collection total. English is the default expression, with `--lang
  ENG,FRE`, `--only <itemid>` and `--limit`.

  Two things the Court publishes *about* a case are linked from it rather than
  republished, and both ride along on an unbounded download — each is one index
  walk with no body fetch, and each produces an input a later stage needs, so
  neither is a command anyone has to remember (the expensive `ai-*` passes are
  the ones that stay explicit; `--only` and `--limit` skip both). `summaries.py`
  attaches the Court's own Case-Law Information Note — its plain account of what
  the case decided, 6,505 in English — joined on `(application number, date)`,
  and `hudoc parse` folds the resulting `clin/` sidecar into the artifact.
  `translations.py` drafts `commentary/hudoc/<itemid>.md` for each of
  Domstolsverkets 87 Swedish translations, joined on the ECLI, for `kommentar
  parse` to pick up: the translation says what the judgment says, so it is
  commentary on the judgment, the inverse of the English-translation link an SFS
  commentary opens with. Both joins share `download.unique_index`, which tells
  apart the two reasons two cases claim one key — HUDOC's own duplicate items
  and shared ECLIs (the key identifies no case, so it is dropped and counted:
  10 ECLIs and 121 application/date pairs over 39,046 records) from a store
  harvested in two languages, where every expression of a case repeats its
  identity and no join is possible at all (that raises). Body downloads are the cost of a run, so a
  small `ThreadPoolExecutor` (`WORKERS=4`) keeps fetches in flight ahead of the
  walk (~0.15s/doc measured, vs ~0.33s sequential — the full English harvest
  runs in about an hour). `HudocCase` projects the metadata and
  heading/numbered-paragraph body to `/dom/echr/{itemid}` artifacts. The HTML
  parser reads HUDOC's generated CSS heading styles, removes individual TOC
  links without deleting their shared judgment container, deliberately skips
  bodies with no numbered judgment paragraphs, and context-suffixes restarted paragraph
  numbering (`#P1-2`) while preserving the first canonical `#P1`.
- **`accommodanda/coe/`** harvests the Treaty Office's anonymous JSON web
  service (`conventions-ws.coe.int`, whose token is embedded in the public
  `full-list2` page) rather than scraping the Cloudflare-fronted portal HTML:
  one search POST returns all 233 treaties with metadata, `getLieux` resolves
  opening places, and each official English text downloads as a plain PDF from
  `rm.coe.int` (no challenge). The web service's TLS offers a legacy small DH
  key, hence `lib.net.mount_legacy_tls`, mounted for that host only. `Treaty`
  artifacts live at `/ext/coe/{ETS-or-CETS-number}` and carry article/subarticle
  fragments (`#A8`, `#A6P3Ld`); every official text is a PDF, so `parse.py`'s
  body path is uniformly `pdftohtml -> page_paragraphs -> build_structure`.
  Numeric, Roman and compound article designations are supported; exceptional
  section-only amending instruments use `sektion` provisions. Repeated printed
  article/paragraph/list designators retain their first canonical fragment and
  receive contextual occurrence suffixes thereafter, so artifact IDs remain
  unique even across annexes, replacement text and editorial footnotes.
  Treaty summaries sit behind the scraped portal and are not carried on the
  record.
- **`accommodanda/icrc/`** harvests the ICRC's own anonymous Drupal 10
  JSON:API (`ihl-databases.icrc.org/en/jsonapi/node/treaty`) rather than its
  React front end: one paginated list call (page size 50) enumerates the 111
  IHL instruments — the four 1949 Geneva Conventions, their Additional
  Protocols, the Hague law and the weapons/cultural-property regimes — and
  one per-treaty `include=`-expanded fetch returns the whole self-contained
  envelope: metadata, the authentic article text
  (`field_treaty_content`), and per-state participation
  (`field_treaty_state_parties`) with depositary/topics/languages resolved as
  taxonomy terms. Unlike coe, there is no PDF: the stored record is the raw
  JSON:API envelope, so `parse.py` is pure and offline (article body HTML →
  stycken via BeautifulSoup; commentary front matter — ToC/Foreword/
  Introduction — is dropped). Incremental via the node's `changed` stamp plus
  `HarvestWatermark`; `--only <ICRC-number>`, `--limit`, `--force`. `Treaty`
  artifacts live at `/ext/icrc/{ICRC-number}`; the URI grammar stays local to
  the vertical rather than in `lib` — nothing else mints an ICRC target yet
  (rule:second-use-goes-to-lib). `icrc/data/names.json` curates the four
  Geneva Conventions and three Additional Protocols (ICRC numbers
  365/370/375/380/470/475/615) with informal Swedish names and acronyms (GK
  I–IV, TP I–III), surfaced first on the folkrätt landing under
  "Genèvekonventionerna och tilläggsprotokollen"; every other instrument
  lists A–Z under "Övriga instrument", the same landing-only pattern as coe (no
  faceted browse tree of its own). This is a first cut of treaty ingest only;
  ICRC/IHL caselaw is out of scope.
- **`accommodanda/untc/`** harvests the UN Treaty Collection's Multilateral
  Treaties Deposited with the Secretary-General (MTDSG) register: a curated
  list of 14 instruments (`untc/data/treaties.json`, one harvest engine over
  all — rule:configured-by-data) — VCLT, UNCLOS, the Genocide Convention, the
  core human-rights instruments (ICERD, ICESCR, ICCPR, CEDAW, CAT, CRC, CMW,
  CRPD, CED) and the Refugee Convention plus its Protocol. Each treaty is one
  static-HTML fetch from `ViewDetailsIII.aspx` (an ASP.NET page that answers
  unattended clients directly, no challenge); the corpus is tiny and fixed, so
  the harvest is a plain loop, skipping a page already on disk unless `--full`
  re-fetches it (a new ratification changes the participation table). The
  MTDSG carries **status only, not treaty text** — a treaty's authentic text
  lives in per-treaty UNTS PDFs outside this uniform scrape — so `structure`
  is deliberately empty and the artifact is metadata (conclusion/entry into
  force/UNTS registration) plus the participation list, with the rendered
  page linking out to the UN authentic text. `parse.py` scrapes the page's
  stable ASP.NET control ids and the participation grid, anchored on the
  grid's own control id (`tblgrid`) rather than a header cell, since some
  treaties precede it with a decoy territorial-notification table under the
  same "Participant" header; footnote `<sup>`s are stripped, and each
  participant's consent form (accession/succession/formal
  confirmation/acceptance/ratification) is read off a case-sensitive trailing
  marker. `Treaty` artifacts live at `/ext/untc/{unts}` -- the UNTS
  registration number, not the MTDSG id (see the text half below); the URI grammar
  stays local to the vertical (rule:second-use-goes-to-lib). The folkrätt
  landing's UN half groups the curated instruments by subject (Traktaträtt
  och havsrätt / Mänskliga rättigheter / Flyktingrätt), each group
  chronological — the same landing-only pattern as coe/icrc, no faceted
  browse tree of its own.

  **The text half (2026-08-13).** The MTDSG carries status and no treaty
  text, so these artifacts shipped six metadata rows and an empty
  structure — nothing for a citation to land on, which is why the 327 ICJ
  references could only name the instrument and not its article. Each
  treaty is now fetched twice: the status from the MTDSG, the authentic
  text from its own depositary. **Not from the UNTS**, which reproduces
  each instrument as registered and so is a scanned corpus — volume 999
  carries the ICCPR over 92 pages with an image on all 92, volume 1161 the
  Berne Convention over 44 of 44, and there is no API. `text.py` reads
  OHCHR's HTML for twelve and a born-digital PDF for VCLT and UNCLOS,
  giving 1,020 articles over 1,043 nodes (14 preambles, 9 annex headings)
  with every anchor unique — `unique_id` has nothing left to disambiguate.
  The count is exact against the instrument itself for all fourteen
  (Genocide 19, ICCPR 53, VCLT 85, CRC 54, CMW 93, UNCLOS 320 plus 125
  over its nine annexes).

  A published PDF is not only the treaty, and four rules earn their place
  from what that costs (2026-08-14). **A contents block is cut whole, not
  line by line**: UNCLOS opens with 33 pages of contents that set each
  entry's `Article N.` and `ANNEX I.` on lines of their own, with the
  dotted leader only on the title beside them — dropping the leader lines
  alone counted 885 articles against 320 and filed 444 provisions under
  Annex IX. **A leader is five dots**, because three is the ellipsis the
  Refugee Protocol sets inside its article 1(2), and reading that as a
  contents entry dropped the paragraph defining who the Protocol covers.
  **A second contents block ends the treaty**: the same PDF prints the
  conference's Final Act after the Convention, whose Annexes I, II and VI
  would otherwise claim the anchors of the Convention's own. An annex both
  scopes the articles under it (UNCLOS restarts at Article 1 nine times)
  and is a provision itself, since Annex I is a list of 17 species with no
  article at all — it used to print under article 320, "Authentic texts".
  **A contents block is at least five leader lines**: one dotted line is a
  line of the treaty — a schedule row, a tariff table, a signature page —
  and cutting at it drops every article above it and publishes the rest
  green. The whole mechanism is calibrated on the one PDF in the corpus
  that has a contents block at all, so the guard is for the fifteenth
  treaty. Two invariants back it: an article heading with no text under it
  is a contents entry, not an article (it would take the plain `#A5`
  anchor and leave the real article 5 with a `unique_id` suffix), and each
  curated entry carries the treaty's own article count, which the parse
  must match or raise.

  The page renders that text through the same `page.provision_section`
  walk `icrc` uses, so `/untc/I-31363#A74` now lands on Article 74; until
  2026-08-14 the renderer read only the participation table and every
  article anchor the ICJ and ICC references mint was a link to nothing.
  **Open:** the two PDF treaties keep one paragraph per source line, where
  the twelve OHCHR ones carry whole paragraphs. A join-unfinished-lines
  reflow was measured and withdrawn: it reads an article's rubric as the
  first sentence of its body (UNCLOS article 192 became "General
  obligation States have the obligation to protect…"), so the rubric needs
  handling of its own before any reflow lands.

  **The identity is now the UNTS registration number** in the UN's own
  form (`I-14668`, as in `volume-999-I-14668-English.pdf`), replacing the
  MTDSG chapter id: it is what the UNTS cites itself by, and it survives
  for an instrument whose depositary is not the UN, where an MTDSG id does
  not exist. The 14 stored pages were relocated rather than re-fetched.

  **Standing scope goal**: a convention is in scope if any Swedish
  förarbete or eurlex document cites it — measured from the corpus, not
  curated by hand — and its text comes from its depositary (WIPO Lex for
  Berne and the IP treaties, HCCH, ILO NORMLEX), keyed on its UNTS number.
  The curated 14 are the first cut, not the target.
- **`accommodanda/icc/`** harvests International Criminal Court case law —
  the curated ~269-decision substantive set (Rome-Statute verdicts,
  sentences, confirmation, arrest warrants, appeal judgments, reparations,
  investigation/admissibility/prosecutor-review decisions), not the ~10k
  procedural mass. Two Cloudflare-free sources, since the ICC's own
  `/court-record` detail pages are Cloudflare-walled: icc-cpi.int
  `/decisions` is server-rendered and facetable by
  `decision_type_of_decision` — the curated facet ids
  (`icc/data/decision_types.json`) scope the harvest and yield each
  record's document number — and the ICC Legal Tools API
  (legal-tools.org, a React SPA over a LoopBack JSON backend) resolves a
  document number to the decision's metadata and PDF via
  `GET /api/ltdDocs?filter={"where":{"externalId":{"like":"<base
  number>"}}}`, picking the English primary among translation variants
  (case-sensitive prefix match: the scrape gives `-red`, Legal Tools
  stores `-Red`). 268/269 decisions resolved with text; the one Legal
  Tools can't resolve stays metadata-only (empty structure), like a
  status record. `Decision`/`Block` (HUDOC-shaped) project to an
  `avgorande`/`icc` artifact whose numbered paragraphs become the
  citation-unit article tree (`P<n>` ids); `parse.py` extracts the PDF via
  `lib/pdftext`, strips the per-page court-record running header, and
  classifies numbered paragraphs vs. section headings. `Decision`
  artifacts live at `/ext/icc/{doc-number}` (slashes flattened to
  underscores); the URI grammar stays local to the vertical
  (rule:second-use-goes-to-lib). Swedish relevance: Sweden is a
  Rome-Statute party (incorporated via lag 2014:406) and Swedish courts
  apply international criminal law in universal-jurisdiction cases; the
  Inter-American and African human-rights courts were deliberately *not*
  added alongside it — not binding on or applied in Sweden, comparative
  only. The folkrätt landing lists ICC decisions grouped by Rome-Statute
  decision type, newest first per group, under "Internationella
  brottmålsdomstolen (ICC)"; like coe/icrc/untc it has no faceted browse
  tree of its own. Wired through `build.py`, `layout`, `catalog`,
  `facets`, `datasets` and `render`. `test/test_icc.py` (20 tests) runs
  off a stored-record fixture (`test/files/icc/ICC-01_04-02_06-2359.json`)
  plus pure unit tests of the PDF-paragraph classifier, the Legal Tools
  footer furniture rules and the sibling-filing citation matcher — no
  network, no PDF binary. A real download+parse+relate+generate harvest has
  run: all 269 curated decisions are live on `/folkratt/` and
  `/icc/{doc-number}`.
- **`accommodanda/icj/`** harvests International Court of Justice case law —
  255 of the Court's 877 decisions: 158 judgments, 31 advisory opinions and
  the 66 orders that indicate provisional measures. The ~620 docket orders
  that fix and extend time-limits for the Memorial and the Counter-Memorial
  are deliberately out; they are bookkeeping, not a reader's document.
  Written and oral pleadings are out for the same reason the remissvar are:
  they are the parties' word, not the Court's. The PCIJ series (1922–1946)
  is a different harvest, not a bigger one — mostly French, with pleadings
  mixed into the judgment files and no consistent language code.

  **Why it was worth adding, given that no Swedish court cites it.** Two
  reasons, both measured. The corpus already held the treaties the ICJ
  interprets — the Genocide Convention, the VCLT, UNCLOS, the ICCPR and CAT
  in `untc`, the Hague Regulations and Geneva Conventions in `icrc` — and
  those 125 pages had **zero** inbound links: nothing in 296,240 documents
  cited them. And the corpus already cites the Court itself and resolves
  nothing: 1,669 hits for "Internationella domstolen" (1,525 of them in
  förarbeten, 41 in SFS) and 403 for the official "ICJ Reports" citation
  form (294 from `hudoc`, where the Strasbourg court cites The Hague).
  Sweden is also a party in three ICJ cases, one of them squarely Swedish
  law: *Guardianship of Infants* (Netherlands v. Sweden, 1958) turned on a
  barnavårdsnämnd's skyddsuppfostran against a Dutch guardianship order.

  **Transport.** One index, two routes. The Drupal view at
  `icj-cij.org/decisions` answers ordinary HTTP and returns the whole
  877-row history in one request with `from=1946` — its default is
  `from=2023`, which shows 87, and it does not paginate. No `to` is sent:
  the select only offers years up to the current one and answers an
  out-of-range year with an empty result page under a 200, so pinning an
  upper year would harvest nothing the first January after it went stale.
  The decision PDFs are behind a Cloudflare challenge that no header or
  cookie from the index clears, so they come through
  `lib.browser.DetachedChrome` — the headful transport `rs` and
  `foreskrift` already use — at about 9 s per document.

  **Reading the printed Reports.** The Court publishes each decision as its
  page range from the bound *I.C.J. Reports*, so every PDF opens with the
  publisher's front matter (a bilingual cover, the official-citation page,
  and since 2012 a table of contents) before the decision starts.
  `body_pages` cuts at the Court's own letterhead over a `YEAR` line;
  keying on the letterhead alone starts at page 1 and keeps the French.
  Everything before ~mid-2004 is a scan with an OCR text layer — the split
  is not a date but a measurement (a scanned page range carries a raster
  image on every page, 0.95–1.00 of them, against 0.00–0.03 for a typeset
  one), and a date rule would have been wrong: the July 2004 Wall opinion
  is a scan and the December 2004 judgment in the same volume is not.

  `icj/ocr.py` repairs that layer's systematic confusions, measured at
  ~0.43% of tokens over ten decisions and dominated by `l` read as `1`
  ("al1" for all, 400 occurrences) and `m` split into `rn` ("Judgrnent",
  "Charnber", 235). The repair is dictionary-guided rather than a list of
  known bad words: a token is rewritten only when one confusion turns it
  into a word the Court itself uses and it is not already one, and two
  candidate readings mean no rewrite. The vocabulary
  (`icj/data/vocabulary.txt`, `tools/icj_vocabulary.py`) comes from the
  born-digital decisions, which need no repair — so the corpus defining "a
  word" never depends on the repair being right.

  **The paragraph is the citation anchor.** The Reports set a numbered
  paragraph flush with the one above it, so `page_paragraphs` hands back a
  whole run of reasoning as one block (4,900 characters holding paragraphs
  1–5 of the 2024 Gaza order). `paragraph_chain` cuts those runs by finding
  the Court's own numbering among every number in the text: it takes every
  candidate in reading order and keeps the longest chain that counts up in
  steps of at most four, provided the chain either opens at the Court's
  first paragraph or is long enough that its length is itself the
  evidence. That is what tells the Court's "5." from "Article 5. The
  Parties" and from an ICTY paragraph 531 the Court block-quotes inside
  its own paragraph 309, what keeps one hole from costing every paragraph
  after it, and what stops a lone stray number from becoming a citation
  anchor. A separate or dissenting opinion restarts at 1 and forms its own
  shorter chain, so only the Court's own reasoning is anchored.

  Where the repair count reaches five the page carries a banner saying the text
  saying the text was read off the printed Reports and that the Court
  states the printed version is the official one — evidence from a real
  count, not a guess from the date. Reuse is under the ICJ's non-commercial condition, which
  lagen.nu meets; this is not the 2 § URL freedom SFS and propositioner
  have, and it was weighed before the work started. Artifacts live at
  `/ext/icj/{stem}`, keyed on the Court's own decision filename
  (`070-19860627-JUD-01-00` = case, date, kind, part); the grammar stays
  local to the vertical (rule:second-use-goes-to-lib). The folkrätt landing
  lists the decisions grouped by kind (Domar / Rådgivande yttranden /
  Interimistiska beslut), newest first per group, under "Internationella
  domstolen (ICJ)"; like coe/icrc/untc/icc it has no faceted browse tree of
  its own. Wired through `build.py`, `layout`, `catalog`, `facets` and
  `render`. `test/test_icj.py` runs off a stored-record fixture, pure unit
  tests of the OCR repair and the paragraph chain, and three
  `lines-*.json` fixtures that freeze what `pages_with_ocr` really returns
  for a page range of a stored decision. That last group exists because
  every test written against hand-made strings passed while the corpus
  carried three separate defects (rule:lock-in-with-fixture).
- **Treaty citations (2026-08-14):** `lib/treatyref.py` is the second-use
  shared seam for the two international courts, reading its curated names
  from `lib/data/treaty_names.json` through `lib.datasets` so no source is
  imported. Before it, `icc`'s 269 decisions carried an empty `references`
  and the whole `icrc` corpus -- the Rome Statute among it -- had **zero**
  inbound links, while the decisions were made of those citations: 13,887
  "article N ... of the Statute" forms across 244 of the 269. The matcher
  now yields 2,867 references from 250 ICC decisions (2,431 article-level,
  2,307 onto the Rome Statute) and 1,050 from 163 ICJ ones (669
  article-level), over 21 instruments.

  Five rules earn their place, each from a measured failure: an article
  binds to the **nearest** instrument named, not to every one in range
  (binding to all read "article 3 common to the Geneva Conventions" as
  Rome Statute article 3); a name that **follows** the article beats a
  nearer one before it, because `article N of X` has a direction; an
  article followed by "of \<Instrument\>" never binds **backwards**, so an
  instrument the corpus lacks yields nothing rather than a wrong guess
  (the ICC writes "Covenant *of* Civil and Political Rights" where the
  curated name says "on", and article 9 was being filed against the Rome
  Statute); and **roman** article numbers resolve, since the Genocide
  Convention runs Article I to XIX and the ICJ cites it that way -- an
  Arabic-only pattern missed the corpus's most-cited instrument; and an
  **enumeration** cites every article it names, "articles 15, 53, 54, 58
  and 61 (5) of the Statute" being five citations and a range ("articles 6
  to 8") three, where reading the first two left 568 article numbers
  across 120 ICC decisions unlinked. A bare comma does not join a list:
  "Article 58, 10 February 2006" is an article and a date, so a list is
  only read where it closes with "and", "or" or "to". A name shared by
  several instruments resolves to all of them: common article 3 really is
  an article of each of the four Geneva Conventions.

  **The anchor audit** (`catalog.dangling_anchors`, run at the end of
  `relate`) is what would have caught the defect that started this: 126
  treaty references pointed at an `#A42` on a Hague Convention that
  anchors its Regulations' articles under `#Annex42`, and every count
  involved -- links written, documents related -- looked healthy. It is
  scoped by the source of the document a link points *at*
  (`build.ANCHOR_EXACT`), and that scope is load-bearing rather than a
  convenience: only `icrc` and `untc` render every provision through
  `page.provision_section`, which anchors on the artifact's own node id.
  Every other source mints anchors at render time that no `structure` node
  holds -- sfs a change-act anchor per amendment, eurlex a stycke alias,
  förarbete a page marker, coe a sub-paragraph pinpoint -- so asked of the
  whole corpus the audit reports 1 612 832 live links as broken, in 72 s.
  Scoped it costs 0.1 s.

- **Inline citations (2026-08-14):** the references above landed as
  document-level `references` only -- a citation in the rail, not a link
  where the reader meets it in the text. `lib/artifact.py`'s
  `numbered_nodes` now takes an optional `refs_for` (text -> `[lagrum.Ref]`)
  scanner, so a source that can resolve some of its own English prose gets
  those spans as inline links in its runs; `treatyref.spans()` is the
  `(start, end, uri)` projection `references()` was missing for it. `icc`
  and `icj` wire it through `treaties.refs`, and `icc` adds one thing
  `treatyref` cannot know locally: a decision cites its siblings by filing
  number ("ICC-02/11-01/11-129") on nearly every page, and 1,687 of those
  point at a decision the corpus holds (resolved through whichever variant
  -- a `-Red` redaction, a `-Corr` -- is actually on disk). The pure leaf
  grammar the two modules share (`arabic`/`roman`/`article_fragment`) moved
  out to `lib/treaty_ids.py`, on the same rule as `lib/coe_ids.py`: `lagrum`
  needs the anchor grammar too (below) and importing `treatyref` itself
  would close `lagrum -> treatyref -> catalog -> markdown -> lagrum`.

  Two more citation grammars joined the same seam, each source's own.
  `hudoc/citations.py` resolves a judgment's case-law citations -- "Keenan
  v. the United Kingdom, no. 27229/95" -- against the held corpus's own
  metadata: 88% of the ~175,000 application-number citations across 13,567
  judgments name a document already held, plus the named-case form for a
  citation with no number. Both refuse rather than guess where a number or
  name is borne by more than one held document and no printed date or
  document-kind test picks a single one -- a chamber and a Grand Chamber
  judgment of the same case are different documents (rule:fail-fast).
  `hudoc/casenames.py` turns that same index into a committed join surface
  for the *other* direction -- a Swedish förarbete naming "Osman mot
  Förenade kungariket" -- since `lib` cannot read a vertical's stored
  records (rule:second-use-goes-to-lib) the way `dv`'s and `eurlex`'s own
  named-case snapshots ship theirs: `lagen hudoc casenames` writes
  `hudoc/data/casenames.json` (37,544 case names, 93,781 application
  numbers, every `[kind, date, itemid]` candidate rather than one picked
  winner, keyed on `citations`' own normalized `applicant|respondent|serial`
  and on the appno), read back by `lib.datasets.load_emd_cases`. A
  hand-edited `hudoc/data/respondents_sv.json` maps the Swedish respondent
  names onto the snapshot's normalized respondent keys. `lib/emdref.py` (2026-08-15)
  is the `lagrum` parse type (EMDRATTSFALL) that resolves a Swedish citation
  through it -- see below.
  `icj/reports.py` reads the Court's own citation grammar, "I.C.J. Reports
  1990, p. 92": each decision's official citation sits on its own PDF cover
  sheet (227 of the 255 held covers yield one -- `pdftotext` first, the OCR
  route only where that fails) and is stored as `metadata.reportsCitation`;
  a body citation whose (year, page) is a held decision's own *start* page
  links to it. Pinpoint cites (a page reaching into a decision, not its
  first) stay unlinked on purpose: with 255 of the Court's 877 decisions
  held, the gap between two held start pages says nothing about the unheld
  decisions between them, and attributing a pinpoint to the nearest held
  start would mislink it (rule:fail-fast).

  `lib/lagrum.py`'s FORARBETEN grammar picked up four fixes alongside this:
  kommittédirektiv ("dir. 2016:73" -> `/dir/2016:73`); the dot-dropped
  prop./rskr. forms tables print ("prop 1999/2000:111"); a four-digit
  second riksmöte year within the same century ("2008/2009") folding to the
  corpus's "2008/09" (leaving "1999/2000", the one riksmöte that genuinely
  crosses a century, alone); and Swedish treaty names ("artikel 24 i
  barnkonventionen") linking `untc`/`icrc` targets with a correct article
  anchor, read from `treaty_names.json`'s `names_sv` through the same
  `treaty_ids.article_fragment` grammar `treatyref` anchors on. A fifth fix
  is unrelated to treaties: the EU akttyp terminals (DIREKTIV, FORORDNING,
  ...) now match the definite form too ("förordningen"), closing a mislink
  where "artikel 30 i förordningen (EG) nr 765/2008" fell through to the
  anaphora branch and pinned the article on whichever act was last in
  focus, because the definite noun didn't parse as naming its own act
  (observed in SOU 2021:44).

- **Swedish ECHR citations, sibling-treaty citations, and three more
  grammar productions (2026-08-15):** `lib/emdref.py` is EMDRATTSFALL, the
  matcher the previous entry left "designed, not yet written" -- ECHR case
  law in *Swedish* text ("Osman mot Förenade kungariket", "ansökan nr
  23452/94") over the committed `hudoc/data/casenames.json` snapshot joined
  through `respondents_sv.json`, with `hudoc/citations.py`'s disambiguation
  ported verbatim (a printed date wins; else the sole judgment; several
  candidates and no date stay unlinked). It has no grammar half, so
  `LagrumParser.parse_text` merges its spans beside the Lark tree's,
  grammar winning any overlap.

  `hudoc/treaties.py` links a judgment's own Convention/protocol short
  forms ("Article 8 of the Convention", "Article 1 of Protocol No. 1") over
  `lib.treatyref`, with the local knowledge only an ECHR text can supply:
  "the Convention" is the ECHR (guarded so "the Convention on the Rights of
  the Child" keeps naming the CRC) and "Protocol No. N" numbers the ECHR
  protocol series specifically, since the same words number a different
  family on a CoE treaty page. `hudoc/parse.py`'s `refs_for` now merges it
  with `citations.refs`, case law winning an overlap. `coe/parse.py`'s
  `build_structure` and `icrc/parse.py`'s new `artifact()` wrapper both gained
  the same `refs_for` shape for the sibling instruments a treaty's own text
  names (a protocol citing the Conventions it amends, an Additional
  Protocol's preamble citing the four 1949 Conventions) -- self-citations
  excluded, since a treaty's own title is a description, not a citation.
  `lib/treatyref.py` gained `generic_names`/`generic_context`: every treaty
  family numbers its protocols ("Second Additional Protocol"), so a bare
  ordinal name now binds only within `CONTEXT_WINDOW` (150 chars) of its
  family's own name being present -- "Additional Protocol II to the Geneva
  Conventions" binds, the bare "Second Additional Protocol to this
  Convention" on a CoE page does not. `treaty_names.json` gained the eight
  ECHR protocols carrying their own articles (coe/009/046/114/117/177/187/194/214)
  as curated targets.

  Three more `lagrum` productions, all measured against the held corpus:
  STALLNINGSTAGANDE links Skatteverket rättsliga ställningstaganden by their
  diarienummer shape alone ("dnr 131 599911-10/111" → `rs/skv/131-599911-10-111`),
  since a title or date routinely separates the dnr from the word
  "ställningstagande" and the shape itself is unlike any other agency's dnr;
  `jo_arsb_ref` resolves the printed-ämbetsberättelse citation form ("JO
  2003/04 s. 450", no dnr) through a new committed snapshot,
  `avg/data/arsberattelse.json` (`avg/arsberattelse.py`, `lagen avg
  arsberattelse`, sweeping the JO artifacts' own `officialReport` field --
  1,607 of 1,608 artifacts at the 2026-08-15 census, the one leftover
  reported rather than mapped); `so_ref` reads "SÖ 1982:50" (Sveriges
  internationella överenskommelser) as a förarbete-shaped document ref.
  Alongside them, two corpus-measured fixes: the citation scan now runs over
  a width-preserving whitespace normalization (U+202F/U+00A0 → space) that
  recovers 1,339 "NJA 1991 s. 567"-shaped citations HD's 2016-2020 referat
  typography had put out of the grammar's reach, and the letterless CJEU
  case form is now year-bounded to 1954-1989 (the T-/C- split) so "i mål
  23452/94" -- an ECHR application number wearing the same shape -- no
  longer mints a celex that does not exist.

  `build.py` gained the `avg arsberattelse` action, and a `lagen all
  <action>` sweep now skips (rather than hard-errors on) a source that has
  no such stage/action, the same shortcut the download branch already took
  -- a *named* source still gets the hard error.

- **Identity and graph:** `lib/coe.py` is the second-use shared seam. HUDOC's
  article facet codes (`8`, `6-3-d`, `P1-1`, `P7-4`) map protocol numbers to
  their Treaty Office ETS/CETS instruments and mint exactly the provision URI
  the treaty parser produces. HUDOC stores those as generic top-level
  `references`; `catalog.artifact_links` consumes that source-neutral contract,
  so an ordinary `relate` makes each case inbound on the cited treaty article
  and the existing rail displays "Europadomstolens praxis" there.
- **SFS bridge:** the ECHR instruments actually reproduced in SFS 1994:1219
  (Convention plus Protocols 1, 4, 6, 7, 13 and 16) carry an `rdfs:seeAlso`
  document edge to that SFS. Protocol 12 is intentionally excluded. The CoE
  articles remain the canonical provision nodes. `sfs/parallelappendix.py`
  models the incorporated appendix as aligned instruments, sections, articles
  and paragraphs with stable local fragments — the base convention at `#B1`,
  each protocol at `#B1P<n>`. The generic parser has no treaty-identity lookup;
  the projection resolves each fragment through the curated
  `sfs/data/incorporates.json` (`{sfs}#{fragment}` → `source/number`, eg.
  `coe/046`), so the SFS projection emits the reverse link from those local
  fragments to CoE — a table, not a parsing rule. Such links are `rdfs:seeAlso`,
  not `owl:sameAs`: one SFS article row contains three language versions while
  the Treaty Office artifact is the official English source.

Wired through `build.py`, `layout`, catalog, facets, search/dump and static
rendering; `test/test_{hudoc,coe}.py` includes an end-to-end catalog assertion
that a HUDOC Article 8 edge appears inbound on ETS 005 `#A8`. `icrc` and
`untc` are wired the same way (`build.py`, `layout`, catalog, facets,
`lib/render.py`'s folkrätt landing); `test/test_icrc.py` (10 tests) runs off a
trimmed real Geneva Convention I JSON:API envelope fixture
(`test/files/icrc/365.json`); `test/test_untc.py` (10 tests) runs off a
synthetic trimmed MTDSG fixture (`test/files/untc/I-18232.html`) plus the
depositary's own VCLT PDF, whole, because the article count is an invariant —
both no network. `untc` has run a real download+parse+relate+generate harvest:
all 14 curated treaties are live on `/folkratt/` and `/untc/{unts}`. `icc` is
wired the same way; see its own bullet above for its test/harvest status.

### 7k. stats vertical — 53 measurements of the corpus ✅ (first cut)

`accommodanda/stats/` inverts every other vertical's direction: there is
nothing to download and no document to parse, because the corpus *is* the
input. It reads the finished catalog and artifact trees, writes one artifact
holding 53 measurements, and renders that to `/statistik`. The measurement
catalog — each number with its provenance and its status — is
[`docs/prd-stats.md`](docs/prd-stats.md).

- **Two verbs, deliberately split.** `lagen stats compute` measures (minutes;
  it walks the sfs, eurlex, förarbete and dv artifact trees over a
  `ProcessPoolExecutor`, so it must run after `relate`, which the catalog half
  reads); `lagen stats generate` renders the artifact to the page. The split is
  what makes the numbers *diffable* between builds — two artifacts compared say
  what actually moved in the corpus — and it keeps the architecture's rule
  intact: the artifact on disk is the source of truth, the page is a pure
  projection that cannot say anything `compute` did not measure. Split verbs,
  but both ride a whole-corpus `lagen all rebuild`: `/statistik` has no catalog
  rows, so `render.generate_site` never reaches it and the full-corpus generate
  writes it explicitly beside the editorial pages. Wiring only `compute` into
  the rebuild recomputed the measurements and then republished the previous
  run's page.
- **Not incremental, on purpose.** Every measurement is a fact about the whole
  corpus, so there is no subset of it that could be refreshed on its own; the
  freshness question is "has anything anywhere changed", which only the operator
  can answer. The stage accordingly declares no per-document `inputs` and is
  marked `Stage(..., always=True)` (§5), so it carries no freshness gate at
  all — every invocation re-measures, with or without `--force`. Plain
  no-`inputs` freshness (fresh-by-default) would have been wrong here in the
  other direction: it exists for a stage like `download`, where an existing
  output already answers the only question there is; `stats compute`'s
  question is never settled, so it needed the opposite default. `run_action`
  now runs a single-basefile action in-process rather than through the worker
  pool — not just an optimisation: a pool worker is daemonic and cannot spawn
  the children `stats compute` fans its scan over. Each run also archives the
  artifact under its own date (`layout.stats_snapshot`,
  `artifact/stats/archive/statistik-<date>.json`, same bytes as the live
  artifact) — the only way to answer "how has the corpus changed" rather than
  just "how big is it now" — kept indefinitely (~15 KB/day). `stats` is absent
  from `ARTIFACTS` and its source lists a single basefile verbatim, which is
  what makes an `archive/` subdirectory safe under `artifact/stats/`: nothing
  globs that tree. `compute` is now wired into `lagen all rebuild` itself
  (`build.cmd_all`), between `dump` and `generate` — it needs the catalog
  `relate` just rebuilt and the artifact trees `parse` just wrote, so it
  cannot join the parse loop, which runs before `relate`. Gated on
  whole-corpus runs only, so a single-source rebuild does not pay for it.
- **`model.py`** — `Measure`, whose `kind` (`scalar`/`toplist`/`series`/
  `histogram`/`bars`/`matrix`/`sankey`/`table`) is the on-disk discriminator the renderer
  dispatches on, chosen by *what the data's job is* so the renderer never guesses
  a chart form. `Report.to_artifact()` prunes a measure's empty fields — writing
  all twelve keys on all 53 triples the artifact and makes a diff unreadable, and
  the diff is the point of storing it. `note` is where a measure could admit a
  population caveat beyond its `lede`, rendered *on* the figure; every use
  was pulled at the user's request in favour of folding the same information
  into each measure's `lede`, so the field is currently unused rather than
  retired.
- **`scan.py`** — the expensive half, kept separate so the artifact walk is one
  place and one shape (pure, process-safe, plain tuples out). It owns the two
  measurement rules that silently poison whole families of numbers: **table cells
  count as text** (a `rad`'s `cells` are a list of *run lists* — two levels deep,
  which is exactly what makes a naive read of them come back empty, leaving a
  definition paragraf measuring only its "I denna lag betyder" stem) and
  **provenance markers and renumbering stubs do not** (counted naively, "*Lag
  (2011:590).*" is the shortest rule in Swedish law). It also reads
  `downloaded/sfs/` for change-act titles, which the artifact does not carry.
- **`compute.py`** — the 53 measures in seven groups (A–G), preferring catalog
  SQL over the scan wherever the data is in the catalog. That preference is also
  the roadmap: every measure reaching for `scan` today is one `relate` could
  serve from SQL tomorrow (the PRD's R1–R3). Three measures the first cut left
  out are now in — `text_age` (a statute is a mosaic of paragrafer of different
  ages; the register says which amendment last touched each one), `notice_days`
  and `bill_lag` — each extracted as a pure helper so its population rule is
  testable rather than buried in a builder. Two of the PRD's posts remain
  unbuilt: 45 (share of decisions carrying a curated name) and 48 (which statute
  authorized the most agency regulations). **The default population is gällande
  rätt**: `_in_force` narrows the scan's `laws` to statutes actually in force
  once, before any measure runs, and keeps the unnarrowed list as `laws_all`
  for the four that need the whole history by name — when ikraftträdande has
  fallen historically (27), how much notice a new law has given (28), and the
  proposition/bill lineage measures (42, 43) — where counting only survivors
  would describe which laws happened to last rather than the lawmaking itself.
  Narrowing once here rather than at each call site means reaching for the
  whole history is always a visible, named decision in the measure that makes
  it.
- **Defined terms (53, 54).** Which act states the most legal definitions, and
  which begrepp the corpus defines in the most different ways. A definition is
  whatever the corpus marks as one: an eurlex definitions-article point
  (`eurlex.definitions` stamps `defines` at parse time) and every SFS term run
  `sfs.begrepp` mints, in all four of its modes. A brottsrubricering ("… dömes
  för fyndförseelse till böter") and a parenthesised coinage ("… (dödning)")
  state a definition too — the hard part is only telling which words of the
  sentence *are* the definition, and neither measure needs to know: the text
  does one job, telling two definitions of the same term apart, so the whole
  node is the unit. The finer sentence pick belongs to
  `catalog.definition_sentences`, which quotes the text on the begrepp page and
  does have to get the boundary right.
  - Two exclusions, both about what the corpus is rather than what a definition
    is. A definition that only points elsewhere ("personuppgifter:
    personuppgifter enligt definitionen i artikel 4.1 i förordning (EU)
    2016/679") states none of its own. And a superseded temporal wording is not
    the law today: PBL 1 kap. 4 § stands in the artifact twice, once expiring
    2027-01-01 and once entering into force then, which would give the act 62
    definitions where it states 34 (`scan.superseded_variant` — `sfs.nf`
    suppresses the id of the variant out of force, so an id-less node carrying
    an `upphor`/`ikrafttrader` date *is* that variant).
  - Measure 54 keys on the **begrepp**, not on the surface form the act happens
    to write: a term's identity here is its begrepp uri after the inflection
    fold (`catalog.canonicalize_concepts`), which is what the page the row links
    to counts. Keyed on the surface form instead, 382 concepts split in two
    (`Personuppgift` into personuppgift + personuppgifter) and the row printed a
    number the page it points at contradicts. Two definitions count as one when
    their text is the same, so NIS2 art. 6.9 and CER-direktivet art. 2.6 are two
    definitions of "risk" — they differ by three words.
- **Where a measure's population had to shrink, it says so on the figure.**
  Notice period is a measure of *base statutes* only: the amendment register
  carries `rpubl:utfardandedatum` on 11 of 50,948 entries and the download tree
  has none at all, so the same curve drawn over changes would describe
  registration practice rather than lawmaking. Bill-to-law lag excludes the
  5,106 of 8,822 dated propositions stamped 12-31 or 01-01 — a year written as a
  date, which would put a spurious ±6 months on every old bill. Text age
  excludes laws whose register dates its amendments but does not name what they
  touched, since every paragraf would otherwise fall back to the law's own year
  and the law would read as wholly original.
- **`charts.py`** — form follows `kind`. Ranked things become bar *tables*, not
  SVG: the labels are Swedish statute titles running to 90 characters of
  "Kungörelse om tillämpning av …", and SVG text can neither wrap nor ellipsize,
  so the label wraps like prose and the bar is a CSS width on the value cell —
  which makes the accessible table view *be* the chart rather than an alternative
  to it. Series and distributions are SVG; the matrix is a log-scaled heat table
  (its largest cell is four orders of magnitude above its smallest, and on a
  linear ramp every cell but one reads as empty). Measure 29 is a `sankey`: the
  citation graph as volume, citing group on the left, cited group on the right,
  the same groups standing on both sides so a source citing itself is a ribbon
  like any other. The map holds fourteen groups; a group with no traffic above
  the drawing threshold on one side simply does not appear there, which is how
  Konventioner (1.6 M references in, 437 out) reads as the dead end it is. Its
  groups are mostly the source itself, except eurlex (three
  nodes: treaties, acts, case law — they behave differently) and the
  international-law sources (two: treaty text, case law). Ribbon thickness is
  linear in the count, with a floor, because the corpus spans four orders of
  magnitude; a node bar is the sum of its ribbons *as drawn*, and the number
  beside it is the group's whole volume. Every chart is single-series — the
  corpus has one value per year, per bin, per law, and a ribbon is named at both
  ends — so nothing encodes identity by colour and there is no categorical
  palette or legend.
- Absent from `ARTIFACTS` like `site` and `remisser`: no citation graph, so never
  `relate`d, indexed or dumped. `test/test_stats.py` locks in the scan rules, the
  artifact pruning and the page projection.

### 7m. edpb vertical — Europeiska dataskyddsstyrelsens vägledningar ✅ (first cut)

*(Superseded by §7n: this source is now the `edpb` utgivare of
`accommodanda/guidance/`, which collects twelve EU bodies. The design
below is unchanged and still describes how the EDPB's own documents are
harvested, identified and rendered; only the module path moved.)*

`accommodanda/edpb/` — the site's first **soft law from outside Sweden**, and
the interpretive layer over a regulation the corpus already holds. A riktlinje
binds nobody: the EDPB states, in advance and in general, how the
tillsynsmyndigheterna are to read the allmänna dataskyddsförordningen, and the
myndigheter and domstolar applying it are free to read it otherwise. It is
worth carrying because it is the reading a Swedish reader of the förordning
will actually meet — 43 of the 138 IMY-beslut in the corpus cite it — and
because the citation scan puts each document on the rail of the artikel it
interprets, beside the förordning itself.

60 documents: **riktlinjer** (37), **rekommendationer** (7) and the closed set
of **artikel 29-gruppens vägledningar** the EDPB endorsed on 25 May 2018 — all
16 Endorsement 1/2018 names. 52 are published here in Swedish, 8 in English
(three riktlinjer for which the EDPB has issued no Swedish version, and five
WP29 documents the working party never had translated or whose translations sit
in a 7-Zip archive nothing in the stdlib opens).

- **Nav: under EU-rätt, not a new top-level section.** These documents have no
  CELEX, which is why they are a source of their own rather than an eurlex
  doctype — but a masthead entry organised by *bindingness* would split the EU
  corpus in two and put the GDPR and its riktlinjer in different top-level
  places, which is the one adjacency a reader wants. So the folkrätt pattern:
  edpb browses under `/eurlex/vagledning/` and shares the cross-source selector
  with eurlex (`render.eurlex_axis`, the second user of the selector
  `generate_browse` already carried for hudoc). Swedish soft law (`rs`, allmänna
  råd) stays under Myndigheter, by issuer, where it belongs.
- **The selector names the issuing body, and the rail no longer repeats it.**
  That selector began as one flat "Dokumenttyp" row in which "Riktlinjer" sat
  beside "Förordningar" with nothing saying who wrote which — while the browse
  rail below listed the same document types a second time. It is now a list of
  labelled groups (`EU-rättsakter`, `EDPB:s vägledningar`), so a reader can see
  whose document a listing holds — a riktlinje binds nobody, a förordning binds
  everyone, and a reader who cannot tell them apart cannot weigh either — and
  `_facet_nav(..., primary_in_banner=True)` drops the rail copy, since a choice
  offered twice on one page is a choice offered once and a distraction.
- **Identity is the EDPB's own number** — `edpb/riktlinjer/05-2020`,
  `edpb/rekommendationer/01-2019`, `edpb/wp/248` — the avg/rs grammar with the
  series in place of the myndighet. The EDPB pads the löpnummer in some years
  and not others ("05/2020" beside "1/2018"), so the URI normalises and the
  citation form keeps what the document wrote.
- **A closed corpus written down as data.** The EDPB publishes a document page
  for only eight of the sixteen endorsed WP29 documents, and the seven of those
  under `/documents/guideline/` are stubs —
  five carry no file, one links an unrelated Danish decision, WP250's is titled
  "Dataskyddsombud" (WP243's subject), and two pages exist for each of WP242 and
  WP260 — so `series.WP29` records the Commission newsroom item that actually
  holds each one, and `parse.wp_cover` reads the title and adoption date off the
  document's own Swedish cover. The eight with no page of their own are sourced
  to the endorsement page, which is the EDPB's own statement that they belong
  here. The Swedish translations live inside 10–28 MB per-language ZIPs; only
  the extracted PDF is stored, and a routine run does not re-resolve them.
  **One endorsed document has no WP number at all** — the position paper on the
  artikel 30.5 derogation — and no cover either, setting its title in the
  opening prose and dating itself nowhere, so the registry writes both down and
  it is addressed by subject (`edpb/wp/artikel-30-5`).
- **The two BCR application forms, and the one place this vertical publishes a
  file it did not get from the issuing body.** WP 264 and WP 265 were issued as
  Word *forms*, so no authoritative PDF of either exists — the newsroom still
  serves WP 265 as `.doc`, and WP 264's item serves the WP263 PDF outright. Both
  are taken from Hessens tillsynsmyndighets conversions, because what makes a
  conversion trustworthy is not the host but what it can be checked against:
  WP 264 was compared word for word against the Greek authority's independent
  conversion (identical but for line breaking), WP 265 against the working
  party's own Word file from the newsroom (which the PDF's author metadata still
  names). `wp_cover` re-verifies on every parse that each file names its own WP
  number, so a mirror that starts serving something else fails the parse rather
  than filing the wrong text.
- **The numbered punkt is the citable unit.** The EDPB numbers every
  substantive paragraph and sets the number in a column of its own, which the
  paragraph-gap heuristic cannot see — paragraph 17 of Riktlinjer 05/2020
  arrived glued to the end of 16. `numbered_breaks` reads the numbers as a
  *running sequence* and hands them to `page_paragraphs` as forced breaks (the
  mechanism DV's bitmap paragraph numbers use), so each anchors on its own
  number and a decision citing "punkt 27 i riktlinjer 05/2020" can land there.
  **Only where the numbering is the document's paragraph numbering**, which is
  now tested rather than assumed: a working document that numbers its *sections*
  "1." and "2." had every paragraph under a section glued onto the section
  heading, so WP 250 was a single 46,000-character block and WP 248 a
  33,000-character one. `PUNKT_COVERAGE_MIN` separates the two populations
  (a section-numbered document numbers ≤ 9 % of its paragraphs, a punkt-numbered
  one ≥ 29 %); below it nothing joins and the numbers anchor nothing.
  Adjudicated against the 51 documents that existed before the change: five
  parse differently (WP 250, WP 248, WP 244, Riktlinjer 04/2020, Rekommendationer
  1/2022), the other 46 byte-identically. Of the nine documents added alongside,
  WP 263 has the same shape and would have had the same defect.
- **New parse type `VAGLEDNING`** in `lib/lagrum.py`: `Riktlinjer 05/2020`,
  `riktlinjerna 8/2022`, `riktlinjen 4/2019`, `Rekommendation(er) NN/ÅÅÅÅ`,
  `WP 243`, `WP248 rev.01`. "WP29" names the group, not a document, and is
  dropped the way `jk_is_date` drops a diarienummer that is really a date.
  Added to `ALL_PARSE_TYPES`, so every vertical that links every reference
  flavour picks these up on its next parse.
- **`artikel 29-gruppen` is a body, not artikel 29.** Fixed in
  `LagrumParser.acceptable`: the group is named in every data-protection
  document written since 1995, and reading it as a reference sent 13 of one
  guideline's links to artikel 29 in the GDPR — which repealed the directive
  that established the group and has no such body in it. A corpus-wide fix, not
  an edpb one.
- **Version and language are modelled, not decoration.** A riktlinje is
  adopted, consulted on and re-adopted, and the site republishes these under the
  EDPB's own reuse terms ("the original meaning or message of the documents is
  not distorted") — so stating the version is a condition of publishing them,
  and both it and an English-only document's language ride a banner. The
  version is also load-bearing for *citations*, which is a stronger claim than
  the banner: a citation names the version that existed when it was made, and
  the EDPB renumbers between versions, so resolving one onto the current text
  lands the reader on a different paragraph. Only current versions are carried
  today; `edpb/KNOWN-GAPS.md` records that taking the superseded ones needs the
  URI to be able to name a version (the shape `sfs` has for its lydelser), and
  sketches a future `ai-final-mapping` that would derive the draft↔adopted
  paragraph correspondence. The
  Swedish exceptions in 9 § and 26 a § URL are *not* the basis: both reach
  svenska myndigheters yttranden and handlingar upprättade hos svenska
  myndigheter, and the EDPB is neither.
- **Footnotes were being thrown away, and with them the whole IMY→EDPB
  graph.** IMY names a vägledning in prose ("Europeiska dataskyddsstyrelsens
  riktlinjer om samtycke") and grounds it with the number in the note below —
  and `classify_letterhead` drops every paragraph set below the running size,
  which is exactly where notes live (body 14pt → notes 11pt, body 17pt → notes
  9pt). 83 of the 138 IMY-beslut name this guidance, 43 carry its number, and
  none of those numbers reached the artifact. New
  `lib.pdftext.letterhead_footnotes` reads the same Para stream a second time
  and returns what the classifier dropped, minus the furniture that shares the
  small size; `avg` and `edpb` opt in, every other caller's block stream is
  untouched. All 43 decisions whose PDF names a number now resolve it. The fix
  recovers **1,200 citations across 811 notes in 131 avg decisions** (686 to EU
  acts, 185 to EDPB guidance) plus 2,020 inside the EDPB corpus's own 1,236
  notes. `dv`'s endnote list was the second user, so its template block moved to
  `partials/footnotes.html` (rule:second-use-goes-to-lib); a letterhead PDF's
  notes carry no anchorable inline marker, so they list without a back-link
  rather than with one that goes nowhere. `rs` opted in with the same three
  lines (3,996 notes, 4,243 citations).
- **`footnotes` was not presented body.** `lib/text.BODY_SECTIONS` -- "what the
  reader sees, the index stores and the link walk reads" -- listed only
  `structure` and `body`, so even where an artifact *did* carry notes they
  reached neither the citation graph nor the search index. That had been
  silently true of `dv`'s endnotes since HD started printing them in 2023.
  Adding `"footnotes"` took the IMY→EDPB graph from 13 catalogued edges to 219,
  and from 12 decisions to 43.
- **Scope of the bug, checked rather than assumed:** `forarbete` never had it
  (it keeps notes as `"fotnot"`-typed nodes *inside* `structure`, ~31 per
  document, so they were always in the graph); `foreskrift`, `remisser` and the
  treaty sources classify on text markers with no size rule at all and drop
  nothing. The discard was specific to the three letterhead-classified
  verticals, and all three now keep their notes.
- Wired end-to-end: `lagen edpb download [serie] [--only …] [--force]`,
  `lagen edpb parse`, then the shared relate/index/dump/generate. 51 documents,
  7,510 outbound links, 756 inbound. `test/test_edpb.py` (67 tests) over
  hermetic fixtures in `test/files/edpb/`; `accommodanda/edpb/KNOWN-GAPS.md`
  records the scope left out and why a *named* citation surface ("riktlinjer om
  samtycke", the form IMY uses most) was prototyped and rejected as unsafe.

### 7n. guidance source — EU-organens vägledningar ✅ (first cut)

`accommodanda/guidance/` — **soft law from the EU's agencies and bodies**, one
source collecting twelve issuing bodies the way `avg` collects JO/JK/ARN/IMY/KKV
and `foreskrift` collects seventeen myndigheter. It absorbs the earlier
`accommodanda/edpb/` source (§7m), whose 60 documents are now the `edpb`
utgivare here; nothing about the EDPB's own treatment changed.

The twelve: **EDPB**, **EDPS**, **EBA**, **ESMA**, **EIOPA**, **ECB**,
**ESRB**, **EASA**, **ACER**, **ENISA**, **BEREC**, **EUIPO**. What makes them
one source and not twelve is that they share everything except how their
listing is walked: one document model (`Vagledning`), one parse, one renderer,
one browse. `issuers.py` is the registry that drives it — a body's name, its
series, its number format and the two flags its PDF template needs — and
`download.py` maps each harvest scope to the one runner that walks that body's
site (rule:second-use-goes-to-lib).

- **Identity is the issuing body's own number, never a CELEX.** `edpb/riktlinjer/05-2020`,
  `eba/gl/2021-05`, `ecb/con/2013-82`, `esrb/2014-01`. Measured on the corpus:
  122 förarbeten cite an ECB-yttrande as CON/2013/82 and none as 52013AB0082.
  Where a body has one number sequence across several document kinds — the
  ESRB numbers its 62 rekommendationer, 23 beslut, 20 varningar and 2 råd
  together — the address carries no series segment at all.
- **Two harvest routes, one store.** Ten bodies publish on their own sites and
  are walked there. The ECB and the ESRB publish in EUT instead, so their
  documents come out of CELLAR — the same Publications Office endpoint the
  eurlex source reads, with the same language and format preferences and the
  same fallbacks, but stored under `guidance/<utgivare>/` and identified by the
  body's own number. `guidance/eurlex_download.py` enumerates a body's works by
  its corporate-body URI and reads the number off CELLAR's own
  `resource_legal_internal_number_prefix/_year/_sequential_number` predicates,
  falling back to the number printed in the title.
- **`lib/formex.py`: the Formex reader is now shared.** Reading the XML the
  Publications Office publishes an EU document as had lived inside
  `eurlex/parse.py`. A source may not import a sibling source, and route A
  needed the same reader, so the whole layer moved to `lib/` — the block
  emitters, the zip-member ordering, the patch hook. The move is
  behaviour-neutral: 400 randomly sampled eurlex artifacts re-parse
  byte-identically against the pre-move code. Each source projects those blocks
  onto its own model; guidance's page shows rubriker and stycken and has no
  article rail, so an article becomes a heading that names itself.
- **A route A document arrives in whichever manifestation CELLAR holds, and
  each document has exactly one.** 1 168 of the ECB's yttranden are the PDF the
  ECB itself set, 241 are Formex and 21 are EUR-Lex HTML. Formex is read with
  `lib.formex`, the PDF with the same paragraph reader the site-walked bodies
  use, and the HTML as the flat `<p>` run EUR-Lex serves it as. The ECB's own
  PDF template marks a heading bold at the running text's size and reprints
  "ECB-PUBLIC" on every page, so the registry sets both flags for it: read by
  size instead, one sampled document in three showed no heading at all.

The corpus today: 3 891 documents — ecb 1 617, enisa 568, easa 507, edps 436,
acer 271, esma 128, esrb 99, eba 80, edpb 60, eiopa 58, berec 43, euipo 24.
They carry 75 696 references, 71 426 of which reach a document the corpus holds
(94.4%).
- **`GENERAL` is the commonest Formex root here, and the act reader walks
  straight past it.** An ECB-yttrande is printed in the C series and carries
  its text in `CONTENTS`, not in enacting terms; `parse_act` returned zero
  blocks for all 224 of them. The root tag decides the reader
  (`_formex_main`): ACT keeps the act path, GENERAL and CORR are walked as
  contents, and a rättelse keeps the `DESCRIPTION` naming the passage it
  corrects.
- **An amending act carries its own number last.** The ESRB prints the act it
  amends first and its own number in the trailing parenthesis
  ("…om ändring av beslut ESRB/2011/1 … (ESRB/2020/3)"). Reading the first
  match filed documents under the amended act's number, each one overwriting
  that act's own text: taking the last recovered 23 documents, 76 → 99.
- **How the corpus actually cites this material.** Every mention of the twelve
  bodies across föreskrifter, rättsliga ställningstaganden, JO/JK-beslut,
  förarbeten, remissvar and SFS was classified into four grammars:

  | grammar | hits | linkable |
  |---|---|---|
  | titel — "Europeiska bankmyndighetens riktlinjer om …" | ~1 363 | needs a title index |
  | nummer — "(ESRB/2017/6)", "CON/2013/82" | ~482 | yes |
  | svepande — "EBA kommer att ta fram riktlinjer för …" | ~1 899 | no: names no document |
  | omnämnd — the body's name alone | ~85 500 | not a citation |

  The title form carries most citations for most bodies; the ESRB is the one
  body cited by number more often than by title (156 against 39), and the ECB
  is next. The svepande class is the trap: those are förarbeten describing a
  body's *mandate to issue* guidance, in the future tense, about documents that
  do not exist yet. A grammar that matched them would mint links to nothing.
- **`lib/lagrum.py` mints the number form for five bodies.** ESRB/2017/6,
  EBA/GL/2021/05, ESMA/2013/720 (and ESMA35-43-349, and the joint committee's
  JC/GL/2024/36), CON/2013/82 and BoR (11) 67 each carry their body's own
  acronym, so the number alone names the document and no surrounding words are
  needed to anchor it. Checked against the corpus: all 1 967 documents of those
  five bodies have their own printed number resolve to their own page. EIOPA is
  deliberately left out even though it is cited by number 40 times —
  EIOPA-BoS-19/465 does not say whether the document is a riktlinje or a
  rekommendation, and its address carries that series segment, so minting from
  the number would be guessing.

  Where that pays: **förarbeten**, with 350 of these numbers across 115
  documents. Föreskrifter print exactly one in the whole corpus of 12 903, so
  `foreskrift` keeps its narrow parse-type set rather than paying a full
  re-parse for a single link. The title grammar, which is what föreskrifter
  actually use, is the open work.
- **The EBA's Swedish title comes off its own cover.** 72 of the EBA's 80
  documents *are* Swedish text, but everything the harvest can read names them
  in English — the leaf page's `<h1>`, the link and the file name. The Swedish
  name is printed on the cover of the same PDF the body is read from, so
  `parse.eba_cover_title` reads it there. It reads all 72: take the first cover
  paragraph that is not the shouted running head (an uppercase-letter ratio over
  0.8) and carries a vägledningsord, join a continuation the EBA set on the next
  line, and take the number, the date and the distribution mark off the *ends* —
  never the middle, since an amending riktlinje names the riktlinje it amends by
  number inside its own title.

### 7b. Vertical scope closed ✅

The original lagen.nu source families are covered by SFS, DV, förarbete,
föreskrift, avg, wiki and site; the rewrite also adds EUR-Lex, HUDOC, CoE,
ICRC, UNTC, ICC and remisser. PBR is deliberately archived rather than imported
(§7g). There is no
unnamed “rest of `/mnt/data/lagen/data/`” completion requirement: a future new
source is ordinary product expansion, built as its own vertical, not unfinished
rewrite work.

---

## Key files

| Path | What |
|---|---|
| `tools/golden_sfs.py` | golden-corpus comparator (`normalize` parsed XHTML → NF on the fly) |
| `tools/namedlaws_history.py` | dates `sfs/data/namedlaws.json` from the corpus: walks `rinfoex:upphavdAv` backwards from each named act, `rpubl:upphavandedatum` as the `from`/`until` boundaries, keeping only predecessors whose own title yields the name. Re-runnable/idempotent; `--write` edits the dataset, default prints the diff |
| `../ferenda.old/data/sfs/parsed/` | the golden = old-pipeline parsed XHTML (11,056 docs), normalized per comparison — sibling checkout, not `site/data/` |
| `accommodanda/lib/` | **shared** horizontal libs: `lagrum` (citation engine), `util`, `errors` (`SkipDocument`), `harvest` (shared incremental-download core — `HarvestWatermark`, `walk`), `casenaming`/`eucasenaming` (DV/EU case identity + display naming), `labels` (every source's four reader-facing name forms — eyebrow/h1/official-title/citing-form — dispatched per source over the parse-time-stamped artifact + the curated datasets, read identically by `render.py` and `catalog.py`), `facsimile` (on-demand source-PDF page → retina PNG, disk-cached; `/api/v1/facsimile` + the legacy `/prop/2022/23:10/sid1.png` grammar), `poi` (Apache POI-via-jpype legacy `.doc`/`.docx` extraction to a flat paragraph stream — moved from `dv/word.py` once förarbete became its second caller; `dv/legacy.py` and `forarbete/legacy_formats.word_paras` both read through it, the latter for `.docx` only, `.doc` going through `antiword` instead) |
| `accommodanda/sfs/` | **acts vertical**: `{extract,reader,model,tokenizer,assembler,nf}` parser + `parallelappendix` (structurally detected, aligned bi/trilingual convention appendices, no per-law code; 95/107 detected candidates) + `register` (SFSR→amendments/förarbeten/metadata) + `graphics` (typed omitted-content detection *and* vision-localization — `collect_gaps`/`provenance_sfs`/`localize_group`) + `redaktionell` (typed publisher-editorial-note detection, retyped in place at projection time) + `pdfmirror` (`mirror-pdf`, official-PDF mirror, the crop source) + `asgit` (`history-as-git` — the corpus as a git repo, one commit per amendment event, `docs/prd-sfs-history-as-git.md`) + `__main__` (diagnostic parse/validate CLI; `mirror-pdf`/`ai-includegraphics` are `build.py` actions, not here) |
| `accommodanda/dv/` | **court-decisions vertical**: `download`, `identity`, `model`, `parse`, `structure`, `legacy`, `namedcases` (HD named-precedent harvester), `casenumbers` (the held-case-number snapshot `lib/malnummer.py` resolves "HD:s dom i mål T 3-08" through); the legacy Word extraction itself now lives in `lib/poi.py` (shared with förarbete), `legacy.py` importing it as `poi as word`; canonical case title + HD given names live in `lib/casenaming.py` (shared with the catalog + renderer). `parse.parse_pdf_record` reads a raw pre-referat HD/HFD verdict straight off its PDF attachment (no `innehall` HTML yet), recovering the domskäl paragraph numbers from their unselectable margin bitmaps; `identity.py`'s R2 merge folds that raw record into the later referat that publishes the same målnummer once one exists |
| `accommodanda/forarbete/` | **preparatory-works vertical**: `download` (regeringen.se, 8 types + `pm`, promemorior outside the Ds series), `model`/`structure`/`parse` (PDF/html→nested structure→artifact; `parse.tag_frontmatter` retags the prop/skr överlämnande page — ingress heading, `signatur` signer blocks; `parse.parse_record`'s one body route, `_harvested_body`, reads every §7g frozen corpus alongside live harvests — all re-housed into ordinary `files` form, 2026-07-19), `volumes` (which of a multi-PDF record's `files` are the body and in what order, read from the record's provenance and the landing page's own link text — drops errata/summaries/kortversioner/reprinted-directive/remisslista siblings, collapses a "hela dokumentet" edition published beside its own parts), `jamforelse` (extracts a re-enacting prop's jämförelsetabell/paragrafnyckel bilaga tables into old↔new provision pairs from per-run coordinates; consumed by `sfs/correspond.table_correspond`), `legacy_formats` (body adapters shared by every re-housed corpus and the live harvest — dokumentstatus XML, riksdagen text/tml + skanning2007 html, ABBYY OCR-XML, scanned-PDF OCR text, TRIPS `div.body-text`, `word_paras` for `.doc`/`.docx` — `.doc` via `antiword`, `.docx` via `lib/poi.py`), `propkb` (facsimile-only fetcher for the KB two-chamber scans, 1867–1970 — adds no documents, only page images for the 17,295 XML-only propkb records; built, not yet run at corpus scale), `soukb` (body re-downloader for the KB-digitised SOUs, 1922–1999 — no ABBYY XML sibling, so the scanned OCR'd PDF is the body; walks `https://sou.kb.se/` as the source of truth, forgetting the legacy soukb records; 5,814 basefiles, 128 multi-volume; built, verified on one doc, not yet run at corpus scale), `riksdagen` (doctype-agnostic dokumentlista harvest engine, driven for `bet`/utskottsbetänkanden off data.riksdagen.se, no frozen corpus), `rskr` (second driver over `riksdagen.py`'s engine, for riksdagsskrivelser — HTML body, no PDF), `kommentar` (författningskommentar → EU-directive *genomför* edges, prop + fm), `genomforande` (relate-time resolution pinning each statement to its SFS paragraf, preferring an authored `.ann` genomförande layer over the mechanical `implements` per covered directive), `aigenomforande` (opt-in LLM pass, `lagen forarbete ai-genomforande <prop> <CELEX>`, authoring that `.ann` layer from the prop's per-paragraf FK entries), `fk` (per-paragraf FK commentary text → `kommentarer` artifact section → `fk_kommentar` catalog layer → statute-rail "Författningskommentar"), `lydelse` (two-column nuvarande/föreslagen lydelse tables reconstructed from per-run coordinates → `tabell` blocks in the SFS `rad`/`cells` shape), `tabell` (conservative generic data-table detection for everything tabular that isn't a lydelse comparison, with cross-page continuation, §7g/finding 04) |
| `accommodanda/eurlex/` | **EU vertical (EUR-Lex/CELLAR)**: `download` (SPARQL discovery; a multi-part Formex manifestation fetched whole, as one zip; `lagen eurlex backfill` downloads the acts the corpus cites but does not hold, ranked by `catalog.dangling_targets`), `bulk` (dump import), `correspond` (the EU-act **lineage**: a recast's own jämförelsetabell annex → article↔article pairs, mechanical, extracted by `parse` into the artifact's `correspondence` key; `catalog._index_document` writes them into `directive_correspondence` as it indexes each act, walked transitively by `catalog.predecessor_atoms` under `catalog.caselaw_anchored`, the statute-wide pinpoint-precise case-law rail assignment), `parse`/`parse_html`/`parse_pdf` (Formex/HTML/PDF → one artifact shape; `parse.parse_act_body` descends through Formex's `GENERAL`/`GR.SEQ` wrappers so a multi-file act (2004/18, the Charter) parses through the same walker as an ordinary `ACT` root; `parse.parse_opinion` reads an Advocate General opinion's Formex `CONCLUSION` structure, `parse.parse_hearing_report` a `REPORT.HEARING` -- for the oldest ECR cases the hearing report is the only text CELLAR holds; judgment paragraphs are read from both the pre-2012 plain `NP` and the later `NP.ECR` shapes; citation scanning is per-language -- `_refparser(lang)` loads the English EULAGSTIFTNING surface for the pre-accession case law that exists in no Swedish version), `definitions` (defined-terms extraction + in-act interlinking), `lang`, `model` (`doctype` splits sector-6 CELEX into judgment/opinion/order by document-type letter), `casenames` (harvest CELEX → usual name for named EU cases from Wikidata into `data/casenames.json`, read by `lib/eucasenaming.py`), `data/treaties.json` (curated Swedish names for EU primary law, keyed by CELEX stem, read by `lib/labels.py`) |
| `accommodanda/hudoc/` | **European Court of Human Rights vertical**: HUDOC JSON result pagination + full-text HTML conversion, typed case model, article-facet references into CoE treaty provisions, `citations` (case-law cross-reference matcher), `treaties` (the Convention/protocol short forms a judgment cites, over `lib.treatyref`), `casenames` (`lagen hudoc casenames`, writes the committed `data/casenames.json` join surface `lib/emdref.py`, the Swedish `lagrum` matcher, reads) |
| `accommodanda/coe/` | **Council of Europe Treaty Office vertical**: complete-list/detail/official-text harvest, treaty model, HTML/PDF article parser; canonical `ext/coe/{number}#A…` targets shared with HUDOC |
| `accommodanda/icrc/` | **ICRC international humanitarian law treaty vertical**: anonymous Drupal JSON:API list+detail harvest (no PDF — the envelope carries the authentic text), typed `Treaty` model, offline article-tree parser; canonical `ext/icrc/{number}` targets, curated `data/names.json` for the Geneva Conventions/Additional Protocols |
| `accommodanda/untc/` | **UN Treaty Collection vertical**: two fetches per curated treaty — the MTDSG status page, which carries no treaty text at all, and the authentic text from the instrument's own depositary (OHCHR HTML for twelve, a born-digital PDF for VCLT and UNCLOS; never the UNTS's own volumes, which are scans). Typed `Treaty`/`Party`/`Provision` model, offline participation-grid parser plus `text.py`'s article splitter (1,020 articles over 1,043 nodes, checked against each entry's curated article count); canonical `ext/untc/{unts}` targets keyed on the UNTS registration number, curated `data/treaties.json` (14 instruments: VCLT, UNCLOS, Genocide Convention, the core human-rights treaties, the Refugee Convention + Protocol) |
| `accommodanda/icj/` | **International Court of Justice case-law vertical**: the `icj-cij.org/decisions` Drupal view (one request with `from=1946` returns all 877 rows) scopes the harvest to 255 decisions — judgments, advisory opinions and provisional-measures orders; the PDFs are Cloudflare-walled and fetched through `lib.browser.DetachedChrome`. `parse.py` cuts the printed Reports' front matter at the Court's **dateline** (the letterhead words do not survive OCR) and recovers the numbered paragraphs with `paragraph_chain` — the longest chain counting up in steps of at most four, which must also open at the Court's first paragraph or be long enough that its length is the evidence, so a quoted ICTY paragraph and an annex's page numbers both join none; `ocr.py` repairs the pre-2004 scans' systematic character confusions against a vocabulary harvested from the born-digital decisions |
| `accommodanda/icc/` | **International Criminal Court case-law vertical**: two-source harvest — icc-cpi.int `/decisions` facet scrape (curated Rome-Statute decision types, `data/decision_types.json`) scopes the set and yields document numbers, the Legal Tools API (legal-tools.org) resolves metadata + PDF; HUDOC-shaped `Decision`/`Block` model, `pdftext`-based article parser with numbered-paragraph/heading classification; canonical `ext/icc/{doc-number}` targets kept local to the vertical (rule:second-use-goes-to-lib) |
| `accommodanda/avg/` | **JO/JK/ARN/IMY/KKV-decisions vertical**: `model` (`Beslut`; URI = the citation-minted `avg/{org}/{dnr}`), `download` (JO WordPress admin-ajax API + PDFs; JK one-shot listing + landing pages, `jk_canonical` dnr normalization; ARN one-page vägledande-beslut listing; IMY tillsyn pages, whose diarienummer is read out of the attached PDFs and the documents regrouped by it, plus the praxisbeslut/sanktionsavgift overlay; KKV the diarium narrowed by `KKV_CASETYPES` joined with the curated ärendelista on the dnr; also the store-path helpers `arn_pdf_path`/`jo_pdf_path`/`imy_pdf_path`/`kkv_body_path`/`jo_officialreport_path`/`RE_ARN_DNR`, moved here from the deleted `legacy.py`, §7g teardown 2026-07-19), `parse` (JO/ARN/IMY/KKV PDF via `lib/pdftext`, JK landing HTML, KKV also FrontPage-era windows-1252 HTML and Word; DV parse-type citation scan; an ARN referat's "title" is its preamble paragraph, so the page heads on `lib/labels.first_sentence` of it while the whole preamble still renders as the summary); `arsberattelse` (`lagen avg arsberattelse`, sweeps the JO artifacts' `officialReport` pages into the committed `data/arsberattelse.json` snapshot `lib.lagrum`'s `jo_arsb_ref` production resolves through); `KNOWN-GAPS.md` records the two documents `avg parse` has ever failed on, both since resolved |
| `accommodanda/foreskrift/` | **agency-regulations vertical**: `model` (Regulation/Consolidation/Amendment primitives), `harvest` (per-agency enumerate seam {indexed,paginated,json,sitemap,bespoke} × resolve seam {landing+classify, direct} wired onto `lib/harvest.walk`; `Agency.browser` transport selection; `Skip`/`guarded_enumerate` resilience for flaky indexes; classify seam {file,section,href,single,default_regulation}), `agencies` (per-fs config registry, 71 registered författningssamlingar, 66 live + 5 with no live harvester), `skvfs`/`mtfs` (F5-protected source semantics), `download`, `parse` (PDF → Regulation artifact: text-based `N kap.`/`N §` classify, masthead metadata, bemyndigande/genomför via the citation engine; `clean_title`/`title_from_body` fall back to the PDF's own opening rubric when the harvest title is link chrome), `structure` (kapitel/paragraf nest + SFS `#K2P3` anchors), `data/series.json` (hand-edited designation/official-title/successor registry, `lib/datasets.FS_SERIES` — drives the browse's headings, Swedish ordering and succession folding, `lib/facets.py`). All §7g frozen-import records (the 909 SKVFS/SOSFS/HSLF-FS records, then the ~30 further myndfs corpora, 2,177 documents) were one-time imported and migrated into ordinary harvested form; body PDFs copied under `FORESKRIFT_DOWNLOADED/<fs>/`, `legacy`-marked records kept as ordinary records with a `"source": "*-legacy"` provenance marker. Both one-time import modules (`legacy.py`, twice built and twice deleted once its import ran to completion) are gone (§7g teardown, 2026-07-19) |
| `accommodanda/lib/browser.py` | detached headful-Chrome transport for F5/Shape-protected public sources: navigate without a Playwright/CDP connection, wait the source-configured interval, then attach briefly to read the completed DOM or exact browser-cached PDF; selected only by SKVFS and MTFS; on a headless host it auto-starts a private Xvfb framebuffer and runs Chrome headful against it, torn down on exit |
| `accommodanda/remisser/` | **remiss (referral-response) vertical**: `model` (`Remiss` keyed on the *referred document's* own identity, `basefile = "<typ>/<identifier>"` — not the regeringen.se ärende-page slug, kept in `url` — plus `Remissinstans`/`Remissvar`, `org_slug`, `Remiss.externt_dokument`), `download` (regeringen.se `/remisser/` sync over the AJAX filter listing (`REMISS_CATEGORY`, not the decorative `?p=N`); `parse_arende` raises rather than minting a stub identity when an ärende remits a regeringen-published document of an unrecognised doctype; `pm`/`lr` cross-refs resolved via `lib.regeringen`'s shared identity rules; the examined-ärende index `layout.REMISSER_SEEN` — keyed by URL slug, since only the ärende page names the remitted document — drives the sweep, `until` = deadline + grace period; `sync`'s shared `_poll` step + `sync_one`/`--only`, both gated by `externt_dokument` for ärenden whose remitted document regeringen didn't publish), `parse` (answer → `Remissvar`; `_body_text` dispatches on the file's magic bytes rather than trusting its stored `.pdf` name — `lib/pdftext` with no fixed header for a real PDF, `lib.poi` for the 4 answers actually stored as Word; since 2026-08-04 the shared `pdftext` pipeline also strips running furniture found by shape, drops footnotes, rejoins page-break-split sentences and strips the letter's addressing apparatus before flattening to paragraph text — the whole corpus, 79,982 answers, was reparsed), `ai_analyze` (the sole LLM pass — sentiment+quote per section, `.ann` layer in the curated store, `lib/annstore.py`, joined to forarbete via `layout.resolve_basefile`; a basefile may now name a whole ärende, expanded to every fetched answer still lacking a layer; each quote carries a `quote_type` (`grund`/`standpunkt`) so a stated non-answer is not confused with an invented ground, and a reworded quote is snapped back to the answer's own wording (`snap_to_source`) rather than only rejected; `--update` re-analyses every ärende already covered whose remissperiod has not closed — the same deadline + grace the download side re-polls by (`download.still_open`) — picking up answers that arrived after the first analysis, with a `.ann.watch` marker per ärende so one analysed before its first answer arrived is still tracked; never part of a rebuild). Never `relate`d/published; its `.ann` layer feeds the referred förarbete's rail via `page._remiss_indexes`; `KNOWN-GAPS.md` records the corpus's outstanding non-self-healing gaps; `tools/remisser-eval/` scores `ai-analyze` output against a hand-built `.ann.key` answer key |
| `accommodanda/lib/annstore.py` | the curated store for every `ai-*` action's output (eurlex/kommentar/forarbete (`ai-genomforande`) `.ann`, sfs `.corr` — the latter also written mechanically by `lagen sfs table-correspond` from a prop's own jämförelsetabell bilagor (`forarbete/jamforelse.py`) and by `lagen sfs renumber-correspond` from the register's "betecknas" omfattning clauses (same-law renumbering, RF 2010:1408) — and sfs `.graphics`, `lagen sfs ai-includegraphics`'s vision-localized graphic crops) — `WIKI_ROOT/ann/<source-dir>/<relpath>`, mirroring the artifact tree's relpath grammar; envelope (`meta`: status generated/verified/derived — `derived` marks a layer computed mechanically rather than authored by a model, which `publishable` lets reach the render as it stands — model, date, input sha256 hashes, optional `meta_extra` fields like `.graphics`'s `through` provenance horizon, and `run` — endpoint host, model, sampling, token counts, wall-clock span and a hash of the prompts actually sent, `lib/llm.py`'s `record()` — stamped only when an `ai-*` action opened a recording window and it saw a call, so a `derived` layer stays free of it; `write` calls `llm.rearm()` after stamping so several layers authored in one process each get only their own calls), `guard`/`drifted` gate regeneration and derive staleness (`run`'s prompt hash is deliberately not one of the recomputed `inputs`, since `drifted` recomputes every input from its label alone and a rendered prompt needs the source's own prompt builder, which `lib/` may not call — a prompt change is visible by comparing `run.prompt_sha` across runs, not as staleness); per-entry `"verified": true` curation on a `.graphics` gap is preserved only while both resolved source and stored semantic identity still match, so renumbered/transformed gaps cannot inherit a crop by positional id; `write` itself stays blunt; inventoried by `lagen ann status` |
| `accommodanda/lib/regeringen.py` | shared regeringen.se harvest knowledge (rule:second-use-goes-to-lib): the doctype table (`TYPES`), `ul.list--block` listing walk (`listing_items`), and the identity rules for the two series-numberless doctypes (`pm_identity`, `lr_identity`) so `forarbete/download.py` and `remisser/download.py` mint the same basefile for the same document from different pages |
| `accommodanda/site/` | **editorial-chrome vertical**: `model` (block-tree dataclasses + `Frontpage`/`AboutPage`/`Sitenews`), `parse` (markdown → artifact for `frontpage`/`om/<slug>`/`sitenews`), `render` (artifacts → HTML + Atom, `write_site`). Content is markdown in `lagen-wiki/site/`, migrated once by `tools/migrate_site_content.py`. Never `relate`d/indexed/dumped (absent from `ARTIFACTS`, like remisser); rendered during `generate` |
| `accommodanda/lib/pdftext.py` | **shared font-aware PDF extraction** (förarbete + föreskrift + avg (JO/ARN) + remisser): `pdf_pages` (`pdftohtml -xml` → bold/italic-tagged `Line`s) → `page_paragraphs` (reflow, strip running header/page-no/TOC — a line is stripped only when it *is* the header (identifier + at most a page number/date), not merely when it contains the identifier; `identifier=None` skips header-stripping for sources with no fixed masthead, e.g. remisser) → the vertical's own `classify`; `repair_pdf` (ghostscript rebuild of a PDF whose cross-reference table poppler refuses, cached beside the source) feeds `pages_with_ocr` when poppler's `pdftohtml` fails outright rather than yielding empty text. Four source-agnostic cleaning steps, added for remisser (2026-08-04) where no per-source header string exists (each of ~90 organisations answers on its own letterhead): `strip_page_furniture` (running header/footer found by digit-masked repetition + margin position + font size, not a passed-in identifier), `join_across_pages` (rejoins a sentence a page break split), `drop_footnotes` (drops footnote text and its superscript markers by font size), `strip_addressing` (drops a letter's masthead/reference line/contact block by composition — address tokens + reference labels — since a masthead printed once cannot be found by repetition) |
| `accommodanda/config.py`, `lib/layout.py`, `lib/net.py` | runtime config (`config.yml`/`data_root`/`catalog_root` — the latter decoupling `catalog.sqlite`'s location from the bulk corpus, env `CATALOG_ROOT` — and `wiki_root`, env `WIKI_ROOT`, the one checkout the running site writes into; prod bind-mounts it so both editors' commits land in a real git tree, since the deployed image excludes `.git`), centralized document layout (`page_relpath` on-disk file ↔ `page_url`/`url_to_relpath` public lagen.nu address; `layout.PATCHES = config.WIKI_ROOT/patches`, asserted to exist so a missing mount cannot silently drop every redaction), resilient HTTP session + harvest progress reporter |
| `site/data/{downloaded,artifact}/eurlex/` | harvested EU corpus (`notice.ttl` + best manifestation per language) + artifacts |
| `test/test_eurlex_parse.py`, `test/test_eurlex_html.py`, `test/test_eurlex_definitions.py`, `test/test_eucasenaming.py`, `test/test_eurlex_casenames.py` | EU parser, defined-terms and case-naming suites |
| `accommodanda/lib/wikitext.py` | shared MediaWiki-dump parser (wikilinks + citation engine → runs) |
| `accommodanda/wiki/` | **kommentar + begrepp sources**: `parse` (commentary anchored to §§, concept glossary) |
| `site/data/downloaded/mediawiki/` | MediaWiki dump (SFS commentary + concept pages) |
| `test/test_wiki.py` | wiki parsing suite |
| `site/data/downloaded/forarbete/<type>/<year>/` | harvested förarbeten (record json + landing html + content pdf) + frozen-import records, year-segmented (`fa_year`/`fa_dir` in `lib/layout.py`; `pm` buckets under `_`, dotfile markers stay at the `<type>/` level) |
| `test/test_forarbete_download.py` | förarbete downloader parsing suite (incl. `pm`) |
| `test/test_forarbete_riksdagen.py` | `bet`/utskottsbetänkanden downloader suite (data.riksdagen.se); the shared dokumentlista `harvest()` engine also drives `rskr.py` |
| `test/test_forarbete_legacy.py`, `test/test_forarbete_legacy_formats.py` | parse-route tests for re-housed frozen-corpus förarbete records (trips/text-tml, skanning2007 HTML, ABBYY XML, scanned-PDF OCR, re-OCR sidecar) + the shared body-adapter suite (the one-time import machinery is gone; these exercise `parse_record`'s harvested-form route) |
| `test/test_avg.py` | avg (JO/JK/ARN/IMY/KKV) parser + citation-grammar suite |
| `tools/aigenomforande-bench/` | the 2026-07-23 ai-genomforande benchmark harness: FK-candidate dumper, subagent-adjudicated `.ann.golden` builder, per-model runner/evaluator, and the archived six-model result table (`final_eval.txt`) |
| `tools/remisser-eval/` | the 2026-08-04 remisser ai-analyze evaluation harness, same shape as `tools/aigenomforande-bench`: `make_briefs.py` picks the longest answers of one ärende and writes a briefing file per answer, `import_keys.py` lands a hand-authored `.ann.key` per answer into the curated store (`WIKI_ROOT/ann/remisser/**.ann.key`), `evaluate.py` scores any layer tree against the key (sentiment interval, on-point sentence match, whether criticism was preserved) |
| `tools/golden_dv.py` | DV golden cross-check (references vs old distilled RDF) |
| `tools/golden_dv_structure.py` | DV structural golden (instance/ruling skeleton vs old parsed XHTML) |
| `tools/golden_eurlex.py` | EUR-Lex metadata cross-check against a retained CELLAR snapshot (no legacy oracle exists for this vertical) |
| `accommodanda/build.py` | orchestrator: `lagen <source> <action>` build driver + freshness; corpus verbs `relate`/`generate`/`index`/`dump`/`serve` (one process serving the static site + REST API + MCP) |
| `accommodanda/lib/catalog.py` | derived SQLite catalog + cross-source citation graph (`relate`) |
| `accommodanda/lib/inbound.py` | derived per-document inbound-citation tree under `data_root/inbound/`, written per page by `generate` and read by REST `/document/inbound` + MCP `get_incoming_citations` instead of a live catalog query — see the "Cross-source inbound-link graph" bullet above |
| `accommodanda/lib/page.py` | the shared page kit every source's `render.py` stands on: `Site` (render context), the generic node walk (`render_node`/`render_runs`), the context rail (`Rail`/`RailSection` + margin builders), the page shell (`page`/`page_context`, the TOC collector). Knows no source by name |
| `accommodanda/lib/render.py` | corpus-wide site assembly (`generate`): frontpage, folkrätt/EU-rätt landings, Atom feeds, static chrome, `generate_site` (the render driver, dispatching per-document pages to each source's own `render.py` through the `renderers` registry `build.SOURCE_RENDERERS` hands in) + live ⌘K search |
| `accommodanda/browse.py` | the faceted browse tree, generated as a client of the REST API (`api.app` driven through a FastAPI `TestClient`) — lives outside `lib/` since `lib/` may not import `api`. `render_landing(source, view, banner)` renders a source's root as a landing over all its primary buckets, by count; wired for eurlex only (its default-bucket root used to be a byte copy of the first leaf — one treaty's 8 consolidated versions standing in for 50,000 acts). Other sources still get the default-bucket root |
| `accommodanda/lib/assets/` | the browser-facing static chrome as real files (`style.css`, `editor.css`, `matomo.js` — first in the bundle, the cookie-less Matomo snippet legacy lagen.nu has always used, same-origin `/matomo/`, gated on a hostname→site-id table so a dev serve/mirror stays silent — `dom.js` — shared `window.lagenDom` vocabulary: own-document anchor resolution across split-view panes, id-attribute selector, landing flash, JSON-island parse — `drawers.js` — the mobile bottom toolbar's TOC drawer / context-rail bottom sheet — `scrollspy.js`, `search.js`, `popover.js`, `fullsearch.js`, `versions.js`, `faksimil.js`, `pdf.js` — injects the "Spara som PDF" printer icon on the TOC rail's short-id row and its options dialog (one or two text columns, TOC page numbers, the SFS amendment/transition register, which rail context kinds to print, visa/ladda ned — two columns disables TOC, register and context), which opens the export's waiting page `/internal-api/v1/pdf/vanta` (`api/pdfjob.py`) rather than waiting on a request of its own — `editor.js`, `robots.txt`) — `render.write_assets` ships them via the same Brotli precompression as pages: the JS is concatenated in load order into one `script.js` bundle (the page links a single URL, so a new module publishes via `generate --assets-only` instead of forcing a full regenerate), `style.css` with `editor.css` appended. `style.css`'s `@media print` block is a full paged-media design — a **spread**, not a sheet, of mirrored book pages rather than one fixed margin: a right page holds text at 28 mm (117 mm wide, about 67 characters a line) and the apparatus column at 145 mm (55 mm wide), a left page mirrors them (apparatus at 10 mm, text at 65 mm) through `@page :left`/`@page :right` boxes — the alternating-margin design an earlier attempt abandoned, now made to work because the note's own margin is a calculated offset off the current page fragment's width, not a fixed one; a three-part running head reading outwards (eyebrow on the fold, document title in the middle, chapter · § or article on the open edge, from `string-set`/`string(…, first)`), set in italic serif, and the folio alone on the outer corner; the context apparatus set small in the outer margin beside what it annotates — an article/aside grid pair on the standalone CSS path, a fixed table row on the WeasyPrint path (`body.pdf-weasy`; `api/pdf.py::_mirror_margin_notes` moves the note cells to the verso outer edge after layout, since WeasyPrint's grid paginator is superlinear across hundreds of SFS provisions); a `kolumner=2` compact layout (`@page compact`, 8.25 pt, two 89 mm columns, 8 mm gutter) omits the apparatus; paper tokens black-on-white in both themes — shared by the browser's own Skriv ut and by `api/pdf.py`'s WeasyPrint render, which alone exercises the paged-media rules (`@page`, `string-set`, `target-counter()`) browsers don't implement, and the `.print-toc`/`.print-kontext` markup that only `api/pdf.py` ever injects |
| `accommodanda/lib/text.py` | shared artifact text flattener (node/document/fragment plain text); `sentences(text, clause_breaks=False)` is the shared Swedish-abbreviation-aware sentence splitter, used by `labels._first_sentence` and by `remisser/ai_analyze.answer_units` |
| `accommodanda/lib/search.py` | OpenSearch full-text indexer (standalone units collapsed by `doc_uri`, no parent-child join), `index` |
| `accommodanda/lib/feeds.py` | one dataset per browsable source (the legacy alias where the old site had one, the source's own name where it had none) + the Atom and HTML feed renderers, shared by static `/dataset/<alias>/feed` generation, the live query-param endpoints and the editorial news feed. `nav()` is the source selector every feed screen carries in its left rail |
| `accommodanda/lib/dump.py` | NDJSON bulk corpus dumps (`dump`) |
| `accommodanda/api/app.py` | the **public** FastAPI REST/OpenAPI service (`/api/v1`), mounted on `lagen all serve`; mounts the internal app (`api/internal.py`) at `/internal-api`; `serve()` installs `api/analytics.py`'s Matomo middleware (not at import time, since `generate` drives this same app in-process via `browse.py`'s `TestClient`, which must not count as a daily API consumer) |
| `accommodanda/api/internal.py` | the internal API (`/internal-api/v1`) — a second `FastAPI` app, mounted by `api/app.py`, carrying `auth.router`, `edit.router`, `patch.router`, `graphics.router` and `pdfjob.router` — this loop is the complete route table, nothing is declared from `api/app.py`; `openapi_url`/`docs_url`/`redoc_url` are off by default, and its own schema and Swagger UI answer at `/internal-api/openapi.json`/`/internal-api/docs`, both behind `Depends(require_editor)`, so a reader of the public `/docs` never sees an internal route. Same-origin only app-wide (`Depends(auth.same_origin)`), a gate `api/ops.py`'s router carries too; `errors.install(app)` runs here as well, since a mounted sub-app carries its own exception middleware |
| `accommodanda/api/mcp.py` | public MCP server (Model Context Protocol), mounted at `/mcp`; its `_LoggedMCP` wrapper also reports each JSON-RPC call to `api/analytics.py` under a synthetic `/mcp/<method>/<tool>` URL, so the Matomo Pages report breaks down per tool |
| `accommodanda/api/pdf.py` | `GET /api/v1/pdf` — a generated page re-rendered for paper: WeasyPrint over the same `style.css` `@media print` rules the browser uses, plus the paged-media layer browsers skip (running headers, "n (total)" folios, a PDF outline); `toc`/`kontext` query params print the page's own TOC (resolved page numbers) and the rail's context kinds **in the outer margin** beside each provision, both read from the page's own markup/`#lagen-context` island so the printed apparatus cannot drift from the screen rail; `andringar` keeps or drops the SFS amendment/transition register (`_paper_amendments` also strips the register's screen-only links, keeping the legal text and metadata); `kolumner=2` switches to the compact two-column layout and forces context off. Provision and note are one article/aside pair (`.kontextblock`; a fixed table row under `body.pdf-weasy`, since WeasyPrint's grid paginator is superlinear across hundreds of SFS provisions — `_mirror_margin_notes` then moves the note-cell boxes to the verso outer edge after layout), so the row is as tall as its taller flow — the next provision starts below whichever ran longer — and a note longer than the page splits with the row and continues at the head of the next, in the margin it started in. `_split_sfs_provisions` breaks a § into one fragment per annotated stycke or point first, so a later provision does not wait on an earlier note that outruns its own text. Floats were the obvious mechanism and do not work: WeasyPrint keeps no excluded shape for a float whose box falls outside its container, nor for one carried onto the following page, so notes printed on top of one another. The note must not float: floated properly it lets the text flow past, which fills the page and turns local congestion into a queue that never drains — the GDPR's recitals carry three times the context their own text can sit beside, so one page had an empty margin and another an empty reading column. Density is the lever instead: `MARGIN_CAP` per section, `_short_citer` on each line, and run-in section labels. A margin note lists `MARGIN_CAP` (5) items per section before the rest collapse into the rail's own "+N fler" line, and each line is cut to its identifier and pinpoint (`_short_citer`: "Prop. 2015/16:170, avsnitt 8.4", not the full title, which wrapped over four lines to say the same thing) — the rail's twenty fit a 22 rem screen column, not a 37 mm margin, and the count in the disclosure still covers every item. The document-level panel now hangs beside the first document text as an ordinary margin note, not a full-width block under the frontmatter. Subresources (stylesheet, fonts, facsimile images) resolve in-process, never over the network. Results are disk-cached (`cache/pdfexport/`, LRU, 2 GiB cap) under a content-based key — sha256 of the stored page bytes + toc/kontext/andringar/kolumner + stylesheet text + WeasyPrint version (`PDF_FORMAT`, bumped to 7 with this layout) — so a regenerated or patched page invalidates by construction while byte-identical deploys keep their hits (brottsbalken with everything: 1058 pages, ~145 s cold on dev and some 3× that on prod, instant warm). A lock per cache key means two readers asking for the same cold export share the one render instead of paying for it twice |
| `accommodanda/api/pdfjob.py` | the export as a background job, so no reader waits on an open request: `parse_request(path, kontext, columns)` is the one place all three routes turn a request into a generated page and a context-kind set, forcing the kinds empty when `kolumner=2`. `POST /internal-api/v1/pdf/jobb` starts (or *joins*) a render on a two-thread pool and answers at once, `GET /internal-api/v1/pdf/jobb/{id}` reports how far it has come, and `/internal-api/v1/pdf/vanta` is the waiting screen the "Skapa PDF" button opens — a real address with a progress bar and a time left, which becomes the PDF when the render lands. Progress comes from WeasyPrint's own `weasyprint.progress` log, routed to the job by rendering thread and weighted by measured step shares (setup 17 %, layout 57 %, drawing 21 %, writing 5 %); the page count is estimated from the transformed document until layout's first pass proves it. This replaced a long `fetch` that outlived nginx's 60-second proxy timeout — a 504 for a render that had in fact succeeded — and a blank, addressless tab opened to hold the reader meanwhile |
| `accommodanda/api/analytics.py` | server-side Matomo tracking for the machine-facing surfaces, the counterpart to `lib/assets/matomo.js`'s browser tracker: an ASGI `Tracked` middleware counts the REST/OpenAPI surface (`/api/v1`, `/docs`, `/redoc`, `/openapi.json`; GET only, matched on segment boundaries — the editor's own routes live under the separate `/internal-api` app and never match, so `API_PREFIXES` no longer excludes them by name — minus *successful* same-origin browser XHR from our own pages, which the page tracker already counts), posted off the response path from one bounded-queue daemon thread (dropped when full); failures are counted whoever made them, under an `error` branch of the page title with the URL unchanged — on the MCP side read out of the JSON-RPC envelope after the response, since a tool failure comes back as HTTP 200 (`mcp._failed`, over a response capture bounded by `CAPTURE_MAX`); configured by `matomo_url`/`matomo_site_api` (`MATOMO_URL`/`MATOMO_SITE_API`, both required or nothing is tracked) — a separate Matomo site (3) from the reader-facing pages (2) and legacy lagen.nu (1); carries no visitor address (visits grouped by a per-process-salted hash of address+user-agent+date, no Matomo auth token stored). Wired on prod via `docker-compose.prod.yml` (`MATOMO_URL=http://matomo/matomo.php`) and a `/matomo/` location on the ferenda.lagen.nu vhost (`docker/nginx/ferenda.lagen.nu.conf`, Host pinned to `lagen.nu`, the only name Matomo's own config trusts) |
| `accommodanda/api/errors.py` | site 404/500 exception handlers — a rendered `error.html` page for site paths, JSON with an added `error_id` for the API/OpenAPI/MCP routes, one `lib/errorlog.py` ledger entry behind both; installed on both FastAPI apps (`api/app.py` and, since a mounted sub-app carries its own exception middleware, `api/internal.py`) |
| `accommodanda/lib/errorlog.py` | the served-site HTTP error ledger (`DATA/.build/httperrors.ndjson`, 8-hex ids, rotated at 8 MB keeping one `.1` generation) — the serving-side counterpart to `lib/runlog.py`'s build-side `errors.json`; read by `lagen all errors [<id>\|N]` |
| `accommodanda/lib/pins.py` | citation-shaped-query resolver, shared by REST `/search` and the MCP tools |
| `<catalog_root>/catalog.sqlite` | derived catalog (documents + links); `catalog_root` defaults to `data_root` |
| `site/data/generated/` | generated static site (`index.html`, `sfs/`, `dom/`) |
| `test/test_site.py` | derived-layer suite |
| `site/data/downloaded/sfs/sfsr/` | downloaded SFSR register pages (11,231) |
| `site/data/downloaded/sfs/pdf/` | official published-SFS PDF mirror (1998 onward), keyed by SFS number; the crop source for the `.graphics` layer and `/api/v1/sfs-graphic` |
| `site/data/.build/manifest.sqlite` | build freshness state (input + recipe hashes) |
| `site/data/artifact/{sfs,dom}/` | persisted parse artifacts (the source of truth) |
| `python -m accommodanda.sfs` | `parse` / `validate` / `refs` diagnostic CLI |
| `site/data/artifact/dom/identity-index.json` | canonical case → source records |
| `test/test_dv_identity.py`, `test_dv_parse.py` | DV suites |
| `test/test_lagrum.py` | citation test suite |
| `test/test_sfs_parse.py` | SFS structure + inline-link oracle suite |
| `test/test_sfs_register.py` | SFSR register/amendments/förarbeten/metadata suite |
| `accommodanda/sfs/download.py` | SFS harvester (beta raw-ES) + consolidation archiving |
| `test/test_sfs_download.py` | SFS downloader version/archiving suite |
| `test/test_sfs_graphics.py`, `test/test_sfs_pdfmirror.py` | SFS typed graphic-gap detection + vision-localization + official-PDF URL/worklist mirror suite |
| `accommodanda/sfs/redaktionell.py`, `test/test_sfs_redaktionell.py` | detects a stycke that is a publisher's editorial note (`endast-tryckt`/`upphavd`) rather than statute text; `nf.py`'s `retype_editorial` retypes the projected node in place |
| `accommodanda/sfs/asgit.py` | `history-as-git` export (one commit per amendment event, `git fast-import`) |
| `test/test_sfs_asgit.py` | golden fast-import stream + git round-trip suite |
| `test/files/` | hand-authored fixture corpora (oracle) |
| `lagen/nu/res/extra/sfs.ttl` | named-law dataset (live site data) |
| `site/data/downloaded/dv/` | legacy DV feed (Word docs) |
| `site/data/downloaded/dom/` | new DV API harvest |

## Conventions (from CLAUDE.md)

Target Python 3.14+. Avoid fallback code — assert how the environment
should be. Don't catch exceptions you can't recover from. Imports at top,
grouped. DRY, small functions, no "just in case" complexity.

A bare `pytest` runs exactly the new suites — pyproject's
`[tool.pytest.ini_options]` scopes collection to `test/test_*.py` minus
the `test/files/` fixture tree, so the legacy unittest files
(`integration*.py`, `test[A-Z]*.py`, …) that don't import under modern
Python are never touched.

The judgment-level conventions live as a citable rule catalog in
`docs/conventions.md` (rule slugs like `rule:fail-fast`), enforced by the
`.claude/` guardrails: PreToolUse hooks (path-keyed conventions reminders,
legacy-tree edit block, bare-suppression block, git-guard), the Stop hook
(ruff + ty + `check-layers.py` layer-boundary AST check on edited files),
review agents (`plan-reviewer`, `conventions-enforcer`, `docs-sync`,
`commit-planner`) and the `/wrapup-session` skill.

---

## Diagnostics & golden validation (run directly — *not* `lagen` subcommands)

The build pipeline is `lagen <source> <action>`; the regression/oracle tooling
below is deliberately separate (dev-only, never part of a production build) and
so is easy to forget. All are run by hand:

**SFS golden — `python -m accommodanda.sfs …`**
- `validate GOLDENDIR DOWNLOADDIR --sections structure,references,amendments,metadata`
  — corpus compare against the frozen golden. Reports
  `match + adjudicated = passing` and a per-rule adjudication tally; **`diff` is
  the genuine-regression count**. `--limit`, `--jobs`, `--top`, `--report`.
- `parse FILE` — normal-form JSON for one downloaded doc. `refs FILE GOLDEN` —
  one doc's references vs its golden.

**The adjudication overlay** (the "change-detector, not oracle" layer, §3d) lives
in `tools/golden_sfs.py`: `adjudicate(problems, golden) -> (unexplained,
accepted)`, driven by the `PREDICATES` table (`post-freeze-amendment`,
`stale-consolidation-drift`, `change-reference-staleness`, `balk-basefile-correction`,
`golden-chapter-collapse`, `celex-correction`, `eller-enumeration`, `stycke-pinpoint-drift`,
`brottsrubricering-begrepp`, `post-freeze-source-amendment`; a `chapter-state-leak` predicate
was tried and removed — it would have masked a real parser bug). Several predicates read the diff line's `«clause»` (the
source-node text appended by `format_ref`) — the context that makes them decidable. It runs **automatically**
inside `validate`, and also in `golden_sfs.py compare`. To add a rule: write a
`_predicate(problem, ctx)` and add one `(name, fn)` entry to `PREDICATES`
(extend the `ctx` dict in `adjudicate` if the rule needs more golden context).
Tests: `test/test_golden_adjudicate.py`.

**`python tools/golden_sfs.py …`** — `compare A B [--sections …]` (diff two docs,
shows adjudicated-vs-unexplained), `normalize FILE` (XHTML+RDFa → normal form).
The corpus run is `python -m accommodanda.sfs validate <parseddir> <downloaddir>`,
which normalizes each parsed XHTML to NF on the fly (no frozen golden, no freeze).

**DV goldens — `python tools/golden_dv.py …`** (reference graph vs old distilled
RDF) and **`python tools/golden_dv_structure.py …`** (`normalize` | `compare
PARSED ARTIFACT` | `validate` — the instance/ruling skeleton vs old parsed
XHTML; §4). The structural one measures `accommodanda/dv/structure.py`'s
segmenter against the parser's emitted `structure` section.

**EUR-Lex golden — `python tools/golden_eurlex.py {snapshot,compare}`** — there
is no legacy oracle for EUR-Lex, so this validates the carried metadata fields
(CELEX, date, title, OJ ref, ECLI, doctype) of a deterministic stratified
sample against a retained CELLAR metadata snapshot (`snapshot` draws/freezes
it over the network, `compare` is the offline change detector); `compare
--reparse` exercises the current parser instead of the stored artifact tree.
Same adjudication-ledger pattern as `golden_sfs.py` (§7d).

---

## Progress log

The blow-by-blow development history (dates, individual fixes, edge cases) lives
in `git log`. This document is the forest-level status; section markers
(✅/🚧/⬜) carry the current state. Milestones, newest first:

- **lib/dv/search** (2026-08-22) — a decision is findable by the case number it
  was filed under, months before its referat exists. A commentary names it that
  way and never otherwise: SvJT 2010 s. 94 discusses "Högsta domstolens dom
  2009-11-03 T 3-08", which the corpus holds as NJA 2009 s. 672, and neither
  the search index nor the citation engine could reach it from that string.

  `lib/malnummer.py` owns the printed shape, because both sides need it: one
  number is spelled three ways ("T 3-08", "T3-08", Arbetsdomstolen's
  "A-232-2013" — 877 of the 24,995 held numbers join the letter to the serial),
  and `normalize` spells them alike. The letter groups are a closed vocabulary
  (B, Ö, T, A, M, UM, P, … 29 in all), so "mål nr 4659-11" yields the number and
  not the word "nr"; the lookarounds keep a date out, since the "2009-11" inside
  2009-11-03 wears the same shape.

  **Search:** the whole-document unit gained a `malnummer` field, filled from
  the artifact key only dv carries. It is matched as a *phrase*, in its own
  clause (`search.case_number_queries`, weight 32), never as a field in
  `SEARCH_FIELDS`: per-term, the parts of a case number are ordinary numbers —
  373 of 2,109 sampled numbers hold a year-like token, so "brott 2009" would
  have promoted every decision numbered 2009-something over the documents about
  the subject. Quoting also means what it says again: `prefix_query` used to
  take the words out of the quotes, so `"T 3-08"` became `t* 3* 08*` and matched
  43,648 documents — a third of the corpus — with the one case filed under that
  number nowhere in the top 50. Measured after: `T 3-08`, `T3-08`, `"T 3-08"`
  and the whole SvJT sentence all return NJA 2009 s. 672 first.

  **Citations:** MALNUMMER is the second matcher-without-a-grammar parse type,
  built like EMDRATTSFALL. `lib/malnummer.spans` resolves over a new committed
  snapshot, `dv/data/casenumbers.json` (`dv/casenumbers.py`, `lagen dv
  casenumbers`, 24,411 numbers with `[court, date, local uri]` candidates), and
  links only what it can pin down: the citation must name a court that holds the
  number. The corpus holds no tingsrätt decisions, whose case numbers fill the
  same texts ("Södertörns tingsrätt mål nr B 4318-18") and collide with the held
  ones, and no second court may stand between the named one and the number
  ("HD prövade Södertörns tingsrätts dom i mål nr B 1-85" links nothing). A
  printed date decides between candidates — 298 numbers name more than one
  decision — but does not veto a lone one, since 12 of the 255 citations in a
  2,000-document sample whose number is held and whose court is named print the
  date of an interim beslut or of the föredragning instead. Over that sample the
  matcher resolved 243 citations and left every unheld number, every unnamed
  court and every section range unlinked. The search box reads the number by a
  stricter rule than the printed shape (`query_numbers`): a bare number counts
  only as the whole query, or "17 kap. 17-18 §§" would have become a hit on the
  decision numbered 17-18.

- **api** (2026-08-20) — a crop-review editor for the `.graphics` layer.
  `api/graphicsedit.py` is the content model for one entry (its
  `region_of`/`read`/`write`, the sibling of `api/editcontent.py`'s markdown
  regions); `api/graphics.py` routes `GET
  /internal-api/v1/graphics/{queue,page,pagesize,crop,review}` and `POST
  /internal-api/v1/graphics/cart`, all gated by `auth.require_editor`. The reviewer
  sees the crop and the whole source page with the rectangle drawn on it side
  by side — a confident placement on the wrong figure still returns a clean
  picture, and only the full page reveals it — and carts approve-as-is,
  approve-a-moved-rectangle or approve-the-whole-page, one entry at a time.
  `api/editcart.py` is generalized over draft *kinds* (`_region_of`/`_read`/
  `_write` dispatch on `graphicsedit.KIND`) rather than markdown alone, so the
  existing cart, conflict check and attributed commit carry a graphics
  decision unchanged; the markdown-only wiki index cache-clear now runs only
  when the committed cart holds a markdown kind. `build.rebuild_after_commit`
  gained a `graphics` branch: a reviewed entry needs no reparse or relate (the
  layer is read at generate time, `page._graphics_index`), so checkout
  regenerates only the host statute's page. The page/crop routes deliberately
  bypass `annstore.publishable` — an editor has to see an unreviewed crop to
  judge it — while the public `/api/v1/sfs-graphic` still 404s it.
  `test/test_graphicsedit.py`.
- **lib** (2026-08-20) — every `ai-*` layer records who authored it. A new
  `lib/llm.py` `_Window` accumulates one document's calls (`start_record`
  opens it; `complete_thread`, the single HTTP call site, folds each reply in
  via `_observe`); `record()` returns host, model, sampling, token counts,
  wall-clock span and `prompt_sha` — a hash of the prompts actually sent (the
  rendered text and, for a vision pass, the images with it), not of the code
  that built them, since only what reached the endpoint can have produced the
  reply. `annstore.write` stamps this as `meta.run` when a window is open and
  saw a call, then calls `llm.rearm()` so several layers authored in one
  process (`ai-includegraphics a b c`) each carry only their own calls; a
  mechanically derived layer (sfs road signs) arms no window and carries no
  `run`. The prompt hash lives in `meta.run`, not in the layer's `inputs`:
  `annstore.drifted` recomputes every input from its label alone, and
  recomputing a rendered prompt would mean running a source's prompt builder,
  which `lib/` may not do — so a prompt change does not mark old layers stale
  by itself, it shows up as a changed `prompt_sha` across runs. `build.py`'s
  six per-document `ai-*` actions (`sfs ai-correspond`, `sfs
  ai-includegraphics`, `forarbete ai-genomforande`, `eurlex ai-annotate`,
  `remisser ai-analyze`, `kommentar ai-annotate`) call `llm.start_record()`.
  `test/test_llm_record.py`.
- **render/api** (2026-08-20) — the recovered graphics become readable. Every
  crop is a `button.grafik-open` wrapping its `<img>`, and `assets/grafik.js`
  opens it full size in a lightbox (backdrop/Escape/× close it, a click on the
  image does not; focus returns to the opener). A crop now carries its own
  resolution: `facsimile.CROP_DPI` for the thumbnail printed in the text and
  `CROP_DPI_LARGE` for the lightbox, chosen by `/api/v1/sfs-graphic`'s new
  `stor` parameter and folded into the cache path and the URL's `v` hash — the
  response is `immutable` for a year, so a resolution raise behind an unchanged
  URL would reach no one. A förarbete illustration keeps the page resolution:
  it is shown once, at column width, and nothing opens it larger. Also fixes
  `sfs/graphics.py`'s road-sign alt text, which took only the designator line
  and so cut a wrapped caption mid-phrase ("C3 Förbud mot trafik med annat").
- **sfs** (2026-08-20) — road-sign graphics (2007:90, 326 signs) drop the
  vision model. `sfs/graphics.py`'s `roadsign_boxes`/`roadsign_index` read
  each row's page, crop rectangle and source act off the published PDFs'
  own text layer and rendered ink; `localize_roadsigns` places the gaps and
  reports the designators no PDF draws. `build.py`'s `_sfs_roadsigns_one`
  wires the route into `lagen sfs ai-includegraphics`, and the resulting
  `.graphics` layer writes with `annstore.DERIVED` status (a new third
  status beside generated/verified; `annstore.publishable` is now the one
  policy for what reaches the render). `pdftext.page_boxes` and `Line.bottom`
  supply the pixel-to-point geometry this needed.
- **api/render** (2026-08-20) — `/samling` adds account-free PDF collections.
  The browser keeps one localStorage draft and encodes a compact versioned
  recipe in `/samling#…`; JSON import/export is the long-link fallback.
  Each document has start, SFS-register, EU-preamble and multi-section choices.
  One WeasyPrint layout provides direct starts, mirrored context, a shallow
  printed TOC and a deeper PDF outline. A cover has a blank reverse. The first
  document starts recto. A bounded shared job queue handles the render. Real
  SFS/EU/proposition output and a 5,002-page capacity run were verified.
- **api/render** (2026-08-20) — the PDF export's print layout rebuilt as
  mirrored book pages: a right page holds text at 28 mm (117 mm wide) with
  the apparatus at 145 mm (55 mm wide), a left page mirrors both, through
  `@page :left`/`@page :right` boxes. `api/pdf.py::_mirror_margin_notes`
  moves the note-cell boxes to the verso outer edge after WeasyPrint layout,
  since its grid paginator is superlinear across hundreds of SFS provisions
  (`body.pdf-weasy` keeps the fixed-table path for that reason; the
  standalone CSS grid stays the reference implementation for other engines).
  `_split_sfs_provisions` breaks a § into one fragment per annotated stycke
  or point, so a later provision no longer waits on an earlier note that
  outruns its own text. Two new export options: `andringar` (keep or drop
  the SFS amendment and transition register; the kept register loses its
  screen-only links) and `kolumner=2`, a compact two-column layout
  (`@page compact`, 8.25 pt, two 89 mm columns, 8 mm gutter) that omits
  context entirely; `pdf.js`'s dialog gained both controls.
  The document-level context panel is now a normal margin note beside the
  first document text instead of a full-width block under the frontmatter.
  `PDF_FORMAT` 2 → 7.
- **api/lib/config** (2026-08-21) — the patch tree moves out of the code repo
  into the **content repo**: `accommodanda/patches/` → `<WIKI_ROOT>/patches/`
  (66 files), `layout.PATCHES = config.WIKI_ROOT / "patches"`. A patch is the
  same kind of thing as a commentary or an annotation layer — hand-authored
  editorial knowledge about one document — so it belongs where those live. The
  separate `patch_repo`/`PATCH_REPO` setting added two days earlier is gone,
  with `resolve_patch_repo` and the second prod bind mount
  (`/mnt/data/ferenda-patchrepo:/patchrepo`); `api/patch.py::_commit` commits
  into `config.WIKI_ROOT`, the same checkout the commentary editor writes to,
  so the running site has one write target, one push and one ops row instead of
  two. `layout.patch` still asserts the tree exists (an absent one reads as "no
  patch" and would republish every redaction), which is why `test/conftest.py`
  now creates an empty `patches/` under the session's throwaway `WIKI_ROOT`.
- **api/lib/build** (2026-08-19, superseded 2026-08-21) — `config.py` gained
  `resolve_patch_repo`/`PATCH_REPO` and `lib/layout.py`'s `PATCHES` derived
  from it instead of from a path anchored to the package tree, so the `/patch`
  editor could commit every save (`api/patch.py`'s `_commit`) from a deployed
  image built without `.git` (`.dockerignore`). Prod bind-mounted a second
  checkout for it. The entry above replaces that arrangement with the content
  repo the site already writes into.
- **lib/api/coe** (2026-08-15) — a corpus-wide graph explorer,
  `/hanvisningar/` (`lib/templates/hanvisningar.html`, `lib/assets/graf.js`)
  over a new `GET /api/v1/graph` (`api/reads.graph`, `catalog.py`'s
  `graph_*` queries). Aggregated per neighbor document rather than per
  citation, grouped by a node vocabulary (`lib/facets.FLOW_GROUPS`/
  `flow_group`) moved out of `stats/compute.py` so the graph explorer and
  the stats sankey (§7k) share one map of what a node is; `pinpoint.py`
  gained `unit_anchor` to collapse a fragment to its pinpointable §/article
  for the per-document internal-graph view a fragment uri asks for.
  `coe/parse.py` now links a bare "Article N" in a treaty's own text to the
  sibling provision it names (only ordinals the instrument holds; an
  external treaty citation wins the overlap), giving the ECHR — the
  explorer's default center, article 6 — 29 internal article links to draw.
- **hudoc/coe/icrc/avg/lib** (2026-08-15) — §7j's citation work continues:
  `lib/emdref.py` is EMDRATTSFALL, the Swedish-text ECHR-citation matcher the
  previous entry left designed but unwritten ("Osman mot Förenade
  kungariket", "ansökan nr 23452/94"), over the committed
  `hudoc/data/casenames.json` snapshot with `hudoc/citations.py`'s
  disambiguation ported verbatim. `hudoc/treaties.py` links a judgment's own
  Convention/protocol short forms ("Article 8 of the Convention") over
  `lib.treatyref`, merged into `hudoc/parse.py`'s inline citations beside
  the case-law spans; `coe/parse.py` and `icrc/parse.py` gained the same
  `refs_for` shape for the sibling treaties a text names (self-citations
  excluded). `lib/treatyref.py` gained `generic_names`/`generic_context`
  (a `CONTEXT_WINDOW` of 150 chars) so an ordinal protocol name binds only
  beside its family's own name, and `treaty_names.json` gained the eight
  ECHR protocols that carry their own articles. Three more `lagrum`
  productions: STALLNINGSTAGANDE (Skatteverket ställningstaganden by dnr
  shape → `rs/skv/…`), `jo_arsb_ref` ("JO 2003/04 s. 450" → the JO decision
  it names, through a new committed snapshot `avg/data/arsberattelse.json`
  that `avg/arsberattelse.py`/`lagen avg arsberattelse` writes off the JO
  artifacts' own `officialReport` field, 1,607 of 1,608 at census), and
  `so_ref` ("SÖ 1982:50"). Plus two corpus-measured fixes: a
  width-preserving whitespace normalization recovers 1,339 "NJA … s. …"
  citations HD's 2016-2020 referat typography had put out of reach, and the
  letterless CJEU form is now year-bounded to 1954-1989 so an ECHR
  application number of the same shape stops minting a celex that doesn't
  exist. `lagen all <action>` now skips a source lacking the requested
  action instead of hard-erroring, same as the download branch.

- **icc/icj/hudoc/lib** (2026-08-14) — the three international-law sources'
  own citations start resolving to links, §7j. `icc`'s 269 decisions
  carried an empty `references` and the whole `icrc` corpus — the Rome
  Statute among it — had zero inbound links, though the decisions are made
  of those citations: 13,887 "article N … of the Statute" forms across 244
  of the 269. `lib/treatyref.py` is the shared matcher (curated names from
  `lib/data/treaty_names.json`); five rules earn their place, each from a
  measured failure — nearest-instrument binding, forward-only direction,
  no backward binding past an unmatched "of \<Instrument\>", roman
  numerals (the Genocide Convention runs Article I–XIX), and enumeration
  ("articles 15, 53, 54, 58 and 61 (5)" is five citations). It now yields
  2,867 references from 250 ICC decisions and 1,050 from 163 ICJ ones,
  over 21 instruments. `catalog.dangling_anchors`, run at the end of
  `relate` and scoped to the sources whose pages anchor exactly their
  artifact's nodes (`icrc`, `untc`), caught the defect that started this
  effort: 126 references pointing at `#A42` on a Hague Convention that
  anchors its Regulations under `#Annex42`.

  A second pass put those citations where the reader meets them, not only
  in the rail: `lib/artifact.py`'s `numbered_nodes` takes an optional
  `refs_for` scanner, so `hudoc`, `icc` and `icj` can wire their own
  citation grammars as inline links. `hudoc/citations.py` resolves a
  judgment's case-law citations against the held corpus's own metadata —
  88% of ~175,000 application-number citations across 13,567 judgments
  name a document already held. `icj/reports.py` reads the Court's own
  "I.C.J. Reports 1990, p. 92" grammar off each decision's cover sheet
  (227 of 255 held covers yield one) and links a body citation whose
  (year, page) is a held decision's own start page — never a pinpoint
  past it, since with 255 of 877 decisions held a gap between two start
  pages says nothing about the unheld decisions in it (rule:fail-fast).
  `icc/treaties.py` adds the Court's own filing-number citations, 1,687 of
  which point at a sibling decision the corpus holds, and `icc/parse.py`
  drops the Legal Tools download's own footer furniture (3,116 "PURL:
  https://www.legal-tools.org/…" stamps across 92 decisions) that had been
  sitting in the rendered body text. The shared arabic/roman
  article-fragment grammar moved out to `lib/treaty_ids.py`
  (rule:second-use-goes-to-lib), the UN/IHL twin of `lib/coe_ids.py`, so
  `lib/lagrum.py` can use it without closing `lagrum -> treatyref ->
  catalog -> markdown -> lagrum`. Riding along in `lagrum.py`: Swedish
  treaty names ("artikel 24 i barnkonventionen") now link `untc`/`icrc`
  targets, kommittédirektiv and the dot-dropped prop./rskr. forms parse,
  a four-digit riksmöte year folds to the corpus form, and a mislink is
  fixed where "artikel 30 i förordningen (EG) nr 765/2008" fell through
  to the anaphora branch because the definite noun form didn't parse as
  naming its own act.

- **icj** (2026-08-13) — a sixth folkrätt source, §7j: `icj/` harvests
  International Court of Justice case law. Scope is the Court's own word
  on the law — 255 of its 877 decisions: 158 judgments, 31 advisory
  opinions and the 66 orders indicating provisional measures. The ~620
  docket orders that fix and extend time-limits are out, as are the
  parties' written and oral pleadings and the PCIJ series.

  The reason was measured before the code. `untc`'s 14 treaties and
  `icrc`'s 111 had **zero** inbound links: nothing in 296,240 documents
  cited the Genocide Convention, the VCLT, UNCLOS, the ICCPR or CAT,
  though the corpus holds them all. An ICJ judgment is the document that
  does. The corpus also already cites the Court and resolves nothing —
  1,669 hits for "Internationella domstolen" (1,525 in förarbeten, 41 in
  SFS) and 403 for "ICJ Reports" (294 of them from `hudoc`). Sweden is a
  party in three ICJ cases, one squarely Swedish law: *Guardianship of
  Infants* (Netherlands v. Sweden, 1958), on a barnavårdsnämnd's
  skyddsuppfostran against a Dutch guardianship order. `icj/treaties.py`
  emits the treaty-level `dcterms:references` that make the join, matched
  on each instrument's authoritative English title and the short form the
  Court uses ("the Genocide Convention"); the bare word "Convention"
  deliberately cites nothing, since it names whichever instrument the
  decision is about.

  Two transports for one index. `icj-cij.org/decisions` answers ordinary
  HTTP and returns all 877 rows in one request with `from=1946` — its
  default is `from=2023`, which shows 87, and it does not paginate. No
  `to` is sent: the select offers only years up to the current one and
  answers an out-of-range year with an empty page under a 200. The
  decision PDFs sit behind a Cloudflare challenge no header or cookie
  from the index clears, so they come through
  `lib.browser.DetachedChrome`, the headful transport `rs` and
  `foreskrift` already use — one session for the whole run, ~9 s per
  document, 40 minutes for the 255.

  Each decision is its page range from the printed *I.C.J. Reports*, so
  the PDF opens with the publisher's bilingual front matter; `body_pages`
  cuts at the Court's letterhead over a `YEAR` line. 138 of the 255 are
  scans with an OCR layer — a measurement, not a date: a scanned page
  range carries a raster image on every page (0.95–1.00) against
  0.00–0.03 for a typeset one, and a date rule would have been wrong,
  since the July 2004 Wall opinion is a scan and the December 2004
  judgment in the same volume is not. `icj/ocr.py` repairs that layer's
  systematic confusions — `l` read as `1` ("al1", 400 occurrences), `m`
  split into `rn` ("Judgrnent", "Charnber", 235), ~0.43% of tokens —
  dictionary-guided against a vocabulary harvested from the born-digital
  decisions, so the corpus defining "a word" never depends on the repair
  being right. A token of pure digits is never touched: rewriting "111."
  to "iii." ended one judgment's paragraph sequence at 110. The page says
  so where the repair count reaches five, a threshold read off the corpus:
  the 138 scans repair a median of 19 words and the 117 typeset decisions a
  median of 0, but 27 typeset ones repair 1–8, so a "nonzero" test would
  tell 27 readers their text was machine-read when it was not.

  The numbered paragraph is the citation anchor, and the Reports set each
  one flush with the paragraph above it, so `page_paragraphs` returns a
  whole run of reasoning as one block. `paragraph_chain` cuts those runs
  by taking every number in reading order and keeping the longest chain
  that counts up, stepping over as many as three numbers the scan lost. That tells the
  Court's "5." from "Article 5. The Parties" and from an ICTY paragraph
  531 quoted inside the Court's own paragraph 309. Three earlier designs
  each failed measurably: walking forward from "the next number I expect"
  stopped at the first hole (paragraph 74 of 524 in Croatia v. Serbia);
  rejecting a whole block that held the printer's imposition stamp lost
  paragraphs 75–524 of the same judgment; requiring a step of exactly one
  split a decision into two chains and kept only the longer.

  A real harvest has run: 255 downloaded, 255 parsed, 0 errors, 56,609
  blocks and 23,579 numbered paragraphs. 162 decisions (64%) carry at
  least one treaty reference — 327 in total onto 12 of untc's 14
  instruments, which had none at all before. A random sample of 14
  decisions was checked span by span: 19 of 19 references land on a real
  mention of the named instrument. Reuse is under the ICJ's
  non-commercial condition, which lagen.nu meets; this is not the 2 § URL
  freedom SFS and propositioner have, and it was weighed before the work
  started.

- **hudoc** (2026-08-12) — the source could never reach past its 10,000th
  document. HUDOC answers a query past `start=10000` with an empty page while
  still reporting the true `resultcount`, so the unsliced walk stopped there
  and the store held 7,060 of 21,672 judgments, reaching back only to
  2009-09-22 — Handyside and Golder were unreachable. The walk is now sliced by
  year, newest year first, so the stream stays globally newest-first and the
  watermark stop is unchanged; a year past the cap raises, and an exhausted
  enumeration checks its summed year counts against the collection total. The
  **decisions** collection (33,633) joins judgments as a second download scope
  with its own watermark: a decision is where the Court says why a complaint
  never reaches the merits, and 922 of the 1,088 Swedish cases are decisions.
  Admitting it exposed a judgment-shaped invariant in `parse.py` — the skip
  guard tested for a numbered paragraph, which a decision does not have, so 62%
  of the collection parsed to an empty artifact; the guard now tests for a body
  with neither a numbered paragraph nor a heading (0% of 400 sampled decisions
  and 400 judgments skip). Two things the Court publishes *about* a case are
  linked rather than republished: its own Case-Law Information Note
  (`summaries.py`, joined on application number + date) and Domstolsverkets 87
  Swedish translations, which become commentary on the judgment they translate
  (`translations.py`, joined on ECLI). Both ride along on an unbounded
  download rather than being commands of their own, since each costs one index
  walk and no body fetch. Both joins first assumed a key identifies one case,
  which held for the judgments-only store and not for decisions: HUDOC stores
  some decisions twice and mints one ECLI for decisions taken together, so
  `download.unique_index` now drops such a key and counts it, and reserves
  raising for the store that holds two language expressions of a case.

- **foreskrift/forarbete/icc/icrc** (2026-08-09) — four extraction fixes found
  by the UX audit, each measured over the corpus rather than eyeballed.
  `foreskrift/parse.py` picks the in-force date by the document's *role*: a
  base regulation takes the first "träder i kraft" date it declares, an
  amending one the last, and a konsoliderad masthead vetoes the amending
  reading. Impossible dates fell from 328 to 18; 397 föreskrifter were
  corrected and none lost. `forarbete/parse.py` gained two classifier rules —
  a dateline plus signature block is furniture, not a heading (597 signatory
  names stopped reading as headings across 300 documents), and a figure label
  is told from a heading by the typefaces the document sets *text* in
  (`text_faces`: body face ∪ numbered-heading faces ∪ any face carrying
  running prose), scoped to pages that carry a caption. Both rules add zero
  headings. `icc/parse.py` asks poppler for the invisible OCR layer its scans
  carry, taking metadata-only decisions from 119 to 2. `icrc/parse.py` reads
  the ICRC's `empty` section label for what it is — *no section label*, not
  *no content*: a block with text is the treaty (32 undivided 19th-century
  declarations had lost their whole text, plus 69 operative parts of divided
  ones), a textless block between articles is a division heading, and a
  textless block outside the run of articles is commentary and dropped.
- **wiki/lib** (2026-08-09) — `lib/markdown.py`'s block parser is now
  **markdown-it** too, closing the gap the site vertical's 2026-08-03 swap
  left open: the hand-rolled scanner behind the commentary/concept pages kept
  MediaWiki list markers in the prose (`* Fritt utnyttjande … * Begränsat
  utnyttjande …`, one run-on line) and printed HTML comments as body text — a
  2009 note the authors left themselves, and the passage under it, rendered as
  commentary on LAS, rättegångsbalken and avtalslagen. `blocks()` now walks
  `MarkdownIt("commonmark", {"html": False})`'s token tree into `rubrik`/
  `stycke`/`lista`/`avskiljare`, same as `site/parse.py` (which builds the same
  engine with `.enable("table")` for its `tabell` node); the inline layer
  (links, lagrum citations) stays hand-rolled. New `strip_comments()` drops
  `<!-- … -->` before either parser sees the body — shared by both, since
  `html: False` means "keep as text", not "drop". `wiki/parse.py` emits the new
  `lista` (with `punkt` children) and `avskiljare` blocks as artifact nodes;
  `lib/page.py`'s node walk renders them `<ol|ul class="punkter">` and `<hr>`.
  New `tools/unfold_wiki_lists.py` is a one-off repair over the already-
  converted `lagen-wiki` markdown: the shared `wikitext.blocks()` joins
  consecutive non-blank source lines into one paragraph, so a MediaWiki list
  spanning several source lines converted to one run-on markdown line with
  its markers still inside it; the tool re-splits each into one item per line
  (86 lists across 66 files in `commentary/`+`concept/`, applied once).
- **browse/foreskrift** (2026-08-09) — `browse.render_landing(source, view,
  banner)`: a source's root page as a landing over all its primary buckets
  (by count), replacing the old default — a byte copy of the first leaf.
  Wired for eurlex only: its first leaf is one treaty's 8 consolidated
  versions, no way to open a corpus of 50,000 acts (V4); other sources keep
  the default-bucket root until each has a landing worth the swap.
  `lib/page.py` gained `BRAND` (`Markup("lagen<em>.nu</em>")`) and
  `page_context`/`page` gained `title_html`, so a page's `<h1>` can carry
  markup distinct from its plain-text `<title>` — both frontpage builders
  (`site/render.py`'s editorial frontpage, `lib/render.py`'s corpus-stats
  one) now pass `BRAND` there instead of duplicating the brand markup.
  `catalog.py` gained `andrar_inbound` (mirroring `upphaver_inbound`): the
  inbound side of the `rpubl:andrar` edge, so a base regulation whose agency
  never listed its own amendments (SJÖFS 2005:25) still shows them, unioned
  with its harvest register (`foreskrift/render.py`'s `_andrad_genom`) as new
  "Ändrad t.o.m." / "Ändrad genom" meta rows.
- **lib/dv/eurlex/sfs** (2026-08-07) — the patch layer's shape changed on three
  fronts, on top of §7's port of the legacy sfs/dv patches. New `lib/markup.py`
  (`block_lines` for HTML, `indent_xml` for XML) puts one block element per
  line in a markup document without changing what its parser reads out of it —
  needed because a unified diff is a diff over lines, and two patchable
  sources ship their whole body on one: ~9% of dv's API records, and eurlex's
  Formex/OJ manifestations as a rule (median longest line 45,508 characters).
  `patchsource`, `dv.parse` and `eurlex.parse` normalise through it, only when
  a document actually has a patch. dv's patchable intermediate is now the
  *whole* API record JSON, not just the `innehåll` body, so a redaction
  reaches structured metadata (`malNummerLista`) as well as the running text —
  dv now has three intermediates (the record JSON; the court's own PDF as
  pdftohtml XML for a verdict published before its referat; the frozen notis
  XML for a legacy-only case); a legacy Word referat stays unpatchable.
  Redaction patches are now stored **ROT18** (`.rot18.patch`,
  `lib.patch.obfuscate`), not ROT13 — ROT13 rotates letters only, so every
  personnummer/organisationsnummer/telephone number a redaction removed was
  still readable in the "obfuscated" file; the CLI flag is `--obfuscated`, the
  API field `obfuscated`/`is_obfuscated`. Archived SFS consolidations (the
  `versions` stage) now offer their statute's patch non-fatally via
  `lib.patch.apply_if_fits`: a **correction** that doesn't fit an older wording
  is skipped, a **redaction** that doesn't fit stays fatal and the version is
  recorded as skipped rather than published unredacted.

- **sfs/lib** (2026-08-06) — a named law now resolves against the act that bore
  the name *when a document was written*, not always the current one: `namedlaws.json`
  mapped each name to one SFS id, so a 2010 decision citing "11 kap. 1 §
  socialtjänstlagen" resolved to 2025:400, a statute that didn't exist yet — found
  as a context rail on 11 kap. 1 § listing 5 rättsfall and 100+ myndighetsbeslut all
  older than the law. The dataset stays keyed by SFS id, but a name may now span
  several ids with `from`/`until` (245 → 309 entries, 49 names dated, 64 predecessor
  rows added, derived from the corpus by the new `tools/namedlaws_history.py`).
  `lib/lagrum.py`'s `load_namedlaws`/`load_abbreviations` return a `NamedLaws` (dict
  subclass, `.at(name, when)`); `LagrumParser`/`sfs_parser` gain `written=`, `reset`
  gains it per document for a cached parser. `lib/util.py` gained `approximate_date`
  (a partial date → the middle of the span it can mean — mid-month, mid-year, or for
  a riksmöte its turn-of-year). dv/avg/rs/foreskrift now pass their decision/beslut's
  own date; förarbete's `written_date` falls back to the basefile's year/riksmöte
  (57% of that corpus records no date); wiki stays undated on purpose. **Not yet
  reparsed** — dv, avg, rs, foreskrift and forarbete need a full reparse before the
  corrected links reach the rail. See §3d.

- **render/remisser** (2026-08-06) — the remiss rail states how a section was
  received, not just who answered. Each answer carries a five-level sentiment
  mark set as a geometric shape (direction by the triangle's orientation,
  strength by whether it is filled, a diamond for neither): a lone "−" read as a
  dash separating the organisation from its quote, which inverted the meaning of
  every critical entry at a glance. Three or more answers to one section also get
  a verdict above the list ("Avsnittet har **övervägande kritiserats**") — below
  three there is no mottagande to describe. `page._sentiment_level` is the single
  reader of the band table, so the mark and the verdict cannot disagree about
  where neutral ends (they did, at exactly ±0.15). The rail also decides focus by
  *extent* now rather than by the marker element's own box: a förarbete marks the
  heading itself, one line high, so containment on that rect alone opened a
  section's panel for the moment its title crossed the focus line and closed it
  over the whole body beneath — while the previous nearest-preceding-entry rule
  left 3.2.3's remissvar standing beside an unannotated section 4, saying those
  organisations had commented on it. A heading's extent now runs to its next
  same-or-higher sibling, bounded by every heading rather than only the annotated
  ones. The facsimile control became a real tab pair (page number ⇄ Original,
  `aria-selected`, the selected tab inert) after reading as a toggle disguised as
  tabs, and `ai-analyze` skips answers under `MIN_ANSWER_CHARS` when expanding a
  whole ärende — measured segment yield runs 0% below 300 characters and 16% at
  600–900, against 60% at 900–1200.

- **eurlex** (2026-08-06) — the corpus now names what it deliberately does
  not carry. `UNCARRIED` (CELEX → why) makes `parse_dir` raise `SkipDocument`,
  so the driver writes the empty artifact that marks a document
  built-and-not-to-be-retried: the catalog drops its row and the index its
  units. The bar is *the document cannot be served*, not "it parses badly" —
  the single entry is 32018R0688, whose annex I is a 6,000-page EBA reference
  portfolio (a 97 MB Formex file parsing to 50 M characters and rendering to a
  53 MB page). Each entry states the measurement that meets the bar, in terms
  that stay true, so a later reader can retest rather than inherit it: drop the
  entry, reparse the CELEX, reindex, look for the named failure. Nothing in the
  driver reaps `generated/`, so uncarrying an act that was previously carried
  means unlinking its html by hand, on dev and on prod.

- **forarbete/lib** (2026-08-06) — three `pdftext.page_paragraphs` misreads
  fixed, all found on SOU 2025:115: a superscript footnote-reference marker
  stands as a line of its own (its raised baseline sorts *above* the text it
  follows), so left standing it set the paragraph's own size, forced a
  mid-sentence paragraph break and printed ahead of its own text — new
  `drop_marker_lines` drops it, keeping only a footnote's own leading number
  (told apart by sharing its `top` with a footnote-sized line); the running
  header's residue (a chapter title set beside the identifier survived
  stripping as its own paragraph, 598 of the document's pages, each read as a
  `fotnot`) is now dropped alongside the identifier when it stood as its own
  run and its size *differs* from the page body, not merely falls below it (a
  bilaga's running head is set larger than its own smaller body); and box
  detection (`Para.boxed`, the ruled `ruta`) is now read per contiguous inset
  run — ≥2 lines sharing a left edge (`aligned`) and filling most of the
  body's measure (`measured`, `BOX_MIN_MEASURE`) — with the page's margin read
  as the leftmost start a real share of its lines agree on (`MARGIN_SHARE`)
  and the measure as the furthest right edge among lines starting there,
  rather than off a single page-wide mode, so a page given over to a ruled
  box no longer outvotes the body for its own geometry. `join_across_pages`
  now also closes a word a page break hyphenated, told apart from a hanging
  Swedish compound coordinator ("studie- och yrkesvägledare") by the
  conjunction after it. `forarbete/parse.py` gained `running_text_size` (the
  smaller of a page's own dominant size and the document's — fixes a bilaga
  reproducing text smaller than its body, and SOU 2015:93's near-50/50 split
  between two body sizes, both of which the document-wide mode was reading as
  footnotes or flipping on a hundred paragraphs) and `heading_level_by_size`
  (a font-size → heading-level map learned from the document's own numbered
  headings, gated to sizes where numbered headings are a majority of what's
  set in them and excluding a lagförslag's own kap./§ headings — this is what
  finally places unnumbered display headings like "Sammanfattning" at their
  real level instead of filing them as stycken, SOU 2018:82). Only SOU
  2025:115 and SOU 2018:82 were rebuilt to check these; the corpus at large is
  not yet reparsed. Two unrelated fixes landed alongside: `build.py`'s
  `remisser_ai_analyze` now skips answers under
  `remisser_analyze.MIN_ANSWER_CHARS` (900) when expanding a whole ärende,
  since a segment almost never results below it; and `lib/assets/drawers.js`
  pins the mobile bottom toolbar to `window.visualViewport` rather than the
  layout viewport, which on iOS strands the bar mid-page as the browser
  chrome shrinks or grows.
- **remisser/lib** (2026-08-04) — `lib/pdftext.py` gained four source-agnostic
  PDF-cleaning functions for a corpus with no fixed masthead to name: each of
  remisser's ~90 organisations answers on its own letterhead, so a running
  header can only be found by shape, not a passed-in identifier.
  `strip_page_furniture` finds it by digit-masked repetition across pages +
  margin position + font size; `drop_footnotes` drops footnote text and its
  superscript markers by the same size-drop test that finds förarbete's
  footnotes, applied to discard rather than keep them (a remissvar's footnotes
  are almost always a bare source reference, never the sentence stating why
  the organisation objects, and poppler splices the marker into the middle of
  that sentence); `join_across_pages` rejoins a sentence a page break split;
  `strip_addressing` drops the masthead/reference-line/contact block by
  composition (address tokens + reference labels), which repetition cannot
  catch since a masthead prints once. `remisser/parse.py`'s `_body_text` now
  runs all four, and the whole corpus (79,982 answers) was reparsed.
  `lib/text.py` gained `sentences(text, clause_breaks=False)`, the
  Swedish-abbreviation-aware sentence splitter factored out of
  `labels._first_sentence` (rule:second-use-goes-to-lib) once a second caller
  needed it. `remisser/ai_analyze.py`: `lagen remisser ai-analyze` now accepts
  a whole ärende (`sou/2026-21`) as well as a single answer, expanding it to
  every fetched answer and skipping ones already analysed unless `--force`;
  the prompt now asks for the *reason* an organisation gives rather than its
  verdict, adds a `quote_type` (`grund`/`standpunkt`) so "no reason stated" is
  a legitimate answer instead of an invented ground, and anchors the
  sentiment scale's intermediate values; a reworded quote is snapped back to
  the answer's own wording (`snap_to_source`, `difflib` similarity against
  `answer_units` — sentences split further at clause boundaries) instead of
  only being rejected. `build.py`'s ai-analyze action survives a per-answer
  failure instead of abandoning the rest of the ärende, and reports which
  answers failed. New `tools/remisser-eval/` scores ai-analyze output against
  a hand-built `.ann.key` answer key, following `tools/aigenomforande-bench`'s
  shape. Corpus still publishes no pages of its own — this feeds the referred
  förarbete's rail, unchanged.
- **site** (2026-08-03) — `site/parse.py`'s hand-rolled block scanner replaced
  by **markdown-it-py** (CommonMark + the GFM `table` rule, `html: False`);
  `blocks(body, where)` walks the resulting `SyntaxTreeNode` onto the
  vertical's typed blocks, raising `ValueError` naming the basefile for a
  construct with no block form rather than dropping the prose
  (rule:errors-drive-retry-use-raise). Link *targets* still resolve through
  the shared `lib.markdown.target_uri`, extended locally with site-relative
  `/…`/`#…` and `mailto:`. `model.py` gained `Table` (`tabell`; head/rows/align,
  each cell its own run list) and `Rule` (`avdelare`); `Bullets` gained
  `ordered` (`<ol>` vs `<ul>`); runs gained `italic`. `render.py` emits tables
  (with `text-align` from `|---:|`), ordered lists, `<hr>` and `<em>`.
  `markdown-it-py` moved from a transitive (`rich`) to a declared dependency.
  Fixes live defects: ordered lists and italics on the `/om` pages were
  rendering as literal `1.` and `*asterisks*`, tables as walls of pipes.
- **render/lib** (2026-08-02) — `lib/render.py` (4,551 lines) split three ways.
  The shared page kit — `Site` (the render context), the generic node walk
  (`render_node`/`render_runs`), the context rail (`Rail`/`RailSection` + the
  margin builders) and the page shell (`page`/`page_context`, the `dl.meta`
  block, the TOC collector) — is now `lib/page.py`, knowing no source by name.
  Each of the 14 sources with pages (sfs, dv, forarbete, eurlex, hudoc, coe,
  icrc, untc, icc, edpb, foreskrift, avg, rs, and wiki for begrepp) gained its
  own `render.py` exposing `render(art, site) -> str`, built on the `lib/page`
  kit, with its page template moved from `lib/templates/sources/*.html` to
  `<source>/templates/` (resolved via `lib.tpl.environment(package)`, which
  falls back to `lib/templates`). `lib/render.py` is now corpus-wide site
  assembly only — frontpage, folkrätt/EU-rätt landings, feeds, static chrome,
  `generate_site`. The faceted browse tree — generated by driving `api.app`
  through a `TestClient`, the one sanctioned lib→api inversion — moved out of
  `lib/render.py` into new `accommodanda/browse.py`, retiring the last
  `check-layers.py` `ALLOWLIST` entry: `lib/` no longer imports `api` at all.
  `render.generate_site` now takes a required `renderers` registry
  (`build.SOURCE_RENDERERS`, composed one layer up since `lib` may not import
  a source); `render.render_aggregates` lost its `catalog_path` argument and
  no longer writes browse pages (`build.cmd_generate` calls
  `browse.generate_all` alongside it). `build.GENERATE_CODE` now globs
  `*/render.py` and `*/templates/**/*.html` instead of listing files, so a new
  source's renderer joins the recipe by existing rather than by someone
  remembering to add it. Along the way: `lagrum.sfs_parser()`;
  `harvest.record_unchanged`/`write_record`/`store_record` plus a
  `watermark: HarvestWatermark | None` parameter on `harvest.walk`;
  `doctype`/`CASELAW` moved from `eurlex/model.py` to `lib/eu_structure.py`;
  `check-layers.py`'s `VERTICALS` extended to cover hudoc, coe, icrc, untc,
  icc, edpb, rs and stats.
- **api** (2026-08-01) — the MCP server moves to protocol revision
  **2026-07-28** (SDK `mcp` 1.28.1 → 2.0.0, `FastMCP` → `MCPServer`), the
  revision that deleted the protocol's session concept: no `initialize`
  handshake, no `Mcp-Session-Id`, every call a self-contained POST carrying its
  version and capabilities in `params._meta`, with `server/discover` replacing
  the handshake's capability exchange — so any request may land on any process
  and `/mcp` scales behind plain round-robin. The transport settings moved from
  the constructor onto `streamable_http_app()`; the root-logger guard and the
  session-manager lifespan both stay load-bearing (verified, not assumed). The
  same endpoint still serves 2025-era clients, and `test/test_mcp.py` now drives
  both eras against one running server — one server because `session_manager
  .run()` is once-per-process, so a second uvicorn boot deadlocks on a lifespan
  that can never start. `tools/list`/`server/discover` are advertised cacheable
  for an hour and shareable (SEP-2549), the tool table only changing at deploy.
  Alongside it, `search`/`fetch` now satisfy the result contract OpenAI's hosts
  expect of a knowledge server, met by *naming* fields rather than narrowing
  tools: `search` gained an `id` key (the fragment URI on a paragraph-deep hit,
  so a fetch reads the provision and not the whole statute), `fetch` is a thin
  wrapper over `get_document`, and the citation-graph tools are untouched — the
  contract is a projection of the read view, deliberately not the model
  (rule:own-typed-model). Both declare `TypedDict` returns, which is what makes
  the SDK emit `structuredContent` at all. The relock also surfaced a latent
  bug: `lib/net.py` imports `httpx` (a different package from the declared
  `httpx2`) and had been getting it transitively from mcp 1.x, so the KKVFS
  HTTP/2 harvest would have broken on the next clean `uv sync`; `httpx[http2]`
  is now declared outright.
- **operations/lib** (2026-07-31) — the served-site error ledger lands, the
  serving-side counterpart to `lib/runlog.py`'s build ledger: `lib/errorlog.py`
  (append-only `DATA/.build/httperrors.ndjson`, 8-hex error ids, rotated at
  8 MB keeping one `.1` generation) and `api/errors.py` (FastAPI 404/500
  handlers — a rendered `error.html` page for site paths, JSON with an added
  `error_id` for `/api/`/`/docs`/`/redoc`/`/openapi.json`/`/mcp`, one ledger
  entry behind both), installed from `api/app.py`. New CLI verb `lagen all
  errors [<id>|<N>]` prints the ledger newest-first (default 50) or one entry
  in full with its traceback, given the 8-hex id an error page showed the
  reader. `lib/util.py` gained `now_iso`/`append_json_line`, factored out of
  `runlog.py` and now shared by both ledgers. Alongside it, `lib/pdftext.py`
  gained `repair_pdf` (a ghostscript rebuild of a PDF whose cross-reference
  table poppler refuses outright, cached beside the source), wired into
  `pages_with_ocr`; and `remisser/parse.py` now dispatches an answer's body
  reader on the file's magic bytes rather than trusting its stored `.pdf`
  name, routing the 4 answers that are actually Word documents through
  `lib.poi`. Two new corpus-gap ledgers, `avg/KNOWN-GAPS.md` and
  `remisser/KNOWN-GAPS.md`, record what the pipeline still refuses and why —
  8 remisser whose remitted document has no `lib.regeringen` identity rule
  and 6 with colliding organisation slugs, avg's two KKV parse failures
  (both since resolved).
- **sfs/stats** (2026-07-28) — `sfs/redaktionell.py` lands: a publisher's
  editorial note (`endast-tryckt`, `upphavd`) stops reading as statute text,
  retyped in place at NF projection time by `nf.retype_editorial` — the same
  overlay pattern as `graphics.py`'s omitted-content gaps. Found by the corpus
  statistics themselves, whose "shortest laws" toplist was entirely editorial
  notes. `stats compute` is now wired into `lagen all rebuild` (whole-corpus
  runs only, between `dump` and `generate`, since it reads what both just
  wrote); the new `Stage.always` field makes a no-`inputs` stage never fresh
  instead of fresh-by-default, which is what a stage whose real inputs are the
  whole corpus needs. Each `compute` run now also archives a dated copy
  (`layout.stats_snapshot`, `artifact/stats/archive/statistik-<date>.json`) so
  the series, not just the latest figure, survives. In `stats/compute.py`,
  every measure now defaults to gällande rätt (`_in_force` narrows `laws`
  once; `laws_all` is named explicitly by the four measures — 27, 28, 42, 43 —
  that need the whole history), and the per-measure `note` fields were
  removed at the user's request in favour of folding the same caveats into
  each measure's `lede`.
- **remisser** (2026-07-27) — re-keyed the whole vertical on the document a
  case remits, on top of same-day fixes to the listing walk and cross-refs.
  The listing walk was paging `/remisser/?p=N`, which regeringen.se answers
  with page one regardless of `N`, so incremental sync never saw past the
  newest 20 of 3,291 archived cases — it now uses the same AJAX filter
  endpoint forarbete's listings use (`REMISS_CATEGORY = 2099`), and an empty
  page whose `TotalCount` says cases remain now raises rather than reading as
  a completed sweep. `Remiss.basefile` is no longer the regeringen.se ärende-page
  URL slug; it is `"<typ>/<identifier>"` of the referred document (`sou/2026:14`,
  `pm/LI2026/01339`, `ds/2026:9`, `lr/2026/<title-slug>`), with `Remissvar`
  basefiled `"<typ>/<document id>/<org-slug>"` — keying the corpus on the document
  makes the join to forarbete the basefile itself, not a separate lookup.
  `_match_forarbete` resolves `pm` (departementspromemoria — the modern,
  unnumbered replacement for a Ds) and `lr` (lagrådsremiss) via new shared
  identity rules in `lib/regeringen.py` (`pm_identity`, moved out of
  `forarbete/download.py`; `lr_identity`) — a promemoria on its diarienummer
  (minus any sub-ärende `–N` suffix) or, failing that, its landing slug; a
  lagrådsremiss on `<year>/<title-slug>`. Both verticals now mint identical
  basefiles for the same document reached from different pages. `parse_arende`
  raises, rather than minting a stub identity, when an ärende remits a
  regeringen-published document of an unrecognised doctype. `Remiss.externt_dokument`
  flags a case whose "Dokument(et) som remitteras"/"Genvägar" island links no
  `/rattsliga-dokument/` page — the remitted document was authored by an
  agency, an external party or the EU — and such a case is examined once and
  never fetched again. Because the listing names a case by URL slug while the
  corpus keys it by referred document, a new **examined-ärende index**
  (`layout.REMISSER_SEEN`) is now what drives the sweep in place of "is there
  a record on disk": `{slug: {"basefile": str|null, "until": iso-date|null}}`,
  `until` being the deadline plus `GRACE_PERIOD` (answers accumulate for the
  whole remissperiod, so having examined an ärende is not a reason to skip it —
  only its closing date is). `sync` was restructured around one shared
  `_poll` step used by both the listing walk and a catch-up pass over index
  entries the walk stopped short of; `_is_open` is gone, replaced by
  `_until`/`_needs_poll`. New `layout.resolve_basefile` respells a
  cross-source basefile case-insensitively (regeringen.se prints a
  diarienummer's department prefix inconsistently across its own pages),
  used by `remisser/ai_analyze.py` and `render.py`'s `_remiss_indexes`. Before
  any of this, only 5 documents in the whole corpus carried a förarbete
  cross-ref; verified live, of the current top 20 listing hits 13 are now
  stored (keyed sou/ds/pm) and 7 correctly skipped as external, and a second
  run re-polls the 12 still-open cases without re-fetching the closed one.
- **eurlex/lib** (2026-07-27) — four independent fixes landed together:
  `eurlex/annotate.py` gained `_annex_cut`, which trims trailing annexes from
  the ai-annotate prompt only (the artifact keeps them) — CLP (32008R1272)
  went from 1,132,799 to 40,913 prompt tokens, making it annotatable at all.
  `eurlex/parse_html.py` now recognises recitals in the pre-2000 "Avis
  juridique important" HTML (flat `<p>` paragraphs, no marker table, the
  sequence trusted only while a leading number keeps counting up) and skips
  `<p>` elements that merely wrap block-level content instead of emitting
  them twice (the legacy whole-document wrapper; a judgment `<p>` wrapping a
  `<table>`) — roughly 8,000 previously recital-less acts gain recitals on
  reparse, and the legacy acts' own outbound citation counts fall to their
  true values (the wrapper duplication had been double-counting them).
  `lib/render.py`'s inbound rail now splits the eurlex citer group by
  document kind (`INBOUND_KIND_GROUPS`/`inbound_group`) into
  "EU-domstolens praxis" and "Generaladvokatens förslag till avgörande",
  pulled out of the undifferentiated "EU-rätt" pile the VAT directive's 581
  judgments and 232 AG opinions used to sit in alongside its 138 citing acts.
  `lib/lagrum.py` gained a lettered-point level (`punkt_ref_id`), pinning
  "artikel 6.1 c" and the sub-article-less "artikel 3 a" alike — the corpus
  mostly cites articles that are bare point lists, so gating the letter on a
  preceding sub-article was tried and reverted (it also cost the whole citation
  on the named-act and treaty paths, not just the pinpoint) — and
  `with_indefinite_aliases`, which derives "EU:s dataskyddsförordning" from the
  registered definite "dataskyddsförordningen" so the genitive form resolves too.
- **build/perf** (2026-07-25) — `pdftext.py`'s page-numbering rewritten:
  `page_number_candidates` now splits what a margin line could be offering into
  `strong` (digits-only, may establish or move the running offset) and `weak`
  (a number at the edge of a line of prose, may only confirm an exact match) —
  what finds the folio glued to a footnote without a copyright page or a
  reprinted EU act's running header dragging the count. `printed_pages` numbers
  a document in **sections**: the body, then — when a confirmed backward
  restart happens and the running header names one (`bilaga_labels`, requiring
  the label to repeat on an adjoining page) — each **bilaga** as its own
  section, since where a document restarts at all every bilaga restarts at 1
  (prop. 2021/22:100 has four printed page 1s). A bilaga page anchors
  `bilaga{B}-sid{N}` instead of colliding with the body's `#sid{N}`; a restart
  nothing identifies still yields no anchors, as before. Measured over 180
  sampled PDFs: pages carrying a page number went from 51% to 99%, 0 lost.
  Alongside it: `pdftohtml`'s **and** `pdftotext`'s output are now cached
  brotli-compressed under `cache/pdfconv/` (`layout.PDFCONV`,
  `layout.pdf_conversion`). Profiling a förarbete parse that had slowed to ~1
  document/s across 32 workers showed the converter subprocess is 53-91% of a
  PDF document's parse time (9.95 s of sou/2023:35's 10.9 s), while the
  page-numbering rewrite above added 16 ms to a 714-page proposition. A
  downloaded PDF never changes, so the conversion was pure repeat work on every
  re-parse; the cache costs ~1% of the PDF bytes (~6 GB against 609 GB) and
  turns an 11.89 s conversion into a 0.01 s read. The slowdown itself was
  **not** a code regression: the förarbete download tree had grown to 597 GB /
  57 587 PDFs (4 096 of them over 50 MB — the KB scanned SOU bodies from
  `soukb-scans`), and 32 workers each holding a document's XML DOM exhausted
  31 GB of RAM and drove the machine into swap (110 MB/s swap-out observed at
  95% user CPU). Fewer workers, not more, is the answer there. `FA_CODE` had
  never listed `lib/pdftext.py` or the new `forarbete/volumes.py`, so an edit to
  either could ship without re-staling a single parsed document; both are now
  in the recipe (foreskrift/avg/remisser/coe/icc already listed pdftext).
  `test/test_pdftext.py`, `test/test_forarbete_parse.py`.
- **eurlex** (2026-07-26) — the judgment corpus reads whole, in both
  languages: `_parse_judgment_contents` now reads the pre-2012 ECR Formex
  shape (plain `NP` paragraphs; two thirds of the judgment corpus parsed to
  header + preamble alone without it) and `parse_hearing_report` reads
  `REPORT.HEARING` (for the oldest cases — Beentjes — the hearing report is
  the only text CELLAR holds, and its "Relevant legislation" section is
  where the act citations live). Citation scanning went per-language:
  `lagrum.LagrumParser(lang="eng")` loads an English EULAGSTIFTNING surface
  ("Article 29 (5) of Directive 71/305/EEC", "(EEC) No 2092/91",
  the-directive anaphora, Treaty articles refuse-to-link) for the
  pre-accession case law with no Swedish version, and EURATTSFALL reads the
  pre-1989 numbering ("Case 31/87", "mål 45/87"). On the statute side the
  rail join is pinpoint-precise: `catalog.caselaw_anchored` assigns each
  citation to the paragraf whose genomförande pinpoint covers it most deeply
  (ties: direct claim, then statute order; uncovered citations fall back to
  the article family's first live paragraf — claims on since-renumbered
  anchors cascade rather than swallow).
- **eurlex** (2026-07-24) — directive lineage: `eurlex/correspond.py`
  (run by `eurlex parse`) reads a recast's own
  jämförelsetabell annex into article↔article pairs, mechanical like
  `sfs table-correspond`, but stored under the act's own artifact `correspondence`
  key rather than an authored layer; `catalog._index_document` writes them into
  the new `catalog.directive_correspondence` table as it indexes each act
  (no `relate`-time layer load), and
  `catalog.caselaw_anchored`/`predecessor_atoms` (`(act, cited pinpoint,
  transposed atom, hops)`)
  walk it transitively (`LINEAGE_DEPTH = 3`) so a statute paragraf's
  EU-case-law rail also finds judgments about the predecessor articles its own
  genomförande statement never named — 238 of the 310 LOU paragrafs with a
  genomförande statement gained older case law they lacked (a floor: the
  corpus-wide re-parse the `_emit_table` fix needs has not run).
  `catalog.dangling_targets` + `lagen eurlex backfill [<sector-digit>]
  [--limit N]` fill the gap that made the lineage worth building: the
  sector-3 bulk import ships only acts *in force*, so a repealed act a
  judgment interprets is cited from everywhere and held nowhere (2004/18
  alone: 6 979 references from 790
  held documents); backfill downloads exactly those, ranked by inbound
  citation count off the corpus's own link graph. Parser hardening alongside:
  a multi-part Formex manifestation (an act published across several OJ
  files) is now fetched whole as one zip instead of an arbitrary part;
  `GENERAL`/`GR.SEQ` act roots (2004/18, the Charter) parse through the same
  walker as an ordinary `ACT`; a table row keeps its interior empty cells
  (only trailing ones drop), since in a jämförelsetabell the column a value
  sits in is what it means (§7d, `test/test_eurlex_correspond.py`).
  Alongside it, `forarbete/aigenomforande.py` mappings may now carry an
  optional Swedish-side pinpoint (`"sfs": "S1"`/`"S3N2"`) when the FK scopes a
  transposition claim to a stycke/punkt rather than the whole paragraf,
  existence-checked at relate time against the published law's minted element
  ids and rendered as citation prose ("första stycket genomför …") in the
  statute margin.
- **forarbete** (2026-07-23) — `ai-genomforande` opt-in LLM pass: authors the
  directive→paragraf transposition map for a prop's directive(s) out of its
  författningskommentar, one call per proposed law with a tagged
  multi-directive article catalog; paragraf identity taken from the
  already-stamped `kommentarer` entries (never asked of the model) and every
  asserted article — bare number or pinpoint — validated against the
  directive's real article inventory and its quote against the commentary
  text. `genomforande.resolve` now prefers that
  authored `.ann` layer over the mechanical `implements` per covered
  directive (§7d).
- **lib/dv/eurlex** (2026-07-23) — `lib/labels.py` landed: the four
  reader-facing name forms every document has (eyebrow/h1/official-title/
  citing-form), one dispatch table per source instead of scattered rules in
  `render.py`/`catalog.py`, folded into the `relate`/`generate` recipe-version
  tuples (§5). DV gained R2 coverage of pre-referat HD/HFD verdicts — parsed
  straight off the court's own PDF via `lib/pdftext`, margin-bitmap paragraph
  numbers recovered by counting, folded by `identity.py` into the later
  referat once one is published — plus `PROVNINGSTILLSTAND`/
  `FORHANDSAVGORANDE` exclusion and notis first-paragraph summary recovery
  (§4). eurlex now classifies Advocate General opinions and orders apart from
  judgments (`model.doctype`, `parse.parse_opinion`) and groups the Fördrag
  browse by treaty family instead of year, via a new curated
  `eurlex/data/treaties.json` (§7d).
- **acceptance** (2026-07-20) — first full-corpus acceptance run:
  `lagen all rebuild -j28` over all 15 sources (~295k documents) parsed →
  related → indexed → dumped → generated with **zero failing documents**,
  including the first fully clean förarbete sweep (97,073) after the
  printed-page mapping was rebuilt as a running piecewise offset
  (`lib/pdftext.py` `printed_pages`). A second run is a <30 s no-op (23.8 s);
  inventory counts reconcile exactly; DV/SFS goldens show no corpus-wide
  regression; 14/14 published-URL classes resolve. Two build-driver defects
  surfaced and were addressed: `build.py` now recycles pool workers every
  1,000 docs (`multiprocessing.Pool(maxtasksperchild=…)`) to contain a
  CPython 3.14 incremental-GC worker corruption that `ProcessPoolExecutor`'s
  equivalent deadlocked on, and dispatches longest-expected-first from
  manifest-recorded durations. `requires-python` was raised to >=3.14 (the
  tested/deployed runtime). Full record:
  `docs/rewrite-parity/06-corpus-acceptance-and-verification.md`.
- **forarbete** (2026-07-18) — downloaded + artifact trees year-segmented
  (`<typ>/<year>/<slug>`), ~287k files migrated; pm buckets under `_`; URLs
  unchanged. `lib/layout.py` gains `fa_year`/`fa_dir`/`fa_record_file`; the
  reader `fa_record`, `fa_facsimile_pdf`, `fa_ocr_pdf` and `relpath("forarbete",
  …)` all route through the year segment. A record and its body files stay
  co-located under the same `<typ>/<year>/` dir; per-type dotfile markers
  (`.watermark.json`, `.complete`) stay at the `<typ>/` level so the record
  glob (`*/*/*.json`) never reaches them. The big types (prop ~62k, bet ~42k,
  rskr ~40k) held tens of thousands of files flat before this, the same
  problem SFS's `<year>/<nr>` layout already solved.
- **forarbete** (2026-07-18) — `forarbete/soukb.py`, a **body re-downloader**
  for the KB-digitised SOUs (1922–1999), sibling to `propkb.py` but adding real
  documents rather than a facsimile: there is no ABBYY XML sibling for these
  scans, the OCR'd PDF *is* the body. It walks `https://sou.kb.se/` as the sole
  source of truth (the old `regina.kb.se` start URL is dead, so legacy soukb
  records are forgotten entirely) and writes a fresh harvested record per
  basefile, `files` pointing at the fetched PDF(s); basefile comes from the
  index label via a broadened port of the legacy SOUKB regex. 5,814 distinct
  basefiles, 128 of them multi-volume (one label repeats across several URNs,
  e.g. `1987:3` = 28 volumes of the Långtidsutredning), so `files` is a list in
  index order, one record per basefile. Own verb, `lagen forarbete
  soukb-scans`, resumable per part. Built, verified end-to-end on one small doc
  (1922:1, 10.5 MB) into a scratch tree — not run at corpus scale; the full
  pass is hundreds of GB.
- **forarbete** (2026-07-17) — the §7g frozen→harvested migration's prop slice
  lands: all 28,288 `downloaded/forarbete/prop/*.json` records now carry
  `files` (relative to `downloaded/prop/`) instead of `source`/`legacy_files`,
  zero frozen prop records left. This matters in production, where
  `legacy_files`' `LEGACY_ROOT` mount doesn't exist, so every frozen prop
  re-parsed and failed on every run. Body-format census: abbyy 17,295,
  none/pdf 7,052, skanning2007 2,334, text/tml 1,051, trips 118, word 438;
  2,236 metadata-only. `parse.parse_record` stays additive
  (`legacy_files` present → `_legacy_body`, still serving sou/dir/ds; else the
  new `_harvested_body`) — full removal of `forarbete/legacy.py` waits on
  migrating those three the same way. Two library moves fell out of giving
  förarbete its own Word body: **POI moved `dv/word.py` → `lib/poi.py`**
  (förarbete's second caller made `dv/`-housing a sibling-vertical import —
  rule:second-use-goes-to-lib; `dv/legacy.py` now does
  `from ..lib import poi as word`), and `legacy_formats.word_paras` adds a
  `.doc`/`.docx` body route where **`.doc` goes through `antiword`, not
  POI** — the proptrips-era `.doc` bodies are mostly Word 6/95 binaries POI's
  HWPF refuses (`OldWordFileFormatException`); POI (`lib/poi.py`) handles only
  `.docx`. `antiword` is a new system dependency at parse time, added to
  `docker/accommodanda/Dockerfile` alongside `poppler-utils`. Also new:
  `forarbete/propkb.py`, a facsimile-only fetcher for the KB two-chamber
  proposition scans (1867–1970) — it adds no documents (the ABBYY OCR text
  layer is already complete for all 19,066 propkb records), only a page-image
  "proof" view for the 17,295 fetched XML-only, the scan-PDF url derived
  mechanically from each record's stored ABBYY xml `orig_url` with no index
  crawl. The scan lands at the `layout.fa_facsimile_pdf` rule and is resolved
  from disk by existence (like `_sfs_pdf`); **no record is written**, both
  because `_harvested_body` prefers a PDF over an xml (naming it in `files`
  would flip 17,295 bodies off KB's ABBYY OCR onto a `pdftotext` of the scan)
  and because the record is a content-hashed parse input, so any key written
  into it would re-stale 17,295 parses for bytes parse never reads. Its own
  verb, `lagen forarbete propkb-scans`. Built, not run: the ~79 GB pass has not
  been executed corpus-wide, only prop 1867:1 and 1937:141 as end-to-end checks.
- **foreskrift** (2026-07-16) — the §7g frozen-import machinery for foreskrift
  was removed now that its 909 records (SKVFS/SOSFS/HSLF-FS) are ordinary
  harvested artifacts: body PDFs copied into `FORESKRIFT_DOWNLOADED/<fs>/`,
  records rewritten from `{"legacy": relpath}` to `{"name": ...}` pointers,
  migration proven byte-identical for all 909. `foreskrift/legacy.py`, the
  `foreskrift import-legacy` verb and `LEGACY_CORPORA` are deleted;
  `parse.py:body_path` no longer branches on `LEGACY_ROOT`.
  `test/test_foreskrift_legacy.py` is gone — its one still-valid assertion
  (closed-series agencies rsfs/sosfs/hslffs register with no live harvester)
  moved into `test/test_foreskrift.py`. `lib/legacy_import.py` is unaffected
  and still backs `forarbete/legacy.py` and `avg/legacy.py`.
- **local LLM** (2026-07-15) — `docs/local-llm.md`, an operator runbook for
  running Qwen3.6-35B-A3B (35B MoE, 3B active, vision, reasoning) on llama.cpp
  against one 24 GB GPU, as an unmetered/private alternative to Berget for the
  opt-in `ai-*` passes. Its hybrid attention (10 of 40 layers full, 30 linear
  Gated DeltaNet) makes the full native 262k context cost only ~5.2 GB of KV, so
  the model plus a whole EU act plus ~120 rasterized pages fit in 21.5 GB.
  Validated end-to-end on the real corpus: the GDPR article↔recital mapping over
  all 173 recitals + 99 articles (~97k prompt tokens) came back accurate, and a
  98-act batch ran 98/98 clean. `lib/llm.py` grew the endpoint and sampling that
  needs: **`llm_base_url`** (env `LLM_BASE_URL`) aims the passes at any
  OpenAI-compatible server, and **`llm_temperature`/`llm_top_p`** make the
  sampling configurable — the hardcoded `temperature=0` suits gpt-oss but makes
  Qwen3.6's thinking mode loop (it wants 1.0/0.95). `auth_headers` demands
  `BERGET_API_KEY` only for a remote host, since a llama.cpp server takes no key
  and requiring one there was the thing that made localhost unreachable.
  Defaults are unchanged, so the Berget path stays byte-identical. Two upstream
  llama.cpp bugs bound what is possible today: `--parallel > 1` and
  `--spec-type draft-mtp` both crash the hybrid arch, capping the box at one
  request at a time (~911 tok/s) and leaving a measured ~1.5x (MTP, 127 vs
  87 t/s) on the table until fixed. Corpus sizing measured while there: EUR-Lex is
  ~21,600 acts / ~192M prompt tokens, median act ~7.2k tokens — GDPR at ~97k is a
  p99.9 outlier, not a typical unit of work.

- **foreskrift** (2026-07-15) — the agency registry grew from ~21 to the full
  lagrummet.se government-agency list: `foreskrift/agencies.py` now registers
  71 författningssamlingar (66 live through the shared harvest engine, 5
  frozen-only stubs — rsfs, sosfs/hslffs, sjvfs and svkfs),
  county `\d+FS` series still excluded. Predecessor series (fifs, difs, rnfs,
  trmfs, nutfs, mprtfs, mrtvfs, sisuvfs, amsfs, rffs, lfs, jvsfs, vvfs, trvtfs)
  route via `fs_from_designation`/`DocRef.fs` at harvest time with no registry
  entry of their own, per the MCFFS precedent. `harvest.py`'s `_ref` was
  promoted to public `ref` for the bespoke per-agency enumerators to reuse;
  `Agency` gained an `http2: bool` flag so KKVFS (behind a Cloudflare front
  that 403s HTTP/1.1) rides `lib/net.make_http2_session` (new, `httpx2[http2]`
  extra) instead of the default `requests` session. SKVFS and MTFS alone set
  `Agency.browser` and ride `lib.browser.DetachedChrome`; `RE_KONSOLIDERAD` widened
  to match "konsol" (Swedac abbreviates to `-konsol.pdf`). Two library fixes
  fell out of running the full corpus: `lib/net.request` rides out failures
  for both the `requests` and `httpx` transports, and `lib/util.write_atomic`
  uses a per-process temp name (a fixed name raced two concurrent `lagen`
  invocations pruning the runlog, one crashing the other with
  `FileNotFoundError`). A full harvest at the new scale is under way (~6,750
  base regulations across the ordinary live fs; skolfs, tsfs, fkfs, rams, rfs, dvfs
  the largest), followed by a full `lagen foreskrift rebuild`.
- **sfs** (2026-07-14) — 🚧 convention appendices are parsed by one
  `sfs/parallelappendix.py` with **no per-law knowledge**: article sequences
  locate the per-language blocks, `langdetect` labels each complete block, and
  structural rules read treaties/protocols, divisions, articles and paragraphs.
  `sfs/__init__.py::_assemble` dispatches to it structurally (never by SFS
  number); a statute that isn't a parallel corpus, or one that looks parallel
  but doesn't line up (`AppendixMisaligned`), flat-parses instead. Sequential
  glued headings, multilingual divisions, omitted division headings and SFS
  `/…/` directives are handled generically, bringing coverage from 84 to
  **95/107 structurally detected candidates (89%)**. The five remaining
  parallel fallbacks are three duplicated source article sequences and two
  multi-treaty COTIF bundles. Instruments keep their title/preamble as ingress
  and a protocol number; the projection anchors them `#B1`/`#B1P4` and resolves
  the treaty each reproduces through the curated `sfs/data/incorporates.json`
  (`{sfs}#{fragment}` → `source/number`), adding no per-law code to the parser.
  Current scope and the reproducible tally are in
  `accommodanda/sfs/parallelappendix.md`.
  An earlier per-convention-spec spike was discarded in favour of this
  structural approach.
- **icc** (2026-07-14) — a fifth folkrätt vertical, §7j: `icc/` harvests
  International Criminal Court case law — the curated ~269-decision
  substantive set (Rome-Statute verdicts, sentences, confirmation, arrest
  warrants, appeal judgments, reparations, investigation/admissibility/
  prosecutor-review decisions), not the ~10k procedural mass. Two
  Cloudflare-free sources (the ICC's own `/court-record` detail pages are
  Cloudflare-walled): icc-cpi.int `/decisions`, facetable by
  `decision_type_of_decision` — the curated facet ids
  (`icc/data/decision_types.json`) scope the harvest and yield each
  record's document number — and the ICC Legal Tools API
  (legal-tools.org) resolves a document number to metadata and PDF via a
  case-sensitive `externalId` prefix match, picking the English primary
  over translation variants. 268/269 decisions resolved with text; the
  unresolved one stays metadata-only. `Decision`/`Block` (HUDOC-shaped)
  project numbered paragraphs to the citation-unit article tree; `parse.py`
  extracts the PDF via `lib/pdftext`, strips the per-page court-record
  running header, and classifies numbered paragraphs vs. section headings.
  `Decision` artifacts land at `/ext/icc/{doc-number}`; the URI grammar
  stays local to the vertical (rule:second-use-goes-to-lib). Swedish
  relevance: Sweden is a Rome-Statute party (lag 2014:406) and Swedish
  courts apply international criminal law in universal-jurisdiction cases;
  the Inter-American and African human-rights courts were deliberately
  *not* added alongside it — not binding on or applied in Sweden,
  comparative only. The folkrätt landing lists ICC decisions grouped by
  Rome-Statute decision type under "Internationella brottmålsdomstolen
  (ICC)", the same landing-only pattern as coe/icrc/untc, no faceted
  browse tree of its own; the folkrätt landing now aggregates five sources
  (coe, icrc, untc, icc, hudoc). Wired through `build.py`, `layout`,
  `catalog`, `facets`, `datasets` and `render`. `test/test_icc.py` (11
  tests) runs off a stored-record fixture
  (`test/files/icc/ICC-01_04-02_06-2359.json`) plus pure unit tests of
  the PDF-paragraph classifier, no network, no PDF binary. A real
  download+parse+relate+generate harvest has run: all 269 curated
  decisions are live on `/folkratt/` and `/icc/{doc-number}`.
- **untc** (2026-07-14) — a fourth folkrätt vertical, §7j: `untc/` harvests
  the UN Treaty Collection's MTDSG status register — a curated list of 14
  instruments (`untc/data/treaties.json`, one harvest engine over all —
  rule:configured-by-data): VCLT, UNCLOS, the Genocide Convention, the core
  human-rights instruments (ICERD, ICESCR, ICCPR, CEDAW, CAT, CRC, CMW, CRPD,
  CED) and the Refugee Convention plus its Protocol. Each is one static-HTML
  fetch from `ViewDetailsIII.aspx`; the MTDSG carries status only — dates,
  UNTS registration, per-state participation — not treaty text, which lives
  in per-treaty UNTS PDFs outside this uniform scrape, so `structure` is
  deliberately empty and the rendered page links out to the UN authentic
  text (bespoke per-treaty PDF ingest is a deliberate follow-up, not v1).
  `parse.py` scrapes the page's stable ASP.NET control ids and the
  participation grid, anchored on the grid's own control id (`tblgrid`)
  since some treaties precede it with a decoy territorial-notification table
  under the same "Participant" header. `Treaty` artifacts land at
  `/ext/untc/{mtdsg_no}`; the URI grammar stays local to the vertical
  (rule:second-use-goes-to-lib). The folkrätt landing's UN half groups the
  curated instruments by subject (Traktaträtt och havsrätt / Mänskliga
  rättigheter / Flyktingrätt), the same landing-only pattern as coe/icrc, no
  faceted browse tree of its own; the folkrätt landing now aggregates four
  sources (coe, icrc, untc, hudoc). Wired through `build.py`, `layout`,
  `catalog`, `facets`, `datasets` and `render`. `test/test_untc.py` (10 tests)
  runs off a synthetic trimmed MTDSG fixture, `test/files/untc/XXIII-1.html`,
  no network. A real download+parse+relate+generate harvest has run: all 14
  treaties are live on `/folkratt/` and `/untc/{mtdsg_no}`.
- **icrc** (2026-07-14) — a third folkrätt vertical, §7j: `icrc/` harvests the
  ICRC's anonymous Drupal 10 JSON:API (`ihl-databases.icrc.org`) — one
  paginated list call enumerates the 111 IHL treaties (the four 1949 Geneva
  Conventions, their Additional Protocols, the Hague law, the
  weapons/cultural-property regimes), one per-treaty `include=`-expanded
  fetch returns the whole self-contained envelope (metadata, authentic
  article text, per-state participation), so `parse.py` is pure and offline
  with no PDF step, unlike coe. `Treaty` artifacts land at
  `/ext/icrc/{number}`; the URI grammar stays local to the vertical
  (rule:second-use-goes-to-lib — nothing in `lib` mints an ICRC target yet).
  `icrc/data/names.json` curates the four Geneva Conventions and three
  Additional Protocols with informal Swedish names/acronyms, surfaced first
  on the folkrätt landing ("Genèvekonventionerna och tilläggsprotokollen" vs.
  "Övriga instrument" A–Z) — the same landing-only pattern as coe, no
  faceted browse tree of its own. Wired through `build.py`, `layout`,
  `catalog`, `facets`, `datasets` and `render` (masthead Folkrätt nav gains
  "Internationell humanitär rätt"). First cut of treaty ingest only; ICRC/IHL
  caselaw (ICC, Inter-American, …) is future work. `test/test_icrc.py` (10
  tests) runs off a trimmed real Geneva Convention I envelope fixture,
  `test/files/icrc/365.json`, no network.
- **sfs** (2026-07-13) — the text-only-source loss is now explicit in the
  artifact *and* recovered end to end. `sfs/graphics.py` detects SFST
  omission markers and 2007:90's unmarked road-sign cells, and `nf.py`
  projects them as typed `grafik` nodes carrying the governing SFS
  publication; `sfs/pdfmirror.py` (`lagen sfs mirror-pdf`) stages the
  official published PDFs from 1998 onward under `downloaded/sfs/pdf/` as
  the crop source. The same `graphics.py` module now also resolves each
  gap's provenance deterministically (register-first for bilaga gaps) and
  drives an opt-in vision pass (`lagen sfs ai-includegraphics`,
  `VISION_MODEL` in `config.py`, vision support added to `lib/llm.py`) that
  locates page + bbox in the provenance-correct PDF and writes a `.graphics`
  layer (`lib/annstore.py`, per-entry `verified` surviving reruns).
  `lib/facsimile.py` crops the bbox; `GET /api/v1/sfs-graphic` serves it; the
  renderer's `grafik` node shows the crop when localized, an honest
  placeholder otherwise. `tools/golden_sfs.py` gained the
  `grafik-node-replaces-marker` adjudication family.
- **sfs/dv golden** (2026-07-12) — the rewrite's initial correctness baseline
  and triage pass closed. SFS structure and amendment comparisons now apply
  conservative post-freeze add/change/repeal adjudication and leave the
  special-law/bilaga tail visible. DV date comparison lets a sane, formal
  publishing-court date in the body override conflicting API metadata, retains
  multiple final dates when the text states them, and leaves 15 body-unresolved
  conflicts unadjudicated. Normative DV fixtures and representative structural
  corpus sampling found and locked the credible parser defects; the old
  structural corpus remains a sampling surface rather than an automatic oracle.
- **hudoc, coe** (2026-07-10) — two new verticals, §7j: `hudoc/` harvests
  HUDOC's public JSON result endpoint plus the per-case Word→HTML conversion
  into `HudocCase` artifacts (`/dom/echr/{itemid}`); `coe/` harvests the
  Treaty Office's complete-list table, treaty detail metadata and official
  text into `Treaty` artifacts (`/ext/coe/{number}#A…`). `lib/coe.py` maps
  HUDOC's article-facet codes (`8`, `6-3-d`, `P1-1`) onto the matching
  Treaty Office provision URI, so an ordinary `relate` puts "Europadomstolens
  praxis" inbound on the cited article; ECHR instruments reproduced in SFS
  1994:1219 also carry an `rdfs:seeAlso` bridge to that SFS. Wired through
  `build.py`, `layout`, catalog, facets (new "Dokumenttyp"/"Typ" browse
  schemes, including a `legal-summary` "Rättsfallssammanfattningar" bucket),
  search/dump, render and `api/mcp`. Follow-up hardening the same day: remote
  input validation in both downloaders raises `ValueError` instead of
  `assert`; the duplicated `_norm` helpers were deduped into a None-safe
  `lib.util.normalize_space`; facets' `_eu_kind`/`_hudoc_kind` merged into one
  `_catalog_kind` (shared by eurlex, coe and hudoc); a synthetic
  `test/files/coe/009.pdf` fixture covers the coe PDF-body parse path.
  Later the same day, `coe/download.py` was rewritten: the Cloudflare-fronted
  portal it originally scraped (`parse_listing`/`parse_detail`) is gone,
  replaced by one search POST to the Treaty Office's anonymous JSON web
  service (`conventions-ws.coe.int/WS_LFRConventions`, token embedded in the
  public `full-list2` page — needs `lib.net.mount_legacy_tls`, new, for its
  small-DH-key TLS) that returns all 233 treaties with metadata in one call,
  plus `getLieux` for opening places; official texts still download as plain
  PDFs from `rm.coe.int`, no challenge. Records no longer carry a `summary`
  field (it sits behind the scraped portal). Since every official text is
  now known to be a PDF, `coe/parse.py` dropped its HTML body path entirely
  (`html_paragraphs` removed); fixtures moved to
  `test/files/coe/ws-search.json` + `ws-lieux.json` (listing.html/detail.html
  deleted). `hudoc/download.py` also gained a small `ThreadPoolExecutor`
  (`WORKERS=4`) keeping body fetches in flight ahead of the walk (~0.15s/doc
  vs ~0.33s sequential — a full English harvest drops from ~9h to ~2-4h) and
  raised `PAGE_SIZE` 100→500.
- **lib** (2026-07-10) — the static site's chrome (CSS/JS/robots.txt, formerly
  embedded string constants in `render.py`) extracted to real files under the
  new `lib/assets/`; `render_aggregates` reads them via the module-level
  `ASSETS` path and writes them through the same Brotli precompression as
  pages (`style.css` with `editor.css` appended). The asset files are part of
  `build.py`'s `GENERATE_CODE` watermark, so an asset edit re-stales
  `generate`; `MANIFEST.in` ships them as package data.
- **lib/api** (2026-07-10) — search facets + a full `/sok` results page: a
  `year` facet (`facets.document_year`, reusing browse's per-source year
  extraction) alongside `source`/`kind`, returned as bucketed counts
  (`SearchResponse.facets`) via `post_filter` aggregations (each facet's own
  aggregation still counts against the *other* selected filters); every
  query also runs a prefix-matching branch (`search.prefix_query`) OR'd
  against the exact one; an `INDEX_FORMAT` version folded into each indexed
  unit's stored freshness key lets an index-schema change (like this one)
  reindex on the next ordinary incremental pass, no `--force` needed.
  `render.render_search_page` renders the facet-sidebar results page,
  `fullsearch.js` drives it client-side. `test/test_search.py`, `test/test_api.py`.
- **lib/api** (2026-07-10) — restored legacy per-repository feed surface:
  `lib/feeds.py` maps the old Ferenda `/dataset/{sfs,dv,forarbeten,myndfs,
  myndprax,keyword,eurlex}/feed[.atom]` URLs (+ `rdf_type`/
  `rpubl_rattsfallspublikation`/`dcterms_publisher` query facets) onto the
  rebuilt source names and renders both Atom and an HTML twin; `render.py`
  writes every dataset's feed statically during `generate`, and two new
  `api/app.py` endpoints answer the same query-parameter URLs live off the
  catalog. `/dataset/sitenews` remains the all-feeds directory.
- **api** (2026-07-10) — MCP/serve operational hardening: `api/mcp.py` gained
  a `_LoggedMCP` ASGI wrapper logging one line per JSON-RPC request (client
  IP, method, tool name + truncated arguments — the only tool-level
  visibility, since the access log only sees `POST /mcp/ 200`) and explicitly
  disables the MCP SDK's DNS-rebinding protection (its localhost-only default
  would 421 all production traffic behind the nginx vhost). `api/app.py`'s
  `serve()` now calls `logging.basicConfig(INFO)` so app-level log lines
  (including the new MCP request log) reach stdout alongside uvicorn's access
  log.
- **lib** (2026-07-09) — `lib/annstore.py`: every `ai-*` action's output
  (eurlex/kommentar `.ann`, remisser `.ann`, sfs `.corr`) now lives in a
  dedicated curated store in the git-backed content repo
  (`WIKI_ROOT/ann/<source-dir>/<relpath>`, mirroring the artifact tree's
  relpath grammar) instead of next to the artifact — an LLM output that has
  been hand-verified/edited is curated data, as irreplaceable as hand-written
  wiki markdown, and the artifact tree's contract is "wipeable, rebuildable,
  never hand-touched". Each layer is an envelope (`meta`: status
  generated/verified, model, generated date, per-input sha256 hashes) beside
  the payload's own keys; `status: verified` (flipped by hand) makes
  regeneration refuse without `--force`, checked before the LLM spend;
  staleness is derived from the recorded input hashes, never stored, and a
  stale *verified* layer is flagged for human re-review, never mechanically
  regenerated. New CLI verb `lagen ann status` inventories the store.
  `eurlex/annotate.py`, `wiki/annotate.py`, `remisser/ai_analyze.py`,
  `sfs/correspond.py`, `lib/render.py` and `build.py` (relate's `.corr` load,
  `generate_fingerprint`, `page_signature`) all read/write through the store
  now. `test/test_annstore.py`. **Migration** (any host with pre-cutover
  layers — readers treat a missing layer as "unannotated", so un-moved files
  silently vanish from pages): move them by mirrored relpath, e.g.
  `cd $DATA/artifact && find . \( -name '*.ann' -o -name '*.corr' \) -exec
  install -D {} $WIKI_ROOT/ann/{} \; -delete`, then commit; a meta-less file
  counts as `verified` (unknown provenance is never silently regenerable).
- **api** (2026-07-09) — public **MCP server**: `api/mcp.py` mounts a
  no-auth Streamable HTTP MCP endpoint at `/mcp` on the same `lagen all
  serve` FastAPI app, exposing seven read-only tools (`search`,
  `resolve_citation`, `get_document`, `list_documents`,
  `get_incoming_citations`, `get_outgoing_citations`, `list_sources`) as
  thin wrappers over the same `lib` functions the REST endpoints use.
  `lib/pins.py` extracts the citation-shaped-query resolver (name+pinpoint
  → exact fragment target) shared by REST `/search` and the MCP
  `search`/`resolve_citation` tools. New dep `mcp>=1.13`. `test/test_mcp.py`,
  incl. an end-to-end Streamable HTTP round-trip.
- **sfs/forarbete** (2026-07-09) — `history-as-git`: `sfs/asgit.py` implements
  `docs/prd-sfs-history-as-git.md`, exporting the SFS corpus as a git
  repository (one file per statute, one commit per amendment event grouped by
  proposition, authored/committed by the prop's/rskr's signers, ingress as
  commit body, one `git fast-import` stream, idempotent via per-file
  `Lagen-Transition:` hash trailers with `--rebuild-history` for
  corrections/backfills/attribution/scope changes). Two förarbete
  prerequisites landed to feed it: a fifth harvest
  source, `forarbete/rskr.py` (riksdagsskrivelser off data.riksdagen.se,
  driving `riksdagen.py`'s `_walk`/`sync` now generalized into a
  doctype-agnostic `harvest()`, `bet` as its default driver), and
  `parse.tag_frontmatter` (prop/skr front-matter retagging — the "huvudsakliga
  innehåll" heading promoted to a rubrik, signer names tagged as a new
  `signatur` block kind, read back by `structure.signers`/`structure.ingress`).
  `test/test_sfs_asgit.py`, additions to `test/test_forarbete_parse.py`.
- **api/render** (2026-07-09) — on-demand page facsimiles: `lib/facsimile.py`
  rasterizes one page of a source PDF to a retina PNG (`pdftoppm`, 150 DPI)
  on first request and caches it under `cache/facsimile/` (a pure cache — this
  codebase only writes, an external process evicts); works identically for
  born-digital and scanned PDFs since pdftoppm just rasterizes what is drawn.
  `api/app.py` serves it at the documented `/api/v1/facsimile?uri=&sid=`
  endpoint plus the legacy lagen.nu path grammar
  (`/prop/2022/23:10/sid1.png`), with one resolver per page-oriented PDF
  source (förarbete, föreskrift, avgörande). `render.py` turns every förarbete
  page anchor into a toggle button (`FAKSIMIL` inline JS, now
  `lib/assets/faksimil.js`) that loads the PNG under the anchor on click.
  `test/test_facsimile.py`.
- **lib** (2026-07-09) — `lib/compress.py`'s transparent Brotli compression now
  also covers the raw `downloaded/` tree, not just `artifact/`/`generated/`:
  `write_download` picks plain-vs-Brotli per file (`INCOMPRESSIBLE_SUFFIXES`
  skips already-compressed payloads — PDF/zip/docx/images/…, and sub-512-byte
  files stay plain regardless of extension) and `download_encodings`/`glob`/
  `list_basefiles` (the latter moved here from `lib/util.py`) give downloaders
  and parsers a compress-aware way to enumerate and read that tree. Every
  vertical downloader (sfs, dv, eurlex incl. bulk, forarbete incl. riksdagen +
  legacy importers, foreskrift incl. legacy, avg incl. legacy, remisser) now
  writes payloads/records through `write_download`, and all parse-/build-side
  readers of `downloaded/` go through the new readers/globs. Harvest
  watermark/pending dotfiles are deliberately left plain. `test/test_compress.py`
  covers the new download-side surface.
- **§7d** (2026-07-08) — EU case naming: `lib/eucasenaming.py` (the EU mirror
  of `lib/casenaming.py`) derives a CJEU case's court case number from its
  CELEX and pairs it with a curated usual name harvested from Wikidata
  (`eurlex/casenames.py`, property P476, shipped as `eurlex/data/casenames.json`
  / `NAMEDEUCASES`, ~245 named cases). A judgment's page heading is now its
  usual name / case number (the old Formex "Domstolens dom (…) den …" title
  moves to a "Titel" metadata row), and an inbound citation now reads
  "C-311/18 (Schrems II)" — feeding a new "EU-rätt" group in the inbound panel
  (`page.INBOUND_GROUPS`). New CLI action `lagen eurlex casenames` refreshes
  the snapshot.
- **§5/§6/api** (2026-07-06) — review-fix pass across the corpus: `lib/llm.py`
  gained the shared `author` validate/self-repair-retry loop (factored out of
  the near-identical retry code in eurlex/wiki annotate + remisser
  ai-analyze); `lib/pdftext.py` gained a `hidden=True` mode (recovers an
  OCR text layer `pdftohtml` otherwise drops) and `flat_lines` (page-break-
  flattened line stream), with `eurlex/parse_pdf.py` cut over to consume it
  instead of its own extraction; `lib/compress.py` now writes through
  `util.write_atomic`. `generate_fingerprint()` widened its coarse gate: the
  remiss answers + their `ai-analyze` `.ann` layer (rendered onto the
  referred förarbete's page, never `relate`d, so invisible to the catalog
  signature) now fold in alongside the existing `.corr`/`.versions.json`/
  eurlex-`.ann`/kommentar-`.ann` layers, and the currently-expired-statute
  URI set is folded in too, so an upphävd date passing reopens the gate on
  its own (no file change needed). `api/auth.py` gained in-process login
  rate limiting (per-(IP, username) sliding window + exponential backoff,
  plus a concurrency cap on pbkdf2 work) so a login flood can't pin CPU
  behind the password check. Two ported-from-`lagen/` data files landed:
  `lib/data/begrepp_aliases.json` (concept-normalization overrides) and
  `sfs/data/resources.json` (org/series label → URI lookups feeding
  `sfs/register.py`).
- **§5/§4/§7a/§7e** (2026-07-06) — shared harvest core extracted to
  `lib/harvest.py` (`HarvestWatermark` begin/complete lifecycle + `walk`/
  `Skip`/`ItemKey`/`guarded_enumerate`), closing the §5 "not yet extracted"
  gap; dv, `foreskrift/harvest.py` and avg (jo) now run on `walk`,
  forarbete/riksdagen adopt the begin/complete lifecycle directly. Alongside
  the extraction, a round of incremental-download correctness fixes:
  `forarbete/download.py`'s `iter_listing` was fixed to key listing-exhaustion
  on the raw per-page item count (not the type-filtered one), which had been
  permanently truncating
  pm/ds harvests past a page dominated by the sibling type; `eurlex/download.py`
  now walks caselaw's CELEX-year enumeration from `first_year` regardless of
  the date floor (a judgment's CELEX year is its case year, not its decision
  year), corrected its recency floor to `run - window` (reaching below the max
  seen work date, not pinned to it), keeps wdate-less works past the SPARQL
  filter, and gained a per-sector pending-retry sidecar for no-content works;
  `remisser/download.py` now writes a stub record for any per-case fetch/parse
  failure (previously HTTP errors only); avg's `jo_sync --full` re-resolves
  on-disk docs (via `walk`) and `jk_sync --full` no longer pre-deletes the
  stored landing before refetching; foreskrift's non-PDF response bodies are
  now logged and counted rejections rather than silently dropped.
- **§6** (2026-07-05) — inline content editor: the write side of the service.
  A new `editors` config registry + `editor_secret` back a signed-cookie login
  (`api/auth.py`); `api/edit.py` exposes `/api/v1/{auth,edit}/*` (all gated,
  same-origin only). `api/editcontent.py` locates and rewrites one markdown
  region (a kommentar `## §`-section, or a concept/editorial body) in `WIKI_ROOT`
  in place, byte-preserving everything around it; `api/editcart.py` holds each
  user's pending hunks and, on checkout, makes one git commit authored as that
  user + conflict-checks against `base_sha`. `build.rebuild_after_commit` does the
  synchronous scoped parse→relate→generate (wired into `edit.py` by injection to
  avoid an import cycle). Client: `lib/assets/editor.js` grafts ✎ buttons +
  a cart/checkout UI (with an `sfs:`/`eurlex:`/`begrepp:` link picker) onto the
  otherwise-static pages after `/auth/me`, keyed off a `<meta name="lagen-doc">`.
  Added `markdown.split_frontmatter`/`iter_headings` and `wiki.fragment_heading`
  (inverse of `heading_fragment`). `test/test_edit{content,cart,_api}.py`.
- **§7i** (2026-07-04) — site vertical landed: lagen.nu's editorial chrome
  (curated frontpage, `/om/*` about pages, sitenews feed) moved from
  hand-maintained legacy templates to markdown in `lagen-wiki/site/`,
  migrated once by `tools/migrate_site_content.py`. Small block-tree model
  (`Heading`/`Paragraph`/`Bullets`/`Code`), `parse.py` reusing
  `lib.markdown`'s grammar (plus new `sfs:`/`eurlex:` link schemes), `render.py`
  writing static HTML + Atom (`write_site`). Registered in `build.py` with a
  `parse` Stage but no `relate`/`index`/`dump`, like remisser; wired into
  `generate` (full run, `--aggregates-only`, and `lagen site generate`) where
  the curated frontpage overwrites the generic corpus-stats `index.html`.
- **§6** (2026-07-04) — operations/health dashboard: `lib/runlog.py` owns the
  three `DATA/.build/` state files (run ledger, per-doc error store, rolling
  status snapshot), `build.py` instruments every invocation and extends
  `status` + adds `lagen all runs`, and `api/ops.py` serves `/ops` (originally
  HTTP-Basic via an `ops_token` knob, later unified onto the inline editor's
  session) as a self-contained health matrix + run/failure drill-down,
  independent of the site render.
- **§7h** (2026-07-04) — remisser vertical landed: regeringen.se remiss/referral
  harvest (two-pass sync, stub records for unreachable case pages so an
  incremental watermark can't hide a failure), PDF parse over the shared
  `lib/pdftext` (now header-optional, `identifier=None`, for sources with no
  fixed masthead), and the sole LLM pass `ai-analyze` (sentiment + verbatim
  quote per förarbete section, `.ann` sidecar, retried via the new
  `lib.llm.complete_thread`). Never `relate`d — its `.ann` layer is picked up
  straight off the filesystem (`layout.artifacts`, new) and rendered as a
  "Remissvar" rail section on the referred förarbete's page. `lib/regeringen.py`
  extracted (TYPES + listing walk) once remisser became the second
  regeringen.se harvester alongside forarbete; `lib/util.py` gained
  `swedish_date`/`MONTHS`, shared by foreskrift and remisser.
- **2026-07-03, §7a** — three förarbete extensions: `pm` (promemorior outside
  the Ds series, keyed by diarienummer or landing-page slug) added to the
  regeringen.se downloader's shared category-1325 listing; `bet`
  (utskottsbetänkanden, the prop→enacted-law link) added as a fourth harvest
  source off data.riksdagen.se (`forarbete/riksdagen.py`), backfilling all 161
  riksmöten to work around the API's ~10k-doc pagination cap; `kommentar.py`'s
  genomför-direktiv extraction widened from prop-only to `{prop, fm}`
  (förordningsmotiv), with the alias-binding lookback rescoped from a fixed
  400-char window to the defining sentence — which also fixed a real prop
  misparse, not just an fm edge case.
- **§7g** — frozen legacy corpora imported, not ported: ~38,200 documents
  across three verticals (ARN → avg incl. a new live arn.se harvester,
  9 förarbete corpora 1867–2023 with format-probed body routing +
  ABBYY/Mso/TRIPS adapters + the live-wins/format-tier precedence rule,
  skvfs/sosfs → foreskrift as frozen baselines; SKVFS later gained a live overlay);
  `legacy_root` config,
  point-at-bytes records, re-OCR sidecar seam.
- **guardrails** — docs/conventions.md rule catalog (citable slugs) +
  mechanical enforcement: PreToolUse hooks (conventions reminders,
  legacy-tree/bare-suppression blocks, git-guard), layer-boundary AST
  checker in the Stop hook, hardened ruff (B/BLE/PLC0415/S110/S112 with
  cited suppressions at the sanctioned resilience points), review agents +
  /wrapup skill; bare `pytest` now collects exactly the new suites (which
  surfaced two latent failures: test_eurlex_annotate's stale
  AssertionError expectations, fixed, and test_resolve's
  dataskyddsförordningen alias drift, open).
- **§4/§6** — bare lagen.nu page URLs (`page_url`/`SiteFiles` try_files); DV
  canonical case naming + HD given names; HD modern record format (h1 instances,
  footnotes) + instance/ruling rendering; repealed-statute treatment; statute
  browse hierarchy/filter; named-EU-act citations; build driver the single parse
  entry point.
- **§6/§7e** — incremental `relate`/`index`/`generate` (content-hash sync,
  per-source watermarks); föreskrift vertical (15 agencies harvested, shared PDF
  parser, the statute→föreskrift `bemyndigande` edge end-to-end).
- **§7c/§7d** — EU (EUR-Lex/CELLAR) and wiki (kommentar/begrepp) verticals;
  the concept layer (synthesis + canonicalization); genomför-direktiv edges
  pinned statute↔directive↔proposition.
- **§3d/§5** — adjudication overlay (`change-detector, not oracle`); all 8 legacy
  citation grammars ported to Lark; named-law dataset off RDF.
- **§4/§7a** — DV vertical (identity index, API + legacy-Word parse, reference +
  structural goldens); förarbete vertical (downloader + PDF parser + hierarchy).
- **§6** — derived layer: SQLite catalog + cross-source inbound graph, static
  site with context rail + ⌘K search, publishing (OpenSearch/REST/NDJSON dumps).
- **§2/§3** — Phase 0 golden corpus + comparator; SFS structural parser (98.7%);
  inline-link artifacts; SFSR register/amendments/förarbeten/metadata.
