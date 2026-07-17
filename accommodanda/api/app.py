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
import logging
import re
import sqlite3
import subprocess
import threading
from html import escape
from pathlib import Path

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# StaticFiles.get_response raises Starlette's HTTPException (FastAPI's is a
# subclass, so it would not catch the parent) -- the SiteFiles rewrite catches this
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import config
from ..lib import (
    annstore,
    catalog,
    compress,
    diff,
    facets,
    facsimile,
    feeds,
    history,
    layout,
    pins,
    regeringen,
    search,
)
from ..lib.util import basefile_slug
from . import auth, edit, ops, patch
from . import mcp as mcp_server

CATALOG = config.CATALOG_ROOT / "catalog.sqlite"
DUMPS = config.DATA / "dumps"

app = FastAPI(
    title="lagen.nu API",
    version="1.0",
    description="Search and the citation graph over the Swedish legal corpus.",
    # runs the MCP server's Streamable HTTP session manager (a no-op for the
    # in-process TestClient path, which never calls /mcp) -- see api/mcp.py
    lifespan=mcp_server.lifespan,
)

# the generated static site (served on another port) reaches the API from the
# browser via the ⌘K palette -- a cross-origin GET. The API is public read-only
# data, so any origin may read it.
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["GET"], allow_headers=["*"])

# the ops dashboard (/ops*), registered like /api/v1 -- before the SiteFiles
# mount added in serve(), so its explicit routes win over the static catch-all
app.include_router(ops.router)

# the inline editor's auth + write routes. The mutating side of the service: it
# stays GET-open/CORS-* for the public read API above and same-origin only for
# these (the editor JS is served from this same origin; the session cookie is
# SameSite=Lax), so CORS deliberately keeps blocking cross-origin writes.
app.include_router(auth.router)
app.include_router(edit.router)
app.include_router(patch.router)   # the patch-file (source-fix) editor

# the public MCP server at /mcp (Streamable HTTP) -- the corpus reshaped as tools
# for AI hosts. Added before serve()'s static "/" catch-all so its routes win.
mcp_server.mount(app)

# one search client for the process; constructing it does not open a connection,
# so importing/serving the API never requires a running OpenSearch -- only an
# actual /search call does.
_index = search.SearchIndex()


# a bounded LRU of rendered diffs, keyed on the (basefile, from_version, to)
# triple: two archived consolidations are immutable, so the same triple always
# renders the same HTML and is safe to cache indefinitely. The "current"
# consolidation (to=None) is excluded -- it changes on the next build, so
# caching it would serve a stale diff -- and every miss still does the same
# bounded diff.diff_html work, so the cache is purely an anonymous-traffic
# resource cap, not a correctness dependency.
_diff_lock = threading.Lock()
_diff_cache = {}
_DIFF_CACHE_MAX = 512


def _cached_diff_html(basefile, from_version, to):
    key = (basefile, from_version, to)
    with _diff_lock:
        cached = _diff_cache.get(key)
    if cached is not None:
        return cached
    html, _changed = diff.diff_html(_version_artifact(basefile, from_version),
                                    _version_artifact(basefile, to))
    if to is not None:
        with _diff_lock:
            if len(_diff_cache) >= _DIFF_CACHE_MAX:
                _diff_cache.pop(next(iter(_diff_cache)))
            _diff_cache[key] = html
    return html


def get_con():
    """A read-only catalog connection per request (SQLite connections are not
    shared across threads); `catalog.connect_ro` applies the additive schema
    migrations once per process first."""
    if not CATALOG.exists():
        raise HTTPException(503, "catalog not built -- run `lagen all relate`")
    con = catalog.connect_ro(CATALOG)
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


class SearchFacetBucket(BaseModel):
    value: str
    count: int


class SearchResponse(BaseModel):
    query: str
    total: int
    next_cursor: str | None = None
    facets: dict[str, list[SearchFacetBucket]] = {}
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

# the ⌘K resolver's hits for `q`, shaped as SearchResults (the citation-shaped
# query -> one exact, fragment-deep target the full-text index can't reach);
# shared verbatim with the MCP search/resolve_citation tools (lib/pins.py)
_resolved_results = pins.resolved_results


@app.get("/api/v1/search", response_model=SearchResponse, tags=["search"])
def search_endpoint(
        q: str = Query(..., description="free-text query"),
        source: str | None = Query(None, description="restrict to a source "
                                   "(sfs, dv, hudoc, forarbete, foreskrift, eurlex, coe, avg, kommentar, begrepp)"),
        kind: str | None = Query(None, description="restrict to a document kind"),
        year: str | None = Query(None, pattern=r"^\d{4}$",
                                 description="restrict to a four-digit publication/decision year"),
        limit: int = Query(10, ge=1, le=100),
        offset: int = Query(0, ge=0, le=9900,
                            description="bounded random access; use cursor for deep paging"),
        cursor: str | None = Query(None, max_length=2048,
                                   description="opaque cursor returned by the previous page")):
    """Full-text search, with a citation-aware twist: when the query reads as a
    citation (a law nickname/abbreviation + pinpoint, an EU act + article, or a
    case nickname), the exact resource is resolved and pinned as the first
    result -- so ⌘K + Enter lands on the right §/article, which plain full-text
    can't do (the name appears nowhere in the text). The rest is the usual
    full-text ranking (relevance combined with citation count) with the matching
    §/article fragments and highlights."""
    if cursor and offset:
        raise HTTPException(422, "cursor and offset are mutually exclusive")
    try:
        res = _index.search(q, source=source, kind=kind, year=year,
                            limit=limit, offset=offset, cursor=cursor)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    results = [{**r, "url": layout.page_url(r["uri"])}
               for r in res["results"]]
    total = res["total"]
    # the resolved target is the answer to a citation-shaped query, so it leads;
    # only on the first page (it's one fixed target, not paginated). Drop any
    # full-text row for the same document -- the pinned hit is more precise.
    # Resolution confirms its target against the catalog, but a missing catalog
    # mustn't fail a full-text search, so it's best-effort (no Depends/503).
    if offset == 0 and not cursor and not year and CATALOG.exists():
        con = catalog.connect_ro(CATALOG)
        try:
            pinned = _resolved_results(con, q, source, kind)
        finally:
            con.close()
        results, total = pins.merge_pinned(pinned, results, total, limit)
    return SearchResponse(query=q, total=total,
                          next_cursor=res.get("next_cursor"), facets=res["facets"],
                          results=results)  # ty: ignore[invalid-argument-type]  # result/facet dicts are validated by pydantic at runtime


def _legacy_feed(con, dataset, rdf_type, rpubl_rattsfallspublikation,
                 dcterms_publisher):
    """The shared body of the two legacy feed handlers: dataset lookup, the
    legacy facet params, and the entries -- only the rendering differs."""
    item = feeds.dataset(dataset)
    if not item:
        raise HTTPException(404, "unknown feed dataset %r" % dataset)
    params = {key: value for key, value in (
        ("rdf_type", rdf_type),
        ("rpubl_rattsfallspublikation", rpubl_rattsfallspublikation),
        ("dcterms_publisher", dcterms_publisher),
    ) if value}
    rows = feeds.entries(con, item, rdf_type, rpubl_rattsfallspublikation,
                         dcterms_publisher)
    return item, rows, params


def _sitenews_file(relative, media_type):
    path = layout.GENERATED / "dataset" / "sitenews" / relative
    if not compress.exists(path):
        raise HTTPException(404, "sitenews feed has not been generated")
    return Response(compress.read_bytes(path), media_type=media_type)


@app.get("/dataset/sitenews/feed.atom", include_in_schema=False)
def sitenews_atom_feed():
    return _sitenews_file("feed.atom", "application/atom+xml")


@app.get("/dataset/sitenews/feed", include_in_schema=False)
def sitenews_html_feed():
    return _sitenews_file("feed/index.html", "text/html")


@app.get("/dataset/{dataset}/feed.atom", include_in_schema=False)
def legacy_atom_feed(
        dataset: str,
        rdf_type: str | None = Query(None),
        rpubl_rattsfallspublikation: str | None = Query(None),
        dcterms_publisher: str | None = Query(None),
        con: sqlite3.Connection = Depends(get_con)):
    """Atom at the URLs published by the old Ferenda repositories."""
    item, rows, params = _legacy_feed(con, dataset, rdf_type,
                                      rpubl_rattsfallspublikation,
                                      dcterms_publisher)
    return Response(feeds.render_atom(item, rows, params),
                    media_type="application/atom+xml")


@app.get("/dataset/{dataset}/feed", include_in_schema=False)
def legacy_html_feed(
        dataset: str,
        rdf_type: str | None = Query(None),
        rpubl_rattsfallspublikation: str | None = Query(None),
        dcterms_publisher: str | None = Query(None),
        con: sqlite3.Connection = Depends(get_con)):
    """Human-readable twin of a legacy Atom feed."""
    item, rows, params = _legacy_feed(con, dataset, rdf_type,
                                      rpubl_rattsfallspublikation,
                                      dcterms_publisher)
    return HTMLResponse(feeds.render_html(item, rows, params))


@app.get("/api/v1/facets", response_model=FacetTree, tags=["catalog"])
def facets_endpoint(
        source: str = Query(..., description="a faceted source "
                            "(sfs, dv, hudoc, forarbete, foreskrift, eurlex, coe, avg, begrepp)"),
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
                            "(sfs, dv, hudoc, forarbete, foreskrift, eurlex, coe, avg, begrepp)"),
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
                                   "(sfs, dv, hudoc, forarbete, foreskrift, eurlex, coe, avg, kommentar, begrepp)"),
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
    root = catalog.data_root(con)              # stored paths are data_root-relative
    for uri, src, kind_, label, title, source_url, path, _display in \
            catalog.documents(con, source, kind, limit, offset):
        updated = catalog.artifact_updated(root, path)
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
    art = catalog.load_artifact(catalog.data_root(con), path)
    return Document(uri=uri, source=source, kind=kind, label=label, title=title,
                    source_url=art.get("source_url"), artifact=art,
                    inbound_count=catalog.document_inbound_count(con, uri))


# an SFS basefile / version id as it may appear in a query param: "1998:204",
# "1827:60 s.1007", "2003:466" -- one colon, no path-shaped characters, so it
# can safely become the filesystem segments the layout rules mint
_RE_SFS_ID = re.compile(r"^[^/\\:]+:[^/\\:]+$")


def _sfs_basefile(uri):
    """The statute basefile behind a document uri, for the version endpoints
    (only statutes have archived consolidations)."""
    basefile = catalog.local(catalog.strip_fragment(uri))
    if not _RE_SFS_ID.match(basefile) or ".." in basefile:
        raise HTTPException(404, "%r is not a statute uri -- only SFS "
                                 "documents carry versions" % uri)
    return basefile


def _validate_version_id(version):
    """Raise 400 unless `version` is a well-formed consolidation cutoff -- as
    strictly checked as `_sfs_basefile`'s uri (no ``..`` segment) so a version
    id can't smuggle a path-traversal-shaped value past the one place both
    become filesystem segments (`layout.sfs_version_artifact`)."""
    if (not _RE_SFS_ID.match(version) or ".." in version) \
            and not version.isdigit():
        raise HTTPException(400, "bad version id %r" % version)


def _version_artifact(basefile, version):
    """A consolidation's parsed artifact: a named historical version from the
    archive, or the current one (version None)."""
    if version is None:
        path = layout.artifact("sfs", basefile)
    else:
        _validate_version_id(version)
        path = layout.sfs_version_artifact(basefile, version)
    if not compress.exists(path):
        raise HTTPException(404, "no %s consolidation of %s -- see "
                                 "/api/v1/document/versions"
                                 % (version or "current", basefile))
    return json.loads(compress.read_bytes(path))


class VersionInfo(BaseModel):
    version: str                    # the consolidation cutoff ("2003:466")
    uri: str
    url: str                        # the hosted lydelse page (/1998:204/konsolidering/2003:466)
    ikraft: str | None = None       # when the cutoff amendment entered force
    forarbeten: list[str] = []      # its preparatory works ("Prop. 1997/98:44", …)


class VersionList(BaseModel):
    uri: str
    versions: list[VersionInfo]     # oldest first; the current consolidation excluded


@app.get("/api/v1/document/versions", response_model=VersionList, tags=["document"])
def versions_endpoint(uri: str = Query(..., description="full lagen.nu statute uri"),
                      con: sqlite3.Connection = Depends(get_con)):
    """A statute's archived historical consolidations (lydelser), oldest
    first -- each one browsable at its own page and diffable via
    /api/v1/document/diff. Amendment dates and preparatory works are joined
    in from the statute's register where known."""
    basefile = _sfs_basefile(uri)
    row = catalog.document(con, catalog.BASE + basefile)
    info = (history.amendment_info(json.loads(
                compress.read_bytes(catalog.data_root(con) / row[5])))
            if row and row[5] else {})
    return VersionList(uri=catalog.BASE + basefile, versions=[
        VersionInfo(version=v, uri=vuri, url=layout.page_url(vuri),
                    ikraft=info.get(v, (None, []))[0],
                    forarbeten=info.get(v, (None, []))[1])
        for v, vuri in history.versions(basefile)])


@app.get("/api/v1/document/diff", response_class=HTMLResponse, tags=["document"])
def diff_endpoint(uri: str = Query(..., description="full lagen.nu statute uri"),
                  from_version: str = Query(..., alias="from",
                                            description="older version id, e.g. 2003:466"),
                  to: str | None = Query(None, description="newer version id "
                                         "(default: the current consolidation)")):
    """Compare two consolidations of a statute: the newer text in document
    order with every difference from the older marked up (<ins>/<del>) -- an
    HTML fragment, ready to swap into the page (the old ?diff=true view).
    Version ids are consolidation cutoffs from /api/v1/document/versions.
    Direction is always older -> newer regardless of argument order (the
    current consolidation is by definition newest); the fragment leads with a
    note naming both endpoints."""
    basefile = _sfs_basefile(uri)
    _validate_version_id(from_version)
    if to is not None:
        _validate_version_id(to)
    if to is not None and \
            layout.sfs_version_key(from_version) > layout.sfs_version_key(to):
        from_version, to = to, from_version
    html = _cached_diff_html(basefile, from_version, to)
    note = ('<div class="diff-note">Ändringar från lydelsen enligt '
            'SFS %s till %s. <ins>Tillagd</ins> och <del>borttagen</del> '
            'text är markerad.</div>'
            % (escape(from_version),
               "lydelsen enligt SFS %s" % escape(to) if to
               else "den gällande lydelsen"))
    return HTMLResponse(note + html)


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


# --------------------------------------------------------------------------
# page facsimiles: an on-demand PNG of one source-PDF page (lib/facsimile),
# rendered lazily at retina resolution and cached to disk. Reached both as
# the documented API endpoint (?uri=&sid=) and at the legacy lagen.nu path
# grammar (/prop/2022/23:10/sid1.png), which predates the API. Enabled for
# every page-oriented PDF source: each resolver maps a uri-local document id
# to (source, build-basefile, pdf path) from layout rules + the downloaded
# record -- adding a source is one resolver.
# --------------------------------------------------------------------------

# a förarbete basefile as it appears in a uri path: "prop/2013/14:116" (the
# riksmöte types carry an extra slash), "sou/2021:82", "bet/2020/21:JuU25";
# the type whitelist is the harvest vocabulary + bet
_RE_FA_BASEFILE = re.compile(
    r"^(%s|bet)/(\d{4}(?:/\d{2,4})?:[A-Za-zÅÄÖ]*\d+[a-z]?)$"
    % "|".join(regeringen.TYPES))
# a föreskrift: "<fs>/<year>:<löpnr>" ("mcffs/2026:1")
_RE_FS_BASEFILE = re.compile(r"^([a-zåäö]+)/(\d{4}:\d+)$")
# an avgörande: "avg/<org>/<dnr>" ("avg/jo/2340-2025", "avg/jk/2024/8082")
_RE_AVG_BASEFILE = re.compile(r"^avg/([a-z]+)/([A-Za-z0-9/-]+)$")


def _fa_pdf(local):
    m = _RE_FA_BASEFILE.match(local)
    if not m:
        return None
    typ, num = m.group(1), m.group(2)
    basefile = "%s/%s" % (typ, basefile_slug(num))
    record_path = layout.fa_record(basefile)
    if not compress.exists(record_path):
        return None
    record = json.loads(compress.read_text(record_path))
    pdfs = ([layout.FA_DOWNLOADED / typ / f
             for f in record.get("files", []) if f.lower().endswith(".pdf")]
            or [config.LEGACY_ROOT / f
                for f in record.get("legacy_files", [])
                if f.lower().endswith(".pdf")])
    if pdfs:
        return ("forarbete", basefile, pdfs[0])
    # no PDF body, but the document may still have a page-image scan beside its
    # record (the KB propkb facsimiles -- forarbete/propkb.py). Resolved by rule
    # + existence, like the mirrored SFS PDFs in `_sfs_pdf`: it is a facsimile
    # source, not a parse input, so it is deliberately not named in the record.
    scan = layout.fa_facsimile_pdf(typ, m.group(2))
    return ("forarbete", basefile, scan) if scan.exists() else None


def _foreskrift_pdf(local):
    m = _RE_FS_BASEFILE.match(local)
    if not m or m.group(1) in regeringen.TYPES:
        return None
    fs = m.group(1)
    record_path = (layout.FORESKRIFT_DOWNLOADED / fs
                   / (basefile_slug(local) + ".json"))
    if not compress.exists(record_path):
        return None
    # the page anchors come from the `regulation` PDF (the body foreskrift's
    # parse reads), so that is the one a facsimile must rasterize
    regulation = json.loads(compress.read_text(record_path))["files"].get("regulation")
    if not regulation:
        return None
    return ("foreskrift", local, layout.FORESKRIFT_DOWNLOADED / fs
            / regulation["name"])


def _avg_pdf(local):
    m = _RE_AVG_BASEFILE.match(local)
    if not m:
        return None
    basefile = local[len("avg/"):]
    pdf = (layout.AVG_DOWNLOADED / m.group(1)
           / (basefile_slug(basefile) + ".pdf"))
    return ("avg", basefile, pdf) if pdf.exists() else None


# an SFS: a bare "<year>:<löpnr>" ("2002:780"), no source prefix -- the
# officially published PDF the mirror fetched (pdfmirror), facsimile source for
# both a full published page and a sfs-graphic crop
_RE_SFS_BASEFILE = re.compile(r"^\d{4}:\d+[a-z]?$")


def _sfs_pdf(local):
    if not _RE_SFS_BASEFILE.match(local):
        return None
    pdf = layout.sfs_pdf(local)
    return ("sfs", local, pdf) if pdf.exists() else None


_PDF_RESOLVERS = (_fa_pdf, _avg_pdf, _foreskrift_pdf, _sfs_pdf)

# immutable: the PDF a facsimile renders from never changes in place (a
# re-download replaces the record wholesale), so clients may cache forever
_FAX_HEADERS = {"Cache-Control": "public, max-age=31536000, immutable"}


def _facsimile_response(local, sid):
    """The facsimile PNG for page `sid` of the document at uri-local path
    `local` ("prop/2013/14:116"), rendering into the disk cache on first
    request."""
    if ".." in local or sid < 1:
        raise HTTPException(404, "no such document: %r" % local)
    resolved = next(filter(None, (r(local) for r in _PDF_RESOLVERS)), None)
    if resolved is None:
        raise HTTPException(404, "no PDF source downloaded for %r" % local)
    source, basefile, pdf = resolved
    try:
        png = facsimile.cached_page(source, basefile, pdf, sid)
    except subprocess.CalledProcessError as exc:
        # poppler exit codes (see `man pdftoppm`): 1 is "error opening a PDF
        # file" -- the source is corrupt, a corpus data-integrity problem
        # that must fail loudly, not read as a client 404. 99 ("other
        # error") is what an out-of-range -f/-l page range produces -- a
        # genuinely missing page, so that alone is a 404.
        if exc.returncode == 1:
            raise
        raise HTTPException(404, "%r has no page %d" % (local, sid)) \
            from None
    return FileResponse(png, media_type="image/png", headers=_FAX_HEADERS)


@app.get("/api/v1/facsimile", response_class=FileResponse, tags=["document"],
         responses={200: {"content": {"image/png": {}}}})
def facsimile_endpoint(
        uri: str = Query(..., description="full lagen.nu document uri"),
        sid: int = Query(..., ge=1, description="printed page number "
                         "(the #sid{N} anchor)")):
    """A facsimile PNG of one printed page of the document's source PDF
    (förarbeten, myndighetsföreskrifter, avgöranden), rendered at retina
    resolution (150 DPI) on first request and cached on disk."""
    return _facsimile_response(catalog.local(catalog.strip_fragment(uri)), sid)


# the legacy path grammar in its two arities: riksmöte-numbered förarbeten and
# avgöranden carry an extra slash ("/prop/2022/23:10/sid1.png",
# "/avg/jo/2340-2025/sid1.png"); year-numbered ids do not
# ("/sou/2021:82/sid1.png", "/mcffs/2026:1/sid1.png")
@app.get("/{a}/{b}/{c}/sid{sid:int}.png", include_in_schema=False)
def facsimile_legacy_3(a: str, b: str, c: str, sid: int):
    return _facsimile_response("%s/%s/%s" % (a, b, c), sid)


@app.get("/{a}/{b}/sid{sid:int}.png", include_in_schema=False)
def facsimile_legacy_2(a: str, b: str, sid: int):
    return _facsimile_response("%s/%s" % (a, b), sid)


# sfs-graphic: a crop of the graphic/formula/map the consolidated SFS text drops
# but the published PDF carries. Unlike a facsimile the client sends only the
# viewed statute + gap id; the reviewed .graphics layer holds the geometry AND
# the provenance -- which amending SFS's PDF the region is cropped from (the act
# that last set that wording), not the viewed statute's own PDF.
def _sfs_graphic_response(local, node):
    """The cropped PNG for gap `node` of the SFS at uri-local `local`, its page,
    bbox and source PDF read from the statute's .graphics layer."""
    if ".." in local or not _RE_SFS_BASEFILE.match(local):
        raise HTTPException(404, "not an SFS document: %r" % local)
    layer = annstore.path("sfs", local, ".graphics")
    if not layer.exists():
        raise HTTPException(404, "no graphics layer for %r" % local)
    content = json.loads(layer.read_text())
    entry = content.get(node)
    if entry is None:
        raise HTTPException(404, "no graphic %r in %r" % (node, local))
    if (content.get("meta", {}).get("status") != annstore.VERIFIED
            and not entry.get("verified")):
        raise HTTPException(404, "graphic %r in %r is not verified" % (node, local))
    # the amending SFS whose published PDF carries the region (provenance)
    src, page, bbox = entry["sfs"], entry["page"], entry.get("bbox")
    assert isinstance(src, str) and _RE_SFS_BASEFILE.fullmatch(src), \
        "%s/%s: invalid graphics source %r" % (local, node, src)
    assert isinstance(page, int) and not isinstance(page, bool) and page > 0, \
        "%s/%s: invalid graphics page %r" % (local, node, page)
    if bbox is not None:
        assert facsimile.valid_bbox(bbox), \
            "%s/%s: invalid graphics bbox %r" % (local, node, bbox)
    pdf = layout.sfs_pdf(src)
    if not pdf.exists():
        raise HTTPException(404, "source SFS %s is not mirrored" % src)
    try:
        png = (facsimile.cached_region("sfs", src, pdf, page, bbox) if bbox
               else facsimile.cached_page("sfs", src, pdf, page))
    except subprocess.CalledProcessError as exc:
        if exc.returncode == 1:      # corrupt source PDF -- corpus integrity
            raise
        raise HTTPException(404, "SFS %s has no page %d" % (src, page)) \
            from None
    return FileResponse(png, media_type="image/png", headers=_FAX_HEADERS)


@app.get("/api/v1/sfs-graphic", response_class=FileResponse, tags=["document"],
         responses={200: {"content": {"image/png": {}}}})
def sfs_graphic_endpoint(
        uri: str = Query(..., description="full lagen.nu SFS uri"),
        node: str = Query(..., description="stable graphic-gap key (the "
                          "data-grafik value, e.g. g-a1b2…)"),
        v: str = Query(None, description="opaque cache-buster (the bbox "
                       "version); accepted and ignored")):
    """A PNG crop of a graphic/formula/map the consolidated SFS text omits,
    cut from the published PDF of the amendment that set it (per the reviewed
    .graphics layer), rendered at 150 DPI on first request and cached."""
    return _sfs_graphic_response(catalog.local(catalog.strip_fragment(uri)), node)


@app.get("/api/v1/dumps", response_model=list[DumpInfo], tags=["catalog"])
def dumps_endpoint():
    """The available NDJSON bulk dumps (one per source), for machine consumers."""
    if not DUMPS.exists():
        return []
    return [DumpInfo(source=p.name.split(".", 1)[0], file=p.name,
                     bytes=p.stat().st_size)
            for p in sorted(DUMPS.glob("*.ndjson.gz"))]


def _accept_encoding(scope):
    """The `Content-Encoding` tokens the client will take, from the request's
    Accept-Encoding header. `*` matches any (so a wildcard accepts br/gzip)."""
    for key, value in scope.get("headers", ()):
        if key == b"accept-encoding":
            tokens = {tok.split(b";", 1)[0].strip().decode("latin-1")
                      for tok in value.split(b",")}
            if "*" in tokens:
                tokens |= {enc for enc, _ in compress.ENCODINGS}
            return tokens
    return set()


class SiteFiles(StaticFiles):
    """StaticFiles serving the site at lagen.nu's URI grammar, over the
    precompressed generated/ tree (lib/compress).

    Two things layered on plain StaticFiles:

    * **Precompression.** Pages/assets are stored as `.br` (+ `.gz`), not plain
      (see compress). For each request the best variant the client accepts is
      served *as-is* with `Content-Encoding` + `Vary` -- exactly what nginx's
      `brotli_static`/`gzip_static` would do, so the app and a future nginx-direct
      config behave identically. A client that accepts neither is handed the
      decompressed bytes (nginx would need the plain file; the app just decodes).
      Tiny files kept plain (the size floor) are served by StaticFiles directly.
    * **URI grammar.** A document's bare public URL (/2018:585, /prop/2020/21:22,
      /dom/ad/1993:100, /celex/61954CJ0001) is, on a static miss, rewritten to its
      flattened on-disk file via layout.url_to_relpath, and a directory maps to
      its index.html -- nginx's try_files rules, in Starlette."""

    async def get_response(self, path, scope):
        # a plain file / directory index that StaticFiles serves outright (tiny
        # assets kept uncompressed) wins first; a path never has both a plain and
        # a compressed representation, so a 200 here is authoritative.
        try:
            resp = await super().get_response(path, scope)
            if resp.status_code != 404:
                return resp
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
        accepts = _accept_encoding(scope)
        for rel in self._candidates(path):
            served = await self._serve(rel, accepts, scope)
            if served is not None:
                return served
        raise StarletteHTTPException(404)

    def _candidates(self, path):
        """The logical relpaths a request may resolve to, in order: the path
        itself, its directory index, and the bare-document-URL rewrite."""
        seen = []
        def add(rel):
            if rel and rel not in seen:
                seen.append(rel)
        base = path.rstrip("/")
        if base:
            add(base)
            add(base + "/index.html")           # a browse directory's index
        else:
            add("index.html")                   # the site root
        if path and not path.endswith(".html"):
            add(layout.url_to_relpath(path))     # /2018:585 -> 2018:585.html
        return seen

    async def _serve(self, rel, accepts, scope):
        """A response for logical `rel` -- its best precompressed variant, else a
        plain file StaticFiles serves, else None (nothing on disk)."""
        variants = compress.variants_on_disk(self.directory, rel)
        if variants:
            media_type = compress.media_type(rel)
            for enc, _suffix in compress.ENCODINGS:      # br preferred, then gzip
                if enc in accepts and enc in variants:
                    full, st = variants[enc]
                    resp = FileResponse(full, stat_result=st, media_type=media_type)
                    resp.headers["Content-Encoding"] = enc
                    resp.headers["Vary"] = "Accept-Encoding"
                    return resp
            # client accepts no stored encoding: decode one and serve identity
            enc, (full, _st) = next(iter(variants.items()))
            data = compress.decompress_bytes(Path(full).read_bytes(), enc)
            return Response(data, media_type=media_type,
                            headers={"Vary": "Accept-Encoding"})
        try:
            resp = await super().get_response(rel, scope)
            return resp if resp.status_code != 404 else None
        except StarletteHTTPException as exc:
            if exc.status_code == 404:
                return None
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
    # app-level loggers (notably api.mcp's per-tool-call lines) go to stdout
    # alongside uvicorn's access log -- uvicorn only configures its own loggers,
    # so without a root handler those lines vanish
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s:     %(name)s: %(message)s")
    app.mount("/", SiteFiles(directory=directory, html=True), name="site")
    # proxy_headers so the app sees the real client IP/scheme/host behind the
    # prod TLS proxy (nginx must send X-Forwarded-For/-Proto) -- notably,
    # api/auth.py's per-IP login rate limit keys on `request.client.host`,
    # which would otherwise be nginx's own address for every request. The
    # session cookie's Secure flag is an explicit config switch
    # (config.COOKIE_SECURE), not derived from this header.
    # forwarded_allow_ips defaults to 127.0.0.1, the proxy on the same host.
    uvicorn.run(app, host=host, port=port, proxy_headers=True)
