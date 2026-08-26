"""Render one `Measure` to a figure.

Form follows the measure's job, which is what `kind` records:

  ``toplist``/``table``  -> an HTML bar table. Ranked named things, and the
      names here are Swedish statute titles -- 90 characters of "Kungörelse om
      tillämpning av …". SVG text cannot wrap or ellipsize, so the bar rides
      *inside* a table cell instead: the label wraps like prose, the bar is a
      CSS width, and the accessible table view is the chart rather than an
      alternative to it.
  ``series``    -> an SVG line. Ordered runs over time.
  ``histogram``/``bars``/``profile`` -> SVG columns. A profile's columns are
      values at sampled ranks (largest first), so each column is a real
      thing's own size, not a bucket count.
  ``matrix``    -> an HTML heat table (same label argument as toplist).
  ``sankey``    -> an SVG flow diagram. Volume moving from one category to
      another, where the same categories stand on both sides.
  ``scalar``    -> a hero number, no plot.

Colour comes from the page's own tokens, declared in `style.css` under `.viz`:
one accent hue for the single-series marks (every chart here *is* single-series
-- the corpus has one value per year, per bin, per law), and a validated
sequential ramp for the heat table's magnitude. There is no categorical palette
because nothing here encodes identity by colour; a legend would name one thing.

Every mark carries a `<title>`, which is the browser's own tooltip and the
screen-reader label at once -- an interaction layer that needs no script and
cannot break. Values are printed as text beside the marks, which is also the
relief the light-mode contrast check requires.
"""

import collections
import math

from markupsafe import Markup

from ..lib import tpl
from ..lib.render import escape
from .model import Measure, Row

# the vertical's one template environment: the figure forms here
# (figures.html), the page around them in stats.render (stats.html)
ENV = tpl.environment("ferenda.stats")
TPL = ENV.get_template("figures.html").module

# a sequential blue ramp, light -> dark, for the heat table's magnitude. Steps
# 250-700 of the validated ramp: the lightest step still clears 2:1 on the light
# surface, so the faintest cell is visible rather than merely pale.
HEAT = ("#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b")
# past this step the ramp is dark enough that ink-coloured text on it fails
HEAT_INK_FLIP = 3


def _fmt(value, unit=""):
    """A number as Swedish prose: thin-space thousands, no decimals unless the
    value is a percentage (where the decimal is the information)."""
    if unit == "procent":
        return ("%.1f %%" % value).replace(".", ",")
    return "{:,}".format(int(round(value))).replace(",", " ")


def _nice_max(value):
    """A round axis maximum at or above `value` -- 1/2/5 x a power of ten, so
    the gridlines land on numbers a reader can hold in their head."""
    if value <= 0:
        return 1
    exp = 10 ** math.floor(math.log10(value))
    for step in (1, 2, 2.5, 5, 10):
        if value <= step * exp:
            return step * exp
    return 10 * exp


# --------------------------------------------------------------------------
# HTML forms
# --------------------------------------------------------------------------

def toplist_html(measure, bars=True):
    """Ranked rows as a bar table: label, bar, value (stats.html `toplist`).
    The bar scale is per group, so a measure showing both ends of a range
    does not draw its short end as an invisible sliver against the long
    end's maximum. `bars=False` drops the bars -- the plain-list form a
    profile's named extremes take, where drawing bars again would only
    repeat the columns above."""
    rows = measure.rows
    if not rows:
        return TPL.empty()
    unit = measure.unit or "värde"
    tops = {}
    for r in rows:
        tops[r.group] = max(tops.get(r.group, 0), abs(r.value))
    items, seen = [], None
    for r in rows:
        if r.group != seen:
            seen = r.group
            if seen:
                items.append({"split": seen})
        items.append({"split": None,
                      "href": _href(r.uri) if r.uri else None,
                      "label": r.label, "detail": r.detail,
                      "width": "%.2f" % (100.0 * abs(r.value)
                                         / (tops[r.group] or 1)) if bars
                      else None,
                      "val": _fmt(r.value, unit)})
    return TPL.toplist(unit, items)


def matrix_html(measure):
    """A heat table (stats.html `heat`). The scale is logarithmic and says
    so: the largest cell here is four orders of magnitude above the
    smallest, and on a linear ramp every cell but one reads as empty."""
    cells = measure.cells
    if not cells:
        return TPL.empty()
    rows = sorted({c.row for c in cells})
    cols = sorted({c.col for c in cells})
    index = {(c.row, c.col): c.value for c in cells}
    hi = math.log10(max(c.value for c in cells))
    lo = math.log10(min(c.value for c in cells))
    span = (hi - lo) or 1

    def cell(row, col):
        value = index.get((row, col))
        if value is None:
            return {"nil": True}
        step = min(len(HEAT) - 1,
                   int(round((math.log10(value) - lo) / span * (len(HEAT) - 1))))
        return {"nil": False, "col": col, "color": HEAT[step],
                "on_dark": step >= HEAT_INK_FLIP, "val": _fmt(value)}

    return TPL.heat(cols, [{"label": row,
                            "cells": [cell(row, col) for col in cols]}
                           for row in rows])


def hero_html(measure):
    """One display-size number, or -- when the measure answers with several at
    once -- a row of tiles, each number with its own unit under it."""
    if measure.tiles:
        return TPL.tiles(measure.tiles)
    return TPL.hero(measure.display or _fmt(measure.value or 0, measure.unit))


# --------------------------------------------------------------------------
# SVG forms
# --------------------------------------------------------------------------

W, H = 720, 260
# PAD_T leaves a line's room above the top gridline for the y-axis caption. At
# 16 the caption sat on the same baseline as the topmost tick value and printed
# straight through it -- "TECKEN" over "1 000 000"
PAD_L, PAD_R, PAD_T, PAD_B = 56, 12, 28, 40


def _axes(maximum, xlabel, ylabel, pad_b=PAD_B):
    """Four recessive gridlines with their values, plus the axis captions."""
    parts = []
    for i in range(5):
        y = PAD_T + (H - PAD_T - pad_b) * (1 - i / 4)
        value = maximum * i / 4
        parts.append('<line class="viz-grid" x1="%d" y1="%.1f" x2="%d" y2="%.1f"/>'
                     % (PAD_L, y, W - PAD_R, y))
        parts.append('<text class="viz-tick" x="%d" y="%.1f" text-anchor="end">%s</text>'
                     % (PAD_L - 8, y + 4, _fmt(value)))
    if ylabel:
        parts.append('<text class="viz-axis" x="%d" y="%d" text-anchor="start">%s</text>'
                     % (PAD_L - 48, PAD_T - 12, escape(ylabel)))
    if xlabel:
        parts.append('<text class="viz-axis" x="%d" y="%d" text-anchor="end">%s</text>'
                     % (W - PAD_R, H - 4, escape(xlabel)))
    return "".join(parts)


def _svg(body, title):
    return ('<div class="viz-scroll"><svg class="viz-svg" viewBox="0 0 %d %d" '
            'role="img" aria-label="%s" preserveAspectRatio="xMidYMid meet">'
            "%s</svg></div>" % (W, H, escape(title), body))


def series_svg(measure):
    """A line over an ordered run. Markers are drawn only where the reader needs
    one -- the endpoints and the extremes -- because a dot on every one of 120
    years is noise, not data."""
    points = measure.points
    if len(points) < 2:
        return toplist_html(_as_toplist(measure))
    maximum = _nice_max(max(p.y for p in points))
    plot_w, plot_h = W - PAD_L - PAD_R, H - PAD_T - PAD_B
    xs = [PAD_L + plot_w * i / (len(points) - 1) for i in range(len(points))]
    ys = [PAD_T + plot_h * (1 - p.y / maximum) for p in points]

    body = [_axes(maximum, measure.xlabel, measure.ylabel)]
    body.append('<polyline class="viz-line" points="%s"/>'
                % " ".join("%.1f,%.1f" % (x, y)
                           for x, y in zip(xs, ys, strict=True)))
    peak = max(range(len(points)), key=lambda i: points[i].y)
    trough = min(range(len(points)), key=lambda i: points[i].y)
    for i in {0, len(points) - 1, peak, trough}:
        body.append('<circle class="viz-dot" cx="%.1f" cy="%.1f" r="4.5">'
                    "<title>%s — %s</title></circle>"
                    % (xs[i], ys[i], escape(points[i].x),
                       _fmt(points[i].y, measure.unit)))
    # the peak label names its unit and separates the two numbers with a dash,
    # never a colon: on a per-year series "1994: 1 210" reads as an SFS number,
    # which is the one thing every label on this site must not do by accident
    body.append('<text class="viz-peak" x="%.1f" y="%.1f" text-anchor="%s">%s</text>'
                % (xs[peak], ys[peak] - 10,
                   "start" if peak < len(points) / 2 else "end",
                   escape("%s — %s %s" % (points[peak].x,
                                          _fmt(points[peak].y), measure.unit))))
    # x ticks: first, last and a handful between, never all of them
    step = max(1, len(points) // 8)
    for i in range(0, len(points), step):
        body.append('<text class="viz-tick" x="%.1f" y="%d" text-anchor="middle">%s'
                    "</text>" % (xs[i], H - PAD_B + 18, escape(points[i].x)))
    # an invisible hit target per point, so the whole column is hoverable
    for i, p in enumerate(points):
        body.append('<rect class="viz-hit" x="%.1f" y="%d" width="%.1f" height="%d">'
                    "<title>%s — %s</title></rect>"
                    % (xs[i] - plot_w / len(points) / 2, PAD_T,
                       max(plot_w / len(points), 1), plot_h,
                       escape(p.x), _fmt(p.y, measure.unit)))
    return _svg("".join(body), measure.title)


def bars_svg(measure):
    """Vertical columns for a distribution or a category comparison. Rounded
    data-ends, anchored to the baseline; a 2px surface gap between neighbours.
    A dense run (a sampled profile's ~120 columns) gets a deeper bottom pad,
    so the axis caption clears the rotated rank ticks instead of printing
    through them."""
    points = measure.points
    if not points:
        return '<p class="viz-empty">Inga värden.</p>'
    maximum = _nice_max(max(p.y for p in points))
    pad_b = PAD_B + 14 if len(points) > 30 else PAD_B
    plot_w, plot_h = W - PAD_L - PAD_R, H - PAD_T - pad_b
    slot = plot_w / len(points)
    width = max(slot - 2, 2)                     # the 2px surface gap

    body = [_axes(maximum, measure.xlabel, measure.ylabel, pad_b)]
    for i, p in enumerate(points):
        height = plot_h * p.y / maximum
        x = PAD_L + slot * i + 1
        body.append('<rect class="viz-col" x="%.1f" y="%.1f" width="%.1f" '
                    'height="%.1f" rx="4"><title>%s — %s</title></rect>'
                    % (x, PAD_T + plot_h - height, width, max(height, 0),
                       escape(p.x), _fmt(p.y, measure.unit)))
        # every label up to 14 columns, every other up to 30, and past that
        # (a sampled profile's ~120) a handful plus the last -- the endpoints
        # are the anchors a rank axis is read by. The forced last label wins its
        # slot outright: where the stepped run lands within half a step of the
        # end, its label and the last one printed through each other ("2 955"
        # over "7 873")
        step = 1 if len(points) <= 14 else 2 if len(points) <= 30 \
            else max(2, len(points) // 8)
        last = len(points) - 1
        forced = len(points) > 30
        label = p.x if (i % step == 0
                        and not (forced and 0 < last - i < step * 0.75)) \
            or (forced and i == last) else ""
        if label:
            body.append('<text class="viz-tick" x="%.1f" y="%d" '
                        'text-anchor="end" transform="rotate(-35 %.1f %d)">%s</text>'
                        % (x + width / 2, H - pad_b + 16, x + width / 2,
                           H - pad_b + 16, escape(label[:22])))
    return _svg("".join(body), measure.title)


# --------------------------------------------------------------------------
# the flow diagram (29): who cites whom, as volume
# --------------------------------------------------------------------------

# The same set of groups stands on both sides, so a group that cites itself
# (förarbeten citing förarbeten) is a ribbon from its left node to its right
# node -- the bipartite form, which is the only one that can draw a self-flow
# at all. Geometry, in the units of the 760-wide viewBox:
SK_W = 760
SK_LABEL = 152          # room for a node's label column, each side
SK_NODE = 11            # the node bar's width
SK_GAP = 10             # the surface gap between two stacked nodes
SK_STACK = 480          # what the whole flow is scaled to, in height
SK_TOP = 26             # room above the stack for the two column captions
SK_MIN = 1.6            # the thinnest ribbon drawn (see `sankey_svg`)
SK_LABEL_GAP = 26       # least vertical room a two-line node label needs
# a flow smaller than this share of the total is left undrawn: 48 of the 100
# flows in the corpus are under a twentieth of a pixel, and drawn at the floor
# they would be a haze that reads as traffic. What stays covers 99.9 % of all
# references. The table under the figure carries every flow, drawn or not, and
# stats.html's note for 29 states this threshold to the reader -- keep the two
# in step.
SK_SHARE = 0.0001


def _sk_column(nodes, flows, heights, end, other_rank):
    """One side of the diagram, stacked top down. Returns the node bars as
    `{group: (y, height)}` and where each ribbon meets them as
    `{flow index: y}`.

    `end` reads the group this side of a flow is attached by (`.row` on the
    citing side, `.col` on the cited side). Inside a node the ribbons are
    stacked in the *other* side's node order, which is what keeps one node's
    own ribbons from crossing each other on the way across.

    The gap under a node grows until the node has room for its own label. Every
    bar keeps its true height -- what stretches is the whitespace between them,
    which carries nothing. The alternative, sliding crowded labels apart and
    running a leader line back to the bar, was tried first: eight labels in the
    thin end of the stack all ended up beside a bar that was not theirs."""
    stack = {node: sorted((i for i, f in enumerate(flows) if end(f) == node),
                          key=lambda i: other_rank[i]) for node in nodes}
    tall = {node: sum(heights[i] for i in stack[node]) for node in nodes}
    at, band, y = {}, {}, float(SK_TOP)
    for node, below in zip(nodes, nodes[1:] + [None], strict=True):
        at[node] = (y, tall[node])
        for i in stack[node]:
            band[i] = y
            y += heights[i]
        if below:
            y += max(SK_GAP,
                     SK_LABEL_GAP - tall[node] / 2 - tall[below] / 2)
    return at, band


def _sk_span(at):
    """How tall one column came out, bars and padding together."""
    return max(y + h for y, h in at.values()) - SK_TOP


def sankey_svg(measure):
    """The citation graph as flow: citing group on the left, cited group on the
    right, ribbon thickness the number of references.

    Thickness is linear in the count, which is the only honest reading of a flow
    diagram -- and the corpus spans four orders of magnitude, so the small flows
    are hairlines. `SK_MIN` is the floor: a ribbon under it is drawn at it, and a
    node bar is the sum of its ribbons *as drawn*, so the picture stays
    internally consistent where the floor lifts one. The distortion is a few
    pixels over the whole stack; the number beside every node and the table under
    the figure are the exact ones.

    Colour carries nothing here: every ribbon is the page accent, because both
    ends of a ribbon are named in text and a hue per group -- there are fourteen
    -- could not be told apart anyway. Depth comes from the fill being
    translucent, so crossings darken."""
    cells = measure.cells
    if not cells:
        return TPL.empty()
    total = sum(c.value for c in cells)
    flows = [c for c in cells if c.value >= total * SK_SHARE]
    scale = SK_STACK / sum(c.value for c in flows)
    heights = [max(c.value * scale, SK_MIN) for c in flows]

    # both sides in one order -- by how much the group takes part either way --
    # so a group sits at about the same height on both sides and can be followed
    # across. Ordering each side by its own volume instead would put Förarbeten
    # (which cites most) opposite Författningar (which is cited most), and every
    # ribbon in the diagram would cross.
    weight = collections.Counter()
    for c in flows:
        weight[c.row] += c.value
        weight[c.col] += c.value
    rank = {g: i for i, g in enumerate(sorted(weight, key=lambda g: -weight[g]))}
    left = sorted({c.row for c in flows}, key=lambda g: rank[g])
    right = sorted({c.col for c in flows}, key=lambda g: rank[g])

    out_at, out_band = _sk_column(left, flows, heights, lambda c: c.row,
                                  [rank[c.col] for c in flows])
    in_at, in_band = _sk_column(right, flows, heights, lambda c: c.col,
                                [rank[c.row] for c in flows])
    # both sides hold the same ribbons, so the two stacks differ only in the
    # padding their labels asked for; centring the shorter one keeps them level
    shift = (_sk_span(out_at) - _sk_span(in_at)) / 2

    x0, x1 = SK_LABEL, SK_W - SK_LABEL - SK_NODE
    mid = (x0 + SK_NODE + x1) / 2
    body = ['<text class="viz-axis" x="%d" y="%d" text-anchor="end">hänvisar'
            '</text><text class="viz-axis" x="%d" y="%d">hänvisas till</text>'
            % (x0 + SK_NODE, SK_TOP - 10, x1, SK_TOP - 10)]

    for i in sorted(range(len(flows)), key=lambda i: -heights[i]):
        c, h = flows[i], heights[i]     # thickest first: a hairline stays on top
        ly, ry = out_band[i], in_band[i] + shift
        body.append('<path class="viz-flow" d="M%.1f %.1f C%.1f %.1f %.1f %.1f '
                    '%.1f %.1f L%.1f %.1f C%.1f %.1f %.1f %.1f %.1f %.1f Z">'
                    "<title>%s → %s: %s %s (%s)</title></path>"
                    % (x0 + SK_NODE, ly, mid, ly, mid, ry, x1, ry,
                       x1, ry + h, mid, ry + h, mid, ly + h, x0 + SK_NODE, ly + h,
                       escape(c.row), escape(c.col), _fmt(c.value),
                       escape(measure.unit),
                       _fmt(100.0 * c.value / total, "procent")))

    bottom = SK_TOP
    for nodes, at, end, x, dy, anchor, lx, verb in (
            (left, out_at, lambda c: c.row, x0, 0.0, "end", x0 - 10, "utgående"),
            (right, in_at, lambda c: c.col, x1, shift, "start",
             x1 + SK_NODE + 10, "inkommande")):
        for node in nodes:
            y, h = at[node][0] + dy, at[node][1]
            # the group's whole volume, undrawn flows included -- the bar draws
            # what is above the threshold, the number states what there is
            value = sum(c.value for c in cells if end(c) == node)
            # the last node's second label line sits below its bar's end
            bottom = max(bottom, y + h, y + h / 2 + 16)
            body.append('<rect class="viz-node" x="%d" y="%.1f" width="%d" '
                        'height="%.1f" rx="2"><title>%s: %s %s %s</title></rect>'
                        % (x, y, SK_NODE, h, escape(node), _fmt(value),
                           verb, escape(measure.unit)))
            body.append('<text class="viz-nodelabel" x="%d" y="%.1f" '
                        'text-anchor="%s">%s</text>'
                        '<text class="viz-nodeval" x="%d" y="%.1f" '
                        'text-anchor="%s">%s</text>'
                        % (lx, y + h / 2 - 2, anchor, escape(node),
                           lx, y + h / 2 + 11, anchor, _fmt(value)))
    return ('<div class="viz-scroll"><svg class="viz-svg viz-sankey" '
            'viewBox="0 0 %d %.0f" role="img" aria-label="%s" '
            'preserveAspectRatio="xMidYMid meet">%s</svg></div>'
            % (SK_W, bottom + 8, escape(measure.title), "".join(body)))


def _as_toplist(measure):
    """A series too short to draw a line through, shown as its rows instead."""
    return Measure(measure.id, measure.group, measure.title, "toplist",
                   unit=measure.unit,
                   rows=[Row(p.x, p.y) for p in measure.points])


def _href(uri):
    return uri[len("https://lagen.nu"):] if uri.startswith("https://lagen.nu") else uri


# --------------------------------------------------------------------------

_FORMS = {"toplist": toplist_html, "matrix": matrix_html,
          "scalar": hero_html, "series": series_svg, "sankey": sankey_svg,
          "histogram": bars_svg, "bars": bars_svg, "profile": bars_svg}


def figure(measure):
    """The measure's figure, plus -- for the plotted forms -- the table view the
    accessibility pass requires and the low-contrast marks oblige."""
    html = Markup(_FORMS[measure.kind](measure))
    # a flow diagram opens on the corpus total (which is the hero's, larger than
    # the ribbons' -- the lede splits the two) and closes on the table: the
    # ribbons under the drawing threshold are readable nowhere else
    if measure.kind == "sankey":
        html = Markup(hero_html(measure)) + html + TPL.data_table(
            "från → till", measure.unit or "värde",
            [{"x": "%s → %s" % (c.row, c.col), "y": _fmt(c.value)}
             for c in measure.cells])
    # a profile may carry named extremes; render them as a plain list under
    # the curve -- the columns did the comparing, so the rows get no bars
    if measure.kind == "profile" and measure.rows:
        html += toplist_html(measure, bars=False)
    # a profile gets no table view, and is the one plotted form that loses
    # nothing by it: its rows are 100 lines of "plats 1 743 -> 214 tecken",
    # naming no thing a reader can look up, where a histogram's or a series'
    # rows name a bin and a year. What the profile *does* name -- the record
    # holders at each end -- is the row list above, and every column still
    # carries its own <title> for the pointer and the screen reader
    if measure.kind in ("series", "histogram", "bars") and measure.points:
        html += TPL.data_table(measure.xlabel or "kategori",
                               measure.unit or "värde",
                               [{"x": p.x, "y": _fmt(p.y, measure.unit)}
                                for p in measure.points])
    # a scalar may carry a supporting list; render it under the hero number
    if measure.kind == "scalar" and measure.rows:
        html += toplist_html(measure)
    return html
