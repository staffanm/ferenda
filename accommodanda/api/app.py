"""The public REST/OpenAPI service (REWRITE.md §6) -- the machine-readable face
of the corpus that replaces Fuseki's SPARQL endpoint.

FastAPI gives OpenAPI 3 + a Swagger UI (`/docs`) for free from the typed
handlers below. Three read-only, fully-rebuildable backends:

  * the SQLite **catalog** -- document metadata and the citation graph
    (inbound/outbound), the killer feature exposed as data;
  * **OpenSearch** -- full-text search (lazily connected, so the metadata
    endpoints work even with no cluster running);
  * the **artifact JSON** on disk -- a document's full parsed body.

Document URIs are passed as a `uri` query parameter, never a path segment:
`lagen.nu` URIs carry `:` and `/`, so a query param sidesteps path-encoding.
Published URIs are unchanged from the old pipeline (standing constraint), so an
artifact's `uri` is also its API key, its dump id and its OpenSearch `_id`.
"""

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# StaticFiles.get_response raises Starlette's HTTPException (FastAPI's is a
# subclass, so it would not catch the parent) -- the SiteFiles rewrite catches this
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import config
from ..lib import catalog, facets, layout, resolve, search

CATALOG = config.DATA / "catalog.sqlite"
DUMPS = config.DATA / "dumps"

app = FastAPI(
    title="lagen.nu API",
    version="1.0",
    description="Search and the citation graph over the Swedish legal corpus.",
)

# the generated static site (served on another port) reaches the API from the
# browser via the ⌘K palette -- a cross-origin GET. The API is public read-only
# data, so any origin may read it.
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["GET"], allow_headers=["*"])

# one search client for the process; constructing it does not open a connection,
# so importing/serving the API never requires a running OpenSearch -- only an
# actual /search call does.
_index = search.SearchIndex()


_schema_lock = threading.Lock()
_schema_ready = False


def _ensure_schema():
    """Apply the catalog's additive migrations once per process: a catalog built
    by an older build may lack a column the queries below select. Lock-guarded so
    concurrent first requests don't race on the one-time ALTER, after which the
    per-request connections can stay read-only."""
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if not _schema_ready:
            catalog.connect(str(CATALOG)).close()
            _schema_ready = True


def get_con():
    """A read-only catalog connection per request (SQLite connections are not
    shared across threads)."""
    if not CATALOG.exists():
        raise HTTPException(503, "catalog not built -- run `lagen all relate`")
    _ensure_schema()
    con = sqlite3.connect("file:%s?mode=ro" % CATALOG, uri=True)
    try:
        yield con
    finally:
        con.close()


# --------------------------------------------------------------------------
# response models (these drive the OpenAPI schema)
# --------------------------------------------------------------------------

class Fragment(BaseModel):
    uri: str
    pinpoint: str | None = None
    highlight: list[str] = []


class SearchResult(BaseModel):
    uri: str
    url: str | None = None          # the public page path (/1962:700, /dom/nja/…)
    identifier: str | None = None
    title: str | None = None
    display: str | None = None      # reader-facing heading (short name + acronym, else title)
    source: str | None = None
    kind: str | None = None
    score: float | None = None
    inbound_count: int = 0
    highlight: list[str] = []
    fragments: list[Fragment] = []


class SearchResponse(BaseModel):
    query: str
    total: int
    results: list[SearchResult]


class Citation(BaseModel):
    uri: str
    anchor: str | None = None
    predicate: str | None = None
    text: str | None = None
    label: str | None = None
    title: str | None = None
    source: str | None = None
    hosted: bool = True


class DocumentSummary(BaseModel):
    uri: str
    source: str
    kind: str | None = None
    label: str | None = None
    title: str | None = None
    source_url: str | None = None
    updated: str | None = None      # artifact file mtime, ISO 8601 UTC


class DocumentList(BaseModel):
    total: int                      # documents matching the filter (before paging)
    limit: int
    offset: int
    documents: list[DocumentSummary]


class DocumentMeta(BaseModel):
    uri: str
    source: str
    kind: str | None = None
    label: str | None = None
    title: str | None = None
    inbound_count: int


class Document(DocumentMeta):
    source_url: str | None = None
    artifact: dict


class BrowseDoc(BaseModel):
    uri: str
    url: str                        # the hosted page path (/2018:585, /dom/nja/…)
    display: str                    # the listing handle (law name / short label / id)
    # statute-only listing extras: the title split into a subdued designation/number
    # prefix + the emphasised sort subject, whether it is primary law (else subdued),
    # and its year -- what the SFS listing renders and filters on (None elsewhere)
    pre: str | None = None
    key: str | None = None
    subdued: bool | None = None
    year: str | None = None


class FacetBucket(BaseModel):
    key: str                        # the raw bucket key ("nja", "2024", "A")
    label: str                      # its display label ("NJA – Högsta domstolen")
    slug: str                       # its URL path segment
    count: int                      # documents in this bucket (incl. children)
    children: list["FacetBucket"] | None = None   # the next facet level, if any
    documents: list[BrowseDoc] | None = None      # leaf listing (only from /browse)


class FacetTree(BaseModel):
    source: str
    levels: list[str]               # the facet axis names, outer-first
    default: list[str]              # the landing bucket's key path
    buckets: list[FacetBucket]


class SourceInfo(BaseModel):
    source: str
    documents: int


class DumpInfo(BaseModel):
    source: str
    file: str
    bytes: int


# --------------------------------------------------------------------------
# endpoints
# --------------------------------------------------------------------------

def _resolved_results(con, q, source, kind):
    """The ⌘K resolver's hits for `q`, shaped as SearchResults: a query that *is*
    a citation -- a law nickname/abbr + pinpoint ("avtalslagen 36", "BrB 12:1"),
    an EU act + article ("GDPR art 32") or a case nickname ("Instagrambilden") --
    maps to one exact, fragment-deep target that full-text can't reach (the name
    is nowhere in the document). Each candidate is confirmed against the catalog
    (so an alias for a not-yet-parsed document doesn't surface) and honours the
    same source/kind filter. The document's own label/title/inbound_count are
    used, so a pinned hit ranks and renders like any other."""
    out = []
    for hit in resolve.resolve(q):
        if source and hit["source"] != source:
            continue
        root, _, frag = hit["uri"].partition("#")
        row = catalog.document(con, root)
        if not row:
            continue
        _uri, src, kind_, label, title, path = row
        if kind and kind_ != kind:
            continue
        # the same reader-facing heading the page and full-text hits show (short
        # name + acronym where the artifact has them, else the title) -- stored
        # on the documents row at relate, so no artifact load per resolved hit
        display = catalog.document_display(con, root) or title
        out.append({
            "uri": root, "url": layout.page_url(root),
            "identifier": label, "title": title, "display": display,
            "source": src, "kind": kind_,
            "score": None, "inbound_count": catalog.document_inbound_count(con, root),
            "highlight": [],
            "fragments": ([{"uri": hit["uri"], "pinpoint": frag, "highlight": []}]
                          if frag else []),
        })
    return out


@app.get("/api/v1/search", response_model=SearchResponse, tags=["search"])
def search_endpoint(
        q: str = Query(..., description="free-text query"),
        source: str | None = Query(None, description="restrict to a source "
                                   "(sfs, dv, forarbete, foreskrift, eurlex, kommentar, begrepp)"),
        kind: str | None = Query(None, description="restrict to a document kind"),
        limit: int = Query(10, ge=1, le=100),
        offset: int = Query(0, ge=0)):
    """Full-text search, with a citation-aware twist: when the query reads as a
    citation (a law nickname/abbreviation + pinpoint, an EU act + article, or a
    case nickname), the exact resource is resolved and pinned as the first
    result -- so ⌘K + Enter lands on the right §/article, which plain full-text
    can't do (the name appears nowhere in the text). The rest is the usual
    full-text ranking (relevance combined with citation count) with the matching
    §/article fragments and highlights."""
    res = _index.search(q, source=source, kind=kind, limit=limit, offset=offset)
    results = [{**r, "url": layout.page_url(r["uri"])}
               for r in res["results"]]
    total = res["total"]
    # the resolved target is the answer to a citation-shaped query, so it leads;
    # only on the first page (it's one fixed target, not paginated). Drop any
    # full-text row for the same document -- the pinned hit is more precise.
    # Resolution confirms its target against the catalog, but a missing catalog
    # mustn't fail a full-text search, so it's best-effort (no Depends/503).
    if offset == 0 and CATALOG.exists():
        _ensure_schema()
        con = sqlite3.connect("file:%s?mode=ro" % CATALOG, uri=True)
        try:
            pinned = _resolved_results(con, q, source, kind)
        finally:
            con.close()
        if pinned:
            roots = {p["uri"] for p in pinned}
            kept = [r for r in results if r["uri"] not in roots]
            total += sum(p["uri"] not in {r["uri"] for r in results} for p in pinned)
            results = (pinned + kept)[:limit]
    return SearchResponse(query=q, total=total, results=results)  # ty: ignore[invalid-argument-type]  # results are untyped hit dicts; pydantic validates at runtime


@app.get("/api/v1/facets", response_model=FacetTree, tags=["catalog"])
def facets_endpoint(
        source: str = Query(..., description="a faceted source "
                            "(sfs, dv, forarbete, foreskrift, eurlex, begrepp)"),
        con: sqlite3.Connection = Depends(get_con)):
    """The navigation facets for a source: the ordered buckets (one or two levels
    -- a law's subject initial, a case's court + year) with document counts, plus
    the default landing bucket. The lightweight navigator; for the listings too
    use /browse. A flat listing of a whole source is too large to be useful."""
    if source not in facets.sources():
        raise HTTPException(404, "source %r is not faceted" % source)
    return FacetTree(**facets.tree(con, source))


@app.get("/api/v1/browse", response_model=FacetTree, tags=["catalog"])
def browse_endpoint(
        source: str = Query(..., description="a faceted source "
                            "(sfs, dv, forarbete, foreskrift, eurlex, begrepp)"),
        con: sqlite3.Connection = Depends(get_con)):
    """The complete browse model for a source: the facet navigator *plus* each
    leaf bucket's ordered, display-labelled documents. The single payload the
    static-site generator consumes to write the browse pages -- it has no other
    access to the data store."""
    if source not in facets.sources():
        raise HTTPException(404, "source %r is not faceted" % source)
    return FacetTree(**facets.browse_view(con, source))


@app.get("/api/v1/documents", response_model=DocumentList, tags=["document"])
def documents_endpoint(
        source: str | None = Query(None, description="restrict to a source "
                                   "(sfs, dv, forarbete, foreskrift, eurlex, kommentar, begrepp)"),
        kind: str | None = Query(None, description="restrict to a document kind "
                                 "(law, case, prop, directive, …)"),
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
        con: sqlite3.Connection = Depends(get_con)):
    """List document ids + top-level metadata, filtered by source/kind and
    paginated -- the catalog index that drives /document lookups. This is *not*
    full-text search (that is /search, which requires a query); it enumerates the
    corpus. `source_url` is the publisher's page where known; `updated` is the
    artifact's last-build time."""
    total = catalog.document_count(con, source, kind)
    docs = []
    for uri, src, kind_, label, title, source_url, path, _display in \
            catalog.documents(con, source, kind, limit, offset):
        # synthesized begrepp stubs have no artifact file (path=''); Path('')
        # aliases to the cwd, so this must be excluded before the exists() check
        p = Path(path) if path else None
        updated = (datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat()
                   if p and p.exists() else None)
        docs.append(DocumentSummary(uri=uri, source=src, kind=kind_, label=label,
                                    title=title, source_url=source_url,
                                    updated=updated))
    return DocumentList(total=total, limit=limit, offset=offset, documents=docs)


@app.get("/api/v1/document", response_model=Document, tags=["document"])
def document_endpoint(uri: str = Query(..., description="full lagen.nu document uri"),
                      con: sqlite3.Connection = Depends(get_con)):
    """A document's metadata plus its full parsed artifact (structure/body with
    inline citations)."""
    row = catalog.document(con, uri)
    if not row:
        raise HTTPException(404, "no document %r in the catalog" % uri)
    uri, source, kind, label, title, path = row
    # synthesized begrepp stubs are real catalog rows with no artifact file
    # (path='') -- served as an empty artifact, like the rendered shell pages
    art = json.loads(Path(path).read_bytes()) if path else {}
    return Document(uri=uri, source=source, kind=kind, label=label, title=title,
                    source_url=art.get("source_url"), artifact=art,
                    inbound_count=catalog.document_inbound_count(con, uri))


@app.get("/api/v1/document/inbound", response_model=list[Citation], tags=["document"])
def inbound_endpoint(uri: str = Query(..., description="document or fragment uri"),
                     con: sqlite3.Connection = Depends(get_con)):
    """Which other documents cite exactly `uri` (the killer feature as data) --
    one entry per (citing document, pinpoint). Self-citations excluded."""
    return [Citation(uri=from_uri, anchor=anchor, label=label, title=title,
                     source=src)
            for from_uri, anchor, label, title, src in catalog.inbound(con, uri)]


@app.get("/api/v1/document/outbound", response_model=list[Citation], tags=["document"])
def outbound_endpoint(uri: str = Query(..., description="citing document uri"),
                      con: sqlite3.Connection = Depends(get_con)):
    """Every citation a document makes. Targets not (yet) in the corpus come back
    with `hosted: false` and no label/title."""
    return [Citation(uri=to_uri, anchor=anchor, predicate=predicate, text=text,
                     label=label, title=title, source=src, hosted=src is not None)
            for to_uri, predicate, text, anchor, label, title, src
            in catalog.outbound(con, uri)]


@app.get("/api/v1/sources", response_model=list[SourceInfo], tags=["catalog"])
def sources_endpoint(con: sqlite3.Connection = Depends(get_con)):
    """The corpus' sources and their document counts."""
    return [SourceInfo(source=s, documents=n)
            for s, n in sorted(catalog.counts(con).items())]


@app.get("/api/v1/dumps", response_model=list[DumpInfo], tags=["catalog"])
def dumps_endpoint():
    """The available NDJSON bulk dumps (one per source), for machine consumers."""
    if not DUMPS.exists():
        return []
    return [DumpInfo(source=p.name.split(".", 1)[0], file=p.name,
                     bytes=p.stat().st_size)
            for p in sorted(DUMPS.glob("*.ndjson.gz"))]


class SiteFiles(StaticFiles):
    """StaticFiles serving the site at lagen.nu's URI grammar: a request for a
    document's bare public URL (/2018:585, /prop/2020/21:22, /dom/ad/1993:100,
    /celex/61954CJ0001) is, on a static miss, rewritten to its flattened on-disk
    file via layout.url_to_relpath -- nginx's try_files rules, in Starlette.
    Directories (the /sfs/ etc. browse indexes) and existing files hit first, so
    only an extensionless document URL takes the rewrite."""

    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404 and path and not path.endswith(".html"):
                rel = layout.url_to_relpath(path)
                if rel and rel != path:
                    return await super().get_response(rel, scope)
            raise


def serve(directory, host="127.0.0.1", port=8000):
    """Serve the generated static site *and* the API from one uvicorn process --
    the only server (`lagen serve`). The REST routes (/api/v1/*, /docs,
    /openapi.json) answer first; everything else is served from `directory` as
    static files (html=True maps each dir to its index.html, and SiteFiles maps a
    bare /<sfsid> to its <sfsid>.html). Because the site and API share an origin,
    the ⌘K palette calls the API with relative URLs -- there is no separate API
    server and no configurable API base to go stale. The static mount is added
    here -- not at import -- so the in-process API client used during `generate`
    (which only calls /api/v1) never needs a built site."""
    app.mount("/", SiteFiles(directory=directory, html=True), name="site")
    uvicorn.run(app, host=host, port=port)
