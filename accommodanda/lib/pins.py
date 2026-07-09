"""Citation-shaped query resolution, shaped as search hits -- the one
implementation behind both the REST `/api/v1/search` endpoint and the MCP
`search`/`resolve_citation` tools.

A query that *is* a citation -- a law nickname/abbr + pinpoint ("avtalslagen
36", "BrB 12:1"), an EU act + article ("GDPR art 32") or a case nickname
("Instagrambilden") -- maps to one exact, fragment-deep target that full-text
can't reach (the name is nowhere in the document). `resolve.resolve` proposes
the target(s); each is confirmed against the catalog (so an alias for a
not-yet-parsed document doesn't surface) and honours the same source/kind
filter, and the document's own label/title/inbound_count are attached so a
pinned hit ranks and renders like any other search hit.
"""

from . import catalog, layout, resolve


def resolved_results(con, q, source=None, kind=None):
    """The resolver's hits for `q`, each shaped like a SearchResult dict
    (uri, url, identifier, title, display, source, kind, inbound_count,
    fragments). Empty when `q` reads as no known citation."""
    out = []
    for hit in resolve.resolve(q):
        if source and hit["source"] != source:
            continue
        root, _, frag = hit["uri"].partition("#")
        row = catalog.document(con, root)
        if not row:
            continue
        _uri, src, kind_, label, title, _path = row
        if kind and kind_ != kind:
            continue
        # the same reader-facing heading the page and full-text hits show (short
        # name + acronym where the artifact has them, else the title) -- stored
        # on the documents row at relate, so no artifact load per resolved hit
        display = catalog.document_display(con, root) or title
        out.append({
            "uri": root, "url": layout.page_url(root),
            "identifier": label, "title": title, "display": display,
            "source": src, "kind": kind_,
            "score": None, "inbound_count": catalog.document_inbound_count(con, root),
            "highlight": [],
            "fragments": ([{"uri": hit["uri"], "pinpoint": frag, "highlight": []}]
                          if frag else []),
        })
    return out


def merge_pinned(pinned, results, total, limit):
    """Lead the full-text `results` with the `pinned` (citation-resolved) hits:
    the resolved target is the answer to a citation-shaped query, so it goes
    first; any full-text row for the same document is dropped (the pinned hit
    is more precise) and `total` counts only the pinned documents full-text
    didn't already find. Returns the merged (results, total), capped at
    `limit`. Shared by the REST /search endpoint and the MCP search tool."""
    if not pinned:
        return results, total
    roots = {p["uri"] for p in pinned}
    kept = [r for r in results if r["uri"] not in roots]
    total += sum(p["uri"] not in {r["uri"] for r in results} for p in pinned)
    return (pinned + kept)[:limit], total
