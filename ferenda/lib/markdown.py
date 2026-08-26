"""Parse the git-backed markdown the hand-authored lagen.nu commentary + concept
pages are now stored as (replacing the MediaWiki dump; see
``tools/migrations/mediawiki_to_markdown.py``) into the same inline-run shape every other
source uses.

This is the markdown counterpart of :mod:`ferenda.lib.wikitext`: it offers
the same entry points (``frontmatter`` / ``blocks`` / ``to_runs`` /
``begrepp_uri``) over the same node shapes. The outputs are no longer identical
block for block -- markdown ``blocks`` reads a list as a ``lista`` node where
wikitext still reads the same line as a ``stycke`` -- so ``wiki/parse.py``
handles both. The value-add still links three ways, now in markdown:

  * ``[label](begrepp:Concept)``           -> a ``begrepp/<Concept>`` link;
  * natural-language law/case citations in the prose ("2 kap 2 §
    tryckfrihetsförordningen", "NJA 1990 s. 510") -> run through the **same
    citation engine** statutes and cases use;
  * ``[label](https://…)``                  -> an external link run.

Frontmatter (``categories`` / ``author`` / ``annotates`` / ``aliases`` /
``title``) carries the metadata wikitext used to scrape from the body. Like
wikitext, this keeps only prose + links + headings + the frontmatter metadata --
no formatting -- because that is all the pipeline downstream consumes.

Block structure comes from markdown-it (``_MD`` below), the same engine the
editorial pages read. The inline layer stays hand-rolled: a run is prose, a
link, or a citation the lagrum engine found, and that grammar is narrower than
CommonMark's inline set on purpose.
"""

import re

from markdown_it import MarkdownIt

from .lagrum import CELEX_BASE, Ref, interleave

# CommonMark, `html: False` so a literal `<b>` stays text. The legal-prose
# pages used to have a line scanner of their own, which is how they came to
# print list markers and HTML comments as prose. The editorial pages
# (``site/parse.py``) build the same engine but `.enable("table")`, because
# their model has a table node and this one does not. With tables off,
# markdown-it emits no table token at all, so a pipe table here reads as one
# run-on paragraph with its pipes showing -- the walk below never sees it and
# cannot reject it. No commentary page has one; the two that do (`site/om/api`,
# `site/om/mcp`) are read by the editorial engine.
_MD = MarkdownIt("commonmark", {"html": False})
# tokens the block walk consumes through its own indices, or that carry no block
# of their own; anything else reaching the walk is markdown this contract does
# not model, and says so rather than dropping the prose
_PASSTHROUGH = frozenset({"inline", "heading_close", "paragraph_close",
                          "list_item_open", "list_item_close"})

BEGREPP = "https://lagen.nu/begrepp/"

# `[label](target)`. The label excludes both brackets so a stray `[` before a
# link (from malformed `[[url label]]` wiki source) stays literal text rather
# than being swallowed into the label.
RE_MDLINK = re.compile(r"\[([^][]+)\]\(([^)]+)\)")
RE_HEADING = re.compile(r"^(#+)\s+(.*?)\s*#*\s*$")
# An HTML comment, which is how the authors park prose they have taken out of
# service ("lagstiftningen för 5 § har uppdaterats sedan 2009 när detta skrevs
# så vi tar bort denna text tills vidare", 1982:80). Non-greedy, and matched
# over the whole body rather than line by line: the comments run across
# paragraphs, and one of them (1942:740) contains a bare `--`, so the first
# `-->` is the only reliable end.
RE_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
# a recognised external link target -- everything else in (...) is left literal
RE_URL = re.compile(r"^(?:https?:)?//", re.IGNORECASE)


def strip_comments(text):
    """`text` with its HTML comments and everything they enclose removed.

    Both markdown parsers in the codebase need this and neither gets it for
    free, so the rule lives here once -- the same reason `target_uri` does. The
    legal-prose parser below handles no HTML at all; the editorial parser
    (``site/parse.py``) runs markdown-it with ``html: False``, which is not
    "drop the HTML" but "treat it as text", so a comment reaches the page as
    `&lt;!-- …` there too. That is how a note the authors wrote to each other in
    2009, and the superseded passage under it, came to be printed as commentary
    on LAS, rättegångsbalken and avtalslagen."""
    return RE_HTML_COMMENT.sub("", text)


# a free-standing spaced `--`, the content repo's authoring convention for a
# dash. Whitespace (or the text edge) on both sides is required, so `--force`
# flags, `---` rules and in-word hyphens stay as written.
RE_SPACED_DASHES = re.compile(r"(?<!\S)--(?!\S)")


def dashes(text):
    """`text` with the authored ` -- ` convention rendered as the en dash the
    reader should see. Both markdown parsers apply this to prose text runs only
    (never code spans), so it lives here beside `strip_comments`."""
    return RE_SPACED_DASHES.sub("–", text)


def begrepp_uri(name):
    """A concept name -> its begrepp URI. MediaWiki upper-cases the first letter
    of a page title, so `[allmän handling](begrepp:allmän handling)` and the page
    "Allmän handling" resolve to the same URI. The converter and this parser must
    agree on this exact rule (PRD §3.5: identifiers must not move)."""
    name = name.strip()
    if name:
        name = name[0].upper() + name[1:]
    return BEGREPP + name.replace(" ", "_")


# --------------------------------------------------------------------------
# frontmatter -- a minimal YAML subset: `key: scalar`, `key: [a, b]` (inline
# list), the scalar block form (`key:` then `  - item` lines), and a block list
# of mappings (`key:` then `  - field: value` items, continued by deeper
# `    field: value` lines). The mapping list carries the Step-4 `guidance:`
# sources ({title, url, pdf}); everything else stays one level deep, no anchors.
# --------------------------------------------------------------------------

RE_FM_FENCE = re.compile(r"^---\s*$")
# a mapping field `field: value` -- a bareword key then colon-*space* (or bare
# `field:`), so a scalar list item that is itself a URL (`- https://…`, whose
# `https:` has no following space) is not mistaken for a mapping.
RE_FM_FIELD = re.compile(r"^([\w-]+):(?:\s+(.*))?$")
# a backslash-escaped character inside a double-quoted scalar (`\"` or `\\`)
RE_ESCAPE = re.compile(r"\\(.)")


def _scalar(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        inner = value[1:-1]
        if value[0] == '"':
            # undo guidance_discover.yaml_scalar's `\\` / `\"` escaping (the only
            # producer of backslash escapes in this subset) -- a backslash always
            # starts a two-character escape unit, so a left-to-right \X -> X pass
            # reverses it exactly.
            inner = RE_ESCAPE.sub(r"\1", inner)
        return inner
    return value


def _inline_list(value):
    return [_scalar(item) for item in value[1:-1].split(",") if item.strip()]


# An `author:` byline, as it survives in the hand-authored corpus: three legacy
# MediaWiki spellings the wikitext->markdown migration left verbatim in the
# frontmatter, plus the modern markdown link. 21 of the 28 commentary files
# carry one of the legacy forms, so the value reached the rail as raw markup
# ("[http://wiki.lagen.nu/index.php/Användare:AlexanderAlk Alexander
# Ankerstedt]", D2). Every form reduces to the label: the targets are
# wiki.lagen.nu user pages on the retired MediaWiki, so a link would point at a
# host this corpus is replacing.
RE_BYLINE_LINK = re.compile(
    r"\[\[(?:[^\]|]*\|)?(?P<wl>[^\]|]+)\]\]"            # [[Page|label]] / [[label]]
    r"|\[(?P<md>[^\]]*)\]\((?:[^)]*)\)"                 # [label](url)
    r"|\[(?:https?:)?//[^\s\]]+\s+(?P<ext>[^\]]*)\]"    # [url label]
    r"|\[(?P<bare>(?:https?:)?//[^\s\]]+)\]")           # [url]


def byline(value):
    """An `author:` frontmatter value -> the display name(s), with any link
    markup reduced to its label. Returns None for a missing/blank byline so a
    caller can omit the line entirely. Joins like "X och Y" are preserved --
    only the bracketed spans are rewritten."""
    if not value:
        return None
    text = RE_BYLINE_LINK.sub(
        lambda m: (m.group("wl") or m.group("md") or m.group("ext")
                   or m.group("bare") or "").strip(), value)
    return " ".join(text.split()) or None


def frontmatter(text, path=None):
    """`(meta, body)` from a markdown file. `meta` is the parsed YAML-subset
    frontmatter (``{}`` if the file has none); `body` is everything after the
    closing fence. `path` (if given) names the source file in the error raised
    for a malformed (unterminated) frontmatter fence."""
    lines = text.splitlines()
    if not (lines and RE_FM_FENCE.match(lines[0])):
        return {}, text
    end = next((i for i in range(1, len(lines)) if RE_FM_FENCE.match(lines[i])),
               None)
    if end is None:
        raise ValueError(
            "%s: frontmatter opened with `---` but never closed with a "
            "matching `---` fence" % (path or "<string>"))
    meta, key, mapping = {}, None, None
    for line in lines[1:end]:
        if not line.strip():
            continue
        stripped = line.lstrip()
        if line[:1] in (" ", "\t") and stripped.startswith("- "):
            if key is None:
                raise ValueError(
                    "%s: frontmatter list item %r appears before any key"
                    % (path or "<string>", line))
            item = stripped[2:].strip()
            m = RE_FM_FIELD.match(item)
            if m:                                # `- field: value` -> a new mapping
                mapping = {m.group(1): _scalar(m.group(2) or "")}
                meta.setdefault(key, []).append(mapping)
            else:                                # `- value` -> a scalar item
                meta.setdefault(key, []).append(_scalar(item))
                mapping = None
            continue
        if line[:1] in (" ", "\t") and mapping is not None:   # mapping continuation
            m = RE_FM_FIELD.match(stripped)
            if not m:
                raise ValueError(
                    "%s: bad frontmatter mapping field: %r"
                    % (path or "<string>", line))
            mapping[m.group(1)] = _scalar(m.group(2) or "")
            continue
        key, _, value = line.partition(":")
        key, value, mapping = key.strip(), value.strip(), None
        if not value:
            meta.setdefault(key, [])              # opens a block list
        elif value[0] == "[" and value[-1] == "]":
            meta[key] = _inline_list(value)
        else:
            meta[key] = _scalar(value)
    body = "\n".join(lines[end + 1:])
    return meta, body


# --------------------------------------------------------------------------
# blocks + inline runs
# --------------------------------------------------------------------------

def _text(content):
    """An inline token's raw markdown, one line: soft-wrapped lines joined with a
    single space, as wikitext.blocks did.

    `\\#` is resolved to the literal `#` it escapes -- the only backslash escape
    the corpus uses, left by the converter on any paragraph that began with a
    hash. The rest are left as written, because the raw text goes on to the link
    grammar in `to_runs` and unescaping a `\\[` there would mint a link the
    author escaped precisely to avoid."""
    return dashes(" ".join(line.strip().replace("\\#", "#")
                           for line in content.split("\n") if line.strip()))


def blocks(body, where=None):
    """markdown body -> a flat list of *raw* blocks (link-parsing is left to the
    caller, which sets the citation context):
        ("rubrik", level, heading_text)
        ("stycke", raw_paragraph_text)
        ("lista", ordered, [raw_item_text, …])
        ("avskiljare",)

    The inline text is the *source* markdown, not rendered HTML, so the link
    grammar in :func:`to_runs` still owns what a link means.

    This used to be a line scanner, which is why the commentary pages printed
    their list markers as prose (`* Fritt utnyttjande … * Begränsat …`) and their
    HTML comments as body text. It now runs the same markdown-it configuration
    the editorial pages do, so a construct means one thing across the content
    repo. A block markdown has but this contract does not -- a fenced code
    block, a blockquote -- is a `ValueError` naming `where`, never silently
    dropped prose (rule:errors-drive-retry-use-raise), the same bargain
    `site.parse.blocks` strikes. A table is the exception, because the engine
    is built without the table rule: it never becomes a token, so it arrives
    here as one paragraph of pipes. See the `_MD` comment.

    A list nested inside a list item is flattened into the enclosing list: the
    corpus has none, and one flat level is all the reader-facing model carries.
    HTML comments are dropped, content and all (:func:`strip_comments`);
    `iter_headings` deliberately does not, because the inline editor addresses
    regions by heading index into the unmodified file."""
    out, lists = [], []
    tokens = _MD.parse(strip_comments(body))
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t.type == "heading_open":
            out.append(("rubrik", int(t.tag[1:]), _text(tokens[i + 1].content)))
            i += 3
            continue
        if t.type == "paragraph_open":
            text = _text(tokens[i + 1].content)
            if lists:
                lists[-1][2].append(text)
            else:
                out.append(("stycke", text))
            i += 3
            continue
        if t.type == "hr":
            out.append(("avskiljare",))
        elif t.type in ("bullet_list_open", "ordered_list_open"):
            if lists:                       # nested: keep filling the outer list
                lists.append(lists[-1])
            else:
                lists.append(["lista", t.type == "ordered_list_open", []])
                out.append(lists[-1])
        elif t.type in ("bullet_list_close", "ordered_list_close"):
            lists.pop()
        elif t.type not in _PASSTHROUGH:
            raise ValueError("%s: block markdown %r has no block form"
                             % (where or "<markdown>", t.type))
        i += 1
    return [tuple(b) if isinstance(b, list) else b for b in out]


def split_frontmatter(text, path=None):
    """`(fm_text, body_text)` splitting a markdown file into its frontmatter block
    (fences included, `''` if none) and the verbatim body. Unlike `frontmatter`,
    which reparses and rejoins, both halves come back as the original text so a
    single-region rewrite (the inline editor) can preserve everything it does not
    touch byte-for-byte. `path` (if given) names the source file in the error
    raised for a malformed (unterminated) frontmatter fence."""
    lines = text.splitlines(keepends=True)
    if not (lines and RE_FM_FENCE.match(lines[0].rstrip("\n"))):
        return "", text
    end = next((i for i in range(1, len(lines))
                if RE_FM_FENCE.match(lines[i].rstrip("\n"))), None)
    if end is None:
        raise ValueError(
            "%s: frontmatter opened with `---` but never closed with a "
            "matching `---` fence" % (path or "<string>"))
    return "".join(lines[:end + 1]), "".join(lines[end + 1:])


def iter_headings(body):
    """Yield `(line_index, level, text)` for each ATX heading line in `body`
    (indices into `body.splitlines()`), skipping any `#` inside a ```` ``` ````
    fenced code block. The line-addressable form of `blocks`' heading events, so a
    caller can locate and rewrite one section in place."""
    fence = False
    for i, raw in enumerate(body.splitlines()):
        s = raw.strip()
        if s.startswith("```"):
            fence = not fence
            continue
        if fence:
            continue
        h = RE_HEADING.match(s)
        if h:
            yield i, len(h.group(1)), h.group(2).strip()


def target_uri(target):
    """A markdown link target -> the run uri, or None if it is not a recognised
    link (then the `[label](target)` is left as literal prose). Strict grammar:
    `begrepp:Concept` concept links, `sfs:1949:381` statute links,
    `eurlex:32016R0679` EU-act links, and `http(s)://` / `//` external links
    only; bare `[x](y)` in legal prose stays text (the citation engine owns
    refs). The `source:identifier` schemes are deliberately symmetric -- a source
    is named by its key, never by its on-disk/URL shape."""
    if target.startswith("begrepp:"):
        # `)` in a concept name is %29-escaped to survive the link grammar
        return begrepp_uri(target[len("begrepp:"):].replace("%29", ")"))
    if target.startswith("sfs:"):
        # a statute by SFS number -> its top-level lagen.nu document uri (render's
        # `href` maps it back to the bare /<sfsid> URL). A general link-target
        # rule, not site-specific: any source can now write `[FB](sfs:1949:381)`.
        return "https://lagen.nu/" + target[len("sfs:"):]
    if target.startswith("eurlex:"):
        # an EU act by CELEX -> its ext/celex document uri (render's `href` maps
        # it to the public /celex/<CELEX> URL, the same page the eurlex source
        # builds). Symmetric with `sfs:` -- the content names the source, not the
        # URL path, so `[GDPR](eurlex:32016R0679)` mirrors `[FB](sfs:1949:381)`.
        return CELEX_BASE + target[len("eurlex:"):]
    if RE_URL.match(target):
        # `)` in an external url is %29-escaped the same way (the link grammar's
        # `)` terminator can't appear literally in the target)
        return target.replace("%29", ")")
    return None


# --------------------------------------------------------------------------
# curated external links (`## Externa länkar`): a bullet list of external
# resources shown in the annotated act's rail -- at the document level (PRD
# Step 2) or under the section heading it follows, per article (PRD Step 3).
# Authored in the same annotation markdown the kommentar sections live in.
# --------------------------------------------------------------------------

GUIDANCE_HEADING = "Externa länkar"


def _guidance_item(line):
    """A `- [label](href) — note` bullet -> `{label, href, note?}`, or None when
    the line is not a guidance bullet. A recognised link target is required
    (external url or lagen.nu-absolute); the trailing note after the em-dash is
    optional provenance ("— Europeiska kommissionen", "— utkast")."""
    line = line.strip()
    if line[:1] not in "-*":
        return None
    rest = line[1:].strip()
    m = RE_MDLINK.match(rest)
    if not m:
        return None
    href = target_uri(m.group(2).strip())
    if href is None:
        return None
    note = rest[m.end():].strip().lstrip("—–-").strip()
    item = {"label": m.group(1), "href": href}
    if note:
        item["note"] = note
    return item


def guidance_sections(body):
    """Split every `## Externa länkar` bullet list out of a markdown body into
    `([(owner, [{label, href, note?}])], body_without_those_sections)`. Each tuple
    is one guidance block tagged with `owner` -- the text of the heading it sits
    under, i.e. the section its links attach to (PRD Step 3), or `None` when the
    block precedes any heading (the document-level block, PRD Step 2). The sections
    are removed from the body so they are not also emitted as prose; a body with no
    such heading is returned unchanged (`([], body)`), keeping every existing
    annotation file lossless."""
    lines = body.splitlines()
    sections, kept, owner, i, n = [], [], None, 0, len(lines)
    while i < n:
        h = RE_HEADING.match(lines[i].strip())
        if h and h.group(2).strip().casefold() == GUIDANCE_HEADING.casefold():
            i += 1
            items = []
            while i < n and not RE_HEADING.match(lines[i].strip()):
                item = _guidance_item(lines[i])
                if item:
                    items.append(item)
                i += 1
            if items:
                sections.append((owner, items))
        else:
            if h:
                owner = h.group(2).strip()
            kept.append(lines[i])
            i += 1
    return (sections, "\n".join(kept)) if sections else ([], body)


def citation_runs(plain, links, refparser, parse_kw):
    """`plain` text plus its already-found markup `links` (`[Ref]`) -> the final
    interleaved inline runs, adding whatever the citation engine (`refparser`,
    when given) finds in `plain` that does not overlap a markup link. Shared
    tail of `to_runs` here and in `wikitext.to_runs` -- both build `plain`/
    `links` their own way (`[label](target)` vs. `[[wikilink]]`/`[url label]`)
    but merge them with the citation engine identically."""
    refs = list(links)
    if refparser is not None:
        for r in refparser.parse_text(plain, **parse_kw):
            if not any(w.start < r.end and r.start < w.end for w in links):
                refs.append(r)
    return interleave(plain, refs)


def to_runs(text, refparser=None, **parse_kw):
    """One paragraph of markdown -> inline runs: concept/external links from
    `[label](target)` plus law/case links from the citation engine,
    non-overlapping. `parse_kw` (e.g. `fragment=`/`context=`) is forwarded to the
    citation parser to set the base law for relative references. Mirrors
    wikitext.to_runs."""
    parts, links, length, last = [], [], 0, 0
    for m in RE_MDLINK.finditer(text):
        before = text[last:m.start()]
        parts.append(before)
        length += len(before)
        last = m.end()
        label, uri = m.group(1), target_uri(m.group(2).strip())
        if uri is None:                      # not a link -- keep the literal text
            parts.append(m.group(0))
            length += len(m.group(0))
            continue
        parts.append(label)
        links.append(Ref(length, length + len(label), label,
                         "dcterms:references", uri))
        length += len(label)
    parts.append(text[last:])
    plain = "".join(parts)
    return citation_runs(plain, links, refparser, parse_kw)
