# Ferenda developer setup

Ferenda uses vertical source pipelines (sfs, dv, hudoc, coe,
icrc, untc, icc, eurlex, forarbete, foreskrift, avg, rs, remisser, wiki, site, stats) that go from downloaded (or,
for wiki/site, hand-authored) source files to a typed document model and a JSON
artifact, with the citation engine as a shared library. (`stats` inverts that
direction. It reads the finished corpus and measures it.) This document explains
the package layout and how to run the pipelines.

## Prerequisites

- **Python 3.14+** and **[uv](https://docs.astral.sh/uv/)**. `uv sync`
  installs everything in `pyproject.toml` (incl. `jpype1`).
- **A JVM — only for the shared POI Word path** (`lib/poi.py`, used for DV
  `.doc`/`.docx` inputs). The JVM never runs inside the build
  process: `poi.read()` drives a persistent `lib/poi_worker.py` subprocess
  which owns jpype/POI, speaking line-delimited JSON over its pipes.
  Everything else (SFS, the citation engine, the DV API path, förarbete's
  `.docx` bodies via plain lxml) is pure Python and needs no Java.
- **`antiword`** — förarbete's binary `.doc` bodies (mostly proptrips-era Word
  6/95 binaries that POI's HWPF refuses) are read through it instead of POI.
  `sudo apt-get install -y antiword` on Ubuntu.
- **`Xvfb`** — only needed on a genuinely headless host (no `DISPLAY`, e.g. the
  VPS/CI) for `lib/browser.py`'s detached headful-Chrome transport (SKVFS,
  MTFS); it auto-starts a private virtual framebuffer and points `DISPLAY` at
  it. `sudo apt-get install -y xvfb` on Ubuntu. A desktop with a real
  `DISPLAY` needs nothing.

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
./tools/fetch_poi.sh         # POI jars for DV Word inputs
uv run python -m pytest      # run the maintained test suites
```

> `[tool.pytest.ini_options]` in pyproject.toml scopes collection to
> `test/test_*.py` and excludes the `test/files/` fixture tree. Name an
> individual suite to run a subset.

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
| `begrepp.py` | `find_definitions` — begreppsdefinition heuristics (paragraf mode + defined-term cases) → `dcterms:subject` links. A stycke can define **several** terms: one sentence coins two ("… säkerhetskänslig verksamhet (verksamhetsutövare) ska utreda behovet av säkerhetsskydd (säkerhetsskyddsanalys)"), where the rule used to require the parenthesis to close the sentence and so read only the last. A parenthesis away from the sentence end must read like a coinage (lowercase, digit-free, not a list marker), must not sit in a bilaga/konventionsbilaga/övergångsbestämmelser (`in_appendix` — an annex is a list of things, not drafting), and must not be in `NOT_A_CONCEPT` (an act naming its own actor — "myndigheten", "tillsynsmyndigheten", "chefen" — or giving an act its short title: "allmän dataskyddsförordning", written that way by 170 acts). Measured over 1,500 acts: +916 terms, 0 lost. The löptext form does **not** require the tail "i denna lag" — drafting as often writes "i detta kapitel", "vid tillämpning av 5 §" or nothing (säkerhetsskyddslagen 1 kap. 2 §), and requiring it lost 3,558 definitions in 1,427 acts; "Med" opening an adverbial ("Med undantag av …", "Med hjälp av …") is excluded by name, and the definiendum is trimmed of its article and its scope qualifier ("Med dotterbolag enligt första stycket 3 avses …" defines dotterbolag) |
| `graphics.py` | recovers content omitted by the text-only SFST source. Detection is deterministic and runs at parse time: the source's omission markers — `... är inte med här` and its wording variants (`Bilagan inte med här`, `Bilagor finns inte med här`, `Tabellen ej med här`), the older `Bilagan är här utesluten` / `Tabellen utelämnad` formula, standing alone or trailing a heading or a bilaga's own rubrik — plus otherwise unmarked road-sign cells in 2007:90 become typed `grafik` nodes. Each node carries a stable semantic `key`, hashed from structural path + kind/code + normalized anchor + occurrence within its container; transient `G1` ids remain diagnostic only. Localization resolves provenance (variant-aware: a pending, not-yet-in-force copy of a bilaga gets its own keys and its own source PDF), deduplicates content duplicates by key, strictly validates complete vision output and writes `.graphics` entries keyed by that semantic key with the unhashed identity alongside; wired as `lagen sfs ai-includegraphics`. Road-sign statutes take a deterministic route instead of the vision one: `roadsign_boxes`/`roadsign_index`/`localize_roadsigns` read each sign's page, rectangle and source act off the published PDFs (the designator opens the Märke column, so the sign is the ink between one row's caption and the next; the act that prints a row last owns it) |
| `redaktionell.py` | detects a stycke that is the publisher's *editorial note* rather than statute text, another projection-time overlay in `nf.py`'s NF pass like `graphics.py`'s gaps — `retype_editorial` retypes the finished stycke node as a `redaktionell` node, keeping its id, inline runs and beteckning. Two sorts: `endast-tryckt` (`/Författningens text finns bara i tryckt version/`, a corpus gap — a couple of dozen acts) and `upphavd` (`Har upphävts genom lag (1982:1101)`, a genuine repeal notice carrying the repealing SFS as `satt_av` — ~300 stycken, a handful of them a base act's whole body and the rest single repealed paragrafer inside a live act). The renderer gives the node the same subdued treatment as the grafik placeholder (`p.redaktionell`) |
| `pdfmirror.py` | official published-SFS PDF mirror, the crop source for graphic localization. Each act's source follows from its SFS number: `1998:306`–`2018:159` from direct rkrattsdb URLs, `2018:160`– from svenskforfattningssamling.se document pages, and nothing before `1998:306` (print only). Fetched bytes must be PDFs. `.mirror.json` records the acts an upstream answered it has no PDF for, which is the only thing telling those apart from "not fetched yet" and so the only thing keeping a rerun free. Runs as part of `lagen sfs download` and as `lagen sfs mirror-pdf`, not as a parse stage |
| `correspond.py` | the old-law → new-law paragraf correspondence map for a restructured statute, three routes into the same `.corr` payload: an LLM pass over the proposition's författningskommentar (`lagen sfs ai-correspond`), and the mechanical `table_correspond` over the prop's own jämförelsetabell bilagor (`lagen sfs table-correspond <new> <prop> [<old>[=TAG] ...]`, rows extracted by `forarbete/jamforelse.py`; several old laws — SFB's 23, SFL's 3 — merge into one layer, `=TAG` names an old law's prop-local shorthand so tagged cell references resolve against the right law) — every edge validated against both laws' paragraf inventories either way; plus the *same-law* renumbering route (`lagen sfs renumber-correspond <sfs>`), reading the register's "nuvarande … betecknas …" omfattning clauses into `betecknas` edges carrying the amendment's ikrafttradandedatum, which generate uses to split inbound references temporally ("Hänvisningar till tidigare beteckning 4 kap. 4 §" on RF 4 kap. 6 §) |
| `asgit.py` | `lagen sfs history-as-git <repodir> [basefile...]` — export the corpus as a git repo (one file per statute, one commit per amendment event grouped by proposition, authored by the prop's signers/committed by the rskr's, ingress as commit body); a per-transition hash ledger admits only strict append-only updates, while `--rebuild-history` atomically recreates corrected/backfilled history; implements `docs/prd-sfs-history-as-git.md` |
| `_validate.py` | worker functions for `lagen sfs validate`, in an importable module so `ProcessPoolExecutor` workers can resolve them under `python -m` |
| `__main__.py` | `parse` / `refs` / `validate` CLI |
| `render.py` | the författningssida: statute text, ändringsregister, the lydelse panel and the way-back banners. Registered as this source's page renderer in `build.SOURCE_RENDERERS`; `render(art, site) -> str`, built on the `lib/page` kit, with its own page template in `templates/` |

**Shared library (`lib/`)** — a source may import from here; `lib` never imports from, or branches on, a source.
| File | What |
|---|---|
| `lagrum.py` | Lark/Earley engine; `LagrumParser(parse_types=…)` composes a grammar from LAGRUM / KORTLAGRUM / EULAGSTIFTNING / RATTSFALL / FORARBETEN / …; `lang="eng"` swaps the EULAGSTIFTNING surface to English ("Article 29 (5) of Directive 71/305/EEC") for the pre-accession case law CELLAR holds in no Swedish version; EULAGSTIFTNING pins a recital as well as an article ("skäl 108 i allmänna dataskyddsförordningen" → `#recital-108`, including the "skäl N och artikel N i <akt>" coordination and the generic "skäl N i förordningen"), and EURATTSFALL reads the pre-1989 case numbering ("Case 31/87", "mål 45/87"); EULAGSTIFTNING pins a lettered point too, with or without a sub-article ("artikel 6.1 c" → `#6.1.c`, "artikel 3 a" → `#3.a`, and on a treaty "artikel 6.3 c" → `#A6P3Lc`), and `with_indefinite_aliases` derives "EU:s dataskyddsförordning" (indefinite) from the registered definite "dataskyddsförordningen" so the genitive form still resolves; `FORESKRIFT` recognises a myndighetsföreskrift by its författningssamling designation + number ("PMFS 2022:1", "ELSÄK-FS 2008:1"), the designation terminal built from the `foreskrift/data/series.json` registry (`lib.datasets.load_fs_series`) so only a registered series mints a uri; `load_namedlaws`/`load_abbreviations` return a `NamedLaws` — a dict subclass that is still the flat name→SFS-id map of the current act (the grammar's NAMED_LAW terminal and every existing caller are unchanged) plus `.at(name, when)`, the act that carried the name on a given date, since a name outlives the act holding it and an undated resolution can point a citation at a statute that didn't exist yet; `LagrumParser(..., written=)`/`sfs_parser(..., written=)` set the document's own date, `reset(written=)` resets it per document for a cached parser, and a law the document itself names ("lagen (2001:453) om …") still outranks the dated table whatever the date; FORARBETEN now also reads kommittédirektiv ("dir. 2016:73" → `/dir/2016:73`) and the dot-dropped prop./rskr. forms tables print ("prop 1999/2000:111"), and `_riksmote_str` folds a four-digit second year within the same century ("2008/2009") to the corpus's "2008/09" (leaving "1999/2000", the one riksmöte genuinely spanning a century, untouched); `TREATIES`/`TREATY_PIN` (loaded from `treaty_names.json`'s `names_sv`) link Swedish treaty names ("artikel 24 i barnkonventionen" → `untc/I-27531#A24`) through the same `treaty_ids.article_fragment` anchor grammar `treatyref` uses; the akttyp terminals (DIREKTIV/FORORDNING/REKOMMENDATION/BESLUT) now match the definite form too ("förordningen"), closing a mislink where "artikel 30 i förordningen (EG) nr 765/2008" fell back to the *previous* named act because the definite noun didn't parse as naming its own; the scan runs over a width-preserving whitespace normalization (U+202F/U+00A0 → space; HD's 2016–2020 referat typography, 1,339 otherwise-unreachable citations) while each Ref's text keeps the source's typography; STALLNINGSTAGANDE links Skatteverket ställningstaganden by dnr shape ("dnr 131 599911-10/111" → `rs/skv/…`), `jo_arsb_ref` resolves "JO 2003/04 s. 450" through the committed `avg/data/arsberattelse.json` snapshot (two-decision pages stay unlinked), `so_ref` reads "SÖ 1982:50" → `so/1982:50`, EMDRATTSFALL delegates to `lib/emdref.py` and MALNUMMER to `lib/malnummer.py` (neither has a grammar half), and the letterless CJEU form is year-bounded 1954–1989 so "i mål 23452/94" (an ECHR application number) no longer fabricates a celex; `ENGLAGRUM` links Swedish statutes cited from English text — the register's own prefix anywhere ("SFS 1979:429") and an English chapter/section pinpoint ("Chapter 5, Section 2") bound to the resolved statute reference directly before it, since a free-standing "Chapter N, Section N" in the pan-Nordic journals as often names Finnish or Norwegian law and so stays unlinked with no anchor |
| `emdref.py` | ECHR citations in *Swedish* text ("Osman mot Förenade kungariket", "ansökan nr 23452/94") as `(start, end, uri)` spans — the EMDRATTSFALL parse type's matcher, merged into `LagrumParser.parse_text` for every source running `ALL_PARSE_TYPES` (+ edpb). Matches over the committed `hudoc/data/casenames.json` snapshot joined through `respondents_sv.json`, with `hudoc/citations.py`'s disambiguation ported verbatim: a date printed beside the citation wins, else the sole judgment; chamber + Grand Chamber with no date stays unlinked rather than guessed |
| `malnummer.py` | Swedish court case numbers: the printed shape (`find`/`normalize` — "T 3-08", "T3-08" and "A-232-2013" are one number, and the "2009-11" inside a date is none), and the MALNUMMER parse type's matcher (`spans`), which resolves a decision named before its referat existed — "Högsta domstolens dom 2009-11-03 T 3-08" is what SvJT 2010 s. 94 calls NJA 2009 s. 672. Resolved over the committed `dv/data/casenumbers.json` snapshot, and only when the citation names a court that holds the number: the corpus holds no tingsrätt decisions, whose case numbers fill the same texts and collide with the held ones. A printed date decides between candidates (298 of 24,411 numbers name more than one decision) but never vetoes a lone one — 12 of 255 such citations print the date of an interim beslut or of the föredragning instead. A second, unknown court between the named one and the number ("HD prövade Södertörns tingsrätts dom i mål nr B 1-85") takes the number for its own and blocks the link. `query_numbers` is the stricter search-box rule: a bare number counts only as the whole query, since "17 kap. 17-18 §§" holds no case number though the corpus holds a decision numbered 17-18. Shared with `lib/search.py`, which indexes the same normalized number as a searchable identity |
| `casenaming.py` | court-decision identity — `case_uri` (mint a case's canonical URI via the RATTSFALL parser) + `case_label`/`lopnummer` (referat identity + HD's given names); read identically by dv's parse-time label stamp, the catalog row and the page heading |
| `eucasenaming.py` | the EU mirror of `casenaming.py` — `case_number` (CELEX → court case number, "62018CJ0311" → "C-311/18", also T-/F- courts), `given_name`/`case_name`/`case_citation` (curated usual name, page heading, "C-311/18 (Schrems II)" inbound-citation label) from the shipped `eurlex/data/casenames.json` snapshot; read identically by eurlex's parse-time label stamp, the catalog row and the page heading |
| `labels.py` | one place for every source's four reader-facing name forms — `short_id` (eyebrow), `short_title` (h1), `official_title` (dl.meta "Titel"), `descriptive_label` (compact citing form, I1) — dispatched per source (`_sfs`/`_eurlex`/`_dv`/`_forarbete`/`_foreskrift`/`_avg`/`_hudoc`/`_coe`/`_icrc`/`_untc`/`_icc`, else `_generic`) over the artifact dict's own parse-time stamps plus the curated datasets (`NAMEDLAWS`, `COE_NAMES`, `ICRC_NAMES`, `UNTC_TREATIES`, the new `eurlex/data/treaties.json` for EU primary law); read by both `render.py` (every per-document page) and `catalog.py` (the stamped `descriptive` column) so the two can't drift. A document with no title of its own falls back to its identity, never to the URI tail — the legacy EU court pages carry no title line, and `_local(uri)` standing in for one headed 3 373 judgments "ext/celex/61979CJ0155" instead of "C-155/79". `sfs_is_statute` also lives here (moved from `facets.py`): the title-based lag/balk/grundlag test that stamps every SFS row's catalog `kind` (`lag` vs `forordning`, no longer one uniform `law`) and, through it, its rung in the norm hierarchy (`catalog.norm_level`) |
| `coe.py` | shared Council of Europe identity grammar: ETS/CETS number → `ext/coe/{number}`, article/subarticle fragments, and HUDOC's `8` / `6-3-d` / `P7-4` facet codes → the same treaty provision URIs produced by the Treaty Office vertical |
| `coe_ids.py` | dependency-free CoE article-fragment grammar (`article_fragment`) factored out of `coe.py` so `lib.lagrum` can use it without closing the `lagrum → coe → catalog → markdown → lagrum` import cycle; also used by `sfs/nf.py` |
| `treaty_ids.py` | the UN/IHL counterpart of `coe_ids.py`, same reason: a dependency-free `article_fragment` (plus `arabic`/`roman` numeral conversion) two producers share — `lib.treatyref` (the courts' English citations) and `lib.lagrum` (which links "artikel 24 i barnkonventionen") — without `lagrum` importing `treatyref` and closing `lagrum → treatyref → catalog → markdown → lagrum` |
| `eu_structure.py` | the one EU-act sub-article anchor grammar, entered through `Anchors` (the running article/paragraph/stycke/point context; `anchored_blocks`/`flatten` walk it over a document, `subarticle_key`/`stycke_key`/`first_stycke`/`citable` are its internals), shared by the eurlex parser, the renderer and the wiki guidance layer. A stycke (sub-paragraph) anchors `9.2.S2` the way SFS anchors `P2S2`, and a numbered paragraph answers to both `9.2` and its first-stycke alias `9.2.S1` — `anchored_blocks(…, aliases=False)` suppresses the alias where the walk enumerates nodes rather than the names that reach them (`nest`, the parse-time tree builder, stays in `eurlex/structure.py`) |
| `regeringen.py` | shared regeringen.se harvest knowledge — the doctype table (`TYPES`: url segment, taxonomy category id, identifier regex), the `ul.list--block` listing walk (`listing_items`), and the identity rules for the doctypes regeringen.se publishes without a usable series number (`pm_identity`, moved out of `forarbete/download.py`; `lr_identity`; `slug_number`, which reads the number off the landing slug where the printed one is malformed — "SOU 2023 27", no colon — and only when the slug's own prefix names the type) — all must mint the same basefile for the same document whichever page names it, so forarbete and remisser share the rule rather than each guessing. `regeringen_path` normalises a url to the path the curated tables key on; used by `forarbete/download.py` and `remisser/download.py` |
| `treatyref.py` | treaty citations in an international court's English text, as artifact `references` (`references()`, document-level) and inline-linkable spans (`spans()`, the `(start, end, uri)` projection the artifact's `runs` are built from). One matcher for both courts (rule:second-use-goes-to-lib): `icj` names the instrument it applies, `icc` cites the Rome Statute by article on nearly every page. Article-level where the text names one (`ext/icrc/585#A74`) and instrument-level otherwise; roman numerals resolve too, since the Genocide Convention runs Article I–XIX and the ICJ cites it that way (the numeral grammar itself is `treaty_ids.article_fragment`). An article binds to the **nearest** instrument named, and a name that *follows* it wins over a nearer one before it — "article 3 common to the Geneva Conventions" is an article of the Geneva Conventions however recently the Rome Statute was named — and never binds backwards past an "of \<Instrument\>" it could not match. A caller adds its own unambiguous short forms ("the Statute" is the Rome Statute inside an ICC decision and the Statute of the Court inside an ICJ one) — as plain names, or as compiled patterns where the form needs its own guard (hudoc's "the Convention" stands down before "Convention on …"). A **generic name** (`generic_names` + `generic_context` in the data) binds only where its family is named within `CONTEXT_WINDOW`: every treaty family numbers its protocols, so "Additional Protocol II to the Geneva Conventions" binds where the bare "Second Additional Protocol to this Convention" on a CoE page stays unlinked. Names come from `data/treaty_names.json` through `lib.datasets` — the Geneva/UN instruments plus the ECHR and its protocol series (`coe/009` …) — so no source is imported |
| `harvest.py` | shared incremental-download core — `HarvestWatermark` (begin/complete lifecycle, never-regress date save, crash-safe `dirty` flag that disables the consecutive-hit stop but not the date-conclusive one) + `walk`/`Skip`/`ItemKey`/`guarded_enumerate` (the newest-first download loop over an enumerate/resolve pair); each source states its own `lookahead_limit`/`safety_days` window (dv: 365-day safety window, ~5000-item lookahead; forarbete/riksdagen/foreskrift/avg-jo: 14 days/20 items); plus the record-store layer every flat-listing source shares -- `write_record`/`store_record`/`record_unchanged` (the one home of the artifact-JSON write flags), `pdf_path`/`select_pending`/`walk_records` (a complete listing of records each naming one PDF; rs and edpb call it with their own body-fetch callables) and `dispatch_scopes` (the per-scope sync fan-out used by avg/rs/edpb); used by `dv/download.py`, `foreskrift/harvest.py`, `avg/download.py` (jo), the folkrätt downloaders (hudoc/coe/icrc/untc/icc), `rs`/`edpb`, and directly by `forarbete/download.py` + `forarbete/riksdagen.py` (also driving `forarbete/rskr.py`) |
| `browser.py` | detached headful-Chrome transport for F5/Shape-protected public sources — navigate with no Playwright/CDP client attached, wait the source-configured settle interval, then attach briefly to read the completed DOM or exact browser-cached PDF bytes; selected only by the SKVFS and MTFS `Agency.browser` configs; on a headless host (no `DISPLAY`) `_ensure_display` auto-starts a private Xvfb virtual framebuffer and runs Chrome headful against it (needs `Xvfb` on `PATH`; a genuinely headless host without it is a fail-fast, not a silent fall back to `--headless`) |
| `catalog.py` | the SQLite catalog (`documents`/`links`/`fragments`/`genomforande`/`fk_kommentar`/`correspondence`/`directive_correspondence`/`definitions`/concept tables) built by `relate`, derived and rebuildable — `correspondence` holds the `.corr` edges incl. `ikrafttrader` (when a same-law renumbering took effect, driving the temporal inbound split), `directive_correspondence` the EU-act lineage each act's artifact carries (`eurlex/correspond.py`), written per document by `_index_document` and walked transitively by `predecessor_atoms` under `caselaw_anchored` (the statute-wide, pinpoint-precise case-law rail assignment); `dangling_targets` ranks link targets the corpus cites but holds no `documents` row for, most-cited first — the want-list `lagen eurlex backfill` downloads against; `definitions` holds what each act says its defined terms mean — the defining sentence beside the `dcterms:subject` edge (`definition_sentences`), which the begrepp page prints under the title and the curated description; an SFS stycke often holds more than the definition, so the unit stored is the sentence carrying the term, while an eurlex definitions-article point is the definition whole; `documents.path` is stored `data_root`-relative so the catalog is portable across hosts; a `meta` table records the absolute `data_root` only when the catalog lives outside it (`catalog_root != data_root`, `config.CATALOG_ROOT`) — a colocated catalog (the default) records nothing and resolves paths against the catalog file's own directory, preserving rsync-portability (`catalog.data_root(con)`); a full rebuild (missing catalog, or `--force` over the whole corpus) is built in an `EXCLUSIVE`/journal-`OFF` scratch and atomically swapped in (`catalog.quiesce_wal` + `build._swap_catalog`), incremental relate stays in-place under WAL; `connect_ro`/`load_artifact`/`artifact_updated` are the shared read-only serving-layer helpers used by both REST and MCP; the `graph_*` helpers (`graph_inbound`/`graph_outbound`/`graph_anchor_inbound`/`graph_anchor_outbound`/`graph_out_totals`/`graph_internal`) answer `/api/v1/graph` — one row per neighbor *document* rather than per citation, and `graph_internal` the self-citations the neighbor queries exclude, for a fragment uri's own §/article graph; `norm_chain` (`rebuild_norm_chain`, run once per `relate`) is the subordinate→authority edge table across the norm hierarchy (EU-rätt → lag → förordning → myndighetsföreskrift) built from the typed `rpubl:bemyndigande`/`rpubl:genomforDirektiv`/`rinfoex:kompletterar` edges — it has no reader yet (a rail rendering it was tried and withdrawn), kept as data for a future editorial `ai-*` command rather than a page feature |
| `page.py` | the **shared page kit** every source's renderer stands on: `Site` (the render context — the catalog plus the set of document URIs that exist, so a citation to a document we don't have renders as plain text; plus the cross-document layers a page shows but doesn't own), the generic node walk (`render_node`/`render_runs`, keyed on each artifact node's `type`, so the SFS structure tree and the DV body render through one walk), the context rail (`Rail`/`RailSection` + the margin builders) and the page shell (`page`/`page_context`, the `dl.meta` block, the TOC collector). Knows no source by name — what varies per source lives in that source's own `render.py`; re-exports `human_fragment` from `pinpoint.py` since `eurlex/render.py` and `stats/{scan,compute}.py` import it from here |
| `pinpoint.py` | fragment id → human pinpoint (`human_fragment`, "K2P16S5" → "2 kap. 16 § 5 st"), factored out of `page.py` so the serving layer can name a provision without importing the renderer (`lib/pins.py` labels a citation-resolved search hit with the provision it points at); `pinpoint_label` additionally types the EU anchor grammar ("32" → "artikel 32", "6.1.c" → "artikel 6.1 c", "9.2.S2" → "artikel 9.2 andra stycket"), and owns the `STYCKE_ORDINAL` table `page.py` imports back for the same prose on an SFS pinpoint. `unit_anchor` collapses a fragment to the pinpointable unit it belongs to ("K2P16S5" → "K2P16", "A6P1" → "A6") — the node id `/api/v1/graph`'s internal-graph view groups a document's own citations onto. Same split rationale as `coe_ids.py` |
| `render.py` | **site assembly** — the corpus-wide half of `generate`: the frontpage, the folkrätt/EU-rätt landings and their cross-source axes, the Atom feed pages, the shipped static chrome, and `generate_site` (the render driver: freshness planning against the caller's manifest, then the pages across a process pool). Individual document pages are rendered by their own source (`<source>/render.py`), dispatched through the `SOURCE_RENDERERS` registry `build.py` composes and hands in — `lib/` may not import a source, so the table is built one layer up. All page/node *structure* markup lives in Jinja templates (autoescape + StrictUndefined); only algorithmic emission (`render_runs`, `_citer_line`, the margins' citation prose) formats markup in Python (rule:markup-in-templates) |
| `tpl.py` | the shared Jinja environment (`ENV` over `lib/templates/`, plus `environment(package)` for a vertical's own template dir) — separate from render.py so `lib.feeds` and `api/*` can render templates without importing the renderer |
| `templates/` | the render layer's markup: `page.html` (site chrome: masthead, frontmatter, grid, mobile toolbar — every page's base template), (each source's own page template now lives in `<source>/templates/`, extending page.html with the page body), `nodes.html` (document-tree leaf macros the node walks emit through), `partials/` (`meta`/`banners`/`panels`/`rail` macro libraries), `listings.html` (frontpage, folkrätt, browse, the feed screen + its source selector + the feed directory), `sok.html`/`admin.html`. Template edits re-stale generate (`build.GENERATE_CODE`); output equivalence is checked browser-level by `tools/render_equivalence.py` (HTML5-normalized snapshots + compare) |
| `assets/` | the browser-facing static chrome as real on-disk files (`style.css`, `editor.css`, `matomo.js`, `dom.js`, `drawers.js`, `scrollspy.js`, `search.js`, `popover.js`, `fullsearch.js`, `versions.js`, `faksimil.js`, `grafik.js`, `pdf.js`, `editor.js`, `robots.txt`) — formerly embedded string constants in `render.py`. `render.write_assets` ships them through the same Brotli precompression as pages: the JS files are concatenated in load order into a single **`script.js`** bundle (the page links one URL, so adding a module changes only the bundle, never the per-page HTML — it publishes via `generate --assets-only`, not a full regenerate — and because the tree it writes into is bind-mounted from the host rather than baked into the image, `.github/workflows/deploy.yml` runs that command in the container after every push, or a code deploy would leave the browser on the previous bundle), `style.css` is written as `fonts/fonts.css` + `style.css` + `editor.css`, `robots.txt` copied as-is. `fonts/` holds the **self-hosted** Inter + Source Serif 4 woff2 files (variable fonts, one per family/style × latin/latin-ext with a `font-weight` range covering the weights the pages use, served under `/fonts/` and stored plain — woff2 is already compressed); the pages load **no third-party resource at all**, and the only client state is the sanctioned theme localStorage plus the editor's `lagen_editor` session cookie. `matomo.js` is first in the bundle (it depends on nothing, and an uncaught error anywhere in a concatenated bundle stops everything after it): the cookie-less, feature-detection-less Matomo snippet legacy lagen.nu has always used, pointed at a **same-origin** `/matomo/` (an nginx block on the ferenda.lagen.nu vhost proxying the shared Matomo container) and gated on a hostname→site-id table, so only a host registered in Matomo reports anything and a dev serve stays silent; its machine-facing counterpart is `api/analytics.py`. `dom.js` (next in the bundle) is the scripts' shared vocabulary (`window.lagenDom`: own-document anchor resolution across split-view panes, id-attribute selector, landing flash, JSON-island parse); `search.js` leads the ⌘K palette with instant *local* hits (a terse pinpoint — `4`, `11:2`, `art 5`, `(42`, `skäl 42`, `bilaga III` — resolved against the current page's own anchors, no network); `popover.js` gives every internal reference a hover preview -- a provision link shows the target's rendered text lifted from the fetched page, a whole-document link asks `/api/v1/card` for the citing name and the relate-stamped snippet instead of fetching a megabytes-large page (falling back to the page-lede extraction for pages without a catalog row) -- replacing the old title-attribute tooltip whose ↗ escalates to a split reading view — the target document in its own pane with its own TOC/rail and scrollspy instance (`lagenScrollspy`), resizable/reorderable/closable; `versions.js` carries the "jämför med tidigare lydelse" diff select *and* re-decides, against the reader's own clock, which of a consolidated statute's sibling variants is out of force — the build stamps `temporal-expired`/`temporal-pending` from its own date, which a nightly rebuild keeps within a day, and the `data-upphor`/`data-ikraft` attributes let a page cached past midnight still dim the right half; `grafik.js` opens any recovered graphic (a formula, map or road sign the `.graphics` layer cropped out of the published PDF) full size in a lightbox — the crop is rendered as a `button.grafik-open`, so it is keyboard-reachable, and the overlay asks the endpoint for the full-size render (`stor=1`) rather than stretching the thumbnail, which for the 325 signs 2007:90 prints on one page (of the 326 it lists) is the difference between a 3.5rem cell and a legible sign; `pdf.js` injects the "Spara som PDF" printer icon on the short-id row of the TOC rail and builds its options dialog (one or two text columns, TOC page numbers, the SFS amendment/transition register on an SFS page, which rail context kinds to print under each provision, visa/ladda ned) — the kind list itself is read lazily from the page's own `#lagen-context` island, never hardcoded; choosing two columns disables TOC, register and context, since that compact layout omits all three. The dialog renders nothing itself: it opens the export's own waiting page (`/internal-api/v1/pdf/vanta`, `api/pdfjob.py`), which follows the render and becomes the PDF. `style.css`'s `@media print` block is the paged-media design both the browser's own Skriv ut and that same export render through: A4 as a **spread** of mirrored book pages, not one fixed margin — a right page puts text at 28 mm (117 mm wide, about 67 characters a line) and the apparatus column at 145 mm (55 mm wide); a left page mirrors them, apparatus at 10 mm and text at 65 mm; a three-part running head (eyebrow on the fold, title in the middle, chapter · § on the open edge), set in italic serif through `@page :left`/`@page :right` boxes, and the folio alone on the outer corner; the apparatus is set small in the outer margin beside its provision — an article/aside grid pair on the standalone CSS path, a fixed table row on the WeasyPrint path (`body.pdf-weasy`; `api/pdf.py::_mirror_margin_notes` moves the note cells to the verso outer edge after layout) — so it can run past the foot of the page and continue overleaf; a `kolumner=2` compact layout (`@page compact`, 8.25 pt, two 89 mm columns with an 8 mm gutter) drops the apparatus and fits more text per page instead; paper-only `.print-toc`/`.kontextblock` markup `api/pdf.py` injects, browsers ignoring the paged-media rules they don't implement |
| `feeds.py` | one `Dataset` per browsable source at `/dataset/<alias>/feed[.atom]`, carrying a stable public alias where one exists (`forarbeten`/`myndfs`/`myndprax`/`myndrs`/`keyword`/`euvagledning` → internal source names) and its source name otherwise + the feed renderers — Atom stays Python string-building (XML with a byte-stable contract), the HTML twin is the site's feed screen: the entries in the reading column, `nav()`'s source selector in the left rail, so a reader lands on one feed and can reach every other. Static generation, the live query-parameter endpoints in `api/app.py` and the editorial news feed (`site/render.py`) all render through it, so the three cannot drift |
| `dump.py` | NDJSON bulk corpus export — one gzipped, self-contained JSON line per artifact, no transformation |
| `search.py` | full-text search over the parsed corpus on OpenSearch 2.x — standalone per-unit documents collapsed by `doc_uri`, no parent-child join; facet buckets (source/kind/year) via `post_filter` aggregations, prefix-matching queries (`prefix_query`), an `INDEX_FORMAT` version folded into each unit's stored freshness key so an index-schema change (like adding the year facet) reindexes on the next incremental pass without a blanket `--force`. A source listed in `build.UNSEARCHED` (kommentar, whose prose reaches the reader only through the annotated act's rail and serves no page of its own) has its stale units purged rather than indexed, so a search hit for it can never be a dead link. Bulk writes retry with backoff on both paths — `--jobs > 1` fans them over worker threads each running `streaming_bulk`, not `helpers.parallel_bulk`, which accepts no `max_retries` and so let a chunk the cluster rejected under load go silently missing from the index; `lagen index` now exits non-zero when any unit failed |
| `facets.py` | faceted navigation over the catalog — `tree`/`group`, the single source shared by the REST API (`/facets`) and the static browse pages; `document_year` (a `year` search facet, reusing browse's own per-source year extraction) is shared with the indexer; `kind_labels()`/`SOURCE_LABELS` derive the flat catalog-`kind` → reader-facing-label map (and its singular form, for one search hit rather than a bucket) from the same `SCHEMES` the browse pages use, plus the föreskrift series registry — `/api/v1/search` serves them as `kind_label` per result and `label` per facet bucket, so the search UI keeps no second copy to drift out of sync; `fs_live_series` follows a föreskrift succession chain to the slug that carries its documents today, and `browse_view` folds a föreskrift's base (`/grund`) version out of the listing under its consolidated sibling; `FLOW_GROUPS`/`FLOW_GROUP_NAMES`/`flow_group` are the citation graph's node vocabulary — a catalog (source, kind) collapsed to fifteen groups (eurlex splits three ways, the international-law sources merge two ways), shared by `stats/compute.py`'s sankey measure and `/api/v1/graph`'s group filter so one map says what a node is |
| `pins.py` | citation-shaped query → search-hit-shaped resolved targets (`resolved_results`/`merge_pinned`), shared by the REST `/search` endpoint and the MCP `search`/`resolve_citation` tools. A pinned hit answers a *pinpoint*, so it carries the provision it landed on (`pinpoint.pinpoint_label`) and that provision's own text as its snippet — one artifact read per citation-shaped query, since there is at most one pinned hit and it is the query's answer |
| `resolve.py` | turns a ⌘K query into a precise, fragment-deep resource target — four resolvers over `lib.datasets`, tried in priority order: a CoE treaty short name + article pinpoint (`resolve_treaty`, "EKMR 6" → `ext/coe/005#A6`; fires only when the query carries a pinpoint, so a bare treaty name still resolves as the Swedish incorporation act), then SFS nicknames/abbreviations + citation-engine pinpoints, EU-act short names (its article tail now also accepts a terse bare number, "GDPR 28", not just "GDPR art 28"), and case nicknames |
| `layout.py` | single source of truth for where a `(source, basefile)` document lives, on disk and on the web (`downloaded`/`artifact`/`page_relpath`/`page_url`); `resolve_basefile(source, basefile, *alternates)` settles a cross-source basefile against the artifact tree: case-insensitive respelling (regeringen.se prints a diarienummer's department prefix inconsistently across its own pages — "JU2026/01595" vs "Ju2026/01595"), plus further candidates tried in order, since a remiss page cannot tell whether forarbete keyed a promemoria on its dnr or on the landing slug (~30% are slug-keyed), used by `remisser/ai_analyze.py` and `lib/page.py`'s `_remiss_indexes` |
| `datasets.py` | canonical filesystem paths of the curated named-resource datasets (`NAMEDLAWS`/`NAMEDACTS`/`NAMEDCASES`/`NAMEDEUCASES`/`CASENUMBERS`/`COE_NAMES`/`ICRC_NAMES`/`UNTC_TREATIES`/`ICC_DECISION_TYPES`/`TREATY_NAMES`/`FS_SERIES`/`EMD_CASES`/`EMD_RESPONDENTS`/`JO_ARSBERATTELSE`) that ship in the package tree; `load_emd_cases` is the pure-JSON reader for the committed hudoc case-name snapshot, `load_emd_respondents` the Swedish respondent-name map, `load_jo_arsberattelse` the committed JO ämbetsberättelse page→dnr snapshot (`avg.arsberattelse` writes it, empty dict if not yet generated), `load_casenumbers` the committed case-number→decision snapshot (`dv.casenumbers` writes it) |
| `concepts.py` | begrepp (concept) normalization — a hand-rolled, corpus-aware Swedish de-inflector collapsing inflected term forms onto one canonical `begrepp/<Name>`, plus the hand-edited override file `data/begrepp_aliases.json` |
| `diff.py` | the "jämför lydelser" version-diff view — block-align + word-level `<ins>`/`<del>` over two parsed artifact versions, computed on demand |
| `history.py` | read layer over the SFS version-history sidecar + amendment-register join, shared by the renderer's compare panel and `/api/v1/document/versions` |
| `inbound.py` | the per-document inbound-citation tree under `data_root/inbound/`, written by `generate` (`render._write_inbound`, riding the page render — the same staleness that re-renders a page is what makes its citations stale) and read by REST `/document/inbound` + MCP `get_incoming_citations`. Holds the **complete** set for a document *and every provision in it* (`links.to_root`), one row per (citing document, citing spot, provision cited) — the rail's two reductions stay in the renderer. Ordered by `RAIL_SECTION_ORDER ∩ INBOUND_GROUPS` so the file order follows the site's panel order automatically (case law first for a statute), then by each section's own convention; the order is total and build-independent, so API `offset` paging is stable. `scoped`/`exact` are the serving-side scope filters (a fragment's subtree is a prefix continued by an uppercase segment or the EU dot — never a digit, never the lowercase suffix of an inserted "18 a §"). Keyed by `layout.page_relpath`, so a request finds the file from the uri alone. Absent means uncited — which holds only because `write` also *removes*, because a full run's `render._sync_inbound_tree` covers the 92k cited-but-uncatalogued targets that have no page and sweeps orphans, and because that sweep leaves an `inbound/.built` marker the serving layer checks (a missing tree is a 503, not "nothing cites anything") |
| `artifact.py` | the artifact-*writer* counterpart to `text.py`: block streams -> structure nodes -- `scanned_nodes` (citation-scanned rubrik/stycke runs), `numbered_nodes` (plain runs with de-duplicated anchors, `unique_id`) and `footnote_nodes`; the one home of the node convention avg/rs/edpb/hudoc/icc model.py and `coe/parse.py` project their bodies through, so per-source drift is confined to the parameters they pass (anchor rule, note class). `numbered_nodes` takes an optional `refs_for` (text -> [lagrum.Ref]) so a source that *can* resolve some of its own English prose -- `hudoc/citations.py`'s case-law cross-references, `icc/icj`'s treaty and sibling-filing citations via `lib.treatyref` -- carries those as inline links in its runs; a source with none passes nothing, and the run stays one unscanned block as before |
| `text.py` | one definition of "the plain text behind an artifact's inline-run structure", shared by the catalog (tooltip snippets), search indexing and the bulk dumps; also owns *which* body is the presented one (`presented_consolidation`/`body_sections` — a föreskrift's latest parsed konsoliderad version replaces its base `structure`), the selection render, the citation walk (`catalog.artifact_links`), search and the MCP reader all read through. `sentences(text, clause_breaks=False)` is the one Swedish-abbreviation-aware sentence splitter (moved here from `labels._first_sentence` once a second caller needed it whole, rule:second-use-goes-to-lib); `labels._first_sentence` now delegates to it, and `remisser/ai_analyze.answer_units` calls it with `clause_breaks=True` to number an answer's quotable units for the model to cite |
| `mdtext.py` | the artifact as **markdown** — text.py's readable-text sibling, behind `/api/v1/document?format=md` and the MCP `get_document` tool (where markdown is the default). Dispatches on node `type` only, tuned for the SFS shape (bolded `beteckning`, rubrik levels as headings, the amendment register as its own section), the eurlex shape (division/article headings, the act's own `1.`/`a)`/`(42)` markers) and the förarbete shape (avsnitt headings, lagtext markers, fotnot italics, ruta blockquotes); an unknown type degrades to its runs as a paragraph plus its children. Link runs become inline `[text](uri)` links, so the citation graph survives into the text |
| `compress.py` | transparent Brotli-only compression for `artifact/`/`generated/`/`downloaded/`, written atomically via `util.write_atomic`; the `downloaded/` tree skips already-compressed payloads (`INCOMPRESSIBLE_SUFFIXES` — PDF/zip/docx/images/…) and sub-512-byte files, storing them plain, and hosts the compress-aware `glob`/`list_basefiles` used by downloaders and parsers walking that tree |
| `facsimile.py` | on-demand page facsimiles: one source-PDF page → a retina PNG (`pdftoppm`, 150 DPI), rendered lazily into the `cache/facsimile/` disk cache (evicted externally); `cached(source, basefile, pdf, page, bbox=None, dpi=CROP_DPI)` is the one cache entry point; with `bbox` it crops a region of the page (`render_region`) instead of rendering the whole thing. A *crop* carries its own resolution, twice the page DPI inline (`CROP_DPI`) and four times it for the lightbox (`CROP_DPI_LARGE`, the `stor=1` query param), because the same crop is shown both as a thumbnail among hundreds and alone at full size — the cache path is keyed by the DPI, so raising one lands on fresh files rather than serving the old resolution until eviction (the SFS graphic-crop path, and since 2026-08 a förarbete's own embedded illustrations via `/api/v1/facsimile`'s `bbox=` query param), `page_count` bounds a PDF's pages; served by the API's `/api/v1/facsimile` (+ the legacy `/prop/2022/23:10/sid1.png` path grammar) and `/api/v1/sfs-graphic`, and toggled inline by the page-number buttons on förarbete pages |
| `pdftext.py` | shared font-aware PDF text extraction pipeline for the PDF-bodied verticals — `pdf_pages`/`flat_lines` (poppler `pdftohtml -xml`, `hidden=True` recovers an OCR text layer pdftohtml otherwise drops) → `page_paragraphs`; `pages_with_ocr` is the OCR-aware page reader the letter-shaped corpora share (hidden text first, `repair_pdf` — a ghostscript rebuild of an unreadable cross-reference table, cached beside the source as `.<stem>.repaired.pdf` — when poppler refuses the file outright, ocrmypdf when a PDF still yields nothing); `pdftotext_text` (plain `pdftotext`) is the fallback route for the scanned corpora (soukb, propkb's scan-only props) whose OCR text layer `pdftohtml -xml` renders empty or errors on. **Both converters' output is cached** brotli-compressed under `layout.PDFCONV` (`cache/pdfconv/`, `layout.pdf_conversion(pdf_path, kind)` keyed by the PDF's path under the data root and `kind` — `"xml"`/`"hidden.xml"`/`"txt"` each a separate entry, one stale once older than its PDF): that one subprocess is 53-91% of a PDF document's parse time — 11.8 s for one 4 MB born-digital SOU — and a downloaded PDF is immutable, so every re-parse after a parser change was re-running it for nothing. The XML is far *smaller* than the PDF (a 120 MB scan holds almost no text and yields 40 kB), so the whole cache is ~1% of the downloaded bytes: ~6 GB against 609 GB of corpus PDF. Warm, a conversion that took 11.89 s reads in 0.01 s → a vertical's own `classify`. `page_number_candidates`/`printed_pages` derive the PDF-page ↔ printed-page mapping from marginal folios as a *running piecewise offset* (a PDF page equals its printed page until a folio proves otherwise; the implied offset holds until the next trusted reading). A page in isolation cannot say which of its numbers is the folio, so the reader offers **candidates** and the sequencer picks the one the running numbering expects: a digits-only margin line is *strong* evidence and the only kind that may establish or move the count; a number at the edge of a line of prose is *weak* and may only confirm an exact match. That is what finds a folio glued to a footnote ("Senaste lydelse 2002:621. 115", where the only digits-only line is the footnote marker "2") without letting a copyright page's "Stockholm 2013" or a reprinted EU act's "L 96/119" drag the numbering. The identifier stripper tolerates case and the letter-spacing budget propositions typeset (`PROP. 2017/ 18: 100`). The first reading applies retroactively to unnumbered cover matter; a small *forward* shift (≤ `PAGE_SHIFT_TOL`, an omitted blank leaf) is adopted at once and a large one needs a corroborating peer. Any **backward** step is a section restarting its own numbering, however small — direction is the signal, not size: an eight-page body followed by a four-page bilaga steps back only two pages, and adopting that as a shift re-minted the body's own `#sid1`..`#sid4`. The section ends at the page the step *first appeared on*, not the one that corroborated it, or the new section's own page 1 would keep a fabricated body anchor. What follows is numbered per **bilaga** when the running header names one (`bilaga_labels`, requiring the label to repeat on an adjoining page so a TOC line is not mistaken for a running head): each bilaga is its own section, because where documents restart at all every bilaga restarts at 1 (prop. 2021/22:100 has four printed page 1s), and its pages anchor `#bilaga{B}-sid{N}` instead of colliding with the body. A restart nothing identifies still yields no anchors. Measured over 180 sampled PDFs: pages carrying a page number went from 51% to 99%, 0 lost (used for förarbete anchor citations, §7g finding 04). `pdf_figures` (`Figure`/`is_figure`) reports the images poppler embeds that are document content rather than furniture (inside the text margins, large relative to the text measure), converted to PDF points by `points_from_pdftohtml` for the facsimile crop; `Para.boxed` flags a paragraph set to a narrower measure than the body — a förarbete's ruled `ruta` ("Regeringens förslag:", "Bedömning:"), now recognised per contiguous inset run (`box_base`: ≥2 consecutive lines sharing a left edge (`aligned`) and filling most of the body's measure (`measured`, `BOX_MIN_MEASURE`)) rather than off a single page-wide mode, so a page given over to a ruled box no longer outvotes the body for its own margin (`MARGIN_SHARE`, the leftmost start a real share of the page's lines agree on) and measure (the furthest right edge among lines starting there), and an inset chart column or block quotation no longer misreads as a `ruta`. `page_boxes` returns each page's `(width, height)` in pdftohtml's own pixel space — the ruler `pdfinfo` gives the wrong answer for when a PDF's CropBox differs from its MediaBox (2007:90's road-sign source), needed to convert a text-layer box to the PDF points a crop takes. `drop_marker_lines`, called from `page_paragraphs` itself so every PDF-bodied source gets it, drops a superscript footnote-reference marker that lands as a line of its own — its raised baseline sorts above the text it follows, so left standing it set the paragraph's size, forced a mid-sentence paragraph break and printed ahead of its own text — keeping only a footnote's own leading number (told apart by sharing its `top` with a footnote-sized line). Four further cleaning steps, all source-agnostic — no per-source header string to name — used by remisser, where each of ~90 organisations answers on its own letterhead: `strip_page_furniture` finds a running header/footer by shape (digit-masked text recurring across most pages, sitting in the page's top/bottom margin, no larger than body size) rather than a passed-in identifier; `drop_footnotes` removes footnote text and its superscript reference markers by the same size-drop test `pdftext` already uses to find förarbete's footnotes, applied to discard them instead; `join_across_pages` rejoins a sentence a page break split across two reflowed pages, and now also closes a word a page break hyphenated (dehyphenated, unless the hyphen is a hanging Swedish compound coordinator — "studie- och yrkesvägledare" — told apart by the conjunction after it); `strip_addressing` removes a letter's masthead/reference-line/contact-block by composition (address tokens — e-mail, phone, postnummer, org.nr — and reference labels like "Dnr"/"Datum"/"Postadress"), which repetition cannot catch since a masthead is printed once, not on every page |
| `tabell.py` | table detection from the `pdftext` Line/run geometry, shared by the PDF-bodied sources. Two detectors, because a PDF prints two unrelated kinds of table. `split_generic` (förarbete) reads a **data table** — a budget table, a bilaga listing, a multi-column enumeration — on numeric evidence: ≥3 consecutive multi-cell lines agreeing on ≥2 column starts, and non-leading columns holding mostly amounts, years and rates (TOC dot-leader lines and page-margin markers excluded), so prose that merely breaks into wide-gapped runs stays prose. `split_two_column` (föreskrift) reads a **two-column prose table** — an ordförklaringar table's term and its sentence-long definition — which numeric evidence would reject: it keys on the layout instead (two columns and two only, a wide gutter, a left column of short terms), places cells run-by-run against the columns the opening line fixed rather than by gap-splitting (a long term reaches to within a few units of the gutter and the gap rule would read the line as one cell), and takes the *vertical step* as the row boundary so a term that wraps over two lines keeps its definition beside it. Cells are assembled with `pdftext.join_runs` (spacing by geometry, since a run boundary is not a word boundary) and wrapped lines joined with `dehyphenate` — the two rules `page_paragraphs` applies to prose and which cells bypass; a plain space at each seam instead published "Jord- bruksverket" and "S akområdesutbildning". A term cell assembled from more than two font runs ends the region: MSBFS 2020:9 sets the ADR dangerous-goods tables in landscape with rotated headers, and poppler returns each narrow column as its own syllable fragment. `merge_continued` joins a table split across a page break for either detector, dropping the repeated header |
| `llm.py` | shared client for the OpenAI-compatible chat-completions endpoint used by the opt-in `ai-*` passes — Berget by default, or any compatible server `llm_base_url` points at (e.g. a local llama.cpp, `docs/local-llm.md`) (eurlex/wiki annotate, remisser ai-analyze, sfs ai-includegraphics, forarbete ai-genomforande) — `complete`/`complete_thread` plus `author`, the source-agnostic validate/self-repair-retry loop; `images=`/`vision_content` add vision-model support (page images alongside the prompt), used by `sfs.graphics.localize_group`; a transient 5xx from a hosted endpoint is retried (3 attempts, backoff) before propagating, and the process-wide `USAGE` tally (calls/prompt/completion tokens, from the endpoint's own `usage` object) feeds the ai-* actions' cost reporting; `json_values` reads every consecutive top-level JSON value out of a reply (a model that appends a second object or trailing prose after a complete answer no longer loses it to "Extra data"), and a reply that fails validation twice is persisted to `<data>/llm-debug/rejected-*.json` (full thread + reply, images elided) before the raise so the bytes survive for diagnosis. A per-document provenance window (`start_record`/`record`/`rearm`, a `_Window` dataclass) accumulates every call `complete_thread` makes since the last `start_record()` — host, model, sampling, token counts, wall-clock span and `prompt_sha` (a hash of the prompts actually sent, text and images, not of the code that built them) — which `annstore.write` reads via `record()` and stamps into the written layer's `meta.run` |
| `annstore.py` | the curated store for authored layers (`.ann`/`.corr`/`.graphics` files from the `ai-*` actions and the mechanical `sfs table-correspond`) — `WIKI_ROOT/ann/<source-dir>/<relpath>`, mirroring the artifact tree's relpath grammar; every layer is an envelope (`meta`: status generated/verified/derived — `derived` marks a layer computed mechanically rather than authored by a model, which `publishable` lets reach the render as it stands, model, generated date, input sha256 hashes, and `run` — endpoint host, model, sampling, token counts, wall-clock span and a prompt hash, `lib/llm.py`'s `record()` — stamped only when an `ai-*` action opened a recording window and it saw a call, so a `derived` layer carries none) beside the payload's own keys; `guard` refuses to regenerate a `verified` layer without `--force`, `drifted` derives staleness from recorded input hashes rather than storing it (`run`'s prompt hash is not one of them — recomputing a rendered prompt would mean running a source's prompt builder, which `lib/` may not do, so a prompt change shows up as a changed `run.prompt_sha` across runs, not as staleness); `write` also calls `llm.rearm()` after stamping, so authoring several layers in one process gives each only its own calls; `meta_extra` merges source-specific envelope fields (the `.graphics` layer's `through` provenance horizon). Per-entry curation (a `"verified": true` flag on one `.graphics` gap) is the source's concern — `sfs.graphics.plan_localization` keeps a verified entry only while its source still matches the resolved provenance and hands `write` the final payload, so `write` stays a blunt writer; inventoried by `lagen ann status` |
| `markdown.py` | parse the git-backed wiki markdown (commentary/concept) into the shared inline-run artifact shape — the markdown counterpart of `wikitext.py`. Block structure comes from **markdown-it** (`MarkdownIt("commonmark", {"html": False})`), `blocks()` walking its token tree into `rubrik`/`stycke`/`lista`/`avskiljare` blocks; the inline layer (links + lagrum citations) stays hand-rolled. `site/parse.py` builds the same engine but `.enable("table")`, since its model has a `tabell` node this one does not. `strip_comments()` drops HTML comments (`<!-- … -->`), shared by both markdown consumers — with `html: False` an unstripped comment would otherwise print as literal text |
| `wikitext.py` | parse MediaWiki dump pages into the same inline-run shape; retired from the live pipeline, kept only as the migration/diff tools' reference |
| `runlog.py` | run instrumentation behind the ops dashboard — `runs.ndjson`/`errors.json`/`status.json` under `DATA/.build/` |
| `errorlog.py` | the served-site HTTP error ledger — append-only ndjson at `DATA/.build/httperrors.ndjson`, 8-hex error ids, rotated at 8 MB keeping one `.1` generation; distinct from `runlog.py`'s `errors.json`, which is the *build*'s per-document outcome store — this is the *serving* side, written by `api/errors.py` and read by `lagen all errors` |
| `net.py` | shared HTTP session setup + a resilient `request()` helper for the source downloaders (transport-level retry, Retry-After, throttle logging, riding out failures from both the `requests` and `httpx` transports); `mount_legacy_tls` accepts a legacy small-DH-key TLS handshake for one host prefix only (`conventions-ws.coe.int`); `make_http2_session` (`httpx[http2]` — the 0.x line, a different package from the `httpx2` starlette's TestClient wants) is an HTTP/2-only fallback for hosts that refuse HTTP/1.1 behind a Cloudflare front (foreskrift's kkvfs) **Every request waits out the Crawl-delay the host's robots.txt asks for** (`crawl_delay`/`pace`/`parse_crawl_delay`, read once per host per process, a group naming us outranking the `*` group): the rate a host states outranks whatever `delay` a source passed, and is a floor on it, never a ceiling (rule:respect-politeness). It sits on the request rather than in each source because some thirty sync functions thread their own `delay` down to their own `time.sleep`, and a rule added to any of them is one the next harvester forgets — the EBA asks for 10 seconds and `eba_sync` ran at 0.5 for as long as the harvest existed. The robots read goes around `request` (a 404 is the normal case, not a failure to retry over) and honours a budgeted session's deadline like any other request. A robots.txt that could not be read at all is *not* consent: it raises `RobotsUnread`, the host is paced at `UNREAD_ROBOTS_DELAY` and told so once on stderr, because one blip on the first request of a long harvest would otherwise drop the whole run back to the source's own delay. `forget_crawl_delays` drops the per-host cache. Sources that drive a headful browser (`lib/browser.py`) do not pass through here and are not paced. |
| `patch.py` / `patchit.py` | the source-file patch layer (apply-at-parse) and its interactive authoring CLI — see "Patch files" below |
| `formex.py` | reads a Formex manifestation — the structured XML the Publications Office publishes an EU document as — into an ordered list of typed `Block`s, and is the one home of that reading. `formex_members` splits a `.fmx4.zip` bundle into its parts in document order (the main act promoted ahead of an annex printed on an earlier OJ page), `formex_roots(path, source, key)` applies the document's patch to the main part before parsing, `parse_act`/`walk_content`/`parse_judgment`/`append_annex`/`collect_notes` emit the blocks, and `act_metadata`/`judgment_metadata` read the date, the OJ coordinate and the ECLI. It lives here because two sources read Formex: `eurlex`, for what CELLAR holds under a CELEX, and `guidance`, for the ECB's and the ESRB's soft law, which those bodies publish in EUT rather than on their own sites. Neither the block vocabulary nor the walking is source-specific — each source projects these blocks onto its own model |
| `cellar.py` | CELEX in, bytes out: the Publications Office repository, shared by `eurlex` (whole CELEX sectors) and `guidance` (one named EU body's works) — `fetch_selection`/`store_document` pick the best manifestation per language (Formex > XHTML > HTML > PDF, *verified*: a manifestation may promise `fmx4` and serve a scanned TIFF), `fetch_metadata` the work date, eurovoc concepts and the validity pair (`cdm:resource_legal_in-force` + `cdm:resource_legal_date_end-of-validity`), and `fetch_repeals` what a newly harvested act repeals — the only announcement a repeal gets, since the repealed act carries no inverse edge. `notice_ttl` writes the stored `notice.ttl` and `notice_work_date`/`notice_repeal_date` read it back, across all three notice shapes on disk (synthesized n-triples, the bulk unpacker's turtle subset, and the older tree notices, which write the in-force flag as a boolean rather than a digit) |
| `markup.py` | makes a markup document diffable — one block element per line, without changing what a parser reads out of it (`block_lines` for HTML, `indent_xml` for XML) — because a unified diff is a diff over lines and two patchable sources ship their whole body on one line: ~9% of dv's API records, and eurlex's Formex/OJ manifestations as a rule. Only invoked when a document actually has a patch, from `patchsource.py` and from `dv.parse`/`eurlex.parse` themselves so the patched intermediate re-parses identically |
| `git.py` | the one place that shells out to the git CLI — the inline editor's commit engine, the MediaWiki history importer and the `history-as-git` export |
| `poi.py` / `poi_worker.py` | Apache POI (HWPF/XWPF) → a flat `(text, bold, in_table)` paragraph stream for legacy `.doc`/`.docx` bodies; moved here from `dv/word.py` (2026-07-17) under rule:second-use-goes-to-lib — `dv/legacy.py` imports it as `poi as word` (förarbete's `.docx` bodies went back to plain lxml, and its `.doc` bodies go through `antiword`, since proptrips-era `.doc` is mostly Word 6/95 binaries POI's HWPF refuses); strips Word field-control characters (`\x13`/`\x14`/`\x15`) and their instruction segments, which otherwise leaked into extracted text. Split client/worker (2026-07-19): `poi.py` is jpype-free and drives a persistent `poi_worker` subprocess (line-delimited JSON over pipes, one JVM per build worker, exits on stdin EOF) so the JVM never shares an address space with the build — nothing may import `poi_worker` |
| `errors.py` | `SkipDocument` — the shared control-flow signal a source's extractor raises for an expired/removed/empty document |
| `util.py` | small shared utilities ported from `ferenda.util`, incl. `write_atomic` (same-directory temp file + rename, per-process temp name so concurrent `lagen` invocations can't race each other's rename) and `now_iso`/`append_json_line` (ISO-8601 timestamp + flushed ndjson-line append), shared by `runlog.py`'s run ledger and `errorlog.py`'s served-site error ledger; `approximate_date(value)` turns a partial date — a bare year, a year-month, a riksmöte ("2004/05") — into the middle of the span it can mean (mid-year, mid-month, the riksmöte's turn of year), for dating a citation scan against the act in force when a document with only a partial recorded date was written |

**DV vertical (court decisions)**
| File | What |
|---|---|
| `download.py` | downloader for the rättspraxis API; excludes `PROVNINGSTILLSTAND`/`FORHANDSAVGORANDE` publications (leave-to-appeal notices and CJEU referral requests, neither a decision) and purges any already-stored copy of one |
| `identity.py` | entity-resolution index (one canonical case ← many source records); R2 folds a raw pre-referat verdict (an API record with no referat of its own) into the later referat that publishes its målnummer, guarded to one referat component per målnummer + matching avgörandedatum |
| `model.py` | `Avgorande` model (metadata + ordered Rubrik/Stycke body + footnotes); `Rubrik`/`Stycke` carry an optional source PDF `page` (raw-verdict facsimile links) |
| `parse.py` | **API path** — body from `innehall` HTML, metadata from curated fields; **raw-verdict PDF path** (`parse_pdf_record`) — before a HD/HFD decision's NJA referat is published, its only text is the court's own PDF attachment; body comes from `lib/pdftext`, with domskäl paragraph numbers (printed as unselectable margin bitmaps) recovered by counting small left-margin images and injected back into the reflowed lines; citations are scanned against the decision's own `avgorandedatum` (`sfs_parser(..., written=)`, `scan_body(..., written=)`), so a bare law name resolves to the act in force when the case was decided, not whatever has replaced it since |
| `structure.py` | instance/ruling segmenter (delmål → instans → betänkande/dom → domskäl/domslut) |
| `paths.py` | where a case's parseable source sits: `cases()` (the identity index, keyed by canonical id, keeping only cases with a readable source), `member`/`record` (the API record, else the frozen legacy original) and `verdict_pdf` (the court's own PDF, the body of a verdict published before its referat). Here rather than in `build.py` because none of it is orchestration — and because `patchsource` needs it, which through `build` would be an import cycle (`build` → `api.patch` → `patchsource` → `build`) |
| `namedcases.py` | harvester for HD's named-precedent list (`data/namedcases.json`) |
| `casenumbers.py` | `lagen dv casenumbers` — sweeps the dv artifacts' `malnummer` into the committed snapshot `data/casenumbers.json` (24,411 numbers, `[court, date, local uri]` candidates), the join surface `lib/malnummer.py` resolves "HD:s dom i mål T 3-08" through; the same snapshot shape as `avg/arsberattelse.py`. Runs by itself at the end of a **full-source** `dv parse`, after the R2 artifact reconcile, so a number never survives on a just-deleted artifact; `write` reports whether the file changed, and a changed snapshot re-stales the parse of dv, forarbete, avg, rs and wiki (it is one of their recipe inputs), which the run prints |
| `legacy.py` | **legacy path** — Word referats via `lib/poi.py` (`from ..lib import poi as word`, flat `(text, bold, in_table)` stream → head/body split → `Avgorande`) and notis intermediate XML (TRIPS `<para>` / OOXML `<w:p>` flavors), for cases with no API record. Its `import_identities`/`import_notiser` one-time importers (the frozen notis bodies + the `legacy-identities.json` oracle sidecar) were deleted once run to completion (§7g teardown, 2026-07-19); only the parsers remain runtime code. `notis_summary` recovers a listing description from a notis's own first-paragraph summary line where the frozen oracle's `referatrubrik` has none |
| `render.py` | the rättsfallssida: the referat/dom body, its keywords and the ursprunglig-dom link. Registered as this source's page renderer in `build.SOURCE_RENDERERS`; `render(art, site) -> str`, built on the `lib/page` kit, with its own page template in `templates/` |

**forarbete vertical (preparatory works — prop/sou/ds/dir)**
| File | What |
|---|---|
| `download.py` | regeringen.se harvester (`lagen forarbete download [prop\|sou\|…]`); basefile = the document's own identifier; a `source`-carrying import record is treated as absent so live always wins; `pm` (promemorior outside the Ds series, category 1325 shared with `ds`) keys by diarienummer when the listing shows one, else the landing-page slug |
| `model.py` / `structure.py` / `parse.py` | `Forarbete` model, PDF (font-aware `pdftohtml`, or `pdftotext` fallback for OCR-layer scans) / html → nested structure → citation-scanned artifact; `parse_record`'s one body route, `_harvested_body`, reads `files`/a re-OCR sidecar at `layout.fa_ocr_pdf` from `downloaded/<type>/<year>/` — every §7g frozen corpus (prop, sou, dir, ds, …) is re-housed into that same harvested form (2026-07-19). Font size gates heading detection (footnotes → `fotnot` blocks, gated by `running_text_size` — a page's own dominant size where enough of the page is set in it (`line_body_support`), else the smaller of that page size and the document's, so a bilaga reproducing text smaller than the body, or a document split near-evenly between two sizes, doesn't read half its running text as footnotes, and a page reproducing a differently-sized EU regulation across dozens of pages doesn't read its own prose as headings either; body-sized "N Title" patterns stay stycken) and wrapped multi-line headings fold in `lib/pdftext`. `heading_level_by_size` learns a font-size → heading-level map from the document's own numbered headings (a size counts only where numbered headings are a majority of what's set in it, excluding a lagförslag's own kap./§ headings) and places the unnumbered display headings it recognises no other way ("Sammanfattning", "Förkortningar") at that level, ahead of the flat-level-3 bold-subhead rule. `parse.tag_frontmatter` (prop/skr) retags the un-bold överlämnande page: the "huvudsakliga innehåll" heading becomes a rubrik (so the ingress gets its own avsnitt) and post-signature names become `signatur` blocks; `structure.signers`/`structure.ingress` read them back for `sfs/asgit.py`. `classify` also tags a narrower-measure paragraph `ruta` (the ruled proposal/assessment box) and places each page's `pdf_figures` among its paragraphs as `bild` blocks (`bbox` in PDF points, no pixels copied into the corpus); `Block.spans`/`Para.spans` carry the PDF's own bold/italic runs through to the rendered inline styling (superscript is not a font attribute poppler reports — a footnote marker stays its own run kind); citations are dated by `written_date` (the recorded date, else the basefile's year/riksmöte via `lib.util.approximate_date` — 57% of the corpus, every kommittédirektiv, records no date), so a bare law name resolves to the act in force when the förarbete was written |
| `volumes.py` | which of a record's PDFs are its **body**, and in what order. `files` is every `/contentassets/` PDF the regeringen.se landing page linked, so 485 multi-PDF records carry errata, English summaries, kortversioner, reprinted EU directives and remisslistor alongside the document — reading them all ingested every one as the text, and reading only `files[0]` published SOU 2016:77's one-page *Rättelseblad* as the whole betänkande. The discriminating evidence was already on disk and discarded: the stored landing page's **link text** ("del 3 av 4, bilaga 1-19"), which aligns positionally with `files` for 306 records. Five populations, read off the record with no PDF opened: the curated skip list (`data/skip.json`) takes a document out regardless of file count, KB scan sets keep only the first file (the rest are sibling volumes under one SOU number), budget propositions are skipped, legacy `_N` records keep everything not positively ruled out (no landing page = missing evidence, not evidence of absence), and the rest use the labels. Then: drop by role, collapse a "hela dokumentet" edition published beside its own parts (its page count equals their sum), pick the primary by what its first page says rather than by index, and admit further volumes only on positive evidence (a "del N" label or a matching PDF title). `_pdf_summary` in `parse.py` supplies the cheap per-file probe (`pdfinfo` + one page of `pdftotext`) |
| `jamforelse.py` | extracts a re-enacting prop's provision-mapping tables (titled *Jämförelsetabell*, *Jämförelse mellan …*, *Paragrafnyckel* or *Paragrafregister*) (old↔new provision tables, often in a bilaga volume the artifact parse never reads) from per-run coordinates: a bilaga region is bounded by the "Bilaga N" page-margin marker, a body-chapter table (PBL) by its repeated header pair; each page's columns re-derived by clustering cell starts, headerless/merged-run headers tolerated, per-law sections of a multi-law register split into sibling tables; consumed by `sfs/correspond.table_correspond` |
| `lydelse.py` | reconstructs the two-column *nuvarande/föreslagen lydelse* comparison tables from per-run coordinates: the italic header gives the column boundary, cell lines reflow per column and pair into aligned rows (`tabell` blocks, the SFS `rad`/`cells` shape); page-centered "2 kap."/"28 §" markers come back as kapitel/paragraf blocks |
| `legacy_formats.py` | body adapters read by the harvested-form route (`parse.py`'s `_harvested_body`), all §7g frozen corpora now included — dokumentstatus XML, riksdagen text/tml + skanning2007 html, ABBYY OCR-XML (`abbyy_pages`, fed decompressed bytes from the `downloaded/` tree), scanned-PDF OCR text (`scanned_pdf_pages`), TRIPS `div.body-text` (`trips_paras`), legacy Word (`word_paras` — `.doc` via `antiword`, since the proptrips-era binaries are mostly Word 6/95 that POI's HWPF refuses; `.docx` via `lib/poi.py`) |
| `propkb.py` | facsimile-only fetcher for the KB two-chamber proposition scans (1867–1970) — adds no documents (the ABBYY OCR text layer is already complete for all 19,066 propkb records), only a page-image "proof" view for the 17,295 fetched XML-only; the scan-PDF url is derived mechanically from each record's stored ABBYY xml `orig_url`, no index crawl; the scan lands at the `layout.fa_facsimile_pdf` rule and is resolved from disk by existence, writing **no record** — naming it in `files` would flip bodies off the ABBYY OCR, and the record is a content-hashed parse input, so any key written into it would re-stale 17,295 parses. Own verb: `lagen forarbete propkb-scans`. **Built, not run at corpus scale** — only prop 1867:1 + 1937:141 fetched as end-to-end checks; the full pass is ~79 GB |
| `kbtitles.py` | the reader-facing **title** of a KB two-chamber proposition, called from `parse_record` for every förarbete document: the record keeps what the old ferenda entry held, this undoes the three defects in it before the title reaches the artifact. 1,603 titles are a Python 1-tuple repr (`"('med förslag till lag ...',)"`, an old-ferenda write bug with the non-ASCII escaped inside — `untuple` decodes rather than slices); 8,117 keep the OCR line-break hyphen and the space after it ("dispo¬ sition" → "disposition", the body parse's own rule, applied across the paragraph boundary too — a title with none of the three defects is returned character-for-character, whitespace included); 1,570 are the placeholder "Doc 1952:64", read back off the document's own first page by `title_from_paras` — the title stands between the "Kungl. Maj:ts proposition till riksdagen" head and the "; given Stockholms slott den 15 februari 1952" dateline, with three passes for a title the OCR broke across paragraphs, the budget proposition's display-line head, and a dateline garbled past recognition. 1,551 of 1,570 recovered, 19 left `(rubrik saknas)`; a 600-record control sample of intact titles comes back unchanged on 599 (the 600th is a bound volume where the entry title took the *previous* document's). Two effects reach outside propkb: 47 non-propkb titles carry a line-break hyphen and get the same join (45 of them the result `lib.util.normalize_hints` gives at harvest time), and the 345 records the upstream published without a title (286 prop, 59 dir) now yield `""` rather than the `null` that used to reach the artifact against `Forarbete.title`'s `str`. In `FA_CODE`, so a change here re-reads every förarbete title |
| `soukb.py` | body **re-downloader** for the KB-digitised SOUs (1922–1999) — unlike `propkb.py`, there is no ABBYY XML sibling: the scanned OCR'd PDF *is* the body, so this fetches the document itself and writes a fresh harvested record per basefile. `https://sou.kb.se/` is the single source of truth — it forgets the legacy soukb records entirely (the old `regina.kb.se` start URL is dead) and basefile now comes from the index label (`basefile_of` ports and broadens the legacy SOUKB regex: `1922:1 första serien`→`1922:1fs`, letter suffixes lowercased, `/`-double-issues hyphenated), 5,814 distinct basefiles walked from the live index. 128 basefiles are multi-volume (one label repeats across several URNs, e.g. `1987:3` = 28 volumes of the Långtidsutredning); each URN is a part, so `files` is a list (`<slug>.pdf`, `<slug>-1.pdf`, …) in index order, one record per basefile. Resolves each URN resolver page to its digark scan-PDF, fetches, validates the `%PDF` magic, stores plain; resumable per part. Own verb: `lagen forarbete soukb-scans` (`--limit N` for a test slice), hundreds of GB total. **Built, verified end-to-end on one small doc (1922:1, 10.5 MB) into a scratch tree — not run at corpus scale** |
| `riksdagen.py` | doctype-agnostic data.riksdagen.se dokumentlista harvest engine (`harvest`/`_walk`, riksmöte-sliced backfill, watermark lifecycle); driven with the `bet` (utskottsbetänkanden, the prop→enacted-law link) specifics — PDF-only bodies (printed page = citation anchor), basefile `"<rm>:<beteckning>"` matching the FORARBETEN grammar's bet URIs, the planned/published upgrade cycle; full backfill walks all 161 riksmöten (the API caps one query's pagination at ~10k docs); no frozen legacy corpus |
| `rskr.py` | second driver over `riksdagen.py`'s engine, for riksdagsskrivelser (`rskr`, the chamber's decision letter to the government — the prop→bet→rskr chain's last hop); basefile `"<rm>:<beteckning>"`; body is the API's own small HTML rendering (`dokument_url_html`), not a PDF filbilaga (an rskr is a few boilerplate sentences ending in the talman's/tjänsteman's signature — the committer identity `sfs/asgit.py` mines); every feed entry is published and final, so no planned/published upgrade cycle |
| `kommentar.py` / `genomforande.py` | författningskommentar → `implements` (EU directive article) edges; extracted from `prop` and `fm` (förordningsmotiv) documents — both accompany the final enacted text, unlike a lagrådsremiss/SOU/Ds; `fk_section` also slices out the per-law FK prose consumed by `sfs/correspond.py` (reading a proposition artifact stays förarbete's job); `genomforande.resolve` prefers an authored `.ann` genomförande layer's edges over the mechanical `implements` per covered directive (`genomforande_layers` globs the förarbete annstore subtree, joined by the prop uri the layer records, same pattern as the `.corr` layers), keeping mechanical edges for any directive the layer didn't map |
| `aigenomforande.py` | `lagen forarbete ai-genomforande <prop-basefile> [<CELEX> ...]` (opt-in, LLM) — author the directive→paragraf transposition map for the EU directive(s) a proposition transposes (defaulting to every directive its mechanical `implements` names) out of its författningskommentar. Call granularity is *one LLM call per proposed law* (a huge FK is chunked at `BATCH_CHARS`), the `sfs.correspond` granularity — whole-law context at a fraction of the calls. Paragraf identity is never asked of the model: each candidate FK entry (commentary mentioning "artikel"/"direktiv", already segmented by `fk.py`) gets a stable id E1, E2, … and the model returns the id; each directive is tagged A, B, … with its real article inventory (read from the eurlex artifact), so a multi-directive prop (financial omnibus, NIS2+CER) is one pass. Every mapping is validated — known id, known tag, every cited article reduced via `kommentar.parse_articles` (bare "21", pinpoint "21.1–21.3", lettered "23.4 a" all accepted; base validated against the inventory, pinpoints kept for the margin) and a non-empty supporting quote occurring in that entry's commentary; a failing item is dropped, not stored. A mapping may also carry an optional Swedish-side pinpoint (`"sfs": "S1"` / `"S3N2"`, the SFS element-id syntax) when the FK scopes the claim to a stycke/punkt — shape-checked here (a malformed value is disregarded, never dropping the mapping), existence-checked against the published law's minted element ids at relate time (`genomforande.resolve`), and rendered as citation prose ("första stycket genomför …") in the statute margin. Written as a `.ann` layer in the curated store (`lib/annstore.py`), a richer superset of the mechanical `implements` that `genomforande.resolve` prefers at relate time. Own prompt file `genomforande_prompt.txt`; generous `MAX_TOKENS` (a reasoning model spends its budget before the JSON; a truncated reply loses the batch); the LLM is called only from this action, never from parse/relate/generate — same discipline as `eurlex ai-annotate`/`sfs ai-correspond`. Correctness was benchmarked against an adjudicated golden corpus for the 2025/26 slice (`.ann.golden` layers beside the `.ann` files in `WIKI_ROOT/ann/forarbete/prop/2025/`, scored on demand by `tools/aigenomforande-bench/evaluate.py` — bench data, not a test gate; `test/test_forarbete_aigenomforande.py` is self-contained and covers the non-LLM core) |
| `fk.py` | per-paragraf författningskommentar text extractor: slices a prop's FK chapter into `{law, chapter, paragrafer, lagtext, kommentar}` entries across the three FK styles (lagtext quoted / bare marker / marker inline), with content-based span bounds and marker/heading recovery rules locked to the curated corpus. parse stores the entries as the artifact's `kommentarer` section and stamps commentary blocks `fk: <entry-no>` (the prop page wraps each entry's run in an `.fk-komm` highlight box); `resolve` pins entries to statute anchors at relate time (`fk_kommentar` table, law resolution shared with `genomforande.py`); the statute paragraf's rail shows each prop's comment ("Författningskommentar", newest first, `#sid`-pinpointed provenance) |
| `render.py` | the förarbetessida: the prop/SOU/Ds body and the genomförande margin. Registered as this source's page renderer in `build.SOURCE_RENDERERS`; `render(art, site) -> str`, built on the `lib/page` kit, with its own page template in `templates/`; a `bild` block renders as a `<figure>` cropping its `bbox` from the source PDF on demand (`/api/v1/facsimile?...&bbox=`, `lib/facsimile.cached`); a numbered section hangs its number in a gutter with its own pilcrow -- `_outline`/`_numbered` read the document's own numbering to tell a section number from the running head a scanned proposition prints ("172 Kungl. Maj:ts proposition nr 144 år 1970") -- the outline level a "4.2" implies for "4", the repetition test `pdftext.strip_page_furniture` uses for page furniture, and the highest number the document actually subdivides |

**avg vertical (JO + JK + ARN + IMY + KKV myndighetsavgöranden)**
| File | What |
|---|---|
| `model.py` | `Beslut` model; URI = `avg/{org}/{dnr}`, byte-identical to what MYNDIGHETSBESLUT citations mint. IMY is the one organ whose dnr has to be *read out of the decision PDF* — its site publishes tillsyn pages, not documents — so the model also carries `delar` (the documents one decision was published as) and `tillsyner` (the pages that publish it); KKV joins the diarium's case fields (`arendetyp`, `motpart`) with the curated ärendelista's (`bransch`, `beslutstyp`, `referat_url`); `Fotnot` carries the notes below the running text (`fotnoter` → artifact `footnotes`, when non-empty) — where IMY grounds a vägledning it names in prose with the number IMY cites |
| `download.py` | JO harvester (jo.se WordPress admin-ajax search API + decision PDFs), JK harvester (jk.se listing → per-decision landing pages; `jk_canonical` dnr normalization) and ARN harvester (arn.se one-page vägledande-beslut listing → decision PDFs; a live record overwrites a frozen-import one) and IMY harvester (imy.se `/tillsyner/?page=N` server-rendered listing → tillsyn pages → their attached PDFs, each read for the diarienummer that names it and regrouped by it: one page can decide several ärenden, one ärende be published as several documents, one document hang off several tillsyner — `imy_records` turns all three into one record per decision, and reports the anonymously published decisions whose number is redacted and which therefore have no identity to be filed under). The two curated pages, `praxisbeslut` and `beslut-om-sanktionsavgift`, are read as a metadata overlay (lagrum, nyckelord, överklagan, laga kraft, the fine) — they name no tillsyn the listing does not already carry, and their `/link/<guid>.aspx` links are resolved through the `/tillsyner/rss` feed's GUID↔url pairs in one request instead of one redirect apiece; and KKV harvester, which joins **two** of Konkurrensverkets sources on the diarienummer. The **diarium** (`/diarium/sok-i-Konkurrensverkets-diarium/`) supplies the decisions: "Avslutade ärenden" + "Publicerade beslut" is 10,097 cases, but status says nothing about what kind of ärende a case is (3,675 are remissyttranden), so the harvest also applies the agency's own ärendetyp groups (`KKV_CASETYPES`) and lands on **1,830 tillsynsbeslut since 1998** — företagskoncentrationer deliberately excluded as one-page clearances. The curated **ärendelista** (`/konkurrens/tillsyn-arenden-och-beslut/arendelista/`) supplies what the diarium has none of: for 329 cases, Konkurrensverkets own sectioned account of what the case was about, why it was prioritized, what it decided and what the courts then did — plus branch, parties and the kinds of beslut; a fifth of the entries name several diarienummer (329 cases → 413 dnr) and 346 of those name a case the narrowed diarium set does not carry — cases from 1993–97 that predate the diarium, plus the hand-picked företagsförvärv the bulk exclusion drops — which are stored from the account alone, for **2,176 documents** in all. Behind Cloudflare, HTTP/2-only, so it rides `lib/net.make_http2_session` like `foreskrift`'s KKVFS; `X-Requested-With` turns the server-rendered search into bare result JSON and `Accept: application/json` does the same for the ärendedata and case pages. The diarium's paging is *cumulative*, so a group is taken whole with `take`; the ärendelista's `page` is a true offset. The listing carries the diarienummer, so nothing is read out of a document to mint the identity; the ärendedata page is fetched only for a case that is new or has moved, for the beslutsdatum); also owns the store-path helpers `arn_pdf_path`/`jo_pdf_path`/`imy_pdf_path`/`kkv_body_path`/`jo_officialreport_path` and `RE_ARN_DNR` (moved here from the deleted `legacy.py`, §7g teardown 2026-07-19) — `jo_officialreport_path` is the JO ämbetsberättelse citation map (`jo/.officialreport.json`) re-housed beside the JO records, since jo.se itself never published it |
| `parse.py` | JO/ARN: PDF body via `lib/pdftext` (bold rubriker; JO's "Beslutet i korthet" abstract); JK: landing-page `div.content` (strong→section, em→subsection); IMY: the Swedish parts the record names, read font-driven (`classify_imy` — smaller than the body size is a footnote or masthead, a "N (M)" page mark is a running header, a bold paragraph is a heading whose level is its font size's rank, and the two-column masthead is stripped *in place* because it arrives glued onto body lines); an English translation shares its decision's dnr and is skipped rather than shipped twice; KKV: three body routes behind one `pdf=` parameter that names none of them — PDF (most of them, read by the same font-driven `_classify_font_driven` core as IMY but starting at the bold subject line, because KKV sets the recipient block at the body size), the FrontPage-era HTML the diarium published before ~2006 (windows-1252 asserted from the document's own declaration; three template generations, so the body is found by *shape* — the letterhead is the run of short lines above the first real paragraph — and the oldest generation's `ÄRENDE:`/`SAMMANF:` table is lifted out as the diarium's own abstract) and Word (via `lib/poi`); a case whose decision is on the curated ärendelista has Konkurrensverkets own account of it *heading* the body — the decision document predates the courts that later reviewed it, so the account is the only place the case's outcome is written — and takes the curated case name as its title over the diarium's bureaucratic ärendemening. All citation-scanned with the DV parse-type set. An ARN referat's "title" is really its preamble paragraph, so the page heads on its first sentence (`lib/labels.first_sentence`, Swedish-abbreviation-aware — "s.k.", "kap.", a bare number don't end it) while the whole preamble still renders as the summary (`avg/render.py`). `_footnotes_font_driven` recovers the notes `classify_letterhead` drops below the running size (`lib/pdftext.letterhead_footnotes`) — wired for **imy** only; JO/JK/ARN set none and KKV's three-format dispatcher is left open (`avg/KNOWN-GAPS.md`); the citation scan is dated to the decision's own `beslutsdatum` (`sfs_parser(..., written=)`), so a bare law name resolves to the act in force when the decision was made |
| `render.py` | the myndighetsavgörandesida: the JO/JK/ARN/IMY/KKV decision body (an ARN referat's preamble heads the page via `lib.labels._first_sentence`). Registered as this source's page renderer in `build.SOURCE_RENDERERS`; `render(art, site) -> str`, built on the `lib/page` kit, with its own page template in `templates/` |

| `arsberattelse.py` | `lagen avg arsberattelse` — sweeps the JO artifacts' `officialReport` pages into the committed snapshot `data/arsberattelse.json` ("2005/06 s. 171" → dnr), which `lagrum`'s `jo_arsb_ref` production resolves "JO 2003/04 s. 450" through; the same snapshot shape as `dv/namedcases.py` |

`avg/KNOWN-GAPS.md` records the two documents `avg parse` has ever failed on
across the whole corpus (both KKV, both since resolved) — kept as the
diagnosis, not an open item.

**rs vertical (myndigheternas rättsliga ställningstaganden)**
| File | What |
|---|---|
| `agencies.py` | the data registry: seven myndigheter (Skatteverket, Försäkringskassan, Migrationsverket, Kronofogden, IMY, Finansinspektionen, Konkurrensverket), each with its listing, the citation form of its series, its transport (`browser`) and body format (`page_body`), and what is peculiar about it. Only two have published a short designation for the series — FK writes "FKRS 2020:2" in its own prose, IMY prints "IMYRS 2024:1" on the document — so the rest are cited the way their own page names them ("Konkurrensverkets ställningstagande 2025:1"); no acronym is invented for an agency that has not coined one. `BROWSER_ORGS`/`DEFAULT_ORGS` split the one headful-Chrome agency (skv) out of the default sweep, the föreskrift rule at rs scale |
| `model.py` | `Stallningstagande` model; URI = `rs/{org}/{nummer}`, the avg grammar with the agency's *own number* as the identity rather than a diarienummer — a ställningstagande is published as a numbered item in a series, which is how the agency and everyone citing it names it. Skatteverket falls on the avg side of that line and for the avg reason: it numbers no series and names its own positions "Skatteverkets ställningstagande 2026-07-06, dnr 8-207888-2026", so there the dnr *is* the published designation. Currency is first-class (`status`/`upphavd`/`ersatt_av`/`ersatter`): unlike a beslut, which is a fixed historical artifact, a ställningstagande is in force until the agency withdraws it, and four of the seven say so in the listing itself. `version`/`foregaende_version` carry Migrationsverkets in-place revisions; `doktyp` its rättsliga kommentarer (RK/…), published in the same series |
| `download.py` | one harvester per agency over the same three steps (walk the listing, mint the identity from the agency's own number, fetch the PDF) — no watermark anywhere, the JK/ARN idiom of one walk per run. **IMY**: info blocks → `/link/<guid>.aspx` → the publication page, whose preamble states the number and whose prose is IMY's own summary (cut at the "Om IMY:s rättsliga ställningstaganden" boilerplate). **FI**: a hand-authored table read positionally (Nummer, Titel, Beslutsdatum, Status) — the one listing that keeps *repealed* statements visible, and so the one place a remote string decides whether a document reads as current; `fi_status` maps that column onto the model's two states and raises on a word it does not know rather than defaulting to "gällande". **FK**: 108 documents 2005–, whose number is read from the PDF's own `Serienummer` rather than the listing (which retypes it, once wrongly: the 2026:01 PDF is listed as 2026:03) — the one agency whose identity comes out of the document, so the fetch happens in the sync and `stored_numbers` remembers which number a record was filed under so a later run costs one request (Lifos uses the same memo for the two entries whose number is only in the PDF). **KFM**: a year-grouped list whose numbers run `löpnummer/år` with a verksamhets suffix (1/23/VER); the year heading is the site's grouping, not the number's year, and one entry names no number at all, which is reported rather than invented. **Migrationsverket**: the Lifos database's detailed search filtered on the "Rättsliga ställningstaganden och kommentarer" subject word, 104 documents, `page=N` a true offset — over an AIA-completed TLS chain (`lib/net.mount_aia_chain`), since the site sends neither of the two intermediates above its leaf. **KKV**: a förteckning stating each statement's fate in its own parenthetical ("(upphävt 20 oktober 2025)", "(upphävt genom 2022:2)", "(ersätter 2019:1)"), lifted out of the title into fields; behind the same HTTP/2-only Cloudflare front as KKVFS. **Skatteverket**: the register and page semantics live in `skv.py`; what is here is `skv_sync`, the walk that drives them over the detached headful-Chrome transport — the register once at a 180-second settle, then one paced navigation per document. The pace is the point: measured against the live site, ~30 navigations at 5-second spacing trip the front's rate defence, after which every navigation is rejected whatever profile asks, so a document waits 20 seconds and `SKV_BLOCK_LIMIT` consecutive rejections end the run (a stored record is only ever written with its page, so the next run resumes where this one stopped). `labelled_value` is the shared page-1 reader: these headers are column tables that poppler flattens as either "label label value value" or "label value label value", so a value is anchored on its label and matched on its own shape |
| `skv.py` | Skatteverkets register and page semantics, pure over the HTML so they are testable without a browser. The register is one slow page listing 2,619 entries, of which 2,614 carry the dnr that files them, but the whole list is server-rendered into it as the Sitevision app's initial state, so `parse_index` reads it as JSON rather than scraping rendered rows — and that payload carries each entry's diarienummer, the document's own date (not the day rättslig vägledning published it), its subject taxonomy ids and the validity window. That window's end date is the only place the register states a withdrawal; a window equal to its own start is *not* one, but a withdrawal notice, which Skatteverket publishes with a single day's validity (273 of them). `page_body` reads the document itself — h2–h5 headings, paragraphs, list items as stycken, the dated `div.update` notes Skatteverket sets at the head, and the notes under the closing "Fotnot" heading. `page_relations` reads what the position replaced and what replaced it: both are sentences in fixed words, each naming its counterpart with a marked-up reference carrying the other document's dnr |
| `parse.py` | one body reader for the six PDF agencies — `lib/pdftext.classify_letterhead` — configured per agency by a `Reader` (margin pattern, masthead pattern, the page-1 fields the listing did not carry, and whether headings are marked by weight or by size; FI and Migrationsverket set no bold section headings at all, only larger type). Nothing the listing states is re-derived from the PDF (the avg rule); the PDF fills only what exists nowhere else — IMY's and Kronofogdens dates, four agencies' diarienummer, and Migrationsverkets own Beslutsdatum, which parts company with Lifos's Upphovsdat whenever a statement is revised in place. `drop_front_matter` drops the letterhead caption and the PDF's copy of the title, which the page already carries as its section label and h1. Skatteverket has no letterhead to read at all: `page_fields` routes it to `skv.page_body` on the `page_body` registry flag, and its stored page — not a PDF — is its patchable intermediate, normalised to one block element per line (`lib/markup.block_lines`) in both `parse` and `patchsource`. Bodies are citation-scanned with the full parse-type set — the point of the vertical: a ställningstagande is one long argument about a handful of paragrafer, so scanning it puts the agency's published reading on the rail of each paragraf it interprets; the scan is dated to the position's own `beslutsdatum` (`approximate_date`, `sfs_parser(..., written=)`), so a bare law name resolves to the act in force when it was written |
| `render.py` | the ställningstagandesida: the agency's legal position and its siblings. Registered as this source's page renderer in `build.SOURCE_RENDERERS`; `render(art, site) -> str`, built on the `lib/page` kit, with its own page template in `templates/` |

**guidance source (EU-organens vägledningar — 12 utgivare)**
| File | What |
|---|---|
| `issuers.py` | the data registry driving one shared pipeline for twelve issuing bodies — EDPB, EDPS, EBA, ESMA, EIOPA, ECB, ESRB, EASA, ACER, ENISA, BEREC, EUIPO. An `Issuer` states the body's name (long and short), its `route` (`site` for the ten walked on their own pages, `eurlex` for the two that publish in EUT), its `base` URL, and the two flags its PDF template needs (`feta_rubriker` — whether it marks a heading bold rather than by size; `upprepat_sidhuvud` — whether it reprints a running head on every page). A `Series` states one numbered sequence: its kod, its label, the `identifier` format the body cites it by (`EBA/GL/%s`, `CON/%s`), and `slug` for the address. `kod=None` where a body has one sequence across several document kinds and the address carries no series segment — the ESRB numbers its rekommendationer, beslut, varningar and råd together |
| `edpb_data.py` | the EDPB's own registry, which no walk can derive: the **closed** set of artikel 29-gruppens vägledningar the EDPB endorsed whole on 25 May 2018 (Endorsement 1/2018 names sixteen, all carried), each with the Commission newsroom item id that actually holds its text — except the two BCR application forms, which the working party issued as Word *forms*, so no authoritative PDF exists and each entry names the tillsynsmyndighets conversion it takes instead, with what that conversion was verified against. They are listed rather than harvested because the EDPB publishes a document page for only eight of them and the seven under `/documents/guideline/` are stubs |
| `<body>_download.py` | one harvester per upstream walk: `edpb_download` (Drupal sitemap + the Commission newsroom for WP29), `eba_download`, `easa_download`, `acer_download`, `enisa_download`, `esma_download`, `berec_download`, `edps_download` (headful Chrome — every view is behind an AWS WAF challenge), `eiopa_download`, `euipo_download`. Each owns only how its body's listing is read and how a leaf resolves to a document; everything after that is shared |
| `eurlex_download.py` | route A: the ECB's and the ESRB's harvest out of CELLAR through `lib/cellar.py`, with the same language and format preferences and the same fallbacks the eurlex source uses, but stored under `guidance/<utgivare>/` and identified by the body's own number. `enum_query` enumerates a body's works by its corporate-body URI; `series_number` reads the number off CELLAR's `resource_legal_internal_number_prefix/_year/_sequential_number` predicates and falls back to the *last* number printed in the title — an amending act names the act it amends first and carries its own number in the trailing parenthesis, and reading the first filed 23 ESRB documents under another act's number, each overwriting that act's own text |
| `download.py` | the `lagen guidance download [scope]` front: one scope per upstream walk (not per series — ten of the twelve bodies publish all their series in one tree), fanned out by `lib/harvest.dispatch_scopes`. `ORIGIN` is derived from the scope map so a new runner cannot leave its body off the harvest banner |
| `model.py` | `Vagledning` model; URI = `guidance/{utgivare}/{serie}/{nummer}` (`guidance/edpb/riktlinjer/05-2020`, `guidance/ecb/con/2013-82`, and `guidance/esrb/2014-01` where the body has no series segment) — the avg/rs grammar with the series in place of the myndighet, and the issuer's own number as the identity. These documents have **no CELEX**, which is what keeps them out of eurlex. Three fields are first-class rather than decoration: `sprak` (52 of 60 exist in Swedish, the rest in English only, and a page showing English text must say so), `ersatt_av` (with `ersatt_av_identifier` — where a body has replaced a wording with a later one, this mints the corpus-wide repeal vocabulary `status: upphävt` + `ersattAv` on the artifact, which is what drops the old wording from the browse trees, the feeds, the search results and other documents' citation rails while leaving its page reachable by direct link) and `version` (a riktlinje is adopted, consulted on and re-adopted, so serving version 1 as current would misstate what the board says — and would be exactly the distortion the EDPB's reuse terms forbid). The numbered **punkt** is the citable unit and becomes the anchor (`#punkt27`), which is what a decision citing "punkt 27 i riktlinjer 05/2020" needs |
| `parse.py` | one parse for both routes. **Route B** reads the document's PDF: `lib/pdftext.classify_letterhead` for the body layer, per-issuer field readers for what the cover adds, `numbered_breaks` for the punkt numbering the paragraph-gap heuristic cannot see (the number is set in a column of its own), `join_continuations` to rejoin a paragraph a page break split, and `footnotes` for the apparatus the block classifier drops — a riktlinje cites the yttranden it builds on far more often in its notes than in its running text. `eba_cover_title` reads the **Swedish** title off the EBA's own cover: 72 of its 80 documents are Swedish text but its leaf page, its link and its file name all name them in English, and the corpus cites this material by title far more often than by number. **Route A** reads whichever manifestation CELLAR served — Formex through `lib/formex.py` (`_formex_main` picks the reader off the root tag: `GENERAL`, an ECB-yttrande as printed in the C series, carries its text in CONTENTS and the act reader walks straight past it), the PDF through the same paragraph reader route B uses, and EUR-Lex HTML as the flat `<p>` run it is served as |
| `render.py` | the riktlinjesida: the EDPB/WP29 guidance body and its sections. Registered as this source's page renderer in `build.SOURCE_RENDERERS`; `render(art, site) -> str`, built on the `lib/page` kit, with its own page template in `templates/` |

**lawreview vertical (tidskriftsartiklar — nine journals of `journals.py`)**
| File | What |
|---|---|
| `journals.py` | the data registry: a `Journal` states the axes the nine publishers differ on — kod, full name, citation abbreviation, base URL, archive listing page, and whether the document is a web page (svjt, euar, lod) or a PDF (the rest). Nothing in the vertical branches on the journal code except to read one of these entries |
| `download.py` | the entry point (`sync`, one walker per journal, fanned out through `lib.harvest.dispatch_scopes` the way `guidance`/`rs`/`avg` do — a failing host is reported and the others go on). **svjt** (here) enumerates the archive's own year filter (1916–, nothing hand-kept) and reads each year page's article cards, storing the article's own web page as the document, and uses a harvest watermark so a caught-up run reads only its newest year. **jp** (here) enumerates the one menu page's issues and reads each issue page's text blocks, storing the issue's PDF as the document; the host rate-limits with a non-standard WAF status (466), which `lib.net`'s retry table covers like any other throttle. The other seven journals own modules: `ft.py`, `nmt.py`, `njel.py`, `siplr.py`, `urt.py`, `euar.py`, `lod.py`. Every one of them except `nmt` walks its archive newest-first behind a harvest watermark, so a caught-up run reads the index and its newest issue's page and stops, and never re-fetches an issue page whose records are all stored — for `njel` (whose host asks for a sixty-second crawl delay) that is the difference between minutes and an hour. `nmt` is the exception: its two listing pages are the whole archive, so they are re-read on every run and only the PDFs that move are fetched. `euar` keeps the four item addresses the journal has broken on its own side in `DEAD_ITEM_URLS` (see `KNOWN-GAPS.md`), so its store can complete clean |
| `model.py` | `Artikel` — the thinnest model in the corpus (no currency axis, no version axis, no relation axis: an article is a fixed historical publication). `slug`/`uri` are minted from the journal's own coordinates (svjt: the opening page, `2026-104`; jp: year-issue-sequence, `2025-01-03`), and `identifier` is the citation form itself (one small rule per journal, keyed off its kod — "SvJT 2026 s. 104", "JP 2009 s. 37", "FT 2025 s. 23", …) |
| `parse.py` | mining only: the article's whole text, every paragraph as an ordinary stycke, handed straight to the citation scanner — no headings classified, no cover or running head removed, footnotes kept (that is where the SOU/NJA references are densest). A page-bodied article (svjt, euar, lod) is read off its stored page by the reader its `page_reader` names; a PDF-bodied one off `lib/pdftext`, its citable opening page recovered per `journals.Journal.sida_kalla` (jp's Särtryck footer, ft's first-leaf table of contents, or the listing's own statement) |

There is no renderer: the articles are not republished (no page, no browse
tree, no frontpage entry, no feed, `UNSEARCHED` in `build.py`) — only the
citation scan of their full text feeds the "Artiklar" rail line
(`lib/page.py`) on the statute, förarbete or rättsfall they name, which links
out to the journal's own page for the article.

**lawpub — lawreview's platform scope (the lawpub.se platform, seven publishers)**
| File | What |
|---|---|
| `lawreview/lawpub.py` | the whole scope in one module: the `Publisher` registry (kod, the platform's `/utgivare/<n>` number, full name, icon; `kod_from_icon` reads the identifier off the icon file's own stem), the walker (one paginated listing, `POST /sv/sections/getsectionpage`, newest first, ended by the platform's own `EOF` page; only items marked open (`svg.icon.open`) download — a locked (`Stängd`) item is skipped; the article's PDF comes from `/utils/downloadsection/<id>`, a DOI-keyed item reading its id off its article page only when its PDF is fetched), the `Artikel` model (identifier off the "Publicerad i" line, `FT 2015 s. 551`, the edition standing in where the line states no page, no year where it states no date) and the parse (mining only, like the journals). The scope is a platform, not a journal, so it holds no `journals.Journal` entry — its coordinates are per-article — and `parse.parse` dispatches its `lawpub/…` basefiles here. Two of its seven publishers overlap journal scopes hosted a second way — Förvaltningsrättslig tidskrift (`ft`) and Stockholm IP Law Review (`siplr`) — so an FT or SIPLR article can arrive by either scope, catalogued under two basefiles |

**foreskrift vertical (agency regulations)**
| File | What |
|---|---|
| `agencies.py` | the data registry driving one shared harvest engine — 71 registered författningssamlingar (the full lagrummet.se agency list, county `\d+FS` series excluded), 66 live + 5 closed series with no live harvester (rsfs, sosfs/hslffs, sjvfs, svkfs), no per-agency pipelines; SKVFS and MTFS alone select detached headful Chrome, all others use requests/HTTP2 |
| `harvest.py` | per-agency enumerate/resolve architectures (indexed/paginated/json/sitemap enumerators; landing/direct resolvers + file classifiers) wired onto `lib/harvest.py`'s shared `walk`/`HarvestWatermark` loop; `Agency.browser` selects `lib/browser.py` without changing the loop |
| `skvfs.py` / `mtfs.py` | pure catalogue/identity rules plus protected resolvers for the two F5/Shape sources; SKVFS resolves a detail page then its exact PDF and also emits the RSFS predecessor, while MTFS headings point directly to PDFs |
| `download.py` | the `lagen foreskrift download` front over the engine (`--full`, `--only`; closed-series fs are a logged no-op); `reap` (`stored_series`/`superseded`/`superseded_files`) removes a record an fs reassignment left behind under its old författningssamling — an agency renamed/absorbed (MSB→MCF) re-files its whole listing under the new fs, but the pre-reassignment run's records stay on disk under the old one, claiming the same landing pages; the test is positive (two records claim one landing page, the landing slug says which is real), never a scoped scan (`lagen foreskrift reap [--dry-run]`) |
| `model.py` / `structure.py` / `parse.py` | as-published `Foreskrift` model, PDF → statute-shaped structure → artifact. Five printed shapes are read before or after the prose reflow, each of which the reflow otherwise flattens into running text: the **ingress** (`_ingress_start` — the preamble stating the beslutsdatum and the bemyndigande, required by 18 b § författningssamlingsförordningen (1976:725); `_body_start` skips to the first `N §`, so 11 538 of 11 899 regulations published no preamble at all until it was found by walking back from the body to the last thing that cannot be ingress. Three signals stop the walk, measured over 250 regulations: a **rubrik** before the first § (150 of 229, the commonest by far), a masthead furniture line (47) and the title sentence's semicolon (30); the remaining 2 reach the top and keep the old no-ingress behaviour); the **footnotes** under a page-foot rule (`lib/pdftext.ruled_footnotes` — the rule alone is not evidence, since the same row of underscores precedes an ikraftträdande clause in 3 920 regulations, so the lines below it must also be set smaller than the page's running size; scanned into artifact `footnotes` and rendered through the shared endnote partial with no back-link, poppler having dropped the superscript marker. The notes are read for metadata *with* the body, because a föreskrift prints metadata as a note: SKVFS 2006:32 sets its own ikraftträdande clause under the rule and KIFS 2017:7 grounds the directive it transposes in a "Jfr …" note. Over 1 500 regulations the notes changed metadata on exactly those two shapes and never replaced a value the body stated. A konsoliderad version carries its own notes beside its own body, so the page never lists the base regulation's notes under a consolidated text); the **ordförklaringar table** (`lib/tabell.split_two_column`, `merge_continued` joining it across the page break); the **bullet lists** the reflow glues into one stycke (`_split_bullets` — poppler sets the bullet as its own run, so the character survives; 7 688 blocks carried at least one. Both bullet characters count: an agency setting its bullets in Symbol prints U+F0B7, and SKSFS 2014:7 uses it 90 times with not one U+2022); and the **allmänna råd** (`_group_allmanna_rad` — the advisory text under a paragraf, a råd running from its heading to the next kapitel/paragraf/rubrik, 6 419 blocks across 895 regulations that otherwise read as further stycken of a binding §. A råd also stops at the document's *closing matter* — the rule of underscores above the ikraftträdande block, or a "träder i kraft" sentence whose subject is this document, the same pair `ikrafttradande_date` uses to tell the document's own date from one it quotes. Without that, a råd printed last with no heading after it swallowed the entry-into-force clause and the signing officials, and the page set binding text inside the advisory box under a label saying it is not binding — TFS 2009:2, KVFS 2021:2 and RPSFS 2011:12, ~180 regulations in that position; the heading's own words stay on the node under `text` — not a key of its own, since `lib/text` collects `text` and that is what feeds the search index — so a heading naming the provision it explains links it, "Allmänna råd till 2 kap. 1 § andra stycket häkteslagen (2010:611)" → `2010:611#K2P1S2`). Rubrik depth is the rank of the heading's font size among the *body's* other heading sizes (`_rank_rubriker`), level 1 being the kapitel heading — ranked over the whole PDF the masthead title, set in the kapitel size, pushed every real rubrik one level deeper, and before that every heading of a chaptered document came out level 1 and the table of contents read flat (the closed series' bodies are ordinary corpus PDFs, `parse.body_path` resolves them under the download tree like any harvested source); a record's konsoliderad PDFs parse into the artifact's `consolidations` (deduped, cutoff pinned by `konsolideradTom`, the agency's PDF url retained) — the latest parsed one becomes the presented reading text (`lib/text.presented_consolidation`), and when the base text is also parsed the parse run emits a `.grund.json` sidecar that generate renders as the uncatalogued as-enacted page at `{uri}/grund` (`layout.foreskrift_grund_pages`, the SFS `.versions.json` pattern); `parse.clean_title`/`title_from_masthead` fall back to the PDF's printed masthead title ("…s föreskrifter om …; beslutade den …") when the harvest title is link chrome ("pdf, 63 kB") rather than prose — the masthead is read with its standing text (samling name, ISSN, utgivare, "Utkom från trycket", FS number, dates) deleted, since its two columns interleave and drop that text into the middle of the title sentence; `_fs_key`, not a bare `.lower()`, mints an `upphaver` target's slug, so a designation printed with an accent (ÅFS) folds to its registry slug (`aafs`) instead of a dangling literal one; the citation scan is dated to the föreskrift's own `beslutsdatum`, falling back to its årsutgåva off the basefile (`lib.util.approximate_date` places a bare year mid-year) when none is recorded |
| `data/series.json` | the hand-edited författningssamling registry (`lib/datasets.FS_SERIES`/`load_fs_series`): printed designation, official title and, for a series whose agency was renamed or absorbed, the `successor` slug (DIFS → IMYFS). Drives the browse (`lib/facets.py`): a samling heads by official name + designation rather than its internal slug, orders Swedish-alphabetically (ÅFS after Z, `_fs_order`), and a succeeded series folds its documents under the successor with a note naming the predecessor(s) (`fs_predecessors`) |
| `render.py` | the föreskriftssida: regulation text, ändringsföreskrifter and the consolidation/grund banners. Registered as this source's page renderer in `build.SOURCE_RENDERERS`; `render(art, site) -> str`, built on the `lib/page` kit, with its own page template in `templates/` |

`foreskrift`, `avg` and `rs` share one masthead entry, **Myndigheter**
(`/myndigheter/`, `render._render_myndigheter`) — a landing introducing the
three kinds of thing a förvaltningsmyndighet publishes about the law it
administers side by side: the rules it issues (föreskrifter), the cases it
decides (avgöranden) and how it says it reads them (rättsliga
ställningstaganden), each linking into its own browse tree (`lib/tpl.py`'s
`MAST_NAV`, replacing the old "Föreskrifter" entry, which
left avgöranden unreachable from the chrome). A föreskrift samling under 200
documents (amendments included) lists on one page; at or above it keeps
per-year pages with the year selector as a banner atop the list, not the left
nav (`browse.generate_browse`'s `FS_YEAR_SPLIT_MIN`); an ändringsförfattning
nests under its base regulation instead of listing separately
(`catalog.andrar_edges`, `facets._fold_fs_amendments`); a föreskrift that also
has a konsoliderad version lists once, under that version, marked as
consolidated (`facets._fold_fs_versions`) — its as-enacted `/grund` sibling
stays reachable from the document page rather than listing again beside it.
A succeeded författningssamling gets its own page saying its föreskrifter now
list under the successor (`browse._write_succeeded_series`), and `generate`
deletes any browse directory a run no longer writes (`browse._reap_browse`) —
otherwise a folded-away samling's old pages kept serving under the previous
build's chrome indefinitely.

**eurlex vertical (EU law — EUR-Lex / CELLAR)**
| File | What |
|---|---|
| `download.py` | harvester for the Publications Office CELLAR repository, keyed by CELEX (SPARQL discovery + SOAP/REST fetch; Formex/HTML/PDF manifestations). An act published across several OJ files exposes one Formex item per part and no single item is the document, so a *multi-part* Formex manifestation is fetched whole, as one zip (`ZIP_ACCEPT` on the manifestation URL) — the `.fmx4.zip` bundle `parse.formex_members` already reads in order. Taking a part instead stored 2004/18 as its Annex I, and the Charter of Fundamental Rights as its table of contents. `lagen eurlex backfill [<sector-digit>] [--limit N]` (`build.eurlex_backfill`) downloads through this same `download_document`, but targets acts the corpus *cites but does not hold* rather than walking discovery — ranked by `catalog.dangling_targets` off the citation graph, most-cited first; its first use is the repealed EU acts a sector-3 bulk import never carries (a bulk dump ships only acts in force). `lagen eurlex refresh-metadata [<CELEX> ...]` re-reads CELLAR's metadata into each `notice.ttl` without refetching content — the sweep that gives an already-harvested corpus the validity pair (`cdm:resource_legal_in-force` + `cdm:resource_legal_date_end-of-validity`) `parse` turns into the artifact's `expired` key. The sweep is the backstop, not the mechanism: a repeal is recorded on the *repealed* act, which the work-date-bounded walk never revisits, so `sync` asks each year's newly stored acts what they repeal (`cellar.fetch_repeals`, the `cdm:resource_legal_repeals_resource_legal` edge the old act has no inverse of) and re-reads the targets the corpus holds (`refresh_repeal_targets`) — 64% of the repeals CELLAR records, measured; the rest end by their own terms, named by no incoming act, and need the audit, which skips documents already recorded as repealed so it shrinks each run |
| `bulk.py` | unpack a CELLAR bulk "legislation" dump into the per-CELEX layout the incremental harvester produces, so the whole corpus can be imported from official dumps |
| `model.py` | typed `EurlexDoc` model parsed from Formex (legislation/treaties + judgments); `doctype` classifies a sector-6 CELEX by its two-letter document code — CJ/TJ/FJ judgment, CC/CV/CP an Advocate General opinion, CO/TO/FO an order — so an opinion is no longer misfiled as a judgment |
| `parse.py` | orchestrator: Formex (the structured XML manifestation) → `EurlexDoc` → JSON artifact; `parse_act_body` descends through Formex's sequence wrappers so an `ACT` root (preamble + `ENACTING.TERMS`) and a `GENERAL` root (the CELLAR shape for an act published across several OJ files, wrapping the body in `CONTENTS`, sometimes inside a further `GR.SEQ` as the Charter does) parse through one walker — reading `ENACTING.TERMS` alone left 2004/18 and the Charter with no articles at all; `_emit_table` keeps a table row's *interior* empty cells (only trailing ones are dropped), since in a jämförelsetabell the column a value sits in is what it means; `parse_opinion` reads an AG opinion's `CONCLUSION` structure (opening prose, numbered `NP` opinion paragraphs, `GR.SEQ` section groupings) the same shape a judgment's contents take; judgment paragraphs come from both the pre-2012 plain `NP` and the later `NP.ECR` Formex shapes, and `parse_hearing_report` reads `REPORT.HEARING` (the only text CELLAR holds for the oldest ECR cases, Beentjes included); `_refparser(lang)` scans citations per manifestation language -- the English EULAGSTIFTNING surface for pre-accession case law with no Swedish version. `UNCARRIED` names, CELEX → why, the acts this corpus deliberately does not carry: `parse_dir` raises `SkipDocument` for them and the driver writes the empty artifact that marks a document built-and-not-to-be-retried, so the catalog drops its row and the index its units. The bar is *the document cannot be served* (the one entry is a 97 MB Formex annex that renders to a 53 MB page), each entry states the measurement that meets it, and nothing reaps `generated/` — uncarrying an act that was previously carried means unlinking its html by hand, here and on prod. A division's designation and an article's are kept apart from their titles -- Formex sets `TI`/`STI` and `TI.ART`/`STI.ART` as separate elements, and the block keeps them as `label` and `text` rather than flattening the pair, so the page can hang the designation in a gutter beside the title. `parse_html.py`/`parse_pdf.py` fill the same two fields from the marker and the text they already read separately |
| `parse_html.py` / `parse_pdf.py` | fallback body parsers for the (many older) acts with no Formex — OJ HTML/XHTML, then PDF via `pdftohtml -xml` as last resort; the pre-2000 "Avis juridique important" HTML has no marker tables, so `parse_html` recognises its flat-paragraph recitals by sequence (a leading number trusted only while it counts up) bounded by the framing line and the enacting formula, and skips `<p>` wrapper elements that merely contain block-level content (the legacy whole-document wrapper, a judgment `<p>` wrapping a `<table>`) so their contents are not emitted twice. Titles come from two separate recoveries, because the two document families publish nothing in common: an act's is the `doc-ti`/`ti-doc` class, else (legacy HTML) the class-less header line shaped like an act title; a case's is `case_title`, the opening line that names the document a judgment/order/opinion (`Vocab.decision_opener`) plus the advocate general's name and the date line it runs onto. The courts set those header lines in caps, so `normalize_case` renders the title back in the Court's own case — the opening phrase in the canonical spelling the vocabulary stores, then the advocate general's name in name case, particles and all ("FÖRSLAG TILL AVGÖRANDE AV GENERALADVOKAT JEAN RICHARD DE LA TOUR" → "Förslag till avgörande av generaladvokat Jean Richard de la Tour"). Recasing stops at the opener unless a name follows it (`Vocab.names_follow`): a court's title runs on into the parties, where recasing wrote the Greek utility DEI as "Dei". Case law used to fall through to the *act* recovery, which — a case having no preamble to stop the scan at — read the length of the document and took the first act the case quoted, so 1 134 judgments and opinions were titled after a regulation they cite (62025CC0185 was headed "Artikel 4 led 7 i … förordning (EU) 2016/679 … (allmän dataskyddsförordning)") |
| `structure.py` | group an act's flat block sequence into its containment hierarchy (`nest`, the parse-time tree builder; the anchor grammar itself lives in `lib/eu_structure.py`) |
| `definitions.py` | extract an act's defined terms and interlink their in-act uses |
| `lang.py` | localized structural vocabulary for the non-Formex (html/pdf) parsers (Formex is tag-marked, so its parser needs no language knowledge) and for `annotate.py`'s annex trim, which reads the same `VOCAB[lang]["annex"]` words back out of already-parsed text. `decision`/`decision_name`/`decision_date` are the case-law half — how a court names its own document, stored as the *canonical spelling* of each opening phrase ("Domstolens dom", "Förslag till avgörande av generaladvokat", "Judgment of the General Court") rather than as a pattern, so `parse_html.normalize_case` can render a caps header back in the Court's own case from the same data that matched it (`decision_opener`, longest phrase first; `decision_name` marks the openers a person's name follows). The courts are enumerated rather than matched as "&lt;something&gt;s dom", since a judgment's prose is full of lines opening "Kommissionens beslut … av den …" |
| `annotate.py` | `lagen eurlex ai-annotate <CELEX>` — author the editorial `.ann` layer for a sector-3 act with an LLM, written to the curated store (`lib/annstore.py`); `_annex_cut` trims trailing annexes from the prompt only (the artifact keeps them) — CLP (32008R1272) went from 1,132,799 to 40,913 prompt tokens, making it annotatable at all |
| `correspond.py` | the EU-act **lineage**: a recast's own jämförelsetabell annex (2014/24 bilaga XV → 2004/18, 2004/18 bilaga XII → 93/37, 93/36 and 92/50) read into article↔article pairs. Mechanical — the table is `row` blocks in the act's own structure — so `parse` runs it on every act and stores what it finds under the artifact's `correspondence` key (rule:artifact-is-truth), exactly as the förarbete parser stores `implements`; there is no separate action and no authored layer to keep in step. It is a no-op for ~98% of sector-3 acts and every judgment (0.0 ms), and a few ms on the largest table in the corpus. The table is located by its **header row**, not the annex heading (only ~20% sit under a heading named *Jämförelsetabell*; 2014/24's is `BILAGA XV`); the self column is found by wording because orientation varies and reversed is the norm (424 of 456 tables put the repealed act in column 1); and article numbers are read from cell *text*, never the cell's links, since the citation engine resolves "Artikel 12" in any cell against the act being parsed. A header column the engine left unresolved (Euratom acts, `(EU, Euratom)` numbering) is read via `lagrum.celex_uri`, with the pre-2015 "nr" settling number/year order. `catalog._index_document` writes the pairs into `directive_correspondence` as it indexes each act, so the layer is incremental with the artifact rather than a post-pass re-reading 64k files |
| `casenames.py` | `lagen eurlex casenames` — harvest CELEX → usual name for named EU cases ("Schrems II") from Wikidata (property P476) into `data/casenames.json`, read by `lib/eucasenaming.py` |
| `data/treaties.json` | curated Swedish names for EU primary law (sector-1 CELEX, keyed by CELEX stem with the `(NN)`/`R(NN)` revision suffix stripped) — the founding/consolidated treaties carry no extractable short title on their own, so this stands in as both short and official title; read by `lib/labels._eurlex`. The Fördrag browse *family* grouping (`lib/facets._treaty_family`) is derived from the CELEX document-type letter, not from this file |
| `render.py` | the EU-rättsaktssida: articles, recitals and the editorial annotation layer. Registered as this source's page renderer in `build.SOURCE_RENDERERS`; `render(art, site) -> str`, built on the `lib/page` kit, with its own page template in `templates/`. An article heading hangs its designation in a gutter -- the word small over the number, a pilcrow under it -- beside the article's own title; `_article_parts` reads the designation off the artifact's `label` field. A division heading is all capitals in the sources ("KAPITEL I ALLMÄNNA BESTÄMMELSER"): `_division_label` reads the designation off the artifact's `label` field and `_case_map`/`_sentence_case` re-set the title in sentence case, taking each word's capitals from the act's own prose rather than from a list of proper nouns -- so "kommissionen" lowercases while "Europeiska centralbanken" and "96/9/EG" keep theirs. A word needs *consistent* evidence (twice, and more than twice as often as the act writes it lowercase); a single cross-reference is not evidence. Only an all-caps heading that carries a designation is touched -- a free-form one ("FÖRLAGA TILL INTYG ... AMERIKAS FÖRENTA STATER") is left exactly as published, since its names appear nowhere else in the act to read a case from |

**hudoc vertical (European Court of Human Rights case law)**
| File | What |
|---|---|
| `download.py` | bulk paginator over HUDOC's public `/app/query/results` JSON endpoint plus `/app/conversion/docx/html/body` for each full text, fetched through a small `ThreadPoolExecutor` (`WORKERS=4`) that keeps bodies in flight ahead of the walk; English by default, `PAGE_SIZE=500`, `--lang`, `--only` and `--limit`. Two collections, each a download scope with its own watermark: `judgments` (Grand Chamber + Chamber, 21,672) and `decisions` (33,633). The walk is **sliced by year** (`FIRST_YEAR=1955`) because HUDOC serves no result past `start=10000` while still reporting the true `resultcount` — an unsliced walk silently stopped at the 10,000th document and left the store reaching back only to 2009-09-22; a year past `PAGING_CAP` raises, and an exhausted enumeration checks its summed year counts against the collection total |
| `summaries.py` | the Court's own Case-Law Information Notes (6,505 in English) as a **link on the case they summarise**, never a second document saying the same thing. Runs as part of an unbounded `hudoc download`, since it is one index walk and no body fetch. HUDOC gives a note no pointer to its case, so the join is `(application number, date)`. One sidecar per case under `<downloaded>/hudoc/clin/<itemid>.json`, so a re-run re-stales only the parses whose summary moved; a summary the Court withdraws has its sidecar reaped, and an empty match set raises rather than reaping all of them |
| `translations.py` | Domstolsverkets 87 Swedish translations (2014-01-14 … 2015-12-15) are commentary on the judgment they translate, not documents of their own: this drafts `commentary/hudoc/<itemid>.md` in the content repo, the inverse of the English-translation link `commentary/sfs/1971/291.md` opens with, and `kommentar parse` picks them up. Runs alongside `summaries.py` in the same download. Joined on the ECLI, which a translation shares exactly with its original; a translation whose original is not held is reported, never guessed |
| `download.unique_index` | the half both joins share: `key -> basefile` over the stored records, telling apart the two reasons two cases claim one key. **Same language** is HUDOC's own data — it stores some decisions twice under two item ids, and mints one ECLI for decisions taken together — so the key identifies no case, is dropped and is counted (10 ECLIs, 121 application/date pairs over 39,046 records). **Different languages** means the store was harvested with `--lang ENG,FRE`, where every expression of a case repeats its identity and no join is possible at all: that raises |
| `model.py` | typed `HudocCase`/`Block` model; one stable `/dom/echr/{itemid}` expression per HUDOC item; article-facet metadata becomes explicit references to `ext/coe/{ETS}#A…`; restarted numbering keeps the first canonical paragraph anchor and suffixes later occurrences (`#P1-2`); `record_date` (shared with `citations.py`) reads the decision date off whichever of `judgementdate`/`decisiondate`/`kpdate` the record carries |
| `citations.py` | case-law cross-references inside a judgment's own text, as inline links: application numbers ("no. 27229/95") and named cases without one ("Keenan v. the United Kingdom"), resolved against the held corpus's own metadata/titles (`index()`, built once from the stored records). 88% of the ~175,000 application-number citations across 13,567 judgments name a document this corpus already holds; a number or name borne by several held documents links only where a printed date beside the citation, or the fact that exactly one candidate is a judgment, picks one — ambiguity refuses rather than guesses between a chamber and a Grand Chamber judgment (rule:fail-fast) |
| `treaties.py` | the Convention provisions a judgment applies, on top of `lib.treatyref` (rule:second-use-goes-to-lib): "Article 8 of the Convention" → `ext/coe/005#A8`, "Article 1 of Protocol No. 1" → `ext/coe/009#A1`. Local knowledge `treatyref` can't have: inside an ECHR text "the Convention" is the ECHR (a guarded pattern that stands down before a longer title such as "the Convention on the Rights of the Child") and "Protocol No. N" numbers the ECHR protocol series — the same words number a different family on a CoE treaty page, which is why both are caller extras rather than curated names |
| `casenames.py` | `lagen hudoc casenames` — writes the committed snapshot `data/casenames.json` (37,544 case names + 93,781 application numbers, `[kind,date,itemid]` candidates) off `citations.index`'s stored-record join, keyed on `citations`' own normalized `applicant\|respondent\|serial` and on the application number; `lib.datasets.load_emd_cases` reads it back as pure JSON. The join surface for resolving Swedish citations ("Osman mot Förenade kungariket") in förarbeten — consumed by `lib/emdref.py`, the EMDRATTSFALL parse type's matcher — the same role `dv/namedcases.py` and `eurlex/casenames.py` fill for their sources |
| `data/respondents_sv.json` | hand-edited Swedish respondent-state names mapped onto `casenames.json`'s normalized respondent keys ("Förenade kungariket" → `["united kingdom"]`), a state whose English key shifted over the corpus's lifetime mapped to every key it has borne; read via `lib.datasets.EMD_RESPONDENTS` |
| `parse.py` | converted Word HTML → CSS-derived headings, numbered paragraphs and notes → artifact; skips only TOC links (the TOC can share its container with the judgment) and marks a body with neither a numbered paragraph nor a heading as deliberately empty (`SkipDocument`) — HUDOC's language and cover stubs are one sentence, while a decision numbers nothing and states its facts and reasoning under headings, so the older "no numbered paragraph" test blanked 62% of the decisions collection (4,960 stored documents changed from empty to parsed; the one document that still skips is the French-only stub the guard exists for). An item HUDOC answers 204 No Content for — mostly pre-1980 Commission decisions it holds as metadata only — is stored as an empty body and skipped as "holds no text for this item", which is a different statement from "the body did not parse". `to_artifact` takes `_refs` — `citations.refs` merged with `treaties.refs`, a case-law span winning any overlap — as its `refs_for` scanner, so both the case-law and the Convention/protocol citations render as links |
| `render.py` | the europadomstolssida: the judgment body and its article metadata. Registered as this source's page renderer in `build.SOURCE_RENDERERS`; `render(art, site) -> str`, built on the `lib/page` kit, with its own page template in `templates/` |

**coe vertical (Council of Europe Treaty Office)**
| File | What |
|---|---|
| `download.py` | one search POST to the Treaty Office's anonymous JSON web service (`conventions-ws.coe.int`, token embedded in the public `full-list2` page, mounted via `lib.net.mount_legacy_tls` for its small-DH-key TLS) returns all 233 treaties' metadata in one call; `getLieux` resolves opening places; each official English text downloads as a plain PDF from `rm.coe.int` (no challenge, no HTML scraping) |
| `model.py` | typed `Treaty`; canonical `ext/coe/{ETS-or-CETS-number}` identity and an `rdfs:seeAlso` bridge from the ECHR instruments reproduced in SFS 1994:1219 |
| `parse.py` | official English PDF → article/subarticle tree (`#A8`, `#A6P3Ld`) via `pdftohtml -> page_paragraphs -> build_structure`; supports numeric, Roman and compound article designations plus section-only amending instruments, and context-suffixes repeated printed designators so every node id is unique; `build_structure` takes a `refs_for` scanner (`lib.treatyref.spans`, self excluded), so the sibling treaty a protocol names by full title renders as a link while the treaty's own title stays plain |
| `data/names.json` | Council-of-Europe treaties by Swedish name → ETS/CETS number, hand-edited; read by `lib.lagrum.load_treaties` (citation grammar) **and** by `render._treaty_named` — its keys are the curated *central* treaties surfaced first on the folkrätt landing, and its `abbr` is the badge (EKMR, …) |
| `render.py` | the europarådsfördragssida: the treaty's provisions. Registered as this source's page renderer in `build.SOURCE_RENDERERS`; `render(art, site) -> str`, built on the `lib/page` kit, with its own page template in `templates/` |

**icrc vertical (ICRC international humanitarian law treaties)**
| File | What |
|---|---|
| `download.py` | one paginated list call (`page[limit]=50`) over the ICRC's anonymous Drupal 10 JSON:API (`ihl-databases.icrc.org/en/jsonapi/node/treaty`) enumerates all 111 IHL instruments; one per-treaty `include=`-expanded fetch returns the whole self-contained envelope — metadata, authentic article text (`field_treaty_content`), per-state participation (`field_treaty_state_parties`), depositary/topics/languages taxonomy terms — the stored record, so parse never touches the network; incremental off the node's `changed` stamp + `HarvestWatermark`; `--only <ICRC-number>`, `--limit`, `--force` |
| `model.py` | typed `Treaty`/`Provision`/`Party`; canonical `ext/icrc/{ICRC-number}` identity kept local to the vertical (rule:second-use-goes-to-lib — nothing in `lib` mints an ICRC target yet); article-fragment ids `A<n>`/`Preamble`/`Testimonium`/`Annex<n>`; `kind` classifies doctype as treaty/protocol/declaration |
| `parse.py` | resolves the envelope's `included` relationship graph into the `Treaty` model, then `artifact()`: `.to_artifact()` with the cross-treaty citations resolved via `lib.treatyref` (self excluded) — inline spans where one target is unambiguous ("(Protocol I)" beside its Geneva context), document-level `references` also carrying the family names ("the Geneva Conventions") that resolve to several instruments and so link nothing; article body HTML → stycken via BeautifulSoup; skips commentary front matter (ToC/Foreword/Introduction) |
| `data/names.json` | the four 1949 Geneva Conventions and their three Additional Protocols (ICRC numbers 365/370/375/380/470/475/615), hand-edited, with informal Swedish names and acronyms (GK I–IV, TP I–III) — the curated *central* instruments surfaced first on the folkrätt landing, mirroring `coe/data/names.json` |
| `render.py` | the IHL-fördragssida: the instrument's provisions. Registered as this source's page renderer in `build.SOURCE_RENDERERS`; `render(art, site) -> str`, built on the `lib/page` kit, with its own page template in `templates/` |

**untc vertical (UN Treaty Collection — status and authentic text)**
| File | What |
|---|---|
| `download.py` | two fetches per curated treaty, because no one publisher carries both halves: the status page from `ViewDetailsIII.aspx?src=TREATY&mtdsg_no={id}&chapter={n}&clang=_en` (an ASP.NET page that answers unattended clients directly), and the authentic text from the depositary named in `data/treaties.json` — OHCHR for twelve, a born-digital PDF for VCLT and UNCLOS, never the UNTS's own volumes, which are scans. The corpus is a tiny fixed set, so the walk runs with no watermark, skipping a treaty already on disk unless `--full` re-fetches it (a new ratification changes the participation table); `--only <UNTS number>`, `--limit` |
| `model.py` | typed `Treaty`/`Party`/`Provision`; canonical `ext/untc/{unts}` identity kept local to the vertical (rule:second-use-goes-to-lib). `structure` is the treaty's own articles as the `artikel`/`stycke` node shape `icrc` mints, each keyed on its fragment (`A5`, `AII`, `AnnexVI_A31`); anchors run through `lib.artifact.unique_id` as the net, though scoping now leaves it nothing to disambiguate. `RE_ORDINAL` is what tells an article's number from an annex heading, which numbers nothing |
| `text.py` | the authentic treaty text to provisions. Two shapes reduce to one line stream: OHCHR's `.field--name-body` block (twelve of the fourteen) and a born-digital PDF read with `pdftotext` (VCLT, UNCLOS). An article opens on its own line in three forms — `Article 5`, `Article 1 - Definition…`, `Article 20 Personal mobility` — and a bare space is only allowed before a capital, which keeps "Article 5 shall apply mutatis mutandis" out. `treaty_body` cuts what a published PDF prints around the treaty: a contents block goes **whole** (UNCLOS's 33 pages of it set each `Article N.`/`ANNEX I.` on a line of its own with no leader, so dropping leader lines alone counted 885 articles against 320), a leader needs **five** dots (three is the ellipsis inside the Refugee Protocol's article 1(2)), and a **second** contents block ends the treaty (the same PDF appends the conference's Final Act, whose Annexes I/II/VI would claim the Convention's anchors). An annex scopes the articles under it — UNCLOS restarts at Article 1 nine times — and is a provision itself, since Annex I is 17 species and no article. `fragment` repairs the two numerals the UNCLOS PDF sets with a lowercase L for the digit 1 ("Article 3l"), and only where the run already carries a digit |
| `parse.py` | joins the two halves — scrapes the stable ASP.NET control ids for conclusion/entry-into-force/UNTS registration and the participation grid (anchored on the grid's own control id `tblgrid`, not a header cell — some treaties precede it with a decoy territorial-notification table under the same "Participant" header); footnote `<sup>` stripping, `<a class="noteIndex">`-wrapped declaring states, and consent-form markers (`a` accession, `d` succession, `c` formal confirmation, `A` acceptance, plain date ratification). Offline (reads the stored page) |
| `data/treaties.json` | the curated 14-instrument list (one harvest engine over all, rule:configured-by-data) — VCLT, UNCLOS, the Genocide Convention, the core human-rights instruments (ICERD/ICESCR/ICCPR/CEDAW/CAT/CRC/CMW/CRPD/CED) and the Refugee Convention + Protocol. `unts` is the **identity** — the UNTS registration in the UN's own form (`I-14668`, as in `volume-999-I-14668-English.pdf`) — because it is what the UNTS cites itself by and it survives for an instrument whose depositary is not the UN. `mtdsg_no`/`chapter` complete the status query, `text` names where the authentic text really lives, `title` is the authoritative English name (the page headline is generic), `sv`/`abbr` drive the folkrätt listing, `group` is the Swedish subject heading it files under |
| `render.py` | the FN-fördragssida, in the order the two halves were fetched: the authentic text (every node through `page.provision_section`, the same walk `icrc` uses, so the TOC and the context rail are wired by construction) then the participation table. An annex heading has no ordinal, so `page.article_label` names it by its own heading (shared with `icrc`). Registered as this source's page renderer in `build.SOURCE_RENDERERS`; `render(art, site) -> str`, built on the `lib/page` kit, with its own page template in `templates/` |

**icc vertical (International Criminal Court case law)**
| File | What |
|---|---|
| `download.py` | two-source harvest, both Cloudflare-free (the ICC's own `/court-record` detail pages are Cloudflare-walled): a facet scrape over icc-cpi.int `/decisions` (`decision_type_of_decision`, curated by `data/decision_types.json`) enumerates the curated substantive set and yields each record's document number; the ICC Legal Tools API (`legal-tools.org/api/ltdDocs`, `externalId` `like` prefix match, case-sensitive) resolves that number to metadata, slug and the decision PDF (`/doc/<slug>/pdf`), preferring the English primary over `-t<LANG>` translation variants; incremental via a date watermark, `--only <ICC-doc-number>`, `--limit` |
| `model.py` | typed `Decision`/`Block` (HUDOC-shaped); `to_artifact()` turns numbered paragraphs into the citation-unit article tree (`P<n>` ids); canonical `ext/icc/{doc-number}` identity (slashes flattened to underscores) kept local to the vertical (rule:second-use-goes-to-lib) |
| `treaties.py` | which instruments a decision applies, on top of `lib.treatyref` (rule:second-use-goes-to-lib): `references()` for the document-level `references` (269 decisions went from empty to a matcher that reads 13,887 "article N … of the Statute" forms across 244 of them), `refs()` for the inline spans, which add the Court's own filing-number citations — a decision cites a sibling by document number ("ICC-02/11-01/11-129") on nearly every page, and `_held` resolves the base number onto whichever variant (a `-Red` redaction, a `-Corr`) is actually on disk. Local knowledge `treatyref` can't have: inside an ICC decision "the Statute" is always the Rome Statute (`SHORT_FORMS`), and the Rules of Procedure and Evidence — named 1,752 times — are not a treaty the corpus holds, so they resolve to nothing rather than a guess |
| `parse.py` | Legal Tools metadata (ICC-listing fallback) + PDF text (`lib/pdftext`) → artifact; strips the per-page court-record running header, classifies numbered paragraphs vs. section headings (`_classify`, pure); a decision Legal Tools couldn't resolve stays metadata-only (empty structure). Also drops the Legal Tools download's own furniture — a "No: ICC-… 3/40 PURL: https://www.legal-tools.org/doc/…" footer sat inside the rendered body text 3,116 times across 92 decisions; a footer that is its own paragraph is dropped whole, one glued onto a footnote is stripped off the edge, leaving the footnote's own (unstamped) legal-tools url alone. `to_artifact` takes `treaties.refs` as its `refs_for` scanner, so the treaty and sibling-filing citations above render as links |
| `data/decision_types.json` | the curated Rome-Statute decision types (one harvest engine over all, rule:configured-by-data) — Art 74 verdicts, 76 sentences, 61 confirmation, 58 arrest warrants, 81/82/82.4 appeal judgments, 75 reparations, 15/18-19/53.3/110 — each with the icc-cpi.int facet id, the catalog/facet `kind`, and the Swedish heading it files under on the folkrätt landing; deliberately excludes the ~10k procedural Decision/Order mass |
| `render.py` | the ICC-sida: the decision and its case metadata. Registered as this source's page renderer in `build.SOURCE_RENDERERS`; `render(art, site) -> str`, built on the `lib/page` kit, with its own page template in `templates/` |

**icj vertical (International Court of Justice case law)**
| File | What |
|---|---|
| `download.py` | one index, two transports. The Drupal view at `icj-cij.org/decisions` answers ordinary HTTP, and a single request with `from=1946` returns every decision the Court has ever issued (877 rows) — its default is `from=2023`, which shows 87, and it does not paginate (`?page=1` repeats page 1). No `to` is sent: the select only offers years up to the current one and answers an out-of-range year with an empty page under a 200. `in_scope` keeps the Court's own word on the law — 158 judgments, 31 advisory opinions and the 66 orders that indicate provisional measures — and drops the ~620 docket orders that fix and extend time-limits. The decision PDFs are behind a Cloudflare challenge no header or cookie from the index satisfies, so they are fetched through `lib.browser.DetachedChrome` (one session for the whole run, ~9 s per document); incremental via a date watermark, `--only <decision stem>`, `--limit` |
| `model.py` | typed `Decision`/`Block`; the Court's own decision filename is the identity (`070-19860627-JUD-01-00` = case, date, kind, part), normalised across the one file that separates with `_`; `to_artifact()` turns the numbered paragraphs into the citation-unit tree (`P<n>` ids); canonical `ext/icj/{stem}` kept local to the vertical (rule:second-use-goes-to-lib) |
| `parse.py` | the decision's page range from the printed *I.C.J. Reports* → artifact. `body_pages` cuts the Reports' bilingual front matter at the Court's **dateline** ("YEAR 1986"), which survives OCR where the letterhead does not — the 1949 scan prints "INTERNATIONAL COUI2T OF JUSTICE" — with the letterhead as fallback and a raise when neither is there. `clean` removes the Reports' running head and the printer's imposition stamp, cutting them out of the block rather than rejecting it (rejecting the block cost one judgment 450 paragraphs). `paragraph_chain` picks the Court's own numbering out of every number in the text: the longest chain that counts up in steps of at most four, which must open at the Court's first paragraph and hold at least three members — so a quoted ICTY paragraph joins no chain, an OCR hole is stepped over, and a decision the Reports never numbered gets no anchors rather than invented ones |
| `ocr.py` | dictionary-guided repair of the pre-2002 Reports scans' OCR layer, whose systematic confusions (`al1` for all, `Judgrnent` for Judgment) run at ~0.43% of tokens. A token is rewritten only when one known confusion turns it into a word the Court itself uses and it is not already one; two readings mean no rewrite, and a token of pure digits is never touched (rewriting "111." to "iii." ended a judgment's paragraph sequence) |
| `treaties.py` | which treaties in the corpus a decision applies, on top of `lib.treatyref` (shared with `icc`, rule:second-use-goes-to-lib): the instruments the Court interprets — the Genocide Convention, the VCLT, UNCLOS, the ICCPR/CAT, the Refugee Convention — had zero inbound links before this. `references()` for the document-level `references`, `refs()` for the inline spans. Nothing local is added: the Court's own "the Statute" means the Statute of the Court, which the corpus does not hold, so it correctly resolves to nothing (unlike the same words inside an ICC decision) |
| `reports.py` | the Court's own citation grammar, "I.C.J. Reports 1990, p. 92": 4,012 such citations across 250 of the 255 held decisions, every one plain text. `own_citation` reads a decision's official citation off its own cover sheet (`pdftotext` on the first pages, falling back to `pages_with_ocr` only where that yields nothing) and feeds `Decision.reports_citation`; `index()` inverts that over every held PDF into `(year, volume, start page) -> basefile`; `refs()` links a body citation whose (year, page) is a held decision's own start page — **exact start page only**, since with 255 of 877 decisions held, the gap between two held start pages proves nothing about an unheld decision's pages in between, and attributing a pinpoint cite to the nearest held one would mislink it (rule:fail-fast) |
| `data/vocabulary.txt` | the Court's own words, harvested by `tools/icj_vocabulary.py` from the decisions it published born-digital — identified by measurement (a scan carries a raster image on every page, a typeset volume only where a sketch-map is), so the corpus that defines "a word" never depends on the repair being right. French is in the list and cannot be cut out: the Court's English text quotes it constantly, every I.C.J. citation carries "Recueil". It is harmless because a repair fires only where a *confusion* turns an English token into one of those words, which over 500 measured repairs never happened |
| `render.py` | the ICJ-sida: the decision, its case metadata, and — where the repair count reaches `SCAN_REPAIRS` — the banner saying the text was read off the printed Reports, which the Court states is the official version. The threshold is measured, not assumed: the 138 scans repair a median of 19 words and the 117 typeset decisions a median of 0, but 27 typeset ones repair 1–8, so a "nonzero" test would tell 27 readers something false. Registered in `build.SOURCE_RENDERERS` |

`coe`, `hudoc`, `icrc`, `untc`, `icc` and `icj` share one masthead entry, **Folkrätt**
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
(`/coe/{number}`, `/icrc/{number}`, `/untc/{unts}`, `/icc/{doc-number}`,
`/dom/echr/{itemid}`).

**wiki vertical (git-backed markdown — begrepp + kommentar)**
| File | What |
|---|---|
| `parse.py` | project the markdown wiki into kommentar / begrepp artifacts; the `## heading → host node anchor` grammar (`heading_fragment`, `fragment_heading`), `host_uri`, and the frontmatter-keyed `kommentar_index`/`begrepp_index` |
| `annotate.py` | `lagen kommentar ai-annotate <basefile>` — the Step-4 AI guidance linker: read an annotation's declared guidance PDFs and propose, per article, the guidance links (`.ann` layer, curated store) |
| `guidance_discover.py` | `lagen kommentar {discover,propose}-guidance` — crawl Commission guidance sitemaps into a per-CELEX index + draft a `guidance:` block to review (no LLM) |
| `render.py` | the begreppssida: the concept definition and what cites it. Registered as this source's page renderer in `build.SOURCE_RENDERERS`; `render(art, site) -> str`, built on the `lib/page` kit, with its own page template in `templates/` |

**remisser vertical (regeringen.se referral responses)**
| File | What |
|---|---|
| `model.py` | `Remiss` — keyed on the *referred document's own identity*, `basefile = "<typ>/<identifier>"` (`sou/2026:14`, `pm/LI2026/01339`, `lr/2026/<title-slug>`), not the regeringen.se URL slug the ärende page lives at (kept in `url`); title, dnr, deadline, `remitterat` cross-ref(s) to the referred förarbete, `externt_dokument` flagging an ärende whose remitted document regeringen never published itself, `svar` list of `Remissinstans`. `Remissvar` (one organisation's parsed answer, basefile `"<typ>/<document id>/<org-slug>"`); `org_slug` derives the shared PDF-basename identity `download`/`parse`/`build` all key on |
| `download.py` | regeringen.se `/remisser/` sync, keyed on the remitted document rather than the ärende's own URL slug: `parse_arende` raises when an ärende remits a regeringen-published document but no basefile can be derived from it (an unrecognised doctype) — no stub identity is minted. `_externt_dokument` reads an ärende with no island at all as external unless its *title* names a series identifier — verified across the first 460 ärenden, every island-less page was an agency rapport/framställan/hemställan, an EU proposal or a letter of questions; `MARKUP_FIXES` corrects individual pages whose markup defeats the parser (one heading misspelled "Gevägar") rather than loosening the rule for all 3000+. `_match_forarbete` resolves the referred document's basefile off the "Dokument som remitteras"/"Genvägar" island, carrying the landing `slug` alongside a `pm`'s dnr so the join has both candidates, via `lib.regeringen.TYPES` plus its `pm_identity`/`lr_identity` (a departementspromemoria on its diarienummer, minus any sub-ärende `–N` suffix; a lagrådsremiss on `<year>/<title-slug>`) — the same rules `forarbete/download.py` mints from, so the two verticals land on one basefile for one document. The examined-ärende index (`layout.REMISSER_SEEN`, `{"dirty", "arenden": {url-slug: {"basefile", "until"}}}`) is the sweep's bookkeeping in place of "is there a record on disk", since the listing names an ärende by URL slug and only the ärende page says which document it remits; `until` is the deadline + `GRACE_PERIOD`, because answers accumulate for the whole remissperiod so "already examined" is not a reason to skip an ärende, only its closing date is (`null` basefile = an externally authored ärende, examined once and never fetched again). `sync` shares one `_poll` step (fetch → classify → merge onto any stored record → fetch pending answer PDFs → update the index entry) across two passes: the listing walk (newest-first over ärenden the index says still need it, stopping after `STOP_AFTER` consecutive no-ops; a failure leaves the index dirty so the next run walks the whole archive) and a catch-up pass over index entries the walk stopped short of, re-polled from the url their record carries. `sync_one`/`--only <url>` fetches one known ärende directly through the same `_poll` logic. Returns `{"new","failed","externt","repolled","open","fetched"}` |
| `parse.py` | one answer → `Remissvar`; `_body_text` dispatches on the file's magic bytes (`lib.util.sniff_extension`) rather than trusting its stored `.pdf` name — PDF via the shared `lib/pdftext` (`pdf_pages` + `page_paragraphs`, `repair_pdf` when poppler refuses an unreadable cross-reference table), Word (4 answers stored under a `.pdf` name, 2019–2020) via `lib.poi`, anything else raises. `page_paragraphs` passes `identifier=None` since each organisation's PDF carries its own letterhead, not a fixed running header; the running header/footer that leaves is instead found by shape (`pdftext.strip_page_furniture`), footnotes dropped by font size (`pdftext.drop_footnotes`), a page-break-split sentence rejoined (`pdftext.join_across_pages`) and the masthead/reference-line/contact block dropped by composition (`pdftext.strip_addressing`) — flattened to plain paragraph text |
| `ai_analyze.py` | `lagen remisser ai-analyze <typ>/<document id>/<org-slug>` — the sole LLM pass: maps one answer onto the referred SOU/Ds's sections with a per-section sentiment + verbatim quote plus an overall stance, validated strictly and written as a `.ann` layer in the curated store. A basefile naming a whole ärende (`<typ>/<document id>`, no org-slug) is expanded by `answers()` to every answer actually fetched for it, skipping ones that already carry a layer unless `--force` — and, only when expanding a whole ärende this way, ones under `MIN_ANSWER_CHARS` (900: the pass costs the same per answer regardless of length, and a segment is what the rail needs, which almost never comes from a short answer — 0% of layers under 300 chars produced one, 60% at 900-1200, measured against 1,554 produced layers); a directly named answer always runs whatever its length. A per-answer failure is reported and skipped rather than abandoning the rest of the ärende (`build.remisser_ai_analyze`). Each scored object carries a `quote_type` (`grund`/`standpunkt`) so "the answer states no reason" is a legitimate result rather than an invented ground; a quote the model reworded is snapped back to the answer's own wording (`snap_to_source`, `difflib` similarity against `answer_units` — sentences split further at clause boundaries, `lib.text.sentences(clause_breaks=True)`) instead of only being rejected. Retries once via `lib.llm.author`'s validate/self-repair loop on a malformed reply; joins to forarbete through `layout.resolve_basefile`. `--update` re-runs every ärende ai-analyze has already covered whose remissperiod (deadline + `download.GRACE_PERIOD`, the window the download side re-polls by) has not closed — answers accumulate for the whole period, so an early analysis misses what came later; already-analysed answers are skipped, so it costs the LLM only for the new ones, and it is deliberately never part of a rebuild. `mark_analysed` writes a `.ann.watch` marker per ärende so one analysed before its first answer arrived (or whose answers all failed) is still tracked, since it leaves no `.ann` of its own. Scored against a hand-built answer key by `tools/remisser-eval/` (same shape as `tools/aigenomforande-bench`) |

This source is never `relate`d/`generate`d — it publishes no pages of its own;
`page._remiss_indexes` reads its `.ann` layers straight out of the curated
store (`lib/annstore.py`, `WIKI_ROOT/ann/remisser/…`) and surfaces them as a
"Remissvar" section on the *referred förarbete's* context rail.
`remisser/KNOWN-GAPS.md` tracks the corpus's outstanding non-self-healing
gaps (8 remisser whose remitted document has no `lib.regeringen` identity
rule, 6 with colliding organisation slugs) against the pipeline, not the
data — a self-healing failure (a 0-byte answer, a timeout) is not listed.

**site vertical (editorial chrome — frontpage / om / sitenews)**
| File | What |
|---|---|
| `model.py` | small block-tree dataclasses (`Heading`/`Paragraph`/`Bullets`/`Table`/`Code`/`Rule`; on-disk `type` discriminator `rubrik`/`stycke`/`lista`/`tabell`/`kod`/`avdelare`) plus the three page shapes `Frontpage`, `AboutPage`, `Sitenews` (a list of `NewsItem`) — no citation graph, so no `Forfattning`/`Avgorande`-style domain model; `Bullets.ordered` picks `<ol>` vs `<ul>`, a run gained `italic` |
| `parse.py` | markdown (`lagen-wiki/site/`) → JSON artifact for three fixed basefiles: `frontpage` (curated law list: `## <Category>` + `- [Label](sfs:…)` bullets), `om/<slug>` (about pages), `sitenews` (split into dated `NewsItem`s on `## YYYY-MM-DD HH:MM:SS Title` heads); block/inline layers parsed by **markdown-it-py** (CommonMark + the GFM `table` rule, `html: False`), `blocks(body, where)` mapping its token tree onto the typed blocks and raising `ValueError` naming the basefile for a construct with no block form; link *targets* still resolve through the shared `lib.markdown.target_uri`, extended locally with site-relative `/…`/`#…` and `mailto:` |
| `render.py` | artifacts → static HTML + Atom, one entry point `write_site(out_root)` called by the build driver during `generate`; the curated frontpage overwrites the generic corpus-stats `index.html` (`has_frontpage()` gates that). Page markup lives in the vertical's own `templates/site.html` (built via `lib.tpl.environment`, chrome still from `lib.page.page`); the inline-run/block renderers stay Python (algorithmic emission), as does the Atom XML. The sitenews listing renders on the shared feed screen (`lib.feeds.nav` in the left rail, `listings.feed_body` around the items), so the news feed reads as one feed among the sixteen rather than as an editorial page |

Like `remisser`, `site` is parsed (and, unlike remisser, rendered during
`generate`) but is **absent from `ARTIFACTS`**, so it is never
`relate`d/indexed/dumped — it carries no citation graph. Site artifacts are
folded into `generate_fingerprint()` so an editorial edit reopens the generate
gate. Served at `/` (frontpage), `/om/<slug>` + `/om/` hub, and
`/dataset/sitenews/feed` (+ `/dataset/sitenews/feed.atom`); masthead entries
"Om"/"Nyheter" in `lib/tpl.py`'s `MAST_NAV`.

**stats vertical (corpus-wide measurements — `/statistik`)**
| File | What |
|---|---|
| `model.py` | `Measure` (the on-disk `kind` discriminator the renderer dispatches on: `scalar`/`toplist`/`series`/`histogram`/`bars`/`profile`/`matrix`/`sankey`/`table`; a `profile` draws every value at its rank, largest first, so each bar is a real thing's own size rather than a bucket count; `_rank_profile(log=True)` samples those ranks geometrically, because a corpus whose largest member is a thousand times its median draws as one tall bar beside a flat line on an evenly sampled axis) + `Row`/`Point`/`Cell`/`Tile` (a `Tile` is one headline number in a scalar's KPI row — the form a scalar takes when it answers with several numbers at once, since three display-size figures run together in one hero line wrap mid-figure), and `Report.to_artifact()` which prunes a measure's empty fields so two builds' artifacts diff readably. A population caveat has two homes, neither a footnote: a computed `lede` states the exclusions with their counts (measure 4 names all five), and the page template's own `note=` renders under the figure for the caveat that is prose rather than arithmetic (the EU measures 4 and 6 carry one) |
| `scan.py` | the expensive half: walks the sfs/eurlex/forarbete/dv artifact trees (and `downloaded/sfs/` for change-act titles, which the artifact does not carry) reducing each document to a compact fact row, mapped over a `ProcessPoolExecutor`. Pure and process-safe. Owns the two measurement rules that silently poison whole families of numbers when wrong: **table cells count as text** (a `rad`'s `cells` are a list of *run lists*, two levels deep) and **provenance markers/renumbering stubs do not** |
| `compute.py` | the 54 measurements as seven groups (A–G), preferring catalog SQL over `scan` wherever the data is in the catalog; `compute(catalog_path)` → `Report`. `_in_force` narrows `scan`'s `laws` to gällande rätt once for every measure, keeping the unnarrowed list as `laws_all` for the few (churn, lifespan, repeal counts) that need the whole history by name. The measures whose population rule is load-bearing are pure helpers rather than inline code — `text_age` (mean year of the paragrafer actually in force, returning `None` where the register is too silent to answer), `notice_days` (utfärdande → ikraftträdande, base statutes only) and `bill_lag` (proposition → ikraftträdande) |
| `charts.py` | one `Measure` → its figure. Form follows `kind`: bar *tables* for ranked things (Swedish statute titles are 90 characters and SVG text cannot wrap), SVG lines/columns for series and distributions, a log-scaled heat table for the matrix, and an SVG flow diagram for the sankey (measure 29: which source cites which, ribbon thickness the reference count, the same groups standing on both sides so a source citing itself is a ribbon too). Single-series throughout, so no categorical palette and no legend; plotted forms also emit the table view — except the `profile`, whose table would be 100 lines of "plats 1 743 → 214 tecken" naming nothing a reader can look up, where its own row list already names the record holders at each end |
| `render.py` | the artifact → `/statistik`; raises if the artifact is absent. The page template (`templates/stats.html`) is 1:1 with what renders — every measure an explicit `stat()` call in display order with its title/prose, comment-out-able while tinkering — while every number (and every lede that embeds one, `computed_lede`) comes from the artifact; `templates/figures.html` holds the chart-figure macros `charts.py` drives |

Two verbs, deliberately split: `lagen stats compute` measures the corpus into
`artifact/stats/statistik.json` (minutes — it must run after `relate`, since it
reads the catalog), and `lagen stats generate` renders that artifact to the page.
The split is what makes the numbers diffable between builds, and keeps the
artifact the source of truth. `compute` is deliberately **not incremental**:
every measurement is a fact about the whole corpus, so there is no subset of it
that could be refreshed alone; its `Stage` is marked `always=True` (`build.py`)
so it is never judged fresh on its recipe hash and re-measures on every
invocation, `--force` or not. Each run also archives the artifact under its own
date (`layout.stats_snapshot`, `artifact/stats/archive/statistik-<date>.json`),
since the live artifact alone cannot answer "how has the corpus changed". Like
`site`, `stats` is absent from `ARTIFACTS` and is never `relate`d/indexed/
dumped. `compute` is now part of the standard whole-corpus `lagen all rebuild`
(after `dump`, before `generate` — it needs both the catalog `relate` just
rebuilt and the artifact trees `parse` just wrote); a single-source rebuild
does not pay for it. The page rides that run too: `/statistik` is one
artifact-backed page with no catalog rows, so `render.generate_site` never
reaches it and a full-corpus `generate` writes it explicitly, next to the
editorial pages — without which a rebuild recomputes the measurements and then
republishes the previous run's page. The measurement catalog, with each number's provenance,
is [`../docs/prd-stats.md`](../docs/prd-stats.md).

The catalog-backed document feeds use stable public paths:
`/dataset/{sfs,dv,forarbeten,myndfs,myndprax,keyword,eurlex}/feed.atom` and their
human-readable `/feed` twins, with the established `rdf_type`,
`rpubl_rattsfallspublikation`, and `dcterms_publisher` query parameters.
`/dataset/sitenews` is the all-feeds directory. `lib/feeds.py` maps public
repository aliases to internal source names and renders stable Atom entry ids.

**Service layer**: `api/db.py` owns the catalog handle every API module shares (the path constant, the read-only FastAPI dependency `get_con`, `base_sha`), and `api/reads.py` is the one read path both faces answer through — search, document list, one document, inbound/outbound citations, sources — so REST and MCP cannot drift apart (a down search cluster is a visible error on both: REST 503, MCP tool error). `api/app.py` is the REST/OpenAPI service (search, documents,
citation graph, `/api/v1/card` -- one document's identity card: names, address, citedness and the relate-stamped `snippet`, what popovers and the paraGRAF details panel show for the one selected item -- version history + diff, `GET /api/v1/pdf` — a generated page
re-rendered for paper by `api/pdf.py`, WeasyPrint over the same `style.css`
print rules the browser uses, its subresources — stylesheet, fonts, facsimile
images — all resolved in-process, never over the network; a large export runs
as a background job (`api/pdfjob.py`) that the reader follows on a waiting
page with a progress bar, rather than on a request nginx times out at 60 s;
`api/pdfcollection.py` and `/samling` assemble up to 1,000 browser-owned
documents into one exact WeasyPrint layout, with localStorage drafts,
bookmark-fragment recipes, per-document section/start choices, an optional
cover and a document-only printed TOC). `api/paths.py` answers
`GET /api/v1/path` — the shortest citation chain between two documents —
off the whole document-level graph (~2.6M distinct citing→cited pairs, 271k
documents) held in memory as CSR integer arrays (`lib/pathgraph.py`): relate
writes the arrays as a sidecar beside the catalog (`graph-edges.bin`, ~31 MB,
loaded in ~0.04 s), any (re)load runs in a background thread keyed on the
catalog file's identity, and the endpoint answers 503 until the graph is
ready — a request never waits on a build. The sidecar-less fallback is ONE
sequential scan of `links` filtered in Python, never per-document index
probes: the probe-shaped query measured 2 s on dev NVMe and *hours* on
prod's ~80-IOPS disk (2026-08-26), where it held the module lock, 504:ed
every /path request at nginx's 60 s and pinned the disk at 100 %
that also serves the static site under `lagen serve`. Its API surface is
`/api/v1` and nothing else — search, facets, browse, documents, one document (+versions/diff/
inbound/outbound), sources, graph, facsimile, `sfs-graphic`, `dv-verdict`,
`pdf`, `dumps` — and that is all `/docs`/`/openapi.json` show. Everything the
site calls on itself — login, the three editors, the PDF export's background
jobs — is a second `FastAPI` app, `api/internal.py`, mounted at
`/internal-api` with its own `/internal-api/v1` routes, its own schema and
Swagger UI (`/internal-api/openapi.json`/`/internal-api/docs`, both behind
`auth.require_editor`) and an app-wide `auth.same_origin` dependency, so a
reader of the public schema never sees a write route. `api/mcp.py` mounts a public, no-auth **MCP server** (Model
Context Protocol) at `/mcp` on the same app — the same read-only view reshaped as
tools (search, resolve_citation, get_document, the citation graph, …) so any
MCP-capable AI host can ground answers about Swedish/EU law in the live corpus and
cite the exact §/article; the tools answer through `api/reads.py` like the REST
endpoints (see `api/README.md`). A `_LoggedMCP` ASGI
wrapper logs one line per JSON-RPC request (client IP, method, tool name +
truncated arguments) — the only tool-level visibility, since the uvicorn/nginx
access log sees only `POST /mcp/ 200`; the MCP SDK's DNS-rebinding protection is
explicitly disabled (`enable_dns_rebinding_protection=False`), since its
localhost-only default would 421 all production traffic arriving through the
nginx vhost. `serve()` calls `logging.basicConfig(INFO)` so these and other
app-level log lines reach stdout alongside uvicorn's own access log. `api/ops.py` mounts the
ops health dashboard on the same app (see "Operations" below); `lib/runlog.py`
owns the state files behind it. `api/errors.py` installs the site's 404/500
exception handlers on both apps (`errors.install(app)`, once for the public
app and once for `api/internal.py`'s mounted app — a mounted sub-app carries
its own exception middleware) — a rendered
`error.html` page for site paths, JSON with an added `error_id` for
`/api/`/`/internal-api/`/`/docs`/`/redoc`/`/openapi.json`/`/mcp`, and one `lib/errorlog.py`
ledger entry behind both (see "Operations" below). `api/auth.py` + `api/edit.py` +
`api/editcontent.py` + `api/editcart.py` are the inline content editor — the one
authenticated, mutating surface, served at `/internal-api/v1/{auth,edit}/*`
behind `auth.same_origin` (see "Inline editing" below); `api/graphicsedit.py`
+ `api/graphics.py` are its sibling for signing off the `.graphics` layer's
vision-placed crops (see "Reviewing `.graphics` crops" below) — `editcart.py` is
generalized over draft *kinds* (markdown regions, graphics entries) rather than
markdown alone, so both editors share one cart, conflict check and attributed
commit; `api/patch.py` is a further sibling for authoring source-fix **patch
files** (`lib/patch.py`,
`lib/patchit.py`, `patchsource.py`; see "Patch files" below).
`api/facsimiles.py` owns everything between a request and one
`lib/facsimile.py` render: the six per-source resolvers that turn a uri-local
path into a downloaded PDF, `facsimile_path`/`sfs_graphic_path` and their
response wrappers, `parse_bbox`, `png_path` (which owns the poppler exit-code
split: a missing page is a 404, a corrupt PDF stays a loud 500) and the
immutable cache headers. It is its own module rather than `app.py`'s privates
because `app` imports every router, so a router importing `app` back would be a
cycle; `app.py`, `api/graphics.py`, `api/pdfjob.py` and the legacy facsimile
paths all read it. Its `subresource` is what the **PDF export** fetches a
page's images with. That used to be an in-process `TestClient(app)` answering
any route the app had, which cost an ASGI round trip per image (2.34 ms against
0.11 ms; 0.8 s of it on 2007:90's 325 road signs) and stranded the export's own
routes in `app.py`, the one module that can name `app`. Measured over three
real exports, WeasyPrint asks for exactly two path families —
`/api/v1/facsimile` and `/api/v1/sfs-graphic` — and both end at a `Path`, so
the client is a dispatcher now and anything outside those two is refused
rather than guessed at. `api/analytics.py`
is **server-side Matomo tracking** for the two surfaces that have no browser to
run the pages' tracker snippet in: an ASGI middleware counts the REST API
(`/api/v1`, `/docs` — the editor's own routes live under the separate
`/internal-api` app and never match, and it also drops *successful*
same-origin XHR from our own pages, which the browser tracker already counts)
and `_LoggedMCP` reports each JSON-RPC call under a synthetic
`/mcp/<method>/<tool>` URL, so the Pages report breaks down by tool. **Failures
are counted too**, whoever made the call, under an `error` branch of the page
title (`API/error/search`, `MCP/error/tools/call/get_document`) while the URL
stays the same — so Pages counts demand per endpoint/tool and Titles splits
working from broken. On the MCP side that means reading the outcome out of the
JSON-RPC envelope after the response (a tool failure is answered with HTTP 200),
which is what `mcp._failed` and the wrapper's bounded response capture are for. It posts to the self-hosted Matomo over the
compose network (`MATOMO_URL`) under its **own site id** (`MATOMO_SITE_API`),
separate from the reader-facing sites, so
agent traffic never enters the reader numbers; hits are queued to one daemon
thread (never on the response path, dropped when the queue is full) and carry no
visitor address: Matomo sees the container as the client and groups visits by a
per-process-salted hash of address + user-agent + date. Unset either variable
(a dev serve) and no analytics middleware is installed at all. The reader-facing
half is `lib/assets/matomo.js` in the `script.js` bundle. `lib/pins.py` is the
citation-shaped-query resolver (a name+pinpoint → one exact fragment target)
shared by the REST `/search` and the MCP `search`/`resolve_citation` tools.

**Top-level**: `config.py` resolves the optional `config.yml` — the corpus
roots (`data_root`, `catalog_root`, `wiki_root` — `catalog_root`
decouples `catalog.sqlite` from `data_root` so the latency-sensitive SQLite
catalog can sit on fast local disk while the bulk artifact corpus is on NFS;
defaults to `data_root`, colocated; `wiki_root` names the one checkout the
running site writes into: commentaries, concepts, the site chrome, the
annotation layers and the source patches — see "Patch files"
below), the services the pipeline
talks to (`opensearch_url`, `llm_base_url`/`llm_model`/`llm_temperature`/
`llm_top_p`/`vision_model`) and the deployment's own settings (`editor_secret`,
`editors`, `compress`/`compress_quality`, `cookie_secure`,
`matomo_url`/`matomo_site_api` — the server-side analytics target, off unless
both are set) —
read with ruamel.yaml round-trip mode so a bad value's line number is reported.
What it deliberately does *not* locate is curated source data shipped in the
tree (`lib/datasets.py`'s `NAMEDLAWS`/`NAMEDACTS`/`NAMEDCASES`/`NAMEDEUCASES`/`COE_NAMES`/`ICRC_NAMES`/`UNTC_TREATIES`/`ICC_DECISION_TYPES`/`TREATY_NAMES`/`FS_SERIES`/`EMD_CASES`/`EMD_RESPONDENTS`/`JO_ARSBERATTELSE`,
`sfs/data/resources.json`, …) are anchored by their own callers, not here.

## Running the pipelines

**SFS** (operates on `site/data/{downloaded,artifact}/sfs/`, validated against
the golden corpus in the sibling reference checkout, `../ferenda.old/data/sfs/parsed/`):

```sh
uv run python -m ferenda.build sfs download                              # incremental; --force for a full backfill
uv run python -m ferenda.build sfs download --resume-after '[...]'       # resume a backfill interrupted mid-sweep,
                                                                                # from the ES search_after cursor it printed
uv run python -m ferenda.sfs parse site/data/downloaded/sfs/2018/585.json --basefile 2018:585
# golden = the reference projection's parsed XHTML (scaffolding in the sibling reference checkout), normalized to NF on the fly
uv run python -m ferenda.sfs validate ../ferenda.old/data/sfs/parsed site/data/downloaded/sfs --sections structure,references
uv run python -m ferenda.sfs refs FILE PARSED.xhtml  # citation diff for one doc
```

The SFST consolidation is text-only. During the normal SFS parse, omission
markers and the road-sign tables in 2007:90 are projected as typed `grafik`
nodes; the source model retains the original marker text. Mirror the official
published PDFs (the crop source), then vision-localize the gaps onto them.
Mirroring runs as part of `sfs download` and costs only bandwidth; the
vision pass is opt-in and elective (it costs tokens) and is never part of a
production build:

```sh
uv run python -m ferenda.build sfs mirror-pdf                     # every base act + registered amendment (also run by `sfs download`)
uv run python -m ferenda.build sfs mirror-pdf 2007:90             # named SFS act(s) only
uv run python -m ferenda.build sfs mirror-pdf --full              # re-fetch existing + re-ask about acts once denied
uv run python -m ferenda.build sfs ai-includegraphics 2007:90     # vision-localize that act's gaps
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
semantic identity still match. A road-sign statute (2007:90) skips the vision
model entirely: its 326 signs and their provenance are read off the published
PDFs' own text layer and ink, and the layer it writes is `status: "derived"` —
mechanical, so it renders without per-entry sign-off. The artifact's local `G1` id is not persisted as
identity: the layer is keyed by a `g-…` hash of structural path, kind/code,
normalized anchor and container-local occurrence, and stores the unhashed
`identity` object in each entry for review. Content copies of the same semantic
appendix share a key/crop; a *pending* temporal variant (a container the source
prints beside its in-force sibling with `/Träder i kraft I:.../`) instead gets
its own keys and its own provenance-correct source PDF. Generated candidates
are not publicly rendered until
their entry (or whole layer) is verified — by hand, or by a logged-in editor at
`GET /internal-api/v1/graphics/review` (see "Reviewing `.graphics` crops" below).
`GET /api/v1/sfs-graphic?uri=&node=` serves the
crop (`lib/facsimile.py`'s `cached` with a `bbox`) lazily from the
provenance-correct PDF; the renderer shows the crop where the layer has placed
one — captioned "Karta ur SFS X", linked to the amendment's `#L{nr}` register
entry on the same page — an honest placeholder otherwise, and prints each
temporal variant's entry-into-force state as a subdued slash-delimited
marker (`/Träder i kraft: den dag som regeringen bestämmer/`).

**SFS version history** (historical consolidations / time travel / diff): the
downloader archives every superseded consolidation under
`site/data/downloaded/sfs/archive/{y}/{n}/.versions/`. Retained HTML
consolidations use the same tree. The `versions` stage parses
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
uv run python -m ferenda.build sfs versions            # incremental, all statutes
uv run python -m ferenda.build sfs versions 1998:204   # one statute
uv run python -m ferenda.build sfs parse               # required before a full Git export
uv run python -m ferenda.build sfs history-as-git /path/to/repo             # complete corpus; strict append-only updates
uv run python -m ferenda.build sfs history-as-git /path/to/repo --rebuild-history  # recreate corrected/backfilled history
uv run python -m ferenda.build sfs history-as-git /path/to/repo 1998:204   # separately scoped partial repo
```

**DV** (operates on `site/data/downloaded/dom/` (API) and `site/data/downloaded/dv/` (legacy)):

```sh
# download + build the identity index
uv run python -m ferenda.dv.download site/data/downloaded/dom   # [--full] [--no-bilagor] [--limit N]
uv run python -m ferenda.build dv reindex                  # -> site/data/artifact/dom/identity-index.json
                                                                  # (also auto-run after any harvest that changed records)

# parse (driver-owned; `[ids…]` parses just those, empty = all stale; a case
# without an API record routes through the legacy parser automatically)
uv run python -m ferenda.build dv parse                                       # incremental, both paths
uv run python -m ferenda.dv.legacy --index site/data/artifact/dom/identity-index.json   # legacy path, batch report
uv run python -m ferenda.dv.legacy site/data/downloaded/dv/ADO/1993-100_1.doc # one Word file -> artifact

# rewrite dv/data/casenumbers.json from the parsed artifacts. A full-source
# `dv parse` already ends with this; run it by hand after a targeted parse, or
# to see what the snapshot holds
uv run python -m ferenda.build dv casenumbers
```

The DV parsers use the identity index. Each canonical case uses its best
source: the API record when present, or the Word original otherwise. The
parsers do not merge sources.
The incremental download only covers late publication within its 365-day
safety window below the watermark; a record edit or a referat published
later than that surfaces only under `--full`, so a periodic cron'd `--full`
sweep remains the backstop.

**avg — JO + JK + ARN + IMY + KKV decisions** (operates on `site/data/{downloaded,artifact}/avg/`):

```sh
uv run python -m ferenda.build avg download        # all five organs; or: … download jo
uv run python -m ferenda.build avg parse           # incremental, like every source
uv run python -m ferenda.build avg download jo --only jo/2340-2025   # one decision
uv run python -m ferenda.build avg download imy    # tillsyner + the two curated pages
uv run python -m ferenda.build avg download kkv    # tillsynsbeslut + ärendelista
uv run python -m ferenda.build avg arsberattelse    # rewrite avg/data/arsberattelse.json from the stored JO artifacts
```

`--only` for `imy` names a diarienummer (`--only imy/IMY-2024-2904`) and needs
the decision already harvested: a decision has no page of its own, so the
tillsyn page to refetch is looked up in its stored record. `--only` for `kkv`
also names a diarienummer, which itself contains a slash
(`--only kkv/558/2026`); it re-walks the ärendelista but fetches only the one
curated account it needs.

**rs — rättsliga ställningstaganden from seven myndigheter** (operates on
`site/data/{downloaded,artifact}/rs/`):

```sh
uv run python -m ferenda.build rs download          # the six HTTP agencies; or: … download fk
uv run python -m ferenda.build rs parse             # incremental, like every source
uv run python -m ferenda.build rs download fk --only fk/2025:01   # one statement
uv run python -m ferenda.build rs download migr     # Lifos (RS + RK), AIA-completed TLS
uv run python -m ferenda.build rs browser-download  # Skatteverket, weekly, headful Chrome
```

Identity is the agency's own number (`rs/fk/2025:01`, `rs/kfm/1-23-VER`,
`rs/migr/RS-028-2021`), so `--only` names that and needs its agency scope. A
first `rs download fk` fetches all 108 PDFs, because Försäkringskassans
Serienummer lives only in the document; later runs read the number off the
stored records and cost one listing request.

Skatteverket is the seventh agency and runs on its own command. It sits behind
the F5/Shape challenge SKVFS sits behind, so every navigation goes through
headful Chrome one at a time, and the run is paced well under the rate the
front tolerates. A first `rs browser-download` is 2,614 paced navigations —
some fifteen hours, sliceable with `--limit N`, and a resumed run skips
whatever is already stored — while a weekly run costs the register plus the
handful of documents that moved. Its documents are stored as `.html`, not
`.pdf`: Skatteverket publishes the ställningstagande *as* a web page.

**guidance — EU-organens vägledningar, 12 utgivare** (operates on
`site/data/{downloaded,artifact}/guidance/`):

```sh
uv run python -m ferenda.build guidance download          # every body
uv run python -m ferenda.build guidance download acer     # one body
uv run python -m ferenda.build guidance download ecb esrb # the two CELLAR bodies
uv run python -m ferenda.build guidance download edpb/riktlinjer --only edpb/riktlinjer/05-2020
uv run python -m ferenda.build guidance download edpb/wp --force   # re-resolve the WP29 ZIPs
uv run python -m ferenda.build guidance parse             # incremental, like every source
```

A download scope is one **upstream walk**, not one series: a bare utgivare
where one walk covers all of that body's series (ten of the twelve), and
`<utgivare>/<serie>` where the series come off different upstreams — the EDPB's
two open series come off its sitemap and its closed WP29 series off the
Commission newsroom.

Identity is the issuing body's own number, never a CELEX
(`edpb/riktlinjer/05-2020`, `eba/gl/2021-05`, `ecb/con/2013-82`, and
`esrb/2014-01` where the body numbers in one sequence and the address carries no
series segment), so `--only` names that and needs its scope. The Swedish version
is published wherever the body has issued one and the English one otherwise; the
record says which. The `edpb/wp` scope is a closed corpus of sixteen documents
whose text lives on the Commission newsroom, each costing a 10–28 MB language
ZIP to resolve — a routine run skips whatever is already on disk.

The ECB and the ESRB publish in EUT rather than on their own sites, so `ecb` and
`esrb` harvest out of CELLAR through `lib/cellar.py` with the same language and
format preferences the eurlex source uses, and their documents parse from
whichever manifestation CELLAR served — Formex through `lib/formex.py`, or the
PDF, or EUR-Lex HTML.

Citations to these documents are linked by the `VAGLEDNING` parse type, by the
EDPB's form (`Riktlinjer 05/2020`, `WP 248`) and by the five bodies whose number
carries their own acronym (`ESRB/2017/6`, `EBA/GL/2021/05`, `ESMA/2013/720`,
`CON/2013/82`, `BoR (11) 67`). `guidance/KNOWN-GAPS.md` records what the grammar
deliberately does *not* catch, and why EIOPA and ACER are left out of it.

**lawreview — tidskriftsartiklar, nio tidskrifter** (operates on
`site/data/{downloaded,artifact}/lawreview/`):

```sh
uv run python -m ferenda.build lawreview download           # all nine journals, fanned out one host each
uv run python -m ferenda.build lawreview download svjt      # one journal
uv run python -m ferenda.build lawreview download jp --only jp/2026-01-02
uv run python -m ferenda.build lawreview parse              # incremental, like every source
```

A failing journal is reported and the run carries on with the rest (re-run
the failed scope on its own). Every journal but nmt keeps a harvest
watermark, so a caught-up run reads only the newest year page (svjt) or the
first listing page(s) and the newest issue's page instead of re-walking the
archive; nmt's two listing pages are the whole archive, so it enumerates
them every run.

The articles are not republished on the site: they are mined for the
references they make, which is what puts an article on the context rails of
the statute, förarbete or rättsfall it names. The article's rail line links
to the journal's own page for it, and the articles have no pages, feeds or
search index entries of their own. SvJT's document is the article's
own web page (a page exists for every article, 1916 and all); JP's is the
issue's PDF (its issue page carries the title, author and abstract). JP's
host rate-limits with HTTP 466, which the fetch waits out on its own.
Lov & Data's document is the article's own web page, but only its 2022 and
later volumes exist as pages — the earlier volumes are full-issue PDFs, and
the walk takes only the issues whose pages list articles. Its articles
carry no page numbers, so the identifier stops at the issue
("Lov & Data 3/2022") and the basefile's sequence number keeps the issue's
articles apart.

**lawpub — the platform scope** (operates on
`site/data/{downloaded,artifact}/lawreview/lawpub/`):

```sh
uv run python -m ferenda.build lawreview download lawpub  # the whole listing, newest first
uv run python -m ferenda.build lawreview download lawpub --only lawpub/880
uv run python -m ferenda.build lawreview parse            # incremental, the whole source
```

The platform is a single listing across seven publishers, so there is no
per-publisher scope. The walk stops on a harvest watermark, so a caught-up
run reads only the newest listing pages. Only open-access items are stored;
a locked ("Stängd") item has no PDF the platform will serve. The scope's
articles are lawreview documents: mined, unpublished, their rail lines on
the shared "Artiklar" row, each linking the platform's own page for the
article. Two of the platform's seven publishers — Förvaltningsrättslig
tidskrift (FT) and Stockholm IP Law Review (SIPLR) — are also harvested on
their own hosts by the `ft` and `siplr` scopes, so one article can arrive
twice, catalogued under two basefiles (`lawreview/ft/...` and
`lawreview/lawpub/...`); the shared row then shows both lines, and
de-duplication is an open decision.

**HUDOC + Council of Europe treaties + ICRC IHL treaties + UN Treaty Collection + ICC case law**:

```sh
uv run python -m ferenda.build coe download                 # all Treaty Office instruments
uv run python -m ferenda.build coe parse                    # official PDF text -> article artifacts
uv run python -m ferenda.build hudoc download               # judgments + decisions, then the Court's own
                                                                 # summaries and the Swedish translations
uv run python -m ferenda.build hudoc download decisions     # one collection (each has its own watermark)
uv run python -m ferenda.build hudoc download --limit 1000  # bounded: the two smaller harvests are skipped
uv run python -m ferenda.build hudoc parse
uv run python -m ferenda.build hudoc casenames               # rewrite hudoc/data/casenames.json from the stored records
uv run python -m ferenda.build icrc download                # all ICRC IHL treaties
uv run python -m ferenda.build icrc parse                   # JSON:API envelope -> article artifacts
uv run python -m ferenda.build untc download                # the 14 curated treaties: status page + authentic text
uv run python -m ferenda.build untc parse                   # both halves -> metadata, participation and articles
uv run python -m ferenda.build icc download                 # the curated ICC substantive decisions
uv run python -m ferenda.build icc parse                    # Legal Tools metadata + PDF -> article artifacts
uv run python -m ferenda.build icj download                 # judgments, advisory opinions, provisional-measures orders
uv run python -m ferenda.build icj parse                    # I.C.J. Reports PDF -> numbered-paragraph artifacts
uv run python -m ferenda.build all relate                   # joins HUDOC cases to CoE articles
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
step and `icrc parse` never touches the network. `untc download` fetches each treaty **twice**, because no one publisher carries
both halves: the status from `treaties.un.org`'s `ViewDetailsIII.aspx` (dates,
UNTS registration, per-state participation — and no treaty text at all), and
the authentic text from the treaty's own depositary. Deliberately not from the
UNTS itself, which reproduces each instrument as registered and so is a scanned
corpus: volume 999 carries the ICCPR over 92 pages with an image on all 92, and
volume 1161 the Berne Convention over 44 of 44. OHCHR sits behind the same
Cloudflare challenge as the ICJ and un.org refuses the harvester's user agent on
the UNCLOS PDF, so every text comes through `lib.browser.DetachedChrome` — one
session for the run, about 9 s per treaty. `untc parse` reads both offline.
`icc download` also avoids the Cloudflare-fronted `/court-record` pages: it
facet-scrapes icc-cpi.int `/decisions` for the curated Rome-Statute decision
types to get each record's document number, then resolves that number
against the ICC Legal Tools API (`legal-tools.org/api/ltdDocs`) for metadata
and the decision PDF, so `icc parse` reads the stored Legal Tools record and
PDF text and never touches the network either. `icj download` is the one
folkrätt harvest that needs a browser: the `/decisions` index answers ordinary
HTTP, but every decision PDF under `/sites/default/files/case-related/` returns
a Cloudflare challenge that no header or cookie from the index clears, so the
bodies come through `lib.browser.DetachedChrome` — one headful session for the
whole run, about 9 s per document, ~40 minutes for the 255 in scope. Rerun
`tools/icj_vocabulary.py` after a harvest that adds a year of decisions: it
rebuilds `icj/data/vocabulary.txt`, the word list that guides the OCR repair of
the pre-2002 scans, and the file is a recipe input so a rebuild re-stales every
scanned decision.

**remisser — regeringen.se referral responses** (keyed on the referred
document, not the regeringen.se case-page slug; operates on
`site/data/{downloaded,artifact}/remisser/<typ>/` — an ärende record and its
answer PDFs share one download tree, `site/data/downloaded/remisser/<typ>/<id-slug>.json`
beside `site/data/downloaded/remisser/<typ>/<id-slug>/<org>.pdf`; never
`relate`d/`generate`d — see the module map above):

```sh
uv run python -m ferenda.build remisser download                    # harvest new ärenden + re-poll open ones
uv run python -m ferenda.build remisser download --only <arende-url>  # one ärende, bypassing the listing walk
uv run python -m ferenda.build remisser parse                       # incremental, like every source
uv run python -m ferenda.build remisser ai-analyze <typ>/<document id>/<org-slug>  # the sole LLM pass, one answer
uv run python -m ferenda.build remisser ai-analyze <typ>/<document id>              # whole ärende: every answer still lacking a layer
uv run python -m ferenda.build remisser ai-analyze --update                        # every analysed ärende still open: pick up answers that arrived since
```

**site — lagen.nu's editorial chrome** (frontpage / om / sitenews; parsed +
generated but never `relate`d/indexed/dumped — see the module map above):

```sh
uv run python -m ferenda.build site parse       # markdown -> artifacts, incremental
uv run python -m ferenda.build site generate     # rewrite just the editorial pages (write_site)
```

### Wiki content repo (begrepp + kommentar)

The hand-authored commentary (`kommentar`) and concept glossary (`begrepp`)
are **git-backed markdown** in a separate content repo (`lagen-wiki`),
checked out alongside this one and pointed at by `WIKI_ROOT`:

```sh
git clone <lagen-wiki remote> ../lagen-wiki    # or: git submodule update --init
uv run python -m ferenda.build begrepp parse
uv run python -m ferenda.build kommentar parse
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
`page._kommentar_indexes` (it merges each kommentar `.ann`'s `guidanceLinks`
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
uv run python tools/unfold_wiki_lists.py ../lagen-wiki --apply   # repair folded lists (one-off)
```

`wiki_artifact_diff.py` asserts the migration's safety property: for every
page, `markdown → artifact` is byte-identical to the old `wikitext →
artifact`, modulo two adjudicated, content-free normalisations (see the
script) plus one deliberate exception: a wikitext list line, which the old
parser read as literal prose with its marker left in the text (`# Numrerad
punkt …`), now reads as a `lista`/`punkt` artifact node instead — a content
*fix*, so `wiki_artifact_diff.py` reports it as a mismatch rather than
normalising it away. `unfold_wiki_lists.py` is a separate, one-off repair
over the already-converted `lagen-wiki` markdown: `wikitext.blocks()` (used
by both the old parser and the converter) joins consecutive non-blank source
lines into one paragraph, so a MediaWiki list spanning several source lines
converted to one run-on markdown line with its markers still inside it
(`\# a # b`, `* a * b`, `1) a 2) b`); the tool re-splits each into one item
per line (86 lists across 66 files in `commentary/`+`concept/`, applied
once). `lib/wikitext.py` is retired from the pipeline and kept only as the
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

The markdown files are the source of truth. Edit them directly and commit the
content-repository change.

```sh
uv run python -m ferenda.build site parse    # markdown -> artifacts, incremental
uv run python -m ferenda.build site generate # rewrite the editorial pages
```

### Corpus statistics (`/statistik`)

```sh
uv run python -m ferenda.build stats compute   # measure the corpus (minutes)
uv run python -m ferenda.build stats generate  # render /statistik
```

`compute` reads the catalog and the sfs/eurlex/forarbete/dv artifact trees, so
it must run **after `relate`**. It is not incremental — every measurement is a
fact about the whole corpus, so there is no subset to refresh — and its stage
declares no per-document `inputs` and is marked `always=True`, so there is no
freshness gate: every invocation re-measures, `--force` or not, and archives a
dated copy under `artifact/stats/archive/`. `generate` raises if no artifact
has been computed; a statistics page without measurements would publish an
empty claim. `lagen all rebuild` runs `compute` automatically on a whole-corpus
run, between `dump` and `generate` (not on a single-source rebuild).

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
site/data/downloaded/avg/{jo,jk,arn,imy,kkv}/ # per-decision records (+ jo/arn PDFs, jk landing html)
site/data/downloaded/avg/imy/dok/             # IMY decision PDFs, by asset name (shared between decisions)
site/data/downloaded/avg/kkv/dok/             # KKV decision documents, by diarium file name (pdf/htm/docx)
site/data/downloaded/rs/{fk,migr,kfm,imy,fi,kkv}/  # per-ställningstagande records + their PDFs
site/data/downloaded/rs/skv/                  # per-ställningstagande records + the pages that ARE the documents
site/data/downloaded/hudoc/                   # HUDOC metadata JSON + converted full-text HTML
site/data/downloaded/coe/                     # Treaty Office records + official English texts
site/data/downloaded/icrc/                    # ICRC JSON:API treaty envelopes (metadata + authentic text, no PDF)
site/data/downloaded/untc/                    # MTDSG status pages (metadata + participation) + the depositary's authentic text (.text.html / .pdf)
site/data/downloaded/icc/                     # ICC Legal Tools records (metadata) + decision PDFs
site/data/downloaded/icj/                     # ICJ index rows (metadata) + I.C.J. Reports decision PDFs
site/data/downloaded/forarbete/<type>/<year>/ # regeringen.se harvest + frozen-import records (prop/sou/ds/pm/dir/fm/skr/so/lr), year-segmented (pm buckets under `_`)
site/data/downloaded/forarbete/bet/<year>/    # data.riksdagen.se harvest (utskottsbetänkanden; record json + PDF, no HTML landing page)
site/data/downloaded/forarbete/rskr/<year>/   # data.riksdagen.se harvest (riksdagsskrivelser; record json + HTML body, no PDF)
site/data/ocr/forarbete/<type>/<year>/        # optional re-OCR sidecar PDFs (win over frozen scans)
site/data/downloaded/remisser/<typ>/<id-slug>.json  # regeringen.se remiss ärende record (Remiss json), keyed on the referred document, not the ärende-page slug
site/data/downloaded/remisser/<typ>/<id-slug>/       # its per-organisation answer PDFs (beside the record)
site/data/artifact/stats/statistik.json       # the 54 corpus measurements (no downloaded/ half — the corpus is the input)
site/data/artifact/stats/archive/statistik-<date>.json  # one dated snapshot per compute run, kept indefinitely
```

Historical corpora use the ordinary `site/data/downloaded/` tree and the same
record format as live-harvested documents. They need no separate mount.

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

`fingerprints.json` (the same directory) holds the coarse per-(step, source)
gates that let a whole stage answer "up to date -- skipped" without the per-
document freshness scan. A dry run never records one: `lagen eurlex parse -n`
after a parser edit printed a 64,004-document plan and then marked the source
current, so the real run that followed skipped the entire stale artifact tree.

```sh
uv run python -m ferenda.build <source> status   # extended: also shows failed/empty, writes the authoritative snapshot cell
uv run python -m ferenda.build all runs [N]       # recent runs from the ledger
uv run python -m ferenda.build all errors [N]     # newest N served-site errors (default 50), newest first
uv run python -m ferenda.build all errors <id>    # one error in full, traceback included (the 8-hex id its error page showed)
uv run python -m ferenda.build ann status         # inventory the curated LLM-layer store (lib/annstore.py): status/date/staleness per .ann/.corr layer
```

`lib/errorlog.py` owns a separate ledger, `DATA/.build/httperrors.ndjson`, for
the *serving* side rather than the build: one record per 404/500 the running
site answered, keyed by an 8-hex id the error page shows the reader, so "a
page was broken" becomes a url, referer, client and (for a 500) a traceback
someone can act on. It never mixes with `errors.json` — a document can be
missing from the site with no build having failed, which is exactly the case
worth recording here. Written by `api/errors.py`'s exception handlers,
rotated at 8 MB keeping one `.1` generation, read by `lagen all errors`.

`/ops` is an HTML health dashboard mounted on the same FastAPI app as the REST
API (`api/ops.py`) — a system panel (deployed revision baked at image build,
the lagen-wiki repo's push state, OpenSearch index size), a per-source corpus
inventory (documents + artifact size), the per-source × per-stage matrix, a
stale-snapshot banner, failing-doc totals, the last runs, duration-regression
flags, and the catalog delta — with `/ops/runs`, `/ops/runs/{id}` (per-source timing bars +
segments + errors) and `/ops/failures` (drill-down with tracebacks) alongside
it. It's gated by the inline editor's session (`auth.require_editor`) — it
rides the same editor login rather than a separate credential, so any editor
can view it: no session answers 401, and an unset `editor_secret` disables it
entirely (403), exactly as the edit routes do.

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
uv run python -m ferenda.api.auth hash '<the password>'   # prints the pbkdf2$… line
```

`editor_secret`/`editors` follow the same env→config.yml precedence as the other
knobs (`EDITOR_SECRET` env; `editors` is config-only). Leaving `editor_secret`
unset disables editing wholesale — every `/internal-api/v1/{auth,edit}/*` route, and the
`/ops` dashboard that rides the same session, answers 403.

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
`GET /internal-api/v1/auth/me` check, keyed off a `<meta name="lagen-doc">` the renderer
injects. On a statute / EU-act page an ✎ button on a `§`/article edits the
**commentary** for that node (the official text stays read-only) — the `##`
section is created from its heading if none exists, and the file with an
`annotates:` frontmatter if the host has no commentary at all. Concept and
editorial pages edit their whole markdown body. The editor has a link toolbar
that turns a search hit into an `sfs:`/`eurlex:`/`begrepp:` link.

Edits accumulate in a per-user **cart** (`DATA/.build/edits/<user>.json`, kept
out of the working tree so users don't collide). The masthead carries the
logged-in editor's own control — a circle with their initials, beside the
collection and theme circles, badged with the number of uncommitted changes —
and it opens the checkout. Checkout takes a commit message and turns the whole
cart into **one git commit authored as that user** — byte-for-byte the history
a `git clone` + commit would produce — then synchronously re-parses /
re-relates / regenerates just the touched pages (`build.rebuild_after_commit`)
so the edit is live when the request returns. A hunk that changed on disk since
it was carted fails the checkout (409) rather than clobbering.

The routes are same-origin only (the session cookie is `SameSite=Lax`; CORS
stays GET-open for the public read API). No new dependencies — cookie signing
and password hashing are stdlib `hmac`/`hashlib`.

### Reviewing `.graphics` crops

`sfs ai-includegraphics` (see above) writes each recovered graphic/table/formula
as a `generated` `.graphics` entry; `annstore.publishable` keeps it out of the
public render until a human signs it off. `GET /internal-api/v1/graphics/review`
(`api/graphicsedit.py` + `api/graphics.py`) is where an editor does that — same
login and session as the commentary editor above. The page lists every pending
crop (`GET /graphics/queue`) and, for one at a time, shows the crop next to the
whole source page with its rectangle drawn on it (`GET /graphics/page`,
`GET /graphics/crop`): a confident placement on the wrong figure still returns
a clean, plausible picture, and only the full page reveals it. The reviewer
approves it as-is, drags the rectangle and approves the moved one, or declares
the whole page — `POST /graphics/cart` carts the decision through the same
`editcart.py` cart, `base_sha` conflict check and attributed commit the
commentary editor uses (`editcart.py` now dispatches on the draft's *kind*, so
a graphics decision and a markdown edit share the same machinery). Checkout
regenerates only the host statute's page — a reviewed entry needs no reparse or
relate, since the layer is read at generate time
(`page._graphics_index`) — via `build.rebuild_after_commit`'s `graphics`
branch. The page/crop routes deliberately bypass `annstore.publishable` for a
logged-in editor, since an editor has to see an unreviewed crop to judge it;
the public `GET /api/v1/sfs-graphic` still 404s it.

## Patch files (source corrections + redactions)

Controlled, version-controlled fixes to a document's **source material**, applied
at parse time before the text is tokenised — the reference projection's `patch_if_needed`,
re-done. A **correction** fixes a real error in a downloaded source (an OCR slip, a
broken table); a **redaction** removes personal data (a named party, a
personnummer) and is stored **obfuscated** (ROT13 over letters, ROT5 over
digits) so the removed text is not
plain-text googleable in the committed tree.

A patch is an ordinary unified diff against a document's **best intermediate
format** — the representation its parser actually reads and a human can edit: plain
text for `sfs`; the Formex XML for `eurlex` (the OJ HTML for pre-Formex acts); and
the `pdftohtml -xml` output (verbose but editable) for the PDF-bodied sources
(`forarbete`, `foreskrift`, `remisser`, `edpb`, `rs`, and JO/ARN/IMY under `avg`; JK, and KKV's pre-2006 documents, are landing-page/published
HTML). `dv` has three: the **whole API record JSON**, not just its `innehåll`
body — a redaction has to reach `malNummerLista` and the running text alike, or
a redacted party finds their own case again through the field the patch didn't
touch; the court's own PDF as `pdftohtml -xml`, for a verdict published before
its referat (no `innehåll` yet); and the frozen notis XML for a legacy-only
case. A legacy Word referat has no editable text form (read through POI) and
cannot be patched, the same as avg's two Word documents. `dv`'s and `eurlex`'s
intermediates ship their whole body on one line, which a line-based diff can't
usefully target, so both are normalised to one block element per line first
(`lib.markup.block_lines`/`indent_xml`) — a transform that only inserts
newlines *between* elements, so what the parser reads back out is unchanged.

Each vertical's parser applies the patch at that choke point —
`lib.patch.patch_if_needed(...)` for the text/JSON/HTML/XML sources, a `patch_key`
threaded into `lib.pdftext.pdf_pages` for the PDF ones; a patch that no longer
applies is a **fatal** parse error (the source drifted — it must be regenerated,
never silently skipped). The one exception is an archived SFS consolidation (the
`versions` stage): the statute's patch is offered to every superseded wording via
`lib.patch.apply_if_fits`, which skips a **correction** that doesn't fit an older
lydelse (a conflict there is the normal case, not a broken patch) but keeps a
**redaction** fatal — republishing unredacted personal data because an older
wording didn't line up is exactly the harm, so that version is recorded as
skipped rather than published (`sfs.versions.build`). Patches live committed in
the **content repo** at `patches/<source>/<relpath>.patch` (or `.rot18.patch`),
keyed by the same rule as the artifact tree (`layout.patch` — `PATCHES`, which
is `config.WIKI_ROOT/patches`);
they are folded into every patchable source's parse freshness inputs so editing
one re-stales its document.

They sit in the content repo (`../lagen-wiki`, `WIKI_ROOT`) beside `commentary/`,
`concept/` and `ann/` rather than in this code repo, because a patch is the same
kind of thing as a commentary: hand-authored editorial knowledge about one
document, reviewed and versioned on its own. It also gives the running site one
write target instead of two — one mount, one push, one row on the ops dashboard.
The tree has to sit inside a real git checkout at runtime because the web editor
below *commits* what it writes, and the deployed image is built with `.git`
excluded (`.dockerignore`), so an in-image tree could neither commit nor survive
a container replacement. Production bind-mounts the content repo checkout at
`/wiki`, so a save is an ordinary commit that pushes to origin and reaches dev by
pull — exactly as a commentary edit does.

`layout.patch` asserts the tree exists. An absent one is indistinguishable from
"this document has no patch", so a mistyped mount would drop every `.rot18`
redaction and republish the personal data it removes — silently. The ops
dashboard reports the content checkout's unpushed/dirty state, because nothing
pushes it automatically.

The six folkrätt sources — `hudoc`, `coe`, `icrc`, `untc`, `icc`, `icj` — apply a patch
at parse time the same way (`patch.apply` on the stored record/HTML text for
`hudoc`/`icrc`/`untc`, a `patch_key` threaded into `lib.pdftext.pdf_pages` for the
PDF-bodied `coe`/`icc`/`icj`), but none has a `patchsource.py` `_INTERMEDIATE` entry, so
`mkpatch`/the web editor cannot generate a pristine intermediate to diff against
for them — only a hand-written diff against the stored source text applies.

Author them from the CLI or the inline web editor:

```sh
lagen sfs patch-show 2018:585 > /tmp/585.txt   # the intermediate text (patch applied)
$EDITOR /tmp/585.txt                            # edit to the desired final text
lagen sfs mkpatch 2018:585 /tmp/585.txt "Rättad OCR-felaktighet"
lagen dv mkpatch "NJA 2015 s 1" /tmp/case.json "Avidentifierad part" --obfuscated
```

The web surface (`api/patch.py`, gated by the same editor auth as the commentary
editor) serves `GET /internal-api/v1/patch/edit?source=…&basefile=…` — a textarea seeded
with the intermediate text; saving writes the *minimal* diff, commits it attributed
to the editor, and force-reparses the document so the fix is live. Editing the text
back to the pristine source removes the patch. A logged-in editor reaches it from a
**🩹 Patcha källtext** button that `editor.js` grafts next to the *✎ Kommentera
dokumentet* button on any patchable document page (the page's `<meta name="lagen-doc">`
carries the `data-source`/`data-basefile` identity). See `patches/README.md` in
the content repo.

## Production deployment

Production runs with Docker Compose. The application image contains the code and the full pipeline toolchain. The corpus, catalog, content repository, and secrets remain host-mounted data.

The Compose project provides OpenSearch, the Ferenda application, nginx, and certbot. Pushes to `main` run `.github/workflows/deploy.yml`. Scheduled jobs run incremental downloads and rebuilds inside the application container.

See [`../docs/operating/README.md`](../docs/operating/README.md) for service commands, configuration, corpus seeding, cache policy, and scheduled browser downloads.
