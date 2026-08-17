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
only `search` needs it, and a down cluster is a visible tool error), and the
**artifact JSON** on disk (a document's full parsed body). The tools answer
through `api/reads.py` -- the same functions the REST endpoints call -- so a
corpus fact reaches MCP and REST through one code path.
"""

import contextlib
import itertools
import json
import logging
import time
from collections.abc import Mapping
from typing import Annotated, Literal, TypedDict

from mcp.server import MCPServer
from mcp.server.caching import CacheableMethod, CacheHint
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import ConfigDict, Field

from .. import config
from ..lib import layout, pins, text
from ..lib.search import SearchIndex
from . import analytics, db, reads

# the ceiling on a single document body, shared by `get_document`'s clamp and
# `fetch`, which deliberately reads at it -- one number so raising it can't
# leave the other reader on the old bound.
MAX_CHARS = 200_000

log = logging.getLogger(__name__)

# Shown to the AI host so it knows when to reach for these tools, what the ids
# look like, and the order to call them in. Read once by the host at connect.
INSTRUCTIONS = """\
lagen.nu -- the Swedish legal corpus: statutes (SFS), court decisions (dv),
European Court of Human Rights case law (hudoc), preparatory works (forarbete),
agency regulations (foreskrift), EU law (eurlex), Council of Europe treaties
(coe), JO/JK/ARN decisions (avg) and editorial commentary (kommentar/begrepp) --
with the citation graph between them. Use these tools to ground answers about
Swedish, EU and European human-rights law in the primary sources and to cite the
exact paragraph/article rather than from memory: statutes are amended, and the
corpus carries the current wording.

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


@contextlib.contextmanager
def _root_logging_preserved():
    """Undo any reconfiguration of the *root* logger done inside the block.

    MCPServer's constructor calls logging.basicConfig() -- a library claiming the
    root logger, which belongs to whoever owns the process. Since `mcp` is built
    at module scope (the @mcp.tool decorators below need it), merely importing
    this module would otherwise install the SDK's handler at INFO on every
    process that reaches api/app.py -- including the `lagen` CLI, where it made
    opensearch-py narrate every bulk round-trip into the build output. Snapshot
    and restore, so importing us configures nothing: the app decides (uvicorn's
    own config when serving; app.py's basicConfig under __main__).
    """
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    try:
        yield
    finally:
        root.handlers[:] = handlers
        root.setLevel(level)


# `tools/list` and `server/discover` are the two cacheable methods a host calls
# on every connect. Ours answer from a tool table fixed at import and public
# read-only data, so a client may hold them for an hour and share them across
# authorization contexts -- the corpus grows nightly, but the *tool surface* and
# the instructions only change when this file is deployed. (SEP-2549; the hints
# ride along as ttlMs/cacheScope at 2026-07-28 and are ignored by older clients.)
CACHE_HINTS: Mapping[CacheableMethod, CacheHint] = {
    "tools/list": CacheHint(ttl_ms=3_600_000, scope="public"),
    "server/discover": CacheHint(ttl_ms=3_600_000, scope="public")}

with _root_logging_preserved():
    mcp = MCPServer("lagen.nu", instructions=INSTRUCTIONS,
                    website_url="https://lagen.nu/", cache_hints=CACHE_HINTS)

# one search client for the process; constructing it opens no connection, so
# importing/mounting never needs a running OpenSearch -- only a `search` call
# does. Tests swap this out.
_index = SearchIndex()


@contextlib.contextmanager
def _con():
    """`db.connection()` for a tool call: the same read-only catalog handle the
    REST endpoints take, with the unbuilt catalog reported as a plain
    RuntimeError (the SDK turns that into the tool's error result -- there is no
    HTTP status to raise here)."""
    if not db.catalog_ready():
        raise RuntimeError(db.NOT_BUILT)
    with db.connection() as con:
        yield con


# the corpus sources -- a closed set, so a strict enum: the schema teaches the
# host the vocabulary and it can't pass a value that matches nothing. `kind`, by
# contrast, is source-specific and open-ended (an FS code per agency, an eurlex
# doctype, …), so it stays a guided free string -- a strict enum there would
# reject valid kinds the host sees in results.
Source = Literal["sfs", "dv", "hudoc", "forarbete", "foreskrift", "eurlex",
                 "coe", "avg", "rs", "edpb", "kommentar", "begrepp"]
SourceArg = Annotated[Source | None, Field(
    description="restrict to one corpus source; omit to search all")]
KindArg = Annotated[str | None, Field(
    description="restrict to one document kind. Kinds are source-specific: "
    "law (sfs), case (dv), prop/sou/ds/dir (forarbete), a doctype like "
    "regulation/directive/judgment (eurlex), an FS code like fffs/nfs "
    "(foreskrift), judgment/decision (hudoc), treaty/protocol (coe), "
    "jo/jk/arn (avg), an agency code like fk/migr/imy (rs), kommentar, "
    "begrepp. Omit unless you know the "
    "exact kind (it appears as `kind` on every result).")]

# every tool is a pure read of public data: readOnlyHint lets a host auto-run them
# without a per-call approval prompt (so the multi-step grounding flow isn't
# interrupted); openWorldHint marks results as drawn from a large external corpus,
# not a fixed enumerable set.
READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=True)


# --------------------------------------------------------------------------
# the search/fetch result shapes
#
# `search` and `fetch` are the two tools OpenAI's hosts expect a knowledge
# server to expose, with a fixed result shape: search returns `{results: [{id,
# title, url}]}` and fetch returns `{id, title, text, url, metadata}`, both as
# `structuredContent` (which the SDK only emits for a tool whose return type it
# can build a schema from -- a bare `-> dict` yields none). Declaring these
# shapes is the whole of the adaptation: their required fields are a *subset* of
# what the corpus already answers with, so the contract is met by naming the
# fields rather than by narrowing any tool. Everything the contract doesn't
# mention -- fragments, inbound_count, source/kind, the citation-graph tools --
# stays exactly as it is for every other host.
#
# The hit allows extra keys so lib/search.py can grow fields without them being
# silently dropped from structuredContent. The envelope can't: the SDK builds
# the top-level model itself and drops that config, so every key `search`
# returns is declared here.
# --------------------------------------------------------------------------

class SearchHit(TypedDict):
    """One search result. `id` is what `fetch` takes -- the *most precise*
    target for this hit, so a paragraph-deep match ids the fragment
    (`https://lagen.nu/1962:700#K3P1`), not the whole statute."""

    # pydantic's documented way to configure a TypedDict-derived model; PEP 589
    # allows only annotated declarations in the body, hence the ignore. Verified
    # to reach the wire as `additionalProperties: true` on this hit's schema.
    __pydantic_config__ = ConfigDict(extra="allow")  # ty: ignore[invalid-typed-dict-statement]

    id: str
    title: str
    url: str


class SearchResults(TypedDict):
    """`results` is the contract; `query`/`total` are ours, and are declared so
    they survive into structuredContent."""

    results: list[SearchHit]
    query: str
    total: int


class Document(TypedDict):
    """`get_document`'s reply.

    Declared, rather than left as a bare `-> dict`, because the SDK builds a
    tool's `outputSchema` from its return annotation and emits
    `structuredContent` only when it has one. A bare dict yields neither, so a
    client that reads results structurally -- the read the spec points at -- had
    nothing to read for this tool and had to fall back to parsing the text
    block."""

    uri: str
    source: str | None
    kind: str | None
    label: str | None
    title: str | None
    source_url: str | None
    inbound_count: int
    pinpoint: str | None
    truncated: bool
    text: str


class DocumentList(TypedDict):
    """`list_documents`' page, declared for the reason in `Document`."""

    total: int
    limit: int
    offset: int
    documents: list[dict]


class IncomingCitations(TypedDict):
    """`get_incoming_citations`' page, declared for the reason in `Document`.
    `total` counts the rows `source` left, while `by_source` covers the whole
    scope before that filter -- so the reply still says what the other corpora
    hold."""

    uri: str
    scope: str
    source: str | None
    total: int
    by_source: dict[str, int]
    limit: int
    offset: int
    citations: list[dict]


class ResolvedCitations(TypedDict):
    """`resolve_citation`'s hits, wrapped.

    Wrapped, and not returned as a bare list, because the SDK renders a list
    return as one content block *per element*. A client reading `content[0]` --
    which the spec permits, since `content` is the human-readable rendering --
    then gets element 0 of N with no error raised anywhere. That is
    size-dependent truncation: this tool usually resolves exactly one citation,
    so such a client works perfectly until the day two hits come back, while
    `get_outgoing_citations` silently hands it 1 of 55 from the start. A single
    block cannot be half-read.

    `results` rather than `citations`: these rows are the same shape `search`
    returns under that name, so a client can reuse the same handler."""

    results: list[dict]


class OutgoingCitations(TypedDict):
    """`get_outgoing_citations`' rows, wrapped for the reason in
    `ResolvedCitations`. `citations` matches `get_incoming_citations`, which
    already answers with a dict under that key, so the pair reads alike."""

    citations: list[dict]


class SourceList(TypedDict):
    """`list_sources`' rows, wrapped for the reason in `ResolvedCitations` --
    this one emitted 16 content blocks, one per source."""

    sources: list[dict]


class FetchedDocument(TypedDict):
    """A document (or one provision of it) in the fetch contract's shape.
    `metadata` is the free-form slot, and is where the corpus facts the contract
    has no field for -- source, kind, publisher page, citation count -- ride."""

    id: str
    # `str | None`, not `str`: the catalog's title column is nullable, so a
    # document can genuinely have none. Declaring it required-and-present made
    # the schema promise something the data does not
    title: str | None
    text: str
    url: str
    metadata: dict[str, str | int | bool | None]


def _hit_id(hit):
    """A search hit's `fetch` id: its best fragment URI when the match was
    paragraph-deep, else the document URI. Both are already-valid ids, since a
    fragment URI is just the document URI plus its `#`-pinpoint.

    Indexes `fragments` rather than `.get`-ing it: both producers always set the
    key (`search.parse_hit`, `pins.resolved_results` -- `[]` for a
    document-level match), so a missing one means a hit shape changed under us.
    Raising then beats defaulting, which would silently collapse every id to the
    document URI and have hosts fetch whole statutes instead of the provision.
    """
    return hit["fragments"][0]["uri"] if hit["fragments"] else hit["uri"]


def _page_url(uri, pinpoint=None):
    """The absolute public URL of a document's page, `#pinpoint` appended.

    Absolute, unlike the root-relative path `layout.page_url` mints for the
    site's own pages: an MCP result is read by a client on another origin, and a
    relative path there is not merely unhelpful but wrong -- ChatGPT resolved
    `/1915:218` against `https://chatgpt.com` and rendered its citations pointing
    at that host. Every `url` the tools below emit goes through here or
    `_absolute_page_urls`."""
    return (config.PUBLIC_BASE_URL + layout.page_url(uri)
            + ("#" + pinpoint.lstrip("#") if pinpoint else ""))


def _absolute_page_urls(hits):
    """`hits` with each row's relative `url` replaced by its absolute form.

    The two producers of this row shape (`search.parse_hit`,
    `pins.resolved_results`) set `url` for the site's own pages, so the MCP layer
    rewrites rather than adds. Indexed, not `.get`-ed: a row without the key
    means the shape changed under us, and silently emitting no link is worse than
    failing here (rule:fail-fast)."""
    return [{**hit, "url": config.PUBLIC_BASE_URL + hit["url"]} for hit in hits]


# --------------------------------------------------------------------------
# tools
# --------------------------------------------------------------------------

@mcp.tool(title="Search the Swedish legal corpus", annotations=READ_ONLY)
def search(query: str, source: SourceArg = None, kind: KindArg = None,
           limit: int = 10) -> SearchResults:
    """Full-text search across the whole corpus, ranked by relevance combined
    with how often a document is cited, down to the matching paragraph/article
    (each hit carries the matching `fragments` with highlighted text).

    When the query reads as a citation -- a law nickname/abbreviation + pinpoint
    ("avtalslagen 36", "BrB 12:1"), an EU act + article ("GDPR art 32") or a case
    nickname ("Instagrambilden") -- the exact target is resolved and pinned as the
    first result, which plain full-text can't do (the name appears nowhere in the
    text). `source`/`kind` narrow the hits; `limit` is 1-50.

    Each result: id (pass it to `fetch`; it pinpoints the matching provision
    where the match was paragraph-deep), uri (the document's identifier), url
    (its absolute public page URL, ready to show a reader -- append
    `#<pinpoint>` to deep link), identifier, title, source, kind, inbound_count
    (how often cited), and the matching fragments. Follow up with
    `get_document` (or `fetch`) for the full text.
    """
    limit = max(1, min(limit, 50))
    # a down cluster raises reads.SearchUnavailable, which the SDK returns as
    # the tool's error result -- a visible failure, never a silently smaller
    # answer (the old degrade-to-citation-only read as "nothing else exists")
    res = reads.search(_index, query, source=source, kind=kind, limit=limit)
    return SearchResults(query=query, total=res["total"],
                         results=[{**r, "id": _hit_id(r)}
                                  for r in _absolute_page_urls(res["results"])])


@mcp.tool(title="Resolve a legal citation to its URI", annotations=READ_ONLY)
def resolve_citation(citation: str) -> ResolvedCitations:
    """Resolve a Swedish or EU legal citation written by name/abbreviation into
    its exact lagen.nu document URI(s) -- the reliable way to turn "what the user
    wrote" into a citable, fragment-deep link without full-text search.

    Handles a statute nickname/abbreviation + pinpoint ("avtalslagen 36 §",
    "BrB 3:1"), an EU act + article ("GDPR artikel 32", "dataskyddsförordningen
    art. 6") and a case nickname ("NJA 2015 s. 899", "Instagrambilden"). Returns a
    `results` list (usually one entry, empty if nothing resolves) of {id, uri,
    url, identifier, title, source, kind, inbound_count, fragments} -- the same
    row shape `search` returns, `uri` being the document's identifier and `url`
    its absolute public page URL; when the citation named a paragraph/article,
    `fragments[0].uri` is the pinpointed fragment URI and `id` (what `fetch`
    takes) is that same pinpointed URI.
    """
    with _con() as con:
        return ResolvedCitations(results=[
            {**r, "id": _hit_id(r)}
            for r in _absolute_page_urls(pins.resolved_results(con, citation))])


@mcp.tool(title="Get a document's metadata and text", annotations=READ_ONLY)
def get_document(uri: str, pinpoint: str | None = None,
                 max_chars: int = 20000) -> Document:
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
    max_chars = max(1, min(max_chars, MAX_CHARS))
    with _con() as con:
        data = reads.document(con, uri)
    if data is None:
        raise ValueError("no document %r in the catalog" % uri)
    art = data.pop("artifact")
    if pinpoint:
        want = data["uri"] + "#" + pinpoint.lstrip("#")
        body = next((t for furi, t in text.fragment_texts(art) if furi == want),
                    None)
        if body is None:
            raise ValueError("no section %r in %s -- check the pinpoint against a "
                             "search fragment or a citation anchor"
                             % (pinpoint, uri))
    else:
        body = text.document_text(art)
    # named rather than splatted from `data`: the keys are the tool's declared
    # output schema, so spelling them here is what makes a change to
    # `reads.document` a type error instead of a silently altered contract
    return Document(
        uri=data["uri"], source=data["source"], kind=data["kind"],
        label=data["label"], title=data["title"],
        source_url=data["source_url"], inbound_count=data["inbound_count"],
        pinpoint=pinpoint, truncated=len(body) > max_chars,
        text=body[:max_chars])


@mcp.tool(title="Fetch a document by search-result id", annotations=READ_ONLY)
def fetch(id: str) -> FetchedDocument:
    """Retrieve the full text behind an `id` returned by `search`.

    `id` is a lagen.nu URI. A `#`-fragment in it (`https://lagen.nu/1962:700#K3P1`
    -- what `search` ids a paragraph-deep hit with) fetches just that provision;
    a bare document URI fetches the whole document. Equivalent to
    `get_document`, which takes the URI and the pinpoint separately and can cap
    the length -- prefer that one when you already know both.

    Returns id, title, url (the absolute public page URL, pinpointed where `id`
    was), text and a `metadata` map carrying source, kind,
    label, the publisher's authoritative page, how often the document is cited,
    the `pinpoint` read (null for a whole document) and `truncated`. The body
    caps at 200000 characters: when `metadata.truncated` is true you have a
    prefix, not the whole provision, so don't cite past it -- fetch a
    `#`-pinpointed id instead.
    """
    # the contract asks for the *complete* content, so take get_document's
    # ceiling rather than its (deliberately modest) interactive default
    uri, _, pinpoint = id.partition("#")
    doc = get_document(uri, pinpoint or None, max_chars=MAX_CHARS)
    return FetchedDocument(
        id=id, title=doc["title"], text=doc["text"],
        url=_page_url(doc["uri"], pinpoint),
        metadata={"source": doc["source"], "kind": doc["kind"],
                  "label": doc["label"], "source_url": doc["source_url"],
                  "pinpoint": doc["pinpoint"], "truncated": doc["truncated"],
                  "inbound_count": doc["inbound_count"]})


@mcp.tool(title="List documents in the corpus", annotations=READ_ONLY)
def list_documents(source: SourceArg = None, kind: KindArg = None,
                   limit: int = 50, offset: int = 0) -> DocumentList:
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
        return reads.documents(con, source=source, kind=kind,
                               limit=limit, offset=offset)


@mcp.tool(title="Who cites this document (inbound citations)",
          annotations=READ_ONLY)
def get_incoming_citations(uri: str, limit: int = 50, offset: int = 0,
                           source: str | None = None,
                           scope: Literal["tree", "exact"] = "tree") -> IncomingCitations:
    """Which other documents cite `uri` -- the citation graph inbound, lagen.nu's
    signature feature as data. Answers "which cases apply this statute
    paragraph", "what refers to this ruling".

    Pass a fragment URI (`…#K3P1`) to ask at paragraph level -- much the sharper
    question. A bare document URI answers for the law **and every provision in
    it** (`scope="tree"`, the default), which for a big statute is tens of
    thousands of rows: read `total` and `by_source` in the reply to see the
    shape of it, then narrow by pinpoint or by `source` rather than paging
    through. `scope="exact"` is the narrow question: only citations naming
    `uri` itself, none of its provisions.

    Ordered as lagen.nu itself orders these: case law first for a statute, then
    agency decisions, then the citation graph -- so the default first rows are
    the ones a lawyer would look at first. `source` filters to one corpus
    ("dv" for court decisions, "forarbete" for preparatory works, "sfs" for
    statutes; `list_sources` has them all). `limit`/`offset` page a stable order.

    Returns: uri; scope and source (the filters, echoed); total (rows you can
    page through, so *after* any `source`); by_source ({source: rows} over the
    whole scope, before `source`, so it still tells you what the other corpora
    hold); limit; offset; and citations -- each with uri (the citing document),
    target (the provision it cited), anchor and page (where in the citer it
    sits), label, title, source, kind, date.
    """
    limit, offset = max(1, min(limit, 1000)), max(0, offset)
    with _con() as con:
        return reads.inbound_citations(con, uri, scope=scope, source=source,
                                       limit=limit, offset=offset)


@mcp.tool(title="What this document cites (outbound citations)",
          annotations=READ_ONLY)
def get_outgoing_citations(uri: str) -> OutgoingCitations:
    """Every citation a document makes -- the citation graph outbound. Each entry
    in `citations`: uri (the cited target, with its `#`-fragment where the
    citation is paragraph-deep), anchor (where in the citing document it sits),
    predicate (the relation, e.g. dcterms:references), text (the citation as it
    reads in the source), label/title/source of the target, and `hosted` (false
    when the target is not yet in the corpus -- then label/title are absent).
    Pass a bare document URI.
    """
    with _con() as con:
        return OutgoingCitations(citations=reads.outbound(con, uri))


@mcp.tool(title="List the corpus sources and their sizes", annotations=READ_ONLY)
def list_sources() -> SourceList:
    """The corpus' sources and how many documents each holds -- orientation for
    the `source` filter on `search`/`list_documents`. Each entry in `sources`:
    source, documents.
    """
    with _con() as con:
        return SourceList(sources=reads.sources(con))


# --------------------------------------------------------------------------
# mounting into the FastAPI app (see api/app.py)
# --------------------------------------------------------------------------

# built once at import: creates the Streamable HTTP ASGI app and, lazily, the
# session manager `lifespan` runs. Serving at "/" internally so a mount at
# "/mcp/" lands the endpoint on exactly /mcp/ (see mount()).
#
# One endpoint serves both protocol eras off these settings. A 2026-07-28 client
# sends a self-contained POST -- no initialize handshake, no Mcp-Session-Id, its
# protocol version and capabilities riding in `params._meta` -- and the SDK
# routes it to the single-exchange handler. A 2025-era client still handshakes;
# `stateless_http` gives it a fresh transport per request rather than a session
# pinned to this process, so either way no request needs sticky routing.
#
# DNS-rebinding protection guards localhost-bound servers from hostile web
# pages; this server is public, unauthenticated and read-only, served behind
# nginx which already routes by vhost. Left on (the SDK default), it would
# 421 every request whose Host isn't localhost -- i.e. all production traffic.
_http_app = mcp.streamable_http_app(
    streamable_http_path="/", json_response=True, stateless_http=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False))


@contextlib.asynccontextmanager
async def lifespan(app):
    """Run the Streamable HTTP session manager for the lifetime of the host app.
    Wire this as the FastAPI app's `lifespan` (it is a no-op for the in-process
    TestClient path used during `generate`, which never calls /mcp). Still
    required at 2026-07-28: the manager owns the task group every request --
    session or no session -- is dispatched from."""
    async with mcp.session_manager.run():
        yield


def _message(body):
    """The request body as a JSON-RPC message object, or None if it is not one
    (an empty GET body, a malformed POST). Parsed once per request and handed to
    both readers below."""
    try:
        msg = json.loads(body)
    except ValueError:
        return None
    return msg if isinstance(msg, dict) else None


def _describe(msg, size):
    """One grep-friendly token run for a JSON-RPC request: the method, its
    envelope id, and for tools/call the tool name + its arguments (truncated --
    get_document can take a 200k max_chars but the *arguments* stay small; the cap
    only guards against a hostile oversized payload flooding the log).

    The `id` earns its place by making a client's own giving-up legible: a caller
    that stops waiting may say so with `notifications/cancelled`, whose params
    name the request being abandoned. Without both ids in the log, that notice
    could not be paired with the call it cancelled -- and a client-side timeout is
    otherwise invisible from in here (it is reported to the model, not to us, and
    the model tends to relay it as a server fault). So the cancellation also
    prints its `requestId` and `reason`."""
    if msg is None:
        return "<unparseable body, %d bytes>" % size
    method = msg.get("method", "<no method>")
    ident = "id=%s" % json.dumps(msg.get("id"))
    params = msg.get("params") or {}
    if method == "notifications/cancelled":
        return "%s %s cancels=%s reason=%s" % (
            method, ident, json.dumps(params.get("requestId")),
            json.dumps(params.get("reason"))[:200])
    if method != "tools/call":
        return "%s %s" % (method, ident)
    args = json.dumps(params.get("arguments", {}), ensure_ascii=False)
    return "%s %s %s %s" % (method, ident, params.get("name"), args[:500])


def _called(msg):
    """`(method, tool)` for a JSON-RPC request -- what analytics counts -- or
    None if the body carried no method to count. `tool` is None for every method
    but tools/call."""
    if msg is None or "method" not in msg:
        return None
    if msg["method"] != "tools/call":
        return msg["method"], None
    return msg["method"], msg.get("params", {}).get("name")


# How much of a response to hold on to while deciding whether it was an error.
# A JSON-RPC failure is a sentence ("no document ... in the catalog"), while the
# bodies that run past this are successful reads -- get_document alone returns up
# to MAX_CHARS. So a capture that overflows is a success by construction, and the
# cap is what keeps a 200k-character document from being copied to count it.
CAPTURE_MAX = 64 * 1024


def _failed(status, body, truncated):
    """Whether an MCP response carries an error.

    The status alone cannot say: the transport answers 200 and puts the failure
    *inside* the JSON-RPC envelope -- either a top-level `error` (bad method, bad
    params) or, for a tool that raised, a result flagged `isError`.

    Two responses carry no envelope to read, and neither is a failure: one
    `truncated` at CAPTURE_MAX (only a successful read grows that big -- an error
    is a sentence), and the empty body of the 202 that acknowledges a
    notification. Anything else unreadable is counted as failed rather than
    waved through: this runs *after* the response has gone out, where raising
    would leave the caller mid-stream, so the honest move is to record an
    envelope we cannot vouch for as the anomaly it is."""
    if status is None or status >= 400:
        return True
    if truncated or not body:
        return False
    try:
        msg = json.loads(body)
    except ValueError:
        return True
    if not isinstance(msg, dict):
        return True                    # not an envelope we can vouch for
    # `result` is whatever the peer sent -- `null` is legal JSON-RPC, and any
    # other non-object is malformed. Test the type rather than reaching into it:
    # this runs *past* the response, where an AttributeError does not become a
    # 500 but a second response.
    return "error" in msg or (isinstance(msg.get("result"), dict)
                              and msg["result"].get("isError") is True)


def _header(scope, name):
    """One request header from an ASGI scope, latin-1 decoded as the spec stores
    it, or "-" when absent. Truncated: a user-agent is caller-controlled."""
    want = name.encode("latin-1")
    for key, value in scope.get("headers", ()):
        if key == want:
            return value.decode("latin-1", "replace")[:200]
    return "-"


def _client_ip(scope):
    """The caller's address, not nginx's.

    `scope["client"]` is the socket peer, which behind the compose-network proxy
    is always nginx (172.19.x.x) -- so every MCP line logged the same address and
    could not tell an OpenAI host from a Claude one. The vhost sets
    X-Forwarded-For; its FIRST entry is the original client, the rest are proxies.
    It is caller-forgeable, so it identifies traffic, it does not authorise
    anything."""
    forwarded = _header(scope, "x-forwarded-for")
    if forwarded != "-":
        return forwarded.split(",")[0].strip()
    client = scope.get("client")
    return client[0] if client else "-"


_request_ids = itertools.count(1)

# nginx's `proxy_read_timeout` for the vhost that fronts this app. It is not set
# in docker/nginx/ferenda.lagen.nu.conf, so nginx's own default applies -- keep
# this in step if that changes.
#
# Load-bearing for the log's honesty: the timeout fires while nginx reads the
# response, after which it answers the client 504 and drops the upstream
# connection. The app does not find out. It goes on to emit `http.response.start`
# with 200, so a `done status=200` line can describe a request the caller
# received a 504 for -- measured on 2026-08-17, when a resolve_citation logged as
# arriving at 12:28:20 and nginx logged "upstream timed out while reading
# response header" at 12:29:20, exactly 60 s later. Past this mark the status we
# report is what we *sent*, not what the caller *got*.
PROXY_READ_TIMEOUT = 60.0


class _LoggedMCP:
    """ASGI wrapper logging every MCP request twice -- once on arrival, once on
    completion -- and reporting tool calls to Matomo (api/analytics.py) when a
    tracker is configured.

    Two lines, not one, because a single arrival line cannot distinguish the three
    outcomes that matter when a client reports a failure we cannot see: the call
    answered, the call answered with a JSON-RPC error, or the app never produced a
    response at all. Both lines carry `mcp[<n>]`, so concurrent calls can be
    paired up (`grep 'mcp\\[7\\]'`).

    Every request is logged, whatever its method. Only POST carries a JSON-RPC
    body, but streamable HTTP also uses GET (the SSE stream) and DELETE (session
    teardown), and those used to pass through invisibly -- a blind spot exactly
    where a client-side transport failure would show.

    The status on the done line is what this app *sent*. Past
    PROXY_READ_TIMEOUT that is not what the caller *received* -- nginx has
    already answered it 504 and dropped the connection -- so the line says so
    outright rather than showing a bare `status=200` for a request that failed
    from the caller's side.

    The uvicorn/nginx access logs see only `POST /mcp/ 200`, so tool-level
    visibility has to come from here. A POST body is buffered to be parsed
    (single JSON-RPC messages, stateless_http -- small by construction) and
    replayed to the wrapped app. The response is watched up to CAPTURE_MAX
    because whether a tool call failed is only readable there (see `_failed`).
    Nothing is withheld from the caller: each message is passed on as it arrives,
    and the tracking hit is sent after the response has gone out."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        rid = next(_request_ids)
        method = scope["method"]
        common = "mcp[%d] %s %s ip=%s ua=%s" % (
            # `path` is the full request path even under a Mount (`root_path`
            # carries the prefix separately, so joining them doubles it)
            rid, method, scope.get("path", "-"), _client_ip(scope),
            _header(scope, "user-agent"))

        called, described, replay = None, "", None
        if method == "POST":
            messages = []
            while True:
                message = await receive()
                messages.append(message)
                if (message["type"] != "http.request"
                        or not message.get("more_body")):
                    break
            body = b"".join(m.get("body", b"") for m in messages
                            if m["type"] == "http.request")
            msg = _message(body)
            described = _describe(msg, len(body))
            called = _called(msg)
            replay = iter(messages)

        client_gone = False

        async def receive_replayed():
            """The buffered request messages, then the real channel -- watching
            for `http.disconnect`, which is definite evidence the caller stopped
            waiting (an nginx timeout drops the upstream connection).

            Definite when it arrives, but not something to rely on: nothing polls
            this channel after the body is read, so a disconnect usually goes
            unobserved. The duration against PROXY_READ_TIMEOUT is the dependable
            signal; this one confirms it when we happen to see it."""
            nonlocal client_gone
            message = next(replay, None) if replay is not None else None
            if message is None:
                message = await receive()
            if message["type"] == "http.disconnect":
                client_gone = True
            return message

        log.info("%s start %s", common, described)

        status, captured = None, bytearray()

        async def watched(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            elif message["type"] == "http.response.body":
                # slice to the room left, not just skip once full: with
                # json_response the whole envelope arrives as ONE message, so a
                # test on the buffer alone always sees an empty buffer and copies
                # the entire body -- the cap would bound nothing. And extend, not
                # `+=`: an augmented assignment would rebind the name and so make
                # it local to this closure.
                captured.extend(message.get("body", b"")[:CAPTURE_MAX - len(captured)])
            await send(message)

        started = time.monotonic()
        try:
            await self.app(scope, receive_replayed, watched)
        except Exception:
            # the call did not complete, whatever the caller ends up receiving
            # (api/errors.py's 500, or a body cut short if the transport had
            # already started one). Logged with its traceback here -- this is the
            # one record that a request arrived and died inside us, which is the
            # question a client-side error report cannot answer. Then let it
            # through untouched: this wrapper observes, it does not handle.
            log.exception("%s raised after %.0f ms (status so far %s)",
                          common, (time.monotonic() - started) * 1000, status)
            if analytics.ENABLED and called:
                analytics.track_mcp(scope, *called, failed=True)
            raise
        # `_failed` reads a JSON-RPC envelope, which only a POST answers. A GET is
        # the SSE stream, whose `event:`/`data:` frames are not JSON -- running the
        # envelope test on those would label every stream JSONRPC-ERROR.
        if method == "POST":
            failed = _failed(status, bytes(captured), len(captured) >= CAPTURE_MAX)
            flag = " JSONRPC-ERROR" if failed else ""
        else:
            failed = status is None or status >= 400
            flag = " HTTP-ERROR" if failed else ""
        elapsed = time.monotonic() - started
        # `status` is what we sent, which past the proxy's read timeout is not
        # what the caller got -- see PROXY_READ_TIMEOUT. Say so on the line rather
        # than leaving a bare `status=200` to be read as a delivered 200.
        if elapsed > PROXY_READ_TIMEOUT:
            flag += (" PAST-PROXY-TIMEOUT(%.0fs) status-is-what-we-sent-not-what"
                     "-the-caller-got" % PROXY_READ_TIMEOUT)
        if client_gone:
            flag += " CLIENT-GONE"
        log.info("%s done status=%s %.0f ms bytes=%d%s %s",
                 common, status, elapsed * 1000, len(captured), flag, described)
        if analytics.ENABLED and called:
            analytics.track_mcp(scope, *called, failed=failed)


class BarePathToMount:
    """Make `/mcp` reach the mount instead of being redirected to `/mcp/`.

    This has to sit *ahead of the router*. `Mount("/mcp")` compiles to a pattern
    that requires something after the prefix, so the bare path never matches it;
    Starlette's `redirect_slashes` then notices that adding a slash would match
    and answers 307. Rewriting the path inside the mounted app is therefore too
    late -- nothing gets there.

    A redirect is correct HTTP and still the wrong answer here. The URL we
    publish has no trailing slash, so *every* session paid a 307 on its first
    POST, and a client whose transport will not replay a POST body across a
    redirect fails at the handshake -- which reaches the model as a bare
    "server error" and leaves nothing in our logs, since the request never got
    past the router. Two sources of that failure disappear at once: the extra
    round trip, and the redirect itself.

    Exact match only: `/mcp/…` sub-paths are the transport's own business."""

    def __init__(self, app, path="/mcp"):
        self.app = app
        self.path = path

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path") == self.path:
            scope = {**scope, "path": self.path + "/",
                     "raw_path": (self.path + "/").encode()}
        await self.app(scope, receive, send)


def mount(app):
    """Expose the MCP server on `app` at /mcp and /mcp/, neither redirecting.
    Call before the static site catch-all is mounted (serve() mounts "/" last),
    so the MCP routes win."""
    app.mount("/mcp/", _LoggedMCP(_http_app))
    app.add_middleware(BarePathToMount)
