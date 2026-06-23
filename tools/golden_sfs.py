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
  golden_sfs.py freeze SRCDIR DESTDIR           # batch-normalize a corpus
"""

import argparse
import json
import os
import re
import sys
from concurrent.futures import ProcessPoolExecutor
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
    for a in container.iter("{%s}a" % XHTML):
        rel = a.get("rel")
        if rel and a.get("href"):
            refs.append([fragment, rel, a.get("href")])


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
        # shows up in the freeze report instead of vanishing into a blob
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
    return {(source, predicate, canonicalize_ref_uri(uri))
            for source, predicate, uri in map(tuple, refs)}


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
        for ref in sorted(oldrefs - newrefs):
            problems.append("references: missing %s --%s--> %s" % ref)
        for ref in sorted(newrefs - oldrefs):
            problems.append("references: extra %s --%s--> %s" % ref)

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


# (rule name, predicate). The families are disjoint, so order only decides
# which rule is credited in the unlikely event two would match.
PREDICATES = (
    ("post-freeze-amendment", _post_freeze_amendment),
    ("stale-consolidation-drift", _stale_consolidation_drift),
)


def adjudicate(problems, golden):
    """Partition `problems` against `golden` into (unexplained, accepted).
    `accepted` is a list of (rule, problem) the change-detector posture
    forgives; `unexplained` is the residual -- the regressions that count."""
    ctx = {"horizon": golden_freeze_horizon(golden), "stale": False}
    ctx["stale"] = any(_post_freeze_amendment(p, ctx) for p in problems)
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


# --- batch freezing ------------------------------------------------------

def count_types(nodes, acc):
    for node in nodes:
        acc[node["type"]] = acc.get(node["type"], 0) + 1
        count_types(node.get("children", []), acc)
    return acc


def sanity_check(normalform):
    """Heuristics for documents that normalized without error but whose
    normal form looks too empty to trust."""
    warnings = []
    types = count_types(normalform["structure"], {})
    if not normalform["structure"]:
        # revoked laws legitimately contain only the amendment register
        if not normalform["amendments"]:
            warnings.append("empty structure and no amendments")
    elif not types.get("stycke"):
        warnings.append("no stycke nodes (types: %s)" % types)
    for generic in ("container", "div", "p"):
        if types.get(generic):
            warnings.append("%d generic %s nodes (unrecognized vocabulary?)"
                            % (types[generic], generic))
    if not normalform["metadata"]["properties"].get("dcterms:title"):
        warnings.append("no dcterms:title")
    if not normalform["references"] and types.get("stycke", 0) > 5:
        warnings.append("no references despite %d stycken" % types["stycke"])
    return warnings


def freeze_one(job):
    src, dest, force = job
    if not force and dest.exists() and dest.stat().st_mtime > src.stat().st_mtime:
        return (str(src), "skipped", [])
    try:
        normalform = normalize(src)
    except Exception as e:
        return (str(src), "failed", ["%s: %s" % (type(e).__name__, e)])
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "w") as fp:
        json.dump(normalform, fp, ensure_ascii=False, indent=2, sort_keys=True)
        fp.write("\n")
    return (str(src), "ok", sanity_check(normalform))


def freeze(srcdir, destdir, force, jobs):
    srcdir, destdir = Path(srcdir), Path(destdir)
    files = sorted(srcdir.rglob("*.xhtml"))
    if not files:
        sys.exit("no .xhtml files under %s" % srcdir)
    jobspec = [(src, destdir / src.relative_to(srcdir).with_suffix(".json"),
                force) for src in files]

    counts = {"ok": 0, "skipped": 0, "failed": 0}
    failures, warned = {}, {}
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        for i, (src, status, notes) in enumerate(
                pool.map(freeze_one, jobspec, chunksize=16), 1):
            counts[status] += 1
            if status == "failed":
                failures[src] = notes[0]
            elif notes:
                warned[src] = notes
            if i % 500 == 0 or i == len(files):
                print("\r%d/%d (%d failed, %d warned)"
                      % (i, len(files), counts["failed"], len(warned)),
                      end="", flush=True)
    print()

    report = {"source": str(srcdir), "total": len(files), **counts,
              "warned_count": len(warned), "failures": failures,
              "warnings": warned}
    reportpath = destdir / "freeze-report.json"
    with open(reportpath, "w") as fp:
        json.dump(report, fp, ensure_ascii=False, indent=2, sort_keys=True)

    print("%(total)d documents: %(ok)d frozen, %(skipped)d skipped "
          "(fresh), %(failed)d failed" % report)
    print("%d with warnings; full report in %s" % (len(warned), reportpath))
    for src, error in sorted(failures.items())[:20]:
        print("  FAILED %s: %s" % (src, error))
    if len(failures) > 20:
        print("  ... and %d more failures" % (len(failures) - 20))
    sys.exit(1 if failures else 0)


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
    frz = sub.add_parser("freeze", help="batch-normalize a corpus")
    frz.add_argument("srcdir", help="directory tree of parsed .xhtml files")
    frz.add_argument("destdir", help="output tree of golden .json files")
    frz.add_argument("--force", action="store_true",
                     help="re-normalize even if output is fresh")
    frz.add_argument("--jobs", type=int, default=os.cpu_count(),
                     help="parallel workers (default: cpu count)")
    args = parser.parse_args()

    if args.command == "freeze":
        freeze(args.srcdir, args.destdir, args.force, args.jobs)
    elif args.command == "normalize":
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
