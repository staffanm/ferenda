"""The public MCP (Model Context Protocol) server over the corpus -- the same
read-only view the REST API exposes (api/app.py), reshaped as MCP *tools* so any
MCP-capable AI host (Claude, ChatGPT, …) can ground answers about Swedish (and
EU) law in the live corpus and its citation graph, and cite the exact §/article.

It is mounted into the one `lagen serve` FastAPI process at **/mcp** over the
Streamable HTTP transport, so it ships wherever the site ships -- no second
service, no port, no auth (it is public, read-only data, like the REST API and
the site). `mount(app)` adds the endpoint; `lifespan(app)` runs the transport's
session manager and must be wired into the FastAPI app that mounts it.

Every tool reads the same three rebuildable backends as the REST service: the
SQLite **catalog** (metadata + the citation graph), **OpenSearch** (full-text;
only `search` needs it -- and it degrades to citation resolution when the cluster
is down), and the **artifact JSON** on disk (a document's full parsed body). The
tools are thin wrappers over `lib`, so a corpus fact reaches MCP and REST through
one code path.
"""

import contextlib
from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from opensearchpy.exceptions import OpenSearchException
from pydantic import Field
from starlette.responses import RedirectResponse
from starlette.routing import Route

from .. import config
from ..lib import catalog, layout, pins, text
from ..lib.search import SearchIndex

CATALOG = config.DATA / "catalog.sqlite"

# Shown to the AI host so it knows when to reach for these tools, what the ids
# look like, and the order to call them in. Read once by the host at connect.
INSTRUCTIONS = """\
lagen.nu -- the Swedish legal corpus: statutes (SFS), court decisions (dv),
preparatory works (forarbete), agency regulations (foreskrift), EU law (eurlex),
JO/JK/ARN decisions (avg) and editorial commentary (kommentar/begrepp) -- with the
citation graph between them. Use these tools to ground answers about Swedish and
EU law in the primary sources and to cite the exact paragraph/article rather than
from memory: statutes are amended, and the corpus carries the current wording.

Documents are identified by their public lagen.nu URI, e.g.
`https://lagen.nu/1962:700` (Brottsbalken); a `#`-fragment pinpoints a
paragraph/article -- `#K3P1` is 3 kap. 1 §, `#P6` is 6 §, an EU article is `#32`.

Canonical flow for grounding a legal question:
 1. Turn each law/case into a URI: `resolve_citation` when the user named it
    ("utlänningslagen", "avtalslagen 36 §", "GDPR art 32"), else `search` to find
    it by topic. Prefer `resolve_citation` over guessing a URI.
 2. `get_document(uri, pinpoint=...)` for the exact provision's current text.
 3. `get_incoming_citations(uri + '#' + pinpoint)` for the case law and
    regulations that apply that provision; `get_outgoing_citations` for what it
    relies on. Walking this graph is the point -- it is what a plain web search
    can't do.
 4. Cite the pinpoint fragment (e.g. `#K5P8`), never just the law.

All data is read-only and public; nothing here mutates anything.\
"""

mcp = FastMCP("lagen.nu", instructions=INSTRUCTIONS,
              stateless_http=True, json_response=True,
              streamable_http_path="/")

# one search client for the process; constructing it opens no connection, so
# importing/mounting never needs a running OpenSearch -- only a `search` call
# does (and that degrades gracefully -- see below). Tests swap this out.
_index = SearchIndex()


@contextlib.contextmanager
def _con():
    """A read-only catalog connection, opened per tool call (SQLite connections
    are not shared across threads, and FastMCP runs sync tools in a threadpool);
    `catalog.connect_ro` applies the additive schema migrations once per
    process first."""
    if not CATALOG.exists():
        raise RuntimeError("catalog not built -- run `lagen all relate`")
    con = catalog.connect_ro(CATALOG)
    try:
        yield con
    finally:
        con.close()


# the corpus sources -- a closed set, so a strict enum: the schema teaches the
# host the vocabulary and it can't pass a value that matches nothing. `kind`, by
# contrast, is source-specific and open-ended (an FS code per agency, an eurlex
# doctype, …), so it stays a guided free string -- a strict enum there would
# reject valid kinds the host sees in results.
Source = Literal["sfs", "dv", "forarbete", "foreskrift", "eurlex", "avg",
                 "kommentar", "begrepp"]
SourceArg = Annotated[Source | None, Field(
    description="restrict to one corpus source; omit to search all")]
KindArg = Annotated[str | None, Field(
    description="restrict to one document kind. Kinds are source-specific: "
    "law (sfs), case (dv), prop/sou/ds/dir (forarbete), a doctype like "
    "regulation/directive/judgment (eurlex), an FS code like fffs/nfs "
    "(foreskrift), jo/jk/arn (avg), kommentar, begrepp. Omit unless you know the "
    "exact kind (it appears as `kind` on every result).")]

# every tool is a pure read of public data: readOnlyHint lets a host auto-run them
# without a per-call approval prompt (so the multi-step grounding flow isn't
# interrupted); openWorldHint marks results as drawn from a large external corpus,
# not a fixed enumerable set.
READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True)


# --------------------------------------------------------------------------
# tools
# --------------------------------------------------------------------------

@mcp.tool(title="Search the Swedish legal corpus", annotations=READ_ONLY)
def search(query: str, source: SourceArg = None, kind: KindArg = None,
           limit: int = 10) -> dict:
    """Full-text search across the whole corpus, ranked by relevance combined
    with how often a document is cited, down to the matching paragraph/article
    (each hit carries the matching `fragments` with highlighted text).

    When the query reads as a citation -- a law nickname/abbreviation + pinpoint
    ("avtalslagen 36", "BrB 12:1"), an EU act + article ("GDPR art 32") or a case
    nickname ("Instagrambilden") -- the exact target is resolved and pinned as the
    first result, which plain full-text can't do (the name appears nowhere in the
    text). `source`/`kind` narrow the hits; `limit` is 1-50.

    Each result: uri, url (the public page path -- append `#<pinpoint>` to deep
    link), identifier, title, source, kind, inbound_count (how often cited), and
    the matching fragments. Follow up with `get_document` for the full text.
    """
    limit = max(1, min(limit, 50))
    results, total, note = [], 0, None
    # full-text needs the cluster; if it's down (or the index is missing), still
    # answer with the pinned citation resolution (catalog-only) rather than
    # failing the whole call -- but only an OpenSearch failure degrades; a bug
    # in the reshaping below must still raise
    try:
        res = _index.search(query, source=source, kind=kind, limit=limit)
    except OpenSearchException as exc:
        note = ("full-text search is unavailable (%s); showing only citation "
                "resolution" % type(exc).__name__)
    else:
        results = [{**r, "url": layout.page_url(r["uri"])} for r in res["results"]]
        total = res["total"]
    # the resolved target answers a citation-shaped query, so it leads; drop any
    # full-text row for the same document (the pinned hit is more precise)
    if CATALOG.exists():
        with _con() as con:
            pinned = pins.resolved_results(con, query, source, kind)
        results, total = pins.merge_pinned(pinned, results, total, limit)
    out = {"query": query, "total": total, "results": results}
    if note:
        out["note"] = note
    return out


@mcp.tool(title="Resolve a legal citation to its URI", annotations=READ_ONLY)
def resolve_citation(citation: str) -> list[dict]:
    """Resolve a Swedish or EU legal citation written by name/abbreviation into
    its exact lagen.nu document URI(s) -- the reliable way to turn "what the user
    wrote" into a citable, fragment-deep link without full-text search.

    Handles a statute nickname/abbreviation + pinpoint ("avtalslagen 36 §",
    "BrB 3:1"), an EU act + article ("GDPR artikel 32", "dataskyddsförordningen
    art. 6") and a case nickname ("NJA 2015 s. 899", "Instagrambilden"). Returns a
    list (usually one entry, or empty if nothing resolves) of {uri, url,
    identifier, title, source, kind, inbound_count, fragments}; when the citation
    named a paragraph/article, `fragments[0].uri` is the pinpointed fragment URI.
    """
    with _con() as con:
        return pins.resolved_results(con, citation)


@mcp.tool(title="Get a document's metadata and text", annotations=READ_ONLY)
def get_document(uri: str, pinpoint: str | None = None,
                 max_chars: int = 20000) -> dict:
    """Fetch a document's metadata and its full parsed plain text by URI.

    `uri` is a lagen.nu document URI (e.g. `https://lagen.nu/1962:700`). Pass
    `pinpoint` (e.g. "K3P1" for 3 kap. 1 §, "P6" for 6 §, an EU article id) to get
    just that section's text instead of the whole document -- cheaper and precise;
    pinpoints come from `search` fragments, `resolve_citation`, or the `anchor`
    field of the citation tools. Long bodies are truncated to `max_chars`
    (capped at 200000) with `truncated: true` -- request a specific `pinpoint` for
    a large statute.

    Returns uri, source, kind, label, title, source_url (the publisher's
    authoritative page), inbound_count (how often the document is cited), the
    requested `pinpoint`, and `text`.
    """
    max_chars = max(1, min(max_chars, 200000))
    with _con() as con:
        row = catalog.document(con, uri)
        if not row:
            raise ValueError("no document %r in the catalog" % uri)
        uri, source, kind, label, title, path = row
        # synthesized begrepp stubs are real rows with no artifact file (path='')
        art = catalog.load_artifact(catalog.data_root(con), path)
        inbound = catalog.document_inbound_count(con, uri)
    if pinpoint:
        want = uri + "#" + pinpoint.lstrip("#")
        body = next((t for furi, t in text.fragment_texts(art) if furi == want),
                    None)
        if body is None:
            raise ValueError("no section %r in %s -- check the pinpoint against a "
                             "search fragment or a citation anchor" % (pinpoint, uri))
    else:
        body = text.document_text(art)
    return {"uri": uri, "source": source, "kind": kind, "label": label,
            "title": title, "source_url": art.get("source_url"),
            "inbound_count": inbound, "pinpoint": pinpoint,
            "truncated": len(body) > max_chars, "text": body[:max_chars]}


@mcp.tool(title="List documents in the corpus", annotations=READ_ONLY)
def list_documents(source: SourceArg = None, kind: KindArg = None,
                   limit: int = 50, offset: int = 0) -> dict:
    """Enumerate documents (id + lightweight metadata), filtered by source/kind
    and paginated -- the corpus index, *not* full-text search (that is `search`,
    which takes a query). Use it to see what a source contains, then `get_document`
    each URI. `total` is the match count before paging (stable order by URI), so
    you can page through the whole set; `limit` is 1-500.

    Each entry: uri, source, kind, label, title, source_url (publisher page where
    known), updated (the artifact's last-build time, ISO 8601).
    """
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    with _con() as con:
        total = catalog.document_count(con, source, kind)
        root = catalog.data_root(con)             # stored paths are data_root-relative
        docs = []
        for uri, src, kind_, label, title, source_url, path, _display in \
                catalog.documents(con, source, kind, limit, offset):
            updated = catalog.artifact_updated(root, path)
            docs.append({"uri": uri, "source": src, "kind": kind_, "label": label,
                         "title": title, "source_url": source_url,
                         "updated": updated})
    return {"total": total, "limit": limit, "offset": offset, "documents": docs}


@mcp.tool(title="Who cites this document (inbound citations)",
          annotations=READ_ONLY)
def get_incoming_citations(uri: str, limit: int = 100) -> list[dict]:
    """Which other documents cite exactly `uri` -- the citation graph inbound,
    lagen.nu's signature feature as data. Answers "which cases apply this statute
    paragraph", "what refers to this ruling". Pass a fragment URI
    (`…#K3P1`) to query at paragraph level, or a bare document URI for the whole
    document. One entry per (citing document, pinpoint); self-citations excluded;
    `limit` caps the rows. Each: uri (the citing document), anchor (where in it the
    citation sits), label, title, source.
    """
    limit = max(1, min(limit, 1000))
    with _con() as con:
        return [{"uri": from_uri, "anchor": anchor, "label": label,
                 "title": title, "source": src}
                for from_uri, anchor, label, title, src
                in catalog.inbound(con, uri, limit=limit)]


@mcp.tool(title="What this document cites (outbound citations)",
          annotations=READ_ONLY)
def get_outgoing_citations(uri: str) -> list[dict]:
    """Every citation a document makes -- the citation graph outbound. Each entry:
    uri (the cited target, with its `#`-fragment where the citation is
    paragraph-deep), anchor (where in the citing document it sits), predicate (the
    relation, e.g. dcterms:references), text (the citation as it reads in the
    source), label/title/source of the target, and `hosted` (false when the target
    is not yet in the corpus -- then label/title are absent). Pass a bare document
    URI.
    """
    with _con() as con:
        return [{"uri": to_uri, "anchor": anchor, "predicate": predicate,
                 "text": text_, "label": label, "title": title, "source": src,
                 "hosted": src is not None}
                for to_uri, predicate, text_, anchor, label, title, src
                in catalog.outbound(con, uri)]


@mcp.tool(title="List the corpus sources and their sizes", annotations=READ_ONLY)
def list_sources() -> list[dict]:
    """The corpus' sources and how many documents each holds -- orientation for
    the `source` filter on `search`/`list_documents`. Each: source, documents.
    """
    with _con() as con:
        return [{"source": s, "documents": n}
                for s, n in sorted(catalog.counts(con).items())]


# --------------------------------------------------------------------------
# mounting into the FastAPI app (see api/app.py)
# --------------------------------------------------------------------------

# built once at import: creates the Streamable HTTP ASGI app and, lazily, the
# session manager `lifespan` runs. Serving at "/" internally so a mount at
# "/mcp/" lands the endpoint on exactly /mcp/ (see mount()).
_http_app = mcp.streamable_http_app()


@contextlib.asynccontextmanager
async def lifespan(app):
    """Run the Streamable HTTP session manager for the lifetime of the host app.
    Wire this as the FastAPI app's `lifespan` (it is a no-op for the in-process
    TestClient path used during `generate`, which never calls /mcp)."""
    async with mcp.session_manager.run():
        yield


async def _redirect_to_slash(request):
    # a bare POST/GET /mcp -> /mcp/ (307 preserves method + body), so both the
    # tidy public URL and the mounted path work; MCP clients follow the redirect
    return RedirectResponse(url="/mcp/", status_code=307)


def mount(app):
    """Expose the MCP server on `app` at /mcp (and /mcp/). Call before the static
    site catch-all is mounted (serve() mounts "/" last), so the MCP routes win."""
    app.router.routes.append(
        Route("/mcp", _redirect_to_slash, methods=["GET", "POST", "DELETE"]))
    app.mount("/mcp/", _http_app)
