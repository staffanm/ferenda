"""Tests for the shared HTTP helpers (`accommodanda.lib.net`): the failed-response
description and the raising wrapper `request()` and every direct caller use.

The responses are REAL `requests`/`httpx` objects, built offline. That matters
here more than usual: `raise_for_status` claims to read only what both transports
expose, and a hand-rolled stand-in with the convenient attributes would assert
that claim without testing it -- a real `httpx.Response` has neither `ok` nor
`reason`, and reaches `url` through its bound request."""

import httpx
import pytest
import requests

from accommodanda.lib import net

OVER_CONTEXT = ('{"error":{"code":400,"message":"request (98435 tokens) exceeds '
                'the available context size (65536 tokens), try increasing it"}}')


def requests_response(status, reason, url, body, headers=None):
    resp = requests.Response()
    resp.status_code = status
    resp.reason = reason
    resp.url = url
    resp._content = body.encode()
    resp.headers.update(headers or {})
    return resp


def httpx_response(status, url, body="", headers=None):
    return httpx.Response(status, headers=headers or {}, text=body,
                          request=httpx.Request("GET", url))


def test_raise_for_status_quotes_the_body_the_endpoint_sent():
    # the endpoint's own diagnosis is the whole point: a bare requests error says
    # only "400 Client Error: Bad Request for url: ..." and throws this away
    resp = requests_response(400, "Bad Request",
                             "http://127.0.0.1:8123/v1/chat/completions",
                             OVER_CONTEXT)
    with pytest.raises(requests.HTTPError) as exc:
        net.raise_for_status(resp)
    assert "exceeds the available context size" in str(exc.value)
    assert "HTTP 400 Bad Request" in str(exc.value)
    assert "127.0.0.1:8123" in str(exc.value)


def test_raise_for_status_reads_only_what_both_transports_expose():
    # a real httpx.Response: no `ok`, no `reason`. `request()` retries over either
    # transport, so touching a requests-only attribute would surface as an
    # AttributeError inside the retry loop instead of the real HTTP error.
    resp = httpx_response(429, "https://dg.example/sitemap.xml",
                          headers={"Retry-After": "60", "CF-Ray": "deadbeef"})
    assert not hasattr(resp, "ok") and not hasattr(resp, "reason")
    with pytest.raises(requests.HTTPError) as exc:
        net.raise_for_status(resp)
    assert "HTTP 429 Too Many Requests" in str(exc.value)
    # a throttle states itself in the headers and may send no body at all --
    # quoting only the body would lose the entire diagnosis
    assert "Retry-After: 60" in str(exc.value)
    assert "CF-Ray: deadbeef" in str(exc.value)


def test_a_cookie_value_never_reaches_the_raised_message():
    # this description travels into an exception that `runlog` persists and the
    # ops dashboard renders, so a session cookie would leave stderr for a file
    # and a served page. The cookie's *name* is the diagnostic signal (a WAF
    # challenge sets one); its value is not.
    resp = requests_response(
        403, "Forbidden", "https://gov.example/doc", "",
        {"Set-Cookie": "session=super-secret-value; Path=/; HttpOnly"})
    with pytest.raises(requests.HTTPError) as exc:
        net.raise_for_status(resp)
    assert "super-secret-value" not in str(exc.value)
    assert "session" in str(exc.value)          # the name still identifies it


def test_raise_for_status_passes_a_success_through():
    assert net.raise_for_status(
        requests_response(200, "OK", "https://x/", "{}")) is None
    assert net.raise_for_status(
        requests_response(302, "Found", "https://x/", "")) is None


def test_raise_for_status_attaches_the_response():
    # callers branch on the status (net's own 404 check, the RETRY_STATUS
    # branch), so the response must ride along on the exception
    resp = requests_response(500, "Server Error", "https://x/", "boom")
    with pytest.raises(requests.HTTPError) as exc:
        net.raise_for_status(resp)
    assert exc.value.response is resp


def test_describe_response_truncates_a_gateway_html_page():
    # a gateway in front of the endpoint can answer with a whole HTML page, which
    # must not swamp the traceback the description exists to clarify
    resp = requests_response(502, "Bad Gateway", "https://gateway.example/",
                             "<html>" + "x" * 9000 + "</html>")
    described = net.describe_response(resp, body_chars=600)
    assert len(described) < 600 + 300
    assert "more chars]" in described
