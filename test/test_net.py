"""Tests for the shared HTTP helpers (`accommodanda.lib.net`): the failed-response
description and the raising wrapper `request()` and every direct caller use.

The responses are REAL `requests`/`httpx` objects, built offline. That matters
here more than usual: `raise_for_status` claims to read only what both transports
expose, and a hand-rolled stand-in with the convenient attributes would assert
that claim without testing it -- a real `httpx.Response` has neither `ok` nor
`reason`, and reaches `url` through its bound request."""

import datetime
from unittest import mock

import httpx
import pytest
import requests
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

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


# --------------------------------------------------------------------------
# AIA chain completion (`mount_aia_chain`) -- what makes it safe
# --------------------------------------------------------------------------

def selfsigned(common_name):
    """A self-signed CA -- what an attacker who controls the plain-HTTP
    caIssuers fetch would substitute for the real intermediate."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.datetime.now(datetime.UTC)
    return key, (x509.CertificateBuilder()
                 .subject_name(name).issuer_name(name)
                 .public_key(key.public_key()).serial_number(1)
                 .not_valid_before(now)
                 .not_valid_after(now + datetime.timedelta(days=1))
                 .add_extension(x509.BasicConstraints(ca=True, path_length=None),
                                critical=True)
                 .sign(key, hashes.SHA256()))


def issued_by(issuer_key, issuer_cert, common_name):
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.datetime.now(datetime.UTC)
    return key, (x509.CertificateBuilder()
                 .subject_name(x509.Name(
                     [x509.NameAttribute(NameOID.COMMON_NAME, common_name)]))
                 .issuer_name(issuer_cert.subject)
                 .public_key(key.public_key()).serial_number(2)
                 .not_valid_before(now)
                 .not_valid_after(now + datetime.timedelta(days=1))
                 .sign(issuer_key, hashes.SHA256()))


def test_anchored_rejects_a_certificate_no_trusted_root_signed():
    """The terminator check. A `cafile` entry is a *trust anchor*, so appending
    an unverified AIA-fetched certificate to certifi's bundle would trust it
    outright -- an attacker's self-signed CA included. `_anchored` is what stops
    the walk only on a certificate a real root demonstrably signed."""
    _key, evil = selfsigned("EVIL-CA")
    assert net._anchored(evil) is False


def serves(certificate):
    """A session answering every request with `certificate` -- the plain-HTTP
    caIssuers fetch, with the bytes an attacker on that path would choose."""
    class _Session:
        def request(self, _method, _url, **_kwargs):
            resp = requests.Response()
            resp.status_code = 200
            resp._content = certificate.public_bytes(serialization.Encoding.DER)
            resp.url = "http://evil.example/ca"
            return resp
    return _Session()


def test_omitted_chain_refuses_an_issuer_naming_itself_the_real_one():
    """The signature check, which is the one that matters.

    The caIssuers URL is named by a certificate read over a deliberately
    unverified connection and is fetched over plain HTTP, so an attacker on that
    path chooses the bytes -- and chooses the *name* on them too. A forgery
    carrying the real issuer's DN gets past every name comparison and is stopped
    only by the signature not verifying against its key."""
    real_key, real = selfsigned("Real Intermediate")
    _leaf_key, leaf = issued_by(real_key, real, "victim.example")
    # same subject name as the issuer the leaf really has, a different key
    _evil_key, evil = selfsigned("Real Intermediate")
    assert evil.subject == leaf.issuer          # the name check would pass ...
    with mock.patch.object(net, "_ca_issuers_url",
                           return_value="http://evil.example/ca"), \
            pytest.raises(InvalidSignature):    # ... the signature does not
        net._omitted_chain(leaf, serves(evil), 5)


def test_omitted_chain_refuses_an_issuer_for_another_certificate_entirely():
    """The cheaper half of the same check: a substituted certificate that does
    not even claim to be the leaf's issuer."""
    _evil_key, evil = selfsigned("EVIL-CA")
    _leaf_key, leaf = selfsigned("victim.example")
    with mock.patch.object(net, "_ca_issuers_url",
                           return_value="http://evil.example/ca"), \
            pytest.raises(ValueError):
        net._omitted_chain(leaf, serves(evil), 5)


def test_omitted_chain_gives_up_rather_than_following_a_loop():
    """A self-issued certificate served at its own caIssuers URL would otherwise
    be walked forever; the bound is what stops a malicious or looping AIA
    graph."""
    key, ca = selfsigned("LOOP-CA")
    _leaf_key, leaf = issued_by(key, ca, "victim.example")
    with mock.patch.object(net, "_ca_issuers_url",
                           return_value="http://loop.example/ca"), \
            pytest.raises(ValueError, match="does not reach a trusted root"):
        net._omitted_chain(leaf, serves(ca), 5)


def test_omitted_chain_stops_at_an_anchored_certificate():
    """A leaf whose issuer is already a trust anchor needs no completion at all,
    and must not send a request to find that out."""
    class _Refuses:
        def request(self, *_a, **_kw):
            raise AssertionError("fetched an issuer for an anchored leaf")

    _key, leaf = selfsigned("anchored.example")
    with mock.patch.object(net, "_anchored", return_value=True):
        assert net._omitted_chain(leaf, _Refuses(), 5) == []
