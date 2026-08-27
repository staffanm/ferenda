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


from markupsafe import Markup, escape

from ..lib import compress, layout
from ..lib.page import page_context
from . import charts
from .model import Cell, Measure, Point, Row, Tile

ARTIFACT_BASEFILE = "statistik"


def _linked(text, links):
    """`text` with each key of `links` made a link to its uri, first occurrence
    only.

    A lede that names a document -- the repealed act holding the longest chain
    of inserted paragrafer, say -- should let the reader go there. The linking
    happens here rather than in `compute` so the measurement stays plain text:
    markup in the artifact is markup in the data.

    `text` is escaped first and each label matched in its escaped form, so a
    title carrying markup cannot inject any. Replacing only the first occurrence
    keeps the loop from reaching inside a tag it has already written -- a second
    label that happens to appear inside an emitted href would otherwise cut the
    markup in half."""
    out = str(escape(text))
    for label, uri in links.items():
        out = out.replace(str(escape(label)),
                          '<a href="%s">%s</a>' % (escape(uri), escape(label)), 1)
    return Markup(out)


charts.ENV.filters["linked"] = _linked

# the page template (stats/templates/stats.html, extending lib's page.html);
# it owns the measure catalog, order, prose and section layout. Fetched after
# the filter is registered, so the template can use it.
_PAGE = charts.ENV.get_template("stats.html")


def _as_row(d):
    """One artifact row back as a `Row`, its `steps` with it -- a row that
    carries a chain holds rows of its own, and `Row(**d)` would leave them as
    the dicts they are on disk."""
    row = Row(**{k: v for k, v in d.items() if k != "steps"})
    row.steps = [Row(**step) for step in d.get("steps", [])]
    return row


def _as_measure(d):
    """The artifact dict back as the dataclass `charts` reads. Kept here rather
    than in `charts` so the figure code never sees the on-disk shape."""
    return Measure(
        id=d["id"], group=d["group"], title=d["title"], kind=d["kind"],
        unit=d.get("unit", ""), lede=d.get("lede", ""),
        lede_links=d.get("lede_links", {}),
        value=d.get("value"), display=d.get("display", ""),
        tiles=[Tile(**t) for t in d.get("tiles", [])],
        rows=[_as_row(r) for r in d.get("rows", [])],
        points=[Point(**p) for p in d.get("points", [])],
        cells=[Cell(**c) for c in d.get("cells", [])],
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
    art = compress.read_json(path)
    dest = out_root / "statistik"
    dest.mkdir(parents=True, exist_ok=True)
    compress.write_text(dest / "index.html", render_stats(art),
                        compress.PAGE_ENCODINGS)
    return dest / "index.html"
