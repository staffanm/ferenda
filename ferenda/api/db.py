"""The catalog handle the whole service layer shares -- plus the one fingerprint
both editors guard a concurrent write with.

Every read surface (`api/app.py`'s REST endpoints, `api/mcp.py`'s tools,
`api/ops.py`'s health page) reaches the *same* SQLite catalog, opens it
read-only, and has to answer sanely when it is not built yet. That was five
copies of the same open-and-close preamble and one path constant per module,
two of which had drifted to a raw ``sqlite3.connect("file:…?mode=ro")`` --
bypassing `catalog.connect_ro`'s once-per-process additive migrations, so a
process whose first catalog touch was `/ops` selected a column an older catalog
lacks. One path, one connect, here. (`build.py` keeps its own constant: it
*writes* the catalog, and takes a read-write `catalog.connect`.)

`base_sha` sits here for the same reason: the markdown editor
(`api/editcontent.py`) and the patch editor (`api/patch.py`) fingerprint the
text an edit was based on identically, and a drift guard that differs between
the two write surfaces is a bug waiting for the first divergence.
"""

import hashlib
from contextlib import contextmanager

from fastapi import HTTPException

from .. import config
from ..lib import catalog

CATALOG = config.CATALOG_ROOT / "catalog.sqlite"

NOT_BUILT = "catalog not built -- run `lagen all relate`"


def catalog_ready():
    """Whether a catalog has been built. The read endpoints that *require* it
    take `get_con` (503 when absent); the ones that merely enrich an answer with
    it (the citation pinning behind /search, a dv facsimile's stamped PDF path)
    ask this first and go without."""
    return CATALOG.exists()


@contextmanager
def connection():
    """A read-only catalog connection for the duration of a block. One per
    request / per tool call (SQLite connections are not shared across threads,
    and both FastAPI and the MCP SDK run sync handlers in a threadpool);
    `catalog.connect_ro` applies the additive schema migrations once per process
    before the first one is handed out."""
    con = catalog.connect_ro(CATALOG)
    try:
        yield con
    finally:
        con.close()


def get_con():
    """The FastAPI dependency: one read-only connection for the request, or a
    503 when no catalog has been built. (`browse.py` overrides this to bind the
    in-process generator client to the catalog it is building from.)"""
    if not catalog_ready():
        raise HTTPException(503, NOT_BUILT)
    with connection() as con:
        yield con


def base_sha(text):
    """The fingerprint an editor sends back with a save, so a source that
    changed under an open editing session is a 409 rather than a silent
    overwrite."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
