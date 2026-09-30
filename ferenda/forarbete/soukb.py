"""Body re-downloader for the KB-digitised SOUs (1922-1999).

KB (Kungliga biblioteket) scanned and OCR'd every SOU (Statens offentliga
utredningar) from 1922 up to the point regeringen.se took over (2000-), and
publishes them behind a single HTML index at `https://sou.kb.se/`. Unlike the
KB proposition scans (`forarbete/propkb.py`), there is **no ABBYY XML sibling**:
the scanned, OCR-layered **PDF is the body**. So this module does not fetch a
facsimile beside an existing text body -- it fetches the *document itself* and
writes a fresh harvested record for it, `files` pointing at the PDF.

**The index is the source of truth.** We forget the legacy `soukb` records
entirely and rebuild from what the index lists today: the old `regina.kb.se`
start URL is dead, and the basefile now comes from the index label, not from an
old record. Each SOU is an `<a>` whose *text* is the number+series
(`1922:1 första serien`) and whose *title* is the anchor's `next_sibling` text
node (a `NavigableString`, up to the `<br>`) -- under bs4's `html.parser`
`a.tail` is empty, so `a.next_sibling` is the only handle on the title.

**Multi-volume SOUs repeat a label.** 128 basefiles list several distinct URNs
(e.g. `1987:3` -> 28 volumes of the Långtidsutredning, each `Bil. N`). Those are
the volumes of one document, so a basefile's `files` becomes a list of
`<slug>.pdf`, `<slug>-1.pdf`, ... -- named in index order, the same naming
`download.download_document` uses, and listed in reading order with each
volume's KB title beside it (`record_of`) -- one record per basefile, never a
collision onto one file.

Sizing: the PDFs are scanned page images, 10-150 MB each (the 1922:1fs default
is 149 MB), so the full crawl is **hundreds of GB**. This is its own verb
(`lagen forarbete soukb-scans`), never part of `harvest`, and resumable per part
(a PDF already on disk is skipped), so build + verify on a `--limit`ed slice and
do not launch the full crawl without an explicit go.
"""

import re
import time

from bs4 import BeautifulSoup, NavigableString

from ..lib import compress, layout, util
from ..lib.harvest import fetch_worklist, write_record
from ..lib.net import BROWSER_UA_TRANSPORT, open_session, request
from . import volumes

TYPE = "sou"
KB_HOST = "urn.kb.se"
# what the reading order of a set of volumes decides in its record
VOLUME_FIELDS = ("files", "volumes", "title", "url", "orig_url")
INDEX_URL = "https://sou.kb.se/"
_LABEL = re.compile(r"^\d{4}:\d+")
_PDF_HREF = re.compile(r".*\.pdf$")


def basefile_of(label):
    """The SOU basefile for an index link label. Ports the legacy SOUKB
    transform (`ferenda/sources/legal/se/sou.py`) and broadens it to the forms
    that regex skipped: `1922:1 första serien` -> `1922:1fs` (the first year ran
    34 issues then restarted, so the retroactive "första serien" disambiguates a
    second `1922:1`); a letter suffix -> lowercased (`1989:53A` -> `1989:53a`,
    `1994:11E` -> `1994:11e`); a combined double issue -> hyphenated
    (`1952:16/17` -> `1952:16-17`, keeping the basefile slash-free so it stays one
    flat record, never a subdirectory)."""
    return (label.replace(" första serien", "fs")
                 .replace("/", "-").replace(" ", "").lower())


def walk_index(session):
    """GET the KB SOU index and return `[(basefile, [title, ...], [urn_url, ...]),
    ...]` in index order, grouping the multi-volume entries that repeat a label
    onto one basefile (its two lists are the volumes' titles and urls, in index
    order). Raises on an anchor whose text is not a SOU label, so a changed index
    fails loudly rather than silently dropping documents
    (rule:errors-drive-retry-use-raise)."""
    soup = BeautifulSoup(request(session, "GET", INDEX_URL).text, "html.parser")
    titles = {}                                   # basefile -> [title, ...]
    urls = {}                                     # basefile -> [urn_url, ...]
    for a in soup.find_all("a", href=True):
        if "sou-" not in a["href"]:
            continue
        label = a.get_text().strip()
        if not _LABEL.match(label):
            raise ValueError("KB SOU index: unparseable label %r" % label)
        basefile = basefile_of(label)
        sibling = a.next_sibling
        titles.setdefault(basefile, []).append(
            str(sibling).strip() if isinstance(sibling, NavigableString) else "")
        urls.setdefault(basefile, []).append(a["href"])
    return [(b, titles[b], urls[b]) for b in urls]


def pdf_url(session, urn_url):
    """Resolve one URN resolver url to its digark scan-PDF url. The resolver
    redirects to a `weburn.kb.se/metadata/.../SOU_*.htm` page carrying exactly one
    `.pdf` link. Raises if none is found, so a dead or restructured entry fails
    loudly (rule:errors-drive-retry-use-raise)."""
    soup = BeautifulSoup(request(session, "GET", urn_url).text, "html.parser")
    link = soup.find("a", href=_PDF_HREF)
    if link is None:
        raise ValueError("no scan PDF at %s" % urn_url)
    return link["href"]


def record_of(basefile, titles, urns, files):
    """The record for one index entry. A single volume is the document; a set
    of volumes is listed in reading order (`volumes.kb_order`: the report,
    its parts, then its appendices), with each file's KB title in `volumes`,
    and named after the report. KB's index order is no order: sou/1997:116
    lists its appendix before Barnkommitténs huvudbetänkande, and the record
    used to take its title, its url and its body from whichever came first."""
    if len(files) == 1:
        order = [0]
    else:
        ranked, dropped = volumes.kb_order(files, titles)
        order = ([files.index(f) for f, _label in ranked]
                 + [files.index(f) for f in files if f in dropped])
    record = {"type": TYPE, "basefile": basefile,
              "identifier": "SOU " + basefile, "title": titles[order[0]],
              "date": None, "orig_url": urns[order[0]], "url": urns[order[0]],
              "files": [files[i] for i in order]}
    if len(files) > 1:
        record["volumes"] = [titles[i] for i in order]
    return record


def download_one(session, root, entry, delay):
    """Fetch every part-PDF of one index entry into `root/sou/` and write a fresh
    harvested record whose `files` are those PDFs (the PDF is the body). Returns
    True when it fetched at least one part, False when every part was already
    on disk, so an interrupted run resumes from disk rather than re-downloading
    hundreds of GB.

    With every part on disk, only a set of volumes has its record brought up
    to date: its reading order, its volume titles, and the report's own title
    and url (`record_of`) -- which is how a rerun reorders the sets recorded
    before `volumes` existed, without a download. The record of a single
    volume is left as it is, and so is a record some other route owns: KB's
    file name for a 1999 SOU is the same as regeringen.se's, and sou/1999:78's
    regeringen.se record is the document's (rule:fail-fast would be wrong here
    -- the two sources meet by design at the era boundary).

    A file is named by its place in KB's index (`<slug>.pdf`, `<slug>-1.pdf`,
    …), as it always was, so the files already on disk keep their names; only
    the record's `files` put them in reading order."""
    basefile, titles, urns = entry
    slug = util.basefile_slug(basefile)
    files = [slug + ("" if i == 0 else "-%d" % i) + ".pdf" for i in range(len(urns))]
    recpath = layout.fa_record_file(root, TYPE, basefile)
    dests = [layout.fa_dir(root, TYPE, basefile) / name for name in files]
    record = record_of(basefile, titles, urns, files)
    if all(compress.exists(d) for d in dests):
        stored = compress.read_json(recpath) if compress.exists(recpath) else None
        if stored is None:
            write_record(recpath, record)
        elif (len(files) > 1 and KB_HOST in (stored.get("orig_url") or "")
              and any(stored.get(k) != record[k] for k in VOLUME_FIELDS)):
            write_record(recpath, stored | {k: record[k] for k in VOLUME_FIELDS})
        return False                              # resumable: parts already done
    fetched = False
    for urn_url, dest in zip(urns, dests, strict=True):
        if compress.exists(dest):
            continue                              # resumable: this part is done
        data = request(session, "GET", pdf_url(session, urn_url)).content
        # load-bearing validation of untrusted remote bytes: KB serves the scan
        # as application/octet-stream, so the magic is the only proof we got a PDF
        # and not an error page that would parse to an empty body forever
        if data[:4] != b"%PDF":
            raise ValueError("%s: KB served no PDF at %s (%d bytes)"
                             % (basefile, urn_url, len(data)))
        compress.write_download(dest, data)       # .pdf -> stored plain
        fetched = True
        time.sleep(delay)
    write_record(recpath, record)
    return fetched


def sync(root, limit=None, delay=0.5):
    """Re-download every SOU the KB index lists (1922-1999) as its own body under
    `root/sou/`. Walks the index as the source of truth, forgetting the legacy
    soukb records; each basefile's record is overwritten with a fresh one pointing
    at the fetched PDF(s). Resumable: an entry already complete on disk is skipped.
    Returns (seen, fetched).

    The work-list is enumerated up front (the whole index in one GET) so the
    progress line carries a real total and an ETA (rule:one-line-progress); the
    caller prints the final stdout summary. `--limit` stops after that many entries
    actually fetched (a test slice)."""
    session = open_session(BROWSER_UA_TRANSPORT)
    return fetch_worklist(
        walk_index(session),
        lambda entry: download_one(session, root, entry, delay),
        scope="soukb", limit=limit)
