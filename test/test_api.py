"""The REST/OpenAPI service (accommodanda/api/app.py), driven through FastAPI's
TestClient over a fixture catalog + a faked search backend -- no live cluster,
no network."""

import json
import sqlite3

import pytest
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

    yield TestClient(api.app)
    api.app.dependency_overrides.clear()


def test_search(client):
    r = client.get("/api/v1/search", params={"q": "mord", "source": "sfs"})
    assert r.status_code == 200
    body = r.json()
    assert body["query"] == "mord" and body["total"] == 1
    hit = body["results"][0]
    assert hit["identifier"] == "SFS 1962:700"
    assert hit["fragments"][0]["pinpoint"] == "K3P1"
    # the API resolves each hit's hosted page path (layout.page_relpath)
    assert hit["url"] == "/sfs/1962_700.html"


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


def test_document_returns_metadata_and_artifact(client):
    r = client.get("/api/v1/document",
                   params={"uri": "https://lagen.nu/1962:700"})
    assert r.status_code == 200
    body = r.json()
    assert body["title"] == "Brottsbalk (1962:700)"
    assert body["source_url"] == "https://example/bb"
    assert body["inbound_count"] == 1                  # cited by 2018:585
    assert body["artifact"]["structure"][0]["id"] == "K3P1"


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


def test_sources(client):
    r = client.get("/api/v1/sources")
    assert r.json() == [{"source": "sfs", "documents": 2}]


def test_openapi_served(client):
    r = client.get("/openapi.json")
    assert r.status_code == 200
    assert "/api/v1/search" in r.json()["paths"]
