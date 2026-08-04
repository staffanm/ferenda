"""Parse the editorial ``site/`` markdown (in the ``lagen-wiki`` content repo)
into JSON artifacts. Three fixed basefiles:

  * ``frontpage``       <- ``site/frontpage.md``   -> a ``Frontpage`` (the curated
    law list: ``## <Category>`` headings + ``- [Label](sfs:…)`` bullets)
  * ``om/<slug>``       <- ``site/om/<slug>.md``   -> an ``AboutPage``
  * ``sitenews``        <- ``site/sitenews.md``    -> a ``Sitenews`` (its body is
    split into dated ``NewsItem``s on the ``## YYYY-MM-DD HH:MM:SS Title`` heads)

Editorial content needs the whole ordinary markdown vocabulary -- tables,
ordered lists, emphasis -- which the legal-prose parser (``lib.markdown``:
headings + paragraphs) deliberately does not have. So the block and inline
layers here are parsed by **markdown-it-py** (CommonMark + the GFM table rule)
rather than by a line scanner of our own; what stays local is only the mapping
from its token tree onto this vertical's typed blocks and runs. Link *targets*
are still resolved by the shared grammar (``lib.markdown.target_uri``, extended
here with site-relative and ``mailto:`` targets) -- one place decides what a
link means, whichever parser found it.

A construct the block model has no node for is a `ValueError` naming the
basefile, not silently dropped prose (rule:errors-drive-retry-use-raise): the
content repo is authored by hand, and a table that renders as a wall of pipes is
exactly the failure this parser exists to prevent.
"""

import dataclasses
import re
from pathlib import Path

from markdown_it import MarkdownIt
from markdown_it.tree import SyntaxTreeNode

from ..lib import markdown
from .model import (
    AboutPage,
    Bullets,
    Code,
    Frontpage,
    Heading,
    NewsItem,
    Paragraph,
    Rule,
    Sitenews,
    Table,
)

# CommonMark + GFM pipe tables. `html: False` leaves a literal `<b>` as text:
# every run is escaped on the way out, so raw HTML has no route through and
# should not look as though it has one.
_MD = MarkdownIt("commonmark", {"html": False}).enable("table")

# a sitenews section head: `## 2020-09-17 23:00:00 Title`
RE_NEWS_HEAD = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+(.*)$")

# markdown-it's `style: text-align:left` cell attribute -> the model's `align`
_ALIGN = re.compile(r"text-align:(left|center|right)")


# --------------------------------------------------------------------------
# inline runs
# --------------------------------------------------------------------------

def _site_target(target):
    """A site link target -> its run uri, or None if unrecognised (left literal).
    Reuses the shared grammar (``begrepp:``/``sfs:``/``eurlex:``/external) and
    adds the two forms only editorial pages need: site-relative ``/…``/``#…``
    cross-links, and ``mailto:`` (the about pages and sitenews print contact
    addresses; legal prose never does, so this stays out of ``lib.markdown``)."""
    uri = markdown.target_uri(target)
    if uri is not None:
        return uri
    if target.startswith(("/", "#", "mailto:")):
        return target
    return None


def _run(text, bold=False, italic=False, code=False, uri=None):
    if not (bold or italic or code or uri):
        return text
    run = {"text": text}
    if uri:
        run["uri"] = uri
    if bold:
        run["bold"] = True
    if italic:
        run["italic"] = True
    if code:
        run["code"] = True
    return run


def _runs(node, where, bold=False, italic=False, uri=None):
    """A markdown-it ``inline`` node -> the artifact's run list. Emphasis and
    links nest (``**bold [label](x)**``), so the styling in force is carried
    down the walk rather than tracked as flat spans."""
    out = []
    for child in node.children:
        kind = child.type
        if kind == "text":
            out.append(_run(child.content, bold, italic, False, uri))
        elif kind == "code_inline":
            out.append(_run(child.content, bold, italic, True, uri))
        elif kind in ("softbreak", "hardbreak"):
            # Both collapse to a space, deliberately. A softbreak is the
            # author's wrap column, which is typography and not content. A
            # hardbreak (two trailing spaces) is nominally an explicit break,
            # but in hand-authored prose it is almost always an invisible typo,
            # and the block model has no line-break node to put one in --
            # collapsing beats either inventing a node for a typo or failing a
            # whole page's parse over two characters nobody can see.
            out.append(_run(" ", bold, italic, False, uri))
        elif kind == "strong":
            out += _runs(child, where, True, italic, uri)
        elif kind == "em":
            out += _runs(child, where, bold, True, uri)
        elif kind == "link":
            out += _link_runs(child, where, bold, italic, uri)
        else:
            raise ValueError(
                "%s: inline markdown %r has no run form (site/parse.py maps "
                "text, code, emphasis and links)" % (where, kind))
    return _merge(r for r in out if r != "")


def _merge(runs):
    """Adjacent runs that carry the same styling become one run. Where the
    author hard-wrapped inside a link or a bold span, the softbreak arrives as
    its own run with that styling on it, and the renderer emits one element per
    run -- so a wrapped link rendered as three `<a>`s, the middle one an
    arrow-suffixed space. The wrap column is the author's typography, not
    content, and must not reach the artifact as structure (rule:artifact-is-truth)."""
    out = []
    for run in runs:
        if out and _style(out[-1]) == _style(run):
            out[-1] = (out[-1] + run if isinstance(run, str)
                       else {**run, "text": out[-1]["text"] + run["text"]})
        else:
            out.append(run)
    return out


def _style(run):
    """The styling a run carries -- what decides whether two may be joined."""
    return (None if isinstance(run, str)
            else (run.get("uri"), run.get("bold"), run.get("italic"),
                  run.get("code")))


def _link_runs(node, where, bold, italic, outer_uri):
    """A ``link`` node -> its runs. A target the site grammar doesn't recognise
    keeps its markdown source verbatim, so a mistyped link reads as the broken
    thing it is rather than quietly losing its target."""
    target = str(node.attrs["href"]).strip()
    uri = _site_target(target)
    if uri:
        runs = _runs(node, where, bold, italic, uri)
        if not runs:
            # `[](/a)`: the target resolved but there is nothing to hang it on,
            # so the link would vanish without trace -- the silent loss this
            # module exists to prevent (rule:errors-drive-retry-use-raise)
            raise ValueError(
                "%s: the link to %r has an empty label -- a link with no text "
                "renders as nothing at all" % (where, target))
        return runs
    return ([_run("[", bold, italic, False, outer_uri)]
            + _runs(node, where, bold, italic, outer_uri)
            + [_run("](%s)" % target, bold, italic, False, outer_uri)])


def _text(node):
    """An ``inline`` node -> its plain text (a `Heading` carries text, not runs).

    Every leaf that holds characters counts: keeping only `text` tokens dropped
    a heading's code spans outright (``## The `foo` field`` -> "The  field"),
    which is the silent-loss failure this module exists to prevent."""
    return "".join(t.content for t in node.walk()
                   if t.type in ("text", "code_inline"))


# --------------------------------------------------------------------------
# blocks
# --------------------------------------------------------------------------

def blocks(body, where):
    """A markdown body -> a list of block dataclasses. `where` names the source
    (a basefile) so a construct with no block form points at the file to fix."""
    return _blocks(SyntaxTreeNode(_MD.parse(body)).children, where)


def _blocks(nodes, where):
    out = []
    for node in nodes:
        kind = node.type
        if kind == "heading":
            out.append(Heading(_text(node.children[0]), int(node.tag[1:])))
        elif kind == "paragraph":
            out.append(Paragraph(_runs(node.children[0], where)))
        elif kind in ("bullet_list", "ordered_list"):
            out.append(Bullets([_item(li, where) for li in node.children],
                               ordered=kind == "ordered_list"))
        elif kind == "table":
            out.append(_table(node, where))
        elif kind in ("fence", "code_block"):
            out.append(Code(node.content.rstrip("\n")))
        elif kind == "hr":
            out.append(Rule())
        else:
            raise ValueError(
                "%s: block markdown %r has no block form (site/model.py has "
                "rubrik, stycke, lista, tabell, kod, avdelare)" % (where, kind))
    return out


def _item(li, where):
    """A ``list_item`` node -> its run list. One paragraph per item: the model
    holds a flat run list per ``<li>``, so a nested list or a second paragraph
    inside an item has nowhere to go and says so."""
    kinds = [c.type for c in li.children]
    if kinds != ["paragraph"]:
        raise ValueError(
            "%s: list item holds %s -- a site list item is one paragraph "
            "(Bullets.items is one run list per <li>)" % (where, kinds))
    return _runs(li.children[0].children[0], where)


def _table(node, where):
    """A ``table`` node -> a `Table`. GFM requires the header row, so `thead` is
    always present; `tbody` is absent for a header-only table."""
    sections = {c.type: c for c in node.children}
    header = sections["thead"].children[0]
    body = sections["tbody"].children if "tbody" in sections else []
    return Table(head=[_cell(c, where) for c in header.children],
                 rows=[[_cell(c, where) for c in row.children] for row in body],
                 align=[_align(c) for c in header.children])


def _cell(cell, where):
    """A ``th``/``td`` node -> its run list. An empty cell has no inline child."""
    return _runs(cell.children[0], where) if cell.children else []


def _align(cell):
    m = _ALIGN.search(str(cell.attrs.get("style", "")))
    return m.group(1) if m else None


# --------------------------------------------------------------------------
# artifacts
# --------------------------------------------------------------------------

def _read(path):
    return markdown.frontmatter(Path(path).read_text(encoding="utf-8"))


def _news_id(published):
    """A datetime -> a stable anchor / Atom id fragment: ``n2020-09-17-23-00-00``."""
    return "n" + re.sub(r"[ :]", "-", published)


def frontpage_artifact(path):
    meta, body = _read(path)
    return Frontpage(title=meta["title"], blocks=blocks(body, "frontpage"))


def about_artifact(slug, path):
    meta, body = _read(path)
    return AboutPage(slug=slug, title=meta["title"],
                     blocks=blocks(body, "om/" + slug))


def sitenews_artifact(path):
    meta, body = _read(path)
    items, head, buf = [], None, []

    def flush():
        if head:
            items.append(NewsItem(id=_news_id(head[0]), published=head[0],
                                  title=head[1],
                                  blocks=blocks("\n".join(buf), "sitenews")))

    for line in body.splitlines():
        h = markdown.RE_HEADING.match(line.strip())
        m = RE_NEWS_HEAD.match(h.group(2)) if h else None
        if m:
            flush()
            head, buf = (m.group(1), m.group(2).strip()), []
        elif head is not None:
            buf.append(line)
    flush()
    return Sitenews(title=meta["title"], items=items)


# --------------------------------------------------------------------------
# basefile <-> path index (mirrors wiki/parse.py's begrepp_index/kommentar_index)
# --------------------------------------------------------------------------

def _site_dir(root):
    d = Path(root) / "site"
    assert d.is_dir(), (
        "site content dir %s missing -- WIKI_ROOT (%s) must point at the "
        "lagen-wiki markdown repo; run tools/migrate_site_content.py to populate "
        "site/ or clone the content repo next to this one" % (d, root))
    return d


def list_basefiles(root):
    """The site basefiles present on disk: ``frontpage``, ``sitenews`` (when
    their file exists), and ``om/<slug>`` for each ``site/om/*.md``."""
    d = _site_dir(root)
    out = []
    if (d / "frontpage.md").exists():
        out.append("frontpage")
    if (d / "sitenews.md").exists():
        out.append("sitenews")
    out += ["om/" + p.stem for p in sorted((d / "om").glob("*.md"))]
    return out


def record(root, basefile):
    """basefile -> its source markdown path."""
    d = _site_dir(root)
    if basefile == "frontpage":
        return d / "frontpage.md"
    if basefile == "sitenews":
        return d / "sitenews.md"
    assert basefile.startswith("om/"), "unknown site basefile %r" % basefile
    return d / "om" / (basefile[len("om/"):] + ".md")


def artifact(root, basefile):
    """basefile -> its parsed artifact as a plain JSON-serialisable dict."""
    path = record(root, basefile)
    if basefile == "frontpage":
        art = frontpage_artifact(path)
    elif basefile == "sitenews":
        art = sitenews_artifact(path)
    else:
        art = about_artifact(basefile[len("om/"):], path)
    return dataclasses.asdict(art)
