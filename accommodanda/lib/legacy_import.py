"""Shared machinery for the one-time frozen-corpus imports (REWRITE.md §7g).

Every vertical with a dead upstream (förarbete, föreskrift, avg/ARN) imports
its frozen legacy tree once into its own record layout. The walk shapes and
record schemas are per-vertical, but three things are corpus-independent and
live here:

* **the precedence rule** (:func:`should_write`): a live-harvest record always
  wins, a corpus's own prior import is idempotent on a plain re-run and
  rewritten only under ``force``. Every import stamps its records with a
  ``source`` marker (the corpus tag) that the rule reads; a live harvester
  writes no ``source`` key. A vertical whose frozen corpora can collide on one
  basefile (förarbete) supplies a ``better`` tie-break; one with a single
  frozen source per slot (föreskrift, avg) passes none, and two corpora
  meeting is then a programming error.
* **in-place references** (:func:`rel`): §7g's "point at the bytes, don't copy
  them" -- a record references a frozen body file through its path relative to
  ``config.LEGACY_ROOT``, resolved back at parse time.
* **the frozen-tree walk primitives** (:func:`iter_entries`, :func:`docdir`,
  :func:`read_record`): the old pipeline's ``entries/<dir>/<n>.json`` +
  ``downloaded/<dir>/<n>/`` layout, walked in a deterministic sorted order so
  a ``--limit`` slice is reproducible.
"""

import json
import os
from pathlib import Path

from .. import config
from . import compress


def should_write(existing, source, force=False, better=None):
    """Whether a frozen-import candidate from the corpus tagged ``source``
    should be written at its basefile, given the record already on disk
    (``existing``, or None):

    * no record yet -> write;
    * a live-harvest record (no ``source`` key) always wins -> never
      overwrite it, not even under ``force``;
    * the corpus's own prior import (same ``source``) is skipped on a plain
      re-run (so the artifact keeps its mtime and parse stays fresh) and
      rewritten only under ``force``;
    * a *different* frozen corpus on disk is decided by ``better(existing)``
      (förarbete's body-tier/source-rank comparison); a vertical with a single
      frozen source per slot passes no ``better``, making the case an error.
    """
    if existing is None:
        return True
    if "source" not in existing:
        return False                                  # live harvest always wins
    if existing["source"] == source:
        return force
    assert better is not None, \
        "record %r has an unexpected source for corpus %s" % (existing, source)
    return better(existing)


def rel(path):
    """A frozen body file's path relative to LEGACY_ROOT -- how a record
    references the bytes in place (§7g), resolved back against LEGACY_ROOT at
    parse time."""
    return str(Path(path).relative_to(config.LEGACY_ROOT))


def read_record(recpath):
    """The record already on disk at ``recpath``, or None. Compress-aware: a
    prior import's record may be stored as ``<recpath>.br`` (`should_write`'s
    idempotency rule depends on finding it -- a plain `.exists()` would miss a
    compressed record and silently re-import every run)."""
    return json.loads(compress.read_text(recpath)) if compress.exists(recpath) else None


def iter_entries(entries_dir):
    """The corpus's per-document entry JSONs, walked in a deterministic path
    order (dirs + files sorted, so a ``--limit`` slice is reproducible),
    dotfiles excluded (``.root``, ``.durations.json``). Failure stubs are
    yielded like any other entry -- the walkers skip them by their basefile
    field, not their path."""
    for root, dirs, files in os.walk(entries_dir):
        dirs.sort()                          # in-place: controls traversal order
        for name in sorted(files):
            if name.endswith(".json") and not name.startswith("."):
                yield Path(root) / name


def docdir(downloaded, entrypath, entries_dir):
    """The ``downloaded/`` directory (or, for a flat store, the stem) for an
    entry -- its *location* in the tree, which mirrors ``entries/<rel>.json``.
    The body is located by the entry's own path; identity is read from its
    ``basefile`` field (the two can diverge, so neither is derived from the
    other)."""
    rel_ = entrypath.relative_to(entries_dir)
    return downloaded / rel_.parent / rel_.stem
