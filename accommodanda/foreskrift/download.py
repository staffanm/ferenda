"""Download entry point for the föreskrift vertical -- wires the agency registry
to the shared download engine. ``lagen foreskrift download [fs...]`` downloads
the named författningssamlingar (default all); ``--full`` re-walks and refreshes
existing base regulations (new amendments / consolidations), ``--only BASEFILE``
fetches one (needs a single fs scope)."""

from ..lib.compress import list_basefiles as _list_basefiles
from . import harvest
from .agencies import REGISTRY


def sync(root, scopes=None, full=False, only=None, delay=0.5, log=print):
    """Download the named författningssamlingar (default all in the registry).
    Returns {fs: (seen, new)}."""
    totals = {}
    for fs in (scopes or list(REGISTRY)):
        agency = REGISTRY[fs]
        if agency.enumerate is None:       # closed/static fs: no live downloader
            log("foreskrift %s: no live downloader -- a closed series, its "
                "documents already in the corpus" % fs)
            totals[fs] = (0, 0)
            continue
        totals[fs] = harvest.harvest(agency, root, full=full, only=only,
                                     delay=delay, log=log)
    return totals


def list_basefiles(root, fs):
    return _list_basefiles(root, fs)
