# Consuming lagen.nu — API and data

How to access the corpus programmatically: the REST API, bulk downloads, and the
JSON artifact format. Everything here is **read-only public data** derived from,
and rebuildable from, the JSON artifacts on disk.

The two things to know first:

- **A document's canonical URI is its identity everywhere.** The published
  `https://lagen.nu/<id>` URI is simultaneously the API key (`?uri=…`), the bulk
  dump line id (`uri`), and the search index `_id`. These URIs are guaranteed
  stable across versions.
- **The JSON artifact is the source of truth.** The catalog, search index, and
  every derived view are computed from it. `GET /api/v1/document` returns the
  artifact verbatim, and each bulk-dump line *is* an artifact.

## Contents

- [The REST API](#the-rest-api) — search, resolve, list, get, derived views
- [Bulk download](#bulk-download) — NDJSON dumps
- [The JSON artifact format](#the-json-artifact-format) — the parsed-document schema
- [Derived and editorial layers](#derived-and-editorial-layers) — sidecars and the catalog

---

## The REST API

One uvicorn process serves both the static site and the API; the API lives under
`/api/v1`. Because the site and API share an origin, the site calls the API with
relative URLs — there is no separate API host to configure.

- **Base path:** `/api/v1`. Everything under it is public, read-only and `GET`.
- **CORS:** open to any origin, GET only (`allow_origins: ["*"]`,
  `allow_methods: ["GET"]`).
- **Interactive docs:** `GET /docs` (Swagger UI), `GET /openapi.json` (OpenAPI 3
  schema, generated from the typed handlers). Both describe *this* API and
  nothing else.
- **Document URIs are always a `uri` query parameter**, never a path segment —
  lagen.nu URIs contain `:` and `/`.
- **A URI's path is the document's page path.** `https://lagen.nu/celex/32016R0679`
  is both the identifier and the address; strip the host to get the page, add it
  to get the identifier. (Until 2026-08-29 the `celex`, `coe`, `icrc`, `untc`,
  `icc` and `icj` namespaces carried an extra `ext/` segment in the identifier
  only. A consumer holding those older URIs must drop it.)
- **Errors:** `{"detail": <message>}` JSON. A `404` or a `5xx` adds
  `"error_id": "<id>"`, which names the entry in the server's error ledger —
  quote it in a bug report. The key is `null` if the ledger itself could not be
  written; the response is served either way. A `422` carries FastAPI's list of
  validation errors as `detail`, not a string. Notable: `503 "catalog not built"` if the catalog
  is missing; `404` unknown document; `400` malformed version id; `422` for
  out-of-range query params.

Everything the site drives itself — login, the inline editors, the PDF export's
background jobs — is a **second API at `/internal-api/v1`**, kept out of this
schema on purpose. It is same-origin only, reads included, and its shapes change
with the UI. Nothing in it is part of this contract, and no external consumer
needs it: whatever the site can read there, it reads from `/api/v1` too.

### Search — `GET /api/v1/search`

Full-text search with a citation-aware twist. This is also the ⌘K resolver;
`GET /api/v1/resolve` (below) runs the same resolver on its own, without the
full-text search.

| Param | Default | Notes |
|---|---|---|
| `q` | (required) | free-text query |
| `source` | — | any name `GET /api/v1/sources` lists: `sfs`, `dv`, `forarbete`, `foreskrift`, `avg`, `rs`, `kommentar`, `begrepp`, `eurlex`, `edpb`, `hudoc`, `coe`, `icrc`, `untc`, `icc`, `icj` |
| `kind` | — | restrict to a document kind within the source (`lag`, `forordning`, `case`, `prop`, `sou`, `directive`, …); the buckets of the `kind` facet in any response list them |
| `year` | — | four-digit publication/decision year |
| `limit` | 10 | 1–100 |
| `offset` | 0 | ≥ 0, capped at 9900; use `cursor` for deep paging |
| `cursor` | — | opaque cursor from a previous response's `next_cursor`; mutually exclusive with `offset` |
| `sort` | `relevance` | `citations` orders the matches by their own `inbound_count` instead of the relevance score; a `cursor` is bound to the order that minted it |

On the first page (`offset == 0`) the query is *also* run through the citation
resolver: if `q` reads as a citation (`avtalslagen 36`, `BrB 12:1`, `GDPR art
32`, or a case nickname like `Instagrambilden`), the exact target is confirmed
against the catalog and **pinned as the first result**, with the pinpointed
fragment in `fragments`. Resolution is best-effort — a missing catalog doesn't
fail search.

```jsonc
// SearchResponse
{
  "query": "räntelagen",
  "total": 42,
  "next_cursor": "eyJzb3J0IjpbMTIuM10s...",  // null once the last page is reached
  "facets": {
    // `label` is the reader-facing name, resolved server-side; render
    // `label || value`. A year is its own label, so it has none.
    "source": [{ "value": "sfs", "count": 31, "label": "Författningar" },
               { "value": "dv", "count": 11, "label": "Rättsfall" }],
    "kind": [{ "value": "lag", "count": 31, "label": "Lagar" }],
    "year": [{ "value": "1975", "count": 1 }, { "value": "2024", "count": 3 }]
  },
  "results": [
    {
      "uri": "https://lagen.nu/1975:635",
      "url": "/1975:635",                 // public page path; null if unhosted
      "identifier": "1975:635",
      "title": "Räntelag (1975:635)",
      "display": "Räntelagen",            // reader-facing heading
      "source": "sfs",
      "kind": "lag",
      "kind_label": "Lag",                // what `kind` is called to a reader
      "score": 12.3,                       // null for a pinned/resolved hit
      "inbound_count": 2783,
      "highlight": ["…<em>ränta</em>…"],   // the document's own snippet
      // set only when the query read as a citation. THE ONE THING THAT MOVES
      // THE LINK: follow `url + "#" + pin.pinpoint` when it is set.
      "pin": { "uri": "https://lagen.nu/1975:635#P6", "pinpoint": "P6",
               "label": "6 §", "highlight": ["…"] },
      // where inside the document the query matched — supporting detail under
      // the hit, never its link target
      "fragments": [
        { "uri": "https://lagen.nu/1975:635#P6", "pinpoint": "P6",
          "label": "6 §", "highlight": ["…"] }
      ]
    }
  ]
}
```

`next_cursor` and `facets` are each computed against the *other* selected
filters (a facet's own aggregation ignores its own restriction, so its bucket
counts stay usable for widening the filter), and each aggregation runs over
`source`/`kind`/`year`.

### Resolve a citation — `GET /api/v1/resolve`

The citation resolver on its own: a citation-shaped query becomes its exact
document, and its exact provision when the citation names one. Use it when
only the resolved target is wanted — a citation read as a bag of words also
matches many loosely related documents in `/api/v1/search`. Answers from the
catalog alone; OpenSearch is not involved.

| Param | Default | Notes |
|---|---|---|
| `q` | (required) | the citation: a law name or abbreviation with a pinpoint (`avtalslagen 36 §`, `BrB 12:1`), an EU act with an article or recital (`GDPR artikel 32`), a CJEU case number (`C-199/24`), a treaty article (`EKMR 6`) or a case nickname (`Instagrambilden`) |
| `source` | — | restrict to one source |
| `kind` | — | restrict to a document kind within that source |

The answer has two lists. `results` holds the documents the corpus has, in
the same row shape as a search hit (`score` is null, `pin` marks the
provision). `recognized` holds the citations the query was read as whose
document the corpus does **not** hold — a well-formed case number of a
judgment not decided yet, or not harvested. Such an entry is an identity, not
a document: no page answers its `uri`, and it must not be cited as a source.
Both lists empty means the query does not read as a known citation; that is
not an error, `/api/v1/search` is the right call then.

```jsonc
// GET /api/v1/resolve?q=C-199/24
{
  "query": "C-199/24",
  "results": [ { "uri": "https://lagen.nu/celex/62024CJ0199", "url": "/celex/62024CJ0199",
                 "identifier": "62024CJ0199", "title": "C-199/24", "source": "eurlex",
                 "kind": "judgment", "score": null, "inbound_count": 10,
                 "highlight": [], "pin": null, "fragments": [] } ],
  "recognized": []
}

// GET /api/v1/resolve?q=C-744/28  — well formed, not held
{
  "query": "C-744/28",
  "results": [],
  "recognized": [ { "uri": "https://lagen.nu/celex/62028CJ0744", "source": "eurlex" } ]
}
```

### List documents

**`GET /api/v1/sources`** — every source and its document count:
`[{ "source": "sfs", "documents": 11184 }, …]`.

**`GET /api/v1/documents`** — paginated catalog enumeration (not search — no
query). Filter by `source` and/or `kind`. `limit` default 100 (1–1000), `offset`
≥ 0. To take the whole corpus, use the bulk dumps instead.

```jsonc
// DocumentList
{
  "total": 11184, "limit": 100, "offset": 0,
  "documents": [
    {
      "uri": "https://lagen.nu/2018:585",
      "source": "sfs", "kind": "lag",
      "label": "2018:585", "title": "Lag (2018:585) …",
      "source_url": "https://rkrattsbaser.gov.se/…",   // publisher's page
      "updated": "2026-07-01T09:12:00Z"                 // artifact mtime; null for stubs
    }
  ]
}
```

**`GET /api/v1/facets`** — ordered navigation buckets with counts (no leaf
documents); a lightweight navigator. `source` (required, a faceted source:
`sfs`/`dv`/`hudoc`/`forarbete`/`foreskrift`/`eurlex`/`coe`/`avg`/`begrepp`). Not
every source is faceted; an unfaceted one is a `404`. Returns a
`FacetTree`: `{ source, levels[], default[], buckets[] }` where each bucket is
`{ key, label, slug, count, children?, documents? }`.

**`GET /api/v1/browse`** — the same tree, but every leaf bucket's `documents` are
populated (each a `BrowseDoc`: `{ uri, url, display, short_id?, short_title?,
description?, … }`, plus per-source listing extras — `pre`/`key`/`subdued`/`year`
for statutes, `variant`/`date` for case law, `variant` for an EU act (enacting
body), a court ruling (court) or a treaty (`current` for a consolidated text,
else its family) and `pre`/`key` for a treaty,
`amendments`/`consolidated` for agency regulations). This is the full browse model the static site is generated
from; `/openapi.json` has the field-by-field description.

### Get one document — `GET /api/v1/document?uri=…`

Metadata plus the **full parsed artifact**:

```jsonc
// Document
{
  "uri": "https://lagen.nu/1975:635",
  "source": "sfs", "kind": "lag",
  "label": "1975:635", "title": "Räntelag (1975:635)",
  "inbound_count": 2783,
  "source_url": "https://rkrattsbaser.gov.se/…",
  "artifact": { /* the on-disk artifact JSON, verbatim — see the schema below */ }
}
```

The `artifact` object is the same one you get per line in the bulk dumps.

**`format=md`** swaps the `artifact` field for a `markdown` string — the body
rendered as a reading text (headings, paragraph designations, lists, pipe
tables, every citation as an inline `[text](uri)` link) for consumers that
want prose rather than the tree: a human, an LLM, a RAG chunker. The envelope
and metadata stay JSON. The markdown is a lossy derivation; the artifact
(`format=json`, the default) stays the source of truth. The MCP server's
`get_document` tool answers with the same markdown by default.

### Derived views

**Inbound links / citation graph — `GET /api/v1/document/inbound?uri=…`** — the
killer feature as data: every other document that cites this uri (one entry per
citing document, citing spot and provision cited; self-citations excluded).

`scope=tree`, the default, answers for the uri **and everything inside it** — on
a law that is every citation of every paragraf, which is what mirroring
lagen.nu's own pages takes (brottsbalken, measured 2026-08-07: 40 696 citations
of the act as such, 162 909 counting its 2 844 cited provisions). `scope=exact`
is the narrow question, only the rows naming the uri itself. Pass a fragment uri
(`…#P6`) to ask at paragraph level; `tree` then covers its stycken and points.

Rows come back in the order the site's own context rail uses — case law first
for a statute, then decisions, then the citation graph — so the first page is
representative rather than whichever source name sorts earliest. That order is
total and build-independent, so `offset` paging is stable across rebuilds
(`sort=citations` changes that — see below).
`limit` defaults to (and caps at) 10 000 rows; `total` and `by_source` describe
the whole answer, not the page returned.

**Which of these matter — `inbound_count` and `sort=citations`.** Every row
carries the *citing* document's own citation count, the same number and the
same name `/search` and `/document` answer with, so the reply ranks itself
without a call per row. `sort=citations` orders the whole scope by it, biggest
first; `sort=rail` (the default) keeps the order above.

`sort=citations` is the one order `offset` paging is **not** stable under
across rebuilds: the count is recomputed every build, so a row can move between
pages as the corpus grows. Ties fall back to the rail order, which is stable.
Take the first page, or page a ranked answer in one sitting.

That is the "leading cases on this paragraf" question, and it wants
`source=dv` with it:

```sh
curl -G https://ferenda.lagen.nu/api/v1/document/inbound \
     --data-urlencode "uri=https://lagen.nu/1915:218#P36" \
     -d source=dv -d sort=citations -d limit=5
```

```
  32  Den kollektiva hemförsäkringen (NJA 1987 s. 394)
  29  NJA 1992 s. 66
  27  AD 1998 nr 80
  26  AD 1994 nr 122
  23  AD 1998 nr 97
```

Two things to know before leaning on it. The count is how often a document is
cited, which correlates with authority but is not the same thing: it favours an
old case over a recent one, and it can only count what this corpus holds — a
fresh precedent can matter with a low number. And the row set is unreduced (see
below), so a heavily-citing document repeats: under `sort=citations` over
avtalslagen 36 § *without* a source filter, a 50-row page holds 14 distinct
documents, because a proposition cites the same paragraf from many places. With
`source=dv` it holds 48, and under the default `sort=rail` it holds 46 either
way. Collapse on `uri` if you are ranking documents rather than citations.

`sort=citations` counts the whole scope before paging rather than just the page
— 893 citers and 13 ms for avtalslagen 36 §, 11 693 and 578 ms for the whole of
brottsbalken. The default counts the page alone, which is up to `limit` citers
and costs 8 ms on a 10 000-row page of brottsbalken. Either way it is a small
share of the request: this endpoint reads its whole per-document file before it
pages, which is 260 ms on brottsbalken and 1.85 s on the ECHR. The count runs on
a covering index, so it is index reads rather than table reads. (Measured
2026-08-21 on a warm dev disk; the production host is HDD-class and these were
not measured there.)

The set is **unreduced**: the site folds a document's repeated citations into
one line and hides whole-document citations superseded by a pinpointed one, and
both are presentation. Filter on `predicate` for the typed relations
(`rpubl:bemyndigande`, `rpubl:andrar`, `rpubl:upphaver`) and on `source` for
lagen.nu's own commentary. The citation's surface text is not carried — it
belongs to the citing document, and `/document/outbound` on that uri has it.

Served from a per-document file the build writes, not from a live query: on the
production disk the whole-law query is minutes of scattered reads.

**Outbound citations — `GET /api/v1/document/outbound?uri=…`** — every citation a
document makes; `uri` is the **cited target**. `hosted: false` marks a target not
(yet) in the corpus (then `source`/`label`/`title` are null).

**Version history — `GET /api/v1/document/versions?uri=…`** (statutes and EU
acts only) — a document's archived historical consolidations (*lydelser*),
oldest first, current excluded. `404` if the uri isn't a statute or an EU act.
A statute's `version` is the SFS number of the last amendment folded in; an EU
act's is the ISO date its consolidated wording (CONSLEG) began to apply, which
is also its `ikraft`.

```jsonc
// VersionList
{
  "uri": "https://lagen.nu/1998:204",
  "versions": [
    {
      "version": "2003:466",             // consolidation cutoff
      "uri": "https://lagen.nu/1998:204/konsolidering/2003:466",
      "url": "/1998:204/konsolidering/2003:466",
      "ikraft": "1998-10-24",             // when the cutoff amendment entered force; may be null
      "forarbeten": ["Prop. 1997/98:44"]
    }
  ]
}
```

**Diff between versions — `GET /api/v1/document/diff?uri=…&from=…&to=…`** —
compares two consolidations of a statute or an EU act. `from` (required) =
older version id; `to` (optional) = newer, **default the current
consolidation**. **Returns an HTML fragment** (`text/html`), not JSON: a
leading `<div class="diff-note">` then the newer text marked up with
`<ins>`/`<del>`. Direction is always older→newer.

**Page facsimile — `GET /api/v1/facsimile?uri=…&sid=N`** — a PNG of one
printed page of the document's source PDF (`image/png`), for every
page-oriented PDF source (förarbeten, myndighetsföreskrifter, avgöranden).
`sid` is the printed page number — the same `#sid{N}` anchors the document
pages and citations use. Rendered on demand at retina resolution (150 DPI,
~1240 px wide for A4) and cached: the first request for a page costs
~0.5 s, later ones are served from disk, and the response is
`Cache-Control: immutable` so browsers never re-fetch. Also reachable at the
legacy path grammar, `GET /prop/2022/23:10/sid1.png` /
`GET /sou/2021:82/sid1.png` (undocumented alias, kept for old links).

**Statute graphic — `GET /api/v1/sfs-graphic?uri=…&node=…`** — a PNG crop of a
figure, formula or map the *consolidated* statute text omits but the published
PDF carries. `node` is the gap's stable key (the `data-grafik` value on the
rendered page). The crop is cut from the PDF of the amendment that last set that
wording, not from the viewed statute's own PDF, per the reviewed `.graphics`
layer; a gap nobody has signed off on is a `404`. Two resolutions: the default
is the inline thumbnail, `stor=1` the full-size render — a page of 325 road
signs asks for hundreds of the first and one of the second.

**Original verdict PDF — `GET /api/v1/dv-verdict?court=…&id=…&file=…`** — the
PDF a decision was first served as, before its NJA referat was published. The
three parameters come from the decision's own artifact; there is nothing to
guess.

**Citation-graph neighbourhood — `GET /api/v1/graph?uri=…`** — the same
citations `/document/inbound` and `/document/outbound` serve one row each,
aggregated **per neighbour document** and ready to draw. This is what
[paraGRAF](https://para-graf.tomtebo.org) (github.com/staffanm/para-graf), a
standalone graph-explorer app, walks.

`direction` picks the sides (`in`, `out`, `both` — the default), `groups` is a
comma-separated filter on the flow groups (`Författningar`, `Rättsfall`, …), and
`limit` (default 20, max 300) bounds each side's `top` list. `sort=citations`
ranks `top` by each neighbour's own citedness (`inbound_count`, carried on the
row) instead of its ties to the center, and `grouplimit` caps how many
neighbours one flow group may take — diversity over one dominating source
type. The `total_links` / `total_docs` / `unresolved` counts describe the
whole side, not the page. `depth` (1–3, default 1) answers with a deeper
neighbourhood in one call: the per-side `limit` becomes a whole-view budget
split across the rings (60/40 at depth 2, 50/30/20 at 3), the outer rings
arrive in `expansion.nodes` (each with its `hop`, `side` and
`inbound_count`), and `expansion.edges` lists every document-level citation
among the returned documents — so the picture shows structure, not just
spokes. Depth > 1 answers 503 while the in-memory graph is still loading.

Pass a **fragment** uri (`…#K4P7`) to ask for one provision, and the answer adds
`source_url`: the document's page at its own publisher -- for a source
this site does not render (tidskriftsartiklar), the link to open.
Under `sort=citations` the ranking is by citing
degree off the in-memory graph when it is loaded (the stamped
`inbound_count` still rides each row); the joined fallback ranks by the
stamped count.
`internal`: the document citing itself, as a provision-to-provision graph at
§/article level. `internal=true` asks for that same graph on a plain document
uri too, for a zoomed-in structure view.

**Document identity card — `GET /api/v1/card?uri=…`** — the one-row
answer for the ONE item a reader selected or hovers: citing name
(`citation`), `short_id`, `title`, the reader-facing `url` (and
`source_url` for sources this site does not render), `inbound_count`, and
`snippet` — the words of the place itself. For a document that is its own
opening words (a court decision's sammanfattning, a statute's 1 § with
designation, an EU act's first recital; null until relate has stamped it);
for a **fragment uri** it is that provision's own text under its pinpoint
("1 kap. 5 § Konungen eller drottning som enligt successionsordningen …"),
which costs one artifact read. `uri` takes either form the site writes — the
uri (`https://lagen.nu/1962:700#K3P1`) or the page path for the same place
(`/1962:700#K3P1`), which is what a browser has in an href. The path is the
uri's own path; it simply carries no host. The site's link popovers use it
for every target outside
the page in hand, instead of fetching that page. The graph payload
deliberately does not carry these fields — of 300 neighbours one gets
selected, and this is the call for that one.

**Shortest citation chain — `GET /api/v1/path?from=…&to=…`** — the
six-degrees walk: one shortest chain of citations connecting two documents,
endpoints included, at document level (a hop exists when any provision of one
document cites any provision of the other; a fragment uri is answered for its
document). `direction=out|in|both` says which links a step may follow — with
`both` a hop may run either way, and each step's `forward` says which way it
ran; `links` is how many citations carry the hop. `groups=` filters the
*intermediate* documents by flow group (the endpoints are always allowed).
`distance` is null when no chain exists. `paths=N` (1–5) asks for more than
one route: the shortest stays `path`, the rest arrive as `alternatives`
(`{distance, path}`), next-shortest first, and fewer come back when the graph
holds no further loopless chain. The whole document-level graph (~2.6M edges)
is held in memory, so one chain is one breadth-first search; further chains
are Yen's algorithm over the same graph, which is why `paths` is capped.

**Document as PDF — `GET /api/v1/pdf?path=…`** — a generated page typeset for
paper: A4, running heads, `n (total)` folios, a PDF outline. `path` is the
public page path (`/1998:204`), not a uri. `toc=1` prepends the document's own
table of contents with resolved page numbers; `kontext=` prints chosen context
kinds under each provision (the rail's slugs — `kommentar,dv,forarbete` — or
`alla`); `andringar=0` drops the SFS amendment register; `kolumner=2` uses the
compact two-column layout and omits context; `download=1` serves it as an
attachment. A full statute with all its context takes minutes to lay out, so
expect a slow response on a big document.

### Endpoint → task map

| I want to… | Endpoint |
|---|---|
| search | `GET /api/v1/search` |
| resolve a citation (⌘K) | `GET /api/v1/resolve`; `GET /api/v1/search` also pins the resolved hit first on its first page |
| list sources + counts | `GET /api/v1/sources` |
| enumerate documents | `GET /api/v1/documents` |
| browse by facet | `GET /api/v1/facets`, `GET /api/v1/browse` |
| get one document | `GET /api/v1/document?uri=…` |
| a document as markdown | `GET /api/v1/document?uri=…&format=md` |
| who cites this? | `GET /api/v1/document/inbound?uri=…` |
| which citers weigh most? | `GET /api/v1/document/inbound?uri=…&source=dv&sort=citations` |
| what does this cite? | `GET /api/v1/document/outbound?uri=…` |
| version history | `GET /api/v1/document/versions?uri=…` |
| diff two versions | `GET /api/v1/document/diff?uri=…&from=…&to=…` (HTML) |
| page facsimile (PNG) | `GET /api/v1/facsimile?uri=…&sid=N` |
| a statute's omitted graphic (PNG) | `GET /api/v1/sfs-graphic?uri=…&node=…` |
| the original verdict PDF | `GET /api/v1/dv-verdict?court=…&id=…&file=…` |
| draw the citation graph | `GET /api/v1/graph?uri=…` |
| shortest chain between two documents | `GET /api/v1/path?from=…&to=…` |
| a document as PDF | `GET /api/v1/pdf?path=…` |
| bulk download | `GET /api/v1/dumps` + static fetch |
| machine schema | `GET /openapi.json`, `GET /docs` |

---

## Bulk download

For reprocessing the whole corpus, use the NDJSON dumps rather than paging the
API.

**`GET /api/v1/dumps`** lists them (one per source):

```jsonc
[ { "source": "sfs", "file": "sfs.ndjson.gz", "bytes": 12345678 }, … ]
```

The endpoint reports each dump's `source`, `file` name and size. The dump
**files themselves** are written to `<data_root>/dumps/<source>.ndjson.gz`; the
`/api/v1/dumps` endpoint is a manifest, not a download route, and the app's
static mount serves `generated/`, not `dumps/`. The files are served by the
reverse proxy instead — `location /dumps/` in
`docker/nginx/ferenda.lagen.nu.conf`, over a read-only mount of the same
directory (`docker-compose.prod.yml`) — so a dump is at
`https://ferenda.lagen.nu/dumps/<source>.ndjson.gz`, with an autoindex at
`/dumps/`. nginx and not uvicorn because the set is ~4.5 GB (forarbete alone
~3.6 GB) and wants sendfile and byte ranges. Once fetched:

```sh
zcat sfs.ndjson.gz | head -1     # one artifact per line
```

Each line is a source artifact re-serialised (compactly) and gzipped, **with no
transformation of its contents** — the same JSON object `GET /api/v1/document`
returns in `artifact` (the on-disk artifact is pretty-printed and the dump line
is minified, but the value is identical). Because the citation graph lives
inline in each artifact, a line
is self-contained: no catalog read needed to reprocess the corpus. (Empty
"skipped" documents are omitted.)

---

## The JSON artifact format

Every parsed document is one JSON object. There is no single mandated envelope —
each source owns its shape — but two things are universal: a canonical **`uri`**,
and text encoded as **inline runs** (below). One field is stamped uniformly by
the pipeline:

- **`source_url`** — the publisher's own page for the document (the "Källa"
  link), when derivable. Absent when there is none.

On disk, artifacts live at `<data_root>/artifact/<source>/<...>.json` but are
stored Brotli-compressed (`.json.br`); the API and dumps hand you the
decompressed JSON.

### Inline runs — the one shape to understand

**Every renderable text value is a list, not a string.** An element is either a
plain `str` or a link dict:

```jsonc
"text": [
  "Ränta enligt ",
  { "predicate": "dcterms:references", "uri": "https://lagen.nu/1975:635#P6", "text": "6 §" },
  " räntelagen ska …"
]
```

A link dict is `{ predicate, uri, text }`, optionally with `kind` (e.g. `"term"`
for a concept/defined-term link). Empty text is `[]`; unlinked text is a
single-element `[str]`. Two carriers hold run-lists: a node's **`text`**, and a
table row's **`cells`** (a list of cells, each itself a run-list). **This is the
entire citation graph** — the catalog is just a derived index over these link
dicts. Common `predicate` values: `dcterms:references` (default),
`dcterms:subject` (concept/term), `rpubl:genomforDirektiv`, `rpubl:bemyndigande`.

### Per-source shapes

All bodies are trees/lists of typed nodes (`type` discriminator: `rubrik`,
`stycke`, `paragraf`, `lista`/`punkt`, `tabell`/`rad`, …), with text as inline
runs. The distinctive top-level fields:

**SFS (statutes)** — the deepest model, a real nesting tree.
`{ uri, metadata, structure, amendments }`.
- `metadata` = `{ uri, properties: {…}, secondary: {…} }`; `properties` uses
  RDF-ish keys (`dcterms:identifier`, `dcterms:title`,
  `rpubl:utfardandedatum`, `rpubl:upphavandedatum`).
- `structure` nodes: `rubrik` (`id`, `level`, `text`), `paragraf`
  (`id`, `ordinal`, `children`), `stycke` (`id`, `beteckning` like `"1 §"`,
  `text`, `children?`), `lista`/`punkt`, `tabell` → `rad` with `cells`,
  `upphavd`, `overgangsbestammelse`.
- `amendments` = list of `{ uri, properties, forarbeten, content? }`, one per
  register row.
- A versioned consolidation artifact adds a top-level `version` (e.g. `"2003:466"`).

**DV (court decisions)** — `{ uri, court, court_namn, malnummer, referat,
avgorandedatum, metadata, structure, footnotes, sources }`.
- `metadata` = `{ publiceringsform, typ, rattsomrade, nyckelord, lagrum:
  [{referens, sfsnummer}], forarbeten, sammanfattning, related }`.
- `structure` is the instance/ruling skeleton (delmål → instans → dom →
  domskäl/domslut) as nested `rubrik`/`stycke` blocks.
- `footnotes` = `[{ num, text }]`.

**förarbete (preparatory works)** — flat, **page-precise**. `{ uri, type
(prop|sou|ds|dir|bet|…), identifier, basefile, title, date, structure }`, plus an
optional `implements` list (EU-directive edges) and, for a proposition with a
författningskommentar chapter, a `kommentarer` list — the per-paragraf FK
commentary: `[{ law, chapter, paragrafer, page, kommentar }]` (`law` is the
raw per-law rubrik text, resolved to an SFS uri at relate time; `paragrafer`
is a list because a combined "9 och 10 §§" heading comments several at once;
an empty list marks a law-level comment). Blocks carry `type` (`rubrik` /
`stycke` / `kapitel` / `paragraf` / `fotnot` — small-print footnotes like the
lagtext "Senaste lydelse" provenance — / `tabell`), `text`, and an optional
`page` (the `#sid{N}` anchor), `level`, `num`, plus `fk` on FK commentary
blocks (the entry number — blocks sharing a number belong to one paragraf's
commentary, the prop page's highlight box). A `tabell` block is a
nuvarande/föreslagen lydelse comparison reconstructed from the two-column
layout: `children` are `rad` rows with two-element `cells` (inline-run lists,
citation-scanned), the header row flagged `th` — the same table shape SFS
artifacts use.

**eurlex (EU law)** — `{ uri (…/celex/{CELEX}), celex, doctype
(regulation|directive|decision|judgment|treaty|act), lang, title, date, structure
}`, optional `label, shortname, abbr, ecli, oj`. Blocks carry `type`, `text`,
`num?`, `id?` (= the citation anchor, e.g. article `"5"`), `defines?` (a
definitions-article point → the term it defines).

**föreskrift (agency regulations)** — `{ type: "foreskrift", uri, identifier, fs
(samling code), metadata, structure, consolidations, amendments }`. `metadata`
carries `bemyndigande` (a list of SFS-paragraf uris), `beslutsdatum`,
`ikrafttradandedatum`, etc.

**avg (JO/JK/ARN)** — `{ uri, type: "avgorande", org (jo|jk|arn), identifier,
metadata, structure, sammanfattning? }`. `metadata` = `{ title, publisher,
diarienummer, beslutsdatum?, avgjordAv?, nyckelord? }`.

**hudoc (ECHR case law)** — `{ uri (…/dom/echr/{itemid}), type: "avgorande",
court: "echr", itemid, doctype (judgment|decision|communicated-case|
advisory-opinion|legal-summary|resolution|case-law), title, date, metadata,
references, structure }`. `structure` is heading (`rubrik`) and numbered
paragraph (`stycke`, `id: "P{n}"`) blocks. If numbering restarts in an
operative part, annex or separate opinion, later occurrences are suffixed
(`P1-2`) while the first `P1` stays canonical. `references` is the top-level
`dcterms:references` link list into the cited Convention/Protocol
provisions — CoE Treaty Office fragments (`coe/{ETS}#A…`), the same
inbound-citation contract every other source uses.

**coe (Council of Europe treaties)** — `{ uri (…/coe/{number}), type:
"internationell-overenskommelse", doctype (treaty|protocol), number,
identifier, title, date, metadata, references, structure }`. `structure` is
a nested `rubrik`/`artikel`/`sektion`/`stycke`/`punkt` tree with stable,
document-unique fragment ids (`#A8`, `#A6P3Ld`; occurrence suffixes only when
the printed designator repeats). Roman/compound articles and section-only
amending instruments are retained in the same structure. For the ECHR
instruments incorporated into Swedish law
(Convention plus Protocols 1, 4, 6, 7, 13, 16), `references` carries an
`rdfs:seeAlso` edge to `https://lagen.nu/1994:1219`.

---

## Derived and editorial layers

These sit **beside** an artifact and are versioned independently of it.

**`.versions.json`** (SFS) — an index of a statute's historical consolidations:
`{ versions: [{version, uri}, …], skipped: [{version, error|duplicate_of}, …] }`.
Each listed version has its own full artifact on disk.

**`.ann` sidecars** — the AI-authored (then human-corrected) editorial layer,
kept separate from the parsed artifact. Two shapes:
- eurlex `ai-annotate`: `{ editorialLayer: { recitalGroups: [{ "range": [lo,hi] },…],
  articleToRecitals: { "<article>": [int,…] } } }`.
- remisser `ai-analyze`: `{ overall: {sentiment, quote}, segments:
  [{forarbete_id, sentiment, quote}, …] }`.
- kommentar `ai-annotate`: `{ guidanceLinks: { "<anchor>": [{label, href, desc,
  section}, …] } }`.

**The catalog** (`catalog.sqlite`, the `relate` phase) — a derived, rebuildable
index over the artifacts. You normally reach it via the API, but its tables are:
- **`documents`** `(uri, source, kind, label, title, path, source_url,
  content_hash, expired, display, …)`. `path` is stored `data_root`-relative, so
  the catalog is portable across hosts.
- **`links`** (the graph) `(from_uri, from_anchor, predicate, to_uri, to_root,
  text)` — `to_root` is `to_uri` with the fragment stripped.
- **`fragments`** `(uri, snippet)` — per-node text for link tooltips.
- **`genomforande`** `(sfs_uri, sfs_anchor, directive, article, prop_uri,
  prop_label, pinpoint, partial)` — the förarbete → EU-directive → SFS-paragraf
  *implements* relation.
- **`correspondence`** `(new_uri, old_uri, relation, scope, prop_uri)` — old↔new
  paragraf map.
- **`concept_alias` / `concept_redirect`** — begrepp canonicalization.

Everything in the catalog is recomputable from the artifacts, so treat the
artifacts (or the bulk dumps) as ground truth and the catalog/search index as
convenient, rebuildable projections.
