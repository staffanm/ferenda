"""Harvester for the UN Treaty Collection: each treaty's status *and* its text.

The curated instrument list drives the harvest, and each treaty is fetched
twice from two different publishers, because no one page carries both:

  * the **status** -- ratifications, reservations, entry into force -- from
    ``ViewDetailsIII.aspx`` (an ASP.NET page that answers unattended clients
    directly). The MTDSG carries status only; it holds no treaty text at all,
    which is why an untc artifact used to have an empty structure and nothing
    to anchor a citation on.
  * the **authentic text**, from the treaty's depositary (``text.url`` in
    ``data/treaties.json``). Deliberately not from the UNTS itself: the UNTS
    reproduces each instrument as registered, which means a scan -- volume 999
    carries the ICCPR across 92 pages with an image on all 92, and volume 1161
    the Berne Convention across 44 of 44. The depositaries publish the same
    authentic text as HTML or as a born-digital PDF.

OHCHR sits behind the same Cloudflare challenge as the ICJ, so its pages come
through `lib.browser.DetachedChrome`; the two PDF texts answer ordinary HTTP.
The stored record is the raw page or PDF; parse scrapes it offline.  The corpus is a tiny fixed set, so the shared walk runs with **no
watermark** (the edpb/rs idiom: a complete listing has no depth to stop short
of) -- a page already on disk is skipped unless ``--full`` re-fetches it (a new
ratification changes the participation table, so a periodic ``--full`` refreshes
status).
"""

import time
from pathlib import Path

from ..lib import browser, compress
from ..lib.harvest import ItemKey, verify_pdf, walk
from ..lib.net import HARVESTER_UA as USER_AGENT
from ..lib.net import make_session, request
from .model import DETAIL, load_treaties

# a Chrome profile shared across the run, so one Cloudflare challenge clears
# every OHCHR fetch rather than one per treaty
PROFILE = ".chrome-profile"
# what the OHCHR page must carry to be the treaty and not the challenge page
OHCHR_MARKER = "Article"


def page_path(root, unts):
    return Path(root) / (unts + ".html")


def text_path(root, entry):
    """The treaty's authentic text, beside its status page. The suffix follows
    the reader the curated entry names, so `parse` needs no sniffing."""
    suffix = ".pdf" if entry["text"]["reader"] == "pdf" else ".text.html"
    return Path(root) / (entry["unts"] + suffix)


def fetch_page(session, entry):
    """The raw MTDSG status page for one curated treaty.  The site answers 200
    even for an unknown id, so a fetched page must carry the entry-into-force
    control that every real treaty page has, else the id/scrape has drifted."""
    response = request(session, "GET", DETAIL % (entry["mtdsg_no"], entry["chapter"]),
                       timeout=120)
    if "tcrptEIF" not in response.text:
        raise ValueError("MTDSG %s: not a treaty status page (no entry-into-force)"
                         % entry["mtdsg_no"])
    return response.text


def fetch_text(chrome, entry):
    """The treaty's authentic text as its depositary publishes it.

    Everything goes through Chrome, not only the Cloudflare-walled OHCHR pages:
    un.org serves the UNCLOS PDF a 403 to our harvester's user agent and a 200
    to a browser's, so the two transports were one UA quirk apart. One is
    simpler than two plus a per-host exception, and the browser is already
    running for the twelve OHCHR texts."""
    source = entry["text"]
    if source["reader"] == "pdf":
        data = chrome().pdf(source["url"])
        verify_pdf(data)
        return data
    html = chrome().html(source["url"], marker=OHCHR_MARKER)
    # the challenge page is served under the treaty's own URL, and it holds no
    # article. Storing it would publish "Just a moment..." as a treaty text
    # (rule:fail-fast).
    if OHCHR_MARKER not in html:
        raise ValueError("%s: %s served no article text"
                         % (entry["mtdsg_no"], source["url"]))
    return html


def resolve(session, root, entry, chrome=None, full=False, delay=0.3):
    """Fetch a treaty's status page and its text when missing or forced;
    returns whether it wrote either."""
    status, text = page_path(root, entry["unts"]), text_path(root, entry)
    wrote = False
    if full or not compress.exists(status):
        compress.write_download(status, fetch_page(session, entry))
        time.sleep(delay)
        wrote = True
    if full or not compress.exists(text):
        compress.write_download(text, fetch_text(chrome, entry))
        time.sleep(delay)
        wrote = True
    return wrote


def list_basefiles(root):
    # the status page names the treaty; `*.text.html` and `*.pdf` are its
    # companions and must not read as documents of their own
    return [stem for stem in compress.list_stems(root, "*.html")
            if not stem.endswith(".text")]


def sync(root, full=False, only=None, limit=None, delay=0.3, log=print):
    """Fetch the curated treaties' status pages through the shared download loop
    (`lib.harvest.walk`, no watermark -- the curated list is walked whole every
    run). ``limit`` is that loop's: it caps pages actually *fetched*, not entries
    looked at. Returns (seen, fetched)."""
    root = Path(root)
    session = make_session(USER_AGENT)
    treaties = load_treaties()
    if only and only not in treaties:
        raise ValueError("no curated UN treaty %s" % only)
    entries = [treaties[only]] if only else list(treaties.values())

    # One Chrome for the whole run, started only if an OHCHR text is actually
    # wanted: a run that has every text on disk should not pay for a browser.
    opened = []

    def chrome():
        if not opened:
            session_ = browser.DetachedChrome(root / PROFILE, settle=8.0)
            session_.__enter__()
            opened.append(session_)
        return opened[0]

    try:
        return _walk(session, root, entries, chrome, full, only, limit, log)
    finally:
        for session_ in opened:
            session_.__exit__(None, None, None)


def _walk(session, root, entries, chrome, full, only, limit, log):
    result = walk(
        entries,
        resolve=lambda entry: resolve(session, root, entry, chrome=chrome,
                                      full=full, delay=0.3),
        # a treaty counts as downloaded only when *both* halves are on disk:
        # the status page alone is what the source used to hold, and it carries
        # no text at all
        item_key=lambda entry: ItemKey(
            entry["unts"],
            compress.exists(page_path(root, entry["unts"]))
            and compress.exists(text_path(root, entry))),
        watermark=None,
        full=full,
        limit=limit,
        scope="untc",
        count_label="fetched",
        total=len(entries),
        log=log,
    )
    return result.seen, result.new
