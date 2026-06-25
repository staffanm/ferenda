"""One definition of "the plain text behind an artifact's inline-run structure",
shared by the catalog (link-tooltip snippets), the search indexer (full document
and per-fragment text) and the bulk dumps.

An artifact's text lives in two leaf carriers: a node's ``text`` -- a list of
runs, each a plain ``str`` or a ``{"uri","text",...}`` link dict -- and a table
``rad``'s ``cells`` (a list of cells, each itself a runs list). The body-bearing
sections differ per source: SFS ``structure`` (+ each amendment's ``content``),
DV / förarbete / eurlex ``body``. Everything here is pure.
"""

# the top-level sections that carry renderable body text, across all sources
BODY_SECTIONS = ("structure", "body")


def runs_text(runs):
    """Flatten an inline-run list (str runs + link dicts) to plain text."""
    if isinstance(runs, str):
        return runs
    return "".join(r if isinstance(r, str) else r.get("text", "") for r in runs)


def _collect_text(node, parts):
    """Append every node's runs and table cells, in document order. A node's own
    ``text``/``cells`` come before its descendants (walked via the other keys)."""
    if isinstance(node, dict):
        if "text" in node:
            parts.append(runs_text(node["text"]))
        for cell in node.get("cells", []):
            parts.append(runs_text(cell))
        for key, value in node.items():
            if key not in ("text", "cells"):
                _collect_text(value, parts)
    elif isinstance(node, list):
        for item in node:
            _collect_text(item, parts)


def node_text(node):
    """The full plain text of a node: its own runs and table cells plus every
    descendant's, in document order, whitespace-collapsed. No truncation."""
    parts = []
    _collect_text(node, parts)
    return " ".join(p for p in parts if p).strip()


def document_text(art):
    """The whole document's plain text -- every body-bearing section plus the
    amendments' content concatenated -- for a parent search doc."""
    parts = []
    for section in BODY_SECTIONS:
        _collect_text(art.get(section), parts)
    for amendment in art.get("amendments", []):
        _collect_text(amendment.get("content"), parts)
    return " ".join(p for p in parts if p).strip()


def _collect_fragment_texts(node, doc_uri, out):
    if isinstance(node, dict):
        if node.get("id"):
            out.append((doc_uri + "#" + node["id"], node_text(node)))
        for value in node.values():
            _collect_fragment_texts(value, doc_uri, out)
    elif isinstance(node, list):
        for item in node:
            _collect_fragment_texts(item, doc_uri, out)


def fragment_texts(art):
    """``(fragment-uri, full text)`` for every id-bearing node in the body --
    the per-fragment children of a parent search doc. A fragment's text includes
    its descendants', so a paragraph carries its own numbered points."""
    out = []
    for section in BODY_SECTIONS:
        _collect_fragment_texts(art.get(section), art["uri"], out)
    return out
