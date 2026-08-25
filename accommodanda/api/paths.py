"""The serving-side cache for the citation path graph (lib/pathgraph).

One graph per process, keyed on the catalog file's identity so a nightly
catalog swap is picked up on the next request. A request NEVER waits for a
build: the first shape of this module held every /path request behind a
lock while the graph built under the request thread -- on prod's disk that
build ran for hours, each queued request 504:ed at nginx's 60 s, and the
stuck threads kept the disk at 100 %. Now the build runs in one background
thread and `graph_if_ready` answers None (the endpoint's 503) until it is
done -- which is sub-second when relate's sidecar is present, and one
sequential scan when it is not.
"""

import threading
from pathlib import Path

from ..lib import pathgraph

_lock = threading.Lock()
_graph = None       # (stamp, Graph)
_building = None    # the stamp a background thread is building for, or None


def _stamp(catalog_path):
    st = Path(catalog_path).stat()
    return (str(catalog_path), st.st_mtime_ns, st.st_size)


def graph_if_ready(catalog_path):
    """The current catalog's Graph, or None while it is being built. Asking
    is what starts the build (once); ask again later."""
    global _building
    stamp = _stamp(catalog_path)
    with _lock:
        if _graph and _graph[0] == stamp:
            return _graph[1]
        if _building != stamp:
            _building = stamp
            threading.Thread(target=_build, args=(catalog_path, stamp),
                             daemon=True).start()
        return None


def _build(catalog_path, stamp):
    global _graph, _building
    try:
        g = pathgraph.load(catalog_path)
        with _lock:
            _graph = (stamp, g)
    finally:
        # on failure the stamp is released so the next request retries (and
        # the raise surfaces in the server log); on success it is simply done
        with _lock:
            if _building == stamp:
                _building = None
