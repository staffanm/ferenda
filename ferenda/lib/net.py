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
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator
from urllib.parse import urlsplit, urlunsplit

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
# empty/non-JSON body, and a throttle (403/429, or a non-standard code a
# WAF invented -- 466 is the one the Juridisk Publikation host answers a
# rate-limited client with; some gateways send no Retry-After)
RETRIES = 6
RETRY_BACKOFF = 2.0        # seconds, doubled each attempt, capped at RETRY_MAX
RETRY_MAX = 60.0
RETRY_STATUS = frozenset({403, 408, 425, 429, 466, 500, 502, 503, 504})

# the pipeline's two client identities: the honest harvester UA for services
# that accept it, and a browser UA for the government sites that 403 bare
# clients (the documents are public records; politeness lives in the delays)
HARVESTER_UA = "lagen.nu harvester (https://lagen.nu/, staffan.malmgren@gmail.com)"
BROWSER_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


# --------------------------------------------------------------------------
# robots.txt Crawl-delay
# --------------------------------------------------------------------------
#
# A host that publishes a Crawl-delay is stating the rate it wants to be read
# at, and it outranks whatever a source passed as its own `delay`
# (rule:respect-politeness). The EBA asks for 10 seconds and `eba_sync` ran at
# 0.5, twenty times faster, for as long as the harvest has existed.
#
# It is enforced here rather than in each source because there are some thirty
# sync functions and every one of them threads its own `delay` down to its own
# `time.sleep`: a rule added to any of them is a rule the next harvester
# forgets. `request` is the one call they all make, so the pacing sits on the
# request and cannot be left out.
#
# It is a *floor*, never a ceiling. A source that sleeps longer than the host
# asks keeps its own pace; this only ever adds the difference.

class RobotsUnread(Exception):
    """A host's robots.txt could not be read -- which is not the same as a host
    that answered and asked for nothing."""


#: host -> the Crawl-delay it asks for in seconds, or None where it asks none.
#: Read once per host per process.
_CRAWL_DELAY: dict[str, float | None] = {}
#: hosts whose robots.txt could not be read, so the warning is printed once
_UNREAD: set[str] = set()
#: host -> when the next request to it may be issued (`time.monotonic`)
_NEXT_REQUEST: dict[str, float] = {}
_PACE = threading.Lock()

#: how long to wait for a robots.txt before giving up on reading it. Giving up
#: is not consent: the host is then paced at `UNREAD_ROBOTS_DELAY`, because one
#: that will not answer promptly is the one most likely to want us slower.
ROBOTS_TIMEOUT = 15


def parse_crawl_delay(text: str, user_agent: str) -> float | None:
    """The Crawl-delay in seconds a robots.txt asks of `user_agent`, or None.

    robots.txt is a sequence of groups: one or more ``User-agent`` lines, then
    the directives that apply to them. A group naming us wins over the ``*``
    group, which is the precedence the standard sets -- a host that asks 10
    seconds of everyone and 1 of us means 1. A malformed or absent value is no
    value; we do not guess one."""
    groups: dict[str, float] = {}
    agents: list[str] = []
    fresh = True
    for line in text.splitlines():
        field, _, value = line.split("#")[0].partition(":")
        field, value = field.strip().lower(), value.strip()
        if field == "user-agent":
            if not fresh:                  # a new group starts here
                agents, fresh = [], True
            agents.append(value.lower())
        elif field:
            fresh = False
            if field == "crawl-delay" and agents:
                try:
                    seconds = float(value)
                except ValueError:
                    continue               # not a number: the host asks nothing
                for agent in agents:
                    groups[agent] = seconds
    named = [delay for agent, delay in groups.items()
             if agent and agent != "*" and agent == _product_token(user_agent)]
    return named[0] if named else groups.get("*")


def _product_token(user_agent: str) -> str:
    """The product token a robots.txt group would name us by: the first word of
    the User-Agent, before its version or its parenthesis.

    A substring test over the whole header is what this replaces. Several
    harvesters here send `BROWSER_UA`, so "Mozilla/5.0 (X11; Linux x86_64) …
    Chrome/…" would have matched a group named Chrome, Safari, Gecko or Linux
    and read another crawler's terms as ours."""
    return user_agent.strip().lower().split("/")[0].split(" ")[0]


#: what a host is read at when its robots.txt could not be read at all. Not
#: zero: a host we failed to ask is not a host that said yes, and the run this
#: pacing exists for is long enough that one blip must not silently drop it back
#: to the source's own delay for the rest of the process.
UNREAD_ROBOTS_DELAY = 2.0


def _read_crawl_delay(session, url: str, user_agent: str) -> float | None:
    """Fetch and read one host's robots.txt. Not through `request`: a host with
    no robots.txt answers 404, which is the normal case and not a failure to
    retry over.

    Raises `RobotsUnread` where the file could not be read at all, which is a
    different thing from a host that answered and asked for nothing -- the
    caller must not record a failure as consent."""
    parts = urlsplit(url)
    robots = urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))
    timeout = ROBOTS_TIMEOUT
    deadline = getattr(session, "deadline", None)
    if deadline is not None:
        # a budgeted harvest's deadline binds this read too -- it is a request
        # to the same host, on the same budget
        timeout = min(timeout, max(deadline - time.monotonic(), 1.0))
    try:
        # counted like any other attempt: `attempts()` is the one download
        # metric that cannot be inferred from the others, and a robots read is
        # a request to the host
        _attempts.n = getattr(_attempts, "n", 0) + 1
        response = session.request("GET", robots, timeout=timeout)
    except (requests.exceptions.RequestException, httpx.HTTPError) as exc:
        raise RobotsUnread("%s: %s" % (robots, exc)) from exc
    status = getattr(response, "status_code", 0)
    if status == 404:
        # the normal case for a host that publishes no terms at all
        return None
    if status != 200:
        raise RobotsUnread("%s: HTTP %s" % (robots, status))
    return parse_crawl_delay(getattr(response, "text", "") or "", user_agent)


def crawl_delay(session, url: str) -> float | None:
    """What `url`'s host asks to be read at, read once per host per process.

    A host whose robots.txt could not be read is paced at
    `UNREAD_ROBOTS_DELAY` and said so once on stderr, and is asked again on the
    next request rather than remembered as having answered. Recording the failure as
    "asks for nothing" would let one connection blip on the first request of a
    long harvest drop the whole run back to the source's own delay against a
    host that wanted ten seconds -- the exact failure this pacing exists to
    prevent, made invisible."""
    host = urlsplit(url).netloc
    with _PACE:
        if host in _CRAWL_DELAY:
            return _CRAWL_DELAY[host]
    agent = (getattr(session, "headers", {}) or {}).get("User-Agent") or ""
    try:
        delay = _read_crawl_delay(session, url, agent)
    except RobotsUnread as exc:
        # *not* cached: a failure is not an answer, and caching this one would
        # pin a host that asks ten seconds at two for the rest of the process
        # on the strength of a single blip. The next request to it asks again,
        # and the run self-heals the moment the file comes back. Said once per
        # host, so a persistently unreadable one does not fill the log.
        with _PACE:
            first = host not in _UNREAD
            _UNREAD.add(host)
        if first:
            print("  robots.txt unread, pacing %s at %.1fs until it answers (%s)"
                  % (host, UNREAD_ROBOTS_DELAY, exc), file=sys.stderr, flush=True)
        return UNREAD_ROBOTS_DELAY
    with _PACE:
        # another thread may have read it first; one answer per host either way
        return _CRAWL_DELAY.setdefault(host, delay)


def pace(session, url: str) -> None:
    """Wait until `url`'s host may be read again.

    The slot is reserved under the lock and slept outside it, so two threads
    harvesting one host queue behind each other instead of both sleeping the
    same interval and arriving together.

    A budgeted session's `deadline` binds the wait as it binds everything else:
    a slot that falls past it raises `BudgetExceeded` rather than sleeping
    through the budget and issuing the request late. Under `fan_out` the
    reserved slot grows with the queue depth, so this is what keeps one slow
    host from spending a whole run's budget on waiting."""
    delay = crawl_delay(session, url)
    if not delay:
        return
    host = urlsplit(url).netloc
    deadline = getattr(session, "deadline", None)
    with _PACE:
        now = time.monotonic()
        due = max(now, _NEXT_REQUEST.get(host, now))
        if deadline is not None and due > deadline:
            # raised *before* the slot is taken: reserving one for a request
            # that never happens leaves every later thread on this host queued
            # behind a wait nobody is serving
            raise BudgetExceeded(
                "harvest budget spent waiting out %s's %.0fs crawl-delay "
                "before %s" % (host, delay, url))
        _NEXT_REQUEST[host] = due + delay
    if due > now:
        time.sleep(due - now)


def forget_crawl_delays() -> None:
    """Drop the per-host robots.txt cache and pacing state. For tests, and for
    a long-lived process that should re-read a host's terms."""
    with _PACE:
        _CRAWL_DELAY.clear()
        _NEXT_REQUEST.clear()
        _UNREAD.clear()


def make_session(user_agent: str) -> requests.Session:
    session = requests.Session()
    session.headers["User-Agent"] = user_agent
    adapter = HTTPAdapter(max_retries=_RETRY)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def set_deadline(session: requests.Session | httpx.Client,
                 when: float | None) -> None:
    """Give `session` the wall-clock bound :func:`request` honours -- a
    ``time.monotonic()`` timestamp past which no attempt starts, a running
    attempt's timeout is capped to the time left, and a backoff sleep never
    outlives it. `None` clears it.

    A setter of its own because `deadline` is an attribute neither transport
    declares: `request` reads it back with ``getattr(session, "deadline",
    None)``. Writing it in one named place puts the one unchecked assignment
    here, under a suppression that says why, instead of in every harvest that
    arms a budget."""
    session.deadline = when  # ty: ignore[invalid-assignment] — an extra neither transport type declares


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


# HTTP attempts made, per thread. Per *thread* because harvests fan their scopes
# out across a pool (lib.harvest.fan_out) and a global counter would bill one
# agency's requests to whichever finished next.
#
# Attempts, not calls: `request` retries a throttle or a 5xx internally, and it is
# the attempts that reach the network and that a rate limiter counts. The number
# is the one download metric that cannot be inferred from the others -- a source
# that reports "1,025 seen" may have made 1,025 requests or three SPARQL queries
# returning 1,025 records, and only this tells them apart.
_attempts = threading.local()


def attempts() -> int:
    """HTTP attempts made on this thread since the process started."""
    return getattr(_attempts, "n", 0)


@contextmanager
def counted() -> Iterator[Callable[[], int]]:
    """Count the HTTP attempts made on this thread inside the block. Yields a
    callable returning the delta -- live during the block, final after it."""
    start = attempts()
    total = None

    def delta():
        return attempts() - start if total is None else total
    try:
        yield delta
    finally:
        total = attempts() - start


def request(session, method, url, *, parse_json=False, retries=RETRIES, **kwargs):
    """Perform an HTTP request, riding out the transient failures a long
    unattended harvest meets: an empty/non-JSON 2xx body, a throttle
    (403/429, or a non-standard code a WAF invented such as 466), a 5xx that
    outlived the session's own retries, and connection drops or timeouts. Backoff is exponential (capped at RETRY_MAX) but defers to
    Retry-After. A non-throttle 4xx is a genuine error and is raised at once.
    Every failed response is logged once. Returns the parsed JSON when
    ``parse_json`` is set, else the Response (e.g. for binary downloads).

    Before the first attempt the request waits out whatever Crawl-delay the
    host's robots.txt asks for (`pace`), which outranks the source's own
    `delay` and is a floor on it, never a ceiling.

    A session may carry a ``deadline`` attribute (a ``time.monotonic()``
    timestamp, set by a budgeted incremental harvest): past it, no new attempt
    starts (:class:`BudgetExceeded`), a running attempt's timeout is capped to
    the time remaining, and a backoff sleep never outlives it -- so one sick
    endpoint cannot stall a walk for hours of retry burn."""
    kwargs.setdefault("timeout", 60)
    diagnosed = paced = False
    for attempt in range(retries):
        deadline = getattr(session, "deadline", None)
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BudgetExceeded("harvest budget spent before %s %s"
                                     % (method, url))
            kwargs["timeout"] = min(kwargs["timeout"], max(remaining, 1.0))
        if not paced:
            # what the host asks to be read at, before the first attempt only:
            # a retry has its own backoff and defers to Retry-After, which is
            # the host talking about this request rather than about its rate.
            # After the budget check, because a session past its deadline must
            # issue nothing at all -- the robots.txt read included.
            pace(session, url)
            paced = True
        response = None
        try:
            _attempts.n = getattr(_attempts, "n", 0) + 1
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


def get_text(session, url: str, delay: float) -> str:
    """One page fetched as text, followed by the source's own politeness delay.

    The listing walk every HTML harvest repeats, written once
    (rule:second-use-goes-to-lib): acer, berec, easa, eba, eiopa and esma each
    carried a byte-identical copy. The delay follows the fetch, so a walk that
    stops on this page has already paid for the one it read; the host's own
    Crawl-delay is a floor under it, applied by `request`."""
    text = request(session, "GET", url, timeout=120).text
    time.sleep(delay)
    return text


# no return annotation: the pending lists this feeds are typed `list[Pending]`,
# whose fetcher slot is `Callable[[], bytes | str] | None`, and `list` is
# invariant -- a list of exactly-typed fetchers is then not a `list[Pending]`
def fetcher(session, url: str, *, timeout: float):
    """A callable that downloads `url`'s body -- what `lib.harvest.walk_records`
    takes for a document it may or may not decide to fetch.

    `timeout` is the source's own: the agencies differ by a factor of five in
    how long a large PDF takes them to serve."""
    return lambda: request(session, "GET", url, timeout=timeout).content
