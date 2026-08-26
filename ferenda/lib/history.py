"""Read layer over the SFS version history: the versions-stage sidecar and
the amendment-register join that annotates a version list with dates and
förarbeten. Shared by the renderer (the compare panel + andringar view) and
the API (/document/versions) -- which must not import the renderer. Pure
reads over layout's path rules and artifact dicts.
"""

import json

from . import layout


def versions(basefile):
    """A statute's parsed historical consolidations, oldest first, as
    (version, uri) pairs from the versions-stage sidecar. Empty when the
    stage hasn't run or the statute has no archived history."""
    sidecar = layout.sfs_versions_sidecar(basefile)
    if not sidecar.exists():
        return []
    return [(e["version"], e["uri"])
            for e in json.loads(sidecar.read_text())["versions"]]


def amendment_info(art):
    """version id -> (ikraft date, förarbete identifiers) from a statute
    artifact's amendment register, keyed by the amendments' "SFS "-prefixed
    dcterms:identifier -- what annotates a consolidation in the version
    panel and the versions endpoint."""
    info = {}
    for am in art.get("amendments", []):
        ident = am.get("properties", {}).get("dcterms:identifier", "")
        if ident.startswith("SFS "):
            info[ident[4:]] = (
                am["properties"].get("rpubl:ikrafttradandedatum"),
                am.get("forarbeten", []))
    return info
