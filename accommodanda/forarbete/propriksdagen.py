"""Body fetcher for the propositions riksdagen holds but we never fetched.

1 756 proposition records carry ``files: []`` and a ``data.riksdagen.se`` url --
1 742 of them from before 1999, the era regeringen.se's listing does not reach.
They are not documents without a body: that url *is* riksdagen's body endpoint,
and it serves the whole proposition as OCR'd HTML. What came across in the
legacy import was the sibling ``dokumentstatus`` XML -- a 1.3 kB envelope of
metadata and pointers -- so the import found no body file and wrote none. Every
one of them has been one request away ever since.

This module makes that request. It is the same route `rskr.py` takes for every
riksdagsskrivelse and `riksdagen.py` now takes for a betänkande riksdagen never
attached a printed PDF to (status "saknas"): GET the record's own url, store the
HTML beside the record, point `files` at it.

The body is riksdagen's 2007 scanning export -- Word-generated HTML, ``<div
class=Section1>`` of ``<p class=MsoNormal>``, opening "Observera att dokumentet
är inskannat och fel kan förekomma". `parse.LEGACY_HTML_PARAS` already routes
that shape through `legacy_formats.riksdagen_mso_paras` under the
``skanning2007`` body_format, which 2 334 proposition records in the corpus
already use, so this adds no parser. Verified across the era before building --
one document per half-decade from 1972 to 2013, 25 to 17 538 paragraphs each,
none unreadable.

Not part of `harvest`: it is a one-time repair of an import gap, keyed on
records that exist, so a listing walk can never reach it. Resumable -- a record
that gained a body is skipped, so a killed run is just rerun.
"""

import time
from pathlib import Path

import requests

from ..lib import compress, layout
from ..lib.harvest import write_record
from ..lib.net import BROWSER_UA, make_session, request
from ..lib.util import Reporter, basefile_slug

TYPE = "prop"
BODY_FORMAT = "skanning2007"    # riksdagen's OCR'd Word-HTML export
HOST = "data.riksdagen.se"


def pending(root):
    """Every proposition record with no body and a riksdagen body url, oldest
    first -- the repair's work list. Oldest first because the gap is almost
    entirely pre-1999, and finishing an era at a time makes a killed run's
    progress legible."""
    out = []
    for rec in compress.glob(Path(root) / TYPE, "*/*.json"):
        if rec.name.startswith("."):
            continue
        record = compress.read_json(rec)
        if record.get("files") or HOST not in (record.get("url") or ""):
            continue
        out.append(record)
    return sorted(out, key=lambda r: r["basefile"])


def download_one(root, session, record, delay):
    """Fetch one proposition's body and point its record at it. Returns True
    when a body was stored, False when riksdagen served nothing.

    An empty body is riksdagen's, not ours -- a handful of these urls answer
    with a couple of dozen bytes (prop. 2003/04:181 serves 24). Storing that
    would leave a record claiming a body it does not have, and the parse would
    yield an empty artifact that looks like a parsed document; the record is
    left body-less instead, exactly as it is now, and reported."""
    html = request(session, "GET", record["url"]).text
    if not html.strip():
        return False
    name = basefile_slug(record["basefile"]) + ".html"
    compress.write_download(
        layout.fa_dir(root, TYPE, record["basefile"]) / name, html)
    record["files"] = [name]
    record["body_format"] = BODY_FORMAT
    write_record(layout.fa_record_file(root, TYPE, record["basefile"]), record)
    time.sleep(delay)
    return True


def sync(root, limit=None, delay=0.5, log=print):
    """Fetch the missing bodies. Returns (seen, fetched, empty).

    A per-document failure is recorded and the walk continues: these are 1 700+
    independent one-shot fetches with nothing chaining them, so one 500 must not
    strand the rest (rule:no-catch-log-continue). Rerun to retry -- a record
    that gained a body drops out of `pending`."""
    session = make_session(BROWSER_UA)
    todo = pending(root)[:limit]
    rep = Reporter()
    fetched = empty = 0
    for seen, record in enumerate(todo, start=1):
        # a bad response is this one document's problem: 1 700+ independent
        # one-shot fetches with nothing chaining them, so one 500 or dropped
        # connection must not strand the rest. Nothing else is caught -- a
        # write failure or a malformed record is an environment fault and
        # aborts (rule:no-catch-log-continue: recorded here, retried on a rerun)
        try:
            stored = download_one(root, session, record, delay)
        except requests.RequestException as exc:
            log("  %s: %s (retried on a rerun)" % (record["basefile"], exc))
        else:
            fetched += stored
            if not stored:
                empty += 1
                log("  %s: riksdagen served an empty body at %s"
                    % (record["basefile"], record["url"]))
        rep.update(seen, len(todo), scope="prop bodies", fetched=fetched)
    rep.done()
    return len(todo), fetched, empty
