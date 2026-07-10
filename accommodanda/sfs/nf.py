"""Project a Forfattning tree to the golden-corpus normal form.

Fragment ids replicate the old pipeline's URI minting exactly, including
its quirks, since the golden corpus reflects them:

- ids are hierarchical prefix concatenations (B1 A2 U1 K3 P2 S1 N4 R5);
  avdelning/underavdelning prefixes are dropped whenever a kapitel prefix
  is present, and bilaga prefixes propagate into everything below.
- if a document has kapitel but fewer than two paragrafer numbered "1",
  paragraph numbering is continuous and the kapitel prefix is dropped
  from paragraf ids (P12 instead of K2P12).
- nodes without an ordinal of their own (rubrik, stycke, bilaga) are
  numbered by sibling position -- but position counting compares nodes by
  *content*, so a node whose content duplicates an earlier sibling gets
  the earlier node's number, which then collides...
- ...and a node whose fragment id collides with an already-minted one is
  suppressed: it and its entire subtree go id-less. The same applies to
  temporal variants (a kapitel/paragraf/rubrik/bilaga version not
  currently in force).

Övergångsbestämmelser are dropped from the structure section: the old
pipeline redistributes them into the amendment register, which the new
pipeline cannot build until the SFSR register data is parsed.

When a LagrumParser is supplied, every text node (stycke, listelement,
table cell, rubrik, upphävd placeholder) becomes a list of inline nodes:
plain `str` runs interleaved with `{"predicate", "uri", "text"}` link
objects, one per discovered reference, at its exact position in the text.
References resolve relative to the node's own fragment id (the nearest
identified ancestor's, for id-less nodes). This differs from the old
pipeline, which skipped headings/upphävd and emitted a separate flat
reference list instead of inlining.
"""

from dataclasses import dataclass
from datetime import datetime

from ..lib import lagrum, util
from ..lib.catalog import BASE
from . import begrepp
from . import register as register_mod
from .model import (
    Avdelning,
    Bilaga,
    Kapitel,
    Lista,
    Listelement,
    Overgangsbestammelse,
    Overgangsbestammelser,
    Paragraf,
    Rubrik,
    Stycke,
    Tabell,
    Underavdelning,
    UpphavdParagraf,
    UpphavtKapitel,
)


def to_normalform(doc, basefile, now=None, refparser=None,
                  suppress_temporal=True, register=None, sfst_header=None):
    proj = Projection(minter=IdMinter(continuous=is_continuous(doc),
                                      now=now or datetime.now(),
                                      suppress_temporal=suppress_temporal),
                      refparser=refparser)
    structure = project_children(doc.children, (), proj, "")
    amendments = (build_amendments(doc, register, basefile, proj, refparser)
                  if register is not None else [])
    metadata = (register_mod.build_metadata(sfst_header, register, basefile)
                if register is not None and sfst_header is not None
                else {"uri": None, "properties": {}, "secondary": {}})
    return {
        "uri": register_mod.amendment_uri(basefile, BASE),
        "metadata": metadata,
        "structure": structure,
        "amendments": amendments,
    }


def inline_links(inline, source):
    """The (source, predicate, uri, context) tuples of the reference links in
    one inline-list text value. `context` is the *full text* of the node the
    links sit in -- not just each linked span -- so a diff shows the whole
    clause every reference was read from: the enumeration "… 3 kap. 1, 3, 4
    eller 10 § … yttrandefrihetsgrundlagen" is what makes a bare "10 §" resolve
    to that law's chapter 3, and judging the link needs that context."""
    context = "".join(r if isinstance(r, str) else r["text"] for r in inline)
    return [(source, run["predicate"], run["uri"], context)
            for run in inline if isinstance(run, dict)]


def inline_references(nodes, frag=""):
    """Reconstruct the flat (source-fragment, predicate, uri, text) reference
    tuples the old pipeline emitted, from the links now inlined into the
    structure. Attribution mirrors the old scan exactly: every link in a
    stycke and its nested lists/tables is credited to the stycke's fragment;
    a top-level list's items to the enclosing structural fragment. Rubrik
    (headings), upphavd placeholders and top-level tables are excluded --
    the old pipeline never scanned them (the new one does)."""
    refs = []
    for node in nodes:
        kind = node["type"]
        if kind in ("rubrik", "upphavd", "tabell"):
            continue
        eff = node.get("id") or frag
        if kind == "stycke":
            refs += inline_links(node.get("text", []), eff)
            for child in node.get("children", []):
                if child["type"] == "punkt":
                    refs += inline_links(child.get("text", []), eff)
                elif child["type"] == "tabell":
                    for row in child["children"]:
                        for cell in row["cells"]:
                            refs += inline_links(cell, eff)
        elif kind == "lista":
            for item in node["children"]:
                refs += inline_links(item.get("text", []), frag)
        else:  # structural container: recurse, carrying its fragment down
            refs += inline_references(node.get("children", []), eff)
    return refs


def iter_overgangar(doc):
    """Every Overgangsbestammelse in document order. They sit inside
    Overgangsbestammelser containers, which the old pipeline allowed at top
    level or (deliberate deviation) nested in a kapitel."""
    out = []
    def walk(nodes):
        for node in nodes:
            if isinstance(node, Overgangsbestammelse):
                out.append(node)  # its children are stycken, not more OBs
            else:
                walk(getattr(node, "children", []))
    walk(doc.children)
    return out


def build_amendments(doc, register, basefile, proj, refparser):
    """One amendment entry per register row (base act first, then every
    change act), keyed by URI, joined with the matching övergångsbestämmelse
    content. Omfattning tuples resolve against the base law via a fresh
    parser so the structure parser's learned law names don't leak in."""
    omfattning = lagrum.LagrumParser(refparser.namedlaws, basefile)
    forarbeten = lagrum.LagrumParser(refparser.namedlaws, basefile,
                                     parse_types=[lagrum.FORARBETEN])
    entries, order = {}, []
    for act in register.acts:
        uri = register_mod.amendment_uri(act.sfsnr, BASE)
        entries[uri] = {
            "uri": uri,
            "properties": register_mod.amendment_properties(
                act, basefile, omfattning, BASE),
            "forarbeten": register_mod.parse_forarbeten(
                act.rows.get("Förarbeten", ""), forarbeten)}
        order.append(uri)
    for ob in iter_overgangar(doc):
        uri = register_mod.amendment_uri(ob.sfsnr, BASE)
        if uri not in entries:
            entries[uri] = {"uri": uri, "properties": {}, "forarbeten": []}
            order.append(uri)
        entries[uri].setdefault("content", []).append(
            project_overgangsbestammelse(ob, proj))
    return [entries[uri] for uri in order]


def project_overgangsbestammelse(ob, proj):
    pairs = (("L", ":".join(register_mod.sfs_slug(ob.sfsnr))),)
    node_id = proj.minter.mint(pairs, ob)
    return {"type": "overgangsbestammelse", "id": node_id,
            "children": project_children(
                ob.children, pairs if node_id else None, proj, node_id or "")}


@dataclass
class Projection:
    minter: "IdMinter"
    refparser: lagrum.LagrumParser | None = None

    def inline(self, text, context, live=True, subject_term=None):
        """Return `text` as a list of inline nodes: plain `str` runs and
        `{"predicate", "uri", "text"}` link objects, one per reference
        found, in document order. `context` is the node's own fragment,
        used for relative-reference resolution (the old _currenturl).
        Text without a refparser or a FILTER_LAW hit is a single run.

        `live` is False for content not in force at the projection date (a
        future/sunset temporal variant): such text carries no reference links.
        Its provision is id-suppressed, so any link would fall back to a bare
        ancestor fragment (a chapter), and it is not part of the consolidated
        citation graph anyway -- the old pipeline omitted it too.

        An empty `context` marks an *unanchored* provision: one whose id the
        minter suppressed (content-equality dedup) with no id-bearing ancestor
        either, so a self-reference would attribute to an empty source. The old
        pipeline omitted those, so self-links are dropped here; references to
        other laws still link.

        `subject_term` is a defined begreppsdefinition term found in this node;
        it becomes a dcterms:subject link over the term's span (kind "term")."""
        if not text:
            return []
        refs = []
        if live and self.refparser is not None and lagrum.FILTER_LAW.search(text):
            rp = self.refparser
            refs = rp.parse_text(text, context or None)
            if not context:
                selfuri = rp.self_law_uri
                refs = [r for r in refs
                        if r.uri != selfuri and not r.uri.startswith(selfuri + "#")]
        if subject_term and (idx := text.find(subject_term)) >= 0:
            # the term-use link yields to any citation it overlaps (a defined
            # term is often also a named-law/change-note reference on the same
            # span); interleave needs disjoint spans
            term = lagrum.Ref(idx, idx + len(subject_term), subject_term,
                              "dcterms:subject",
                              begrepp.term_to_subject(subject_term),
                              kind="term")
            refs += lagrum.yield_overlaps([term], refs)
        if not refs:
            return [text]
        return lagrum.interleave(text, refs)


# containers the old _count_elements recursed into (those carrying a
# fragment_label); notably Overgangsbestammelser, listor and tabeller were
# invisible to the count
COUNTED = (Avdelning, Underavdelning, Kapitel, Paragraf, Stycke, Bilaga)


def is_continuous(doc):
    """Continuous § numbering: the document has kapitel, but at most one
    counted paragraf is numbered '1'."""
    kapitel = 0
    ettor = 0
    stack = [c for c in doc.children if isinstance(c, COUNTED)]
    while stack:
        node = stack.pop()
        if isinstance(node, Kapitel):
            kapitel += 1
        elif isinstance(node, Paragraf) and node.ordinal == "1":
            ettor += 1
        stack.extend(c for c in getattr(node, "children", [])
                     if isinstance(c, COUNTED))
    return kapitel > 0 and ettor < 2


def ordfrag(ordinal):
    return (ordinal or "").replace(" ", "")


def in_effect(node, now):
    """A temporal node variant is in force unless its dates say otherwise
    (string dates like 'den dag regeringen bestämmer' never disqualify)."""
    upphor = getattr(node, "upphor", None)
    ikrafttrader = getattr(node, "ikrafttrader", None)
    return ((isinstance(upphor, datetime) and now < upphor) or
            (isinstance(ikrafttrader, datetime) and now > ikrafttrader) or
            (isinstance(upphor, (type(None), str)) and
             isinstance(ikrafttrader, (type(None), str))))


TEMPORAL = (Kapitel, Paragraf, Rubrik, Bilaga)


def _in_force(node, minter):
    """Whether `node` is in force at the projection date -- the same test the
    minter uses to suppress its id. Not-in-force content keeps its place in the
    structure but emits no references (see Projection.inline)."""
    return not (minter.suppress_temporal and isinstance(node, TEMPORAL)
                and not in_effect(node, minter.now))


def temporal_dates(doc):
    """All upphor/ikrafttrader dates in the document -- the candidate
    moments where id suppression flips."""
    dates = set()
    stack = list(doc.children)
    while stack:
        node = stack.pop()
        for attr in ("upphor", "ikrafttrader"):
            value = getattr(node, attr, None)
            if isinstance(value, datetime):
                dates.add(value)
        stack.extend(getattr(node, "children", []))
    return sorted(dates)


class IdMinter:
    def __init__(self, continuous, now, suppress_temporal=True):
        self.continuous = continuous
        self.now = now
        self.suppress_temporal = suppress_temporal
        self.minted = set()

    def mint(self, pairs, node):
        """pairs is the ordered (letter, ordinal-fragment) prefix chain,
        or None if an ancestor was suppressed. Returns a fragment id or
        None (suppressed)."""
        if pairs is None:
            return None
        letters = [letter for letter, frag in pairs]
        skipped = []
        for letter, frag in pairs:
            if letter in ("A", "U") and "K" in letters:
                continue
            if letter == "K" and "P" in letters and self.continuous:
                continue
            skipped.append((letter, frag))
        fragment = "".join(letter + frag for letter, frag in skipped)
        if fragment in self.minted:
            return None
        if (self.suppress_temporal and isinstance(node, TEMPORAL)
                and not in_effect(node, self.now)):
            return None
        self.minted.add(fragment)
        return fragment


def content_key(node):
    """Equality key replicating the old element model exactly (verified
    against ferenda.sources.legal.se.elements): OrdinalElement types
    compare by ordinal ONLY (a list item's text is ignored!), str-based
    types by text, list-based types elementwise -- attributes like a
    bilaga's rubrik never participate."""
    match node:
        case Rubrik():
            return node.text
        case Stycke():
            return (node.text,) + tuple(content_key(c) for c in node.children)
        case Lista():
            return tuple(content_key(c) for c in node.children)
        case Listelement() | Paragraf() | Kapitel() | Avdelning() \
                | Underavdelning():
            return ("OE", node.ordinal)
        case Bilaga():
            return tuple(content_key(c) for c in node.children)
        case Tabell():
            return tuple(tuple(row.cells) for row in node.rows)
        case UpphavtKapitel() | UpphavdParagraf():
            return node.text
        case _:
            return id(node)


def position_ordinal(node, siblings):
    """Sibling position of `node`, where iteration stops at the first
    sibling whose content equals `node` (the old == semantics)."""
    key = content_key(node)
    pos = 0
    for sibling in siblings:
        if type(sibling) is type(node):
            pos += 1
        if content_key(sibling) == key:
            break
    return str(pos)


def extend(pairs, letter, frag):
    return None if pairs is None else pairs + ((letter, frag),)


def project_children(children, pairs, proj, frag, live=True):
    out = []
    for node in children:
        match node:
            case Avdelning():
                sub = extend(pairs, "A", ordfrag(node.ordinal))
                node_id = proj.minter.mint(sub, node)
                ctx = node_id or frag
                kids = [rubrik_nf(node.rubrik, 1, proj, ctx, live=live)]
                if node.underrubrik:
                    kids.append(rubrik_nf(node.underrubrik, 2, proj, ctx, live=live))
                kids += project_children(node.children,
                                         sub if node_id else None, proj, ctx, live)
                out.append({"type": "avdelning", "id": node_id,
                            "ordinal": node.ordinal, "children": kids})
            case Underavdelning():
                sub = extend(pairs, "U", ordfrag(node.ordinal))
                node_id = proj.minter.mint(sub, node)
                ctx = node_id or frag
                kids = [rubrik_nf(node.rubrik, 1, proj, ctx, live=live)]
                kids += project_children(node.children,
                                         sub if node_id else None, proj, ctx, live)
                out.append({"type": "underavdelning", "id": node_id,
                            "children": kids})
            case Kapitel():
                sub = extend(pairs, "K", ordfrag(node.ordinal))
                node_id = proj.minter.mint(sub, node)
                ctx = node_id or frag
                clive = live and _in_force(node, proj.minter)
                kids = [rubrik_nf(node.rubrik, 1, proj, ctx, live=clive)]
                kids += project_children(node.children,
                                         sub if node_id else None, proj, ctx, clive)
                out.append({"type": "kapitel", "id": node_id,
                            "ordinal": node.ordinal, "children": kids})
            case UpphavtKapitel() | UpphavdParagraf():
                out.append({"type": "upphavd",
                            "text": proj.inline(
                                util.normalize_space(node.text), frag, live)})
            case Paragraf():
                sub = extend(pairs, "P", ordfrag(node.ordinal))
                node_id = proj.minter.mint(sub, node)
                plive = live and _in_force(node, proj.minter)
                out.append({"type": "paragraf", "id": node_id,
                            "ordinal": node.ordinal,
                            "children": project_paragraf(
                                node, sub if node_id else None, proj,
                                node_id or frag, plive)})
            case Rubrik():
                sub = extend(pairs, "R", position_ordinal(node, children))
                node_id = proj.minter.mint(sub, node)
                out.append(rubrik_nf(node.text,
                                     3 if node.underrubrik else 2,
                                     proj, node_id or frag, id=node_id,
                                     live=live and _in_force(node, proj.minter)))
            case Stycke():
                sub = extend(pairs, "S", position_ordinal(node, children))
                out.append(stycke_nf(node, sub, proj, frag, live))
            case Lista():
                out.append({"type": "lista", "id": None,
                            "children": flatten_list(node, pairs, proj, frag, live)})
            case Tabell():
                out.append(tabell_nf(node, proj, frag, live))
            case Bilaga():
                sub = extend(pairs, "B", position_ordinal(node, children))
                node_id = proj.minter.mint(sub, node)
                ctx = node_id or frag
                blive = live and _in_force(node, proj.minter)
                kids = [rubrik_nf(node.rubrik, 1, proj, ctx, live=blive)]
                kids += project_children(node.children,
                                         sub if node_id else None, proj, ctx, blive)
                out.append({"type": "bilaga", "id": node_id, "children": kids})
            case Overgangsbestammelser():
                pass  # redistributed into the amendment register downstream
    return out


def project_paragraf(paragraf, pairs, proj, frag, live=True):
    mode = begrepp.paragraf_mode([getattr(s, "text", "") or ""
                                  for s in paragraf.children])
    out = []
    for node in paragraf.children:
        sub = extend(pairs, "S", position_ordinal(node, paragraf.children))
        nf = stycke_nf(node, sub, proj, frag, live, mode=mode)
        if node is paragraf.children[0]:
            nf["beteckning"] = beteckning(paragraf)
        out.append(nf)
    return out


def beteckning(paragraf):
    b = paragraf.ordinal + " \xa7"
    if paragraf.moment:
        b += " " + paragraf.moment + " mom."
    return b


def stycke_nf(stycke, pairs, proj, frag, live=True, mode=None):
    node_id = proj.minter.mint(pairs, stycke)
    eff = node_id or frag
    text = util.normalize_space(stycke.text)
    term = begrepp.defined_term(text, mode, "stycke") if mode else None
    nf = {"type": "stycke", "id": node_id,
          "text": proj.inline(text, eff, live, subject_term=term)}
    # the old find_definitions stops recursing once a term is found in a subtree
    submode = None if term else mode
    items = []
    for child in stycke.children:
        if isinstance(child, Lista):
            items.extend(flatten_list(child, pairs if node_id else None,
                                      proj, eff, live, mode=submode))
        elif isinstance(child, Tabell):
            items.append(tabell_nf(child, proj, eff, live, mode=submode))
    if items:
        nf["children"] = items
    return nf


def flatten_list(lista, pairs, proj, frag, live=True, mode=None):
    """Golden normal form flattens nested lists into document order.
    References in each item resolve against the item's own id."""
    out = []
    for item in lista.children:
        sub = extend(pairs, "N", ordfrag(item.ordinal))
        item_id = proj.minter.mint(sub, item)
        eff = item_id or frag
        text = util.normalize_space(item.text)
        term = begrepp.defined_term(text, mode, "listelement") if mode else None
        out.append({"type": "punkt", "id": item_id, "ordinal": item.ordinal,
                    "text": proj.inline(text, eff, live, subject_term=term)})
        submode = None if term else mode
        for sublist in item.children:
            out.extend(flatten_list(sublist, sub if item_id else None,
                                    proj, eff, live, mode=submode))
    return out


def tabell_nf(tabell, proj, context, live=True, mode=None):
    rows = []
    for row in tabell.rows:
        # only the first cell of a row can name a term
        term = (begrepp.defined_term(util.normalize_space(row.cells[0]),
                                     mode, "tabellrad")
                if mode and row.cells else None)
        cells = [proj.inline(util.normalize_space(cell), context, live,
                             subject_term=(term if i == 0 else None))
                 for i, cell in enumerate(row.cells)]
        rows.append({"type": "rad", "cells": cells})
    return {"type": "tabell", "id": None, "children": rows}


def rubrik_nf(text, level, proj, context, id=None, live=True):
    return {"type": "rubrik", "id": id, "level": level,
            "text": proj.inline(util.normalize_space(text or ""), context, live)}
