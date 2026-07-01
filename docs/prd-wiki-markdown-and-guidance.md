# PRD — Git-backed markdown authoring + curated/AI guidance linking

Status: **Steps 1–4 landed** · Owner: Staffan · Scope:
`accommodanda/wiki`, `accommodanda/lib`, build driver, a new content repo
(`../lagen-wiki`, a sibling).

## 0. Summary

Two related goals:

1. **Move the hand-authored value-add (commentary + concept glossary) off the
   dead MediaWiki/wikitext stack onto a git-backed markdown system.** Authoring
   becomes "edit a `.md` file, commit" instead of running a wiki.
2. **Connect official-but-external guidance** (Commission FAQs, guidance PDFs,
   call-for-evidence pages) to the acts and, eventually, the individual articles
   they explain — first by hand, later assisted by an LLM that reads the PDFs.

Delivered as a **four-step plan**, each step usable on its own and a prerequisite
for the next:

| # | Step | Outcome | Effort |
|---|------|---------|--------|
| 1 | **MediaWiki → git+markdown migration** ✅ | 570 begrepp + 212 kommentar authored as markdown in a sibling content repo; artifacts equivalent modulo enumerated improvements | L |
| 2 | **Manual act-level guidance links** ✅ | A curated "Externa länkar" rail panel on an act (SFS or EU), hand-edited, low friction | S–M |
| 3 | **Curated per-article guidance/commentary** ✅ | Guidance + commentary attached to a *specific article/recital* of an act, shown in that node's context rail | M |
| 4 | **AI guidance linker** ✅ | An `ai-annotate` pass that reads the guidance PDFs an annotation declares and *proposes* article↔guidance links as a `.ann` sidecar for human review | L |

> The four steps below are the author's reading of the stated intent ("move to
> git+markdown", "extend wiki to handle guidance", "for now a manual link at the
> top of the act", "ideally an AI process that reads the PDFs per article").
> Confirm the decomposition before Step 1 begins.

## 1. Background

### 1.1 How the wiki source works today

- Source of truth: a MediaWiki XML export under
  `site/data/mediawiki/downloaded/` (`dump.xml` plus per-page splits).
- `accommodanda/wiki/parse.py` projects two **annotation/value-add sources**:
  - **`begrepp`** (`begrepp_artifact`) — a concept page published at
    `begrepp/<Name>`; `{uri, type, title, categories, body[]}`.
  - **`kommentar`** (`kommentar_artifact`) — per-paragraph statute commentary;
    `{uri, type, basefile, annotates, author, categories, body[]}` where each
    `== 21 kap 1 § ==` heading becomes a `sektion` anchored to the statute
    fragment (`K21P1`). **It has no page tree** — it is shown in the commented
    paragraph's **context rail** (`render._commentary_index` →
    `Rail._commentary`).
- `lib/wikitext.py` turns wikitext into the shared inline-run shape:
  `[[Concept]]` → `begrepp/<Concept>` links, plain-text legal citations →
  citation-engine links, `[[Kategori:]]` → categories, `''Huvudförfattare:''` →
  author. **It strips templates, tables, HTML and bold/italic to plain text.**
- Build wiring (`build.py`): `SOURCES["kommentar"|"begrepp"]`, parse stage only
  (**no download stage** — derived from the dump), invalidated by `WIKI_CODE`
  (`wiki/parse.py`, `lib/wikitext.py`, `lib/lagrum.py`). Indexes
  `kommentar_index` / `begrepp_index` map id/title → file path.
- Downstream is fully decoupled: `catalog.{kommentar,begrepp}_document`,
  `links` table, relate-time concept synthesis, `render_begrepp` / the rail —
  **all consume only the artifact JSON.**

### 1.2 Why change

- The live MediaWiki is effectively frozen; wikitext is a poor authoring format
  and an awkward parse target. There is no review/PR workflow, no diff history
  in a form developers use.
- There is **no mechanism today to attach external official guidance** to an
  act. EU acts in particular accrue substantial Commission guidance/FAQs that
  are authoritative but not in EUR-Lex (e.g. the CRA FAQ and draft guidance).

### 1.3 Decisions already taken

- **Content lives in a separate git repo** — a **sibling** checkout
  (`../lagen-wiki`), pointed at by `WIKI_ROOT`, *not* a git submodule. A sibling
  is just as maintainable here (no remote/`.gitmodules` ceremony, no submodule
  pointer to bump), and the build asserts the content dir exists with a clear
  error. (The earlier draft assumed a submodule; sibling is what shipped.)
- **Internal concept links use standard markdown:** `[label](begrepp:Concept)`;
  **external links** use ordinary `[label](https://…)`.
- **Auto-convert all existing pages, preserving authorship** (one git commit per
  revision, replayed from the host MediaWiki SQLite DB).

## 2. Goals / non-goals

**Goals**
- Author begrepp + kommentar as markdown in a versioned content repo; commit =
  publish-after-build.
- Preserve 100% of what the current pipeline *consumes* from the wiki (prose,
  concept links, citations, headings, categories, author) — verified by
  artifact equality.
- A low-friction, hand-curated way to surface external guidance at the act level
  (Step 2), then per article (Step 3).
- An LLM-assisted, human-in-the-loop way to scale per-article guidance linking
  from PDFs (Step 4).

**Non-goals**
- Re-running or replacing the live MediaWiki. The export is a one-time seed.
- Auto-publishing AI output without review (Step 4 produces drafts only).
- Reconstructing full multi-revision page history that the dump does not contain
  (see §3.3).
- Changing catalog/render/relate beyond what each step explicitly needs.

## 3. Constraints & key findings

### 3.1 The migration is bounded to the artifact-producing layer

Because every consumer reads only the artifact JSON, Step 1 keeps the artifact
shape fixed so nothing downstream (catalog/render/relate) changes. The conversion
is verified **equivalent** by a corpus diff (`tools/wiki_artifact_diff.py`): the
markdown path and the `wikitext` reference produce identical artifacts on all 782
pages.

Note this is *equivalence*, not literal byte-identity with the **pre-migration**
output — that was never fully attainable, because part of the task is moving the
authoring format off wikitext, and two deliberate **improvements** landed with the
migration and are the enumerated, intended divergences:

- **External links.** `[url label]` wiki syntax → real `[label](url)` link runs
  (previously left as literal text). Both the converter and the retained
  `wikitext` reference gained this, so the diff stays clean.
- **Bare `## N §` commentary headings.** Now anchor to `P{N}` (matching a
  continuously-numbered law's SFS fragments) instead of being mis-bucketed under
  the chapter sektion — a bug fix in `heading_fragment`, shared by both paths.

### 3.2 Markdown is lossless w.r.t. consumed content

`wikitext.py` already discards templates/tables/HTML/formatting. The artifact
keeps only prose + links + headings + categories + author. A markdown format
that carries *those* loses nothing the site uses.

### 3.3 History/authorship reality (measured on `dump.xml`)

- 1,795 pages, **1,795 revisions — exactly one per page.** The dump is a
  *latest-snapshot* export; it does **not** contain multi-revision history.
- **1,269 / 1,795** pages carry a revision contributor username + timestamp.
- **40** pages carry an explicit `Huvudförfattare` byline.

Implication: from *this* dump alone, "preserve history" would mean only **one
seed commit per page** (last editor, revision timestamp, byline → frontmatter).

**Resolved (O1): the full history is obtainable from the host's MediaWiki
database** — reportedly a SQLite DB. MediaWiki keeps every revision in
`page` / `revision` / `text` (+ `actor`, `comment` in newer schemas; older
versions inline `rev_user_text` / `rev_comment` and store wikitext in
`text.old_text`). The converter (§4.1) therefore sources from **the DB, not
`dump.xml`**, and replays **one git commit per revision** in chronological
order, authored per revision. `dump.xml` becomes a fallback only. **Caveat C1:
confirm the DB is reachable and inspect its actual schema** (MediaWiki version
determines whether wikitext is in `text.old_text` vs the `content`/`slots`/`text`
model, and whether authorship is in `actor` vs `rev_user_text`).

### 3.4 Conversion scope (main namespace, ns0)

- **570 begrepp** (title without `/`), **212 kommentar** (`SFS/` titles),
  **redirects → 77 aliased concepts**, plus Talk/Kategori/User/Template/etc.
  namespaces that are **not** converted (not content the pipeline ingests). (The
  565/213/162 figures in the earlier draft were estimated from `dump.xml`; the
  live SQLite DB — the authoritative source actually used — gives these.)

### 3.5 Identifier stability (hard requirement)

`begrepp/<Name>` URIs must not move — many inbound links and the concept graph
depend on them. MediaWiki **ucfirsts** the first letter and maps spaces to `_`
(`begrepp_uri`). The markdown parser and converter must reproduce this exactly,
and the concept-link scheme must resolve to the identical URI.

## 4. The four-step plan

---

### Step 1 — MediaWiki → git+markdown migration

**Objective.** Replace the XML dump as the wiki source with markdown files in a
sibling content repo, producing equivalent artifacts (§3.1).

**Content repo layout** (`../lagen-wiki`, a sibling pointed at by `WIKI_ROOT`):
```
concept/<Name>.md                  # one file per concept (filename = title, _→space)
commentary/<source>/<relpath>.md   # commentary, filed under the annotated source
                                   #   and its basefile→path rule, e.g.
                                   #   SFS/1915:218 → commentary/sfs/1915/218.md
```
- Commentary is filed under the **source** it annotates (today only `sfs`) using
  that source's `layout.relpath` rule — the same path convention every other
  source's artifacts use — so a second host (eurlex in Step 2+) slots in as
  `commentary/eurlex/…` without a flat namespace collision.
- The path is organisational; **frontmatter `title:` / `annotates:` is
  authoritative** for identity (avoids filesystem-encoding ambiguity for `/`,
  `:`, Swedish characters).
- (The earlier draft proposed flat `begrepp/<Name>.md` + `kommentar/<sfsid>.md`;
  the source-scoped layout above is what shipped.)

**Markdown format.**

Begrepp:
```markdown
---
title: Abandonering
categories: [Processrätt]
author: Staffan Malmgren        # optional; from Huvudförfattare byline
aliases: [Abandonera]            # optional; from #REDIRECT pages (see below)
---
[Abandonering](begrepp:Abandonering) åsyftar det förhållandet att en
konkursförvaltare under pågående [konkurs](begrepp:konkurs) låter viss egendom
utgå ur boet och lämnar egendomen till [gäldenär](begrepp:Gäldenär)en …

Konkursförvaltaren har rätt att återkalla sitt beslut (NJA 2004 s. 777).
```

Kommentar:
```markdown
---
annotates: 2009:400              # SFS id today; a CELEX in Step 2+
author: ...
---
## 21 kap 1 §
Prose; "7 kap 3 §" and "NJA 1990 s. 510" auto-link relative to the annotated law.
```

**Link conventions.**
- **Concept link:** `[label](begrepp:Concept)` → `begrepp_uri(Concept)`. A `)`
  in a concept name (e.g. `Mål (process)`) is `%29`-escaped in the file and
  decoded by the parser.
- **Legal citations:** stay **plain text**, auto-linked by the citation engine
  (unchanged — the core value). The annotated law remains the relative-reference
  base for kommentar.
- **External link:** standard `[label](https://…)` → external run (a `)` in the
  url is `%29`-escaped the same way). A lagen.nu-absolute url renders as an
  internal link via `render.href`.
- **Commentary heading → anchor:** `## N kap M §` → `#K{N}P{M}`; a bare `## N §`
  (continuously-numbered law) → `#P{N}`; `## N kap` → `#K{N}`.
- Optional explicit schemes (`sfs:1962:700#K3P1`, `celex:32024R2847#5`) — deferred
  (not needed; the citation engine covers prose refs).

**New module — `lib/markdown.py`.** A small, hand-rolled parser (consistent with
the hand-rolled `wikitext.py`; avoids a new dependency — **decision O2**) that
yields the *same* outputs `wikitext` does:
- `frontmatter(text) -> (dict, body)`.
- `blocks(body) -> [("rubrik", level, text) | ("stycke", raw)]` (ATX `#`
  headings, blank-line-separated paragraphs).
- `to_runs(text, refparser, **kw)` — parse `[label](scheme:…)` / `[label](url)`
  links into Refs, run the citation engine on the remaining plain text,
  non-overlapping (mirrors `wikitext.to_runs`).
`wiki/parse.py`'s `begrepp_artifact` / `kommentar_artifact` switch from
`wikitext.*` to `markdown.*`; their artifact assembly is otherwise unchanged.

**Converter — `tools/mediawiki_to_markdown.py`** (one-time, not shipped in the
pipeline). **Source: the host MediaWiki SQLite DB** (per §3.3), so the full
revision history is replayed into git; `dump.xml` is a latest-only fallback.
1. Enumerate ns0 pages from `page`; for each, read **all** revisions from
   `revision` (joined to `text`/`content` for wikitext and `actor`/`comment`
   for author + message), ordered by `rev_timestamp`.
2. For each revision: wikitext → markdown body — `[[A|b]]`→`[b](begrepp:A)`,
   `[[A]]`→`[A](begrepp:A)`, `==x==`→`## x`, strip templates/tables/HTML (same as
   today), `[[Kategori:X]]`→ frontmatter, byline → frontmatter `author`.
3. Redirects → `aliases:` on the target file (decision O3: aliases vs drop;
   redirect *creation/retarget* over time is itself part of a page's history).
4. **One git commit per revision**, in global chronological order across pages:
   `--author="<username> <user@lagen.nu>"` (username→identity map, O7),
   `--date=<rev_timestamp>`, message from the revision comment. The result is a
   content repo whose `git log`/`git blame` mirror the wiki's real history.
   (Idempotent/resumable so a large replay can be re-run.)

**Build rewiring.**
- `WIKI_ROOT` → the sibling content path (default `../lagen-wiki`; overridable via
  `config.yml` `wiki_root` or the `WIKI_ROOT` env var). `kommentar_index` /
  `begrepp_index` glob the markdown tree and key on frontmatter, not XML titles.
- `WIKI_CODE` → `wiki/parse.py`, `lib/markdown.py`, `lib/lagrum.py` (drop
  `wikitext.py`). `lib/wikitext.py` is **retired from the pipeline**, kept only as
  the converter's / equality-diff's reference.
- No download stage (content is in the sibling repo).

**Risks & mitigations.**
- *URI drift* (§3.5) → converter + parser share one `begrepp_uri`; the equality
  diff covers it.
- *Content-repo build ergonomics* → README documents the sibling checkout; the
  build asserts the content dir exists with a clear error.
- *Markdown ambiguity in legal prose* (a literal `[...]` or `(...)` mistaken for
  a link) → strict link grammar; the citation engine already owns bare refs; a
  stray `[` before a link stays literal text.
- *Lossy conversion* → caught by the artifact-diff.

**Acceptance criteria** — *met*.
- For all 782 pages, `markdown→artifact` equals the retained `wikitext→artifact`
  reference (`tools/wiki_artifact_diff.py`: **782 matched / 0 mismatched**),
  modulo two adjudicated content-free normalisations (edge whitespace; empty
  template-only paragraphs) and the two intended improvements in §3.1. As a
  one-off migration, the full corpus diff is a **local gate**, not CI; the
  CI-friendly surrogate is `test_wiki.py::test_conversion_is_lossless` (a fixture
  exercising the same path).
- `test/test_wiki.py` passes (16 tests, incl. regressions for the bare-§ anchor
  and external links); `lagen {begrepp,kommentar} parse` produce the artifacts and
  relate cleanly (570 + 212 documents).
- Sibling content repo documented in README; `WIKI_ROOT` points at it.

**Dependencies:** none (foundation).

---

### Step 2 — Manual act-level external guidance links (MVP, the "for now")

**Objective.** Let an editor add, in one markdown file, a curated list of
external official guidance shown at the **top of an act's page** — for SFS and,
crucially, EU acts (the CRA motivating case).

**Approach** (*landed*). Generalize the annotation layer to **any host**:
- `kommentar`'s `annotates:` accepts a **CELEX** as well as an SFS id; the host
  URI is resolved accordingly (`wiki.host_uri`: an id with a `:` is an SFS
  top-level page, else a `https://lagen.nu/ext/celex/<CELEX>` act). `kommentar_index`
  now globs all of `commentary/**` (not just `commentary/sfs/`), so a
  `commentary/eurlex/<year>/<CELEX>.md` annotation is picked up via the eurlex
  basefile→path rule.
- Add a document-level **external-links** block to the annotation markdown:
  ```markdown
  ---
  annotates: 32024R2847
  ---
  ## Externa länkar
  - [CRA Implementation FAQ](https://digital-strategy.ec.europa.eu/…)
    — Europeiska kommissionen
  - [Draft Commission guidance on the CRA](https://ec.europa.eu/…) — utkast
  ```
  `markdown.guidance(body)` splits this section out into a typed
  `guidance: [{label, href, note?}]` artifact field, removing it from the body so
  it is not also emitted as prose. A bullet's trailing `— note` (em-/en-dash or
  hyphen) is optional provenance. A body with no `## Externa länkar` heading is
  returned untouched, so every existing annotation file stays lossless. (The
  authoring keyword and the rendered rail section share the one name, "Externa
  länkar" — `markdown.GUIDANCE_HEADING`.)

**Render** (*landed*). `Rail._guidance` emits a `<div class="rail-sec vagledning">`
("Externa länkar") of links into the **document-level rail panel** (key `""`,
under an "Om dokumentet" head), built by `Rail.add_document` alongside any
law-level commentary; a lagen.nu-absolute href renders internal, any other is
`rel="external"`. `render_sfs` already called `add_document`; `render_eurlex` now
does too. The client shows this panel as the rail default (`island[''] || EMPTY`),
so it **replaces the empty-rail placeholder** ("Ingen rättspraxis … har ännu
knutits till denna del") at the top of the document. `Site` gains a `guidance`
index (`_guidance_index`: act URI → items, read from the kommentar artifacts at
`from_catalog`). **Decision O4: the document-level rail panel, not a top-of-body
section** — the rail's whole-document slot was previously empty for most acts, the
links sit beside the act rather than pushing its text down, and the name is the
generic **"Externa länkar"** (any external resource, not only official guidance).
Per-article guidance reuses the same rail mechanism, keyed per node, in Step 3.

**Catalog/links.** **Decision O5: render-only** (no `guidance` catalog table) —
external targets are not hosted, so they carry no inbound edge and relate is
unchanged. A queryable provenance table can be added later if wanted.

**Risks.** External link rot (no validation pass initially; could add a periodic
checker later). No inbound graph for external resources (acceptable — they don't
live in the corpus).

**Acceptance** — *met*. Authoring `commentary/eurlex/2024/32024R2847.md` (the
eurlex basefile→path rule) with a `## Externa länkar` block makes the CRA guidance
links render in the document-level rail panel of `/celex/32024R2847`;
`test_wiki.py::test_eurlex_guidance_renders_in_document_rail` covers the EU-host
annotation end-to-end (parse → catalog → render), plus unit tests for the markdown
guidance split and the CELEX host resolution.

**Dependencies:** Step 1 (markdown authoring + the host-agnostic annotation).

---

### Step 3 — Curated per-article guidance & commentary

**Objective.** Attach guidance/commentary to a **specific article or recital** of
an act, shown in that node's context rail (the existing kommentar mechanism, now
for EU acts).

**Approach** (*landed*).
- `heading_fragment` gained EU forms: `## Artikel 5` → `5`, the dotted
  sub-article `## Artikel 3.4` → `3.4` and `## Artikel 5.2 a` → `5.2.a`, and the
  recital `## Skäl 13` / `## (13)` → `recital-13`. **Decision O8: sub-article
  anchors are the *dotted* `article.paragraph.point` grammar
  (`eurlex.structure.subarticle_key`) — a definitions point `#3.4`, a list point
  `#5.2.a`.** This one grammar is shared by the renderer's node ids, the guidance
  `.ann` keys and the eurlex editorial `.ann` (see O12); the earlier parenthesised
  `5(2)` render/editorial key is retired (`Editorial` still normalises any legacy
  `5(2)` on load). `dangling_anchors` tolerates a non-enumerated sub-article by its
  base article.
- A per-article/recital section carries prose **and/or** a `## Externa länkar`
  list. `markdown.guidance_sections` tags each links block with the heading it sits
  under; `kommentar_artifact` attaches it to that section node (`node["guidance"]`)
  or to the document (`art["guidance"]`, the Step-2 behaviour).
- `_commentary_index` was **already** host-agnostic (keyed on `annotates`, i.e. the
  resolved host URI), so EU commentary needed no change. A new
  `_article_guidance_index` keys per-section guidance `(host_uri, anchor)`.
- **Recitals are anchorable without an editorial layer.** `render_eurlex` now mints
  the `#recital-N` id for every numbered recital unconditionally (previously only
  when a `.ann` was present), so a recital can be cited and commented on regardless.

**Render** (*landed*). `Rail.add` surfaces per-node guidance beside its commentary:
`Rail._guidance` was generalised to `_guidance_html(items)`, reused by both the
document-level panel (Step 2) and a single node's rail (Step 3). Reuses
`Rail._commentary` unchanged.

**Validation** (*landed*). `wiki.dangling_anchors(komm, host)` returns section
anchors with no matching node in the annotated act; `build.kommentar_anchor_warnings`
runs it corpus-wide. It is wired two ways: a **warning printed during `relate`**,
and a `lagen kommentar validate [basefiles…]` action. (Running it surfaced ~14
pre-existing SFS commentary files with drifted anchors — e.g. a `## 24 kap 2 §`
whose base paragraph was amended into `2 a §`/`2 b §` — triage separately.)

**Known limitation.** Per-article targeting reaches any node the artifact gives a
structural `id` (articles, definition/list points, recitals). A plain sub-paragraph
the eurlex parser leaves unanchored is addressable only via its article; this is
adequate (the nodes worth commenting on all carry anchors) and `dangling_anchors`
flags anything that doesn't.

**Acceptance** — *met*. Authoring `## Artikel 3.4` / `## Skäl 12` on `32024R2847`
shows the prose + guidance in that node's rail (verified end-to-end on the live
CRA page); `test_wiki.py` covers the EU anchor forms, per-section guidance attach,
recital rendering, and the dangling-anchor check; a validation check flags dangling
anchors at relate time and on demand.

**Dependencies:** Steps 1–2.

---

### Step 4 — AI guidance linker (PDF → proposed article↔guidance links)

**Objective.** Scale Step 3: an `ai-annotate`-style, **human-in-the-loop** pass
that reads guidance PDFs and proposes which article(s) each guidance section /
FAQ entry explains.

**Approach** (*landed*, reuses existing machinery; module `wiki/annotate.py`,
action `lagen kommentar ai-annotate <basefile>`).
- *Declare, don't guess*. The guidance documents are authored by hand in the
  annotation's frontmatter — a `guidance:` block list of `{title, url, pdf}`
  mappings (a new minimal extension to the `lib/markdown` frontmatter parser:
  block lists of mappings, distinguished from scalar URL items by the
  colon-*space* rule). The direct `pdf:` link is hand-supplied because a guidance
  document is short-lived and its URL is not derivable from the act — so the AI
  pass never has to find/guess it (**decision O9**).
- *Ingest*: download + cache each PDF under `kommentar/guidance/` (keyed on the
  url — a guidance doc outlives a build but not the act, and the source may go
  dark); flatten it to **page-marked** plain text (`[Sida N]` markers) with the
  same `pdftohtml -xml` extraction the eurlex PDF parser uses, page-aware here.
- *Prompt* (`wiki/guidance_linker_prompt.txt`, Swedish, like the preamble
  analyzer): given **only the act's article list** (number + heading — never the
  full act text, which is superfluous and would bloat the context) + the guidance
  text, return JSON `{"links": [{section, title, page, articles[]}]}` — one entry
  per FAQ question / guidance section, mechanically (no editorialising). Reuses
  `lib/llm.complete` (extended with `max_tokens`, raised to 32k — gpt-oss is a
  reasoning model whose chain-of-thought exhausts the endpoint's 4096 default
  before it emits the answer), the `_validate` + single-retry harness, the
  model-via-config plumbing.
- *Fine-grained targets* (**decision O12**). The map spliced into the prompt is
  not just the article list but every targetable node — whole article (`6`),
  sub-article (`2.21`, `6.2.a`), and recital (`recital-15`) — each led by the
  exact anchor token the model copies back into `targets`. The grammar is
  `eurlex.structure.subarticle_key`/`anchored_blocks`, shared with the renderer, so
  an accepted link's key is always a node the renderer actually mints. The prompt
  drives the model to the *most specific* node: a FAQ answer about two definitions
  links to `["2.21", "2.22"]`, not to article 2 as a whole; recitals are
  first-class targets (the FAQ leans on them for purpose/scope).
- *Output*: a **`.ann` sidecar** next to the kommentar artifact,
  `{"guidanceLinks": {anchor: [{label, href, desc, section}]}}` — the per-node
  guidance shape the rail already renders, so an accepted link promotes for free.
  `label` names the source + its section reference ("Frågor och svar om dataakten,
  question 8"); `desc` is that section's title (the FAQ question), rendered after
  the link as `link: question`. Kept **separate** from the hand-edited markdown:
  the markdown is the human-authored editorial prose + curated `## Externa länkar`;
  the `.ann` is the AI-created, then human-corrected mechanical link layer
  (**decision O10**, same split as eurlex's `.ann` editorial layer).

**Robustness decisions.**
- *Section reference is the durable locator, not the page* (**decision O11**). A
  FAQ question number (`question 8`, `fråga 25`) survives a guidance revision; a
  page number drifts. The guidance doc's own `section` — carrying the doc's own
  kind word, following its structure — is kept as a **first-class field** (and
  named in the rendered label), so the link stays human-dereferenceable even when
  the `#page=N` deep link rots.
- *Pages located, not trusted*. The model miscounts pages, so the `#page=N`
  anchor is computed deterministically by matching the (alnum-normalised) section
  title back into the page-marked text — exact even across straight-vs-curly
  quotes.
- *No hallucinated anchors*. `_validate` rejects (and feeds back on the retry)
  any cited target absent from the act's real anchor set (articles, sub-articles
  and recitals).

**Risks.** Hallucinated/over-broad links (mitigated: anchor validation, "omit if
uncertain" in the prompt, `.ann`-only — never auto-published); PDF extraction
quality; cost/latency (opt-in, named ids only, same as every ai-* action).

**Render: wired.** `_article_guidance_index` loads each kommentar artifact's
`.ann` sibling and merges its `guidanceLinks` into the same `(law_uri, anchor)`
rail index the curated per-article guidance uses, so an AI-proposed link renders
in the annotated node's rail exactly like a hand-authored one. A sub-article
(paragraph/point) carries no structural id of its own, so `render_eurlex` now
synthesises its `2.21` citation anchor + rail entry when *something* targets it —
the editorial recital layer **or** a guidance/commentary link — otherwise it stays
an anchorless leaf. A full/forced `generate` folds the layer in (the coarse
`generate_watermark` includes the kommentar `.ann` files, so the corpus-wide
generate sees them).

**Storage.** The kommentar artifact is filed under its host source, reusing that
source's path transform — `kommentar/artifact/eurlex/2023/32023R2854.json`,
`kommentar/artifact/sfs/2009/400.json` (`layout.kommentar_host`) — mirroring the
content repo's `commentary/<source>/…` layout, so commentaries on different
sources can never collide on one flat name. (The path move surfaced a latent
`catalog.rebuild` bug: a document's identity is its uri, not its path, so a moved
artifact must be re-indexed under the new path and not pruned as a "vanished" old
path — fixed + regression-tested.)

**Acceptance** — *met*. `lagen kommentar ai-annotate 32023R2854` (the Data Act)
downloads the Commission's Data Act FAQ PDF declared in
`commentary/eurlex/2023/32023R2854.md`, and writes a `.ann` mapping ~54 fine-grained
targets (whole articles, sub-articles like `2.21`/`2.22`, and recitals) to their
FAQ questions — e.g. FAQ *question 8* ("What determines whether a connected product
falls in scope…") lands on definitions `2.21` and `2.22`, not on article 2 as a
whole. Each link carries its verbatim question, its `question N` section reference,
and a title-located page deep link; every target is validated to exist in the act.
The rendered page shows each link in its node's rail as
`[source, question N](…#page=N): the question`. `test_wiki.py` covers the
frontmatter mapping-list parse, the fine-grained act map + anchor set, the anchor
validation (accept + reject), the deterministic page lookup, the link/label
assembly, and an end-to-end render of sub-article + recital guidance.

**Dependencies:** Steps 1–3 (it emits a render-ready Step-3-shaped layer) +
`ai-annotate` infrastructure.

## 5. Cross-cutting concerns

- **Testing/golden.** Step 1's artifact-equality diff is the linchpin (local
  gate; `test_wiki.py` is the CI surrogate); later steps add fixtures
  (`test/test_wiki.py`) and, where they touch the citation engine or relate,
  golden re-validation.
- **Sequencing.** Strictly 1 → 2 → 3 → 4; each is independently shippable.
- **Rollback.** Step 1 has landed (the markdown sibling repo is the source of
  truth); `lib/wikitext.py` is retained as the converter/diff reference, so the
  conversion remains re-runnable from the DB.
- **Docs.** README documents the sibling checkout + content layout; `REWRITE.md`
  reflects the markdown source.

## 6. Open questions

Step 1 closed O1/O2/O3/O6/O7; Step 2 closed O4/O5; Step 3 closed O8; Step 4
closed O9/O10/O11.

- **O1 — History depth.** *Done:* full per-revision history replayed from the host
  MediaWiki SQLite DB (1.39 MCR schema: wikitext in `text.old_text` via
  `slots`/`content`, authorship in `actor`), one git commit per revision (5,245
  commits). C1 confirmed.
- **O2 — Markdown parser.** *Done:* hand-rolled minimal parser (`lib/markdown.py`,
  no dependency), twin of `wikitext.py`.
- **O3 — Redirects.** *Done:* converted to `aliases:` frontmatter, resolved at
  relate via the `concept_redirect` table → folded onto the canonical concept (77
  aliased concepts).
- **O4 — Act-level guidance placement.** *Done:* the **document-level rail panel**
  (key `""`, "Externa länkar" under "Om dokumentet"), replacing the empty-rail
  placeholder — not a top-of-body section. Named generically ("Externa länkar")
  so it serves any external resource, not only official guidance. Per-article
  guidance reuses the same rail mechanism in Step 3.
- **O5 — Guidance provenance.** *Done:* render-only (no `guidance` catalog table);
  external resources carry no inbound edge, so relate is unchanged.
- **O6 — Content repo name/location.** *Done:* sibling `../lagen-wiki` (not a
  submodule); will also host the Step-2/3 guidance annotations (same repo).
- **O7 — Author identity mapping.** *Done:* synthesised `Name <slug@lagen.nu>`
  from the MediaWiki actor name (no external table); bare IPs kept as-is.
- **O8 — EU sub-article/recital anchor form.** *Done:* one **dotted** grammar
  everywhere — `## Artikel 3.4` → `3.4`, `## Artikel 5.2 a` → `5.2.a`,
  `## Skäl 13` / `## (13)` → `recital-13` — shared by the renderer, the guidance
  `.ann` and the eurlex editorial `.ann` (the parenthesised `5(2)` form is retired;
  `Editorial` normalises any legacy key on load). See O12. Recitals were made
  anchorable without an editorial `.ann` so they are commentable like articles.
- **O9 — How the AI pass learns which PDF to read.** *Done:* hand-declared in the
  annotation's `guidance:` frontmatter (`{title, url, pdf}`); the direct `pdf:`
  link is authored, not guessed — a guidance doc is short-lived and its URL is
  not derivable from the act. (Frontmatter parser gained block lists of mappings.)
- **O10 — AI output format/location.** *Done:* a `.ann` sidecar next to the
  kommentar artifact (`{"guidanceLinks": {anchor: [{label, href, desc, section}]}}`),
  the per-node guidance shape the rail renders — kept **separate** from the
  hand-edited markdown (the human-edited vs AI-created+human-corrected split,
  mirroring eurlex's `.ann`). The artifact is filed under its host source
  (`kommentar/artifact/<host_source>/<host_relpath>.json`, `layout.kommentar_host`)
  so cross-source basefiles can't collide. Render weaving done: merged into
  `_article_guidance_index` so the links show in the annotated node's rail.
- **O11 — Durable guidance reference.** *Done:* the guidance document's own
  `section` (a FAQ question number, carrying the doc's own kind word — "question 8")
  is the human-dereferenceable locator and a first-class field named in the link
  label; the `#page=N` deep link is a located-not-trusted convenience (matched back
  from the section title, since the model miscounts pages).
- **O12 — Link granularity + one sub-article grammar.** *Done:* the model maps to
  fine-grained targets, not only whole articles — sub-articles (`2.21`) and recitals
  (`recital-15`). The sub-article id grammar is now **dotted everywhere**
  (`eurlex.structure.subarticle_key`, the single source): the renderer's node id,
  the wiki commentary headings (`## Artikel 5.2 a` → `5.2.a`), the guidance `.ann`
  keys and the eurlex editorial `.ann` `articleToRecitals` keys all agree, so every
  layer lands on the same node. This reconciled the earlier split where render/
  editorial used a parenthesised `5(2)` while commentary used dotted `5.2`; the
  parenthesised form is retired (the eurlex `.ann` files were migrated, the prompt
  emits dotted, and `Editorial` still normalises any legacy `5(2)` on load). The
  renderer synthesises a sub-article's citation anchor + rail entry when a guidance
  (or editorial) link targets it; `_host_anchors` now includes the dotted
  sub-article anchors so commentary/guidance on a sub-article validates.
