"""The REST/OpenAPI service (accommodanda/api/app.py), driven through FastAPI's
TestClient over a fixture catalog + a faked search backend -- no live cluster,
no network."""

import json
import sqlite3
import time

import pytest
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
from opensearchpy.exceptions import ConnectionError as OpenSearchConnectionError

from accommodanda import config
from accommodanda.api import app as api
from accommodanda.api import db, reads
from accommodanda.lib import catalog, compress, facets, inbound, pathgraph


@pytest.fixture
def client(tmp_path):
    art_dir = tmp_path / "artifact"
    art_dir.mkdir()
    bb = art_dir / "bb.json"
    bb.write_text(json.dumps({
        "uri": "https://lagen.nu/1962:700", "source_url": "https://example/bb",
        "metadata": {"properties": {"dcterms:title": "Brottsbalk (1962:700)"}},
        "structure": [{"type": "paragraf", "id": "K3P1",
                       "text": ["Den som dödar annan döms för mord."]}]}))
    fl = art_dir / "fl.json"
    fl.write_text(json.dumps({
        "uri": "https://lagen.nu/2018:585",
        "metadata": {"properties": {"dcterms:title": "Förvaltningslag (2018:585)"}},
        "structure": [{"type": "paragraf", "id": "P1",
                       "text": ["Se ", {"uri": "https://lagen.nu/1962:700#K3P1",
                                        "text": "3 kap. 1 §"}, " brottsbalken."]}]}))
    cat = tmp_path / "catalog.sqlite"
    catalog.rebuild(cat, "sfs", [bb, fl])

    # the inbound endpoint answers from generate's per-document citation files,
    # not from the catalog -- write them here as a generate run would
    # (render._write_inbound). data_root is tmp_path (the catalog's own dir).
    con = catalog.connect(cat)
    uris = ("https://lagen.nu/1962:700", "https://lagen.nu/2018:585")
    for uri in uris:
        inbound.write(tmp_path, uri, inbound.citations(con, uri))
    inbound.mark_built(tmp_path, len(uris), 0)
    con.close()

    # point the request-scoped catalog connection at the fixture catalog
    def _con():
        con = sqlite3.connect(cat)
        try:
            yield con
        finally:
            con.close()
    api.app.dependency_overrides[api.get_con] = _con

    # a fake search backend -- the API must not require a live OpenSearch
    class FakeIndex:
        def search(self, q, source=None, kind=None, year=None, limit=10, offset=0,
                   cursor=None, sort="relevance"):
            self.last_sort = sort
            if cursor == "bad":
                raise ValueError("invalid search cursor")
            return {"total": 1, "next_cursor": None, "facets": {
                "source": [{"value": "sfs", "count": 1}],
                "kind": [{"value": "lag", "count": 1}],
                "year": [{"value": "1962", "count": 1}]}, "results": [{
                "uri": "https://lagen.nu/1962:700", "url": "/1962:700",
                "identifier": "SFS 1962:700",
                "title": "Brottsbalk (1962:700)", "source": "sfs", "kind": "lag",
                "score": 9.1, "inbound_count": 1,
                "highlight": ["… <em>%s</em> …" % q],
                "pin": None,
                "fragments": [{"uri": "https://lagen.nu/1962:700#K3P1",
                               "pinpoint": "K3P1", "label": "3 kap. 1 §",
                               "highlight": ["<em>%s</em>" % q]}]}]}
    api._index = FakeIndex()
    # the citation-pinning path opens the configured catalog directly rather
    # than taking the request connection (a missing catalog must not fail a
    # full-text search, so it is best-effort, with no Depends/503) -- point it
    # at the fixture too, or a pinned hit reads the developer's real corpus
    real_catalog, db.CATALOG = db.CATALOG, cat

    client = TestClient(api.app)
    client.catalog_path = cat            # for tests that add rows directly
    yield client
    db.CATALOG = real_catalog
    api.app.dependency_overrides.clear()


def test_search(client):
    r = client.get("/api/v1/search", params={"q": "mord", "source": "sfs"})
    assert r.status_code == 200
    body = r.json()
    assert body["query"] == "mord" and body["total"] == 1
    # source/kind buckets are named server-side from the facet schemes, so the
    # search UI does not keep its own abbreviated copy (N4); year is its own name
    assert body["facets"]["source"] == [
        {"value": "sfs", "count": 1, "label": "Författningar"}]
    assert body["facets"]["year"] == [{"value": "1962", "count": 1, "label": None}]
    hit = body["results"][0]
    assert hit["identifier"] == "SFS 1962:700"
    # singular: one hit, not a bucket. Brottsbalken is a balk, so its kind is
    # 'lag' -- SFS splits lag from förordning in the catalog (see test_facets)
    assert hit["kind_label"] == "Lag"
    # a full-text hit carries the passages it matched -- and no pin, so the hit
    # leads to the document (a client follows url + "#" + pin.pinpoint only
    # where a citation-shaped query resolved a provision)
    assert hit["pin"] is None
    assert hit["fragments"][0]["pinpoint"] == "K3P1"
    assert hit["highlight"] == ["… <em>mord</em> …"]     # the document's own
    # the API resolves each hit's public page path (layout.page_url): a statute
    # at lagen.nu's bare /<sfsid> address, colon kept
    assert hit["url"] == "/1962:700"


def test_search_sort_citations_reaches_the_index(client):
    # the order is the index's to apply; the endpoint validates and forwards
    r = client.get("/api/v1/search", params={"q": "mord", "sort": "citations"})
    assert r.status_code == 200
    assert api._index.last_sort == "citations"
    assert client.get("/api/v1/search", params={
        "q": "mord", "sort": "nonsens"}).status_code == 422


def test_search_accepts_year_facet(client):
    r = client.get("/api/v1/search", params={"q": "mord", "year": "1962"})
    assert r.status_code == 200
    assert client.get("/api/v1/search",
                      params={"q": "mord", "year": "62"}).status_code == 422


def test_search_cursor_validation_and_bounded_offset(client):
    assert client.get("/api/v1/search",
                      params={"q": "mord", "cursor": "bad"}).status_code == 422
    assert client.get("/api/v1/search",
                      params={"q": "mord", "offset": 10000}).status_code == 422
    assert client.get("/api/v1/search", params={
        "q": "mord", "cursor": "anything", "offset": 1}).status_code == 422


def test_search_fails_visibly_when_opensearch_is_down(client):
    """A down cluster is a 503 with a plain reason, not a raw 500 -- and the
    same visible-failure policy as the MCP search tool (one code path,
    api/reads.py)."""
    class Down:
        def search(self, *a, **k):
            raise OpenSearchConnectionError("no cluster")
    api._index = Down()
    r = client.get("/api/v1/search", params={"q": "mord"})
    assert r.status_code == 503
    assert "search is unavailable" in r.json()["detail"]


def test_legacy_atom_feed_urls_and_filters(client):
    r = client.get("/dataset/sfs/feed.atom")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/atom+xml")
    assert r.text.count("<entry>") == 2
    filtered = client.get(
        "/dataset/sfs/feed.atom", params={"rdf_type": "type/lag"})
    assert filtered.status_code == 200
    assert "https://lagen.nu/1962:700" in filtered.text
    assert "/dataset/sfs/feed.atom?rdf_type=type%2Flag" in filtered.text


def test_legacy_html_feed_and_unknown_dataset(client):
    r = client.get("/dataset/sfs/feed", params={"rdf_type": "type/lag"})
    assert r.status_code == 200 and "Alla författningar" in r.text
    assert client.get("/dataset/no-such-source/feed.atom").status_code == 404


def test_documents_lists_ids_and_metadata(client):
    r = client.get("/api/v1/documents")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2 and body["limit"] == 100 and body["offset"] == 0
    by_uri = {d["uri"]: d for d in body["documents"]}
    assert set(by_uri) == {"https://lagen.nu/1962:700", "https://lagen.nu/2018:585"}
    bb = by_uri["https://lagen.nu/1962:700"]
    assert bb["label"] == "SFS 1962:700"
    assert bb["source_url"] == "https://example/bb"      # indexed from the artifact
    assert bb["updated"] is not None                     # artifact mtime
    # a document without a source_url comes back with null, not omitted
    assert by_uri["https://lagen.nu/2018:585"]["source_url"] is None
    # no full content -- this is an index, not /document
    assert "artifact" not in bb


def test_documents_filter_and_paginate(client):
    assert client.get("/api/v1/documents",
                      params={"source": "dv"}).json()["total"] == 0
    assert client.get("/api/v1/documents",
                      params={"source": "sfs"}).json()["total"] == 2
    page = client.get("/api/v1/documents",
                      params={"limit": 1, "offset": 1}).json()
    assert page["total"] == 2 and len(page["documents"]) == 1
    # ordered by uri, so offset 1 is the second
    assert page["documents"][0]["uri"] == "https://lagen.nu/2018:585"


def test_documents_begrepp_stub_has_no_updated_timestamp(client):
    # a synthesized begrepp stub (path='') must not report a plausible-looking
    # but meaningless `updated` -- Path('') aliases to the server's cwd, so an
    # unguarded p.exists()/p.stat() reports *something* rather than nothing
    con = sqlite3.connect(client.catalog_path)
    with con:
        con.execute(
            "INSERT INTO documents (uri, source, kind, label, title, path, "
            " source_url, content_hash, expired, display) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("https://lagen.nu/begrepp/Mord", "begrepp", "begrepp",
             "Mord", "Mord", "", None, "x", None, "Mord"))
    con.close()
    body = client.get("/api/v1/documents", params={"source": "begrepp"}).json()
    assert body["total"] == 1
    assert body["documents"][0]["updated"] is None


def test_documents_hides_a_repealed_act_unless_asked(client):
    # a repealed document is out of the enumeration by default, the way it is
    # out of browse and search -- and still retrievable by uri, so a citation
    # to it resolves. 31995L0046 stopped applying when the GDPR replaced it.
    con = sqlite3.connect(client.catalog_path)
    with con:
        con.execute(
            "INSERT INTO documents (uri, source, kind, label, title, path, "
            " source_url, content_hash, expired, display) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("https://lagen.nu/ext/celex/31995L0046", "eurlex", "directive",
             "31995L0046", "Dataskyddsdirektivet", "", None, "x",
             "2018-05-24", "Dataskyddsdirektivet"))
        # a repeal that has not taken effect yet is not a repeal
        con.execute(
            "INSERT INTO documents (uri, source, kind, label, title, path, "
            " source_url, content_hash, expired, display) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("https://lagen.nu/ext/celex/32099L0001", "eurlex", "directive",
             "32099L0001", "Ännu gällande", "", None, "x",
             "2099-01-01", "Ännu gällande"))
    con.close()
    listed = client.get("/api/v1/documents", params={"source": "eurlex"}).json()
    assert [d["label"] for d in listed["documents"]] == ["32099L0001"]
    assert listed["total"] == 1

    both = client.get("/api/v1/documents",
                      params={"source": "eurlex", "include_expired": True}).json()
    assert [d["label"] for d in both["documents"]] == ["31995L0046", "32099L0001"]
    assert both["total"] == 2

    assert client.get("/api/v1/document",
                      params={"uri": "https://lagen.nu/ext/celex/31995L0046"}
                      ).status_code == 200


def test_document_returns_metadata_and_artifact(client):
    r = client.get("/api/v1/document",
                   params={"uri": "https://lagen.nu/1962:700"})
    assert r.status_code == 200
    body = r.json()
    assert body["title"] == "Brottsbalk (1962:700)"
    assert body["source_url"] == "https://example/bb"
    assert body["inbound_count"] == 1                  # cited by 2018:585
    assert body["artifact"]["structure"][0]["id"] == "K3P1"


def test_document_format_md_swaps_the_artifact_for_markdown(client):
    body = client.get("/api/v1/document",
                      params={"uri": "https://lagen.nu/1962:700",
                              "format": "md"}).json()
    # the envelope and metadata stay JSON; only the body is transformed
    assert body["title"] == "Brottsbalk (1962:700)"
    assert body["inbound_count"] == 1
    assert "artifact" not in body
    assert body["markdown"].startswith("# Brottsbalk (1962:700)")
    assert "Den som dödar annan döms för mord." in body["markdown"]

    assert client.get("/api/v1/document",
                      params={"uri": "https://lagen.nu/1962:700",
                              "format": "xml"}).status_code == 422


def test_document_begrepp_stub_served_with_empty_artifact(client):
    # a synthesized begrepp stub is a real catalog row with no artifact file
    # (path='', as minted by catalog.synthesize_concepts) -- /document must
    # serve it as an empty artifact, not 500 on reading Path('')
    con = sqlite3.connect(client.catalog_path)
    with con:
        con.execute(
            "INSERT INTO documents (uri, source, kind, label, title, path, "
            " source_url, content_hash, expired, display) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("https://lagen.nu/begrepp/Mord", "begrepp", "begrepp",
             "Mord", "Mord", "", None, "x", None, "Mord"))
    con.close()
    r = client.get("/api/v1/document",
                   params={"uri": "https://lagen.nu/begrepp/Mord"})
    assert r.status_code == 200
    body = r.json()
    assert body["label"] == "Mord" and body["artifact"] == {}


def test_document_unknown_uri_404(client):
    r = client.get("/api/v1/document", params={"uri": "https://lagen.nu/9999:1"})
    assert r.status_code == 404


def test_inbound_is_the_citation_graph(client):
    r = client.get("/api/v1/document/inbound",
                   params={"uri": "https://lagen.nu/1962:700#K3P1"})
    assert r.status_code == 200
    body = r.json()
    assert [c["uri"] for c in body["citations"]] == ["https://lagen.nu/2018:585"]
    assert body["citations"][0]["label"] == "SFS 2018:585"
    assert body["total"] == 1 and body["by_source"] == {"sfs": 1}


def test_inbound_on_a_law_reaches_the_citations_of_its_paragrafer(client):
    """The default scope answers for the law *and everything in it*: the fixture's
    only citation names 3 kap. 1 §, never the balk as such, so `exact` -- the
    question this endpoint used to answer -- finds nothing at all."""
    tree = client.get("/api/v1/document/inbound",
                      params={"uri": "https://lagen.nu/1962:700"}).json()
    assert [c["target"] for c in tree["citations"]] == \
        ["https://lagen.nu/1962:700#K3P1"]
    exact = client.get("/api/v1/document/inbound",
                       params={"uri": "https://lagen.nu/1962:700",
                               "scope": "exact"}).json()
    assert exact["total"] == 0 and exact["citations"] == []


def test_inbound_source_filter_narrows_but_by_source_counts_the_whole_scope(client):
    """`source` narrows the citing side (the fixture's one citation is from
    sfs, so asking for dv finds nothing) while `by_source` still reports the
    unfiltered scope -- it is what tells a client which corpus to ask for."""
    body = client.get("/api/v1/document/inbound",
                      params={"uri": "https://lagen.nu/1962:700",
                              "source": "dv"}).json()
    assert body["source"] == "dv"
    assert body["total"] == 0 and body["citations"] == []
    assert body["by_source"] == {"sfs": 1}


def test_inbound_refuses_when_the_tree_is_not_built(client, tmp_path):
    """Absence of one file means "nothing cites this"; absence of the whole tree
    means the corpus was never generated, or the deploy's artifact rsync has not
    landed. Answering the second with `total: 0` would report, with a 200, that
    nothing in Swedish law cites anything -- so it refuses, exactly as a missing
    catalog does.

    Keyed on the sweep's marker, not on the directory: the sweep *creates* the
    directory before it fills it, and `generate --ignore-code-changes` renders
    almost no pages, so a directory holding only the uncatalogued targets would
    have passed a `is_dir()` test and reached the very failure this prevents."""
    (tmp_path / inbound.TREE / inbound.BUILT).unlink()
    r = client.get("/api/v1/document/inbound",
                   params={"uri": "https://lagen.nu/1962:700"})
    assert r.status_code == 503 and "generate" in r.json()["detail"]


def test_inbound_pages_a_stable_order(client):
    body = client.get("/api/v1/document/inbound",
                      params={"uri": "https://lagen.nu/1962:700",
                              "limit": 1, "offset": 1}).json()
    # `total` and `by_source` describe the whole answer, not the page returned
    assert body["total"] == 1 and body["by_source"] == {"sfs": 1}
    assert body["citations"] == []


def _cited_corpus(tmp_path):
    """A law, three documents citing one of its paragrafer, and a different
    number of citations *of each citer* -- so "which of these weighs most" has
    an unambiguous answer. Returns the connection; the inbound sidecar the
    endpoint reads is written from the catalog, as `generate` writes it."""
    (tmp_path / "artifact").mkdir()          # data_root's fail-fast wants one
    con = catalog.connect(tmp_path / "catalog.sqlite")
    law = "https://lagen.nu/1962:700"
    con.execute("INSERT INTO documents (uri, source, kind, label, title, path) "
                "VALUES (?, 'sfs', 'lag', 'BrB', 'Brottsbalk', '')", (law,))
    # The prop is the most-cited citer and the rail order puts it *last* (case
    # law leads a statute's panel), so the two orders disagree -- without that
    # a passing test proves nothing about which one the endpoint used.
    for citer, source, label, weight in (
            ("https://lagen.nu/dom/nja/2013s376", "dv", "NJA 2013 s. 376", 5),
            ("https://lagen.nu/dom/nja/2016s3", "dv", "NJA 2016 s. 3", 2),
            ("https://lagen.nu/prop/2020/21:1", "forarbete", "Prop. 2020/21:1", 9)):
        con.execute("INSERT INTO documents (uri, source, kind, label, title, "
                    "path) VALUES (?, ?, 'x', ?, 'T', '')",
                    (citer, source, label))
        con.execute("INSERT INTO links (from_uri, from_anchor, predicate, "
                    "to_uri, to_root) VALUES (?, 'P1', 'dcterms:references', "
                    "?, ?)", (citer, law + "#K3P1", law))
        for i in range(weight):
            con.execute(
                "INSERT INTO links (from_uri, from_anchor, predicate, to_uri, "
                "to_root) VALUES (?, ?, 'dcterms:references', ?, ?)",
                ("https://lagen.nu/other/%d" % i, "P%d" % i, citer, citer))
    inbound.write(tmp_path, law, inbound.citations(con, law))
    inbound.mark_built(tmp_path, 1, 0)
    con.commit()          # so a second connection (the HTTP path) sees the rows
    return con, law


def test_inbound_rows_carry_the_citing_document_s_own_citation_count(tmp_path):
    """Every row says how heavily the *citer* is cited, so "which of these
    matter" is answerable from the reply instead of a call per row. Same number
    and same name /search and /document answer with."""
    con, law = _cited_corpus(tmp_path)
    rows = reads.inbound_citations(con, law, limit=10, offset=0)["citations"]
    assert {r["label"]: r["inbound_count"] for r in rows} == {
        "NJA 2013 s. 376": 5, "NJA 2016 s. 3": 2, "Prop. 2020/21:1": 9}
    # ...and the default order is the rail's, which ignores the count: the
    # most-cited citer here is the prop, and it comes last
    assert [r["label"] for r in rows] == [
        "NJA 2013 s. 376", "NJA 2016 s. 3", "Prop. 2020/21:1"]
    con.close()


def test_inbound_sorted_by_citations_puts_the_weightiest_citer_first(tmp_path):
    """`sort=citations` is the "leading cases on this paragraf" question. The
    default order is the site's context rail -- case law first, then the rest --
    which answers a different one."""
    con, law = _cited_corpus(tmp_path)
    ranked = reads.inbound_citations(con, law, sort="citations",
                                     limit=10, offset=0)
    assert [r["label"] for r in ranked["citations"]] == [
        "Prop. 2020/21:1", "NJA 2013 s. 376", "NJA 2016 s. 3"]
    assert ranked["sort"] == "citations"      # echoed back, like scope/source
    # the filters compose: the prop drops out, the case order is unchanged
    cases = reads.inbound_citations(con, law, sort="citations", source="dv",
                                    limit=10, offset=0)
    assert [r["label"] for r in cases["citations"]] == [
        "NJA 2013 s. 376", "NJA 2016 s. 3"]
    assert cases["by_source"] == {"dv": 2, "forarbete": 1}   # the whole scope
    con.close()


def test_inbound_citation_sort_breaks_ties_on_the_rail_order(tmp_path):
    """Two citers nothing cites are both 0, so the sort has nothing to say
    about them. Python's sort is stable and the rail order is total, so they
    keep it -- which is what makes `offset` paging stable under either sort."""
    con, law = _cited_corpus(tmp_path)
    con.execute("DELETE FROM links WHERE from_uri LIKE '%/other/%'")
    default = reads.inbound_citations(con, law, limit=10, offset=0)
    ranked = reads.inbound_citations(con, law, sort="citations",
                                     limit=10, offset=0)
    assert {r["inbound_count"] for r in ranked["citations"]} == {0}
    assert [r["uri"] for r in ranked["citations"]] == \
        [r["uri"] for r in default["citations"]]
    con.close()


def test_inbound_ranking_reaches_the_rest_endpoint(tmp_path):
    """Through HTTP, not through `reads`: the endpoint has to pass `sort` on and
    the response model has to carry `inbound_count`. Dropping either left every
    test above green while the endpoint answered rail rows under a
    `"sort": "citations"` label."""
    con, law = _cited_corpus(tmp_path)
    con.close()

    def _fresh():
        # TestClient runs the endpoint on a worker thread, and a sqlite
        # connection belongs to the thread that opened it
        request_con = sqlite3.connect(tmp_path / "catalog.sqlite")
        try:
            yield request_con
        finally:
            request_con.close()

    api.app.dependency_overrides[api.get_con] = _fresh
    try:
        body = TestClient(api.app).get(
            "/api/v1/document/inbound",
            params={"uri": law, "sort": "citations", "limit": 10}).json()
    finally:
        api.app.dependency_overrides.clear()
    assert body["sort"] == "citations"
    assert [(c["label"], c["inbound_count"]) for c in body["citations"]] == [
        ("Prop. 2020/21:1", 9), ("NJA 2013 s. 376", 5), ("NJA 2016 s. 3", 2)]


def test_inbound_counts_more_citers_than_sqlite_binds_at_once(tmp_path):
    """`sort=citations` counts the whole scope, and SQLite binds one variable
    per uri with a hard cap (32 766 here). The ECHR has 50 626 citers, so the
    unchunked query raised OperationalError -- an unhandled 500 on a public
    endpoint. Proven against the limit itself rather than a stand-in."""
    con = catalog.connect(tmp_path / "catalog.sqlite")
    limit = sqlite3.connect(":memory:").getlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER)
    uris = ["https://lagen.nu/x/%d" % i for i in range(limit + 50)]
    con.execute("INSERT INTO links (from_uri, from_anchor, predicate, to_uri, "
                "to_root) VALUES ('https://lagen.nu/citer', 'P1', "
                "'dcterms:references', ?, ?)", (uris[0], uris[0]))
    assert catalog.inbound_counts_for(con, uris) == {uris[0]: 1}
    con.close()


def test_inbound_rejects_an_unknown_sort(client):
    assert client.get("/api/v1/document/inbound",
                      params={"uri": "https://lagen.nu/1962:700",
                              "sort": "nonsens"}).status_code == 422


def test_outbound_marks_unhosted_targets(client):
    r = client.get("/api/v1/document/outbound",
                   params={"uri": "https://lagen.nu/2018:585"})
    rows = r.json()
    assert any(c["uri"] == "https://lagen.nu/1962:700#K3P1" and c["hosted"]
               for c in rows)


def test_facets_returns_navigation_tree(client):
    # the two fixture laws file under their subject initial: Brottsbalk -> B,
    # Förvaltningslag -> F (the 'Lag'/'balk' designation isn't the sort word)
    r = client.get("/api/v1/facets", params={"source": "sfs"})
    assert r.status_code == 200
    tree = r.json()
    assert tree["levels"] == ["Bokstav"]
    assert [b["slug"] for b in tree["buckets"]] == ["b", "f"]
    assert tree["default"] == ["B"]


def test_facets_unknown_source_404(client):
    assert client.get("/api/v1/facets",
                      params={"source": "kommentar"}).status_code == 404


def test_browse_returns_navigator_with_leaf_documents(client):
    r = client.get("/api/v1/browse", params={"source": "sfs"})
    assert r.status_code == 200
    view = r.json()
    # the 'F' bucket (Förvaltningslag) carries its leaf documents, labelled + URL'd
    f = next(b for b in view["buckets"] if b["slug"] == "f")
    assert f["count"] == 1 and f["children"] is None
    assert f["documents"] == [{"uri": "https://lagen.nu/2018:585",
                               "url": "/2018:585",
                               "display": "Förvaltningslag (2018:585)",
                               # the labels-derived listing forms (I2); short_title
                               # is the namedlaws name for 2018:585
                               "short_id": "SFS 2018:585",
                               "short_title": "Säkerhetsskyddslagen",
                               "description": None, "variant": None, "date": None,
                               "pre": "", "key": "Förvaltningslag (2018:585)",
                               "subdued": False, "year": "2018",
                               # föreskrift-only: the ändringsförfattningar
                               # nested under their base (F5) and the
                               # konsoliderad marker (B4) -- None elsewhere
                               "amendments": None, "consolidated": None}]


def test_sources(client):
    r = client.get("/api/v1/sources")
    assert r.json() == [{"source": "sfs", "documents": 2}]


def test_serve_mounts_static_site_alongside_api(client, tmp_path):
    # `serve()` mounts the generated site at / on the same app: the REST routes
    # still answer first, everything else falls through to the static files
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("<html>frontpage</html>")
    api.app.mount("/", StaticFiles(directory=str(site), html=True), name="site")
    try:
        assert client.get("/api/v1/sources").status_code == 200   # API wins
        root = client.get("/")
        assert root.status_code == 200 and "frontpage" in root.text
    finally:
        api.app.router.routes.pop()                               # unmount


def test_site_asset_revalidation_304(client, tmp_path, monkeypatch):
    # SiteFiles' precompressed branch builds its FileResponse directly, outside
    # StaticFiles.get_response's not-modified check -- a browser revalidating
    # style.css/script.js with If-None-Match must get a 304, not the body again
    monkeypatch.setattr(config, "COMPRESS", True)
    site = tmp_path / "site"
    site.mkdir()
    compress.write_text(site / "style.css", "body { color: red }\n" * 100)
    api.app.mount("/", api.SiteFiles(directory=str(site), html=True), name="site")
    try:
        first = client.get("/style.css", headers={"Accept-Encoding": "br"})
        assert first.status_code == 200
        assert first.headers["content-encoding"] == "br"
        etag = first.headers["etag"]
        again = client.get("/style.css", headers={"Accept-Encoding": "br",
                                                  "If-None-Match": etag})
        assert again.status_code == 304
        assert not again.content
    finally:
        api.app.router.routes.pop()                               # unmount


def test_openapi_served(client):
    r = client.get("/openapi.json")
    assert r.status_code == 200
    assert "/api/v1/search" in r.json()["paths"]


def test_the_public_schema_carries_only_the_public_api(client):
    """/docs is the public contract, so nothing the site drives itself may
    appear in it -- a reader who saw POST .../edit/commit beside GET
    .../search could not tell which of the two is a promise."""
    paths = client.get("/openapi.json").json()["paths"]
    assert all(p.startswith("/api/v1/") for p in paths), sorted(paths)
    assert not any(p.startswith("/api/v1/" + internal) for p in paths
                   for internal in ("auth", "edit", "patch", "graphics",
                                    "pdf/jobb", "pdf/samling"))
    assert not any(p.startswith("/ops") or p.startswith("/internal-api")
                   for p in paths)


@pytest.fixture
def editing_on(monkeypatch):
    """An editor secret, so `require_editor` answers 401 (log in) rather than
    403 (editing disabled). Without it these tests read whatever `config.yml`
    the machine happens to carry -- they passed here and failed on a checkout
    with no `editor_secret`, which is the wrong thing to be sensitive to: what
    they are about is whether the *route* is there, not how the host is set up."""
    monkeypatch.setattr(config, "EDITOR_SECRET", "test-signing-key")


def test_the_internal_api_answers_under_its_own_prefix(client, editing_on):
    """The routes moved rather than vanished -- and 404, not 401, is what the
    public prefix now says about them."""
    assert client.get("/api/v1/auth/me").status_code == 404
    assert client.get("/api/v1/edit/cart").status_code == 404
    # 401 is the editor gate answering, i.e. the route is there
    assert client.get("/internal-api/v1/auth/me").status_code == 401
    assert client.get("/internal-api/v1/edit/cart").status_code == 401


@pytest.mark.parametrize("path", ["/internal-api/v1/edit/cart",
                                  "/internal-api/v1/graphics/queue",
                                  "/internal-api/v1/pdf/jobb/x",
                                  "/ops"])
@pytest.mark.parametrize("headers", [{"sec-fetch-site": "cross-site"},
                                     {"sec-fetch-site": "same-site"},
                                     {"origin": "https://evil.example"}])
def test_the_internal_surface_refuses_another_origin(client, path, headers):
    """Every internal route, reading or writing, and the ops dashboard with
    them. CORS alone would not do it: it stops a cross-origin browser from
    *reading* a GET's response, and the crop-review queue is a GET."""
    r = client.get(path, headers=headers)
    assert r.status_code == 403, (path, headers, r.status_code)
    assert r.json()["detail"] == "this endpoint answers same-origin requests only"


@pytest.mark.parametrize("headers", [{}, {"sec-fetch-site": "same-origin"},
                                     {"sec-fetch-site": "none"},
                                     {"origin": "http://testserver"},
                                     {"origin": "https://testserver"}])
def test_the_internal_surface_lets_its_own_origin_through(client, editing_on,
                                                         headers):
    """Our own page, a typed address or a bookmark, and a non-browser caller
    (curl, the in-process client `generate` runs) all reach the editor gate --
    401 is that gate, not the origin check.

    The https case is the one that bites: `Origin` is compared on host alone,
    because the scheme the app computes for itself is `https` only when
    `FORWARDED_ALLOW_IPS` names the proxy. That variable has been wrong on this
    deployment before, and comparing the whole origin string turned that into a
    403 on every editor POST, every PDF job and every /ops page."""
    assert client.get("/internal-api/v1/edit/cart",
                      headers=headers).status_code == 401


def test_an_internal_error_answers_json_not_the_site_page(client):
    """`/internal-api` is in `errors.JSON_PREFIXES`, and it has to be: the JS
    that drives these routes (editor.js, patch_edit.html, pdf_wait.html) calls
    `.json()` on a failed response, and without the prefix a 404 there would
    hand it the site's rendered HTML error page."""
    r = client.get("/internal-api/v1/pdf/jobb/nosuchjob")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")
    assert r.json()["detail"] == "no such pdf job"
    # 404 and 5xx are the statuses that carry a ledger reference -- the
    # documented contract in docs/api/README.md
    assert "error_id" in r.json()


def test_the_public_api_stays_open_to_any_origin(client):
    """The read API is the one thing this split must not narrow."""
    r = client.get("/api/v1/sources", headers={"sec-fetch-site": "cross-site",
                                               "origin": "https://evil.example"})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "*"


def test_search_pins_a_pinpoint_citation_with_its_provision(client):
    # the reader's query names a provision, so the hit says which provision it
    # landed on and shows that provision's own words -- "Brottsbalk (1962:700)"
    # alone gave no sign the pin had worked (Q2)
    body = client.get("/api/v1/search",
                      params={"q": "3 kap. 1 § brottsbalken"}).json()
    hit = body["results"][0]
    assert hit["uri"] == "https://lagen.nu/1962:700"
    pin = hit["pin"]
    assert pin["pinpoint"] == "K3P1"
    assert pin["label"] == "3 kap. 1 §"
    assert pin["highlight"] == ["Den som dödar annan döms för mord."]
    # the pin is the answer; a resolved hit has no full-text passages to fold in
    assert hit["fragments"] == []


def test_search_pins_the_terse_law_first_pinpoint(client):
    # "BrB 3:1" is how a lawyer types it; the grammar wants "3 kap. 1 §"
    pin = client.get("/api/v1/search", params={"q": "BrB 3:1"}) \
        .json()["results"][0]["pin"]
    assert pin["pinpoint"] == "K3P1" and pin["label"] == "3 kap. 1 §"


def test_search_explicit_offset_walks_raw_without_the_pin(client):
    # an explicit offset -- 0 included -- is raw bounded random access: no
    # pinned lead (it would push the page's last raw hit past the boundary
    # offset=limit resumes from) and no related-hit cap. The offsetless first
    # page keeps the pin.
    q = {"q": "3 kap. 1 § brottsbalken"}
    with_pin = client.get("/api/v1/search", params=q).json()
    raw = client.get("/api/v1/search", params={**q, "offset": 0}).json()
    assert with_pin["results"][0]["pin"]["pinpoint"] == "K3P1"
    # a pinned lead carries score None; the raw page is the index's hit alone
    assert [r["score"] for r in raw["results"]] == [9.1]


def test_graph_neighborhood_and_pinpoint(client):
    # doc level: fl cites bb once, aggregated per neighbor with flow groups
    r = client.get("/api/v1/graph", params={"uri": "https://lagen.nu/1962:700"})
    assert r.status_code == 200
    d = r.json()
    assert d["group"] == "Författningar" and d["anchor"] is None
    assert [n["uri"] for n in d["inbound"]["top"]] \
        == ["https://lagen.nu/2018:585"]
    assert d["inbound"]["total_links"] == 1
    assert d["internal"] is None

    # internal=true asks for the unit graph on a document uri too -- the
    # explorer's zoomed-in structure view -- with no focus unit among the
    # nodes (the fixture corpus has no self-citations, so it is empty)
    d = client.get("/api/v1/graph", params={
        "uri": "https://lagen.nu/1962:700", "internal": "true"}).json()
    assert d["internal"] == {"nodes": [], "edges": [], "truncated": 0}

    # a fragment uri answers for that provision alone and adds the (here
    # empty) internal unit graph, with the reader-facing pinpoint label --
    # and a deep arrival anchor (#K3P1S2) answers for the § its `pinpoint`
    # names, not the stycke subtree alone
    for frag in ("K3P1", "K3P1S2"):
        d = client.get("/api/v1/graph",
                       params={"uri": "https://lagen.nu/1962:700#" + frag}).json()
        assert d["pinpoint"] == "3 kap. 1 §" and d["unit"] == "K3P1"
        # `citation` names the document as well as the place: "3 kap. 1 §"
        # alone is a pinpoint into nothing, and the graph's center is drawn
        # from it
        assert d["citation"] == "3 kap. 1 § brottsbalken"
        assert [n["uri"] for n in d["inbound"]["top"]] \
            == ["https://lagen.nu/2018:585"]
        assert d["internal"]["edges"] == []

    # direction excludes a side; the group filter narrows the neighbor set;
    # unknown uris and unknown groups fail visibly
    d = client.get("/api/v1/graph", params={
        "uri": "https://lagen.nu/1962:700", "direction": "out"}).json()
    assert d["inbound"] is None and d["outbound"]["total_links"] == 0
    assert client.get("/api/v1/graph", params={
        "uri": "https://lagen.nu/1962:700",
        "groups": "Rättsfall"}).json()["inbound"]["top"] == []
    assert client.get("/api/v1/graph",
                      params={"uri": "https://lagen.nu/x"}).status_code == 404
    assert client.get("/api/v1/graph", params={
        "uri": "https://lagen.nu/1962:700",
        "groups": "Nonsens"}).status_code == 422
    # the neighbourhood cap. The explorer asks for 120 a side -- article 6 ECHR
    # has 31,996 citers, and 22 of them was a keyhole -- so the ceiling is well
    # past that, and past it the request fails rather than being clamped
    assert client.get("/api/v1/graph", params={
        "uri": "https://lagen.nu/1962:700", "limit": 300}).status_code == 200
    assert client.get("/api/v1/graph", params={
        "uri": "https://lagen.nu/1962:700", "limit": 301}).status_code == 422


def test_graph_labels_only_the_rows_the_reply_carries(tmp_path):
    """An unfiltered inbound side takes the counts-only queries and labels its
    top rows alone; a group-filtered one still labels every neighbor, because
    the filter reads each one's `source` and `kind`. The two must answer the
    same -- naming every group filters nothing out.

    The join it drops cost one random row lookup per *citer*: article 6 ECHR
    has 50,624 of them, and labelling them all to answer with 120 measured
    5.4 s of a 5.8 s reply on prod's disk."""
    con = catalog.connect(tmp_path / "catalog.sqlite")
    law = "https://lagen.nu/1962:700"
    con.execute("INSERT INTO documents (uri, source, kind, label, title, path) "
                "VALUES (?, 'sfs', 'law', 'BrB', 'Brottsbalk', '')", (law,))
    # five citers, each citing 3 kap. 1 § a different number of times, so the
    # `n DESC` order the reply is sliced by is unambiguous
    for i in range(5):
        citer = "https://lagen.nu/dom/HFD/%d" % i
        con.execute("INSERT INTO documents (uri, source, kind, label, title, "
                    "path) VALUES (?, 'dv', 'verdict', ?, 'T', '')",
                    (citer, "HFD %d" % i))
        for _ in range(i + 1):
            con.execute(
                "INSERT INTO links (from_uri, from_anchor, predicate, to_uri, "
                "to_root) VALUES (?, 'P1', 'dcterms:references', ?, ?)",
                (citer, law + "#K3P1", law))

    every_group = set(facets.FLOW_GROUP_NAMES)
    for uri in (law, law + "#K3P1"):
        unfiltered = reads.graph(con, uri, limit=2)
        filtered = reads.graph(con, uri, groups=every_group, limit=2)
        assert unfiltered == filtered
        side = unfiltered["inbound"]
        # the totals describe the whole neighborhood, not the two rows drawn
        assert (side["total_docs"], side["total_links"]) == (5, 15)
        assert [(r["label"], r["links"]) for r in side["top"]] \
            == [("HFD 4", 5), ("HFD 3", 4)]
        assert side["top"][0]["group"] == "Rättsfall"
    con.close()


def test_graph_answers_for_a_node_nothing_cites(tmp_path):
    """`catalog.graph_labels` builds an `IN (…)` list from the rows the reply
    carries. A node with no citers builds `IN ()`, which SQLite accepts and
    nothing matches -- so the empty side is an empty side, not an error."""
    con = catalog.connect(tmp_path / "catalog.sqlite")
    con.execute("INSERT INTO documents (uri, source, kind, label, title, path) "
                "VALUES ('https://lagen.nu/x', 'sfs', 'law', 'X', 'T', '')")
    assert catalog.graph_labels(con, []) == {}
    assert reads.graph(con, "https://lagen.nu/x")["inbound"] \
        == {"total_links": 0, "total_docs": 0, "top": []}
    con.close()


def test_graph_carries_the_publishers_source_url(tmp_path):
    """The graph payload hands out the document's own publisher page. For a
    tidskriftsartikel the site renders no page -- source_url is the only link
    a consumer may show, never the /lawreview/ path."""
    con = catalog.connect(tmp_path / "catalog.sqlite")
    art = "https://lagen.nu/lawreview/lod/2022-1-01"
    con.execute("INSERT INTO documents (uri, source, kind, label, title, "
                "descriptive, path, source_url) VALUES (?, 'lawreview', "
                "'artikel', 'Lov & Data 1/2022', 'Fremtidens IT-kontraktret', "
                "'Henrik Udsen', '', 'https://lod.lovdata.no/article/x')",
                (art,))
    d = reads.graph(con, art)
    assert d["source_url"] == "https://lod.lovdata.no/article/x"
    assert d["label"] == "Lov & Data 1/2022"
    # a document with no recorded source_url answers None, not ''
    con.execute("INSERT INTO documents (uri, source, kind, label, title, "
                "path, source_url) VALUES ('https://lagen.nu/y', 'sfs', "
                "'law', 'Y', 'T', '', '')")
    assert reads.graph(con, "https://lagen.nu/y")["source_url"] is None
    con.close()


def test_graph_internal_for_a_document_uri_carries_real_edges(tmp_path):
    """`internal=True` on a document uri assembles the unit graph with no
    focus unit: the nodes are exactly the units the self-citations touch --
    no phantom None node -- and the edges arrive at unit level."""
    con = catalog.connect(tmp_path / "catalog.sqlite")
    law = "https://lagen.nu/1962:700"
    con.execute("INSERT INTO documents (uri, source, kind, label, title, path) "
                "VALUES (?, 'sfs', 'law', 'BrB', 'Brottsbalk', '')", (law,))
    # 3 kap. 1 § cites 1 kap. 1 § twice, at stycke depth once -- both rows
    # must collapse onto the K3P1 -> K1P1 unit edge
    for to_frag in ("K1P1", "K1P1S1"):
        con.execute("INSERT INTO links (from_uri, from_anchor, predicate, "
                    "to_uri, to_root) VALUES (?, 'K3P1S2', "
                    "'dcterms:references', ?, ?)",
                    (law, law + "#" + to_frag, law))
    d = reads.graph(con, law, internal=True)
    assert d["internal"]["edges"] == [["K3P1", "K1P1", 2]]
    assert [u["anchor"] for u in d["internal"]["nodes"]] == ["K1P1", "K3P1"]
    assert d["internal"]["truncated"] == 0
    con.close()


def test_json_answers_declare_their_charset(client):
    """`application/json` without a charset makes a browser viewing the raw
    answer guess, and Safari guesses Latin-1 -- "säkerhetsskyddslagen" came
    out as "sÃ¤kerhetsskyddslagen". Every JSON endpoint states utf-8."""
    for path in ("/api/v1/graph?uri=https://lagen.nu/1962:700",
                 "/api/v1/sources"):
        r = client.get(path)
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/json; charset=utf-8"


def _path(client, params):
    """GET /api/v1/path, riding out the 503s while the graph loads in its
    background thread -- a request never waits on the build (the lock-and-
    build-under-request shape 504:ed for hours on prod's disk)."""
    for _ in range(300):
        r = client.get("/api/v1/path", params=params)
        if r.status_code != 503:
            return r
        time.sleep(0.02)
    raise AssertionError("the path graph never became ready")


def test_path_walks_the_shortest_chain(client):
    """/api/v1/path: fl cites bb, so the out-chain fl->bb is one step, the
    in-chain bb->fl mirrors it, and out from bb reaches nothing."""
    bb, fl = "https://lagen.nu/1962:700", "https://lagen.nu/2018:585"
    r = _path(client, {"from": fl, "to": bb, "direction": "out"})
    assert r.status_code == 200
    d = r.json()
    assert d["from"] == fl and d["to"] == bb and d["distance"] == 1
    assert [s["uri"] for s in d["path"]] == [fl, bb]
    # the hop carries one citation, in citing direction; the last step has no hop
    assert (d["path"][0]["links"], d["path"][0]["forward"]) == (1, True)
    assert (d["path"][1]["links"], d["path"][1]["forward"]) == (None, None)

    d = client.get("/api/v1/path", params={"from": bb, "to": fl,
                                           "direction": "in"}).json()
    assert d["distance"] == 1 and d["path"][0]["forward"] is False

    d = client.get("/api/v1/path", params={"from": bb, "to": fl,
                                           "direction": "out"}).json()
    assert d["distance"] is None and d["path"] == []

    # a fragment uri answers for its document; from==to is a zero-step chain
    d = client.get("/api/v1/path", params={"from": fl + "#P1",
                                           "to": fl}).json()
    assert d["distance"] == 0 and [s["uri"] for s in d["path"]] == [fl]

    assert client.get("/api/v1/path", params={
        "from": "https://lagen.nu/x", "to": bb}).status_code == 404
    assert client.get("/api/v1/path", params={
        "from": fl, "to": bb, "groups": "Nonsens"}).status_code == 422


def test_path_group_filter_gates_the_intermediates(client):
    """The groups filter constrains the documents a chain may pass through --
    never its endpoints. A dv citer two steps from bb loses its chain when
    the intermediate författning is filtered away."""
    bb, fl = "https://lagen.nu/1962:700", "https://lagen.nu/2018:585"
    dom = "https://lagen.nu/dom/nja/2020s1"
    con = sqlite3.connect(client.catalog_path)
    con.execute("INSERT INTO documents (uri, source, kind, label, title, "
                "path) VALUES (?, 'dv', 'verdict', 'NJA 2020 s. 1', 'T', '')",
                (dom,))
    con.execute("INSERT INTO links (from_uri, predicate, to_uri, to_root) "
                "VALUES (?, 'dcterms:references', ?, ?)", (dom, fl, fl))
    con.commit(); con.close()
    q = {"from": dom, "to": bb, "direction": "out"}
    d = _path(client, q).json()
    assert [s["uri"] for s in d["path"]] == [dom, fl, bb]
    d = _path(client, {**q, "groups": "Rättsfall,Förarbeten"}).json()
    assert d["distance"] is None


def test_graph_sort_citations_and_grouplimit(tmp_path):
    """`sort=citations` ranks neighbours by their own (stamped) citedness
    instead of their ties to the center; `grouplimit` caps how many of one
    flow group `top` carries. The totals keep describing the whole side."""
    con = catalog.connect(tmp_path / "catalog.sqlite")
    law = "https://lagen.nu/1900:1"
    a, b = "https://lagen.nu/dom/nja/A", "https://lagen.nu/dom/nja/B"
    c = "https://lagen.nu/sou/1900:2"
    rows = [(law, "sfs", "law", "L"), (a, "dv", "verdict", "NJA A"),
            (b, "dv", "verdict", "NJA B"), (c, "forarbete", "sou", "SOU C")]
    for uri, source, kind, label in rows:
        con.execute("INSERT INTO documents (uri, source, kind, label, title, "
                    "path) VALUES (?, ?, ?, ?, 'T', '')",
                    (uri, source, kind, label))
    def link(f, t, n=1):
        for i in range(n):
            con.execute("INSERT INTO links (from_uri, from_anchor, predicate, "
                        "to_uri, to_root) VALUES (?, ?, 'dcterms:references', "
                        "?, ?)", (f, "P%d" % i, t, t))
    link(a, law, 5)      # A holds the most ties to the center...
    link(b, law, 1)
    link(c, law, 1)
    link(c, b, 3)        # ...but B is the cited authority
    catalog.stamp_inbound_counts(con)

    by_links = reads.graph(con, law, direction="in", limit=10)
    assert [r["uri"] for r in by_links["inbound"]["top"]] == [a, b, c]
    by_cite = reads.graph(con, law, direction="in", limit=10,
                          sort="citations")
    assert [r["uri"] for r in by_cite["inbound"]["top"]][0] == b
    # same semantics as everywhere else inbound_count appears: (citer,
    # pinpoint) entries -- C cites B from three distinct anchors
    assert by_cite["inbound"]["top"][0]["inbound_count"] == 3
    capped = reads.graph(con, law, direction="in", limit=10, grouplimit=1)
    assert [r["group"] for r in capped["inbound"]["top"]] \
        == ["Rättsfall", "Förarbeten"]
    # grouplimit narrows `top`, never the totals
    assert capped["inbound"]["total_docs"] == 3
    con.close()


def test_graph_depth_expands_rings_in_one_answer(tmp_path):
    """depth=2 answers with the outer ring and the whole neighbourhood's
    induced edge list in ONE call -- the old client-side walk asked each
    frontier node separately and starved the view. The per-side limit is a
    whole-view budget: hop 1 gives up rows so the rings have room."""
    con = catalog.connect(tmp_path / "catalog.sqlite")
    C, A, Z = ("https://lagen.nu/c", "https://lagen.nu/dom/a",
               "https://lagen.nu/sou/z")
    D, E = "https://lagen.nu/d", "https://lagen.nu/dom/e"
    rows = [(C, "sfs", "law"), (A, "dv", "verdict"), (Z, "forarbete", "sou"),
            (D, "sfs", "law"), (E, "dv", "verdict")]
    for uri, source, kind in rows:
        con.execute("INSERT INTO documents (uri, source, kind, label, title, "
                    "path) VALUES (?, ?, ?, 'L', 'T', '')",
                    (uri, source, kind))
    for f, to in [(A, C), (Z, A), (C, D), (D, E)]:
        con.execute("INSERT INTO links (from_uri, predicate, to_uri, to_root) "
                    "VALUES (?, 'dcterms:references', ?, ?)", (f, to, to))
    catalog.stamp_inbound_counts(con)
    csr = pathgraph.build(con)

    d = reads.graph(con, C, depth=2, limit=10, csr=csr)
    assert d["depth"] == 2
    assert [r["uri"] for r in d["inbound"]["top"]] == [A]
    assert [r["uri"] for r in d["outbound"]["top"]] == [D]
    exp = d["expansion"]
    assert {(n["uri"], n["hop"], n["side"]) for n in exp["nodes"]} \
        == {(Z, 2, "in"), (E, 2, "out")}
    # the induced edges cover the whole view: spokes AND the outer hops
    assert sorted(map(tuple, exp["edges"])) == sorted(
        [(A, C, 1), (Z, A, 1), (C, D, 1), (D, E, 1)])
    # the ring node carries its stamped citedness
    assert {n["uri"]: n["inbound_count"] for n in exp["nodes"]}[E] == 1

    # the group filter gates the rings like it gates hop 1: with only
    # rättsfall + författningar allowed, Z (a sou) falls out of the ring
    d = reads.graph(con, C, depth=2, limit=10, csr=csr,
                    groups={"Rättsfall", "Författningar"})
    assert {n["uri"] for n in d["expansion"]["nodes"]} == {E}

    # depth=1 keeps the old shape: no expansion, full hop-1 budget
    d = reads.graph(con, C, depth=1, limit=10)
    assert d["expansion"] is None and d["depth"] == 1
    con.close()


def test_graph_inbound_ranks_off_the_csr_without_the_join(tmp_path):
    """With the CSR at hand, sort=citations/grouplimit answer from the
    counts-only query + the arrays, labelling only the reply's rows -- the
    12k-citer documents join is what 504:ed depth-2 brottsbalken on prod.
    Ranking is by CSR in-degree; the row still displays the stamped count."""
    con = catalog.connect(tmp_path / "catalog.sqlite")
    law = "https://lagen.nu/1900:1"
    a, b = "https://lagen.nu/dom/nja/A", "https://lagen.nu/dom/nja/B"
    c = "https://lagen.nu/sou/1900:2"
    for uri, source, kind, label in [
            (law, "sfs", "law", "L"), (a, "dv", "verdict", "NJA A"),
            (b, "dv", "verdict", "NJA B"), (c, "forarbete", "sou", "SOU C")]:
        con.execute("INSERT INTO documents (uri, source, kind, label, title, "
                    "path) VALUES (?, ?, ?, ?, 'T', '')",
                    (uri, source, kind, label))
    def link(f, t, n=1):
        for i in range(n):
            con.execute("INSERT INTO links (from_uri, from_anchor, predicate, "
                        "to_uri, to_root) VALUES (?, ?, 'dcterms:references', "
                        "?, ?)", (f, "P%d" % i, t, t))
    link(a, law, 5)
    link(b, law, 1)
    link(c, law, 1)
    link(c, b, 3)        # b is the cited authority (CSR in-degree 1, stamped 3)
    catalog.stamp_inbound_counts(con)
    csr = pathgraph.build(con)

    d = reads.graph(con, law, direction="in", limit=10, sort="citations",
                    csr=csr)
    top = d["inbound"]["top"]
    assert top[0]["uri"] == b and top[0]["inbound_count"] == 3
    assert d["inbound"]["total_docs"] == 3
    capped = reads.graph(con, law, direction="in", limit=10, grouplimit=1,
                         csr=csr)
    assert [r["group"] for r in capped["inbound"]["top"]] \
        == ["Rättsfall", "Förarbeten"]
    filtered = reads.graph(con, law, direction="in", limit=10,
                           groups={"Förarbeten"}, csr=csr)
    assert [r["uri"] for r in filtered["inbound"]["top"]] == [c]
    con.close()


def test_an_empty_groups_filter_fails_visibly(client):
    # `?groups=,` used to survive as an empty set, which the two answer
    # paths read oppositely (filter-everything vs no-filter)
    for path, params in (
            ("/api/v1/graph", {"uri": "https://lagen.nu/1962:700"}),
            ("/api/v1/path", {"from": "https://lagen.nu/1962:700",
                              "to": "https://lagen.nu/2018:585"})):
        r = client.get(path, params={**params, "groups": ","})
        assert r.status_code == 422
        assert "no flow group" in r.json()["detail"]
