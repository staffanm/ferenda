"""lib/pathgraph: the citation path graph, its sidecar, and the BFS walk."""

import os

from ferenda.lib import catalog, pathgraph


def _catalog(tmp_path):
    """a -> b -> c, with a stray link to a uri the catalog does not hold
    (must fall out of the graph) and a duplicate a -> b row (must dedupe)."""
    path = tmp_path / "catalog.sqlite"
    con = catalog.connect(path)
    docs = {"https://lagen.nu/a": ("sfs", "lag"),
            "https://lagen.nu/b": ("dv", "verdict"),
            "https://lagen.nu/c": ("sfs", "lag")}
    for uri, (source, kind) in docs.items():
        con.execute("INSERT INTO documents (uri, source, kind, label, title, "
                    "path) VALUES (?, ?, ?, 'L', 'T', '')", (uri, source, kind))
    links = [("https://lagen.nu/a", "https://lagen.nu/b"),
             ("https://lagen.nu/a", "https://lagen.nu/b"),
             ("https://lagen.nu/b", "https://lagen.nu/c"),
             ("https://lagen.nu/a", "https://lagen.nu/elsewhere")]
    for f, t in links:
        con.execute("INSERT INTO links (from_uri, predicate, to_uri, to_root) "
                    "VALUES (?, 'dcterms:references', ?, ?)", (f, t, t))
    con.commit()
    con.close()
    return path


def test_build_walk_and_directions(tmp_path):
    cat = _catalog(tmp_path)
    g = pathgraph.load(cat)          # no sidecar yet: built from the catalog
    a, b, c = ("https://lagen.nu/%s" % x for x in "abc")
    assert len(g.fwd_dst) == 2       # deduped, and the unheld target dropped
    assert pathgraph.shortest(g, a, c, direction="out") == [a, b, c]
    assert pathgraph.shortest(g, c, a, direction="out") is None
    assert pathgraph.shortest(g, c, a, direction="in") == [c, b, a]
    assert pathgraph.shortest(g, c, a, direction="both") == [c, b, a]
    # the group filter gates intermediates only: b is a rättsfall
    assert pathgraph.shortest(g, a, c, groups={"Författningar"}) is None
    assert pathgraph.shortest(g, a, c, groups={"Rättsfall"}) == [a, b, c]


def test_induced_edges_carry_the_citation_count(tmp_path):
    """The edge list the deep neighbourhood view draws, answered off the arrays
    the rings were expanded from. It used to be a SQL query asking `from_uri IN
    (…) AND to_root IN (…)`, which SQLite plans as one two-column seek per pair
    of the two lists -- 458 returned documents meant 209 764 b-tree descents
    into a 15.9M-entry index, and on prod's ~80-IOPS disk that timed out."""
    cat = _catalog(tmp_path)
    g = pathgraph.load(cat)
    a, b, c = ("https://lagen.nu/%s" % x for x in "abc")
    # a cites b twice (the duplicate row the graph dedupes into one edge), b
    # cites c once -- the weight is the citations behind the edge
    assert pathgraph.induced_edges(g, {a, b, c}) == [(a, b, 2), (b, c, 1)]
    # only edges with BOTH ends in the set, and a uri the graph does not hold
    # is simply not there
    assert pathgraph.induced_edges(g, {a, c}) == []
    assert pathgraph.induced_edges(g, {b, c, "https://lagen.nu/nowhere"}) \
        == [(b, c, 1)]
    assert pathgraph.induced_edges(g, set()) == []


def test_inbound_counts_answer_what_the_catalog_aggregate_did(tmp_path):
    """The inbound side of /api/v1/graph, off the arrays. The SQL it replaces
    walked every link row pointing at the document however few the reply
    carried -- miljöbalken's 220,617 of them were 19.6 MB of idx_links_to_root
    cold, and 37 s of a 37.8 s reply on prod."""
    cat = _catalog(tmp_path)
    con = catalog.connect(cat)
    # one more citer of b, so the ordering has something to order
    con.execute("INSERT INTO links (from_uri, predicate, to_uri, to_root) "
                "VALUES ('https://lagen.nu/c', 'dcterms:references', "
                "'https://lagen.nu/b', 'https://lagen.nu/b')")
    con.commit()
    g = pathgraph.build(con)
    a, b, c = ("https://lagen.nu/%s" % x for x in "abc")
    # a cites b twice (deduped to one edge of weight 2), c once
    assert pathgraph.inbound_counts(g, b) == [(a, 2), (c, 1)]
    assert pathgraph.inbound_counts(g, c) == [(b, 1)]
    assert pathgraph.inbound_counts(g, a) == []
    for uri in (a, b, c):
        assert pathgraph.inbound_counts(g, uri) \
            == catalog.graph_inbound_counts(con, uri)
    # equally-cited citers come out in uri order
    for citer in ("https://lagen.nu/z", a):
        con.execute("INSERT INTO links (from_uri, predicate, to_uri, to_root) "
                    "VALUES (?, 'dcterms:references', "
                    "'https://lagen.nu/c', 'https://lagen.nu/c')", (citer,))
    con.commit()
    assert pathgraph.inbound_counts(pathgraph.build(con), c) \
        == [(a, 1), (b, 1), ("https://lagen.nu/z", 1)]
    con.close()


def test_sidecar_roundtrip_and_staleness(tmp_path):
    cat = _catalog(tmp_path)
    n, m = pathgraph.write_sidecar(cat)
    assert (n, m) == (3, 2)
    g = pathgraph.load_sidecar(cat)
    a, c = "https://lagen.nu/a", "https://lagen.nu/c"
    assert pathgraph.shortest(g, a, c) == [a, "https://lagen.nu/b", c]
    # the weights ride the sidecar too, or a reloaded graph would draw every
    # edge as a single citation
    assert pathgraph.induced_edges(g, {a, "https://lagen.nu/b"}) \
        == [(a, "https://lagen.nu/b", 2)]
    # a catalog written after the sidecar makes the sidecar stale: refused,
    # so `load` falls back to building from the catalog itself
    side = pathgraph.sidecar_path(cat)
    os.utime(cat, ns=(cat.stat().st_atime_ns, side.stat().st_mtime_ns + 1))
    assert pathgraph.load_sidecar(cat) is None
    assert pathgraph.load(cat) is not None


def test_inbound_count_is_stamped_at_relate_and_read_first(tmp_path):
    """`document_inbound_count` answers from the column relate stamps; a
    catalog no relate has stamped yet still counts live."""
    cat = _catalog(tmp_path)
    con = catalog.connect(cat)
    b = "https://lagen.nu/b"
    live = catalog.document_inbound_count(con, b)   # NULL column: counts live
    # a cites b twice from the same (no) anchor: one (citer, pinpoint) entry
    assert live == 1
    assert catalog.stamp_inbound_counts(con) >= 1
    assert con.execute("SELECT inbound_count FROM documents WHERE uri=?",
                       (b,)).fetchone()[0] == live
    assert catalog.document_inbound_count(con, b) == live
    # the stamped number is a relate-time snapshot: a link added without a
    # restamp does not move it (the next relate does)
    con.execute("INSERT INTO links (from_uri, predicate, to_uri, to_root) "
                "VALUES ('https://lagen.nu/c', 'dcterms:references', ?, ?)",
                (b, b))
    assert catalog.document_inbound_count(con, b) == live
    con.close()


def test_longest_shortest_measures_the_induced_subgraph(tmp_path):
    """The diameter measure walks only the population it is given. Dropping a
    node does not just shorten the answer -- it removes the chain that ran
    through it, which is why the base-act filter in stats.compute changes 71
    hops into 23 rather than trimming a few."""
    cat = _catalog(tmp_path)
    g = pathgraph.load(cat)
    a, b, c = ("https://lagen.nu/%s" % x for x in "abc")
    # the whole chain, not just its ends: the reader checks a "2 steg" row by
    # reading what stands between them
    assert pathgraph.longest_shortest(g, [a, b, c]) == [[a, b, c], [b, c]]
    # b carries the whole chain: without it a reaches nothing
    assert pathgraph.longest_shortest(g, [a, c]) == []
    # k bounds the answer, longest first
    assert pathgraph.longest_shortest(g, [a, b, c], k=1) == [[a, b, c]]
    # a uri the graph does not hold is not in the population
    assert pathgraph.longest_shortest(g, [a, b, c, "https://lagen.nu/nowhere"]) \
        == [[a, b, c], [b, c]]
