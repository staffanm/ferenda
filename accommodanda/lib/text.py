"""One definition of "the plain text behind an artifact's inline-run structure",
shared by the search indexer (full document and per-fragment text), the MCP
pinpoint reader, the renderer and the bulk dumps.

An artifact's text lives in two leaf carriers: a node's ``text`` -- a list of
runs, each a plain ``str`` or a ``{"uri","text",...}`` link dict -- and a table
``rad``'s ``cells`` (a list of cells, each itself a runs list). The body-bearing
sections differ per source: SFS ``structure`` (+ each amendment's ``content``),
DV / förarbete / eurlex ``body``. Everything here is pure.
"""

# the top-level sections that carry renderable body text, across all sources
BODY_SECTIONS = ("structure", "body")


def _tom_key(cons):
    """Chronological sort key for a consolidation: its cutoff amendment's
    year:number parsed from the ``konsolideradTom`` uri; an unpinned
    consolidation (tom None/unreadable) sorts first."""
    tom = cons.get("konsolideradTom") or ""
    year, _, nr = tom.rpartition("/")[2].partition(":")
    return (int(year), int(nr)) if year.isdigit() and nr.isdigit() else (0, 0)


def presented_consolidation(art):
    """The consolidation an artifact presents as its reading text: the latest
    (by ``konsolideradTom``) of its parsed consolidated versions, or None when
    no consolidation carries a parsed structure. A konsoliderad version is the
    base text with its amendments folded in, so where one exists it is the
    current-law text the page shows, the search index stores and the citation
    walk reads -- the as-enacted base then stays reachable as the ``/grund``
    page. Field-driven: any source whose artifacts store a ``consolidations``
    array contributes; everyone else returns None."""
    parsed = [c for c in art.get("consolidations") or [] if c.get("structure")]
    return max(parsed, key=_tom_key) if parsed else None


def body_sections(art):
    """The node-lists that carry the document's *presented* body text, in
    order -- what the reader sees, the index stores and the link walk reads.
    A presented consolidation replaces the base ``structure`` (their §§ mint
    the same fragment ids, so walking both would double every anchor and
    index superseded text beside its replacement); otherwise the generic
    sections."""
    cons = presented_consolidation(art)
    if cons:
        return [cons["structure"]]
    return [art.get(section) for section in BODY_SECTIONS]


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
    for nodes in body_sections(art):
        _collect_text(nodes, parts)
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
    for nodes in body_sections(art):
        _collect_fragment_texts(nodes, art["uri"], out)
    return out
