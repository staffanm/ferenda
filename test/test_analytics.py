"""Server-side Matomo tracking (ferenda/api/analytics.py): which requests
become a hit, what the hit says, and what never leaves the process."""

import httpx
import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from ferenda import config
from ferenda.api import analytics
from ferenda.api import app as apimod


@pytest.fixture
def hits(monkeypatch):
    """Every hit the code under test would send, captured instead of posted --
    so no test needs a Matomo, and the worker thread is never started."""
    sent = []
    monkeypatch.setattr(analytics, "_enqueue",
                        lambda params, headers: sent.append((params, headers)))
    monkeypatch.setattr(config, "MATOMO_SITE_API", 3)
    return sent


@pytest.fixture
def client(hits):
    """A toy app behind the middleware: one API route, one editor route, one
    static-site route, one 404."""
    async def ok(request):
        return PlainTextResponse("ok")

    async def missing(request):
        return PlainTextResponse("gone", status_code=404)

    app = Starlette(routes=[Route("/api/v1/search", ok),
                            Route("/internal-api/v1/auth/me", ok),
                            Route("/api/v1/vanished", missing),
                            Route("/docs", ok),
                            Route("/2018:585", ok)],
                    # mirrors the real app's stack: CORSMiddleware answers a
                    # preflight itself with a 200, *inside* Tracked, which is
                    # what the GET guard exists to keep out of the numbers
                    middleware=[Middleware(CORSMiddleware, allow_origins=["*"],
                                           allow_methods=["GET"])])
    return TestClient(analytics.Tracked(app), base_url="https://ferenda.lagen.nu")


def test_an_api_call_is_one_hit_on_the_machine_site(client, hits):
    client.get("/api/v1/search?q=mord", headers={"user-agent": "curl/8.5.0"})
    (params, headers), = hits
    assert params["idsite"] == 3
    assert params["url"] == "https://ferenda.lagen.nu/api/v1/search?q=mord"
    assert params["action_name"] == "API/search"
    # the audience here *is* what Matomo would otherwise drop as a bot
    assert params["bots"] == "1"
    assert headers["User-Agent"] == b"curl/8.5.0"


def test_the_visitor_id_carries_no_address(client, hits):
    client.get("/api/v1/search", headers={"user-agent": "curl/8.5.0"})
    params, _headers = hits[0]
    assert len(params["_id"]) == 16
    assert "testclient" not in str(params) and "cip" not in params


def test_one_caller_is_one_visitor_and_two_are_two():
    first = analytics._visitor_id("192.0.2.1", "curl/8.5.0")
    assert first == analytics._visitor_id("192.0.2.1", "curl/8.5.0")
    assert first != analytics._visitor_id("192.0.2.9", "curl/8.5.0")
    assert first != analytics._visitor_id("192.0.2.1", "python-httpx/0.27")


def test_the_static_site_is_not_tracked_here(client, hits):
    # the page counts itself in the browser (lib/assets/matomo.js)
    client.get("/2018:585")
    assert hits == []


def test_our_own_pages_calling_the_api_are_not_counted(client, hits):
    client.get("/api/v1/search?q=mord", headers={"sec-fetch-site": "same-origin"})
    client.get("/api/v1/search?q=mord",
               headers={"referer": "https://ferenda.lagen.nu/2018:585"})
    assert hits == []
    # ...but a foreign browser app using the API is a real consumer
    client.get("/api/v1/search?q=mord",
               headers={"sec-fetch-site": "cross-site",
                        "referer": "https://example.org/app"})
    assert len(hits) == 1
    assert hits[0][0]["urlref"] == "https://example.org/app"


def test_the_editors_own_routes_are_not_an_audience(client, hits):
    # auth/me fires on every page load; counting it would make the API numbers
    # a copy of the site's page views
    client.get("/internal-api/v1/auth/me")
    assert hits == []


def test_an_error_is_tracked_under_an_error_branch(client, hits):
    # was: errors were dropped as "not audience". They are the half of the
    # traffic nobody reports back, so they are counted -- under a title branch
    # that keeps them out of the working-traffic numbers.
    client.get("/api/v1/vanished")
    (params, _headers), = hits
    assert params["action_name"] == "API/error/vanished"
    assert params["url"] == "https://ferenda.lagen.nu/api/v1/vanished"


def test_a_failing_call_from_our_own_pages_is_still_counted(client, hits):
    # the successful twin of this call is skipped (the browser tracker counted
    # the page that made it) -- but a defect is worth having whoever hit it
    client.get("/api/v1/vanished", headers={"sec-fetch-site": "same-origin"})
    assert hits[0][0]["action_name"] == "API/error/vanished"


def test_an_exception_past_the_handlers_is_counted_then_re_raised(hits):
    async def boom(request):
        raise RuntimeError("kaboom")

    app = Starlette(routes=[Route("/api/v1/boom", boom)])
    client = TestClient(analytics.Tracked(app), base_url="https://ferenda.lagen.nu")
    with pytest.raises(RuntimeError):
        client.get("/api/v1/boom")
    # the caller's exception is untouched; the failure is still on the record
    assert hits[0][0]["action_name"] == "API/error/boom"


def test_a_cors_preflight_is_not_a_second_hit(client, hits):
    # this middleware sits outside CORSMiddleware, which answers the preflight
    # itself with a 200 -- counting it would double every cross-origin consumer
    resp = client.options("/api/v1/search",
                          headers={"origin": "https://example.org",
                                   "access-control-request-method": "GET"})
    assert resp.status_code == 200         # CORS answered it; only the GET guard stops the hit
    assert hits == []


def test_a_non_ascii_user_agent_does_not_break_the_hit(hits):
    """A user-agent carrying a byte >= 0x80 -- ordinary crawler traffic. Starlette
    latin-1-decodes header bytes, and httpx refuses to encode the resulting str
    as a header: a UnicodeEncodeError, which is not an httpx.HTTPError and would
    therefore kill the drain thread rather than lose one hit. Driven through a
    raw ASGI scope, since the test client cannot carry these bytes unchanged."""
    raw = "Mozilla/5.0 (caf\xe9)".encode("latin-1")
    analytics._hit("https://ferenda.lagen.nu/api/v1/search", "API/search",
                   Request({"type": "http", "method": "GET", "root_path": "",
                            "scheme": "https", "path": "/api/v1/search",
                            "server": ("ferenda.lagen.nu", 443),
                            "query_string": b"", "client": ("192.0.2.1", 44321),
                            "headers": [(b"user-agent", raw)]}))
    _params, headers = hits[0]
    assert headers["User-Agent"] == raw          # the caller's own bytes, verbatim
    # ...and that is a header httpx will actually send
    assert httpx.Client().build_request(
        "POST", "http://matomo/matomo.php", headers=headers).headers["user-agent"]


def test_the_build_driving_the_app_in_process_is_not_a_consumer():
    # `generate` runs ~12 GET /api/v1/browse through an in-process TestClient
    # (browse.py) inside the same container that carries MATOMO_URL; the
    # middleware is installed by serve(), not at import, so the build is silent
    assert all(m.cls is not analytics.Tracked for m in apimod.app.user_middleware)


def test_the_openapi_pages_count_as_api(client, hits):
    client.get("/docs")
    assert hits[0][0]["action_name"] == "API/docs"


def test_a_path_that_merely_starts_like_ours_is_not_ours(hits):
    # `/docs` must not claim `/docsomething`: with failures tracked too, a bare
    # startswith would let any remote caller mint tracked action names
    assert analytics.under("/docs", analytics.API_PREFIXES)
    assert analytics.under("/docs/oauth2-redirect", analytics.API_PREFIXES)
    assert not analytics.under("/docsomething", analytics.API_PREFIXES)
    assert not analytics.under("/api/v1x/search", analytics.API_PREFIXES)
    # the internal API is a separate path namespace, so the site's own chatter
    # (auth/me on every page load) falls outside the tracked set by shape
    assert not analytics.under("/internal-api/v1/auth/me", analytics.API_PREFIXES)


def test_a_tool_call_is_counted_under_its_tool_name(hits):
    scope = {"type": "http", "method": "POST", "path": "/mcp/", "root_path": "",
             "scheme": "https", "server": ("ferenda.lagen.nu", 443),
             "query_string": b"", "client": ("192.0.2.1", 44321),
             "headers": [(b"host", b"ferenda.lagen.nu"),
                         (b"user-agent", b"claude-code/2.1")]}
    analytics.track_mcp(scope, "tools/call", "get_document")
    (params, headers), = hits
    assert params["idsite"] == 3
    # synthetic: every MCP request is a POST to the same path, so the tool name
    # has to reach the URL for the Pages report to say anything
    assert params["url"] == "https://ferenda.lagen.nu/mcp/tools/call/get_document"
    assert params["action_name"] == "MCP/tools/call/get_document"
    assert headers["User-Agent"] == b"claude-code/2.1"


def test_a_failed_tool_call_is_counted_under_the_error_branch(hits):
    scope = {"type": "http", "method": "POST", "path": "/mcp/", "root_path": "",
             "scheme": "https", "server": ("ferenda.lagen.nu", 443),
             "query_string": b"", "client": ("192.0.2.1", 44321), "headers": []}
    analytics.track_mcp(scope, "tools/call", "get_document", failed=True)
    params, _headers = hits[0]
    assert params["action_name"] == "MCP/error/tools/call/get_document"
    # ...but the URL is the tool's own either way, so Pages still counts demand
    assert params["url"] == "https://ferenda.lagen.nu/mcp/tools/call/get_document"


def test_a_handshake_is_counted_without_a_tool(hits):
    scope = {"type": "http", "method": "POST", "path": "/mcp/", "root_path": "",
             "scheme": "https", "server": ("ferenda.lagen.nu", 443),
             "query_string": b"", "client": ("192.0.2.1", 44321), "headers": []}
    analytics.track_mcp(scope, "initialize", None)
    assert hits[0][0]["action_name"] == "MCP/initialize"
