"""The shared body projection: block streams -> artifact ``structure`` nodes.

Several sources parse their document into a flat stream of rubrik/stycke blocks
and project that stream onto the shared artifact node convention (a ``structure``
list of ``{"type": …, "text": …}`` dicts the catalog, renderer and search walk
generically). The projection is the same walk every time, and it was written out
five times before this module existed (rule:second-use-goes-to-lib).

Two shapes, because two kinds of source produce two kinds of block stream:

  * :func:`scanned_nodes` -- the citation-scanned form (avg, rs, edpb). Every
    block's text goes through the source's own `lagrum` scanner, so the runs
    carry the links that put the document on the rail of the paragraf it
    interprets; a stycke anchors on its position in the document.
  * :func:`numbered_nodes` -- the plain form (hudoc, icc, icj). The court numbers its
    own paragraphs, so a stycke anchors on *that* number where it has one, and
    the text is a single unscanned run.

Both emit exactly one node per block, in order, so a source that has a key of
its own to add (edpb's ``punkt``, hudoc's note class) zips its blocks against
the returned nodes and writes that key at the call site, where the drift is
visible.

A third projection, :func:`provision_nodes`, serves the treaty corpora (icrc,
untc): their parsers read whole *provisions* rather than a block stream, so an
article arrives with its paragraphs already gathered.

The rest of the module is the small machinery every artifact producer repeats:
:func:`prune` (the optional-key rule the on-disk shape follows), :func:`flatten`
and :func:`nest_by_level` (the document-order and the tree view of a nested
``structure``), and :class:`Fotnot`.

Everything here is pure: blocks in, plain dicts out.
"""

from dataclasses import dataclass, field

from .lagrum import interleave


def unique_id(base, ids):
    """`base`, suffixed to stay unique within `ids` (a caller-owned counter dict
    mutated in place): the second ``P12`` becomes ``P12-2``. A document that
    numbers a paragraph twice -- a court reprinting a numbered passage, a treaty
    whose annex restarts at Article 1 -- must still mint one anchor per node, or
    the later one is unreachable and the earlier one ambiguous."""
    ids[base] = ids.get(base, 0) + 1
    return base if ids[base] == 1 else "%s-%d" % (base, ids[base])


def prune(d):
    """`d` without the keys whose value says nothing -- ``None``, ``""``, ``[]``,
    ``{}``. This is the on-disk shape every source's ``to_artifact`` writes: an
    optional key appears only where the document has it, so a document's JSON
    says what that document has and a diff between two builds stays readable.
    Written eight times as an ``if self.x: art["x"] = self.x`` chain and once as
    this comprehension before it moved here (rule:second-use-goes-to-lib).

    ``0`` and ``False`` are values a document *has* (``statesParties: 0``,
    ``historical: False``) and stay."""
    return {k: v for k, v in d.items() if v not in (None, "", [], {})}


def _serial_anchor(_block, serial):
    return "S%d" % serial


def scanned_nodes(blocks, scanner, anchor=_serial_anchor):
    """`blocks` (kind/text/level) as citation-scanned structure nodes: a rubrik
    keeps its nesting level, a stycke gets an anchor from `anchor(block, serial)`
    -- the running stycke count, unless the source's own numbering names a
    better one.

    A ``tabell`` block carries `rows` (a list of rows, each a list of cell
    strings) instead of text, and projects onto the corpus-wide table node the
    renderer already draws -- the same ``tabell``/``rad``/``cells`` shape
    `forarbete.parse` emits. Every cell is scanned like any other text, so a
    lagrum named inside a table still links. `th` marks a header row where the
    source published one."""
    out, serial = [], 0
    for block in blocks:
        if block.kind == "tabell":
            out.append({"type": "tabell", "children": [
                {"type": "rad",
                 "cells": [interleave(cell, scanner.parse_text(cell, context={}))
                           for cell in row]}
                | ({"th": True} if block.th and i == 0 else {})
                for i, row in enumerate(block.rows)]})
            continue
        runs = interleave(block.text, scanner.parse_text(block.text, context={}))
        if block.kind == "rubrik":
            out.append({"type": "rubrik", "level": block.level, "text": runs})
            continue
        serial += 1
        out.append({"type": "stycke", "id": anchor(block, serial), "text": runs})
    return out


@dataclass
class Block:
    """One block of a parsed body, the input :func:`numbered_nodes` projects.
    Shared verbatim by hudoc, icc and icj -- three sources whose parser emits
    this exact stream and whose only consumer of it is that projection
    (rule:second-use-goes-to-lib). A kind other than ``rubrik`` projects onto a
    stycke, which is how hudoc carries its ``note`` blocks through and tags them
    at the call site."""
    kind: str                    # rubrik | stycke (hudoc also: note)
    text: str
    level: int = 1
    number: str | None = None


def numbered_nodes(blocks, refs_for=None):
    """`blocks` (kind/text/level/number) as structure nodes anchored on the
    document's own paragraph numbers: ``P42`` where the block printed one (kept
    as ``ordinal`` too, so the renderer can set it in the gutter), the running
    stycke count otherwise. These are the English-language international
    sources, whose prose the Swedish citation grammar has nothing to say about
    -- so text is one unscanned run, unless the source hands in its own
    `refs_for` (text -> [lagrum.Ref]) for the citations it *can* resolve
    (treaty articles, sibling filings), and the runs then carry those links."""
    def runs(text):
        return interleave(text, refs_for(text)) if refs_for else [text]
    out, serial, ids = [], 0, {}
    for block in blocks:
        if block.kind == "rubrik":
            out.append({"type": "rubrik", "level": block.level,
                        "text": runs(block.text)})
            continue
        serial += 1
        node = {"type": "stycke", "text": runs(block.text),
                "id": unique_id("P%s" % block.number if block.number
                                else "S%d" % serial, ids)}
        if block.number:
            node["ordinal"] = block.number
        out.append(node)
    return out


@dataclass
class Fotnot:
    """A note set below the running text. `mark` is the marker the document
    printed (``""`` where it printed none); `text` is the note body,
    citation-linked downstream like any other text.

    Worth carrying because of what these notes hold: an agency names a
    vägledning in prose ("Europeiska dataskyddsstyrelsens riktlinjer om
    samtycke") and grounds it with the number in the note below ("Riktlinjer
    05/2020"). Discard the notes and the decision cites nothing a citation scan
    can resolve -- which is exactly what happened: 43 of the 83 IMY-beslut that
    name this guidance carry its number, and not one of those numbers reached
    the artifact.

    Shared by avg, rs and guidance, which had one byte-identical copy each
    (rule:second-use-goes-to-lib). dv keeps its own: its footnotes are numbered
    (``num``), not marked, and take a different route to the artifact."""
    mark: str
    text: str


def footnote_nodes(notes, scanner):
    """Footnotes -- :class:`Fotnot`s, or ``(mark, text)`` pairs -- as the
    artifact's footnote nodes, citation-scanned like the body: an agency grounds
    in a note the vägledning its prose only names, so a dropped note costs the
    document exactly the reference a scan could resolve.

    Both shapes, because both are what the callers hold: the two extractors in
    `lib.pdftext` (`letterhead_footnotes`, `ruled_footnotes`) return pairs, and a
    source that keeps its notes typed passes its `fotnoter` straight in."""
    return [{"mark": mark,
             "text": interleave(text, scanner.parse_text(text, context={}))}
            for mark, text in ((n.mark, n.text) if isinstance(n, Fotnot) else n
                               for n in notes)]


@dataclass
class Provision:
    """One provision of a treaty, as :func:`provision_nodes` reads it: an
    article (or annex, or preamble) with its body already split into stycken.
    The treaty corpora each keep their own richer provision type -- icrc records
    the raw ICRC section, untc derives its ordinal from the fragment -- and
    adapt to this one at the projection, so the shared projection needs no
    knowledge of either source."""
    heading: str                    # "Article 1 - Respect for the Convention"
    fragment: str | None = None     # stable id: A1, Annex1, Testimonium
    ordinal: str | None = None      # the article number, where it has one
    paragraphs: list[str] = field(default_factory=list)
    kind: str = "artikel"           # "artikel" | "rubrik" (a division heading)


def provision_nodes(provisions, refs_for=None):
    """`provisions` as artifact structure nodes: an ``artikel`` anchored on its
    fragment over one ``stycke`` per paragraph (``A5``, ``A5S1``, ``A5S2``), a
    ``rubrik`` for a division heading. Both treaty corpora mint this exact shape,
    so a citation into a treaty anchors the same way whichever corpus holds it
    (rule:second-use-goes-to-lib).

    Anchors run through :func:`unique_id`, because a treaty numbers Article 1
    more than once: an annex restarts at 1, and a duplicate anchor leaves the
    later article unreachable and the earlier one ambiguous. `refs_for`
    (text -> [lagrum.Ref]) links the citations a source can resolve; without it
    a paragraph is one unscanned run."""
    def runs(text):
        return interleave(text, refs_for(text)) if refs_for else [text]
    out, ids = [], {}
    for provision in provisions:
        if provision.kind == "rubrik":
            out.append({"type": "rubrik", "level": 1, "text": [provision.heading]})
            continue
        anchor = unique_id(provision.fragment or "Preamble", ids)
        node = {"type": "artikel", "id": anchor, "text": [provision.heading],
                "children": [{"type": "stycke", "id": "%sS%d" % (anchor, i),
                              "text": runs(paragraph)}
                             for i, paragraph in enumerate(provision.paragraphs, 1)]}
        if provision.ordinal:
            node["ordinal"] = provision.ordinal
        out.append(node)
    return out


def flatten(structure, *, containers, marker=None):
    """The document-order flat block list of a nested `structure`: a node whose
    ``type`` is in `containers` becomes `marker(node)` -- the block it was built
    from -- followed by its flattened children, and every other node passes
    through as it is. Without a `marker` the container is transparent and only
    its children are emitted.

    The inverse of each source's own `nest`, and written once per source before
    it moved here (rule:second-use-goes-to-lib): förarbete turns an ``avsnitt``
    back into its ``rubrik``, and dv's structural wrappers (instans/dom/domskäl)
    carry no block of their own, so they hoist their prose children.
    `foreskrift.structure` still keeps a third copy, which turns a
    ``kapitel``/``paragraf`` back into its marker block."""
    out = []
    for node in structure:
        if node.get("type") in containers:
            if marker:
                out.append(marker(node))
            out.extend(flatten(node["children"], containers=containers,
                               marker=marker))
        else:
            out.append(node)
    return out


def nest_by_level(blocks, level_of, make_node, leaf=None):
    """`blocks` -> a nested node list, by the level each container states:
    `level_of(block)` gives the level of a block that opens a container (None for
    a block that does not), `make_node(block)` builds the container node, and the
    container attaches under the nearest open container of *lower* level. A block
    that opens nothing goes to `leaf(block, children)`, which places it among the
    open container's `children` -- by default, unchanged and in order.

    Two sources rebuild a tree this way from a flat stream that kept the levels:
    a förarbete's numbered outline (14 -> 14.3 -> 14.3.4) and an EU act's
    divisions (parts > titles > chapters > sections). Their pop-attach-push was
    the same five lines twice (rule:second-use-goes-to-lib); what differs is what
    a container is made of and where the leaves go, which is what the two
    callbacks carry."""
    root, stack = [], []            # stack: the open (level, node) containers

    def children():
        return stack[-1][1]["children"] if stack else root

    for block in blocks:
        level = level_of(block)
        if level is None:
            if leaf:
                leaf(block, children())
            else:
                children().append(block)
            continue
        while stack and stack[-1][0] >= level:
            stack.pop()
        node = make_node(block)
        children().append(node)
        stack.append((level, node))
    return root
