# accommodanda — developer setup

The rebuilt ferenda pipeline: vertical source pipelines (sfs, dv, eurlex,
forarbete, foreskrift, avg, wiki, site) that go from downloaded (or, for
wiki/site, hand-authored) source files to a typed document model and a JSON
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
| `extract.py` | body extraction from rkrattsbaser HTML (+ archival `<pre>`) |
| `reader.py` | `TextReader` — faithful port incl. autostrip blank-line semantics |
| `tokenizer.py` | recognizers → flat event stream |
| `assembler.py` | RANK-driven stack machine → document tree |
| `model.py` | typed dataclasses (`Forfattning`, `Kapitel`, `Paragraf`, …) |
| `nf.py` | tree → golden normal form (replicates old URI-minting quirks) |
| `register.py` | SFSR register page → amendments + change tuples |
| `versions.py` | archived consolidations (download archive, three raw generations) → per-version artifacts + `.versions.json` sidecar |
| `__main__.py` | `parse` / `refs` / `validate` CLI |

**Citation engine (shared library)**
| File | What |
|---|---|
| `lagrum.py` | Lark/Earley engine; `LagrumParser(parse_types=…)` composes a grammar from LAGRUM / KORTLAGRUM / EULAGSTIFTNING / RATTSFALL / FORARBETEN / … |
| `casenaming.py` | court-decision identity — `case_uri` (mint a case's canonical URI via the RATTSFALL parser) + `case_label`/`lopnummer` (referat identity + HD's given names); read identically by dv's parse-time label stamp, the catalog row and the page heading |
| `eu_structure.py` | the one EU-act sub-article anchor grammar (`anchored_blocks`/`subarticle_key`/`flatten`), shared by the eurlex parser, the renderer and the wiki guidance layer (`nest`, the parse-time tree builder, stays in `eurlex/structure.py`) |
| `legacy_import.py` | shared §7g frozen-import core — `should_write` precedence (live-wins / own-import-idempotent-unless-force / optional `better()` tie-break), `rel` (in-place LEGACY_ROOT-relative body references), `iter_entries`/`docdir`/`read_record` walk primitives; used by `forarbete/legacy.py`, `foreskrift/legacy.py`, `avg/legacy.py` |
| `regeringen.py` | shared regeringen.se harvest knowledge — the doctype table (`TYPES`: url segment, taxonomy category id, identifier regex) and the `ul.list--block` listing walk (`listing_items`); used by `forarbete/download.py` and `remisser/download.py` |
| `harvest.py` | shared incremental-download core — `HarvestWatermark` (begin/complete lifecycle, never-regress date save, crash-safe `dirty` flag that disables the consecutive-hit stop but not the date-conclusive one) + `walk`/`Skip`/`ItemKey`/`guarded_enumerate` (the newest-first download loop over an enumerate/resolve pair); each source states its own `lookahead_limit`/`safety_days` window (dv: 365-day safety window, ~5000-item lookahead; forarbete/riksdagen/foreskrift/avg-jo: 14 days/20 items); used by `dv/download.py`, `foreskrift/harvest.py`, `avg/download.py` (jo), and directly by `forarbete/download.py` + `forarbete/riksdagen.py` |

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
| `model.py` / `structure.py` / `parse.py` | `Forarbete` model, PDF (font-aware `pdftohtml`, or `pdftotext` fallback for OCR-layer scans) / html → nested structure → citation-scanned artifact; `_legacy_body` prefers a re-OCR sidecar at `layout.fa_ocr_pdf` |
| `legacy.py` | one-time import of the nine frozen förarbete corpora (`lagen forarbete import-legacy <corpus>`, §7g) — shared precedence core; regeringen-era + KB corpora entries-driven, the TRIPS family (proptrips/dirtrips/dirasp) walked downloaded-first (path-derived basefile, ~half their entries are null) |
| `legacy_formats.py` | frozen body adapters — dokumentstatus XML, riksdagen text/tml + skanning2007 html, ABBYY OCR-XML (`abbyy_pages`), scanned-PDF OCR text (`scanned_pdf_pages`), TRIPS `div.body-text` (`trips_paras`) |
| `riksdagen.py` | downloader for utskottsbetänkanden (`bet`, the prop→enacted-law link) off the data.riksdagen.se dokumentlista JSON feed; PDF-only bodies (printed page = citation anchor); basefile `"<rm>:<beteckning>"` matching the FORARBETEN grammar's bet URIs; full backfill walks all 161 riksmöten (the API caps one query's pagination at ~10k docs); no frozen legacy corpus |
| `kommentar.py` / `genomforande.py` | författningskommentar → `implements` (EU directive article) edges; extracted from `prop` and `fm` (förordningsmotiv) documents — both accompany the final enacted text, unlike a lagrådsremiss/SOU/Ds; `fk_section` also slices out the per-law FK prose consumed by `sfs/correspond.py` (reading a proposition artifact stays förarbete's job) |

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
| `agencies.py` | the data registry driving one shared harvest engine — 17 live författningssamlingar + 4 frozen-only (skvfs/rsfs, sosfs/hslffs, §7g), no per-agency pipelines (~100 agencies share a few publishing architectures) |
| `harvest.py` | per-agency enumerate/resolve architectures (indexed/paginated/json/sitemap enumerators; landing/direct resolvers + file classifiers) wired onto `lib/harvest.py`'s shared `walk`/`HarvestWatermark` loop |
| `download.py` | the `lagen foreskrift download` front over the engine (`--full`, `--only`; frozen-only fs are a logged no-op) |
| `legacy.py` | one-time import of the two harvest-blocked corpora (`lagen foreskrift import-legacy {skvfs\|sosfs}`) — frozen bytes referenced in place (§7g) |
| `model.py` / `structure.py` / `parse.py` | as-published `Foreskrift` model, PDF → statute-shaped structure → artifact (`parse.body_path` resolves a frozen-import body under LEGACY_ROOT) |

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
| `annotate.py` | `lagen eurlex ai-annotate <CELEX>` — author the editorial `.ann` layer for a sector-3 act with an LLM |

**wiki vertical (git-backed markdown — begrepp + kommentar)**
| File | What |
|---|---|
| `parse.py` | project the markdown wiki into kommentar / begrepp artifacts; the `## heading → host node anchor` grammar (`heading_fragment`, `fragment_heading`), `host_uri`, and the frontmatter-keyed `kommentar_index`/`begrepp_index` |
| `annotate.py` | `lagen kommentar ai-annotate <basefile>` — the Step-4 AI guidance linker: read an annotation's declared guidance PDFs and propose, per article, the guidance links (`.ann` sidecar) |
| `guidance_discover.py` | `lagen kommentar {discover,propose}-guidance` — crawl Commission guidance sitemaps into a per-CELEX index + draft a `guidance:` block to review (no LLM) |

**remisser vertical (regeringen.se referral responses)**
| File | What |
|---|---|
| `model.py` | `Remiss` (case: title, dnr, deadline, `remitterat` cross-ref to the referred förarbete, `svar` list of `Remissinstans`), `Remissvar` (one organisation's parsed answer); `org_slug` derives the shared basename identity `download`/`parse`/`build` all key on |
| `download.py` | regeringen.se `/remisser/` two-pass sync — discover new cases (`--full` re-walks everything), then re-poll every still-open case for newly-arrived answers and fetch any answer PDF not yet cached; `sync_one`/`--only <url>` fetches one known case directly; any per-case fetch or parse failure (HTTP error, or a 200 whose DOM doesn't match — a bot-challenge interstitial, a truncation) is written as a *stub* record (from listing facts only) so the incremental watermark can't hide the failure — re-polled until it succeeds |
| `parse.py` | one answer PDF → `Remissvar` via the shared `lib/pdftext` (`pdf_pages` + `page_paragraphs`), flattened to plain paragraph text; passes `identifier=None` since each organisation's PDF carries its own letterhead, not a fixed running header |
| `ai_analyze.py` | `lagen remisser ai-analyze <case-slug>/<org-slug>` — the sole LLM pass: maps one answer onto the referred SOU/Ds's sections with a per-section sentiment + verbatim quote plus an overall stance, validated strictly and written as a `.ann` sidecar; retries once via `lib.llm.complete_thread` on a malformed reply |

This source is never `relate`d/`generate`d — it publishes no pages of its own;
`render._remiss_indexes` reads its `.ann` sidecars straight off the filesystem
(`layout.artifacts("remisser")`) and surfaces them as a "Remissvar" section on
the *referred förarbete's* context rail.

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

**Service layer**: `api/app.py` is the REST/OpenAPI service (search, documents,
citation graph, version history + diff) that also serves the static site under
`lagen serve`. `api/ops.py` mounts the ops health dashboard on the same app
(see "Operations" below); `lib/runlog.py` owns the state files behind it.
`api/auth.py` + `api/edit.py` + `api/editcontent.py` + `api/editcart.py` are the
inline content editor — the one authenticated, mutating surface (see "Inline
editing" below).

## Running the pipelines

**SFS** (operates on `site/data/{downloaded,artifact}/sfs/`, validated against
the golden corpus in the old checkout, `../ferenda.old/data/sfs/parsed/`):

```sh
uv run python -m accommodanda.sfs parse site/data/downloaded/sfs/2018/585.json --basefile 2018:585
# golden = the old pipeline's parsed XHTML (scaffolding in the old checkout), normalized to NF on the fly
uv run python -m accommodanda.sfs validate ../ferenda.old/data/sfs/parsed site/data/downloaded/sfs --sections structure,references
uv run python -m accommodanda.sfs refs FILE PARSED.xhtml  # citation diff for one doc
```

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
`/api/v1/document/versions`). A future `history-as-git` export is specced in
[`docs/prd-sfs-history-as-git.md`](../docs/prd-sfs-history-as-git.md).

```sh
uv run python -m accommodanda.build sfs versions            # incremental, all statutes
uv run python -m accommodanda.build sfs versions 1998:204   # one statute
```

**DV** (operates on `site/data/downloaded/dom/` (API) and `site/data/downloaded/dv/` (legacy)):

```sh
# download + build the identity index
uv run python -m accommodanda.dv.download site/data/downloaded/dom   # [--full] [--no-bilagor] [--limit N]
uv run python -m accommodanda.dv.identity                       # -> site/data/artifact/dom/identity-index.json

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
article 2 as a whole. The result is written as a **`.ann` sidecar** next to the
kommentar artifact — `{"guidanceLinks": {anchor: [{label, href, desc, section}]}}` —
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
site/data/artifact/sfs/                       # parsed JSON artifacts (+ .versions.json sidecars)
site/data/{downloaded,artifact}/sfs/archive/  # superseded consolidations, raw + parsed
site/data/downloaded/dom/                     # DV new-API harvest (per court)
site/data/downloaded/dv/                      # DV legacy feed (.doc/.docx)
site/data/artifact/dom/identity-index.json    # canonical case -> source records
site/data/downloaded/avg/{jo,jk,arn}/         # JO/JK/ARN records (+ jo/arn PDFs, jk landing html)
site/data/downloaded/forarbete/<type>/        # regeringen.se harvest + frozen-import records (prop/sou/ds/pm/dir/fm/skr/so/lr)
site/data/downloaded/forarbete/bet/           # data.riksdagen.se harvest (utskottsbetänkanden; record json + PDF, no HTML landing page)
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
