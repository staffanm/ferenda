"""The served-site error ledger: one record per HTTP error the site answered
with, keyed by a short id the error page shows the reader.

The point is the hand-off. A reader who hits a broken page can quote eight hex
characters, and `lagen all errors <id>` produces the request that produced it --
url, referer, client, and for a 500 the whole traceback. Without that the report
is "some page was broken yesterday", which is not a bug report.

Not to be confused with `runlog`'s ``errors.json``, which is the *build*'s
per-document outcome store ("did sfs/parse/1998:204 fail last run"). This is the
*serving* side, and the two never mix: a document can be missing from the site
without any build having failed, which is precisely the case worth recording.

An append-only ndjson file, rotated at `MAX_BYTES` to a single ``.1`` generation
so a bot storm (or a storage fault that turns every request into an error) is
bounded rather than unbounded. Readers see both generations, newest first.

Pure functions over explicit Paths, no app import -- api/ writes and build.py
reads through this module without either importing the other, the same shape
runlog.py uses.
"""

import os
import re
import secrets
import traceback
from pathlib import Path

from .util import append_json_line, now_iso, read_json_lines

ID_BYTES = 4                        # 4 bytes -> the 8 hex chars an error page shows
# what an error id looks like, so a caller can tell one from any other argument
# (`lagen all errors` takes an id or a count, and 1 in 43 ids is all digits)
RE_ID = re.compile(r"[0-9a-f]{%d}" % (ID_BYTES * 2))
TB_CAP = 8192                       # chars of traceback kept (the tail, where the raise is)
MAX_BYTES = 8 * 1024 * 1024         # rotate past this; one older generation is kept
FIELDS = ("id", "time", "status", "method", "url", "client", "referer",
          "user_agent", "detail", "exc_type", "exc_message", "traceback")


def new_id():
    """A fresh error id: 8 hex chars, short enough to read over the phone.

    Not a uuid: this identifies one entry in one site's ledger, so it needs to
    be unguessable-ish and unique among a bounded set, not globally unique. 4
    bytes gives a 50% collision chance somewhere around 80 000 recorded errors,
    and a collision is harmless anyway -- `entries` returns both matches and the
    timestamps tell them apart."""
    return secrets.token_hex(ID_BYTES)


def _rotate(path):
    """Move the ledger aside once it passes `MAX_BYTES`, keeping one generation.

    os.replace over the previous ``.1`` is atomic, so a reader mid-read keeps
    the file it opened."""
    if path.exists() and path.stat().st_size > MAX_BYTES:
        os.replace(path, path.with_suffix(path.suffix + ".1"))


def record(path, status, *, method=None, url=None, client=None, referer=None,
           user_agent=None, detail=None, exc=None, t=None):
    """Append one error and return its record (the caller wants ``["id"]`` for
    the page it is about to render).

    `exc` is the live exception for a 500; its type, message and traceback tail
    are kept. A 404 has none -- what matters there is the url and the referer
    that pointed at it, which is how a dead internal link is found."""
    rec = {"id": new_id(), "time": now_iso(t), "status": status,
           "method": method, "url": url, "client": client, "referer": referer,
           "user_agent": user_agent, "detail": detail,
           "exc_type": type(exc).__name__ if exc is not None else None,
           "exc_message": str(exc) if exc is not None else None,
           "traceback": "".join(traceback.format_exception(
               type(exc), exc, exc.__traceback__))[-TB_CAP:]
           if exc is not None else None}
    _rotate(path)
    append_json_line(path, rec)         # mkdir + flushed append, shared with runlog
    return rec


def _read_file(path):
    """Records from one generation, oldest first -- `util.read_json_lines`,
    shared with the run ledger, which meets the same torn-tail case.

    ``errors="replace"`` because a user-agent or referer copied off the wire can
    carry bytes that are not valid UTF-8; a mangled header is worth keeping as a
    record, unlike a mangled JSON structure."""
    return read_json_lines(path, errors="replace")


def entries(path, error_id=None, limit=None):
    """Recorded errors, newest first, across both generations.

    `error_id` filters to that id (a list, not a single record: ids can in
    principle repeat, and returning both beats picking one arbitrarily).
    `limit` caps the result.

    Each generation is reversed on its own and the *current* one goes first --
    concatenating and reversing the whole would surface the rotated-away
    generation as the newest."""
    path = Path(path)
    out = list(reversed(_read_file(path))) + list(reversed(
        _read_file(path.with_suffix(path.suffix + ".1"))))
    if error_id:
        out = [r for r in out if r["id"] == error_id]
    return out[:limit] if limit else out
