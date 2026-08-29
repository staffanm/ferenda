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

from ferenda.lib import catalog


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
    """The live count -- `document_inbound_count`'s fallback on a catalog no
    relate has stamped, and the whole of `inbound_counts_for` -- groups by
    (to_root, from_uri, from_anchor). Reading the latter two out of the links
    *table* means one random row lookup per link -- 228 297 of them for
    Rättegångsbalken, landing on ~48 500 scattered pages. The index has to
    carry all three."""
    con = catalog.connect(tmp_path / "catalog.sqlite")
    _link(con)
    plan = _plan_of_last_query(
        con, lambda: catalog.inbound_counts_for(con, ["https://lagen.nu/b"]))
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
        con, lambda: catalog.inbound_counts_for(con, ["https://lagen.nu/b"]))
    assert "COVERING INDEX idx_links_to_root" in plan, plan
    assert catalog.document_inbound_count(con, "https://lagen.nu/b") == 1
    # idempotent: a second relate must not pay the rebuild again
    assert catalog.widen_to_root_index(con) is False
    con.close()


def test_a_provision_inbound_query_reads_only_that_provisions_rows(tmp_path):
    """`graph_anchor_inbound_counts` used to find the *document* by `to_root`
    and then test `to_uri` per row -- and `to_uri` is not in the to_root index,
    so every link into the document was fetched out of the 2.5 GB table.
    Article 6 ECHR sits under 1,439,778 of them: 143,662 scattered pages to
    answer with 300 rows, 6.5 s cold on dev's NVMe and minutes on prod's disk.
    Seeking `to_uri` reads the provision's own range, and `from_uri` in the
    index keeps the group-by off the table: 3,112 pages, 0.04 s."""
    path = tmp_path / "catalog.sqlite"
    con = catalog.connect(path)
    plan = _plan_of_last_query(
        con, lambda: catalog.graph_anchor_inbound_counts(
            con, "https://lagen.nu/b", "P2"))
    assert "COVERING INDEX idx_links_to_uri" in plan, plan
    assert "to_root" not in plan, plan
    con.close()


def test_a_provision_counts_its_subdivisions_and_no_neighbour(tmp_path):
    """The `to_uri` range is wider than the three matches (it admits "A60"
    where "A6" must not match it), so the GLOBs still decide -- on index rows,
    which costs nothing. And a `to_uri` carrying a second "#" belongs to no
    provision: `set_genomforande` wrote 1,011 of those, which `to_root = ?`
    used to exclude for the 289 whose to_root was itself a fragment and count
    for the other 722."""
    path = tmp_path / "catalog.sqlite"
    con = catalog.connect(path)
    root = "https://lagen.nu/coe/005"
    for frag in ("A6", "A6P1", "A60", "A6.2"):
        con.execute("INSERT INTO links (from_uri, predicate, to_uri, to_root) "
                    "VALUES ('https://lagen.nu/a', 'dcterms:references', ?, ?)",
                    ("%s#%s" % (root, frag), root))
    # the two shapes of the nested-fragment defect, neither of them article 6
    con.execute("INSERT INTO links (from_uri, predicate, to_uri, to_root) "
                "VALUES ('https://lagen.nu/a', 'rpubl:genomforDirektiv', ?, ?)",
                ("%s#A6.4.a#1" % root, root))
    con.execute("INSERT INTO links (from_uri, predicate, to_uri, to_root) "
                "VALUES ('https://lagen.nu/a', 'rpubl:genomforDirektiv', ?, ?)",
                ("%s#A6.4.a#2" % root, "%s#A6.4.a" % root))
    con.commit()
    # A6 itself, A6P1 and A6.2 -- not A60, and neither nested row
    assert catalog.graph_anchor_inbound_counts(con, root, "A6") \
        == [("https://lagen.nu/a", 3)]
    assert catalog.graph_anchor_inbound_counts(con, root, "A60") \
        == [("https://lagen.nu/a", 1)]
    # a document citing its own provision is not its own neighbour
    con.execute("INSERT INTO links (from_uri, predicate, to_uri, to_root) "
                "VALUES (?, 'dcterms:references', ?, ?)",
                (root, "%s#A6" % root, root))
    con.commit()
    assert catalog.graph_anchor_inbound_counts(con, root, "A6") \
        == [("https://lagen.nu/a", 3)]
    con.close()


def test_a_narrow_to_uri_index_is_widened(tmp_path):
    """The `to_root` widening's sibling, and the same trap: every catalog built
    before it carries `idx_links_to_uri` narrow, and `CREATE INDEX IF NOT
    EXISTS` keeps that definition. Narrow, the provision query reads the same
    rows out of the table -- 232 MB for article 6 ECHR against the covering
    index's 12.7 MB."""
    path = tmp_path / "catalog.sqlite"
    con = sqlite3.connect(path)
    con.executescript(catalog.SCHEMA)
    con.execute("CREATE INDEX idx_links_to_uri ON links(to_uri)")
    _link(con)
    con.commit()
    con.close()

    con = catalog.connect(path)
    assert catalog.widen_to_uri_index(con) is True
    plan = _plan_of_last_query(
        con, lambda: catalog.graph_anchor_inbound_counts(
            con, "https://lagen.nu/b", "P2"))
    assert "COVERING INDEX idx_links_to_uri" in plan, plan
    assert catalog.graph_anchor_inbound_counts(con, "https://lagen.nu/b", "P2") \
        == [("https://lagen.nu/a", 1)]
    # to_uri stays leftmost, so a plain equality lookup keeps its index
    lookup = _plan_of_last_query(
        con, lambda: con.execute("SELECT from_uri FROM links WHERE to_uri = ?",
                                 ("https://lagen.nu/b#P2",)).fetchall())
    assert "idx_links_to_uri" in lookup, lookup
    assert catalog.widen_to_uri_index(con) is False   # idempotent
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

    # ...and `uri` sits second, so the paginated listing walks the index in order
    # instead of sorting the whole source to return the first few. Without it the
    # plan is `USE TEMP B-TREE FOR ORDER BY` and the cost scales with the source,
    # not the page: measured on prod, forarbete (97k rows) took 154 s cold and
    # the caller got nginx's 60 s 504 while the query ran on.
    listing = _plan_of_last_query(
        con, lambda: catalog.documents(con, "sfs", None, 2, 0))
    assert "TEMP B-TREE" not in listing, listing
    assert "idx_docs_source" in listing, listing

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
        "uri": "https://lagen.nu/icrc/195",
        "metadata": {"properties": {"dcterms:title": "Hague Convention (IV)"}},
        "structure": [{"type": "artikel", "id": nid, "text": ["Text."]}
                      for nid in target_ids]}))
    citing = art / "citing.json"
    citing.write_text(json.dumps({
        "uri": "https://lagen.nu/icc/0001",
        "metadata": {"properties": {"dcterms:title": "A decision"}},
        "structure": [{"type": "stycke", "id": "S1", "text": [
            "See ", {"uri": "https://lagen.nu/icrc/195#" + cited_anchor,
                     "text": "article 42"}, "."]}]}))
    path = tmp_path / "catalog.sqlite"
    catalog.rebuild(path, "icrc", [target, citing])
    return catalog.connect(path)


def test_a_definition_is_stored_as_the_sentence_that_states_it():
    """brottsbalken 10 kap. 8 § 1 st runs two sentences and only the first
    defines fyndförseelse. The begrepp page prints what the act says, so the
    unit stored is the sentence carrying the term, not the whole stycke."""
    art = {"uri": "https://lagen.nu/1962:700", "structure": [
        {"type": "paragraf", "id": "K10P8", "children": [
            {"type": "stycke", "id": "K10P8S1", "text": [
                "Fullgör man ej vad i lag är föreskrivet om skyldighet att "
                "tillkännagiva hittegods, dömes för ",
                {"kind": "term", "predicate": "dcterms:subject",
                 "text": "fyndförseelse",
                 "uri": "https://lagen.nu/begrepp/Fyndförseelse"},
                " till böter. Underlåter man att fullgöra sådan skyldighet med "
                "uppsåt att tillägna sig godset, skall gälla vad där är stadgat."]}]}]}
    assert catalog.definition_sentences(art) == [(
        "https://lagen.nu/begrepp/Fyndförseelse", "K10P8S1", "fyndförseelse",
        "Fullgör man ej vad i lag är föreskrivet om skyldighet att tillkännagiva "
        "hittegods, dömes för fyndförseelse till böter.")]


def test_a_swedish_definition_reaches_into_its_list_only_when_it_is_open():
    """The two shapes separate on whether the text closes, not on whether the
    node has children. Uppbördslagen 1 § stops on "om inte annat anges" and its
    body is the list under it; brottsbalken 6 kap. 1 § states våldtäkt in a
    whole sentence and *then* lists the acts it covers -- appending those turned
    a 257-character definition into 1 185 characters of the whole paragraf."""
    def stycke(lead, term, items):
        return {"uri": "https://lagen.nu/1953:272", "structure": [
            {"type": "paragraf", "id": "P1", "children": [
                {"type": "stycke", "id": "P1S1", "text": [
                    lead,
                    {"kind": "term", "predicate": "dcterms:subject", "text": term,
                     "uri": "https://lagen.nu/begrepp/X"},
                    ""],
                 "children": [{"type": "punkt", "text": [i]} for i in items]}]}]}

    open_lead = stycke("Med ", "skatt", ["kommunal inkomstskatt,",
                                         "statlig inkomstskatt."])
    # the lead-in carries no terminator at all -- not even the colon
    open_lead["structure"][0]["children"][0]["text"][2] = \
        " avses i denna lag, om inte annat anges"
    assert catalog.definition_sentences(open_lead)[0][3] == (
        "Med skatt avses i denna lag, om inte annat anges kommunal "
        "inkomstskatt, statlig inkomstskatt.")

    closed = stycke("Den som genomför ett samlag döms för ", "våldtäkt",
                    ["ett vaginalt samlag,", "en annan sexuell handling."])
    closed["structure"][0]["children"][0]["text"][2] = " till fängelse."
    assert catalog.definition_sentences(closed)[0][3] == (
        "Den som genomför ett samlag döms för våldtäkt till fängelse.")


def test_an_eu_definition_point_reaches_past_its_own_colon():
    """A definitions-article point is the definition whole -- except where the
    definition is a sub-list and the point's own text stops at the colon
    (NIS2 art. 6.1). Then the sub-list is what the act says."""
    art = {"uri": "https://lagen.nu/celex/32022L2555", "lang": "swe",
           "structure": [{"type": "article", "num": "6", "children": [
               {"type": "paragraph", "id": "6.9", "defines": "risk",
                "text": ["risk: risk för förlust orsakad av en incident."]},
               {"type": "paragraph", "id": "6.1",
                "defines": "nätverks- och informationssystem",
                "text": ["nätverks- och informationssystem:"],
                "children": [{"type": "point", "num": "a",
                              "text": ["Ett elektroniskt kommunikationsnät."]}]}]}]}
    assert [(anchor, sentence) for _, anchor, _, sentence
            in catalog.definition_sentences(art)] == [
        ("6.9", "risk: risk för förlust orsakad av en incident."),
        ("6.1", "nätverks- och informationssystem: Ett elektroniskt "
                "kommunikationsnät.")]


def test_a_definition_folds_onto_the_canonical_concept(tmp_path):
    """The wiki page *Risken* absorbs the form *Risk*, so links to Risk are
    remapped onto it. The definitions beside those links have to move with them
    -- left behind, Risk holds 31 legaldefinitioner and no page while the page
    holds none. 1 077 rows over 494 concepts were in that state."""
    db = str(tmp_path / "catalog.sqlite")
    law = tmp_path / "law.json"
    law.write_text(json.dumps({
        "uri": "https://lagen.nu/1990:931", "structure": [
            {"type": "paragraf", "id": "P1", "children": [
                {"type": "stycke", "id": "P1S1", "text": [
                    "Köparen bär ",
                    {"kind": "term", "predicate": "dcterms:subject", "text": "risk",
                     "uri": "https://lagen.nu/begrepp/Risk"},
                    " för varan efter avlämnandet."]}]}],
        "metadata": {"properties": {"dcterms:title": "Köplag (1990:931)"}}}),
        encoding="utf-8")
    wiki = tmp_path / "risken.json"
    wiki.write_text(json.dumps({"uri": "https://lagen.nu/begrepp/Risken",
                                "type": "begrepp", "title": "Risken",
                                "body": [{"type": "stycke", "text": ["Om risk."]}]}),
                    encoding="utf-8")
    catalog.rebuild(db, "sfs", [law])
    catalog.rebuild(db, "begrepp", [wiki])
    con = catalog.connect(db)
    catalog.canonicalize_concepts(con)
    assert [r[0] for r in con.execute("SELECT concept FROM definitions")] \
        == ["https://lagen.nu/begrepp/Risken"]
    assert len(catalog.concept_definitions(
        con, "https://lagen.nu/begrepp/Risken")) == 1
    con.close()


def test_an_english_act_states_no_swedish_concept():
    """The begrepp namespace is Swedish, so an English manifestation's terms are
    not concepts here -- the rule `definition_links` already applies."""
    english = {"uri": "https://lagen.nu/celex/32022L2555", "lang": "eng",
               "structure": [{"type": "paragraph", "id": "6.9", "defines": "risk",
                              "text": ["risk: risk of loss caused by an incident."]}]}
    assert catalog.definition_sentences(english) == []


def test_a_definition_with_no_body_is_listed_with_nothing_to_quote():
    """32015R0104 art. 3 f is "total tillåten fångstmängd (TAC): " and stops --
    the source left the body out. The act still defines the term, so the row
    stays with an empty sentence; dropping it hid 863 concepts' occurrences."""
    empty = {"uri": "https://lagen.nu/celex/32015R0104", "lang": "swe",
             "structure": [{"type": "point", "id": "3.f",
                            "defines": "total tillåten fångstmängd (TAC)",
                            "text": ["total tillåten fångstmängd (TAC): "]}]}
    assert catalog.definition_sentences(empty) == [(
        "https://lagen.nu/begrepp/Total_tillåten_fångstmängd_(TAC)", "3.f",
        "total tillåten fångstmängd (TAC)", "")]


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
        ("https://lagen.nu/icc/0001", "https://lagen.nu/icrc/195#A42", 1)]
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
    law, eu = "https://lagen.nu/x", "https://lagen.nu/celex/32016R0679"
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
                "'https://lagen.nu/celex/39999R9999', "
                "'https://lagen.nu/celex/39999R9999')", (eu,))

    rows = catalog.graph_anchor_inbound(con, law, "K4P7")
    assert [(r[0], r[1]) for r in rows] == [(citer, 2)]     # not the K4P70 row
    rows = catalog.graph_anchor_inbound(con, eu, "6.1")
    assert [(r[0], r[1]) for r in rows] == [(citer, 2)]     # not the 6.10 row
    rows = catalog.graph_anchor_outbound(con, eu, "6.1")
    assert [(r[0], r[1]) for r in rows] == [(law, 1)]
    assert catalog.graph_anchor_out_totals(con, eu, "6.1") == (2, 2)


def test_first_prose_skips_headings_and_cuts_at_a_word(tmp_path):
    long = ("Den som med uppsåt eller av oaktsamhet vållar annan person skada "
            "skall ersätta skadan i den omfattning som följer av denna lag, "
            "om inte annat är särskilt föreskrivet i annan författning eller avtal.")
    art = {"structure": [
        {"type": "rubrik", "text": ["Första avdelningen"]},
        {"type": "kapitel", "children": [
            {"type": "rubrik", "text": ["1 kap. Inledande bestämmelser"]},
            {"type": "paragraf", "text": ["Kort."]},
            {"type": "paragraf", "text": [long[:50], {"uri": "x", "text": long[50:]}]},
        ]}]}
    got = catalog.first_prose(art)
    assert got.startswith("Den som med uppsåt")
    assert len(got) <= catalog._SNIPPET_LEN + 2
    # nothing prose-like: no snippet, not a heading masquerading as one
    assert catalog.first_prose({"structure": [
        {"type": "rubrik", "text": ["Bara rubriker här i detta dokument, inga stycken alls någonstans"]}]}) is None
    # a författningssamling masthead and OCR debris off a scanned page are
    # not prose; the digits-heavy lagtext right after them is
    lagtext = ("1 § I denna lag finns bestämmelser om 1. avgifter enligt "
               "8 § andra stycket lagen (2016:960), 2. tillsyn enligt 16 §.")
    assert catalog.first_prose({"structure": [
        {"type": "stycke", "text": ["ISSN 1102-5468 Ansvarig utgivare: Charlotte Havermark, Skogsstyrelsen"]},
        {"type": "stycke", "text": [".-lascs.srii<~nt I J / ,,;: --- ~~ .. ;;~ !! ((\\ // )) [[ ]] ** ++ == ?? .. ,, ;; :: '' <<>>"]},
        {"type": "stycke", "text": [lagtext]}]}) == lagtext


def test_document_snippet_uses_what_each_source_has():
    long = "Skyddet för fysiska personer vid behandling av personuppgifter är en grundläggande rättighet som var och en har."
    # an EU act's preamble formalities are furniture; the first recital wins,
    # led by its own number
    act = {"uri": "https://lagen.nu/celex/32016R0679",
           "structure": [{"type": "preamble", "children": [
        {"type": "citation", "text": ["med beaktande av fördraget om Europeiska unionens funktionssätt, särskilt artiklarna 16 och 114"]},
        {"type": "recital", "num": "1", "text": [long]}]}]}
    assert catalog._document_snippet(act, "eurlex").startswith("(1) Skyddet")
    # an författning opens on its first paragraf, designation included, with
    # every heading before it skipped
    law = {"structure": [
        {"type": "rubrik", "text": ["Första avdelningen"]},
        {"type": "kapitel", "children": [
            {"type": "rubrik", "text": ["1 kap. Inledande bestämmelser"]},
            {"type": "paragraf", "id": "K1P1", "children": [
                {"type": "stycke", "text": ["Fast egendom är jord."]}]}]}]}
    assert catalog._document_snippet(law, "sfs") \
        == "1 kap. 1 § Fast egendom är jord."
    assert catalog._document_snippet(law, "foreskrift") \
        == "1 kap. 1 § Fast egendom är jord."
    # a stycke that introduces a list quotes its first item, ellipsis after
    listy = {"structure": [{"type": "paragraf", "id": "P1", "children": [
        {"type": "stycke",
         "text": ["Denna förordning är meddelad med stöd av"],
         "children": [
             {"type": "punkt", "text": ["8 § andra stycket lagen (2016:960) om arbetstid,"]},
             {"type": "punkt", "text": ["16 § samma lag."]}]}]}]}
    assert catalog._document_snippet(listy, "sfs") \
        == ("1 § Denna förordning är meddelad med stöd av "
            "8 § andra stycket lagen (2016:960) om arbetstid, …")
    # an EU court decision opens on its first numbered ground, 50-word
    # capped -- never the keyword strings or the quoted act's recitals
    # case law is the CELEX's own sector: a 6-leading number (AG opinions
    # included), no doctype needed
    dom = {"uri": "https://lagen.nu/celex/62021CJ0001", "structure": [
        {"type": "keyword", "text": ["Begäran om förhandsavgörande"]},
        {"type": "recital", "num": "1", "text": [long]},
        {"type": "paragraph", "num": "1", "text": [
            "Begäran om förhandsavgörande avser tolkningen av artikel 4.1 "
            "första stycket e i Europaparlamentets och rådets direktiv "
            "2003/4/EG av den 28 januari 2003 om allmänhetens tillgång till "
            "miljöinformation och om upphävande av rådets direktiv "
            "90/313/EEG (EUT L 41, 2003, s. 26)."]}]}
    got = catalog._document_snippet(dom, "eurlex")
    assert got.startswith("Begäran om förhandsavgörande avser tolkningen")
    assert len(got.split()) <= 51

    # an ICC/ICJ decision skips the bench roster to the first real paragraph
    icj = {"structure": [
        {"type": "stycke", "text": ["Present: President Donoghue; Vice-President Gevorgian; Judges Tomka, Abraham, Bennouna, Yusuf and Xue"]},
        {"type": "stycke", "text": [long]}]}
    assert catalog._document_snippet(icj, "icj").startswith("Skyddet")
    # the wiki concepts keep their tree under `body`
    assert catalog._document_snippet({"body": [{"type": "stycke",
        "text": [long]}]}, "begrepp").startswith("Skyddet")
    # hudoc answers with its conclusions, never the procedural boilerplate
    case = {"metadata": {"conclusions": ["Violation of P1-1",
                                         "Pecuniary damage - financial award"]},
            "structure": [{"type": "stycke", "text": [
                "The European Court of Human Rights, sitting on 11 October as a Committee composed of judges"]}]}
    assert catalog._document_snippet(case, "hudoc") \
        == "Violation of P1-1; Pecuniary damage - financial award"
    assert catalog._document_snippet({"structure": case["structure"],
                                      "metadata": {}}, "hudoc") is None
    # a pre-Formex act whose first text node is its own title: the guard
    # skips past the echo to the next paragraph, and gives None only when
    # nothing follows
    echo = ("Council Regulation (EEC) No 939/76 of 23 April 1976 concluding "
            "the Financial Protocol between the Community and Malta")
    opening = ("The Financial Protocol between the European Economic "
               "Community and Malta shall be concluded on behalf of the "
               "Community.")
    pre_formex = {"uri": "https://lagen.nu/celex/31976R0939",
                  "title": echo,
                  "structure": [{"type": "stycke", "text": [echo]},
                                {"type": "stycke", "text": [opening]}]}
    assert catalog._document_snippet(pre_formex, "eurlex") == opening
    assert catalog._document_snippet(
        {**pre_formex, "structure": pre_formex["structure"][:1]},
        "eurlex") is None
    # the echo still fires when the 60-char slice lands on a word boundary
    # (a trailing space on one side must not defeat the comparison)
    spaced = "Regulation of the Council concerning the common approach to x"
    assert spaced[59] == " "
    assert catalog._document_snippet(
        {"uri": "https://lagen.nu/celex/31976R0940", "title": spaced,
         "structure": [{"type": "stycke", "text": [
             spaced + " and enough further words to clear the eighty "
             "character prose floor"]}]},
        "eurlex") is None
    # a journal article takes its first paragraph, capped at 50 words
    fifty_plus = " ".join("ord%d" % i for i in range(60))
    art50 = catalog._document_snippet(
        {"structure": [{"type": "stycke", "text": [fifty_plus]}]}, "lawreview")
    assert art50.endswith("ord49 …") and len(art50.split()) == 51
    assert catalog._document_snippet({"structure": [{"type": "stycke",
        "text": [long]}]}, "lawreview") == long
