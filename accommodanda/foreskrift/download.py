"""Harvest entry point for the föreskrift vertical -- wires the agency registry
to the shared harvest engine. ``lagen foreskrift download [fs...]`` harvests the
named författningssamlingar (default all); ``--full`` re-walks and refreshes
existing base regulations (new amendments / consolidations), ``--only BASEFILE``
fetches one (needs a single fs scope)."""

from . import harvest
from .agencies import REGISTRY


def sync(root, scopes=None, full=False, only=None, delay=0.5, log=print):
    """Harvest the named författningssamlingar (default all in the registry).
    Returns {fs: (seen, new)}."""
    totals = {}
    for fs in (scopes or list(REGISTRY)):
        agency = REGISTRY[fs]
        totals[fs] = harvest.harvest(agency, root, full=full, only=only,
                                     delay=delay, log=log)
    return totals


def list_basefiles(root, fs):
    return harvest.list_basefiles(root, fs)
