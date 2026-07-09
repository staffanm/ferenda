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
# a signer line's departement parenthetical: "Mikael Damberg
# (Justitiedepartementet)" -- shared with parse._is_signer_name
RE_TRAILING_PAREN = re.compile(r"\s*\([^)]*\)$")


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
                    "level": level, "text": b["text"], "children": []}
            if b.get("page") is not None:   # a page-less (text/tml) body omits page
                node["page"] = b["page"]
            if num:
                node["num"] = num
            while stack and stack[-1]["level"] >= level:
                stack.pop()
            (stack[-1]["children"] if stack else root).append(node)
            stack.append(node)
        else:
            (stack[-1]["children"] if stack else root).append(b)
    return root


def signers(structure):
    """The signer names of a parsed artifact, in document order: the `signatur`
    blocks parse.tag_frontmatter (prop/skr) or parse.rskr_body (rskr) tagged,
    with any departement parenthetical stripped ("Mikael Damberg
    (Justitiedepartementet)" -> "Mikael Damberg"). Empty when the document's
    front matter defeated the tagging (OCR noise, reflowed lines)."""
    return [RE_TRAILING_PAREN.sub("", runs_text(b["text"])).strip()
            for b in flatten(structure) if b.get("type") == "signatur"]


def ingress(structure):
    """The first paragraph under a proposition's "huvudsakliga innehåll"
    heading (the avsnitt parse.tag_frontmatter promoted), or None. This is the
    government's own one-paragraph summary of the proposal -- the natural log
    message for the sfs history-as-git export."""
    for node in structure:
        if (node.get("type") == "avsnitt"
                and "huvudsakliga innehåll" in runs_text(node["text"])):
            for child in node["children"]:
                if child.get("type") == "stycke":
                    return runs_text(child["text"]).strip()
    return None


def flatten(structure):
    """The inverse of `nest`: the document-order flat block list, with each
    `avsnitt` turned back into its `rubrik` heading block followed by its
    children. Lets a linear consumer (kommentar.py) walk a nested artifact."""
    out = []
    for node in structure:
        if node.get("type") == "avsnitt":
            head = {"type": "rubrik", "level": node.get("level"),
                    "text": node["text"]}
            if node.get("page") is not None:
                head["page"] = node["page"]
            if "num" in node:
                head["num"] = node["num"]
            out.append(head)
            out.extend(flatten(node["children"]))
        else:
            out.append(node)
    return out
