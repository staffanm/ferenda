"""The unit index (ferenda/lib/unitindex.py) behind /api/v1/range/filter and
/api/v1/range/units: each document and provision keyed by its own uri hash, read
here the way a client reads it."""

import hashlib
import json
import struct
import zlib

import pytest
from fastapi.testclient import TestClient

from ferenda.api import app as api
from ferenda.lib import catalog, fusefilter, layout, unitindex

BB = "https://lagen.nu/1962:700"
PROP = "https://lagen.nu/prop/1997/98:45"
GDPR = "https://lagen.nu/celex/32016R0679"
NJA = "https://lagen.nu/dom/nja/2013s372"
PINPOINTS = {"sfs": ".+", "eurlex": ".+", "forarbete": r"sid\d+", "dv": None}


def _write(path, art):
    path.write_text(json.dumps(art))
    return path


@pytest.fixture
def corpus(tmp_path):
    """A catalog over four documents -- a statute, a proposition, an EU act and a
    judgment with no anchors -- and its unit index."""
    arts = tmp_path / "artifact"
    arts.mkdir()
    sfs = [_write(arts / "bb.json", {
        "uri": BB, "metadata": {"properties": {"dcterms:title": "Brottsbalk (1962:700)"}},
        "structure": [{"type": "kapitel", "id": "K3", "children": [
            {"type": "paragraf", "id": "K3P1", "text": ["Den som dödar annan döms för mord."]}]}]})]
    forarbete = [_write(arts / "prop.json", {
        "uri": PROP, "metadata": {"properties": {"dcterms:title": "Miljöbalk"}},
        "body": [{"type": "avsnitt", "id": "a4.1", "page": 39, "text": ["Bakgrund"],
                  "children": [{"type": "stycke", "text": ["Text på sidan 39."]}]},
                 {"type": "stycke", "page": 40, "text": ["Text på sidan 40."]}]})]
    eurlex = [_write(arts / "gdpr.json", {
        "uri": GDPR, "metadata": {"properties": {"dcterms:title": "GDPR"}},
        "structure": [{"type": "recital", "num": "26", "text": ["Principerna bör gälla."]},
                      {"type": "article", "num": "6", "id": "6", "text": ["Laglig behandling"]},
                      {"type": "paragraph", "num": "1", "text": ["Behandling är laglig om"]},
                      {"type": "point", "num": "a", "text": ["samtycke har lämnats,"]},
                      {"type": "paragraph", "num": "2", "text": ["Medlemsstaterna får"]}]})]
    dv = [_write(arts / "nja.json", {
        "uri": NJA, "metadata": {"properties": {"dcterms:title": "NJA 2013 s. 372"}},
        "body": [{"type": "stycke", "text": ["Högsta domstolen fastställer hovrättens dom."]}]})]
    cat = tmp_path / "catalog.sqlite"
    for source, paths in (("sfs", sfs), ("eurlex", eurlex), ("forarbete", forarbete), ("dv", dv)):
        catalog.rebuild(cat, source, paths, pinpoints=PINPOINTS[source])
    unitindex.update(cat, PINPOINTS)
    return cat


@pytest.fixture
def client(corpus):
    real, layout.CATALOG = layout.CATALOG, corpus
    yield TestClient(api.app)
    layout.CATALOG = real


def _key(uri):
    return int.from_bytes(hashlib.sha256(uri.encode()).digest()[:8], "big")


def _decode(data):
    """What a client does with a content answer: ``{suffix: (uri, text or marker)}``."""
    assert data[:4] == b"LUR1"
    bits, count = struct.unpack_from("<BI", data, 4)
    at, out = 9, {}
    for _ in range(count):
        suffix, ulen, tlen = struct.unpack_from("<IHI", data, at)
        at += 10
        uri = data[at:at + ulen].decode()
        at += ulen
        if tlen in (unitindex.NO_TEXT, unitindex.NO_TEXT_OVER_CAP):
            out[suffix] = (uri, tlen)
        else:
            out[suffix] = (uri, zlib.decompress(data[at:at + tlen], -15).decode())
            at += tlen
    assert not any(data[at:])                     # the rest is padding
    return bits, out


def _fetch(client, uri, bits=16):
    k = _key(uri)
    width = -(-bits // 4)
    hexprefix = "%0*x" % (width, (k >> (64 - bits)) << (4 * width - bits))
    answer = client.get("/api/v1/range/units/%s?bits=%d&content=true" % (hexprefix, bits))
    assert answer.status_code == 200
    got_bits, units = _decode(answer.content)
    assert got_bits == bits
    return units.get((k >> (64 - bits - 32)) & 0xFFFFFFFF), answer


@pytest.mark.parametrize("uri, held", [
    (BB, True), (BB + "#K3P1", True), (BB + "#K3P2", False),
    (PROP + "#sid39", True), (PROP + "#a4.1", False),
    (GDPR + "#6.1", True), (GDPR + "#6.1.a", True), (GDPR + "#recital-26", True), (GDPR + "#99", False),
    (NJA, True), ("https://lagen.nu/1962:701", False),
])
def test_the_filter_holds_every_unit_and_nothing_else(client, uri, held):
    answer = client.get("/api/v1/range/filter")
    assert answer.status_code == 200 and "no-transform" in answer.headers["cache-control"]
    assert (_key(uri) in fusefilter.Filter(answer.content)) is held


def test_the_filter_lists_the_most_cited_units(corpus):
    data = unitindex.filter_path(corpus).read_bytes()
    end = fusefilter.Filter(data).end
    (count,) = struct.unpack_from("<I", data, end)
    assert len(data) == end + 4 + 8 * count


@pytest.mark.parametrize("uri, expected", [
    (BB + "#K3P1", "Den som dödar annan döms för mord."),
    (PROP + "#sid39", "Bakgrund"),                       # the heading and ...
    (PROP + "#sid39", "Text på sidan 39."),              # ... the text under it on the page
    (GDPR + "#6.1", "samtycke har lämnats,"),            # a paragraph carries its points
    (GDPR + "#recital-26", "Principerna bör gälla."),
    (NJA, "Högsta domstolen fastställer hovrättens dom."),   # no anchors: the whole text
])
def test_a_unit_answers_with_its_own_text(client, uri, expected):
    found, _ = _fetch(client, uri)
    assert found and found[0] == uri
    assert expected in found[1]


def test_a_paragraph_stops_at_the_next(client):
    found, _ = _fetch(client, GDPR + "#6.1")
    assert "Medlemsstaterna får" not in found[1]


def test_a_document_with_anchors_carries_no_text(client):
    found, _ = _fetch(client, BB)
    assert found == (BB, unitindex.NO_TEXT)


@pytest.mark.parametrize("bits", [12, 13, 16, 20])
def test_every_answer_of_a_prefix_length_has_one_size(client, bits):
    sizes = {len(_fetch(client, uri, bits)[1].content) for uri in (BB, PROP + "#sid39", GDPR, NJA)}
    width = -(-bits // 4)
    sizes.add(len(client.get("/api/v1/range/units/%s?bits=%d&content=true" % ("0" * width, bits)).content))
    assert len(sizes) == 1


def test_the_answer_without_text_is_padded_suffixes(client):
    answers = [client.get("/api/v1/range/units/%04x" % n) for n in (0, 0xffff, _key(BB) >> 48)]
    lines = [a.text.split() for a in answers]
    assert len({len(x) for x in lines}) == 1
    assert "%08x" % ((_key(BB) >> 16) & 0xFFFFFFFF) in lines[2]
    assert all(x == sorted(x) and all(len(e) == 8 for e in x) for x in lines)


@pytest.mark.parametrize("path", ["units/c4a", "units/c4a1f", "units/xyz1", "units/c4a1?bits=11",
                                  "units/c4a1?bits=21", "units/c4a?bits=13"])
def test_a_malformed_request_is_a_422(client, path):
    assert client.get("/api/v1/range/" + path).status_code == 422


def test_the_legacy_range_route_still_answers(client, corpus):
    assert client.get("/api/v1/range/000").status_code in (200, 503)


def test_no_store_is_a_503(client, corpus):
    unitindex.store_path(corpus).unlink()
    unitindex.filter_path(corpus).unlink()
    assert client.get("/api/v1/range/units/0000").status_code == 503
    assert client.get("/api/v1/range/filter").status_code == 503


def test_an_update_rewrites_only_changed_documents(corpus):
    arts = corpus.parent / "artifact"
    art = json.loads((arts / "nja.json").read_text())
    art["body"][0]["text"] = ["Högsta domstolen ändrar hovrättens dom."]
    _write(arts / "nja.json", art)
    catalog.rebuild(corpus, "dv", [arts / "nja.json"], pinpoints=None)
    assert unitindex.update(corpus, PINPOINTS)[1] == 1
    (arts / "bb.json").unlink()
    catalog.rebuild(corpus, "sfs", [], pinpoints=".+")
    units, rewritten = unitindex.update(corpus, PINPOINTS)
    assert rewritten == 1
    data = unitindex.filter_path(corpus).read_bytes()
    assert _key(BB + "#K3P1") not in fusefilter.Filter(data)


def test_a_code_change_rebuilds_unless_code_changes_are_ignored(corpus, monkeypatch):
    monkeypatch.setattr(unitindex, "code_version", lambda: "changed")
    assert unitindex.update(corpus, PINPOINTS, ignore_code=True)[1] == 0
    assert unitindex.update(corpus, PINPOINTS)[1] == 4        # every document again
    assert unitindex.update(corpus, PINPOINTS)[1] == 0
