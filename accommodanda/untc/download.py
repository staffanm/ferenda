"""Harvester for the UN Treaty Collection (MTDSG status pages).

The curated instrument list drives the harvest: one static-HTML fetch per
treaty from ``ViewDetailsIII.aspx`` (an ASP.NET page that answers unattended
clients directly).  The stored record is the raw page; parse scrapes it
offline.  The corpus is a tiny fixed set, so the shared walk runs with **no
watermark** (the edpb/rs idiom: a complete listing has no depth to stop short
of) -- a page already on disk is skipped unless ``--full`` re-fetches it (a new
ratification changes the participation table, so a periodic ``--full`` refreshes
status).
"""

import time
from pathlib import Path

from ..lib import compress
from ..lib.harvest import ItemKey, walk
from ..lib.net import HARVESTER_UA as USER_AGENT
from ..lib.net import make_session, request
from .model import DETAIL, load_treaties


def page_path(root, mtdsg_no):
    return Path(root) / (mtdsg_no + ".html")


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


def resolve(session, root, entry, full=False, delay=0.3):
    """Fetch a treaty's page when missing or forced; returns whether it wrote."""
    path = page_path(root, entry["mtdsg_no"])
    if not (full or not compress.exists(path)):
        return False
    compress.write_download(path, fetch_page(session, entry))
    time.sleep(delay)
    return True


def list_basefiles(root):
    return compress.list_stems(root, "*.html")


def sync(root, full=False, only=None, limit=None, delay=0.3, log=print):
    """Fetch the curated treaties' status pages through the shared download loop
    (`lib.harvest.walk`, no watermark -- the curated list is walked whole every
    run). ``limit`` is that loop's: it caps pages actually *fetched*, not entries
    looked at. Returns (seen, fetched)."""
    root = Path(root)
    session = make_session(USER_AGENT)
    treaties = load_treaties()
    if only:
        if only not in treaties:
            raise ValueError("no curated UN treaty %s" % only)
        return 1, int(resolve(session, root, treaties[only], full=full, delay=delay))

    entries = list(treaties.values())
    result = walk(
        entries,
        resolve=lambda entry: resolve(session, root, entry, full=full, delay=delay),
        item_key=lambda entry: ItemKey(
            entry["mtdsg_no"], compress.exists(page_path(root, entry["mtdsg_no"]))),
        watermark=None,
        full=full,
        limit=limit,
        scope="untc",
        count_label="fetched",
        total=len(entries),
        log=log,
    )
    return result.seen, result.new
