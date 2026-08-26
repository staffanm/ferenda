"""The document-level citation graph as CSR arrays -- built at relate,
walked by /api/v1/path.

Two I/O lessons are baked in (measured on prod 2026-08-26, where the 6 GB
catalog sits on a ~80-IOPS virtual disk):

* `build` reads the links table with ONE sequential full-table scan and
  filters/dedupes in Python. The obvious `SELECT DISTINCT ... WHERE to_root
  IN (SELECT uri FROM documents)` plans as ~322k random index probes --
  2 s on dev NVMe, *hours* on prod. Count I/O, not seconds.
* relate writes the finished arrays as a sidecar (`graph-edges.bin`) beside
  the catalog, so a serving process loads the graph in well under a second
  instead of paying the scan at all. The sidecar travels with the catalog
  (same directory, same rsync).
"""

import sqlite3
from array import array
from collections import deque
from pathlib import Path

from . import facets

# every flow group gets a small integer; BFS filters on these
GROUP_ID = {name: i for i, name in enumerate(facets.FLOW_GROUP_NAMES)}

SIDECAR = "graph-edges.bin"
_MAGIC = b"lagen-pathgraph-1\n"


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
    """The whole graph off one connection: one sequential scan of `links`
    (never per-document index probes -- see the module docstring), membership
    and dedupe in Python."""
    docs = {uri for (uri,) in con.execute("SELECT uri FROM documents")}
    ids = {}
    pairs = []
    seen = set()
    for a, b in con.execute("SELECT from_uri, to_root FROM links"):
        if a == b or b not in docs:
            continue
        ia = ids.setdefault(a, len(ids))
        ib = ids.setdefault(b, len(ids))
        key = ia << 32 | ib
        if key in seen:
            continue
        seen.add(key)
        pairs.append((ia, ib))
    uris = [None] * len(ids)
    for uri, i in ids.items():
        uris[i] = uri
    group = array("b", bytes(len(ids)))
    for uri, source, kind in con.execute(
            "SELECT uri, source, kind FROM documents"):
        i = ids.get(uri)
        if i is not None:
            group[i] = GROUP_ID[facets.flow_group(source, kind)]
    return Graph(uris, ids, _csr(len(ids), pairs),
                 _csr(len(ids), [(b, a) for a, b in pairs]), group)


# --------------------------------------------------------------------------
# the sidecar: the finished arrays on disk, written at relate, loaded at serve
# --------------------------------------------------------------------------

def sidecar_path(catalog_path):
    return Path(catalog_path).with_name(SIDECAR)


def write_sidecar(catalog_path):
    """Build the graph from the catalog and store it beside it. Called at the
    end of every relate that changed anything, so the two move together."""
    con = sqlite3.connect("file:%s?mode=ro" % catalog_path, uri=True)
    try:
        g = build(con)
    finally:
        con.close()
    out = sidecar_path(catalog_path)
    tmp = out.with_suffix(".tmp")
    names = "\x00".join(facets.FLOW_GROUP_NAMES).encode()
    with open(tmp, "wb") as f:
        f.write(_MAGIC)
        f.write(b"%d %d %d\n" % (len(g.uris), len(g.fwd_dst), len(names)))
        f.write(names + b"\n")
        f.write("\n".join(g.uris).encode() + b"\n")
        for arr in (g.fwd_off, g.fwd_dst, g.rev_off, g.rev_dst, g.group):
            arr.tofile(f)
    tmp.replace(out)                     # atomic: readers see old or new
    return len(g.uris), len(g.fwd_dst)


def load_sidecar(catalog_path):
    """The Graph from the sidecar, or None when it is absent, older than the
    catalog, or written under another format/group vocabulary -- the caller
    then rebuilds from the catalog (and relate rewrites the sidecar on its
    next run)."""
    path = sidecar_path(catalog_path)
    try:
        if path.stat().st_mtime_ns < Path(catalog_path).stat().st_mtime_ns:
            return None
    except FileNotFoundError:
        return None
    with open(path, "rb") as f:
        if f.readline() != _MAGIC:
            return None
        n, m, nameslen = map(int, f.readline().split())
        names = f.read(nameslen).decode().split("\x00")
        if tuple(names) != facets.FLOW_GROUP_NAMES:
            return None                  # the vocabulary moved under it
        f.readline()                     # the newline closing the names block
        uris = [f.readline().rstrip(b"\n").decode() for _ in range(n)]
        fwd_off = array("i"); fwd_off.fromfile(f, n + 1)
        fwd_dst = array("i"); fwd_dst.fromfile(f, m)
        rev_off = array("i"); rev_off.fromfile(f, n + 1)
        rev_dst = array("i"); rev_dst.fromfile(f, m)
        group = array("b"); group.fromfile(f, n)
    ids = {uri: i for i, uri in enumerate(uris)}
    return Graph(uris, ids, (fwd_off, fwd_dst), (rev_off, rev_dst), group)


def load(catalog_path):
    """The graph for a catalog: the sidecar when it is current, else a build
    from the catalog itself."""
    g = load_sidecar(catalog_path)
    if g is not None:
        return g
    con = sqlite3.connect("file:%s?mode=ro" % catalog_path, uri=True)
    try:
        return build(con)
    finally:
        con.close()


def degree_in(g, i):
    """How many documents cite node `i` -- the CSR's own authority signal,
    read without touching the catalog."""
    return g.rev_off[i + 1] - g.rev_off[i]


def expand(g, frontier_uris, exclude, *, reverse, budget, allowed=None,
           grouplimit=None, prefer_ties=False):
    """One more ring of a neighbourhood: the documents adjacent to
    `frontier_uris` (their citation targets, or their citers when `reverse`),
    ranked and cut to `budget`. `exclude` is everything already on the stage.
    `allowed` filters on flow-group ids, `grouplimit` caps one group's share
    of the ring, and the ranking is the caller's sort: each node's own
    citedness (`degree_in`), or with `prefer_ties` how many frontier
    documents it connects to. Returns [(uri, ties, degree), ...]."""
    off, dst = (g.rev_off, g.rev_dst) if reverse else (g.fwd_off, g.fwd_dst)
    ties = {}
    for uri in frontier_uris:
        u = g.ids.get(uri)
        if u is None:
            continue
        for i in range(off[u], off[u + 1]):
            v = dst[i]
            if g.uris[v] in exclude:
                continue
            if allowed is not None and g.group[v] not in allowed:
                continue
            ties[v] = ties.get(v, 0) + 1
    ranked = sorted(
        ties.items(),
        key=(lambda kv: (-kv[1], -degree_in(g, kv[0]))) if prefer_ties
        else (lambda kv: (-degree_in(g, kv[0]), -kv[1])))
    ring = []
    per_group = {}
    for v, t in ranked:
        gid = g.group[v]
        if grouplimit is not None and per_group.get(gid, 0) >= grouplimit:
            continue
        per_group[gid] = per_group.get(gid, 0) + 1
        ring.append((g.uris[v], t, degree_in(g, v)))
        if len(ring) == budget:
            break
    return ring


# --------------------------------------------------------------------------
# the walk
# --------------------------------------------------------------------------

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
        else {GROUP_ID[name] for name in groups}
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
