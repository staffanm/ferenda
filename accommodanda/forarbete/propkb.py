"""Facsimile fetcher for the KB two-chamber proposition scans (1867-1970).

KB (Kungliga biblioteket) publishes each scanned proposition twice at
`weburn.kb.se`: the ABBYY OCR **XML** (small, the text) and the **scan PDF**
(large, the page images -- what the printed page actually looked like). The old
codebase downloaded only the XML for all but 1,769 of them, because 79 GB of
scans did not fit the disk of the day; `options.py` called that state
`metadataonly`, which named the *fetch* choice, not a missing document. So the
text layer is complete: **no proposition is absent**, and this module adds no
new documents. What it adds is the facsimile -- the proof view (`lib/facsimile`)
that every other paged source already offers -- for the 17,297 XML-only records.

**No index crawl.** The old `PropKB` walked `riksdagstryck.kb.se` to discover
basefiles; we no longer need to, because every propkb record already carries the
XML url it was fetched from, and the scan PDF is that url's mechanical sibling
(the old downloader derived one from the other in reverse:
`url.replace(".pdf", ".xml").replace("pdf/web", "xml")`):

    .../tvåkammarriksdagen/xml/1937/web_prop_1937____141/prop_1937____141.xml
    .../tvåkammarriksdagen/pdf/web/1937/web_prop_1937____141/prop_1937____141.pdf

Deriving beats re-walking on every axis that matters: it needs no listing
requests, it cannot mint a basefile the corpus does not already have, and it is
resumable per record. The record set is the work list.

**The PDF is a facsimile, not a body, and the record is never touched.** The
scan lands at the `layout.fa_facsimile_pdf` rule and is resolved from disk by
existence (`api._fa_pdf`), exactly like the mirrored SFS PDFs. Two reasons, both
load-bearing:

  * `parse._harvested_body` routes on file extension and prefers a PDF over an
    XML, so listing the scan in `files` would silently flip 17,297 bodies off
    KB's ABBYY OCR onto a pdftotext of the same scan -- a body change disguised
    as an image fetch. The ABBYY XML stays the body.
  * the record is a *parse input* (`build.fa_parse_inputs`, hashed by content in
    `build.hash_files`), so writing any key into it -- even one parse never reads
    -- would re-stale all 17,297 prop parses and re-run the ABBYY parse of the
    whole KB century for a set of images.

So this fetch is genuinely inert with respect to every other phase: it adds
files nothing else reads and mutates nothing.

The 1,769 records whose body already *is* the KB scan (their `orig_url` is the
pdf url) need nothing: the same rule already resolves to that PDF.

Sizing, measured over the 1,769 KB scans already on disk: 4.6 MB mean, 2.9 MB
median, 25 MB max -> **~79 GB** projected for the remaining 17,297 (~51 GB on
the median, so treat 79 GB as the pessimistic end). Those 1,769 are the ones the
old crawl chose to fetch in full, so they are not a random sample of the corpus
and the true figure may sit below the range. This is a deliberate, one-time bulk
fetch: it is not part of any incremental build and no other phase depends on it.
"""

import json
import time
from pathlib import Path

from ..lib import compress, layout, util
from ..lib.net import BROWSER_UA, make_session, request

TYPE = "prop"
HOST = "weburn.kb.se"


def scan_url(xml_url):
    """The scan-PDF url for a propkb record's ABBYY XML url -- the inverse of the
    old PropKB's `.replace(".pdf", ".xml").replace("pdf/web", "xml")`. Raises
    ValueError on a url that is not a KB xml url, so a mis-keyed record fails
    loudly rather than fetching something arbitrary
    (rule:errors-drive-retry-use-raise)."""
    if HOST not in xml_url or "/xml/" not in xml_url or not xml_url.endswith(".xml"):
        raise ValueError("not a KB ABBYY xml url: %s" % xml_url)
    return xml_url.replace("/xml/", "/pdf/web/", 1)[:-len(".xml")] + ".pdf"


def wanted(record):
    """Whether `record` is a propkb document still missing its scan. False for a
    record whose body already is the KB scan (its `orig_url` is the pdf url --
    the 1,769 the old crawl fetched in full) and for every non-KB prop."""
    url = record.get("orig_url") or ""
    return HOST in url and "/xml/" in url and url.endswith(".xml")


def download_one(session, root, record, delay):
    """Fetch one record's scan PDF to its `fa_facsimile_pdf` slot under
    `root/prop/`. Returns True when it fetched, False when the scan was already
    on disk (so an interrupted run resumes from disk, not from a bookkeeping
    key). The record is deliberately not written -- see the module docstring."""
    basefile = record["basefile"]
    dest = layout.fa_dir(root, TYPE, basefile) / layout.fa_facsimile_pdf(TYPE, basefile).name
    if dest.exists():
        return False                                  # resumable: already fetched
    data = request(session, "GET", scan_url(record["orig_url"])).content
    # load-bearing validation of untrusted remote bytes: KB serves the scan as
    # application/octet-stream, so the magic is the only proof we got a PDF and
    # not an error page that would render as a blank facsimile forever
    if data[:4] != b"%PDF":
        raise ValueError("%s: KB served no PDF at %s (%d bytes)"
                         % (basefile, scan_url(record["orig_url"]), len(data)))
    compress.write_download(dest, data)               # .pdf -> stored plain
    time.sleep(delay)
    return True


def sync(root, limit=None, delay=0.5):
    """Fetch the missing KB scan PDFs for every propkb record under `root/prop/`.
    Resumable: a record whose scan is already stored is skipped, so an
    interrupted run continues where it stopped. Returns (seen, fetched).

    The work-list is enumerated up front (every propkb record `wanted` needs a
    scan) so the progress line carries a real total and an ETA
    (rule:one-line-progress); the caller prints the final stdout summary."""
    session = make_session(BROWSER_UA)
    worklist = [r for r in (json.loads(compress.read_text(p))
                            for p in sorted(compress.glob(Path(root) / TYPE, "*/*.json")))
                if wanted(r)]
    rep = util.Reporter()
    seen = fetched = 0
    for record in worklist:
        seen += 1
        if download_one(session, root, record, delay):
            fetched += 1
        rep.update(seen, len(worklist), scope="propkb", fetched=fetched)
        if limit and fetched >= limit:
            break
    rep.done()
    return seen, fetched
