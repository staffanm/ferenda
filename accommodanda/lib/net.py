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

import json
import ssl
import sys
import time

import httpx
import requests
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
HARVESTER_UA = "lagen.nu harvester (https://lagen.nu/, staffan@tomtebo.org)"
BROWSER_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def make_session(user_agent):
    session = requests.Session()
    session.headers["User-Agent"] = user_agent
    adapter = HTTPAdapter(max_retries=_RETRY)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def make_http2_session(user_agent):
    """An HTTP/2-capable client for a host that refuses HTTP/1.1. Konkurrensverket
    sits behind a Cloudflare front that 403s every HTTP/1.1 request and only serves
    HTTP/2, which requests/urllib3 cannot speak; httpx (the ``httpx2`` fork, with
    the ``h2`` codec from its ``[http2]`` extra) can. The returned client presents
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


def mount_legacy_tls(session, prefix):
    """Accept a legacy small-DH-key TLS handshake for one host prefix only
    (e.g. ``https://conventions-ws.coe.int/``), keeping the standard retry
    policy. The security level is lowered for that host alone, never
    session-wide."""
    session.mount(prefix, _LegacyTLSAdapter(max_retries=_RETRY))


def _log_failure(exc, response):
    """Write what the server actually returned to stderr, so a throttle/WAF
    block (a 403/429 with Retry-After or an HTML body) can be told apart from a
    genuine error or a one-off empty body."""
    if response is None:
        print("download request failed: %s: %s" % (type(exc).__name__, exc),
              file=sys.stderr, flush=True)
        return
    lines = ["download request failed: HTTP %d for %s"
             % (response.status_code, response.url)]
    for header in ("Retry-After", "RateLimit-Reset", "X-RateLimit-Remaining",
                   "X-RateLimit-Limit", "Server", "Via", "CF-Ray", "X-Cache",
                   "X-Amzn-Trace-Id", "Content-Type", "Set-Cookie"):
        if header in response.headers:
            lines.append("  %s: %s" % (header, response.headers[header]))
    body = " ".join((response.text or "").split())
    if body:
        lines.append("  body[:600]: %s" % body[:600])
    print("\n".join(lines), file=sys.stderr, flush=True)


def _retry_after(response):
    """The server-requested cooldown in seconds, if it sent a numeric
    Retry-After; else None (fall back to exponential backoff)."""
    value = response.headers.get("Retry-After") if response is not None else None
    return float(value) if value and value.isdigit() else None


def is_not_found(exc):
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
    ``parse_json`` is set, else the Response (e.g. for binary downloads)."""
    kwargs.setdefault("timeout", 60)
    diagnosed = False
    for attempt in range(retries):
        response = None
        try:
            response = session.request(method, url, **kwargs)
            response.raise_for_status()
            return response.json() if parse_json else response
        # both transports: requests raises RequestException (its JSONDecodeError
        # included, a subclass); the httpx HTTP/2 client raises httpx.HTTPError for
        # transport/status failures and a bare json.JSONDecodeError for an
        # empty/non-JSON 2xx body (requests' JSONDecodeError also subclasses it).
        except (requests.exceptions.RequestException, httpx.HTTPError,
                json.JSONDecodeError) as exc:
            response = getattr(exc, "response", None) or response
            status = getattr(response, "status_code", None)
            if not diagnosed:
                _log_failure(exc, response)
                diagnosed = True
            transient = (isinstance(exc, json.JSONDecodeError)
                         or status is None or status in RETRY_STATUS)
            if not transient or attempt == retries - 1:
                raise
            wait = _retry_after(response) or min(RETRY_MAX, RETRY_BACKOFF * 2 ** attempt)
            print("  retry %d/%d in %.0fs (HTTP %s)"
                  % (attempt + 1, retries - 1, wait, status or "-"),
                  file=sys.stderr, flush=True)
            time.sleep(wait)
