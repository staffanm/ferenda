"""The served-site error ledger and the 404/500 responses built on it.

Covers the three things that have to hold for `lagen all errors <id>` to be
worth anything: the id shown on the page is the id in the ledger, the record
carries what a bug report needs (url, referer, traceback), and a reader gets a
page while an API client keeps its JSON.
"""

import json
import re

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from accommodanda.api import errors as api_errors
from accommodanda.lib import errorlog


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    path = tmp_path / "httperrors.ndjson"
    monkeypatch.setattr(api_errors, "LEDGER", path)
    return path


@pytest.fixture
def client(ledger):
    """A minimal app carrying only the error handlers -- the real app pulls in
    the catalog and the MCP lifespan, neither of which this is about."""
    app = FastAPI()

    @app.get("/boom")
    def boom():
        raise RuntimeError("the storage mount is on fire")

    @app.get("/api/v1/boom")
    def api_boom():
        raise RuntimeError("the storage mount is on fire")

    @app.get("/api/v1/rejected")
    def rejected():
        raise HTTPException(422, "cursor and offset are mutually exclusive")

    api_errors.install(app)
    return TestClient(app, raise_server_exceptions=False)


def _shown_id(html):
    """The 8-hex reference the page tells the reader to quote."""
    found = re.search(r"<code>([0-9a-f]{8})</code>", html)
    assert found, html[:400]
    return found.group(1)


# --------------------------------------------------------------------------
# the ledger
# --------------------------------------------------------------------------

def test_record_returns_an_eight_hex_id_and_reads_back(ledger):
    rec = errorlog.record(ledger, 404, method="GET", url="https://lagen.nu/x",
                          referer="https://lagen.nu/sfs/")
    assert re.fullmatch(r"[0-9a-f]{8}", rec["id"])
    (back,) = errorlog.entries(ledger, error_id=rec["id"])
    assert back["url"] == "https://lagen.nu/x"
    assert back["referer"] == "https://lagen.nu/sfs/"
    assert back["status"] == 404


def test_a_500_keeps_the_exception_and_its_traceback(ledger):
    try:
        raise ValueError("nope")
    except ValueError as exc:
        rec = errorlog.record(ledger, 500, exc=exc)
    assert rec["exc_type"] == "ValueError"
    assert rec["exc_message"] == "nope"
    assert "ValueError: nope" in rec["traceback"]


def test_entries_are_newest_first(ledger):
    ids = [errorlog.record(ledger, 404, url="/%d" % i)["id"] for i in range(5)]
    assert [r["id"] for r in errorlog.entries(ledger)] == list(reversed(ids))


def test_rotation_keeps_the_older_generation_readable_and_ordered(ledger,
                                                                  monkeypatch):
    """A bot storm (or a storage fault turning every request into an error)
    must not grow the ledger without bound -- but rotating must not reorder it
    either: the rotated-away generation is the *older* one.

    Only two generations are kept by design, so with a tiny MAX_BYTES the
    oldest records are gone. What must hold is that whatever survives is the
    *newest* run of records, still newest-first -- i.e. a prefix of the
    reversed insertion order, never a shuffled or oldest-first one."""
    monkeypatch.setattr(errorlog, "MAX_BYTES", 400)
    ids = [errorlog.record(ledger, 404, url="/%d" % i)["id"] for i in range(12)]
    assert ledger.with_suffix(ledger.suffix + ".1").exists()
    got = [r["id"] for r in errorlog.entries(ledger)]
    assert got and got == list(reversed(ids))[:len(got)]


def test_a_truncated_tail_line_does_not_take_the_read_down(ledger):
    good = errorlog.record(ledger, 404, url="/ok")["id"]
    with open(ledger, "a", encoding="utf-8") as fp:
        fp.write('{"id": "deadbeef", "sta')      # a write cut mid-append
    assert [r["id"] for r in errorlog.entries(ledger)] == [good]


# --------------------------------------------------------------------------
# the responses
# --------------------------------------------------------------------------

def test_a_site_404_is_a_page_whose_id_is_in_the_ledger(client, ledger):
    resp = client.get("/no-such-page", headers={"referer": "https://lagen.nu/sfs/"})
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("text/html")
    assert "Sidan finns inte" in resp.text
    (rec,) = errorlog.entries(ledger, error_id=_shown_id(resp.text))
    assert rec["status"] == 404
    assert rec["url"].endswith("/no-such-page")
    # the field that earns the whole ledger: an internal 404 with a referer is
    # a dead link the site itself published
    assert rec["referer"] == "https://lagen.nu/sfs/"


def test_a_site_500_is_a_page_and_the_traceback_is_recorded(client, ledger):
    resp = client.get("/boom")
    assert resp.status_code == 500
    assert "Något gick fel" in resp.text
    # the reader is never shown the stack, only the reference
    assert "RuntimeError" not in resp.text
    (rec,) = errorlog.entries(ledger, error_id=_shown_id(resp.text))
    assert rec["exc_type"] == "RuntimeError"
    assert "the storage mount is on fire" in rec["traceback"]


def test_an_api_client_keeps_json_and_gains_the_error_id(client, ledger):
    resp = client.get("/api/v1/boom")
    assert resp.status_code == 500
    assert resp.headers["content-type"].startswith("application/json")
    body = resp.json()
    assert errorlog.entries(ledger, error_id=body["error_id"])


def test_a_client_error_is_neither_paged_nor_logged(client, ledger):
    """A 4xx that is not a 404 is the caller's own malformed request; one
    ledger line per bad query string would bury the errors worth reading. It
    keeps the plain JSON body its client parses -- and carries no error id,
    because nothing was recorded to look up."""
    resp = client.get("/api/v1/rejected", headers={"accept": "text/html"})
    assert resp.status_code == 422
    assert resp.headers["content-type"].startswith("application/json")
    assert "error_id" not in resp.json()
    assert errorlog.entries(ledger) == []


def test_an_all_digit_id_is_still_read_as_an_id(ledger, monkeypatch):
    """`lagen all errors <arg>` takes an id or a count. About 1 in 43 ids is
    all digits (token_hex over 8 chars), so telling them apart on `isdigit()`
    would read a reader's quoted "20260731" as a count and print 20 million
    ledger lines instead of their one record. Shape decides."""
    assert errorlog.RE_ID.fullmatch("20260731")      # a plausible all-digit id
    assert errorlog.RE_ID.fullmatch("3f9a1c07")
    assert not errorlog.RE_ID.fullmatch("50")        # a count
    assert not errorlog.RE_ID.fullmatch("3f9a1c0")   # too short
    assert not errorlog.RE_ID.fullmatch("zzzzzzzz")  # not hex


def test_a_malformed_line_before_the_tail_is_not_swallowed(ledger):
    """The tail tolerance is for a write caught mid-append. Corruption earlier
    in the file is a real integrity failure in the one ledger whose job is
    "the error was recorded somewhere", so it must raise, not lose records."""
    errorlog.record(ledger, 404, url="/one")
    with open(ledger, "a", encoding="utf-8") as fp:
        fp.write("{not json at all}\n")
    errorlog.record(ledger, 404, url="/two")
    with pytest.raises(json.JSONDecodeError):
        errorlog.entries(ledger)
