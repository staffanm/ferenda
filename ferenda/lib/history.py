"""Read layer over a document's version history: the versions-stage sidecar
and the amendment-register join that annotates a version list with dates and
förarbeten. Shared by the renderers (the compare panel + andringar view) and
the API (/document/versions) -- which must not import a renderer. Pure
reads over layout's path rules and artifact dicts. Two sources keep a
history today: sfs (archived rkrattsbaser consolidations) and eurlex
(CONSLEG wordings); `layout.versions_sidecar` is the dispatch.
"""

import json

from . import layout


def versions(source, basefile):
    """A document's parsed historical consolidations, oldest first, as
    (version, uri) pairs from the versions-stage sidecar. Empty when the
    stage hasn't run or the document has no archived history."""
    sidecar = layout.versions_sidecar(source, basefile)
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
