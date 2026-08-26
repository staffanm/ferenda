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
# bumped when the file layout changes: an older sidecar is refused
# (load_sidecar returns None) and the graph is rebuilt from the catalog,
# which the next relate then writes back in the new layout.
_MAGIC = b"lagen-pathgraph-2\n"


class Graph:
    """CSR adjacency over document uris: `fwd` follows citations (citing ->
    cited), `rev` the other way. `group[i]` is the node's flow-group id."""

    __slots__ = ("uris", "ids", "fwd_off", "fwd_dst", "rev_off", "rev_dst",
                 "group", "weight")

    def __init__(self, uris, ids, fwd, rev, group, weight):
        self.uris, self.ids = uris, ids
        # citations per forward edge, parallel to `fwd_dst`: the weight the
        # graph explorer draws an edge with. Kept here rather than counted per
        # request -- see `induced_edges`.
        self.weight = weight
        self.fwd_off, self.fwd_dst = fwd
        self.rev_off, self.rev_dst = rev
        self.group = group


def _csr(n, pairs, weights=None):
    """(src, dst) int pairs -> (offsets, destinations) arrays. With `weights`
    (one per pair) the same permutation is applied to them, so `w[i]` belongs
    to `dst[i]`; the returned weights are then a third element."""
    deg = array("i", bytes(4 * (n + 1)))
    for s, _d in pairs:
        deg[s + 1] += 1
    off = array("i", bytes(4 * (n + 1)))
    for i in range(n):
        off[i + 1] = off[i] + deg[i + 1]
    dst = array("i", bytes(4 * len(pairs)))
    fill = array("i", off)
    if weights is None:
        for s, d in pairs:
            dst[fill[s]] = d
            fill[s] += 1
        return off, dst
    w = array("i", bytes(4 * len(pairs)))
    for j, (s, d) in enumerate(pairs):
        dst[fill[s]] = d
        w[fill[s]] = weights[j]
        fill[s] += 1
    return off, dst, w


def build(con):
    """The whole graph off one connection: one sequential scan of `links`
    (never per-document index probes -- see the module docstring), membership
    and dedupe in Python."""
    docs = {uri for (uri,) in con.execute("SELECT uri FROM documents")}
    ids = {}
    pairs = []
    counts = []
    at = {}                              # packed pair -> its slot in `pairs`
    for a, b in con.execute("SELECT from_uri, to_root FROM links"):
        if a == b or b not in docs:
            continue
        ia = ids.setdefault(a, len(ids))
        ib = ids.setdefault(b, len(ids))
        key = ia << 32 | ib
        slot = at.get(key)
        if slot is None:
            at[key] = len(pairs)
            pairs.append((ia, ib))
            counts.append(1)
        else:
            counts[slot] += 1
    uris = [None] * len(ids)
    for uri, i in ids.items():
        uris[i] = uri
    group = array("b", bytes(len(ids)))
    for uri, source, kind in con.execute(
            "SELECT uri, source, kind FROM documents"):
        i = ids.get(uri)
        if i is not None:
            group[i] = GROUP_ID[facets.flow_group(source, kind)]
    fwd_off, fwd_dst, weight = _csr(len(ids), pairs, counts)
    return Graph(uris, ids, (fwd_off, fwd_dst),
                 _csr(len(ids), [(b, a) for a, b in pairs]), group, weight)


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
        for arr in (g.fwd_off, g.fwd_dst, g.rev_off, g.rev_dst, g.group,
                    g.weight):
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
        weight = array("i"); weight.fromfile(f, m)
    ids = {uri: i for i, uri in enumerate(uris)}
    return Graph(uris, ids, (fwd_off, fwd_dst), (rev_off, rev_dst), group,
                 weight)


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


def induced_edges(g, uris):
    """Every citation among `uris` as `(citing, cited, citations)` -- the edge
    list the deep neighbourhood view draws, so citers citing each other show as
    structure and not just spokes.

    Answered from the arrays, not from the catalog. The SQL this replaces asked
    `from_uri IN (…) AND to_root IN (…)` over 16M link rows, which SQLite plans
    as a two-column seek per *pair* of the two lists: 458 returned documents
    meant 209 764 b-tree descents into a 15.9M-entry index. Warm on dev that is
    58 ms and invisible; on prod, where the catalog sits on an ~80-IOPS disk and
    a cold descent is a physical read, it is the whole cost of a depth=2 request
    on a well-cited node (32014L0024 timed out). Here it is `len(uris)` walks of
    an in-memory adjacency run -- the same arrays the rings were expanded from,
    which the endpoint has already loaded.

    A uri the graph does not hold contributes no edges: hop 1 is read live from
    the catalog while these arrays are relate's snapshot, so a document
    catalogued since the last relate is a node with no edges rather than an
    error."""
    want = {g.ids[u] for u in uris if u in g.ids}
    out = []
    for u in want:
        for i in range(g.fwd_off[u], g.fwd_off[u + 1]):
            v = g.fwd_dst[i]
            if v in want:
                out.append((g.uris[u], g.uris[v], g.weight[i]))
    # by uri pair, not by the internal ids -- those are assigned in link-scan
    # order and move on every relate, and the payload should not
    out.sort()
    return out


# --------------------------------------------------------------------------
# the walk
# --------------------------------------------------------------------------

def _sides(g, direction):
    """The CSR arrays a step may follow: "out" citations, "in" citers, "both"
    either (the graph walked undirected)."""
    sides = []
    if direction in ("out", "both"):
        sides.append((g.fwd_off, g.fwd_dst))
    if direction in ("in", "both"):
        sides.append((g.rev_off, g.rev_dst))
    return sides


def _bfs(g, s, t, sides, allowed, banned_nodes=frozenset(),
         banned_hops=frozenset()):
    """One shortest chain of node ids from `s` to `t`, or None. `allowed`
    gates the *intermediate* nodes by flow-group id (the endpoints always
    pass). `banned_nodes` and `banned_hops` (ordered `(u, v)` pairs) are what
    `k_shortest` closes off to force the walk onto a different route -- a hop
    is the ordered pair of documents, whichever CSR side carries it, since
    that is what makes two chains different to a reader."""
    if s == t:
        return [s]
    prev = {s: -1}
    q = deque([s])
    while q:
        u = q.popleft()
        for off, dst in sides:
            for i in range(off[u], off[u + 1]):
                v = dst[i]
                if v in prev or v in banned_nodes or (u, v) in banned_hops:
                    continue
                if v == t:
                    path = [t, u]
                    while path[-1] != s:
                        path.append(prev[path[-1]])
                    path.reverse()
                    return path
                if allowed is not None and g.group[v] not in allowed:
                    continue
                prev[v] = u
                q.append(v)
    return None


def k_shortest(g, from_uri, to_uri, *, direction="both", groups=None, k=1):
    """Up to `k` loopless chains from `from_uri` to `to_uri`, shortest first,
    endpoints included -- Yen's algorithm over `_bfs`. `[]` when no chain
    exists. Each chain is a list of uris and no two are the same.

    Yen's costs one BFS per node of each chain already found, so `k` is the
    caller's budget, not a free parameter: /api/v1/path caps it. `k=1` is the
    single BFS `shortest` has always run."""
    s, t = g.ids.get(from_uri), g.ids.get(to_uri)
    if s is None or t is None:
        return []
    allowed = None if groups is None else {GROUP_ID[name] for name in groups}
    sides = _sides(g, direction)
    first = _bfs(g, s, t, sides, allowed)
    if first is None:
        return []
    found = [first]
    candidates = []                     # (length, chain), Yen's B set
    while len(found) < k:
        prev_chain = found[-1]
        for i in range(len(prev_chain) - 1):
            root, spur = prev_chain[:i + 1], prev_chain[i]
            # every chain already found that starts the same way gives up its
            # next hop, so the spur walk cannot re-find it; the root's own
            # nodes go too, which is what keeps the chain loopless
            hops = {(c[i], c[i + 1]) for c in found
                    if len(c) > i + 1 and c[:i + 1] == root}
            rest = _bfs(g, spur, t, sides, allowed,
                        banned_nodes=frozenset(root[:-1]), banned_hops=hops)
            if rest is None:
                continue
            chain = root[:-1] + rest
            if chain not in found and chain not in [c for _n, c in candidates]:
                candidates.append((len(chain), chain))
        if not candidates:
            break
        # shortest first; the chain itself breaks ties, so the answer does not
        # depend on the order the spurs happened to be tried
        candidates.sort()
        found.append(candidates.pop(0)[1])
    return [[g.uris[i] for i in chain] for chain in found]


def shortest(g, from_uri, to_uri, *, direction="both", groups=None):
    """The uris of one shortest chain from `from_uri` to `to_uri`, endpoints
    included -- or None when no chain exists. `direction` says which links a
    step may follow: "out" follows citations, "in" follows citers, "both"
    walks the graph undirected. `groups` (a set of flow-group names) filters
    the *intermediate* documents; the endpoints are always allowed."""
    chains = k_shortest(g, from_uri, to_uri, direction=direction, groups=groups)
    return chains[0] if chains else None
