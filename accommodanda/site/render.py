"""Render the parsed site artifacts (frontpage, /om about pages, sitenews) to
static HTML under the generated-site root, reusing the shared page chrome
(``lib.render.page``) so the editorial pages carry the same masthead, fonts and
styling as every document page. ``write_site`` is the single entry point the
build driver calls during ``generate``.

The frontpage overwrites the generic corpus-stats ``index.html`` (the driver
skips writing that one when a curated frontpage artifact exists, so there is no
write-then-clobber). The sitenews listing lives at ``/dataset/sitenews/feed``
(a directory index) with an Atom feed beside it at ``/dataset/sitenews/feed.atom``
-- the URLs the legacy site published.
"""

import json
from pathlib import Path

from ..lib import layout
from ..lib.render import escape, href, page

FEED_URL = "https://lagen.nu/dataset/sitenews/feed"


# --------------------------------------------------------------------------
# inline runs + blocks -> HTML
# --------------------------------------------------------------------------

def _run_html(run):
    if isinstance(run, str):
        return escape(run)
    html = escape(run["text"])
    if run.get("code"):
        html = "<code>%s</code>" % html
    if "uri" in run:
        uri = run["uri"]
        ext = not (uri.startswith("https://lagen.nu") or uri[:1] in ("/", "#"))
        html = '<a%s href="%s"%s>%s</a>' % (
            ' class="ext"' if ext else "", escape(href(uri)),
            ' rel="external"' if ext else "", html)
    if run.get("bold"):
        html = "<strong>%s</strong>" % html
    return html


def _runs_html(runs):
    return "".join(_run_html(r) for r in runs)


def _runs_text(runs):
    return "".join(r if isinstance(r, str) else r["text"] for r in runs)


def _block_html(block):
    t = block["type"]
    if t == "rubrik":
        lvl = min(max(block["level"], 2), 4)
        return "<h%d>%s</h%d>" % (lvl, escape(block["text"]), lvl)
    if t == "stycke":
        return "<p>%s</p>" % _runs_html(block["runs"])
    if t == "lista":
        return "<ul>%s</ul>" % "".join(
            "<li>%s</li>" % _runs_html(item) for item in block["items"])
    if t == "kod":
        return "<pre>%s</pre>" % escape(block["text"])
    raise ValueError("unknown site block type %r" % t)


def _blocks_html(blocks):
    return "".join(_block_html(b) for b in blocks)


# --------------------------------------------------------------------------
# page renderers
# --------------------------------------------------------------------------

def render_frontpage(art):
    """The curated frontpage: the categorised law list, replacing the generic
    corpus-stats page. A `solo` (single-column) page; the `.frontpage` wrapper
    drives the multi-column CSS (cluster `## ` headings span all columns, the
    `### category` + law lists flow into columns) so the dense index stays
    scannable in two levels."""
    body = '<div class="frontpage">%s</div>' % _blocks_html(art["blocks"])
    return page(art["title"], "Start", "", body,
                eyebrow="Sveriges lagar, med kontext", solo=True,
                body_class=" site")


def render_about(art):
    """An /om about page -- hand-authored prose + links, single column."""
    return page(art["title"], "Om", "", _blocks_html(art["blocks"]),
                solo=True, body_class=" site")


def render_sitenews(art):
    """The news listing: every item in full, each an ``<article>`` anchored by
    its id so the Atom feed's per-entry links resolve to it."""
    items = sorted(art["items"], key=lambda it: it["published"], reverse=True)
    articles = []
    for it in items:
        articles.append(
            '<article class="news-item" id="%s"><p class="news-date">%s</p>'
            '<h2>%s</h2>%s</article>'
            % (escape(it["id"]), escape(it["published"][:10]),
               escape(it["title"]), _blocks_html(it["blocks"])))
    body = ('<p class="feed-link"><a class="ext" rel="external" '
            'href="/dataset/sitenews/feed.atom">Atom-flöde</a></p>%s'
            % "".join(articles))
    return page(art["title"], "Nyheter", "", body, eyebrow="Nyheter", solo=True,
                body_class=" site")


# --------------------------------------------------------------------------
# Atom feed
# --------------------------------------------------------------------------

def _rfc3339(published):
    """"2020-09-17 23:00:00" -> "2020-09-17T23:00:00Z". The authored datetimes
    are naive local time; the feed publishes them as UTC-stamped instants (a
    news feed does not need sub-hour precision)."""
    return published.replace(" ", "T") + "Z"


def _summary(item):
    """The teaser text for a feed entry: its first paragraph, plain."""
    for b in item["blocks"]:
        if b["type"] == "stycke":
            return _runs_text(b["runs"])
    return item["title"]


def render_atom(art):
    items = sorted(art["items"], key=lambda it: it["published"], reverse=True)
    updated = _rfc3339(items[0]["published"]) if items else "1970-01-01T00:00:00Z"
    entries = []
    for it in items:
        permalink = "%s#%s" % (FEED_URL, it["id"])
        entries.append(
            "<entry><title>%s</title><id>%s</id>"
            '<link rel="alternate" href="%s"/>'
            "<updated>%s</updated><published>%s</published>"
            '<summary type="text">%s</summary></entry>'
            % (escape(it["title"]), escape(permalink), escape(permalink),
               _rfc3339(it["published"]), _rfc3339(it["published"]),
               escape(_summary(it))))
    return ('<?xml version="1.0" encoding="utf-8"?>\n'
            '<feed xmlns="http://www.w3.org/2005/Atom">'
            "<title>%s</title><id>%s</id>"
            '<link rel="self" href="%s.atom"/>'
            '<link rel="alternate" href="%s"/>'
            "<updated>%s</updated>%s</feed>\n"
            % (escape(art["title"]), escape(FEED_URL), escape(FEED_URL),
               escape(FEED_URL), updated, "".join(entries)))


# --------------------------------------------------------------------------
# driver entry point
# --------------------------------------------------------------------------

def has_frontpage():
    """Whether a curated frontpage artifact exists -- the driver uses this to
    decide whether to suppress the generic corpus-stats ``index.html``."""
    return layout.artifact("site", "frontpage").exists()


def write_site(out_root):
    """Write every parsed site artifact to its page(s) under `out_root`: the
    frontpage to ``index.html``, each about page to ``om/<slug>.html``, and the
    sitenews listing + Atom feed under ``dataset/sitenews/feed``. Driven purely
    by which artifacts exist (an empty site source writes nothing)."""
    out = Path(out_root)
    for path in layout.artifacts("site"):
        art = json.loads(path.read_text(encoding="utf-8"))
        if art["type"] == "frontpage":
            (out / "index.html").write_text(render_frontpage(art))
        elif art["type"] == "om":
            dest = out / "om" / (art["slug"] + ".html")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(render_about(art))
        elif art["type"] == "sitenews":
            feed = out / "dataset" / "sitenews" / "feed"
            feed.mkdir(parents=True, exist_ok=True)
            (feed / "index.html").write_text(render_sitenews(art))
            (feed.parent / "feed.atom").write_text(render_atom(art))
        else:
            raise ValueError("unknown site artifact type %r at %s" % (art["type"], path))
