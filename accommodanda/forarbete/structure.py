"""Group a förarbete's flat block sequence into its natural section hierarchy.

Förarbeten carry a real outline -- numbered headings 14 -> 14.3 -> 14.3.4 (the
depth you see in the TOC), plus unnumbered sub-headings -- but the parser emits
them as a flat run of blocks each tagged with a heading `level`. `nest` rebuilds
the tree: a `rubrik` opens an `avsnitt` (section) nested under the nearest open
section of lower level, and every other block (stycke / kapitel / paragraf) is
that section's content.

A section gets an `id` -- its heading's dotted number where present, else a
running counter -- so it anchors the TOC and becomes a search fragment. It is
NOT a citation target: förarbete citations are page-precise (`#sid{N}`), and
every leaf keeps its `page`, so those anchors are unaffected.

`flatten` is the inverse view: the document-order block sequence (sections back
to `rubrik` blocks), for consumers that walk the text linearly -- the
författningskommentar extractor in `kommentar.py`.
"""

import re

from ..lib.text import runs_text

RE_LEAD_NUM = re.compile(r"^(\d+(?:\.\d+)*)\b")        # "14" / "14.3.4" leading a heading


def _section_id(num, counter, seen):
    """A unique section anchor: the dotted number ('a14.3.4') where the heading
    has one, else a running counter ('sec7'); a '-2' suffix breaks any clash."""
    base = ("a" + num) if num else ("sec%d" % counter)
    sid, n = base, 2
    while sid in seen:
        sid, n = "%s-%d" % (base, n), n + 1
    seen.add(sid)
    return sid


def nest(blocks):
    """Flat förarbete block dicts ({type, text, page, level?, num?}) -> a nested
    `structure` list. `rubrik` blocks become `avsnitt` containers; the rest are
    their content, in document order."""
    root, stack, seen, counter = [], [], set(), 0
    for b in blocks:
        if b.get("type") == "rubrik":
            counter += 1
            level = b.get("level") or 1
            m = RE_LEAD_NUM.match(runs_text(b["text"]))
            num = m.group(1) if m else None
            node = {"type": "avsnitt", "id": _section_id(num, counter, seen),
                    "level": level, "text": b["text"], "page": b.get("page"),
                    "children": []}
            if num:
                node["num"] = num
            while stack and stack[-1]["level"] >= level:
                stack.pop()
            (stack[-1]["children"] if stack else root).append(node)
            stack.append(node)
        else:
            (stack[-1]["children"] if stack else root).append(b)
    return root


def flatten(structure):
    """The inverse of `nest`: the document-order flat block list, with each
    `avsnitt` turned back into its `rubrik` heading block followed by its
    children. Lets a linear consumer (kommentar.py) walk a nested artifact."""
    out = []
    for node in structure:
        if node.get("type") == "avsnitt":
            head = {"type": "rubrik", "level": node.get("level"),
                    "text": node["text"], "page": node.get("page")}
            if "num" in node:
                head["num"] = node["num"]
            out.append(head)
            out.extend(flatten(node["children"]))
        else:
            out.append(node)
    return out
