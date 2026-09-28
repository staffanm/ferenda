"""The Oblivious HTTP gateway (RFC 9458): `/api/v1/ohttp-keys` and
`/api/v1/ohttp-gateway`.

NOTE: These endpoints are currently disabled in the API (not mounted in
`ferenda.api.app`) due to the lack of a usable OHTTP relay. The code is
preserved here, but it is untested in the real world.

A client that must not show us which citation it checks sends its request
through a relay. The relay sees the client's address and an opaque blob; we see
the request and the relay's address. Neither sees both.

The blob is a Binary HTTP request (RFC 9292) sealed with HPKE to one of our
public keys. The gateway opens it, runs the inner request against this same app
*in process* -- it never opens a socket, so an inner request cannot reach
another host -- and seals the answer with a key derived from the same HPKE
context.

Only `INNER_PATH` is served. Dispatching in process passes around everything
nginx enforces in front of the app (rate limits, the facsimile render gate), so
the gateway serves the two routes built for it and nothing else. The inner
request carries no header of the client's except `Accept` (and `Accept-Encoding`
for `/api/v1/packs/…`, so a pack travels compressed). For `/api/v1/range/…`,
compression is never used: a compressed body's length follows its content,
which is what the fixed-size range answer exists to hide.

An error the relay may see (bad media type, unknown key, a blob that does not
open) is an ordinary HTTP status. An error in the inner request (a path outside
`INNER_PATH`, a 404 from the route) travels sealed, inside a 200, so the relay
cannot tell a hit from a miss.

Keys live in the JSON file `config.OHTTP_KEYS_FILE` names, newest first:
``[{"key_id": 2, "private_key": "<64 hex>"}, {"key_id": 1, ...}]``. Every
listed key opens requests and is published. To rotate, run
``python -m ferenda.api.ohttp keygen <file>``, restart the service, and remove
the old entry 72 hours later -- a client may hold the old configuration for
`KEYS_MAX_AGE`. With the setting unset, both routes answer 404.
"""

import asyncio
import functools
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from cryptography.hazmat.primitives import hashes, hmac
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDFExpand
from fastapi import APIRouter, HTTPException, Request, Response
from pyhpke import AEADId, CipherSuite, KDFId, KEMId, OpenError

from .. import config

# The endpoints below are implemented per RFC 9458, but disabled in the
# supported API and untested in the real world (no usable OHTTP relay).
router = APIRouter(prefix="/api/v1", tags=["ohttp"])

# the one suite we publish: DHKEM(X25519, HKDF-SHA256), HKDF-SHA256, AES-128-GCM
KEM_ID, KDF_ID, AEAD_ID = 0x0020, 0x0001, 0x0001
SUITE = CipherSuite.new(KEMId.DHKEM_X25519_HKDF_SHA256, KDFId.HKDF_SHA256,
                        AEADId.AES128_GCM)
NK, NN, NENC = 16, 12, 32          # AES-128-GCM key + nonce, X25519 public key
HEADER_LEN = 7                     # key_id(1) kem_id(2) kdf_id(2) aead_id(2)

# an encapsulated GET is a few hundred bytes; this bounds what an anonymous
# POST can make us hold before the key lookup rejects it
MAX_REQUEST_BYTES = 8192
# both directions are padded to a multiple of this before sealing (RFC 9292
# section 3.8), so a ciphertext's length says little about the message in it
PAD_BLOCK = 256
# how long a client may cache /ohttp-keys. Shorter than the time a rotated-out
# key stays in the file, or a client holding the old configuration gets a 400.
KEYS_MAX_AGE = 86400

# the one query the routes take: /api/v1/range/units' prefix length and whether
# to send text (?bits=16&content=true). Any other parameter is refused.
_QUERY_PARAM = r"(?:bits=\d{1,2}|content=(?:true|false))"
INNER_PATH = re.compile(r"^/api/v1/(?:range|packs)/[A-Za-z0-9/_-]+"
                        r"(?:\?%s(?:&%s)?)?$" % (_QUERY_PARAM, _QUERY_PARAM))
INNER_METHODS = ("GET", "HEAD")


# --------------------------------------------------------------------------
# keys
# --------------------------------------------------------------------------

@functools.cache
def _keys():
    """``{key_id: (pyhpke private key, raw public key)}``, newest first. Read
    once per process: a rotation needs a restart, which a deploy does anyway."""
    if config.OHTTP_KEYS_FILE is None:
        raise HTTPException(404, "the Oblivious HTTP gateway is not configured")
    entries = json.loads(config.OHTTP_KEYS_FILE.read_text(encoding="utf-8"))
    assert entries, "%s lists no key" % config.OHTTP_KEYS_FILE
    keys = {}
    for entry in entries:
        raw = bytes.fromhex(entry["private_key"])
        public = X25519PrivateKey.from_private_bytes(raw).public_key()
        assert 0 <= entry["key_id"] <= 255 and entry["key_id"] not in keys, \
            "key_id %r in %s is out of range or listed twice" \
            % (entry["key_id"], config.OHTTP_KEYS_FILE)
        keys[entry["key_id"]] = (SUITE.kem.deserialize_private_key(raw),
                                 public.public_bytes_raw())
    return keys


def key_config(key_id, public):
    """One key configuration (RFC 9458 section 3.1)."""
    return (bytes([key_id]) + KEM_ID.to_bytes(2) + public
            + (4).to_bytes(2) + KDF_ID.to_bytes(2) + AEAD_ID.to_bytes(2))


@router.get("/ohttp-keys", summary="The Oblivious HTTP gateway's public keys",
            response_class=Response,
            responses={200: {"content": {"application/ohttp-keys": {}}}})
def ohttp_keys():
    """The key configurations a client seals its requests to, as
    `application/ohttp-keys` (RFC 9458 section 3.2): each configuration
    prefixed with its 2-byte length, newest key first.

    Untested in the real world.
    """
    body = b"".join(len(cfg).to_bytes(2) + cfg
                    for cfg in (key_config(key_id, public)
                                for key_id, (_, public) in _keys().items()))
    return Response(body, media_type="application/ohttp-keys", headers={
        "Cache-Control": "public, max-age=%d" % KEYS_MAX_AGE,
        "ETag": '"%s"' % hashlib.sha256(body).hexdigest()[:16]})


# --------------------------------------------------------------------------
# Binary HTTP (RFC 9292), known-length messages only
# --------------------------------------------------------------------------

def _varint(data, pos):
    """The QUIC variable-length integer at `pos` (RFC 9000 section 16)."""
    length = 1 << (data[pos] >> 6)
    if pos + length > len(data):
        raise ValueError("truncated integer")
    return (int.from_bytes(data[pos:pos + length]) & ((1 << (8 * length - 2)) - 1),
            pos + length)


def _encode_varint(value):
    for bits, length in ((6, 1), (14, 2), (30, 4), (62, 8)):
        if value < 1 << bits:
            return (value | ((length.bit_length() - 1) << (8 * length - 2))).to_bytes(length)
    raise ValueError("%d does not fit a variable-length integer" % value)


def _field(data, pos):
    length, pos = _varint(data, pos)
    if pos + length > len(data):
        raise ValueError("truncated field")
    return data[pos:pos + length], pos + length


def decode_request(data):
    """``(method, path, headers)`` of a known-length Binary HTTP request.
    `headers` is a list of ``(name, value)`` byte pairs. Content and trailers
    are not read: the gateway serves GET and HEAD."""
    try:
        framing, pos = _varint(data, 0)
        if framing != 0:
            raise ValueError("not a known-length request (framing %d)" % framing)
        method, pos = _field(data, pos)
        _scheme, pos = _field(data, pos)
        _authority, pos = _field(data, pos)
        path, pos = _field(data, pos)
        # a message may stop after any section (RFC 9292 section 3.8)
        section, pos = _field(data, pos) if pos < len(data) else (b"", pos)
        headers, at = [], 0
        while at < len(section):
            name, at = _field(section, at)
            value, at = _field(section, at)
            headers.append((name, value))
    except IndexError:
        raise ValueError("truncated message") from None
    return method.decode("ascii"), path.decode("ascii"), headers


def encode_response(status, headers, content):
    """A known-length Binary HTTP response, zero-padded to `PAD_BLOCK`."""
    section = b"".join(_encode_varint(len(part)) + part
                       for name, value in headers for part in (name, value))
    message = (_encode_varint(1) + _encode_varint(status)
               + _encode_varint(len(section)) + section
               + _encode_varint(len(content)) + content)
    return message + bytes(-len(message) % PAD_BLOCK)


# --------------------------------------------------------------------------
# encapsulation (RFC 9458 section 4)
# --------------------------------------------------------------------------

def open_request(blob):
    """``(binary http request, enc, hpke context)`` of an Encapsulated Request.
    Raises ValueError for a blob we cannot open."""
    if len(blob) < HEADER_LEN + NENC:
        raise ValueError("encapsulated request is too short")
    header, enc = blob[:HEADER_LEN], blob[HEADER_LEN:HEADER_LEN + NENC]
    suite = tuple(int.from_bytes(header[i:i + 2]) for i in (1, 3, 5))
    if suite != (KEM_ID, KDF_ID, AEAD_ID):
        raise ValueError("unsupported HPKE suite %04x/%04x/%04x" % suite)
    if header[0] not in _keys():
        raise ValueError("key-not-found: no key with id %d" % header[0])
    context = SUITE.create_recipient_context(
        enc, _keys()[header[0]][0], info=b"message/bhttp request\x00" + header)
    try:
        return context.open(blob[HEADER_LEN + NENC:]), enc, context
    except OpenError:
        raise ValueError("the encapsulated request does not open") from None


def seal_response(message, enc, context, nonce=None):
    """The Encapsulated Response for `message`, under a key derived from the
    request's HPKE context. `nonce` is random; a test passes the RFC's."""
    nonce = nonce or os.urandom(max(NK, NN))
    extract = hmac.HMAC(enc + nonce, hashes.SHA256())
    extract.update(context.export(b"message/bhttp response", max(NK, NN)))
    prk = extract.finalize()
    key = HKDFExpand(hashes.SHA256(), NK, b"key").derive(prk)
    aead_nonce = HKDFExpand(hashes.SHA256(), NN, b"nonce").derive(prk)
    return nonce + AESGCM(key).encrypt(aead_nonce, message, b"")


# --------------------------------------------------------------------------
# the gateway
# --------------------------------------------------------------------------

async def _dispatch(app, method, path, accept, encodings=()):
    """Run one request against `app` in process: ``(status, headers, body)``."""
    headers = [(b"host", b"lagen.nu")] + [(b"accept", v) for v in accept]
    if encodings:
        headers.extend([(b"accept-encoding", v) for v in encodings])
    path, _, query = path.partition("?")
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
             "method": method, "scheme": "https", "path": path,
             "raw_path": path.encode("ascii"), "query_string": query.encode("ascii"),
             "headers": headers,
             "client": None, "server": ("lagen.nu", 443)}
    request = [{"type": "http.request", "body": b"", "more_body": False}]
    status, headers, body = None, [], bytearray()

    async def receive():
        if request:
            return request.pop()
        # a streaming response waits on this for a disconnect that never comes;
        # its task group cancels the wait when the body is sent
        await asyncio.Event().wait()

    async def send(message):
        nonlocal status, headers
        if message["type"] == "http.response.start":
            status, headers = message["status"], list(message["headers"])
        elif message["type"] == "http.response.body":
            body.extend(message.get("body", b""))

    await app(scope, receive, send)
    return status, headers, bytes(body)


def _refusal(status, detail):
    return (status, [(b"content-type", b"application/json")],
            json.dumps({"detail": detail}).encode())


@router.post("/ohttp-gateway", summary="Oblivious HTTP gateway",
             response_class=Response,
             responses={200: {"content": {"message/ohttp-res": {}}}})
async def ohttp_gateway(request: Request):
    """Open an Encapsulated Request (`message/ohttp-req`), serve the inner
    request in process, answer with the Encapsulated Response
    (`message/ohttp-res`).

    The inner request must be a GET or HEAD of `/api/v1/range/…` or
    `/api/v1/packs/…`. Anything else is refused *inside* the sealed response,
    with the status the route itself would have used (403, 405), so the relay
    learns nothing from the outer 200.

    Untested in the real world.
    """
    _keys()                        # 404 before anything else when unconfigured
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() \
            != "message/ohttp-req":
        raise HTTPException(415, "send message/ohttp-req")
    blob = bytearray()
    async for chunk in request.stream():
        blob.extend(chunk)
        if len(blob) > MAX_REQUEST_BYTES:
            raise HTTPException(413, "request body exceeds %d bytes" % MAX_REQUEST_BYTES)
    try:
        message, enc, context = open_request(bytes(blob))
        method, path, headers = decode_request(message)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    if method not in INNER_METHODS:
        inner = _refusal(405, "the gateway serves GET and HEAD")
    elif not INNER_PATH.match(path):
        inner = _refusal(403, "the gateway serves /api/v1/range/ and /api/v1/packs/")
    else:
        accepts = [v for name, v in headers if name.lower() == b"accept"]
        encodings = [v for name, v in headers if name.lower() == b"accept-encoding"] if path.startswith("/api/v1/packs/") else []
        inner = await _dispatch(request.app, method, path, accepts, encodings)
    return Response(seal_response(encode_response(*inner), enc, context),
                    media_type="message/ohttp-res",
                    headers={"Cache-Control": "no-store"})


# --------------------------------------------------------------------------
# CLI: mint a key into the key file
# --------------------------------------------------------------------------

def _main(argv):
    """Put a new key first in the key file, creating the file (mode 0600) when
    it does not exist. The older keys stay, so requests sealed to them open
    until someone removes them."""
    if len(argv) != 2 or argv[0] != "keygen":
        sys.exit("usage: python -m ferenda.api.ohttp keygen <key file>")
    path = Path(argv[1])
    entries = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    used = {entry["key_id"] for entry in entries}
    key_id = (max(used, default=0) + 1) % 256
    assert key_id not in used, "key id %d is taken -- remove the retired keys from %s" \
        % (key_id, path)
    entries.insert(0, {"key_id": key_id,
                       "private_key": X25519PrivateKey.generate().private_bytes_raw().hex()})
    path.touch(mode=0o600)
    path.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
    print("key %d written to %s (%d key(s) listed) -- restart the service to "
          "publish it" % (key_id, path, len(entries)))


if __name__ == "__main__":
    _main(sys.argv[1:])
