"""The public MCP server (accommodanda/api/mcp.py) over a fixture catalog + a
faked search backend -- the tool functions directly (fast, no network) plus one
end-to-end Streamable HTTP round-trip through a real MCP client to prove the
mounted /mcp endpoint and the transport wiring."""

import json

import anyio
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from opensearchpy.exceptions import ConnectionError as OpenSearchConnectionError

from accommodanda.api import app as api
from accommodanda.api import mcp as mcpmod
from accommodanda.lib import catalog


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

    # point the tools at the fixture catalog (catalog.connect_ro tracks its
    # one-time migration per path, so a fresh tmp catalog needs no flag reset)
    monkeypatch.setattr(mcpmod, "CATALOG", cat)

    # a fake search backend -- the tools must not require a live OpenSearch
    class FakeIndex:
        def search(self, q, source=None, kind=None, limit=10, offset=0):
            return {"total": 1, "results": [{
                "uri": "https://lagen.nu/1962:700", "identifier": "SFS 1962:700",
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
    assert hit["url"] == "/1962:700"                 # layout.page_url
    assert hit["fragments"][0]["pinpoint"] == "K3P1"


def test_search_degrades_without_opensearch(corpus):
    class Down:
        def search(self, *a, **k):
            raise OpenSearchConnectionError("no cluster")
    mcpmod._index = Down()
    res = mcpmod.search("mord")
    # the call still succeeds (no exception), just with a note and no full-text
    assert "note" in res and res["results"] == []


def test_resolve_citation_to_fragment(corpus):
    hits = mcpmod.resolve_citation("brottsbalken 3 kap. 1 §")
    assert hits, "expected the nickname+pinpoint to resolve"
    assert hits[0]["uri"] == "https://lagen.nu/1962:700"
    assert hits[0]["fragments"][0]["uri"] == "https://lagen.nu/1962:700#K3P1"


def test_resolve_citation_bare_sfs_number(corpus):
    # the id-shaped probe API clients naturally send ("SFS 2018:585")
    hits = mcpmod.resolve_citation("SFS 2018:585")
    assert hits and hits[0]["uri"] == "https://lagen.nu/2018:585"
    # a bare page-number law id: only the catalog knows the _s.1 suffix
    hits = mcpmod.resolve_citation("SFS 1904:48")
    assert hits and hits[0]["uri"] == "https://lagen.nu/1904:48_s.1"
    # ...and a pinpoint follows the rewritten root
    hits = mcpmod.resolve_citation("1904:48 3 §")
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
    inbound = mcpmod.get_incoming_citations("https://lagen.nu/1962:700#K3P1")
    assert any(c["uri"] == "https://lagen.nu/2018:585" for c in inbound)

    outbound = mcpmod.get_outgoing_citations("https://lagen.nu/2018:585")
    ref = next(c for c in outbound if c["uri"] == "https://lagen.nu/1962:700#K3P1")
    assert ref["hosted"] is True and ref["text"] == "3 kap. 1 §"


def test_list_documents_and_sources(corpus):
    docs = mcpmod.list_documents(source="sfs")
    assert docs["total"] == 3
    assert {d["uri"] for d in docs["documents"]} == {
        "https://lagen.nu/1962:700", "https://lagen.nu/2018:585",
        "https://lagen.nu/1904:48_s.1"}

    sources = mcpmod.list_sources()
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
        assert t.annotations and t.annotations.readOnlyHint is True

    props = tools["search"].inputSchema["properties"]
    # source is an optional enum of exactly the corpus sources
    source_enum = next(b["enum"] for b in props["source"]["anyOf"] if "enum" in b)
    assert set(source_enum) == {"sfs", "dv", "hudoc", "forarbete", "foreskrift",
                                "eurlex", "coe", "avg", "kommentar", "begrepp"}
    # kind is a plain string (no enum) but carries guidance
    assert not any("enum" in b for b in props["kind"]["anyOf"])
    assert "fffs" in props["kind"]["description"]


def test_end_to_end_streamable_http(corpus, caplog):
    """A real MCP client over the mounted /mcp endpoint: initialize, list the
    tools, call one -- proving the transport + mount + lifespan are wired."""
    caplog.set_level("INFO", logger="accommodanda.api.mcp")
    async def scenario():
        config = uvicorn.Config(api.app, host="127.0.0.1", port=8791,
                                log_level="error", lifespan="on")
        server = uvicorn.Server(config)
        async with anyio.create_task_group() as tg:
            tg.start_soon(server.serve)
            while not server.started:
                await anyio.sleep(0.05)
            try:
                # the tidy public URL (no trailing slash) must work too
                async with streamable_http_client("http://127.0.0.1:8791/mcp") \
                        as (r, w, _):
                    async with ClientSession(r, w) as session:
                        await session.initialize()
                        names = {t.name for t in (await session.list_tools()).tools}
                        assert {"search", "get_document", "resolve_citation",
                                "get_incoming_citations"} <= names
                        out = await session.call_tool(
                            "get_document",
                            {"uri": "https://lagen.nu/1962:700"})
                        payload = json.loads(out.content[0].text)
                        assert payload["title"] == "Brottsbalk (1962:700)"
            finally:
                server.should_exit = True

    anyio.run(scenario)
    # every JSON-RPC request logs one line (the access log only shows POST
    # /mcp/); a tools/call line carries the tool name + its arguments
    logged = [r.message for r in caplog.records
              if r.name == "accommodanda.api.mcp"]
    assert any(m.endswith("initialize") for m in logged)
    assert any("tools/call get_document" in m
               and '"uri": "https://lagen.nu/1962:700"' in m for m in logged)
