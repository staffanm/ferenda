"""Project the git-backed markdown wiki into kommentar / begrepp artifacts.

A **kommentar** file (`commentary/sfs/2009/400.md`, frontmatter
`annotates: 2009:400`) is per-paragraph commentary: each `## 21 kap 1 §` heading
becomes a section anchored to the statute fragment it annotates (`2009:400#K21P1`),
and the heading itself is a link to that paragraph -- so `relate` records a
kommentar→paragraph edge and the statute paragraph shows the commentary in its
margin (the old side-by-side). A continuously-numbered law is commented with bare
`## N §` headings (anchored `#P{N}`, the chapter dropped); a per-chapter law with
`## N kap M §` (anchored `#K{N}P{M}`). The prose is citation-scanned with the
commented law as the relative-reference base.

A **begrepp** file (`concept/Ne bis in idem.md`, frontmatter `title:`) is a
concept definition published at `begrepp/Ne_bis_in_idem`; its
`[label](begrepp:Concept)` links resolve to other concepts and its prose
citations to laws/cases, so the concept becomes a hub the rest of the corpus
links into. `aliases:` (from MediaWiki redirects) record alternate names that
resolve to the concept.

The source is markdown in the ``lagen-wiki`` sibling content repo (parsed by
``lib/markdown``); this module was previously fed the MediaWiki XML dump via
``lib/wikitext`` -- the artifact it emits is unchanged (PRD §3.1: the migration is
bounded to this layer).
"""

import functools
import glob
import re
from pathlib import Path

from ..eurlex.structure import anchored_blocks
from ..lib import markdown
from ..lib.datasets import NAMEDLAWS as SFS_NAMEDLAWS
from ..lib.lagrum import (
    EULAGSTIFTNING,
    EURATTSFALL,
    FORARBETEN,
    KORTLAGRUM,
    LAGRUM,
    MYNDIGHETSBESLUT,
    RATTSFALL,
    LagrumParser,
    load_abbreviations,
    load_namedlaws,
)

PARSE_TYPES = [LAGRUM, KORTLAGRUM, EULAGSTIFTNING, RATTSFALL, FORARBETEN,
               EURATTSFALL, MYNDIGHETSBESLUT]

# a commentary heading -> the statute fragment it annotates. The chapter is
# optional: a continuously-numbered law (avtalslagen, where § runs 1..40 across
# the chapters) is commented with bare `## N §` headings under a `## N kap`
# heading, and SFS mints those paragrafs as `P{N}` (the chapter segment dropped);
# a per-chapter-numbered law (brottsbalken) is commented with fully-qualified
# `## N kap M §` headings and SFS mints `K{N}P{M}`. Matching that, a bare `N §`
# yields `P{N}` and a qualified `N kap M §` yields `K{N}P{M}`.
RE_PARA = re.compile(r"(?:(\d+)\s*kap\.?\s+)?(\d+)\s*([a-z])?\s*§(?:\s+(\d+)\s*st)?")
RE_KAP = re.compile(r"^\s*(\d+)\s*kap")
# an EU act is commented per article (PRD Step 3): `## Artikel 5` -> "5", the
# sub-article forms `## Artikel 3.4` -> "3.4", `## Artikel 5.2 a` -> "5.2.a" --
# the dotted `article(.paragraph)(.point)` anchors render_eurlex mints from the
# act's structure (a definitions point is `#3.4`, a list point `#5.2.a`).
RE_ARTIKEL = re.compile(r"^\s*[Aa]rtikel\s+(\d+)(?:[.\s]+(\d+))?(?:[.\s]+([a-z]))?\b")
# and per recital: `## Skäl 13` or `## (13)` -> "recital-13" (the `#recital-N`
# anchor render_eurlex mints for a numbered preamble recital).
RE_SKAL = re.compile(r"^\s*(?:[Ss]käl\s+|\()(\d+)\)?\s*$")


@functools.cache
def _vocab():
    return load_namedlaws(SFS_NAMEDLAWS), load_abbreviations(SFS_NAMEDLAWS)


def _parser(basefile):
    namedlaws, abbreviations = _vocab()
    return LagrumParser(namedlaws, basefile=basefile,
                        abbreviations=abbreviations, parse_types=PARSE_TYPES)


def heading_fragment(heading):
    """A commentary section heading -> the host node's anchor it annotates.
    SFS: "21 kap 1 §" -> "K21P1"; "1 kap. 1 c §" -> "K1P1c"; "1 §" -> "P1";
    "25 kap" -> "K25". EU: "Artikel 5" -> "5"; "Artikel 3.4" -> "3.4";
    "Artikel 5.2 a" -> "5.2.a"; "Skäl 13" / "(13)" -> "recital-13"."""
    m = RE_PARA.search(heading)
    if m:
        frag = ("K%s" % m.group(1)) if m.group(1) else ""
        frag += "P%s" % m.group(2)
        if m.group(3):
            frag += m.group(3)
        if m.group(4):
            frag += "S%s" % m.group(4)
        return frag
    m = RE_KAP.match(heading)
    if m:
        return "K%s" % m.group(1)
    m = RE_ARTIKEL.match(heading)
    if m:
        return ".".join(g for g in m.groups() if g)
    m = RE_SKAL.match(heading)
    if m:
        return "recital-%s" % m.group(1)
    return None


def _read(path):
    return markdown.frontmatter(Path(path).read_text(encoding="utf-8"))


def host_uri(annotates):
    """The annotated act's uri from an `annotates:` value. An SFS number
    ("2009:400") is a lagen.nu top-level page; a CELEX ("32024R2847", no colon) is
    the ext/celex act the eurlex source publishes -- so a single annotation layer
    serves any host (PRD Step 2)."""
    a = str(annotates)
    return "https://lagen.nu/" + a if ":" in a \
        else "https://lagen.nu/ext/celex/" + a


def kommentar_artifact(path):
    meta, body = _read(path)
    basefile = str(meta["annotates"])
    law_uri = host_uri(basefile)
    sections, body = markdown.guidance_sections(body)
    # each `## Externa länkar` block attaches to the section heading it sits under
    # (its fragment), or to the document as a whole (None); the document-level
    # block rides the act's rail (Step 2), a per-section block the article's rail
    # alongside its commentary (Step 3).
    guidance = {}
    for owner, items in sections:
        if owner is None:                    # document-level block (Step 2)
            frag = None
        else:                                # per-section block (Step 3)
            frag = heading_fragment(owner)
            assert frag is not None, (
                "%s: a `## %s` block sits under heading %r, which is not an "
                "anchorable article/paragraph/recital -- a mis-numbered heading "
                "would silently attach its links to the whole document instead"
                % (basefile, markdown.GUIDANCE_HEADING, owner))
        guidance.setdefault(frag, []).extend(items)
    parser = _parser(basefile)
    nodes, section, frag = [], None, None
    for block in markdown.blocks(body):
        if block[0] == "rubrik":
            _, level, heading = block
            f = heading_fragment(heading)
            if f:
                frag, section = f, []
                node = {"type": "sektion", "id": frag, "heading": heading,
                        "text": [{"predicate": "dcterms:references",
                                  "uri": "%s#%s" % (law_uri, frag),
                                  "text": heading}],
                        "children": section}
                if frag in guidance:        # per-article external links (Step 3)
                    node["guidance"] = guidance[frag]
                nodes.append(node)
            else:
                (section if section is not None else nodes).append(
                    {"type": "rubrik", "level": level, "text": [heading]})
        else:
            runs = markdown.to_runs(block[1], parser, fragment=frag)
            (section if section is not None else nodes).append(
                {"type": "stycke", "text": runs})
    art = {"uri": "https://lagen.nu/kommentar/" + basefile, "type": "kommentar",
           "basefile": basefile, "annotates": law_uri, "author": meta.get("author"),
           "categories": meta.get("categories", []), "body": nodes}
    if None in guidance:          # document-level external links (Step 2)
        art["guidance"] = guidance[None]
    return art


def _id_recital_anchors(structure):
    """Each node's structural `id` plus the `recital-N` anchor render mints for a
    numbered recital (which carries no structural id of its own)."""
    anchors = set()
    for node in structure:
        if node.get("id"):
            anchors.add(node["id"])
        if node.get("type") == "recital" and (node.get("num") or "").isdigit():
            anchors.add("recital-%s" % node["num"])
        anchors |= _id_recital_anchors(node.get("children", []))
    return anchors


def _host_anchors(structure):
    """Every anchor a commentary section can target in a host act: each node's
    structural `id` and `recital-N`, plus -- for an EU act -- the dotted
    sub-article anchors (`5.2`, `6.2.a`) the renderer mints for paragraphs/points
    that carry no id of their own (`anchored_blocks`, a no-op for non-EU hosts)."""
    return (_id_recital_anchors(structure)
            | {a for a, _ in anchored_blocks(structure)})


def dangling_anchors(komm_art, host_art):
    """Section anchors in a kommentar artifact that have no matching node in the
    act it annotates -- a mis-numbered `## Artikel N` / `## N kap M §` / `## Skäl N`
    whose commentary and guidance would never surface in any node's rail (PRD
    Step 3 validation). A sub-article ("5.2") that isn't itself enumerated is
    tolerated when its base article ("5") exists."""
    anchors = _host_anchors(host_art.get("structure", []))
    return [b["id"] for b in komm_art.get("body", [])
            if b.get("type") == "sektion"
            and b["id"] not in anchors and b["id"].split(".")[0] not in anchors]


def begrepp_artifact(path):
    meta, body = _read(path)
    title = meta["title"]
    parser = _parser("begrepp")
    nodes = []
    for block in markdown.blocks(body):
        if block[0] == "rubrik":
            nodes.append({"type": "rubrik", "level": block[1], "text": [block[2]]})
        else:
            nodes.append({"type": "stycke",
                          "text": markdown.to_runs(block[1], parser, context={})})
    art = {"uri": markdown.begrepp_uri(title), "type": "begrepp", "title": title,
           "categories": meta.get("categories", []), "body": nodes}
    aliases = [markdown.begrepp_uri(a) for a in meta.get("aliases", [])]
    if aliases:                              # MediaWiki redirects -> alternate uris
        art["aliases"] = aliases
    return art


# --------------------------------------------------------------------------
# basefile <-> file indexes. Frontmatter is authoritative (filenames are a
# convenience; `/`, `:` and Swedish characters make them ambiguous) -- PRD §1.
# --------------------------------------------------------------------------

def _wiki_dir(root, sub):
    d = Path(root) / sub
    assert d.is_dir(), (
        "wiki content dir %s missing -- WIKI_ROOT (%s) must point at the "
        "lagen-wiki markdown repo (a sibling checkout, not a submodule); clone it "
        "next to this repo or set wiki_root in config.yml / $WIKI_ROOT" % (d, root))
    return d


@functools.cache
def kommentar_index(root):
    """basefile -> path, over commentary/**/*.md, keyed on `annotates:` (an SFS
    number or a CELEX). The commentary/guidance is filed under the source it
    annotates -- `commentary/sfs/1915/218.md`, `commentary/eurlex/2024/32024R2847.md`
    -- via that source's basefile->path rule; the frontmatter basefile stays
    authoritative, the path is the source-scoped storage location."""
    out = {}
    for path in _wiki_dir(root, "commentary").rglob("*.md"):
        meta, _ = _read(str(path))
        out[str(meta["annotates"])] = str(path)
    return out


@functools.cache
def begrepp_index(root):
    """concept title -> path, over concept/*.md, keyed on `title:`."""
    out = {}
    for path in glob.glob(str(_wiki_dir(root, "concept") / "*.md")):
        meta, _ = _read(path)
        out[meta["title"]] = path
    return out
