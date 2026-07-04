# accommodanda — developer setup

The rebuilt ferenda pipeline: vertical source pipelines (sfs, dv, eurlex,
forarbete, foreskrift, avg, wiki) that go from downloaded source files to a
typed document model and a JSON artifact, with the citation engine as a
shared library. For *why* it's shaped this way and what's done vs.
pending, read [`../REWRITE.md`](../REWRITE.md); this file is just how to
get it running.

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
| `legacy_import.py` | shared §7g frozen-import core — `should_write` precedence (live-wins / own-import-idempotent-unless-force / optional `better()` tie-break), `rel` (in-place LEGACY_ROOT-relative body references), `iter_entries`/`docdir`/`read_record` walk primitives; used by `forarbete/legacy.py`, `foreskrift/legacy.py`, `avg/legacy.py` |
| `regeringen.py` | shared regeringen.se harvest knowledge — the doctype table (`TYPES`: url segment, taxonomy category id, identifier regex) and the `ul.list--block` listing walk (`listing_items`); used by `forarbete/download.py` and `remisser/download.py` |

**DV vertical (court decisions)**
| File | What |
|---|---|
| `download.py` | downloader for the rättspraxis API |
| `identity.py` | entity-resolution index (one canonical case ← many source records) |
| `model.py` | `Avgorande` model (metadata + ordered Rubrik/Stycke body + footnotes) |
| `parse.py` | **API path** — body from `innehall` HTML, metadata from curated fields |
| `structure.py` | instance/ruling segmenter (delmål → instans → betänkande/dom → domskäl/domslut) |
| `naming.py` | canonical case title — referat identity + HD's given names (`case_label`) |
| `namedcases.py` | harvester for HD's named-precedent list (`data/namedcases.json`) |
| `word.py` | **legacy path** — POI (HWPF/XWPF) → flat `(text, bold, in_table)` stream |
| `legacy.py` | legacy stream → head/body split → `Avgorande` |

**forarbete vertical (preparatory works — prop/sou/ds/dir)**
| File | What |
|---|---|
| `download.py` | regeringen.se harvester (`lagen forarbete download [prop\|sou\|…]`); basefile = the document's own identifier; a `source`-carrying import record is treated as absent so live always wins |
| `model.py` / `structure.py` / `parse.py` | `Forarbete` model, PDF (font-aware `pdftohtml`, or `pdftotext` fallback for OCR-layer scans) / html → nested structure → citation-scanned artifact; `_legacy_body` prefers a re-OCR sidecar at `layout.fa_ocr_pdf` |
| `legacy.py` | one-time import of the nine frozen förarbete corpora (`lagen forarbete import-legacy <corpus>`, §7g) — shared precedence core; regeringen-era + KB corpora entries-driven, the TRIPS family (proptrips/dirtrips/dirasp) walked downloaded-first (path-derived basefile, ~half their entries are null) |
| `legacy_formats.py` | frozen body adapters — dokumentstatus XML, riksdagen text/tml + skanning2007 html, ABBYY OCR-XML (`abbyy_pages`), scanned-PDF OCR text (`scanned_pdf_pages`), TRIPS `div.body-text` (`trips_paras`) |
| `kommentar.py` / `genomforande.py` | författningskommentar → `implements` (EU directive article) edges |

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
| `harvest.py` | the shared harvest engine (enumerate → resolve → fetch per architecture) |
| `download.py` | the `lagen foreskrift download` front over the engine (`--full`, `--only`; frozen-only fs are a logged no-op) |
| `legacy.py` | one-time import of the two harvest-blocked corpora (`lagen foreskrift import-legacy {skvfs\|sosfs}`) — frozen bytes referenced in place (§7g) |
| `model.py` / `structure.py` / `parse.py` | as-published `Foreskrift` model, PDF → statute-shaped structure → artifact (`parse.body_path` resolves a frozen-import body under LEGACY_ROOT) |

**remisser vertical (regeringen.se referral responses)**
| File | What |
|---|---|
| `model.py` | `Remiss` (case: title, dnr, deadline, `remitterat` cross-ref to the referred förarbete, `svar` list of `Remissinstans`), `Remissvar` (one organisation's parsed answer); `org_slug` derives the shared basename identity `download`/`parse`/`build` all key on |
| `download.py` | regeringen.se `/remisser/` two-pass sync — discover new cases (`--full` re-walks everything), then re-poll every still-open case for newly-arrived answers and fetch any answer PDF not yet cached; `sync_one`/`--only <url>` fetches one known case directly; a 404/500 case page is written as a *stub* record (from listing facts only) so the incremental watermark can't hide the failure — re-polled until it succeeds |
| `parse.py` | one answer PDF → `Remissvar` via the shared `lib/pdftext` (`pdf_pages` + `page_paragraphs`), flattened to plain paragraph text; passes `identifier=None` since each organisation's PDF carries its own letterhead, not a fixed running header |
| `ai_analyze.py` | `lagen remisser ai-analyze <case-slug>/<org-slug>` — the sole LLM pass: maps one answer onto the referred SOU/Ds's sections with a per-section sentiment + verbatim quote plus an overall stance, validated strictly and written as a `.ann` sidecar; retries once via `lib.llm.complete_thread` on a malformed reply |

This source is never `relate`d/`generate`d — it publishes no pages of its own;
`render._remiss_indexes` reads its `.ann` sidecars straight off the filesystem
(`layout.artifacts("remisser")`) and surfaces them as a "Remissvar" section on
the *referred förarbete's* context rail.

**Service layer**: `api/app.py` is the REST/OpenAPI service (search, documents,
citation graph, version history + diff) that also serves the static site under
`lagen serve`.

## Running the pipelines

**SFS** (operates on the golden / downloaded trees under `site/data/sfs/`):

```sh
uv run python -m accommodanda.sfs parse site/data/sfs/downloaded/2018/585.json --basefile 2018:585
# golden = the old pipeline's parsed XHTML (site/data/sfs/parsed), normalized to NF on the fly
uv run python -m accommodanda.sfs validate site/data/sfs/parsed site/data/sfs/downloaded --sections structure,references
uv run python -m accommodanda.sfs refs FILE PARSED.xhtml  # citation diff for one doc
```

**SFS version history** (historical consolidations / time travel / diff): the
downloader archives every superseded consolidation under
`site/data/sfs/archive/downloaded/{y}/{n}/.versions/` (the old site's two HTML
generations live there too, imported wholesale). The `versions` stage parses
them into `archive/artifact/…/.versions/{vy}/{vn}.json` plus a per-statute
`artifact/{y}/{n}.versions.json` sidecar; `generate` then renders one page per
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

**DV** (operates on `site/data/domstol/` (API) and `site/data/dv/` (legacy)):

```sh
# download + build the identity index
uv run python -m accommodanda.dv.download site/data/domstol/downloaded   # [--full] [--no-bilagor] [--limit N]
uv run python -m accommodanda.dv.identity                       # -> site/data/dv/identity-index.json

# parse (API path is driver-owned; `[ids…]` parses just those, empty = all stale)
uv run python -m accommodanda.build dv parse                                       # API path, incremental
uv run python -m accommodanda.dv.legacy --index site/data/dv/identity-index.json   # legacy POI path, batch report
uv run python -m accommodanda.dv.legacy site/data/dv/downloaded/ADO/1993-100_1.doc # one Word file -> artifact
```

The DV parsers are driven by the identity index: each canonical case is
parsed from its single best source — the API record when present, the
legacy Word original otherwise (no cross-source merge; see REWRITE.md §4).

**avg — JO + JK + ARN decisions** (operates on `site/data/avg/`):

```sh
uv run python -m accommodanda.build avg download        # all three organs; or: … download jo
uv run python -m accommodanda.build avg parse           # incremental, like every source
uv run python -m accommodanda.build avg download jo --only jo/2340-2025   # one decision
```

**remisser — regeringen.se referral responses** (operates on `site/data/remisser/`;
never `relate`d/`generate`d — see the module map above):

```sh
uv run python -m accommodanda.build remisser download                    # harvest new cases + re-poll open ones
uv run python -m accommodanda.build remisser download --only <case-url>  # one case, bypassing the listing walk
uv run python -m accommodanda.build remisser parse                       # incremental, like every source
uv run python -m accommodanda.build remisser ai-analyze <case-slug>/<org-slug>  # the sole LLM pass
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
this — `kommentar/artifact/<host_source>/<host_relpath>.json` (e.g.
`kommentar/artifact/eurlex/2023/32023R2854.json`), reusing the host source's own
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
hub pages that link it (`site/data/kommentar/guidance-index.json`). The DG WAF
429s a random slice of every run, so the index **merges across runs and
converges** — re-run to fill the gaps, or `--force` for a clean authoritative
rebuild when the rate budget is fresh. So the usual flow is `discover-guidance`
once, then `propose-guidance <CELEX>` per act.

Guidance *published in the OJ* is a different animal — it gets its own sector-5
`XC`/`DC` CELEX and is machine-linked to the parent act in Cellar
(`work_cites_work` / `resource_legal_based_on_resource_legal`), so it belongs in
the corpus as an ordinary eurlex document, not as an external `.ann` link
(sector-5 harvest is not wired yet).

The action downloads + caches each PDF (under `kommentar/guidance/`), flattens it
to page-marked text, and asks the configured Berget model to map guidance sections
(FAQ questions) to the act's **fine-grained targets** — not just whole articles but
the sub-articles and recitals the act divides into: a single definition `2.21`, a
numbered paragraph `6.2`, a recital `recital-15` (the dotted sub-article / `recital-N`
anchor grammar `eurlex.structure` mints, shared with the renderer and the wiki
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

## Data layout

The pipelines read large data trees that live under `site/data/` (not all
committed):

```
site/data/sfs/downloaded/                     # SFS raw (beta JSON + legacy sfst/sfsr HTML)
site/data/sfs/parsed/                         # old pipeline's frozen golden corpus (XHTML)
site/data/sfs/artifact/                       # parsed JSON artifacts (+ .versions.json sidecars)
site/data/sfs/archive/{downloaded,artifact}/  # superseded consolidations, raw + parsed
site/data/domstol/downloaded/                 # DV new-API harvest (per court)
site/data/dv/{downloaded,intermediate}/       # DV legacy feed (.doc/.docx + old XML)
site/data/dv/identity-index.json              # canonical case -> source records
site/data/avg/downloaded/{jo,jk,arn}/         # JO/JK/ARN records (+ jo/arn PDFs, jk landing html)
site/data/forarbete/downloaded/<type>/        # regeringen.se harvest + frozen-import records
site/data/forarbete/ocr/<type>/               # optional re-OCR sidecar PDFs (win over frozen scans)
site/data/remisser/cases/                     # regeringen.se remiss case records (Remiss json)
site/data/remisser/downloaded/<case-slug>/    # per-organisation answer PDFs
```

The frozen legacy corpora (REWRITE.md §7g) are NOT under `site/data/`: import
records reference their body files in place under `legacy_root` (config.yml;
defaults to the sibling `../ferenda.old/data`) — moving those trees means
updating that one key, never rewriting records.

## Production deployment (Docker)

The prod host runs one compose project that serves **both** sites behind the
legacy nginx: `lagen.nu` (the legacy stack) and `ferenda.lagen.nu` (this
rebuilt site). Which services start is chosen by a Compose **profile**:

| invocation | services | use |
|---|---|---|
| `docker compose up -d` | `opensearch` only | dev — run `lagen all serve` from the working tree |
| `docker compose --profile prod up -d` | full stack | prod — legacy stack + `accommodanda` + `opensearch` |

`opensearch` carries no profile, so it starts in both; everything else is
`profiles: [prod]`. On the prod host, set `COMPOSE_PROFILES=prod` in `.env` so
every command (`ps`, `logs`, `restart`, …) sees the whole stack without the flag.

**One-time host setup**

1. Tag the existing legacy app image so the `ferenda` service can reference it —
   it can't build from this branch (which deleted `requirements.txt`):
   `docker tag <old-ferenda-image> lagen-ferenda:legacy`.
2. Create the read-write data dirs the `accommodanda` service mounts:
   `/mnt/data/accommodanda` (the corpus / `data_root` — NOT `/mnt/data/ferenda`,
   which is the unrelated legacy `ferenda` checkout) and `/mnt/data/lagen-wiki`
   (a clone of the `lagen-wiki` markdown repo — `WIKI_ROOT`).
3. Point `ferenda.lagen.nu` DNS at the host, then issue the SAN cert covering
   both vhosts (one-time; `certbot renew`, run automatically by the `certbot`
   service, only renews an existing cert, it doesn't create one):
   ```sh
   docker compose --profile prod up -d nginx certbot   # nginx must be up first to answer the ACME challenge
   docker compose run --rm certbot certonly --webroot -w /var/www/certbot \
     -d lagen.nu -d ferenda.lagen.nu \
     --email staffan.malmgren@gmail.com --agree-tos --non-interactive
   docker compose restart nginx   # pick up the new cert
   ```
   Cert issuance/renewal uses the official `certbot/certbot` image (see the
   `certbot` service in `docker-compose.yml`) — not the nginx image itself.
   nginx doesn't auto-reload on renewal, so reload it periodically (e.g. a
   host cron running `docker compose exec nginx nginx -s reload` weekly is
   harmless whether or not a cert actually changed).

**Bring-up**

```sh
docker compose --profile prod up -d
```

`accommodanda` is a frozen image: the code is baked in, but it also carries the
full pipeline toolchain (poppler-utils, tesseract+swe, ocrmypdf, raptor2-utils,
a JRE + the POI jars), so **download + rebuild run in the container** against the
read-write corpus mount:

```sh
docker compose exec accommodanda lagen all download
docker compose exec accommodanda lagen all rebuild   # parse→relate→index→dump→generate
docker compose exec accommodanda lagen all index
# or, without the serving process running:
docker compose run --rm accommodanda lagen all rebuild
```

One uvicorn process serves the static site + REST API (`lagen all serve`, the
image `CMD`); the nginx `ferenda.lagen.nu` vhost reverse-proxies to it on `:8000`
(the app resolves lagen.nu's bare-URL grammar itself, so nginx needs no
`try_files` rules). The corpus mount is read-write and shared with serving; a
rebuild writes essentially per-file, but for strict isolation restart the
service afterwards (`docker compose restart accommodanda`).
