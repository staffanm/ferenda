"""Shortest paths over the document-level citation graph -- /api/v1/path.

The graph the endpoint walks is the catalog's `links` table collapsed to
distinct (citing document, cited document) pairs: 2.6M edges over 271k
documents (measured 2026-08-25). That fits comfortably in one process as CSR
integer arrays (~25 MB plus the uri strings), where a full breadth-first
search runs in tens of milliseconds -- so an arbitrary-pair query needs no
precomputation, no landmark tables, and no SQL recursion. The adjacency is
built lazily on the first path request and cached against the catalog file's
identity, so a nightly catalog rebuild is picked up on the next request.
"""

import sqlite3
import threading
from array import array
from collections import deque
from pathlib import Path

from ..lib import facets

# every flow group gets a small integer; the BFS filters on these
_GROUP_ID = {name: i for i, name in enumerate(facets.FLOW_GROUP_NAMES)}


class Graph:
    """CSR adjacency over document uris: `fwd` follows citations (citing ->
    cited), `rev` the other way. `group[i]` is the node's flow-group id."""

    __slots__ = ("uris", "ids", "fwd_off", "fwd_dst", "rev_off", "rev_dst",
                 "group")

    def __init__(self, uris, ids, fwd, rev, group):
        self.uris, self.ids = uris, ids
        self.fwd_off, self.fwd_dst = fwd
        self.rev_off, self.rev_dst = rev
        self.group = group


def _csr(n, pairs):
    """(src, dst) int pairs -> (offsets, destinations) arrays."""
    deg = array("i", bytes(4 * (n + 1)))
    for s, _d in pairs:
        deg[s + 1] += 1
    off = array("i", bytes(4 * (n + 1)))
    for i in range(n):
        off[i + 1] = off[i] + deg[i + 1]
    dst = array("i", bytes(4 * len(pairs)))
    fill = array("i", off)
    for s, d in pairs:
        dst[fill[s]] = d
        fill[s] += 1
    return off, dst


def build(con):
    """The whole document-level graph off one connection. Streaming: the 2.6M
    edge rows never exist as one list of tuples."""
    ids = {}
    pairs = []
    for a, b in con.execute(
            "SELECT DISTINCT from_uri, to_root FROM links "
            "WHERE from_uri != to_root AND to_root IN "
            "(SELECT uri FROM documents)"):
        ia = ids.setdefault(a, len(ids))
        ib = ids.setdefault(b, len(ids))
        pairs.append((ia, ib))
    uris = [None] * len(ids)
    for u, i in ids.items():
        uris[i] = u
    group = array("b", bytes(len(ids)))
    for uri, source, kind in con.execute(
            "SELECT uri, source, kind FROM documents"):
        i = ids.get(uri)
        if i is not None:
            group[i] = _GROUP_ID[facets.flow_group(source, kind)]
    fwd = _csr(len(ids), pairs)
    rev = _csr(len(ids), [(b, a) for a, b in pairs])
    return Graph(uris, ids, fwd, rev, group)


# one graph per process, rebuilt when the catalog file changes identity
_lock = threading.Lock()
_cached = None          # (stamp, Graph)


def _stamp(path):
    st = Path(path).stat()
    return (str(path), st.st_mtime_ns, st.st_size)


def graph_for(catalog_path):
    global _cached
    stamp = _stamp(catalog_path)
    with _lock:
        if _cached and _cached[0] == stamp:
            return _cached[1]
        con = sqlite3.connect("file:%s?mode=ro" % catalog_path, uri=True)
        try:
            g = build(con)
        finally:
            con.close()
        _cached = (stamp, g)
        return g


def shortest(g, from_uri, to_uri, *, direction="both", groups=None):
    """The uris of one shortest chain from `from_uri` to `to_uri`, endpoints
    included -- or None when no chain exists. `direction` says which links a
    step may follow: "out" follows citations, "in" follows citers, "both"
    walks the graph undirected. `groups` (a set of flow-group names) filters
    the *intermediate* documents; the endpoints are always allowed."""
    s, t = g.ids.get(from_uri), g.ids.get(to_uri)
    if s is None or t is None:
        return None
    if s == t:
        return [from_uri]
    allowed = None if groups is None \
        else {_GROUP_ID[name] for name in groups}
    sides = []
    if direction in ("out", "both"):
        sides.append((g.fwd_off, g.fwd_dst))
    if direction in ("in", "both"):
        sides.append((g.rev_off, g.rev_dst))
    prev = {s: -1}
    q = deque([s])
    while q:
        u = q.popleft()
        for off, dst in sides:
            for i in range(off[u], off[u + 1]):
                v = dst[i]
                if v in prev:
                    continue
                if v == t:
                    path = [t, u]
                    while path[-1] != s:
                        path.append(prev[path[-1]])
                    return [g.uris[i] for i in reversed(path)]
                if allowed is not None and g.group[v] not in allowed:
                    continue
                prev[v] = u
                q.append(v)
    return None
