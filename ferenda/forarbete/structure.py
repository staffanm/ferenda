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

from ..lib.artifact import flatten as flatten_nodes
from ..lib.artifact import nest_by_level
from ..lib.text import runs_text

RE_LEAD_NUM = re.compile(r"^(\d+(?:\.\d+)*)\b")        # "14" / "14.3.4" leading a heading
# a signer line's departement parenthetical: "Mikael Damberg
# (Justitiedepartementet)" -- shared with parse._is_signer_name
RE_TRAILING_PAREN = re.compile(r"\s*\([^)]*\)$")

SECTIONS = frozenset({"avsnitt"})       # the one container kind `nest` opens


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
    `structure` list. `rubrik` blocks become `avsnitt` containers, each under the
    nearest open section of lower level; the rest are their content, in document
    order."""
    seen, counter = set(), 0

    def avsnitt(b):
        nonlocal counter
        counter += 1
        num = m.group(1) if (m := RE_LEAD_NUM.match(runs_text(b["text"]))) else None
        node = {"type": "avsnitt", "id": _section_id(num, counter, seen),
                "level": b.get("level") or 1, "text": b["text"], "children": []}
        if b.get("page") is not None:   # a page-less (text/tml) body omits page
            node["page"] = b["page"]
        if num:
            node["num"] = num
        return node

    return nest_by_level(
        blocks,
        lambda b: (b.get("level") or 1) if b.get("type") == "rubrik" else None,
        avsnitt)


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


def beredning(structure):
    """A proposition's own preceding SOU or Ds -- `{identifier, uri}` of the
    first SOU/Ds citation inside its "Ärendet och dess beredning" section, or
    None when the section is absent (pre-1970s propositions carry no numbered
    TOC) or names no utredning (a bill drafted without one). The citation is
    already a link run (`_scan` in parse.py ran the FORARBETEN grammar over
    every block before `nest` built this tree), so no re-parsing is needed."""
    for node in structure:
        if node.get("type") != "avsnitt":
            continue
        if "beredning" in runs_text(node["text"]).lower():
            for block in flatten(node["children"]):
                for run in block.get("text") or []:
                    if not isinstance(run, dict):
                        continue
                    uri = run.get("uri", "")
                    if uri.startswith(("https://lagen.nu/sou/", "https://lagen.nu/ds/")):
                        return {"identifier": run["text"], "uri": uri}
    return None


def _rubrik(node):
    """An `avsnitt` back as the `rubrik` block `nest` built it from."""
    head = {"type": "rubrik", "level": node.get("level"), "text": node["text"]}
    if node.get("page") is not None:
        head["page"] = node["page"]
    if "num" in node:
        head["num"] = node["num"]
    return head


def flatten(structure):
    """The inverse of `nest`: the document-order flat block list, with each
    `avsnitt` turned back into its `rubrik` heading block followed by its
    children. Lets a linear consumer (kommentar.py) walk a nested artifact."""
    return flatten_nodes(structure, containers=SECTIONS, marker=_rubrik)
