"""Parse the editorial ``site/`` markdown (in the ``lagen-wiki`` content repo)
into JSON artifacts. Three fixed basefiles:

  * ``frontpage``       <- ``site/frontpage.md``   -> a ``Frontpage`` (the curated
    law list: ``## <Category>`` headings + ``- [Label](sfs:…)`` bullets)
  * ``om/<slug>``       <- ``site/om/<slug>.md``   -> an ``AboutPage``
  * ``sitenews``        <- ``site/sitenews.md``    -> a ``Sitenews`` (its body is
    split into dated ``NewsItem``s on the ``## YYYY-MM-DD HH:MM:SS Title`` heads)

Editorial content needs richer *block* markup than the legal-prose parser
(``lib.markdown`` is headings + paragraphs only), so the block layer (bullet
lists, fenced code) is parsed here. Everything generic is reused from
``lib.markdown``: ``frontmatter``, the ``RE_MDLINK``/``RE_HEADING`` grammar, and
``target_uri`` (extended there with the ``sfs:`` scheme) -- no forked copies.
"""

import dataclasses
import re
from pathlib import Path

from ..lib import markdown
from .model import (
    AboutPage,
    Bullets,
    Code,
    Frontpage,
    Heading,
    NewsItem,
    Paragraph,
    Sitenews,
)

RE_BOLD = re.compile(r"\*\*(.+?)\*\*")
RE_CODE = re.compile(r"`([^`]+)`")
# a sitenews section head: `## 2020-09-17 23:00:00 Title`
RE_NEWS_HEAD = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+(.*)$")


# --------------------------------------------------------------------------
# inline runs
# --------------------------------------------------------------------------

def _site_target(target):
    """A site link target -> its run uri, or None if unrecognised (left literal).
    Reuses the shared grammar (``begrepp:``/``sfs:``/external) and adds
    site-relative ``/…`` and ``#…`` targets (about-page cross-links)."""
    uri = markdown.target_uri(target)
    if uri is not None:
        return uri
    if target.startswith(("/", "#")):
        return target
    return None


def _spans(text, bold):
    """Plain text with ``\\`code\\`` spans -> runs, all tagged with `bold`."""
    runs, pos = [], 0
    for m in RE_CODE.finditer(text):
        if m.start() > pos:
            runs.append(_run(text[pos:m.start()], bold, False))
        runs.append(_run(m.group(1), bold, True))
        pos = m.end()
    if pos < len(text):
        runs.append(_run(text[pos:], bold, False))
    return runs


def _run(text, bold, code, uri=None):
    if not (bold or code or uri):
        return text
    run = {"text": text}
    if uri:
        run["uri"] = uri
    if bold:
        run["bold"] = True
    if code:
        run["code"] = True
    return run


def _links(text, bold):
    """A run of text (already inside/outside a bold span) -> runs, resolving
    ``[label](target)`` links and leaving unresolved ones as literal text."""
    runs, pos = [], 0
    for m in markdown.RE_MDLINK.finditer(text):
        if m.start() > pos:
            runs += _spans(text[pos:m.start()], bold)
        uri = _site_target(m.group(2).strip())
        if uri:
            runs.append(_run(m.group(1), bold, False, uri))
        else:
            runs += _spans(m.group(0), bold)
        pos = m.end()
    if pos < len(text):
        runs += _spans(text[pos:], bold)
    return runs


def inline(text):
    """One line/paragraph of markdown -> inline runs. Handles ``**bold**`` (which
    may wrap a link), ``[label](target)`` links, and ``\\`code\\`` spans."""
    runs, pos = [], 0
    for m in RE_BOLD.finditer(text):
        if m.start() > pos:
            runs += _links(text[pos:m.start()], False)
        runs += _links(m.group(1), True)
        pos = m.end()
    if pos < len(text):
        runs += _links(text[pos:], False)
    return runs


# --------------------------------------------------------------------------
# blocks
# --------------------------------------------------------------------------

def blocks(body):
    """A markdown body -> a list of block dataclasses (`Heading`/`Paragraph`/
    `Bullets`/`Code`). Paragraphs are blank-line separated (lines joined with a
    space); ``- ``/``* `` runs become one `Bullets`; ```` ``` ```` fences a
    `Code` block."""
    out, para, lines, i = [], [], body.splitlines(), 0

    def flush():
        if para:
            out.append(Paragraph(inline(" ".join(para))))
            para.clear()

    while i < len(lines):
        s = lines[i].strip()
        heading = markdown.RE_HEADING.match(s)
        if not s:
            flush()
            i += 1
        elif s.startswith("```"):
            flush()
            i += 1
            code = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1                                # closing fence
            out.append(Code("\n".join(code)))
        elif heading:
            flush()
            out.append(Heading(heading.group(2).strip(), len(heading.group(1))))
            i += 1
        elif s[:2] in ("- ", "* "):
            flush()
            items = []
            while i < len(lines) and lines[i].strip()[:2] in ("- ", "* "):
                items.append(inline(lines[i].strip()[2:].strip()))
                i += 1
            out.append(Bullets(items))
        else:
            para.append(s)
            i += 1
    flush()
    return out


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
    return Frontpage(title=meta["title"], blocks=blocks(body))


def about_artifact(slug, path):
    meta, body = _read(path)
    return AboutPage(slug=slug, title=meta["title"], blocks=blocks(body))


def sitenews_artifact(path):
    meta, body = _read(path)
    items, head, buf = [], None, []

    def flush():
        if head:
            items.append(NewsItem(id=_news_id(head[0]), published=head[0],
                                  title=head[1], blocks=blocks("\n".join(buf))))

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
