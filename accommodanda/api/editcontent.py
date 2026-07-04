"""Locate, read and rewrite one editable markdown *region* in the lagen-wiki
content repo -- the content model behind the inline editor.

Three kinds of content are editable, each a different span of a `.md` file:

  * **kommentar** -- one `## …` section of `commentary/<host>/…md`, anchored to a
    host node (SFS `§`, EU article, recital). The region is the whole section
    (heading + prose + any `## Externa länkar` block under it). Commenting on a
    node with no commentary yet synthesises the section (`fragment_heading`) and,
    if the host has no commentary file at all, the file (with `annotates:`).
  * **begrepp** -- the whole body of `concept/<Name>.md` below its frontmatter.
  * **site** -- the whole body of an editorial `site/*.md` page below its
    frontmatter.

This is service-layer glue, not a `lib/` helper: it reads the wiki verticals'
own path indexes and heading grammar (`wiki.parse`, `site.parse`), which `lib/`
may not import. It never mutates the parsed artifacts -- only the markdown source
of truth; regeneration is a separate step (`build.rebuild_after_commit`).

Everything outside the touched region is preserved byte-for-byte
(`markdown.split_frontmatter` keeps the frontmatter and body verbatim), so an
edit made through the web UI is indistinguishable from one made in an editor +
`git commit`.
"""

import dataclasses
import hashlib
from pathlib import Path

from .. import config
from ..lib import layout, markdown
from ..site import parse as site_parse
from ..wiki import parse as wiki_parse

KINDS = ("kommentar", "begrepp", "site")


@dataclasses.dataclass(frozen=True)
class Region:
    """The address of one editable hunk. `ref` is the primary id of the file
    (kommentar: the host act's `annotates` basefile; begrepp: the concept title;
    site: the site basefile). For a kommentar, `anchor` is the host node id of the
    section (`P7`, an EU `5.2`, …) or `None` for the **document-level** commentary
    on the act as a whole (the prose before the first `## §`, shown in the "Om
    dokumentet" rail). begrepp/site are whole-body, so they carry no anchor."""
    kind: str
    ref: str
    anchor: str | None = None

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError("unknown edit kind %r" % self.kind)
        if self.kind != "kommentar" and self.anchor:
            raise ValueError("%s regions are whole-body (no anchor)" % self.kind)

    @property
    def key(self):
        return "%s:%s#%s" % (self.kind, self.ref, self.anchor or "")


def region_of(draft):
    """Rebuild a Region from a stored cart draft."""
    return Region(draft["kind"], draft["ref"], draft.get("anchor"))


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _wiki_path(*parts):
    return config.WIKI_ROOT.joinpath(*parts)


# --------------------------------------------------------------------------
# locate: region -> its markdown file (+ whether it exists yet)
# --------------------------------------------------------------------------

def _contained(path):
    """`path`, asserted to sit inside WIKI_ROOT. A crafted `ref` reaches the path
    rules (`layout.relpath`, `site.parse.record`) verbatim, and a `..`/absolute
    segment there could otherwise target a file outside the content repo -- so
    every located path is confined here, before any read or write. A path escape
    is malformed input, not a server fault: raises ValueError."""
    root = config.WIKI_ROOT.resolve()
    resolved = path.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError("edit target %r is outside the content repo" % str(path))
    return resolved


def locate(region):
    """`(path, exists)` for a region's markdown file, confined to WIKI_ROOT. Only
    kommentar can point at a not-yet-created file (the first comment on a host
    act); begrepp and site always edit a file the user is looking at."""
    if region.kind == "kommentar":
        index = wiki_parse.kommentar_index(str(config.WIKI_ROOT))
        if region.ref in index:
            return _contained(Path(index[region.ref])), True
        rel = layout.relpath("kommentar", region.ref).with_suffix(".md")
        return _contained(_wiki_path("commentary", rel)), False
    if region.kind == "begrepp":
        index = wiki_parse.begrepp_index(str(config.WIKI_ROOT))
        if region.ref not in index:
            raise ValueError("no concept %r in the content repo" % region.ref)
        return _contained(Path(index[region.ref])), True
    path = site_parse.record(str(config.WIKI_ROOT), region.ref)
    if not _contained(path).exists():
        raise ValueError("no site page %r in the content repo" % region.ref)
    return _contained(path), True


# --------------------------------------------------------------------------
# kommentar section spans (heading -> next anchoring heading)
# --------------------------------------------------------------------------

def _section_span(body, anchor):
    """The `[start, end)` line span (indices into `body.splitlines()`) of the
    kommentar section that annotates `anchor`, or `None` if the file has no such
    section yet. A section runs from its `## <anchorable>` heading up to the next
    anchoring heading -- so an intervening `## Externa länkar` or `### sub`
    heading stays inside it (matching `kommentar_artifact`'s grouping)."""
    anchored = [(i, wiki_parse.heading_fragment(txt))
                for i, _lvl, txt in markdown.iter_headings(body)]
    boundaries = [i for i, frag in anchored if frag is not None]
    for i, frag in anchored:
        if frag == anchor:
            later = [b for b in boundaries if b > i]
            return (i, later[0] if later else len(body.splitlines()))
    return None


def _first_section_line(body):
    """The line index of the first `## <anchorable>` section heading, i.e. where
    the document-level preamble ends. `len(lines)` when the file is all preamble
    (no anchored sections yet)."""
    for i, _lvl, txt in markdown.iter_headings(body):
        if wiki_parse.heading_fragment(txt) is not None:
            return i
    return len(body.splitlines())


def _section_template(anchor):
    """The seed markdown for a brand-new kommentar section: its `## <heading>`
    (the inverse of `heading_fragment`) and a blank line to type under. Raises
    ValueError for a node the host never anchors (a förarbete section, say)."""
    return "## %s\n\n" % wiki_parse.fragment_heading(anchor)


# --------------------------------------------------------------------------
# read / write a region
# --------------------------------------------------------------------------

def read(region):
    """The region's current markdown, as `{markdown, exists, base_sha}`.
    `base_sha` fingerprints the on-disk content the edit is based on, so a commit
    can detect a region that changed underneath a pending draft. A kommentar
    section that doesn't exist yet reads as its template with `exists=False` and
    an empty `base_sha` (the base is "no section")."""
    path, exists = locate(region)
    template = "" if region.anchor is None else _section_template(region.anchor)
    if region.kind == "kommentar" and not exists:
        return {"markdown": template, "exists": False, "base_sha": ""}
    _fm, body = markdown.split_frontmatter(path.read_text(encoding="utf-8"))
    if region.kind == "kommentar":
        lines = body.splitlines(keepends=True)
        if region.anchor is None:                  # document-level (the preamble)
            span = (0, _first_section_line(body))
        else:
            span = _section_span(body, region.anchor)
            if span is None:                       # node has no commentary yet
                return {"markdown": template, "exists": False, "base_sha": ""}
        text = "".join(lines[span[0]:span[1]]).rstrip("\n")
        text = text + "\n" if text else template
        return {"markdown": text, "exists": bool(text.strip()),
                "base_sha": _sha(text) if text.strip() else ""}
    # begrepp / site: the whole body below the frontmatter
    text = body.rstrip("\n") + "\n"
    return {"markdown": text, "exists": True, "base_sha": _sha(text)}


def write(region, new_text):
    """Apply `new_text` to the region's file in place and return
    `{kind, basefile, path, created}` for the rebuild/commit. Everything outside
    the region -- frontmatter, sibling sections, other pages -- is untouched. A
    kommentar edit must keep its section anchored: `new_text` must open with a
    `## …` heading whose `heading_fragment` is exactly the region's anchor
    (else the section would silently lose its host node)."""
    path, exists = locate(region)
    if region.kind == "kommentar":
        if region.anchor is not None:
            _check_section_anchor(region.anchor, new_text)
        if exists:
            fm, body = markdown.split_frontmatter(path.read_text(encoding="utf-8"))
        else:
            fm, body = _new_kommentar_frontmatter(region.ref), ""
        spliced = (_splice_preamble(body, new_text) if region.anchor is None
                   else _splice_section(body, region.anchor, new_text))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(fm + spliced, encoding="utf-8")
        return {"kind": "kommentar", "basefile": region.ref,
                "path": path, "created": not exists}
    fm, _body = markdown.split_frontmatter(path.read_text(encoding="utf-8"))
    path.write_text(fm + new_text.rstrip("\n") + "\n", encoding="utf-8")
    return {"kind": region.kind, "basefile": region.ref,
            "path": path, "created": False}


def _check_section_anchor(anchor, new_text):
    heads = list(markdown.iter_headings(new_text))
    if not heads or wiki_parse.heading_fragment(heads[0][2]) != anchor:
        raise ValueError(
            "a commentary section must begin with a `## …` heading for the node "
            "it annotates (%s)" % anchor)


def _new_kommentar_frontmatter(basefile):
    return "---\nannotates: %s\n---\n" % basefile


def _splice_preamble(body, new_text):
    """Replace the document-level preamble (everything before the first anchored
    `## §` section) with `new_text`, keeping the sections that follow. Clearing it
    (empty `new_text`) drops the preamble entirely."""
    block = new_text.rstrip("\n")
    tail = "".join(body.splitlines(keepends=True)[_first_section_line(body):])
    if not block:
        return tail
    return block + "\n\n" + tail if tail else block + "\n"


def _splice_section(body, anchor, new_text):
    """Replace the section for `anchor` with `new_text`, or append it (a new
    section) with a blank-line separator. Trailing whitespace is normalised so
    sections stay one blank line apart."""
    section = new_text.rstrip("\n") + "\n"
    span = _section_span(body, anchor)
    if span is None:
        if not body.strip():
            return section
        return body.rstrip("\n") + "\n\n" + section
    lines = body.splitlines(keepends=True)
    head, tail = "".join(lines[:span[0]]), "".join(lines[span[1]:])
    if tail:                              # keep a blank line before the next section
        section += "\n"
    return head + section + tail
