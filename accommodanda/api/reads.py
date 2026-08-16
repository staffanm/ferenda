"""One read path for the corpus facts both service faces answer with.

The REST endpoints (`api/app.py`) and the MCP tools (`api/mcp.py`) serve the
same six answers: search, document list, one document, inbound citations,
outbound citations, sources. Before this module each face built its answer
separately, and they drifted: REST let a search-cluster failure escape as a
raw 500 while MCP silently shrank its answer to citation-only results, and
the two inbound endpoints grew disjoint filters (`scope` on REST, `source`
on MCP). Every function here returns plain dicts/lists; a face adds only its
own presentation (pydantic models and facet labels on REST, search-hit `id`s
on MCP) and its own error idiom for the typed failures raised here.
"""

import collections

from opensearchpy.exceptions import OpenSearchException

from ..lib import catalog, facets, inbound, pins
from ..lib.pinpoint import (
    citation_label,
    is_change_marker,
    pinpoint_label,
    short_name,
    unit_anchor,
)
from . import db


class SearchUnavailable(RuntimeError):
    """Full-text search cannot answer -- the OpenSearch cluster is down or the
    index missing. Both faces surface this as a visible error (REST: 503,
    MCP: a tool error), never as a silently smaller answer: a degraded reply
    reads as "the corpus holds nothing else", which is worse than failing."""


class InboundUnavailable(RuntimeError):
    """The inbound-citation sidecar tree is not built on this corpus."""


def search(index, query, *, source=None, kind=None, year=None, limit,
           offset=None, cursor=None):
    """Full-text hits plus the pinned citation resolution:
    {query, total, next_cursor, facets, results}. `offset=None` is the
    forward stream (title-cluster capped, cursor-paged); an explicit offset
    (0 included) is raw bounded random access -- see SearchIndex.search.
    A client error (bad cursor, bad field) raises ValueError; an unreachable
    cluster raises SearchUnavailable."""
    try:
        res = index.search(query, source=source, kind=kind, year=year,
                           limit=limit, offset=offset, cursor=cursor)
    except OpenSearchException as exc:
        raise SearchUnavailable("full-text search is unavailable (%s)"
                                % type(exc).__name__) from exc
    results, total = res["results"], res["total"]
    # the resolved target answers a citation-shaped query, so it leads; only on
    # the first page (it is one fixed target, not paginated), and a missing
    # catalog must not fail a full-text search (best-effort, no 503)
    # only on the capped forward first page: an explicit offset (0 included) is
    # a raw random-access walk, and prepending a pin there would push its last
    # raw hit past the page boundary that offset=limit resumes from
    if offset is None and not cursor and not year and db.catalog_ready():
        with db.connection() as con:
            pinned = pins.resolved_results(con, query, source, kind)
        results, total = pins.merge_pinned(pinned, results, total, limit)
    return {"query": query, "total": total, "next_cursor": res["next_cursor"],
            "facets": res["facets"], "results": results}


def documents(con, *, source=None, kind=None, limit, offset):
    """The catalog index page: {total, limit, offset, documents} with one
    lightweight dict per document."""
    root = catalog.data_root(con)      # stored paths are data_root-relative
    docs = [{"uri": uri, "source": src, "kind": kind_, "label": label,
             "title": title, "source_url": source_url,
             "updated": catalog.artifact_updated(root, path)}
            for uri, src, kind_, label, title, source_url, path, _display
            in catalog.documents(con, source, kind, limit, offset)]
    return {"total": catalog.document_count(con, source, kind),
            "limit": limit, "offset": offset, "documents": docs}


def document(con, uri):
    """One document's metadata, full parsed artifact and inbound count -- or
    None when the catalog has no such uri (each face raises its own way)."""
    row = catalog.document(con, uri)
    if not row:
        return None
    uri, source, kind, label, title, path, _descriptive = row
    # synthesized begrepp stubs are real catalog rows with no artifact file
    # (path='') -- served as an empty artifact, like the rendered shell pages
    art = catalog.load_artifact(catalog.data_root(con), path)
    return {"uri": uri, "source": source, "kind": kind, "label": label,
            "title": title, "source_url": art.get("source_url"),
            "inbound_count": catalog.document_inbound_count(con, uri),
            "artifact": art}


def inbound_citations(con, uri, *, scope="tree", source=None, limit, offset):
    """Who cites `uri`. Two orthogonal filters, both available to both faces:
    `scope` narrows the asked-about side ("tree": the uri and everything
    inside it, the default; "exact": only rows naming the uri itself), and
    `source` narrows the citing side to one corpus. `total` counts after both
    filters (it is what paging walks); `by_source` counts the whole scope
    before `source`, so the reply still says what the other corpora hold."""
    root = catalog.data_root(con)
    if not inbound.available(root):
        raise InboundUnavailable("inbound citations not built -- run "
                                 "`lagen all generate`")
    rows = inbound.read(root, uri)
    rows = inbound.scoped(rows, uri) if scope == "tree" else inbound.exact(rows, uri)
    counts = inbound.by_source(rows)
    if source is not None:
        rows = [row for row in rows if row["source"] == source]
    return {"uri": uri, "scope": scope, "source": source, "total": len(rows),
            "by_source": counts, "limit": limit, "offset": offset,
            "citations": rows[offset:offset + limit]}


def outbound(con, uri):
    """Every citation `uri` makes; `hosted` is False when the target is not in
    the corpus (then label/title are absent)."""
    return [{"uri": to_uri, "anchor": anchor, "predicate": predicate,
             "text": text, "label": label, "title": title, "source": src,
             "hosted": src is not None}
            for to_uri, predicate, text, anchor, label, title, src
            in catalog.outbound(con, uri)]


def sources(con):
    """The corpus' sources and their document counts."""
    return [{"source": s, "documents": n}
            for s, n in sorted(catalog.counts(con).items())]


# how much of a document's internal citation graph one reply carries: enough
# for every statute but brottsbalken-class codes, whose long tail of one-off
# cross-references is noise at graph scale (the reply says how much it cut)
INTERNAL_EDGE_CAP = 600


def _graph_side(rows, groups, limit):
    """One direction of a node's neighborhood: the catalog's aggregated rows
    shaped and group-filtered, totals counted over the *filtered* set so the
    reply's numbers describe what its list is drawn from."""
    kept = []
    for uri, n, label, title, source, kind, descriptive in rows:
        group = facets.flow_group(source, kind)
        if groups and group not in groups:
            continue
        kept.append({"uri": uri, "label": label, "title": title,
                     "descriptive": descriptive, "source": source,
                     "kind": kind, "group": group, "n": n})
    return {"total_links": sum(r["n"] for r in kept), "total_docs": len(kept),
            "top": kept[:limit]}


def _graph_internal(con, root, focus_unit):
    """The document's internal citation graph at unit (§/article) level:
    {nodes, edges, truncated}. Change-marker anchors (L1988:942) are the
    change-entry lists, not provisions a reader navigates, and are dropped."""
    edges = collections.Counter()
    for from_anchor, to_uri, n in catalog.graph_internal(con, root):
        a = unit_anchor(from_anchor)
        b = unit_anchor(catalog.fragment(to_uri) or "")
        if not a or not b or a == b or is_change_marker(a) \
                or is_change_marker(b):
            continue
        edges[(a, b)] += n
    ranked = edges.most_common()
    kept, dropped = ranked[:INTERNAL_EDGE_CAP], ranked[INTERNAL_EDGE_CAP:]
    units = {u for (a, b), _n in kept for u in (a, b)}
    units.add(focus_unit)
    degree = collections.Counter()
    for (a, b), n in kept:
        degree[a] += n
        degree[b] += n
    return {"nodes": [{"anchor": u, "label": pinpoint_label(u) or u,
                       "n": degree[u]} for u in sorted(units)],
            "edges": [[a, b, n] for (a, b), n in kept],
            "truncated": len(dropped)}


def graph(con, uri, *, direction="both", groups=None, limit=20):
    """One node's neighborhood in the citation graph, ready to draw -- or None
    when the catalog has no such document. `uri` may name a document or a
    provision (`...#K4P7`): a provision answers with the citers/targets of
    that unit alone, plus the whole document's internal unit graph."""
    root, _, frag = uri.partition("#")
    row = catalog.document(con, root)
    if not row:
        return None
    _uri, source, kind, label, title, _path, descriptive = row
    unit = unit_anchor(frag) if frag else None
    if frag:
        # keyed on the *unit*, not the raw fragment: a deep arrival anchor
        # (#K4P7S2) answers for the § the reply's `pinpoint` names
        in_rows = catalog.graph_anchor_inbound(con, root, unit)
        out_rows = catalog.graph_anchor_outbound(con, root, unit)
        raw_links, _raw_docs = catalog.graph_anchor_out_totals(con, root, unit)
    else:
        in_rows = catalog.graph_inbound(con, root)
        out_rows = catalog.graph_outbound(con, root)
        raw_links, _raw_docs = catalog.graph_out_totals(con, root)
    unresolved = raw_links - sum(r[1] for r in out_rows)
    result = {
        "uri": uri, "root": root, "anchor": frag or None, "unit": unit,
        "pinpoint": (pinpoint_label(unit) or unit) if unit else None,
        # what to *call* this node: the catalog's compact citing name, and the
        # whole citation a reader recognises. "ETS No. 005" is the Treaty
        # Office's filing number, not a name -- the center of the graph read
        # "artikel 6 · ETS No. 005" where a lawyer writes "Artikel 6 EKMR".
        "descriptive": descriptive or None,
        "citation": citation_label(short_name(descriptive) or label, unit or ""),
        "label": label, "title": title, "source": source, "kind": kind,
        "group": facets.flow_group(source, kind),
        "inbound": None, "outbound": None, "internal": None,
    }
    if direction in ("in", "both"):
        result["inbound"] = _graph_side(in_rows, groups, limit)
    if direction in ("out", "both"):
        result["outbound"] = _graph_side(out_rows, groups, limit)
        result["outbound"]["unresolved"] = unresolved
    if frag:
        result["internal"] = _graph_internal(con, root, unit)
    return result
