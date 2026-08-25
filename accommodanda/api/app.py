"""The public REST/OpenAPI service (REWRITE.md §6) -- the machine-readable face
of the corpus that replaces Fuseki's SPARQL endpoint.

Everything in *this* module is public: read-only, callable from any origin, and
described by `/docs` + `/openapi.json`. The site's own surface -- login, the
editors, the PDF export's background jobs, the ops dashboard -- is a second app
mounted at `/internal-api/v1` (api/internal.py), out of the public schema and
same-origin only. docs/api/README.md is the prose contract for the public half;
accommodanda/api/README.md is the developer's tour of both.

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

import logging
import os
import re
import sqlite3
import threading
from contextlib import asynccontextmanager
from html import escape
from pathlib import Path
from typing import Literal

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# StaticFiles.get_response raises Starlette's HTTPException (FastAPI's is a
# subclass, so it would not catch the parent) -- the SiteFiles rewrite catches this
from starlette.datastructures import Headers
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import NotModifiedResponse

from .. import config
from ..lib import (
    catalog,
    compress,
    diff,
    facets,
    facsimile,
    feeds,
    history,
    layout,
    mdtext,
    pathgraph,
    search,
)
from . import (
    analytics,
    db,
    errors,
    facsimiles,
    internal,
    ops,
    paths,
    pdf,
    pdfcollection,
    pdfjob,
    reads,
)
from . import mcp as mcp_server
from .db import get_con

DUMPS = config.DATA / "dumps"

# The blurb at the top of /docs. It carries the three facts a first-time caller
# needs before reading any single endpoint; docs/api/README.md is the long form.
DESCRIPTION = """
Search, the catalog and the citation graph over the Swedish legal corpus:
statutes (SFS), case law, preparatory works, agency regulations, EU law and
Council of Europe / ECHR material.

**A document's canonical uri is its identity everywhere.** The published
`https://lagen.nu/<id>` uri is at once this API's key (`?uri=…`), the bulk-dump
line id and the search index `_id`, and it is stable across versions. Uris are
always a `uri` query parameter, never a path segment -- they contain `:` and
`/`.

**The JSON artifact is the source of truth.** The catalog and the search index
are derived from it and rebuildable. `GET /api/v1/document` hands it back
verbatim, and each line of a bulk dump *is* one.

**Everything here is read-only and open to any origin** (`GET`, `*`). An error
is `{"detail": …}`; a 404 or a 5xx adds an `error_id` that names the entry in
the server's own error ledger, worth quoting in a bug report -- `null` on the
rare occasion the ledger itself could not be written. (On a 422 the `detail` is
FastAPI's list of validation errors, not a message.)

For reprocessing the whole corpus use the NDJSON dumps rather than paging these
endpoints -- `GET /api/v1/dumps` is the manifest.
"""

TAGS = [
    {"name": "search",
     "description": "Full text plus citation resolution. One endpoint; the "
                    "⌘K palette uses no other."},
    {"name": "catalog",
     "description": "What the corpus holds: sources, navigation facets and "
                    "the bulk-dump manifest."},
    {"name": "document",
     "description": "One document: its parsed body, its citations in both "
                    "directions, its versions, and its pages as images or PDF."},
]

@asynccontextmanager
async def _lifespan(application):
    # start loading the path graph before anyone asks for it -- the load runs
    # in its own thread (api/paths), never under a request, and /path answers
    # 503 until it lands. Skipped when no catalog is built (dev serve on an
    # empty tree).
    if db.catalog_ready():
        paths.graph_if_ready(db.CATALOG)
    async with mcp_server.lifespan(application):
        yield


class UTF8JSONResponse(JSONResponse):
    """JSONResponse declaring its charset. The body is UTF-8 with non-ASCII
    literals (`ensure_ascii=False`), but bare `application/json` makes a
    browser viewing the raw answer guess -- and Safari guesses Latin-1,
    rendering "säkerhetsskyddslagen" as "sÃ¤kerhetsskyddslagen". Starlette
    appends the charset only for text/* media types, so JSON states it here."""
    media_type = "application/json; charset=utf-8"


app = FastAPI(
    title="lagen.nu API",
    version="1.0",
    description=DESCRIPTION,
    openapi_tags=TAGS,
    default_response_class=UTF8JSONResponse,
    # warms the path graph, then runs the MCP server's Streamable HTTP
    # session manager (a no-op for the in-process TestClient path, which
    # never calls /mcp) -- see api/mcp.py
    lifespan=_lifespan,
)

# the generated static site (served on another port) reaches the API from the
# browser via the ⌘K palette -- a cross-origin GET. The API is public read-only
# data, so any origin may read it. This covers the mounted /internal-api too,
# which is why that app carries a same-origin gate of its own: CORS only stops
# a cross-origin browser from *reading* a response, and half the internal
# surface is a GET whose body is nobody else's business.
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["GET"], allow_headers=["*"])

# FastAPI serves the interactive docs at exactly /docs and /redoc, and the ops
# dashboard sits at exactly /ops; the trailing-slash forms are different paths.
# Starlette's own redirect_slashes never fires for them because serve() mounts
# the static site at "/", so every such path matches something and /docs/ 404s
# instead -- which is what a reader who types the directory-looking form gets.
# Redirect them by hand, ahead of that mount. 308 rather than 307: these are
# permanent and GET-only. /ops/ joined them because the editor's own login sent
# the user there, so every successful login ended on a 404 (assets/editor.js).
app.add_route("/docs/", lambda request: RedirectResponse("/docs", 308))
app.add_route("/redoc/", lambda request: RedirectResponse("/redoc", 308))
app.add_route("/ops/", lambda request: RedirectResponse("/ops", 308))

# the ops dashboard (/ops*), registered like /api/v1 -- before the SiteFiles
# mount added in serve(), so its explicit routes win over the static catch-all.
# Its routes are out of the public schema and same-origin only (api/ops.py).
app.include_router(ops.router)

# the internal API: login, the three editors and the PDF export's background
# jobs, at /internal-api/v1 (api/internal.py). A mounted app rather than a
# router, so its routes cannot reach the public /openapi.json at all and its
# own schema is a separate document. Mounted here, before serve()'s static "/"
# catch-all, for the same reason /api/v1 is declared here. Nothing of it is
# declared in this module -- api/internal.py lists every route it serves.
app.mount(internal.PREFIX, internal.app, name="internal-api")

# the public MCP server at /mcp (Streamable HTTP) -- the corpus reshaped as tools
# for AI hosts. Added before serve()'s static "/" catch-all so its routes win.
mcp_server.mount(app)

# rendered 404/500 pages for the site, JSON (plus an error_id) for the API, and
# a ledger entry behind both -- see api/errors.py and `lagen all errors`
errors.install(app)

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


# --------------------------------------------------------------------------
# response models
#
# These *are* the published schema: every field description below is what a
# reader of /docs and /openapi.json gets, so they say what the value means to a
# consumer, not how the handler produced it. docs/api/README.md is the same
# contract in prose -- keep the two in step.
# --------------------------------------------------------------------------

class Fragment(BaseModel):
    """A place *inside* a document: the uri plus what to call it. Follow it as
    `SearchResult.url + "#" + pinpoint`."""

    uri: str = Field(description="the fragment uri, e.g. "
                     "https://lagen.nu/1975:635#P6")
    pinpoint: str | None = Field(
        None, description="the anchor within the document (\"P6\", \"K4P7\", "
        "\"A32\") -- the part after the #")
    label: str | None = Field(
        None, description="what names this place: the pinpoint written the way "
        "a reader cites it (\"4 kap. 4 §\", \"artikel 32\"), or -- where the "
        "anchor has no citation grammar, as a förarbete's \"sec745\" has none "
        "-- the heading the document prints over that section. Null where "
        "there is neither.")
    highlight: list[str] = Field(
        [], description="query matches in this passage, as HTML with the "
        "matched terms in <em>")


class SearchResult(BaseModel):
    """One hit. `url` is where to send a reader, unless `pin` is set -- then it
    is `url + "#" + pin.pinpoint`."""

    uri: str = Field(description="the document's canonical uri -- its id "
                     "everywhere: this API's ?uri=, the dump line, the search "
                     "index _id")
    url: str | None = Field(
        None, description="the public page path (/1975:635, /dom/nja/2015s1). "
        "Null for a document the site does not host a page for.")
    identifier: str | None = Field(
        None, description="the document's own printed id (\"1975:635\", "
        "\"NJA 2015 s. 1\")")
    title: str | None = Field(None, description="its full title")
    display: str | None = Field(
        None, description="the reader-facing heading: the short name plus its "
        "acronym where there is one, else the title")
    source: str | None = Field(
        None, description="which corpus it comes from (sfs, dv, forarbete, …)")
    kind: str | None = Field(
        None, description="the document kind within that source (lag, "
        "forordning, case, prop, …)")
    kind_label: str | None = Field(
        None, description="what `kind` is called to a reader (\"Lagrådsremiss\", "
        "\"Betänkande\"). A hit whose identifier is its own title -- every "
        "förarbete published without a series number -- has nothing else to "
        "say what sort of document it is.")
    score: float | None = Field(
        None, description="relevance, combining text match with citation "
        "count. Null for a pinned hit, which was resolved rather than ranked.")
    inbound_count: int = Field(
        0, description="how many catalogued documents cite this one -- the "
        "same graph /document/inbound serves in full")
    highlight: list[str] = Field(
        [], description="the document's own snippet: what this hit is about. "
        "Always the document's, never a passage's.")
    pin: Fragment | None = Field(
        None, description="the provision a citation-shaped query resolved to "
        "(\"avtalslagen 36 §\"). The one thing that moves the link off the "
        "document: follow `url + \"#\" + pin.pinpoint` when a pin is set, and "
        "`url` otherwise.")
    fragments: list[Fragment] = Field(
        [], description="the passages inside this document where the query "
        "matched, most relevant first. Supporting detail to show under the "
        "hit -- never its link target, since a word can stand in a document "
        "for reasons that are not what the reader asked for "
        "(\"dataförordningen\" stands in article 47 of the EU Data Act because "
        "that article amends another regulation by quoting its title).")


class SearchFacetBucket(BaseModel):
    """One value of one facet, with how many hits carry it. Render
    `label or value`."""

    value: str = Field(description="the raw facet value (\"sfs\", \"bet\", "
                       "\"2024\")")
    count: int = Field(description="hits with this value, counted against the "
                       "*other* selected filters -- so the number stays usable "
                       "for widening the search")
    label: str | None = Field(
        None, description="the reader-facing name for `value`, from the same "
        "facet schemes the browse pages use. Null when the value is its own "
        "label, as a year is.")


class SearchResponse(BaseModel):
    """The answer to /search. Page it with `next_cursor`; `offset` is the
    bounded random-access alternative."""

    query: str = Field(description="the query as asked, echoed back")
    total: int = Field(description="matching documents, before paging")
    next_cursor: str | None = Field(
        None, description="opaque cursor for the next page; null once the last "
        "page is reached")
    facets: dict[str, list[SearchFacetBucket]] = Field(
        {}, description="bucket counts per facet field (source, kind, year)")
    results: list[SearchResult]


class Citation(BaseModel):
    """One citation a document makes -- the outbound direction."""

    uri: str = Field(description="the cited target: a document uri or a "
                     "fragment of one")
    anchor: str | None = Field(
        None, description="where in the citing document the citation sits")
    predicate: str | None = Field(
        None, description="the relation, when it is a typed one "
        "(rpubl:bemyndigande, rpubl:andrar, rpubl:upphaver); "
        "dcterms:references is the plain reference")
    text: str | None = Field(
        None, description="the citation's own surface text, as the citing "
        "document wrote it (\"6 § räntelagen\")")
    label: str | None = Field(None, description="the target's printed id; null "
                              "when the target is not hosted")
    title: str | None = Field(None, description="the target's title; null when "
                              "the target is not hosted")
    source: str | None = Field(None, description="the target's corpus; null "
                               "when the target is not hosted")
    hosted: bool = Field(True, description="false when the cited document is "
                         "not (yet) in the corpus -- the citation is real, the "
                         "target is not held here")


class InboundCitation(BaseModel):
    """One citation *into* a document. Distinct from `Citation` because the
    inbound direction has a field the outbound one cannot have: `target`, the
    provision the citation landed on, which is the whole answer when the query
    was a law rather than a paragraf. `hosted` has no inbound counterpart -- a
    citer is a catalogued document or it would not be here."""

    uri: str = Field(description="the citing document")
    target: str = Field(description="what it cited: the queried uri or a "
                        "fragment of it")
    anchor: str | None = Field(
        None, description="where in the citing document the citation sits")
    page: int | None = Field(
        None, description="the printed page it sits on, where the citing "
        "document has pages (förarbeten, föreskrifter, avgöranden)")
    predicate: str | None = Field(
        None, description="the relation, when it is a typed one "
        "(rpubl:bemyndigande, rpubl:andrar, rpubl:upphaver)")
    label: str | None = Field(None, description="the citing document's printed id")
    title: str | None = Field(None, description="its title")
    source: str | None = Field(None, description="its corpus")
    kind: str | None = Field(None, description="its document kind")
    date: str | None = Field(None, description="its date (ISO 8601), where the "
                             "source records one")
    inbound_count: int = Field(
        description="how many documents cite the *citing* document -- its "
        "own authority signal, so a caller can rank \"the leading cases on "
        "this paragraf\" without a lookup per row. Same number and same name "
        "as /search and /document answer with. It is how often a document is "
        "cited, which correlates with authority but is not the same thing: it "
        "favours an old case over a recent one, and it can only count what "
        "this corpus holds. Order by it with ?sort=citations.")


class InboundCitations(BaseModel):
    """Who cites a document. One row per (citing document, citing spot,
    provision cited) -- unreduced, so a document that cites the same provision
    five times is five rows."""

    uri: str = Field(description="the uri asked about, echoed back")
    scope: str = Field(description="the scope asked for, echoed back")
    source: str | None = Field(None, description="the citing-side filter, "
                               "echoed back")
    sort: str = Field(description="the order asked for, echoed back")
    total: int = Field(description="rows matching the scope and filter, before "
                       "paging")
    limit: int
    offset: int
    by_source: dict[str, int] = Field(
        description="{source: rows} over the whole scope, not the page -- so a "
        "client that took the first 10 000 of brottsbalken's 162 909 can still "
        "see what the rest is made of, and page towards it, instead of "
        "inferring the corpus from a slice")
    citations: list[InboundCitation]


class DocumentSummary(BaseModel):
    """A document as it appears in a listing: its id and top-level metadata,
    without its body. Fetch the body with /document?uri=…"""

    uri: str = Field(description="the canonical uri -- the key for every other "
                     "endpoint")
    source: str = Field(description="which corpus it comes from")
    kind: str | None = Field(None, description="the document kind within it")
    label: str | None = Field(None, description="its printed id (\"2018:585\")")
    title: str | None = Field(None, description="its full title")
    source_url: str | None = Field(
        None, description="the publisher's own page for the document (the "
        "\"Källa\" link), where one is derivable")
    updated: str | None = Field(
        None, description="when the artifact was last built (ISO 8601 UTC). "
        "Null for a stub with no artifact of its own.")


class DocumentList(BaseModel):
    """One page of the catalog enumeration."""

    total: int = Field(description="documents matching the filter, before paging")
    limit: int
    offset: int
    documents: list[DocumentSummary]


class DocumentMeta(BaseModel):
    """The metadata head every document answer carries."""

    uri: str = Field(description="the canonical uri")
    source: str = Field(description="which corpus it comes from")
    kind: str | None = Field(None, description="the document kind within it")
    label: str | None = Field(None, description="its printed id")
    title: str | None = Field(None, description="its full title")
    inbound_count: int = Field(description="how many catalogued documents cite it")


class Document(DocumentMeta):
    """A document: its metadata plus the parsed artifact itself."""

    source_url: str | None = Field(
        None, description="the publisher's own page for the document")
    artifact: dict = Field(
        description="the on-disk artifact JSON, verbatim -- the same object a "
        "bulk-dump line carries, and the source of truth everything else here "
        "is derived from. Each source owns its shape, but every renderable "
        "text value is a list of inline runs: a plain string, or a link dict "
        "{predicate, uri, text}. Those link dicts are the citation graph. See "
        "the artifact-format section of docs/api/README.md.")


class MarkdownDocument(DocumentMeta):
    """A document: its metadata plus the body rendered as markdown."""

    source_url: str | None = Field(
        None, description="the publisher's own page for the document")
    markdown: str = Field(
        description="the document body as markdown: title, headings, "
        "paragraph designations, lists and tables, with every citation as an "
        "inline [text](uri) link. A lossy reading text derived from the "
        "artifact -- the artifact (format=json) stays the source of truth.")


class BrowseDoc(BaseModel):
    """A leaf entry in a browse listing -- what one line of a generated browse
    page says. Only /browse populates these; /facets stops at the counts."""

    uri: str = Field(description="the canonical uri")
    url: str = Field(description="the hosted page path (/2018:585, /dom/nja/…)")
    display: str = Field(description="the listing handle: law name, short "
                         "label, or bare id")
    short_id: str | None = Field(
        None, description="the bare id shown as the bold linked term")
    short_title: str | None = Field(None, description="the short human name")
    description: str | None = Field(
        None, description="the source's one-line description -- a case's "
        "sammanfattning")
    variant: str | None = Field(
        None, description="dv only: the case-law form (dom/referat/notis) the "
        "listing groups under")
    date: str | None = Field(
        None, description="dv only: the avgörandedatum bare domar sort by")
    pre: str | None = Field(
        None, description="sfs only: the subdued designation/number prefix of "
        "the title")
    key: str | None = Field(
        None, description="sfs only: the emphasised sort subject of the title")
    subdued: bool | None = Field(
        None, description="sfs only: false for primary law, true for the rest "
        "(rendered subdued)")
    year: str | None = Field(None, description="sfs only: its year")
    amendments: list["BrowseDoc"] | None = Field(
        None, description="föreskrift only: the ändringsförfattningar nested "
        "under their base regulation")
    consolidated: bool | None = Field(
        None, description="föreskrift only: this entry is the konsoliderade "
        "version, and the text as promulgated lives at <uri>/grund")


class FacetBucket(BaseModel):
    """One navigation bucket: a court, a year, a subject initial. Buckets nest
    at most two levels deep."""

    key: str = Field(description="the raw bucket key (\"nja\", \"2024\", \"A\")")
    label: str = Field(description="its display label (\"NJA – Högsta "
                       "domstolen\")")
    slug: str = Field(description="its URL path segment on the site")
    count: int = Field(description="documents in this bucket, children included")
    children: list["FacetBucket"] | None = Field(
        None, description="the next facet level, where there is one")
    documents: list[BrowseDoc] | None = Field(
        None, description="the leaf listing. Populated by /browse only; always "
        "null from /facets.")


class FacetTree(BaseModel):
    """A source's whole navigation model: which axes it is filed by, where a
    reader lands, and the buckets themselves."""

    source: str = Field(description="the source asked about, echoed back")
    levels: list[str] = Field(description="the facet axis names, outer first "
                              "(e.g. [\"court\", \"year\"])")
    default: list[str] = Field(description="the landing bucket's key path")
    buckets: list[FacetBucket]


class SourceInfo(BaseModel):
    """One corpus and how much of it there is."""

    source: str = Field(description="the source name, as every ?source= "
                        "parameter takes it")
    documents: int = Field(description="catalogued documents in it")


class DumpInfo(BaseModel):
    """One bulk dump. This is a manifest entry, not a download link -- the
    files are served beside the API at /dumps/<file>."""

    source: str = Field(description="the source the dump holds")
    file: str = Field(description="its filename, e.g. sfs.ndjson.gz")
    bytes: int = Field(description="its size on disk")


# --------------------------------------------------------------------------
# endpoints
# --------------------------------------------------------------------------

@app.get("/api/v1/search", response_model=SearchResponse, tags=["search"],
         summary="Search the corpus, with citations resolved")
def search_endpoint(
        q: str = Query(..., description="free-text query"),
        source: str | None = Query(None, description="restrict to one source "
                                   "-- any name /api/v1/sources lists (sfs, "
                                   "dv, forarbete, eurlex, hudoc, …)"),
        kind: str | None = Query(None, description="restrict to a document "
                                 "kind within the source (lag, forordning, "
                                 "case, prop, sou, directive, …)"),
        year: str | None = Query(None, pattern=r"^\d{4}$",
                                 description="restrict to a four-digit publication/decision year"),
        limit: int = Query(10, ge=1, le=100),
        offset: int | None = Query(None, ge=0, le=9900,
                            description="bounded random access, raw result "
                            "stream (no related-hit decluttering); omit it "
                            "and page by cursor for the decluttered stream"),
        cursor: str | None = Query(None, max_length=2048,
                                   description="opaque cursor returned by the previous page")):
    """Full-text search, with a citation-aware twist: when the query reads as a
    citation (a law nickname/abbreviation + pinpoint, an EU act + article, or a
    case nickname), the exact resource is resolved and pinned as the first
    result -- so ⌘K + Enter lands on the right §/article, which plain full-text
    can't do (the name appears nowhere in the text). That resolved provision is
    the hit's `pin`, and it is the only thing that moves a hit's link off the
    document. The rest is the usual full-text ranking (relevance combined with
    citation count); each hit carries the document's own snippet plus, in
    `fragments`, the passages inside it where the query matched.

    Resolution runs on the first page only, and is best-effort: an unbuilt
    catalog costs the pin, not the search.

    Paging: follow `next_cursor` until it comes back null. `offset` is the
    bounded random-access alternative (up to 9 900) and the two are mutually
    exclusive. `facets` counts each field against the *other* selected filters,
    so a bucket's count stays a usable answer to "what if I widen this?"; every
    bucket carries a reader-facing `label` where its raw value is not one.

    This is the one endpoint that needs a running OpenSearch and a built index
    -- 503 when either is missing. The catalog endpoints answer without it."""
    if cursor and offset is not None:
        raise HTTPException(422, "cursor and offset are mutually exclusive")
    try:
        res = reads.search(_index, q, source=source, kind=kind, year=year,
                           limit=limit, offset=offset, cursor=cursor)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except reads.SearchUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    kind_label = facets.kind_labels(singular=True)   # one hit, not a bucket
    results = [{**r, "kind_label": kind_label.get(r.get("kind"))}
               for r in res["results"]]
    return SearchResponse(query=q, total=res["total"],
                          next_cursor=res["next_cursor"],
                          facets=_labelled_facets(res["facets"]),
                          # result/facet dicts are validated by pydantic at runtime
                          results=results)


def _labelled_facets(buckets):
    """Name every facet bucket server-side, from the facet schemes (N4). `source`
    and `kind` get reader-facing labels; `year` is its own label."""
    named = {"source": facets.SOURCE_LABELS, "kind": facets.kind_labels()}
    return {field: [dict(b, label=named[field].get(b["value"])) if field in named
                    else b for b in rows]
            for field, rows in buckets.items()}


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
    """Human-readable twin of a legacy Atom feed -- the same page static
    generation writes, so a filtered feed is the site's feed screen too."""
    item, rows, params = _legacy_feed(con, dataset, rdf_type,
                                      rpubl_rattsfallspublikation,
                                      dcterms_publisher)
    return HTMLResponse(feeds.render_page(item, rows, params))


@app.get("/api/v1/facets", response_model=FacetTree, tags=["catalog"],
         summary="A source's navigation buckets and their counts")
def facets_endpoint(
        source: str = Query(..., description="a faceted source: sfs, dv, "
                            "forarbete, foreskrift, avg, rs, begrepp, "
                            "eurlex, edpb, hudoc, coe, icrc, untc, icc, "
                            "icj. A source with no facet scheme is a 404."),
        con: sqlite3.Connection = Depends(get_con)):
    """The navigation facets for a source: the ordered buckets (one or two levels
    -- a law's subject initial, a case's court + year) with document counts, plus
    the default landing bucket. The lightweight navigator; for the listings too
    use /browse. A flat listing of a whole source is too large to be useful."""
    if source not in facets.sources():
        raise HTTPException(404, "source %r is not faceted" % source)
    return FacetTree(**facets.tree(con, source))


@app.get("/api/v1/browse", response_model=FacetTree, tags=["catalog"],
         summary="The same buckets, with each leaf's documents")
def browse_endpoint(
        source: str = Query(..., description="a faceted source: sfs, dv, "
                            "forarbete, foreskrift, avg, rs, begrepp, "
                            "eurlex, edpb, hudoc, coe, icrc, untc, icc, "
                            "icj. A source with no facet scheme is a 404."),
        con: sqlite3.Connection = Depends(get_con)):
    """The complete browse model for a source: the facet navigator *plus* each
    leaf bucket's ordered, display-labelled documents. The single payload the
    static-site generator consumes to write the browse pages -- it has no other
    access to the data store."""
    if source not in facets.sources():
        raise HTTPException(404, "source %r is not faceted" % source)
    return FacetTree(**facets.browse_view(con, source))


@app.get("/api/v1/documents", response_model=DocumentList, tags=["document"],
         summary="Enumerate documents by source and kind")
def documents_endpoint(
        source: str | None = Query(None, description="restrict to one source "
                                   "-- any name /api/v1/sources lists"),
        kind: str | None = Query(None, description="restrict to a document kind "
                                 "within the source (lag, forordning, case, "
                                 "prop, sou, directive, …)"),
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
        include_expired: bool = Query(False, description="also list documents "
                                      "whose repeal has taken effect"),
        con: sqlite3.Connection = Depends(get_con)):
    """List document ids + top-level metadata, filtered by source/kind and
    paginated -- the catalog index that drives /document lookups. This is *not*
    full-text search (that is /search, which requires a query); it enumerates the
    corpus. `source_url` is the publisher's page where known; `updated` is the
    artifact's last-build time.

    A repealed document -- a statute upphävd, an EU act no longer in force, a
    withdrawn ställningstagande -- is left out, so the listing states current
    law; `include_expired=true` puts them back. Either way the document stays
    retrievable by uri and through the citation endpoints."""
    listing = reads.documents(con, source=source, kind=kind,
                              limit=limit, offset=offset,
                              include_expired=include_expired)
    return DocumentList(total=listing["total"], limit=limit, offset=offset,
                        documents=[DocumentSummary(**d)
                                   for d in listing["documents"]])


@app.get("/api/v1/document", response_model=Document | MarkdownDocument,
         tags=["document"],
         summary="One document and its full parsed artifact")
def document_endpoint(uri: str = Query(..., description="full lagen.nu document uri"),
                      format: Literal["json", "md"] = Query(
                          "json", description="body format: 'json' (default) "
                          "returns the parsed artifact verbatim, 'md' the "
                          "body rendered as markdown"),
                      con: sqlite3.Connection = Depends(get_con)):
    """One document, whole: its catalog metadata plus the parsed artifact --
    the structure/body with every citation inline.

    The artifact is the source of truth; everything else this API answers is
    derived from it. Each source owns its own shape (a statute nests into
    kapitel/paragraf/stycke, a förarbete is a flat page-precise list), but one
    rule is universal: every renderable text value is a *list of inline runs*,
    each element either a plain string or a link dict
    `{predicate, uri, text}`. Those link dicts are the citation graph -- the
    catalog is an index over them.

    `format=md` swaps the artifact for the body rendered as markdown --
    headings, paragraph designations, lists, tables, citations as inline
    links -- for consumers that want a reading text (a human, an LLM, a RAG
    chunker) rather than the tree. The envelope and metadata stay JSON.

    The same object comes back per line in the bulk dumps, so a consumer
    reprocessing the whole corpus should take the dumps and never call this
    endpoint in a loop. See docs/api/README.md for the per-source shapes."""
    data = reads.document(con, uri)
    if data is None:
        raise HTTPException(404, "no document %r in the catalog" % uri)
    if format == "md":
        art = data.pop("artifact")
        return MarkdownDocument(**data, markdown=mdtext.document_markdown(
            art, title=data["title"] or data["label"]))
    return Document(**data)


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
    return compress.read_json(path)


class VersionInfo(BaseModel):
    """One archived consolidation (lydelse) of a statute: the text as it stood
    after a named amendment, before the next one."""

    version: str = Field(description="the consolidation cutoff -- the SFS "
                         "number of the amendment this text includes "
                         "(\"2003:466\"). Pass it as ?from= / ?to= to /diff.")
    uri: str = Field(description="the version's own canonical uri")
    url: str = Field(description="its hosted page "
                     "(/1998:204/konsolidering/2003:466)")
    ikraft: str | None = Field(
        None, description="when the cutoff amendment entered force (ISO 8601). "
        "Null where the statute's register does not say.")
    forarbeten: list[str] = Field(
        [], description="the amendment's preparatory works, as printed "
        "(\"Prop. 1997/98:44\")")


class VersionList(BaseModel):
    """A statute's version history."""

    uri: str = Field(description="the statute asked about, echoed back")
    versions: list[VersionInfo] = Field(
        description="oldest first. The current consolidation is excluded -- it "
        "is what /document returns.")


@app.get("/api/v1/document/versions", response_model=VersionList,
         tags=["document"],
         summary="A statute's archived consolidations")
def versions_endpoint(uri: str = Query(..., description="full lagen.nu statute uri"),
                      con: sqlite3.Connection = Depends(get_con)):
    """A statute's archived historical consolidations (lydelser), oldest
    first -- each one browsable at its own page and diffable via
    /api/v1/document/diff. Amendment dates and preparatory works are joined
    in from the statute's register where known.

    Statutes only: SFS is the one source the pipeline consolidates over time,
    so any other uri is a 404. The *current* consolidation is not in the list
    -- it is what /document returns, and it is what /diff compares against when
    `to` is omitted."""
    basefile = _sfs_basefile(uri)
    row = catalog.document(con, catalog.BASE + basefile)
    info = (history.amendment_info(
                catalog.load_artifact(catalog.data_root(con), row[5]))
            if row else {})
    return VersionList(uri=catalog.BASE + basefile, versions=[
        VersionInfo(version=v, uri=vuri, url=layout.page_url(vuri),
                    ikraft=info.get(v, (None, []))[0],
                    forarbeten=info.get(v, (None, []))[1])
        for v, vuri in history.versions(basefile)])


@app.get("/api/v1/document/diff", response_class=HTMLResponse,
         tags=["document"],
         summary="Two consolidations compared, as marked-up HTML")
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


INBOUND_MAX = 10_000            # rows per response; ~3.5 MB of JSON


@app.get("/api/v1/document/inbound", response_model=InboundCitations,
         tags=["document"], summary="Who cites this document")
def inbound_endpoint(uri: str = Query(..., description="document or fragment uri"),
                     scope: str = Query(
                         "tree", pattern="^(tree|exact)$",
                         description="tree: uri and everything inside it "
                                     "(default); exact: only citations naming "
                                     "uri itself"),
                     source: str | None = Query(
                         None, description="only citations from one corpus "
                                           "(dv, forarbete, sfs, …)"),
                     sort: str = Query(
                         "rail", pattern="^(rail|citations)$",
                         description="rail: the context rail's order (default); "
                                     "citations: most-cited citing document "
                                     "first"),
                     limit: int = Query(INBOUND_MAX, ge=1, le=INBOUND_MAX),
                     offset: int = Query(0, ge=0),
                     con: sqlite3.Connection = Depends(get_con)):
    """Which other documents cite `uri` (the killer feature as data) -- one entry
    per (citing document, spot it cites from, provision cited).

    `scope=tree`, the default, answers for the uri **and everything inside it**:
    on a law that is every citation of every paragraf, which is what mirroring
    lagen.nu's own pages takes -- brottsbalken was cited 40 696 times as an act
    and 162 909 times counting its 2 844 cited provisions when this was measured
    (2026-08-07), and reaching those the old way meant one call per provision. `scope=exact` is the narrow question
    (only rows naming `uri` itself), which is what this endpoint used to answer.

    Ordered as the site's context rail orders its panels -- case law first for a
    statute, then decisions, then the citation graph -- so the first page is
    representative rather than whichever source name sorts earliest. That order
    is total and build-independent, so `offset` paging is stable (`sort` can
    change that -- see below).

    **Which of these matter?** Every row carries the citing document's own
    `inbound_count` -- how many documents cite *it* -- so the answer ranks
    itself without a call per row. `sort=citations` orders the whole scope by
    it, biggest first.

    **Pair that with `source=dv` when the question is about case law.** A
    statute or a proposition is cited an order of magnitude more often than any
    judgment, so unfiltered `sort=citations` answers with legislation and
    preparatory works and never reaches a case: on avtalslagen 36 § the top
    three are SFS 1994:1512 (955), Prop. 2007/08:95 (946) and Prop. 2004/05:85
    (681), while the most-cited *case* on that paragraf -- NJA 1987 s. 394, Den
    kollektiva hemförsäkringen -- has 32, and leads once the filter is on. The
    filter is also much the cheaper question: 300 citers against 893.

    Read the count as a hint, not a verdict: it favours an old case over a
    recent one and counts only what this corpus holds.

    `sort=citations` is the one order `offset` paging is **not** stable under
    across rebuilds -- the count is recomputed every build, so a row can move
    between pages as the corpus grows. Ties fall back to the rail order, which
    is stable. Page a ranked answer in one sitting, or take the first page and
    stop.

    The complete set, unreduced: the site folds a document's repeated citations
    into one line and hides whole-document citations superseded by a pinpointed
    one, and both are presentation. `predicate` separates the typed relations
    (bemyndigande, ändrar, upphäver) and `source` the commentary. The citation's
    surface text is not carried -- it belongs to the citing document, and
    `/document/outbound` on that uri has it.
    """
    try:
        data = reads.inbound_citations(con, uri, scope=scope, source=source,
                                       sort=sort, limit=limit, offset=offset)
    except reads.InboundUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    return InboundCitations(
        uri=uri, scope=scope, source=source, sort=sort, total=data["total"],
        limit=limit, offset=offset, by_source=data["by_source"],
        citations=[InboundCitation(**row) for row in data["citations"]])


@app.get("/api/v1/document/outbound", response_model=list[Citation],
         tags=["document"], summary="What this document cites")
def outbound_endpoint(uri: str = Query(..., description="citing document uri"),
                      con: sqlite3.Connection = Depends(get_con)):
    """Every citation a document makes -- the mirror of /document/inbound, and
    `uri` here is the *citing* document.

    One row per citation, in document order, carrying the surface text the
    citing document actually wrote ("6 § räntelagen"). That text lives on this
    side only: the inbound answer cannot have it, because it belongs to the
    citer, not to the cited provision.

    A target the corpus does not (yet) hold comes back with `hosted: false` and
    no label/title/source -- the citation is real either way, and roughly one
    in twenty points outside what is held here.

    The same links sit inline in the artifact `/document` returns; this is the
    flat, already-resolved view of them."""
    return [Citation(**row) for row in reads.outbound(con, uri)]


@app.get("/api/v1/sources", response_model=list[SourceInfo], tags=["catalog"],
         summary="The corpus' sources and their document counts")
def sources_endpoint(con: sqlite3.Connection = Depends(get_con)):
    """Every source in the corpus and how many documents it holds.

    The `source` names here are exactly what the `source` parameter of /search,
    /documents, /facets and /browse takes, so this is the endpoint to call
    first when writing against the API -- the set grows as sources are added,
    and hardcoding it dates."""
    return [SourceInfo(**row) for row in reads.sources(con)]


class GraphNeighbor(BaseModel):
    """One document on the other end of the citation relation, with the two
    documents' links between them counted into one row."""

    uri: str = Field(description="the neighbour's canonical uri")
    label: str | None = Field(None, description="its printed id")
    title: str | None = Field(None, description="its title")
    descriptive: str | None = Field(
        None, description="the compact citing form -- what to print on the "
        "node (\"NJA 2015 s. 1\", \"Räntelagen\")")
    source: str = Field(description="its corpus")
    kind: str | None = Field(None, description="its document kind")
    group: str = Field(description="the flow group it belongs to "
                       "(\"Författningar\", \"Rättsfall\", …) -- the same "
                       "names ?groups= filters on")
    n: int = Field(description="links between the two documents")
    inbound_count: int | None = Field(
        None, description="how many documents cite the neighbour itself -- "
        "the authority signal ?sort=citations ranks by. Null on the "
        "default-shape inbound side, which skips the per-neighbour join")


class GraphSide(BaseModel):
    """One direction of the neighbourhood. `top` is the `limit` biggest
    neighbours; the totals describe the whole side."""

    total_links: int = Field(description="links on this side, over the "
                             "resolved and group-filtered set")
    total_docs: int = Field(description="distinct documents on this side")
    unresolved: int = Field(
        0, description="outbound only: citations whose target is not in the "
        "corpus, so they have no neighbour row")
    top: list[GraphNeighbor]


class GraphUnit(BaseModel):
    """One provision of the document, as a node of its internal graph."""

    anchor: str = Field(description="the unit's fragment id (\"K4P7\", \"A6\")")
    label: str = Field(description="its reader form (\"4 kap. 7 §\")")
    n: int = Field(description="internal links touching the unit")


class GraphInternal(BaseModel):
    """The document citing itself: which of its provisions cite which. Only
    answered for a fragment uri."""

    nodes: list[GraphUnit]
    edges: list[tuple[str, str, int]] = Field(
        description="(citing unit anchor, cited unit anchor, links)")
    truncated: int = Field(description="unit pairs left out by the edge cap")


class GraphResponse(BaseModel):
    """One node's neighbourhood in the citation graph, aggregated per neighbour
    document and ready to draw."""

    uri: str = Field(description="the uri asked about, echoed back")
    root: str = Field(description="`uri` without its fragment -- the document")
    anchor: str | None = Field(None, description="the fragment as asked, if any")
    unit: str | None = Field(
        None, description="the pinpointable unit that anchor belongs to (an "
        "anchor inside a stycke answers for its paragraf)")
    pinpoint: str | None = Field(None, description="that unit's reader form "
                                 "(\"4 kap. 7 §\")")
    descriptive: str | None = Field(
        None, description="the compact citing name of the document")
    citation: str = Field(description="the whole node written as a citation "
                          "(\"Artikel 6 EKMR\")")
    label: str | None = Field(None, description="the document's printed id")
    title: str | None = Field(None, description="its title")
    source: str = Field(description="its corpus")
    kind: str | None = Field(None, description="its document kind")
    group: str = Field(description="the flow group it belongs to")
    source_url: str | None = Field(
        None, description="the document's page at its own publisher. For a "
        "source this site does not render (tidskriftsartiklar), this is the "
        "link to open -- the site has no page for it.")
    inbound: GraphSide | None = Field(
        None, description="who cites this node; null when ?direction= excluded it")
    outbound: GraphSide | None = Field(
        None, description="what this node cites; null when ?direction= excluded it")
    internal: GraphInternal | None = Field(
        None, description="the document's own provision-to-provision graph. "
        "Present for a fragment uri, and for a document uri with "
        "?internal=true.")


@app.get("/api/v1/graph", response_model=GraphResponse, tags=["document"],
         summary="A node's neighbourhood in the citation graph")
def graph_endpoint(uri: str = Query(..., description="document or fragment uri"),
                   direction: str = Query(
                       "both", pattern="^(in|out|both)$",
                       description="which sides of the neighborhood to answer"),
                   groups: str | None = Query(
                       None, description="comma-separated flow-group filter "
                                         "(Författningar, Rättsfall, …)"),
                   limit: int = Query(20, ge=1, le=300),
                   sort: str = Query(
                       "links", pattern="^(links|citations)$",
                       description="order of `top`: 'links' by ties to the "
                       "center, 'citations' by how cited the neighbour "
                       "itself is (the inbound_count relate stamps; a "
                       "catalog no relate has stamped yet ranks all as 0)"),
                   grouplimit: int | None = Query(
                       None, ge=1, le=300,
                       description="max neighbours per flow group in `top` "
                       "-- diversity over one dominating source type"),
                   internal: bool = Query(
                       False, description="include the document's internal "
                       "unit graph for a document uri too (a fragment uri "
                       "always carries it)"),
                   con: sqlite3.Connection = Depends(get_con)):
    """One node's neighborhood in the citation graph, aggregated per neighbor
    document and ready to draw -- what the paraGRAF explorer
    (para-graf.tomtebo.org) walks.

    Each neighbor row is a distinct document with the link count between the
    two; `/document/inbound` has the same facts one citation per row. A
    fragment uri (`...#K4P7`) answers for that provision alone and adds
    `internal`: the whole document's provision-to-provision citation graph at
    unit (§/article) level. `internal=true` adds that graph to a document
    uri's answer as well."""
    wanted = None
    if groups:
        wanted = {g.strip() for g in groups.split(",") if g.strip()}
        unknown = wanted - set(facets.FLOW_GROUP_NAMES)
        if unknown:
            raise HTTPException(422, "unknown flow group(s): %s"
                                % ", ".join(sorted(unknown)))
    data = reads.graph(con, uri, direction=direction, groups=wanted,
                       limit=limit, internal=internal, sort=sort,
                       grouplimit=grouplimit)
    if data is None:
        raise HTTPException(404, "no document %r in the catalog" % uri)
    return GraphResponse(**data)


class PathStep(BaseModel):
    """One document on the chain. `n`/`forward` describe the hop to the NEXT
    step (null on the last): how many citations carry it, and whether it runs
    in citing direction from this document to the next (false: the next
    document cites this one -- a hop a direction=both walk may take)."""
    uri: str
    label: str | None = None
    title: str | None = None
    descriptive: str | None = None
    group: str = Field(description="the flow group it belongs to")
    n: int | None = Field(None, description="citations carrying the hop to "
                                            "the next step; null on the last")
    forward: bool | None = Field(
        None, description="whether the hop to the next step follows citing "
        "direction; null on the last")


class PathResponse(BaseModel):
    from_uri: str = Field(serialization_alias="from")
    to_uri: str = Field(serialization_alias="to")
    direction: str
    distance: int | None = Field(description="steps on the shortest chain; "
                                             "null when no chain exists")
    path: list[PathStep] = Field(description="the chain, endpoints included; "
                                             "empty when no chain exists")


@app.get("/api/v1/path", response_model=PathResponse, tags=["document"],
         summary="The shortest citation chain between two documents")
def path_endpoint(from_uri: str = Query(..., alias="from",
                                        description="start document uri"),
                  to_uri: str = Query(..., alias="to",
                                      description="end document uri"),
                  direction: str = Query(
                      "both", pattern="^(in|out|both)$",
                      description="which links a step may follow: 'out' "
                      "citations, 'in' citers, 'both' either"),
                  groups: str | None = Query(
                      None, description="comma-separated flow-group filter "
                      "for the intermediate documents (endpoints are always "
                      "allowed)"),
                  con: sqlite3.Connection = Depends(get_con)):
    """The shortest chain of citations connecting two documents -- the
    six-degrees walk paraGRAF draws when an end point is set. The chain is
    document-level: a hop exists when any provision of one document cites any
    provision of the other. A fragment uri is answered for its document.

    The whole citation graph (2.6M document pairs) is held in memory as
    integer adjacency arrays, so the answer is one breadth-first search --
    tens of milliseconds. The graph loads in the background (relate's
    sidecar, or one sequential catalog scan); until it is ready the endpoint
    answers 503 rather than making the request wait."""
    wanted = None
    if groups:
        wanted = {g.strip() for g in groups.split(",") if g.strip()}
        unknown = wanted - set(facets.FLOW_GROUP_NAMES)
        if unknown:
            raise HTTPException(422, "unknown flow group(s): %s"
                                % ", ".join(sorted(unknown)))
    ends = []
    for uri in (from_uri, to_uri):
        root = uri.partition("#")[0]
        if not catalog.document(con, root):
            raise HTTPException(404, "no document %r in the catalog" % root)
        ends.append(root)
    g = paths.graph_if_ready(db.CATALOG)
    if g is None:
        raise HTTPException(
            503, "the citation graph is still loading -- try again shortly",
            headers={"Retry-After": "30"})
    chain = pathgraph.shortest(g, ends[0], ends[1],
                               direction=direction, groups=wanted)
    steps = []
    if chain:
        labels = catalog.graph_labels(con, chain)
        for i, uri in enumerate(chain):
            label, title, source, kind, descriptive = labels[uri]
            n = forward = None
            if i + 1 < len(chain):
                n, forward = _hop_count(con, uri, chain[i + 1], direction)
            steps.append(PathStep(
                uri=uri, label=label, title=title, descriptive=descriptive,
                group=facets.flow_group(source, kind), n=n, forward=forward))
    return PathResponse(from_uri=ends[0], to_uri=ends[1], direction=direction,
                        distance=len(chain) - 1 if chain else None,
                        path=steps)


def _hop_count(con, a, b, direction):
    """(citations, forward) for one hop of a chain: how many links carry it,
    and in which citing direction. A 'both' walk prefers the forward reading
    when the two documents cite each other."""
    count = lambda x, y: con.execute(
        "SELECT count(*) FROM links WHERE from_uri = ? AND to_root = ?",
        (x, y)).fetchone()[0]
    if direction != "in":
        fwd = count(a, b)
        if fwd:
            return fwd, True
    return count(b, a), False


# --------------------------------------------------------------------------
# page facsimiles: an on-demand PNG of one source-PDF page (lib/facsimile),
# rendered lazily at retina resolution and cached to disk. Reached both as
# the documented API endpoint (?uri=&sid=) and at the legacy lagen.nu path
# grammar (/prop/2022/23:10/sid1.png), which predates the API. Enabled for
# every page-oriented PDF source: each resolver maps a uri-local document id
# to (source, build-basefile, pdf path) from layout rules + the downloaded
# record -- adding a source is one resolver.
# --------------------------------------------------------------------------

@app.get("/api/v1/facsimile", response_class=FileResponse, tags=["document"],
         responses={200: {"content": {"image/png": {}}}},
         summary="A PNG of one printed page of the source PDF")
def facsimile_endpoint(
        uri: str = Query(..., description="full lagen.nu document uri"),
        sid: int = Query(..., ge=1, description="printed page number "
                         "(the #sid{N} anchor)"),
        bbox: str | None = Query(None, description="crop to x0,y0,x1,y1 in PDF "
                                 "points from the page's top-left; omit for the "
                                 "whole page")):
    """A facsimile PNG of one printed page of the document's source PDF
    (förarbeten, myndighetsföreskrifter, avgöranden), rendered at retina
    resolution (150 DPI) on first request and cached on disk. `bbox` crops to a
    region of that page -- what a figure inside a förarbete is."""
    return facsimiles.facsimile_response(catalog.local(catalog.strip_fragment(uri)), sid,
                               facsimiles.parse_bbox(bbox) if bbox else None)


_DV_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
                         r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_DV_COURT_RE = re.compile(r"[A-Za-zÅÄÖåäö0-9]+")


@app.get("/api/v1/dv-verdict", response_class=FileResponse, tags=["document"],
         responses={200: {"content": {"application/pdf": {}}}},
         summary="The original verdict PDF behind a decision")
def dv_verdict_endpoint(
        court: str = Query(..., description="court code (domstolKod), e.g. HDO"),
        id: str = Query(..., description="the record's UUID"),
        file: str = Query(..., description="the PDF attachment filename")):
    """The original verdict PDF a decision was first served as, before its NJA
    referat was published ("Ursprunglig dom", R2). Served from the DV download
    store. The path segments are validated so a crafted request can't escape it."""
    name = Path(file).name
    if not (_DV_COURT_RE.fullmatch(court) and _DV_UUID_RE.fullmatch(id)
            and name.lower().endswith(".pdf")):
        raise HTTPException(400, "bad dv-verdict request")
    path = layout.DOM_DOWNLOADED / court / id / name
    if not path.is_file():
        raise HTTPException(404, "verdict pdf not found")
    return FileResponse(path, media_type="application/pdf",
                        headers={"Content-Disposition": 'inline; filename="%s"' % name})


@app.get("/api/v1/pdf", tags=["document"],
         responses={200: {"content": {"application/pdf": {}}}},
         summary="A document typeset for paper, as PDF")
def pdf_endpoint(
        path: str = Query(..., description="public page path, e.g. /1998:204 "
                          "or /prop/2020/21:22"),
        toc: bool = Query(False, description="prepend an Innehåll section "
                          "whose entries carry printed page numbers"),
        kontext: str = Query("", description="comma-separated context kinds to "
                             "print under each provision/section (the rail's "
                             "slugs, e.g. kommentar,dv,forarbete), or 'alla'"),
        andringar: bool = Query(True, description="include the SFS amendment "
                                "and transition-provision register"),
        kolumner: int = Query(1, ge=1, le=2, description="one or two text "
                              "columns; two columns omit context"),
        download: bool = Query(False, description="serve as attachment "
                               "(download) instead of inline (display)")):
    """The page typeset for paper as a PDF: A4, running headers, "n (total)"
    folios, a PDF outline -- the same print stylesheet the browser uses, plus
    the paged-media layer browsers skip. `toc` adds the page's own TOC with
    resolved page numbers; `kontext` prints the chosen context kinds under
    each provision, the way the screen page shows them in the rail.
    `andringar` controls the SFS amendment register. `kolumner=2` uses the
    compact two-column layout and omits context."""
    generated, kinds = pdfjob.parse_request(path, kontext, kolumner)
    try:
        data = pdf.export(generated, toc=toc, kinds=kinds,
                          subresource=facsimiles.subresource,
                          amendments=andringar, columns=kolumner)
    except FileNotFoundError:
        raise HTTPException(404, "no generated page at %r" % path) from None
    except pdf.SubresourceUnavailable as exc:
        # a degraded PDF is never served or cached; the failure is usually
        # transient (facsimile render, NFS), so the client should retry
        raise HTTPException(503, "subresource failed: %s" % exc) from None
    return Response(data, media_type="application/pdf", headers={
        "Content-Disposition": '%s; filename="%s"' % (
            "attachment" if download else "inline", pdf.filename_for(path))})


@app.get("/samling", response_class=HTMLResponse, include_in_schema=False)
def pdf_collection_page():
    """The browser-owned editor for a bookmarkable document collection."""
    return HTMLResponse(pdfcollection.collection_page())


# the legacy path grammar in its two arities: riksmöte-numbered förarbeten and
# avgöranden carry an extra slash ("/prop/2022/23:10/sid1.png",
# "/avg/jo/2340-2025/sid1.png"); year-numbered ids do not
# ("/sou/2021:82/sid1.png", "/mcffs/2026:1/sid1.png")
@app.get("/{a}/{b}/{c}/sid{sid:int}.png", include_in_schema=False)
def facsimile_legacy_3(a: str, b: str, c: str, sid: int):
    return facsimiles.facsimile_response("%s/%s/%s" % (a, b, c), sid)


@app.get("/{a}/{b}/sid{sid:int}.png", include_in_schema=False)
def facsimile_legacy_2(a: str, b: str, sid: int):
    return facsimiles.facsimile_response("%s/%s" % (a, b), sid)


@app.get("/api/v1/sfs-graphic", response_class=FileResponse,
         tags=["document"],
         responses={200: {"content": {"image/png": {}}}},
         summary="A graphic the consolidated statute text omits")
def sfs_graphic_endpoint(
        uri: str = Query(..., description="full lagen.nu SFS uri"),
        node: str = Query(..., description="stable graphic-gap key (the "
                          "data-grafik value, e.g. g-a1b2…)"),
        v: str = Query(None, description="opaque cache-buster (the bbox "
                       "version); accepted and ignored"),
        stor: bool = Query(False, description="render the crop for full-size "
                           "viewing (the lightbox) rather than for the "
                           "thumbnail printed in the text; no effect on a gap "
                           "whose layer entry names a whole page rather than a "
                           "rectangle of one")):
    """A PNG crop of a graphic/formula/map the consolidated SFS text omits,
    cut from the published PDF of the amendment that set it (per the reviewed
    .graphics layer), rendered on first request and cached. Two resolutions, one
    per use: the inline thumbnail, and `stor=1` for the lightbox. A page of road
    signs asks for hundreds of the first and one of the second, so serving the
    large one to both would cost the reader megabytes nothing on screen uses."""
    return facsimiles.sfs_graphic_response(
        catalog.local(catalog.strip_fragment(uri)), node,
        facsimile.CROP_DPI_LARGE if stor else facsimile.CROP_DPI)


@app.get("/api/v1/dumps", response_model=list[DumpInfo], tags=["catalog"],
         summary="The NDJSON bulk dumps on offer")
def dumps_endpoint():
    """The NDJSON bulk dumps on offer, one per source -- the right way to take
    the whole corpus.

    A *manifest*, not a download route: it reports each dump's source, file
    name and size, and the files themselves are served beside the API at
    `/dumps/<file>` (by the reverse proxy, because the set is several
    gigabytes and wants sendfile and byte ranges).

    Each line of a dump is one source artifact, minified, with nothing removed
    or transformed -- the same object `/document` returns in `artifact`.
    Because the citation graph sits inline in each artifact, a line is
    self-contained: reprocessing the corpus needs no second call. Documents
    that parsed to nothing are omitted."""
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
        # each candidate relpath is looked up once -- its precompressed variants
        # first, then (in `_serve`) plain StaticFiles, which is what serves the
        # tiny files kept uncompressed and answers a directory with its index or
        # its trailing-slash redirect. A path never has both a plain and a
        # compressed representation, so the order within a candidate settles
        # nothing that could differ.
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
        # StaticFiles types `directory` as optional; SiteFiles is always
        # constructed with one (rule:fail-fast)
        assert self.directory is not None, "SiteFiles has no directory"
        variants = compress.variants_on_disk(self.directory, rel)
        if variants:
            media_type = compress.media_type(rel)
            for enc, _suffix in compress.ENCODINGS:      # br preferred, then gzip
                if enc in accepts and enc in variants:
                    full, st = variants[enc]
                    resp = FileResponse(full, stat_result=st, media_type=media_type)
                    resp.headers["Content-Encoding"] = enc
                    resp.headers["Vary"] = "Accept-Encoding"
                    # FileResponse stamps ETag/Last-Modified but never reads the
                    # request's conditional headers -- that check lives in
                    # StaticFiles.get_response, which this precompressed branch
                    # bypasses. Replicate it, or every revalidation resends the
                    # full body as a 200.
                    if self.is_not_modified(resp.headers, Headers(scope=scope)):
                        return NotModifiedResponse(resp.headers)
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
    # Matomo tracking for the machine-facing routes -- the counterpart to the
    # browser snippet the generated pages carry (lib/assets/matomo.js), reporting
    # to its own Matomo site (api/analytics.py). Installed here for the same
    # reason as the static mount below: `generate` drives this very app through
    # an in-process TestClient (browse.py) inside the same container that carries
    # MATOMO_URL, so an import-time middleware would have every nightly build
    # report its own ~12 /api/v1/browse calls as a daily API consumer.
    # ...and announced either way: a tracker whose only symptom of being
    # misconfigured is numbers that never arrive should say, once, which it is.
    if analytics.ENABLED:
        logging.info("matomo: tracking /api/v1 + /mcp as site %d via %s",
                     config.MATOMO_SITE_API, analytics.TRACKER)
        app.add_middleware(analytics.Tracked)
    else:
        logging.info("matomo: server-side tracking off "
                     "(needs both MATOMO_URL and MATOMO_SITE_API)")
    app.mount("/", SiteFiles(directory=directory, html=True), name="site")
    # proxy_headers so the app sees the real client IP/scheme/host behind the
    # prod TLS proxy (nginx must send X-Forwarded-For/-Proto) -- notably,
    # api/auth.py's per-IP login rate limit keys on `request.client.host`,
    # which would otherwise be nginx's own address for every request. The
    # session cookie's Secure flag is an explicit config switch
    # (config.COOKIE_SECURE), not derived from this header.
    #
    # proxy_headers alone is not enough: uvicorn rewrites the client address
    # only for peers named in FORWARDED_ALLOW_IPS, which defaults to 127.0.0.1
    # -- the same *host*, not the same compose network. On prod nginx is its own
    # container at 172.19.0.4, so the header was read from an untrusted peer and
    # dropped, and every request logged `"client": "172.19.0.4"`. That put the
    # whole internet in one login-quota bucket, so bots probing /auth/login
    # locked the editors out, and it collapsed Matomo's visitor ids into one.
    # Prod sets FORWARDED_ALLOW_IPS to the compose network's subnet -- nginx's
    # container IP does not survive a recreate -- which is safe because the API
    # port is published to no host interface, so only that network reaches it.
    #
    # Announced at startup for the same reason the Matomo line above is: a
    # misconfiguration whose only symptom is a rate limit that fires for the
    # wrong people should say, once, what it trusts.
    logging.info("proxy: trusting X-Forwarded-* from %s (FORWARDED_ALLOW_IPS); "
                 "client addresses and the login rate limit key on it",
                 os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1 (default)"))
    uvicorn.run(app, host=host, port=port, proxy_headers=True)
