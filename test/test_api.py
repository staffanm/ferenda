"""The REST/OpenAPI service (accommodanda/api/app.py), driven through FastAPI's
TestClient over a fixture catalog + a faked search backend -- no live cluster,
no network."""

import json
import sqlite3

import pytest
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
from opensearchpy.exceptions import ConnectionError as OpenSearchConnectionError

from accommodanda import config
from accommodanda.api import app as api
from accommodanda.api import db
from accommodanda.lib import catalog, compress, inbound


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
                   cursor=None):
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
                "fragments": [{"uri": "https://lagen.nu/1962:700#K3P1",
                               "pinpoint": "K3P1", "highlight": ["<em>%s</em>" % q]}]}]}
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
    assert hit["fragments"][0]["pinpoint"] == "K3P1"
    # the API resolves each hit's public page path (layout.page_url): a statute
    # at lagen.nu's bare /<sfsid> address, colon kept
    assert hit["url"] == "/1962:700"


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


def test_document_returns_metadata_and_artifact(client):
    r = client.get("/api/v1/document",
                   params={"uri": "https://lagen.nu/1962:700"})
    assert r.status_code == 200
    body = r.json()
    assert body["title"] == "Brottsbalk (1962:700)"
    assert body["source_url"] == "https://example/bb"
    assert body["inbound_count"] == 1                  # cited by 2018:585
    assert body["artifact"]["structure"][0]["id"] == "K3P1"


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


def test_search_pins_a_pinpoint_citation_with_its_provision(client):
    # the reader's query names a provision, so the hit says which provision it
    # landed on and shows that provision's own words -- "Brottsbalk (1962:700)"
    # alone gave no sign the pin had worked (Q2)
    body = client.get("/api/v1/search",
                      params={"q": "3 kap. 1 § brottsbalken"}).json()
    hit = body["results"][0]
    assert hit["uri"] == "https://lagen.nu/1962:700"
    frag = hit["fragments"][0]
    assert frag["pinpoint"] == "K3P1"
    assert frag["label"] == "3 kap. 1 §"
    assert frag["highlight"] == ["Den som dödar annan döms för mord."]


def test_search_pins_the_terse_law_first_pinpoint(client):
    # "BrB 3:1" is how a lawyer types it; the grammar wants "3 kap. 1 §"
    frag = client.get("/api/v1/search", params={"q": "BrB 3:1"}) \
        .json()["results"][0]["fragments"][0]
    assert frag["pinpoint"] == "K3P1" and frag["label"] == "3 kap. 1 §"


def test_search_explicit_offset_walks_raw_without_the_pin(client):
    # an explicit offset -- 0 included -- is raw bounded random access: no
    # pinned lead (it would push the page's last raw hit past the boundary
    # offset=limit resumes from) and no related-hit cap. The offsetless first
    # page keeps the pin.
    q = {"q": "3 kap. 1 § brottsbalken"}
    with_pin = client.get("/api/v1/search", params=q).json()
    raw = client.get("/api/v1/search", params={**q, "offset": 0}).json()
    assert with_pin["results"][0]["fragments"][0]["pinpoint"] == "K3P1"
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

    # a fragment uri answers for that provision alone and adds the (here
    # empty) internal unit graph, with the reader-facing pinpoint label --
    # and a deep arrival anchor (#K3P1S2) answers for the § its `pinpoint`
    # names, not the stycke subtree alone
    for frag in ("K3P1", "K3P1S2"):
        d = client.get("/api/v1/graph",
                       params={"uri": "https://lagen.nu/1962:700#" + frag}).json()
        assert d["pinpoint"] == "3 kap. 1 §" and d["unit"] == "K3P1"
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
