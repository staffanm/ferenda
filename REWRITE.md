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
  lib/      shared horizontal libs (full map: accommodanda/README.md "Shared library (lib/)") — lagrum (citation engine), catalog, render, layout, net, markdown, util, errors, casenaming, eucasenaming, labels, eu_structure, datasets, search, facets, feeds, dump, pins, resolve, text, compress, facsimile, pdftext, llm, annstore, wikitext, runlog, patch·patchit, git, harvest, regeringen, poi, concepts, diff, history, assets, coe, coe_ids
  config.py runtime config (config.yml / data_root / catalog_root / wiki_root)
  sfs/      acts vertical — download·graphics·pdfmirror·extract·reader·model·tokenizer·assembler·nf·parallelappendix·register·versions·correspond·asgit·begrepp·_validate (+ __main__)
  dv/       court-decisions vertical — download·identity·namedcases·model·parse·structure·legacy
  forarbete/ preparatory-works vertical — download·riksdagen·rskr·model·parse·structure·kommentar·genomforande·fk·jamforelse·lydelse·tabell·legacy_formats
  eurlex/   EU vertical (EUR-Lex/CELLAR) — download·bulk·annotate·casenames·definitions·parse·parse_html·parse_pdf·structure·lang·model
  hudoc/    ECHR case-law vertical — download·model·parse
  coe/      Council of Europe Treaty Office vertical — download·model·parse
  icrc/     ICRC international humanitarian law treaty vertical — download·model·parse
  untc/     UN Treaty Collection (MTDSG status) vertical — download·model·parse
  icc/      International Criminal Court case-law vertical — download·model·parse
  foreskrift/ agency-regulations vertical — agencies·harvest·download·model·parse·structure
  avg/      JO/JK/ARN-decisions vertical — download·model·parse
  remisser/ remiss (referral-response) vertical — model·download·parse·ai_analyze
  site/     editorial-chrome vertical (frontpage/om/sitenews) — model·parse·render (markdown content repo, WIKI_ROOT)
  wiki/     kommentar + begrepp sources — parse·annotate·guidance_discover (markdown content repo, WIKI_ROOT)
  api/      HTTP API — app (REST/OpenAPI + static site + legacy feeds), mcp (MCP server), ops (health dashboard), auth·edit·editcontent·editcart (inline content editor), patch (source-fix editor)
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

**Sanctioned exception — `lib.render` drives the REST API in-process.** The
corpus-wide *browse* pages are generated by driving `api.app` through a FastAPI
`TestClient` over the catalog (`render.render_aggregates` → `generate_browse`),
rather than re-deriving the listings in the renderer. This is a deliberate
inversion (lib reaching "up" to the API layer): it *guarantees* the static browse
pages are byte-for-byte the same listing the REST endpoint serves, so the two can
never drift. The dependency is one-way and confined to aggregate-page generation;
per-document rendering never touches the API.

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
  Detection is deterministic: `sfs/graphics.py` turns both slash-delimited and
  plain `... är inte med här` placeholders into typed `grafik` nodes during the `nf.py` projection
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
  (2007:90's hundreds of signs) without losing prior sign-offs. The layer
  stores raw PDF points (top-left origin) and is hand-editable; generated,
  unverified candidates stay out of the public render. `lib/facsimile.py`
  crops the bbox (`render_region`/`cached_region`); `GET
  /api/v1/sfs-graphic?uri=&node=` serves the crop lazily, resolving the
  provenance-correct PDF from the `.graphics` layer; the renderer's `grafik`
  node emits a `<figure>`/`<img>` crop with source-SFS attribution when the
  layer has placed it, else an honest placeholder. `golden_sfs.py`'s
  `grafik-node-replaces-marker` adjudication family accepts the new grafik
  nodes as new-is-right against the old pipeline's dropped-graphics golden.
  `test/test_sfs_graphics.py`, `test/test_sfs_pdfmirror.py`.
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
- ✅ **Named-law data** — `sfs.ttl` → hand-editable `sfs_namedlaws.json` (187 labels /
  106 abbrevs; `load_namedlaws`/`load_abbreviations`/`register.abbreviations` read
  JSON, no rdflib). Complete for SFS's bare-citation class — all 12 balkar + the
  grundlagar are present (that is where "brottsbalken → 1962:700" comes from). Within
  SFS the *full* citation form is the convention (resolved by SFS number or in-document
  learning), so the colloquial long tail (`avtalslagen`, …) is DV/förarbete work, not
  §3. `riksdagsordningen` de-staled to the current `2014:801`.
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
    against body pinpoints naturally); `render_dv` shows the four groups.
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
    DOMSTOLSAVGORANDEN — "porting" these means greenfield grammar design,
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
  `site/data/.build/manifest.json`) keyed on input hash **+ recipe version**
  (a hash of the stage's own impl files, so editing the parser re-stales
  every doc without a blanket `--force`). **Implicit deps** (a downstream
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
- ✅ **Cross-source inbound-link graph** — the killer feature, working
  end-to-end. `catalog.inbound(con, uri)` = the distinct docs citing exactly
  that fragment uri. Verified on the partial corpus: **2,037 cases cite
  räntelagen § 6** (`1975:635#P6`); a case → law-paragraph → back-to-every-
  case-on-that-paragraph round-trip renders both directions.
- ✅ **Static HTML site** (`accommodanda/lib/render.py`, `generate`). A single
  generic node renderer (keyed on artifact `type`) handles both the SFS
  structure tree and the DV body; **outbound** links are live `<a>`s to the
  cited doc's exact paragraph. **Inbound** links at two granularities: a
  per-paragraph margin annotation (id-bearing nodes) *and* a per-document
  panel (`document_inbound`) for citations to the law/case as a whole — the
  **27% of citations that carry no `#fragment`** (and all case inbound) that
  no paragraph annotation surfaces. A `Site` holds the set of known document
  URIs, so a citation to a doc we don't have **renders as muted text, not a
  404** (`.noref`) — becomes live once that doc is parsed. Frontpage ranks
  laws by inbound count. `lagen all generate` →
  `site/data/generated/{index.html,style.css,sfs/*.html,dom/*.html}`;
  `lagen all serve [--port]` serves it. `test/test_site.py`.
- ✅ **2026 presentation redesign — the scroll-driven context rail.** The page
  shell was rebuilt (`render.page`): a sticky masthead with per-section nav, a
  three-column grid (TOC · reading column · context rail) that under 64rem
  becomes a single reading column with the side columns as drawers — the TOC an
  off-canvas left drawer, the rail a bottom sheet, opened from a fixed bottom
  toolbar (Innehåll · Sök · Kontext, `render.MOBILE_BAR` + `lib/assets/
  drawers.js`) while the masthead wraps (icon-only search, horizontally
  scrollable nav) and scrolls away — a serif/sans type system on warm paper, and SFS §-numerals
  hung in a gutter with a permalink pilcrow. The big structural change is that
  **inbound is no longer floated inline next to each paragraph** — a `Rail`
  collector gathers every id-bearing node's context (who cites it — split
  temporally when the label was renumbered — which EU article it transposes,
  correspondence/tidigare-beteckning margins, FK/kommentar/remiss and
  bemyndigande panels) into a single JSON island, and the client
  (`lib/assets/scrollspy.js`, `window.lagenScrollspy(root, island)` — one
  instance per reading surface, returning a destroy function; the page's own
  `.gr-body` gets one at load, each split-view pane gets its own, below) swaps
  the right-hand rail to the paragraph at the
  top of the viewport as you scroll (the "Kontext för …" panel; nodes that
  drive it carry `data-rail`). All
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
  acts collapse `ext/celex/` to `/celex/`. `render.href`, the API (`SearchResult`/
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
  författning" watermark that stays visible at any scroll depth (`render._expired_banner`
  + the `body.expired` treatment). A *future* repeal date is still in force.
  `test/test_site.py`.
- ✅ **Statute browse listing — visual hierarchy.** An SFS entry is split
  into its dropped designation/number prefix (shown subdued) and the subject it
  sorts under (emphasised), so the eye lands on the sort key (`facets._sfs_split`);
  parliamentary primary law (a *lag*, a *balk*, or a grundlag) is shown at full
  weight while secondary instruments (förordning, kungörelse, …) are dimmed
  (`_sfs_is_statute`). The listing carries `pre`/`key`/`subdued` on each
  `BrowseDoc`. `test/test_facets.py`, `test/test_api.py`.
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
    `document/inbound` (the killer feature as data),
    `document/outbound` (`hosted` flag for un-parsed targets), `sources`, `dumps`.
    Auto `/openapi.json` + `/docs`. CORS-open (read-only public data) so the
    static site reaches it cross-origin. Verified live against the **real
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
  - ✅ **NDJSON bulk dumps** (`lib/dump.py`, `lagen <src> dump`) — every
    `artifact/<source>/**.json` re-serialised one-per-line, gzipped, to
    `site/data/dumps/<source>.ndjson.gz`. Each line round-trips to its on-disk
    artifact; the citation graph is already inline, so a line is self-contained
    (no catalog read, no transform). Listed at `/api/v1/dumps`. Verified on the
    real `kommentar` source (212 lines). `test/test_dump.py`.
  - New deps: `opensearch-py`, `fastapi`, `uvicorn` (pyproject). ✅ **`lagen all
    index` run at corpus scale** against a provisioned OpenSearch — works.
    ✅ **Incremental relate + index** (content-hash diff, see 2026-06-26 log).
  - ✅ **MCP server** (`accommodanda/api/mcp.py`, mounted at `/mcp` via
    Streamable HTTP on the same `lagen all serve` FastAPI app) — the same
    read-only view reshaped as seven tools (`search`, `resolve_citation`,
    `get_document`, `list_documents`, `get_incoming_citations`,
    `get_outgoing_citations`, `list_sources`) for any MCP-capable AI host,
    public and unauthenticated like REST. The tools are thin wrappers over
    the same `lib` functions the REST endpoints use; `lib/pins.py` was
    extracted as the shared citation-shaped-query resolver (name+pinpoint →
    exact fragment target) behind both REST `/search` and the MCP
    `search`/`resolve_citation` tools. `test/test_mcp.py`, incl. an
    end-to-end Streamable HTTP round-trip against a running app.
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
    anonymous readers — the affordances are grafted client-side (`render.EDITOR`,
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
  (Stage), `catalog.forarbete_document` (source `forarbete`), `render_forarbete`
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
  in focus. `render._commentary_index` builds `{(law_uri, anchor) → prose}` from
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
    rejected** (SFS-defined alone, before nyckelord). `render_begrepp` shows the
    stub note + inbound; `generate_site` renders the path-less stub.
    `test/test_wiki.py`. **EU defined terms now promoted too**
    (`catalog.definition_links`): each Swedish EU act's definitions-article point
    that `defines` a term emits a `dcterms:subject` edge to `begrepp/<Name>`,
    anchored to the point — so an EU term joins the shared namespace (`ränta`,
    `royalties`) and the concept page shows which EU act defines it, while the
    act-local term-use interlinking (a use → the act's own definition point) is
    untouched. Swedish manifestation only (the namespace is Swedish); English acts
    excluded. Verified on 32003L0049 → Ränta/Royalties concepts with the act
    inbound.
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
  `render_eurlex` (doctype-labelled CELEX page), `page_relpath` routes
  `ext/celex/…` → `eurlex/{celex}.html`. **The payoff:** a CELEX citation to an act
  we've now parsed renders as a **local** link (`site.has` wins over
  `is_external`); only *un-parsed* EU acts still fall back to the external EUR-Lex
  href — exactly the §6 "becomes live once parsed" promise, now for EU law.
- ✅ **Corpus on disk:** ~102k EU documents parsed to artifacts
  (`site/data/artifact/eurlex/`); manifestation mix ~73k Formex / ~11k HTML / 122
  PDF. `test/test_eurlex_parse.py` (Formex, 11 tests), `test/test_eurlex_html.py`
  (HTML/PDF fallback, 5).
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
  from elsewhere, feeding a new "EU-rätt" inbound-panel group
  (`render.INBOUND_GROUPS`). Refreshed via `lagen eurlex casenames`.
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

### 7e. Myndighetsföreskrifter vertical (agency regulations) 🚧

Binding regulations issued by ~100 agencies into their own författningssamling
(FFFS, AFS, NFS, …). The value-add: a föreskrift's **`bemyndigande`** points into
SFS at the empowering paragraf — a *new* edge type (statute → regulation) that
makes a law's page list the regulations issued under it — plus `genomforDirektiv`
(→ EU) and `upphaver`/`andrar` (the intra-fs amendment graph). Note the FORESKRIFTER
*citation* grammar was never implemented in the old engine (§5 💤), so föreskrifter
are not yet citation *targets*; the inbound value comes from the edges above.

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
  (`render.bemyndigande_margin`) listing them — the headline value-add (a statute now lists
  the regulations issued under it). The edge is a *typed* relation, kept out of the generic
  "Hänvisat till av" panel (its own `_NOT_BEMYNDIGANDE` filter), and the föreskrift page
  shows the mirror outbound "Bemyndigande". Föreskrift is now a first-class rendered source
  (`render_foreskrift`, lagen.nu's `/{fs}/{år}:{nr}` route, browse + frontpage), its
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

### 7f. avg vertical — JO + JK + ARN myndighetsavgöranden ✅ (first cut)

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
  storage relpath), `catalog.avg_document`, `render_avg` (JO-beslut/JK-beslut
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

`accommodanda/remisser/` — remiss (public referral) cases from
regeringen.se/remisser/: a remiss sends a SOU/Ds out for consultation, and over
the referral period answers ("remissvar") accumulate from courts, agencies and
organisations. This corpus is **never published as its own pages** — it only
feeds an opt-in LLM pass whose output surfaces on the *referred* förarbete's
context rail, so it has no `relate`/`index`/`dump`/`generate` stage at all.

- **`model.py`**: `Remiss` (the case: title, dnr, deadline, cross-ref to the
  referred förarbete via `remitterat`, and `svar` — the `Remissinstans` list of
  organisations that have answered), `Remissvar` (one organisation's parsed
  answer). `org_slug` derives the filed-under-basename identity that
  `download.py`/`parse.py`/`build.py` all key on.
- **`download.py`**: harvests the paginated `/remisser/` listing plus each case
  page's metadata, "Remissinstanser" PDF and "Remissvar" list; a Genvägar
  shortcut (or, failing that, the case title) is matched against
  `lib.regeringen.TYPES` to recover the referred förarbete's canonical
  basefile. `sync` runs two passes — discover new cases newest-first (stopping
  at the first already-known slug; `--full` re-walks everything), then
  re-poll every still-open case (deadline unknown, or within a 21-day grace
  period of it) for newly-arrived answers and fetch any answer PDF not yet
  cached. Any per-case fetch or parse failure — an HTTP error, or a 200
  response whose DOM doesn't match what `parse_case` expects (a bot-challenge
  interstitial, a truncated response) — is written as a *stub* record from the
  listing facts alone — the on-disk slug is the incremental stop condition, so
  a silently-skipped failure would otherwise hide that case from every later
  incremental run; the stub has no deadline, so it stays "open" and gets
  re-polled until a real fetch succeeds. `sync_one`/`--only <url>` fetches one
  already-known case directly, bypassing the listing walk.
- **`parse.py`**: one answer PDF → `Remissvar`, via the shared
  `lib/pdftext` (`pdf_pages` + `page_paragraphs`) flattened to plain paragraph
  text — no structural classification, since the only downstream consumer is
  an LLM reading prose. Unlike JO/ARN/föreskrift there is no fixed running
  header to strip (each organisation's PDF carries its own letterhead), so
  `page_paragraphs` now accepts `identifier=None`/`""` and skips
  header-stripping outright rather than matching on a bad substitute.
- **`ai_analyze.py`** — `lagen remisser ai-analyze <case-slug>/<org-slug>`, the
  sole LLM pass over this corpus (never called from parse/relate/generate, the
  same doctrine as `kommentar ai-annotate`): maps one answer onto the specific
  sections of the referred SOU/Ds it discusses, with a per-section sentiment
  score and a verbatim quote plus an overall stance, validated strictly
  (every cited section id real, every quote a verbatim substring of the
  answer) and written as a `.ann` layer in the curated store (`lib/annstore.py`,
  `WIKI_ROOT/ann/remisser/…`, mirroring the answer artifact's relpath). Retries
  once as a real assistant/user follow-up turn on a malformed reply — since
  generalized into `lib.llm.author` (§5/§6/api, 2026-07-06), the shared
  validate/self-repair-retry loop eurlex/wiki annotate now use too.
- **Wired into `render.py`**: `_remiss_indexes` walks the remisser artifact
  tree directly (`layout.artifacts("remisser")`, not the catalog — this source
  is never `relate`d), picking up each answer's mirrored `.ann` layer from the
  curated store (`lib.annstore`), and builds
  `remiss_feedback`/`remiss_overall` on `Site`; `Rail._remiss_html` renders
  them as a "Remissvar" section — per-section on the cited `avsnitt`, and a
  document-level "most interesting feedback" panel via `Rail.add_document`,
  now wired into `render_forarbete`.
- **`lib/regeringen.py`** (new, rule:second-use-goes-to-lib): the doctype table
  (`TYPES`) and listing-DOM walk (`listing_items`) both `forarbete/download.py`
  and `remisser/download.py` need, extracted once remisser became the second
  regeringen.se harvester (remisser no longer imports from `forarbete`).
- Wired end-to-end: `lagen remisser download [--only <url>] [--full]`
  (harvest) + `parse` Stage (recipe includes `lib/pdftext.py`); no
  `relate`/`index`/`dump`/`generate` — this source publishes nothing of its
  own. `test/test_remisser.py`, `test/test_remisser_parse.py`,
  `test/test_remisser_render.py`, `test/test_remisser_ai_analyze.py`,
  `test/test_pdftext.py` (32 tests, hermetic).

### 7i. site vertical — lagen.nu's editorial chrome ✅ (first cut)

`accommodanda/site/` carries the parts of lagen.nu that are hand-authored
prose, not extracted legal-document semantics: the curated frontpage law
list, the `/om/*` about pages, and the sitenews feed. Content is markdown in
the same `lagen-wiki` repo as `concept/`/`commentary/`, under a new `site/`
tree (`site/frontpage.md`, `site/sitenews.md`, `site/om/*.md`), populated
one-off by `tools/migrate_site_content.py` from the legacy MediaWiki
`Lagen.nu:Huvudsida` page, `lagen/nu/res/static/*.rst`, and `sitenews.txt` —
the markdown is the source of truth thereafter.

- **`model.py`**: a small block tree (`Heading`/`Paragraph`/`Bullets`/`Code`,
  Swedish on-disk discriminators `rubrik`/`stycke`/`lista`/`kod`) plus the
  three page shapes `Frontpage`, `AboutPage`, `Sitenews`/`NewsItem` — no
  `Forfattning`/`Avgorande`-style domain model, since there's no citation
  graph to hang one on.
- **`parse.py`**: markdown → JSON artifact for three fixed basefiles
  (`frontpage`, `om/<slug>`, `sitenews`, the last split into dated
  `NewsItem`s on `## YYYY-MM-DD HH:MM:SS Title` heads); reuses
  `lib.markdown`'s frontmatter/link/heading grammar and adds only the block
  layer (bullet lists, fenced code) the legal-prose parser doesn't need. A
  generic, symmetric `sfs:`/`eurlex:` link scheme (`[FB](sfs:1949:381)`,
  `[GDPR](eurlex:32016R0679)`) was added to `lib.markdown.target_uri` for the
  frontpage's law links — the content names the source, never its URL shape.
- **`render.py`**: artifacts → static HTML + an Atom feed, one entry point
  `write_site(out_root)`. Registered in `build.py` as `SOURCES["site"]` with
  a `parse` Stage, but — like `remisser` — it is **absent from `ARTIFACTS`**,
  so it is never `relate`d/indexed/dumped. It *is* rendered during
  `generate`: `cmd_generate` calls `write_site` on a full run, on
  `--aggregates-only`, and on `lagen site generate`. The curated frontpage
  overwrites the generic corpus-stats `index.html` (`write_index=False`
  threaded through `render.generate_site`/`render_aggregates` when
  `has_frontpage()`); site artifacts are folded into `generate_watermark()`
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

### 7j. HUDOC + Council of Europe treaties + ICRC IHL treaties + UN Treaty Collection + ICC case law ✅ (first cut)

Five verticals sharing one folkrätt (international law) landing page:

- **`accommodanda/hudoc/`** harvests the public JSON endpoint used by HUDOC's
  own result UI (`/app/query/results`) and the selected document's converted
  Word HTML (`/app/conversion/docx/html/body`). Scope: Grand Chamber and
  Chamber judgments only (524 + 21,137 English documents at implementation
  time — Committee judgments, decisions, legal summaries, advisory opinions,
  resolutions and communicated cases are excluded; `--only <itemid>` can still
  fetch one deliberately). The bulk walk is newest-first and
  watermark-bounded; English is the default expression, with `--lang ENG,FRE`,
  `--only <itemid>` and `--limit`. Body downloads are the cost of a run, so a
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
  marker. `Treaty` artifacts live at `/ext/untc/{mtdsg_no}`; the URI grammar
  stays local to the vertical (rule:second-use-goes-to-lib). The folkrätt
  landing's UN half groups the curated instruments by subject (Traktaträtt
  och havsrätt / Mänskliga rättigheter / Flyktingrätt), each group
  chronological — the same landing-only pattern as coe/icrc, no faceted
  browse tree of its own. Bespoke per-treaty text ingest (parsing the UNTS
  PDFs) is a deliberate follow-up, not v1.
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
  `facets`, `datasets` and `render`. `test/test_icc.py` (11 tests) runs
  off a stored-record fixture (`test/files/icc/ICC-01_04-02_06-2359.json`)
  plus pure unit tests of the PDF-paragraph classifier — no network, no
  PDF binary. A real download+parse+relate+generate harvest has run: all
  269 curated decisions are live on `/folkratt/` and
  `/icc/{doc-number}`.
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
synthetic trimmed MTDSG fixture (`test/files/untc/XXIII-1.html`) — both no
network. `untc` has run a real download+parse+relate+generate harvest: all 14
curated treaties are live on `/folkratt/` and `/untc/{mtdsg_no}`. `icc` is
wired the same way; see its own bullet above for its test/harvest status.

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
| `../ferenda.old/data/sfs/parsed/` | the golden = old-pipeline parsed XHTML (11,056 docs), normalized per comparison — sibling checkout, not `site/data/` |
| `accommodanda/lib/` | **shared** horizontal libs: `lagrum` (citation engine), `util`, `errors` (`SkipDocument`), `harvest` (shared incremental-download core — `HarvestWatermark`, `walk`), `casenaming`/`eucasenaming` (DV/EU case identity + display naming), `labels` (every source's four reader-facing name forms — eyebrow/h1/official-title/citing-form — dispatched per source over the parse-time-stamped artifact + the curated datasets, read identically by `render.py` and `catalog.py`), `facsimile` (on-demand source-PDF page → retina PNG, disk-cached; `/api/v1/facsimile` + the legacy `/prop/2022/23:10/sid1.png` grammar), `poi` (Apache POI-via-jpype legacy `.doc`/`.docx` extraction to a flat paragraph stream — moved from `dv/word.py` once förarbete became its second caller; `dv/legacy.py` and `forarbete/legacy_formats.word_paras` both read through it, the latter for `.docx` only, `.doc` going through `antiword` instead) |
| `accommodanda/sfs/` | **acts vertical**: `{extract,reader,model,tokenizer,assembler,nf}` parser + `parallelappendix` (structurally detected, aligned bi/trilingual convention appendices, no per-law code; 95/107 detected candidates) + `register` (SFSR→amendments/förarbeten/metadata) + `graphics` (typed omitted-content detection *and* vision-localization — `collect_gaps`/`provenance_sfs`/`localize_group`) + `pdfmirror` (`mirror-pdf`, official-PDF mirror, the crop source) + `asgit` (`history-as-git` — the corpus as a git repo, one commit per amendment event, `docs/prd-sfs-history-as-git.md`) + `__main__` (diagnostic parse/validate CLI; `mirror-pdf`/`ai-includegraphics` are `build.py` actions, not here) |
| `accommodanda/dv/` | **court-decisions vertical**: `download`, `identity`, `model`, `parse`, `structure`, `legacy`, `namedcases` (HD named-precedent harvester); the legacy Word extraction itself now lives in `lib/poi.py` (shared with förarbete), `legacy.py` importing it as `poi as word`; canonical case title + HD given names live in `lib/casenaming.py` (shared with the catalog + renderer). `parse.parse_pdf_record` reads a raw pre-referat HD/HFD verdict straight off its PDF attachment (no `innehall` HTML yet), recovering the domskäl paragraph numbers from their unselectable margin bitmaps; `identity.py`'s R2 merge folds that raw record into the later referat that publishes the same målnummer once one exists |
| `accommodanda/forarbete/` | **preparatory-works vertical**: `download` (regeringen.se, 8 types + `pm`, promemorior outside the Ds series), `model`/`structure`/`parse` (PDF/html→nested structure→artifact; `parse.tag_frontmatter` retags the prop/skr överlämnande page — ingress heading, `signatur` signer blocks; `parse.parse_record`'s one body route, `_harvested_body`, reads every §7g frozen corpus alongside live harvests — all re-housed into ordinary `files` form, 2026-07-19), `legacy_formats` (body adapters shared by every re-housed corpus and the live harvest — dokumentstatus XML, riksdagen text/tml + skanning2007 html, ABBYY OCR-XML, scanned-PDF OCR text, TRIPS `div.body-text`, `word_paras` for `.doc`/`.docx` — `.doc` via `antiword`, `.docx` via `lib/poi.py`), `propkb` (facsimile-only fetcher for the KB two-chamber scans, 1867–1970 — adds no documents, only page images for the 17,295 XML-only propkb records; built, not yet run at corpus scale), `soukb` (body re-downloader for the KB-digitised SOUs, 1922–1999 — no ABBYY XML sibling, so the scanned OCR'd PDF is the body; walks `https://sou.kb.se/` as the source of truth, forgetting the legacy soukb records; 5,814 basefiles, 128 multi-volume; built, verified on one doc, not yet run at corpus scale), `riksdagen` (doctype-agnostic dokumentlista harvest engine, driven for `bet`/utskottsbetänkanden off data.riksdagen.se, no frozen corpus), `rskr` (second driver over `riksdagen.py`'s engine, for riksdagsskrivelser — HTML body, no PDF), `kommentar` (författningskommentar → EU-directive *genomför* edges, prop + fm), `genomforande` (relate-time resolution pinning each statement to its SFS paragraf), `fk` (per-paragraf FK commentary text → `kommentarer` artifact section → `fk_kommentar` catalog layer → statute-rail "Författningskommentar"), `lydelse` (two-column nuvarande/föreslagen lydelse tables reconstructed from per-run coordinates → `tabell` blocks in the SFS `rad`/`cells` shape), `tabell` (conservative generic data-table detection for everything tabular that isn't a lydelse comparison, with cross-page continuation, §7g/finding 04) |
| `accommodanda/eurlex/` | **EU vertical (EUR-Lex/CELLAR)**: `download` (SPARQL discovery), `bulk` (dump import), `parse`/`parse_html`/`parse_pdf` (Formex/HTML/PDF → one artifact shape; `parse.parse_opinion` reads an Advocate General opinion's Formex `CONCLUSION` structure), `definitions` (defined-terms extraction + in-act interlinking), `lang`, `model` (`doctype` splits sector-6 CELEX into judgment/opinion/order by document-type letter), `casenames` (harvest CELEX → usual name for named EU cases from Wikidata into `data/casenames.json`, read by `lib/eucasenaming.py`), `data/treaties.json` (curated Swedish names for EU primary law, keyed by CELEX stem, read by `lib/labels.py`) |
| `accommodanda/hudoc/` | **European Court of Human Rights vertical**: HUDOC JSON result pagination + full-text HTML conversion, typed case model, article-facet references into CoE treaty provisions |
| `accommodanda/coe/` | **Council of Europe Treaty Office vertical**: complete-list/detail/official-text harvest, treaty model, HTML/PDF article parser; canonical `ext/coe/{number}#A…` targets shared with HUDOC |
| `accommodanda/icrc/` | **ICRC international humanitarian law treaty vertical**: anonymous Drupal JSON:API list+detail harvest (no PDF — the envelope carries the authentic text), typed `Treaty` model, offline article-tree parser; canonical `ext/icrc/{number}` targets, curated `data/names.json` for the Geneva Conventions/Additional Protocols |
| `accommodanda/untc/` | **UN Treaty Collection (MTDSG status) vertical**: one static-HTML fetch per curated treaty, typed `Treaty`/`Party` model with an empty `structure` (the MTDSG carries status only — text lives in per-treaty UNTS PDFs, out of scope), offline participation-grid parser; canonical `ext/untc/{mtdsg_no}` targets, curated `data/treaties.json` (14 instruments: VCLT, UNCLOS, Genocide Convention, the core human-rights treaties, the Refugee Convention + Protocol) |
| `accommodanda/icc/` | **International Criminal Court case-law vertical**: two-source harvest — icc-cpi.int `/decisions` facet scrape (curated Rome-Statute decision types, `data/decision_types.json`) scopes the set and yields document numbers, the Legal Tools API (legal-tools.org) resolves metadata + PDF; HUDOC-shaped `Decision`/`Block` model, `pdftext`-based article parser with numbered-paragraph/heading classification; canonical `ext/icc/{doc-number}` targets kept local to the vertical (rule:second-use-goes-to-lib) |
| `accommodanda/avg/` | **JO/JK/ARN-decisions vertical**: `model` (`Beslut`; URI = the citation-minted `avg/{org}/{dnr}`), `download` (JO WordPress admin-ajax API + PDFs; JK one-shot listing + landing pages, `jk_canonical` dnr normalization; ARN one-page vägledande-beslut listing; also the store-path helpers `arn_pdf_path`/`jo_pdf_path`/`jo_officialreport_path`/`RE_ARN_DNR`, moved here from the deleted `legacy.py`, §7g teardown 2026-07-19), `parse` (JO/ARN PDF via `lib/pdftext`, JK landing HTML; DV parse-type citation scan) |
| `accommodanda/foreskrift/` | **agency-regulations vertical**: `model` (Regulation/Consolidation/Amendment primitives), `harvest` (per-agency enumerate seam {indexed,paginated,json,sitemap,bespoke} × resolve seam {landing+classify, direct} wired onto `lib/harvest.walk`; `Agency.browser` transport selection; `Skip`/`guarded_enumerate` resilience for flaky indexes; classify seam {file,section,href,single,default_regulation}), `agencies` (per-fs config registry, 71 registered författningssamlingar, 66 live + 5 with no live harvester), `skvfs`/`mtfs` (F5-protected source semantics), `download`, `parse` (PDF → Regulation artifact: text-based `N kap.`/`N §` classify, masthead metadata, bemyndigande/genomför via the citation engine), `structure` (kapitel/paragraf nest + SFS `#K2P3` anchors). All §7g frozen-import records (the 909 SKVFS/SOSFS/HSLF-FS records, then the ~30 further myndfs corpora, 2,177 documents) were one-time imported and migrated into ordinary harvested form; body PDFs copied under `FORESKRIFT_DOWNLOADED/<fs>/`, `legacy`-marked records kept as ordinary records with a `"source": "*-legacy"` provenance marker. Both one-time import modules (`legacy.py`, twice built and twice deleted once its import ran to completion) are gone (§7g teardown, 2026-07-19) |
| `accommodanda/lib/browser.py` | detached headful-Chrome transport for F5/Shape-protected public sources: navigate without a Playwright/CDP connection, wait the source-configured interval, then attach briefly to read the completed DOM or exact browser-cached PDF; selected only by SKVFS and MTFS; on a headless host it auto-starts a private Xvfb framebuffer and runs Chrome headful against it, torn down on exit |
| `accommodanda/remisser/` | **remiss (referral-response) vertical**: `model` (`Remiss`/`Remissinstans`/`Remissvar`, `org_slug`), `download` (regeringen.se `/remisser/` two-pass sync + `sync_one`/`--only`, stub records for any per-case fetch/parse failure), `parse` (answer PDF → `Remissvar` via `lib/pdftext` with no fixed header), `ai_analyze` (the sole LLM pass — sentiment+quote per section, `.ann` layer in the curated store, `lib/annstore.py`). Never `relate`d/published; its `.ann` layer feeds the referred förarbete's rail via `render._remiss_indexes` |
| `accommodanda/lib/annstore.py` | the curated store for every `ai-*` action's output (eurlex/kommentar `.ann`, sfs `.corr` — the latter also written mechanically by `lagen sfs table-correspond` from a prop's own jämförelsetabell bilagor (`forarbete/jamforelse.py`) and by `lagen sfs renumber-correspond` from the register's "betecknas" omfattning clauses (same-law renumbering, RF 2010:1408) — and sfs `.graphics`, `lagen sfs ai-includegraphics`'s vision-localized graphic crops) — `WIKI_ROOT/ann/<source-dir>/<relpath>`, mirroring the artifact tree's relpath grammar; envelope (`meta`: status generated/verified, model, date, input sha256 hashes, optional `meta_extra` fields like `.graphics`'s `through` provenance horizon), `guard`/`drifted` gate regeneration and derive staleness; per-entry `"verified": true` curation on a `.graphics` gap is preserved only while both resolved source and stored semantic identity still match, so renumbered/transformed gaps cannot inherit a crop by positional id; `write` itself stays blunt; inventoried by `lagen ann status` |
| `accommodanda/lib/regeringen.py` | shared regeringen.se harvest knowledge (rule:second-use-goes-to-lib): the doctype table (`TYPES`) and `ul.list--block` listing walk (`listing_items`), used by both `forarbete/download.py` and `remisser/download.py` |
| `accommodanda/site/` | **editorial-chrome vertical**: `model` (block-tree dataclasses + `Frontpage`/`AboutPage`/`Sitenews`), `parse` (markdown → artifact for `frontpage`/`om/<slug>`/`sitenews`), `render` (artifacts → HTML + Atom, `write_site`). Content is markdown in `lagen-wiki/site/`, migrated once by `tools/migrate_site_content.py`. Never `relate`d/indexed/dumped (absent from `ARTIFACTS`, like remisser); rendered during `generate` |
| `accommodanda/lib/pdftext.py` | **shared font-aware PDF extraction** (förarbete + föreskrift + avg (JO/ARN) + remisser): `pdf_pages` (`pdftohtml -xml` → bold/italic-tagged `Line`s) → `page_paragraphs` (reflow, strip running header/page-no/TOC — `identifier=None` skips header-stripping for sources with no fixed masthead, e.g. remisser) → the vertical's own `classify` |
| `accommodanda/config.py`, `lib/layout.py`, `lib/net.py` | runtime config (`config.yml`/`data_root`/`catalog_root` — the latter decoupling `catalog.sqlite`'s location from the bulk corpus, env `CATALOG_ROOT`), centralized document layout (`page_relpath` on-disk file ↔ `page_url`/`url_to_relpath` public lagen.nu address), resilient HTTP session + harvest progress reporter |
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
| `test/test_avg.py` | avg (JO/JK/ARN) parser + citation-grammar suite |
| `tools/golden_dv.py` | DV golden cross-check (references vs old distilled RDF) |
| `tools/golden_dv_structure.py` | DV structural golden (instance/ruling skeleton vs old parsed XHTML) |
| `tools/golden_eurlex.py` | EUR-Lex metadata cross-check against a retained CELLAR snapshot (no legacy oracle exists for this vertical) |
| `accommodanda/build.py` | orchestrator: `lagen <source> <action>` build driver + freshness; corpus verbs `relate`/`generate`/`index`/`dump`/`serve` (one process serving the static site + REST API + MCP) |
| `accommodanda/lib/catalog.py` | derived SQLite catalog + cross-source citation graph (`relate`) |
| `accommodanda/lib/render.py` | static HTML site w/ inbound annotations + live ⌘K search (`generate`) |
| `accommodanda/lib/assets/` | the browser-facing static chrome as real files (`style.css`, `editor.css`, `dom.js` — shared `window.lagenDom` vocabulary: own-document anchor resolution across split-view panes, id-attribute selector, landing flash, JSON-island parse — `scrollspy.js`, `search.js`, `popover.js`, `fullsearch.js`, `versions.js`, `faksimil.js`, `drawers.js` — the mobile bottom toolbar's TOC drawer / context-rail bottom sheet — `editor.js`, `robots.txt`) — `render.write_assets` ships them via the same Brotli precompression as pages: the JS is concatenated in load order into one `script.js` bundle (the page links a single URL, so a new module publishes via `generate --assets-only` instead of forcing a full regenerate), `style.css` with `editor.css` appended |
| `accommodanda/lib/text.py` | shared artifact text flattener (node/document/fragment plain text) |
| `accommodanda/lib/search.py` | OpenSearch full-text indexer (standalone units collapsed by `doc_uri`, no parent-child join), `index` |
| `accommodanda/lib/feeds.py` | legacy dataset-alias map + pure Atom/HTML feed renderer, shared by static `/dataset/<alias>/feed` generation and the live query-param endpoints |
| `accommodanda/lib/dump.py` | NDJSON bulk corpus dumps (`dump`) |
| `accommodanda/api/app.py` | FastAPI REST/OpenAPI service, mounted on `lagen all serve` |
| `accommodanda/api/mcp.py` | public MCP server (Model Context Protocol), mounted at `/mcp` |
| `accommodanda/lib/pins.py` | citation-shaped-query resolver, shared by REST `/search` and the MCP tools |
| `<catalog_root>/catalog.sqlite` | derived catalog (documents + links); `catalog_root` defaults to `data_root` |
| `site/data/generated/` | generated static site (`index.html`, `sfs/`, `dom/`) |
| `test/test_site.py` | derived-layer suite |
| `site/data/downloaded/sfs/sfsr/` | downloaded SFSR register pages (11,231) |
| `site/data/downloaded/sfs/pdf/` | official published-SFS PDF mirror (1998 onward), keyed by SFS number; the crop source for the `.graphics` layer and `/api/v1/sfs-graphic` |
| `site/data/.build/manifest.json` | build freshness state (input + recipe hashes) |
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
`commit-planner`) and the `/wrapup` skill.

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
  `generate_watermark`, `page_signature`) all read/write through the store
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
  (`render.INBOUND_GROUPS`). New CLI action `lagen eurlex casenames` refreshes
  the snapshot.
- **§5/§6/api** (2026-07-06) — review-fix pass across the corpus: `lib/llm.py`
  gained the shared `author` validate/self-repair-retry loop (factored out of
  the near-identical retry code in eurlex/wiki annotate + remisser
  ai-analyze); `lib/pdftext.py` gained a `hidden=True` mode (recovers an
  OCR text layer `pdftohtml` otherwise drops) and `flat_lines` (page-break-
  flattened line stream), with `eurlex/parse_pdf.py` cut over to consume it
  instead of its own extraction; `lib/compress.py` now writes through
  `util.write_atomic`. `generate_watermark()` widened its coarse gate: the
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
  avoid an import cycle). Client: `render.EDITOR`/`editor.js` grafts ✎ buttons +
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
