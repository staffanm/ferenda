"""The range index (ferenda/lib/rangeindex.py) and /api/v1/range: a client checks
a citation against a bucket of hashes, written here the way the client does it."""

import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from ferenda.api import app as api
from ferenda.lib import catalog, layout, rangeindex, text

BB = "https://lagen.nu/1962:700"
PROP = "https://lagen.nu/prop/1997/98:45"
GDPR = "https://lagen.nu/celex/32016R0679"


def _write(path, art):
    path.write_text(json.dumps(art))
    return path


@pytest.fixture
def corpus(tmp_path):
    """A catalog over three documents, one per anchor grammar, and its sidecar."""
    arts = tmp_path / "artifact"
    arts.mkdir()
    sfs = [_write(arts / "bb.json", {
        "uri": BB, "metadata": {"properties": {"dcterms:title": "Brottsbalk (1962:700)"}},
        "structure": [{"type": "kapitel", "id": "K3", "children": [
            {"type": "paragraf", "id": "K3P1", "text": ["Den som dödar annan döms för mord."],
             "children": [{"type": "stycke", "id": "K3P1S1", "text": ["..."]}]}]}]})]
    forarbete = [_write(arts / "prop.json", {
        "uri": PROP, "metadata": {"properties": {"dcterms:title": "Miljöbalk"}},
        "body": [{"type": "avsnitt", "id": "a4.1", "page": 39, "text": ["Bakgrund"]},
                 {"type": "stycke", "page": 40, "text": ["Löptext."]},
                 # a bilaga restarts its page count: its page 39 is another page
                 {"type": "stycke", "page": 7, "bilaga": "2", "text": ["Bilagetext."]}]})]
    eurlex = [_write(arts / "gdpr.json", {
        "uri": GDPR, "metadata": {"properties": {"dcterms:title": "GDPR"}},
        "structure": [{"type": "recital", "num": "83", "text": ["Skäl."]},
                      {"type": "article", "num": "32", "id": "32", "text": ["Säkerhet"]},
                      {"type": "paragraph", "num": "1", "text": ["Med beaktande av ..."]}]})]
    cat = tmp_path / "catalog.sqlite"
    for source, paths, pinpoints in (("sfs", sfs, ".+"), ("eurlex", eurlex, ".+"),
                                     ("forarbete", forarbete, r"sid\d+")):
        catalog.rebuild(cat, source, paths, pinpoints=pinpoints)
    con = catalog.connect_ro(cat)
    rangeindex.write_sidecar(con, cat)
    con.close()
    return cat


@pytest.fixture
def client(corpus):
    real, layout.CATALOG = layout.CATALOG, corpus
    yield TestClient(api.app)
    layout.CATALOG = real


def _check(client, uri):
    """What Slopcheck does: ``(document held, target held)`` from one request."""
    root = uri.split("#")[0]
    answer = client.get("/api/v1/range/" + hashlib.sha256(root.encode()).hexdigest()[:3])
    assert answer.status_code == 200
    held = set(answer.text.split())
    return (hashlib.sha256(root.encode()).hexdigest()[:16] in held,
            hashlib.sha256(uri.encode()).hexdigest()[:16] in held)


def test_citable_anchors_cover_the_three_grammars(corpus):
    arts = corpus.parent / "artifact"
    assert text.citable_anchors(json.loads((arts / "bb.json").read_text())) == {
        "K3", "K3P1", "K3P1S1"}
    # a page anchor per printed page, none for the bilaga's own count
    assert text.citable_anchors(json.loads((arts / "prop.json").read_text())) == {
        "a4.1", "sid39", "sid40"}
    # the artifact stamps an id on the article only; the rest are derived
    assert text.citable_anchors(json.loads((arts / "gdpr.json").read_text())) >= {
        "32", "32.1", "recital-83"}


@pytest.mark.parametrize("uri, expected", [
    (BB, (True, True)),
    (BB + "#K3P1", (True, True)),
    (BB + "#K3P1S1", (True, True)),
    (BB + "#K3P2", (True, False)),              # the statute is held, the § is not in it
    (PROP + "#sid39", (True, True)),
    (PROP + "#sid7", (True, False)),            # a bilaga page is not "s. 7"
    (PROP + "#a4.1", (True, False)),            # an id, but nothing cites a section
    (GDPR + "#32.1", (True, True)),
    (GDPR + "#recital-83", (True, True)),
    (GDPR + "#99", (True, False)),
    ("https://lagen.nu/1962:701", (False, False)),
    (BB.replace("lagen.nu", "LAGEN.NU"), (False, False)),   # hashed as written, no folding
])
def test_a_client_reads_the_answer_itself(client, uri, expected):
    assert _check(client, uri) == expected


def test_every_answer_has_the_same_shape(client):
    answers = [client.get("/api/v1/range/%03x" % n) for n in (0, 0x7ff, 0xfff,
               rangeindex.prefix(BB), rangeindex.prefix(PROP), rangeindex.prefix(GDPR))]
    assert len({len(a.content) for a in answers}) == 1
    assert len({len(a.text.split()) for a in answers}) == 1
    for a in answers:
        lines = a.text.split()
        assert lines == sorted(lines) and len(set(lines)) == len(lines)
        assert all(len(line) == 16 for line in lines)
        assert "no-transform" in a.headers["cache-control"]


def test_the_answer_is_never_compressed(client):
    answer = client.get("/api/v1/range/000", headers={"accept-encoding": "br, gzip"})
    assert "content-encoding" not in answer.headers
    # ... while an ordinary answer of the same size still is
    assert client.get("/openapi.json", headers={"accept-encoding": "gzip"}
                      ).headers["content-encoding"] == "gzip"


def test_an_answer_is_stable_between_requests(client):
    assert client.get("/api/v1/range/000").text == client.get("/api/v1/range/000").text


@pytest.mark.parametrize("prefix", ["c4a1", "c4", "C4A", "xyz"])
def test_a_malformed_prefix_is_a_422(client, prefix):
    assert client.get("/api/v1/range/" + prefix).status_code == 422


def test_no_sidecar_is_a_503(client, corpus):
    rangeindex.sidecar_path(corpus).unlink()
    assert client.get("/api/v1/range/000").status_code == 503


def test_a_dropped_document_leaves_the_index(corpus):
    (corpus.parent / "artifact" / "bb.json").unlink()
    catalog.rebuild(corpus, "sfs", [])
    con = catalog.connect_ro(corpus)
    assert con.execute("SELECT count(*) FROM range_anchors WHERE uri = ?",
                       (BB,)).fetchone()[0] == 0
    documents, entries, _ = rangeindex.write_sidecar(con, corpus)
    con.close()
    assert documents == 2
    assert rangeindex.entry(BB) not in rangeindex.bucket(corpus, rangeindex.prefix(BB))[0]


def test_a_sidecar_in_another_layout_is_refused(corpus):
    rangeindex.sidecar_path(corpus).write_bytes(b"lagen-rangeindex-0\n" + bytes(64))
    with pytest.raises(AssertionError, match="another layout"):
        rangeindex.bucket(corpus, 0)
