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
import json
import re
import sys
from pathlib import Path

from lxml import etree

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
# A predicate works from the problem string plus the golden normal form alone
# (the new normal form is not threaded in). So a predicate that needs to know
# the document is stale relies on the post-freeze-amendment diff being present
# in the same run -- i.e. the amendments section must be among those compared.

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


# (rule name, predicate). The families are disjoint, so order only decides
# which rule is credited in the unlikely event two would match.
PREDICATES = (
    ("post-freeze-amendment", _post_freeze_amendment),
    ("stale-consolidation-drift", _stale_consolidation_drift),
    ("change-reference-staleness", _change_reference_staleness),
    ("balk-basefile-correction", _balk_basefile_correction),
    ("eller-enumeration", _eller_enumeration),
)


def adjudicate(problems, golden):
    """Partition `problems` against `golden` into (unexplained, accepted).
    `accepted` is a list of (rule, problem) the change-detector posture
    forgives; `unexplained` is the residual -- the regressions that count."""
    own_base = golden.get("uri", "").split("/konsolidering")[0]
    ctx = {"horizon": golden_freeze_horizon(golden), "stale": False,
           "own_base": own_base}
    ctx["stale"] = any(_post_freeze_amendment(p, ctx) for p in problems)
    # stycken whose ändringshänvisning was bumped to a post-freeze act -- the
    # missing pre-bump note from the same source is then forgivable too.
    ctx["bumped_sources"] = {
        src for kind, src, uri in filter(None, map(reference_diff, problems))
        if kind == "extra" and ctx["horizon"] is not None
        and (key := self_change_ref_key(uri, ctx["own_base"])) is not None
        and key > ctx["horizon"]}
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
        problems = compare(old, load(args.new), sections=args.sections.split(","))
        unexplained, accepted = adjudicate(problems, old)
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
