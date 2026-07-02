"""Group a föreskrift's flat block run into its kapitel / paragraf tree.

Unlike a förarbete (a flat outline of numbered headings -- see
``forarbete/structure.py``), a föreskrift is a *statute*: its skeleton is
``kapitel`` -> ``paragraf`` -> ``stycke``, with ``rubrik`` headings labelling
groups of paragrafer. So the nesting here is statute-shaped, and the anchor it
mints on each paragraf is the SFS one lagen.nu has always used -- ``K2P3`` (2
kap. 3 §) or ``P3`` (a chapterless föreskrift's 3 §). That anchor is what makes a
föreskrift paragraf a *citation target*: a statute's ``bemyndigande`` edge, or a
cross-reference from another regulation, resolves to ``…/fffs/2013:10#K2P3``.

A ``rubrik`` does not contain paragrafer (a heading just precedes the paragrafer
it introduces); only a ``kapitel`` does. Loose stycken before the first paragraf
(a chapterless preamble-less föreskrift, or a short declarative one with no §§ at
all) stay at the top level, so a body without a single § still renders.
"""

import re

RE_LEAD_PARA = re.compile(r"^(\d+\s*[a-z]?)\s*§")     # "3 §" / "3 a §" leading a block
RE_LEAD_KAP = re.compile(r"^(\d+)\s*kap\.")           # "2 kap." leading a block


def _para_anchor(kap, num, seen):
    """The SFS paragraf anchor: 'K2P3' under 2 kap., else 'P3'; the §-number is
    de-spaced ('3 a' -> '3a'). A '-2' suffix breaks the rare clash (a föreskrift
    that restarts § numbering without a chapter)."""
    pnum = re.sub(r"\s+", "", num)
    base = ("K%sP%s" % (kap, pnum)) if kap else ("P%s" % pnum)
    anchor, n = base, 2
    while anchor in seen:
        anchor, n = "%s-%d" % (base, n), n + 1
    seen.add(anchor)
    return anchor


def _strip_marker(runs):
    """Drop the leading ``N §`` from a paragraf's first text run -- the §-number
    now hangs in the gutter (rendered from ``ordinal``), so the body stycke must
    not repeat it. The marker is plain text leading the first (string) run."""
    if runs and isinstance(runs[0], str):
        runs = list(runs)
        runs[0] = RE_LEAD_PARA.sub("", runs[0], count=1).lstrip()
        if not runs[0]:                  # the marker was the whole first run
            runs = runs[1:]
    return runs


def nest(blocks):
    """Flat föreskrift block dicts ({type, text, page, num?}) -> the nested
    ``structure`` list, in the **shared statute node shape** the renderer and
    catalog already speak (``id`` anchor + ``ordinal`` number; a paragraf's body
    is a ``stycke`` child, a kapitel's title a ``rubrik`` child) -- so föreskrift
    pages reuse ``render_node`` and each paragraf becomes a fragment/citation
    target for free. ``kapitel`` opens a chapter; ``paragraf`` (the ``#K2P3``
    anchor) opens under it; ``rubrik``/``stycke`` are content of the open paragraf,
    else the open chapter, else top-level."""
    root, seen = [], set()
    kap = None          # the open kapitel node, or None
    kapnum = None
    para = None         # the open paragraf node, or None

    def sink():
        """Where a content block (rubrik/stycke) currently belongs."""
        return para["children"] if para else (kap["children"] if kap else root)

    for b in blocks:
        t = b.get("type")
        if t == "kapitel":
            kapnum = b.get("num")
            kap = {"type": "kapitel", "id": "K%s" % kapnum if kapnum else None,
                   "ordinal": kapnum, "page": b.get("page"),
                   "children": [{"type": "rubrik", "text": b["text"], "level": 1,
                                 "page": b.get("page")}]}
            para = None
            root.append(kap)
        elif t == "paragraf":
            para = {"type": "paragraf",
                    "id": _para_anchor(kapnum, b.get("num") or "", seen),
                    "ordinal": b.get("num"), "page": b.get("page"),
                    "children": [{"type": "stycke", "text": _strip_marker(b["text"]),
                                  "page": b.get("page")}]}
            (kap["children"] if kap else root).append(para)  # ty: ignore[unresolved-attribute]  # artifact tree nodes are untyped dicts; children is a list
        elif t == "rubrik":
            # a heading ends the current paragraf's reach and labels what follows
            para = None
            (kap["children"] if kap else root).append(b)  # ty: ignore[unresolved-attribute]
        else:
            sink().append(b)
    return root


def flatten(structure):
    """The inverse of :func:`nest`: the document-order flat block list, each
    container turned back into its own marker block followed by its children.
    Lets a linear consumer walk a nested artifact."""
    out = []
    for node in structure:
        if node.get("type") in ("kapitel", "paragraf"):
            out.append({k: node[k]
                        for k in ("type", "id", "ordinal", "page") if k in node})
            out.extend(flatten(node.get("children", [])))
        else:
            out.append(node)
    return out
