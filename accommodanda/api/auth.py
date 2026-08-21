"""Login + session for the inline content editor (REWRITE.md §6, the write
side of the service) -- plus `same_origin`, the gate the whole internal surface
carries.

The public site and REST API are read-only; editing content is the one
authenticated, mutating surface. Editors are a small hand-curated registry in
config.yml (``config.EDITORS``) -- there is no self-signup -- and each maps to a
git ``name``/``email`` so a commit made through the web UI is attributed exactly
as a `git clone` + commit would be (`lib/editcart.py` stamps it).

Auth is a signed session cookie, not HTTP Basic: unlike the curl-friendly ops
dashboard, the editor is a stateful JS surface that needs a real login/logout
and a per-request identity to attribute commits. The cookie carries the
username, a fingerprint of the editor's current password hash, and an
expiry, signed with HMAC-SHA256 over ``config.EDITOR_SECRET`` (a tampered or
forged cookie fails ``verify`` and is rejected) -- no server-side session
table, so it survives a restart and adds no state to guard. The pwhash
fingerprint is the session's revocation lever: change an editor's password
and every session issued under the old one stops matching (see
``_pwhash_fingerprint``). An unset ``editor_secret`` disables editing
wholesale: ``require_editor`` answers 403 -- which also gates the ops
dashboard (`/ops`, api/ops.py), since it rides this same editor session
rather than a separate credential.

These routes answer at ``/internal-api/v1/auth/…``; the module lives here
rather than in `api/internal.py` because `require_editor` and `same_origin` --
the two gates -- belong together, and `api/ops.py` imports both without
importing the internal app.

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
import threading
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from .. import config

router = APIRouter(prefix="/auth", tags=["auth"])

COOKIE = "lagen_editor"
# The session cookie is HttpOnly, so page scripts can't tell whether a session
# exists. This companion cookie is a readable, non-secret hint ("a login
# happened here") that lets editor.js skip the /auth/me roundtrip on every
# anonymous page load; the session cookie alone remains the credential.
COOKIE_HINT = "lagen_editor_hint"
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


def _pwhash_fingerprint(pwhash):
    """A short, non-secret fingerprint of an editor's ``pwhash``: stable while
    the entry is unchanged, different the moment the password (and so the
    salt+digest) changes. Embedding it in the session cookie is the session's
    revocation mechanism: sessions carry no server-side state (REWRITE.md/the
    module docstring -- a cookie survives a restart) and the site has a
    handful of editors, so the cheapest sound revocation lever is to make a
    password change (``pwhash`` in config.yml + a restart) invalidate every
    outstanding session for that editor, rather than build and guard a
    separate revocation table for a 14-day cookie. The tradeoff: an editor
    can't revoke one specific stolen session while keeping others alive --
    rotating the password kills all of them at once, which is proportionate
    here."""
    return _b64(hashlib.sha256(pwhash.encode("utf-8")).digest())[:16]


def issue(username, pwhash, *, ttl=SESSION_TTL):
    """A signed cookie value carrying ``username``, a fingerprint of the
    editor's current ``pwhash`` and an absolute expiry."""
    body = json.dumps({"u": username, "pf": _pwhash_fingerprint(pwhash),
                       "exp": int(time.time()) + ttl},
                      separators=(",", ":")).encode("utf-8")
    return "%s.%s" % (_b64(body), _b64(_sign(body)))


def verify(token):
    """The ``(username, pwhash_fingerprint)`` claims of a valid, unexpired
    cookie, or ``None``. Any malformation (bad shape, forged signature, past
    expiry) is an anonymous request, not an error -- returns ``None`` so the
    caller answers 401. The caller still has to check the fingerprint against
    the editor's *current* ``pwhash`` -- this only proves the cookie was
    genuinely issued by us, not that it's still current."""
    if not token or "." not in token:
        return None
    body_txt, sig_txt = token.rsplit(".", 1)
    try:
        body_b = _unb64(body_txt)
        if not hmac.compare_digest(_unb64(sig_txt), _sign(body_b)):
            return None
        claims = json.loads(body_b)
    # binascii.Error (bad base64url) and json.JSONDecodeError are both
    # ValueErrors -- a malformed cookie is an anonymous request, not an error
    except ValueError:
        return None
    # past the signature check the payload is one `issue()` wrote -- but
    # possibly a *previous deploy's* issue(): a claim added to the cookie
    # format is missing from every cookie minted before the deploy, for up to
    # SESSION_TTL. Per this function's own contract that is an anonymous
    # request (re-login mints the new format), not a 500.
    try:
        if claims["exp"] < time.time():
            return None
        return claims["u"], claims["pf"]
    except KeyError:
        return None


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
    """The Editor for this request, or ``None`` when editing is disabled, the
    request is anonymous/stale, or the cookie's pwhash fingerprint no longer
    matches the editor's current entry (their password was changed since the
    session was issued -- see ``_pwhash_fingerprint``). The soft form behind
    ``/auth/me`` and the client login check -- ``require_editor`` is the hard
    gate."""
    if not config.EDITOR_SECRET:
        return None
    claims = verify(request.cookies.get(COOKIE))
    if claims is None:
        return None
    username, fingerprint = claims
    entry = config.EDITORS.get(username)
    if entry is None or fingerprint != _pwhash_fingerprint(entry["pwhash"]):
        return None
    return Editor(username, entry)


_CROSS_ORIGIN = "this endpoint answers same-origin requests only"


def same_origin(request: Request):
    """Refuse a request that a browser made from another origin.

    Two headers, because they fail in different directions:

      * ``Sec-Fetch-Site`` is sent by every current browser and by nothing
        else. ``same-origin`` is our own page; ``none`` is a typed address or
        a bookmark, which is how an editor reaches `/internal-api/v1/pdf/vanta`
        or the review pages; anything else (``cross-site``, ``same-site``) is
        another origin driving us.
      * ``Origin`` is sent on every POST and on cross-origin fetches, including
        by a browser too old to send the first header. It is compared on
        **host alone**, not on the whole origin string.

    A request with neither header is not a browser -- curl, the test client,
    the in-process client `generate` runs -- and is let through. This is a
    same-origin gate, not authentication: `require_editor` is what decides who
    may act, and it still gates every route that acts.

    Host alone, because the scheme half of our own origin is not a fact the app
    holds. ``request.url.scheme`` says ``https`` only when uvicorn accepted
    ``X-Forwarded-Proto``, which it does only for a peer named in
    ``FORWARDED_ALLOW_IPS`` -- and that variable has already been wrong on this
    deployment once (see `app.serve`, where every request was attributed to
    nginx's own address). In that state the browser sends
    ``Origin: https://ferenda.lagen.nu``, the app computes ``http://…``, and
    every editor POST, every PDF job and every ``/ops`` page answers 403 with a
    message pointing at the browser rather than at the env var. The host
    answers the question this gate is asking, and it answers it from the
    request's own ``Host`` header. A scheme downgrade is not reachable past a
    TLS-terminating proxy that redirects port 80 and sets HSTS, and it would
    not be this gate's job to catch it if it were.
    """
    site = request.headers.get("sec-fetch-site")
    if site is not None and site not in ("same-origin", "none"):
        raise HTTPException(403, _CROSS_ORIGIN)
    origin = request.headers.get("origin")
    if origin is not None and urlsplit(origin).netloc != request.url.netloc:
        raise HTTPException(403, _CROSS_ORIGIN)


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
# login rate limiting -- a per-IP + per-account sliding window with
# exponential backoff, plus a hard cap on concurrent pbkdf2 work
# --------------------------------------------------------------------------
#
# Every login POST -- including one for an unknown username, which is
# deliberately checked against `_DUMMY_PWHASH` to equalize timing -- burns a
# full PBKDF2_ROUNDS pbkdf2 call in Starlette's bounded sync threadpool. Left
# unchecked, a flood of attempts pins CPU there and starves the rest of the
# (small, single-process) site. Two independent, dependency-free, in-memory
# guards:
#
#   * `_RateLimiter` rejects an over-quota (ip, username) *before* any pbkdf2
#     work runs, with a delay that grows exponentially per key so repeated
#     guessing gets throttled hard while a handful of genuine mistyped
#     passwords are barely noticed.
#   * `_LOGIN_SEM` bounds how many pbkdf2 calls run at once across all keys,
#     so a flood spread over many distinct IPs/usernames (each individually
#     under quota) still can't monopolize the threadpool.
#
# State lives only for the process lifetime -- a restart forgets past
# attempts, which is fine for this threat model (no distributed attacker
# coordination worth persisting through a restart).

_LOGIN_WINDOW = 60.0            # seconds; attempt counts reset after this
_LOGIN_FREE_ATTEMPTS = 5        # attempts per key per window before backoff starts
_LOGIN_BACKOFF_BASE = 2.0       # seconds; doubles per attempt past the free quota
_LOGIN_BACKOFF_MAX = 300.0      # 5 minutes; the backoff cap per key
_LOGIN_KEYS_MAX = 10_000        # bound tracked keys so a flood of distinct
                                # IPs/usernames can't grow the dict without limit
_LOGIN_MAX_CONCURRENT = 4       # hard cap on simultaneous pbkdf2 work


class _RateLimiter:
    """A per-key sliding-window attempt counter with exponential backoff.
    Threadsafe; sized for a handful of editors and casual attack traffic, not
    a distributed flood -- that's what the concurrency cap below is for."""

    def __init__(self):
        self._lock = threading.Lock()
        self._state = {}   # key -> (count, window_start, blocked_until)

    def check(self, key):
        """Raise 429 if `key` is currently backed off, or if this attempt
        itself would spend past the free quota -- so the (FREE_ATTEMPTS+1)th
        attempt in a window is rejected outright, not merely the one after
        that. Otherwise record the attempt and return."""
        now = time.monotonic()
        with self._lock:
            if len(self._state) > _LOGIN_KEYS_MAX:
                # an attacker flooding with distinct keys (e.g. random
                # usernames) shouldn't grow this dict without bound; forgetting
                # all counters is safe -- worst case a few free attempts return.
                self._state.clear()
            count, window_start, blocked_until = self._state.get(key, (0, now, 0.0))
            if now < blocked_until:
                raise HTTPException(
                    429, "too many login attempts -- try again later",
                    headers={"Retry-After": str(int(blocked_until - now) + 1)})
            if now - window_start > _LOGIN_WINDOW:
                count, window_start = 0, now
            if count >= _LOGIN_FREE_ATTEMPTS:
                over = count - _LOGIN_FREE_ATTEMPTS + 1
                blocked_until = now + min(_LOGIN_BACKOFF_BASE * 2 ** (over - 1),
                                          _LOGIN_BACKOFF_MAX)
                self._state[key] = (count + 1, window_start, blocked_until)
                raise HTTPException(
                    429, "too many login attempts -- try again later",
                    headers={"Retry-After": str(int(blocked_until - now) + 1)})
            self._state[key] = (count + 1, window_start, 0.0)

    def reset(self, key):
        """Forget `key`'s attempt history -- called after a successful login
        so a real editor's next mistyped password starts a fresh quota."""
        with self._lock:
            self._state.pop(key, None)


_login_limiter = _RateLimiter()
_LOGIN_SEM = threading.BoundedSemaphore(_LOGIN_MAX_CONCURRENT)


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------

class LoginBody(BaseModel):
    username: str
    password: str


class Me(BaseModel):
    username: str
    name: str


@router.post("/login", response_model=Me)
def login(body: LoginBody, request: Request, response: Response):
    """Exchange a username + password for a signed session cookie. A wrong
    username and a wrong password fail identically -- same 401, same pbkdf2 cost
    (an unknown user is checked against `_DUMMY_PWHASH`) -- so neither the
    response nor its timing can enumerate editors. Rate limited per client IP
    and per attempted username (`_login_limiter`) before any pbkdf2 runs, and
    the pbkdf2 call itself is gated by `_LOGIN_SEM` so at most
    `_LOGIN_MAX_CONCURRENT` hashes run at once regardless of how the attempts
    are spread across keys."""
    if not config.EDITOR_SECRET:
        raise HTTPException(403, "editing disabled -- set `editor_secret`")
    ip = request.client.host if request.client else "unknown"
    _login_limiter.check(("ip", ip))
    _login_limiter.check(("user", body.username))
    if not _LOGIN_SEM.acquire(blocking=False):
        raise HTTPException(429, "server is busy handling logins -- try again shortly")
    try:
        entry = config.EDITORS.get(body.username)
        ok = verify_password(body.password, entry["pwhash"] if entry else _DUMMY_PWHASH)
    finally:
        _LOGIN_SEM.release()
    if not (entry and ok):
        raise HTTPException(401, "bad username or password")
    _login_limiter.reset(("ip", ip))
    _login_limiter.reset(("user", body.username))
    # Secure is an explicit config switch (config.COOKIE_SECURE, on by default)
    # rather than inferred from the request's scheme/forwarded-proto header --
    # that header is only as trustworthy as the proxy in front of the app, and
    # a client that reaches uvicorn directly could spoof it. Flip it off in
    # config.yml only for a plain-http dev serve. HttpOnly + SameSite=Lax carry
    # the CSRF/theft protection either way.
    response.set_cookie(COOKIE, issue(body.username, entry["pwhash"]), max_age=SESSION_TTL,
                        httponly=True, samesite="lax",
                        secure=config.COOKIE_SECURE, path="/")
    response.set_cookie(COOKIE_HINT, "1", max_age=SESSION_TTL,
                        httponly=False, samesite="lax",
                        secure=config.COOKIE_SECURE, path="/")
    return Me(username=body.username, name=entry["name"])


@router.post("/logout")
def logout(response: Response):
    """Clear the session cookie. Idempotent -- safe to call when not logged in."""
    response.delete_cookie(COOKIE, path="/")
    response.delete_cookie(COOKIE_HINT, path="/")
    return {"ok": True}


@router.get("/me", response_model=Me)
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
