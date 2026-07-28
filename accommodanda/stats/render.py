"""Render the statistik artifact to ``/statistik``.

A pure projection: everything on the page is in the artifact, and the only thing
this module decides is presentation order and grouping. That is what makes the
numbers auditable -- the page cannot say anything `compute` did not measure.

The page is `solo` (single column, no TOC rail) with its own in-page navigation,
because the reader's task here is browsing, not following a document.
"""

import json

from ..lib import compress, layout
from ..lib.render import escape, page
from . import charts
from .model import Cell, Measure, Point, Row

GROUPS = (
    ("A", "Lagbokens storlek och form"),
    ("B", "Förändring och omsättning"),
    ("C", "Tid och livslängd"),
    ("D", "Hänvisningsgrafen"),
    ("E", "Förarbeten"),
    ("F", "Rättspraxis"),
    ("G", "Föreskrifter, remisser och omvärlden"),
)

ARTIFACT_BASEFILE = "statistik"


def _measure_html(m):
    parts = ['<section class="stat" id="m%d">' % m["id"]]
    parts.append('<h3><span class="stat-no">%d</span> %s</h3>'
                 % (m["id"], escape(m["title"])))
    if m.get("lede"):
        parts.append('<p class="stat-lede">%s</p>' % escape(m["lede"]))
    parts.append(charts.figure(_as_measure(m)))
    if m.get("note"):
        parts.append('<p class="stat-note">%s</p>' % escape(m["note"]))
    parts.append("</section>")
    return "".join(parts)


def _as_measure(d):
    """The artifact dict back as the dataclass `charts` reads. Kept here rather
    than in `charts` so the figure code never sees the on-disk shape."""
    return Measure(
        id=d["id"], group=d["group"], title=d["title"], kind=d["kind"],
        unit=d.get("unit", ""), lede=d.get("lede", ""), note=d.get("note", ""),
        value=d.get("value"), display=d.get("display", ""),
        rows=[Row(**r) for r in d.get("rows", [])],
        points=[Point(**p) for p in d.get("points", [])],
        cells=[Cell(**c) for c in d.get("cells", [])],
        columns=d.get("columns", []),
        xlabel=d.get("xlabel", ""), ylabel=d.get("ylabel", ""))


def render_stats(art):
    by_group = {}
    for m in art["measures"]:
        by_group.setdefault(m["group"], []).append(m)

    nav = ['<nav class="stat-nav" aria-label="Avsnitt"><ol>']
    for key, title in GROUPS:
        if by_group.get(key):
            nav.append('<li><a href="#g%s">%s</a></li>' % (key, escape(title)))
    nav.append("</ol></nav>")

    body = ["".join(nav)]
    for key, title in GROUPS:
        measures = by_group.get(key)
        if not measures:
            continue
        body.append('<section class="stat-group" id="g%s"><h2>%s</h2>%s</section>'
                    % (key, escape(title),
                       "".join(_measure_html(m) for m in measures)))
    body.append('<p class="stat-foot">Mätt %s mot korpusen som den såg ut då. '
                "Varje siffra är räknad ur artefakterna och katalogen — inga "
                "uppskattningar utom där noten säger det.</p>"
                % escape(art["generated"]))
    return page("Statistik om korpuset", "Statistik", "", "".join(body),
                eyebrow="Siffror om svensk rätt", solo=True,
                body_class=" site stats")


def write_stats(out_root):
    """Write ``statistik/index.html`` from the computed artifact. Raises if the
    artifact is absent -- rendering a statistics page without measurements would
    publish an empty claim, so `lagen stats compute` must have run."""
    path = layout.artifact("stats", ARTIFACT_BASEFILE)
    if not compress.exists(path):
        raise FileNotFoundError(
            "no stats artifact at %s -- run `lagen stats compute` first" % path)
    art = json.loads(compress.read_text(path))
    dest = out_root / "statistik"
    dest.mkdir(parents=True, exist_ok=True)
    compress.write_text(dest / "index.html", render_stats(art),
                        compress.PAGE_ENCODINGS)
    return dest / "index.html"
