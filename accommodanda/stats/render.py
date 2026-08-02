"""Render the statistik artifact to ``/statistik``.

The numbers are the artifact's: every figure, and every lede whose sentence
embeds a measured value (``computed_lede`` in the template), comes from what
`compute` measured -- that is what keeps the page auditable. The *page* is
the template's: stats.html names each measure explicitly, in order, with its
title and static prose, so presentation is edited there, 1:1 with what
renders.

The page is `solo` (single column, no TOC rail) with its own in-page
navigation, because the reader's task here is browsing, not following a
document.
"""

import json

from ..lib import compress, layout
from ..lib.page import page_context
from . import charts
from .model import Cell, Measure, Point, Row

ARTIFACT_BASEFILE = "statistik"

# the page template (stats/templates/stats.html, extending lib's page.html);
# it owns the measure catalog, order, prose and section layout
_PAGE = charts.ENV.get_template("stats.html")


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
    """The page template (stats.html) is 1:1 with the rendered page: it names
    every measure explicitly, in order, with its title/lede/note -- this
    function only hands it the measured numbers (by id) and the figure
    renderer. A measure the artifact does not carry is skipped by the
    template, so a subset artifact renders only what it measured."""
    return _PAGE.render(page_context(
        "Statistik över innehållet på lagen.nu", "Statistik", "",
        eyebrow="Siffror om svensk rätt", solo=True,
        body_class=" site stats",
        measures={m["id"]: m for m in art["measures"]},
        generated=art["generated"],
        # the template hands its visible title along, so a figure's SVG
        # accessible name follows the heading above it, not the artifact's
        # own (now presentation-inert) title stamp
        figure=lambda m, title: charts.figure(
            _as_measure(dict(m, title=title)))))


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
