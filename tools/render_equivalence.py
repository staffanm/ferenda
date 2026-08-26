"""Before/after equivalence harness for the Jinja port of the render layer.

Renders a deterministic sample of the site -- N documents per source, the
historical sfs lydelse pages, every aggregate/browse/feed page the generate
phase writes, the editorial site pages and /statistik -- normalizes each page
down to what a browser actually distinguishes, and snapshots the result as
plain-text files. Two snapshots taken before and after a render-layer change
can then be diffed file by file; a clean diff means the change is invisible
in a browser.

Normalization rules (the point of the tool -- byte equality is NOT required):
  * whitespace-only text is dropped where it never renders (between block
    elements) and collapsed to one space in inline flow, where one-vs-none
    is a visible difference;
  * runs of whitespace collapse to a single space; leading/trailing spaces
    inside block-level elements are stripped (the browser does the same);
  * <pre>, <script>, <style> and <textarea> content is verbatim (the rail's
    JSON island must compare exactly);
  * attributes are sorted; the serialization is canonical.
Atom feeds are XML, not HTML: they are snapshotted byte-exact.

Usage (dev-only tool; html5lib comes from the dev dependency group):
  uv run python tools/render_equivalence.py snapshot BEFORE
  ... change the renderer ...
  uv run python tools/render_equivalence.py snapshot after-phase2
  uv run python tools/render_equivalence.py compare BEFORE after-phase2
  uv run python tools/render_equivalence.py selftest   # normalizer fixed point

Snapshots live under site/data/render-eq/<label>/ (inside the gitignored
corpus tree, so they survive the session without touching git).
"""

import argparse
import difflib
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

import html5lib

from ferenda import browse
from ferenda.build import SOURCE_RENDERERS, sfs_version_pages
from ferenda.lib import catalog, compress, layout, page, render
from ferenda.site import render as site_render
from ferenda.stats import render as stats_render

SNAP_ROOT = layout.DATA / "render-eq"
CATALOG = layout.DATA / "catalog.sqlite"

# ---------------------------------------------------------------------------
# HTML5 normalizer
# ---------------------------------------------------------------------------

# raw-text elements: the parser leaves their text undecoded, so serialization
# must not escape it (escaping && inside a script would double on re-parse)
RAW = {"script", "style"}
# entity-decoded verbatim elements: whitespace is significant, but the text
# round-trips through entity decode/encode
VERBATIM = {"pre", "textarea"}
# elements whose direct whitespace-only text never renders: pure flow/table
# containers where the browser drops inter-tag whitespace entirely
WS_DROP = {"html", "head", "body", "div", "section", "article", "nav",
           "header", "footer", "aside", "main", "ul", "ol", "dl", "table",
           "thead", "tbody", "tfoot", "tr", "details", "figure", "form",
           "select", "colgroup", "hgroup"}
# block-level elements: leading/trailing inline whitespace inside them is
# invisible, so the canonical form strips it
BLOCK = WS_DROP | {"p", "li", "td", "th", "dt", "dd", "figcaption", "summary",
                   "blockquote", "caption", "h1", "h2", "h3", "h4", "h5",
                   "h6", "title"}
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "source", "track", "wbr"}

_WS = re.compile(r"\s+")


def _attr_escape(v):
    return (v.replace("&", "&amp;").replace('"', "&quot;")
            .replace("<", "&lt;").replace(">", "&gt;"))


def _text_escape(v):
    return v.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _tag(el):
    # namespaceHTMLElements=False leaves plain tags for HTML; inline SVG keeps
    # its namespace -- strip it, the local name is what matters for comparison
    t = el.tag
    return t.rsplit("}", 1)[-1] if isinstance(t, str) else t


def _serialize(el, out, mode):
    """`mode` is None (normal flow), "raw" (script/style: text passes through
    exactly) or "verbatim" (pre/textarea: text is significant but re-escaped)."""
    tag = _tag(el)
    if not isinstance(tag, str):        # comment/PI node: not browser-visible
        if el.tail:
            out.append(("text" if mode is None else mode, el.tail))
        return
    attrs = "".join(' %s="%s"' % (k.rsplit("}", 1)[-1], _attr_escape(v))
                    for k, v in sorted(el.attrib.items()))
    out.append(("tag", "<%s%s>" % (tag, attrs)))
    inner = mode or ("raw" if tag in RAW else
                     "verbatim" if tag in VERBATIM else None)
    if el.text:
        out.append(("text" if inner is None else inner, el.text))
    for child in el:
        _serialize(child, out, inner)
    if tag not in VOID:
        out.append(("tag", "</%s>" % tag))
    if el.tail:
        out.append(("text" if mode is None else mode, el.tail))


def _fold_text(tokens):
    """Apply the whitespace model over the token stream, in document order.
    Text tokens collapse; whether a whitespace-only token survives (as one
    space) or vanishes depends on the nearest enclosing element, which we
    track with a tag stack.

    Readability newlines are inserted ONLY where the model itself says
    whitespace is insignificant (inside pure flow containers and after a
    block close), so re-normalizing the output is a fixed point -- the
    selftest's guarantee that the pretty-printing cannot smuggle in visible
    whitespace."""
    out = []
    stack = []          # open element names
    last_space = True   # start of document behaves like after a block edge
    for kind, value in tokens:
        if kind == "tag":
            closing = value.startswith("</")
            name = value[2 if closing else 1:].split(">")[0].split(" ")[0]
            if closing:
                # strip a trailing space before a block close
                if name in BLOCK and out and out[-1] == " ":
                    out.pop()
                if stack and stack[-1] == name:
                    stack.pop()
            out.append(value)
            if not closing and name not in VOID:
                stack.append(name)
            in_dropzone = (stack and stack[-1] in WS_DROP) if not closing \
                else (stack and stack[-1] in WS_DROP)
            if name in BLOCK or (name in VOID and name in ("br", "hr")):
                last_space = True   # block edge: next leading space is invisible
                if len(out) >= 2 and out[-2] == " " and not closing:
                    # space directly before a block open never renders
                    out.pop(-2)
            else:
                last_space = False
            # newline where it can never render: we are now directly inside a
            # pure flow container (ws-only text is dropped there), or right
            # after a block-level close in any context the model strips
            if in_dropzone and name not in RAW | VERBATIM:
                out.append("\n")
        elif kind == "raw":
            out.append(value)
            last_space = False
        elif kind == "verbatim":
            out.append(_text_escape(value))
            last_space = False
        else:
            parent = stack[-1] if stack else "html"
            if not value.strip():
                if parent in WS_DROP:
                    continue
                if last_space:
                    continue
                out.append(" ")
                last_space = True
                continue
            text = _WS.sub(" ", value)
            if last_space:
                text = text.lstrip(" ")
            out.append(_text_escape(text))
            last_space = text.endswith(" ")
    # collapse newline runs (a close inside a container emits up to two)
    joined = "".join(out)
    return re.sub(r"\n+", "\n", joined)


def normalize(html_text):
    doc = html5lib.parse(html_text, namespaceHTMLElements=False)
    tokens = []
    _serialize(doc, tokens, None)
    return "<!doctype html>\n" + _fold_text(tokens) + "\n"


# ---------------------------------------------------------------------------
# sampling
# ---------------------------------------------------------------------------

def _spaced(rows, n):
    if len(rows) <= n:
        return rows
    step = len(rows) / n
    return [rows[int(i * step)] for i in range(n)]


def sample_documents(con, per_source):
    """Deterministic (uri, source, path, title) sample: `per_source` docs per
    source, evenly spaced over the uri-sorted set for variety (years, courts,
    document sizes)."""
    root = catalog.data_root(con)
    jobs = []
    for (source,) in con.execute(
            "SELECT DISTINCT source FROM documents ORDER BY source"):
        if source == "kommentar":      # annotation layer, no pages of its own
            continue
        rows = con.execute(
            "SELECT uri, source, path, title FROM documents WHERE source=? "
            "ORDER BY uri", (source,)).fetchall()
        for uri, src, path, title in _spaced(rows, per_source):
            jobs.append((uri, src, str(root / path) if path else path, title))
    return jobs


def sample_version_pages(n):
    """A few uncatalogued extra pages: sfs historical consolidations and
    föreskrift /grund pages, sampled the same way generate collects them."""
    versions = sfs_version_pages(
        sorted(layout.SFS_ARTIFACT.glob("*/*.versions.json")))
    grund = sorted(layout.foreskrift_grund_pages())
    return _spaced(versions, n) + _spaced(grund, n)


def snapshot_documents(con, site, out, per_source, versions):
    pages = sample_documents(con, per_source) + sample_version_pages(versions)
    skipped = []
    for uri, source, path, title in pages:
        try:
            art = (json.loads(compress.read_bytes(path)) if path
                   else {"uri": uri, "type": source, "title": title})
        except FileNotFoundError:
            skipped.append(uri)
            continue
        html = render.render_document(art, source, site, SOURCE_RENDERERS)
        rel = Path("doc") / page.doc_relpath(uri)
        target = out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(normalize(html), encoding="utf-8")
    return len(pages) - len(skipped), skipped


def snapshot_aggregates(con, out):
    """Everything render_aggregates writes (except the static assets), via the
    real writers into a scratch tree, then normalized into the snapshot.
    Browse trees are rendered whole -- they are cheap relative to documents."""
    tmp = Path(tempfile.mkdtemp(prefix="render-eq-"))
    try:
        render.render_aggregates(con, tmp,
                                 write_index=not site_render.has_frontpage())
        browse.generate_all(str(CATALOG), tmp, con)
        site_render.write_site(tmp)
        if compress.exists(layout.artifact("stats",
                                           stats_render.ARTIFACT_BASEFILE)):
            stats_render.write_stats(tmp)
        count = 0
        seen = set()
        for path in sorted(tmp.rglob("*")):
            if not path.is_file():
                continue
            # a page is stored as either the plain file or a compressed
            # variant; fold both onto the logical name and read through compress
            logical = path
            for suffix in compress.SUFFIXES:
                if path.name.endswith(suffix):
                    logical = path.with_name(path.name[:-len(suffix)])
                    break
            if logical in seen:
                continue
            seen.add(logical)
            if logical.name.endswith((".css", ".js", ".txt", ".woff2")):
                continue               # static assets (incl. font binaries)
            rel = logical.relative_to(tmp)
            target = out / "agg" / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            text = compress.read_text(logical)
            if logical.name.endswith(".atom"):
                target.write_text(text, encoding="utf-8")   # XML: byte-exact
            else:
                target.write_text(normalize(text), encoding="utf-8")
            count += 1
        return count
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def cmd_snapshot(label, per_source, versions):
    out = SNAP_ROOT / label
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    con = catalog.connect(str(CATALOG))
    site = page.Site.from_catalog(con)
    ndocs, skipped = snapshot_documents(con, site, out, per_source, versions)
    naggs = snapshot_aggregates(con, out)
    print("snapshot %s: %d document pages, %d aggregate pages -> %s"
          % (label, ndocs, naggs, out))
    if skipped:
        print("  skipped %d vanished artifacts (catalog ahead of tree): %s"
              % (len(skipped), ", ".join(skipped[:5])))


def cmd_compare(before, after, context):
    a_root, b_root = SNAP_ROOT / before, SNAP_ROOT / after
    for root in (a_root, b_root):
        assert root.exists(), "no snapshot %r under %s" % (root.name, SNAP_ROOT)
    a_files = {p.relative_to(a_root) for p in a_root.rglob("*") if p.is_file()}
    b_files = {p.relative_to(b_root) for p in b_root.rglob("*") if p.is_file()}
    only_a, only_b = sorted(a_files - b_files), sorted(b_files - a_files)
    changed = []
    for rel in sorted(a_files & b_files):
        a_text = (a_root / rel).read_text(encoding="utf-8")
        b_text = (b_root / rel).read_text(encoding="utf-8")
        if a_text != b_text:
            changed.append((rel, a_text, b_text))
    for rel in only_a:
        print("only in %s: %s" % (before, rel))
    for rel in only_b:
        print("only in %s: %s" % (after, rel))
    for rel, a_text, b_text in changed:
        print("=== %s" % rel)
        diff = difflib.unified_diff(a_text.splitlines(), b_text.splitlines(),
                                    before, after, lineterm="", n=1)
        shown = list(diff)[2:2 + context]
        print("\n".join(shown))
        print()
    print("compared %d common files: %d differ, %d only-before, %d only-after"
          % (len(a_files & b_files), len(changed), len(only_a), len(only_b)))
    return 1 if (changed or only_a or only_b) else 0


def cmd_check(label, sources, per_source, context):
    """Fast mid-phase spot check: render the sampled documents of `sources`
    with the CURRENT code and diff each against the stored snapshot -- seconds
    per source instead of a full snapshot run. The phase gate is still the
    full snapshot+compare."""
    snap = SNAP_ROOT / label
    assert snap.exists(), "no snapshot %r under %s" % (label, SNAP_ROOT)
    con = catalog.connect(str(CATALOG))
    site = page.Site.from_catalog(con)
    wanted = set(sources.split(","))
    pages = [j for j in sample_documents(con, per_source) if j[1] in wanted]
    ok = bad = missing = 0
    for uri, source, path, title in pages:
        try:
            art = json.loads(compress.read_bytes(path)) if path else {
                "uri": uri, "type": source, "title": title}
        except FileNotFoundError:
            continue
        live = normalize(render.render_document(art, source, site, SOURCE_RENDERERS))
        stored_path = snap / "doc" / page.doc_relpath(uri)
        if not stored_path.exists():
            missing += 1
            continue
        stored = stored_path.read_text(encoding="utf-8")
        if live == stored:
            ok += 1
            continue
        bad += 1
        print("=== %s" % uri)
        diff = difflib.unified_diff(stored.splitlines(), live.splitlines(),
                                    label, "live", lineterm="", n=1)
        print("\n".join(list(diff)[2:2 + context]))
        print()
    print("check vs %s: %d ok, %d differ, %d not in snapshot"
          % (label, ok, bad, missing))
    return 1 if bad else 0


def cmd_selftest(per_source):
    """The normalizer must be a fixed point on its own output, or the harness
    could hide its own bugs."""
    con = catalog.connect(str(CATALOG))
    site = page.Site.from_catalog(con)
    pages = sample_documents(con, per_source)
    bad = 0
    for uri, source, path, title in pages:
        try:
            art = json.loads(compress.read_bytes(path)) if path else {
                "uri": uri, "type": source, "title": title}
        except FileNotFoundError:
            continue
        html = render.render_document(art, source, site, SOURCE_RENDERERS)
        once = normalize(html)
        twice = normalize(once)
        if once != twice:
            bad += 1
            print("NOT a fixed point: %s" % uri)
            for line in difflib.unified_diff(once.splitlines(),
                                             twice.splitlines(), "once",
                                             "twice", lineterm="", n=1):
                print(line)
                if bad > 40:
                    break
    print("selftest: %d/%d pages stable" % (len(pages) - bad, len(pages)))
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    snap = sub.add_parser("snapshot")
    snap.add_argument("label")
    snap.add_argument("--per-source", type=int, default=50)
    snap.add_argument("--versions", type=int, default=10)
    comp = sub.add_parser("compare")
    comp.add_argument("before")
    comp.add_argument("after")
    comp.add_argument("--context", type=int, default=40,
                      help="diff lines shown per differing file")
    st = sub.add_parser("selftest")
    st.add_argument("--per-source", type=int, default=3)
    chk = sub.add_parser("check")
    chk.add_argument("label")
    chk.add_argument("--sources", required=True,
                     help="comma-separated source list to spot-check")
    chk.add_argument("--per-source", type=int, default=50)
    chk.add_argument("--context", type=int, default=40)
    args = ap.parse_args()
    if args.cmd == "snapshot":
        cmd_snapshot(args.label, args.per_source, args.versions)
        return 0
    if args.cmd == "compare":
        return cmd_compare(args.before, args.after, args.context)
    if args.cmd == "check":
        return cmd_check(args.label, args.sources, args.per_source,
                         args.context)
    return cmd_selftest(args.per_source)


if __name__ == "__main__":
    sys.exit(main())
