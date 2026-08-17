"""The public MCP server (accommodanda/api/mcp.py) over a fixture catalog + a
faked search backend -- the tool functions directly (fast, no network) plus
end-to-end Streamable HTTP round-trips through real MCP clients of *both*
protocol eras, to prove the mounted /mcp endpoint and the transport wiring."""

import contextlib
import json

import anyio
import httpx
import pytest
import uvicorn
from mcp.client import Client, ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.mcpserver.exceptions import ToolError
from opensearchpy.exceptions import ConnectionError as OpenSearchConnectionError
from starlette.testclient import TestClient

from accommodanda import config
from accommodanda.api import analytics, db, reads
from accommodanda.api import app as api
from accommodanda.api import mcp as mcpmod
from accommodanda.lib import catalog, inbound


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    art_dir = tmp_path / "artifact"
    art_dir.mkdir()
    bb = art_dir / "bb.json"
    bb.write_text(json.dumps({
        "uri": "https://lagen.nu/1962:700", "source_url": "https://example/bb",
        "metadata": {"properties": {"dcterms:title": "Brottsbalk (1962:700)"}},
        "structure": [{"type": "paragraf", "id": "K3P1",
                       "text": ["Den som berövar annan livet döms för mord."]}]}))
    fl = art_dir / "fl.json"
    fl.write_text(json.dumps({
        "uri": "https://lagen.nu/2018:585",
        "metadata": {"properties": {"dcterms:title": "Förvaltningslag (2018:585)"}},
        "structure": [{"type": "paragraf", "id": "P1",
                       "text": ["Se ", {"uri": "https://lagen.nu/1962:700#K3P1",
                                        "predicate": "dcterms:references",
                                        "text": "3 kap. 1 §"}, " brottsbalken."]}]}))
    # a page-number law: its uri carries the corpus basefile slug (_s.1),
    # which a bare "1904:48" probe can only reach via the catalog
    sml = art_dir / "sml.json"
    sml.write_text(json.dumps({
        "uri": "https://lagen.nu/1904:48_s.1",
        "metadata": {"properties": {
            "dcterms:title": "Lag (1904:48 s.1) om samäganderätt"}},
        "structure": [{"type": "paragraf", "id": "P3",
                       "text": ["Kunna delägarne ej enas..."]}]}))
    cat = tmp_path / "catalog.sqlite"
    catalog.rebuild(cat, "sfs", [bb, fl, sml])

    # get_incoming_citations answers from generate's per-document citation files,
    # not from the catalog -- write them here as a generate run would
    # (render._write_inbound). data_root is tmp_path (the catalog's own dir).
    con = catalog.connect(cat)
    uris = ("https://lagen.nu/1962:700", "https://lagen.nu/2018:585",
            "https://lagen.nu/1904:48_s.1")
    for uri in uris:
        inbound.write(tmp_path, uri, inbound.citations(con, uri))
    inbound.mark_built(tmp_path, len(uris), 0)
    con.close()

    # point the tools at the fixture catalog (catalog.connect_ro tracks its
    # one-time migration per path, so a fresh tmp catalog needs no flag reset)
    monkeypatch.setattr(db, "CATALOG", cat)

    # a fake search backend -- the tools must not require a live OpenSearch.
    # Same call surface as the REST fake: reads.search drives both faces
    # through one signature (source/kind/year/limit/offset/cursor) and reads
    # next_cursor + facets off every reply.
    class FakeIndex:
        def search(self, q, source=None, kind=None, year=None, limit=10,
                   offset=0, cursor=None):
            return {"total": 1, "next_cursor": None, "facets": {}, "results": [{
                "uri": "https://lagen.nu/1962:700", "url": "/1962:700",
                "identifier": "SFS 1962:700",
                "title": "Brottsbalk (1962:700)", "source": "sfs", "kind": "law",
                "score": 9.1, "inbound_count": 1,
                "highlight": ["… <em>%s</em> …" % q],
                "fragments": [{"uri": "https://lagen.nu/1962:700#K3P1",
                               "pinpoint": "K3P1", "highlight": ["<em>%s</em>" % q]}]}]}
    monkeypatch.setattr(mcpmod, "_index", FakeIndex())
    return cat


def test_search_combines_fulltext_and_pins(corpus):
    res = mcpmod.search("mord", source="sfs")
    assert res["query"] == "mord"
    hit = res["results"][0]
    assert hit["identifier"] == "SFS 1962:700"
    assert hit["url"] == config.PUBLIC_BASE_URL + "/1962:700"
    assert hit["fragments"][0]["pinpoint"] == "K3P1"


def test_every_tool_url_is_absolute(corpus):
    """`url` must carry its origin. A root-relative path is resolved by the
    reading client against *its own* host: ChatGPT rendered `/1915:218` as
    https://chatgpt.com/1915:218 and every citation it published was broken.

    The site's own pages keep linking relatively (`layout.page_url`) -- this is
    the MCP boundary's job, so it is checked on every tool that emits a url."""
    base = config.PUBLIC_BASE_URL
    urls = [mcpmod.search("mord", source="sfs")["results"][0]["url"],
            mcpmod.resolve_citation("brottsbalken 3 kap. 1 §")["results"][0]["url"],
            mcpmod.fetch("https://lagen.nu/1962:700#K3P1")["url"],
            mcpmod.fetch("https://lagen.nu/1962:700")["url"]]
    for url in urls:
        assert url.startswith(base + "/"), url
        # the origin appears once: a producer that starts absolutizing upstream
        # would otherwise double it silently
        assert url.count("://") == 1, url


def test_search_fails_visibly_without_opensearch(corpus):
    """A down cluster is a visible error, never a silently smaller answer --
    the old degrade to citation-only results read as "the corpus holds nothing
    else". Same policy as REST's 503 (one code path, api/reads.py)."""
    class Down:
        def search(self, *a, **k):
            raise OpenSearchConnectionError("no cluster")
    mcpmod._index = Down()
    with pytest.raises(reads.SearchUnavailable, match="unavailable"):
        mcpmod.search("mord")
    # and through the server it is a tool error carrying the same reason (the
    # transport turns a ToolError into the client's isError result)
    with pytest.raises(ToolError, match="unavailable"):
        anyio.run(lambda: mcpmod.mcp.call_tool("search", {"query": "mord"}))


def test_resolve_citation_to_fragment(corpus):
    hits = mcpmod.resolve_citation("brottsbalken 3 kap. 1 §")["results"]
    assert hits, "expected the nickname+pinpoint to resolve"
    assert hits[0]["uri"] == "https://lagen.nu/1962:700"
    assert hits[0]["fragments"][0]["uri"] == "https://lagen.nu/1962:700#K3P1"


def test_resolve_citation_bare_sfs_number(corpus):
    # the id-shaped probe API clients naturally send ("SFS 2018:585")
    hits = mcpmod.resolve_citation("SFS 2018:585")["results"]
    assert hits and hits[0]["uri"] == "https://lagen.nu/2018:585"
    # a bare page-number law id: only the catalog knows the _s.1 suffix
    hits = mcpmod.resolve_citation("SFS 1904:48")["results"]
    assert hits and hits[0]["uri"] == "https://lagen.nu/1904:48_s.1"
    # ...and a pinpoint follows the rewritten root
    hits = mcpmod.resolve_citation("1904:48 3 §")["results"]
    assert hits[0]["fragments"][0]["uri"] == "https://lagen.nu/1904:48_s.1#P3"


def test_get_document_full_and_pinpoint(corpus):
    doc = mcpmod.get_document("https://lagen.nu/1962:700")
    assert doc["title"] == "Brottsbalk (1962:700)"
    assert doc["source_url"] == "https://example/bb"
    assert "berövar annan livet" in doc["text"] and not doc["truncated"]
    # inbound_count: fl cites this document (its K3P1 fragment)
    assert doc["inbound_count"] == 1

    frag = mcpmod.get_document("https://lagen.nu/1962:700", pinpoint="K3P1")
    assert "berövar annan livet" in frag["text"]

    with pytest.raises(ValueError):
        mcpmod.get_document("https://lagen.nu/1962:700", pinpoint="P999")
    with pytest.raises(ValueError):
        mcpmod.get_document("https://lagen.nu/9999:1")


def test_get_document_truncates(corpus):
    doc = mcpmod.get_document("https://lagen.nu/1962:700", max_chars=10)
    assert doc["truncated"] and len(doc["text"]) == 10


def test_citation_graph(corpus):
    incoming = mcpmod.get_incoming_citations("https://lagen.nu/1962:700#K3P1")
    assert any(c["uri"] == "https://lagen.nu/2018:585"
               for c in incoming["citations"])
    assert incoming["total"] == 1 and incoming["by_source"] == {"sfs": 1}

    # a bare law uri answers for the law *and its provisions*: the only citation
    # here names 3 kap. 1 §, so the model still finds it without knowing to ask
    # at paragraph level -- which is the whole reason the default scope changed
    whole = mcpmod.get_incoming_citations("https://lagen.nu/1962:700")
    assert [c["target"] for c in whole["citations"]] == \
        ["https://lagen.nu/1962:700#K3P1"]

    # and the source filter narrows without a second query
    assert mcpmod.get_incoming_citations(
        "https://lagen.nu/1962:700", source="dv")["total"] == 0

    # scope="exact" asks only about the law itself -- the fixture's one
    # citation names 3 kap. 1 §, so the narrow question finds nothing
    exact = mcpmod.get_incoming_citations("https://lagen.nu/1962:700",
                                          scope="exact")
    assert exact["scope"] == "exact" and exact["total"] == 0

    outbound = mcpmod.get_outgoing_citations("https://lagen.nu/2018:585")["citations"]
    ref = next(c for c in outbound if c["uri"] == "https://lagen.nu/1962:700#K3P1")
    assert ref["hosted"] is True and ref["text"] == "3 kap. 1 §"


def test_list_documents_and_sources(corpus):
    docs = mcpmod.list_documents(source="sfs")
    assert docs["total"] == 3
    assert {d["uri"] for d in docs["documents"]} == {
        "https://lagen.nu/1962:700", "https://lagen.nu/2018:585",
        "https://lagen.nu/1904:48_s.1"}

    sources = mcpmod.list_sources()["sources"]
    assert {"source": "sfs", "documents": 3} in sources


def test_tool_schemas_steer_the_model():
    """The steering signals a host reads at connect: every tool is annotated
    read-only, `source` is a closed enum (so a wrong value can't be passed), and
    `kind` stays a described free string (source-specific, not enumerable)."""
    tools = {t.name: t for t in anyio.run(mcpmod.mcp.list_tools)}
    assert set(tools) >= {"search", "resolve_citation", "get_document",
                          "list_documents", "get_incoming_citations",
                          "get_outgoing_citations", "list_sources"}
    # read-only annotation on every tool (lets a host auto-run them)
    for t in tools.values():
        assert t.annotations and t.annotations.read_only_hint is True

    props = tools["search"].input_schema["properties"]
    # source is an optional enum of exactly the corpus sources
    source_enum = next(b["enum"] for b in props["source"]["anyOf"] if "enum" in b)
    assert set(source_enum) == {"sfs", "dv", "hudoc", "forarbete", "foreskrift",
                                "eurlex", "coe", "avg", "rs", "edpb",
                                "kommentar", "begrepp"}
    # kind is a plain string (no enum) but carries guidance
    assert not any("enum" in b for b in props["kind"]["anyOf"])
    assert "fffs" in props["kind"]["description"]


def test_search_and_fetch_satisfy_the_openai_contract(corpus):
    """`search` and `fetch` carry the field names OpenAI's hosts require of a
    knowledge server. Those fields are a subset of what the corpus already
    answers with, so meeting the contract narrows nothing: the hit keeps its
    citation-graph payload, and `id` pinpoints the matching provision rather
    than costing a whole-statute read."""
    hit = mcpmod.search("mord", source="sfs")["results"][0]
    assert {"id", "title", "url"} <= set(hit)
    assert hit["id"] == "https://lagen.nu/1962:700#K3P1"
    assert hit["inbound_count"] == 1 and hit["fragments"]   # no slot in the contract

    doc = mcpmod.fetch(hit["id"])
    assert set(doc) == {"id", "title", "text", "url", "metadata"}
    assert doc["id"] == hit["id"]
    assert doc["url"] == config.PUBLIC_BASE_URL + "/1962:700#K3P1"
    assert "berövar annan livet" in doc["text"]
    assert doc["metadata"]["source"] == "sfs"
    assert doc["metadata"]["pinpoint"] == "K3P1"

    # a bare document URI is an equally valid id -- then it is the whole document
    whole = mcpmod.fetch("https://lagen.nu/1962:700")
    assert whole["metadata"]["pinpoint"] is None
    assert whole["url"] == config.PUBLIC_BASE_URL + "/1962:700"

    # resolve_citation hands back the same handle, so its hits are fetchable too
    assert (mcpmod.resolve_citation("brottsbalken 3 kap. 1 §")["results"][0]["id"]
            == hit["id"])


def test_hit_id_falls_back_to_the_document_for_a_document_level_match(corpus):
    """A match that isn't paragraph-deep (a title hit, an `is_doc` hit) carries
    `fragments: []`, and its id is then the document URI -- the branch every
    non-pinpointed search result takes, and the one that decides whether a host
    fetching that id gets a document or a KeyError."""
    class DocLevel:
        def search(self, q, source=None, kind=None, year=None, limit=10,
                   offset=0, cursor=None):
            return {"total": 1, "next_cursor": None, "facets": {}, "results": [{
                "uri": "https://lagen.nu/2018:585", "url": "/2018:585",
                "identifier": "SFS 2018:585",
                "title": "Förvaltningslag (2018:585)", "source": "sfs",
                "kind": "law", "score": 4.2, "inbound_count": 0,
                "highlight": [], "fragments": []}]}
    mcpmod._index = DocLevel()

    hit = mcpmod.search("förvaltning")["results"][0]
    assert hit["id"] == "https://lagen.nu/2018:585"
    assert mcpmod.fetch(hit["id"])["metadata"]["pinpoint"] is None


def test_contract_tools_emit_structured_content(corpus):
    """The contract wants `structuredContent` *and* a JSON duplicate in
    `content`. The SDK only emits the former for a tool whose return type it can
    build a schema from -- a bare `-> dict` yields neither schema nor structure,
    which is why these two tools declare TypedDict returns."""
    tools = {t.name: t for t in anyio.run(mcpmod.mcp.list_tools)}
    assert set(tools["fetch"].output_schema["required"]) == {
        "id", "title", "text", "url", "metadata"}
    assert "results" in tools["search"].output_schema["required"]

    for name, args in (("search", {"query": "mord"}),
                       ("fetch", {"id": "https://lagen.nu/1962:700"})):
        res = anyio.run(lambda n=name, a=args: mcpmod.mcp.call_tool(n, a))
        assert res.structured_content == json.loads(res.content[0].text), name


@contextlib.asynccontextmanager
async def _served(port):
    """The whole api app on a real port, lifespan on (so mcp.lifespan runs the
    transport's session manager -- without it every /mcp request 500s).

    Once per *process*: the session manager refuses a second run(), and the
    `mcp` server object is module-level, so a second uvicorn boot would hang
    forever on a lifespan that can never start. Drive every client from the one
    instance below rather than adding a second _served() test.
    """
    server = uvicorn.Server(uvicorn.Config(
        api.app, host="127.0.0.1", port=port, log_level="error", lifespan="on"))
    async with anyio.create_task_group() as tg:
        tg.start_soon(server.serve)
        while not server.started:
            await anyio.sleep(0.05)
        try:
            yield
        finally:
            server.should_exit = True


def test_end_to_end_streamable_http(corpus, caplog):
    """Real MCP clients of both protocol eras against the one mounted /mcp
    endpoint, proving the transport + mount + lifespan are wired.

    A 2026-07-28 client discovers the server, lists the tools and calls one with
    no initialize handshake and no session id -- each POST stands alone, so any
    request could have landed on any process. A pre-2026 client still opens with
    `initialize` and negotiates down; hosts upgrade on their own schedule, so
    dropping them would silently unpublish the corpus.
    """
    caplog.set_level("INFO", logger="accommodanda.api.mcp")

    async def scenario():
        async with _served(8791):
            # /mcp and /mcp/ are the same endpoint, and neither redirects.
            # The published URL has no trailing slash, so a 307 there put one on
            # the first POST of every session -- and a client whose transport
            # does not replay a POST body across a redirect fails the handshake
            # with nothing in our logs to show for it.
            async with httpx.AsyncClient(follow_redirects=False) as raw:
                base = "http://127.0.0.1:8791/mcp"
                accept = {"accept": "application/json, text/event-stream"}
                body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}

                bare = await raw.post(base, json=body, headers=accept)
                slashed = await raw.post(base + "/", json=body, headers=accept)
                assert bare.status_code == 200, bare.status_code
                assert "result" in bare.json()
                assert bare.json() == slashed.json()
                # no redirect, on either -- not merely one that happens to be
                # followed. A `Location` here is the thing being removed.
                for reply in (bare, slashed):
                    assert "location" not in reply.headers, reply.headers

                # One content block per tool, whatever the row count. The SDK
                # renders a *list* return as one block per element, so a client
                # reading content[0] -- which the spec permits, `content` being
                # the human-readable rendering -- silently got element 0 of N:
                # list_sources answered in 16 blocks and get_outgoing_citations
                # in 55. Size-dependent truncation is the worst shape, since a
                # single-hit resolve_citation makes such a client look correct
                # right up until a second row appears.
                for tool, args in (("list_sources", {}),
                                   ("get_outgoing_citations",
                                    {"uri": "https://lagen.nu/2018:585"}),
                                   ("resolve_citation",
                                    {"citation": "brottsbalken 3 kap. 1 §"})):
                    reply = await raw.post(
                        base, headers=accept,
                        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": tool, "arguments": args}})
                    result = reply.json()["result"]
                    assert len(result["content"]) == 1, (tool,
                                                         len(result["content"]))
                    # and that one block carries the whole answer, not a row of it
                    assert isinstance(json.loads(result["content"][0]["text"]),
                                      dict), tool

                # Every tool declares an outputSchema, so every reply carries
                # structuredContent -- the read the spec points a client at. The
                # SDK derives the schema from the return annotation and a bare
                # `-> dict` yields neither, which left three tools with nothing
                # to read structurally.
                listed = await raw.post(
                    base, headers=accept,
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
                names = {t["name"]: t for t in listed.json()["result"]["tools"]}
                assert len(names) == 8, sorted(names)
                missing = [n for n, t in names.items() if "outputSchema" not in t]
                assert not missing, missing

                # DELETE (session teardown) answers the same on both, so the two
                # paths are one endpoint rather than two that merely agree about
                # POST. GET is left out on purpose: it opens the transport's
                # long-lived SSE stream, so asserting on it would mean waiting
                # for a stream designed never to end on its own.
                a = await raw.request("DELETE", base, headers=accept)
                b = await raw.request("DELETE", base + "/", headers=accept)
                assert a.status_code == b.status_code, (a.status_code, b.status_code)
                assert "location" not in a.headers, a.headers

            # the tidy public URL (no trailing slash) must work too
            async with Client("http://127.0.0.1:8791/mcp") as client:
                listed = await client.list_tools()
                assert {"search", "get_document", "resolve_citation",
                        "get_incoming_citations"} <= {t.name for t in listed.tools}
                # the tool table is fixed at import over public data, so it is
                # advertised as cacheable for an hour and shareable (CACHE_HINTS)
                assert listed.ttl_ms == 3_600_000
                assert listed.cache_scope == "public"
                out = await client.call_tool("get_document",
                                             {"uri": "https://lagen.nu/1962:700"})
                assert json.loads(out.content[0].text)["title"] == \
                    "Brottsbalk (1962:700)"

            async with streamable_http_client("http://127.0.0.1:8791/mcp") as (r, w):
                async with ClientSession(r, w) as session:
                    assert (await session.initialize()).protocol_version == \
                        "2025-11-25"
                    out = await session.call_tool(
                        "get_document", {"uri": "https://lagen.nu/1962:700"})
                    assert json.loads(out.content[0].text)["title"] == \
                        "Brottsbalk (1962:700)"

    anyio.run(scenario)
    # every JSON-RPC request logs a start and a done line (the access log only
    # shows POST /mcp/); each carries the envelope id, and a tools/call line the
    # tool name + its arguments
    logged = [r.message for r in caplog.records
              if r.name == "accommodanda.api.mcp"]
    assert any("server/discover id=" in m for m in logged)      # the 2026 opener
    assert any("initialize id=" in m for m in logged)           # the 2025 opener
    assert any("tools/call id=" in m and "get_document" in m
               and '"uri": "https://lagen.nu/1962:700"' in m for m in logged)
    # arrival and completion, for the same request
    assert any(" start " in m and "get_document" in m for m in logged)
    assert any(" done status=200 " in m and "get_document" in m for m in logged)


def _tracked(monkeypatch, asgi_app, hits):
    """A TestClient over `_LoggedMCP` with tracking on and hits captured."""
    monkeypatch.setattr(analytics, "ENABLED", True)
    monkeypatch.setattr(analytics, "_enqueue",
                        lambda params, headers: hits.append(params))
    monkeypatch.setattr(config, "MATOMO_SITE_API", 3)
    return TestClient(mcpmod._LoggedMCP(asgi_app))

def test_every_request_logs_arrival_and_completion(monkeypatch, caplog):
    """Two lines per request, whatever the method.

    Written because the wrapper logged only POST and only on arrival, so three
    situations were indistinguishable in the log: a call that answered, a call
    that answered a JSON-RPC error, and a call that never produced a response.
    A GET or DELETE -- streamable HTTP uses both -- was invisible altogether,
    which is the blind spot a client-side "the tool failed" report lands in."""
    caplog.set_level("INFO", logger="accommodanda.api.mcp")

    async def jsonrpc(scope, receive, send):
        if scope["method"] == "POST":
            await receive()
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body",
                    "body": b'{"jsonrpc":"2.0","id":1,"result":{}}'})

    client = TestClient(mcpmod._LoggedMCP(jsonrpc))
    client.post("/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                               "params": {"name": "search",
                                          "arguments": {"query": "mord"}}},
                headers={"x-forwarded-for": "203.0.113.7, 10.0.0.1",
                         "user-agent": "openai-mcp/1.0.0"})
    client.get("/mcp/")
    # by logger name, not by substring: other libraries (httpx) log their own
    # "HTTP Request: POST ..." lines into caplog whenever the run enables CLI
    # logging, and a substring filter then matches those too
    lines = [r.message for r in caplog.records if r.name == mcpmod.log.name]

    post = [m for m in lines if "POST" in m]
    assert len(post) == 2, post
    assert " start " in post[0] and "tools/call id=1 search" in post[0]
    assert " done status=200 " in post[1] and "bytes=" in post[1]
    # the caller's address, not the proxy's -- every line used to read 172.19.0.4
    assert "ip=203.0.113.7" in post[0] and "ua=openai-mcp/1.0.0" in post[0]
    # both lines carry the same request id, so concurrent calls can be paired
    assert post[0].split()[0] == post[1].split()[0]

    # ...and a non-POST is logged too, without the JSON-RPC envelope test being
    # applied to a body that carries no envelope
    get = [m for m in lines if "GET" in m]
    assert len(get) == 2, get
    assert "JSONRPC-ERROR" not in get[1]


def test_a_cancellation_names_the_call_it_abandons(caplog):
    """A client is free to stop waiting whenever it likes, and `cancelled` is the
    one notice we get when it does. It has to name the abandoned request, or it
    cannot be paired with the call it refers to -- and a client-side timeout is
    otherwise invisible here: it is reported to the model, not to us, and the
    model relays it as a server fault."""
    caplog.set_level("INFO", logger="accommodanda.api.mcp")

    async def ack(scope, receive, send):
        await receive()
        await send({"type": "http.response.start", "status": 202, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    client = TestClient(mcpmod._LoggedMCP(ack))
    client.post("/mcp/", json={"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                               "params": {"name": "search",
                                          "arguments": {"query": "mord"}}})
    client.post("/mcp/", json={
        "jsonrpc": "2.0", "method": "notifications/cancelled",
        "params": {"requestId": 7, "reason": "client timeout"}})
    lines = [r.message for r in caplog.records if r.name == mcpmod.log.name]

    assert any("tools/call id=7 search" in m for m in lines)
    cancel = [m for m in lines if "notifications/cancelled" in m]
    assert cancel, lines
    assert "cancels=7" in cancel[0], cancel[0]
    assert "client timeout" in cancel[0], cancel[0]
    # a 202 with an empty body is an acknowledged notification, not a failure
    assert not any("JSONRPC-ERROR" in m for m in lines), lines


def test_a_response_past_the_proxy_timeout_says_so(monkeypatch, caplog):
    """A 200 we sent after nginx gave up is not a 200 the caller got.

    `proxy_read_timeout` fires while nginx reads the response; it then answers the
    caller 504 and drops the upstream connection, and this app never finds out --
    it emits `http.response.start` with 200 regardless. Measured on prod: a tool
    call arrived 12:28:20 and nginx logged "upstream timed out" at 12:29:20. So a
    bare `status=200` past that mark would be a lie by omission."""
    caplog.set_level("INFO", logger="accommodanda.api.mcp")
    monkeypatch.setattr(mcpmod, "PROXY_READ_TIMEOUT", 0.0)   # everything is late

    async def slow(scope, receive, send):
        await receive()
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body",
                    "body": b'{"jsonrpc":"2.0","id":1,"result":{}}'})

    TestClient(mcpmod._LoggedMCP(slow)).post(
        "/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    done = [r.message for r in caplog.records
            if r.name == mcpmod.log.name and " done " in r.message]
    assert len(done) == 1 and "status=200" in done[0]
    assert "PAST-PROXY-TIMEOUT" in done[0], done[0]
    assert "not-what-the-caller-got" in done[0], done[0]


def test_a_caller_that_stopped_waiting_is_recorded(monkeypatch, caplog):
    """`http.disconnect` is definite evidence the caller went away -- which is
    what an nginx read timeout looks like from in here, since it drops the
    upstream connection. Only seen when something polls the channel, so the flag
    confirms the duration signal rather than replacing it."""
    caplog.set_level("INFO", logger="accommodanda.api.mcp")

    async def polls_until_disconnect(scope, receive, send):
        await receive()                      # the buffered body
        assert (await receive())["type"] == "http.disconnect"
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": b"{}"})

    async def receive_then_disconnect():
        return {"type": "http.disconnect"}

    wrapper = mcpmod._LoggedMCP(polls_until_disconnect)
    body = b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
    scope = {"type": "http", "method": "POST", "path": "/mcp/",
             "headers": [(b"user-agent", b"openai-mcp/1.0.0")],
             "client": ("10.0.0.1", 1234)}
    sent = []

    async def drive():
        first = iter([{"type": "http.request", "body": body, "more_body": False}])

        async def receive():
            return next(first, None) or await receive_then_disconnect()
        await wrapper(scope, receive, lambda m: _noop(sent, m))
    anyio.run(drive)
    done = [r.message for r in caplog.records
            if r.name == mcpmod.log.name and " done " in r.message]
    assert len(done) == 1 and "CLIENT-GONE" in done[0], done


async def _noop(sink, message):
    sink.append(message)


def test_a_request_that_dies_inside_the_app_is_logged(monkeypatch, caplog):
    """An exception on the way out leaves a record with its traceback. Without
    it, an app-side failure and a request that never arrived look identical --
    which is exactly the ambiguity that made a client's error report
    unfalsifiable from our side."""
    caplog.set_level("INFO", logger="accommodanda.api.mcp")

    async def boom(scope, receive, send):
        await receive()
        raise RuntimeError("the tool exploded")

    with pytest.raises(RuntimeError, match="exploded"):
        TestClient(mcpmod._LoggedMCP(boom)).post(
            "/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    ours = [r for r in caplog.records if r.name == mcpmod.log.name]
    assert any(" start " in r.message and "tools/list" in r.message for r in ours)
    assert any("raised after" in r.message for r in ours)
    assert any(r.exc_info for r in ours), "the traceback is the point"


def test_the_wrapper_passes_the_response_through_while_counting_it(monkeypatch):
    """Drives `_LoggedMCP` itself -- the response-watching closure that decides
    what Matomo is told. Written after that closure shipped with an augmented
    assignment that made its buffer function-local: every tracked MCP call raised
    UnboundLocalError *after* the response had started, which the SDK turned into
    a second http.response.start. Nothing here drove the wrapper, so nothing
    caught it."""
    hits = []
    body = b'{"jsonrpc":"2.0","id":1,"result":{"content":[],"isError":true}}'

    async def jsonrpc(scope, receive, send):
        await receive()
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": body})

    resp = _tracked(monkeypatch, jsonrpc, hits).post(
        "/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": "get_document"}})
    assert resp.status_code == 200 and resp.content == body   # caller unaffected
    assert hits[0]["action_name"] == "MCP/error/tools/call/get_document"




def test_a_big_response_is_not_copied_whole_to_classify_it(monkeypatch):
    """CAPTURE_MAX must bound what the wrapper buffers. It did not: the cap was
    tested against the buffer *before* appending, and with json_response the whole
    envelope arrives as one message -- so an empty buffer accepted a 200k-char
    get_document reply in full, every time.

    What the bug costs is memory, not correctness -- the classification comes out
    the same either way -- so the assertion has to be on the bytes actually
    buffered, which is what `_failed` is handed."""
    hits, seen = [], {}
    monkeypatch.setattr(mcpmod, "CAPTURE_MAX", 32)
    monkeypatch.setattr(mcpmod, "_failed", lambda status, body, truncated:
                        bool(seen.update(size=len(body), truncated=truncated)))
    body = b'{"jsonrpc":"2.0","id":1,"result":{"content":[],"isError":true}}'
    assert len(body) > 32

    async def big(scope, receive, send):
        await receive()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": body})

    resp = _tracked(monkeypatch, big, hits).post(
        "/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": "get_document"}})
    assert resp.content == body                     # nothing withheld from the caller
    assert seen == {"size": 32, "truncated": True}  # ...and only 32 bytes kept
    assert hits[0]["action_name"] == "MCP/tools/call/get_document"


def test_an_exception_inside_the_transport_is_counted_then_re_raised(monkeypatch):
    hits = []

    async def boom(scope, receive, send):
        await receive()
        raise RuntimeError("transport gave up")

    with pytest.raises(RuntimeError):
        _tracked(monkeypatch, boom, hits).post(
            "/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                           "params": {"name": "search"}})
    assert hits[0]["action_name"] == "MCP/error/tools/call/search"


def test_what_counts_as_a_failed_response():
    """A JSON-RPC failure rides inside an HTTP 200, so `_failed` -- which decides
    whether a tracked MCP call lands in Matomo's error branch -- has to read the
    envelope, not the status."""
    ok = b'{"jsonrpc":"2.0","id":1,"result":{"content":[{"text":"{}"}]}}'
    assert mcpmod._failed(200, ok, False) is False
    # a tool that raised: 200, with the result flagged
    flagged = b'{"jsonrpc":"2.0","id":1,"result":{"content":[],"isError":true}}'
    assert mcpmod._failed(200, flagged, False) is True
    # a protocol-level failure: bad method or bad params
    protocol = b'{"jsonrpc":"2.0","id":1,"error":{"code":-32601,"message":"nope"}}'
    assert mcpmod._failed(200, protocol, False) is True
    # a body that is not an envelope at all
    assert mcpmod._failed(200, b'[1, 2]', False) is True
    # transport-level
    assert mcpmod._failed(500, b"", False) is True
    assert mcpmod._failed(None, b"", False) is True
    # `result` is whatever the peer sent, and this runs past the response where
    # an AttributeError would become a second response rather than a 500
    for odd in (b"null", b"5", b'"ok"', b"true"):
        assert mcpmod._failed(200, b'{"jsonrpc":"2.0","id":1,"result":%s}' % odd,
                              False) is False
    # the two bodies with no envelope to read, neither of them a failure: one cut
    # off at CAPTURE_MAX (only a successful read grows that big) ...
    assert mcpmod._failed(200, ok[:40], True) is False
    # ... and the empty 202 that acknowledges a notification
    assert mcpmod._failed(202, b"", False) is False
    # anything else unreadable is the anomaly it looks like, not a success
    assert mcpmod._failed(200, b"<html>a proxy error page</html>", False) is True


def test_what_a_request_body_is_counted_as():
    """`_called` is what decides which tool an MCP hit is filed under in Matomo
    (api/analytics.track_mcp), so its reading of a body is fixtured here."""
    call = mcpmod._message(json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "get_document", "arguments": {"uri": "x"}}}).encode())
    assert mcpmod._called(call) == ("tools/call", "get_document")
    # every other method counts as itself, with no tool
    assert mcpmod._called(mcpmod._message(b'{"method": "initialize"}')) == \
        ("initialize", None)
    # nothing to count: a notification-less body, a non-object, a GET's empty body
    assert mcpmod._called(mcpmod._message(b'{"id": 1}')) is None
    assert mcpmod._called(mcpmod._message(b'[1, 2]')) is None
    assert mcpmod._called(mcpmod._message(b"")) is None
    # ...and the log line survives a body that could not be read at all
    assert mcpmod._describe(mcpmod._message(b"not json"), 8) == \
        "<unparseable body, 8 bytes>"
