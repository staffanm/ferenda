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

import html
import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse

from .. import config
from ..lib import catalog, runlog
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


# --------------------------------------------------------------------------
# rendering helpers -- no template engine, no site shell, one CSS constant
# --------------------------------------------------------------------------

CSS = """
:root { color-scheme: light dark; }
body { font: 14px/1.5 system-ui, sans-serif; margin: 1.5rem; }
h1, h2 { font-weight: 600; }
h1 { font-size: 1.4rem; }
h2 { font-size: 1.1rem; margin-top: 2rem; }
nav a { margin-right: 1rem; }
a { color: #06c; }
table { border-collapse: collapse; margin: .5rem 0; }
th, td { border: 1px solid #8884; padding: .25rem .5rem; text-align: left;
         vertical-align: top; }
th { background: #8881; }
.matrix td { text-align: center; }
.ok { background: #2ecc7133; }
.stale { background: #f39c1233; }
.fail { background: #e74c3c55; font-weight: 600; }
.empty { color: #8888; }
.banner { background: #e74c3c22; border: 1px solid #e74c3c88;
          padding: .5rem .75rem; border-radius: 4px; margin: .5rem 0; }
.strip { display: flex; gap: .35rem; flex-wrap: wrap; margin: .5rem 0; }
.chip { padding: .15rem .5rem; border-radius: 3px; border: 1px solid #8884;
        text-decoration: none; }
.chip.ok { background: #2ecc7133; }
.chip.errors { background: #e74c3c33; }
.chip.aborted { background: #f39c1233; }
.chip.running { background: #3498db33; }
.bars { display: flex; align-items: center; gap: 2px; min-height: 1.4rem; }
.bar { height: 1.1rem; min-width: 2px; border-radius: 2px; }
.regress { color: #e74c3c; font-weight: 600; }
code, pre { font-family: ui-monospace, monospace; }
pre { white-space: pre-wrap; background: #8881; padding: .5rem;
      border-radius: 4px; overflow-x: auto; }
.small { color: #888; font-size: .85rem; }
"""


def _page(title, body, *, refresh=None):
    """The whole HTML document: escaped title, one inline stylesheet, a shared
    nav, and the pre-built (already-escaped) `body`. `refresh` adds a meta
    auto-refresh (seconds) for the live health overview."""
    meta = ('<meta http-equiv="refresh" content="%d">' % refresh) if refresh else ""
    return (
        "<!doctype html><html><head><meta charset=utf-8>"
        '<meta name=viewport content="width=device-width,initial-scale=1">'
        "%s<title>%s</title><style>%s</style></head><body>"
        '<nav><a href="/ops">health</a><a href="/ops/runs">runs</a>'
        '<a href="/ops/failures">failures</a></nav>'
        "<h1>%s</h1>%s</body></html>"
        % (meta, html.escape(title), CSS, html.escape(title), body))


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
        parts.append('<div class="banner">No completed run since %s (%s) -- '
                     "the health snapshot is stale.</div>"
                     % (html.escape(updated), _age(updated)))
    parts.append('<p class="small">snapshot updated %s. %d docs failing overall.</p>'
                 % (html.escape(_age(updated)) if updated else "never", len(errors)))

    # last-5-runs strip
    if runs:
        chips = "".join(
            '<a class="chip %s" href="/ops/runs/%s" title="%s">%s</a>'
            % (r["status"] if r["ok"] is not False else "errors",
               html.escape(r["run"]),
               html.escape("%s  %s" % (r["t"], " ".join(r["argv"]))),
               html.escape(r["run"]))
            for r in runs[:5])
        parts.append('<h2>recent runs</h2><div class="strip">%s</div>' % chips)
    else:
        parts.append('<h2>recent runs</h2><p class="empty">no runs recorded yet.</p>')

    # per-source x per-stage matrix
    sources = sorted(k for k in status if k != "_updated")
    if sources:
        columns = _stage_columns(status)
        head = "".join("<th>%s</th>" % s for s in columns)
        rows = []
        for src in sources:
            cells = []
            for stage in columns:
                cell = status[src].get(stage)
                if cell is None:
                    cells.append('<td class="empty">&middot;</td>')
                    continue
                failed = cell.get("failed", 0)
                stale = cell.get("stale", 0)
                cls = "fail" if failed else ("stale" if stale else "ok")
                key = (stage, src)
                regress = history.get(key, {}).get("regression")
                bits = ["%d/%d" % (cell.get("fresh", 0), cell.get("total", 0))]
                if failed:
                    bits.append('<span class="fail">%d failed</span>' % failed)
                if stale:
                    bits.append("%d stale" % stale)
                tip = "last ok %s" % _age(successes.get(key))
                if regress:
                    bits.append('<span class="regress">slow</span>')
                cells.append('<td class="%s" title="%s">%s</td>'
                             % (cls, html.escape(tip), "<br>".join(bits)))
            rows.append("<tr><th>%s</th>%s</tr>"
                        % (html.escape(src), "".join(cells)))
        parts.append('<h2>pipeline health</h2>'
                     '<table class="matrix"><tr><th>source</th>%s</tr>%s</table>'
                     % (head, "".join(rows)))
    else:
        parts.append('<h2>pipeline health</h2>'
                     '<p class="empty">no status snapshot yet -- run a full-source '
                     "step or `lagen &lt;source&gt; status`.</p>")

    # catalog delta: parsed-but-not-catalogued per source
    parts.append("<h2>catalog delta</h2>")
    if counts is None:
        parts.append('<p class="empty">catalog not built -- run `lagen all relate`.</p>')
    else:
        drows = []
        for src in sources:
            parse_cell = status[src].get("parse", {})
            fresh = parse_cell.get("fresh", 0)
            catn = counts.get(src, 0)
            drows.append("<tr><th>%s</th><td>%d</td><td>%d</td><td%s>%d</td></tr>"
                         % (html.escape(src), fresh, catn,
                            ' class="fail"' if fresh - catn > 0 else "",
                            fresh - catn))
        parts.append("<table><tr><th>source</th><th>parsed fresh</th>"
                     "<th>in catalog</th><th>delta</th></tr>%s</table>"
                     % "".join(drows) if drows
                     else '<p class="empty">no sources to compare.</p>')

    return _page("ops health", "".join(parts), refresh=60)


@router.get("/ops/runs", response_class=HTMLResponse,
            dependencies=[Depends(require_editor)])
def ops_runs():
    """Run history, newest first: start time, wall-clock, argv, the
    ok/errors/aborted/running outcome, and the segment count."""
    runs = runlog.read_runs(RUNS)
    if not runs:
        return _page("runs", '<p class="empty">no runs recorded yet.</p>')
    rows = []
    for r in runs:
        outcome = r["status"] if r["ok"] is not False else "errors"
        wall = "%.1fs" % r["secs"] if r["secs"] is not None else "&mdash;"
        rows.append(
            '<tr><td><a href="/ops/runs/%s">%s</a></td><td>%s</td><td>%s</td>'
            '<td><code>%s</code></td><td class="%s">%s</td><td>%d</td></tr>'
            % (html.escape(r["run"]), html.escape(r["run"]), html.escape(r["t"]),
               wall, html.escape(" ".join(r["argv"])), outcome,
               html.escape("%s (%d err)" % (outcome, r["errors"] or 0)),
               r["segments"]))
    return _page("runs",
                 "<table><tr><th>run</th><th>started</th><th>wall</th><th>argv</th>"
                 "<th>outcome</th><th>segments</th></tr>%s</table>" % "".join(rows))


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

    parts = ['<p class="small">%s &mdash; <code>%s</code> &mdash; %s</p>'
             % (html.escape(start["t"]),
                html.escape(" ".join(start["argv"])),
                html.escape(detail["status"]))]

    # per-step timing bars: one row per step, one block per source width ∝ secs
    by_step = {}
    for seg in segments:
        by_step.setdefault(seg["step"], []).append(seg)
    parts.append("<h2>timings</h2>")
    for step in sorted(by_step):
        segs = by_step[step]
        widest = max((s["secs"] for s in segs), default=0) or 1
        blocks = "".join(
            '<div class="bar" style="width:%dpx;background:%s" title="%s"></div>'
            % (max(2, int(240 * s["secs"] / widest)), _color(s["source"]),
               html.escape("%s %s: %.1fs (%s)"
                           % (step, s["source"], s["secs"], s["status"])))
            for s in sorted(segs, key=lambda s: -s["secs"]))
        parts.append('<div class="bars"><b style="width:6rem">%s</b>%s</div>'
                     % (html.escape(step), blocks))

    # segment table
    rows = []
    for seg in segments:
        slowest = ", ".join("%s %.1fs" % (bf, sc)
                            for bf, sc in seg.get("slowest") or [])
        rows.append(
            "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
            '<td>%s</td><td class="%s">%s</td><td>%.1f</td><td class="small">%s</td></tr>'
            % (html.escape(seg["step"]), html.escape(seg["source"]),
               _n(seg.get("total")), _n(seg.get("ran")),
               _n(seg.get("skipped_fresh")), _n(seg.get("skipdoc")),
               "fail" if seg["errors"] else "", seg["errors"],
               seg["secs"], html.escape(slowest)))
    parts.append("<h2>segments</h2><table><tr><th>step</th><th>source</th>"
                 "<th>total</th><th>ran</th><th>skipped</th><th>skipdoc</th>"
                 "<th>errors</th><th>secs</th><th>slowest docs</th></tr>%s</table>"
                 % "".join(rows))

    # this run's errors grouped by (source, stage)
    errors = runlog.read_errors(ERRORS)
    here = {k: v for k, v in errors.items() if v.get("run") == run_id}
    parts.append("<h2>errors in this run</h2>")
    if not here:
        parts.append('<p class="empty">none recorded for this run.</p>')
    else:
        grouped = {}
        for key, ent in here.items():
            source, stage, basefile = key.split("/", 2)
            grouped.setdefault((source, stage), []).append((basefile, ent))
        for (source, stage), items in sorted(grouped.items()):
            parts.append("<h3>%s / %s (%d)</h3>"
                         % (html.escape(source), html.escape(stage), len(items)))
            for basefile, ent in items:
                parts.append(_error_block(basefile, ent))

    return _page("run %s" % run_id, "".join(parts))


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
        rows.append((src, stg, basefile, errors[key]))

    filt = []
    if source:
        filt.append("source=%s" % html.escape(source))
    if stage:
        filt.append("stage=%s" % html.escape(stage))
    head = '<p class="small">%d failing docs%s</p>' % (
        len(rows), (" (" + ", ".join(filt) + ")") if filt else "")

    if not rows:
        return _page("failures", head + '<p class="empty">no matching failures.</p>')

    body = ["<table><tr><th>source</th><th>stage</th><th>basefile</th>"
            "<th>error</th></tr>"]
    for src, stg, basefile, ent in rows:
        body.append("<tr><td>%s</td><td>%s</td><td><code>%s</code></td><td>%s</td></tr>"
                    % (html.escape(src), html.escape(stg), html.escape(basefile),
                       _error_block("", ent)))
    body.append("</table>")
    return _page("failures", head + "".join(body))


def _n(v):
    """A cell value where None (a step with no doc counts) shows as a dash."""
    return "&mdash;" if v is None else str(v)


def _error_block(basefile, ent):
    """The one-line error plus its (possibly sampled) traceback in a
    <details>; every stored value is escaped."""
    label = html.escape("%s: %s" % (basefile, ent["error"])) if basefile \
        else html.escape(ent["error"])
    tb = ent.get("traceback")
    if not tb:
        return "<code>%s</code>" % label
    return ("<details><summary><code>%s</code></summary><pre>%s</pre></details>"
            % (label, html.escape(tb)))
