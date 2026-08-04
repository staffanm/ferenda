"""Shared HTTP session setup and a resilient request helper for the source
downloaders.

A single transient 5xx/429 or connection blip during a multi-thousand-document
harvest would otherwise abort the whole walk, so every session retries those at
the transport layer with exponential backoff (POST included -- the search
endpoints page over POST). ``raise_on_status=False`` leaves the final response
for the caller's ``raise_for_status()`` so error semantics are unchanged.

On top of that, ``request()`` rides out what the transport layer cannot see --
an empty/non-JSON 2xx body, or a 403/429 throttle -- honouring Retry-After, and
logs every failed response (status, headers, body) to stderr so a WAF/rate-limit
block is distinguishable from a genuine error.
"""

import atexit
import functools
import json
import os
import socket
import ssl
import sys
import tempfile
import time
from pathlib import Path

import certifi
import httpx
import requests
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import AuthorityInformationAccessOID
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

_RETRY = Retry(total=4, backoff_factor=0.5,
               status_forcelist=(429, 500, 502, 503, 504),
               allowed_methods=frozenset({"GET", "POST"}),
               raise_on_status=False)

# request()-level retry: covers the gaps urllib3 cannot -- a 2xx with an
# empty/non-JSON body, and a 403/429 throttle (some gateways send no Retry-After)
RETRIES = 6
RETRY_BACKOFF = 2.0        # seconds, doubled each attempt, capped at RETRY_MAX
RETRY_MAX = 60.0
RETRY_STATUS = frozenset({403, 408, 425, 429, 500, 502, 503, 504})

# the pipeline's two client identities: the honest harvester UA for services
# that accept it, and a browser UA for the government sites that 403 bare
# clients (the documents are public records; politeness lives in the delays)
HARVESTER_UA = "lagen.nu harvester (https://lagen.nu/, staffan.malmgren@gmail.com)"
BROWSER_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def make_session(user_agent: str) -> requests.Session:
    session = requests.Session()
    session.headers["User-Agent"] = user_agent
    adapter = HTTPAdapter(max_retries=_RETRY)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def make_http2_session(user_agent: str) -> httpx.Client:
    """An HTTP/2-capable client for a host that refuses HTTP/1.1. Konkurrensverket
    sits behind a Cloudflare front that 403s every HTTP/1.1 request and only serves
    HTTP/2, which requests/urllib3 cannot speak; httpx (the 0.x line declared as
    ``httpx[http2]`` -- a *different package* from the ``httpx2`` starlette's
    TestClient wants, not a version of it -- with the ``h2`` codec from its
    ``[http2]`` extra) can. The returned client presents
    the same small surface the harvest engine uses -- ``.request(method, url,
    ...)`` returning a response with ``raise_for_status``/``json``/``text``/
    ``content``/``status_code``/``headers``/``url``, plus a mutable ``.headers``
    dict -- so it drops into :func:`request` interchangeably with a requests
    Session, riding out failures via that function's ``httpx.HTTPError`` branch.
    ``follow_redirects`` mirrors requests' default; :func:`request`'s own retry
    loop stands in for the urllib3 transport-level retry a requests session gets."""
    client = httpx.Client(http2=True, follow_redirects=True)
    client.headers["User-Agent"] = user_agent
    return client


class _LegacyTLSAdapter(HTTPAdapter):
    """An HTTPS adapter that accepts a legacy small-DH-key handshake, which
    OpenSSL 3 refuses at its default security level (DH_KEY_TOO_SMALL)."""

    def init_poolmanager(self, *args, **kwargs):
        context = ssl.create_default_context()
        context.set_ciphers("DEFAULT:@SECLEVEL=1")
        kwargs["ssl_context"] = context
        return super().init_poolmanager(*args, **kwargs)


def mount_legacy_tls(session: requests.Session, prefix: str) -> None:
    """Accept a legacy small-DH-key TLS handshake for one host prefix only
    (e.g. ``https://conventions-ws.coe.int/``), keeping the standard retry
    policy. The security level is lowered for that host alone, never
    session-wide."""
    session.mount(prefix, _LegacyTLSAdapter(max_retries=_RETRY))


class _BundleTLSAdapter(HTTPAdapter):
    """An HTTPS adapter verifying against a caller-supplied trust bundle."""

    def __init__(self, cafile, **kwargs):
        self._cafile = cafile
        super().__init__(**kwargs)

    def init_poolmanager(self, *args, **kwargs):
        kwargs["ssl_context"] = ssl.create_default_context(cafile=self._cafile)
        return super().init_poolmanager(*args, **kwargs)


def _leaf_certificate(host, port, timeout):
    """The leaf certificate a host presents, read without verifying it -- which
    is all this step is for: the certificate's own AIA extension is what names
    the issuer to go and fetch, and until that issuer is in hand no chain can be
    built to verify anything against. Verification happens on the real requests,
    against the completed bundle."""
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout) as raw:
        with context.wrap_socket(raw, server_hostname=host) as tls:
            der = tls.getpeercert(binary_form=True)
    # typed Optional because a socket that never handshook has no peer cert;
    # this one has (the `with` completed), and a None here is a broken TLS stack
    assert der is not None, "%s completed a TLS handshake with no certificate" % host
    return x509.load_der_x509_certificate(der)


def _ca_issuers_url(certificate):
    """The caIssuers URL a certificate's Authority Information Access extension
    names -- where the issuer that signed it can be downloaded."""
    access = certificate.extensions.get_extension_for_class(
        x509.AuthorityInformationAccess).value
    urls = [d.access_location.value for d in access
            if d.access_method == AuthorityInformationAccessOID.CA_ISSUERS]
    if not urls:
        raise ValueError("certificate %s names no caIssuers URL, so the chain "
                         "its server omitted cannot be completed"
                         % certificate.subject.rfc4514_string())
    return urls[0]


# how far up an omitted chain to walk before giving up. Two is the real depth
# today (lifos omits both its intermediate and the cross-signed root above it);
# the bound is what stops a malicious or looping AIA graph from being followed
# forever.
AIA_CHAIN_MAX = 4


@functools.cache
def _trusted_roots():
    """The system trust anchors (certifi), grouped by subject -- the terminator
    every completed chain has to reach."""
    roots = {}
    for root in x509.load_pem_x509_certificates(Path(certifi.where()).read_bytes()):
        roots.setdefault(root.subject, []).append(root)
    return roots


def _anchored(certificate):
    """Whether `certificate` was issued by a trust anchor -- i.e. the chain
    above it is already complete. Verified cryptographically, not by name: a
    subject match alone proves nothing."""
    for root in _trusted_roots().get(certificate.issuer, ()):
        try:
            certificate.verify_directly_issued_by(root)
            return True
        except (ValueError, TypeError, InvalidSignature):
            continue
    return False


def _omitted_chain(leaf, session, timeout):
    """The intermediates a server left out, fetched by walking its leaf's AIA
    pointers upward until one of them is issued by a trust anchor.

    **Every link is verified before it is used.** `verify_directly_issued_by`
    checks that the fetched certificate actually signed the one below it, and
    the walk only stops on a certificate a certifi root demonstrably signed. So
    the bytes an attacker could substitute at the (plain-HTTP, by convention)
    caIssuers URL cannot enter the trust store: a forged or self-signed
    certificate fails the signature check, and a real certificate that chains
    nowhere trusted fails the terminator check. That is what makes this
    different from turning verification off, which is what the shape of this
    failure -- "unable to get local issuer certificate" -- usually tempts."""
    chain, certificate = [], leaf
    while not _anchored(certificate):
        if len(chain) == AIA_CHAIN_MAX:
            raise ValueError(
                "the chain above %s does not reach a trusted root within %d "
                "AIA hops" % (leaf.subject.rfc4514_string(), AIA_CHAIN_MAX))
        issuer = x509.load_der_x509_certificate(
            request(session, "GET", _ca_issuers_url(certificate),
                    timeout=timeout).content)
        # raises if `issuer` did not sign `certificate` -- the check the whole
        # helper's safety rests on
        certificate.verify_directly_issued_by(issuer)
        chain.append(issuer)
        certificate = issuer
    return chain


def mount_aia_chain(session: requests.Session, prefix: str, host: str,
                    port: int = 443, timeout: float = 30) -> None:
    """Verify one host's TLS against a trust bundle completed by AIA chasing,
    for that host prefix alone.

    Some servers send only their leaf certificate and leave the client to find
    the intermediates above it -- ``lifos.migrationsverket.se`` is one, and omits
    two (Let's Encrypt's YR2 and the ISRG "Root YR" cross-signed into ISRG Root
    X1) -- so every requests/curl fetch of it fails with "unable to get local
    issuer certificate" while browsers, which chase the certificate's Authority
    Information Access pointers, load it fine. This does the same, and verifies
    each fetched certificate against the one below it and the top of the walk
    against a certifi root (see :func:`_omitted_chain`) before any of it is
    trusted.

    The intermediates then go into the bundle an ``SSLContext`` verifies
    against, which is the only trust material it takes and which it takes only
    from disk -- hence the temporary file, one per mounted host, removed when
    the process exits."""
    chain = _omitted_chain(_leaf_certificate(host, port, timeout), session, timeout)
    handle, path = tempfile.mkstemp(prefix="aia-", suffix=".pem")
    with os.fdopen(handle, "wb") as bundle:
        bundle.write(Path(certifi.where()).read_bytes())
        for certificate in chain:
            bundle.write(b"\n")
            bundle.write(certificate.public_bytes(Encoding.PEM))
    atexit.register(lambda: Path(path).unlink(missing_ok=True))
    session.mount(prefix, _BundleTLSAdapter(path, max_retries=_RETRY))


# response headers worth quoting when a request fails: they are what tells a
# throttle or a WAF block apart from a genuine error
DIAGNOSTIC_HEADERS = ("Retry-After", "RateLimit-Reset", "X-RateLimit-Remaining",
                      "X-RateLimit-Limit", "Server", "Via", "CF-Ray", "X-Cache",
                      "X-Amzn-Trace-Id", "Content-Type", "Set-Cookie")

# a header whose *value* must never be reproduced: this description now travels
# into a raised exception, which `runlog` persists and the ops dashboard renders,
# so a session cookie would leave stderr for a file and a served page. That a
# cookie was set is the diagnostic signal (a WAF challenge sets one); its value
# is not, so only the names are kept.
REDACTED_HEADERS = {"set-cookie"}


def _header_value(name, value):
    if name.lower() not in REDACTED_HEADERS:
        return value
    return "<%s redacted>" % ", ".join(
        sorted({part.split("=", 1)[0].strip() for part in value.split(",")
                if part.strip()}) or ["value"])


def describe_response(response: requests.Response, body_chars: int = 2000) -> str:
    """What the server actually returned, as diagnostic lines: status, reason,
    url, the `DIAGNOSTIC_HEADERS` it sent, and its body truncated to
    `body_chars`.

    The single description of a failed HTTP response (rule:second-use-goes-to-lib):
    `_log_failure` prints it while a retry is still coming, `raise_for_status`
    raises it, and every direct caller in the package reaches it through the
    latter. Both want the same facts -- the headers are what distinguish a 429
    throttle from a real error, and the body is where an endpoint states its own
    diagnosis ("exceeds the available context size").

    Reads only what both transports this package speaks expose: `status_code`
    rather than requests' `ok`, and `reason_phrase` where httpx has no `reason`,
    because `request()` retries over either one.
    """
    reason = getattr(response, "reason", None) or getattr(
        response, "reason_phrase", "")
    lines = ["HTTP %d%s for %s" % (response.status_code,
                                   " " + reason if reason else "", response.url)]
    lines += ["  %s: %s" % (h, _header_value(h, response.headers[h]))
              for h in DIAGNOSTIC_HEADERS if h in response.headers]
    body = " ".join((response.text or "").split())
    if body:
        lines.append("  body[:%d]: %s%s" % (
            body_chars, body[:body_chars],
            "... [%d more chars]" % (len(body) - body_chars)
            if len(body) > body_chars else ""))
    return "\n".join(lines)


def raise_for_status(response: requests.Response) -> None:
    """`response.raise_for_status()` with what the server actually returned in
    the message -- `describe_response`: status, url, the diagnostic headers and
    the body. The single way this package turns a failed HTTP response into an
    exception (rule:second-use-goes-to-lib).

    The bare requests version reports only "400 Client Error: Bad Request for
    url: ..." and discards the rest, which is where the diagnosis lives: an LLM
    endpoint states its own fault in the body ("request (98435 tokens) exceeds
    the available context size"), while a throttle or WAF states it in
    `Retry-After`/`CF-Ray` and may send no useful body at all. Without them the
    caller sees a generic 4xx and has to reproduce the request by hand.

    """
    # requests initialises status_code to None and the adapter fills it in, so
    # the declared type is int|None; any response that reached us has one
    assert response.status_code is not None, "response carries no status code"
    if response.status_code < 400:
        return
    raise requests.HTTPError(describe_response(response), response=response)


def _log_failure(exc, response):
    """Write what the server actually returned to stderr, so a throttle/WAF
    block (a 403/429 with Retry-After or an HTML body) can be told apart from a
    genuine error or a one-off empty body. Keeps a shorter body than the raising
    caller: a harvest logs this on every retry of every document, where the
    raise happens once."""
    if response is None:
        print("download request failed: %s: %s" % (type(exc).__name__, exc),
              file=sys.stderr, flush=True)
        return
    print("download request failed: " + describe_response(response, 600),
          file=sys.stderr, flush=True)


def _retry_after(response):
    """The server-requested cooldown in seconds, if it sent a numeric
    Retry-After; else None (fall back to exponential backoff)."""
    value = response.headers.get("Retry-After") if response is not None else None
    return float(value) if value and value.isdigit() else None


class BudgetExceeded(Exception):
    """Raised by :func:`request` when the session's ``deadline`` (a monotonic
    timestamp a budgeted harvest sets, see ``lib.harvest.walk``) has passed:
    no further attempt or backoff sleep is worth its wall-clock cost. The
    harvest loop treats it like any other per-item failure -- the store stays
    dirty and the next run retries."""


def is_not_found(exc: requests.RequestException) -> bool:
    """Whether `exc` is a 404 raised by :func:`request`. A 404 is the one
    status a harvester routinely reads as *content* -- "the upstream holds no
    such document" -- rather than as a failure, so telling it apart from every
    other error is a recurring need. (`request` raises any non-throttle 4xx at
    once, so this is only ever reached for a real answer.)"""
    return exc.response is not None and exc.response.status_code == 404


def request(session, method, url, *, parse_json=False, retries=RETRIES, **kwargs):
    """Perform an HTTP request, riding out the transient failures a long
    unattended harvest meets: an empty/non-JSON 2xx body, a throttle (403/429),
    a 5xx that outlived the session's own retries, and connection drops or
    timeouts. Backoff is exponential (capped at RETRY_MAX) but defers to
    Retry-After. A non-throttle 4xx is a genuine error and is raised at once.
    Every failed response is logged once. Returns the parsed JSON when
    ``parse_json`` is set, else the Response (e.g. for binary downloads).

    A session may carry a ``deadline`` attribute (a ``time.monotonic()``
    timestamp, set by a budgeted incremental harvest): past it, no new attempt
    starts (:class:`BudgetExceeded`), a running attempt's timeout is capped to
    the time remaining, and a backoff sleep never outlives it -- so one sick
    endpoint cannot stall a walk for hours of retry burn."""
    kwargs.setdefault("timeout", 60)
    diagnosed = False
    for attempt in range(retries):
        deadline = getattr(session, "deadline", None)
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BudgetExceeded("harvest budget spent before %s %s"
                                     % (method, url))
            kwargs["timeout"] = min(kwargs["timeout"], max(remaining, 1.0))
        response = None
        try:
            response = session.request(method, url, **kwargs)
            raise_for_status(response)
            return response.json() if parse_json else response
        # both transports: requests raises RequestException (its JSONDecodeError
        # included, a subclass); the httpx HTTP/2 client raises httpx.HTTPError for
        # transport/status failures and a bare json.JSONDecodeError for an
        # empty/non-JSON 2xx body (requests' JSONDecodeError also subclasses it).
        except (requests.exceptions.RequestException, httpx.HTTPError,
                json.JSONDecodeError) as exc:
            response = getattr(exc, "response", None) or response
            status = getattr(response, "status_code", None)
            transient = (isinstance(exc, json.JSONDecodeError)
                         or status is None or status in RETRY_STATUS)
            if not transient or attempt == retries - 1:
                # the raise carries the full description already (raise_for_status);
                # logging it here too would print headers and body twice
                if response is None:
                    _log_failure(exc, response)
                raise
            if not diagnosed:
                _log_failure(exc, response)
                diagnosed = True
            wait = _retry_after(response) or min(RETRY_MAX, RETRY_BACKOFF * 2 ** attempt)
            if deadline is not None:
                # sleep at most to the deadline; the next attempt then raises
                # BudgetExceeded rather than burning another timeout
                wait = min(wait, max(deadline - time.monotonic(), 0))
            print("  retry %d/%d in %.0fs (HTTP %s)"
                  % (attempt + 1, retries - 1, wait, status or "-"),
                  file=sys.stderr, flush=True)
            time.sleep(wait)
