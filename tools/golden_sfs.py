"""Golden-corpus comparator for SFS documents.

Reduces a parsed SFS document (the old pipeline's XHTML+RDFa output) to a
canonical JSON normal form, and diffs two normal forms. The normal form is
the regression contract for the new pipeline: a port of the SFS parser is
correct for a document when its normalized output matches the frozen one.

The normal form has four sections:

  metadata    -- document-level properties from <head> (multi-valued
                 properties as sorted lists)
  structure   -- the body outline: kapitel/rubrik/paragraf/stycke/punkt
                 nodes with ordinals and whitespace-normalized text
  references  -- sorted (from_fragment, predicate, to_uri) tuples for every
                 inline link (dcterms:references, dcterms:subject)
  amendments  -- the change register: one entry per amending law with
                 förarbeten, ikraftträdande and affected provisions

Usage:
  golden_sfs.py normalize PARSED.xhtml          # normal form to stdout
  golden_sfs.py compare A B                     # A/B are .xhtml or .json
"""

import argparse
import ast
import json
import re
import sys
from pathlib import Path

from lxml import etree

from accommodanda.sfs import graphics as sfs_graphics

XHTML = "http://www.w3.org/1999/xhtml"

# RDFa attributes use prefixed names (dcterms:references etc.); the prefix
# map is fixed across the corpus so we treat the qnames as opaque strings.

# structural container types observed across the full corpus (census 2026-06)
STRUCTURE_TYPES = {
    "rinfoex:Avdelning": "avdelning",
    "rpubl:Kapitel": "kapitel",
    "rpubl:Paragraf": "paragraf",
    "rinfoex:Bilaga": "bilaga",
}
STRUCTURE_CLASSES = {
    "underavdelning": "underavdelning",
    "overgangsbestammelse": "overgangsbestammelse",
}

# Head/register properties that legitimately repeat and must compare as sets
MULTIVALUED = {
    "rpubl:konsolideringsunderlag",
    "rpubl:forarbete",
    "rpubl:inforsI",
    "rpubl:ersatter",
    "rpubl:upphaver",
    "owl:sameAs",
    "rdf:type",
}

# Volatile bookkeeping that must not cause golden-test failures
IGNORED_PROPERTIES = {
    "rinfoex:senastHamtad",
    "rinfoex:senastKontrollerad",
    "prov:wasGeneratedBy",
}


def localname(elem):
    return etree.QName(elem).localname


def collapse(text):
    return re.sub(r"\s+", " ", text or "").strip()


def own_text(elem, skip=()):
    """Text of elem and descendants, excluding subtrees whose localname is
    in skip and excluding pure-markup spans (isPartOf etc.)."""
    parts = [elem.text or ""]
    for child in elem:
        if localname(child) not in skip:
            parts.append(own_text(child, skip))
        parts.append(child.tail or "")
    return "".join(parts)


def add_meta(meta, prop, value):
    if prop in IGNORED_PROPERTIES or not prop:
        return
    if prop in MULTIVALUED:
        meta.setdefault(prop, []).append(value)
    elif prop in meta and meta[prop] != value:
        # repeated single-valued property: degrade to list so nothing is lost
        existing = meta[prop] if isinstance(meta[prop], list) else [meta[prop]]
        meta[prop] = existing + [value]
    else:
        meta[prop] = value


def normalize_metadata(head):
    """Document-level metadata. Properties about secondary resources (org
    labels) are keyed by their subject URI."""
    docuri = head.get("about")
    meta = {}
    secondary = {}
    for elem in head:
        about = elem.get("about")
        prop = elem.get("property") or elem.get("rel") or elem.get("rev")
        value = elem.get("content") or elem.get("href")
        if localname(elem) == "title":
            value = collapse(elem.text)
        if elem.get("rev"):
            prop = "rev:" + prop
        if about and about != docuri:
            secondary.setdefault(about, {})[prop] = value
        else:
            add_meta(meta, prop, value)
    for values in meta.values():
        if isinstance(values, list):
            values.sort()
    return {"uri": docuri, "properties": meta, "secondary": secondary}


def extract_references(container, fragment, refs):
    # the full text of the node, shared by every reference in it, so a diff
    # carries the whole clause the links were read from (the enumeration /
    # named law that resolves a bare "10 §"), not just the linked span
    context = " ".join("".join(container.itertext()).split())
    for a in container.iter("{%s}a" % XHTML):
        rel = a.get("rel")
        if rel and a.get("href"):
            refs.append([fragment, rel, a.get("href"), context])


def normalize_table(table):
    rows = []
    for tr in table.iter("{%s}tr" % XHTML):
        rows.append({"type": "rad",
                     "cells": [collapse(own_text(cell, skip=("span",)))
                               for cell in tr
                               if localname(cell) in ("td", "th")]})
    return {"type": "tabell", "id": table.get("id"), "children": rows}


def normalize_stycke(p, refs, fragment=""):
    # inherited fragment is only a reference-source fallback, not an id
    extract_references(p, p.get("id") or fragment, refs)
    node = {
        "type": "stycke",
        "id": p.get("id"),
        "text": collapse(own_text(p, skip=("ol", "ul", "span", "table"))),
    }
    beteckning = p.find("{%s}span[@class='paragrafbeteckning']" % XHTML)
    if beteckning is not None:
        node["beteckning"] = collapse(beteckning.text)
    # one document-order pass: list items (nested sublists become their own
    # punkt entries) and tables, interleaved as they appear
    items = []
    for el in p.iter("{%s}li" % XHTML, "{%s}table" % XHTML):
        if localname(el) == "table":
            items.append(normalize_table(el))
        else:
            items.append({
                "type": "punkt",
                "id": el.get("id"),
                "ordinal": el.get("content"),
                "text": collapse(own_text(el, skip=("span", "ol", "ul"))),
            })
    if items:
        node["children"] = items
    return node


def normalize_body_element(elem, refs, fragment=""):
    """Dispatch on typeof/class/tag; returns a node dict or None."""
    tag = localname(elem)
    typeof = elem.get("typeof")
    cls = elem.get("class") or ""

    if typeof == "rinfoex:Stycke":
        return normalize_stycke(elem, refs, fragment)

    if typeof in STRUCTURE_TYPES:
        node = {
            "type": STRUCTURE_TYPES[typeof],
            "id": elem.get("id"),
            "ordinal": elem.get("content"),
            "children": [],
        }
        for child in elem:
            sub = normalize_body_element(child, refs, elem.get("id") or fragment)
            if sub is not None:
                node["children"].append(sub)
        return node

    if tag in ("h1", "h2", "h3"):
        return {"type": "rubrik", "id": elem.get("id"),
                "level": int(tag[1]), "text": collapse(own_text(elem))}

    if "upphavdparagraf" in cls or "upphavtkapitel" in cls:
        return {"type": "upphavd", "text": collapse(own_text(elem))}

    if cls in STRUCTURE_CLASSES:
        node = {"type": STRUCTURE_CLASSES[cls], "id": elem.get("id"),
                "children": []}
        for child in elem:
            sub = normalize_body_element(child, refs, elem.get("id") or fragment)
            if sub is not None:
                node["children"].append(sub)
        return node

    if tag == "table":
        return normalize_table(elem)

    # the amendment register is handled separately; spans are RDFa plumbing
    if "register" in cls or tag == "span":
        return None

    if tag in ("p", "div", "li"):
        # safety net: never flatten a container that holds structural
        # children -- recurse and surface it as a generic container so it
        # shows up as a diff instead of vanishing into a blob
        if any(child.get("typeof") in STRUCTURE_TYPES
               or child.get("typeof") == "rinfoex:Stycke"
               or (child.get("class") or "") in STRUCTURE_CLASSES
               for child in elem.iter()):
            node = {"type": "container", "id": elem.get("id"),
                    "tag": tag, "class": cls or None, "children": []}
            for child in elem:
                sub = normalize_body_element(
                    child, refs, elem.get("id") or fragment)
                if sub is not None:
                    node["children"].append(sub)
            return node
        text = collapse(own_text(elem, skip=("span",)))
        if text:
            extract_references(elem, elem.get("id") or fragment, refs)
            return {"type": tag, "id": elem.get("id"), "text": text}
    if tag in ("ol", "ul"):
        # same shape as list items inside a stycke: flat punkt entries
        node = {"type": "lista", "id": elem.get("id"), "children": []}
        extract_references(elem, elem.get("id") or fragment, refs)
        for el in elem.iter("{%s}li" % XHTML, "{%s}table" % XHTML):
            if localname(el) == "table":
                node["children"].append(normalize_table(el))
            else:
                node["children"].append({
                    "type": "punkt",
                    "id": el.get("id"),
                    "ordinal": el.get("content"),
                    "text": collapse(own_text(el, skip=("span", "ol", "ul"))),
                })
        return node
    return None


def normalize_register(body, refs):
    """The change register: registerpost divs describing each amending law."""
    amendments = []
    for post in body.iter("{%s}div" % XHTML):
        if (post.get("class") or "") != "registerpost":
            continue
        entry = {"uri": post.get("about"), "properties": {}, "forarbeten": []}
        for span in post.iter("{%s}span" % XHTML):
            prop = span.get("property") or span.get("rel")
            value = span.get("content") or span.get("href")
            if prop == "rpubl:forarbete":
                ident = span.find("{%s}span[@property='dcterms:identifier']" % XHTML)
                entry["forarbeten"].append(
                    ident.get("content") if ident is not None else value)
            elif span.getparent() is post or span.getparent().get("class") != "registerpost":
                # skip nested forarbete sub-properties (identifier, rdf:type)
                if span.getparent().get("rel") == "rpubl:forarbete":
                    continue
                add_meta(entry["properties"], prop, value)
        # transitional provisions etc. attached to this amendment
        for child in post:
            if localname(child) != "span":
                node = normalize_body_element(
                    child, refs, child.get("id") or entry["uri"])
                if node is not None:
                    entry.setdefault("content", []).append(node)
        for values in entry["properties"].values():
            if isinstance(values, list):
                values.sort()
        entry["forarbeten"].sort()
        amendments.append(entry)
    return amendments


def normalize(path):
    tree = etree.parse(str(path))
    root = tree.getroot()
    head = root.find("{%s}head" % XHTML)
    body = root.find("{%s}body" % XHTML)

    refs = []
    structure = []
    for child in body:
        node = normalize_body_element(child, refs)
        if node is not None:
            structure.append(node)

    amendments = normalize_register(body, refs)
    return {
        "uri": body.get("about"),
        "metadata": normalize_metadata(head),
        "structure": structure,
        "references": sorted(set(map(tuple, refs))),
        "amendments": amendments,
    }


# --- comparison ---------------------------------------------------------

def node_label(node):
    return node.get("id") or node.get("uri") or "%s:%r" % (
        node.get("type"), (node.get("text") or "")[:40])


def diff_nodes(old, new, path, problems):
    if old.get("type") != new.get("type") or old.get("id") != new.get("id"):
        problems.append("%s: node mismatch: %s != %s"
                        % (path, node_label(old), node_label(new)))
        return
    here = "%s/%s" % (path, node_label(old))
    for key in ("ordinal", "text", "beteckning", "level"):
        if old.get(key) != new.get(key):
            problems.append("%s: %s changed:\n  old: %r\n  new: %r"
                            % (here, key, old.get(key), new.get(key)))
    oldkids, newkids = old.get("children", []), new.get("children", [])
    diff_nodelists(oldkids, newkids, here, problems)


def labeled_map(nodes):
    """Map nodes by label; duplicate labels (id-less nodes with equal
    text) get an occurrence suffix so both sides pair positionally."""
    out = {}
    counts = {}
    for node in nodes:
        label = node_label(node)
        counts[label] = counts.get(label, 0) + 1
        if counts[label] > 1:
            label = "%s#%d" % (label, counts[label])
        out[label] = node
    return out


def diff_nodelists(old, new, path, problems):
    oldmap = labeled_map(old)
    newmap = labeled_map(new)
    for label in oldmap.keys() - newmap.keys():
        problems.append("%s: missing node %s" % (path, label))
    for label in newmap.keys() - oldmap.keys():
        problems.append("%s: extra node %s" % (path, label))
    for label in oldmap.keys() & newmap.keys():
        diff_nodes(oldmap[label], newmap[label], path, problems)
    oldorder = [l for l in oldmap if l in newmap]
    neworder = [l for l in newmap if l in oldmap]
    if oldorder != neworder:
        problems.append("%s: node order differs" % path)


def diff_dicts(old, new, path, problems):
    for key in old.keys() - new.keys():
        problems.append("%s: missing %s (was %r)" % (path, key, old[key]))
    for key in new.keys() - old.keys():
        problems.append("%s: extra %s (= %r)" % (path, key, new[key]))
    for key in old.keys() & new.keys():
        if isinstance(old[key], dict) and isinstance(new[key], dict):
            diff_dicts(old[key], new[key], "%s.%s" % (path, key), problems)
        elif old[key] != new[key]:
            problems.append("%s.%s changed:\n  old: %r\n  new: %r"
                            % (path, key, old[key], new[key]))


ALL_SECTIONS = ("metadata", "structure", "references", "amendments")

# The old pipeline's citation parser escaped "X- och Y-lagen" compounds to
# "X-_och_Y-lagen" before grammar matching and did not reliably undo it;
# 1021 documents in the corpus carry the corruption. Canonicalize both
# sides so the contract enforces neither variant.
re_descape_compound = re.compile(
    r"\b(\w+-)_(och)_(\w+-?)(lagen|förordningen)\b")


def canonicalize_node_texts(nodes):
    for node in nodes:
        text = node.get("text")
        if isinstance(text, list):
            # new-pipeline text nodes are inline lists (str runs + link
            # objects); the amendment contract compares text, not links
            text = "".join(p if isinstance(p, str) else p["text"]
                           for p in text)
        if text:
            node["text"] = re_descape_compound.sub(r"\1 \2 \3\4", text)
        canonicalize_node_texts(node.get("children", []))


# Page-numbered laws (1845:50 s.1): the old pipeline derived the law id
# for in-document relative references from its own minted URI ("50_s.1"),
# then split it on "s." leaving the underscore behind -- producing
# "1845:50__s._1" with a doubled underscore. Canonicalize to the form its
# own test suite expects ("1910:103_s._1").
re_sidnr_slug = re.compile(r"(\d):(\d+)_*\s*s\._?\s*(\d+)")


def canonicalize_ref_uri(uri):
    return re_sidnr_slug.sub(r"\1:\2_s._\3", uri)


def canonicalize_refs(refs):
    """Map each reference to its *context* (the full text of the source node it
    was read from), keyed by the canonical (source, predicate, uri) triple. The
    triple is the match key; the context is carried for diff readability and may
    be '' for a pre-text golden (refs without a 4th element)."""
    out = {}
    for ref in map(tuple, refs):
        out[(ref[0], ref[1], canonicalize_ref_uri(ref[2]))] = (
            ref[3] if len(ref) > 3 else "")
    return out


def format_ref(kind, key, text):
    """A references diff line. The source node's full clause is appended in
    guillemets so the diff shows *what* each side read the reference from -- the
    context that makes a bare "10 §" resolve to a particular chapter/law, which
    is what distinguishes a real citation from a span that should not have
    linked. The URI stays the last whitespace-free token so the diff parsers
    still recover it."""
    line = "references: %s %s --%s--> %s" % (kind, key[0], key[1], key[2])
    return line + ("  «%s»" % text if text else "")


# amendment properties whose values are paragraph URIs carrying the same
# page-number slug quirk the reference comparison canonicalizes away
AMENDMENT_URI_PROPS = ("rpubl:ersatter", "rpubl:upphaver", "rpubl:inforsI")


def canonicalize_amendment(entry):
    """A copy of one amendment entry with URI-valued properties slug-
    canonicalized and content node texts descaped, ready for raw diffing."""
    entry = dict(entry)
    props = dict(entry.get("properties", {}))
    for key in AMENDMENT_URI_PROPS:
        if key in props:
            props[key] = sorted(canonicalize_ref_uri(u) for u in props[key])
    # dcterms:isPartOf is a derived containment artifact of the old RDFa
    # serialization (which övergångsbestämmelse fragments got their own URI);
    # it's redundant with the content tree and not reproduced
    props.pop("dcterms:isPartOf", None)
    entry["properties"] = props
    canonicalize_node_texts(entry.get("content", []))
    return entry


# document metadata stamped with the consolidation run-date — not
# reproducible, so canonicalized away (the konsolidering URI itself, built
# from the cutoff SFS rather than the date, is kept)
METADATA_VOLATILE = ("dcterms:issued", "owl:sameAs")


# a department's sub-organisation suffix ("Finansdepartementet BA", "… S2")
# is bureaucratic noise that drifts at the source and resolves to the same
# org URI; canonicalize it out of the secondary label so only genuine
# department renames register as diffs
re_suborg = re.compile(r",? (och|[A-ZÅÄÖ\d]{1,5})$")


def canon_org_label(label):
    while True:
        stripped = re_suborg.sub("", label)
        if stripped == label:
            return label
        label = stripped


def canon_secondary(secondary):
    return {uri: {k: (canon_org_label(v) if k == "rdfs:label" else v)
                  for k, v in props.items()}
            for uri, props in secondary.items()}


def canon_title(title):
    """Mechanical old-header typography, not title semantics.

    Older golden headers variably print page-number identifiers as ``s. 1`` and
    retain a terminal semicolon from the running heading. The current source
    normalizes both. Do not modernize spelling or drop royal prefixes here:
    those may be substantive title changes and remain reviewable.
    """
    return re.sub(r"s\.\s+(\d+)", r"s.\1", title or "").rstrip(";")


def diff_metadata(old, new, problems):
    if old["uri"] != new["uri"]:
        problems.append("uri: %r != %r" % (old["uri"], new["uri"]))
    if old["metadata"]["uri"] != new["metadata"]["uri"]:
        problems.append("metadata.uri: %r != %r"
                        % (old["metadata"]["uri"], new["metadata"]["uri"]))
    o = {k: v for k, v in old["metadata"]["properties"].items()
         if k not in METADATA_VOLATILE}
    n = {k: v for k, v in new["metadata"]["properties"].items()
         if k not in METADATA_VOLATILE}
    if "dcterms:title" in o and "dcterms:title" in n:
        o["dcterms:title"] = canon_title(o["dcterms:title"])
        n["dcterms:title"] = canon_title(n["dcterms:title"])
    diff_dicts(o, n, "metadata.properties", problems)
    diff_dicts(canon_secondary(old["metadata"]["secondary"]),
               canon_secondary(new["metadata"]["secondary"]),
               "metadata.secondary", problems)


def diff_amendments(old, new, problems):
    oldam = {a["uri"]: canonicalize_amendment(a) for a in old}
    newam = {a["uri"]: canonicalize_amendment(a) for a in new}
    for uri in oldam.keys() - newam.keys():
        problems.append("amendments: missing %s" % uri)
    for uri in newam.keys() - oldam.keys():
        problems.append("amendments: extra %s" % uri)
    for uri in oldam.keys() & newam.keys():
        o, n = dict(oldam[uri]), dict(newam[uri])
        ocontent, ncontent = o.pop("content", []), n.pop("content", [])
        diff_dicts(o, n, "amendments[%s]" % uri, problems)
        diff_nodelists(ocontent, ncontent,
                       "amendments[%s].content" % uri, problems)


def compare(old, new, sections=ALL_SECTIONS):
    problems = []
    for side in (old, new):
        canonicalize_node_texts(side.get("structure", []))
        for amendment in side.get("amendments", []):
            canonicalize_node_texts(amendment.get("content", []))
    if "metadata" in sections:
        diff_metadata(old, new, problems)
    if "structure" in sections:
        diff_nodelists(old["structure"], new["structure"], "structure",
                       problems)

    if "references" in sections and "references" in old and "references" in new:
        # the new pipeline inlines links into the text nodes instead of
        # emitting a flat reference list, so this oracle only applies when
        # both sides are old-style normal forms
        oldrefs = canonicalize_refs(old["references"])
        newrefs = canonicalize_refs(new["references"])
        for key in sorted(set(oldrefs) - set(newrefs)):
            problems.append(format_ref("missing", key, oldrefs[key]))
        for key in sorted(set(newrefs) - set(oldrefs)):
            problems.append(format_ref("extra", key, newrefs[key]))

    if "amendments" in sections:
        diff_amendments(old["amendments"], new["amendments"], problems)
    return problems


# --- adjudication -------------------------------------------------------
#
# The golden corpus is a change-detector, not an oracle (§2): a fraction of
# the diffs are the new pipeline being *right* where the golden is stale or
# carries an old-pipeline defect. Rather than re-investigate those every run,
# classify whole *families* of them with a few predicates. An adjudicated diff
# is still reported (so a class that suddenly grows stays visible) but does not
# count as a regression -- only the *unexplained* residual does.
#
# Most predicates work from the problem string plus the golden normal form.
# The deliberately narrow structure-staleness predicate also receives the
# candidate normal form, because exact amendment notes and repeal targets are
# the positive evidence needed to adjudicate structure. A predicate that needs
# to know the document is stale still requires a post-freeze-amendment diff in
# the same run -- i.e. amendments must be among the compared sections.

re_sfs_number = re.compile(r"(\d{4}):(\d+)")


def sfs_key(text):
    """(year, nr) sort key for the first SFS number in `text`, else None."""
    m = re_sfs_number.search(text or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def golden_freeze_horizon(golden):
    """The latest change-act SFS number the golden knew -- the cutoff the
    post-freeze-amendment predicate measures an extra amendment against."""
    keys = [k for a in golden.get("amendments", [])
            for k in (sfs_key(a.get("uri")),) if k]
    return max(keys) if keys else None


# A references-section diff line: "references: <extra|missing> <source>
# --<predicate>--> <uri>" (source may be empty). Match the source lazily so
# the predicate arrow is not swallowed.
# the URI is the first whitespace-free token after the arrow; an optional
# «linked text» may follow (see format_ref), so don't anchor at end-of-line
re_reference_diff = re.compile(r"references: (extra|missing) (.*?) --\S+--> (\S+)")
re_change_ref = re.compile(r"#L(\d{4}):(\d+)$")


def reference_diff(problem):
    """(kind, source, uri) for a references diff line, else None."""
    m = re_reference_diff.match(problem)
    return m.groups() if m else None


re_clause = re.compile(r"«(.*)»\s*$")


def reference_clause(problem):
    """The source-node clause `format_ref` appended in guillemets, or ''."""
    m = re_clause.search(problem)
    return m.group(1) if m else ""


def self_change_ref_key(uri, own_base):
    """(year, nr) if `uri` is an ändringshänvisning into the document's own
    law (own_base + '#L<year>:<nr>'), else None. An ändringshänvisning is an
    *internal* link to the act that last amended a paragraf -- that act has no
    consolidated document of its own, so it lives under the law's own URI."""
    if not own_base or not uri.startswith(own_base + "#L"):
        return None
    m = re_change_ref.search(uri)
    return (int(m.group(1)), int(m.group(2))) if m else None


re_balk_law = re.compile(r"\d+:\d+_\d+$")   # "1736:0123_1" -- a 1734-lag balk


def balk_self_ref(uri, own_base, collapsed_base):
    """Whether `uri` is a self-reference into a 1734 års lag balk -- 'full'
    (the corrected ".../1736:0123_1#…") or 'collapsed' (the old pipeline's
    ".../1736:0123#…", which lost the balk suffix), else None."""
    if collapsed_base is None:
        return None
    if uri == own_base or uri.startswith(own_base + "#"):
        return "full"
    if uri == collapsed_base or uri.startswith(collapsed_base + "#"):
        return "collapsed"
    return None


re_chapter_prefix = re.compile(r"^K\d+[a-z]?")
re_paragraf_prefix = re.compile(r"^(K\d+[a-z]?)?P\d+[a-z]?")


def paragraf_of(fragment):
    """The paragraf-level prefix of a source fragment ("K2P1S13" -> "K2P1",
    "P5S2" -> "P5"), or None when the fragment is not paragraf-rooted (a bare
    chapter or an empty source)."""
    m = re_paragraf_prefix.match(fragment or "")
    return m.group(0) if m else None


def golden_chapter_collapsed(golden):
    """True when the golden piled essentially every paragraf into a single
    chapter -- the old pipeline's table-of-contents collapse, where a chapter
    list ("N kap. - Title" lines) was mis-read as chapter openings and the body
    fell into the last one. The new pipeline distributes the chapters correctly,
    so its references carry the right chapter prefixes and no longer match the
    golden's collapsed ones; that divergence is the new pipeline being right."""
    counts = []

    def walk(nodes):
        for node in nodes:
            if not isinstance(node, dict):
                continue
            if node.get("type") == "kapitel" and node.get("id"):
                counts.append(sum(
                    1 for c in node.get("children", [])
                    if isinstance(c, dict) and c.get("type") == "paragraf"))
            walk(node.get("children", []))

    walk(golden.get("structure", []))
    total = sum(counts)
    return len(counts) >= 3 and total > 0 and max(counts) / total >= 0.8


def chapter_collapse_key(source, uri, own_base):
    """A reference's (source, uri) with the chapter prefix removed, so a link the
    golden read from its collapse chapter (eg "K29P5") and the one the new
    pipeline distributed ("K3P5") -- same continuous paragraf number, different
    chapter -- map together. The chapter is stripped from the source fragment and,
    for a self-reference to a paragraf, from the target (renumbered the same way);
    named-chapter and external targets keep their identical-on-both-sides form."""
    src = re_chapter_prefix.sub("", source or "")
    if own_base and uri.startswith(own_base + "#"):
        frag = uri[len(own_base) + 1:]
        m = re.match(r"K\d+[a-z]?(P.*)$", frag)
        if m:
            uri = own_base + "#" + m.group(1)
    return (src, uri)


# A well-formed sector-3 CELEX in the new pipeline's URI: 3 + year(4) + type
# (1-2 letters) + number(4, zero-padded), with an optional pinpoint fragment.
re_celex_uri = re.compile(
    r"(.*/ext/celex/)3(\d{4})([A-Z]{1,2})(\d{4})(#.*)?$")


def celex_descramble(uri):
    """The *old* pipeline's scrambled rendering of a well-formed new CELEX
    URI, else None. The old legalref engine built a CELEX as
    3+number+type+year ("3625R2017") where the correct form is
    3+year+type+number ("32017R0625") -- so it named a non-existent act
    (year "0625"). Inverting the new URI gives the exact scrambled string the
    golden carries, so a corrected extra and the golden's scrambled miss can be
    recognised as one mirror pair."""
    m = re_celex_uri.match(uri)
    if not m:
        return None
    base, year, descriptor, number, frag = m.groups()
    return "%s3%d%s%s%s" % (base, int(number), descriptor, year, frag or "")


def celex_old_scrambles(uri):
    """Every string the old pipeline could have rendered for the well-formed
    sector-3 CELEX `uri` (empty for a non sector-3 URI):

    * the year/number field-swap it always applied (`celex_descramble`); plus
    * when `uri` is a *directive* (type ``L``), the same swap with the type letter
      forced to ``R``. The old engine -- like the pre-fix grammar -- defaulted a
      parenthesised designation cited bare as "direktiv (EU) YYYY/NNNN" to a
      regulation, so its scramble for such a directive is ``3<number>R<year>``.
      Pairing that against the new pipeline's corrected ``3<year>L<number>`` lets
      the directive-letter correction be forgiven as a mirror, the way the plain
      year/number scramble already is (lagrum.rattsakt_part fix)."""
    primary = celex_descramble(uri)
    if primary is None:
        return set()
    base, year, descriptor, number, frag = re_celex_uri.match(uri).groups()
    out = {primary}
    if descriptor == "L":
        out.add("%s3%dR%s%s" % (base, int(number), year, frag or ""))
    return out


# document-level metadata that is the *consolidation envelope* -- it moves as
# new amending acts fold in: the konsolidering cutoff URI, the "i lydelse
# enligt SFS …" identifier, the underlag list, the responsible department.
# Drift here is accepted only once a post-freeze amendment has independently
# shown the document stale (§3c).
ENVELOPE_PREFIXES = (
    "metadata.uri",
    "metadata.properties.dcterms:identifier",
    "metadata.properties.rpubl:konsolideringsunderlag",
    "metadata.properties.rpubl:konsoliderar",
    "metadata.properties.dcterms:creator",
    "metadata.secondary",
)


def _post_freeze_amendment(problem, ctx):
    """An amending act the new pipeline has and the golden lacks, whose SFS
    number postdates everything the golden knew = added after the freeze."""
    if not problem.startswith("amendments: extra "):
        return False
    key = sfs_key(problem)
    return key is not None and ctx["horizon"] is not None and key > ctx["horizon"]


def _stale_consolidation_drift(problem, ctx):
    """Consolidation-envelope metadata that drifts as amendments land, on a
    document a post-freeze amendment already marked stale."""
    return ctx["stale"] and problem.startswith(ENVELOPE_PREFIXES)


def _change_reference_staleness(problem, ctx):
    """An ändringshänvisning (#L<act>) that drifted because the freshly-
    downloaded consolidation was amended after the golden froze. A paragraf's
    closing "Lag (NNNN:NN)." names the act that last amended it; re-amendment
    bumps it to a later act. The extra names a post-freeze act (new-is-right);
    the matching missing is the pre-bump note, forgiven only when that same
    stycke's reference was in fact bumped -- so a stycke-renumbering diff to a
    pre-horizon act stays unexplained."""
    parsed = reference_diff(problem)
    if parsed is None or ctx["horizon"] is None:
        return False
    kind, source, uri = parsed
    key = self_change_ref_key(uri, ctx["own_base"])
    if key is None:
        return False
    if kind == "extra":
        return key > ctx["horizon"]
    return source in ctx["bumped_sources"]


def _post_freeze_source_amendment(problem, ctx):
    """A reference diff sourced from a paragraf a post-freeze amendment rewrote.
    Its closing "Lag (NNNN:NN)." / "Förordning (NNNN:NN)." now names an act later
    than the golden's freeze horizon (seen as the paragraf bumping its #L
    ändringshänvisning past the horizon), so the golden's whole reference set for
    that paragraf predates the current text -- renumbered cross-references, added
    or removed list members, reworded clauses. None of those can be validated
    against the stale golden, so every reference sourced from the paragraf is
    forgiven. The ändringshänvisning note itself is credited to the narrower
    change-reference-staleness rule, which runs first."""
    parsed = reference_diff(problem)
    if parsed is None:
        return False
    para = paragraf_of(parsed[1])
    return para is not None and para in ctx["bumped_paragrafs"]


# A current SFS provision normally ends with the formal amendment note that
# introduced its present wording: "Lag (2024:123)." / "Förordning (2024:123)."
# This is stronger evidence than merely finding a newer SFS number somewhere in
# the prose (where it may be an ordinary citation).
re_amendment_note = re.compile(
    r"\b(?:Lag|Förordning) \((\d{4}):(\d+)\)(?:\.|\s|$)")
re_structure_extra = re.compile(r"^structure(?:/.*)?: extra node (\S+)$")
re_structure_missing = re.compile(r"^structure(?:/.*)?: missing node (\S+)$")
re_structure_changed = re.compile(r"^structure(?:/(.*))?: \w+ changed:")

def is_marker_only(text):
    """Whether `text` is *only* an omission marker (+ an optional trailing
    change note) -- the shape the new pipeline replaces with a grafik node. A
    stycke with real prose around a marker is NOT marker-only and its removal
    stays a real regression."""
    return isinstance(text, str) and sfs_graphics.marker_gap(text) is not None


def changed_old_new(problem):
    """The (old, new) values of a ``… changed:`` diff line, or (None, None) --
    the two repr'd scalars diff_nodes emits."""
    m = re.search(r"\n  old: (.*)\n  new: (.*)$", problem, re.S)
    if not m:
        return None, None
    try:
        return ast.literal_eval(m.group(1)), ast.literal_eval(m.group(2))
    except (ValueError, SyntaxError):
        return None, None


def node_text(node):
    """Plain text for one normal-form subtree, including inline link runs."""
    text = node.get("text") or ""
    if isinstance(text, list):
        text = "".join(p if isinstance(p, str) else p.get("text", "")
                       for p in text)
    return " ".join([text] + [node_text(c) for c in node.get("children", [])])


def nodes_by_id(nodes):
    """First normal-form node for each minted id, recursively."""
    out = {}

    def walk(items):
        for node in items:
            if node.get("id"):
                out.setdefault(node["id"], node)
            walk(node.get("children", []))

    walk(nodes)
    return out


def post_freeze_note(node, horizon):
    """Whether `node`'s subtree carries a formal note newer than `horizon`."""
    return horizon is not None and any(
        (int(year), int(number)) > horizon
        for year, number in re_amendment_note.findall(node_text(node)))


def post_freeze_repealed_ids(new, horizon):
    """Node ids explicitly repealed by candidate amendments after `horizon`."""
    out = set()
    if horizon is None:
        return out
    for amendment in new.get("amendments", []):
        key = sfs_key(amendment.get("uri"))
        if key is None or key <= horizon:
            continue
        values = amendment.get("properties", {}).get("rpubl:upphaver", [])
        if isinstance(values, str):
            values = [values]
        for uri in values:
            if "#" in uri:
                out.add(uri.split("#", 1)[1])
    return out


def _post_freeze_structure(problem, ctx):
    """A current structure addition/change proved newer than the golden.

    Three independent gates deliberately make this narrow:

    * the amendments comparison contains an added act after the golden horizon;
    * the candidate normal form is available (old-only compare calls cannot
      adjudicate structure);
    * the exact added/changed node, or its closest id-bearing ancestor, contains
      a formal Lag/Förordning amendment note after that horizon; or an exact
      missing node is explicitly named by a post-freeze `rpubl:upphaver`.

    Other missing nodes and all order changes remain review items rather than
    being guessed away.
    """
    if not ctx["stale"]:
        return False
    m = re_structure_extra.match(problem)
    if m:
        node = ctx["new_nodes"].get(m.group(1))
        return node is not None and post_freeze_note(node, ctx["horizon"])
    m = re_structure_missing.match(problem)
    if m:
        return m.group(1) in ctx["post_freeze_repeals"]
    m = re_structure_changed.match(problem)
    if not m:
        return False
    ids = [part for part in (m.group(1) or "").split("/") if part]
    node = next((ctx["new_nodes"][node_id] for node_id in reversed(ids)
                 if node_id in ctx["new_nodes"]), None)
    return node is not None and post_freeze_note(node, ctx["horizon"])


# --- grafik-node-replaces-marker -----------------------------------------
#
# The SFST text database drops graphics/formulas/maps/road signs; the old
# pipeline carried the editorial omission marker ("/Formeln är inte med här/")
# through as inline text, while the new pipeline lifts it into a typed grafik
# node (sfs.graphics + nf). Against a golden built from the old output that
# surfaces as three mirrored, new-is-right diffs: an extra grafik node, the
# missing marker-only stycke it replaced, and -- for a marker that trailed a
# `Bilaga N` heading -- the heading text losing only the marker. Each gate is
# narrow: a real prose stycke that went missing, or a heading change that is not
# just a stripped marker, stays a review item.

def _is_grafik_extra(problem, ctx):
    m = re_structure_extra.match(problem)
    if not m:
        return False
    node = ctx["new_nodes"].get(m.group(1))
    return node is not None and node.get("type") == "grafik"


def _is_grafik_missing(problem, ctx):
    m = re_structure_missing.match(problem)
    if not m:
        return False
    node = ctx["golden_nodes"].get(m.group(1))
    return node is not None and is_marker_only(node.get("text"))


def _is_grafik_heading(problem):
    m = re_structure_changed.match(problem)
    if not m or ": text changed:" not in problem:
        return False
    old, new = changed_old_new(problem)
    if not isinstance(old, str):
        return False
    clean, sort = sfs_graphics.heading_gap(old)
    return sort is not None and clean.strip() == (new or "").strip()


def grafik_paired_problems(problems, ctx):
    """Only accept a complete marker->grafik replacement in one parent.

    The old independent predicates could forgive a marker that simply vanished,
    or a phantom grafik with no removed marker. Exact per-parent cardinality is
    intentionally conservative: any mismatch leaves the whole group for review.
    """
    groups = {}
    for problem in problems:
        parent = problem.partition(":")[0]
        if _is_grafik_extra(problem, ctx):
            groups.setdefault(parent, ([], []))[0].append(problem)
        elif _is_grafik_missing(problem, ctx) or _is_grafik_heading(problem):
            groups.setdefault(parent, ([], []))[1].append(problem)
    paired = set()
    for extras, removed in groups.values():
        if extras and len(extras) == len(removed):
            paired.update(extras)
            paired.update(removed)
    return paired


def _grafik_extra_node(problem, ctx):
    return problem in ctx["grafik_paired"] and _is_grafik_extra(problem, ctx)


def _grafik_missing_marker(problem, ctx):
    return problem in ctx["grafik_paired"] and _is_grafik_missing(problem, ctx)


def _grafik_heading_marker(problem, ctx):
    return problem in ctx["grafik_paired"] and _is_grafik_heading(problem)


# An "eller" enumeration of paragraf numbers terminated by a *single* § -- e.g.
# "1, 3, 5 eller 6 §", "26 eller 26 a §". By Swedish drafting convention an "och"
# list ends in double §§ ("4, 5 och 6 §§") and an "eller" list in single §; the
# old SimpleParse grammar parsed the former but never the latter, so the members
# the new pipeline extracts from an "eller …  §" list are extras it is right about.
re_eller_enum = re.compile(
    r"(\d+(?: ?[a-z])?(?: ?, ?\d+(?: ?[a-z])?)* ?eller ?\d+(?: ?[a-z])?) ?§(?! ?§)")
re_target_paragraf = re.compile(r"#(?:K[0-9a-z]+)?P(\d+[a-z]?)(?:$|[SNO])")
re_target_chapter = re.compile(r"#K(\d+)[a-z]?")


def eller_enum_paragrafs(clause):
    """Paragraf numbers enumerated with 'eller' and a single § in `clause`,
    normalized like a fragment ('6 a' -> '6a')."""
    out = set()
    for m in re_eller_enum.finditer(clause or ""):
        out.update(t.replace(" ", "") for t in re.split(r", ?| ?eller ?", m.group(1)))
    return out


def _eller_enumeration(problem, ctx):
    """An extra paragraf reference the new pipeline read from an "eller … §"
    enumeration the old grammar could not parse: the target's paragraf number is
    one of the enumerated members and its chapter, if any, is named in the same
    clause. A clear old-pipeline grammar gap -- new-is-right."""
    parsed = reference_diff(problem)
    if parsed is None or parsed[0] != "extra":
        return False
    uri = parsed[2]
    para = re_target_paragraf.search(uri)
    if para is None:
        return False
    clause = reference_clause(problem)
    if para.group(1) not in eller_enum_paragrafs(clause):
        return False
    chapter = re_target_chapter.search(uri)
    return chapter is None or re.search(
        r"\b%s ?[a-z]? ?kap" % chapter.group(1), clause) is not None


def _balk_basefile_correction(problem, ctx):
    """A 1734 års lag balk ("1736:0123 1" = byggningabalken, "… 2" =
    handelsbalken) whose self-references the new pipeline mints against the full
    basefile (".../1736:0123_1#…"), where the old pipeline collapsed the bare
    suffix to ".../1736:0123#…" -- losing the _1/_2 distinction. The corrected
    extra and the collapsed golden miss form a mirror pair; forgive each only
    when its counterpart shares the source stycke (so a genuine new drop or a
    spurious new add stays unexplained)."""
    parsed = reference_diff(problem)
    if parsed is None:
        return False
    kind, source, uri = parsed
    flavour = balk_self_ref(uri, ctx["own_base"], ctx["balk_collapsed_base"])
    if kind == "extra":
        return flavour == "full" and source in ctx["balk_collapsed_sources"]
    return flavour == "collapsed" and source in ctx["balk_full_sources"]


def _golden_chapter_collapse(problem, ctx):
    """A reference diff that is purely the golden's TOC-collapse chapter relabel:
    the new pipeline's distributed link (extra) mirrors a collapsed golden one
    (missing) modulo chapter prefix, or vice versa. Fires only when the golden is
    collapsed; the mirror requirement keeps a genuine new drop/add visible -- the
    residual is real reference-resolution drift, not the structural collapse."""
    if not ctx["golden_collapsed"]:
        return False
    parsed = reference_diff(problem)
    if parsed is None:
        return False
    kind, source, uri = parsed
    key = chapter_collapse_key(source, uri, ctx["own_base"])
    if kind == "extra":
        return key in ctx["collapse_missing_keys"]
    return key in ctx["collapse_extra_keys"]


def _celex_correction(problem, ctx):
    """A CELEX reference the new pipeline mints in the correct sector-3 form
    where the old pipeline scrambled the year/number fields (§7d). The
    corrected extra (".../32017R0625") and the golden's scrambled miss
    (".../3625R2017") are a mirror pair from the same source stycke; forgive
    each only when its counterpart is present, so a CELEX the new pipeline
    genuinely added (no scrambled mirror) or one it dropped stays visible."""
    parsed = reference_diff(problem)
    if parsed is None:
        return False
    kind, source, uri = parsed
    if kind == "extra":
        return any((source, s) in ctx["celex_missing"]
                   for s in celex_old_scrambles(uri))
    return (source, uri) in ctx["celex_extra_scrambled"]


def _stycke_pinpoint_drift(problem, ctx):
    """A reference whose target (predicate + uri) is identical on both sides but
    read from a different stycke of the *same paragraf* -- the citation graph edge
    is unchanged; only which stycke of the citing paragraf carries the back-link
    drifted. The parser now numbers stycken correctly (a list embedded mid-clause
    keeps its trailing continuation in the stycke, assembler.py), but the old
    pipeline split that continuation into its own stycke (or, for a different
    paragraf, folded it into a list item), so its stycken run off by one and its
    references land on a stale stycke id. Forgive only as a mirror pair -- an extra
    with a missing counterpart from a *different* stycke of the same paragraf, or
    vice versa -- so a genuinely added or dropped edge (no counterpart) stays
    visible. Keyed on paragraf_of, so bilaga `S#` offsets (not paragraf-rooted)
    and bare-chapter relabels (different paragraf) are out of scope."""
    parsed = reference_diff(problem)
    if parsed is None:
        return False
    kind, source, uri = parsed
    para = paragraf_of(source)
    if para is None:
        return False
    others = (ctx["pinpoint_missing"] if kind == "extra"
              else ctx["pinpoint_extra"]).get((para, uri), set())
    return any(other != source for other in others)


# brottsrubricering definition clauses ("... döms för <offence> till böter/
# fängelse", "För <offence> döms till ..."); replicated from accommodanda.sfs
# .begrepp so this standalone comparator stays import-free of the package.
re_begrepp_diff = re.compile(r"begrepp: (extra|missing) (\S+)")
re_brottsdef_clause = re.compile(
    r"\b(?:döms|dömes)(?: han)?(?:,[\w\xa7 ]+,)? för [\w ]{3,50} till "
    r"(?:böter|fängelse)")
re_brottsdef_alt_clause = re.compile(
    r"[Ff]ör [\w ]{3,50} (?:döms|dömas) till (?:böter|fängelse)")


def _brottsrubricering_begrepp(problem, ctx):
    """A begreppsdefinition the new pipeline extracted that the golden lacks,
    whose defining clause is a criminal-offence definition ("... döms för X till
    böter/fängelse"). The old pipeline missed these crime names whenever the
    offence clause sat in a list continuation -- the new parser folds that
    continuation back into the stycke (assembler.py), so the brottsrubricering
    fires and the crime becomes a concept. A new-is-right gain, scoped to the
    offence-clause pattern so an ordinary added term (or extractor noise) is not
    blanket-forgiven; only an `extra` (the golden cannot newly *lack* a term it
    never had as a mirror)."""
    m = re_begrepp_diff.match(problem)
    if m is None or m.group(1) != "extra":
        return False
    clause = reference_clause(problem)
    return bool(re_brottsdef_clause.search(clause)
                or re_brottsdef_alt_clause.search(clause))


# (rule name, predicate). The families are disjoint, so order only decides
# which rule is credited in the unlikely event two would match.
PREDICATES = (
    ("post-freeze-amendment", _post_freeze_amendment),
    ("stale-consolidation-drift", _stale_consolidation_drift),
    ("change-reference-staleness", _change_reference_staleness),
    ("post-freeze-structure", _post_freeze_structure),
    ("balk-basefile-correction", _balk_basefile_correction),
    ("golden-chapter-collapse", _golden_chapter_collapse),
    ("celex-correction", _celex_correction),
    ("eller-enumeration", _eller_enumeration),
    ("stycke-pinpoint-drift", _stycke_pinpoint_drift),
    ("brottsrubricering-begrepp", _brottsrubricering_begrepp),
    # graphics the SFST text drops -> a typed grafik node (three mirror diffs)
    ("grafik-node-replaces-marker", _grafik_extra_node),
    ("grafik-node-replaces-marker", _grafik_missing_marker),
    ("grafik-node-replaces-marker", _grafik_heading_marker),
    # broadest last: a post-freeze-rewritten paragraf forgives any of its
    # references not already claimed by a more specific (new-is-right) family.
    ("post-freeze-source-amendment", _post_freeze_source_amendment),
)


def adjudicate(problems, golden, new=None):
    """Partition `problems` against `golden` into (unexplained, accepted).
    `accepted` is a list of (rule, problem) the change-detector posture
    forgives; `unexplained` is the residual -- the regressions that count."""
    own_base = golden.get("uri", "").split("/konsolidering")[0]
    ctx = {"horizon": golden_freeze_horizon(golden), "stale": False,
           "own_base": own_base,
           "new_nodes": nodes_by_id(new.get("structure", [])) if new else {},
           "golden_nodes": nodes_by_id(golden.get("structure", []))}
    ctx["grafik_paired"] = grafik_paired_problems(problems, ctx)
    ctx["post_freeze_repeals"] = post_freeze_repealed_ids(
        new or {}, ctx["horizon"])
    ctx["stale"] = any(_post_freeze_amendment(p, ctx) for p in problems)
    # stycken whose ändringshänvisning was bumped to a post-freeze act -- the
    # missing pre-bump note from the same source is then forgivable too.
    ctx["bumped_sources"] = {
        src for kind, src, uri in filter(None, map(reference_diff, problems))
        if kind == "extra" and ctx["horizon"] is not None
        and (key := self_change_ref_key(uri, ctx["own_base"])) is not None
        and key > ctx["horizon"]}
    # paragrafs holding a post-freeze-bumped stycke -- the whole paragraf was
    # rewritten, so all of its (renumbered/reworded) references are stale too.
    ctx["bumped_paragrafs"] = {
        para for src in ctx["bumped_sources"]
        if (para := paragraf_of(src)) is not None}
    # 1734-lag balk self-reference correction (full vs collapsed basefile): the
    # collapsed prefix is the own URI with its bare numeric suffix dropped.
    ctx["balk_collapsed_base"] = (
        own_base.rsplit("_", 1)[0]
        if re_balk_law.search(own_base.rsplit("/", 1)[-1]) else None)
    ctx["balk_full_sources"], ctx["balk_collapsed_sources"] = set(), set()
    for kind, src, uri in filter(None, map(reference_diff, problems)):
        flavour = balk_self_ref(uri, own_base, ctx["balk_collapsed_base"])
        if kind == "extra" and flavour == "full":
            ctx["balk_full_sources"].add(src)
        elif kind == "missing" and flavour == "collapsed":
            ctx["balk_collapsed_sources"].add(src)
    # CELEX year/number-scramble correction: pair a corrected extra with the
    # golden's scrambled miss from the same source (keyed (source, scrambled)).
    ctx["celex_missing"] = {
        (src, uri) for kind, src, uri in filter(None, map(reference_diff, problems))
        if kind == "missing"}
    ctx["celex_extra_scrambled"] = {
        (src, scrambled)
        for kind, src, uri in filter(None, map(reference_diff, problems))
        if kind == "extra" for scrambled in celex_old_scrambles(uri)}
    # golden TOC-collapse: when the golden dumped the whole body into one chapter,
    # pair the new pipeline's distributed link with the collapsed golden one by
    # their chapter-stripped (source, uri) -- a mirror, so unpaired diffs survive.
    ctx["golden_collapsed"] = golden_chapter_collapsed(golden)
    ctx["collapse_missing_keys"], ctx["collapse_extra_keys"] = set(), set()
    if ctx["golden_collapsed"]:
        for kind, src, uri in filter(None, map(reference_diff, problems)):
            key = chapter_collapse_key(src, uri, own_base)
            (ctx["collapse_missing_keys"] if kind == "missing"
             else ctx["collapse_extra_keys"]).add(key)
    # stycke-pinpoint drift: index each reference diff by (paragraf, uri) per
    # side, so a back-link re-anchored to a different stycke of the same paragraf
    # is recognised as a mirror pair (same edge, drifted citing pinpoint).
    ctx["pinpoint_missing"], ctx["pinpoint_extra"] = {}, {}
    for kind, src, uri in filter(None, map(reference_diff, problems)):
        para = paragraf_of(src)
        if para is None:
            continue
        bucket = ctx["pinpoint_extra"] if kind == "extra" else ctx["pinpoint_missing"]
        bucket.setdefault((para, uri), set()).add(src)
    unexplained, accepted = [], []
    for problem in problems:
        for rule, predicate in PREDICATES:
            if predicate(problem, ctx):
                accepted.append((rule, problem))
                break
        else:
            unexplained.append(problem)
    return unexplained, accepted


def load(path):
    path = Path(path)
    if path.suffix == ".json":
        return json.loads(path.read_text())
    return normalize(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    norm = sub.add_parser("normalize", help="emit normal form as JSON")
    norm.add_argument("file")
    comp = sub.add_parser("compare", help="diff two documents")
    comp.add_argument("old", help="golden document (.xhtml or .json)")
    comp.add_argument("new", help="candidate document (.xhtml or .json)")
    comp.add_argument("--sections", default=",".join(ALL_SECTIONS),
                      help="comma-separated subset of sections to compare "
                           "(default: all)")
    args = parser.parse_args()

    if args.command == "normalize":
        json.dump(normalize(args.file), sys.stdout,
                  ensure_ascii=False, indent=2, sort_keys=True)
        print()
    else:
        old = load(args.old)
        new = load(args.new)
        problems = compare(old, new, sections=args.sections.split(","))
        unexplained, accepted = adjudicate(problems, old, new)
        if accepted:
            print("%d adjudicated (new-is-right):" % len(accepted))
            for rule, problem in accepted:
                print("  [%s] %s" % (rule, problem.splitlines()[0]))
        if unexplained:
            print("%d difference(s):" % len(unexplained))
            for problem in unexplained:
                print("  " + problem)
            sys.exit(1)
        print("identical" if not problems else "identical after adjudication")


if __name__ == "__main__":
    main()
