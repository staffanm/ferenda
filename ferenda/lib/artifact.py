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
  * :func:`numbered_nodes` -- the plain form (hudoc, icc). The court numbers its
    own paragraphs, so a stycke anchors on *that* number where it has one, and
    the text is a single unscanned run.

Both emit exactly one node per block, in order, so a source that has a key of
its own to add (edpb's ``punkt``, hudoc's note class) zips its blocks against
the returned nodes and writes that key at the call site, where the drift is
visible.

Everything here is pure: blocks in, plain dicts out.
"""

from .lagrum import interleave


def unique_id(base, ids):
    """`base`, suffixed to stay unique within `ids` (a caller-owned counter dict
    mutated in place): the second ``P12`` becomes ``P12-2``. A document that
    numbers a paragraph twice -- a court reprinting a numbered passage, a treaty
    whose annex restarts at Article 1 -- must still mint one anchor per node, or
    the later one is unreachable and the earlier one ambiguous."""
    ids[base] = ids.get(base, 0) + 1
    return base if ids[base] == 1 else "%s-%d" % (base, ids[base])


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


def footnote_nodes(notes, scanner):
    """``[(mark, text)]`` -> the artifact's footnote nodes, citation-scanned like
    the body: an agency grounds in a note the vägledning its prose only names, so
    a dropped note costs the document exactly the reference a scan could resolve.

    Pairs rather than a source's own Fotnot, because that is what both extractors
    in `lib.pdftext` return (`letterhead_footnotes`, `ruled_footnotes`) and a
    source that keeps its notes typed unwraps them in one comprehension."""
    return [{"mark": mark,
             "text": interleave(text, scanner.parse_text(text, context={}))}
            for mark, text in notes]
