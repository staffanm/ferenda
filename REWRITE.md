# Ferenda rewrite plan

Status of the effort to rebuild ferenda — the framework behind lagen.nu —
keeping its accumulated domain knowledge while discarding the framework
that wrapped it. Living document; update status markers as work lands.

Legend: ✅ done · 🚧 in progress · ⬜ not started · 💤 deliberately deferred

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
  not separate concerns. SQLite/Elasticsearch are derived and rebuildable.
- **Machine-readable publishing survives, but not necessarily as RDF.**
  Plan: REST/OpenAPI + bulk dumps; MCP later; no GraphQL. Retire Fuseki;
  keep Elasticsearch.
- **The internal model is ours** — typed dataclasses with Swedish domain
  vocabulary, not tied to the dead rpubl/rinfoex vocabularies. Any
  Akoma Ntoso / RDF mapping is a downstream *projection*, not the model.
- **Native artifact format:** JSON with a JSON-LD context is the
  recommendation; final syntax decision still open.
- **Split the codebase, not the repo:** data pipeline vs consuming apps
  (web is just one consumer), divided at the artifact boundary, same repo.

### Target architecture (three layers)

1. **Vertical source pipelines** — `sources/sfs/`, `sources/dv/`,
   `sources/prop/`, … Each owns its full chain (fetch → extract → parse →
   typed model → artifact) and its *own* document model. No universal
   `Document` base class; share conventions as small libraries, not
   inheritance. Each exposes only its artifacts plus a tiny orchestrator
   protocol (`download()`, `parse(basefile)`, `list_basefiles()`).
2. **Horizontal libraries** — genuinely cross-source machinery: the
   citation engine (lagrum/förarbete/rättsfall recognition), identity/URI
   minting, the artifact envelope, fetch utilities, the make-like
   incremental build driver (a good idea from the old code — keep it, as a
   dumb orchestrator over file freshness, not as methods on a class), and
   the golden-corpus validation harness.
3. **Corpus-wide derived layer** — the reborn `relate` phase. Reads
   published artifacts across all sources into the SQLite catalog + ES;
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
  lib/      shared horizontal libs — lagrum (citation engine), util, errors
  sfs/      acts vertical — extract·reader·model·tokenizer·assembler·nf·register (+ __main__)
  dv/       court-decisions vertical — download·identity·model·parse·word·legacy
  build.py  orchestrator — the `lagen` build driver, composes the verticals
```

A vertical imports from `lib`; `lib` never imports a vertical; only `build`
(the orchestrator) imports across verticals.

---

## 2. Phase 0 — Regression safety net ✅

Before touching anything, make the old pipeline's output reproducible so
the new one can be checked against it. The old pipeline can no longer run
(it depends on `pkg_resources`, dropped by modern setuptools), so its
final output *is* the spec.

- ✅ `tools/golden_sfs.py` — comparator: `normalize` (old XHTML+RDFa →
  normal-form JSON), `compare --sections metadata,structure,references,amendments`,
  `freeze`.
- ✅ `site/data/sfs/golden/` — frozen golden tree: 11,056 SFS documents
  (the 174 zero-byte parsed files are old-pipeline dummies for
  removed/expired docs).
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

### 3a. Structural parser ✅ (98.7%)

`accommodanda/` — new architecture, recognition heuristics ported from the
old `sfs_parser` but the structure redesigned.

- ✅ `extract.py` — body extraction from rkrattsbaser HTML (+ archival
  `<pre>` path), encoding sniffing, `SkipDocument`.
- ✅ `reader.py` — `TextReader`, faithful port incl. autostrip-dependent
  blank-line semantics (matters inside tables).
- ✅ `model.py` — typed dataclasses: `Forfattning`, `Avdelning`,
  `Underavdelning`, `Kapitel`, `Paragraf` (temporal variants, moment),
  `Rubrik`, `Stycke`, `Lista`/`Listelement`, `Tabell`,
  `Overgangsbestammelser`, `Bilaga`, etc.
- ✅ `tokenizer.py` — ported recognizers emit a flat event stream.
- ✅ `assembler.py` — RANK-driven stack machine builds the tree.
- ✅ `nf.py` — projects the tree to golden normal form, **replicating the
  old URI-minting quirks exactly** (continuous-§ numbering, content-equality
  ordinal dedup, `in_effect` temporal suppression, skipfragments).
- ✅ `sfs/__main__.py` (`python -m accommodanda.sfs`) — `parse FILE` and `validate GOLDEN DOWNLOADED
  --sections …` (process-pool, diff bucketing).
- **Status:** structure match **98.7%** (10,912/11,056). Remaining ~144
  docs: övergångsbestämmelse-inside-kapitel (deliberate deviation), stale
  golden vs freshly downloaded amended laws, long-tail numbering.

### 3b. Citation recognition (legalref → Lark) ✅

`accommodanda/lib/lagrum.py` — Lark port of the old `legalref.py`
LAGRUM + EULAGSTIFTNING grammars.

- ✅ Earley grammar; trigger-regex scanning with longest-prefix retry
  (mirrors the old `(ref/plain)+` PEG root).
- ✅ Ported formatter semantics: relative-ref completion from structural
  context, sticky-chapter rules, external-law combined-link rule,
  "samma lag"/lastlaw, in-document law-name learning, direct URI minting
  (no COIN). Fragment letters K/P/O(mom)/S/N/M(mening)/L(ändring).
- ✅ The old `-_och_-` / `|lagen` preprocessing corruption is **gone by
  construction** — Lark regex terminals match `-lagen`-suffixed words
  directly.
- ✅ `FILTER_LAW` pre-filter ported to `nf.Projection.inline` (behaviorally
  significant and deliberately reproduced: lowercase "lag (1988:1556)" /
  "förordning (…)" is still never parsed, as in the old pipeline).
- ✅ Wired into `nf.py` as **inline links** (see §3d): every text node
  becomes a list of `str` runs + `{predicate,uri,text}` link objects at
  exact positions, with per-link sub-spans recovered from the parse tree.
  Refs in nested lists still attribute (for the reconstructed tuple oracle)
  to the stycke fragment but resolve against the item's own id.
- ✅ `test/test_lagrum.py` — pytest port of the old `integrationLegalRef`
  driver over `test/files/legalref/{SFS,EGLag}`; the only expected failures
  are exactly the cases the old engine also failed.
- ✅ `validate --sections references` extended; `refs` CLI for single docs.
- **Status:** 2018:585 = 219/222 tuples, 0 extra. Corpus-wide
  structure+references **86.7%** (9,581/11,056). Sampling shows the bulk
  of remaining diffs are **old-pipeline defects, not new gaps**:
  cross-document `lastlaw`/`namedlaws` state leaks, chain fragmentation
  linking chapters to the wrong law, and all-or-nothing `RefParseError`
  holes the new scanner fills.

### 3c. SFSR register / amendments / förarbeten / metadata ✅

`accommodanda/sfs/register.py` — parses the downloaded SFSR register page
(`site/data/sfs/register/{year}/{nr}.html`) into one amendment entry per
change act (base act first), keyed by URI. Port of the old
`extract_metadata_register` (sfs.py:604-789) minus the framework.

- ✅ Property mapping in the post-`polish_metadata` form the golden records:
  identifier/arsutgava/lopnummer, departement → org URI (rdflib resolver
  over `swedishlegalsource.ttl`, with `sanitize_departement`),
  publisher/beslutadAv/forfattningssamling constants → URIs, the rinfo
  `owl:sameAs`, dates, CELEX → `genomforDirektiv` + `celexNummer`.
- ✅ **Omfattning → the `L*` change tuples** (`rpubl:ersatter`/`upphaver`/
  `inforsI`): each changecat classified by prefix, resolved against the base
  law via a fresh `LagrumParser` (forced predicate), plus raw `rpubl:andrar`.
- ✅ **Övergångsbestämmelse join** (`nf.py`): the OB blocks the structure
  parser drops are projected with `L{sfsnr}`/`L{sfsnr}S{n}` ids and joined
  onto their change act's entry as `content`; their text feeds the same
  reference scan (the `L`-source ref tuples).
- ✅ List/scalar normalization mirrors the golden's `add_meta` (single value
  of a non-multivalued property collapses to a scalar).
- ✅ `validate --sections amendments` + `diff_amendments` in `golden_sfs.py`
  (URI-slug canonicalized; the old serializer's `dcterms:isPartOf`
  containment is a redundant derived artifact — not reproduced; per-amendment
  `forarbeten` deferred, see below). `test/test_sfs_register.py` —.
- **Status:** amendments match **97.5%** (10,775/11,056). The 281 diffs:
  ~128 **extra amendments = change acts added after the golden freeze**
  (new-is-right — adjudication, below); ~69 docs where the old pipeline
  suppressed a base-act/fallback övergångsbestämmelse `L`-id and the new one
  mints it (faithful-reproduction gap in the `coin_uri` collision rule);
  the rest mixed Omfattning-tuple defects/edges.
- ✅ **Per-amendment Förarbeten** — the FORARBETEN grammar landed, so
  `parse_forarbeten` now scans each "Förarbeten:" field and emits the
  identifier form the golden records (`ucfirst(ref.text)` + prop spelling
  normalization). **99% per-entry** on a 120-doc sample; the comparison no
  longer excludes it.
- ✅ **Document-level metadata** (94.8%) — `parse_sfst_header` + `build_metadata`.
  The `metadata` section is the *konsolidering envelope*: register/SFST-header
  descriptive fields (title, departement→creator org, utfärdande/ikraft/upphävd
  dates, omtryck, CELEX) plus the derived consolidation fields (identifier
  "i lydelse enligt SFS …", `konsoliderar`, `konsolideringsunderlag` = base +
  all change acts, `rdf:type`, the `/konsolidering/<cutoff>` URI). The
  responsible department comes from the SFST header (authoritative, as in the
  old pipeline); sub-org suffixes ("Finansdepartementet BA") are canonicalized
  out of the comparison as drift. The run-date fields (`dcterms:issued`, the
  date-stamped `owl:sameAs`) are not reproduced — canonicalized away.
  **metadata match 94.8%** (1,895/2,000 sample); the residual is the same
  stale-golden drift (cutoff + department both move as new amendments land)
  plus golden title-truncation — all new-is-right. `dcterms:alternate` comes
  from `sfs.ttl`; `rdfs:label` (emitted selectively by the old pipeline) and
  the date-form konsolidering version for un-amended laws are not reproduced.

### 3d. Remaining SFS work ⬜ / 🚧

- ✅ **SFS downloader** `accommodanda/sfs/download.py` — harvests the new beta
  database (`beta.rkrattsbaser.gov.se`), an ASP.NET SPA over an **open raw-
  Elasticsearch passthrough** (`POST /elasticsearch/SearchEsByRawJson`, body
  `{"searchIndexes":["Sfs"],"api":"search","json":<ES query>}`). The ES
  `_source` is the **entire consolidated act in one JSON** — body text
  (`fulltext.forfattningstext`, already the parser's plain-text layout),
  register, and the amendment list — so one request replaces the old two-page
  SFST+SFSR scrape. Enumeration is `match_all` + sort + `search_after` (past
  ES's 10k window); no SFS-number guessing. Incremental (newest-first by
  `uppdateradDateTime`, stop at first unchanged page) + `--full` modes, atomic
  writes, idempotent, politeness delay, `list_basefiles()`. **13,789 acts.**
  - **Consolidation archiving** (the SFS-only complication the DV path lacks):
    a `grundforfattning`'s content changes as amending acts fold in. Each
    distinct consolidation is identified by `fulltext.andringInford`
    ("t.o.m. SFS 2026:764", = the old `_find_uppdaterad_tom`). When a
    re-download carries a different `andringInford`, the on-disk copy is moved
    to `source/archive/{year}/{nr}/{version}.json` before the new one
    overwrites `source/{year}/{nr}.json` — recreating the old downloader's
    get_archive_version/archive machinery. A same-version data correction
    overwrites without archiving (keyed on legal version, not checksum).
    `test/test_sfs_download.py`.
  - **Layout** mirrors the dv/ vs domstol/ split (user decision): the new
    harvest lives in its own `site/data/sfs/source/` tree, leaving the frozen
    legacy `downloaded/`+`register/` HTML (which the golden was derived from)
    pristine. They are the *same* documents keyed by beteckning, so the new
    simply supersedes the old — no identity reconciliation as DV needed.
- ✅ **JSON-or-HTML parse selection** (DV single-best-source pattern) —
  `sfs.load_inputs` prefers the new JSON `_source` over the legacy SFST+SFSR
  HTML when present, so `lagen sfs parse 2018:585` / `python -m accommodanda.sfs
  parse` transparently use whichever exists. `register.register_from_source` /
  `sfst_header_from_source` map the JSON's structured fields back onto the same
  `Register`/header intermediates the HTML parsers emit, so **all** of
  `amendment_properties`/`build_metadata`/`parse_forarbeten` is reused
  untouched. Verified faithful: parsing 2018:585 from JSON vs HTML gives **0
  field diffs** across the 10 shared amendments and identical base metadata —
  the only deltas are genuine freshness (JSON cutoff 2026:764/13 amendments vs
  frozen HTML 2023:390/10). Build `parse` now also emits the `metadata` section
  for both paths. (Also fixed: `SFS_CODE`/`DV_CODE` recipe-hash paths pointed at
  pre-reorg flat module locations, so recipe-versioning silently no-op'd.)
- 🚧 **Adjudication overlay** — decision made: **per-class predicates**
  (~5 rules), not per-tuple corrections. Now unblocked and higher-value: the
  first predicate is "**extra amendment whose change act postdates the golden
  freeze = accepted stale-golden**" (absorbs the ~128 extra-amendment diffs
  above plus the post-feed cross-document ref leaks in 3b). Cross-cutting over
  structure/references/amendments.
- ⬜ **begrepp / `find_definitions`** port → `dcterms:subject` links to
  `https://lagen.nu/begrepp/…` (~2% of golden tuples; currently excluded
  from comparison).
- ⬜ **Named-law data** — extend `lagen/nu/res/extra/sfs.ttl` to recover
  the cross-document name resolutions the old leak provided legitimately
  (it's live site data, so handle carefully).
- ✅ **Inline links / runs-spans (refs)** — every NF text node (`stycke`,
  `punkt`, table cell, `rubrik`, `upphavd`) is now a LIST of inline nodes:
  plain `str` runs interleaved with `{"predicate","uri","text"}` link
  objects at their exact positions; the flat top-level `references` list is
  **dropped from the artifact**. Per-link sub-spans recovered by threading
  each emit's originating-node token span (`node_span`/`law_id_span`)
  through every LAGRUM+EU `fmt_*` in `lagrum.py`, plus a `link_spans`
  trailing-marker absorption pass (`§§`/`kap.` attach to the nearest
  preceding link) — reproducing the fixtures' boundaries exactly: change-note
  links whole "Lag (2001:1016)."; anonymous law → bare number ("(1976:580)"
  → "1976:580"); named law → whole "name (number)"; a combined external ref
  (single section-bearing inner ref) extends to swallow the law expression.
  **All text nodes are now scanned**, including headings/upphävd/top-level
  tables — a deliberate divergence (the old pipeline skipped them; this
  self-links e.g. a chapter heading's own "12 kap."→#K12).
  `nf.inline_references(structure)` reconstructs the old
  `(source,predicate,uri)` tuples from the inline links (excluding the
  newly-scanned node kinds) so the golden refs oracle still runs: 2018:585 =
  **219/222, 0 extra** (3 missing = golden frozen from a newer doc version;
  "2018:1177" is absent from the downloaded HTML — data skew, not a parser
  gap). New oracle `test/test_sfs_parse.py::test_sfs_links` — set-equality
  vs the fixtures' `<LinkSubject dcterms:references>` leaves, URI
  base-normalized; the one known gap is `regression-kommentar-inte-rubrik`
  (the lowercase-`förordning` FILTER_LAW quirk). `golden_sfs.canonicalize_node_texts`
  folds inline lists → string so the amendment/structure comparators still
  apply.
- ⬜ **Bold/italic runs** — the other half of runs/spans; refs done above,
  character formatting not yet emitted.

---

## 4. DV vertical (second vertical) 🚧

Court decisions (vägledande avgöranden). Forces the two highest-value
horizontal pieces: KORTLAGRUM citations and the cross-source link graph.

- ✅ **Downloader** `accommodanda/dv/download.py` — harvests the new courts'
  publication service at `rattspraxis.etjanst.domstol.se` (open JSON API
  behind an Angular SPA): `POST /api/v1/sok` paginates the whole corpus,
  `GET /api/v1/bilagor/{id}` for PDFs. Records stored verbatim as
  `site/data/domstol/downloaded/{domstolKod}/{uuid}.json` + attachments.
  Incremental (newest-first, stops at first seen page) and `--full`
  (oldest-first) modes; idempotent, atomic writes, politeness delay.
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
    referat, 231 HSV, …). Index at `site/data/dv/identity-index.json`.
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
- 🚧 **DV parser** — `accommodanda/dv/model.py` (flat Avgorande:
  metadata + ordered Rubrik/Stycke body blocks — court decisions have no
  rigid nesting) and `accommodanda/dv/parse.py`. **API path done:** body
  from `innehall` HTML (each `<p>` classified heading-vs-paragraph;
  numbered prejudikat paragraphs carry an ordinal; `<br>`/entities/`&nbsp;`
  handled, separators dropped), metadata from the curated fields,
  projected to a JSON artifact. Driven by the identity index (consumes
  the `domstol` member per case). **17,090 API-backed cases parse, 0
  failures**; the 966 empty bodies are exactly the records with no
  `innehall` (995 summary-only) — zero content dropped. `test/test_dv_parse.py`.
  Remaining increments, seams marked in the code:
  - ✅ **Legacy Word path (POI)** — `accommodanda/dv/word.py` reads the
    *original* binary `.doc` (POI **HWPF**) and `.docx` (POI **XWPF**) via
    jpype, **not** the antiword DocBook intermediate — a real DOM
    (paragraphs, table cells, bold runs) recovering the label/value
    structure antiword flattened. POI 5.4.1 jars vendored in `vendor/poi/`;
    OpenJDK 21 + `jpype1` deps; log4j-api pointed at SimpleLogger so its
    "no provider" notice stays off stdout. `accommodanda/dv/legacy.py`
    splits the flat `(text, bold)` stream into header / bold-label metadata
    / `REFERAT` body / `Sökord`/`Litteratur` footer → `Avgorande`,
    preferring the identity index's canonical referat/court. The whole
    referat is one Word table, so the body discriminator is the `REFERAT`
    marker, not table membership. **15,624 legacy docs parse, 0 empty
    bodies, 0 failures.** `test/test_dv_legacy.py` (14 JVM-free unit tests
    over synthetic streams).
  - ✅ **Field-level merge — investigated and rejected.** Measured the gaps
    a merge could fill for the 14,838 cases with both sources: body-fallback
    opportunity is **0** (all 965 API-empty bodies are summary-only nämnd
    records with no legacy original); the only fields legacy carries beyond
    identity are `Lagrum`/`Sökord`, filling API gaps on just ~10%/~7% of
    linked cases; `rättsområde`/`förarbeten`/`litteratur` are genuinely
    empty API-wide (not a parser bug) and absent from legacy too. So the
    architecture is **single-best-source per canonical case** (API when
    present, POI-legacy otherwise), not a merge.
  - 🚧 **Notisfall — deferred.** 852 sole-source cases (6 from the 1990s,
    504 from the 2000s, 304 from the 2010s, 38 from the 2020s) whose
    individual originals are zero-byte. 851/852 have the frozen `<body>`
    intermediate; the recent `notiser_*.zip` carries multi-notis `.docx`
    (`HDO_2017_notis_007-016.docx`) POI-able for ~342 but needing
    per-notis splitting + canonical-ID matching (the old `parse_not`
    lineage). Pre-2010 majority has only the frozen intermediate regardless.
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
  - Summary-only nämnd records (no `innehall`) get the sammanfattning as
    body downstream.
- ✅ **DV golden corpus (reference graph)** — `tools/golden_dv.py`. The old
  pipeline's distilled RDF (`site/data/dv/distilled/{COURT}/{id}.rdf`, 15,858
  files) is the frozen oracle: per case a document URI + its
  `dcterms:references` set. Cases match by URI (which now agree — the RDF shows
  `dom/rh/2009:37`, **independently confirming the case-URI re-minting**).
  Compares reference sets. On 3,143 matched referat cases: **96.8% old-ref
  recall**, 77.8% exact + 6.9% superset (84.7% find ≥ everything old did). The
  residual misses are editor-derived lagrum not cited verbatim in the body
  (the same signal as the 81% lagrumLista recall) + the new scanner filling old
  all-or-nothing holes — change-detector posture, investigated not assumed. The
  857 "no new artifact" are NJA notisfall (deferred) + the old pipeline's
  separate *verdict* resources (`dom/{court}/{malnr}/{date}`), not coverage
  gaps. ⬜ Metadata-field comparison (referatrubrik, dates) still to add.

---

## 5. Horizontal libraries (extract after DV) ⬜

- 🚧 Promote `accommodanda/lib/lagrum.py` → a `citations/` package,
  parameterized by grammar set (LAGRUM/KORTLAGRUM/FORARBETEN/RATTSFALL/…),
  context provider, and pre-filter — keeping the old
  `LegalRef(*parse_types)` configurability, which was a good idea.
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
- ⬜ Identity / URI minting library (with the court-code and
  referat-series canonicalization the old `canonicalize_uri` did).
- ⬜ Artifact envelope + JSON-LD context.
- 🚧 Incremental build driver (make-like freshness orchestration) —
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
    `site/data/sfs/artifact/<y>/<n>.json` and `site/data/dv/artifact/<slug>.json`
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
    `site/data/dv/identity-index.json`.
  - ✅ **Driver progress logging** — `run_action` prints a throttled
    single-line `\r` counter to stderr (`parse 5400/11228  ran … err …`) every
    50 docs; the per-document loop was otherwise silent until the final report.
  - ✅ `relate` + `generate` landed as **corpus-level verbs** (not per-doc
    Stages — see §6): the catalog rebuild and the static-site render. The
    earlier "per-doc upsert" plan was revised once it was clear generate's
    prerequisite set is data-dependent (the inbound set), not a static
    per-basefile input list.
- ⬜ Generic golden-corpus comparator (factor out of `golden_sfs.py`).

## 6. Derived layer + publishing 🚧

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
  rebuildable from artifacts alone, never a source of truth. Two tables:
  `documents(uri, source, kind, label, title, path)` and
  `links(from_uri, from_anchor, predicate, to_uri, to_root, text)`. One
  **generic walk** (`collect_links`) extracts edges from either source —
  works because citations are inline (`text`/`cells` run-lists) and both
  verticals mint the same `https://lagen.nu/<id>#<fragment>` URIs.
  `rebuild()` is per-source (drop + re-insert that source's rows),
  single-process and transactional (sidesteps multi-writer SQLite
  contention). `lagen all relate` → **catalog at `site/data/catalog.sqlite`**.
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
- ✅ **Case-law citation graph reconnected — DV document URI re-minted to the
  old scheme.** Was: the DV vertical published `dom/AD_1993_nr_100` (an ad-hoc
  referat-slug) while RATTSFALL citations mint the old rinfo canonical
  `dom/ad/1993:100` / `dom/nja/{year}s{page}` / `.../not/{n}` — so 42,281
  case→case edges pointed at URIs no document had. **User constraint: published
  case URLs / internal URI-shaped ids must NOT change from the old pipeline.**
  Fix (`dv/parse.py::case_uri`): mint the document URI by running the case's
  referat through the **same RATTSFALL parser citations use**, so the document
  URI is byte-identical to any reference to it, by construction — the old
  published identifier, not a new one. **All 17,393 referat cases parse, 0 fall
  back** (verified across the whole index). `test/test_dv_parse.py`
  (`case_uri` + minting tests). Required a full DV re-parse → re-relate →
  re-generate (the `uri` lives inside each artifact).
  - ⬜ **Non-referat cases (~1,335, ~7%)** keep a stable slug URI for now.
    They are never citation targets (RATTSFALL only names referat/notis), so
    the graph doesn't need them; but the old pipeline published them under the
    *verdict* scheme `dom/{publisher_slug}/{malnummer}/{avgorandedatum}`
    (`swedishlegalsource.space.ttl`). Restoring that needs a verified DV-court
    → rinfo-org-slug map (HDO→hd, ADO→ad, … across every hovrätt/kammarrätt) —
    deferred rather than guessed, since the URI is a published identifier.
- 🚧 **Freshness/incrementality** for relate+generate. `generate` now treats
  `relate` as its upstream dep and **auto-runs it** for any source whose
  artifacts are newer than the catalog (`stale_sources()`, make's
  target-older-than-prerequisite rule; `--force` re-relates all) — so
  `lagen all generate` alone refreshes the catalog then renders. Still
  coarse-grained: both relate and generate rebuild *whole*. Per-doc
  incremental generate is tractable and the remaining work — doc X's
  prerequisites are X's own artifact + the artifacts of its *inbound set*
  (`SELECT DISTINCT from_uri FROM links WHERE to_root = X`, the old deps-file
  contents as a catalog query); regenerate X iff any of those changed. Fine
  whole-rebuild for now (minutes). `parse` stays an explicit upstream step.
- ⬜ Elasticsearch indexing (keep; replaces Fuseki) — deferred (user decision).
- ⬜ REST/OpenAPI + bulk dumps; MCP later.
- 💤 **Note:** first end-to-end cut run against a *partial* corpus (SFS parse
  was still in flight: 283/11k laws, 8,374 cases). Once the full SFS parse
  lands, re-run `lagen all relate && lagen all generate` — most of the 11,133
  distinct cited law-roots (only 147 renderable so far) become live targets.

## 7. Further verticals 🚧

### 7a. Förarbeten vertical (preparatory works) 🚧

The third leg of lagen.nu's killer feature — förarbeten (prop/SOU/Ds/dir + the
lesser types) annotated onto the statute paragraphs they comment on. ~31,700
förarbete citations currently render as dead `.noref` text; this vertical makes
them resolve.

- ✅ **Downloader** `accommodanda/forarbete/download.py` — harvests all eight
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
    regeringen.se publishes untitled-by-number (SÖ, lagrådsremiss) fall back to
    the landing-page slug.
  - Incremental (newest-first, stop at first on-disk) + `--full`; atomic writes;
    browser UA (regeringen.se 403s bots); politeness delay. Stores per doc:
    `<slug>.json` record + landing `<slug>.html` + content PDF(s) under
    `site/data/forarbete/<type>/`. `test/test_forarbete_download.py`.
  - ⬜ **Older-period sources** (riksdagen data API, KB scans) — regeringen.se
    only reaches back ~1990s; the same-identifier basefile means these slot in
    as alternate sources later (the old pipeline's CompositeRepository idea).
  - ⬜ **lr/SÖ content links** — these expose an extensionless
    `/contentassets/<hash>/<slug>/` (HTML-rendered), not a `.pdf`; landing HTML
    is captured but no file pulled yet.
- ✅ **Parser** `accommodanda/forarbete/{model,parse}.py` (PDF → artifact). Text
  via poppler `pdftotext` (plain reading-order mode — isolates the running
  header + page number on their own lines, unlike `-layout` which mashes them
  into the alternating outer margin). Flat model (`Block`: rubrik/stycke + page),
  like DV. **Page = PDF index = printed page** (modern PDFs number from the
  title page), so each block carries its `#sid{N}` anchor — the target förarbete
  citations resolve to (`prop. X s. 39` → `prop/X#sid39`). Reflows wrapped lines
  (de-hyphenates), strips the running header (substring, anywhere — it bleeds
  into body lines), skips TOC pages, detects numbered headings. **URI minted to
  the citation-target form** (`prop/{riksmöte}:{no}`, `sou/{year}:{no}`, …) so
  document and citation agree by construction (the DV-URI lesson). Body scanned
  for refs (same engine as DV) → inline links. Validated: prop 2025/26:161 →
  284 blocks, 464 links (sfs 320, prop 126, sou 7, bet 4, celex 3, rskr 3).
  `test/test_forarbete_parse.py`.
- ✅ **Wired through build + catalog + render**: `lagen forarbete parse`
  (Stage), `catalog.forarbete_document` (source `forarbete`), `render_forarbete`
  (förarbete page with `#sid{N}` page anchors + page-level inbound margin notes),
  `doc_relpath` routes förarbete URIs to the `fa/` tree. So `relate`/`generate`
  light up the förarbete inbound graph — the ~31,700 dead förarbete citations
  resolve and each förarbete shows what cites it (and at which page).
- ⬜ Older-period sources (riksdagen/KB), lr/SÖ content, page-number offset for
  docs whose front matter shifts the printed sequence.

### 7c. Wiki value-add — kommentar + begrepp ✅ (first cut)

The hand-authored MediaWiki content (the dump in
`site/data/mediawiki/downloaded/`) imported as **two ordinary sources**, proving
the manually-written value-add flows through the identical artifact → catalog →
inbound → render pipeline as the machine-extracted sources.

- ✅ **Shared wikitext parser** `accommodanda/lib/wikitext.py`: MediaWiki XML →
  blocks; each prose paragraph → inline runs combining `[[wikilinks]]` (→
  `begrepp/<Concept>`) **and** the citation engine's law/case/förarbete links,
  non-overlapping. Author byline + `[[Kategori:]]` extracted.
- ✅ **`kommentar` source** `accommodanda/wiki/parse.py::kommentar_artifact` —
  per-paragraph SFS commentary. Each `== 21 kap 1 § ==` heading → a section
  anchored to the statute fragment (`2009:400#K21P1`) and **linking** to it, so
  `relate` records a kommentar→paragraph edge and the statute paragraph shows
  the commentary in its margin (the old side-by-side — grouped as "Kommentar",
  with the comment text as the hover snippet). Prose citation-scanned with the
  commented law as the relative-reference base (so "7 kap 3 §" resolves to the
  same law, "tryckfrihetsförordningen" / "NJA 1990 s. 510" to their docs).
  212 pages, **5,808 commentary→paragraph edges**.
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
- ⬜ **Next (per the design note)**: case↔concept *edges* (concept page listing
  its tagged cases — needs nyckelord → begrepp edges in `relate`, with synonym /
  defined-in-commentary resolution); embed commentary prose *inline* at the
  paragraph (not only the margin link); topic taxonomy (`Lagar inom …`); and the
  authoring layer (Git-backed prose editor committing markdown via PRs).

### 7b. Remaining verticals ⬜

The rest of `/mnt/data/lagen/data/{…}`. Each built the same way; the horizontal
layer should by now be stable enough that new sources are mostly grammar +
model + extraction.

---

## Key files

| Path | What |
|---|---|
| `tools/golden_sfs.py` | golden-corpus comparator / freeze |
| `site/data/sfs/golden/` | frozen SFS golden tree (11,056 docs) |
| `accommodanda/lib/` | **shared** horizontal libs: `lagrum` (citation engine), `util`, `errors` (`SkipDocument`) |
| `accommodanda/sfs/` | **acts vertical**: `{extract,reader,model,tokenizer,assembler,nf}` parser + `register` (SFSR→amendments/förarbeten/metadata) + `__main__` (validate CLI) |
| `accommodanda/dv/` | **court-decisions vertical**: `download`, `identity`, `model`, `parse`, `word`, `legacy` |
| `accommodanda/forarbete/` | **preparatory-works vertical**: `download` (regeringen.se, all 8 types) |
| `accommodanda/lib/wikitext.py` | shared MediaWiki-dump parser (wikilinks + citation engine → runs) |
| `accommodanda/wiki/` | **kommentar + begrepp sources**: `parse` (commentary anchored to §§, concept glossary) |
| `site/data/mediawiki/downloaded/` | MediaWiki dump (SFS commentary + concept pages) |
| `test/test_wiki.py` | wiki parsing suite |
| `site/data/forarbete/<type>/` | harvested förarbeten (record json + landing html + content pdf) |
| `test/test_forarbete_download.py` | förarbete downloader parsing suite |
| `tools/golden_dv.py` | DV golden cross-check (references vs old distilled RDF) |
| `accommodanda/build.py` | orchestrator: `lagen <source> <action>` build driver + freshness; corpus verbs `relate`/`generate`/`serve` |
| `accommodanda/lib/catalog.py` | derived SQLite catalog + cross-source citation graph (`relate`) |
| `accommodanda/lib/render.py` | static HTML site w/ inbound annotations (`generate`) |
| `site/data/catalog.sqlite` | derived catalog (documents + links) |
| `site/data/generated/` | generated static site (`index.html`, `sfs/`, `dom/`) |
| `test/test_site.py` | derived-layer suite |
| `site/data/sfs/register/` | downloaded SFSR register pages (11,231) |
| `site/data/.build/manifest.json` | build freshness state (input + recipe hashes) |
| `site/data/{sfs,dv}/artifact/` | persisted parse artifacts (the source of truth) |
| `python -m accommodanda.sfs` | `parse` / `validate` / `refs` diagnostic CLI |
| `site/data/dv/identity-index.json` | canonical case → source records |
| `test/test_dv_identity.py`, `test_dv_parse.py` | DV suites |
| `test/test_lagrum.py` | citation test suite |
| `test/test_sfs_parse.py` | SFS structure + inline-link oracle suite |
| `test/test_sfs_register.py` | SFSR register/amendments/förarbeten/metadata suite |
| `accommodanda/sfs/download.py` | SFS harvester (beta raw-ES) + consolidation archiving |
| `test/test_sfs_download.py` | SFS downloader version/archiving suite |
| `test/files/` | hand-authored fixture corpora (oracle) |
| `lagen/nu/res/extra/sfs.ttl` | named-law dataset (live site data) |
| `site/data/dv/` | legacy DV feed (Word docs) |
| `site/data/domstol/` | new DV API harvest |

## Conventions (from CLAUDE.md)

Target Python 3.10+. Avoid fallback code — assert how the environment
should be. Don't catch exceptions you can't recover from. Imports at top,
grouped. DRY, small functions, no "just in case" complexity.

Run the new test suites by naming them explicitly —
`pytest test/test_lagrum.py test/test_sfs_parse.py test/test_sfs_register.py
test/test_dv_identity.py test/test_dv_parse.py test/test_dv_legacy.py
test/test_build.py test/test_sfs_download.py test/test_site.py
test/test_forarbete_download.py test/test_forarbete_parse.py`. A bare
`pytest test/` fails at
collection: `test/` is a package and the legacy `integration*.py` files
don't import under modern Python (pre-existing, out of scope).

---

## Progress log

**2026-06-15 (wiki value-add: kommentar + begrepp as sources)**
- Imported the hand-authored MediaWiki dump as two ordinary sources, restoring
  the per-paragraph **commentary side-by-side** (in the statute paragraph's
  margin, grouped "Kommentar") and the **concept/keyword glossary** (`begrepp/`
  pages that act as hubs). Shared `lib/wikitext.py` parses wikitext, merging
  `[[wikilinks]]`→concepts with the citation engine's law/case/förarbete links;
  `wiki/parse.py` builds kommentar artifacts (sections anchored to statute
  fragments) + begrepp artifacts. Wired through build/catalog/render exactly
  like the other sources — no new integration machinery, just two more
  artifact producers feeding the inbound graph. 212 + 565 pages; 5,808
  commentary→paragraph edges; 468 concepts with inbound. Inbound entries now
  link to the citing pinpoint. `test/test_wiki.py`. The architecture's payoff:
  manually-written value-add is just another source. (Follow-ups in §7c:
  case↔concept edges, inline commentary, taxonomy, the authoring layer.)

**2026-06-15 (sticky scrollspy table of contents — all document types)**
- Every document with ≥3 headings gets a sticky left-hand TOC, scrollspy-driven
  (the entry for the section at the top of the viewport highlights, and the TOC
  auto-scrolls to keep it visible). General across SFS / DV / förarbete via a
  `Toc` collector threaded through the renderer: each heading registers its text
  + level and gets a stable anchor id (existing node id, or a generated `secN`),
  so body anchors and TOC links agree by construction. SFS chapters enter the
  TOC through their title rubrik ("1 kap. Statsskickets grunder"), not a
  redundant bare "1 kap."; sub-rubriker nest by level. Layout reworked to a
  flex `.layout` (sticky `nav.toc` + `main`); TOC hidden under 64rem. Vanilla
  `scrollspy.js` asset (no deps, rAF-throttled scroll handler). `render_toc`,
  `Toc`, `plain()`, and the `SCROLLSPY` asset. Render-only (regenerate, no relate).

**2026-06-15 (render polish: list items, grouped + pinpointed inbound, tooltips)**
- **SFS list items now render.** A `stycke` carries both intro text *and* punkt
  children (`K8P7S1` → text + two punkt); `render_node` returned at the text
  branch, dropping the list. Now renders the punkt/lista children too
  (`<ol class="punkter">`) — numbered lists were invisible before.
- **Inbound panel grouped by source** (Författningar / Förarbeten / Rättsfall),
  each its own labelled section, instead of one mixed list.
- **Human-readable, pinpointed inbound labels.** `human_fragment` turns a minted
  fragment into a lawyer's pinpoint (`K2P16S5` → "2 kap. 16 § 5 st", `sid39` →
  "s. 39"); `describe_citer` shows the citing document's **name + pinpoint**
  ("Skollag (2010:800) 2 kap. 16 § 5 st") using the law title + the `from_anchor`
  already in `links`. Inbound is now keyed per (citer, pinpoint), so a law citing
  from several places shows each.
- **Outbound link tooltips.** New `fragments(uri, snippet)` catalog table
  (populated in `relate`: each id-bearing node → its text + list items, ≤220
  chars). `render_runs` adds a `title=` with the target paragraph's text, so
  hovering an SFS→SFS link previews where it points. `Site.snippet` caches lookups.
- Touched: `lib/catalog.py` (fragments table + snippet + richer `inbound`),
  `lib/render.py` (`human_fragment`/`describe_citer`/grouped panel/list fix/
  tooltips). Needs a re-relate (fragments) + regenerate.

**2026-06-14 (§7a förarbeten — PDF parser + the third leg goes live)**
- Built `accommodanda/forarbete/{model,parse}.py`. PDF text via poppler
  `pdftotext` (plain mode isolates header/page-number; `-layout` mashes them
  into the alternating margin). Flat Block model (rubrik/stycke + **page**);
  page = PDF index = printed page (modern PDFs number from the title page), so
  each block exposes its `#sid{N}` anchor — the page-precise förarbete citation
  target. Reflows wrapped lines, strips the running header *as a substring*
  (it bleeds mid-line in plain mode — the citation scanner was self-linking it),
  skips TOC pages, detects numbered headings. **URI minted to the citation form**
  (`prop/{riksmöte}:{no}`, `sou/{year}:{no}`, …) so doc and citation agree by
  construction. Body ref-scanned (same engine as DV) → inline links.
- **Wired through the whole derived layer**: `lagen forarbete parse` (Stage),
  `catalog.forarbete_document`, `render_forarbete` (förarbete page with `#sid{N}`
  anchors + per-page inbound margin notes), `doc_relpath` → `fa/` tree.
- **Validated end-to-end** on a 565-doc sample (prop/sou/ds/dir): parse 0
  failures; relate added 565 docs + 300k links; **the killer feature's third
  leg is live** — Regeringsformen (1974:152) §8:7 now shows **132 förarbete** in
  its paragraph inbound margin (alongside case law), and förarbete pages render
  prose + live citations + page anchors. The cross-source SFS→förarbete fill is
  modest only because the sample is recent förarbete; the full harvest resolves
  the bulk of the 51k+15k prop/sou citations.

**2026-06-14 (§7a förarbeten vertical — downloader)**
- Built `accommodanda/forarbete/download.py` from first principles off the live
  regeringen.se (the old `Regeringen` source targeted the pre-rebuild site).
  Harvests all **eight** types under `/rattsliga-dokument/`. Key discovery: the
  `?p=N` links are inert; the listing is paged by the page's own AJAX call
  `GET /Filter/GetFilteredItems?…&preFilteredCategories=<taxonomy-id>&page=N`
  returning `{"Message": <ul.list--block>, "TotalCount": N}` (captured the exact
  request from the browser network tab). Per-type taxonomy ids (prop 1329 =
  4,336 docs, sou 1331 = 3,158, dir 1327 = 2,432, ds/fm/skr/so/lr …).
  **basefile = the document's own identifier** (prop "2025/26:279", sou
  "2020:1") per the cross-source-identity requirement; SÖ/lagrådsremiss (untitled
  by number) fall back to the slug. Incremental + `--full`, atomic writes,
  browser UA (site 403s bots), politeness. Stores record json + landing html +
  content PDF(s). Validated end-to-end: PDFs are valid `%PDF`, identifiers clean,
  pagination advances. `test/test_forarbete_download.py`. Parser/model/URI
  minting + older-period sources (riksdagen/KB) still ⬜.

**2026-06-14 (§4 DV golden cross-check — references)**
- Built `tools/golden_dv.py` against the old pipeline's distilled RDF (15,858
  files, the frozen DV oracle). **96.8% old-reference recall** on 3,143 matched
  referat cases (77.8% exact, 84.7% find ≥ old). Independently **confirms the
  case-URI re-minting** (old RDF subject is `dom/rh/2009:37`, what the new
  pipeline now mints). Residual = editor-derived lagrum + new-fills-old-holes.
  See §4. (Metadata-field comparison still to add.)

**2026-06-14 (§6 follow-ups: EU/celex citations → EUR-Lex external links)**
- ~30k citations to EU acts (minted as `lagen.nu/ext/celex/{CELEX}`, the `ext/`
  = external-reference namespace) were rendering as dead `.noref` text. They
  now render as **external links to EUR-Lex** (`render.is_external` /
  `href` → `eur-lex.europa.eu/...?uri=CELEX:{n}`, styled with an ↗). The
  identifier is unchanged (constraint-safe) — only the rendered href resolves
  to the external service, since the site doesn't host EU acts. 1 new test.

**2026-06-14 (§6 follow-ups: inbound quality — exclude self-citations)**
- **41% of all edges (334,788) are self-citations** — a document referencing
  its own fragments (chapter-heading self-links "12 kap."→#K12, internal
  "enligt 3 §" cross-refs). Excluded these from inbound annotations + the
  most-cited rankings (`catalog._NOT_SELF`: `from_uri <> to_root`): the panel
  shows which *other* documents cite a target (the killer feature), not a
  document's own outbound links (navigable in place). Fixes the long tail —
  a paragraph cited only internally no longer shows a misleading "Hänvisat
  till av" panel of its own law's paragraphs. 1 new test.

**2026-06-14 (§6 follow-ups: navigation / browsability)**
- The 28k-page site had only a ~50-item frontpage and no way to find a specific
  doc. Added **browse pages** (`render_browse`): a complete sectioned index of
  every law (grouped by year, newest first) at `/sfs/` and every case (grouped
  by court/series from the uri) at `/dom/`, linked from the frontpage. Frontpage
  now shows totals + two ranked "most-hänvisade" columns (laws *and* cases, by
  inbound count — Miljöbalken/Rättegångsbalken/Brottsbalken lead). 2 new tests.

**2026-06-14 (§6 follow-ups: richer inbound + case-graph reconnection)**
- **Document-level inbound + dead-link gating** in the renderer. Inbound now
  shows at two granularities: per-paragraph margin annotation *and* a per-
  document panel (`document_inbound`) for citations to the law/case as a whole
  — surfacing the **27% of citations with no `#fragment`** (and all case
  inbound) that paragraph annotations never showed. A `Site` holds the set of
  known doc URIs so citations to docs we don't have render as muted `.noref`
  text, not 404 links (was 14% of links → broken hrefs).
- **Case-law citation graph reconnected** (the big one). Found 42,281 case→case
  edges pointing at URIs no document had: the DV vertical minted ad-hoc
  referat-slugs (`dom/AD_1993_nr_100`) while RATTSFALL citations mint the old
  rinfo scheme (`dom/ad/1993:100`). Per the **user constraint (published/
  internal URIs must not change from the old pipeline)**, fixed by minting the
  DV document URI through the *same RATTSFALL parser citations use*
  (`dv/parse.py::case_uri`) — byte-identical by construction, the old
  published id. All 17,393 referat cases parse, 0 fall back. Full DV re-parse →
  re-relate → re-generate: case→case edges **0 → 31,507 connected (75%)**,
  **7,083 cases now have inbound** (was 0; the unconnected 25% are genuine pre-
  1981 / notisfall absences). Verified: NJA 2011 s. 357 shows 38 citing cases.
  Non-referat cases (~7%, never citation targets) keep a slug URI pending a
  verified court→org-slug map for the old verdict scheme.

**2026-06-14 (§6 derived layer: relate + generate + the inbound-link site)**
- Built the corpus-wide derived layer end-to-end. **`relate`**
  (`accommodanda/lib/catalog.py`) builds a derived SQLite catalog — `documents`
  + `links` — from the artifacts via one generic edge-extraction walk
  (`collect_links`) that works for both sources because citations are inline
  and URIs already agree. **`generate`** (`accommodanda/lib/render.py`) renders
  a static, interlinked HTML site: a single type-keyed node renderer for both
  SFS structure and DV body; outbound links live; **inbound links annotated in
  the margin** (`Hänvisat till av`) from the catalog — the lagen.nu killer
  feature. Frontpage ranks laws by inbound count. New corpus-level CLI verbs
  `lagen all {relate,generate,serve}`, special-cased outside the per-doc Stage
  machinery — a doc's HTML has a *data-dependent* prerequisite set (its own
  artifact + its inbound set, read from the catalog), which the static
  `Stage.inputs(basefile)` protocol can't express; `generate` auto-runs
  `relate` when artifacts are newer than the catalog.
- Verified end-to-end on the partial corpus (283 laws / 8,374 cases, ~157k
  edges): case **AD 2024 nr 95** → cites **2018:585 §K2P4/§K3P1** → those
  paragraphs show the case in their margin → click back. **2,037 cases cite
  räntelagen § 6.** Screenshotted law/case/frontpage. `test/test_site.py`.
- DV decisions made earlier in the session: **DV references now inline**
  (matching SFS) — each body block's `text` is a runs list, top-level
  `references` dropped; shared `lagrum.interleave(text, refs)` splices runs
  (SFS `Projection.inline` refactored onto it). Predicate `dcterms:references`
  (SFS uses it uniformly; `rinfoex:lagrum` abandoned when SFS went inline).
- Open: relate/generate are full rebuilds (no incremental freshness yet); ES
  deferred; once the full SFS parse lands, re-relate+generate fills the
  currently-unrenderable cited law-roots (147/11,133).

**2026-06-14 (inline links / runs-spans in the artifact)**
- The parsed artifact now carries **discovered links inline** instead of a
  flat `references` list: every NF text node is a LIST of `str` runs +
  `{predicate,uri,text}` link objects at exact positions; top-level
  `references` dropped. Per-link sub-spans recovered by threading each
  emit's originating-node token span through every LAGRUM+EU `fmt_*` in
  `lagrum.py` (+ a trailing-marker absorption pass), reproducing the
  fixtures' link boundaries (change-note whole-span, anonymous-law bare
  number, named-law name+number, combined-external-ref law swallow).
- User decisions: text is **always** a list (even `["plain"]`); **all** text
  nodes scanned incl. headings/upphävd/top-level tables (deliberate
  divergence — produces self-links like a heading's "12 kap."→#K12).
- `nf.inline_references()` reconstructs the old `(source,predicate,uri)`
  tuples from inline links (excluding the newly-scanned kinds) so the golden
  refs oracle keeps working (`sfs refs`/`validate`): 2018:585 = 219/222, **0
  extra** (3 missing = golden newer than the downloaded HTML). New
  `test_sfs_links` oracle (set-equality vs fixture `<LinkSubject>`, URI
  base-normalized). `golden_sfs.canonicalize_node_texts` folds lists→string.
  Touched: `accommodanda/lib/lagrum.py`, `accommodanda/sfs/nf.py`,
  `accommodanda/sfs/__main__.py`, `tools/golden_sfs.py`, `test/test_sfs_parse.py`.
 

**2026-06-14 (download UX: harvest routing, progress, politeness)**
- **Bare `lagen <src> download` now runs the full bulk harvest** (`Source.harvest`),
  not a `list_basefiles()` loop — discovery of *new* docs is impossible
  otherwise. Per-doc targeted fetch stays for explicit basefiles.
- Added driver **progress logging** (`run_action` → throttled `\r` counter on
  stderr) so multi-thousand-doc runs aren't silent, and a **politeness delay**
  on per-doc fetches.
- `lagen dv download` now **auto-rebuilds the identity index** after the harvest
  (`identity.reindex`, refactored out of its `main`), gated on records having
  changed — one global pass, no parsing needed.

**2026-06-14 (download stages)**
- Wired per-document `download` stages into the build driver for both sources
  (`lagen sfs download [basefile]`, `lagen dv download [basefile]`) — the old
  `download_single` shape (optional basefile), not bulk. SFS fetches one act's
  `_source` (`fetch_one` by beteckning) + archives; DV re-fetches one record
  (`fetch_record` by the index's uuid, `GET /publiceringar/{id}`). inputs/code
  empty → on-disk = fresh until `--force`. Independent of `parse` (preserves
  the JSON-or-HTML fallback). New-doc *discovery* stays in the bulk harvesters
  (the old `download_new`). Verified end-to-end on 2018:585 (SFS) and a read-
  only API check (DV).

**2026-06-14 (SFS downloader + JSON parse path)**
- Discovered the new beta database exposes an **open raw-Elasticsearch
  passthrough** (`/elasticsearch/SearchEsByRawJson`) returning the whole
  consolidated act (text+register+amendments) as one JSON `_source`. Built
  `accommodanda/sfs/download.py`: `search_after` enumeration (13,789 acts),
  incremental/`--full`, **consolidation archiving** keyed on
  `fulltext.andringInford` (recreates the old get_archive_version/archive
  machinery; adds the `archive/` tree). `test/test_sfs_download.py`.
- Wired **JSON-or-HTML source selection** (`sfs.load_inputs`, DV single-best-
  source pattern): `register_from_source`/`sfst_header_from_source` map the
  JSON onto the same intermediates the HTML parsers emit, reusing all
  downstream code. Verified 0 field-diffs HTML-vs-JSON on 2018:585 (deltas are
  pure freshness). Build `parse` now emits `metadata` for both paths. Fixed
  `SFS_CODE`/`DV_CODE` recipe-hash paths (stale pre-reorg locations → no-op).
 

**2026-06-14 (SFSR register / förarbeten / metadata)**
- SFSR **amendments** section: `accommodanda/sfs/register.py` + `nf.py` join,
  **97.5%** corpus match (10,775/11,056). Property mapping, Omfattning → the
  `L*` change tuples, övergångsbestämmelse content joined with `L`-ids.
- **Förarbeten** wired in once the FORARBETEN grammar landed
  (`parse_forarbeten`): **99%** per-entry on a 120-doc sample; comparison
  un-deferred.
- **Document-level metadata** built (`parse_sfst_header` + `build_metadata`):
  the konsolidering envelope (identifier, konsoliderar, konsolideringsunderlag,
  the `/konsolidering/<cutoff>` URI) + descriptive fields. Climbed 70% → 90% →
  **94.8%** fixing real bugs as diffs surfaced (underlag = *all* change acts
  not cutoff-filtered; identifier `s. ` spacing; SFST-header departement is
  authoritative; sub-org suffix + run-date fields canonicalized away).
- All three residuals are the same **stale-golden drift** (consolidation
  cutoff + department move as new amendments land) → motivates the
  adjudication overlay (§3d). `test/test_sfs_register.py`.

**2026-06-14 (later)**
- Incremental build driver `accommodanda/build.py` + the `lagen <source>
  <action> [basefile...]` CLI (console script `lagen`, or `python -m
  accommodanda.build`). Generic driver over a per-source `Stage` protocol;
  content-hash + recipe-version freshness (manifest in `.build/`); implicit
  deps with `--no-deps`; `--force`, `-j`, `--dry-run`, `status`. **`parse`
  wired for SFS + DV — artifacts now land on disk** (`site/data/{sfs,dv}/
  artifact/`), the first persisted artifact corpus. `relate`/`generate`
  slot in next as the DB+HTML slice. `test/test_build.py`.
  Design decisions taken with the user: source-first ordering,
  content-hash over mtime, implicit upstream builds. `relate` confirmed
  **per-document** (upsert into a shared catalog; inbound links are a
  query at generate time), not a corpus-level batch.

**2026-06-14**
- Legacy DV Word path built on **Apache POI via jpype** (OpenJDK 21, POI
  5.4.1 vendored), reading the *original* binary `.doc` (HWPF) and `.docx`
  (XWPF) rather than the antiword DocBook intermediate — recovers bold
  runs and table-cell structure antiword flattened. `dv_word.py`
  (extraction) + `dv_legacy.py` (head/body split → `Avgorande`, identity
  index supplies canonical referat/court). **15,624 docs, 0 failures.**
  `test/test_dv_legacy.py`.
- Field-level merge investigated against the data and **rejected**:
  body-fallback opportunity is literally 0, other gaps marginal →
  single-best-source-per-case, not merge.
- Notisfall (852 sole-source cases) deferred per decision; sources
  characterized (frozen `<body>` for the pre-2010 bulk, `notiser_*.zip`
  `.docx` POI-able for ~342 recent, needs `parse_not`-style splitting).
- **SFSR register / amendments section built** (`accommodanda/sfs/register.py` +
  `nf.py` join, `test/test_sfs_register.py`). Parses the downloaded
  register page → one amendment entry per change act with post-polish
  properties (departement→org-URI via an rdflib `swedishlegalsource.ttl`
  resolver, CELEX, dates), Omfattning → the `L*` `ersatter`/`upphaver`/
  `inforsI` change tuples (resolved against the base law), and the dropped
  övergångsbestämmelse blocks projected + joined as `content` with
  `L{sfsnr}` ids. `validate --sections amendments` + `diff_amendments`.
  **Corpus match 97.5% (10,775/11,056).** Diffs: ~128 extra amendments =
  change acts postdating the golden freeze (new-is-right), ~69 base-OB
  `L`-id suppression-rule gaps, rest mixed Omfattning defects.
- Förarbeten (per-amendment) deferred to the FORARBETEN grammar; document-
  level `metadata` (konsolidering envelope) left for the consolidation step.
- Adjudication overlay decision recorded: per-class predicates, first rule =
  accept post-freeze extra amendments.

**2026-06-13 (later still)**
- Ported the remaining four old-engine grammars as configurable parse
  types: RATTSFALL (`dom/…` case-law URIs), FORARBETEN (prop/SOU/Ds/bet/
  rskr/celex with page-lists, "a. prop." state and avsnitt/committee
  context), EURATTSFALL (CJEU → celex), MYNDIGHETSBESLUT (JO/JK/ARN with
  JK date-disambiguation), plus ENKLALAGRUM (absolute-only SFS subset).
  Each validated against its `test/files/legalref/` fixture corpus
  (EURATTSFALL against a hand-authored table — the ECJ fixtures are
  broken/encoding-mangled). Refined the framework so roots come from the
  requested types but rules/triggers from the dependency-expanded set.
  `dv_parse` now scans court-decision bodies with all seven grammars.
  All eight old-engine parse types are now ported; the four never
  implemented in the old engine (FORESKRIFTER, INTL*, DOMSTOLSAVGORANDEN)
  stay deferred.

**2026-06-13 (later)**
- KORTLAGRUM ported into `accommodanda/lib/lagrum.py` (abbreviated SFS refs);
  `load_abbreviations` reads the 110 `dcterms:alternative` entries. Wired
  into `dv_parse.extract_references`; `references` now populated (was an
  empty stub). `Short` fixtures added to `test/test_lagrum.py`.
  Corpus check: 81.2% `lagrumLista` recall on a 500-case sample.
- **Citation engine made parse-type configurable** (the old
  `LegalRef(*parse_types)` idea): `LagrumParser(parse_types=…)` composes
  grammar + roots + trigger from only the requested types via the
  `ROOTS`/`RULES`/`TRIGGER_SRC`/`DEPENDS` tables. Behaviour-preserving
  refactor (SFS still {LAGRUM, EULAGSTIFTNING}); a new grammar is now a
  table entry + formatter. Old engine implements only 8 of its 12 declared
  types; 3 now ported, 5 remain, 4 were never implemented (see §5).

**2026-06-13**
- SFS parse fixtures wired in as a second, oracle-grade regression layer:
  `test/test_sfs_parse.py` over `test/files/sfs/parse/`;
  added `suppress_temporal` to `nf.to_normalform`; promoted 3 fixtures the
  old parser failed.
- DV downloader run to completion — full 17,254-record harvest into
  `site/data/domstol/` (moved from an initial wrong path; fixed a crash on
  an attachment lacking `fillagringId`).
- DV identity indexer built (`dv_identity.py`,): 18,728 canonical
  cases, 14,838 linked, audited for under/over-linking. Compared against
  the old `CompositeRepository` (assumed identity + winner-takes-all) —
  the indexer *manufactures* identity and keeps all sources for a
  field-level merge.
- DV parser started (`dv_model.py` + `dv/parse.py`,): API
  `innehall` path parses all 17,090 API-backed cases with zero failures
  and zero content dropped.
- Coverage analysis: characterized + verified the 1,638 legacy-only cases
  (HD notisfall, AD 2006–2017 referat, non-referat Svea hovrätt) →
  reprioritized the legacy-OOXML path as the next increment.

**Earlier**
- Phase 0 golden corpus + comparator; SFS structural parser (98.7%);
  Lark citation engine (86.7% structure+refs, `test/test_lagrum.py`).
