"""Download entry point for the föreskrift vertical -- wires the agency registry
to the shared download engine. ``lagen foreskrift download [fs...]`` downloads
the named författningssamlingar (default all); ``--full`` re-walks and refreshes
existing base regulations (new amendments / consolidations), ``--only BASEFILE``
fetches one (needs a single fs scope)."""

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext

from ..lib.compress import list_basefiles as _list_basefiles
from ..lib.util import NullReporter, Reporter
from . import harvest
from .agencies import REGISTRY


def browser_scopes():
    """The författningssamlingar whose sites gate public documents behind a
    headful-browser (F5/Shape) WAF, so they need the slow, serial DetachedChrome
    transport (skvfs, mtfs). Kept out of the default parallel `download` and run
    on their own schedule via the `browser-download` action -- concurrent Chrome
    would fight over the process-global DISPLAY and Playwright's single-thread
    sync API."""
    return [fs for fs in REGISTRY if REGISTRY[fs].browser]


def default_scopes():
    """Every författningssamling except the browser-shielded ones -- what a bare
    `download` fans out across the pool."""
    return [fs for fs in REGISTRY if not REGISTRY[fs].browser]


def _one(fs, root, full, only, delay, log, reporter):
    """Harvest a single agency, returning (fs, (seen, new)). Closed/static
    författningssamlingar (no live downloader) are a no-op."""
    agency = REGISTRY[fs]
    if agency.enumerate is None:
        log("foreskrift %s: no live downloader -- a closed series, its "
            "documents already in the corpus" % fs)
        return fs, (0, 0)
    return fs, harvest.harvest(agency, root, full=full, only=only, delay=delay,
                               log=log, reporter=reporter)


def sync(root, scopes=None, full=False, only=None, delay=0.5, log=print, jobs=1):
    """Download the named författningssamlingar (default all in the registry),
    printing each agency's own summary line as it finishes. Returns {fs: (seen,
    new)}.

    With ``jobs > 1`` (and more than one agency, no ``--only``) the agencies are
    harvested concurrently: each hits a different remote host, so the fan-out is
    polite, and the wall time drops from the sum of every site to roughly the
    slowest single one. Concurrent per-agency live progress lines would collide,
    so each worker reports through a NullReporter and the coordinator shows one
    aggregate '(done/total agencies)' line, printing each agency's summary above
    it as the future completes."""
    fslist = list(scopes or REGISTRY)
    if jobs <= 1 or only or len(fslist) <= 1:
        # sequential: each agency keeps its own live progress line
        totals = {}
        for fs in fslist:
            _, totals[fs] = _one(fs, root, full, only, delay, log, None)
            log("foreskrift %s: %d seen, %d new" % (fs, *totals[fs]))
        return totals

    # parallel: quiet workers (NullReporter), one shared aggregate line. Each
    # worker logs into its own buffer so a completed agency's stray notes and its
    # summary print together, on the coordinator thread, without interleaving.
    totals = {}
    buffers = {fs: [] for fs in fslist}
    running: set[str] = set()
    state = threading.Lock()                 # guards `running`
    # DetachedChrome starts headful Chrome on a private Xvfb display and points the
    # *process-global* DISPLAY at it (browser.py:_ensure_display), so two browser
    # agencies at once corrupt each other's display -> stalled navigation. Serialise
    # them; the HTTP agencies (the overwhelming majority) still run fully parallel.
    browser_lock = threading.Lock()

    def work(fs):
        with state:
            running.add(fs)
        try:
            gate = browser_lock if REGISTRY[fs].browser else nullcontext()
            with gate:
                return _one(fs, root, full, only, delay,
                            buffers[fs].append, NullReporter())
        finally:
            with state:
                running.discard(fs)

    rep = Reporter()
    done = new_total = 0
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = {pool.submit(work, fs): fs for fs in fslist}
        for future in as_completed(futures):
            fs, (seen, new) = future.result()
            totals[fs] = (seen, new)
            new_total += new
            done += 1
            rep.clear()                          # lift the live line off the row
            for line in buffers[fs]:
                log(line)
            log("foreskrift %s: %d seen, %d new" % (fs, seen, new))
            # name what is still in flight, so a slow browser agency (settle 20s per
            # navigation) reads as working, not as a frozen counter
            with state:
                busy = sorted(running)
            note = "  [running: %s]" % ", ".join(busy) if busy else ""
            rep.update(done, len(fslist), scope="foreskrift", new=new_total, note=note)
    rep.done()
    return totals


def list_basefiles(root, fs):
    return _list_basefiles(root, fs)
