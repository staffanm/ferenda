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

- [The REST API](#the-rest-api) — search, list, get, derived views
- [Bulk download](#bulk-download) — NDJSON dumps
- [The JSON artifact format](#the-json-artifact-format) — the parsed-document schema
- [Derived and editorial layers](#derived-and-editorial-layers) — sidecars and the catalog

---

## The REST API

One uvicorn process serves both the static site and the API; the API lives under
`/api/v1`. Because the site and API share an origin, the site calls the API with
relative URLs — there is no separate API host to configure.

- **Base path:** `/api/v1`
- **CORS:** open to any origin, GET only (`allow_origins: ["*"]`,
  `allow_methods: ["GET"]`). The public read API is cross-origin usable; the
  mutation surface (inline editor) is same-origin only.
- **Interactive docs:** `GET /docs` (Swagger UI), `GET /openapi.json` (OpenAPI 3
  schema, generated from the typed handlers).
- **Document URIs are always a `uri` query parameter**, never a path segment —
  lagen.nu URIs contain `:` and `/`.
- **Errors:** FastAPI's `{"detail": "<message>"}` JSON. Notable: `503 "catalog
  not built"` if the catalog is missing; `404` unknown document; `400` malformed
  version id; `422` for out-of-range query params.

### Search — `GET /api/v1/search`

Full-text search with a citation-aware twist. This is also the ⌘K resolver —
there is no separate `/resolve` endpoint.

| Param | Default | Notes |
|---|---|---|
| `q` | (required) | free-text query |
| `source` | — | `sfs`, `dv`, `hudoc`, `forarbete`, `foreskrift`, `eurlex`, `coe`, `avg`, `kommentar`, `begrepp` |
| `kind` | — | restrict to a document kind |
| `year` | — | four-digit publication/decision year |
| `limit` | 10 | 1–100 |
| `offset` | 0 | ≥ 0, capped at 9900; use `cursor` for deep paging |
| `cursor` | — | opaque cursor from a previous response's `next_cursor`; mutually exclusive with `offset` |

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
    "source": [{ "value": "sfs", "count": 31 }, { "value": "dv", "count": 11 }],
    "kind": [{ "value": "law", "count": 31 }],
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
      "kind": "law",
      "score": 12.3,                       // null for a pinned/resolved hit
      "inbound_count": 2783,
      "highlight": ["…<em>ränta</em>…"],
      "fragments": [
        { "uri": "https://lagen.nu/1975:635#P6", "pinpoint": "P6", "highlight": ["…"] }
      ]
    }
  ]
}
```

`next_cursor` and `facets` are each computed against the *other* selected
filters (a facet's own aggregation ignores its own restriction, so its bucket
counts stay usable for widening the filter), and each aggregation runs over
`source`/`kind`/`year`.

### List documents

**`GET /api/v1/sources`** — every source and its document count:
`[{ "source": "sfs", "documents": 11184 }, …]`.

**`GET /api/v1/documents`** — paginated catalog enumeration (not search — no
query). Filter by `source` and/or `kind`. `limit` default 100 (1–1000), `offset`
≥ 0.

```jsonc
// DocumentList
{
  "total": 11184, "limit": 100, "offset": 0,
  "documents": [
    {
      "uri": "https://lagen.nu/2018:585",
      "source": "sfs", "kind": "law",
      "label": "2018:585", "title": "Lag (2018:585) …",
      "source_url": "https://rkrattsbaser.gov.se/…",   // publisher's page
      "updated": "2026-07-01T09:12:00Z"                 // artifact mtime; null for stubs
    }
  ]
}
```

**`GET /api/v1/facets`** — ordered navigation buckets with counts (no leaf
documents); a lightweight navigator. `source` (required, a faceted source:
`sfs`/`dv`/`hudoc`/`forarbete`/`foreskrift`/`eurlex`/`coe`/`avg`/`begrepp`). Returns a
`FacetTree`: `{ source, levels[], default[], buckets[] }` where each bucket is
`{ key, label, slug, count, children?, documents? }`.

**`GET /api/v1/browse`** — the same tree, but every leaf bucket's `documents` are
populated (each a `BrowseDoc`: `{ uri, url, display, pre?, key?, subdued?, year?
}`; the `pre/key/subdued/year` extras are statute-listing only). This is the full
browse model the static site is generated from.

### Get one document — `GET /api/v1/document?uri=…`

Metadata plus the **full parsed artifact**:

```jsonc
// Document
{
  "uri": "https://lagen.nu/1975:635",
  "source": "sfs", "kind": "law",
  "label": "1975:635", "title": "Räntelag (1975:635)",
  "inbound_count": 2783,
  "source_url": "https://rkrattsbaser.gov.se/…",
  "artifact": { /* the on-disk artifact JSON, verbatim — see the schema below */ }
}
```

The `artifact` object is the same one you get per line in the bulk dumps.

### Derived views

**Inbound links / citation graph — `GET /api/v1/document/inbound?uri=…`** — the
killer feature as data: every other document that cites *exactly* this uri (one
entry per citing document + pinpoint; self-citations excluded). Pass a fragment
uri (`…#P6`) for citations to that paragraph, or a bare uri for the whole
document. Returns a list of `Citation` — for inbound, `uri` is the **citing**
document, with its `label`/`title`/`source`.

**Outbound citations — `GET /api/v1/document/outbound?uri=…`** — every citation a
document makes; `uri` is the **cited target**. `hosted: false` marks a target not
(yet) in the corpus (then `source`/`label`/`title` are null).

**Version history — `GET /api/v1/document/versions?uri=…`** (statutes only) — a
statute's archived historical consolidations (*lydelser*), oldest first, current
excluded. `404` if the uri isn't a statute.

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
compares two consolidations. `from` (required) = older version id; `to`
(optional) = newer, **default the current consolidation**. **Returns an HTML
fragment** (`text/html`), not JSON: a leading `<div class="diff-note">` then the
newer text marked up with `<ins>`/`<del>`. Direction is always older→newer.

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

### Endpoint → task map

| I want to… | Endpoint |
|---|---|
| search | `GET /api/v1/search` |
| resolve a citation (⌘K) | `GET /api/v1/search` (first page pins the resolved hit) |
| list sources + counts | `GET /api/v1/sources` |
| enumerate documents | `GET /api/v1/documents` |
| browse by facet | `GET /api/v1/facets`, `GET /api/v1/browse` |
| get one document | `GET /api/v1/document?uri=…` |
| who cites this? | `GET /api/v1/document/inbound?uri=…` |
| what does this cite? | `GET /api/v1/document/outbound?uri=…` |
| version history | `GET /api/v1/document/versions?uri=…` |
| diff two versions | `GET /api/v1/document/diff?uri=…&from=…&to=…` (HTML) |
| page facsimile (PNG) | `GET /api/v1/facsimile?uri=…&sid=N` |
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
static mount serves `generated/`, not `dumps/`. Exposing the files publicly is a
deployment concern (a static route in the reverse proxy over `<data_root>/dumps`).
Once fetched:

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

**eurlex (EU law)** — `{ uri (…/ext/celex/{CELEX}), celex, doctype
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
provisions — CoE Treaty Office fragments (`ext/coe/{ETS}#A…`), the same
inbound-citation contract every other source uses.

**coe (Council of Europe treaties)** — `{ uri (…/ext/coe/{number}), type:
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
