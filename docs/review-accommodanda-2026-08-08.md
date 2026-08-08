# Code review: `accommodanda/` — consistency · reuse · dead code · minimization

2026-08-08. A whole-codebase pass over the rebuilt pipeline (~65,600 lines,
199 files) looking for inconsistencies between sources, missed reuse of
`lib/`, dead code, and general minimization. Four independent audits
(duplication, dead code, lib/api minimization, cross-source consistency)
were run and their findings merged, de-duplicated and spot-verified against
the working tree and the live corpus.

## Status (updated the same day, after the fix pass)

A same-day orchestrated fix pass applied every finding whose implementation
was clear. Unless a finding is listed under **OPEN** below, it is **FIXED**
in the working tree (uncommitted), verified by the full suite plus
per-change proofs (byte-identical artifact/render/config-value comparisons
where output identity was required, live-catalog checks for the rest).

**FIXED highlights**: 1.1/1.2 (`.grund.json` leak + `layout.artifacts()`
archive exclusion; all seven hand-globs collapsed), 1.3 (registry test,
`test/test_registries.py`), 1.4 (`connect_ro` in ops/browse), 1.5 (patch
application *wired* for icrc/untc/icc, mirroring hudoc/coe, rather than
dropping the inputs), all of 1.6, §2.1 (all nine deletions), §3.1–3.5
(including `compress.read_json` at 91 sites — the audit's 56 counted only
the `read_text` variant — and `lib/util` gaining `MONTHS_EN`,
`english_date`, `fold_swedish`), §4.1/4.2/4.4/4.5 (new `lib/artifact.py`;
rs/edpb storage-walk and the scope dispatcher into `lib/harvest.py`;
`lib/page.document_body`), §5.1/5.2, §6.1 (label tables derived from
`facets.SCHEMES` — three user-visible label strings changed to the SCHEMES
wording: JO's feed label, rs parentheticals restored, förarbete feed list
grown from 8 to 11 kinds), §6.4/6.5, 7.1 (the `parse(basefile, root)`
rename; `parse_record` now means only the record-dict shape).

**§4.3 FIXED (same day, after measurement)**: the union of the two rules
(paragraph ends with title, or paragraph is the start of the title) is now
one shared `lib/util.drop_leading_title_echo`, with edpb's guards kept.
Measured over all 311 rs+edpb documents before adoption: the union removed
six real title echoes the single rules each missed (5 migr, 1 wp/259) and
no genuine content; re-measured after, implementation ≡ union. Regression
tests lock both newly-caught shapes in.

**§6.2 FIXED (same day, after measurement)**: the merge claim was measured
before merging — and corrected. Sampling every source against the live
catalog showed the value-identical set is 7 sources, not the shapes-based
count: foreskrift (labels interpolates the designation into the title),
icrc and untc (labels uses curated abbreviations "GK I"/"CRC" as the id)
differ in *values*, so they stay bespoke beside dv/eurlex/hudoc/icc, each
with a comment stating its exact difference. The 7 identical sources plus
kommentar now share one `_labelled_document` builder that consumes
`labels.document_labels` (one authority; only `kind` stays per-source, as
data). kommentar got a deliberately inert `labels` entry — it is never a
page of its own and no rail prints its name — which also fixes the live
gap where its catalog `descriptive` column held the raw uri tail
("kommentar/1810:0926"). Verified: the new `document_row` reproduces every
sampled live row (label, title AND kind) for all 15 sources, 0 mismatches;
358 scoped tests pass. The descriptive column self-corrects on the next
relate (already pending).

**§6.3 FIXED (per ruling)**: new `api/reads.py` is the one read path — all
six payloads (search, documents, document, inbound, outbound, sources) are
built there and both faces serialize its dicts. A down search cluster is now
a visible error on both sides (REST: 503 with a plain reason instead of a
raw 500; MCP: a tool error instead of the silent degrade to citation-only —
the `note` field went with the mechanism). The two inbound filters turned
out to be orthogonal, so both faces got both: `scope` (tree/exact — what
the question covers) and `source` (who is citing), with `total` counted
after both filters and `by_source` before `source`. api/README.md's
one-code-path claim is now true and its prose updated; tests cover the
503, the tool error, and both filters on both faces.

**§7.2 REVISED AND FIXED (per ruling; artifacts re-key on the session-end
rebuild)**: the original "unify onto `date`" direction was wrong — the
semantic review showed most date keys name *different events* and must stay
distinct (`beslutsdatum`, `antagen`, `utfardandedatum`, `utkomFranTryck`,
`ikrafttradandedatum`, treaty opening/adoption/conclusion; föreskrift alone
carries three dates). `catalog.document_date` is the deliberate one-date
projection for ordering, not a smell (docstring now says so). What WAS the
same fact, now renamed: hudoc/icc `date` → `avgorandedatum` (both rendered
"Avgörandedatum"; matches dv), rs `doktyp` + forarbete top-level `type` →
`doctype` (same role as the six `doctype` sources; also removes forarbete's
collision with the universal node discriminator `type` — note icc and
foreskrift keep their *broad-class* top-level `type`, a different role),
and dv footnote `num` → `mark` (a marker need not be a number;
`footnote_items` lost its `key` parameter). The search year facet now reads
the `document_date` projection instead of the raw key. Left alone on
purpose: block-number `ordinal`/`punkt`/`num` (same role but bound into
each source's citation-anchor grammar) and metadata casing (cosmetic; the
international camelCase is the registries' own vocabulary).
- §7.4 site-page reaping, §7.5 folkrätt test-coverage asymmetry.
- §2.2 test-only symbols (several deliberately parked; `Treaty.unid` is a
  data decision).
- §3.5's `%PDF` sniff conversion — skipped on judgment: at a boolean gate
  the literal magic-byte check reads better than
  `document_extension(data) != ".pdf"`; the audit itself rated it
  completeness-only.
**browsable flag — FIXED (per ruling)**: `facets.UNGENERATED` (coe/icrc/
untc/icc — the folkrätt landing lists them in full) + `facets.browsable()`
now declare, next to the schemes, which of them become browse-page trees;
`browse.py` derives its skip set from that instead of stating its own, and
a registry test locks the coherence. The API keeps answering `/browse` for
the four (the scheme exists and serves search buckets); what changed is
that the site generator and the scheme table can no longer disagree.

**untc `source_url` — WITHDRAWN (verified 2026-08-08)**: the finding was
wrong. All 14 untc rows and artifacts carry the full MTDSG status-page URL,
minted from the one shared `DETAIL` template in untc/model.py (whose comment
exists precisely so downloader and artifact cannot drift). The raw-HTML
download tree not embedding the URL is the same principled
reconstruct-from-identity situation as hudoc/dv/sfs.

**`_document_description` field-driven — DROPPED (measured, ruled 2026-08-08)**:
only 15 of 255 rs documents carry a `sammanfattning` (IMY 5, KKV 10; FK/migr/
kfm/FI none), it is the agency's *web-page ingress* scraped at download — not
document content — and at least one is stale process text, not a summary. The
dv-only branch stays: dv's description is intrinsic (the referat's own summary
line). If IMY/KKV ingresses become worth showing, that is a small deliberate
feature, not a refactor.

**Operational notes**: the catalog still holds the 1,650 `/grund` rows
until foreskrift is re-related + re-dumped (covered by the pending
`lagen all rebuild`); adding `lib/artifact.py` to six sources' CODE tuples
re-stales their parses once (recipe-version invalidation, expected).

The headline: the codebase is in good shape on the things the conventions
police mechanically (no unused imports, no unreachable code, no `os.path`
drift, no raw SQL in `api/`, `lib/` never imports a source). The findings
cluster where no guardrail looks: registry tables in `build.py` that were
copy-pasted instead of derived, `lib/` helpers that exist but are
re-implemented inline at their call sites, and naming drift in the
per-source protocol that only shows when you read all fifteen sources
side by side.

---

## 1. Correctness findings (found while looking for duplication)

### 1.1 HIGH — foreskrift `.grund.json` sidecars are catalogued as corpus documents — live data bug

`build.py:3465-3466` hand-globs `*/*.json` for the foreskrift artifact
list instead of calling `layout.artifacts("foreskrift")`. The glob matches
the `.grund.json` base-version sidecars that `layout._is_document_artifact`
(`lib/layout.py:289-292`) exists specifically to exclude. Verified against
the live corpus:

- 1,650 `.grund.json.br` files under `site/data/artifact/foreskrift/`
- 1,650 spurious rows in the catalog's `documents` table (12.8 % of the
  12,899 foreskrift rows), e.g. `https://lagen.nu/afs/2023:10/grund`
- 27,658 `links` rows whose `from_uri` is a `/grund` URI

Consequence: every föreskrift's base version is related, indexed and
dumped as a second document, and its citations are counted twice into the
inbound totals of the SFS paragraphs it is issued under. Browse counts and
stats are inflated the same way.

**Fix:** use `layout.artifacts("foreskrift")` (one line), then re-relate
and re-dump foreskrift. Two audits found this independently and both
verified it against the catalog.

### 1.2 HIGH — the hand-globs exist because `layout.artifacts()` is incomplete

`_is_document_artifact` excludes the sidecars but `layout.artifacts()`
(`lib/layout.py:309`) still recurses into `artifact/sfs/archive/**`
(31,213 archived consolidation files, verified on disk). `build.py:3450`
states this constraint in a comment — and the workaround (hand-globbing)
was then copied to 7 of 15 `ARTIFACTS` entries, of which five (begrepp,
eurlex, avg, rs, edpb) are provably equivalent to `layout.artifacts()` and
one (foreskrift) introduced 1.1. `build.py:3457-3459` even carries a
comment forbidding exactly this.

**Fix:** teach `_is_document_artifact` (or `artifacts()`) to skip the
`archive/` subtree, then collapse all `ARTIFACTS` entries to
`layout.artifacts(name)`. ~18 lines, and the drift class is gone.

### 1.3 MEDIUM — no test ties the source registries together

`SOURCES` (18 entries), `ARTIFACTS` (15), `SOURCE_RENDERERS` (14) and
`facets.sources()` (14) each encode the source set separately; the
`{remisser, site, stats}` exceptions live in free-text comments in three
places (`build.py:3067-3069, 3290-3294, 3318-3321`). No test asserts the
registries agree. A parametrized `SOURCES × ARTIFACTS × SOURCE_RENDERERS`
completeness test would have caught 1.1 and would catch the next missing
entry. Related contradiction: `browse.py:456` skips
`("kommentar","coe","icrc","untc","icc")` from generation while
`facets.sources()` includes coe/icrc/untc/icc — four sources have a
registered facet scheme, are served by `/api/v1/browse`, and are never
generated. A `browsable` flag on the scheme would make one authority.

### 1.4 MEDIUM — `api/ops.py` bypasses `catalog.connect_ro`, skipping column migrations

`api/ops.py:109,120` (and `browse.py:56`) open the catalog with raw
`sqlite3.connect("file:…?mode=ro")`. `catalog.connect_ro`
(`lib/catalog.py:301-315`) exists to run the additive `ALTER`s first;
`source_stats` selects `art_size`, a migrated column. `/ops` can be a
process's first catalog touch, so on a pre-migration catalog the health
page 500s — the one page whose job is to load when things are broken.
Zero-line fix.

### 1.5 MEDIUM — patch inputs are declared for three sources that never apply patches

icrc, untc and icc declare `_patch_input` as a parse-freshness input
(`build.py:2327, 2365, 2406`) but no code in those packages applies a
patch (`icc/parse.py:58` passes `patch_key=None` explicitly). A patch
authored for them would re-stale the parse and then be silently ignored.
Harmless today (`accommodanda/patches/` holds only dv/sfs/eurlex), but a
trap. Adjacent doc drift: `README.md:1034-1075` omits edpb and rs from
the patchable list (both are wired and `mkpatch`-authorable) and hudoc/coe
(wired and applied, but absent from `patchsource._INTERMEDIATE`).

### 1.6 Smaller confirmed defects

- **Dead politeness delay:** `icrc/download.py:110` and
  `untc/download.py:50` accept `delay=0.3` and never sleep (`time` is not
  imported); `build.py` passes `POLITENESS` into a no-op. Every other
  source sleeps.
- **`--full` doesn't exist:** the CLI has no `--full` argument, yet
  `build.py:1276` tests `"--full" in basefiles` (unreachable — argparse
  rejects the token first) and the `notes` strings at `build.py:1440,
  2453, 3081` document `--full` where the real flag is `-f/--force`.
- **forarbete bypasses `lib/net`:** `forarbete/download.py:140-147`
  hand-rolls `session.get` + one retry on 400, losing `net.request`'s
  backoff, `Retry-After` handling and the harvest deadline. The only
  downloader of 18 outside `net.request` (wiki/guidance_discover's bypass
  is documented as deliberate; `wiki/annotate.py:76` is a bare
  `requests.get` and should also route through net).
- **sfs model annotation is wrong:** `sfs/model.py` declares
  `date | str | None` where the producer mints `datetime`
  (`sfs/tokenizer.py:75`) and consumers `isinstance(value, datetime)`.
  Works only because `datetime` subclasses `date`.
- **`sfs/download.py:53`** `PAGE_DELAY = 1.0` contradicts its own argparse
  default `0.3` at `:299`.
- **`untc` records store no source URL at all** — reconstructable only
  from `model.DETAIL %`; the other URL-less sources are covered by the
  `layout.source_url` fallback, untc's is genuinely lossy.
- **`build.py:1616-1619`** is the file's only catch-to-log
  (`dv_namedcases`): invisible to `runs`/`errors` where every other broad
  catch records an error segment.

---

## 2. Dead code

Verified by AST indexing of every load-context name, string literal,
template token and keyword argument across `accommodanda/`, `test/`,
`tools/`, templates and `pyproject.toml`. No unused modules, no
unreachable branches, no unused imports anywhere; the 35 vulture hits on
`lib/lagrum.py fmt_*` are all grammar-dispatched via
`getattr(self, 'fmt_' + node.data)` and each has a matching production.

### 2.1 Confirmed dead (safe to delete)

| symbol | where | note |
|---|---|---|
| `KKV_ARENDELISTA_PAGE` | `avg/download.py:238` | sibling `KKV_TAKE` is used |
| `RE_TOC_PAGENO` | `forarbete/tabell.py:29` | sibling TOC regexes used |
| `RE_LEAD_KAP` | `foreskrift/structure.py:21` | sibling `RE_LEAD_PARA` used |
| `RE_WIKILINK` | `lib/wikitext.py:25` | superseded by `RE_INLINE_LINK` |
| `TITLE_WITHOUT_BASEFILE` | `sfs/register.py:76` | ported for a legacy check that never was |
| `_by_alpha` | `lib/facets.py:49` | 0 uses in the strategy registry; a no-op duplicate of the default sort |
| `SearchIndex.doccount` | `lib/search.py:812` | only "callers" are a legacy whoosh test and an unrelated fabric task |
| `WalkResult.newest_date` | `lib/harvest.py:197` | write-only field; no caller of `walk()` reads it |
| `sitemap_enumerate` | `foreskrift/harvest.py:571` | already documented as dead in `docs/conventions.md:211` |

### 2.2 Live only from tests or tools (decide, don't just delete)

- `eurlex/parse.py:108 load_formex` — superseded by the patch-aware
  `_formex_roots`; genuinely redundant.
- `sfs/register.py:127 forfattningstyp` and `:390 lfragment` — ported,
  caller never ported.
- `forarbete/legacy_formats.py:106 dokumentstatus_meta` — documented as
  an entry point, never wired.
- `dv/parse.py:277 decision_date_from_text` — used only by
  `tools/golden_dv.py`.
- Deliberately parked, keep: `lib/catalog.py:611 norm_level` (the
  norm-chain layer awaits its editorial reader), `sfs/extract.py:67
  sanitize_body` (already a documented cautionary tale),
  `dv/parse.py:172 parse_innehall` (self-described test seam).
- `icrc/model.py:72 Treaty.unid` — write-only in code, but serialized
  into the published artifact JSON; removing it changes artifact content,
  so it is a data decision.
- `stats/`: `Measure.kind == "table"` and `Measure.columns` have zero
  producers; `note=` is never passed. `stats/compute.py:945 jobs` is
  never passed by its sole caller.

---

## 3. Reuse: lib/ helpers that exist but aren't used

Ranked by (lines saved × copies × drift risk).

### 3.1 `lib.harvest.write_record` / `store_record` re-implemented at 13 call sites in 8 sources

`lib/harvest.py:351` is exactly
`compress.write_download(path, json.dumps(record, ensure_ascii=False, indent=2))`.
Only avg imports it. coe, hudoc, icrc, icc, forarbete (×4 files),
foreskrift (×3 sites), remisser all hand-write the same expression —
currently in step by luck, which is precisely the four-flags-drift the
docstring at `build.py:246-255` warns about. `hudoc/download.py:118-127`
and `coe/download.py:104-122` additionally re-implement `store_record` /
`record_unchanged` outright (load, compare, write-if-changed). ~30 lines,
8 sources, a real drift hazard removed.

### 3.2 `untc/download.sync` hand-rolls the `lib.harvest.walk` loop

`untc/download.py:50-67` re-implements the enumerate/count/limit loop
that `harvest.walk` documents as its own use case, and loses per-document
error isolation (a bad doc aborts untc's whole sync where `walk` counts
and continues), `Skip` handling, and the correct `--limit` semantics.
~14 lines → one `walk()` call, matching its four sibling folkrätt sources.

### 3.3 `json.loads(compress.read_text(p))` — 56 sites, no helper

The single most repeated expression in the package (30 files; build.py
alone has 11). A guarded variant (`… if compress.exists(p) else None`)
repeats at 5 more sites. **Fix:** `compress.read_json(path)` next to
`read_text`/`read_bytes`.

### 3.4 `list_basefiles` flat-glob — 5 byte-identical copies, and four signatures

`coe/hudoc/icc/icrc/untc` carry the identical stem-glob (three with the
same trailing comment). Meanwhile the name has four signatures across the
tree — `(root)`, `(root, fs)`, `()`, plus four sources whose listing
lives in `build.py` instead of the source package, three of those via a
*fourth* same-named helper `compress.list_basefiles`. Worst case:
remisser has two `list_basefiles` returning different units (cases vs
answers). **Fix:** `compress.list_stems(root, pattern)` for the five
copies; `<source>/download.py:list_basefiles(root)` as the canonical
home.

### 3.5 Smaller reuse misses

- **Month tables:** `lib/util.MONTHS`/`swedish_date` exist; Swedish
  tables re-declared in dv (×2), edpb, avg, `stats/compute.py`; English
  tables + the same "D Month YYYY → ISO" function twice (untc, icc).
  Widen `lib/util` to `MONTHS_SV`/`MONTHS_EN` + `en_date()`.
- **`str.maketrans("åäö", "aao")` ×4** — three inside foreskrift alone,
  plus `lagrum.SLUG_TRANS`. One `lib/util.fold_swedish()`.
- **PDF magic-byte sniff** — `lib/util.document_extension` exists;
  `data[:4] != b"%PDF"` re-appears at 7 sites (3 inside forarbete).
- **`Path.exists()` on compressed trees:** `forarbete/soukb.py:110,114`
  and `propkb.py:91` hardcode compression policy that `compress.exists`
  owns.

---

## 4. Duplication between sources (rule:second-use-goes-to-lib)

### 4.1 rs ↔ edpb share a copied ~90-line storage-and-walk block

`rs/download.py:172-268` and `edpb/download.py:109-192`: `basefile()`,
`pdf_path()`, the non-PDF guard, `store()`, `_select()`, `_walk()` —
`pdf_path` and `store` byte-identical down to the docstring sentence, the
`_walk` docstrings sharing three verbatim paragraphs. The one drift
(bare records vs `(record, fetch)` pairs) is parameterizable. ~70 lines.

### 4.2 `to_artifact()` body projection — five copies in two clusters

- Cluster A (citation-scanned rubrik/stycke + footnotes): avg
  (`model.py:120-176`), rs (`:123-166`), edpb (`:145-188`) — avg and rs
  byte-identical, edpb drifted only by its `punkt` anchor.
- Cluster B (plain rubrik/stycke with de-duplicating anchor): hudoc
  (`model.py:62-81`), icc (`:75-92`) — verbatim except one branch; the
  anchor-dedup counter has a third standalone copy at `coe/parse.py:42-44`.

This is the artifact node convention — the one place drift must not
happen. A small `lib/` helper (`body_nodes`/`footnote_nodes`) saves
~90 lines across 5 sources. Also inside cluster A: `avg/model.py:139-165`
builds metadata with 14 consecutive `if X: metadata[k] = X` where rs and
edpb use a tuple loop for the same job — same idiom, one copy 3× longer.

### 4.3 Title-echo folding duplicated rs ↔ edpb, with diverged behaviour

`rs/parse.py:226-246` and `edpb/parse.py:243-266`: byte-identical fold
function under two names, then two *different* correctness envelopes for
the same "strip the repeated title" cleanup (`endswith` vs `startswith` +
guard). Two copies is below the usual bar, but the drift here is
behavioural in a shared cleanup step.

### 4.4 Renderer preamble — six verbatim copies

The `Toc()/Rail()/render_node(...)/rail.add_document()` opening is
identical in avg, rs, edpb, hudoc, icc, wiki render.py (loop-variable
name aside). A `lib/page.py: document_body(art, site)` helper would
absorb it; dv/forarbete/eurlex/foreskrift/sfs have genuinely custom
walkers and stay as they are.

### 4.5 Per-scope sync dispatcher — three verbatim copies

`avg/download.py:1456-1465`, `rs/download.py:866-874`,
`edpb/download.py:475-488` all iterate scopes, derive `scoped_only`, and
collect `{scope: (seen, new)}`. Honest count: a minority pattern
(3 of 14), but a verbatim one. foreskrift's is genuinely different —
leave it.

### 4.6 Checked and *not* duplication (leave alone)

- `flatten(structure)` ×4 (lib/eu_structure, dv, forarbete, foreskrift):
  each wants a different head projection; the drift is design, not decay.
- Per-source `nest()`: four genuinely different structural grammars.
- Per-source `Block`/`Fotnot` dataclasses: deliberate model ownership.
- `lib/net`, `lib/harvest.walk`, `lib/compress`, `lib/pdftext`: uniformly
  used; the shared machinery already got extracted where it should be.
- `number_slug` in `rs/agencies.py` vs `edpb/series.py`: same name,
  *different* behaviour — a readability trap, not duplication.

---

## 5. build.py: copy-paste that should be a factory

### 5.1 Eight near-verbatim source registration blocks (~105 lines)

The five folkrätt blocks (`build.py:2249-2437`: hudoc, coe, icrc, untc,
icc) and the avg/rs/edpb trio (`:2642-2903`) repeat the same
inputs/parse_run/harvest/Source quadruple; the only drift is the counted
noun in the banner ("changed"/"fetched"/"stored"/"new" for the same
tally). The one-line `X_parse_run` is identical for 10 sources. A
`simple_source(...)` factory inside build.py (lib/ must not know sources)
collapses it. Also: 18 wrappers around `layout.artifact(name, bf)` — 13
two-line named functions plus 5 inline lambdas — are `functools.partial`;
the `--only needs exactly one <scope>` guard is written 5×; the
harvest-dispatch block appears twice byte-identically
(`build.py:3783-3800` ≡ `4870-4891`).

### 5.2 config.py resolvers — largest single mechanical saving (~110 lines)

13 resolvers, 3 patterns: 8 structurally identical string resolvers
(~15 lines each), 2 byte-identical bool resolvers, and 3 *different* int
implementations. One `_resolve_str(doc, key, env, default, post=None)` +
`_resolve_bool` covers them. No config key is dead — all 17 have readers.

---

## 6. lib/ and api/ internal findings

### 6.1 Label tables duplicated and already drifted

`lib/render.py:97-137` carries three per-source label tables duplicating
`facets.SCHEMES` — the avg copy has *already drifted* ("Riksdagens
ombudsmän" vs "Justitieombudsmannen (JO)"), and the same file hand-rolls
`SELECT DISTINCT kind` three times while using `facets.tree` correctly at
`:212`. `facets.SOURCE_LABELS`' own comment documents this exact failure
mode having happened before. Similarly `lib/page.py:448-462
INBOUND_GROUPS`: 11 of 16 labels are literal copies of `SOURCE_LABELS`
(import graph verified acyclic — page may import facets); an ORDER list
plus a 5-entry override dict ends the third copy of the drift hazard.

### 6.2 `catalog.document_row` + `labels.document_labels` — two parallel per-source dispatches

Both are invoked on the same artifact at the same call site
(`lib/catalog.py:1001-1002`); for 7 of 15 sources the extraction is
identical (~90 lines). Collapsible, but `documents.label/.title` are read
by 14 SQL sites — needs column-by-column verification, not a blind merge.

### 6.3 REST and MCP rebuild the same six payloads

`api/app.py` and `api/mcp.py` independently serialize documents,
document, inbound, outbound, sources and search. `api/README.md` claims
one code path; false today, and already diverged (REST `/search` 500s on
`OpenSearchException` where MCP degrades to citation-only; REST has
`scope=exact`, MCP a `source` filter). One `api/reads.py` both faces
call. ~60 lines plus the divergence class.

### 6.4 Smaller api/ items

- The read-only-connect preamble ×5, `CATALOG` path declared ×4,
  `CATALOG.exists()` ×7 → one `api/db.py`.
- Commit-as-editor implemented twice (`api/editcart.py:155-171`,
  `api/patch.py:130-152`) — `lib/git.py` should own `commit_as`.
- The facsimile PNG response written twice byte-for-byte
  (`api/app.py:862-875` ≡ `966-974`); `facsimile.cached_page`/`cached_region`
  differ only in renderer.
- `api/ops.py:104-124`: `_catalog_counts` ≡ `_source_stats`, and each
  `/ops` load runs an extra query for a number it already holds.
- `api/patch.py` reaches into `patchsource._INTERMEDIATE` and re-implements
  its guard and `current()` — give patchsource a public predicate.
- `api/auth.py:148-154`: `json.JSONDecodeError` is a subclass of
  `ValueError` (dead half of the tuple); the isinstance ladder re-checks
  claims that the HMAC already proves we minted.
- Ten `.get(key, default)` in ops/app on keys the writers always emit —
  index directly so a schema change fails loudly.

### 6.5 lib/ display rules for forarbete hardcoded

`lib/page.py` has four `source == "forarbete"` branches
(`:534, 549, 621, 625`) and a forarbete-only `forarbete_pinpoint`
(`:500-516`), in a module that already tables per-source sets
(`PARAGRAF_SOURCES`, `SUBTITLED_SOURCES`) four lines away. The remaining
per-source branches in facets/feeds/render/pins are at or under the
4-of-14 threshold and should stay; `layout.relpath`'s 18-way dispatch is
a complete path grammar and stays too.

---

## 7. Protocol and naming inconsistencies

### 7.1 The parse entry point has three names, one of them overloaded three ways

- `parse(basefile, root) → artifact dict`: hudoc, coe, icrc, untc, icc
- `parse_record(basefile, root) → artifact dict`: edpb, avg, rs, remisser
- `parse_record(record_dict, root) → model object`: forarbete, foreskrift
- and `parse_record` is *also* a private helper with yet other signatures
  in hudoc and coe; site's `artifact(root, basefile)` reverses everyone
  else's argument order.

Canonical: `parse(basefile, root) → artifact dict` (majority, and the
shape `build.py`'s runners want). Rename-only, mechanical, removes a
genuine footgun.

### 7.2 Artifact key drift for identical semantics

`lib/catalog.py:902-916 document_date` is an 8-branch fallback over key
names for one concept — the cost is already being paid in lib/. Top-level
date is `"date"` (7 sources) vs `"avgorandedatum"` (dv) vs absent (5);
doctype is `"doctype"` vs `"doktyp"` vs `"type"`; footnotes key `"num"`
vs `"mark"`; ordinal `"ordinal"` vs `"punkt"` vs `"num"`; metadata casing
mixes camelCase into the Swedish sources (`avgjordAv`, `ersattAv`).
Unify the date/doctype keys when a corpus reparse is already scheduled
(one is pending); the number-key drift is principled (each source's
real-world identifier) — leave it.

### 7.3 Model conventions

Uniform where it matters (all 71 model dataclasses bare `@dataclass`;
dates uniformly `str | None` with the rationale written down in
remisser). Real drift: `title` required/defaulted/optional/renamed across
sources; `source_url` required field / optional field / property / absent;
`level` defaults 1 / 0 / None / required. Where the projection lives
(`to_artifact` on the class ×7, free function in parse.py ×3, `asdict`
×2) is arbitrary with no rule.

### 7.4 site and stats opt out of the render protocol

Both are written outside `generate_site` (`build.py:4084-4232`) — no
manifest entry, no freshness signature, no reaping. This is the mechanism
behind the known "deleting an /om page leaves the generated HTML serving
200". Defensible for stats (one fixed page); site needs an artifact→page
mapping generate can plan and sweep.

### 7.5 Test coverage asymmetry

The five folkrätt sources have 9-11 test functions each (single-document
smoke) against sfs's ~397/forarbete's ~268; `test_labels.py` covers 10 of
14 renderers (missing avg, rs, edpb, foreskrift); wiki/begrepp (81 tests)
and stats have zero fixtures. And the registry-completeness test from 1.3
is the missing guard with the most evidence behind it.

---

## 8. Checked and clean

For the record, these were audited and came back clean: no unused
modules or unreachable code anywhere; no unused imports; every `cmd_*`
CLI command wired; no `os.path` (one legitimate `commonpath`); stdlib
`json` uniformly; no raw SQL in `api/`; all compressed reads through
`lib/compress`; all downloaders through `compress.write_download`;
exception discipline in `lib/` good (31 `except`, all narrow or
rule-annotated); config keys all live; `structure.py` placement
principled (exists exactly where a source rebuilds a tree); dataclass
options uniform. The ~75 public `lib/` functions with no cross-module
caller are scoping nits, not dead code.

---

## Suggested order of work

1. **1.1 + 1.2** — the `.grund.json` leak and the `layout.artifacts()`
   archive fix (small code change, then re-relate + re-dump foreskrift).
2. **1.3** — the registry-completeness test, so 1.1 can't recur.
3. **1.6's small bugs** — dead delay ×2, `--full` strings + dead branch,
   forarbete → `net.request`, sfs annotation, `connect_ro` in ops/browse.
4. **§2.1 dead-code deletions** (mechanical; §2.2 needs per-item decisions).
5. **§3.1-3.4** — adopt the existing lib/ helpers (write_record, walk,
   read_json, list_stems).
6. **§5** — the build.py factory and config.py resolver collapse
   (~215 lines, mechanical).
7. **§4.1-4.2, §6.1-6.3** — the judgment-level consolidations.
8. **7.1 rename** and, when the pending corpus reparse runs, **7.2's
   date/doctype keys**.
