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

from . import eu_structure

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


def _id_nodes(node):
    """Every id-bearing node in a body subtree, in document order.

    The one walk behind `fragment_ids`, `fragment_texts` and `fragment_text`
    (rule:second-use-goes-to-lib). It descends *every* value, not just
    ``children``: a walker that followed only children would miss the ids the
    search index does find, and one that read ``structure`` directly would read
    a consolidated statute's superseded base text instead of the lydelse
    actually shown. That invariant now has one place to be forgotten rather
    than three."""
    return (n for n in _nodes(node) if n.get("id"))


def _nodes(node):
    """Every dict in a body subtree, in document order, at any depth."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _nodes(value)
    elif isinstance(node, list):
        for item in node:
            yield from _nodes(item)


def body_id_nodes(art):
    """Every id-bearing node of the presented body, in document order -- the
    one walk behind `fragment_ids`, `fragment_texts`, `fragment_node` and the
    hierarchy's amendment dating (one walk, many ids)."""
    for nodes in body_sections(art):
        yield from _id_nodes(nodes)


def fragment_ids(art):
    """Every minted element id in the document's presented body (K1P2, K1P2S1,
    K1P2S1N4, …) -- the id vocabulary an authored layer's pinpoint is checked
    against (forarbete.genomforande.resolve).

    Shares `body_sections` and the `_id_nodes` walk with `fragment_texts` and
    `fragment_text`; that walk's docstring states the invariant."""
    return {node["id"] for node in body_id_nodes(art)}


def citable_anchors(art):
    """Every fragment a citation to this document can name, read off the
    presented body -- the set `lib.unitindex` publishes, so a client can tell
    "12 kap. 52 §" from a provision the statute does not have.

    Three grammars, all field-driven: a node's minted ``id`` (K1P2, a14.3.1); an
    EU act's sub-article anchors (`25.1`, `recital-83`), which the artifact does
    not stamp and `eu_structure.anchored_blocks` derives; and the printed page a
    node sits on (``page`` -> `sid39`, what "prop. 1997/98:45 s. 39" names). A
    page inside a bilaga that restarts its own count is left out: the renderer
    anchors it `bilaga…-sid…`, and no citation grammar produces that.

    What a renderer mints for navigation only -- an SFS change-act marker
    (`L2007:1419`), a generated heading anchor -- is not here: nothing cites
    it."""
    anchors = set()
    for section in body_sections(art):
        for node in _nodes(section):
            if node.get("id"):
                anchors.add(node["id"])
            if node.get("page") and not node.get("bilaga"):
                anchors.add("sid%d" % node["page"])
        if isinstance(section, list):
            anchors.update(anchor for anchor, _ in eu_structure.anchored_blocks(section))
    return anchors


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

# The same question asked for a *resolved pinpoint*, where `artikel`/`article`
# do belong: there the pinpoint stands on the row already ("artikel 6"), and the
# heading is the only thing that says what the article is about -- "Article 6 –
# Right to a fair trial" rather than a number the reader still has to look up.
PROVISION_HEADING_TYPES = HEADING_TYPES | {"artikel", "article"}


def fragment_texts_and_headings(art):
    """``(fragment-uri, full text, own heading)`` for every id-bearing node in
    the body. The heading is '' for a node type that prints none (see
    HEADING_TYPES) -- it is what names the fragment where its anchor has no
    citation grammar, as a förarbete's "sec745" has none."""
    return [(art["uri"] + "#" + node["id"], node_text(node),
             runs_text(node.get("text") or []).strip()
             if node.get("type") in HEADING_TYPES else "")
            for node in body_id_nodes(art)]


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
    return next((node for node in body_id_nodes(art)
                 if node["id"] == frag), None)


def provision_heading(art, frag):
    """The heading one id-bearing node prints over itself ("Right to a fair
    trial", "8.5.1 Samspelet mellan …"), or '' when its type prints none.

    A treaty article writes its own designation into that heading ("Article 6 –
    Right to a fair trial") where an EU act keeps designation and rubric apart
    (eurlex/parse_html._emit_structural_row), so the caller composing a name for
    the provision has to allow for both."""
    node = fragment_node(art, frag)
    return (runs_text(node.get("text") or []).strip()
            if node and node.get("type") in PROVISION_HEADING_TYPES else "")


def page_nodes(art):
    """``{page: [node, …]}``: the nodes each printed page carries, in document
    order. A node without a page of its own is on the page of the node before
    it. Each node comes without its children -- they follow as nodes of their
    own, and can sit on the next page -- and a node with no text of its own (a
    bare container) is left out. A bilaga that restarts its own count is left
    out whole: its pages are not the document's (see `citable_anchors`)."""
    pages = {}
    current = None
    for section in body_sections(art):
        stack = list(reversed(section if isinstance(section, list) else [section]))
        while stack:
            node = stack.pop()
            if not isinstance(node, dict):
                continue
            if node.get("bilaga"):
                current = None
                continue
            current = node.get("page") or current
            if current and node.get("text"):
                pages.setdefault(current, []).append(
                    {k: v for k, v in node.items() if k != "children"})
            stack.extend(reversed(node.get("children") or []))
    return pages


def eu_units(art):
    """``{anchor: [block, …]}``: every anchor an EU act's renderer mints
    (`25.1`, `25.1.a`, `9.2.S2`, `recital-83`) with the blocks it names, in
    document order. The flat block list keeps a paragraph apart from its points,
    so an anchor takes its own block and the blocks under it ("6.1" and its
    points "6.1.a" …) up to the next anchor that is not below it, or the next
    heading or article. A numbered paragraph's first-stycke alias (`9.2.S1`,
    `eu_structure.first_stycke`) names that one block."""
    anchors = eu_structure.Anchors()
    blocks = [(anchors.key(b.get("type"), b.get("num"), b.get("id"), b.get("depth")), b)
              for b in eu_structure.flatten(art.get("structure") or [])]
    units = {}
    for i, (key, block) in enumerate(blocks):
        if key is None or key in units:
            continue
        unit = [block]
        for k, b in blocks[i + 1:]:
            if k is not None and not k.startswith(key + "."):
                break
            if k is None and b.get("type") in ("heading", "article"):
                break
            unit.append(b)
        units[key] = unit
        alias = eu_structure.first_stycke(block.get("type"), block.get("num"), key)
        if alias:
            units.setdefault(alias, [block])
    return units


# the containers a court decision keeps text in that is not the deciding
# court's own reasoning: the föredragande's proposal and the separate opinions
_NOT_THE_COURT = frozenset({"betankande", "skiljaktig", "tillagg"})


def _numbered(node, number, container=None):
    """The first stycke numbered `number` under `node` in document order,
    outside `_NOT_THE_COURT`, and inside a `container` node when one is named."""
    if isinstance(node, list):
        return next((hit for n in node
                     if (hit := _numbered(n, number, container)) is not None), None)
    if not isinstance(node, dict) or node.get("type") in _NOT_THE_COURT:
        return None
    # a stycke with an id is reached by the id grammar: a treaty's A5P2 carries
    # ordinal "2" too, and "P2" must not find it
    if container is None and node.get("type") == "stycke" and not node.get("id") \
            and str(node.get("ordinal")) == number:
        return node
    inner = None if node.get("type") == container else container
    return next((hit for key, value in node.items() if key != "text"
                 if (hit := _numbered(value, number, inner)) is not None), None)


def _deciding(nodes):
    """The nodes that hold the deciding court's text, one per decision: the last
    ``instans`` of the list (or of each ``delmal`` in it), or the whole list when
    it has no instances at all -- a record parsed without them."""
    parts = [n for n in nodes if n.get("type") == "delmal"]
    if parts:
        return [d for part in parts for d in _deciding(part.get("children") or [])]
    instances = [n for n in nodes if n.get("type") == "instans"]
    return [instances[-1]] if instances else [nodes]


def case_paragraph(art, number):
    """The numbered paragraph `number` ("26") of the deciding court's own text
    -- what "NJA 2022 s. 522 p. 26" names -- or None.

    Every court in the record numbers its paragraphs from 1, and so do the
    föredragande and a dissent, so the number alone names nothing: the lookup
    reads the last instance only and skips `_NOT_THE_COURT`. A numbered list
    inside the reasoning is parsed as numbered stycken too ("1 vapnet har
    innehafts …" in NJA 2021 s. 341), and a domslut often numbers its items, so
    the first match inside a ``domskal`` wins over any other. A split case
    (delmål I, II) has one decision per part: a number found in more than one
    part names no single paragraph, and is None."""
    hits = [hit for scope in _deciding(art.get("structure") or art.get("body") or [])
            if (hit := _numbered(scope, number, "domskal")
                or _numbered(scope, number)) is not None]
    return hits[0] if len(hits) == 1 else None


_PAGE = re.compile(r"sid(\d+)")
_CASE_PARAGRAPH = re.compile(r"[Pp](\d+)")


def pinpoint_nodes(art, frag):
    """``(unit type, [node, …])``: the part of the presented body a pinpoint
    names, or None when the document has no such part. The unit type is the
    first node's own type, or "sida" for a page. Four grammars, tried in this
    order:

    * a node id (K12, K1P5S2, B1A6, the EU articles);
    * an EU anchor the artifact stamps no id on (`25.1`, `recital-83`), see
      `eu_units`;
    * a printed page (`sid12`, type "sida"), see `page_nodes`;
    * a court decision's numbered paragraph (`P26` or `p26`), see
      `case_paragraph`. A node id wins, so HUDOC's `P44` stays its own node.

    The one lookup behind /api/v1/document, /api/v1/card, the search pins and
    the MCP pinpoint reader (rule:second-use-goes-to-lib). The unit index reads
    thousands of anchors per document, so `unitindex.units_of` builds the id
    map, `page_nodes` and `eu_units` once and looks each anchor up in them.
    It publishes no case paragraphs: `citable_anchors` has none."""
    node = fragment_node(art, frag)
    if node:
        return node.get("type"), [node]
    blocks = eu_units(art).get(frag) if art.get("structure") else None
    if blocks:
        return blocks[0].get("type"), blocks
    m = _PAGE.fullmatch(frag)
    if m and (nodes := page_nodes(art).get(int(m[1]))):
        return "sida", nodes
    m = _CASE_PARAGRAPH.fullmatch(frag)
    if m and (node := case_paragraph(art, m[1])):
        return node.get("type"), [node]
    return None


def has_pages(art):
    """Whether any node of the presented body carries a printed page -- what
    tells "no page 12 in this document" from "this document has no page data"
    (older propositions have none)."""
    return any(node.get("page") for section in body_sections(art)
               for node in _nodes(section))


def anchor_text(art, frag):
    """The presented body text behind one pinpoint -- what the pinpoint says,
    as plain text: the nodes `pinpoint_nodes` finds, joined. '' when the
    document has no such part."""
    found = pinpoint_nodes(art, frag)
    return " ".join(node_text(n) for n in found[1]) if found else ""


def fragment_text(art, frag):
    """The text of one id-bearing node, or '' when the presented body has no
    node with that id.

    The one-fragment twin of `fragment_texts`, which reads every node in the
    document: the search path resolves a citation to a single provision and
    wants that provision's words, and building Inkomstskattelagen's whole
    fragment map to return one of them is work the query waits on."""
    node = fragment_node(art, frag)
    return node_text(node) if node else ""
