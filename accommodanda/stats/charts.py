"""Render one `Measure` to a figure.

Form follows the measure's job, which is what `kind` records:

  ``toplist``/``table``  -> an HTML bar table. Ranked named things, and the
      names here are Swedish statute titles -- 90 characters of "Kungörelse om
      tillämpning av …". SVG text cannot wrap or ellipsize, so the bar rides
      *inside* a table cell instead: the label wraps like prose, the bar is a
      CSS width, and the accessible table view is the chart rather than an
      alternative to it.
  ``series``    -> an SVG line. Ordered runs over time.
  ``histogram``/``bars`` -> SVG columns.
  ``matrix``    -> an HTML heat table (same label argument as toplist).
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

import math

from ..lib.render import escape
from .model import Measure, Row

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

def toplist_html(measure):
    """Ranked rows as a bar table: label, bar, value. The bar is a background
    width on the value cell, so it never fights the text for space."""
    rows = measure.rows
    if not rows:
        return '<p class="viz-empty">Inga värden.</p>'
    # one bar scale per group, so a measure showing both ends of a range does not
    # draw its short end as an invisible sliver against the long end's maximum
    tops = {}
    for r in rows:
        tops[r.group] = max(tops.get(r.group, 0), abs(r.value))
    out = ['<table class="viz-bars"><thead><tr><th scope="col">Vad</th>'
           '<th scope="col" class="num">%s</th></tr></thead><tbody>'
           % escape(measure.unit or "värde")]
    seen = None
    for r in rows:
        if r.group != seen:
            seen = r.group
            if seen:
                out.append('<tr class="viz-split"><th scope="rowgroup" colspan="2">'
                           "%s</th></tr>" % escape(seen))
        top = tops[r.group] or 1
        label = escape(r.label)
        if r.uri:
            label = '<a href="%s">%s</a>' % (escape(_href(r.uri)), label)
        if r.detail:
            label += ' <span class="viz-detail">%s</span>' % escape(r.detail)
        out.append(
            '<tr><th scope="row">%s</th>'
            '<td class="num"><span class="viz-bar" style="--w:%.2f%%"></span>'
            '<span class="viz-val">%s</span></td></tr>'
            % (label, 100.0 * abs(r.value) / top, _fmt(r.value, measure.unit)))
    out.append("</tbody></table>")
    return "".join(out)


def matrix_html(measure):
    """A heat table. The scale is logarithmic and says so: the largest cell here
    is four orders of magnitude above the smallest, and on a linear ramp every
    cell but one reads as empty."""
    cells = measure.cells
    if not cells:
        return '<p class="viz-empty">Inga värden.</p>'
    rows = sorted({c.row for c in cells})
    cols = sorted({c.col for c in cells})
    index = {(c.row, c.col): c.value for c in cells}
    hi = math.log10(max(c.value for c in cells))
    lo = math.log10(min(c.value for c in cells))
    span = (hi - lo) or 1

    out = ['<div class="viz-scroll"><table class="viz-heat">'
           '<thead><tr><th scope="col"><span class="vh">Från ↓ till →</span></th>']
    for col in cols:
        out.append('<th scope="col">%s</th>' % escape(col))
    out.append("</tr></thead><tbody>")
    for row in rows:
        out.append('<tr><th scope="row">%s</th>' % escape(row))
        for col in cols:
            value = index.get((row, col))
            if value is None:
                out.append('<td class="viz-nil"><span class="vh">—</span></td>')
                continue
            step = min(len(HEAT) - 1,
                       int(round((math.log10(value) - lo) / span * (len(HEAT) - 1))))
            out.append(
                '<td class="viz-cell%s" style="--c:%s" title="%s → %s: %s">'
                "<span>%s</span></td>"
                % (" on-dark" if step >= HEAT_INK_FLIP else "", HEAT[step],
                   escape(row), escape(col), _fmt(value), _fmt(value)))
        out.append("</tr>")
    out.append("</tbody></table></div>")
    return "".join(out)


def hero_html(measure):
    display = measure.display or _fmt(measure.value or 0, measure.unit)
    return '<p class="viz-hero">%s</p>' % escape(display)


# --------------------------------------------------------------------------
# SVG forms
# --------------------------------------------------------------------------

W, H = 720, 260
PAD_L, PAD_R, PAD_T, PAD_B = 56, 12, 16, 40


def _axes(maximum, xlabel, ylabel):
    """Four recessive gridlines with their values, plus the axis captions."""
    parts = []
    for i in range(5):
        y = PAD_T + (H - PAD_T - PAD_B) * (1 - i / 4)
        value = maximum * i / 4
        parts.append('<line class="viz-grid" x1="%d" y1="%.1f" x2="%d" y2="%.1f"/>'
                     % (PAD_L, y, W - PAD_R, y))
        parts.append('<text class="viz-tick" x="%d" y="%.1f" text-anchor="end">%s</text>'
                     % (PAD_L - 8, y + 4, _fmt(value)))
    if ylabel:
        parts.append('<text class="viz-axis" x="%d" y="%d" text-anchor="start">%s</text>'
                     % (PAD_L - 48, PAD_T - 4, escape(ylabel)))
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
                    "<title>%s: %s</title></circle>"
                    % (xs[i], ys[i], escape(points[i].x),
                       _fmt(points[i].y, measure.unit)))
    body.append('<text class="viz-peak" x="%.1f" y="%.1f" text-anchor="%s">%s</text>'
                % (xs[peak], ys[peak] - 10,
                   "start" if peak < len(points) / 2 else "end",
                   escape("%s: %s" % (points[peak].x, _fmt(points[peak].y)))))
    # x ticks: first, last and a handful between, never all of them
    step = max(1, len(points) // 8)
    for i in range(0, len(points), step):
        body.append('<text class="viz-tick" x="%.1f" y="%d" text-anchor="middle">%s'
                    "</text>" % (xs[i], H - PAD_B + 18, escape(points[i].x)))
    # an invisible hit target per point, so the whole column is hoverable
    for i, p in enumerate(points):
        body.append('<rect class="viz-hit" x="%.1f" y="%d" width="%.1f" height="%d">'
                    "<title>%s: %s</title></rect>"
                    % (xs[i] - plot_w / len(points) / 2, PAD_T,
                       max(plot_w / len(points), 1), plot_h,
                       escape(p.x), _fmt(p.y, measure.unit)))
    return _svg("".join(body), measure.title)


def bars_svg(measure):
    """Vertical columns for a distribution or a category comparison. Rounded
    data-ends, anchored to the baseline; a 2px surface gap between neighbours."""
    points = measure.points
    if not points:
        return '<p class="viz-empty">Inga värden.</p>'
    maximum = _nice_max(max(p.y for p in points))
    plot_w, plot_h = W - PAD_L - PAD_R, H - PAD_T - PAD_B
    slot = plot_w / len(points)
    width = max(slot - 2, 2)                     # the 2px surface gap

    body = [_axes(maximum, measure.xlabel, measure.ylabel)]
    for i, p in enumerate(points):
        height = plot_h * p.y / maximum
        x = PAD_L + slot * i + 1
        body.append('<rect class="viz-col" x="%.1f" y="%.1f" width="%.1f" '
                    'height="%.1f" rx="4"><title>%s: %s</title></rect>'
                    % (x, PAD_T + plot_h - height, width, max(height, 0),
                       escape(p.x), _fmt(p.y, measure.unit)))
        label = p.x if len(points) <= 14 else (p.x if i % 2 == 0 else "")
        if label:
            body.append('<text class="viz-tick" x="%.1f" y="%d" '
                        'text-anchor="end" transform="rotate(-35 %.1f %d)">%s</text>'
                        % (x + width / 2, H - PAD_B + 16, x + width / 2,
                           H - PAD_B + 16, escape(label[:22])))
    return _svg("".join(body), measure.title)


def _as_toplist(measure):
    """A series too short to draw a line through, shown as its rows instead."""
    return Measure(measure.id, measure.group, measure.title, "toplist",
                   unit=measure.unit,
                   rows=[Row(p.x, p.y) for p in measure.points])


def _href(uri):
    return uri[len("https://lagen.nu"):] if uri.startswith("https://lagen.nu") else uri


# --------------------------------------------------------------------------

_FORMS = {"toplist": toplist_html, "table": toplist_html, "matrix": matrix_html,
          "scalar": hero_html, "series": series_svg,
          "histogram": bars_svg, "bars": bars_svg}


def figure(measure):
    """The measure's figure, plus -- for the plotted forms -- the table view the
    accessibility pass requires and the low-contrast marks oblige."""
    html = _FORMS[measure.kind](measure)
    if measure.kind in ("series", "histogram", "bars") and measure.points:
        rows = "".join("<tr><th scope=\"row\">%s</th><td class=\"num\">%s</td></tr>"
                       % (escape(p.x), _fmt(p.y, measure.unit))
                       for p in measure.points)
        html += ('<details class="viz-data"><summary>Visa som tabell</summary>'
                 '<div class="viz-scroll"><table class="viz-plain"><thead><tr>'
                 '<th scope="col">%s</th><th scope="col" class="num">%s</th></tr>'
                 "</thead><tbody>%s</tbody></table></div></details>"
                 % (escape(measure.xlabel or "kategori"),
                    escape(measure.unit or "värde"), rows))
    # a scalar may carry a supporting list; render it under the hero number
    if measure.kind == "scalar" and measure.rows:
        html += toplist_html(measure)
    return html
