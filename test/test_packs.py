"""The document packs API (ferenda/lib/packs.py) and GET /api/v1/packs/{pack_id}:
testing deterministic pack resolution, caching, and serving."""

import json
import sqlite3

import brotli
import pytest
from fastapi.testclient import TestClient

from ferenda.api import app as api
from ferenda.lib import compress, layout, packs

SFS_URI = "https://lagen.nu/1998:204"
CELEX1_URI = "https://lagen.nu/celex/12012M/TXT"
CELEX3_URI = "https://lagen.nu/celex/32016R0679"
CELEX6_URI = "https://lagen.nu/celex/62019CJ0311"
NJA_URI = "https://lagen.nu/dom/nja/2021s100"
SOU_URI = "https://lagen.nu/sou/1997:1"
PROP_URI = "https://lagen.nu/prop/1997/98:45"
DS_URI = "https://lagen.nu/ds/2024:1"


def _write_art(data_dir, path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    compress.write_json(path, data)
    return str(path.relative_to(data_dir))


@pytest.fixture
def corpus(tmp_path):
    """A small test corpus with documents across sfs, eurlex, dv, and forarbete."""
    data_dir = tmp_path / "data"
    art_dir = data_dir / "artifact"
    cat_path = data_dir / "catalog.sqlite"
    cat_path.parent.mkdir(parents=True, exist_ok=True)

    # create artifact files
    p_sfs = _write_art(data_dir, art_dir / "sfs" / "1998" / "204.json", {
        "uri": SFS_URI, "title": "Personuppgiftslag", "structure": [{"type": "paragraf", "id": "P1"}]
    })
    p_celex1 = _write_art(data_dir, art_dir / "eurlex" / "12012M_TXT.json", {
        "uri": CELEX1_URI, "title": "EU-fördraget", "structure": []
    })
    p_celex3 = _write_art(data_dir, art_dir / "eurlex" / "32016R0679.json", {
        "uri": CELEX3_URI, "title": "GDPR", "structure": [{"type": "article", "id": "32"}]
    })
    p_celex6 = _write_art(data_dir, art_dir / "eurlex" / "62019CJ0311.json", {
        "uri": CELEX6_URI, "title": "Schrems II", "structure": []
    })
    p_nja = _write_art(data_dir, art_dir / "dom" / "nja" / "2021s100.json", {
        "uri": NJA_URI, "title": "NJA 2021 s. 100", "body": []
    })
    p_sou = _write_art(data_dir, art_dir / "forarbete" / "sou" / "1997_1.json", {
        "uri": SOU_URI, "title": "SOU 1997:1", "body": []
    })
    p_prop = _write_art(data_dir, art_dir / "forarbete" / "prop" / "1997_98_45.json", {
        "uri": PROP_URI, "title": "Prop. 1997/98:45", "body": []
    })
    p_ds = _write_art(data_dir, art_dir / "forarbete" / "ds" / "2024_1.json", {
        "uri": DS_URI, "title": "Ds 2024:1", "body": []
    })

    # populate catalog.sqlite
    con = sqlite3.connect(cat_path)
    con.execute("""
        CREATE TABLE documents (
            uri TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            kind TEXT,
            title TEXT,
            path TEXT NOT NULL,
            date TEXT,
            inbound_count INTEGER
        )
    """)
    rows = [
        (SFS_URI, "sfs", "law", "Personuppgiftslag", p_sfs, "1998-04-29", 100),
        (CELEX1_URI, "eurlex", "treaty", "EU-fördraget", p_celex1, "2012-10-26", 50),
        (CELEX3_URI, "eurlex", "regulation", "GDPR", p_celex3, "2016-04-27", 300),
        (CELEX6_URI, "eurlex", "case", "Schrems II", p_celex6, "2020-07-16", 20),
        (NJA_URI, "dv", "case", "NJA 2021 s. 100", p_nja, "2021-03-15", 15),
        (SOU_URI, "forarbete", "sou", "SOU 1997:1", p_sou, "1997-01-10", 5),
        (PROP_URI, "forarbete", "prop", "Prop. 1997/98:45", p_prop, "1997-12-04", 10),
        (DS_URI, "forarbete", "ds", "Ds 2024:1", p_ds, "2024-02-01", 1),
    ]
    con.executemany("INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    con.commit()
    con.close()

    return data_dir, cat_path


def test_resolve_pack_documents_patterns(corpus):
    _, cat_path = corpus
    con = sqlite3.connect(cat_path)

    # 1. core: ordered by inbound_count
    core_docs = packs.resolve_pack_documents(con, "core")
    assert [uri for uri, _ in core_docs[:3]] == [CELEX3_URI, SFS_URI, CELEX1_URI]

    # 2. sfs decade
    sfs_docs = packs.resolve_pack_documents(con, "sfs/1990s")
    assert [uri for uri, _ in sfs_docs] == [SFS_URI]
    assert packs.resolve_pack_documents(con, "sfs/2000s") == []

    # 3. celex/1
    celex1 = packs.resolve_pack_documents(con, "celex/1")
    assert [uri for uri, _ in celex1] == [CELEX1_URI]

    # 4. celex sector + year
    celex3 = packs.resolve_pack_documents(con, "celex/3/2016")
    assert [uri for uri, _ in celex3] == [CELEX3_URI]
    celex6 = packs.resolve_pack_documents(con, "celex/6/2019")
    assert [uri for uri, _ in celex6] == [CELEX6_URI]

    # 5. court block
    nja_block = packs.resolve_pack_documents(con, "dom/nja/2020-2024")
    assert [uri for uri, _ in nja_block] == [NJA_URI]
    nja_short = packs.resolve_pack_documents(con, "nja/2020-2024")
    assert [uri for uri, _ in nja_short] == [NJA_URI]

    # 6. förarbeten
    sou_doc = packs.resolve_pack_documents(con, "sou/1997")
    assert [uri for uri, _ in sou_doc] == [SOU_URI]
    prop_year = packs.resolve_pack_documents(con, "prop/1997")
    assert [uri for uri, _ in prop_year] == [PROP_URI]
    prop_sess = packs.resolve_pack_documents(con, "prop/1997-98")
    assert [uri for uri, _ in prop_sess] == [PROP_URI]
    ds_doc = packs.resolve_pack_documents(con, "ds/2024")
    assert [uri for uri, _ in ds_doc] == [DS_URI]

    # 7. unknown/invalid
    assert packs.resolve_pack_documents(con, "unknown/pack") == []
    assert packs.resolve_pack_documents(con, "../invalid") == []

    con.close()


def test_assemble_and_cache_pack(corpus, tmp_path):
    data_dir, cat_path = corpus
    cache_dir = tmp_path / "cache" / "packs"
    pack_file = packs.get_cached_pack(cat_path, data_dir, cache_dir, "celex/3/2016")
    assert pack_file is not None and pack_file.exists()

    # check payload
    raw = brotli.decompress(pack_file.read_bytes())
    data = json.loads(raw)
    assert data["pack"] == "celex/3/2016"
    assert CELEX3_URI in data["documents"]
    assert data["documents"][CELEX3_URI]["title"] == "GDPR"

    # repeated call returns cached file immediately
    assert packs.get_cached_pack(cat_path, data_dir, cache_dir, "celex/3/2016") == pack_file


@pytest.fixture
def client(corpus, tmp_path, monkeypatch):
    data_dir, cat_path = corpus
    cache_dir = tmp_path / "cache" / "packs"
    monkeypatch.setattr(layout, "CATALOG", cat_path)
    monkeypatch.setattr(layout, "DATA", data_dir)
    monkeypatch.setattr(layout, "PACKS_CACHE", cache_dir)
    return TestClient(api.app)


def test_pack_endpoint_serves_brotli_when_accepted(client):
    res = client.get("/api/v1/packs/core", headers={"accept-encoding": "br"})
    assert res.status_code == 200
    assert res.headers["content-encoding"] == "br"
    assert res.headers["content-type"].startswith("application/json")
    data = res.json()
    assert data["pack"] == "core"
    assert CELEX3_URI in data["documents"]


def test_pack_endpoint_serves_plain_json_when_no_brotli(client):
    res = client.get("/api/v1/packs/sfs/1990s", headers={"accept-encoding": "identity"})
    assert res.status_code == 200
    assert "content-encoding" not in res.headers
    data = res.json()
    assert data["pack"] == "sfs/1990s"
    assert SFS_URI in data["documents"]


def test_pack_endpoint_404_on_missing_pack(client):
    res = client.get("/api/v1/packs/sfs/1800s")
    assert res.status_code == 404
    assert "pack not found" in res.json()["detail"]


def test_pack_endpoint_400_on_invalid_pack_id(client):
    res = client.get("/api/v1/packs/..%2Fescaped")
    assert res.status_code in (400, 404)


def test_pack_endpoint_503_when_catalog_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(layout, "CATALOG", tmp_path / "missing.sqlite")
    monkeypatch.setattr(layout, "PACKS_CACHE", tmp_path / "cache" / "packs")
    c = TestClient(api.app)
    res = c.get("/api/v1/packs/core")
    assert res.status_code == 503
