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


def test_sidecar_roundtrip_and_staleness(tmp_path):
    cat = _catalog(tmp_path)
    n, m = pathgraph.write_sidecar(cat)
    assert (n, m) == (3, 2)
    g = pathgraph.load_sidecar(cat)
    a, c = "https://lagen.nu/a", "https://lagen.nu/c"
    assert pathgraph.shortest(g, a, c) == [a, "https://lagen.nu/b", c]
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
