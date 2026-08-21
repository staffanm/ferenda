"""The ops dashboard (`/ops`) -- an at-a-glance health view over the run
instrumentation `lib/runlog.py` writes under ``DATA/.build/``.

It renders its own small HTML (a local ``_page`` shell, ``templates/ops.html``)
rather than render.py's site shell, but wears the site's ``/style.css`` so the
two look like one application. That is a change: the shell used to carry a
whole private stylesheet so the page would load when no site had been built.
The site is built, so the second design system only made ops look foreign.

It only *reads* the runlog files through the same module the build driver
writes them with -- neither side imports the other.

Auth is the inline editor's session (``auth.require_editor``): the dashboard
serves the same small hand-curated set of editors, so it rides their login
rather than carrying a second credential. No/expired session -> 401 (log in);
editing disabled (no ``editor_secret``) -> 403 -- exactly as the edit routes
answer. This is an HTML view for humans; a curl/monitoring integration should
target a JSON API endpoint, not this page.
"""

import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from markupsafe import Markup
from opensearchpy.exceptions import OpenSearchException

from .. import config
from ..lib import catalog, git, layout, runlog, search, tpl
from . import db
from .auth import require_editor

RUNS = config.DATA / ".build" / "runs.ndjson"
ERRORS = config.DATA / ".build" / "errors.json"
STATUS = config.DATA / ".build" / "status.json"

# The pipeline every source walks, in run order -- the health table's columns.
# These are read from the *run ledger*, not from status.json: only a full-source
# `parse`/`download` writes a status cell, so relate/index/dump/generate were
# blank for every source in the snapshot while the ledger held 23 runs of each.
STAGES = ["download", "parse", "relate", "index", "dump", "generate"]

# Steps outside that spine: one source's own extra work (sfs's `versions`,
# stats's `compute`, dv's `namedcases`), or an occasional manual harvest
# (`browser-download`). As columns they were 18 empty cells and one number, so
# they get a list of their own under the table instead.
STALE_AFTER_H = 26        # snapshot-age warning threshold (a daily run + slack)

# How far past its own median a step has to run before the table says so. The
# old flag fired at 1.5x and said only "slow", which on the live dashboard
# marked 11 of 19 sources -- a warning that is always on is not a warning. The
# cell now prints the multiple it measured, so "2.4x median" can be judged.
SLOW_FACTOR = 2.0

# Ledger "sources" that are not sources: the whole-site generate and relate's
# cross-corpus correspondence pass both record their segments under a name of
# their own. They belong to no row of a per-source table.
PSEUDO_SOURCES = {"__site__", "__corr__"}

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


def _stage_cell(seg, hist, now):
    """One (source, stage) cell from the last ledger segment that ran it, or
    None when it never has. `hist` is that key's duration history."""
    if seg is None:
        return None
    errors, status = seg["errors"], seg["status"]
    ratio = hist["ratio"] if hist else 0
    # "ran/total" only where the two count the same things -- how many of this
    # source's documents did work. `index` counts *units* in `ran` (a document
    # becomes many searchable units), so forarbete's cell read "6870102/97187",
    # which looks like a fault and is a ratio. Above total, show total alone and
    # leave the other number to the tooltip.
    total, ran = seg["total"], seg["ran"]
    counts = ("%s/%s" % (ran, total) if ran is not None and total is not None
              and ran <= total else
              str(total) if total is not None else None)
    return {
        "cls": "fail" if errors else ("stale" if status == "skipped" else "ok"),
        "age": _age(seg["t"], now=now),
        "errors": errors,
        "skipped": status == "skipped",
        "counts": counts,
        # the parse cell's catalog delta, filled by the caller for the sources
        # that catalogue at all. Always present: the template environment is
        # strict, and a key that appears only sometimes is a 500 waiting to fire
        "delta": None,
        # the multiple, not the word "slow": a bare flag at 1.5x marked 11 of
        # 19 sources and told the reader nothing about how much. "per doc" says
        # the comparison is a rate, so a big run is not called slow for being big
        "slow": ("%.1fx median%s" % (ratio, " per doc" if hist["rate"] else "")
                 if ratio >= SLOW_FACTOR else None),
        "tip": "%s %s: %.1fs, ran %s of %s, %d errors, last ran %s"
               % (seg["step"], seg["source"], seg["secs"], ran, total, errors,
                  seg["t"]),
    }


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


def _source_stats():
    """{source: (docs, bytes)} from the catalog -- the corpus table's rows and
    the document counts the catalog-delta widget compares against -- or None
    when no catalog is built, which is a legitimate empty state for both, not
    a 503. One query per page load, not one per widget."""
    if not db.catalog_ready():
        return None
    with db.connection() as con:
        return catalog.source_stats(con)


def _version():
    """The running accommodanda revision: the git sha baked into the image at
    build (ACCOMMODANDA_GIT_SHA, .git being dockerignored), else -- in a dev
    working tree -- the live short HEAD, else 'unknown'."""
    return (os.environ.get("ACCOMMODANDA_GIT_SHA")
            or git.run(config.REPO, "rev-parse", "--short=12", "HEAD",
                       capture=True, check=False)
            or "unknown")


def _repo_state(repo):
    """One checkout's push state, shaped for the template."""
    ahead, dirty = git.push_state(repo)
    return {"no_upstream": ahead is None, "ahead": ahead, "dirty": dirty}


def _index_size():
    """Human size of the OpenSearch index, 'index not built' when absent, or
    'unavailable' when the cluster can't be reached -- the health page must load
    even when search is down (same spirit as _source_stats' None)."""
    try:
        size = _index.store_size()
    except OpenSearchException:
        return "unavailable"
    return _human_bytes(size) if size is not None else "index not built"


def _duration(secs):
    """A wall clock a reader can size up: '13.4s', '35m 12s', '9h 53m'. The
    runs table printed raw seconds, where sfs's versions step reads
    '35583.5s' -- a number nobody converts to ten hours at a glance."""
    if secs < 60:
        return "%.1fs" % secs
    if secs < 3600:
        return "%dm %ds" % (secs // 60, secs % 60)
    return "%dh %dm" % (secs // 3600, (secs % 3600) // 60)


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
    """Health overview: one row per source carrying both what the corpus holds
    (documents, artifact bytes) and how each pipeline step last went, plus the
    recent runs and the steps outside the common spine.

    Corpus size and pipeline health were two tables keyed by the same sources,
    read one under the other; they are one table now. The stage cells come from
    the run ledger rather than status.json -- see STAGES."""
    status = runlog.read_status(STATUS)
    errors = runlog.read_errors(ERRORS)
    runs = runlog.read_runs(RUNS)
    segments = runlog.last_segments(RUNS)
    history = runlog.duration_history(RUNS)
    stats = _source_stats()
    now = datetime.now(timezone.utc)

    parts = []

    updated = status.get("_updated")
    if updated and (now - _parse_iso(updated) > timedelta(hours=STALE_AFTER_H)):
        parts.append(TPL.stale_banner(updated, _age(updated, now=now)))

    # system: running revision + host, push state of the checkout the site
    # writes into, search-index size. The content repo is reported because both
    # editors commit into it -- commentaries and source patches alike -- but
    # nothing pushes it, so unpushed redactions would sit there unannounced.
    parts.append(TPL.system_line(
        _age(updated, now=now) if updated else "never", len(errors),
        _version(), runlog.this_host(), _repo_state(config.WIKI_ROOT),
        _index_size()))

    # one row per source: every source the catalog knows plus every source the
    # ledger has run a step for -- a source that parses but catalogues nothing
    # (remisser) belongs on the health table even with no corpus numbers
    sources = sorted((set(stats or ()) | {src for _step, src in segments})
                     - PSEUDO_SOURCES)

    def row(src):
        docs, nbytes = (stats or {}).get(src, (None, None))
        cells = [_stage_cell(segments.get((stage, src)),
                             history.get((stage, src)), now)
                 for stage in STAGES]
        # the catalog delta, folded into the cell it belongs beside instead of
        # the separate table it was: parse wrote N artifacts, the catalog holds
        # M. Only for sources that catalogue at all -- remisser parses 80,200
        # consultation responses and publishes none of them by decision, which
        # the old table flagged red every single load.
        parsed = status.get(src, {}).get("parse", {}).get("fresh")
        if (src in layout.CATALOGUED_SOURCES and cells[STAGES.index("parse")]
                and parsed is not None and docs is not None and parsed > docs):
            cells[STAGES.index("parse")]["delta"] = parsed - docs
        return {"source": src, "docs": docs,
                "size": _human_bytes(nbytes) if nbytes is not None else None,
                "cells": cells}

    parts.append(TPL.health_table(
        STAGES, [row(src) for src in sources],
        {"docs": sum(d for d, _ in stats.values()),
         "size": _human_bytes(sum(b for _, b in stats.values()))}
        if stats else None))

    # Everything the per-source table cannot hold: a step outside the common
    # spine (sfs's versions, stats' compute, dv's namedcases), and the two
    # corpus-wide passes that record under a name of their own. Without this the
    # whole-site generate -- which is what actually builds the pages, and shows
    # as an empty cell for every source -- appeared nowhere on the dashboard.
    parts.append(TPL.other_steps([
        {"step": step, "source": src, "pseudo": src in PSEUDO_SOURCES,
         "cell": _stage_cell(seg, history.get((step, src)), now)}
        for (step, src), seg in sorted(segments.items())
        if step not in STAGES or src in PSEUDO_SOURCES]))

    parts.append(TPL.runs_strip([
        {"outcome": r["status"] if r["ok"] is not False else "errors",
         "run": r["run"], "host": r["host"], "age": _age(r["t"], now=now),
         # argv[1:], as the runs table does: every chip would open with `lagen`
         "argv": " ".join(r["argv"][1:]) if r["argv"] else "—",
         "tip": "%s  %s" % (r["t"], " ".join(r["argv"] or ["(damaged)"]))}
        for r in runs[:8]]))

    return _page("ops health", Markup("").join(parts), refresh=60)


@router.get("/ops/runs", response_class=HTMLResponse,
            dependencies=[Depends(require_editor)])
def ops_runs():
    """Run history, newest first: start time, wall-clock, argv, the
    ok/errors/aborted/running outcome, and the segment count."""
    runs = runlog.read_runs(RUNS)
    if not runs:
        return _page("runs", TPL.empty("no runs recorded yet."))
    now = datetime.now(timezone.utc)
    rows = []
    for r in runs:
        outcome = r["status"] if r["ok"] is not False else "errors"
        rows.append({"run": r["run"], "t": r["t"], "age": _age(r["t"], now=now),
                     "host": r["host"], "elsewhere": r["host"] not in
                     (None, runlog.this_host()),
                     "wall": (_duration(r["secs"])
                              if r["secs"] is not None else "—"),
                     # what the run did, not how it was typed: `lagen` and the
                     # trailing flags are the same on every row
                     "argv": " ".join(r["argv"][1:]) if r["argv"] else "—",
                     "outcome": outcome,
                     "errors": r["errors"] or 0,
                     "sources": ", ".join(r["sources"]) or "—",
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

    parts = [TPL.run_header(start["t"], " ".join(start["argv"] or ["(damaged)"]),
                            detail["status"], detail["host"],
                            detail["host"] not in (None, runlog.this_host()))]

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
    # every key is written by `runlog.emit_segment` on every segment, so they
    # are read directly -- a missing one is ledger drift, not a blank cell
    # (`total`/`ran` are legitimately None for a step with no doc counts)
    parts.append(TPL.segments_table([
        {"step": seg["step"], "source": seg["source"],
         "total": _n(seg["total"]), "ran": _n(seg["ran"]),
         "skipped": _n(seg["skipped_fresh"]),
         "skipdoc": _n(seg["skipdoc"]), "errors": seg["errors"],
         "secs": "%.1f" % seg["secs"],
         "slowest": ", ".join("%s %.1fs" % (bf, sc)
                              for bf, sc in seg["slowest"])}
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
