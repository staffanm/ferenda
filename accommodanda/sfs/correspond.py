"""Derive the old-law -> new-law paragraf correspondence map for a restructured
statute, with an LLM, from the proposition's författningskommentar.

When a law is re-enacted in a new structure (säkerhetsskyddslagen 1996:627 ->
2018:585), the proposition's FK states, paragraf by paragraf, how the new
provision relates to the old one -- but in heterogeneous prose ("som delvis
motsvarar 1 § 1996 års säkerhetsskyddslag", "har förts över från 10 § andra
stycket …", "saknar motsvarighet") that a regex cannot reliably tell apart from
an incidental citation or a negation. So, like eurlex ai-annotate, an explicit
opt-in LLM pass reads the (paragraf-segmented) FK plus both laws' paragraf
inventories and emits {newParagraf, oldParagraf, relation, scope} edges.

Every edge is mechanically validated before it is kept: both endpoints must be
real paragrafs in the two laws, the relation/scope must be in the controlled
vocabulary, and the model's supporting quote must occur in the FK text -- so a
hallucinated anchor or an invented sentence is dropped, not stored. The result
is written by the caller as a `.corr` layer in the curated store (lib.annstore),
the mirror of the förarbete genomför edges.

The LLM is called only from `correspond`, reached only by `lagen sfs
ai-correspond`; never from a corpus-wide parse/relate/generate.
"""

import json
import re
from pathlib import Path

from ..lib import llm
from ..lib.util import normalize_fold as _norm

PROMPT = Path(__file__).with_name("correspondence_prompt.txt")
RELATIONS = {"motsvarar", "overfort"}
SCOPES = {"helt", "i_sak", "i_huvudsak", "delvis", None}
QUOTE_KEY = 40         # chars of a quote's normalised prefix that must occur in FK


def paragraf_index(art):
    """Ordered [(anchor, label)] for every paragraf in an SFS artifact: 'K3P17' ->
    '3 kap. 17 §' in a chaptered law, 'P32a' -> '32 a §' in a flat one. The anchor
    is the fragment id the renderer emits; the label is how the FK names the
    paragraf, so the model can map a cited "32 a §" onto the right anchor."""
    out = []

    def walk(nodes, kap=None):
        for n in nodes:
            if n.get("type") == "kapitel":
                walk(n.get("children", []), n.get("ordinal"))
                continue
            if n.get("type") == "paragraf" and n["id"] is not None:
                # id-suppressed paragrafs (temporal/dedup, nf.IdMinter) have
                # no anchor to link to -- offering them to the model would
                # only invite edges that validate_edges must then drop
                ordn = n.get("ordinal")
                out.append((n["id"], "%s kap. %s §" % (kap, ordn) if kap
                            else "%s §" % ordn))
            if n.get("children"):
                walk(n["children"], kap)

    walk(art["structure"])
    return out


def detect_old_law(new_art):
    """The SFS uri the new statute repeals, read from its transition provisions:
    the `dcterms:references` to another whole SFS in a stycke/punkt that mentions
    'upphäv' ("Genom lagen upphävs säkerhetsskyddslagen (1996:627)"). None if no
    such reference is found -- then the caller must be given the old law."""
    found = []

    def walk(node):
        if isinstance(node, list):
            for x in node:
                walk(x)
        elif isinstance(node, dict):
            runs = node.get("text")
            if isinstance(runs, list) and "upphäv" in _norm(
                    "".join(r if isinstance(r, str) else r.get("text", "")
                            for r in runs)):
                for r in runs:
                    uri = r.get("uri", "") if isinstance(r, dict) else ""
                    if (r.get("predicate") if isinstance(r, dict) else None) \
                            == "dcterms:references" and re.search(r"/\d{4}:\d+$", uri):
                        found.append(uri)
            for key in ("children", "content"):
                walk(node.get(key, []))

    walk(new_art.get("amendments", []))
    return found[0] if found else None


def validate_edges(raw, new_anchors, old_anchors, old_uri, fk):
    """Keep only the model's edges that check out: both endpoints are real
    paragrafs, relation/scope are in the controlled vocabulary, and the supporting
    quote actually occurs in the FK text (a 40-char prefix, whitespace/case
    normalised). Drops hallucinated anchors and invented quotes. Returns
    (edges, rejected)."""
    norm_fk = _norm(fk)
    edges, rejected = [], []
    for e in raw:
        new_a, old_a, key = (e.get("newParagraf"), e.get("oldParagraf"),
                             _norm(e.get("quote", ""))[:QUOTE_KEY])
        if (new_a in new_anchors and old_a in old_anchors
                and e.get("relation") in RELATIONS and e.get("scope") in SCOPES
                and key and key in norm_fk):
            edges.append({"newParagraf": new_a, "oldParagraf": old_a,
                          "oldUri": old_uri + "#" + old_a,
                          "relation": e["relation"], "scope": e.get("scope"),
                          "quote": e["quote"].strip()})
        else:
            rejected.append(e)
    return edges, rejected


def _inventory(idx):
    # dict.fromkeys dedupes the few paragraf anchors a consolidated structure
    # repeats, keeping document order, so the model isn't fed a line twice
    return "\n".join("%s = %s" % kv for kv in dict.fromkeys(idx))


def build_prompt(new_idx, old_idx, fk):
    return (PROMPT.read_text()
            .replace("[[NEW_LAW]]", _inventory(new_idx))
            .replace("[[OLD_LAW]]", _inventory(old_idx))
            .replace("[[KOMMENTAR]]", fk))


def correspond(new_art, prop_art, old_art, fk):
    """Derive and validate the correspondence edges from the proposition's
    författningskommentar text `fk` (extracted by `forarbete.kommentar.fk_section`
    -- reading the proposition artifact is förarbete's job, so build composes the
    two verticals rather than sfs importing forarbete); return (payload, stats).
    The payload is `{"correspondence": {...}}`, with the new-law paragraf anchors
    relative to `new_art` (the caller stores it keyed by the new statute)."""
    new_idx, old_idx = paragraf_index(new_art), paragraf_index(old_art)
    if not fk:
        # validated (not asserted) before the LLM spend: a missing FK
        # subsection is bad input data, not a programming bug
        # (rule:errors-drive-retry-use-raise)
        raise ValueError("no författningskommentar subsection for %s in %s"
                         % (new_art["uri"], prop_art.get("identifier")))
    raw = json.loads(llm.complete(build_prompt(new_idx, old_idx, fk)))
    edges, rejected = validate_edges(
        raw.get("correspondences", []), {a for a, _ in new_idx},
        {a for a, _ in old_idx}, old_art["uri"], fk)
    sidecar = {"correspondence": {"newLaw": new_art["uri"],
                                  "oldLaw": old_art["uri"],
                                  "proposition": prop_art["uri"], "edges": edges}}
    return sidecar, {"raw": len(raw.get("correspondences", [])),
                     "emitted": len(edges), "rejected": len(rejected)}


def corr_rows(sidecar):
    """The catalog rows (new_uri, old_uri, relation, scope, prop_uri) for one
    `.corr` sidecar -- each edge's new-law paragraf anchor joined to the new law's
    uri, the old paragraf carried as its full uri. The relate post-pass loads
    these into the `correspondence` table (catalog.set_correspondence)."""
    c = sidecar["correspondence"]
    return [(c["newLaw"] + "#" + e["newParagraf"], e["oldUri"],
             e["relation"], e.get("scope"), c.get("proposition"))
            for e in c["edges"]]
