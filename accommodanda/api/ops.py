"""The ops dashboard (`/ops`) -- an at-a-glance health view over the run
instrumentation `lib/runlog.py` writes under ``DATA/.build/``.

Deliberately self-contained: it renders its own minimal HTML (a local
``_page`` shell + one CSS constant), never reusing render.py's site shell,
because the health page must load precisely when the corpus is broken or no
site has been built. It only *reads* the runlog files through the same
module the build driver writes them with -- neither side imports the other.

Auth is the inline editor's session (``auth.require_editor``): the dashboard
serves the same small hand-curated set of editors, so it rides their login
rather than carrying a second credential. No/expired session -> 401 (log in);
editing disabled (no ``editor_secret``) -> 403 -- exactly as the edit routes
answer. This is an HTML view for humans; a curl/monitoring integration should
target a JSON API endpoint, not this page.
"""

import os
import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from markupsafe import Markup
from opensearchpy.exceptions import OpenSearchException

from .. import config
from ..lib import catalog, git, runlog, search, tpl
from .auth import require_editor

RUNS = config.DATA / ".build" / "runs.ndjson"
ERRORS = config.DATA / ".build" / "errors.json"
STATUS = config.DATA / ".build" / "status.json"
CATALOG = config.CATALOG_ROOT / "catalog.sqlite"

# the canonical pipeline stages, in run order, the health matrix lays out as
# columns (a source that never ran a stage simply has no cell there). The actual
# columns are these unioned with any stage present in the snapshot (see
# `_stage_columns`), so a cell a source wrote -- e.g. sfs's "versions" -- is
# never silently hidden.
STAGES = ["download", "parse", "versions", "relate", "index", "dump", "generate"]

STALE_AFTER_H = 26        # snapshot-age warning threshold (a daily run + slack)

router = APIRouter()

# one search client for the module (constructing it opens no connection -- only
# an actual store_size() call does), mirroring api/app.py's single _index.
_index = search.SearchIndex()


# --------------------------------------------------------------------------
# rendering helpers -- the dashboard's whole markup lives in templates/ops.html
# (its own minimal shell, deliberately not the site chrome)
# --------------------------------------------------------------------------

TPL = tpl.environment("accommodanda.api").get_template("ops.html").module


def _page(title, body, *, refresh=None):
    """The whole HTML document (ops.html `shell`): one inline stylesheet, a
    shared nav, and the pre-rendered `body`. `refresh` adds a meta
    auto-refresh (seconds) for the live health overview."""
    return TPL.shell(title, body, refresh)


def _stage_columns(status):
    """The matrix columns: the canonical STAGES in run order, plus any stage a
    source actually wrote that STAGES doesn't know about (appended, sorted), so
    every snapshot cell is displayed."""
    present = {stage for src, cells in status.items() if src != "_updated"
               for stage in cells}
    return STAGES + [s for s in sorted(present) if s not in STAGES]


def _hue(source):
    """A deterministic hue (0-359) per source, so the same source keeps its
    colour across every timing bar and run."""
    return int.from_bytes(source.encode("utf-8"), "big") % 360 if source else 0


def _color(source):
    return "hsl(%d, 65%%, 55%%)" % _hue(source)


def _parse_iso(t):
    """A stored `now_iso` timestamp back to an aware datetime."""
    return datetime.strptime(t, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _age(t, *, now=None):
    """A human 'Nh Nm ago' / 'Nd ago' for a stored timestamp."""
    if not t:
        return "never"
    delta = (now or datetime.now(timezone.utc)) - _parse_iso(t)
    secs = int(delta.total_seconds())
    if secs < 3600:
        return "%dm ago" % (secs // 60)
    if secs < 86400:
        return "%dh %dm ago" % (secs // 3600, (secs % 3600) // 60)
    return "%dd ago" % (secs // 86400)


def _catalog_counts():
    """Per-source catalog document counts, or None when no catalog is built --
    a legitimate empty-state for the delta widget, not a 503."""
    if not CATALOG.exists():
        return None
    con = sqlite3.connect("file:%s?mode=ro" % CATALOG, uri=True)
    try:
        return catalog.counts(con)
    finally:
        con.close()


def _source_stats():
    """{source: (docs, bytes)} from the catalog, or None when it isn't built."""
    if not CATALOG.exists():
        return None
    con = sqlite3.connect("file:%s?mode=ro" % CATALOG, uri=True)
    try:
        return catalog.source_stats(con)
    finally:
        con.close()


def _version():
    """The running accommodanda revision: the git sha baked into the image at
    build (ACCOMMODANDA_GIT_SHA, .git being dockerignored), else -- in a dev
    working tree -- the live short HEAD, else 'unknown'."""
    return (os.environ.get("ACCOMMODANDA_GIT_SHA")
            or git.run(config.REPO, "rev-parse", "--short=12", "HEAD",
                       capture=True, check=False)
            or "unknown")


def _index_size():
    """Human size of the OpenSearch index, 'index not built' when absent, or
    'unavailable' when the cluster can't be reached -- the health page must load
    even when search is down (same spirit as _catalog_counts' None)."""
    try:
        size = _index.store_size()
    except OpenSearchException:
        return "unavailable"
    return _human_bytes(size) if size is not None else "index not built"


def _human_bytes(n):
    """A compact decimal size ('39.5 GB', '812 MB', '4.0 kB'), matching how
    OpenSearch's own _cat output sizes stores."""
    size = float(n)
    for unit in ("B", "kB", "MB", "GB", "TB"):
        if size < 1000 or unit == "TB":
            return "%d %s" % (size, unit) if unit == "B" else "%.1f %s" % (size, unit)
        size /= 1000


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------

@router.get("/ops", response_class=HTMLResponse, dependencies=[Depends(require_editor)])
def ops_overview():
    """Health overview: the per-source x per-stage matrix, a snapshot-age
    banner, total failing docs, the last-5-runs strip, per-cell last-success
    age + duration-regression flags, and the catalog delta."""
    status = runlog.read_status(STATUS)
    errors = runlog.read_errors(ERRORS)
    runs = runlog.read_runs(RUNS)
    successes = runlog.last_success(RUNS)
    history = runlog.duration_history(RUNS)
    counts = _catalog_counts()

    parts = []

    updated = status.get("_updated")
    if updated and (datetime.now(timezone.utc) - _parse_iso(updated)
                    > timedelta(hours=STALE_AFTER_H)):
        parts.append(TPL.stale_banner(updated, _age(updated)))
    parts.append(TPL.snapshot_line(_age(updated) if updated else "never",
                                   len(errors)))

    # system: running revision, wiki push state, search-index size
    ahead, dirty = git.push_state(config.WIKI_ROOT)
    parts.append(TPL.system_table(
        _version(),
        {"no_upstream": ahead is None, "ahead": ahead, "dirty": dirty},
        _index_size()))

    # corpus: document count + artifact size per source, from the catalog
    stats = _source_stats()
    crows = []
    if stats:
        for src in sorted(stats):
            docs, nbytes = stats[src]
            crows.append({"source": src, "docs": docs,
                          "size": _human_bytes(nbytes)})
        crows.append({"source": "total",
                      "docs": sum(d for d, _ in stats.values()),
                      "size": _human_bytes(sum(b for _, b in stats.values()))})
    parts.append(TPL.corpus_table(crows))

    # last-5-runs strip
    parts.append(TPL.runs_strip([
        {"outcome": r["status"] if r["ok"] is not False else "errors",
         "run": r["run"], "tip": "%s  %s" % (r["t"], " ".join(r["argv"]))}
        for r in runs[:5]]))

    # per-source x per-stage matrix
    sources = sorted(k for k in status if k != "_updated")
    columns = _stage_columns(status) if sources else []

    def matrix_cell(src, stage):
        cell = status[src].get(stage)
        if cell is None:
            return None
        failed = cell.get("failed", 0)
        stale = cell.get("stale", 0)
        key = (stage, src)
        return {"cls": "fail" if failed else ("stale" if stale else "ok"),
                "tip": "last ok %s" % _age(successes.get(key)),
                "counts": "%d/%d" % (cell.get("fresh", 0),
                                     cell.get("total", 0)),
                "failed": failed, "stale": stale,
                "regress": history.get(key, {}).get("regression")}

    parts.append(TPL.matrix(columns, [
        {"source": src, "cells": [matrix_cell(src, stage) for stage in columns]}
        for src in sources]))

    # catalog delta: parsed-but-not-catalogued per source
    delta = None
    if counts is not None:
        delta = [{"source": src,
                  "fresh": (fresh := status[src].get("parse", {}).get("fresh", 0)),
                  "catn": (catn := counts.get(src, 0)),
                  "delta": fresh - catn}
                 for src in sources]
    parts.append(TPL.delta_table(delta))

    return _page("ops health", Markup("").join(parts), refresh=60)


@router.get("/ops/runs", response_class=HTMLResponse,
            dependencies=[Depends(require_editor)])
def ops_runs():
    """Run history, newest first: start time, wall-clock, argv, the
    ok/errors/aborted/running outcome, and the segment count."""
    runs = runlog.read_runs(RUNS)
    if not runs:
        return _page("runs", TPL.empty("no runs recorded yet."))
    rows = []
    for r in runs:
        outcome = r["status"] if r["ok"] is not False else "errors"
        rows.append({"run": r["run"], "t": r["t"],
                     "wall": ("%.1fs" % r["secs"]
                              if r["secs"] is not None else "—"),
                     "argv": " ".join(r["argv"]), "outcome": outcome,
                     "outcome_text": "%s (%d err)" % (outcome,
                                                      r["errors"] or 0),
                     "segments": r["segments"]})
    return _page("runs", TPL.runs_table(rows))


@router.get("/ops/runs/{run_id}", response_class=HTMLResponse,
            dependencies=[Depends(require_editor)])
def ops_run_detail(run_id: str):
    """One run: per-step timing bars (a coloured block per source, width
    proportional to seconds), a segment table, and the run's errors grouped by
    (source, stage). 404 for an unknown run id."""
    detail = runlog.run_detail(RUNS, run_id)
    if detail is None:
        raise HTTPException(404, "no run %r in the ledger" % run_id)
    segments = detail["segments"]
    start = detail["start"]

    parts = [TPL.run_header(start["t"], " ".join(start["argv"]),
                            detail["status"])]

    # per-step timing bars: one row per step, one block per source width ∝ secs
    by_step = {}
    for seg in segments:
        by_step.setdefault(seg["step"], []).append(seg)
    def step_bars(step, segs):
        widest = max((s["secs"] for s in segs), default=0) or 1
        return {"step": step,
                "blocks": [{"width": max(2, int(240 * s["secs"] / widest)),
                            "color": _color(s["source"]),
                            "tip": "%s %s: %.1fs (%s)" % (step, s["source"],
                                                          s["secs"],
                                                          s["status"])}
                           for s in sorted(segs, key=lambda s: -s["secs"])]}

    parts.append(TPL.timings([step_bars(step, segs)
                              for step, segs in sorted(by_step.items())]))

    # segment table
    parts.append(TPL.segments_table([
        {"step": seg["step"], "source": seg["source"],
         "total": _n(seg.get("total")), "ran": _n(seg.get("ran")),
         "skipped": _n(seg.get("skipped_fresh")),
         "skipdoc": _n(seg.get("skipdoc")), "errors": seg["errors"],
         "secs": "%.1f" % seg["secs"],
         "slowest": ", ".join("%s %.1fs" % (bf, sc)
                              for bf, sc in seg.get("slowest") or [])}
        for seg in segments]))

    # this run's errors grouped by (source, stage)
    errors = runlog.read_errors(ERRORS)
    grouped = {}
    for key, ent in errors.items():
        if ent.get("run") != run_id:
            continue
        source, stage, basefile = key.split("/", 2)
        grouped.setdefault((source, stage), []).append(
            {"label": "%s: %s" % (basefile, ent["error"]),
             "tb": ent.get("traceback")})
    parts.append(TPL.run_errors([
        {"source": source, "stage": stage, "entries": entries}
        for (source, stage), entries in sorted(grouped.items())]))

    return _page("run %s" % run_id, Markup("").join(parts))


@router.get("/ops/failures", response_class=HTMLResponse,
            dependencies=[Depends(require_editor)])
def ops_failures(source: str | None = Query(None), stage: str | None = Query(None)):
    """The `errors.json` drill-down: one row per currently-failing doc,
    optionally filtered by ?source= and ?stage=, each traceback tucked into a
    <details>."""
    errors = runlog.read_errors(ERRORS)
    rows = []
    for key in sorted(errors):
        src, stg, basefile = key.split("/", 2)
        if source and src != source:
            continue
        if stage and stg != stage:
            continue
        ent = errors[key]
        rows.append({"source": src, "stage": stg, "basefile": basefile,
                     "label": ent["error"], "tb": ent.get("traceback")})

    filt = []
    if source:
        filt.append("source=%s" % source)
    if stage:
        filt.append("stage=%s" % stage)
    return _page("failures",
                 TPL.failures_page(len(rows), ", ".join(filt), rows))


def _n(v):
    """A cell value where None (a step with no doc counts) shows as a dash."""
    return "—" if v is None else str(v)
