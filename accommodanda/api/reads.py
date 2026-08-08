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

from opensearchpy.exceptions import OpenSearchException

from ..lib import catalog, inbound, pins
from . import db


class SearchUnavailable(RuntimeError):
    """Full-text search cannot answer -- the OpenSearch cluster is down or the
    index missing. Both faces surface this as a visible error (REST: 503,
    MCP: a tool error), never as a silently smaller answer: a degraded reply
    reads as "the corpus holds nothing else", which is worse than failing."""


class InboundUnavailable(RuntimeError):
    """The inbound-citation sidecar tree is not built on this corpus."""


def search(index, query, *, source=None, kind=None, year=None, limit,
           offset=0, cursor=None):
    """Full-text hits plus the pinned citation resolution:
    {query, total, next_cursor, facets, results}. A client error (bad cursor,
    bad field) raises ValueError; an unreachable cluster raises
    SearchUnavailable."""
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
    if offset == 0 and not cursor and not year and db.catalog_ready():
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
    uri, source, kind, label, title, path = row
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
