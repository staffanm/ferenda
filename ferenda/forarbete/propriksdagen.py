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

**The scans** (`scan_sync`, ``lagen forarbete propriksdagen-scan``) replace that
HTML body. The HTML is riksdagen's own OCR, and a poor one ("tUl riksdagen",
"Slockholm", "hittiUs" on the first page of prop. 1993/94:130); its page breaks
do not match the printed pages either (259 sections, 110 pages). Riksdagen
publishes the scan it was read from as the document's PDF attachment -- page
images with no text layer. So this walks riksdagen's own listing riksmöte by
riksmöte, takes every proposition that has a PDF, stores it as the body and the
facsimile (`layout.fa_facsimile_pdf`, one file for both), OCRs it into the
förarbete OCR copy (`layout.fa_ocr_pdf`, which parse reads first) when it has no
text layer, and writes the record from the listing entry, its url the
document's landing page on riksdagen.se. It builds the records itself, so it
runs on an empty store as well as over the HTML-bodied records. A record some
other route owns (a regeringen.se PDF, a Word body) is left alone.
"""

import subprocess
import time
from pathlib import Path
from urllib.parse import quote

import requests

from ..lib import compress, layout
from ..lib.harvest import fetch_worklist, write_record
from ..lib.net import BROWSER_UA_TRANSPORT, HARVESTER_TRANSPORT, open_session, request
from ..lib.pdftext import ocr_pdf, pdftotext_text
from ..lib.util import basefile_slug
from . import riksdagen

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
    session = open_session(BROWSER_UA_TRANSPORT)
    empty = 0

    def fetch(record):
        nonlocal empty
        # a bad response is this one document's problem: 1 700+ independent
        # one-shot fetches with nothing chaining them, so one 500 or dropped
        # connection must not strand the rest. Nothing else is caught -- a
        # write failure or a malformed record is an environment fault and
        # aborts (rule:no-catch-log-continue: recorded here, retried on a rerun)
        try:
            stored = download_one(root, session, record, delay)
        except (requests.RequestException, ValueError,
                subprocess.CalledProcessError) as exc:
            log("  %s: %s (retried on a rerun)" % (record["basefile"], exc))
            return False
        if not stored:
            empty += 1
            log("  %s: riksdagen served an empty body at %s"
                % (record["basefile"], record["url"]))
        return stored

    # `limit` slices the work-list rather than stopping the walk: every record
    # here needs a fetch, so the two are the same cut and the progress line's
    # total is then the run's own
    seen, fetched = fetch_worklist(pending(root)[:limit], fetch,
                                   scope="prop bodies")
    return seen, fetched, empty


# the listing, one riksmöte at a time: un-narrowed it stops at 10 000 entries
# (`riksdagen.iter_pages`), and the scanned era alone is ~4 000 propositions
SCAN_LISTING = (riksdagen.API + "/dokumentlista/?doktyp=prop&utformat=json"
                "&sort=datum&sortorder=desc&sz=200")
# The last riksmöte whose propositions riksdagen serves as scans, measured
# (2026-09) over the whole listing: every riksmöte from 1971 to 1994/95 lists a
# PDF for nearly every proposition, and the sampled ones are page images with no
# text layer. From 1995/96 most entries list no PDF, and those that do are
# born-digital. The listing's `status` does not tell the two apart: the scans
# read "importerad" up to 1989/90 and "digitaliserad" from 1990/91, and so do
# born-digital PDFs of 1996/97 to 2001/02. Two 1973 PDFs are born-digital too, so
# the file decides whether it needs OCR, not the riksmöte.
LAST_SCANNED = 1994
# riksdagen redirects this to the landing page, whose address also carries a
# slug of the title (…/proposition/andringar-i-brottsbalken-m-m_gh03130/)
LANDING_PREFIX = "https://www.riksdagen.se/sv/dokument-och-lagar/dokument/"
LANDING = LANDING_PREFIX + "proposition/_%s/"
# the prefix stops at "dokument/": riksdagen files some propositions under
# another document kind, and 212 of the scanned ones land on
# ".../dokument/regeringens-skrivelse/…_gi03225/" (prop. 1994/95:225)


def scanned_entries(session, delay):
    """Every listing entry with a PDF from the scanned era (to `LAST_SCANNED`),
    newest riksmöte first."""
    return [entry
            for rm in riksdagen.riksmoten(LAST_SCANNED)
            for page in riksdagen.iter_pages(
                session, SCAN_LISTING + "&rm=" + quote(rm), delay)
            for entry in riksdagen._docs(page)
            if riksdagen.pdf_fil(entry) is not None]


def claimable(record):
    """Whether the scan may be this document's body: nothing is stored yet, or
    what is stored is riksdagen's own HTML OCR (or no body at all), or it is
    already this route's. A record another route owns keeps its body."""
    return (record is None or not record.get("files")
            or record.get("body_format") == BODY_FORMAT
            or (record.get("url") or "").startswith(LANDING_PREFIX))


def scan_one(session, root, entry, delay, log=print):
    """Store one proposition's PDF, OCR it when it is a scan, and write its
    record. Returns True when it did work, False when the document was already
    done or is not this route's."""
    basefile = riksdagen.basefile_of(entry)
    recpath = layout.fa_record_file(root, TYPE, basefile)
    record = compress.read_json(recpath) if compress.exists(recpath) else None
    if not claimable(record):
        log("  %s: %s owns the body -- left alone"
            % (basefile, (record or {}).get("url")))
        return False
    # the record is written last, so one pointing at its landing page is done
    if record is not None and record.get("url", "").startswith(LANDING_PREFIX):
        return False                                  # resumable: already done
    fil = riksdagen.pdf_fil(entry)
    assert fil is not None, "scanned_entries passed %s without a PDF" % basefile
    name = basefile_slug(basefile) + ".pdf"
    scan = layout.fa_dir(root, TYPE, basefile) / name
    if not compress.exists(scan):
        data = request(session, "GET", fil["url"], timeout=120).content
        # load-bearing validation of untrusted remote bytes: an error page
        # served with 200 must not become the document's body and facsimile
        if data[:4] != b"%PDF":
            raise ValueError("%s: %s is not a PDF" % (basefile, fil["url"]))
        compress.write_download(scan, data)           # .pdf -> stored plain
        time.sleep(delay)
    if not pdftotext_text(scan).strip():              # a scan: page images only
        ocr_pdf(scan, "swe", dest=layout.fa_ocr_pdf(TYPE, basefile))
    landing = request(session, "HEAD", LANDING % entry["dok_id"].lower(),
                      allow_redirects=True).url
    write_record(recpath, {
        "type": TYPE, "basefile": basefile,
        "identifier": "Prop. " + basefile, "title": entry["titel"],
        "date": entry["datum"], "url": landing, "dok_id": entry["dok_id"],
        "files": [name]})
    return True


def scan_sync(root, limit=None, delay=0.5, log=print):
    """Store, OCR and record every proposition riksdagen lists a PDF for in the
    scanned era. Returns (seen, done). Resumable: a document whose record
    points at its landing page is skipped. A failed request, a response that is
    no PDF and an OCR that fails on one scan are that one document's problem:
    recorded, and retried on a rerun. A failed write is the environment's and
    aborts (rule:no-catch-log-continue)."""
    session = open_session(HARVESTER_TRANSPORT)
    entries = scanned_entries(session, delay)
    log("forarbete propriksdagen-scan: %d propositions with a PDF listed"
        % len(entries))

    def fetch(entry):
        try:
            return scan_one(session, root, entry, delay, log)
        except (requests.RequestException, ValueError,
                subprocess.CalledProcessError) as exc:
            log("  %s: %s (retried on a rerun)"
                % (entry.get("dok_id"), exc))
            return False

    return fetch_worklist(entries, fetch, scope="prop scans", limit=limit,
                          count_label="scanned")
