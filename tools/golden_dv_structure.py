"""DV structural golden -- the instance/ruling skeleton oracle (REWRITE.md §4).

The old pipeline's parsed XHTML+RDFa (``site/data/dv/parsed/{COURT}/{id}.xhtml``)
segmented each referat into its decision structure, which the distilled-RDF
oracle (``golden_dv.py``) does not capture:

  delmal[ordinal]            split case (I, II) -- wraps the instances
    instans[court]           an instance stage (div.instans, dcterms:creator)
      betankande             the föredragande/revisionssekreterare proposal,
                             a *sibling* of dom -- separated by construction
        domskal / domslut
      dom                    the court's own ruling
        domskal              domskäl (reasoning)
        domslut              domslut (the operative ruling)
      skiljaktig             dissenting opinion
      tillagg                concurring addition

This reduces that to a *coarse skeleton* -- the ordered tree of
``(kind, court, ordinal)``, no body text -- and treats it as the spec the new
DV parser's instance model must reproduce. Text is deliberately excluded: the
old input is Word/OCR, so text equality would be all noise; the contract is the
segmentation, not the wording.

Two things to keep in mind, both inherited from the golden methodology (§2):

  * **Spec-first.** The new DV artifact is currently flat (``body`` blocks, no
    ``structure``), so ``validate`` reports all-missing until the parser is
    extended to emit the instance tree. That is the point -- the target is
    written down first. The artifact contract the reducer reads: a nested
    ``structure`` list whose nodes are ``{"type": <kind>, "court"?, "ordinal"?,
    "children": [...]}`` for the kinds above (leaf prose nodes are ignored).
  * **Change detector, not ground truth.** The old FSM segmentation is
    heuristic (the messiest source); a real fraction of diffs will be old mis-
    segmentation, and the new parser may legitimately improve on it. Diffs are
    investigated, not assumed regressions.

    python tools/golden_dv_structure.py normalize PARSED.xhtml
    python tools/golden_dv_structure.py compare PARSED.xhtml ARTIFACT.json
    python tools/golden_dv_structure.py validate [--limit N] [--top N]
"""

import argparse
import glob
import importlib.util
import json
import re
import sys
from collections import Counter
from pathlib import Path

from lxml import etree

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from accommodanda import config  # noqa: E402

XHTML = "http://www.w3.org/1999/xhtml"
PARSED = str(config.DATA / "dv" / "parsed")
ARTIFACTS = str(config.DATA / "dv" / "artifact")

# the decision-structure block kinds, in document order of nesting. bodymeta
# (the referat headnote/keywords) and endmeta (sökord/litteratur trailer) are
# metadata wrappers, not decision structure -- excluded.
STRUCTURE_KINDS = ("delmal", "instans", "betankande", "dom",
                   "domskal", "domslut", "skiljaktig", "tillagg")

# the two referat document types the new pipeline also mints (the third old
# rdf:type, VagledandeDomstolsavgorande, is the verdict resource, not the case)
REFERAT_TYPES = ("Rattsfallsreferat", "Rattsfallsnotis")


def _ln(elem):
    return etree.QName(elem).localname


def _make_node(div):
    """A skeleton node for one structure div. `id` is the discriminating label
    the tree differ pairs on -- the court for an instans, the ordinal for a
    delmal -- so stages line up across old/new by what they are, not position."""
    node = {"type": div.get("class"), "id": None, "children": []}
    prop, content = div.get("property"), div.get("content")
    if prop == "dcterms:creator":          # instans -> its court
        node["id"] = content
    elif prop == "rinfoex:delmalordinal":  # delmal -> I / II / ...
        node["id"] = node["ordinal"] = content
    return node


def _collect(elem, out):
    """Append every structure div descended from `elem` to `out`, nesting one
    inside another and descending transparently through non-structure wrappers
    (the prose paragraphs between the segmentation markers)."""
    for child in elem:
        if _ln(child) == "div" and child.get("class") in STRUCTURE_KINDS:
            node = _make_node(child)
            _collect(child, node["children"])
            out.append(node)
        else:
            _collect(child, out)


def _head_value(head, localname, prop):
    for elem in head:
        if _ln(elem) == localname and (elem.get("property") == prop
                                       or elem.get("rel") == prop):
            return elem.get("content") or elem.get("href")
    return None


def _referat_type(head):
    for elem in head:
        if elem.get("rel") == "rdf:type" or elem.get("property") == "rdf:type":
            local = (elem.get("href") or elem.get("content") or "").rsplit("#", 1)[-1]
            if local in REFERAT_TYPES:
                return local
    return None


def normalize(path):
    """Reduce a parsed referat XHTML to the structural normal form."""
    root = etree.parse(str(path)).getroot()
    head = root.find("{%s}head" % XHTML)
    body = root.find("{%s}body" % XHTML)
    structure = []
    _collect(body, structure)
    return {"uri": body.get("about"),
            "identifier": _head_value(head, "meta", "dcterms:identifier"),
            "type": _referat_type(head),
            "structure": structure}


def skeleton_from_artifact(art):
    """The same normal form from a new DV artifact's `structure` section (the
    contract the parser must satisfy). Leaf prose nodes are dropped; structure
    nodes are re-keyed onto the (type, id) the differ pairs on."""
    def convert(nodes):
        out = []
        for n in nodes:
            kind = n.get("type")
            if kind not in STRUCTURE_KINDS:
                out += convert(n.get("children", []))   # transparent leaf wrapper
                continue
            node = {"type": kind, "id": None,
                    "children": convert(n.get("children", []))}
            if kind == "instans":
                node["id"] = n.get("court")
            elif kind == "delmal":
                node["id"] = node["ordinal"] = n.get("ordinal")
            out.append(node)
        return out
    return {"uri": art.get("uri"), "identifier": art.get("identifier"),
            "type": art.get("doctype") or art.get("type"),
            "structure": convert(art.get("structure", []))}


def compare(old, new, golden_sfs):
    """Diff two structural normal forms; reuses the SFS node-list differ (same
    tree shape) so duplicate-label pairing and order checks come for free."""
    problems = []
    if old.get("uri") != new.get("uri"):
        problems.append("uri: %r != %r" % (old.get("uri"), new.get("uri")))
    golden_sfs.diff_nodelists(old.get("structure", []), new.get("structure", []),
                              "structure", problems)
    return problems


# --- corpus run ---------------------------------------------------------

def load_golden_sfs():
    spec = importlib.util.spec_from_file_location(
        "golden_sfs", Path(__file__).resolve().parent / "golden_sfs.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def index_new():
    """doc-uri -> new artifact path, over all DV artifacts."""
    out = {}
    for p in glob.glob(ARTIFACTS + "/*.json"):
        raw = Path(p).read_bytes()
        if raw.strip():
            out[json.loads(raw)["uri"]] = p
    return out


def signature(problem):
    """Bucket key: strip document-specific specifics from a diff message."""
    sig = re.sub(r"[0-9]+", "#", problem.split("\n")[0])
    return re.sub(r"'[^']*'", "'…'", sig)


def cmd_validate(args, golden_sfs):
    new_by_uri = index_new()
    print("indexed %d new DV artifacts by uri" % len(new_by_uri))
    files = sorted(glob.glob(PARSED + "/*/*.xhtml"))
    if args.limit:
        files = files[:args.limit]

    counts = Counter()
    buckets = Counter()
    examples = {}
    for i, path in enumerate(files, 1):
        old = normalize(path)
        artpath = new_by_uri.get(old["uri"])
        if not artpath:
            counts["uri_absent"] += 1
            continue
        new = skeleton_from_artifact(json.loads(Path(artpath).read_bytes()))
        problems = compare(old, new, golden_sfs)
        if not problems:
            counts["match"] += 1
        else:
            counts["diff"] += 1
            for problem in problems:
                sig = signature(problem)
                buckets[sig] += 1
                examples.setdefault(sig, (old["uri"], problem))
        if i % 500 == 0 or i == len(files):
            print("\r%d/%d %s" % (i, len(files), dict(counts)), end="", flush=True)
    print()

    scored = counts["match"] + counts["diff"]
    print("%d parsed referat: %d matched a new artifact (%d had none)"
          % (len(files), scored, counts["uri_absent"]))
    if scored:
        print("  structure match: %d (%.1f%%), diff: %d"
              % (counts["match"], 100 * counts["match"] / scored, counts["diff"]))
    print("top structure-diff buckets:")
    for sig, n in buckets.most_common(args.top):
        uri, example = examples[sig]
        print("  %6d  %s\n          e.g. %s: %s"
              % (n, sig, uri, example.split("\n")[0][:100]))


def main():
    golden_sfs = load_golden_sfs()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    n = sub.add_parser("normalize", help="structural normal form to stdout")
    n.add_argument("file")
    c = sub.add_parser("compare", help="diff a parsed referat vs a new artifact")
    c.add_argument("parsed", help="old parsed .xhtml")
    c.add_argument("artifact", help="new artifact .json")
    v = sub.add_parser("validate", help="corpus structure cross-check")
    v.add_argument("--limit", type=int)
    v.add_argument("--top", type=int, default=20)
    args = ap.parse_args()

    if args.command == "normalize":
        json.dump(normalize(args.file), sys.stdout, ensure_ascii=False, indent=2)
        print()
    elif args.command == "compare":
        old = normalize(args.parsed)
        new = skeleton_from_artifact(json.loads(Path(args.artifact).read_bytes()))
        problems = compare(old, new, golden_sfs)
        if problems:
            print("%d difference(s):" % len(problems))
            for problem in problems:
                print("  " + problem)
            sys.exit(1)
        print("identical")
    else:
        cmd_validate(args, golden_sfs)


if __name__ == "__main__":
    main()
