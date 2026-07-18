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
the parts of one document, so a basefile's `files` becomes a list
(`<slug>.pdf`, `<slug>-1.pdf`, ...) in index order -- the same naming
`download.download_document` uses -- one record per basefile, never a collision
onto one file.

Sizing: the PDFs are scanned page images, 10-150 MB each (the 1922:1fs default
is 149 MB), so the full crawl is **hundreds of GB**. This is its own verb
(`lagen forarbete soukb-scans`), never part of `harvest`, and resumable per part
(a PDF already on disk is skipped), so build + verify on a `--limit`ed slice and
do not launch the full crawl without an explicit go.
"""

import json
import re
import time

from bs4 import BeautifulSoup, NavigableString

from ..lib import compress, layout, util
from ..lib.net import BROWSER_UA, make_session, request

TYPE = "sou"
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
    """GET the KB SOU index and return `[(basefile, title, [urn_url, ...]), ...]`
    in index order, grouping the multi-volume entries that repeat a label onto one
    basefile (its `urn_url` list is the volumes, in order). The title is the first
    volume's. Raises on an anchor whose text is not a SOU label, so a changed index
    fails loudly rather than silently dropping documents
    (rule:errors-drive-retry-use-raise)."""
    soup = BeautifulSoup(request(session, "GET", INDEX_URL).text, "html.parser")
    titles = {}                                   # basefile -> first volume's title
    urls = {}                                     # basefile -> [urn_url, ...]
    for a in soup.find_all("a", href=True):
        if "sou-" not in a["href"]:
            continue
        label = a.get_text().strip()
        if not _LABEL.match(label):
            raise ValueError("KB SOU index: unparseable label %r" % label)
        basefile = basefile_of(label)
        sibling = a.next_sibling
        if basefile not in urls:
            titles[basefile] = (str(sibling).strip()
                                if isinstance(sibling, NavigableString) else "")
            urls[basefile] = []
        urls[basefile].append(a["href"])
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


def download_one(session, root, entry, delay):
    """Fetch every part-PDF of one index entry into `root/sou/` and write a fresh
    harvested record whose `files` are those PDFs (the PDF is the body). Returns
    True when it fetched at least one part, False when the entry was already
    complete on disk (record + all parts), so an interrupted run resumes from disk
    rather than re-downloading hundreds of GB."""
    basefile, title, urns = entry
    slug = util.basefile_slug(basefile)
    files = [slug + ("" if i == 0 else "-%d" % i) + ".pdf" for i in range(len(urns))]
    recpath = layout.fa_record_file(root, TYPE, basefile)
    dests = [layout.fa_dir(root, TYPE, basefile) / name for name in files]
    if compress.exists(recpath) and all(d.exists() for d in dests):
        return False                              # resumable: entry already done
    fetched = False
    for urn_url, dest in zip(urns, dests, strict=True):
        if dest.exists():
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
    record = {"type": TYPE, "basefile": basefile,
              "identifier": "SOU " + basefile, "title": title,
              "date": None, "orig_url": urns[0], "url": urns[0], "files": files}
    compress.write_download(recpath, json.dumps(record, ensure_ascii=False,
                                                 indent=2))
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
    session = make_session(BROWSER_UA)
    worklist = walk_index(session)
    rep = util.Reporter()
    seen = fetched = 0
    for entry in worklist:
        seen += 1
        if download_one(session, root, entry, delay):
            fetched += 1
        rep.update(seen, len(worklist), scope="soukb", fetched=fetched)
        if limit and fetched >= limit:
            break
    rep.done()
    return seen, fetched
