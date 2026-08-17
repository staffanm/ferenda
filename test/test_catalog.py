"""Catalog schema invariants that are about *cost*, not about results.

A query that reads the right rows the wrong way is still correct, so nothing
else in the suite notices it -- and on dev's NVMe nothing notices it at runtime
either. Prod's disk does ~100 random IOPS, where the inbound-citation count took
190 s cold for as long as its index was not covering.

The corpus-wide anchor audit (`dangling_anchors`) sits here too: it is the
same shape of defect -- a query whose result nothing else in the suite can
see, because a link with a dead fragment counts as a link everywhere else.
"""

import json
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


def test_a_narrow_docs_source_index_is_widened(tmp_path):
    """`idx_docs_source` must cover (source, art_size). Narrow, the ops
    dashboard's per-source totals read `art_size` out of the 173.8 MB
    `documents` table; covering, the same query is a 5.5 MB index scan -- which
    is what the page costs on a cold page cache. Every catalog built before the
    widening carries the narrow one, and `IF NOT EXISTS` keeps it."""
    path = tmp_path / "catalog.sqlite"
    con = catalog.connect(path)          # fresh catalogs get it wide already
    assert catalog.widen_docs_source_index(con) is False
    con.execute("DROP INDEX idx_docs_source")
    con.execute("CREATE INDEX idx_docs_source ON documents(source)")
    assert catalog.widen_docs_source_index(con) is True
    plan = _plan_of_last_query(con, lambda: catalog.source_stats(con))
    assert "COVERING INDEX idx_docs_source" in plan, plan
    # source stays the leading column, so a plain lookup keeps its index
    lookup = _plan_of_last_query(
        con, lambda: con.execute("SELECT uri FROM documents WHERE source = 'sfs'")
        .fetchall())
    assert "idx_docs_source" in lookup, lookup
    assert catalog.widen_docs_source_index(con) is False   # idempotent
    con.close()


def test_connect_leaves_no_transaction_open(tmp_path):
    """`connect` must hand back a connection with nothing in flight.

    `_record_data_root` runs DML, and sqlite3's legacy isolation_level opens an
    implicit transaction before DML -- so `connect` used to return mid-write, and
    the next explicit BEGIN died with "cannot start a transaction within a
    transaction". That is what `rebuild` does via `widen_docs_source_index`, so
    `lagen all relate` crashed on every catalog old enough to need the widening.

    The `data_root=` argument is the whole point of this test: without it
    `_record_data_root` returns before touching the database, which is why the
    widening tests above never caught this. `rebuild` always passes it."""
    root = tmp_path / "corpus"
    root.mkdir()
    path = tmp_path / "catalog.sqlite"

    con = catalog.connect(path, data_root=root)
    assert con.in_transaction is False
    # an older catalog carries both indexes narrow; the widenings BEGIN, so they
    # are what a dangling transaction breaks
    con.execute("DROP INDEX idx_docs_source")
    con.execute("CREATE INDEX idx_docs_source ON documents(source)")
    con.execute("DROP INDEX IF EXISTS idx_links_to_root")
    con.execute("CREATE INDEX idx_links_to_root ON links(to_root)")
    con.commit()
    con.close()

    con = catalog.connect(path, data_root=root)
    assert con.in_transaction is False
    assert catalog.widen_to_root_index(con) is True
    assert catalog.widen_docs_source_index(con) is True
    assert (tuple(r[2] for r in con.execute("PRAGMA index_info(idx_docs_source)"))
            == catalog.INDEX_DOCS_SOURCE_COLUMNS)
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


# --------------------------------------------------------------------------
# the corpus-wide anchor audit
# --------------------------------------------------------------------------

def _corpus(tmp_path, target_ids, cited_anchor):
    """A two-document corpus: one artifact holding `target_ids`, one citing it
    at `cited_anchor`. `rebuild` records the artifacts' paths, so the audit
    reads the real files back the way it does on the corpus."""
    art = tmp_path / "artifact"
    art.mkdir()
    target = art / "target.json"
    target.write_text(json.dumps({
        "uri": "https://lagen.nu/ext/icrc/195",
        "metadata": {"properties": {"dcterms:title": "Hague Convention (IV)"}},
        "structure": [{"type": "artikel", "id": nid, "text": ["Text."]}
                      for nid in target_ids]}))
    citing = art / "citing.json"
    citing.write_text(json.dumps({
        "uri": "https://lagen.nu/ext/icc/0001",
        "metadata": {"properties": {"dcterms:title": "A decision"}},
        "structure": [{"type": "stycke", "id": "S1", "text": [
            "See ", {"uri": "https://lagen.nu/ext/icrc/195#" + cited_anchor,
                     "text": "article 42"}, "."]}]}))
    path = tmp_path / "catalog.sqlite"
    catalog.rebuild(path, "icrc", [target, citing])
    return catalog.connect(path)


def test_a_source_outside_the_audit_is_not_read_at_all(tmp_path):
    """The audit is only answerable for a source whose page offers exactly the
    anchors its artifact carries. sfs mints a change-act anchor per amendment,
    eurlex a stycke alias, forarbete a page marker, and `Toc` a generated
    anchor for any heading with no id -- none of them a `structure` node. Asked
    of every source the audit calls 1 612 832 live links broken, so the caller
    names what it can answer for."""
    con = _corpus(tmp_path, ["Annex42"], "A42")
    assert catalog.dangling_anchors(con, ("sfs", "eurlex")) == []
    assert catalog.dangling_anchors(con, ("icrc",))
    con.close()


def test_an_anchor_the_target_does_not_hold_is_reported(tmp_path):
    """The failure a link count cannot show: the link exists, the target
    exists, and the anchor goes nowhere. 126 treaty references pointed at an
    `#A42` on a convention that anchors its Regulations' articles under
    `#Annex42`, and every count involved looked healthy."""
    con = _corpus(tmp_path, ["Annex42"], "A42")
    assert catalog.dangling_anchors(con, ("icrc",)) == [
        ("https://lagen.nu/ext/icc/0001", "https://lagen.nu/ext/icrc/195#A42", 1)]
    con.close()


def test_an_anchor_the_target_holds_is_not_reported(tmp_path):
    con = _corpus(tmp_path, ["Annex42"], "Annex42")
    assert catalog.dangling_anchors(con, ("icrc",)) == []
    con.close()


def test_anchor_glob_covers_both_fragment_grammars(tmp_path):
    """`graph_anchor_*` must count a unit's subdivisions in both grammars the
    corpus writes -- letter-opened (K4P7S2) and the EU acts' dot-joined
    (6.1.c) -- and must not leak a sibling whose id merely extends the
    digits (K4P70, 6.10). The dot grammar held 299 060 point/stycke-level
    link targets when the miss was found."""
    con = catalog.connect(tmp_path / "catalog.sqlite")
    law, eu = "https://lagen.nu/x", "https://lagen.nu/ext/celex/32016R0679"
    citer = "https://lagen.nu/citer"
    for uri in (law, eu, citer):
        con.execute("INSERT INTO documents (uri, source, kind, label, title, "
                    "path) VALUES (?, 'sfs', 'law', 'L', 'T', '')", (uri,))
    for target in ("#K4P7", "#K4P7S2", "#K4P70"):
        con.execute("INSERT INTO links (from_uri, from_anchor, predicate, "
                    "to_uri, to_root) VALUES (?, 'P1', 'dcterms:references', "
                    "?, ?)", (citer, law + target, law))
    for target in ("#6.1", "#6.1.c", "#6.10"):
        con.execute("INSERT INTO links (from_uri, from_anchor, predicate, "
                    "to_uri, to_root) VALUES (?, 'P1', 'dcterms:references', "
                    "?, ?)", (citer, eu + target, eu))
    # outbound from a dot-grammar subdivision must count under its unit
    con.execute("INSERT INTO links (from_uri, from_anchor, predicate, to_uri, "
                "to_root) VALUES (?, '6.1.c', 'dcterms:references', ?, ?)",
                (eu, law, law))
    # ...and an unresolved target still counts in the anchor-level totals
    con.execute("INSERT INTO links (from_uri, from_anchor, predicate, to_uri, "
                "to_root) VALUES (?, '6.1', 'dcterms:references', "
                "'https://lagen.nu/ext/celex/39999R9999', "
                "'https://lagen.nu/ext/celex/39999R9999')", (eu,))

    rows = catalog.graph_anchor_inbound(con, law, "K4P7")
    assert [(r[0], r[1]) for r in rows] == [(citer, 2)]     # not the K4P70 row
    rows = catalog.graph_anchor_inbound(con, eu, "6.1")
    assert [(r[0], r[1]) for r in rows] == [(citer, 2)]     # not the 6.10 row
    rows = catalog.graph_anchor_outbound(con, eu, "6.1")
    assert [(r[0], r[1]) for r in rows] == [(law, 1)]
    assert catalog.graph_anchor_out_totals(con, eu, "6.1") == (2, 2)
