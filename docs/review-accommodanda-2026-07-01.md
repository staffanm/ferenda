# Code review: `accommodanda/` — security · performance · engineering

2026-07-01. A three-dimension review of the rebuilt pipeline (~20,400 lines,
76 files) against the plan in `REWRITE.md` and the conventions in `CLAUDE.md`.
Performance claims marked *measured* were verified against the live 2.2 GB
catalog (124,544 documents, 4.19 M links, 2.9 M fragments, 6.3 GB artifacts).

Each finding carries a status: **FIXED** (applied in this pass, tests green)
or **OPEN** (recommended, needs its own change).

Threat model used: document *content* is remote-supplied (government sites can
be compromised or serve malformed data); the API + static site are public on
ferenda.lagen.nu; the download/annotate CLIs are operator-run.

---

## 1. Security

### 1.1 HIGH — Stored XSS in the ⌘K search palette — **FIXED**
`lib/render.py` (`SEARCH` JS): result rendering interpolated `display`,
`title`, `identifier`, the target href and the OpenSearch highlight fragment
into `innerHTML` unescaped; the highlight is the *body text of scraped
documents* (no `encoder` set in `lib/search.py:HIGHLIGHT`). A corpus document
containing `<img onerror=…>` would execute in every visitor's browser that
searched for it — persistent XSS, reachable from every page.
**Fix applied:** `esc()` helper escapes every interpolated value (incl. the
reflected query in the "Inga träffar" note); `HIGHLIGHT` now sets
`"encoder": "html"` so OpenSearch escapes the fragment body while keeping only
its own `<em>` markers as markup (applies at query time — no reindex needed).

### 1.2 MEDIUM — XML entity-expansion DoS on remote XML — **FIXED**
Three stdlib-`ElementTree` parses of remote-supplied XML (billion-laughs
memory exhaustion): `eurlex/parse.py` (Formex file + zip members),
`eurlex/download.py` (SOAP response). **Fix applied:** both now parse with a
hardened lxml `XMLParser(resolve_entities=False, load_dtd=False,
no_network=True, remove_comments=True, remove_pis=True)` — the same posture
`lib/pdftext.py`/`eurlex/parse_pdf.py` already had. The third site
(`lib/wikitext.py:load_page`) was dead code and is deleted (§3.5).

### 1.3 LOW — path-traversal defense-in-depth in the static rewrite — **FIXED**
`layout.url_to_relpath` fed an unquoted attacker-controlled request path into
the try-files rewrite, safe only because of Starlette's containment re-check.
Now refuses `..` segments itself (returns `None` → the miss stays a 404).

### 1.4 LOW — EUR-Lex SOAP credentials not XML-escaped — **FIXED**
`eurlex/download.py:soap_search` interpolated `EURLEX_USERNAME`/`PASSWORD`
into the envelope raw (a password containing `&`/`<` would corrupt the
request; robustness, not third-party injection). Now `escape()`d like the
query. Credentials are correctly env-only and never logged.

### 1.5 LOW / informational — **OPEN** (accepted or optional)
- **CORS `allow_origins=["*"]`** (`api/app.py`): acceptable posture — read-only
  public data, no credentials, GET-only. Recorded as a deliberate decision.
- **Unbounded response reads** (`lib/net.py`): a hostile server could return a
  huge body (`.content` reads whole). Operator-run harvest tooling; optional
  hardening is a max-bytes streaming guard.

### Checked and found sound
SQL fully parameterized everywhere (the only string-built fragment is an
internal int `LIMIT`); server-side HTML rendering escapes all content and the
Rail JSON island guards `</script>` breakout; download path construction
validates/sanitizes remote-supplied ids (dv UUID/court regex + `..` asserts,
CELEX regex, slug substitution); all subprocess calls are list-arg, no
`shell=True`; no pickle/eval/exec; `ruamel.yaml` round-trip (safe) config
loading; TLS never disabled; `BERGET_API_KEY`/EURLEX creds not logged.

---

## 2. Performance

### 2.1 Incremental generate re-reads + re-hashes all 6.3 GB of artifacts — **OPEN** (top win)
`build.py:page_signature` reads and sha256s every page's artifact (plus
sidecars) in the single-threaded planning loop — a "one document changed" run
pays a full-corpus read. The catalog already stores the hash
(`documents.content_hash`, written at relate): select it in `generate_site`'s
row query and hash only the `.ann`/`.corr` sidecars from disk. Costs a
one-time full re-render (signature format change).

### 2.2 Incremental relate re-reads a whole source to find one changed doc — **OPEN**
`catalog.rebuild` reads + hashes every artifact of a changed source just to
*decide* unchanged. Store `(size, mtime_ns)` beside `content_hash` (additive
column, `connect()` already migrates) and skip the read when both match —
consistent with how `file_watermark` already trusts mtime.

### 2.3 Generate planning: 124k × `page_dependency_digest` ≈ 31 s single-threaded (*measured*) — **OPEN**
Batch it (one streamed query over `links` chunked by `to_root` ≈ 3 s corpus-
wide) or move the digest into the parallel workers.

### 2.4 No SQLite pragmas on the rebuild path — **FIXED**
`catalog.connect` now sets `journal_mode=WAL` + `synchronous=NORMAL`: no more
multi-GB rollback journal on a forced relate, and readers (API, render
workers) no longer block on the writer. The catalog is derived/rebuildable, so
the durability trade is free. (Read-only connections use raw
`sqlite3.connect(mode=ro)` and are unaffected.)

### 2.5 Search indexing N+1 inbound-count queries — **FIXED**
`lib/search.py` ran one GROUP-BY subquery per document (124k queries per full
reindex, ~20–30 s). New `catalog.document_inbound_counts(con)` computes all
roots in one pass (*measured* 3.0 s); the indexer reads from that dict.

### 2.6 Build stages hashed each doc's inputs twice — **FIXED**
`build.ensure` computed `hash_files(stage.inputs(...))` in `is_fresh` and
again for the manifest entry. Now hashed once and passed through
(`is_fresh(..., inputs_hash=)`).

### 2.7 API citation-resolve loaded a full artifact per hit — **FIXED**
`_resolved_results` parsed the entire artifact JSON (many MB for a big
statute) per resolved ⌘K query just to compute the display heading, which the
catalog already stores. New `catalog.document_display()` reads the stored
column (falls back to title for pre-column rows).

### 2.8 `_drop_document` full-scanned the 2.9 M-row fragments table — **FIXED**
`LIKE 'uri#%'` is case-insensitive → can't use the PK index (*confirmed via
query plan*). Replaced with an index-usable range predicate
(`uri >= ?||'#' AND uri < ?||'$'`).

### 2.9 Dumps gzipped at level 9 — **FIXED**
`lib/dump.py` now uses `compresslevel=6` (2–3× faster over ~6.3 GB for a few
percent larger output; no byte-identity contract on dumps).

### 2.10 Situational — **OPEN**
- 57 MB manifest shipped whole to every parse worker (`build.py` pool
  initargs); filter to the running source's key prefix — matters more under
  spawn (Python 3.14 default).
- `Rail.add` runs 4 preloadable queries per id-bearing node (correspondence /
  genomförande / bemyndigande tables are ≤1.3k rows — preload into `Site`
  dicts like `Site.aliases` already does).
- `Site.snippets` cache is unbounded (~70 MB+/worker worst case at full
  corpus, × N workers).

### Verified non-issues
Lark/Earley parsers are cached (`@functools.cache`) — never rebuilt per
document; regexes module-level in all hot loops; catalog indexes match every
hot WHERE clause (query plans checked); connections reused per worker/source;
`rebuild` is one transaction with `executemany`; manifest loading process-
cached with a tiny watermark fast path.

---

## 3. Engineering / conventions

### 3.1 Layer-boundary violations (`lib` → vertical, vertical → vertical) — mostly **OPEN**
The load-bearing rule is "lib never imports a vertical; only build composes
across verticals". Found and judged real violations:

| Where | Import | Status |
|---|---|---|
| `lib/layout.py` | `from ..dv.parse import slug` | **FIXED** — `case_slug` now lives in `lib/layout` (path grammar is layout's); `dv/parse` imports it back |
| `lib/render.py` | `from ..dv import naming` | **OPEN** — `dv/naming.case_label` is cross-layer shared code operating on artifact metadata; move to `lib/` |
| `lib/render.py`, `wiki/parse.py`, `wiki/annotate.py` | `eurlex.structure.{flatten, subarticle_key, anchored_blocks}` | **OPEN** — the artifact-tree walkers are already generic ("the one canonical sub-article id grammar"); moving them to `lib/` fixes one lib→vertical *and* both wiki→eurlex vertical→vertical imports in one move |
| `lib/resolve.py` | `from ..dv import namedcases` | **OPEN** — loader belongs beside `lib/datasets`' path |
| `sfs/correspond.py` | `forarbete.kommentar` + `forarbete.structure` | **OPEN** — route through `lib/` or `build` |
| `lib/render.py` → `api.app` via `fastapi.testclient` | browse pages generated by driving the API in-process | **OPEN — decision needed**: sanction it in REWRITE.md (it does guarantee browse pages match REST responses) or move browse-page generation to `api/`/`build` |

Source-name *branching* in `lib/catalog`/`render`/`facets`/`layout` was
reviewed and judged legitimate — the derived layer keying on artifact/catalog
metadata, exactly what REWRITE §1 permits.

### 3.2 Swallowed exceptions — **FIXED**
`lib/lagrum.py:try_parse` caught `except Exception` around the Lark parse
while introspecting `pos_in_stream` — a genuine engine bug would silently
become "no reference here" (silent under-linking, in the module where it's
hardest to notice). Narrowed to `lark.exceptions.UnexpectedInput`. All other
broad catches audited and found to be the sanctioned resilience points
(harvest walk, build driver per-doc, validation harness, legacy-stats CLI) —
each logs/records and continues by documented design.

### 3.3 Load-bearing asserts in LLM-retry validation — **FIXED**
`eurlex/annotate.py` and `wiki/annotate.py` `_validate` used `assert` for the
checks whose exceptions drive the feed-back-and-retry loop — stripped under
`python -O`, the validation would silently pass. Converted to `ValueError`
raises; the catches narrowed accordingly (test updated).

### 3.4 DRY: quintuplicated harvest helpers — **FIXED**
- `write_atomic` existed in **five** byte-near-identical copies
  (sfs/dv/eurlex/forarbete/foreskrift downloaders) plus two *unguarded*
  tmp+replace variants in `build.py`. Now one `lib/util.write_atomic`
  (bytes-or-str superset); all seven sites repointed.
- `basefile_slug`/`record_path`/`list_basefiles` were byte-identical between
  `forarbete/download.py` and `foreskrift/harvest.py` (docstrings included).
  Now in `lib/util`; both verticals import them.
- The two `USER_AGENT` strings (harvester identity ×3 copies, browser UA ×2)
  now live as `lib/net.HARVESTER_UA` / `BROWSER_UA` beside `make_session`.
- **OPEN**: the bigger promotion — `foreskrift/harvest.py`'s engine core
  (`.complete`-marker loop, `Skip` resilience) is the generalized version of
  `forarbete.sync` but lives in a vertical; promoting it to `lib/harvest.py`
  is the structural fix (and what a new source should build on). The sfs vs
  eurlex watermark read/write near-duplication rides along.

### 3.5 Dead code — **FIXED** (deleted, zero references verified)
`eurlex/download.Notice`'s unused lookup surface (`value`/`objects`/
`subjects`/`subject_objects` + the `_spo`/`_pos`/`_by_pred` indexes — callers
only ever `ttl()`); `eurlex/parse._celex_from_path` + stale CLI banner;
`lib/wikitext.load_page`/`is_redirect` (+ the ElementTree import, `MW`,
`RE_REDIRECT`); `dv/parse.body_links`. **OPEN** (decide, don't delete):
`foreskrift/harvest.sitemap_enumerate` (speculative — no agency uses it; its
stated user STAFS was never wired); `sfs/extract.sanitize_body` is used only
by test fixtures — *possible production gap* (the 2010:110 missing-newline fix
never runs in the production parse path), worth investigating.

### 3.6 In-function imports — **FIXED**
`foreskrift/parse.py` imported `Amendment` inside a loop → top. (The
`dv/word.py` POI/jpype in-function imports are documented as technically
forced — JVM must be started first — and stay.)

### 3.7 Testing gaps — **OPEN**
No coverage: `dv/download.py` (the most test-worthy gap — sfs/eurlex/
forarbete downloads all have suites), `lib/net.py` (shared retry/backoff
deserves a fake-session unit test), `eurlex/bulk.py`, `eurlex/parse_pdf.py`,
`foreskrift/download.py`, `forarbete/genomforande.py` (only indirect),
`dv/word.py` (needs JVM; understandable). Model-typing nit: several dataclass
fields are bare `list` with the element type in a comment — tighten
opportunistically.

### Clean bill
No mutable default arguments anywhere; pyflakes/ruff clean; no TODO/FIXME/
debug leftovers; precondition asserts used per convention; imports grouped.

---

## 4. Test status after the fixes

All named suites pass (685+ tests) except one **pre-existing** failure,
reproduced with the pre-review code restored:
`test_sfs_parse.py::test_sfs_links[tricky-overlappande-tabellrader]` — extra
riksdagsordningen (2014:801) reference tuples vs the fixture. Unrelated to
this pass; likely fallout from an earlier namedlaws/fixture change.

## 5. Suggested next steps, in order

1. §2.1 + §2.2 — kill the double full-corpus read+hash on incremental
   relate/generate (turns "one doc changed" from minutes of I/O into seconds).
2. §3.1 — move the artifact-level helpers (`eurlex/structure` walkers,
   `dv/naming`, `dv/namedcases` loader) into `lib/`; decide the render→api
   inversion.
3. §2.3 — batch/parallelize the generate dependency digests.
4. §3.4 — promote the harvest engine to `lib/harvest.py` (the JO/JK port
   below is the natural forcing function).
5. §3.7 — tests for `dv/download.py` and `lib/net.py`; investigate the
   `sanitize_body` production gap (§3.5).
