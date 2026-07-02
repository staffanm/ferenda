"""Diff two parsed versions of a document -- the "jämför med tidigare
lydelser" view (the old pipeline's requesthandler.diff_versions + htmldiff,
re-done over the artifact JSON instead of generated HTML).

Both sides are flattened to their reading-order text blocks (headings,
stycken, list items, table rows), the block sequences are aligned with
difflib, and changed block pairs get a word-level ``<ins>``/``<del>`` markup
pass. A provision marker (paragraf beteckning / punkt ordinal) participates
in the alignment -- so a renumbered paragraf reads as a change -- but renders
as the normal view's ``span.num``, and headings carry the normal view's
``rubrik`` class, so the diff reads like the page it replaces. The result is
one HTML fragment: the *whole* newer text in document order with every
difference marked, exactly what the old htmldiff view showed. Pure: dicts in,
HTML string out; the API serves it on demand.
"""

from difflib import SequenceMatcher
from html import escape

from .text import runs_text

# node types rendered as one text block; containers are recursed into
_LEAF = ("stycke", "punkt", "listelement", "upphavd", "moment")


def blocks(nodes):
    """Flatten a normal-form node tree to its reading-order text blocks:
    ``{"kind", "id", "marker", "text"}``, the diffable unit. The marker is
    the provision's own number (paragraf beteckning / punkt ordinal), kept
    apart from the text so it can render as the normal view's ``span.num``
    -- alignment still sees it (see diff_html), so numbering changes surface."""
    out = []
    for node in nodes:
        kind = node.get("type")
        if kind == "rubrik":
            out.append({"kind": "rubrik", "level": node.get("level") or 2,
                        "id": node.get("id"), "marker": "",
                        "text": runs_text(node.get("text", []))})
        elif kind == "tabell":
            for rad in node.get("children", []):
                out.append({"kind": "rad", "id": rad.get("id"), "marker": "",
                            "text": " | ".join(runs_text(c)
                                               for c in rad.get("cells", []))})
        elif kind in _LEAF:
            marker = node.get("beteckning") or node.get("ordinal") or ""
            out.append({"kind": kind, "id": node.get("id"),
                        "marker": str(marker),
                        "text": runs_text(node.get("text", []))})
            out += blocks(node.get("children", []))
        else:   # container: paragraf, kapitel, avdelning, bilaga, lista, ...
            out += blocks(node.get("children", []))
    return out


def _key(block):
    """The alignment identity of a block: marker + text, so a renumbering
    without a text change still registers as a change."""
    return "%s\x1f%s" % (block["marker"], block["text"])


def _worddiff(old, new):
    """Word-level <del>/<ins> markup of one changed block pair, on the
    whitespace-collapsed token streams (artifact text is already
    space-normalized)."""
    a, b = old.split(), new.split()
    sm = SequenceMatcher(a=a, b=b, autojunk=False)
    out = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            out.append(escape(" ".join(b[j1:j2])))
            continue
        if i2 > i1:
            out.append("<del>%s</del>" % escape(" ".join(a[i1:i2])))
        if j2 > j1:
            out.append("<ins>%s</ins>" % escape(" ".join(b[j1:j2])))
    return " ".join(out)


def _block_html(block, inner, status="", marker=""):
    """One rendered block, shaped like the normal view: marker as span.num,
    headings as h{level+1}.rubrik (both carry the reading typography and the
    scroll offset), plus a diff-<status> class where the block changed."""
    nid = block.get("id")
    ida = ' id="%s"' % escape(nid) if nid and status != "removed" else ""
    if marker:
        inner = '<span class="num">%s</span> %s' % (marker, inner)
    if block["kind"] == "rubrik":
        lvl = min(block.get("level") or 2, 5) + 1
        cls = "rubrik" + (" diff-%s" % status if status else "")
        return '<h%d%s class="%s">%s</h%d>' % (lvl, ida, cls, inner, lvl)
    cls = ' class="diff-%s"' % status if status else ""
    return "<p%s%s>%s</p>" % (ida, cls, inner)


def _pair_html(old, new):
    """A replaced block pair: word-level marking over the text, the marker
    plain when unchanged, del/ins-wrapped when the provision was renumbered."""
    if old["marker"] == new["marker"]:
        marker = escape(new["marker"])
    else:
        marker = "".join(("<del>%s</del>" % escape(old["marker"])
                          if old["marker"] else "",
                          " " if old["marker"] and new["marker"] else "",
                          "<ins>%s</ins>" % escape(new["marker"])
                          if new["marker"] else ""))
    return _block_html(new, _worddiff(old["text"], new["text"]), "changed",
                       marker)


def diff_html(from_art, to_art):
    """The full newer text in document order with every difference from the
    older version marked: removed blocks as <del>, added as <ins>, changed
    pairs word-diffed. Returns (html, changed_blocks)."""
    a = blocks(from_art.get("structure", []))
    b = blocks(to_art.get("structure", []))
    sm = SequenceMatcher(a=[_key(x) for x in a], b=[_key(x) for x in b],
                         autojunk=False)
    parts, changed = [], 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            parts += [_block_html(x, escape(x["text"]),
                                  marker=escape(x["marker"])) for x in b[j1:j2]]
            continue
        changed += max(i2 - i1, j2 - j1)
        # pair replaced blocks positionally for word-level marking; the
        # unpaired tail (and pure inserts/deletes) shows whole-block ins/del
        for k in range(max(i2 - i1, j2 - j1)):
            old = a[i1 + k] if i1 + k < i2 else None
            new = b[j1 + k] if j1 + k < j2 else None
            if old and new:
                parts.append(_pair_html(old, new))
            elif old:
                parts.append(_block_html(
                    old, "<del>%s</del>" % escape(old["text"]), "removed",
                    "<del>%s</del>" % escape(old["marker"])
                    if old["marker"] else ""))
            elif new:
                parts.append(_block_html(
                    new, "<ins>%s</ins>" % escape(new["text"]), "added",
                    "<ins>%s</ins>" % escape(new["marker"])
                    if new["marker"] else ""))
    return '<div class="version-diff">%s</div>' % "".join(parts), changed
