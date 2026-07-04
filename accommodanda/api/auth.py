"""Login + session for the inline content editor (REWRITE.md §6, the write
side of the service).

The public site and REST API are read-only; editing content is the one
authenticated, mutating surface. Editors are a small hand-curated registry in
config.yml (``config.EDITORS``) -- there is no self-signup -- and each maps to a
git ``name``/``email`` so a commit made through the web UI is attributed exactly
as a `git clone` + commit would be (`lib/editcart.py` stamps it).

Auth is a signed session cookie, not HTTP Basic: unlike the curl-friendly ops
dashboard, the editor is a stateful JS surface that needs a real login/logout
and a per-request identity to attribute commits. The cookie carries only the
username + an expiry, signed with HMAC-SHA256 over ``config.EDITOR_SECRET`` (a
tampered or forged cookie fails ``verify`` and is rejected) -- no server-side
session table, so it survives a restart and adds no state to guard. An unset
``editor_secret`` disables editing wholesale: ``require_editor`` answers 403,
mirroring how an unset ``ops_token`` disables ``/ops``.

Passwords are stored only as ``pbkdf2$rounds$salt$hash`` strings (stdlib
``hashlib.pbkdf2_hmac``); ``python -m accommodanda.api.auth hash`` mints one to
paste into config.yml so a plaintext password is never written down.
"""

import base64
import hashlib
import hmac
import json
import os
import sys
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from .. import config

router = APIRouter()

COOKIE = "lagen_editor"
SESSION_TTL = 14 * 24 * 3600          # two weeks; re-login after that
PBKDF2_ROUNDS = 260_000               # OWASP-ish floor for pbkdf2-sha256


# --------------------------------------------------------------------------
# base64url helpers (shared by the pbkdf2 hashes and the signed cookie)
# --------------------------------------------------------------------------

def _b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(txt):
    return base64.urlsafe_b64decode(txt + "=" * (-len(txt) % 4))


# --------------------------------------------------------------------------
# password hashing (stdlib pbkdf2)
# --------------------------------------------------------------------------

def hash_password(password, *, rounds=PBKDF2_ROUNDS):
    """A self-describing ``pbkdf2$rounds$salt$hash`` string (all base64url). The
    per-hash salt and rounds travel with the digest, so verification needs no
    separate parameters and the cost can be raised for new hashes without
    invalidating old ones."""
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    return "pbkdf2$%d$%s$%s" % (rounds, _b64(salt), _b64(dk))


def verify_password(password, stored):
    """Whether ``password`` matches a ``stored`` ``pbkdf2$…`` string, in constant
    time. A malformed stored value is a config error, not a wrong password, so it
    raises rather than quietly returning False."""
    scheme, rounds, salt, digest = stored.split("$")
    if scheme != "pbkdf2":
        raise ValueError("unknown password hash scheme %r" % scheme)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                             _unb64(salt), int(rounds))
    return hmac.compare_digest(dk, _unb64(digest))


# a throwaway hash the login path verifies against when the username is unknown,
# so a bad username costs the same pbkdf2 work as a bad password -- the timing
# can't be used to enumerate which editor names exist. Minted once at import.
_DUMMY_PWHASH = hash_password("")


# --------------------------------------------------------------------------
# signed session cookie (stdlib hmac)
# --------------------------------------------------------------------------

def _sign(payload_b):
    return hmac.new(config.EDITOR_SECRET.encode("utf-8"), payload_b,
                    hashlib.sha256).digest()


def issue(username, *, ttl=SESSION_TTL):
    """A signed cookie value carrying ``username`` and an absolute expiry."""
    body = json.dumps({"u": username, "exp": int(time.time()) + ttl},
                      separators=(",", ":")).encode("utf-8")
    return "%s.%s" % (_b64(body), _b64(_sign(body)))


def verify(token):
    """The username inside a valid, unexpired cookie, or ``None``. Any
    malformation (bad shape, forged signature, past expiry) is an anonymous
    request, not an error -- returns ``None`` so the caller answers 401."""
    if not token or "." not in token:
        return None
    body_txt, sig_txt = token.rsplit(".", 1)
    try:
        body_b = _unb64(body_txt)
        if not hmac.compare_digest(_unb64(sig_txt), _sign(body_b)):
            return None
        claims = json.loads(body_b)
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(claims, dict) or claims.get("exp", 0) < time.time():
        return None
    return claims.get("u")


# --------------------------------------------------------------------------
# the request-scoped identity + gate
# --------------------------------------------------------------------------

class Editor:
    """The logged-in user resolved from the cookie against the registry -- the
    git identity every write endpoint stamps and commits under."""

    def __init__(self, username, entry):
        self.username = username
        self.name = entry["name"]
        self.email = entry["email"]


def current_editor(request: Request):
    """The Editor for this request, or ``None`` when editing is disabled or the
    request is anonymous/stale. The soft form behind ``/auth/me`` and the client
    login check -- ``require_editor`` is the hard gate."""
    if not config.EDITOR_SECRET:
        return None
    username = verify(request.cookies.get(COOKIE))
    entry = config.EDITORS.get(username) if username else None
    return Editor(username, entry) if entry else None


def require_editor(request: Request) -> Editor:
    """The single auth gate on every mutating endpoint. Editing off (no
    ``editor_secret``) -> 403 with a hint; anonymous/expired/unknown -> 401."""
    if not config.EDITOR_SECRET:
        raise HTTPException(403, "editing disabled -- set `editor_secret` "
                                 "(config.yml) or the EDITOR_SECRET env var")
    editor = current_editor(request)
    if editor is None:
        raise HTTPException(401, "log in to edit")
    return editor


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------

class LoginBody(BaseModel):
    username: str
    password: str


class Me(BaseModel):
    username: str
    name: str


@router.post("/api/v1/auth/login", response_model=Me, tags=["auth"])
def login(body: LoginBody, request: Request, response: Response):
    """Exchange a username + password for a signed session cookie. A wrong
    username and a wrong password fail identically -- same 401, same pbkdf2 cost
    (an unknown user is checked against `_DUMMY_PWHASH`) -- so neither the
    response nor its timing can enumerate editors."""
    if not config.EDITOR_SECRET:
        raise HTTPException(403, "editing disabled -- set `editor_secret`")
    entry = config.EDITORS.get(body.username)
    ok = verify_password(body.password, entry["pwhash"] if entry else _DUMMY_PWHASH)
    if not (entry and ok):
        raise HTTPException(401, "bad username or password")
    # Secure only when the request is https. Behind the prod TLS proxy that
    # relies on uvicorn's proxy-header handling (serve() enables it) surfacing
    # the forwarded scheme, so the vhost must send `X-Forwarded-Proto`; on a
    # plain-http dev serve the cookie still works. HttpOnly + SameSite=Lax carry
    # the CSRF/theft protection either way.
    response.set_cookie(COOKIE, issue(body.username), max_age=SESSION_TTL,
                        httponly=True, samesite="lax",
                        secure=request.url.scheme == "https", path="/")
    return Me(username=body.username, name=entry["name"])


@router.post("/api/v1/auth/logout", tags=["auth"])
def logout(response: Response):
    """Clear the session cookie. Idempotent -- safe to call when not logged in."""
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/api/v1/auth/me", response_model=Me, tags=["auth"])
def me(editor: Editor = Depends(require_editor)):
    """Who the current session is -- the client's login check that decides
    whether to render the edit affordances."""
    return Me(username=editor.username, name=editor.name)


# --------------------------------------------------------------------------
# CLI: mint a pwhash to paste into config.yml
# --------------------------------------------------------------------------

def _main(argv):
    if len(argv) != 2 or argv[0] != "hash":
        sys.exit("usage: python -m accommodanda.api.auth hash <password>")
    print(hash_password(argv[1]))


if __name__ == "__main__":
    _main(sys.argv[1:])
