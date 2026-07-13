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
import re
import sys
from collections import Counter
from pathlib import Path

from rdflib import Graph, URIRef
from rdflib.namespace import DCTERMS, RDF

RPUBL = "http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ#"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from accommodanda.dv.identity import norm_malnr
from accommodanda.dv.model import Stycke
from accommodanda.dv.parse import decision_date_from_text
from accommodanda.dv.structure import flatten
from accommodanda.lib import (
    casenaming,
    catalog,
    layout,
)

# The distilled RDF oracle is temporary scaffolding, not a long-lived artifact,
# so it is NOT under data_root -- it lives in the old checkout (source-first
# layout). This is only the default; override with --distilled.
DISTILLED_DEFAULT = "../ferenda.old/data/dv/distilled"
BASE = "https://lagen.nu/"


REFERAT_TYPES = (URIRef(RPUBL + "Rattsfallsreferat"),
                 URIRef(RPUBL + "Rattsfallsnotis"))
REFERAT_AV = URIRef(RPUBL + "referatAvDomstolsavgorande")
REFERATRUBRIK = URIRef(RPUBL + "referatrubrik")
AVGORANDEDATUM = URIRef(RPUBL + "avgorandedatum")
MALNUMMER = URIRef(RPUBL + "malnummer")


def norm_literal(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _doc(g):
    for t in REFERAT_TYPES:
        for subject in g.subjects(RDF.type, t):
            return subject
    return None


def old_case(rdf_path):
    """(uri, references, metadata) from one old distilled RDF graph.

    Date and case number live on the linked verdict resource, not the referat
    resource. Keep sets: split cases may link more than one verdict and the
    comparison must show that rather than silently choosing one.
    """
    g = Graph()
    g.parse(rdf_path)
    doc = _doc(g)
    refs = {str(o) for o in g.objects(None, DCTERMS.references)
            if isinstance(o, URIRef)}
    verdicts = set(g.objects(doc, REFERAT_AV)) if doc is not None else set()
    return (str(doc) if doc is not None else None,
            {strip_eu_frag(r) for r in refs},
            {"identifier": sorted(norm_literal(v)
                                  for v in g.objects(doc, DCTERMS.identifier)),
             "referatrubrik": sorted(norm_literal(v)
                                      for v in g.objects(doc, REFERATRUBRIK)),
             "avgorandedatum": sorted({norm_literal(v) for verdict in verdicts
                                        for v in g.objects(verdict, AVGORANDEDATUM)}),
             "malnummer": sorted({norm_literal(v) for verdict in verdicts
                                   for v in g.objects(verdict, MALNUMMER)})})


def old_refs(rdf_path):
    """(doc_uri, set of referenced uris) from one distilled RDF. The canonical
    case is the subject typed rpubl:Rattsfallsreferat/-notis (the referat URI
    the new pipeline also uses) -- not the separate VagledandeDomstolsavgorande
    *verdict* resource (dom/{court}/{malnr}/{date}) the old pipeline also
    emitted. References (attached to the case or its #-fragment court
    instances) are collected document-wide."""
    doc, refs, _ = old_case(rdf_path)
    return doc, refs


def strip_eu_frag(uri):
    """Normalise a reference uri for set comparison: drop article fragments on
    celex refs and #sid page fragments on förarbeten (the new scanner records
    them, the comparison is at document granularity)."""
    base = uri.split("#", 1)[0]
    return base


def new_refs(art):
    return {strip_eu_frag(run["uri"])
            for _, run in catalog.artifact_links(art)}


def new_metadata(art):
    """The artifact fields corresponding to the old RDF facts.

    The new courts API calls the old referatrubrik `sammanfattning`. This is an
    exact normalized comparison, deliberately not a fuzzy title match: a diff is
    evidence to inspect, not something this change detector adjudicates.
    """
    return {"identifier": sorted(norm_literal(v) for v in art.get("referat", [])),
            "referatrubrik": ([norm_literal(art.get("metadata", {}).get(
                "sammanfattning"))] if art.get("metadata", {}).get("sammanfattning")
                                else []),
            "avgorandedatum": sorted(norm_literal(value) for value in
                                      (art.get("avgorandedatum_lista")
                                       or ([art["avgorandedatum"]]
                                           if art.get("avgorandedatum") else []))),
            "malnummer": sorted(norm_literal(v) for v in art.get("malnummer", []))}


def artifact_text_date(art):
    """The artifact date when its formal final-ruling sentence confirms it."""
    def text(runs):
        return "".join(run if isinstance(run, str) else run.get("text", "")
                       for run in runs)

    body = [Stycke(text(block.get("text", [])))
            for block in flatten(art.get("structure", []))]
    return decision_date_from_text(
        body, art["court"], art["court_namn"], art.get("referat", []),
        art.get("avgorandedatum"))


def canonical_metadata(field, values):
    if field == "identifier":
        # Compare published identities, not their display spelling: HFD 2012:41
        # and HFD 2012 ref. 41 are the same referat.  The citation grammar still
        # keeps NJA's page identity distinct from its editorial löpnummer.
        return {casenaming.case_uri(v) for v in values}
    if field == "malnummer":
        out = set()
        for value in values:
            compact = norm_malnr(value)
            match = re.fullmatch(
                r"([A-ZÅÄÖ]+)-?(\d+)-(\d+)-((?:19|20)\d{2})", compact)
            if match:
                series, first, last, year = match.groups()
                first_num, last_num = int(first), int(last)
                # AD's old RDF compresses a consecutive set as A-33-38-2011;
                # the API lists A 33-11 ... A 38-11. Only expand a forward,
                # bounded range so malformed identifiers remain visible.
                if first_num <= last_num <= first_num + 100:
                    out.update("%s%d%s" % (series, num, year[-2:])
                               for num in range(first_num, last_num + 1))
                    continue
            m = re.fullmatch(r"([A-ZÅÄÖ]+)-?(\d+)-((?:19|20)\d{2})", compact)
            if m:
                compact = "%s%s%s" % (m.group(1), m.group(2), m.group(3)[-2:])
            out.add("".join(c for c in compact if c.isalnum()))
        return out
    return set(values)


def metadata_status(old, new):
    if old == new:
        return "exact"
    if not old:
        return "old-missing"
    if not new:
        return "new-missing"
    if old < new:
        return "new-superset"
    if new < old:
        return "new-subset"
    if old & new:
        return "overlap"
    return "disjoint"


def referatrubrik_status(old, new):
    """Separate API field truncation from a substantively different summary.

    The courts API caps a number of summaries at exactly 2,000 characters.
    Prefix agreement is useful evidence only at that boundary; arbitrary prefix
    similarity remains an unexplained difference.
    """
    status = metadata_status(old, new)
    if status == "disjoint" and len(old) == len(new) == 1:
        old_value, new_value = next(iter(old)), next(iter(new))
        # Whitespace normalization can turn the source's 2,000 characters into
        # 1,999, so both lengths represent the same boundary.
        if len(new_value) in (1999, 2000) and old_value.startswith(new_value):
            return "new-truncated"
        if len(old_value) in (1999, 2000) and new_value.startswith(old_value):
            return "old-truncated"
    return status


def index_new():
    """doc-uri -> new artifact path, over all DV artifacts."""
    out = {}
    # layout.artifacts filters out index sidecars (identity-index.json), which a
    # raw glob of the dv artifact dir would choke on (it's a JSON list, not a doc)
    for p in layout.artifacts("dv"):
        raw = Path(p).read_bytes()
        if raw.strip():
            out[json.loads(raw)["uri"]] = p
    return out


def main():
    assert __doc__ is not None
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--show", type=int, default=10, help="example diffs")
    ap.add_argument("--distilled", default=DISTILLED_DEFAULT,
                    help="old-pipeline distilled RDF oracle tree (scaffolding; "
                         "default %(default)s)")
    args = ap.parse_args()

    new_by_uri = index_new()
    print("indexed %d new DV artifacts by uri" % len(new_by_uri))

    rdfs = sorted(glob.glob(args.distilled + "/*/*.rdf"))
    if not rdfs:   # raise, not assert: a missing/mistyped --distilled must fail
                   # loudly (even under `python -O`), not report a false-clean golden
        raise SystemExit("no distilled oracle .rdf files under %s -- the DV golden "
                         "is temporary scaffolding (see --distilled)" % args.distilled)
    if args.limit:
        rdfs = rdfs[:args.limit]

    matched = uri_absent = 0
    exact = subset = superset = overlap = disjoint = 0
    tot_old = tot_new = tot_common = 0
    examples = []
    meta_counts = {field: Counter() for field in
                   ("identifier", "referatrubrik", "avgorandedatum", "malnummer")}
    meta_examples = {}
    for rp in rdfs:
        doc, o, old_meta = old_case(rp)
        if not doc:
            continue
        np_ = new_by_uri.get(doc)
        if not np_:
            uri_absent += 1
            continue
        matched += 1
        art = json.loads(Path(np_).read_bytes())
        n = new_refs(art)
        new_meta = new_metadata(art)
        for field in meta_counts:
            old_values = canonical_metadata(field, old_meta[field])
            new_values = canonical_metadata(field, new_meta[field])
            status = (referatrubrik_status(old_values, new_values)
                      if field == "referatrubrik"
                      else metadata_status(old_values, new_values))
            if (field == "avgorandedatum" and status == "disjoint"
                    and artifact_text_date(art) in new_values):
                status = "text-confirmed"
            meta_counts[field][status] += 1
            if status != "exact":
                meta_examples.setdefault((field, status),
                                         (doc, old_meta[field], new_meta[field]))
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
    print("\nmetadata agreement (change detector; differences need adjudication):")
    for field, counts in meta_counts.items():
        print("  %-17s exact %6d  +new %6d  -new %6d  overlap %6d  "
              "disjoint %6d  old-missing %6d  new-missing %6d  "
              "new-truncated %6d  old-truncated %6d  text-confirmed %6d"
              % (field, counts["exact"], counts["new-superset"],
                 counts["new-subset"], counts["overlap"], counts["disjoint"],
                 counts["old-missing"], counts["new-missing"],
                 counts["new-truncated"], counts["old-truncated"],
                 counts["text-confirmed"]))
        for status in ("disjoint", "overlap", "new-subset", "new-superset",
                       "new-missing", "old-missing"):
            example = meta_examples.get((field, status))
            if example:
                doc, old, new = example
                print("      %s e.g. %s: old=%r new=%r" %
                      (status, doc, old, new))


if __name__ == "__main__":
    main()
