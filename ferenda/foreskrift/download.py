"""Download entry point for the föreskrift vertical -- wires the agency registry
to the shared download engine. ``lagen foreskrift download [scope...]``
downloads the named scopes (default all); ``--full`` re-walks and refreshes
existing base regulations (new amendments / consolidations), ``--only BASEFILE``
fetches one (needs a single scope).

A scope is one *listing to walk*, which is the författningssamling code for the
70 samlingar one agency owns outright (``fffs``) and ``hslffs-<publisher>`` for
the six sites that all publish into HSLF-FS (see `Agency.scope`)."""

import re
from pathlib import Path

from ..lib import compress, datasets
from ..lib import harvest as harvest_lib
from ..lib.compress import list_basefiles as _list_basefiles
from ..lib.util import NullReporter, Reporter, fold_swedish, record_path
from . import harvest
from .agencies import REGISTRY


def browser_scopes():
    """The scopes whose sites gate public documents behind a
    JavaScript-challenge (F5/Shape) WAF, so they need the serial Camoufox
    transport (skvfs, mtfs). Kept out of the default parallel `download` and run
    on their own schedule via the `browser-download` action -- Playwright's sync
    API is not built for one browser per thread."""
    return [scope for scope in REGISTRY if REGISTRY[scope].browser]


def default_scopes():
    """Every scope except the browser-shielded ones -- what a bare `download`
    fans out across the pool."""
    return [scope for scope in REGISTRY if not REGISTRY[scope].browser]


def _one(scope, root, full, only, delay, log, reporter):
    """Harvest a single scope, returning (scope, (seen, new)). Closed/static
    författningssamlingar (no live downloader) are a no-op."""
    agency = REGISTRY[scope]
    if agency.enumerate is None:
        log("foreskrift %s: no live downloader -- a closed series, its "
            "documents already in the corpus" % scope)
        return scope, (0, 0)
    return scope, harvest.harvest(agency, root, full=full, only=only, delay=delay,
                                  log=log, reporter=reporter)


def sync(root, scopes=None, full=False, only=None, delay=0.5, log=print, jobs=1):
    """Download the named scopes (default all in the registry), printing each
    one's own summary line as it finishes. Returns {scope: (seen, new)}.

    The fan-out itself is `lib.harvest.fan_out`, shared with every other
    multi-scope source (avg's organs, rs's agencies): with ``jobs > 1`` the
    agencies are harvested concurrently, each hitting a different remote host, so
    the wall time drops from the sum of every site to roughly the slowest single
    one. This vertical supplies only what is its own -- which agencies exist,
    which are browser-shielded, and how to harvest one."""
    scopelist = list(scopes or REGISTRY)
    quiet = NullReporter()

    def one(scope, into):
        # a worker's live progress line would collide with the others', so a
        # parallel run reports through NullReporter and writes into `into`; the
        # sequential path gets the real Reporter and prints as it goes
        parallel = jobs > 1 and not only and len(scopelist) > 1
        return _one(scope, root, full, only, delay, into,
                    quiet if parallel else Reporter())[1]

    return harvest_lib.fan_out(
        scopelist, one, jobs=jobs, label="foreskrift",
        serial=[s for s in scopelist if REGISTRY[s].browser], log=log)


def list_basefiles(root, fs):
    return _list_basefiles(root, fs)


def stored_series(root):
    """Every författningssamling with records on disk. Not the same set as
    ``REGISTRY``: a predecessor samling has no harvester of its own (nobody
    issues an SRVFS any more) and so no registry entry, but its still-in-force
    regulations sit in the corpus under their own fs, harvested off the
    successor agency's listing. Anything walking the store must read the store."""
    return sorted(p.name for p in Path(root).iterdir() if p.is_dir())


_FS_SERIES = datasets.load_fs_series()


def _lineage(fs):
    """`fs` and the series that succeeded it (series.json `successor`)."""
    chain = [fs]
    while _FS_SERIES.get(chain[-1], {}).get("successor"):
        chain.append(_FS_SERIES[chain[-1]]["successor"])
    return set(chain)


def superseded(root, series=None):
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
    bodies = {}
    empty = []
    held = {}
    for fs in (series or stored_series(root)):
        for basefile in _list_basefiles(root, fs):
            path = record_path(root, fs, basefile)
            record = compress.read_json(path)
            if record.get("url"):
                claims.setdefault(record["url"], []).append(basefile)
            # a direct-PDF series (LIVSFS) files every record under the one
            # year index as its url, so there the regulation PDF's own url is
            # what two records share. Its filename is no evidence of the
            # samling (Livsmedelsverket names SLVFS 2000:3's file
            # livsfs-2003-3-andr-1996-32.pdf); the record the later run wrote
            # is the re-filing, so the newer one wins
            regulation = record.get("files", {}).get("regulation") or {}
            if regulation.get("url"):
                bodies.setdefault(regulation["url"], []).append(
                    (compress.stat(path).st_mtime_ns, basefile))
            if not any(record.get("files", {}).values()):
                empty.append(basefile)
            held.setdefault(basefile.split("/", 1)[1], set()).add(basefile)
    stale = {}
    for url, claimants in bodies.items():
        if len(claimants) < 2:
            continue
        claimants.sort()
        stale.update({bf: (claimants[-1][1], url) for _, bf in claimants[:-1]})
    # a record that holds no document at all (its row linked another
    # document's landing page, so nothing was fetched) is superseded by the
    # same number filed under a series of the same lineage (series.json
    # `successor`: livsfs/1997:27, emptied, next to slvfs/1997:27)
    for basefile in empty:
        fs, number = basefile.split("/", 1)
        kin = [bf for bf in held[number] - {basefile} - set(empty)
               if fs in _lineage(bf.split("/", 1)[0]) or bf.split("/", 1)[0] in _lineage(fs)]
        if len(kin) == 1:
            stale[basefile] = (kin[0], "")
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
