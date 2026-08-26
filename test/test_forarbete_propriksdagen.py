"""The one-time repair of propositions riksdagen serves but we never stored.

1 756 records carry `files: []` and a data.riksdagen.se url -- that url is
riksdagen's body endpoint, but the legacy import took the sibling
`dokumentstatus` XML (a metadata envelope) and wrote no body. Network-free:
`request` is stubbed."""

import json

import pytest
import requests

from ferenda.forarbete import propriksdagen as pr
from ferenda.lib import compress, layout
from ferenda.lib.util import write_atomic

BODY = ('<div class="brask">Observera att dokumentet är inskannat</div>'
        '<div class=Section1><p class=MsoNormal>Regeringens proposition</p></div>')


def _record(root, basefile, *, files=None, url=None):
    record = {"type": "prop", "basefile": basefile, "identifier": "Prop. " + basefile,
              "title": "En proposition", "date": "1984-01-01",
              "url": url if url is not None else
                     "http://data.riksdagen.se/dokument/G70340",
              "files": files or []}
    path = layout.fa_record_file(root, "prop", basefile)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, json.dumps(record, ensure_ascii=False))
    return record


def _stub(monkeypatch, bodies):
    """`bodies` maps url -> html, or to an exception instance to raise."""
    def fake(session, method, url, **kw):
        got = bodies[url]
        if isinstance(got, Exception):
            raise got
        return type("R", (), {"text": got})()
    monkeypatch.setattr(pr, "request", fake)
    monkeypatch.setattr(pr, "make_session", lambda ua: None)
    monkeypatch.setattr(pr.time, "sleep", lambda *_: None)


def test_pending_selects_only_body_less_riksdagen_records(tmp_path):
    _record(tmp_path, "1983/84:40")                                  # the target
    _record(tmp_path, "1984/85:1", files=["1984-85-1.pdf"])          # has a body
    _record(tmp_path, "1985/86:2",
            url="https://www.regeringen.se/rattsliga-dokument/x/")   # not riksdagen
    _record(tmp_path, "1986/87:3", url="")                           # no url at all
    assert [r["basefile"] for r in pr.pending(tmp_path)] == ["1983/84:40"]


def test_download_one_stores_the_body_and_points_the_record(tmp_path, monkeypatch):
    record = _record(tmp_path, "1983/84:40")
    _stub(monkeypatch, {record["url"]: BODY})
    assert pr.download_one(tmp_path, None, record, delay=0) is True
    body = layout.fa_dir(tmp_path, "prop", "1983/84:40") / "1983-84-40.html"
    assert body.read_text() == BODY
    on_disk = json.loads(compress.read_text(
        layout.fa_record_file(tmp_path, "prop", "1983/84:40")))
    assert on_disk["files"] == ["1983-84-40.html"]
    # the existing route for riksdagen's OCR'd Word-HTML -- no new parser
    assert on_disk["body_format"] == "skanning2007"
    assert pr.pending(tmp_path) == []          # and it drops out of the work list


def test_an_empty_body_is_not_stored(tmp_path, monkeypatch):
    """A handful of these urls answer with a couple of dozen bytes (prop.
    2003/04:181 serves 24). Storing that would leave a record claiming a body it
    has not got, and the parse would yield an empty artifact that reads as a
    parsed document."""
    record = _record(tmp_path, "2003/04:181")
    _stub(monkeypatch, {record["url"]: "   \n "})
    assert pr.download_one(tmp_path, None, record, delay=0) is False
    assert not (layout.fa_dir(tmp_path, "prop", "2003/04:181")
                / "2003-04-181.html").exists()
    on_disk = json.loads(compress.read_text(
        layout.fa_record_file(tmp_path, "prop", "2003/04:181")))
    assert on_disk["files"] == []              # left exactly as it was
    assert "body_format" not in on_disk


def test_sync_reports_empties_and_walks_past_a_bad_response(tmp_path, monkeypatch):
    """1 700+ independent one-shot fetches with nothing chaining them: one 500
    must not strand the rest. A rerun retries it -- the record still has no
    body, so it stays in `pending`."""
    logged = []
    good = _record(tmp_path, "1983/84:40")
    empty = _record(tmp_path, "1984/85:50",
                    url="http://data.riksdagen.se/dokument/EMPTY")
    broken = _record(tmp_path, "1985/86:60",
                     url="http://data.riksdagen.se/dokument/BROKEN")
    _stub(monkeypatch, {good["url"]: BODY, empty["url"]: "",
                        broken["url"]: requests.HTTPError("500 Server Error")})
    seen, fetched, n_empty = pr.sync(tmp_path, log=logged.append, delay=0)
    assert (seen, fetched, n_empty) == (3, 1, 1)
    assert any("500 Server Error" in m for m in logged)
    assert any("served an empty body" in m for m in logged)
    # both unfetched documents remain on the work list for a rerun
    assert sorted(r["basefile"] for r in pr.pending(tmp_path)) == [
        "1984/85:50", "1985/86:60"]


def test_sync_does_not_swallow_a_non_network_failure(tmp_path, monkeypatch):
    """Only a bad response is this document's own problem. A write failure or a
    malformed record is an environment fault and must abort the run rather than
    be logged past (rule:no-catch-log-continue)."""
    record = _record(tmp_path, "1983/84:40")
    _stub(monkeypatch, {record["url"]: BODY})
    monkeypatch.setattr(pr.compress, "write_download",
                        lambda *a, **kw: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        pr.sync(tmp_path, delay=0)
