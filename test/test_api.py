"""The REST/OpenAPI service (accommodanda/api/app.py), driven through FastAPI's
TestClient over a fixture catalog + a faked search backend -- no live cluster,
no network."""

import json
import sqlite3

import pytest
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from accommodanda.api import app as api
from accommodanda.lib import catalog


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
        def search(self, q, source=None, kind=None, limit=10, offset=0):
            return {"total": 1, "results": [{
                "uri": "https://lagen.nu/1962:700", "identifier": "SFS 1962:700",
                "title": "Brottsbalk (1962:700)", "source": "sfs", "kind": "law",
                "score": 9.1, "inbound_count": 1,
                "highlight": ["… <em>%s</em> …" % q],
                "fragments": [{"uri": "https://lagen.nu/1962:700#K3P1",
                               "pinpoint": "K3P1", "highlight": ["<em>%s</em>" % q]}]}]}
    api._index = FakeIndex()

    client = TestClient(api.app)
    client.catalog_path = cat            # for tests that add rows directly
    yield client
    api.app.dependency_overrides.clear()


def test_search(client):
    r = client.get("/api/v1/search", params={"q": "mord", "source": "sfs"})
    assert r.status_code == 200
    body = r.json()
    assert body["query"] == "mord" and body["total"] == 1
    hit = body["results"][0]
    assert hit["identifier"] == "SFS 1962:700"
    assert hit["fragments"][0]["pinpoint"] == "K3P1"
    # the API resolves each hit's public page path (layout.page_url): a statute
    # at lagen.nu's bare /<sfsid> address, colon kept
    assert hit["url"] == "/1962:700"


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
    rows = r.json()
    assert [c["uri"] for c in rows] == ["https://lagen.nu/2018:585"]
    assert rows[0]["label"] == "SFS 2018:585"


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
                               "pre": "", "key": "Förvaltningslag (2018:585)",
                               "subdued": False, "year": "2018"}]


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


def test_openapi_served(client):
    r = client.get("/openapi.json")
    assert r.status_code == 200
    assert "/api/v1/search" in r.json()["paths"]
