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
from pydantic import BaseModel

from ..lib import catalog, layout, search
from .. import config

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
    url: str | None = None          # the hosted page path (/sfs/1962_700.html)
    identifier: str | None = None
    title: str | None = None
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

@app.get("/api/v1/search", response_model=SearchResponse, tags=["search"])
def search_endpoint(
        q: str = Query(..., description="free-text query"),
        source: str | None = Query(None, description="restrict to a source "
                                   "(sfs, dv, forarbete, eurlex, kommentar, begrepp)"),
        kind: str | None = Query(None, description="restrict to a document kind"),
        limit: int = Query(10, ge=1, le=100),
        offset: int = Query(0, ge=0)):
    """Full-text search. Ranks whole documents (relevance combined with citation
    count) and returns the matching §/article fragments with highlights."""
    res = _index.search(q, source=source, kind=kind, limit=limit, offset=offset)
    results = [{**r, "url": "/" + layout.page_relpath(r["uri"])}
               for r in res["results"]]
    return SearchResponse(query=q, total=res["total"], results=results)


@app.get("/api/v1/documents", response_model=DocumentList, tags=["document"])
def documents_endpoint(
        source: str | None = Query(None, description="restrict to a source "
                                   "(sfs, dv, forarbete, eurlex, kommentar, begrepp)"),
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
    for uri, src, kind_, label, title, source_url, path in \
            catalog.documents(con, source, kind, limit, offset):
        p = Path(path)
        updated = (datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat()
                   if p.exists() else None)
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
    art = json.loads(Path(path).read_bytes() or b"{}")
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


def run(host="127.0.0.1", port=8001):
    """Launch the API with uvicorn (the `lagen serve-api` entry point)."""
    uvicorn.run(app, host=host, port=port)
