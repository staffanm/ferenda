"""The Oblivious HTTP gateway (ferenda/api/ohttp.py): the cryptography against
the complete example in RFC 9458 appendix A, and the gateway's own rules through
a TestClient, with pyhpke playing the client."""

import json

import pytest
from cryptography.hazmat.primitives import hashes, hmac
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDFExpand
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from ferenda import config
from ferenda.api import app as api
from ferenda.api import ohttp

# RFC 9458 appendix A
RFC_SECRET_KEY = "3c168975674b2fa8e465970b79c8dcf09f1c741626480bd4c6162fc5b6a98e1a"
RFC_PUBLIC_KEY = "31e1f05a740102115220e9af918f738674aec95f54db6e04eb705aae8e798155"
RFC_BHTTP_REQUEST = "00034745540568747470730b6578616d706c652e636f6d012f"
RFC_ENCAPSULATED_REQUEST = (
    "010020000100014b28f881333e7c164ffc499ad9796f877f4e1051ee6d31bad1"
    "9dec96c208b4726374e469135906992e1268c594d2a10c695d858c40a026e796"
    "5e7d86b83dd440b2c0185204b4d63525")
RFC_BHTTP_RESPONSE = "0140c8"
RFC_RESPONSE_NONCE = "c789e7151fcba46158ca84b04464910d"
RFC_ENCAPSULATED_RESPONSE = (
    "c789e7151fcba46158ca84b04464910d86f9013e404feea014e7be4a441f234f"
    "857fbd")


@pytest.fixture
def keyfile(tmp_path, monkeypatch):
    """A key file holding the RFC's key as key 1, configured."""
    path = tmp_path / "ohttp-keys.json"
    path.write_text(json.dumps([{"key_id": 1, "private_key": RFC_SECRET_KEY}]))
    monkeypatch.setattr(config, "OHTTP_KEYS_FILE", path)
    ohttp._keys.cache_clear()
    yield path
    ohttp._keys.cache_clear()


@pytest.fixture
def gateway(keyfile):
    """The gateway beside a stand-in for the routes it serves: the stand-in
    answers with the headers it was sent, so a test can see what got through."""
    app = FastAPI()
    app.include_router(ohttp.router)

    @app.get("/api/v1/range/{prefix}")
    def stand_in(prefix: str, request: Request):
        return {"prefix": prefix, "headers": sorted(request.headers.keys())}

    @app.get("/api/v1/packs/core")
    def pack_stand_in(request: Request):
        return {"pack": "core", "headers": sorted(request.headers.keys())}

    @app.get("/api/v1/document")
    def outside():
        return {"reached": True}

    return TestClient(app)


def _field(value):
    return ohttp._encode_varint(len(value)) + value


def _bhttp_request(method, path, headers=()):
    section = b"".join(_field(name) + _field(value) for name, value in headers)
    return (ohttp._encode_varint(0) + _field(method) + _field(b"https")
            + _field(b"lagen.nu") + _field(path) + _field(section))


def _seal(message, key_id=1):
    """What a client does: ``(encapsulated request, enc, context)``."""
    header = (bytes([key_id]) + ohttp.KEM_ID.to_bytes(2) + ohttp.KDF_ID.to_bytes(2)
              + ohttp.AEAD_ID.to_bytes(2))
    public = ohttp.SUITE.kem.deserialize_public_key(bytes.fromhex(RFC_PUBLIC_KEY))
    enc, context = ohttp.SUITE.create_sender_context(
        public, info=b"message/bhttp request\x00" + header)
    return header + enc + context.seal(message), enc, context


def _open(blob, enc, context):
    """The client's half of `ohttp.seal_response`: ``(status, headers, content,
    whole padded message)``."""
    nonce, sealed = blob[:16], blob[16:]
    extract = hmac.HMAC(enc + nonce, hashes.SHA256())
    extract.update(context.export(b"message/bhttp response", 16))
    prk = extract.finalize()
    message = AESGCM(HKDFExpand(hashes.SHA256(), 16, b"key").derive(prk)).decrypt(
        HKDFExpand(hashes.SHA256(), 12, b"nonce").derive(prk), sealed, b"")
    framing, pos = ohttp._varint(message, 0)
    assert framing == 1
    status, pos = ohttp._varint(message, pos)
    section, pos = ohttp._field(message, pos)
    content, pos = ohttp._field(message, pos)
    headers, at = {}, 0
    while at < len(section):
        name, at = ohttp._field(section, at)
        headers[name], at = ohttp._field(section, at)
    assert not any(message[pos:]), "the padding is zero bytes"
    return status, headers, content, message


def _post(gateway, content, ctype="message/ohttp-req"):
    return gateway.post("/api/v1/ohttp-gateway", content=content,
                        headers={"content-type": ctype})


def _ask(gateway, method, path, headers=()):
    blob, enc, context = _seal(_bhttp_request(method, path, headers))
    answer = _post(gateway, blob)
    assert answer.status_code == 200
    assert answer.headers["content-type"] == "message/ohttp-res"
    assert answer.headers["cache-control"] == "no-store"
    return _open(answer.content, enc, context)


def test_rfc9458_example_opens_and_seals_byte_for_byte(keyfile):
    message, enc, context = ohttp.open_request(bytes.fromhex(RFC_ENCAPSULATED_REQUEST))
    assert message.hex() == RFC_BHTTP_REQUEST
    assert ohttp.decode_request(message) == ("GET", "/", [])
    sealed = ohttp.seal_response(bytes.fromhex(RFC_BHTTP_RESPONSE), enc, context,
                                 nonce=bytes.fromhex(RFC_RESPONSE_NONCE))
    assert sealed.hex() == RFC_ENCAPSULATED_RESPONSE


def test_keys_are_published_length_prefixed(gateway):
    answer = gateway.get("/api/v1/ohttp-keys")
    assert answer.headers["content-type"] == "application/ohttp-keys"
    assert "max-age=86400" in answer.headers["cache-control"]
    # 2-byte length, then key id 1, X25519, the key, one 4-byte suite list
    assert answer.content.hex() == ("0029" "01" "0020" + RFC_PUBLIC_KEY
                                    + "0004" "0001" "0001")


def test_inner_request_is_served_in_process_and_padded(gateway):
    status, headers, content, message = _ask(gateway, b"GET", b"/api/v1/range/c4a")
    assert status == 200
    assert headers[b"content-type"].startswith(b"application/json")
    assert json.loads(content)["prefix"] == "c4a"
    assert len(message) % ohttp.PAD_BLOCK == 0


def test_only_accept_reaches_the_inner_route(gateway):
    _, _, content, _ = _ask(gateway, b"GET", b"/api/v1/range/c4a", headers=[
        (b"accept", b"application/json"), (b"authorization", b"Bearer x"),
        (b"cookie", b"lagen_editor=x"), (b"x-forwarded-for", b"10.0.0.1"),
        (b"accept-encoding", b"br")])
    assert json.loads(content)["headers"] == ["accept", "host"]


@pytest.mark.parametrize("path", [
    b"/api/v1/document", b"/internal-api/v1/auth/me", b"/ops",
    b"/api/v1/range/../document", b"/api/v1/range/c4a?uri=x", b"/api/v1/rangefinder/x"])
def test_a_path_outside_range_and_packs_is_refused_inside_the_seal(gateway, path):
    status, _, content, _ = _ask(gateway, b"GET", path)
    assert status == 403
    assert "range" in json.loads(content)["detail"]


def test_a_mutating_method_is_refused_inside_the_seal(gateway):
    status, _, _, _ = _ask(gateway, b"POST", b"/api/v1/range/c4a")
    assert status == 405


def test_a_missing_inner_resource_is_a_sealed_404(gateway):
    status, _, _, _ = _ask(gateway, b"GET", b"/api/v1/packs/missing")
    assert status == 404


def test_pack_served_through_gateway_passes_accept_encoding(gateway):
    status, _, content, _ = _ask(gateway, b"GET", b"/api/v1/packs/core", headers=[
        (b"accept", b"application/json"), (b"accept-encoding", b"br")])
    assert status == 200
    res = json.loads(content)
    assert res["pack"] == "core"
    assert res["headers"] == ["accept", "accept-encoding", "host"]


def test_outer_errors(gateway):
    blob, _, _ = _seal(_bhttp_request(b"GET", b"/api/v1/range/c4a"))
    assert _post(gateway, blob, "application/json").status_code == 415
    unknown = _post(gateway, bytes([9]) + blob[1:])
    assert unknown.status_code == 400 and "key-not-found" in unknown.json()["detail"]
    assert _post(gateway, blob[:-1] + bytes([blob[-1] ^ 1])).status_code == 400   # bad tag
    assert _post(gateway, blob[:20]).status_code == 400                            # too short
    assert _post(gateway, blob[:3] + b"\x00\x02" + blob[5:]).status_code == 400    # other KDF
    assert _post(gateway, blob + bytes(ohttp.MAX_REQUEST_BYTES)).status_code == 413
    # opens, but is not a Binary HTTP request
    assert _post(gateway, _seal(b"\x01\x40\xc8")[0]).status_code == 400


def test_gateway_endpoints_disabled_in_app(keyfile):
    client = TestClient(api.app)
    assert client.get("/api/v1/ohttp-keys").status_code == 404
    assert _post(client, b"x").status_code == 404


def test_unconfigured_gateway_yields_404(monkeypatch):
    monkeypatch.setattr(config, "OHTTP_KEYS_FILE", None)
    ohttp._keys.cache_clear()
    app = FastAPI()
    app.include_router(ohttp.router)
    client = TestClient(app)
    assert client.get("/api/v1/ohttp-keys").status_code == 404
    assert _post(client, b"x").status_code == 404


def test_keygen_puts_the_new_key_first_and_keeps_the_old(tmp_path, capsys):
    path = tmp_path / "keys.json"
    ohttp._main(["keygen", str(path)])
    ohttp._main(["keygen", str(path)])
    entries = json.loads(path.read_text())
    assert [entry["key_id"] for entry in entries] == [2, 1]
    assert len({entry["private_key"] for entry in entries}) == 2
    assert path.stat().st_mode & 0o777 == 0o600
    assert "restart" in capsys.readouterr().out
