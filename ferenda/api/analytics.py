"""Server-side Matomo tracking for the machine-facing surfaces -- the REST API
and the MCP server.

The site's HTML pages report themselves from the browser (`lib/assets/matomo.js`,
the cookie-less page tracker). An API call or an MCP
tool call has no browser to run that snippet in, so those hits are sent from
here: one call to Matomo's HTTP Tracking API per request, off the response path.

Four things this is built around:

  * **Its own Matomo site.** `config.MATOMO_SITE_API` is a *different* site id
    from the one the pages use. Agents and scripts have no bounce rate or visit
    duration worth reading. Mixing them into the human numbers would make both
    reports misleading, so machine traffic uses a separate Matomo site.
  * **Off the hot path.** `track` only enqueues; one daemon thread drains the
    queue and does the HTTP. Analytics must never add latency to an API response
    nor fail one, so the queue is bounded and overflow is dropped on the floor.
  * **No visitor address is stored.** Matomo sees the app container as the
    client (overriding it needs an auth token we deliberately do not hold, so
    there is no Matomo credential in this deployment at all). Visitors are
    instead grouped by `_id`, a keyed hash of the caller's address, its
    user-agent and the date, under a salt minted fresh per process -- a day's
    calls from one client group into one visit without the address itself
    reaching Matomo. Same privacy stance as the pages' cookie-less tracker.
  * **Only foreign traffic -- until something breaks.** The site's own pages
    call `/api/v1/*` over XHR (the ⌘K palette, the context rail, the citation
    graph). Those are already counted as page views by the browser tracker,
    so counting them again here would bury actual API consumers under the site's
    own chatter -- same-origin browser fetches are skipped, see `_own_page_xhr`.
    A *failing* call is exempt: every error is recorded, whoever made it, under
    an `error` branch of the page title. Failures are the half of the traffic
    nobody reports back, so the numbers have to.
"""

import hashlib
import logging
import queue
import secrets
import threading
import time
from urllib.parse import urlsplit

import httpx
from starlette.requests import Request

from .. import config
from .errors import under

log = logging.getLogger(__name__)

# the REST surface worth counting: the public read API plus its human-facing
# OpenAPI pages. Deliberately not the whole app -- the static site below "/" is
# counted in the browser, /ops is an operator dashboard rather than an audience,
# and /internal-api is the site calling itself (`auth/me` alone fires on every
# page load, so counting it would make the "API" numbers a copy of the site's
# page views). Both of those now sit outside this prefix set by construction.
# (Not the same set as errors.JSON_PREFIXES, which answers a different question:
# what shape an error takes. Matched the same way, with `under`.)
API_PREFIXES = ("/api/v1", "/docs", "/redoc", "/openapi.json")

ENABLED = bool(config.MATOMO_URL and config.MATOMO_SITE_API)
# Parsed once, here, so a malformed endpoint raises at import -- the worker
# thread must never be handed a URL it can choke on: httpx.InvalidURL is not an
# httpx.HTTPError, so it would escape the drain loop's catch, kill the only
# consumer of the queue and stop tracking silently. config's scheme check does
# not see a bad port or a stray newline; this does.
TRACKER = httpx.URL(config.MATOMO_URL) if ENABLED else None

_QUEUE_MAX = 512                 # ~a burst of hits; beyond it Matomo is the problem
_TIMEOUT = 5.0                   # a tracking POST is fire-and-forget, never worth waiting on
# per-process, never persisted: restarting the app breaks visitor continuity,
# which is the privacy-preferring direction to fail in.
_SALT = secrets.token_bytes(16)

_queue = queue.Queue(maxsize=_QUEUE_MAX)
_worker_lock = threading.Lock()
_worker = None


def _visitor_id(client_ip, user_agent):
    """Matomo's 16-hex `_id` for this caller: a keyed digest of address +
    user-agent + today's date. Stable within a day so one client's calls read as
    one visit, unlinkable across days, and one-way -- the address is a hash
    input here and is never sent."""
    seed = "%s\0%s\0%s" % (client_ip, user_agent, time.strftime("%Y-%m-%d"))
    return hashlib.blake2s(seed.encode("utf-8"), key=_SALT,
                           digest_size=8).hexdigest()


def _drain():
    """The worker loop: one Matomo POST per queued hit, forever."""
    # nothing enqueues unless ENABLED, which is what parsed TRACKER
    assert TRACKER is not None, "the tracking worker started with no endpoint"
    with httpx.Client(timeout=_TIMEOUT) as http:
        while True:
            params, headers = _queue.get()
            try:
                http.post(TRACKER, data=params, headers=headers)
            except httpx.HTTPError as exc:
                # The one thing this thread must not do is die: it is the only
                # drain of a bounded queue, so an unhandled error here would
                # silently stop all later tracking. A hit is disposable and there
                # is nothing to retry onto (Matomo is either up or it isn't), so
                # the recovery is exactly: drop it, keep draining.
                log.warning("matomo: %s", exc)


def _enqueue(params, headers):
    """Hand a built hit to the worker, starting it on first use -- and restarting
    it if it ever died, since a dead `Thread` is not `None` and nothing else
    would ever notice the drain had stopped. Full queue => dropped at debug: the
    worker logs the actual failure, and a filling queue is that failure's
    symptom, not news worth repeating once per request on the serving path."""
    global _worker
    if _worker is None or not _worker.is_alive():
        with _worker_lock:
            if _worker is None or not _worker.is_alive():
                _worker = threading.Thread(target=_drain, name="matomo",
                                           daemon=True)
                _worker.start()
    try:
        _queue.put_nowait((params, headers))
    except queue.Full:
        log.debug("matomo: queue full, hit dropped")


def _hit(url, title, request):
    """Build and enqueue one Matomo pageview for `request`.

    `bots=1` is load-bearing: Matomo drops requests from known bot user-agents by
    default, and on this site id the "bots" -- crawlers, agent frameworks, MCP
    hosts -- *are* the audience. `send_image=0` asks for a 204 instead of the
    tracking gif. The caller's user-agent and Accept-Language ride as the
    outgoing request's own headers rather than as `ua`/`lang` parameters, which
    Matomo only honours for an authenticated tracker."""
    user_agent = request.headers.get("user-agent", "")
    params = {"idsite": config.MATOMO_SITE_API, "rec": "1", "apiv": "1",
              "url": url, "action_name": title,
              "_id": _visitor_id(request.client.host if request.client else "-",
                                 user_agent),
              "bots": "1", "send_image": "0"}
    if request.headers.get("referer"):
        params["urlref"] = request.headers["referer"]
    # As *bytes*: Starlette latin-1-decodes header bytes off the wire, so a
    # user-agent carrying any byte >= 0x80 (ordinary crawler traffic) arrives as
    # a non-ASCII str, which httpx refuses to encode -- a UnicodeEncodeError,
    # not an httpx.HTTPError, i.e. one that would kill the drain thread. Encoding
    # back to latin-1 sends the caller's original bytes through verbatim.
    _enqueue(params,
             {"User-Agent": user_agent.encode("latin-1"),
              "Accept-Language":
                  request.headers.get("accept-language", "").encode("latin-1")})


def _own_page_xhr(request):
    """Whether this is one of our own pages calling the API from the browser.
    `Sec-Fetch-Site` is sent by every current browser and by nothing else, so a
    same-origin value identifies site chatter precisely; the Referer check
    catches a browser old enough to lack the header."""
    if request.headers.get("sec-fetch-site") == "same-origin":
        return True
    referer = request.headers.get("referer", "")
    return bool(referer) and urlsplit(referer).netloc == request.url.netloc


def _keep_warm(request):
    """Whether this is the prod keep-warm cron rather than a caller.

    The 31 GB OpenSearch index cannot stay in prod's ~8 GB page cache, so a
    search after an idle hour reads hundreds of scattered blocks off an
    HDD-class disk and costs 10+ s. A cron search every 15 minutes holds the
    hot blocks resident. The header is the probe's own declaration -- no
    user-agent sniffing, and a caller who sets it only removes itself from
    our audience numbers."""
    return request.headers.get("x-keep-warm") == "1"


def _titled(steps, failed):
    """A Matomo page title from its path steps, branched on the outcome:
    `API/search` when it worked, `API/error/search` when it did not.

    Matomo splits a title on "/" into a tree, so one `error` branch per surface
    collects every failure of that surface -- the split a reader wants first
    ("is anything broken?") without a second site or a configured custom
    dimension, and each tool/endpoint still keeps its own leaf underneath."""
    return "/".join([steps[0]] + (["error"] if failed else []) + list(steps[1:]))


def _api_title(path, failed):
    """The title for a REST hit: `/api/v1/search` -> "API/search"."""
    return _titled(["API", path.removeprefix("/api/v1").strip("/")], failed)


def track_mcp(scope, method, tool, failed=False):
    """Track one MCP request -- `method` is the JSON-RPC method, `tool` the tool
    name for a `tools/call` (None otherwise), and `failed` whether the response
    carried an error.

    The URL is synthetic (`/mcp/tools/call/get_document`): every MCP request is a
    POST to the same `/mcp/` path, so the tool name has to go *somewhere* for the
    Pages report to be worth anything, and a URL path is what Matomo builds its
    tree from. Nothing links to these; they exist to be counted. The URL is the
    same whether the call worked or not -- so Pages counts demand per tool, and
    the title's `error` branch counts what that demand ran into."""
    request = Request(scope)
    steps = [method] if tool is None else [method, tool]
    _hit(str(request.base_url).rstrip("/") + "/mcp/" + "/".join(steps),
         _titled(["MCP"] + steps, failed), request)


class Tracked:
    """ASGI middleware recording the public REST surface (`API_PREFIXES`). Pure
    pass-through for everything else -- notably the static site, which is by far
    the most requested path through this app and tracks itself in the browser,
    and /internal-api, which is the site calling itself.

    A plain ASGI class rather than a `@app.middleware("http")` function: those
    are Starlette `BaseHTTPMiddleware` instances, which wrap every response in a
    task group and a stream regardless of whether we care about the path."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        # GET only: the read API answers nothing else, and this middleware sits
        # *outside* CORSMiddleware (add_middleware inserts at position 0, so the
        # last one added is outermost), which answers a cross-origin client's
        # OPTIONS preflight itself with a 200 -- counting those would double
        # every hit from exactly the browser-app consumers worth counting.
        if (scope["type"] != "http" or scope["method"] != "GET"
                or not under(scope["path"], API_PREFIXES)):
            return await self.app(scope, receive, send)
        status = None

        async def watched(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, watched)
        except Exception:
            # Something got past the app's own exception handlers (api/errors.py).
            # Whether the caller has already had a 200 header or gets a 500 does
            # not change what happened -- the request did not complete, and that
            # is the thing worth counting. Recorded, then re-raised untouched:
            # this middleware observes, it does not handle.
            self._record(scope, failed=True)
            raise
        self._record(scope, failed=status is None or status >= 400)

    def _record(self, scope, failed):
        request = Request(scope)
        # Our own pages call the API over XHR, and the browser tracker already
        # counted the page that made the call -- so a *successful* one would
        # double-count. A failing one is a defect report rather than an audience
        # measurement, and those are worth having whoever made the call. The
        # prod keep-warm cron is the same trade one step further out: it is our
        # own traffic, four calls an hour, and counting it would report a
        # machine as an audience.
        if failed or not (_own_page_xhr(request) or _keep_warm(request)):
            _hit(str(request.url), _api_title(scope["path"], failed), request)
