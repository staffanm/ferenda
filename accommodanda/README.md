# accommodanda — developer setup

The rebuilt ferenda pipeline: vertical source pipelines (sfs, dv, hudoc, coe,
icrc, untc, icc, eurlex, forarbete, foreskrift, avg, remisser, wiki, site) that go from downloaded (or,
for wiki/site, hand-authored) source files to a typed document model and a JSON
artifact, with the citation engine as a shared library. For *why* it's
shaped this way and what's done vs. pending, read
[`../REWRITE.md`](../REWRITE.md); this file is just how to get it running.

## Prerequisites

- **Python 3.10+** and **[uv](https://docs.astral.sh/uv/)**. `uv sync`
  installs everything in `pyproject.toml` (incl. `jpype1`).
- **A JVM — only for the legacy DV Word path** (`dv_word.py` reads binary
  `.doc`/`.docx` through Apache POI via jpype). Everything else (SFS, the
  citation engine, the DV API path) is pure Python and needs no Java.

  On Ubuntu 24.04:

  ```sh
  sudo apt-get install -y openjdk-21-jdk-headless
  ```

  jpype auto-discovers `libjvm.so`; you normally don't need `JAVA_HOME`.
  The `-headless` package is enough — POI's HWPF/XWPF reading needs no AWT.

- **The POI jar stack** (not committed — `vendor/poi/*.jar` is gitignored).
  Fetch once after checkout:

  ```sh
  ./tools/fetch_poi.sh
  ```

  Idempotent; pulls POI 5.4.1 + runtime deps from Maven Central into
  `vendor/poi/`.

## Quick start

```sh
uv sync                      # Python deps
./tools/fetch_poi.sh         # POI jars (legacy DV only)
uv run python -m pytest      # bare pytest collects exactly the new suites
```

> `[tool.pytest.ini_options]` in pyproject.toml scopes collection to
> `test/test_*.py` (minus the `test/files/` fixture tree), so the legacy
> unittest files (`integration*.py`, `test[A-Z]*.py`, …) that don't import
> under modern Python are never touched. Name individual suites as usual
> to run a subset.

## Module map

**SFS vertical**
| File | What |
|---|---|
| `download.py` | harvester for consolidated SFS off the beta rkrattsbaser ES passthrough (one request per document; the register + amendment list come in the same `_source`) |
| `extract.py` | body extraction from rkrattsbaser HTML (+ archival `<pre>`) |
| `reader.py` | `TextReader` — faithful port incl. autostrip blank-line semantics |
| `tokenizer.py` | recognizers → flat event stream |
| `assembler.py` | RANK-driven stack machine → document tree |
| `model.py` | typed dataclasses (`Forfattning`, `Kapitel`, `Paragraf`, …) |
| `parallelappendix.py` | parses a statute whose sole `Bilaga` is a convention printed in two or three language copies into an aligned `Konventionsbilaga`, with **no per-law knowledge**. Article structure locates blocks, `langdetect` labels each whole block, and strict instrument/article alignment rejects inconsistent sources while permitting compatible division headings to be omitted in one language. Wired into `_assemble`; non-parallel and misaligned sources flat-parse. Handles 95/107 structurally detected corpus candidates (89%), including ECHR, Montreal, the tax-exchange family, CRC and directive-wrapped ATMF; the five deliberate parallel fallbacks are three duplicated article sequences and two multi-treaty COTIF bundles. Each instrument keeps its title and preamble as ingress and a protocol number; the projection anchors it as `#B1`/`#B1P4` and resolves the treaty it reproduces through the curated `data/incorporates.json` (`{sfs}#{fragment}` → `source/number`, eg. `coe/046`) so its articles link to `ext/coe/NNN`. See `parallelappendix.md` |
| `nf.py` | tree → golden normal form (replicates old URI-minting quirks) |
| `register.py` | SFSR register page → amendments + change tuples; `resource_map`/`lookup_resource` resolve org/series labels via the ported `data/resources.json` dataset |
| `versions.py` | archived consolidations (download archive, three raw generations) → per-version artifacts + `.versions.json` sidecar |
| `begrepp.py` | `find_definitions` — begreppsdefinition heuristics (paragraf mode + defined-term cases) → `dcterms:subject` links |
| `graphics.py` | recovers content omitted by the text-only SFST source. Detection is deterministic and runs at parse time: the slash-delimited and plain `... är inte med här` corpus forms plus otherwise unmarked road-sign cells in 2007:90 become typed `grafik` nodes. Each node carries a stable semantic `key`, hashed from structural path + kind/code + normalized anchor + occurrence within its container; transient `G1` ids remain diagnostic only. Localization resolves provenance (variant-aware: a pending, not-yet-in-force copy of a bilaga gets its own keys and its own source PDF), deduplicates content duplicates by key, strictly validates complete vision output and writes `.graphics` entries keyed by that semantic key with the unhashed identity alongside; wired as `lagen sfs ai-includegraphics` |
| `pdfmirror.py` | official published-SFS PDF mirror, the crop source for graphic localization. Each act's source follows from its SFS number: `1998:306`–`2018:159` from direct rkrattsdb URLs, `2018:160`– from svenskforfattningssamling.se document pages, and nothing before `1998:306` (print only). Fetched bytes must be PDFs. `.mirror.json` records the acts an upstream answered it has no PDF for, which is the only thing telling those apart from "not fetched yet" and so the only thing keeping a rerun free. Runs as part of `lagen sfs download` and as `lagen sfs mirror-pdf`, not as a parse stage |
| `correspond.py` | the old-law → new-law paragraf correspondence map for a restructured statute, three routes into the same `.corr` payload: an LLM pass over the proposition's författningskommentar (`lagen sfs ai-correspond`), and the mechanical `table_correspond` over the prop's own jämförelsetabell bilagor (`lagen sfs table-correspond <new> <prop> [<old>[=TAG] ...]`, rows extracted by `forarbete/jamforelse.py`; several old laws — SFB's 23, SFL's 3 — merge into one layer, `=TAG` names an old law's prop-local shorthand so tagged cell references resolve against the right law) — every edge validated against both laws' paragraf inventories either way; plus the *same-law* renumbering route (`lagen sfs renumber-correspond <sfs>`), reading the register's "nuvarande … betecknas …" omfattning clauses into `betecknas` edges carrying the amendment's ikrafttradandedatum, which generate uses to split inbound references temporally ("Hänvisningar till tidigare beteckning 4 kap. 4 §" on RF 4 kap. 6 §) |
| `asgit.py` | `lagen sfs history-as-git <repodir> [basefile...]` — export the corpus as a git repo (one file per statute, one commit per amendment event grouped by proposition, authored by the prop's signers/committed by the rskr's, ingress as commit body); a per-transition hash ledger admits only strict append-only updates, while `--rebuild-history` atomically recreates corrected/backfilled history; implements `docs/prd-sfs-history-as-git.md` |
| `_validate.py` | worker functions for `lagen sfs validate`, in an importable module so `ProcessPoolExecutor` workers can resolve them under `python -m` |
| `__main__.py` | `parse` / `refs` / `validate` CLI |

**Shared library (`lib/`)** — a source may import from here; `lib` never imports from, or branches on, a source.
| File | What |
|---|---|
| `lagrum.py` | Lark/Earley engine; `LagrumParser(parse_types=…)` composes a grammar from LAGRUM / KORTLAGRUM / EULAGSTIFTNING / RATTSFALL / FORARBETEN / … |
| `casenaming.py` | court-decision identity — `case_uri` (mint a case's canonical URI via the RATTSFALL parser) + `case_label`/`lopnummer` (referat identity + HD's given names); read identically by dv's parse-time label stamp, the catalog row and the page heading |
| `eucasenaming.py` | the EU mirror of `casenaming.py` — `case_number` (CELEX → court case number, "62018CJ0311" → "C-311/18", also T-/F- courts), `given_name`/`case_name`/`case_citation` (curated usual name, page heading, "C-311/18 (Schrems II)" inbound-citation label) from the shipped `eurlex/data/casenames.json` snapshot; read identically by eurlex's parse-time label stamp, the catalog row and the page heading |
| `coe.py` | shared Council of Europe identity grammar: ETS/CETS number → `ext/coe/{number}`, article/subarticle fragments, and HUDOC's `8` / `6-3-d` / `P7-4` facet codes → the same treaty provision URIs produced by the Treaty Office vertical |
| `coe_ids.py` | dependency-free CoE article-fragment grammar (`article_fragment`) factored out of `coe.py` so `lib.lagrum` can use it without closing the `lagrum → coe → catalog → markdown → lagrum` import cycle; also used by `sfs/nf.py` |
| `eu_structure.py` | the one EU-act sub-article anchor grammar (`anchored_blocks`/`subarticle_key`/`flatten`), shared by the eurlex parser, the renderer and the wiki guidance layer (`nest`, the parse-time tree builder, stays in `eurlex/structure.py`) |
| `legacy_import.py` | shared §7g frozen-import core — `should_write` precedence (live-wins / own-import-idempotent-unless-force / optional `better()` tie-break), `rel` (in-place LEGACY_ROOT-relative body references), `iter_entries`/`docdir`/`read_record` walk primitives; used by `forarbete/legacy.py`, `avg/legacy.py` |
| `regeringen.py` | shared regeringen.se harvest knowledge — the doctype table (`TYPES`: url segment, taxonomy category id, identifier regex) and the `ul.list--block` listing walk (`listing_items`); used by `forarbete/download.py` and `remisser/download.py` |
| `harvest.py` | shared incremental-download core — `HarvestWatermark` (begin/complete lifecycle, never-regress date save, crash-safe `dirty` flag that disables the consecutive-hit stop but not the date-conclusive one) + `walk`/`Skip`/`ItemKey`/`guarded_enumerate` (the newest-first download loop over an enumerate/resolve pair); each source states its own `lookahead_limit`/`safety_days` window (dv: 365-day safety window, ~5000-item lookahead; forarbete/riksdagen/foreskrift/avg-jo: 14 days/20 items); used by `dv/download.py`, `foreskrift/harvest.py`, `avg/download.py` (jo), and directly by `forarbete/download.py` + `forarbete/riksdagen.py` (also driving `forarbete/rskr.py`) |
| `browser.py` | detached headful-Chrome transport for F5/Shape-protected public sources — navigate with no Playwright/CDP client attached, wait the source-configured settle interval, then attach briefly to read the completed DOM or exact browser-cached PDF bytes; selected only by the SKVFS and MTFS `Agency.browser` configs |
| `catalog.py` | the SQLite catalog (`documents`/`links`/`fragments`/`genomforande`/`fk_kommentar`/`correspondence`/concept tables) built by `relate`, derived and rebuildable — `correspondence` holds the `.corr` edges incl. `ikrafttrader` (when a same-law renumbering took effect, driving the temporal inbound split); `documents.path` is stored `data_root`-relative so the catalog is portable across hosts; `connect_ro`/`load_artifact`/`artifact_updated` are the shared read-only serving-layer helpers used by both REST and MCP |
| `render.py` | the `generate` phase — a single generic node walk renders every source's artifact to static, interlinked HTML (live outbound links, an inbound-context rail collected from the catalog); also renders the full ⌘K-backed search page (`render_search_page`, `fullsearch.js`) served at `/sok` |
| `assets/` | the browser-facing static chrome as real on-disk files (`style.css`, `editor.css`, `dom.js`, `scrollspy.js`, `search.js`, `popover.js`, `fullsearch.js`, `versions.js`, `faksimil.js`, `drawers.js`, `editor.js`, `robots.txt`) — formerly embedded string constants in `render.py`. `render.write_assets` ships them through the same Brotli precompression as pages: the JS files are concatenated in load order into a single **`script.js`** bundle (the page links one URL, so adding a module changes only the bundle, never the per-page HTML — it publishes via `generate --assets-only`, not a full regenerate), `style.css` is written with `editor.css` appended, `robots.txt` copied as-is. `dom.js` (first in the bundle) is the scripts' shared vocabulary (`window.lagenDom`: own-document anchor resolution across split-view panes, id-attribute selector, landing flash, JSON-island parse); `search.js` leads the ⌘K palette with instant *local* hits (a terse pinpoint — `4`, `11:2`, `art 5`, `(42`, `skäl 42`, `bilaga III` — resolved against the current page's own anchors, no network); `popover.js` gives every internal reference a hover preview of the target's rendered text (replacing the old title-attribute tooltip) whose ↗ escalates to a split reading view — the target document in its own pane with its own TOC/rail and scrollspy instance (`lagenScrollspy`), resizable/reorderable/closable |
| `feeds.py` | legacy dataset-alias map (`sfs`/`dv`/`forarbeten`/`myndfs`/`myndprax`/`keyword`/`eurlex` → the rebuilt source names) + pure Atom/HTML feed renderer, shared by static `/dataset/<alias>/feed[.atom]` generation and the live query-param endpoints in `api/app.py` |
| `dump.py` | NDJSON bulk corpus export (REWRITE.md §6) — one gzipped, self-contained JSON line per artifact, no transformation |
| `search.py` | full-text search over the parsed corpus on OpenSearch 2.x — standalone per-unit documents collapsed by `doc_uri`, no parent-child join; facet buckets (source/kind/year) via `post_filter` aggregations, prefix-matching queries (`prefix_query`), an `INDEX_FORMAT` version folded into each unit's stored freshness key so an index-schema change (like adding the year facet) reindexes on the next incremental pass without a blanket `--force` |
| `facets.py` | faceted navigation over the catalog — `tree`/`group`, the single source shared by the REST API (`/facets`) and the static browse pages; `document_year` (a `year` search facet, reusing browse's own per-source year extraction) is shared with the indexer |
| `pins.py` | citation-shaped query → search-hit-shaped resolved targets (`resolved_results`/`merge_pinned`), shared by the REST `/search` endpoint and the MCP `search`/`resolve_citation` tools |
| `resolve.py` | turns a ⌘K query into a precise, fragment-deep resource target — three resolvers (SFS/EU-act/case nicknames + citation-engine pinpoints) over `lib.datasets` |
| `layout.py` | single source of truth for where a `(source, basefile)` document lives, on disk and on the web (`downloaded`/`artifact`/`page_relpath`/`page_url`) |
| `datasets.py` | canonical filesystem paths of the curated named-resource datasets (`NAMEDLAWS`/`NAMEDACTS`/`NAMEDCASES`/`NAMEDEUCASES`/`COE_NAMES`/`ICRC_NAMES`/`UNTC_TREATIES`/`ICC_DECISION_TYPES`) that ship in the package tree |
| `concepts.py` | begrepp (concept) normalization — a hand-rolled, corpus-aware Swedish de-inflector collapsing inflected term forms onto one canonical `begrepp/<Name>`, plus the hand-edited override file `data/begrepp_aliases.json` |
| `diff.py` | the "jämför lydelser" version-diff view — block-align + word-level `<ins>`/`<del>` over two parsed artifact versions, computed on demand |
| `history.py` | read layer over the SFS version-history sidecar + amendment-register join, shared by the renderer's compare panel and `/api/v1/document/versions` |
| `text.py` | one definition of "the plain text behind an artifact's inline-run structure", shared by the catalog (tooltip snippets), search indexing and the bulk dumps |
| `compress.py` | transparent Brotli-only compression for `artifact/`/`generated/`/`downloaded/`, written atomically via `util.write_atomic`; the `downloaded/` tree skips already-compressed payloads (`INCOMPRESSIBLE_SUFFIXES` — PDF/zip/docx/images/…) and sub-512-byte files, storing them plain, and hosts the compress-aware `glob`/`list_basefiles` used by downloaders and parsers walking that tree |
| `facsimile.py` | on-demand page facsimiles: one source-PDF page → a retina PNG (`pdftoppm`, 150 DPI), rendered lazily into the `cache/facsimile/` disk cache (evicted externally); `render_region`/`cached_region` crop a bbox of a page instead of rendering the whole thing (the SFS graphic-crop path), `page_count` bounds a PDF's pages; served by the API's `/api/v1/facsimile` (+ the legacy `/prop/2022/23:10/sid1.png` path grammar) and `/api/v1/sfs-graphic`, and toggled inline by the page-number buttons on förarbete pages |
| `pdftext.py` | shared font-aware PDF text extraction pipeline for the PDF-bodied verticals — `pdf_pages`/`flat_lines` (poppler `pdftohtml -xml`, `hidden=True` recovers an OCR text layer pdftohtml otherwise drops) → `page_paragraphs` → a vertical's own `classify` |
| `llm.py` | shared client for the OpenAI-compatible chat-completions endpoint used by the opt-in `ai-*` passes — Berget by default, or any compatible server `llm_base_url` points at (e.g. a local llama.cpp, `docs/local-llm.md`) (eurlex/wiki annotate, remisser ai-analyze, sfs ai-includegraphics) — `complete`/`complete_thread` plus `author`, the source-agnostic validate/self-repair-retry loop; `images=`/`vision_content` add vision-model support (page images alongside the prompt), used by `sfs.graphics.localize_group` |
| `annstore.py` | the curated store for authored layers (`.ann`/`.corr`/`.graphics` files from the `ai-*` actions and the mechanical `sfs table-correspond`) — `WIKI_ROOT/ann/<source-dir>/<relpath>`, mirroring the artifact tree's relpath grammar; every layer is an envelope (`meta`: status generated/verified, model, generated date, input sha256 hashes) beside the payload's own keys; `guard` refuses to regenerate a `verified` layer without `--force`, `drifted` derives staleness from recorded input hashes rather than storing it; `meta_extra` merges source-specific envelope fields (the `.graphics` layer's `through` provenance horizon). Per-entry curation (a `"verified": true` flag on one `.graphics` gap) is the source's concern — `sfs.graphics.plan_localization` keeps a verified entry only while its source still matches the resolved provenance and hands `write` the final payload, so `write` stays a blunt writer; inventoried by `lagen ann status` |
| `markdown.py` | parse the git-backed wiki markdown (commentary/concept/site) into the shared inline-run artifact shape — the markdown counterpart of `wikitext.py` |
| `wikitext.py` | parse MediaWiki dump pages into the same inline-run shape; retired from the live pipeline, kept only as the migration/diff tools' reference |
| `runlog.py` | run instrumentation behind the ops dashboard — `runs.ndjson`/`errors.json`/`status.json` under `DATA/.build/` |
| `net.py` | shared HTTP session setup + a resilient `request()` helper for the source downloaders (transport-level retry, Retry-After, throttle logging, riding out failures from both the `requests` and `httpx` transports); `mount_legacy_tls` accepts a legacy small-DH-key TLS handshake for one host prefix only (`conventions-ws.coe.int`); `make_http2_session` (`httpx2[http2]`) is an HTTP/2-only fallback for hosts that refuse HTTP/1.1 behind a Cloudflare front (foreskrift's kkvfs) |
| `patch.py` / `patchit.py` | the source-file patch layer (apply-at-parse) and its interactive authoring CLI — see "Patch files" below |
| `git.py` | the one place that shells out to the git CLI — the inline editor's commit engine, the MediaWiki history importer and the `history-as-git` export |
| `errors.py` | `SkipDocument` — the shared control-flow signal a source's extractor raises for an expired/removed/empty document |
| `util.py` | small shared utilities ported from `ferenda.util`, incl. `write_atomic` (same-directory temp file + rename, per-process temp name so concurrent `lagen` invocations can't race each other's rename) |

**DV vertical (court decisions)**
| File | What |
|---|---|
| `download.py` | downloader for the rättspraxis API |
| `identity.py` | entity-resolution index (one canonical case ← many source records) |
| `model.py` | `Avgorande` model (metadata + ordered Rubrik/Stycke body + footnotes) |
| `parse.py` | **API path** — body from `innehall` HTML, metadata from curated fields |
| `structure.py` | instance/ruling segmenter (delmål → instans → betänkande/dom → domskäl/domslut) |
| `namedcases.py` | harvester for HD's named-precedent list (`data/namedcases.json`) |
| `word.py` | **legacy path** — POI (HWPF/XWPF) → flat `(text, bold, in_table)` stream |
| `legacy.py` | legacy stream → head/body split → `Avgorande` |

**forarbete vertical (preparatory works — prop/sou/ds/dir)**
| File | What |
|---|---|
| `download.py` | regeringen.se harvester (`lagen forarbete download [prop\|sou\|…]`); basefile = the document's own identifier; a `source`-carrying import record is treated as absent so live always wins; `pm` (promemorior outside the Ds series, category 1325 shared with `ds`) keys by diarienummer when the listing shows one, else the landing-page slug |
| `model.py` / `structure.py` / `parse.py` | `Forarbete` model, PDF (font-aware `pdftohtml`, or `pdftotext` fallback for OCR-layer scans) / html → nested structure → citation-scanned artifact; `_legacy_body` prefers a re-OCR sidecar at `layout.fa_ocr_pdf`. Font size gates heading detection (footnotes → `fotnot` blocks, body-sized "N Title" patterns stay stycken) and wrapped multi-line headings fold in `lib/pdftext`. `parse.tag_frontmatter` (prop/skr) retags the un-bold överlämnande page: the "huvudsakliga innehåll" heading becomes a rubrik (so the ingress gets its own avsnitt) and post-signature names become `signatur` blocks; `structure.signers`/`structure.ingress` read them back for `sfs/asgit.py` |
| `jamforelse.py` | extracts a re-enacting prop's provision-mapping tables (titled *Jämförelsetabell*, *Jämförelse mellan …*, *Paragrafnyckel* or *Paragrafregister*) (old↔new provision tables, often in a bilaga volume the artifact parse never reads) from per-run coordinates: a bilaga region is bounded by the "Bilaga N" page-margin marker, a body-chapter table (PBL) by its repeated header pair; each page's columns re-derived by clustering cell starts, headerless/merged-run headers tolerated, per-law sections of a multi-law register split into sibling tables; consumed by `sfs/correspond.table_correspond` |
| `lydelse.py` | reconstructs the two-column *nuvarande/föreslagen lydelse* comparison tables from per-run coordinates: the italic header gives the column boundary, cell lines reflow per column and pair into aligned rows (`tabell` blocks, the SFS `rad`/`cells` shape); page-centered "2 kap."/"28 §" markers come back as kapitel/paragraf blocks |
| `legacy.py` | one-time import of the nine frozen förarbete corpora (`lagen forarbete import-legacy <corpus>`, §7g) — shared precedence core; regeringen-era + KB corpora entries-driven, the TRIPS family (proptrips/dirtrips/dirasp) walked downloaded-first (path-derived basefile, ~half their entries are null) |
| `legacy_formats.py` | frozen body adapters — dokumentstatus XML, riksdagen text/tml + skanning2007 html, ABBYY OCR-XML (`abbyy_pages`), scanned-PDF OCR text (`scanned_pdf_pages`), TRIPS `div.body-text` (`trips_paras`) |
| `riksdagen.py` | doctype-agnostic data.riksdagen.se dokumentlista harvest engine (`harvest`/`_walk`, riksmöte-sliced backfill, watermark lifecycle); driven with the `bet` (utskottsbetänkanden, the prop→enacted-law link) specifics — PDF-only bodies (printed page = citation anchor), basefile `"<rm>:<beteckning>"` matching the FORARBETEN grammar's bet URIs, the planned/published upgrade cycle; full backfill walks all 161 riksmöten (the API caps one query's pagination at ~10k docs); no frozen legacy corpus |
| `rskr.py` | second driver over `riksdagen.py`'s engine, for riksdagsskrivelser (`rskr`, the chamber's decision letter to the government — the prop→bet→rskr chain's last hop); basefile `"<rm>:<beteckning>"`; body is the API's own small HTML rendering (`dokument_url_html`), not a PDF filbilaga (an rskr is a few boilerplate sentences ending in the talman's/tjänsteman's signature — the committer identity `sfs/asgit.py` mines); every feed entry is published and final, so no planned/published upgrade cycle |
| `kommentar.py` / `genomforande.py` | författningskommentar → `implements` (EU directive article) edges; extracted from `prop` and `fm` (förordningsmotiv) documents — both accompany the final enacted text, unlike a lagrådsremiss/SOU/Ds; `fk_section` also slices out the per-law FK prose consumed by `sfs/correspond.py` (reading a proposition artifact stays förarbete's job) |
| `fk.py` | per-paragraf författningskommentar text extractor: slices a prop's FK chapter into `{law, chapter, paragrafer, lagtext, kommentar}` entries across the three FK styles (lagtext quoted / bare marker / marker inline), with content-based span bounds and marker/heading recovery rules locked to the curated corpus. parse stores the entries as the artifact's `kommentarer` section and stamps commentary blocks `fk: <entry-no>` (the prop page wraps each entry's run in an `.fk-komm` highlight box); `resolve` pins entries to statute anchors at relate time (`fk_kommentar` table, law resolution shared with `genomforande.py`); the statute paragraf's rail shows each prop's comment ("Författningskommentar", newest first, `#sid`-pinpointed provenance) |

**avg vertical (JO + JK + ARN myndighetsavgöranden)**
| File | What |
|---|---|
| `model.py` | `Beslut` model; URI = `avg/{org}/{dnr}`, byte-identical to what MYNDIGHETSBESLUT citations mint |
| `download.py` | JO harvester (jo.se WordPress admin-ajax search API + decision PDFs), JK harvester (jk.se listing → per-decision landing pages; `jk_canonical` dnr normalization) and ARN harvester (arn.se one-page vägledande-beslut listing → decision PDFs; a live record overwrites a frozen-import one) |
| `legacy.py` | one-time import of the frozen ARN corpus 1991–2022 (`lagen avg import-legacy arn <tree>`, §7g) — fragment.html metadata, magic-sniffed bodies converted to PDF via soffice |
| `parse.py` | JO/ARN: PDF body via `lib/pdftext` (bold rubriker; JO's "Beslutet i korthet" abstract); JK: landing-page `div.content` (strong→section, em→subsection); all citation-scanned with the DV parse-type set |

**foreskrift vertical (agency regulations)**
| File | What |
|---|---|
| `agencies.py` | the data registry driving one shared harvest engine — 71 registered författningssamlingar (the full lagrummet.se agency list, county `\d+FS` series excluded), 66 live + 5 closed series with no live harvester (rsfs, sosfs/hslffs, sjvfs, svkfs), no per-agency pipelines; SKVFS and MTFS alone select detached headful Chrome, all others use requests/HTTP2 |
| `harvest.py` | per-agency enumerate/resolve architectures (indexed/paginated/json/sitemap enumerators; landing/direct resolvers + file classifiers) wired onto `lib/harvest.py`'s shared `walk`/`HarvestWatermark` loop; `Agency.browser` selects `lib/browser.py` without changing the loop |
| `skvfs.py` / `mtfs.py` | pure catalogue/identity rules plus protected resolvers for the two F5/Shape sources; SKVFS resolves a detail page then its exact PDF and also emits the RSFS predecessor, while MTFS headings point directly to PDFs |
| `download.py` | the `lagen foreskrift download` front over the engine (`--full`, `--only`; closed-series fs are a logged no-op) |
| `model.py` / `structure.py` / `parse.py` | as-published `Foreskrift` model, PDF → statute-shaped structure → artifact (the closed series' bodies are ordinary corpus PDFs, `parse.body_path` resolves them under the download tree like any harvested source) |

**eurlex vertical (EU law — EUR-Lex / CELLAR)**
| File | What |
|---|---|
| `download.py` | harvester for the Publications Office CELLAR repository, keyed by CELEX (SPARQL discovery + SOAP/REST fetch; Formex/HTML/PDF manifestations) |
| `bulk.py` | unpack a CELLAR bulk "legislation" dump into the per-CELEX layout the incremental harvester produces, so the whole corpus can be imported from official dumps |
| `model.py` | typed `EurlexDoc` model parsed from Formex (legislation/treaties + judgments) |
| `parse.py` | orchestrator: Formex (the structured XML manifestation) → `EurlexDoc` → JSON artifact |
| `parse_html.py` / `parse_pdf.py` | fallback body parsers for the (many older) acts with no Formex — OJ HTML/XHTML, then PDF via `pdftohtml -xml` as last resort |
| `structure.py` | group an act's flat block sequence into its containment hierarchy (`nest`, the parse-time tree builder; the anchor grammar itself lives in `lib/eu_structure.py`) |
| `definitions.py` | extract an act's defined terms and interlink their in-act uses |
| `lang.py` | localized structural vocabulary for the non-Formex (html/pdf) parsers (Formex is tag-marked, so its parser needs no language knowledge) |
| `annotate.py` | `lagen eurlex ai-annotate <CELEX>` — author the editorial `.ann` layer for a sector-3 act with an LLM, written to the curated store (`lib/annstore.py`) |
| `casenames.py` | `lagen eurlex casenames` — harvest CELEX → usual name for named EU cases ("Schrems II") from Wikidata (property P476) into `data/casenames.json`, read by `lib/eucasenaming.py` |

**hudoc vertical (European Court of Human Rights case law)**
| File | What |
|---|---|
| `download.py` | bulk paginator over HUDOC's public `/app/query/results` JSON endpoint (Grand Chamber + Chamber judgments only, English by default, `PAGE_SIZE=500`) plus `/app/conversion/docx/html/body` for each full text, fetched through a small `ThreadPoolExecutor` (`WORKERS=4`) that keeps bodies in flight ahead of the walk; newest-first watermark, `--lang`, `--only` and `--limit` |
| `model.py` | typed `HudocCase`/`Block` model; one stable `/dom/echr/{itemid}` expression per HUDOC item; article-facet metadata becomes explicit references to `ext/coe/{ETS}#A…`; restarted numbering keeps the first canonical paragraph anchor and suffixes later occurrences (`#P1-2`) |
| `parse.py` | converted Word HTML → CSS-derived headings, numbered paragraphs and notes → artifact; skips only TOC links (the TOC can share its container with the judgment) and marks language/cover placeholders with no numbered body as deliberately empty (`SkipDocument`) |

**coe vertical (Council of Europe Treaty Office)**
| File | What |
|---|---|
| `download.py` | one search POST to the Treaty Office's anonymous JSON web service (`conventions-ws.coe.int`, token embedded in the public `full-list2` page, mounted via `lib.net.mount_legacy_tls` for its small-DH-key TLS) returns all 233 treaties' metadata in one call; `getLieux` resolves opening places; each official English text downloads as a plain PDF from `rm.coe.int` (no challenge, no HTML scraping) |
| `model.py` | typed `Treaty`; canonical `ext/coe/{ETS-or-CETS-number}` identity and an `rdfs:seeAlso` bridge from the ECHR instruments reproduced in SFS 1994:1219 |
| `parse.py` | official English PDF → article/subarticle tree (`#A8`, `#A6P3Ld`) via `pdftohtml -> page_paragraphs -> build_structure`; supports numeric, Roman and compound article designations plus section-only amending instruments, and context-suffixes repeated printed designators so every node id is unique |
| `data/names.json` | Council-of-Europe treaties by Swedish name → ETS/CETS number, hand-edited; read by `lib.lagrum.load_treaties` (citation grammar) **and** by `render._coe_named` — its keys are the curated *central* treaties surfaced first on the folkrätt landing, and its `abbr` is the badge (EKMR, …) |

**icrc vertical (ICRC international humanitarian law treaties)**
| File | What |
|---|---|
| `download.py` | one paginated list call (`page[limit]=50`) over the ICRC's anonymous Drupal 10 JSON:API (`ihl-databases.icrc.org/en/jsonapi/node/treaty`) enumerates all 111 IHL instruments; one per-treaty `include=`-expanded fetch returns the whole self-contained envelope — metadata, authentic article text (`field_treaty_content`), per-state participation (`field_treaty_state_parties`), depositary/topics/languages taxonomy terms — the stored record, so parse never touches the network; incremental off the node's `changed` stamp + `HarvestWatermark`; `--only <ICRC-number>`, `--limit`, `--force` |
| `model.py` | typed `Treaty`/`Provision`/`Party`; canonical `ext/icrc/{ICRC-number}` identity kept local to the vertical (rule:second-use-goes-to-lib — nothing in `lib` mints an ICRC target yet); article-fragment ids `A<n>`/`Preamble`/`Testimonium`/`Annex<n>`; `kind` classifies doctype as treaty/protocol/declaration |
| `parse.py` | resolves the envelope's `included` relationship graph into the `Treaty` model, then `.to_artifact()`; article body HTML → stycken via BeautifulSoup; skips commentary front matter (ToC/Foreword/Introduction) |
| `data/names.json` | the four 1949 Geneva Conventions and their three Additional Protocols (ICRC numbers 365/370/375/380/470/475/615), hand-edited, with informal Swedish names and acronyms (GK I–IV, TP I–III) — the curated *central* instruments surfaced first on the folkrätt landing, mirroring `coe/data/names.json` |

**untc vertical (UN Treaty Collection — MTDSG status)**
| File | What |
|---|---|
| `download.py` | one static-HTML fetch per curated treaty from `ViewDetailsIII.aspx?src=TREATY&mtdsg_no={id}&chapter={n}&clang=_en` (an ASP.NET page that answers unattended clients directly); the corpus is a tiny fixed set, so this is a plain loop, skipping a page already on disk unless `--full` re-fetches it; `--only <MTDSG-id>`, `--limit` |
| `model.py` | typed `Treaty`/`Party`; canonical `ext/untc/{mtdsg_no}` identity kept local to the vertical (rule:second-use-goes-to-lib); `structure` is deliberately empty — the MTDSG carries status only, a treaty's authentic text lives in per-treaty UNTS PDFs outside this uniform scrape, and the page links out to it |
| `parse.py` | scrapes the stable ASP.NET control ids for conclusion/entry-into-force/UNTS registration and the participation grid (anchored on the grid's own control id `tblgrid`, not a header cell — some treaties precede it with a decoy territorial-notification table under the same "Participant" header); footnote `<sup>` stripping, `<a class="noteIndex">`-wrapped declaring states, and consent-form markers (`a` accession, `d` succession, `c` formal confirmation, `A` acceptance, plain date ratification). Offline (reads the stored page) |
| `data/treaties.json` | the curated 14-instrument list (one harvest engine over all, rule:configured-by-data) — VCLT, UNCLOS, the Genocide Convention, the core human-rights instruments (ICERD/ICESCR/ICCPR/CEDAW/CAT/CRC/CMW/CRPD/CED) and the Refugee Convention + Protocol; `mtdsg_no`/`chapter` complete the query, `title` is the authoritative English name (the page headline is generic), `sv`/`abbr` drive the folkrätt listing, `group` is the Swedish subject heading it files under |

**icc vertical (International Criminal Court case law)**
| File | What |
|---|---|
| `download.py` | two-source harvest, both Cloudflare-free (the ICC's own `/court-record` detail pages are Cloudflare-walled): a facet scrape over icc-cpi.int `/decisions` (`decision_type_of_decision`, curated by `data/decision_types.json`) enumerates the curated substantive set and yields each record's document number; the ICC Legal Tools API (`legal-tools.org/api/ltdDocs`, `externalId` `like` prefix match, case-sensitive) resolves that number to metadata, slug and the decision PDF (`/doc/<slug>/pdf`), preferring the English primary over `-t<LANG>` translation variants; incremental via a date watermark, `--only <ICC-doc-number>`, `--limit` |
| `model.py` | typed `Decision`/`Block` (HUDOC-shaped); `to_artifact()` turns numbered paragraphs into the citation-unit article tree (`P<n>` ids); canonical `ext/icc/{doc-number}` identity (slashes flattened to underscores) kept local to the vertical (rule:second-use-goes-to-lib) |
| `parse.py` | Legal Tools metadata (ICC-listing fallback) + PDF text (`lib/pdftext`) → artifact; strips the per-page court-record running header, classifies numbered paragraphs vs. section headings (`_classify`, pure); a decision Legal Tools couldn't resolve stays metadata-only (empty structure) |
| `data/decision_types.json` | the curated Rome-Statute decision types (one harvest engine over all, rule:configured-by-data) — Art 74 verdicts, 76 sentences, 61 confirmation, 58 arrest warrants, 81/82/82.4 appeal judgments, 75 reparations, 15/18-19/53.3/110 — each with the icc-cpi.int facet id, the catalog/facet `kind`, and the Swedish heading it files under on the folkrätt landing; deliberately excludes the ~10k procedural Decision/Order mass |

`coe`, `hudoc`, `icrc`, `untc` and `icc` share one masthead entry, **Folkrätt**
(`/folkratt/`, an international-law umbrella for the later ICJ sources). The
bespoke `render.render_folkratt` landing lists every CoE instrument
alphabetically by its significant title (`lib.coe.significant_title`, the SFS
"Lag (yyyy:nn) om …" convention), each with its amending protocols nested
beneath the convention they amend (`lib.coe.protocol_reference` + a
longest-prefix title match), split into *Centrala* (the `names.json` treaties)
and *Övriga* A–Z; beside it the ICRC IHL instruments lead with
"Genèvekonventionerna och tilläggsprotokollen" (the `icrc/data/names.json`
central instruments), then carve the rest into a subject index by the ICRC's
own `field_treaty_topics` taxonomy (Stridsmetoder och stridsmedel, Sjö- och
luftkrigföring, Skydd av krigets offer, …), each group chronological; the UN
half lists the `untc/data/treaties.json` curated instruments grouped by
subject (Traktaträtt och havsrätt, Mänskliga rättigheter, Flyktingrätt), each
group chronological; the ICC half lists the curated substantive decisions
grouped by Rome-Statute decision type (`icc/data/decision_types.json`'s
labels, e.g. "Domar – fällande/friande (art. 74)"), each group newest first.
Beside all four sits the Europadomstolen (hudoc) faceted case browse, which
relocates under `/folkratt/hudoc/`; none of coe, icrc, untc or icc has a
faceted browse tree of its own — their whole listing lives on the landing
page. Treaty/case document *pages* keep their canonical addresses
(`/coe/{number}`, `/icrc/{number}`, `/untc/{mtdsg_no}`, `/icc/{doc-number}`,
`/dom/echr/{itemid}`).

**wiki vertical (git-backed markdown — begrepp + kommentar)**
| File | What |
|---|---|
| `parse.py` | project the markdown wiki into kommentar / begrepp artifacts; the `## heading → host node anchor` grammar (`heading_fragment`, `fragment_heading`), `host_uri`, and the frontmatter-keyed `kommentar_index`/`begrepp_index` |
| `annotate.py` | `lagen kommentar ai-annotate <basefile>` — the Step-4 AI guidance linker: read an annotation's declared guidance PDFs and propose, per article, the guidance links (`.ann` layer, curated store) |
| `guidance_discover.py` | `lagen kommentar {discover,propose}-guidance` — crawl Commission guidance sitemaps into a per-CELEX index + draft a `guidance:` block to review (no LLM) |

**remisser vertical (regeringen.se referral responses)**
| File | What |
|---|---|
| `model.py` | `Remiss` (case: title, dnr, deadline, `remitterat` cross-ref to the referred förarbete, `svar` list of `Remissinstans`), `Remissvar` (one organisation's parsed answer); `org_slug` derives the shared basename identity `download`/`parse`/`build` all key on |
| `download.py` | regeringen.se `/remisser/` two-pass sync — discover new cases (`--full` re-walks everything), then re-poll every still-open case for newly-arrived answers and fetch any answer PDF not yet cached; `sync_one`/`--only <url>` fetches one known case directly; any per-case fetch or parse failure (HTTP error, or a 200 whose DOM doesn't match — a bot-challenge interstitial, a truncation) is written as a *stub* record (from listing facts only) so the incremental watermark can't hide the failure — re-polled until it succeeds |
| `parse.py` | one answer PDF → `Remissvar` via the shared `lib/pdftext` (`pdf_pages` + `page_paragraphs`), flattened to plain paragraph text; passes `identifier=None` since each organisation's PDF carries its own letterhead, not a fixed running header |
| `ai_analyze.py` | `lagen remisser ai-analyze <case-slug>/<org-slug>` — the sole LLM pass: maps one answer onto the referred SOU/Ds's sections with a per-section sentiment + verbatim quote plus an overall stance, validated strictly and written as a `.ann` layer in the curated store; retries once via `lib.llm.author`'s validate/self-repair loop on a malformed reply |

This source is never `relate`d/`generate`d — it publishes no pages of its own;
`render._remiss_indexes` reads its `.ann` layers straight out of the curated
store (`lib/annstore.py`, `WIKI_ROOT/ann/remisser/…`) and surfaces them as a
"Remissvar" section on the *referred förarbete's* context rail.

**site vertical (editorial chrome — frontpage / om / sitenews)**
| File | What |
|---|---|
| `model.py` | small block-tree dataclasses (`Heading`/`Paragraph`/`Bullets`/`Code`; on-disk `type` discriminator `rubrik`/`stycke`/`lista`/`kod`) plus the three page shapes `Frontpage`, `AboutPage`, `Sitenews` (a list of `NewsItem`) — no citation graph, so no `Forfattning`/`Avgorande`-style domain model |
| `parse.py` | markdown (`lagen-wiki/site/`) → JSON artifact for three fixed basefiles: `frontpage` (curated law list: `## <Category>` + `- [Label](sfs:…)` bullets), `om/<slug>` (about pages), `sitenews` (split into dated `NewsItem`s on `## YYYY-MM-DD HH:MM:SS Title` heads); reuses `lib.markdown`'s frontmatter/link/heading grammar, adds only the block layer (bullets, fenced code) |
| `render.py` | artifacts → static HTML + Atom, one entry point `write_site(out_root)` called by the build driver during `generate`; the curated frontpage overwrites the generic corpus-stats `index.html` (`has_frontpage()` gates that) |

Like `remisser`, `site` is parsed (and, unlike remisser, rendered during
`generate`) but is **absent from `ARTIFACTS`**, so it is never
`relate`d/indexed/dumped — it carries no citation graph. Site artifacts are
folded into `generate_watermark()` so an editorial edit reopens the generate
gate. Served at `/` (frontpage), `/om/<slug>` + `/om/` hub, and
`/dataset/sitenews/feed` (+ `/dataset/sitenews/feed.atom`); masthead entries
"Om"/"Nyheter" in `lib/render.py`'s `MAST_NAV`.

The catalog-backed document feeds retain the legacy public contract too:
`/dataset/{sfs,dv,forarbeten,myndfs,myndprax,keyword,eurlex}/feed.atom` and their
human-readable `/feed` twins, with the old `rdf_type`,
`rpubl_rattsfallspublikation`, and `dcterms_publisher` query parameters.
`/dataset/sitenews` is the all-feeds directory. `lib/feeds.py` maps those legacy
repository aliases to the rebuilt source names and renders stable Atom entry ids.

**Service layer**: `api/app.py` is the REST/OpenAPI service (search, documents,
citation graph, version history + diff) that also serves the static site under
`lagen serve`. `api/mcp.py` mounts a public, no-auth **MCP server** (Model
Context Protocol) at `/mcp` on the same app — the same read-only view reshaped as
tools (search, resolve_citation, get_document, the citation graph, …) so any
MCP-capable AI host can ground answers about Swedish/EU law in the live corpus and
cite the exact §/article; the tools are thin wrappers over the same `lib`
functions the REST endpoints use (see `api/README.md`). A `_LoggedMCP` ASGI
wrapper logs one line per JSON-RPC request (client IP, method, tool name +
truncated arguments) — the only tool-level visibility, since the uvicorn/nginx
access log sees only `POST /mcp/ 200`; the MCP SDK's DNS-rebinding protection is
explicitly disabled (`enable_dns_rebinding_protection=False`), since its
localhost-only default would 421 all production traffic arriving through the
nginx vhost. `serve()` calls `logging.basicConfig(INFO)` so these and other
app-level log lines reach stdout alongside uvicorn's own access log. `api/ops.py` mounts the
ops health dashboard on the same app (see "Operations" below); `lib/runlog.py`
owns the state files behind it. `api/auth.py` + `api/edit.py` +
`api/editcontent.py` + `api/editcart.py` are the inline content editor — the one
authenticated, mutating surface (see "Inline editing" below); `api/patch.py` is
its sibling for authoring source-fix **patch files** (`lib/patch.py`,
`lib/patchit.py`, `patchsource.py`; see "Patch files" below). `lib/pins.py` is the
citation-shaped-query resolver (a name+pinpoint → one exact fragment target)
shared by the REST `/search` and the MCP `search`/`resolve_citation` tools.

**Top-level**: `config.py` resolves the optional `config.yml` — the corpus
roots (`data_root`, `legacy_root`, `wiki_root`), the services the pipeline
talks to (`opensearch_url`, `llm_base_url`/`llm_model`/`llm_temperature`/
`llm_top_p`/`vision_model`) and the deployment's own settings (`ops_token`,
`editor_secret`, `editors`, `compress`/`compress_quality`, `cookie_secure`) —
read with ruamel.yaml round-trip mode so a bad value's line number is reported.
What it deliberately does *not* locate is curated source data shipped in the
tree (`lib/datasets.py`'s `NAMEDLAWS`/`NAMEDACTS`/`NAMEDCASES`/`NAMEDEUCASES`/`COE_NAMES`/`ICRC_NAMES`/`UNTC_TREATIES`/`ICC_DECISION_TYPES`,
`sfs/data/resources.json`, …) are anchored by their own callers, not here.

## Running the pipelines

**SFS** (operates on `site/data/{downloaded,artifact}/sfs/`, validated against
the golden corpus in the old checkout, `../ferenda.old/data/sfs/parsed/`):

```sh
uv run python -m accommodanda.build sfs download                              # incremental; --force for a full backfill
uv run python -m accommodanda.build sfs download --resume-after '[...]'       # resume a backfill interrupted mid-sweep,
                                                                                # from the ES search_after cursor it printed
uv run python -m accommodanda.sfs parse site/data/downloaded/sfs/2018/585.json --basefile 2018:585
# golden = the old pipeline's parsed XHTML (scaffolding in the old checkout), normalized to NF on the fly
uv run python -m accommodanda.sfs validate ../ferenda.old/data/sfs/parsed site/data/downloaded/sfs --sections structure,references
uv run python -m accommodanda.sfs refs FILE PARSED.xhtml  # citation diff for one doc
```

The SFST consolidation is text-only. During the normal SFS parse, omission
markers and the road-sign tables in 2007:90 are projected as typed `grafik`
nodes; the source model retains the original marker text. Mirror the official
published PDFs (the crop source), then vision-localize the gaps onto them.
Mirroring runs as part of `sfs download` and costs only bandwidth; the
vision pass is opt-in and elective (it costs tokens) and is never part of a
production build:

```sh
uv run python -m accommodanda.build sfs mirror-pdf                     # every base act + registered amendment (also run by `sfs download`)
uv run python -m accommodanda.build sfs mirror-pdf 2007:90             # named SFS act(s) only
uv run python -m accommodanda.build sfs mirror-pdf --full              # re-fetch existing + re-ask about acts once denied
uv run python -m accommodanda.build sfs ai-includegraphics 2007:90     # vision-localize that act's gaps
```

The mirror writes `site/data/downloaded/sfs/pdf/{year}/{number}.pdf`. Which
source holds an act follows from its SFS number, and both boundaries are exact
act numbers rather than dates: `2018:160` onward is the authentic online series
at svenskforfattningssamling.se, `1998:306`–`2018:159` is the printed series'
rkrattsdb mirror (so early-2018 acts, published before the 1 April switch, come
from there), and anything before `1998:306` exists only on paper — naming one
is an error. Beside the PDFs, `.mirror.json` records the acts an upstream
answered it has no PDF for: a missing file alone cannot say whether an act was
never fetched or has nothing to fetch, so without that record every such act
cost a request on every run. Each act is therefore asked about at most once —
the price being that a negative is permanent, so if the publisher posts a PDF it
previously lacked, only `--full` will find it. `ai-includegraphics` mirrors any
source PDF it still needs, so `mirror-pdf` need not have been run first.

Note that rkrattsdb.gov.se rate-limits: it starts returning `403` for a few
minutes after a burst, which `lib/net.py` rides out with backoff but which can
still abort a corpus-wide sweep. A rerun resumes cheaply (everything already on
disk is skipped). `ai-includegraphics` resolves each gap's provenance
deterministically — the amending SFS that last set that wording (register-first
for bilaga gaps, e.g. 2004:629's two independently-amended map appendices),
never guessed by the model — then asks the vision model (`VISION_MODEL` in
`config.py`, separate from the text `LLM_MODEL`) to locate page + bbox in that
PDF, writing a `.graphics` layer to the curated store (`lib/annstore.py`) with
per-entry `verified` flags that survive a rerun only while both provenance and
semantic identity still match. The artifact's local `G1` id is not persisted as
identity: the layer is keyed by a `g-…` hash of structural path, kind/code,
normalized anchor and container-local occurrence, and stores the unhashed
`identity` object in each entry for review. Content copies of the same semantic
appendix share a key/crop; a *pending* temporal variant (a container the source
prints beside its in-force sibling with `/Träder i kraft I:.../`) instead gets
its own keys and its own provenance-correct source PDF. Generated candidates
are not publicly rendered until
their entry (or whole layer) is verified. `GET /api/v1/sfs-graphic?uri=&node=` serves the
crop (`lib/facsimile.py`'s `render_region`/`cached_region`) lazily from the
provenance-correct PDF; the renderer shows the crop where the layer has placed
one — captioned "Karta ur SFS X", linked to the amendment's `#L{nr}` register
entry on the same page — an honest placeholder otherwise, and prints each
temporal variant's entry-into-force state as a subdued slash-delimited
marker (`/Träder i kraft: den dag som regeringen bestämmer/`).

**SFS version history** (historical consolidations / time travel / diff): the
downloader archives every superseded consolidation under
`site/data/downloaded/sfs/archive/{y}/{n}/.versions/` (the old site's two HTML
generations live there too, imported wholesale). The `versions` stage parses
them into `artifact/sfs/archive/…/.versions/{vy}/{vn}.json` plus a per-statute
`artifact/sfs/{y}/{n}.versions.json` sidecar; `generate` then renders one page per
historical lydelse at `/{sfsnr}/konsolidering/{version}` (watermarked
"Inaktuell författning"), the statute page grows a "Jämför lydelser" panel and
the bottom-of-page **Ändringar och övergångsbestämmelser** register view (per
amendment: publication links, the point-in-time konsolidering link, a diff
link against the previous lydelse, övergångsbestämmelser, förarbeten). The
diff view (`?diff=<version>`, `versions.js`) is computed on demand by
`GET /api/v1/document/diff` — always oldest→newest — (see also
`/api/v1/document/versions`). The whole history is also exportable as a git
repository (`history-as-git`, `sfs/asgit.py`), per
[`docs/prd-sfs-history-as-git.md`](../docs/prd-sfs-history-as-git.md).

```sh
uv run python -m accommodanda.build sfs versions            # incremental, all statutes
uv run python -m accommodanda.build sfs versions 1998:204   # one statute
uv run python -m accommodanda.build sfs parse               # required before a full Git export
uv run python -m accommodanda.build sfs history-as-git /path/to/repo             # complete corpus; strict append-only updates
uv run python -m accommodanda.build sfs history-as-git /path/to/repo --rebuild-history  # recreate corrected/backfilled history
uv run python -m accommodanda.build sfs history-as-git /path/to/repo 1998:204   # separately scoped partial repo
```

**DV** (operates on `site/data/downloaded/dom/` (API) and `site/data/downloaded/dv/` (legacy)):

```sh
# download + build the identity index
uv run python -m accommodanda.dv.download site/data/downloaded/dom   # [--full] [--no-bilagor] [--limit N]
uv run python -m accommodanda.build dv reindex                  # -> site/data/artifact/dom/identity-index.json
                                                                  # (also auto-run after any harvest that changed records)

# parse (API path is driver-owned; `[ids…]` parses just those, empty = all stale)
uv run python -m accommodanda.build dv parse                                       # API path, incremental
uv run python -m accommodanda.dv.legacy --index site/data/artifact/dom/identity-index.json   # legacy POI path, batch report
uv run python -m accommodanda.dv.legacy site/data/downloaded/dv/ADO/1993-100_1.doc # one Word file -> artifact
```

The DV parsers are driven by the identity index: each canonical case is
parsed from its single best source — the API record when present, the
legacy Word original otherwise (no cross-source merge; see REWRITE.md §4).
The incremental download only covers late publication within its 365-day
safety window below the watermark; a record edit or a referat published
later than that surfaces only under `--full`, so a periodic cron'd `--full`
sweep remains the backstop.

**avg — JO + JK + ARN decisions** (operates on `site/data/{downloaded,artifact}/avg/`):

```sh
uv run python -m accommodanda.build avg download        # all three organs; or: … download jo
uv run python -m accommodanda.build avg parse           # incremental, like every source
uv run python -m accommodanda.build avg download jo --only jo/2340-2025   # one decision
```

**HUDOC + Council of Europe treaties + ICRC IHL treaties + UN Treaty Collection + ICC case law**:

```sh
uv run python -m accommodanda.build coe download                 # all Treaty Office instruments
uv run python -m accommodanda.build coe parse                    # official PDF text -> article artifacts
uv run python -m accommodanda.build hudoc download               # English GC+Chamber judgments, incremental
uv run python -m accommodanda.build hudoc download --lang ENG,FRE --limit 1000
uv run python -m accommodanda.build hudoc parse
uv run python -m accommodanda.build icrc download                # all ICRC IHL treaties
uv run python -m accommodanda.build icrc parse                   # JSON:API envelope -> article artifacts
uv run python -m accommodanda.build untc download                # the 14 curated MTDSG treaties
uv run python -m accommodanda.build untc parse                   # status page -> metadata + participation artifact
uv run python -m accommodanda.build icc download                 # the curated ICC substantive decisions
uv run python -m accommodanda.build icc parse                    # Legal Tools metadata + PDF -> article artifacts
uv run python -m accommodanda.build all relate                   # joins HUDOC cases to CoE articles
```

`coe download` never touches the Cloudflare-fronted portal pages: it POSTs one
search to the Treaty Office's anonymous JSON web service
(`conventions-ws.coe.int`, token embedded in the public `full-list2` page,
mounted through `lib.net.mount_legacy_tls` for its small-DH-key TLS), which
returns all 233 treaties with metadata in that one response, then downloads
each official English text as a plain PDF from `rm.coe.int`. HUDOC itself is
directly harvestable off `/app/query/results` and needs no browser automation
either; its body fetches run through a small worker pool (`WORKERS=4` in
`hudoc/download.py`) since they are the whole cost of a harvest. `icrc
download` reads the ICRC's own anonymous Drupal 10 JSON:API
(`ihl-databases.icrc.org`) directly — one paginated list call enumerates the
111 treaties, one `include=`-expanded fetch per treaty returns the whole
envelope including the authentic article text, so there is no separate PDF
step and `icrc parse` never touches the network. `untc download` fetches one
static-HTML status page per curated treaty from `treaties.un.org`'s
`ViewDetailsIII.aspx`; the MTDSG carries status only (dates, UNTS
registration, per-state participation), not the treaty text, so `untc parse`
scrapes that offline and the rendered page links out to the UN authentic text.
`icc download` also avoids the Cloudflare-fronted `/court-record` pages: it
facet-scrapes icc-cpi.int `/decisions` for the curated Rome-Statute decision
types to get each record's document number, then resolves that number
against the ICC Legal Tools API (`legal-tools.org/api/ltdDocs`) for metadata
and the decision PDF, so `icc parse` reads the stored Legal Tools record and
PDF text and never touches the network either.

**remisser — regeringen.se referral responses** (operates on
`site/data/{downloaded,artifact}/remisser/` — case records and answer PDFs
share one download tree, `site/data/downloaded/remisser/<case>.json` beside
`site/data/downloaded/remisser/<case>/<org>.pdf`; never `relate`d/`generate`d
— see the module map above):

```sh
uv run python -m accommodanda.build remisser download                    # harvest new cases + re-poll open ones
uv run python -m accommodanda.build remisser download --only <case-url>  # one case, bypassing the listing walk
uv run python -m accommodanda.build remisser parse                       # incremental, like every source
uv run python -m accommodanda.build remisser ai-analyze <case-slug>/<org-slug>  # the sole LLM pass
```

**site — lagen.nu's editorial chrome** (frontpage / om / sitenews; parsed +
generated but never `relate`d/indexed/dumped — see the module map above):

```sh
uv run python -m accommodanda.build site parse       # markdown -> artifacts, incremental
uv run python -m accommodanda.build site generate     # rewrite just the editorial pages (write_site)
```

### Wiki content repo (begrepp + kommentar)

The hand-authored commentary (`kommentar`) and concept glossary (`begrepp`)
are **git-backed markdown** in a separate content repo (`lagen-wiki`),
checked out alongside this one and pointed at by `WIKI_ROOT`:

```sh
git clone <lagen-wiki remote> ../lagen-wiki    # or: git submodule update --init
uv run python -m accommodanda.build begrepp parse
uv run python -m accommodanda.build kommentar parse
```

`WIKI_ROOT` defaults to `../lagen-wiki` (a sibling of the repo); override it
with the `wiki_root` key in `config.yml` or the `WIKI_ROOT` env var. The
content layout is `concept/<Name>.md` (frontmatter `title:`) and
`commentary/<source>/<relpath>.md` (frontmatter `annotates:`) — the commentary
is filed under the source it annotates and that source's basefile→path rule, so
`SFS/1915:218` lives at `commentary/sfs/1915/218.md`. The parsed artifact mirrors
this — `site/data/artifact/kommentar/<host_source>/<host_relpath>.json` (e.g.
`site/data/artifact/kommentar/eurlex/2023/32023R2854.json`), reusing the host source's own
path transform (`layout.kommentar_host`) so commentaries on different sources can
never collide on one flat name. Concept links are
`[label](begrepp:Concept)`, external links are ordinary markdown
`[label](https://…)`, legal citations stay plain text (the citation engine links
them), and `aliases:` carries old names from MediaWiki redirects. The parser is
`lib/markdown.py`.

Each `## …` heading anchors the section to the host node it annotates, per host:

| heading | anchor | host |
|---|---|---|
| `## N §` | `#P{N}` | continuously-numbered SFS |
| `## N kap M §` | `#K{N}P{M}` | per-chapter SFS |
| `## Artikel N` | `#{N}` | EU act article |
| `## Artikel N.M` / `## Artikel N.M a` | `#{N}.{M}` / `#{N}.{M}.{a}` | EU sub-article (definition/list point) |
| `## Skäl N` or `## (N)` | `#recital-{N}` | EU recital |

`annotates:` is an SFS number (`2009:400`) or a CELEX (`32024R2847`); the host act
is resolved accordingly (`wiki.host_uri`). A section may carry prose **and** a
curated external-links list: a `## Externa länkar` bullet block attaches to the
section heading it sits under (per-article guidance, shown in that node's rail),
or to the act as a whole when it precedes any section heading (document-level,
shown in the "Om dokumentet" rail). Bullets are `- [label](https://…) — note`.

`lagen kommentar validate [basefiles…]` reports section anchors that match no node
in the annotated act (a mistyped `## Artikel 99` / amended-away `## 24 kap 2 §`);
the same check warns during `relate`.

`lagen kommentar ai-annotate <basefile>` (opt-in, LLM) is the AI guidance linker
(PRD Step 4). An annotation declares its external guidance documents by hand in a
`guidance:` frontmatter block — a list of `{title, url, pdf}` mappings, the `pdf:`
being the direct download link (a guidance doc is short-lived; the URL is not
derivable from the act):

```markdown
---
annotates: 32023R2854
guidance:
  - title: Frågor och svar om dataakten
    url: https://digital-strategy.ec.europa.eu/en/library/…-data-act
    pdf: https://ec.europa.eu/newsroom/dae/redirection/document/108144
---
## Externa länkar
- [Frågor och svar om dataakten (FAQ)](https://…) — Europeiska kommissionen
```

The `guidance:` block is authored by hand because the one thing no machine can
derive is the binding "*this document is guidance on **this** act*": a Commission
DG microsite carries no machine-readable link from a guidance PDF to the
legislation it explains (verified against Cellar / EUR-Lex / data.europa.eu — the
relation lives only in prose). `lagen kommentar propose-guidance <dg-page-url |
CELEX> [<CELEX>]` does the drudge around that judgement: given a guidance *page*
URL (e.g. `…/en/policies/data-act`) it scrapes that page for the act's EUR-Lex
reference (a cross-check against the optional CELEX) and the guidance/library
items it links, resolves each to its current
`newsroom/dae/redirection/document/NNNNN` PDF (that id is version-specific — it
changes on every FAQ revision, which is why it can't be authored once), and prints
a **draft `guidance:` block** to review and paste. A human still decides which
candidates are genuine guidance on the act (not the factsheets / impact
assessments / general policy the page also lists).

Given a **CELEX** instead of a URL, it looks the page(s) up in an index built by
`lagen kommentar discover-guidance`, which crawls the configured Commission
guidance sites' sitemaps (`guidance_discover.GUIDANCE_SITES` — only DG CONNECT's
`digital-strategy.ec.europa.eu/en/policies/<slug>` hubs follow an enumerable
per-act shape today; sibling DG sites stay manual) and records, per act CELEX, the
hub pages that link it (`site/data/artifact/kommentar/guidance-index.json`). The DG WAF
429s a random slice of every run, so the index **merges across runs and
converges** — re-run to fill the gaps, or `--force` for a clean authoritative
rebuild when the rate budget is fresh. So the usual flow is `discover-guidance`
once, then `propose-guidance <CELEX>` per act.

Guidance *published in the OJ* is a different animal — it gets its own sector-5
`XC`/`DC` CELEX and is machine-linked to the parent act in Cellar
(`work_cites_work` / `resource_legal_based_on_resource_legal`), so it belongs in
the corpus as an ordinary eurlex document, not as an external `.ann` link
(sector-5 harvest is not wired yet).

The action downloads + caches each PDF (under `site/data/downloaded/kommentar/guidance/`), flattens it
to page-marked text, and asks the configured Berget model to map guidance sections
(FAQ questions) to the act's **fine-grained targets** — not just whole articles but
the sub-articles and recitals the act divides into: a single definition `2.21`, a
numbered paragraph `6.2`, a recital `recital-15` (the dotted sub-article / `recital-N`
anchor grammar `lib.eu_structure` mints, shared with the renderer and the wiki
commentary headings, so a link lands on the exact node). A FAQ answer about two definitions links to exactly those two, not to
article 2 as a whole. The result is written as a **`.ann` layer** in the curated
store (`lib/annstore.py`, `WIKI_ROOT/ann/kommentar/…`, mirroring the kommentar
artifact's own relpath) — `{"guidanceLinks": {anchor: [{label, href, desc, section}]}}` —
the AI-created (then human-corrected) layer, kept separate from the hand-edited
markdown, mirroring eurlex's `.ann` editorial layer. `label` names the source and
its own section reference ("Frågor och svar om dataakten, question 8"), `desc` is
that section's title (the FAQ question), so the rail renders `link: question`. The
guidance document's own `section` (a FAQ question number) is the durable,
human-dereferenceable locator; the `#page=N` deep link is a convenience, located by
matching the section title back into the PDF (the model miscounts pages). Like every
`ai-*` action the LLM is called only here, never from a corpus-wide
parse/relate/generate. The `.ann` is woven into the annotated act's rail by
`render._kommentar_indexes` (it merges each kommentar `.ann`'s `guidanceLinks`
alongside the curated per-article guidance); a sub-article gets its citation anchor
+ rail only when something targets it, so a forced/full `generate` surfaces the AI
links on the right nodes.

A kommentar is a **separate source**: editing a `commentary/…md` file shows up on
the annotated act's page only after re-running the wiki pipeline and the catalog —
`lagen kommentar parse && lagen kommentar relate && lagen <host> generate
<basefile>` (e.g. `lagen eurlex generate 32024R2847`; the host's own
`parse`/`generate` stages never read the wiki).

The repo was seeded from the live MediaWiki SQLite DB, replaying the full
per-revision history as one git commit per revision:

```sh
uv run python tools/mediawiki_to_markdown.py path/to/lagen.sqlite ../lagen-wiki
uv run python tools/wiki_artifact_diff.py path/to/lagen.sqlite   # losslessness check
```

`wiki_artifact_diff.py` asserts the migration's safety property: for every
page, `markdown → artifact` is byte-identical to the old `wikitext →
artifact` (modulo two adjudicated, content-free normalisations — see the
script). `lib/wikitext.py` is retired from the pipeline and kept only as the
converter's/diff's reference.

### Site content (frontpage + om + sitenews)

lagen.nu's editorial chrome — the curated frontpage law list, the `/om/*`
about pages, and the sitenews feed — is likewise **git-backed markdown**,
alongside `concept/` and `commentary/` in the same `lagen-wiki` repo
(`WIKI_ROOT`):

```
site/frontpage.md      # ## <Category> headings + - [Label](sfs:…) bullets
site/om/<slug>.md       # one file per /om/<slug> about page
site/sitenews.md        # ## YYYY-MM-DD HH:MM:SS Title sections, newest content first
```

It was populated once by `tools/migrate_site_content.py`, converting three
legacy sources: the frontpage from the MediaWiki `Lagen.nu:Huvudsida` page (ns
4, in the sqlite dump), the about pages from `lagen/nu/res/static/*.rst`
(docutils RST), and the sitenews feed from `lagen/nu/res/static/sitenews.txt`.
Read-only over those legacy trees, like `tools/mediawiki_to_markdown.py`; the
markdown is the source of truth thereafter — hand-edit it, don't re-run the
migration.

```sh
uv run python -m accommodanda.build site parse    # markdown -> artifacts, incremental
uv run python -m accommodanda.build site generate # rewrite the editorial pages
```

## Data layout

The pipelines read large data trees that live under `site/data/` (not all
committed):

```
site/data/downloaded/sfs/                     # SFS raw (beta JSON + legacy sfst/sfsr HTML)
site/data/downloaded/sfs/pdf/                 # mirrored official SFS PDFs (1998–; the graphic-crop source)
site/data/artifact/sfs/                       # parsed JSON artifacts (+ .versions.json sidecars)
site/data/{downloaded,artifact}/sfs/archive/  # superseded consolidations, raw + parsed
site/data/downloaded/dom/                     # DV new-API harvest (per court)
site/data/downloaded/dv/                      # DV legacy feed (.doc/.docx)
site/data/artifact/dom/identity-index.json    # canonical case -> source records
site/data/downloaded/avg/{jo,jk,arn}/         # JO/JK/ARN records (+ jo/arn PDFs, jk landing html)
site/data/downloaded/hudoc/                   # HUDOC metadata JSON + converted full-text HTML
site/data/downloaded/coe/                     # Treaty Office records + official English texts
site/data/downloaded/icrc/                    # ICRC JSON:API treaty envelopes (metadata + authentic text, no PDF)
site/data/downloaded/untc/                    # MTDSG status pages (metadata + participation, no treaty text)
site/data/downloaded/icc/                     # ICC Legal Tools records (metadata) + decision PDFs
site/data/downloaded/forarbete/<type>/        # regeringen.se harvest + frozen-import records (prop/sou/ds/pm/dir/fm/skr/so/lr)
site/data/downloaded/forarbete/bet/           # data.riksdagen.se harvest (utskottsbetänkanden; record json + PDF, no HTML landing page)
site/data/downloaded/forarbete/rskr/          # data.riksdagen.se harvest (riksdagsskrivelser; record json + HTML body, no PDF)
site/data/ocr/forarbete/<type>/               # optional re-OCR sidecar PDFs (win over frozen scans)
site/data/downloaded/remisser/<case-slug>.json  # regeringen.se remiss case record (Remiss json)
site/data/downloaded/remisser/<case-slug>/      # its per-organisation answer PDFs (beside the record)
```

The frozen legacy corpora (REWRITE.md §7g) are NOT under `site/data/`: import
records reference their body files in place under `legacy_root` (config.yml;
defaults to the sibling `../ferenda.old/data`) — moving those trees means
updating that one key, never rewriting records.

## Operations

`lib/runlog.py` owns three state files under `DATA/.build/`. The run ledger and
error store are written by `build.py` on every *pipeline* `lagen` invocation (a
no-op under `--dry-run`, and for the non-pipeline verbs `serve`/`runs`, which
carry no run id). `status` is the deliberate exception: it too carries no run id
and never touches the ledger, but it writes the authoritative `status.json`
snapshot cell directly (see below).

- `runs.ndjson` — append-only run ledger: one block of events per invocation
  (run-start, one segment per (step, source) executed, run-end).
- `errors.json` — per-document latest-outcome store keyed
  `<source>/<stage>/<basefile>`, set on failure and cleared on success, so a
  "failed" doc is distinguishable from one that was simply never touched.
- `status.json` — rolling per-source × per-stage health snapshot
  (`{total, fresh, stale, missing, failed, empty}` per cell).

```sh
uv run python -m accommodanda.build <source> status   # extended: also shows failed/empty, writes the authoritative snapshot cell
uv run python -m accommodanda.build all runs [N]       # recent runs from the ledger
uv run python -m accommodanda.build ann status         # inventory the curated LLM-layer store (lib/annstore.py): status/date/staleness per .ann/.corr layer
```

`/ops` is an HTML health dashboard mounted on the same FastAPI app as the REST
API (`api/ops.py`) — the per-source × per-stage matrix, a stale-snapshot
banner, failing-doc totals, the last runs, duration-regression flags, and the
catalog delta — with `/ops/runs`, `/ops/runs/{id}` (per-source timing bars +
segments + errors) and `/ops/failures` (drill-down with tracebacks) alongside
it. It's gated by HTTP Basic auth (user `ops`, password = the `ops_token` key
in `config.yml` or the `OPS_TOKEN` env var); leaving it unset disables the
dashboard (every route answers 403).

## Inline editing (web UI)

The git-backed markdown — legal-source **commentary** (`commentary/…md`),
**concept** pages (`concept/…md`) and the **editorial** site pages
(`site/…md`) — can be edited **inline on the live site** by a logged-in user,
instead of cloning `lagen-wiki` and committing by hand. It is the only
authenticated, mutating part of the service; the public read API stays GET-only.

**Who can edit** is a hand-curated registry in `config.yml` (there is no
self-signup). Each entry maps a login to the git identity its commits are
attributed to and a password hash:

```yaml
editor_secret: <random hex>          # signs the session cookie; unset ⇒ editing off (403)
editors:
  staffan:
    name: Staffan Malmgren           # -> GIT_AUTHOR_NAME / GIT_COMMITTER_NAME
    email: staffan@example.org        # -> GIT_AUTHOR_EMAIL / GIT_COMMITTER_EMAIL
    pwhash: "pbkdf2$260000$…$…"        # never a plaintext password
```

Mint a `pwhash` (nothing is stored in the clear):

```sh
uv run python -m accommodanda.api.auth hash '<the password>'   # prints the pbkdf2$… line
```

`editor_secret`/`editors` follow the same env→config.yml precedence as the other
knobs (`EDITOR_SECRET` env; `editors` is config-only). Leaving `editor_secret`
unset disables editing wholesale — every `/api/v1/{auth,edit}/*` route answers
403 — exactly as an unset `ops_token` disables `/ops`.

The session cookie's `Secure` flag is `cookie_secure` (`EDITOR_COOKIE_SECURE`
env), on by default; flip it off in `config.yml` only for a plain-http dev
serve. A password change (a new `pwhash`, plus a restart) invalidates every
outstanding session for that editor — the cookie embeds a fingerprint of the
current `pwhash`, which is the revocation mechanism (there is no server-side
session table to keep a separate blocklist in).

Login is rate-limited in-process (`api/auth.py`): a per-(IP, username) sliding
window allows 5 free attempts per minute, then backs off exponentially up to
5 minutes (`429` + `Retry-After`), and a hard concurrency cap bounds how many
pbkdf2 hashes run at once — so a flood can't pin CPU behind the password check
and starve the rest of the (small, single-process) server. State is in-memory
only; a restart forgets past attempts.

**How it works.** The static pages are byte-identical for anonymous readers;
`editor.js` (served with the site) grafts the edit UI on client-side after a
`GET /api/v1/auth/me` check, keyed off a `<meta name="lagen-doc">` the renderer
injects. On a statute / EU-act page an ✎ button on a `§`/article edits the
**commentary** for that node (the official text stays read-only) — the `##`
section is created from its heading if none exists, and the file with an
`annotates:` frontmatter if the host has no commentary at all. Concept and
editorial pages edit their whole markdown body. The editor has a link toolbar
that turns a search hit into an `sfs:`/`eurlex:`/`begrepp:` link.

Edits accumulate in a per-user **cart** (`DATA/.build/edits/<user>.json`, kept
out of the working tree so users don't collide). "Checkout" opens a
commit-message box and turns the whole cart into **one git commit authored as
that user** — byte-for-byte the history a `git clone` + commit would produce — 
then synchronously re-parses / re-relates / regenerates just the touched pages
(`build.rebuild_after_commit`) so the edit is live when the request returns. A
hunk that changed on disk since it was carted fails the checkout (409) rather
than clobbering.

The routes are same-origin only (the session cookie is `SameSite=Lax`; CORS
stays GET-open for the public read API). No new dependencies — cookie signing
and password hashing are stdlib `hmac`/`hashlib`.

## Patch files (source corrections + redactions)

Controlled, version-controlled fixes to a document's **source material**, applied
at parse time before the text is tokenised — the old pipeline's `patch_if_needed`,
re-done. A **correction** fixes a real error in a downloaded source (an OCR slip, a
broken table); a **redaction** removes personal data (a named party, a
personnummer) and is stored **rot13-obfuscated** so the removed text is not
plain-text googleable in the committed tree.

A patch is an ordinary unified diff against a document's **best intermediate
format** — the representation its parser actually reads and a human can edit: plain
text for `sfs`, the `innehåll` HTML for `dv`, the Formex XML for `eurlex`, and the
`pdftohtml -xml` output (verbose but editable) for the PDF-bodied sources
(`forarbete`, `foreskrift`, `remisser`, and JO/ARN under `avg`; JK is landing-page
HTML). Each vertical's parser applies the patch at that choke point —
`lib.patch.patch_if_needed(...)` for the text/HTML/XML sources, a `patch_key`
threaded into `lib.pdftext.pdf_pages` for the PDF ones; a patch that no longer
applies is a **fatal** parse error (the source drifted — it must be regenerated,
never silently skipped). Patches live committed in the repo at
`patches/<source>/<relpath>.patch` (or `.rot13.patch`), keyed by the same rule as
the artifact tree (`layout.patch`); they are folded into every patchable source's
parse freshness inputs so editing one re-stales its document.

Author them from the CLI or the inline web editor:

```sh
lagen sfs patch-show 2018:585 > /tmp/585.txt   # the intermediate text (patch applied)
$EDITOR /tmp/585.txt                            # edit to the desired final text
lagen sfs mkpatch 2018:585 /tmp/585.txt "Rättad OCR-felaktighet"
lagen dv mkpatch "NJA 2015 s 1" /tmp/case.html "Avidentifierad part" --rot13
```

The web surface (`api/patch.py`, gated by the same editor auth as the commentary
editor) serves `GET /api/v1/patch/edit?source=…&basefile=…` — a textarea seeded
with the intermediate text; saving writes the *minimal* diff, commits it attributed
to the editor, and force-reparses the document so the fix is live. Editing the text
back to the pristine source removes the patch. A logged-in editor reaches it from a
**🩹 Patcha källtext** button that `editor.js` grafts next to the *✎ Kommentera
dokumentet* button on any patchable document page (the page's `<meta name="lagen-doc">`
carries the `data-source`/`data-basefile` identity). See
[`patches/README.md`](patches/README.md).

## Production deployment (Docker)

Deployed to **ferenda-vps** as a standalone accommodanda-only stack — the legacy
lagen.nu stack is not on this box. The authoritative runbook (host bootstrap,
disk layout, secrets, CI, cron) is **[`../docs/deploy-vps.md`](../docs/deploy-vps.md)**;
this section is just the shape of it.

The repo-root `docker-compose.yml` defines four services, selected by a Compose
**profile**:

| invocation | services | use |
|---|---|---|
| `docker compose up -d` | `opensearch` only | dev — run `lagen all serve` from the working tree |
| `docker compose --profile prod up -d` | full stack | prod — `opensearch` + `accommodanda` + `nginx` + `certbot` |

`opensearch` carries no profile, so it starts in both; everything else is
`profiles: [prod]`. Everything runs unprivileged (`accommodanda` as uid 1000
matching the host `ferenda` user that owns the bind mounts, `nginx` as uid 101)
except the `certbot` sidecar, which is root inside its own container.

`accommodanda` is built on the box from this checkout (`docker/accommodanda/Dockerfile`):
the code is baked in, and it carries the full pipeline toolchain (poppler-utils,
tesseract+swe, ocrmypdf, raptor2-utils, a JRE + the POI jars), so **download +
rebuild run in the container** against the read-write corpus mount:

```sh
docker compose exec accommodanda lagen all rebuild   # parse→relate→index→dump→generate
docker compose exec accommodanda lagen all all       # download too, then rebuild
```

One uvicorn process serves the static site + REST API (`lagen all serve`, the
image `CMD`); the `nginx` vhost reverse-proxies to it on `:8000` (the app
resolves lagen.nu's bare-URL grammar itself, so nginx needs no `try_files`
rules). TLS for `ferenda.lagen.nu` is issued once with `tools/vps/issue-cert.sh`
(certbot `--standalone`, before nginx exists) and renewed by the `certbot`
sidecar thereafter.

**Continuous deploy + nightly sync.** Pushes to `modernization` trigger
`.github/workflows/deploy.yml` on a self-hosted runner on the VPS (update the
on-box checkout → build → `up -d` → `lagen all rebuild`); a `ferenda` crontab
runs `tools/vps/nightly.sh` (`lagen all all`) nightly. See the runbook.

**Bootstrap by rsync (skip the from-scratch rebuild)**

A full first `relate`/`generate` over the ~200K-document corpus is slow. Since
the catalog stores `data_root`-relative paths (see REWRITE.md §6 — the catalog is
*portable*), you can seed a new host from an already-built dev corpus instead:
rsync the artifact tree, `catalog.sqlite`, and `generated/` into the host's
`data_root`, then let the host update incrementally (`lagen all rebuild` only
re-does what changed). The paths resolve against the host's own `data_root`, not
the dev machine's.

One caveat: migrate the dev catalog **before** rsyncing. An older catalog holds
absolute paths; `rebuild()` rewrites them to relative in place, but only on the
host where those absolute paths are valid (it fails loud otherwise). Run
`lagen all relate` on dev once — it re-relates every source and relativises the
whole catalog — then rsync.
