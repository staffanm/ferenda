"""Full-text search over the parsed corpus, on OpenSearch 2.x.

Derived & rebuildable from artifacts + catalog (REWRITE.md §6) -- the search
index is never a source of truth. It keeps the domain knowledge of the old
``ferenda/fulltextindex.py:ElasticSearchIndex`` (field boosts, paragraph-precise
hits, ``inbound_count`` ranking) but **without a parent-child join**: that join's
global ordinals are held in heap and grow with doc count, and at ~1M+ docs (the
full corpus, more once the flat verticals gain structure) they were the dominant
consumer behind the parent circuit breaker.

Instead every unit is a **standalone document** carrying its parent's metadata,
and search **collapses by ``doc_uri``** to one result per document:

  * one **whole-document** unit per artifact (``is_doc=true``) -- full text +
    metadata + ``inbound_count`` (the "most-hänvisade" ranking signal);
  * one **fragment** unit per id-bearing node (``is_doc=false``) -- the
    §/article/section text + ``pinpoint``, with the parent's title/identifier/
    ``inbound_count`` denormalised on, so a fragment that wins a group still
    carries the document's display data and authority.

A query scores all units, ``collapse`` keeps the top-scoring unit per
``doc_uri`` (usually the matching paragraph), and a ``cardinality`` agg reports
the distinct-document total. No join, no routing -- it scales on a normal heap.

Extraction (``doc_actions``) is pure and unit-testable; the cluster round-trip
needs a running OpenSearch (``OPENSEARCH_URL``, default localhost:9200).
"""

import json
import sys
import time
from pathlib import Path

from opensearchpy import OpenSearch, helpers
from opensearchpy.exceptions import ConnectionError as OpenSearchConnectionError
from opensearchpy.exceptions import ConnectionTimeout

from .. import config
from . import catalog, text

INDEX = "lagen"

# Resilience against a busy cluster: a read timeout while OpenSearch is merging
# segments or running a delete_by_query is transient, not fatal. Every index op
# here is idempotent (a bulk re-index overwrites by _id, a re-delete is a no-op),
# so retrying with exponential backoff is always safe.
REQUEST_TIMEOUT = 60      # per-request read timeout (opensearch-py's default is 10s)
DELETE_TIMEOUT = 600      # delete_by_query over a large source can run minutes
DELETE_BATCH = 1024       # doc_uris per terms-delete (well under max_terms_count)
RETRIES = 6               # backoff attempts before surfacing a transient failure
BACKOFF_CAP = 60          # seconds -- 2, 4, 8, 16, 32, 60, 60 …
_TRANSIENT = (ConnectionTimeout, OpenSearchConnectionError)


def _retry(fn, label):
    """Run `fn`, retrying a transient OpenSearch connection failure (a read
    timeout against a busy cluster) with exponential backoff; re-raise anything
    else, and the transient error itself once the attempts are spent. Layered
    under the client's own fast retry (`retry_on_timeout`): the client absorbs
    blips, this absorbs sustained busyness (a long merge or delete)."""
    for attempt in range(1, RETRIES + 1):
        try:
            return fn()
        except _TRANSIENT:
            if attempt == RETRIES:
                raise
            delay = min(BACKOFF_CAP, 2 ** attempt)
            sys.stderr.write(
                "\n  opensearch: %s timed out (attempt %d/%d) -- retrying in %ds\n"
                % (label, attempt, RETRIES, delay))
            time.sleep(delay)

# Query-time field boosts (index-time boost was deprecated in ES5; query-time is
# version-safe and identical in effect): the identifier dominates, then title,
# then body. `all` is the copy_to catch-all. Ranking authority comes from
# inbound_count (function_score).
SEARCH_FIELDS = ["identifier^16", "title^4", "label^3", "text", "all"]

MAPPING = {
    "settings": {
        # 0 replicas: the dev cluster (docker-compose.yml) is single-node, so a
        # replica can never allocate and the index would sit perpetually `yellow`.
        "number_of_replicas": 0,
        # bulk-rebuilt, read-mostly -- refresh rarely so a multi-hundred-thousand
        # doc run isn't flushing constantly (index_source refreshes once at the end).
        "refresh_interval": "60s",
    },
    "mappings": {
        # strict: a document field absent from this mapping is rejected, never
        # silently dynamic-mapped -- which is exactly how `doc_uri` once became a
        # `text` field (breaking collapse) when collapse-model docs were written
        # into a pre-existing join-model index.
        "dynamic": "strict",
        "properties": {
            "doc_uri":       {"type": "keyword"},   # parent document -- collapse key
            # the artifact content hash this unit was indexed at (catalog
            # content_hash); index_source diffs it to skip unchanged documents
            "version":       {"type": "keyword", "index": False},
            "uri":           {"type": "keyword"},   # this unit (document or fragment)
            "identifier":    {"type": "text", "copy_to": "all"},
            "title":         {"type": "text", "copy_to": "all"},
            "label":         {"type": "keyword", "copy_to": "all"},
            "text":          {"type": "text", "copy_to": "all"},
            "source":        {"type": "keyword"},
            "kind":          {"type": "keyword"},
            "pinpoint":      {"type": "keyword"},
            "inbound_count": {"type": "long"},
            "is_doc":        {"type": "boolean"},    # whole-document unit vs fragment
            # the human heading shown for a hit (catalog.display_title: short name
            # + acronym where the artifact has them, else the full title). Display
            # only -- the full `title` stays the searchable field, so changing the
            # shown label never costs findability.
            "display":       {"type": "keyword", "index": False},
            # display-only copies of the document's identity on a fragment unit
            # (index:false so a title/identifier query matches the WHOLE-DOC unit,
            # not every one of its fragments -- otherwise a title hit would collapse
            # to a random paragraph). Returned in _source for the result label.
            "doc_title":     {"type": "keyword", "index": False},
            "doc_label":     {"type": "keyword", "index": False},
            "doc_display":   {"type": "keyword", "index": False},
            "all":           {"type": "text"},
        }
    }
}

HIGHLIGHT = {"fields": {"text": {}, "title": {}},
             "fragment_size": 150, "number_of_fragments": 2}


# --------------------------------------------------------------------------
# extraction -- artifact + catalog row -> bulk actions (pure)
# --------------------------------------------------------------------------

def doc_actions(row, inbound_count, version=None):
    """Yield the index units for one catalogued document: one whole-document unit
    plus one unit per id-bearing fragment, all standalone (no join/routing) and
    all carrying `doc_uri` (the collapse key) + the document's display metadata,
    `inbound_count` (denormalised onto the fragments so a fragment that wins its
    group still ranks and renders with the document's authority) and `version`
    (the artifact content hash, so a re-index can tell what's already current).
    `row` is a `documents` row (uri, source, kind, label, title, path); the body
    text comes from the artifact JSON on disk.

    Pure: the caller supplies `inbound_count`/`version` (read from the catalog up
    front), so no DB handle is touched while the bulk helper streams these actions
    -- which lets index_source feed parallel_bulk from a pool thread safely.

    No `_index` -- index_source passes index= to the bulk helper, so the actions
    follow the SearchIndex instance's index, not a hardcoded constant."""
    uri, source, kind, label, title, path = row
    shared = {"doc_uri": uri, "source": source, "kind": kind,
              "version": version, "inbound_count": inbound_count}
    if not path:
        # a synthesized stub (e.g. a begrepp concept minted from references) has
        # no artifact on disk -- only its identity is searchable: one whole-doc
        # unit carrying its name, no body, no fragments
        yield {"_id": uri, "_source": {**shared, "uri": uri, "is_doc": True,
               "identifier": label, "title": title, "label": label,
               "display": title}}
        return
    raw = Path(path).read_bytes()
    if not raw.strip():
        return
    art = json.loads(raw)
    # the reader-facing heading, shared with the page and listings: short name +
    # acronym where the artifact carries them, else the full title (catalog)
    display = catalog.display_title(art, title)
    frags = [(fu, ft) for fu, ft in text.fragment_texts(art) if ft]
    # the whole-document unit carries the searchable identity; it carries the body
    # `text` ONLY when there are no fragments to hold it (DV/forarbete/eurlex
    # today). When fragments exist they own the body text, so a body-term query
    # matches a fragment (which collapses with a pinpoint), not the document.
    doc = {**shared, "uri": uri, "is_doc": True,
           "identifier": label, "title": title, "label": label, "display": display}
    if not frags:
        doc["text"] = text.document_text(art)
    yield {"_id": uri, "_source": doc}
    for frag_uri, frag_text in frags:
        yield {"_id": frag_uri,
               "_source": {**shared, "uri": frag_uri, "is_doc": False,
                           "text": frag_text,
                           "pinpoint": frag_uri.split("#", 1)[1],
                           "doc_title": title, "doc_label": label,
                           "doc_display": display}}


# --------------------------------------------------------------------------
# query body (pure)
# --------------------------------------------------------------------------

def query_body(q, source=None, kind=None, limit=10, offset=0):
    """The OpenSearch request body for a free-text search. Every matching unit
    (whole-document or fragment) is scored, then `collapse` keeps the top-scoring
    unit per `doc_uri` -- so a query returns one result per document, represented
    by whichever unit matched best (usually the matching paragraph, which carries
    a pinpoint). Ranking is relevance combined (sum) with log1p(inbound_count), so
    a well-matched, heavily-cited statute outranks an equally-matched obscure one.
    A `cardinality` agg on doc_uri gives the distinct-document total."""
    must = {"simple_query_string": {"query": q, "default_operator": "and",
                                    "fields": SEARCH_FIELDS}}
    filt = []
    if source:
        filt.append({"term": {"source": source}})
    if kind:
        filt.append({"term": {"kind": kind}})
    return {
        "from": offset, "size": limit,
        "query": {"function_score": {
            "query": {"bool": {"must": must, "filter": filt}},
            "field_value_factor": {"field": "inbound_count",
                                   "modifier": "log1p", "missing": 0},
            "boost_mode": "sum",
        }},
        "collapse": {"field": "doc_uri"},
        "highlight": HIGHLIGHT,
        "aggs": {"docs": {"cardinality": {"field": "doc_uri"}}},
    }


def parse_hit(h):
    """One collapsed search result: the document (its `doc_uri` + denormalised
    metadata) represented by its best-matching unit. When that unit is a fragment,
    its pinpoint + highlight are surfaced as the single `fragments` entry (the
    shape the API/UI deep-links from); a whole-document match has none."""
    src = h["_source"]
    hl = h.get("highlight", {})
    fragments = ([] if src.get("is_doc") else
                 [{"uri": src["uri"], "pinpoint": src.get("pinpoint"),
                   "highlight": hl.get("text", [])}])
    return {
        "uri": src["doc_uri"],
        "identifier": src.get("identifier") or src.get("doc_label"),
        "title": src.get("title") or src.get("doc_title"),
        "display": src.get("display") or src.get("doc_display"),
        "source": src.get("source"), "kind": src.get("kind"),
        "score": h.get("_score"), "inbound_count": src.get("inbound_count", 0),
        "highlight": hl.get("text", []) or hl.get("title", []),
        "fragments": fragments,
    }


# --------------------------------------------------------------------------
# client wrapper
# --------------------------------------------------------------------------

class SearchIndex:
    """A thin wrapper over the OpenSearch client -- the only place that talks to
    the cluster, so everything above stays pure and testable."""

    def __init__(self, url=None, index=INDEX):
        self.index = index
        self.client = OpenSearch(
            hosts=[url or config.OPENSEARCH_URL],
            timeout=REQUEST_TIMEOUT, max_retries=3, retry_on_timeout=True)

    def ensure_index(self, recreate=False):
        def go():
            if recreate and self.client.indices.exists(index=self.index):
                self.client.indices.delete(index=self.index)
            if not self.client.indices.exists(index=self.index):
                self.client.indices.create(index=self.index, body=MAPPING)
            else:
                self._require_current_schema()
        _retry(go, "ensure_index")

    def _require_current_schema(self):
        """Refuse to index into a pre-existing index whose mapping predates the
        current search schema -- e.g. one created under the old parent-child join,
        where `doc_uri` was dynamically mapped as `text` and collapse/aggregations
        then fail with a cryptic 400 at *search* time. A field type can't be
        changed in place, and the index is fully rebuildable, so fail early with
        the fix rather than indexing into a broken mapping."""
        props = (next(iter(self.client.indices.get_mapping(index=self.index)
                           .values())).get("mappings", {}).get("properties", {}))
        if props.get("doc_uri", {}).get("type") != "keyword":
            raise RuntimeError(
                "OpenSearch index %r has an incompatible mapping (doc_uri is %s, "
                "not keyword) -- it predates the current search schema. Recreate "
                "it (the index is derived & rebuildable):\n"
                "    curl -X DELETE %s/%s\n    lagen all index"
                % (self.index, props.get("doc_uri", {}).get("type", "missing"),
                   config.OPENSEARCH_URL, self.index))
        # additive migration: an index built under an older schema may lack fields
        # the current code emits (e.g. `version` before incremental indexing,
        # `display`/`doc_display` for the reader-facing heading). The strict mapping
        # would reject any unit carrying an unmapped field, so add the missing ones
        # by explicit put_mapping -- allowed under strict (only *dynamic* field
        # introduction is refused). Old units read the new fields back as null, so
        # the next run reindexes the source once, as intended. (A type *change*
        # still can't be migrated -- that is what the doc_uri guard above catches.)
        want = MAPPING["mappings"]["properties"]
        missing = {name: spec for name, spec in want.items()  # ty: ignore[unresolved-attribute]
                   if name not in props}
        if missing:
            self.client.indices.put_mapping(
                index=self.index, body={"properties": missing})

    def exists(self):
        """Whether the index is present in the cluster -- the caller's gate for a
        watermark skip: if the index was dropped, a 'fresh' source must still be
        reindexed rather than skipped into an empty index."""
        return self.client.indices.exists(index=self.index)

    def indexed_versions(self, source):
        """{doc_uri: version} for a source's whole-document units already in the
        index -- the artifact content hash each was indexed at. The is_doc unit's
        _id is the doc_uri, so the scan reads identity + version with no body.
        Drives index_source's diff; empty when the index doesn't exist yet."""
        if not self.client.indices.exists(index=self.index):
            return {}
        scan = helpers.scan(
            self.client, index=self.index, _source=["version"],
            query={"query": {"bool": {"filter": [
                {"term": {"source": source}},
                {"term": {"is_doc": True}}]}}})
        return {hit["_id"]: hit["_source"].get("version") for hit in scan}

    def delete_doc_uris(self, doc_uris):
        """Remove every unit (document + fragments) of the given documents, in
        terms-query batches so the request stays well under OpenSearch's
        max_terms_count regardless of how many documents changed/vanished."""
        uris = list(doc_uris)
        for start in range(0, len(uris), DELETE_BATCH):
            batch = uris[start:start + DELETE_BATCH]
            _retry(lambda b=batch: self.client.delete_by_query(
                index=self.index, body={"query": {"terms": {"doc_uri": b}}},
                refresh=True, conflicts="proceed",
                request_timeout=DELETE_TIMEOUT), "delete_by_query(%d docs)"
                % len(batch))

    def _bulk(self, actions, jobs):
        """Stream `actions` into the index. Chunks are bounded by BYTES, not just
        count: a förarbete/eurlex artifact is full document text, so 500 in one
        request once ballooned past OpenSearch's parent circuit breaker; 5 MB/chunk
        keeps the per-request reservation small regardless of document size.
        jobs>1 fans the round-trips across a thread pool (parallel_bulk); the
        action generator is still pulled single-threaded, so no DB handle is
        shared across threads. Returns (indexed, errors)."""
        common = dict(index=self.index, chunk_size=200,
                      max_chunk_bytes=5 * 1024 * 1024,
                      request_timeout=REQUEST_TIMEOUT)
        if jobs > 1:
            indexed, errors = 0, []
            for ok, item in helpers.parallel_bulk(
                    self.client, actions, thread_count=jobs, queue_size=jobs,
                    raise_on_exception=False, raise_on_error=False, **common):  # ty: ignore[invalid-argument-type]
                if ok:
                    indexed += 1
                else:
                    errors.append(item)
            return indexed, errors
        # single-threaded path keeps the 429 backoff (parallel_bulk has no retry)
        return helpers.bulk(self.client, actions, raise_on_error=False,
                            max_retries=RETRIES, initial_backoff=2,
                            max_backoff=BACKOFF_CAP, **common)  # ty: ignore[invalid-argument-type]

    def index_source(self, con, source, progress=None, jobs=1, force=False):
        """Sync one source's units to its catalogued documents. Incremental by
        content hash: a document already indexed at its current `content_hash` is
        left untouched; new/changed ones are (re)indexed; units of documents that
        vanished from the catalog -- or whose artifact is gone from disk -- are
        dropped. `force` reindexes every document regardless of hash (a full
        rebuild without deleting the index by hand -- used when the index code
        changed). `jobs>1` parallelises the bulk round-trips. Returns
        (documents, indexed, errors, missing, skipped, deleted)."""
        self.ensure_index()
        rows = con.execute(
            "SELECT uri, source, kind, label, title, path, content_hash "
            "FROM documents WHERE source = ? ORDER BY uri", (source,)).fetchall()
        have = self.indexed_versions(source)
        present = {row[0] for row in rows}

        todo, missing, skipped = [], [], 0
        for row in rows:
            uri, path, chash = row[0], row[5], row[6]
            if path and not Path(path).exists():
                # the catalog points at an artifact removed since the last relate;
                # skip it (re-run relate to prune the stale row for good). A
                # path-less row is a synthesized stub (no artifact) -- not missing.
                missing.append(catalog.local(uri))
            elif not force and chash is not None and have.get(uri) == chash:
                skipped += 1                          # already current -- skip
            else:
                todo.append(row)

        # drop units for documents gone from the catalog, plus the prior units of
        # the ones we're re-indexing (a changed doc may have shed fragments, whose
        # stale units a same-_id overwrite wouldn't reach). New docs aren't indexed
        # yet, so they need no pre-delete.
        stale = (set(have) - present) | {r[0] for r in todo if r[0] in have}
        self.delete_doc_uris(stale)

        # everything the threaded bulk needs, read from the DB up front (the action
        # generator must touch no DB handle -- see doc_actions / _bulk)
        counts = {r[0]: catalog.document_inbound_count(con, r[0]) for r in todo}

        def actions():
            for i, row in enumerate(todo):
                yield from doc_actions(row[:6], counts[row[0]], version=row[6])
                if progress:
                    progress(i + 1, len(todo), catalog.local(row[0]))

        indexed, errors = (self._bulk(actions(), jobs) if todo else (0, []))
        if todo or stale:
            _retry(lambda: self.client.indices.refresh(index=self.index), "refresh")
        return len(rows), indexed, errors, missing, skipped, len(stale)

    def search(self, q, source=None, kind=None, limit=10, offset=0):
        res = _retry(lambda: self.client.search(
            index=self.index,
            body=query_body(q, source, kind, limit, offset)), "search")
        # `total` is the distinct-document count (cardinality agg), not the raw
        # unit hits -- collapse dedupes the returned rows but not hits.total.
        total = res.get("aggregations", {}).get("docs", {}).get(
            "value", len(res["hits"]["hits"]))
        return {"total": total,
                "results": [parse_hit(h) for h in res["hits"]["hits"]]}

    def doccount(self):
        return _retry(lambda: self.client.count(index=self.index), "count")["count"]
