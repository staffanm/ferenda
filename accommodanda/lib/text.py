"""One definition of "the plain text behind an artifact's inline-run structure",
shared by the search indexer (full document and per-fragment text), the MCP
pinpoint reader, the renderer and the bulk dumps.

An artifact's text lives in two leaf carriers: a node's ``text`` -- a list of
runs, each a plain ``str`` or a ``{"uri","text",...}`` link dict -- and a table
``rad``'s ``cells`` (a list of cells, each itself a runs list). The body-bearing
sections differ per source: SFS ``structure`` (+ each amendment's ``content``),
DV / förarbete / eurlex ``body``. Everything here is pure.
"""

import re

# the top-level sections that carry renderable body text, across all sources
# "footnotes" is presented body text like the rest: it renders at the foot of
# the page, so the reader sees it, the index must store it and the link walk must
# read it. Leaving it out cost every citation a document keeps in its notes --
# which for an IMY-beslut is the one that *identifies* the vägledning its prose
# names, and for a court decision the whole apparatus DV has printed as endnotes
# since 2023.
BODY_SECTIONS = ("structure", "body", "footnotes")

# tokens a Swedish sentence never ends on -- abbreviations whose trailing dot is
# not a full stop. Moved here from `labels._first_sentence` when a second caller
# (remisser ai-analyze, splitting an answer into the units a reworded quote is
# matched back against) needed the same boundary rule
# (rule:second-use-goes-to-lib).
NON_TERMINAL = frozenset({
    "bl.a", "ca", "dnr", "dvs", "e.d", "etc", "fr.o.m", "jfr", "kap", "kr",
    "m.fl", "m.m", "milj", "nr", "p.g.a", "s.k", "t.ex", "t.o.m"})

_SENTENCE_BOUNDARY = re.compile(r"[.!?](?=\s|$)")
# a colon or dash before a capital or a list marker ends a *clause* that a reader
# would quote on its own: "CKS avstyrker därför X av följande skäl: – Utredningens
# egna data ger inte stöd för ...". Splitting there makes the reason addressable
# without the verdict in front of it -- the one sub-sentence trim the old
# free-form quoting used well. Opt-in: `labels._first_sentence` wants whole
# sentences, and a title cut at its first colon would lose its subject.
_CLAUSE_BOUNDARY = re.compile(r"[:;](?=\s+[-–—•]?\s*[A-ZÅÄÖ])|(?<=\s)[–—](?=\s)")


def _is_boundary(text, end):
    """Whether the terminator ending at `end` closes a sentence: not when the
    word before it is a known abbreviation, an initial, a bare number, or itself
    contains a dot ("3 kap.", "J.A.", "2026:20")."""
    tail = ((text[:end].rsplit(None, 1) or [""])[-1].lower().lstrip("(\"'”„"))
    return not (tail in NON_TERMINAL or len(tail) <= 1 or "." in tail
                or tail.isdigit())


def _emit(out, chunk):
    """Append `chunk` as a unit unless it carries no letters. A clause break can
    leave the bullet or dash that introduced the clause stranded on its own, and
    a unit a reader could not quote is worse than no unit -- it takes a number
    and returns punctuation."""
    chunk = chunk.strip()
    if chunk and re.search(r"[^\W\d_]", chunk):
        out.append(chunk)


def sentences(text, clause_breaks=False):
    """`text` split into sentences, Swedish-abbreviation-aware. Text with no
    terminator at all is one sentence, which is what makes a bare list item
    ("Tillstyrks") a unit like any other rather than being swallowed by its
    neighbour; text carrying no letters at all ("123. 456.") yields nothing,
    since a unit a reader could not quote is worse than no unit. The split is
    *stable* -- callers that match a model's quote back against these units must
    use this same function, with the same flags, on both sides.

    `clause_breaks` additionally ends a unit at a colon, semicolon or dash that
    introduces one, so a verdict and the reason after it are separately
    quotable."""
    bounds = sorted(
        [m.start() for m in _SENTENCE_BOUNDARY.finditer(text)
         if _is_boundary(text, m.start())]
        + ([m.start() for m in _CLAUSE_BOUNDARY.finditer(text)]
           if clause_breaks else []))
    out, start = [], 0
    for b in bounds:
        _emit(out, text[start:b + 1])
        start = b + 1
    _emit(out, text[start:])
    return out


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


def drop_prefix(runs, n):
    """`runs` with the first `n` characters of its flattened text removed.

    A heading's own number is not reliably one run: Formex sets "Artikel 6" and
    "b" as siblings, and a förarbete's number is often a styled run of its own.
    A caller that locates the number in the flattened text therefore has to cut
    by character offset rather than by run index -- and keep the links in the
    rest of the runs intact, which is what this returns."""
    out = []
    for run in runs:
        text = run if isinstance(run, str) else run.get("text", "")
        if n >= len(text):
            n -= len(text)
            continue
        rest = text[n:]
        n = 0
        out.append(rest if isinstance(run, str) else dict(run, text=rest))
    return out


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


def id_nodes(node):
    """Every id-bearing node in a body subtree, in document order.

    The one walk behind `fragment_ids`, `fragment_texts` and `fragment_text`
    (rule:second-use-goes-to-lib). It descends *every* value, not just
    ``children``: a walker that followed only children would miss the ids the
    search index does find, and one that read ``structure`` directly would read
    a consolidated statute's superseded base text instead of the lydelse
    actually shown. That invariant now has one place to be forgotten rather
    than three."""
    if isinstance(node, dict):
        if node.get("id"):
            yield node
        for value in node.values():
            yield from id_nodes(value)
    elif isinstance(node, list):
        for item in node:
            yield from id_nodes(item)


def _body_id_nodes(art):
    for nodes in body_sections(art):
        yield from id_nodes(nodes)


def fragment_ids(art):
    """Every minted element id in the document's presented body (K1P2, K1P2S1,
    K1P2S1N4, …) -- the id vocabulary an authored layer's pinpoint is checked
    against (forarbete.genomforande.resolve).

    Shares `body_sections` and the `id_nodes` walk with `fragment_texts` and
    `fragment_text`; that walk's docstring states the invariant."""
    return {node["id"] for node in _body_id_nodes(art)}


# Node types whose own ``text`` runs are a HEADING rather than body text, so a
# fragment of that type can be named by the words the document itself prints
# over it. Measured over 25 artifacts per source: `avsnitt` (förarbete, 962 of
# 962 nodes carry one), `sektion` (kommentar), `rubrik` (SFS) and `heading`
# (eurlex annexes: "BILAGA I") print a heading; `stycke`, `punkt` and eurlex
# `paragraph` print body text, and `paragraf`/`kapitel` print nothing at all.
# `artikel`/`article` do print one, and stay out: their anchor already yields a
# citation ("artikel 47") through lib/pinpoint, which is shorter and is what a
# reader cites.
HEADING_TYPES = frozenset({"avsnitt", "sektion", "rubrik", "heading"})


def fragment_texts_and_headings(art):
    """``(fragment-uri, full text, own heading)`` for every id-bearing node in
    the body. The heading is '' for a node type that prints none (see
    HEADING_TYPES) -- it is what names the fragment where its anchor has no
    citation grammar, as a förarbete's "sec745" has none."""
    return [(art["uri"] + "#" + node["id"], node_text(node),
             runs_text(node.get("text") or []).strip()
             if node.get("type") in HEADING_TYPES else "")
            for node in _body_id_nodes(art)]


def fragment_texts(art):
    """``(fragment-uri, full text)`` for every id-bearing node in the body --
    the per-fragment children of a parent search doc. A fragment's text includes
    its descendants', so a paragraph carries its own numbered points."""
    return [(uri, body) for uri, body, _ in fragment_texts_and_headings(art)]


def fragment_node(art, frag):
    """The one id-bearing node of the presented body with id `frag`, or None.

    The node-level base under `fragment_text` and the MCP pinpoint reader
    (which renders the node via mdtext.node_markdown): both answer for a
    single provision, and each wants a different rendering of the same
    subtree (rule:second-use-goes-to-lib)."""
    return next((node for node in _body_id_nodes(art)
                 if node["id"] == frag), None)


def fragment_text(art, frag):
    """The text of one id-bearing node, or '' when the presented body has no
    node with that id.

    The one-fragment twin of `fragment_texts`, which reads every node in the
    document: the search path resolves a citation to a single provision and
    wants that provision's words, and building Inkomstskattelagen's whole
    fragment map to return one of them is work the query waits on."""
    node = fragment_node(art, frag)
    return node_text(node) if node else ""
