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

from ..lib import catalog, facets, inbound, layout, pathgraph, pins
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
           offset=None, cursor=None, sort="relevance"):
    """Full-text hits plus the pinned citation resolution:
    {query, total, next_cursor, facets, results}. `offset=None` is the
    forward stream (title-cluster capped, cursor-paged); an explicit offset
    (0 included) is raw bounded random access -- see SearchIndex.search.
    `sort="citations"` orders the matches by their stored inbound_count
    instead of relevance (the cursor remembers its order and refuses to be
    replayed under another). A client error (bad cursor, bad field) raises
    ValueError; an unreachable cluster raises SearchUnavailable."""
    try:
        res = index.search(query, source=source, kind=kind, year=year,
                           limit=limit, offset=offset, cursor=cursor,
                           sort=sort)
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


def documents(con, *, source=None, kind=None, limit, offset,
              include_expired=False):
    """The catalog index page: {total, limit, offset, documents} with one
    lightweight dict per document. Repealed documents are left out unless
    `include_expired` -- the same rule browse and search apply."""
    root = catalog.data_root(con)      # stored paths are data_root-relative
    docs = [{"uri": uri, "source": src, "kind": kind_, "label": label,
             "title": title, "source_url": source_url,
             "updated": catalog.artifact_updated(root, path)}
            for uri, src, kind_, label, title, source_url, path, _display
            in catalog.documents(con, source, kind, limit, offset,
                                 include_expired)]
    return {"total": catalog.document_count(con, source, kind, include_expired),
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


def inbound_citations(con, uri, *, scope="tree", source=None, sort="rail",
                      limit, offset):
    """Who cites `uri`. Two orthogonal filters, both available to both faces:
    `scope` narrows the asked-about side ("tree": the uri and everything
    inside it, the default; "exact": only rows naming the uri itself), and
    `source` narrows the citing side to one corpus. `total` counts after both
    filters (it is what paging walks); `by_source` counts the whole scope
    before `source`, so the reply still says what the other corpora hold.

    Every row carries the citing document's own `inbound_count` -- how many
    documents cite *it* -- so a caller ranking "the leading cases on this
    paragraf" has the authority signal on the row instead of one lookup per
    citer. It is `catalog.document_inbound_count`'s number, the same one
    `search` and `document` already answer with under the same name.

    `sort="citations"` orders by it, biggest first. That has to count the whole
    scope before paging, not just the page: 893 citers and 13 ms for
    avtalslagen 36 §, 11 693 and 578 ms for the whole of brottsbalken
    (measured 2026-08-21 on a warm dev disk; the query runs on
    `idx_links_to_root` as a covering index, so it is index reads, not table
    reads). `sort="rail"`, the default, keeps the order the site's own context
    rail uses and counts only the page -- which is still a query the endpoint
    did not make before: up to `limit` distinct citers, 2 925 of them on a
    10 000-row page of brottsbalken. It is small against what the request
    already costs, because `inbound.read` is whole-file: 8 ms of counting
    against 260 ms of reading there, 5 ms against 1.85 s on the ECHR, 13 ms
    against 120 ms on avtalslagen 36 § (warm dev disk; prod is HDD-class and
    unmeasured, but the read is the bigger half there too).

    Ties under "citations" fall back to the rail order -- Python's sort is
    stable and that order is total. The *primary* key is not: a citation count
    is recomputed every build, which is exactly why `inbound.sort_key` refused
    to order the file on it. Ordering on it at query time is a deliberate trade,
    but it means `offset` paging under "citations" can drop or repeat a row
    across a rebuild, where "rail" cannot."""
    # both faces validate these at their edge (a pattern on REST, a Literal on
    # MCP), so a bad value here is a third caller's typo -- which must not read
    # as "ordered by citations" while quietly answering in rail order, nor as
    # the whole tree while quietly answering the narrow `exact` question. The
    # scope branch below is an if/else, so an unrecognised value silently means
    # `exact` -- the narrower answer, which is the one that looks like a result.
    assert scope in ("tree", "exact"), "unknown scope %r" % scope
    assert sort in ("rail", "citations"), "unknown sort %r" % sort
    root = catalog.data_root(con)
    if not inbound.available(root):
        raise InboundUnavailable("inbound citations not built -- run "
                                 "`lagen all generate`")
    rows = inbound.read(root, uri)
    rows = inbound.scoped(rows, uri) if scope == "tree" else inbound.exact(rows, uri)
    counts = inbound.by_source(rows)
    if source is not None:
        rows = [row for row in rows if row["source"] == source]
    if sort == "citations":
        cited = catalog.inbound_counts_for(con, {row["uri"] for row in rows})
        rows = sorted(rows, key=lambda row: -cited.get(row["uri"], 0))
        page = rows[offset:offset + limit]
    else:
        page = rows[offset:offset + limit]
        cited = catalog.inbound_counts_for(con, {row["uri"] for row in page})
    return {"uri": uri, "scope": scope, "source": source, "sort": sort,
            "total": len(rows), "by_source": counts, "limit": limit,
            "offset": offset,
            "citations": [dict(row, inbound_count=cited.get(row["uri"], 0))
                          for row in page]}


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


def _graph_side(rows, groups, limit, sort="links", grouplimit=None):
    """One direction of a node's neighborhood: the catalog's aggregated rows
    shaped and group-filtered, totals counted over the *filtered* set so the
    reply's numbers describe what its list is drawn from.

    `sort="citations"` ranks by the neighbour's own citedness (the stamped
    `inbound_count`) instead of its ties to the center -- what puts NJA
    landmark cases ahead of one prolific self-referencing SOU. `grouplimit`
    caps how many of one flow group `top` carries, so a node drowning in one
    source type still shows the breadth of its neighborhood; the totals keep
    describing the whole filtered set."""
    kept = []
    for uri, n, label, title, source, kind, descriptive, cited in rows:
        group = facets.flow_group(source, kind)
        if groups and group not in groups:
            continue
        kept.append({"uri": uri, "label": label, "title": title,
                     "descriptive": descriptive, "source": source,
                     "kind": kind, "group": group, "links": n,
                     "inbound_count": cited})
    if sort == "citations":
        # stable: equally-cited neighbours keep their ties-to-center order
        kept.sort(key=lambda r: -(r["inbound_count"] or 0))
    top = []
    per_group = collections.Counter()
    for r in kept:
        if grouplimit is not None and per_group[r["group"]] >= grouplimit:
            continue
        per_group[r["group"]] += 1
        top.append(r)
        if len(top) == limit:
            break
    return {"total_links": sum(r["links"] for r in kept), "total_docs": len(kept),
            "top": top}


def _labelled_row(uri, labels, **extra):
    """One neighbour/ring row off a `graph_labels` answer -- the one shape
    every counts-first path fills in after it knows which rows it carries."""
    label, title, source, kind, descriptive, cited = labels[uri]
    return {"uri": uri, "label": label, "title": title,
            "descriptive": descriptive, "source": source, "kind": kind,
            "group": facets.flow_group(source, kind),
            "inbound_count": cited, **extra}


def _graph_side_unfiltered(con, counts, limit):
    """The same answer as `_graph_side` with no group filter, labelling only
    the `limit` rows the reply carries.

    A group filter has to know every neighbor's group to decide what it keeps,
    so it needs them all labelled. With no filter nothing is dropped: the
    totals are the whole aggregate and the order is `n` descending, both
    already settled by `counts`. Labelling the other 50,504 rows of article 6
    ECHR only to discard them cost most of that reply -- see the note above
    `catalog.graph_inbound_counts`."""
    labels = catalog.graph_labels(con, [uri for uri, _n in counts[:limit]])
    top = [_labelled_row(uri, labels, links=n) for uri, n in counts[:limit]]
    return {"total_links": sum(n for _uri, n in counts),
            "total_docs": len(counts), "top": top}


def _graph_inbound_side(con, root, unit, groups, limit, sort, grouplimit,
                        csr=None):
    """The inbound direction for a document (`unit` None) or one provision.

    A group filter, `sort=citations` and `grouplimit` all need every
    neighbour's group -- and joining `documents` for that measured 5+ s over
    brottsbalken's 12,754 citers on prod's disk (a depth-2 ask 504:ed at
    nginx's 60 s). With the in-memory CSR at hand the same answer comes from
    the counts-only query: group and authority (citing-degree) per candidate
    off the arrays, and `graph_labels` for just the rows the reply carries.
    Ranking under sort=citations is then by CSR in-degree (distinct citing
    documents) while the row still *displays* the stamped inbound_count --
    the same trade the depth rings already make. Without a CSR (not yet
    loaded, or a direct reads caller) the joined path answers as before."""
    if not groups and sort == "links" and grouplimit is None:
        return _graph_side_unfiltered(
            con, catalog.graph_anchor_inbound_counts(con, root, unit)
            if unit else catalog.graph_inbound_counts(con, root), limit)
    if csr is None:
        return _graph_side(
            catalog.graph_anchor_inbound(con, root, unit) if unit
            else catalog.graph_inbound(con, root), groups, limit,
            sort=sort, grouplimit=grouplimit)
    counts_q = (catalog.graph_anchor_inbound_counts(con, root, unit) if unit
                else catalog.graph_inbound_counts(con, root))
    allowed = None if not groups \
        else {pathgraph.GROUP_ID[name] for name in groups}
    kept = []
    total_links = 0
    for uri, n in counts_q:
        i = csr.ids.get(uri)
        if i is None:
            continue             # a citer the CSR predates: next build has it
        if allowed is not None and csr.group[i] not in allowed:
            continue
        total_links += n
        kept.append((uri, n, pathgraph.degree_in(csr, i), csr.group[i]))
    if sort == "citations":
        kept.sort(key=lambda r: (-r[2], -r[1]))
    top_rows = []
    per_group = collections.Counter()
    for uri, n, _deg, gid in kept:
        if grouplimit is not None and per_group[gid] >= grouplimit:
            continue
        per_group[gid] += 1
        top_rows.append((uri, n))
        if len(top_rows) == limit:
            break
    labels = catalog.graph_labels(con, [uri for uri, _n in top_rows])
    top = [_labelled_row(uri, labels, links=n) for uri, n in top_rows]
    return {"total_links": total_links, "total_docs": len(kept), "top": top}


# how the per-side `limit` is split across the rings of a deep
# neighbourhood: zooming out trades hop-1 breadth for hop-2/3 reach
RING_SHARE = {1: (1.0,), 2: (0.6, 0.4), 3: (0.5, 0.3, 0.2)}


def _graph_expansion(con, csr, root, result, *, direction, depth, limit,
                     groups, grouplimit, sort):
    """The rings beyond hop 1, expanded server-side off the in-memory CSR
    (lib/pathgraph), plus EVERY document-level citation among the returned
    documents -- so the client draws one payload instead of recursively
    asking each frontier node for its own neighbourhood (7 nodes a side and
    a handful of citers each was all the old client-side walk showed).
    Rings rank by each candidate's own citedness (sort=citations) or by how
    many frontier documents it connects to (sort=links); `groups` and
    `grouplimit` gate the rings the same way they gate hop 1."""
    allowed = None if groups is None \
        else {pathgraph.GROUP_ID[name] for name in groups}
    included = {root}
    for side in ("inbound", "outbound"):
        if result[side]:
            included |= {r["uri"] for r in result[side]["top"]}
    nodes = []
    shares = RING_SHARE[depth]
    for side_name, reverse in (("in", True), ("out", False)):
        key = "inbound" if side_name == "in" else "outbound"
        if not result[key]:
            continue
        frontier = [r["uri"] for r in result[key]["top"]]
        for hop in range(2, depth + 1):
            budget = max(1, int(limit * shares[hop - 1]))
            ring = pathgraph.expand(
                csr, frontier, included, reverse=reverse, budget=budget,
                allowed=allowed, grouplimit=grouplimit,
                prefer_ties=(sort == "links"))
            frontier = [uri for uri, _t, _d in ring]
            included |= set(frontier)
            nodes.extend((uri, hop, side_name) for uri, _t, _d in ring)
    labels = catalog.graph_labels(con, [uri for uri, _h, _s in nodes])
    shaped = [_labelled_row(uri, labels, hop=hop, side=side_name)
              for uri, hop, side_name in nodes]
    edges = [list(row)
             for row in catalog.graph_induced_edges(con, sorted(included))]
    return {"nodes": shaped, "edges": edges}


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
    if focus_unit:               # None on a document-level `internal` ask
        units.add(focus_unit)
    degree = collections.Counter()
    for (a, b), n in kept:
        degree[a] += n
        degree[b] += n
    return {"nodes": [{"anchor": u, "label": pinpoint_label(u) or u,
                       "n": degree[u]} for u in sorted(units)],
            "edges": [[a, b, n] for (a, b), n in kept],
            "truncated": len(dropped)}


def card(con, uri):
    """One document's (or provision's) identity card -- what a link popover
    or a details panel shows without loading the artifact: the citing name,
    the short id, the reader-facing address, the citedness, and the
    document's own opening words (`snippet`, stamped at relate; COALESCEd
    with `description` so a catalog stamped before the snippet column still
    answers for every court decision). One indexed row lookup; the graph
    payload deliberately does NOT carry this -- of 300 neighbours one gets
    selected, and this is the call for that one. None for a uri the catalog
    does not hold."""
    root, _, frag = uri.partition("#")
    row = con.execute(
        "SELECT source, kind, label, title, descriptive, short_id, "
        "       NULLIF(source_url, ''), "
        "       COALESCE(NULLIF(snippet, ''), NULLIF(description, '')), "
        "       inbound_count FROM documents WHERE uri = ?",
        (root,)).fetchone()
    if not row:
        return None
    (source, kind, label, title, descriptive,
     short_id, source_url, snippet, cited) = row
    unit = unit_anchor(frag) if frag else None
    return {
        "uri": uri, "root": root,
        "source": source, "kind": kind,
        "group": facets.flow_group(source, kind),
        "label": label, "short_id": short_id, "title": title,
        "descriptive": descriptive or None,
        "citation": citation_label(short_name(descriptive) or label,
                                   unit or ""),
        "pinpoint": (pinpoint_label(unit) or unit) if unit else None,
        "url": layout.page_url(root) + (("#" + frag) if frag else ""),
        "source_url": source_url,
        "snippet": snippet,
        "inbound_count": cited,
    }


def graph(con, uri, *, direction="both", groups=None, limit=20,
          internal=False, sort="links", grouplimit=None, depth=1, csr=None):
    """One node's neighborhood in the citation graph, ready to draw -- or None
    when the catalog has no such document. `uri` may name a document or a
    provision (`...#K4P7`): a provision answers with the citers/targets of
    that unit alone, plus the whole document's internal unit graph. `internal`
    asks for that unit graph on a *document* uri too -- what the explorer's
    zoomed-in structure view draws."""
    root, _, frag = uri.partition("#")
    row = catalog.document(con, root)
    if not row:
        return None
    _uri, source, kind, label, title, _path, descriptive = row
    unit = unit_anchor(frag) if frag else None
    if frag:
        # keyed on the *unit*, not the raw fragment: a deep arrival anchor
        # (#K4P7S2) answers for the § the reply's `pinpoint` names
        out_rows = catalog.graph_anchor_outbound(con, root, unit)
        raw_links, _raw_docs = catalog.graph_anchor_out_totals(con, root, unit)
    else:
        out_rows = catalog.graph_outbound(con, root)
        raw_links, _raw_docs = catalog.graph_out_totals(con, root)
    unresolved = raw_links - sum(r[1] for r in out_rows)
    extras = con.execute(
        "SELECT NULLIF(source_url, '') FROM documents WHERE uri = ?",
        (root,)).fetchone()
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
        # the publisher's own page. For a source the site does not render
        # (tidskriftsartiklar -- page.CITER_STYLE's one external=True), this
        # is where a reader opens the document; the site's /lawreview/ path
        # serves nothing and must never be handed out as a link.
        "source_url": extras[0],
        "inbound": None, "outbound": None, "internal": None,
    }
    # under a deeper view the whole per-side limit is a budget across the
    # rings: hop 1 gives up breadth so hops 2-3 have room (RING_SHARE)
    hop1 = max(1, int(limit * RING_SHARE[depth][0]))
    if direction in ("in", "both"):
        result["inbound"] = _graph_inbound_side(con, root, unit, groups,
                                                hop1, sort, grouplimit,
                                                csr=csr)
    if direction in ("out", "both"):
        result["outbound"] = _graph_side(out_rows, groups, hop1,
                                         sort=sort, grouplimit=grouplimit)
        result["outbound"]["unresolved"] = unresolved
    result["depth"] = depth
    result["expansion"] = None
    if depth > 1:
        assert csr is not None, "depth > 1 needs the pathgraph CSR"
        result["expansion"] = _graph_expansion(
            con, csr, root, result, direction=direction, depth=depth,
            limit=limit, groups=groups, grouplimit=grouplimit, sort=sort)
    if frag or internal:
        result["internal"] = _graph_internal(con, root, unit)
    return result
