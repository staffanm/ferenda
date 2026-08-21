"""The internal API (`/internal-api/v1`) -- the half of the service the site
drives itself.

The service answers two different audiences over one origin, and they used to
share one path namespace and one OpenAPI schema:

  * **`/api/v1`** is the public read API: search, the catalog, a document and
    the citation graph. Anyone may call it, from anywhere, and `/docs` is its
    contract (docs/api/README.md is the prose version).
  * **`/internal-api/v1`** is what the served pages and the editor tooling call:
    login, the commentary/patch/crop editors, and the PDF export's background
    jobs. Nobody outside this origin has a reason to call any of it, and its
    shapes change whenever the UI does.

Publishing both in one schema advertised the second as if it were the first --
a reader of `/docs` saw `POST /api/v1/edit/commit` beside `GET /api/v1/search`
and could not tell which one is a promise. So they are two FastAPI apps: this
one is mounted at `/internal-api`, keeps its routes out of the public
`/openapi.json`, and serves its own Swagger UI at `/internal-api/docs` behind
the editor session.

Every route here is same-origin only, state-changing or not: `same_origin` is
an app-wide dependency, and the ops dashboard (`api/ops.py`), which is the same
audience at a different path, carries it too. That is a wider gate than the
CORS policy on the public app, which only stops a cross-origin *browser* from
reading a response -- reading the crop-review queue or an editor's name is
exactly as internal as writing a commit.
"""

from fastapi import Depends, FastAPI
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import JSONResponse

from . import auth, edit, errors, graphics, patch, pdfjob
from .auth import require_editor, same_origin

# what the mount point is, spelled once -- the JS that calls these routes
# (lib/assets/editor.js, collection.js, pdf.js) hardcodes the same string
PREFIX = "/internal-api"


app = FastAPI(
    title="lagen.nu internal API",
    version="1.0",
    description="The site's own surface: the editor, the ops tooling and the "
                "PDF export's background jobs. Not a public contract -- these "
                "shapes change with the UI. The public API is at /api/v1.",
    dependencies=[Depends(same_origin)],
    # its own Swagger UI, behind the editor session (below) rather than at the
    # FastAPI defaults, which take no dependency
    docs_url=None, redoc_url=None, openapi_url=None,
)

# the whole route table: each router declares its own leaf prefix (/auth,
# /edit, …) and gets the version segment here, so the mount point and the
# version are spelled in one place each and this loop is the complete answer to
# what the internal API serves.
for router in (auth.router, edit.router, patch.router, graphics.router,
               pdfjob.router):
    app.include_router(router, prefix="/v1")


@app.get("/openapi.json", include_in_schema=False,
         dependencies=[Depends(require_editor)])
def internal_openapi():
    """The internal schema. Behind the editor session for the same reason the
    routes it describes are same-origin: it is a map of the write surface, and
    the people who need it are the people who can already use it."""
    return JSONResponse(app.openapi())


@app.get("/docs", include_in_schema=False,
         dependencies=[Depends(require_editor)])
def internal_docs():
    """Swagger UI for the internal schema, at /internal-api/docs."""
    return get_swagger_ui_html(openapi_url=PREFIX + "/openapi.json",
                               title="lagen.nu internal API")


# the same rendered-page/JSON split the public app uses: a mounted sub-app has
# its own exception middleware, so the handlers must be installed on it too
errors.install(app)
