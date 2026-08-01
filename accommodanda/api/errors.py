"""The site's error responses: a rendered 404/500 page for readers, JSON for API
clients, and one ledger entry behind both.

Every error answer carries an id (`lib.errorlog`). The page shows it, the JSON
body carries it as ``error_id``, and `lagen all errors <id>` prints what was
recorded -- so a reader's "page 404s" becomes a url, a referer and a timestamp,
and a 500 becomes a traceback, without anyone grepping a container log.

Two response shapes from one handler, chosen by path: ``/api/v1/*`` (and the
OpenAPI routes) keep the JSON body their clients parse -- now with the id added
-- and everything else, which is the browsable site, gets HTML. Deciding on the
path rather than on Accept is deliberate: a browser sends ``Accept: text/html``
to the API too, and a script that curls a document url wants the same body a
browser's tab would show.

The handlers are installed by `install`, called from api.app at import.
"""

from fastapi.responses import HTMLResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import config
from ..lib import errorlog
from ..lib.tpl import ENV

# The ledger lives under CATALOG_ROOT -- the path config already guarantees is
# local disk -- and NOT under the data root beside the run ledger.
#
# That distinction is the whole point: on prod the data root is NFS, and the
# failure this ledger most needs to record is exactly the one where that mount
# is unreadable. Writing it there made the recording itself raise EIO inside the
# handler, so every honest 404 escalated to a bare 500 with no page and no
# entry (observed on ferenda.lagen.nu, 2026-08-01). An error ledger on the
# storage whose failure it reports is not a ledger.
LEDGER = config.CATALOG_ROOT / "httperrors.ndjson"

# path prefixes that answer JSON rather than a page
API_PREFIXES = ("/api/", "/docs", "/redoc", "/openapi.json", "/mcp")

_COPY = {
    404: ("Sidan finns inte",
          "Adressen leder inte till något dokument här. Den kan vara felstavad, "
          "eller peka på något som aldrig funnits på lagen.nu.",
          (("Till startsidan", "/"),
           ("Bläddra bland lagar", "/sfs/"),
           ("Bläddra bland rättsfall", "/dom/"))),
    500: ("Något gick fel",
          "Det här är vårt fel, inte ditt. Felet är loggat och går att spåra på "
          "referensen nedan.",
          (("Till startsidan", "/"),
           ("Om lagen.nu", "/om/"))),
}


def _page(status, error_id):
    """The rendered error page. `status` is 404 or 500; anything else borrows
    the 500 copy, since an unexpected status is a server-side surprise."""
    title, lead, suggestions = _COPY.get(status, _COPY[500])
    return ENV.get_template("error.html").render(
        # error.html's own slots
        error_id=error_id, suggestions=suggestions,
        # page.html's frontmatter carries the heading block: the status code as
        # the eyebrow, the message as the h1, the explanation as the subtitle
        title=title, eyebrow="Fel %d" % status, subtitle=lead,
        # the shell's remaining slots. solo drops the side columns (there is no
        # document here to hang a toc or a rail off); the rest are the empty
        # values StrictUndefined would otherwise refuse
        kind="", solo=True, own_h1=False, body_class="", head="", body="",
        toc="", island="", meta="", summary="", summary_text="")


def _wants_json(request):
    return request.url.path.startswith(API_PREFIXES)


def _record(request, status, exc=None, detail=None):
    """One ledger entry for this request, returning its id -- or None when the
    ledger could not be written.

    The referer is the field that earns this whole module: an internal 404 with
    a referer is a dead link the site itself published, which is a bug, while
    the same 404 with no referer is usually a bot walking urls.

    A failed *write* is caught, narrowly and deliberately: this is the one place
    in the system where failing to record must not change what the reader gets.
    An unwritable ledger is a real problem, but escalating every 404 on the site
    into a 500 because we could not take a note about it is strictly worse than
    serving the page without a reference -- and it is what happened when the
    ledger still lived on the NFS data root. The recovery is known and complete
    (render the page, drop the reference), which is what separates this from the
    catch-to-log the conventions forbid (rule:no-catch-log-continue). The
    traceback still reaches the process log via ServerErrorMiddleware."""
    try:
        return errorlog.record(
            LEDGER, status,
            method=request.method, url=str(request.url),
            client=request.client.host if request.client else None,
            referer=request.headers.get("referer"),
            user_agent=request.headers.get("user-agent"),
            detail=detail, exc=exc)["id"]
    except OSError:
        return None


async def http_exception_handler(request, exc):
    """Starlette/FastAPI HTTPException -- the raised 404s, the 422s, the 503
    from an unbuilt catalog. Only 404 and 5xx get a page and a ledger entry: a
    422 is the client's own malformed request, and logging one per bad query
    string would bury the errors worth reading."""
    if exc.status_code != 404 and exc.status_code < 500:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code,
                            headers=exc.headers)
    error_id = _record(request, exc.status_code, detail=exc.detail)
    if _wants_json(request):
        return JSONResponse({"detail": exc.detail, "error_id": error_id},
                            status_code=exc.status_code,
                            headers=exc.headers)
    return HTMLResponse(_page(exc.status_code, error_id),
                        status_code=exc.status_code)


async def unhandled_exception_handler(request, exc):
    """Anything that escaped a route: the traceback is what gets recorded, and
    the reader gets an id instead of a stack dump.

    The traceback still reaches the process log: Starlette's
    ServerErrorMiddleware calls this handler first and re-raises afterwards, so
    uvicorn logs the exception after the reader has been handed the page. This
    replaces the bare "Internal Server Error" body, it does not suppress the
    log."""
    error_id = _record(request, 500, exc=exc)
    if _wants_json(request):
        return JSONResponse({"detail": "Internal Server Error",
                             "error_id": error_id}, status_code=500)
    return HTMLResponse(_page(500, error_id), status_code=500)


def install(app):
    """Register both handlers on the app.

    `Exception` is the catch-all Starlette routes through
    ServerErrorMiddleware, which calls the handler and then re-raises, so
    registering it only replaces the response body -- the traceback still
    reaches the process log."""
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
