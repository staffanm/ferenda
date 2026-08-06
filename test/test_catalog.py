"""Catalog schema invariants that are about *cost*, not about results.

A query that reads the right rows the wrong way is still correct, so nothing
else in the suite notices it -- and on dev's NVMe nothing notices it at runtime
either. Prod's disk does ~100 random IOPS, where the inbound-citation count took
190 s cold for as long as its index was not covering.
"""

import sqlite3

import pytest

from accommodanda.lib import catalog


def _plan_of_last_query(con, call):
    """The query plan of whatever SQL `call` runs, captured from the connection
    rather than restated here. A test that keeps its own copy of the statement
    locks in the copy: adding a column to the real `SELECT` un-covers the index
    and leaves the copy -- and the suite -- green."""
    seen = []
    con.set_trace_callback(seen.append)
    call()
    con.set_trace_callback(None)
    assert seen, "no SQL ran"
    # the traced SQL has its parameters already inlined, so it EXPLAINs with no
    # bindings; the plan of the *first* statement is the one under test
    return " ".join(row[3] for row in
                    con.execute("EXPLAIN QUERY PLAN " + seen[0]))


def _link(con, from_uri="https://lagen.nu/a", anchor="P1"):
    con.execute("INSERT INTO links (from_uri, from_anchor, predicate, to_uri, "
                "to_root) VALUES (?, ?, 'dcterms:references', "
                "'https://lagen.nu/b#P2', 'https://lagen.nu/b')",
                (from_uri, anchor))


def test_inbound_count_is_answered_from_the_index_alone(tmp_path):
    """`document_inbound_count` groups by (to_root, from_uri, from_anchor).
    Reading the latter two out of the links *table* means one random row lookup
    per link -- 228 297 of them for Rättegångsbalken, landing on ~48 500
    scattered pages. The index has to carry all three."""
    con = catalog.connect(tmp_path / "catalog.sqlite")
    _link(con)
    plan = _plan_of_last_query(
        con, lambda: catalog.document_inbound_count(con, "https://lagen.nu/b"))
    assert "COVERING INDEX idx_links_to_root" in plan, plan
    con.close()


def test_a_narrow_to_root_index_is_widened(tmp_path):
    """`CREATE INDEX IF NOT EXISTS` keeps whatever definition a name already
    has, so a catalog built before the index was widened would silently keep the
    narrow one -- the exact shape that was slow in production."""
    path = tmp_path / "catalog.sqlite"
    con = sqlite3.connect(path)
    con.executescript(catalog.SCHEMA)
    con.execute("CREATE INDEX idx_links_to_root ON links(to_root)")
    _link(con)
    con.commit()
    con.close()

    con = catalog.connect(path)
    assert catalog.widen_to_root_index(con) is True
    plan = _plan_of_last_query(
        con, lambda: catalog.document_inbound_count(con, "https://lagen.nu/b"))
    assert "COVERING INDEX idx_links_to_root" in plan, plan
    assert catalog.document_inbound_count(con, "https://lagen.nu/b") == 1
    # idempotent: a second relate must not pay the rebuild again
    assert catalog.widen_to_root_index(con) is False
    con.close()


def test_a_half_widened_index_is_not_mistaken_for_the_real_one(tmp_path):
    """The miss is detected by asking for the indexed columns, not by matching
    the recorded CREATE: `links(to_root, from_anchor)` mentions the right names
    and still is not covering."""
    path = tmp_path / "catalog.sqlite"
    con = sqlite3.connect(path)
    con.executescript(catalog.SCHEMA)
    con.execute("CREATE INDEX idx_links_to_root ON links(to_root, from_anchor)")
    con.commit()
    con.close()

    con = catalog.connect(path)
    assert catalog.widen_to_root_index(con) is True
    con.close()


@pytest.mark.parametrize("existing", ["links(to_root)", None])
def test_serving_never_builds_the_index_over_a_populated_table(tmp_path, existing):
    """`connect_ro`'s one-time migration runs inside the first serving request,
    with concurrent requests queued behind it. Re-sorting every link row there
    would trade a slow first read for a stalled one.

    Both starting states have to hold: a catalog built before the widening (the
    narrow index), and one with no `to_root` index at all -- which is what a
    relate killed mid-rebuild would leave behind, and the state where an
    unguarded `CREATE INDEX IF NOT EXISTS` in `connect` would do the full build.
    """
    path = tmp_path / "catalog.sqlite"
    con = sqlite3.connect(path)
    con.executescript(catalog.SCHEMA)
    if existing:
        con.execute("CREATE INDEX idx_links_to_root ON %s" % existing)
    _link(con)
    con.commit()
    con.close()

    catalog.connect_ro(path).close()
    con = sqlite3.connect(path)
    assert tuple(row[2] for row in con.execute(
        "PRAGMA index_info(idx_links_to_root)")) == (
            ("to_root",) if existing else ())
    con.close()


def test_a_failed_widening_leaves_the_old_index_in_place(tmp_path, monkeypatch):
    """The drop and the rebuild are one transaction. Bare DDL commits as it goes,
    so a `DROP` that landed before its `CREATE` died would leave a populated
    catalog with no index at all -- slower than the narrow one it replaced, and
    looking like a plan regression rather than a crash."""
    path = tmp_path / "catalog.sqlite"
    con = sqlite3.connect(path)
    con.executescript(catalog.SCHEMA)
    con.execute("CREATE INDEX idx_links_to_root ON links(to_root)")
    _link(con)
    con.commit()
    con.close()

    con = catalog.connect(path)
    monkeypatch.setattr(catalog, "_CREATE_TO_ROOT",
                        "CREATE INDEX idx_links_to_root ON links(no_such_column)")
    with pytest.raises(sqlite3.OperationalError):
        catalog.widen_to_root_index(con)
    con.close()          # the uncommitted DROP goes with it, as on a dead relate

    con = sqlite3.connect(path)
    assert tuple(row[2] for row in con.execute(
        "PRAGMA index_info(idx_links_to_root)")) == ("to_root",)
    con.close()
