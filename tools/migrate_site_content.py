#!/usr/bin/env python3
"""One-time migration of lagen.nu's editorial chrome into a markdown-first
``site/`` tree inside the ``lagen-wiki`` content repo (alongside ``concept/`` and
``commentary/``). Three legacy sources, three markdown files:

  * the curated frontpage law list -- MediaWiki page ``Lagen.nu:Huvudsida``
    (ns 4) in the sqlite dump, the wikitext after the ``= Index =`` marker
        -> ``site/frontpage.md``   (``## <Category>`` + ``- [Label](sfs:…)`` bullets;
           ``**bold**`` marks a commented law, carried over from ``'''…'''``)
  * the ``/om/*`` about pages -- ``lagen/nu/res/static/*.rst`` (docutils RST)
        -> ``site/om/<slug>.md``   (one per file; H1 -> ``title:`` frontmatter)
  * the site news -- ``lagen/nu/res/static/sitenews.txt`` (``DATETIME Title`` +
    HTML paragraphs)
        -> ``site/sitenews.md``    (repeated ``## <datetime> <Title>`` sections)

Read-only over the legacy trees (like ``tools/mediawiki_to_markdown.py`` it never
runs or extends legacy code). The generated markdown is the source of truth
thereafter -- hand-edit it, don't re-run this. Converters are lossy by design, so
the script asserts measured invariants (every about page produced a non-empty
file; the frontpage law-link count equals the source bullet count; the news-item
count equals the header-line count) and fails fast on unknown sitenews markup
rather than dropping content silently.

Usage:
  tools/migrate_site_content.py                       # defaults from config
  tools/migrate_site_content.py --wiki-root ../lagen-wiki --db … --static …
"""

import argparse
import re
import sqlite3
import sys
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag

# a standalone tool: add the repo root so `accommodanda` imports resolve when run
# directly, before importing from it (this file lives in tools/, excluded from ruff)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from accommodanda import config

REPO = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO / "mediawiki-db" / "db" / "lagen.sqlite"
DEFAULT_STATIC = REPO / "lagen" / "nu" / "res" / "static"


# --------------------------------------------------------------------------
# shared inline conversion
# --------------------------------------------------------------------------

RE_SFS_LINK = re.compile(r"\[\[SFS/([^\]|]+?)(?:\|([^\]]+))?\]\]")
RE_KAT_LINK = re.compile(r"\[\[:?Kategori:([^\]|]+?)(?:\|([^\]]+))?\]\]")
RE_WIKI_LINK = re.compile(r"\[\[([^\]|:]+?)(?:\|([^\]]+))?\]\]")
RE_EXT_LINK = re.compile(r"\[((?:https?:)?//[^\s\]]+)(?:\s+([^\]]+))?\]")
RE_BOLDITALIC = re.compile(r"'''''(.+?)'''''")
RE_BOLD = re.compile(r"'''(.+?)'''")
RE_ITALIC = re.compile(r"''(.+?)''")
RE_TEMPLATE = re.compile(r"\{\{[^}]*\}\}")
RE_BLANKS = re.compile(r"\n{3,}")


def wiki_inline(text):
    """One run of MediaWiki inline markup -> markdown, for the frontpage. SFS
    wikilinks become ``sfs:`` links, category links collapse to their plain
    label (there are no category pages in the new site), residual concept links
    become ``begrepp:`` links, single-bracket ``[url label]`` externals become
    markdown links, and ``'''…'''``/``''…''`` emphasis becomes ``**…**``/``*…*``."""
    text = RE_TEMPLATE.sub("", text)
    text = RE_SFS_LINK.sub(
        lambda m: "[%s](sfs:%s)" % ((m.group(2) or m.group(1)).strip(), m.group(1).strip()),
        text)
    text = RE_KAT_LINK.sub(lambda m: (m.group(2) or m.group(1)).strip(), text)
    text = RE_WIKI_LINK.sub(
        lambda m: "[%s](begrepp:%s)" % ((m.group(2) or m.group(1)).strip(), m.group(1).strip()),
        text)
    text = RE_EXT_LINK.sub(
        lambda m: "[%s](%s)" % ((m.group(2) or m.group(1)).strip(), m.group(1).strip())
        if m.group(2) else m.group(1).strip(),
        text)
    text = RE_BOLDITALIC.sub(r"**\1**", text)
    text = RE_BOLD.sub(r"**\1**", text)
    text = RE_ITALIC.sub(r"*\1*", text)
    return text.strip()


# --------------------------------------------------------------------------
# 1. frontpage: Lagen.nu:Huvudsida (ns 4), the wikitext after `= Index =`
# --------------------------------------------------------------------------

RE_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
RE_WIKI_HEADING = re.compile(r"^=+\s*(.*?)\s*=+\s*$")


def huvudsida_wikitext(db):
    con = sqlite3.connect(db)
    row = con.execute(
        "SELECT t.old_text FROM page p "
        "JOIN slots s ON s.slot_revision_id = p.page_latest "
        "JOIN content c ON c.content_id = s.slot_content_id "
        "JOIN text t ON ('tt:' || t.old_id) = c.content_address "
        "WHERE p.page_namespace = 4 AND p.page_title = 'Huvudsida'").fetchone()
    con.close()
    assert row, "Lagen.nu:Huvudsida (ns 4) not found in %s" % db
    text = row[0]
    assert "= Index =" in text, "Huvudsida has no `= Index =` marker"
    return text.split("= Index =", 1)[1]


def build_frontpage(db):
    wt = RE_COMMENT.sub("", huvudsida_wikitext(db))
    source_bullets = len(re.findall(r"^\s*\*\s.*\[\[SFS/", wt, re.MULTILINE))
    out = []
    for raw in wt.splitlines():
        line = raw.strip()
        if not line or line in ("{|", "|}", "|") or line.startswith("|-"):
            continue                              # wikitable scaffolding
        h = RE_WIKI_HEADING.match(line)
        if h:
            out.append("")
            out.append("## " + wiki_inline(h.group(1)))
            out.append("")
        elif line.startswith("*"):
            out.append("- " + wiki_inline(line[1:].strip()))
        else:
            out.append("")
            out.append(wiki_inline(line))
            out.append("")
    body = RE_BLANKS.sub("\n\n", "\n".join(out)).strip()
    md_bullets = len(re.findall(r"^- .*\]\(sfs:", body, re.MULTILINE))
    assert md_bullets == source_bullets, (
        "frontpage law-link count %d != source bullet count %d"
        % (md_bullets, source_bullets))
    return "---\ntitle: lagen.nu\n---\n\n%s\n" % body, md_bullets


# --------------------------------------------------------------------------
# 2. about pages: lagen/nu/res/static/*.rst -> site/om/<slug>.md
# --------------------------------------------------------------------------

RE_RST_REF = re.compile(r"`([^`]+?)\s*<([^>]+)>`_")           # `text <target>`_
RE_RST_LITERAL = re.compile(r"``([^`]+)``")                   # ``code``
RE_RST_DOCINFO = re.compile(r"^:[\w-]+:(?:\s.*)?$")
RE_UNDERLINE = re.compile(r"^([=\-~^\"'`+*#])\1{2,}\s*$")


def rst_target(target):
    """An RST link target -> a markdown href. A scheme or a leading ``/``/``#`` is
    kept verbatim (external URL / site-absolute lagen.nu path / fragment); a bare
    word (``english``, ``innehall``) is another about page, ``/om/<word>``."""
    target = target.strip()
    if "://" in target or target.startswith(("mailto:", "/", "#")):
        return target
    return "/om/" + target


def rst_inline(text):
    text = RE_RST_REF.sub(lambda m: "[%s](%s)" % (m.group(1).strip(), rst_target(m.group(2))), text)
    text = RE_RST_LITERAL.sub(r"`\1`", text)
    return text


def rst_to_md(text):
    """A docutils RST about page -> ``(title, markdown_body)``. Handles the
    subset the lagen.nu pages actually use: an underlined H1 title + section
    underlines, ``:field:`` docinfo (dropped -- footer is deferred), ``.. ``
    comments (dropped), ``::`` literal blocks (fenced code), ``*``/``-`` bullet
    lists, embedded ``\\`text <target>\\`_`` links, and blank-line paragraphs."""
    lines = [ln.rstrip() for ln in text.expandtabs(2).splitlines()]
    title = None
    out, para = [], []

    def flush():
        if para:
            out.append(rst_inline(" ".join(para)))
            out.append("")
            para.clear()

    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        nxt = lines[i + 1] if i + 1 < n else ""
        if not line.strip():
            flush()
            i += 1
            continue
        if RE_RST_DOCINFO.match(line.strip()):        # docinfo field, dropped
            i += 1
            continue
        if line.startswith(".. "):                    # RST comment/directive block
            i += 1
            while i < n and (not lines[i].strip() or lines[i].startswith(" ")):
                i += 1
            continue
        if RE_UNDERLINE.match(nxt) and len(nxt.strip()) >= len(line.strip()):
            flush()
            if title is None:
                title = line.strip()
            else:
                out.append("## " + rst_inline(line.strip()))
                out.append("")
            i += 2
            continue
        stripped = line.strip()
        if stripped[:2] in ("* ", "- "):              # bullet item
            flush()
            out.append("- " + rst_inline(stripped[2:].strip()))
            i += 1
            continue
        if line.rstrip().endswith("::"):              # literal block intro
            para.append(line.rstrip()[:-2].rstrip() + ":")
            flush()
            i += 1
            while i < n and not lines[i].strip():
                i += 1
            code = []
            while i < n and (lines[i].startswith(" ") or not lines[i].strip()):
                code.append(lines[i][2:] if lines[i].startswith("  ") else lines[i])
                i += 1
            while code and not code[-1].strip():
                code.pop()
            out.append("```")
            out.extend(code)
            out.append("```")
            out.append("")
            continue
        para.append(stripped)
        i += 1
    flush()
    assert title, "no underlined H1 title found"
    body = RE_BLANKS.sub("\n\n", "\n".join(out)).strip()
    return title, body


def build_about_pages(static):
    pages = {}
    for rst in sorted(static.glob("*.rst")):
        title, body = rst_to_md(rst.read_text(encoding="utf-8"))
        pages[rst.stem] = "---\ntitle: %s\n---\n\n%s\n" % (title, body)
    return pages


# --------------------------------------------------------------------------
# 3. sitenews: lagen/nu/res/static/sitenews.txt -> site/sitenews.md
# --------------------------------------------------------------------------

RE_NEWS_HEAD = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) (.*)$")
# the tag vocabulary two decades of hand-written sitenews HTML actually uses;
# anything outside this set aborts the run rather than being dropped silently
NEWS_TAGS = {"p", "a", "ul", "ol", "li", "pre", "em", "i", "strong", "b", "br"}


def _news_href(href):
    """A sitenews link target -> an absolute href. A site-relative ``/1986:223``
    (or ``/prop/…``) is re-homed onto the live host; external URLs and mailto
    stay verbatim."""
    if href.startswith("/"):
        return "https://lagen.nu" + href
    return href


def _news_inline(node):
    """Inline children of a block element -> markdown text."""
    parts = []
    for child in node.children:
        if isinstance(child, NavigableString):
            parts.append(re.sub(r"\s+", " ", str(child)))
        elif isinstance(child, Tag):
            inner = _news_inline(child).strip()
            if child.name == "a":
                parts.append("[%s](%s)" % (inner, _news_href(child.get("href", ""))))
            elif child.name in ("strong", "b"):
                parts.append("**%s**" % inner)
            elif child.name in ("em", "i"):
                parts.append("*%s*" % inner)
            elif child.name == "br":
                parts.append(" ")
            else:
                parts.append(inner)
    return "".join(parts)


def news_html_to_md(html):
    soup = BeautifulSoup(html, "lxml")
    seen = {t.name for t in soup.find_all(True)} - {"html", "body"}
    unknown = seen - NEWS_TAGS
    assert not unknown, "sitenews: unhandled HTML tag(s) %s -- extend NEWS_TAGS " \
        "or convert by hand (refusing to drop content silently)" % sorted(unknown)
    out = []
    body = soup.body or soup
    for el in body.find_all(["p", "ul", "ol", "pre"], recursive=False):
        if el.name == "p":
            out.append(_news_inline(el).strip())
        elif el.name in ("ul", "ol"):
            for li in el.find_all("li", recursive=False):
                out.append("- " + _news_inline(li).strip())
        elif el.name == "pre":
            out.append("```")
            out.append(el.get_text().strip("\n"))
            out.append("```")
        out.append("")
    return RE_BLANKS.sub("\n\n", "\n".join(out)).strip()


def build_sitenews(static):
    text = (static / "sitenews.txt").read_text(encoding="utf-8")
    header_count = sum(1 for ln in text.splitlines() if RE_NEWS_HEAD.match(ln))
    items, cur_head, cur_body = [], None, []
    for line in text.splitlines():
        m = RE_NEWS_HEAD.match(line)
        if m:
            if cur_head:
                items.append((cur_head, "\n".join(cur_body)))
            cur_head, cur_body = (m.group(1), m.group(2)), []
        elif cur_head:
            cur_body.append(line)
    if cur_head:
        items.append((cur_head, "\n".join(cur_body)))
    assert len(items) == header_count, (
        "sitenews item count %d != header-line count %d" % (len(items), header_count))
    out = ["---", "title: Nyheter om lagen.nu", "---", ""]
    for (dt, title), body in items:
        out.append("## %s %s" % (dt, title))
        out.append("")
        out.append(news_html_to_md(body))
        out.append("")
    return "\n".join(out).rstrip() + "\n", len(items)


# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--wiki-root", type=Path, default=config.WIKI_ROOT,
                    help="lagen-wiki checkout (site/ is written under it)")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB, help="MediaWiki sqlite dump")
    ap.add_argument("--static", type=Path, default=DEFAULT_STATIC,
                    help="legacy lagen/nu/res/static dir")
    args = ap.parse_args()

    site = args.wiki_root / "site"
    (site / "om").mkdir(parents=True, exist_ok=True)

    frontpage, n_laws = build_frontpage(args.db)
    (site / "frontpage.md").write_text(frontpage, encoding="utf-8")
    print("frontpage.md: %d law links" % n_laws)

    pages = build_about_pages(args.static)
    for slug, md in pages.items():
        assert len(md.strip().splitlines()) > 3, "about page %s came out empty" % slug
        (site / "om" / (slug + ".md")).write_text(md, encoding="utf-8")
    n_rst = len(list(args.static.glob("*.rst")))
    assert len(pages) == n_rst, "wrote %d about pages, expected %d" % (len(pages), n_rst)
    print("om/*.md: %d about pages" % len(pages))

    sitenews, n_news = build_sitenews(args.static)
    (site / "sitenews.md").write_text(sitenews, encoding="utf-8")
    print("sitenews.md: %d news items" % n_news)

    print("wrote site/ under %s" % args.wiki_root)


if __name__ == "__main__":
    main()
