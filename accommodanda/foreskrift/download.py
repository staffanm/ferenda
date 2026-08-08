"""Download entry point for the föreskrift vertical -- wires the agency registry
to the shared download engine. ``lagen foreskrift download [fs...]`` downloads
the named författningssamlingar (default all); ``--full`` re-walks and refreshes
existing base regulations (new amendments / consolidations), ``--only BASEFILE``
fetches one (needs a single fs scope)."""

import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
from pathlib import Path

from ..lib import compress
from ..lib.compress import list_basefiles as _list_basefiles
from ..lib.util import NullReporter, Reporter, fold_swedish, record_path
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


def stored_series(root):
    """Every författningssamling with records on disk. Not the same set as
    ``REGISTRY``: a predecessor samling has no harvester of its own (nobody
    issues an SRVFS any more) and so no registry entry, but its still-in-force
    regulations sit in the corpus under their own fs, harvested off the
    successor agency's listing. Anything walking the store must read the store."""
    return sorted(p.name for p in Path(root).iterdir() if p.is_dir())


def superseded(root, scopes=None):
    """Harvested records that a *later* run has re-filed under another
    författningssamling, as ``{basefile: (winning basefile, landing url)}``.

    An agency that has taken over a renamed agency's samling serves several
    författningssamlingar off one listing, and which samling a row belongs to is
    read from its printed designation (``fs_from_designation``). Turning that on
    for an agency already harvested without it re-files its whole back
    catalogue: MCF's listing was first walked under ``msbfs`` (the agency's fs at
    the time), so every MCFFS/SÄIFS/SRVFS/KBMFS regulation on mcf.se also has an
    ``msbfs/...`` record naming a samling that never issued it -- MSB was renamed
    at the end of 2025, so "MSBFS 2026:8" does not exist. Both records then parse,
    publish and cite, and a rail row lists the same rule twice.

    A run never deletes what it merely failed to enumerate (a half-served
    paginated listing must not look like a repeal). The test here is positive
    and local: two records claim *the same landing page*, so the site itself says
    they are one document, and the one whose stored designation the landing slug
    does not corroborate is the leftover."""
    claims = {}
    for fs in (scopes or stored_series(root)):
        for basefile in _list_basefiles(root, fs):
            url = compress.read_json(
                record_path(root, fs, basefile)).get("url")
            if url:
                claims.setdefault(url, []).append(basefile)
    stale = {}
    for url, basefiles in claims.items():
        if len(basefiles) < 2:
            continue
        # the landing slug names the samling the site itself files the document
        # under ("…/gallande-regler/mcffs-20268/" -> "mcffs"); the claim it
        # corroborates is the real one, the rest are the leftovers. Slugs are
        # ASCII, fs codes are not (SÄIFS -> "saifs-…"), so both fold.
        named = re.match(r"[a-zåäö]+", url.rstrip("/").rsplit("/", 1)[-1].lower())
        winners = [bf for bf in basefiles
                   if named and fold_swedish(bf.split("/", 1)[0])
                   == fold_swedish(named.group())]
        if len(winners) != 1:
            # nothing (or everything) corroborated. The ordinary cause is
            # several regulations legitimately sharing one index page as their
            # source, so the group is skipped silently rather than reported:
            # removal needs the landing slug to name exactly one of the claims,
            # and without that there is no evidence to act on or to show.
            continue
        stale.update({bf: (winners[0], url) for bf in basefiles
                      if bf != winners[0]})
    return stale


def superseded_files(root, basefile):
    """Every downloaded file the superseded record for `basefile` owns: the
    record JSON, the cached landing page, and the bodies it points at (the
    regulation PDF and any consolidation/amendment/attachment). The winning
    record downloaded its own copies under its own slug, so nothing here is
    shared with it."""
    fs = basefile.split("/", 1)[0]
    record = record_path(root, fs, basefile)
    paths = [record, record.with_suffix(".html")]
    files = compress.read_json(record).get("files", {})
    for role in files.values():
        for entry in (role if isinstance(role, list) else [role]):
            if entry and entry.get("name"):
                paths.append(Path(root) / fs / entry["name"])
    return paths
