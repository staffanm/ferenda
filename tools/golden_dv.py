"""DV golden cross-check (REWRITE.md §4).

The old pipeline's distilled RDF (`site/data/dv/distilled/{COURT}/{id}.rdf`,
15,858 files) is a frozen oracle for court-decision parsing: per case it
records the document URI and its `dcterms:references` set. This compares the
new artifacts against it -- primarily the reference graph (the citation
extraction that powers the inbound links), and as a free side-effect it
confirms the document URIs agree (the case-URI re-minting).

Cases are matched by document URI, which both representations now share
(`dom/{serie}/{year}:{nr}` / `dom/nja/{year}s{page}`). Following the golden
methodology, this is a *change detector*: differences are investigated, not
assumed to be regressions -- the new scanner is known to fill all-or-nothing
holes the old engine left.

    python tools/golden_dv.py [--limit N]
"""

import argparse
import glob
import json
import sys
from pathlib import Path

from rdflib import Graph, URIRef
from rdflib.namespace import DCTERMS, RDF

RPUBL = "http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ#"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from accommodanda.lib import catalog  # noqa: E402

DISTILLED = "site/data/dv/distilled"
ARTIFACTS = "site/data/dv/artifact"
BASE = "https://lagen.nu/"


REFERAT_TYPES = (URIRef(RPUBL + "Rattsfallsreferat"),
                 URIRef(RPUBL + "Rattsfallsnotis"))


def old_refs(rdf_path):
    """(doc_uri, set of referenced uris) from one distilled RDF. The canonical
    case is the subject typed rpubl:Rattsfallsreferat/-notis (the referat URI
    the new pipeline also uses) -- not the separate VagledandeDomstolsavgorande
    *verdict* resource (dom/{court}/{malnr}/{date}) the old pipeline also
    emitted. References (attached to the case or its #-fragment court
    instances) are collected document-wide."""
    g = Graph()
    g.parse(rdf_path)
    doc = None
    for t in REFERAT_TYPES:
        for s in g.subjects(RDF.type, t):
            doc = str(s)
            break
        if doc:
            break
    refs = {str(o) for o in g.objects(None, DCTERMS.references)
            if isinstance(o, URIRef)}
    return doc, {strip_eu_frag(r) for r in refs}


def strip_eu_frag(uri):
    """Normalise a reference uri for set comparison: drop article fragments on
    celex refs and #sid page fragments on förarbeten (the new scanner records
    them, the comparison is at document granularity)."""
    base = uri.split("#", 1)[0]
    return base


def new_refs(art):
    return {strip_eu_frag(run["uri"])
            for _, run in catalog.artifact_links(art)}


def index_new():
    """doc-uri -> new artifact path, over all DV artifacts."""
    out = {}
    for p in glob.glob(ARTIFACTS + "/*.json"):
        raw = Path(p).read_bytes()
        if raw.strip():
            out[json.loads(raw)["uri"]] = p
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--show", type=int, default=10, help="example diffs")
    args = ap.parse_args()

    new_by_uri = index_new()
    print("indexed %d new DV artifacts by uri" % len(new_by_uri))

    rdfs = sorted(glob.glob(DISTILLED + "/*/*.rdf"))
    if args.limit:
        rdfs = rdfs[:args.limit]

    matched = uri_absent = 0
    exact = subset = superset = overlap = disjoint = 0
    tot_old = tot_new = tot_common = 0
    examples = []
    for rp in rdfs:
        doc, o = old_refs(rp)
        if not doc:
            continue
        np_ = new_by_uri.get(doc)
        if not np_:
            uri_absent += 1
            continue
        matched += 1
        n = new_refs(json.loads(Path(np_).read_bytes()))
        tot_old += len(o)
        tot_new += len(n)
        tot_common += len(o & n)
        if o == n:
            exact += 1
        elif o and o <= n:
            superset += 1            # new found everything old did, plus more
        elif n and n <= o:
            subset += 1              # new missed some old refs
        elif o & n:
            overlap += 1
        else:
            disjoint += 1
            if len(examples) < args.show:
                examples.append((doc, sorted(o - n)[:4], sorted(n - o)[:4]))

    print("\nmatched %d cases by uri (%d old RDFs had no new artifact)"
          % (matched, uri_absent))
    if not matched:
        return
    print("reference-set agreement:")
    print("  exact           %6d (%.1f%%)" % (exact, 100 * exact / matched))
    print("  new ⊇ old (+new) %6d (%.1f%%)" % (superset, 100 * superset / matched))
    print("  new ⊊ old (miss) %6d (%.1f%%)" % (subset, 100 * subset / matched))
    print("  partial overlap %6d (%.1f%%)" % (overlap, 100 * overlap / matched))
    print("  disjoint        %6d (%.1f%%)" % (disjoint, 100 * disjoint / matched))
    recall = 100 * tot_common / tot_old if tot_old else 0
    print("\nold-reference recall: %d/%d (%.1f%%)  |  new total refs: %d"
          % (tot_common, tot_old, recall, tot_new))
    for doc, miss, extra in examples:
        print("  %s\n     old-only: %s\n     new-only: %s" % (doc, miss, extra))


if __name__ == "__main__":
    main()
