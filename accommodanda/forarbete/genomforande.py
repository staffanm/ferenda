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

from ..lib import catalog, compress
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


def resolve(con):
    """Re-derive every genomför-direktiv -> SFS-paragraf relation in the catalog
    from the förarbete artifacts' `implements` sections (only the props that
    carry such edges are read). Returns the number of relations pinned."""
    title_idx, path_idx = law_index(con)
    root = catalog.data_root(con)              # stored paths are data_root-relative
    props = con.execute(
        "SELECT DISTINCT d.uri, d.path FROM links l "
        "JOIN documents d ON d.uri = l.from_uri "
        "WHERE l.predicate = 'rpubl:genomforDirektiv' AND d.source = 'forarbete'"
    ).fetchall()
    rows = []
    for prop_uri, prop_path in props:
        art = json.loads(compress.read_bytes(root / prop_path))
        prop_date, prop_label = art.get("date"), art.get("identifier")
        for rec in art.get("implements", []):
            sfs_uri = resolve_law(rec.get("law"), prop_date, title_idx, path_idx)
            anchor = kommentar.paragraf_fragment(rec.get("chapter"),
                                                 rec.get("paragraf"))
            if not (sfs_uri and anchor):
                continue
            by_art = kommentar.pinpoints_by_article(rec.get("pinpoints") or [])
            partial = int(bool(rec.get("partial")))
            for article in rec.get("articles", []):
                pin = ", ".join(by_art.get(article, []))
                rows.append((sfs_uri, anchor, rec["directive"], article,
                             prop_uri, prop_label, pin, partial))
    catalog.set_genomforande(con, rows)
    return len(rows)
