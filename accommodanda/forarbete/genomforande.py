"""Resolve a proposition's genomför-direktiv statements to the SFS paragraf they
transpose -- the cross-document join the parser cannot make (REWRITE.md §7d).

`kommentar.extract` records, per statement, the EU directive article plus the
*law* (the författningskommentar's level-2 rubrik) and the paragraf it comments
on. Pinning that to a statute paragraf needs the SFS corpus, so it runs at relate
time over the catalog:

  * a "lag om ändring i X (YYYY:NN)" rubrik names the amended SFS directly;
  * a new law is named by title only -- matched against the SFS title index, with
    ties (a new law replacing an older same-named one) broken by the SFS whose
    ikraftträdande is the closest date after the proposition.

Each resolved statement becomes a `genomforande` row (rendered in the statute
paragraf's margin) and an sfs-paragraf -> directive-article edge (so the directive
article's inbound shows the implementing statute). Lives in the förarbete vertical
because the rubrik semantics are förarbete-specific; it imports only the shared
catalog (never the SFS vertical -- the statute corpus is read through the catalog).
"""

import json

from ..lib import annstore, catalog, compress, text
from . import kommentar


def law_index(con):
    """norm-title -> [sfs uri] (for new-law title matching) and sfs uri ->
    artifact path (for the ikraftträdande tie-break)."""
    title, path = {}, {}
    root = catalog.data_root(con)              # stored paths are data_root-relative
    for uri, t, p in con.execute(
            "SELECT uri, title, path FROM documents WHERE source = 'sfs'"):
        path[uri] = str(root / p) if p else p
        if t:
            title.setdefault(catalog.norm_title(t), []).append(uri)
    return title, path


def _ikraft(path):
    props = json.loads(compress.read_bytes(path)).get("metadata", {}).get(
        "properties", {})
    return props.get("rpubl:ikrafttradandedatum")


def resolve_law(law, prop_date, title_idx, path_idx):
    """The SFS uri a författningskommentar section's `law` rubrik refers to, or
    None when it cannot be resolved to a statute we hold."""
    sfsnr = kommentar.sfs_number(law)
    if sfsnr:                                       # "lag om ändring i X (YYYY:NN)"
        uri = catalog.BASE + sfsnr
        return uri if uri in path_idx else None
    cand = title_idx.get(catalog.norm_title(kommentar.proposed_name(law)), [])
    if len(cand) == 1:
        return cand[0]
    if len(cand) > 1 and prop_date:                 # new law vs an older namesake
        after = sorted((d, u) for u in cand
                       for d in [_ikraft(path_idx[u])] if d and d > prop_date)
        return after[0][1] if after else None       # closest ikraft after the prop
    return None


def genomforande_layers():
    """prop-uri -> the LLM-authored genomförande edges for it, read from every
    `.ann` layer in the förarbete annstore subtree (the opt-in `ai-genomforande`
    output). Globbed once in `relate` and passed to `resolve`, exactly as the
    `.corr` layers are -- the join is the prop uri the layer records, never a
    catalog path, so it is independent of where the catalog's data_root points
    (a portable catalog on another host, a test's tmp dir). A `.ann` without a
    `genomforande` payload (a future forarbete editorial layer) is skipped."""
    out = {}
    for p in annstore.tree("forarbete").rglob("*.ann"):
        layer = json.loads(p.read_text()).get("genomforande")
        if layer:
            out.setdefault(layer["proposition"], []).extend(layer["edges"])
    return out


def directive_base(uri):
    """The fragment-free form of a directive uri. The mechanical extractor's
    alias resolution yields a fragment-bearing `directive` for a minority of
    edges (a pinpointed citation resolved as-is; measured 51/372 on the
    2025/26 props), while an authored layer always records the base uri --
    so any directive-identity join must reduce both sides through this."""
    return uri.split("#")[0] if uri else uri


def prop_implements(art, layer_edges):
    """The genomför-direktiv edges to resolve for one proposition: `layer_edges`
    (its authored `.ann` genomförande edges, from `genomforande_layers`) when it
    has any, superseding the mechanical `implements` for every directive the
    layer covers (compared fragment-free, `directive_base`), plus the mechanical
    edges for any *other* directive the layer did not map (a prop transposing
    two directives, only one run through ai-genomforande). Without an authored
    layer, the mechanical `implements` alone -- the pass is opt-in."""
    mech = art.get("implements", [])
    if not layer_edges:
        return mech
    authored = {directive_base(e["directive"]) for e in layer_edges}
    return layer_edges + [r for r in mech
                          if directive_base(r.get("directive")) not in authored]


def resolve(con, layers=None):
    """Re-derive every genomför-direktiv -> SFS-paragraf relation in the catalog
    from the förarbete props' genomför-direktiv edges (only the props that carry
    such edges are read -- the authored `.ann` layer from `layers` where present,
    else the mechanical `implements`; `prop_implements`). `layers` is the
    prop-uri -> authored-edges map `genomforande_layers` globs at relate time;
    None (the default, and how the pin tests drive it) means mechanical only.
    Returns the number of relations pinned."""
    layers = layers or {}
    title_idx, path_idx = law_index(con)
    root = catalog.data_root(con)              # stored paths are data_root-relative
    props = con.execute(
        "SELECT DISTINCT d.uri, d.path FROM links l "
        "JOIN documents d ON d.uri = l.from_uri "
        "WHERE l.predicate = 'rpubl:genomforDirektiv' AND d.source = 'forarbete'"
    ).fetchall()
    rows, sfs_ids = [], {}
    for prop_uri, prop_path in props:
        art = json.loads(compress.read_bytes(root / prop_path))
        prop_date, prop_label = art.get("date"), art.get("identifier")
        for rec in prop_implements(art, layers.get(prop_uri)):
            sfs_uri = resolve_law(rec.get("law"), prop_date, title_idx, path_idx)
            anchor = kommentar.paragraf_fragment(rec.get("chapter"),
                                                 rec.get("paragraf"))
            if not (sfs_uri and anchor):
                continue
            # the reference's Swedish-side stycke/punkt pinpoint ("S1", "S3N2"),
            # kept only when the published law actually mints that element id --
            # forgiving: a pinpoint the paragraf doesn't have (the model said
            # "S5" on a two-stycke paragraf, or the law changed since the prop)
            # is disregarded and the paragraf-level reference stands
            sfs_pin = rec.get("sfs") or ""
            if sfs_pin:
                if sfs_uri not in sfs_ids:
                    sfs_ids[sfs_uri] = text.fragment_ids(
                        json.loads(compress.read_bytes(path_idx[sfs_uri])))
                if anchor + sfs_pin not in sfs_ids[sfs_uri]:
                    sfs_pin = ""
            by_art = kommentar.pinpoints_by_article(rec.get("pinpoints") or [])
            partial = int(bool(rec.get("partial")))
            for article in rec.get("articles", []):
                pin = ", ".join(by_art.get(article, []))
                rows.append((sfs_uri, anchor, rec["directive"], article,
                             prop_uri, prop_label, pin, partial, sfs_pin))
    catalog.set_genomforande(con, rows)
    return len(rows)
