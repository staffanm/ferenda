"""Full-text search over the parsed corpus, on OpenSearch 3.7.

The search index is derived and rebuildable from artifacts plus the catalog.
It is never a source of truth. It uses field boosts, paragraph-precise hits,
and ``inbound_count`` ranking without a parent-child join. That join's
global ordinals are held in heap and grow with doc count, and at ~1M+ docs (the
full corpus, more once the flat verticals gain structure) they were the dominant
consumer behind the parent circuit breaker.

Instead every unit is a **standalone document** carrying its parent's metadata:

  * one **whole-document** unit per artifact (``is_doc=true``) -- full text +
    metadata + ``inbound_count`` (the "most-hänvisade" ranking signal);
  * one **fragment** unit per id-bearing node (``is_doc=false``) -- the
    §/article/section text + ``pinpoint``, with the parent's title/identifier/
    ``inbound_count`` denormalised on, so a fragment that wins a group still
    carries the document's display data and authority.

A result query scores only whole-document units, giving exact totals and stable
deep pagination via ``search_after``. A second query, bounded to that result
page's document ids, collapses the fragment units to recover each document's
best matching paragraph/article. No join or routing.

Extraction (``doc_actions``) is pure and unit-testable; the cluster round-trip
needs a running OpenSearch (``OPENSEARCH_URL``, default localhost:9200).
"""

import base64
import json
import queue
import re
import sys
import threading
import time

from opensearchpy import OpenSearch, helpers
from opensearchpy.exceptions import ConnectionError as OpenSearchConnectionError
from opensearchpy.exceptions import ConnectionTimeout, TransportError

from .. import config
from . import catalog, catalog_rows, compress, facets, layout, malnummer, text
from .pinpoint import acronym, pinpoint_label

INDEX = "lagen"
# bump when emitted units change without artifact changes. 5: dropped the `all`
# copy_to catch-all (see SEARCH_FIELDS) -- a mapping change, so the index has to
# be recreated rather than migrated, and every unit re-emitted into it. 6: a
# document whose label is its own title, with no number in it, no longer emits
# `identifier` (see _citation_identifier) -- emitted-unit change only, so an
# ordinary incremental index pass refreshes the affected units. 7: a fragment
# unit carries the `heading` its document prints over it, which names a passage
# whose anchor has no citation grammar (a förarbete's "sec745"). A new field,
# added to a live index by `_require_current_schema`'s put_mapping -- no
# recreate -- but every unit has to be re-emitted to fill it, which the bumped
# format forces.
INDEX_FORMAT = "7"
# Deliberately NOT bumped for the `malnummer` field. Only dv artifacts carry the
# key it reads, and editing this file already restales every source's index step
# through the INDEX_CODE fingerprint (build.py) -- so a bump would add nothing
# but a second reason to re-emit. The mapping arrives by put_mapping as usual
# (`_require_current_schema`); one scoped run fills the units:
#     lagen dv index

# Resilience against a busy cluster: a read timeout while OpenSearch is merging
# segments or running a delete_by_query is transient, not fatal. Every index op
# here is idempotent (a bulk re-index overwrites by _id, a re-delete is a no-op),
# so retrying with exponential backoff is always safe.
REQUEST_TIMEOUT = 60      # per-request read timeout (opensearch-py's default is 10s)
DELETE_TIMEOUT = 600      # delete_by_query over a large source can run minutes
DELETE_BATCH = 1024       # doc_uris per terms-delete (well under max_terms_count)
TASK_POLL_SECONDS = 2     # how often to poll a backgrounded delete_by_query task
RETRIES = 6               # backoff attempts before surfacing a transient failure
BACKOFF_CAP = 60          # seconds -- 2, 4, 8, 16, 32, 60, 60 …
POOL_MAXSIZE = 16         # keep-alive connections per host (urllib3 defaults to 1);
                          # enough for the serving threadpool -- `lagen index` sizes
                          # it to its own --jobs
PAUSE_EVERY_BYTES = 512 * 1024 * 1024   # body text shipped between GC-breather
                          # pauses (0 = never). Bytes, not units: heap pressure
                          # tracks the volume of text analysed/buffered, and a
                          # source like sfs fans 11k documents into ~1M tiny §
                          # units, so a unit count would pause every split second.
PAUSE_SECONDS = 5         # how long to idle so the cluster's heap can be reclaimed
_TRANSIENT = (ConnectionTimeout, OpenSearchConnectionError)


def _is_circuit_breaker(exc):
    """True for a parent circuit-breaker trip (HTTP 429): OpenSearch rejecting a
    request because node heap is momentarily saturated -- typically by the very
    reindex/merge we're driving. Unlike a 4xx client error it's transient (GC
    frees heap within seconds), so it's safe to back off and retry."""
    return isinstance(exc, TransportError) and exc.status_code == 429


def _retry(fn, label):
    """Run `fn`, retrying a transient OpenSearch failure -- a read timeout against
    a busy cluster, or a 429 circuit-breaker trip while it's under heap pressure
    -- with exponential backoff; re-raise anything else, and the transient error
    itself once the attempts are spent. Layered under the client's own fast retry
    (`retry_on_timeout`): the client absorbs blips, this absorbs sustained
    busyness (a long merge, a delete, a heap-pressure breaker trip)."""
    for attempt in range(1, RETRIES + 1):
        try:
            return fn()
        except TransportError as e:
            if not (isinstance(e, _TRANSIENT) or _is_circuit_breaker(e)):
                raise
            if attempt == RETRIES:
                raise
            delay = min(BACKOFF_CAP, 2 ** attempt)
            sys.stderr.write(
                "\n  opensearch: %s unavailable (attempt %d/%d) -- retrying in %ds\n"
                % (label, attempt, RETRIES, delay))
            time.sleep(delay)


def _index_version(content_hash):
    """Version stored on an indexed unit.

    Artifact hashes alone cannot notice an index-schema change (such as adding
    the year facet).  Folding a small format version into them makes the next
    ordinary incremental index pass rebuild every affected document once.
    """
    return ("%s:%s" % (INDEX_FORMAT, content_hash)
            if content_hash is not None else None)

# Query-time field boosts (index-time boost was deprecated in ES5; query-time is
# version-safe and identical in effect): the identifier dominates, then title,
# then body. Ranking authority comes from inbound_count (function_score).
#
# No `all` catch-all. It came across from the legacy schema
# (`ferenda/fulltextindex.py`), where every field was `copy_to: ["all"]` *with
# index-time boosts* -- pre-ES5 that was the way to get per-field weighting: you
# queried the one concatenated field and the baked-in boosts did the ranking.
# Moving to query-time boosts made it redundant, but it was carried over and
# then listed here beside the very fields it duplicates. It cost ~8 GB of the
# 52 GB index (a second copy of every body token, positions and all) and
# *diluted* the boosts above, since a body hit scored once as `text` and again
# as `all` -- a title hit led 5:2 rather than the intended 4:1.
#
# Nothing needs it: `simple_query_string` with default_operator AND already
# matches each term against any field independently, so a query split across
# title and body still matches without a concatenated field (which is what a
# catch-all buys under `multi_match: best_fields`, a query shape we do not use).
# Highlighting reads `text`/`title` only. The one thing lost is a quoted phrase
# spanning a field boundary, which is a concatenation artefact, not a phrase.
SEARCH_FIELDS = ["identifier^16", "title^4", "label^3", "text"]

# `malnummer` is deliberately NOT in that list. It is searched as a phrase, by
# `case_number_queries` -- see there for why a per-term field would misrank.
#
# 32, measured, not chosen for symmetry with `identifier^16`: a case number query
# has to beat the noise its own words make. "T 3-08" scores 130 on NJA 2024 s.
# 936, whose label is "FT till T (NJA 2024 s. 936)" -- one letter of it matching
# `identifier^16` -- while the decision actually filed under T 3-08 draws 4.8
# from its body. The phrase itself is worth 88.7 at ^16 (idf over the 23,738
# decisions carrying a case number), which loses; at ^32 it is 177, and the right
# decision leads.
CASE_NUMBER_BOOST = 32

# repealed acts whose repeal is in force are excluded from results; a future,
# not-yet-in-force repeal date (and a null expired) is kept (S6/S7). Evaluated at
# query time against `now` so it stays correct between reindexes.
REPEALED_IN_FORCE = {"range": {"expired": {"lte": "now/d"}}}

# The "acts" tier that outranks every other source at equal relevance (S3):
# Swedish statutes/ordinances (sfs), agency regulations (foreskrift) and EU
# acts + treaties (eurlex, minus its case law -- judgments/AG opinions). The
# bonus is a flat score summed onto the text + inbound score; tune ACT_TIER_BOOST
# against the live index (higher = more decisive tiering).
ACT_TIER_BOOST = 12.0
ACT_TIER_FILTER = {"bool": {"minimum_should_match": 1, "should": [
    {"term": {"source": "sfs"}},
    {"term": {"source": "foreskrift"}},
    {"bool": {"must": [
        {"term": {"source": "eurlex"}},
        {"terms": {"kind": ["regulation", "directive", "decision",
                            "treaty", "act"]}}]}},
]}}

MAPPING = {
    "settings": {
        # 0 replicas: the dev cluster (docker-compose.yml) is single-node, so a
        # replica can never allocate and the index would sit perpetually `yellow`.
        "number_of_replicas": 0,
        # bulk-rebuilt, read-mostly -- refresh rarely so a multi-hundred-thousand
        # doc run isn't flushing constantly (index_source refreshes once at the end).
        "refresh_interval": "60s",
        # DEFLATE instead of LZ4 for the stored fields. This index is
        # page-cache-bound, not CPU-bound: on prod it is 34 GB against ~11 GB of
        # cache on a disk that does ~100 random IOPS, so a search over cold
        # blocks costs 10+ s where a warm one costs ~100 ms. Bytes are the
        # scarce resource and `_source` carries the text of all 14 M fragment
        # docs, so trading decompression CPU for a smaller store buys cache
        # coverage. Static: it applies to segments written after it is set, so a
        # live index needs a reindex (or a force-merge) to convert what is
        # already on disk.
        "codec": "best_compression",
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
            "identifier":    {"type": "text"},
            # the case numbers a court decision was filed under ("T 3-08"), a
            # second citation identity beside the referat number -- multi-valued
            # and non-unique in both directions (see doc_actions), searched as a
            # phrase (see case_number_queries), in one spelling (lib/malnummer).
            # `text`, not `keyword`: a keyword matches only a *quoted* query
            # ("T 3-08" against a keyword field is two terms, T and 3-08, and
            # neither is the value), where the analyzed form also matches
            # "t 3-08". Array values are 100 positions apart (the text default),
            # so a phrase never runs from one case number into the next.
            "malnummer":     {"type": "text"},
            "title":         {"type": "text"},
            "label":         {"type": "keyword"},
            "text":          {"type": "text"},
            "source":        {"type": "keyword"},
            "kind":          {"type": "keyword"},
            "year":          {"type": "keyword"},
            # the declared repeal date (catalog `expired`), so a query-time range
            # can drop acts whose repeal is in force while keeping a future,
            # not-yet-in-force repeal visible (S6/S7). Absent when never repealed.
            "expired":       {"type": "date", "format": "yyyy-MM-dd"},
            "pinpoint":      {"type": "keyword"},
            "inbound_count": {"type": "long"},
            "is_doc":        {"type": "boolean"},    # whole-document unit vs fragment
            # the human heading shown for a hit (catalog_rows.display_title: short name
            # + acronym where the artifact has them, else the full title). Display
            # only -- the full `title` stays the searchable field, so changing the
            # shown label never costs findability.
            "display":       {"type": "keyword", "index": False},
            # display-only copies of the document's identity on a fragment unit
            # (index:false so a title/identifier query matches the WHOLE-DOC unit,
            # not every one of its fragments -- otherwise a title hit would collapse
            # to a random paragraph). Returned in _source for the result label.
            # the heading the document prints over this fragment (index:false,
            # display only -- it names the passage in a result list; making it
            # searchable would score a section's title as if it were the
            # document's). Only on a fragment whose node type prints one.
            "heading":       {"type": "keyword", "index": False},
            "doc_title":     {"type": "keyword", "index": False},
            "doc_label":     {"type": "keyword", "index": False},
            "doc_display":   {"type": "keyword", "index": False},
        }
    }
}

# A whole-document unit carries its artifact's ENTIRE body in `text`; the large
# statutes and förarbeten run past a million characters. OpenSearch refuses to
# highlight a field longer than `index.highlight.max_analyzed_offset` (default
# 1_000_000) and fails the WHOLE search with a 400 -- so a common prefix that
# happens to match one oversized document 500s the endpoint ("arbets", "arbetsmiljöl").
# The query-level `max_analyzer_offset` caps analysis at a fixed offset instead of
# erroring, which also bounds the per-hit highlight cost -- the real match is either
# near the top of the body or, for an id-bearing document, recovered precisely by
# the fragment query below. It MUST stay <= the index's max_analyzed_offset (the
# 1_000_000 default) or the 400 comes back; test_highlight_cap_stays_under_index_limit
# locks that in (the client is mocked, so no unit test can exercise the live path).
#
# Verified live against the pinned OpenSearch 2.9.0 cluster (both compose files):
# the query-level key really is `max_analyzer_offset` (note: NOT the `_analyzed_`
# index-setting spelling) -- the cluster REJECTS an unknown highlight key with
# `x_content_parse_exception: unknown field [max_analyzed_offset] did you mean
# [max_analyzer_offset]?` (so a silent no-op is impossible), and supplying the
# correct key turned `q=arbets` from 400 to 200 (~200ms) against a >1M-char doc.
HIGHLIGHT = {"fields": {"text": {}, "title": {}},
             "max_analyzer_offset": 100_000,
             # the client injects the fragment as innerHTML: html-encode the
             # body (parsed remote content) so only the <em> markers are markup
             "encoder": "html",
             "fragment_size": 150, "number_of_fragments": 2}


# --------------------------------------------------------------------------
# extraction -- artifact + catalog row -> bulk actions (pure)
# --------------------------------------------------------------------------

_LABEL_NUMBER = re.compile(r"\d")


def _citation_identifier(label, title):
    """The `identifier` to index for a document -- or None when its label is not a
    citation identity at all.

    `identifier^16` (SEARCH_FIELDS) exists so a citation-shaped label like
    "SFS 1962:700" or "Prop. 2022/23:106" dominates every other signal. A document
    with no official number, though, is filed under its own heading -- a
    lagrådsremiss by `lib/regeringen.py:lr_identity` -- so its label IS its title,
    and the x16 boost lands on ordinary prose. The live top hit for "olaga hot mot
    journalist" was a lagrådsremiss that drew 43 of its 116 points from
    `identifier:mot` alone: the word "mot" in its title, counted a second time at
    sixteen times the weight.

    Equal label and title is not enough to tell the two apart, because a court
    decision's citation is also its title: all 23,733 catalogued dv decisions have
    label == title == "NJA 2005 s. 417", and so do 683 eurlex regulations (their
    CELEX) and 286 propositioner filed under a bare "Prop. 1993/94:100". A number
    is what separates them -- every one of those carries one, while 93% of the
    2,764 lagrådsremisser with label == title do not, nor do 30,046 begrepp names
    or the 204 kommentar pages labelled "Kommentar". So a label that repeats the
    title and holds no number indexes no identifier, and the document stays fully
    findable through `title` (measured: 11 of 12 sampled concept pages still lead
    their own name, the twelfth at rank 3)."""
    if (label or "").strip() != (title or "").strip():
        return label                    # an identity of its own -- always indexed
    return label if _LABEL_NUMBER.search(label or "") else None


def doc_actions(row, inbound_count, version=None, expired=None):
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
    -- which is what lets `_threaded_bulk` hand them to worker threads at all.

    No `_index` -- index_source passes index= to the bulk helper, so the actions
    follow the SearchIndex instance's index, not a hardcoded constant."""
    uri, source, kind, label, title, path = row
    facet_row = facets.Row(uri, catalog.local(uri), kind, label, title, None)
    year = facets.document_year(source, facet_row)
    shared = {"doc_uri": uri, "source": source, "kind": kind,
              "version": version, "inbound_count": inbound_count}
    # identity fields for the whole-document unit: `label` is a keyword (an exact
    # whole-value match, so a single word never hits it), `identifier` is the
    # x16-boosted text field and is emitted only for a real citation identity
    identity = {"label": label, "title": title}
    if identifier := _citation_identifier(label, title):
        identity["identifier"] = identifier
    if year:
        shared["year"] = year
    if expired:
        shared["expired"] = expired          # repeal date -> query-time filter (S6/S7)
    if not path:
        # a synthesized stub (e.g. a begrepp concept minted from references) has
        # no artifact on disk -- only its identity is searchable: one whole-doc
        # unit carrying its name, no body, no fragments
        yield {"_id": uri, "_source": {**shared, **identity, "uri": uri,
               "is_doc": True, "display": title}}
        return
    raw = compress.read_bytes(path)          # decompressed artifact bytes
    if not raw.strip():
        return
    art = json.loads(raw)
    # the year facet reads the one-date projection, not the raw "date" key --
    # a court decision's date is "avgorandedatum", a väglednings "antagen"
    doc_date = catalog_rows.document_date(art)
    if "year" not in shared and doc_date and re.match(r"\d{4}", doc_date):
        shared["year"] = doc_date[:4]
    # the reader-facing heading, shared with the page and listings: short name +
    # acronym where the artifact carries them, else the full title (catalog)
    display = catalog_rows.display_title(art, title)
    frags = [(fu, ft, fh) for fu, ft, fh
             in text.fragment_texts_and_headings(art) if ft]
    # The whole-document unit also carries the complete body: result paging then
    # operates over exactly one unit per document (exact total + search_after).
    # Fragment units remain for a bounded second query that finds the best
    # paragraph/article pinpoint for each document on the returned page.
    # a published alternate citation with no body span (a JO decision's
    # ämbetsberättelse "JO 1990/91 s. 70") rides the searchable body text, so
    # querying the citation form finds the decision. Field-driven on the
    # metadata key; sources without one contribute nothing.
    alt = art.get("metadata", {}).get("officialReport")
    doc = {**shared, **identity, "uri": uri, "is_doc": True,
           "display": display,
           "text": ((alt + "\n") if alt else "") + text.document_text(art)}
    # the case numbers the decision was filed under. A second way in for a reader
    # who has the case number and not the referat number -- a commentary cites
    # "Högsta domstolens dom 2009-11-03 T 3-08", the referat is NJA 2009 s. 672.
    # An identity, not a key, in both directions: one referat collects several
    # cases decided together (NJA 1992 s. 740 is T 369-91 and T 224-91, printed
    # inside it as I and II), and the same case number reappears in another
    # court's series. Whole-document unit only, like `identifier` -- a case
    # number names the decision, never one paragraph of it. Field-driven on the
    # artifact key; sources without one contribute nothing.
    if numbers := art.get("malnummer"):
        doc["malnummer"] = [malnummer.normalize(n) for n in numbers]
    yield {"_id": uri, "_source": doc}
    # a fragment carries the document's label as `doc_label` -- index:false, so it
    # is the hit's display identifier and never a scored field; the amplification
    # _citation_identifier removes cannot arise here
    for frag_uri, frag_text, frag_heading in frags:
        unit = {**shared, "uri": frag_uri, "is_doc": False,
                "text": frag_text,
                "pinpoint": frag_uri.split("#", 1)[1],
                "doc_title": title, "doc_label": label,
                "doc_display": display}
        if frag_heading:
            unit["heading"] = fragment_heading(frag_heading)
        yield {"_id": frag_uri, "_source": unit}


# --------------------------------------------------------------------------
# query body (pure)
# --------------------------------------------------------------------------

_QUERY_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_QUOTED = re.compile(r'"[^"]*"')


def prefix_query(q):
    """A safe simple-query-string alternative with every ordinary word made a
    prefix.  The original query is still searched (and boosted) alongside it;
    this branch is what lets ``upphovsr`` match the token ``upphovsrätt``.  By
    extracting words instead of appending ``*`` to the raw expression we don't
    turn quotes, parentheses or other simple-query syntax into malformed input.

    A quoted span is carried over whole, never expanded: quotes are how a reader
    asks for exactly this string, and taking its words out of the quotes said the
    opposite.  ``"T 3-08"`` became ``t* 3* 08*``, which under AND matched 43,648
    documents -- a third of the corpus -- and buried the one case actually filed
    under that number.  Words outside the quotes are still prefixed, so
    ``"T 3-08" hovr`` keeps the phrase and completes ``hovrätten``.
    """
    out, pos = [], 0
    for quoted in _QUOTED.finditer(q):
        out += [word + "*" for word in _QUERY_WORD.findall(q[pos:quoted.start()])]
        out.append(quoted.group())
        pos = quoted.end()
    return " ".join(out + [word + "*"
                           for word in _QUERY_WORD.findall(q[pos:])])


def case_number_queries(q):
    """One clause per case number printed in the query -- ``T 3-08``, ``mål nr
    4659-11``, ``Högsta domstolens dom 2009-11-03 T 3-08`` -- and none for a
    query that holds no case number.

    A phrase, not a field in SEARCH_FIELDS. Per-term matching would score the
    parts of a case number separately, and the parts are ordinary numbers: 373
    of 2,109 sampled case numbers hold a year-like token, so ``brott 2009`` would
    have promoted every decision whose case number happens to contain 2009 over
    the documents actually about it -- scored high, because the field is three
    tokens long and boosted. As a phrase the whole number has to appear.

    `lib/malnummer.query_numbers` decides what a case number is, and spells it
    the way the indexed field is spelled, so ``T3-08`` and ``T 3-08`` are one
    query. It is stricter than the printed shape on purpose: "17 kap. 17-18 §§"
    holds no case number, though the corpus does hold a decision numbered 17-18.

    A CJEU case number (``C-199/24``, ``mål C‑199/24``) is the same failure
    class on the eurlex side: the standard analyzer splits it into "c", "199"
    and "24", each an ordinary token, so the judgment itself drowned under
    every document holding those two numbers. There is no separate field to
    phrase over -- an eurlex judgment is *titled* by its case number -- so the
    clause is a phrase on `title` (`lib/malnummer.eu_query_numbers`).
    """
    return ([{"match_phrase": {"malnummer": {"query": number,
                                             "boost": CASE_NUMBER_BOOST}}}
             for number in malnummer.query_numbers(q)]
            + [{"match_phrase": {"title": {"query": number,
                                           "boost": CASE_NUMBER_BOOST}}}
               for number in malnummer.eu_query_numbers(q)])


def _text_query(q):
    exact = {"simple_query_string": {"query": q, "default_operator": "and",
                                      "fields": SEARCH_FIELDS, "boost": 2}}
    prefixed = prefix_query(q)
    if not prefixed:
        # nothing but punctuation to complete -- and then nothing to read a case
        # number out of either, since one is at least two words of digits
        return exact
    return {"bool": {"should": [
        exact,
        {"simple_query_string": {"query": prefixed, "default_operator": "and",
                                  "analyze_wildcard": True,
                                  "fields": SEARCH_FIELDS}},
        *case_number_queries(q),
    ], "minimum_should_match": 1}}


def _facet_filters(source=None, kind=None, year=None, exclude=None):
    values = {"source": source, "kind": kind, "year": year}
    return [{"term": {field: value}} for field, value in values.items()
            if value and field != exclude]


def encode_cursor(sort, seen, by="relevance"):
    raw = json.dumps({"sort": sort, "seen": seen, "by": by},
                     separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor):
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        value = json.loads(raw)
        sort, seen = value["sort"], value["seen"]
        # a cursor minted before orders existed carries no "by": relevance
        by = value.get("by", "relevance")
        if not isinstance(sort, list) or len(sort) != 2:
            raise ValueError
        if not isinstance(seen, int) or seen < 0:
            raise ValueError
        if by not in ("relevance", "citations"):
            raise ValueError
        return sort, seen, by
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("invalid search cursor") from exc


def cursor_state(cursor, sort, offset):
    """(search_after, consumed) off a cursor -- or the fresh-page state. The
    cursor's opaque `sort` values are positions in ONE sort order: replayed
    under another they would bind to different fields, so a mismatch is the
    client's error, said out loud."""
    if not cursor:
        return None, offset or 0
    after, seen, by = decode_cursor(cursor)
    if by != sort:
        raise ValueError("cursor was made under sort=%s -- repeat that sort "
                         "or start over without the cursor" % by)
    return after, seen


def query_body(q, source=None, kind=None, limit=10, offset=0, year=None,
               search_after=None, highlight=True, sort="relevance"):
    """Search one whole-document unit per result.

    The stable ``(_score, doc_uri)`` sort supports ``search_after`` beyond the
    bounded result window, and ``track_total_hits`` gives an exact document
    total. ``fragment_query_body`` separately recovers the best pinpoint for the
    small set of documents on this page. ``sort="citations"`` orders by the
    unit's stored ``inbound_count`` alone (doc_uri tiebreak, so search_after
    still holds); the text query still gates *which* documents match, and
    ``track_scores`` keeps the relevance score on each hit.

    ``highlight=False`` drops the snippets, for a query that ranks more documents
    than the page shows (``SearchIndex.search``'s candidate window): highlighting
    is what a search costs -- 50 hits without it answer in 0.07-0.10s where 10
    hits with it take 0.25-0.35s -- and ``document_highlight_body`` then gets the
    snippets for the page alone.
    """
    filters = _facet_filters(source, kind, year)

    # `post_filter` narrows the returned hits while the aggregations see every
    # text match.  Each facet then applies the *other* selected filters, so after
    # choosing a source the type/year counts remain narrowed but the source list
    # still offers a way out of that choice.
    def facet_agg(field):
        return {"filter": {"bool": {"filter":
                                     _facet_filters(source, kind, year, field)}},
                "aggs": {"values": {
                    "terms": {"field": field, "size": 1000},
                }}}

    body = {
        "from": offset, "size": limit,
        "track_total_hits": True,
        "query": {"function_score": {
            "query": {"bool": {"must": _text_query(q),
                               "filter": [{"term": {"is_doc": True}}],
                               "must_not": [REPEALED_IN_FORCE]}},
            # ranking authority is inbound_count (log1p) plus a flat tier bonus
            # for the *acts* -- statutes, agency regulations and EU acts/treaties
            # -- so a search for a law's name lands on the law itself rather than
            # its preparatory works (S3). Both functions and the text score sum.
            "functions": [
                {"field_value_factor": {"field": "inbound_count",
                                        "modifier": "log1p", "missing": 0}},
                {"filter": ACT_TIER_FILTER, "weight": ACT_TIER_BOOST},
            ],
            "score_mode": "sum",
            "boost_mode": "sum",
        }},
        "post_filter": {"bool": {"filter": filters}},
        "sort": ([{"inbound_count": {"order": "desc", "missing": 0}},
                  {"doc_uri": "asc"}] if sort == "citations"
                 else [{"_score": "desc"}, {"doc_uri": "asc"}]),
        "aggs": {
            "source": facet_agg("source"),
            "kind": facet_agg("kind"),
            "year": facet_agg("year"),
        },
    }
    if highlight:
        body["highlight"] = HIGHLIGHT
    if sort == "citations":
        body["track_scores"] = True
    if search_after is not None:
        body.pop("from")
        body["search_after"] = search_after
    return body


def document_highlight_body(q, uris):
    """Whole-document snippets for the documents on one returned result page.

    Highlighting is per document -- one hit's snippet does not depend on the
    others -- so taking it out of the ranking query and asking for exactly the
    page's documents returns the same fragments for less work."""
    return {
        "size": len(uris),
        "query": {"bool": {"must": _text_query(q),
                           "filter": [{"term": {"is_doc": True}},
                                      {"terms": {"uri": uris}}]}},
        # identity only: the ranking query already returned each document's
        # metadata, and a whole-document `_source` is its entire body
        "_source": ["uri"],
        "highlight": HIGHLIGHT,
    }


# How many matching passages one result carries. A document-level hit answers
# "this document" and a passage answers "and here is where the words stand" --
# one passage per document made the second answer look like the first, since a
# single §/article reads as *the* place the query matched. The old lagen.nu
# search showed up to three (ferenda/fulltextindex.py, `has_child` inner hits);
# three still fits under a hit without turning the result list into a page of
# text.
PASSAGES_PER_HIT = 3

# how many the query asks for. A fragment's text includes its descendants', so
# a §, its stycke and its chapter answer the same query with the same words:
# "företagsrekonstruktion" returned 14 § and 14 § 1 st of SFS 2022:1328 with one
# identical snippet. `distinct_passages` drops the repeats, and asking for more
# than we keep leaves it something to fall back on.
PASSAGE_CANDIDATES = PASSAGES_PER_HIT + 3


def fragment_query_body(q, doc_uris):
    """The matching passages of each document on one returned result page.

    Collapsed by `doc_uri` so each document answers once, with its top
    PASSAGES_PER_HIT fragments as that group's inner hits. The outer hit needs
    nothing but the group key -- a fragment's `_source` carries its whole text,
    and the passages themselves come back from `inner_hits`."""
    return {
        "size": len(doc_uris),
        "query": {"bool": {
            "must": _text_query(q),
            "filter": [{"term": {"is_doc": False}},
                       {"terms": {"doc_uri": doc_uris}}],
        }},
        "_source": ["doc_uri"],
        "collapse": {"field": "doc_uri",
                     "inner_hits": {"name": "passages",
                                    "size": PASSAGE_CANDIDATES,
                                    "_source": ["uri", "pinpoint", "heading"],
                                    "highlight": HIGHLIGHT}},
    }


# Swedish function words whose highlight says nothing about relevance. The index
# analyses `text`/`title` with the standard analyzer -- no Swedish stopword filter
# (verified against the live index settings) -- so every query term is matched and
# marked, and a query like "olaga hot mot journalist" came back with SOU 2016:44
# "Kraftsamling <em>mot</em> antiziganism": a snippet whose only mark is the word
# "mot". Removing the filter at index time would change what matches; this removes
# only the <em> wrapper, at serve time, so what matched is unchanged and what the
# reader sees is the content words. Curated, not derived: a stopword list belongs
# to a language, and only these appear as bare marks in real snippets.
HIGHLIGHT_STOPWORDS = frozenset(
    "och i av på att som för med till mot om en ett den det de eller samt vid "
    "under efter utan mellan genom".split())

_EM = re.compile(r"<em>(.*?)</em>", re.DOTALL)


def strip_stopword_highlights(fragments):
    """Highlight fragments with the marks around bare function words removed --
    the text itself is untouched, only the `<em>` wrapper goes. A fragment left
    with no mark at all carried nothing but function-word matches and is dropped,
    unless it is the only fragment there is (an empty snippet reads as "no match
    in the body", which is worse than a weak one)."""
    kept, marks_only = [], []
    for fragment in fragments:
        cleaned = _EM.sub(
            lambda m: (m.group(1) if m.group(1).strip().lower()
                       in HIGHLIGHT_STOPWORDS else m.group(0)), fragment)
        (kept if "<em>" in cleaned else marks_only).append(cleaned)
    return kept or marks_only[:1]


def _hit_highlight(h):
    """The snippets to show for a hit -- the body's, or the title's where the body
    has none -- with the function-word marks removed."""
    hl = h.get("highlight", {})
    return (strip_stopword_highlights(hl.get("text", []))
            or strip_stopword_highlights(hl.get("title", [])))


# How much of a fragment's heading to keep. It is a line label in a result list,
# not a title: 935 of 962 sampled förarbete headings already fit, and the rest
# are runaway parses ("3 Vidare har hävdats att skattefri försäljning …") that
# would push the marked text off the line.
HEADING_CHARS = 80


def fragment_heading(heading):
    """A fragment's heading trimmed to HEADING_CHARS, on a word boundary."""
    if len(heading) <= HEADING_CHARS:
        return heading
    return heading[:HEADING_CHARS].rsplit(" ", 1)[0] + "…"


def parse_fragment(h):
    """One matching passage of a document: where in it the words stand, and the
    words themselves.

    `label` names the place. First choice is the pinpoint as a reader cites it
    ("3 kap. 1 §", "artikel 47"); where the anchor has no citation grammar -- a
    förarbete section id ("sec745") -- it is the heading the document prints
    over that section ("8.5.1 Samspelet mellan dataskyddsförordningens och
    dataförordningens bestämmelser"), indexed with the fragment. None only where
    the anchor has neither, as an EDPB stycke ("punkt5") has neither."""
    src = h["_source"]
    return {"uri": src["uri"], "pinpoint": src.get("pinpoint"),
            "label": (pinpoint_label(src.get("pinpoint") or "")
                      or src.get("heading") or None),
            "highlight": strip_stopword_highlights(
                h.get("highlight", {}).get("text", []))}


def distinct_passages(passages, limit):
    """The passages to show under one hit: the first `limit` that mark words no
    passage above them already marked. Keyed on the first marked span, since a
    nested provision and its parent highlight the same words in the same order.

    A passage that repeats the DOCUMENT's snippet is kept: it says where those
    words stand, which the snippet does not -- "Artikel 47" under the EU Data
    Act names the article that quotes the act's title."""
    seen, kept = set(), []
    for passage in passages:
        if not passage["highlight"] or passage["highlight"][0] in seen:
            continue
        seen.add(passage["highlight"][0])
        kept.append(passage)
        if len(kept) == limit:
            break
    return kept


def parse_hit(h):
    """Shape one whole-document hit.

    `pin` is the citation-resolved target and stays None here: full text finds
    documents, and the passages it matched inside one are `fragments`. Only
    `pins.resolved_results` -- where the query IS a pinpoint -- sets a pin, and
    only a pin moves the hit's link off the document (see api/README.md).
    """
    src = h["_source"]
    return {
        "uri": src["doc_uri"],
        # the public page path, set here rather than by each consumer: the other
        # producer of this shape (`pins.resolved_results`) has always set it, so
        # both REST and MCP were re-adding it to every full-text row
        "url": layout.page_url(src["doc_uri"]),
        "identifier": src.get("identifier"),
        "title": src.get("title"),
        "display": src.get("display"),
        # the name line where a hit row has one line for the document: the
        # acronym its display heading carries ("GDPR"), None where it has none
        "abbr": acronym(src.get("display")) or None,
        "source": src.get("source"), "kind": src.get("kind"),
        "score": h.get("_score"), "inbound_count": src.get("inbound_count", 0),
        "highlight": _hit_highlight(h),
        "pin": None,
        "fragments": [],
    }


# --------------------------------------------------------------------------
# presentation-side declutter (pure)
# --------------------------------------------------------------------------

# One legislative project prints the SAME title on every step of its
# beredningskedja, and each step is its own document: "Skärpt syn på brott mot
# journalister och vissa andra samhällsnyttiga funktioner" is the title of the
# lagrådsremiss, of Bet. 2022/23:JuU27 and of Prop. 2022/23:106 alike, while
# SOU 2022:2 words it "En skärpt syn på brott mot journalister och utövare av
# vissa samhällsnyttiga funktioner". All four score alike, so all four take a
# slot on page 1 and the reader sees one project four times.
#
# CLUSTER_CAP hits per project keeps the project visible without letting it own
# the page. The candidate window is what the freed slots are filled from.
CLUSTER_CAP = 2
CLUSTER_WINDOW = 3           # candidates ranked per requested hit ...
CLUSTER_WINDOW_MAX = 50      # ... and never more than this many in total
# Token overlap (Jaccard) at which two titles are the same project. Measured over
# the live top-50 of 20 queries: the SOU variant above sits at 0.71 against its
# prop/bet/lr, and a distinct project reaches 0.69 at most -- "Lag (1960:729) om
# upphovsrätt till litterära och konstnärliga verk" against the props that amend
# it, or one agency's penningtvätt-föreskrifter against another's. So 0.7 admits
# the reworded step of a chain and stops before an act merges with its own
# preparatory works. Exact equality is the 1.0 case, and covers a title whose
# words are merely reordered ("Vårdnad om barn m.m." / "om vårdnad om barn m.m.").
CLUSTER_JACCARD = 0.7

_TITLE_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)


def title_tokens(title):
    """A title's word tokens, lowercased and free of punctuation -- so "m.m." and
    "m m" tokenise alike and a trailing comma never separates two clusters."""
    return frozenset(_TITLE_PUNCT.sub(" ", (title or "").lower()).split())


def same_project(a, b):
    """Whether two titles -- as `title_tokens` sets -- name the same legislative
    project (CLUSTER_JACCARD). A title with no words (an untitled document) matches
    nothing, itself included -- otherwise every one of them would collapse into a
    single cluster."""
    if not a or not b:
        return False
    return len(a & b) / len(a | b) >= CLUSTER_JACCARD


def cap_title_clusters(titles, limit, cap=CLUSTER_CAP):
    """Which of `titles` -- one candidate hit each, best-scoring first -- to show
    on a page of `limit`, and how many candidates that consumed.

    Returns `(keep, used)`: `keep` is candidate indices in score order, at most
    `cap` per project; `used` is how far into the candidates the page reached, so
    the caller's cursor can advance past the ones it dropped. When the candidates
    run out before the page is full the capped-out hits come back to fill it, so a
    page is never shorter than the raw query would have made it -- decluttering
    must not cost a reader a result."""
    clusters, keep, capped, used = [], [], [], 0
    for i, title in enumerate(titles):
        if len(keep) == limit:
            break
        used = i + 1
        tokens = title_tokens(title)
        cluster = next((c for c in clusters if same_project(tokens, c[0])), None)
        if cluster is None:
            clusters.append([tokens, 1])
            keep.append(i)
        elif cluster[1] < cap:
            cluster[1] += 1
            keep.append(i)
        else:
            capped.append(i)
    return sorted(keep + capped[:limit - len(keep)]), used


# --------------------------------------------------------------------------
# client wrapper
# --------------------------------------------------------------------------

class SearchIndex:
    """A thin wrapper over the OpenSearch client -- the only place that talks to
    the cluster, so everything above stays pure and testable."""

    def __init__(self, url=None, index=INDEX, pool_maxsize=POOL_MAXSIZE):
        self.index = index
        # urllib3 pools one connection per host by default, so every caller past
        # the first (`_threaded_bulk`'s workers, the API's threadpool) opens a
        # connection the pool then discards on return -- a new TCP handshake per
        # request plus a urllib3 warning each time. Size the pool to the callers.
        self.client = OpenSearch(
            hosts=[url or config.OPENSEARCH_URL], pool_maxsize=pool_maxsize,
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
        # ty infers the heterogeneous MAPPING dict literal's values as a union
        # (str | dict | ...), so `want` includes str, on which .items() is
        # unresolved; at runtime it is always the properties dict.
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

        def go():
            scan = helpers.scan(
                self.client, index=self.index, _source=["version"],
                query={"query": {"bool": {"filter": [
                    {"term": {"source": source}},
                    {"term": {"is_doc": True}}]}}})
            return {hit["_id"]: hit["_source"].get("version") for hit in scan}
        # scan drives a scroll of its own -- a breaker trip mid-scroll must not
        # abort the whole run, so retry it as one unit (the scroll restarts).
        return _retry(go, "indexed_versions(%s)" % source)

    def delete_source_async(self, source):
        """Start a source-wide delete as a background task (returns its task id) --
        the full-reindex path, where the diff-driven per-doc_uri batching in
        `delete_doc_uris` is pure overhead: we're re-indexing everything anyway.
        Backgrounded (`wait_for_completion=False`) so index_source can re-index
        concurrently instead of blocking the first document behind a scan+delete of
        millions of units. Overlapping is safe: bulk actions are unconditional
        `index` (overwrite) ops, while this delete guards its own removals with
        internal versioning + `conflicts=proceed` -- a unit we re-index is never
        deleted (version conflict -> skipped) and a brand-new fragment is invisible
        to the delete's point-in-time scroll. Only true orphans (units of shrunken
        or vanished documents, never re-indexed) are removed. No `refresh`: the
        re-index overwrites surviving _ids regardless, and index_source's trailing
        refresh makes the removals visible."""
        return _retry(lambda: self.client.delete_by_query(
            index=self.index, body={"query": {"term": {"source": source}}},
            conflicts="proceed", wait_for_completion=False,
            request_timeout=REQUEST_TIMEOUT),
            "delete_source_async(%s)" % source)["task"]

    def wait_for_task(self, task_id, label="task"):
        """Block until a backgrounded task (a delete_by_query) reports completed,
        polling the tasks API. Surfaces the delete's own failures once done."""
        while not _retry(lambda: self.client.tasks.get(task_id=task_id),
                         "%s poll" % label)["completed"]:
            time.sleep(TASK_POLL_SECONDS)

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
        jobs>1 fans the round-trips across worker threads; the action generator is
        still pulled single-threaded (by the feeder), so no DB handle is shared
        across threads. Returns (indexed, errors)."""
        common = dict(index=self.index, chunk_size=200,
                      max_chunk_bytes=5 * 1024 * 1024,
                      request_timeout=REQUEST_TIMEOUT)
        if jobs > 1:
            return self._threaded_bulk(actions, jobs, common)
        return helpers.bulk(self.client, actions, raise_on_error=False,
                            max_retries=RETRIES, initial_backoff=2,
                            max_backoff=BACKOFF_CAP, **common)  # ty: ignore[invalid-argument-type]  # **common widens kwargs to object

    def _threaded_bulk(self, actions, jobs, common):
        """`jobs` worker threads, each running `streaming_bulk` over its share of
        `actions`, fed from one queue so the generator itself stays
        single-threaded.

        Not `helpers.parallel_bulk`, which is the obvious call and was the one
        here: it hands each chunk straight to `_process_bulk_chunk` and takes no
        `max_retries` at all, so a chunk the cluster rejects under load (429, or a
        circuit breaker) fails every item in it *permanently* and the units are
        silently missing from the index. One rebuild lost 1,497 eurlex and 241
        förarbete documents that way. `streaming_bulk` is the helper that owns the
        retry-with-backoff loop, so each worker runs that instead."""
        work = queue.Queue(maxsize=jobs * 4)
        indexed, errors, failures, lock = 0, [], [], threading.Lock()

        def worker():
            nonlocal indexed
            # whether this worker has taken its sentinel: there is exactly one per
            # worker, so the post-failure drain below must not wait for a second
            drained = False

            def drain():
                nonlocal drained
                while (action := work.get()) is not None:
                    yield action
                drained = True

            try:
                for ok, item in helpers.streaming_bulk(
                        self.client, drain(), raise_on_exception=False,
                        raise_on_error=False, max_retries=RETRIES,
                        initial_backoff=2, max_backoff=BACKOFF_CAP, **common):
                    with lock:
                        if ok:
                            indexed += 1
                        else:
                            errors.append(item)
            except Exception as exc:  # noqa: BLE001 — thread boundary: an exception
                # left in a worker is lost, so it is marshalled to the feeder and
                # re-raised there (below) rather than swallowed (rule:no-catch-log-continue).
                # `raise_on_exception=False` only covers a TransportError inside
                # the chunk; a SerializationError on a bad action is not a
                # TransportError and escapes regardless. Draining continues
                # deliberately: a worker that simply stops leaves the feeder
                # blocked on a queue nobody empties -- with every worker dead that
                # is a hang, not a crash, and `lagen index` sits there forever.
                # Only until this worker's own sentinel, though: the last chunk is
                # flushed *after* `drain` takes it, so a failure there would
                # otherwise wait on a sentinel that has already been consumed --
                # the same deadlock, one window narrower.
                with lock:
                    failures.append(exc)
                while not drained and work.get() is not None:
                    pass

        workers = [threading.Thread(target=worker, daemon=True)
                   for _ in range(jobs)]
        for w in workers:
            w.start()
        try:
            fed = 0
            for action in actions:
                work.put(action)
                fed += 1
        finally:
            for _ in workers:            # one sentinel each, then drain out
                work.put(None)
            for w in workers:
                w.join()
        if failures:
            raise failures[0]
        if fed != indexed + len(errors):
            # every action `streaming_bulk` is handed yields exactly one outcome,
            # so a shortfall means units went to the cluster unaccounted for --
            # the silent under-indexing this whole path exists to stop. Raise, not
            # assert: `python -O` would strip the one check that catches it.
            raise ValueError(
                "index: fed %d actions but accounted for %d indexed + %d failed"
                % (fed, indexed, len(errors)))
        return indexed, errors

    def index_source(self, con, source, progress=None, jobs=1, force=False,
                     inbound_counts=None):
        """Sync one source's units to its catalogued documents. Incremental by
        content hash: a document already indexed at its current `content_hash` is
        left untouched; new/changed ones are (re)indexed; units of documents that
        vanished from the catalog -- or whose artifact is gone from disk -- are
        dropped. `force` reindexes every document regardless of hash (a full
        rebuild without deleting the index by hand -- used when the index code
        changed). `jobs>1` parallelises the bulk round-trips. `inbound_counts` is
        the whole-corpus {root_uri: count} map (~7s to build over a 10M-row link
        table); pass it in to compute it once across a multi-source run rather
        than per source. Returns
        (documents, indexed, errors, missing, skipped, deleted)."""
        self.ensure_index()
        rows = con.execute(
            "SELECT uri, source, kind, label, title, path, content_hash, expired "
            "FROM documents WHERE source = ? ORDER BY uri", (source,)).fetchall()
        # stored paths are data_root-relative (portable catalog); resolve to
        # absolute so the missing-artifact check and doc_actions (which reads the
        # artifact bytes) work in absolute paths. A stub's empty path stays empty.
        root = catalog.data_root(con)
        rows = [(*r[:5], str(root / r[5]) if r[5] else r[5], r[6], r[7]) for r in rows]
        present = {row[0] for row in rows}
        # a full reindex ignores prior versions: skip the whole-source scan (which
        # runs for minutes on a large source, all before the first doc is indexed)
        # and, below, drop the source in one delete instead of diffing per doc.
        have = {} if force else self.indexed_versions(source)

        todo, missing, skipped = [], [], 0
        for row in rows:
            uri, path, chash = row[0], row[5], row[6]
            if path and not compress.exists(path):   # artifact stored precompressed
                # the catalog points at an artifact removed since the last relate;
                # skip it (re-run relate to prune the stale row for good). A
                # path-less row is a synthesized stub (no artifact) -- not missing.
                missing.append(catalog.local(uri))
            elif (not force and chash is not None
                  and have.get(uri) == _index_version(chash)):
                skipped += 1                          # already current -- skip
            else:
                todo.append(row)

        # drop stale units before (re)indexing. Full reindex: one source-wide
        # delete (a single refresh) beats scan + per-doc_uri batches. Incremental:
        # only docs gone from the catalog, plus prior units of the ones we're
        # re-indexing (a changed doc may have shed fragments, whose stale units a
        # same-_id overwrite wouldn't reach). New docs aren't indexed yet.
        if force:
            # background the delete and index concurrently -- see delete_source_async
            delete_task = self.delete_source_async(source)
            deleted = len(present)
        else:
            delete_task = None
            stale = (set(have) - present) | {r[0] for r in todo if r[0] in have}
            self.delete_doc_uris(stale)
            deleted = len(stale)

        # everything the threaded bulk needs, read from the DB up front (the action
        # generator must touch no DB handle -- see doc_actions / _bulk). The caller
        # may hand in the (expensive, corpus-wide) inbound-count map so a run over
        # many sources builds it once instead of per source.
        if inbound_counts is None:
            inbound_counts = catalog.document_inbound_counts(con)
        counts = {r[0]: inbound_counts.get(r[0], 0) for r in todo}

        def actions():
            since_pause = 0
            for i, row in enumerate(todo):
                for action in doc_actions(row[:6], counts[row[0]],
                                          version=_index_version(row[6]),
                                          expired=row[7]):
                    yield action
                    since_pause += len(action["_source"].get("text", ""))
                    # a GC breather every PAUSE_EVERY_BYTES of body text: the bulk
                    # workers all drain from this single generator (see _bulk), so
                    # pausing it idles the whole pipeline and lets the cluster
                    # reclaim heap -- cheap insurance against the parent breaker on
                    # a big reindex. Gated on text volume, the heap-pressure proxy.
                    # Silent: a printed line here would tear the live \r progress
                    # counter; the pause just freezes it for PAUSE_SECONDS.
                    if PAUSE_EVERY_BYTES and since_pause >= PAUSE_EVERY_BYTES:
                        time.sleep(PAUSE_SECONDS)
                        since_pause = 0
                if progress:
                    progress(i + 1, len(todo), catalog.local(row[0]))

        indexed, errors = (self._bulk(actions(), jobs) if todo else (0, []))
        # ensure the backgrounded full-source delete finished before we call the
        # removals done and make them visible
        if delete_task:
            self.wait_for_task(delete_task, "delete_source(%s)" % source)
        if todo or deleted:
            _retry(lambda: self.client.indices.refresh(index=self.index), "refresh")
        return len(rows), indexed, errors, missing, skipped, deleted

    def search(self, q, source=None, kind=None, limit=10, offset=None,
               year=None, cursor=None, sort="relevance"):
        search_after, seen = cursor_state(cursor, sort, offset)
        # A wider candidate window than the page, so the beredningskedja cap below
        # has hits to fill the slots it frees. Only where the page reads the result
        # stream forward: the cursorless first page, and every cursor page (the
        # cursor advances past each candidate consumed, so the pages stay disjoint
        # and nothing dropped here comes back later). `offset` paging is bounded
        # random access, where page N must line up with `from` -- capping any of
        # its pages would re-show on page 2 candidates page 1 consumed past its
        # limit and never show the capped ones. The mode is the caller's explicit
        # signal: offset given (0 included) is random access, raw on every page;
        # offset absent (None) is the forward stream. An offset defaulting to 0
        # made the two first pages indistinguishable and the offset walk
        # incoherent.
        offset_mode = offset is not None and not cursor
        window = (limit if offset_mode else
                  max(limit, min(limit * CLUSTER_WINDOW, CLUSTER_WINDOW_MAX)))
        res = _retry(lambda: self.client.search(
            index=self.index,
            # `from` must be an int: the mode sentinel None means "first page"
            # here (a None reaching the body is an OpenSearch 400)
            body=query_body(q, source, kind, window, offset or 0, year,
                            search_after, highlight=False, sort=sort)),
            "search")
        candidates = res["hits"]["hits"]
        # `total` and the facet counts stay the raw query's throughout: the cap is
        # presentation, not a filter -- the documents it holds back are still hits.
        keep, used = (
            (list(range(len(candidates))), len(candidates)) if offset_mode else
            cap_title_clusters([hit["_source"].get("title") or ""
                                for hit in candidates], limit))
        hits = [candidates[i] for i in keep]
        aggregations = res.get("aggregations", {})
        raw_total = res["hits"].get("total", len(candidates))
        total = raw_total.get("value", len(candidates)) \
            if isinstance(raw_total, dict) else raw_total

        def buckets(field):
            return [{"value": bucket["key"], "count": bucket["doc_count"]}
                    for bucket in aggregations.get(field, {}).get(
                        "values", {}).get("buckets", [])]

        results = [parse_hit(hit) for hit in hits]
        if results:
            doc_uris = [result["uri"] for result in results]
            # snippets for the page's documents, which the ranking query above no
            # longer carries (see query_body's `highlight`)
            highlight_res = _retry(lambda: self.client.search(
                index=self.index, body=document_highlight_body(q, doc_uris)),
                "document highlight")
            snippets = {hit["_source"]["uri"]: _hit_highlight(hit)
                        for hit in highlight_res["hits"]["hits"]}
            for result in results:
                result["highlight"] = snippets.get(result["uri"], [])
            fragment_res = _retry(lambda: self.client.search(
                index=self.index, body=fragment_query_body(q, doc_uris)),
                "fragment search")
            # the passages are ADDED to the hit; the document keeps its own
            # snippet and stays what the hit links to. Overwriting the snippet
            # with a passage's, and letting the client link to that passage,
            # sent a reader who searched an act's name ("dataförordningen") into
            # the one article that quotes the title (article 47 of the EU Data
            # Act, which amends another regulation).
            passages = {hit["_source"]["doc_uri"]:
                        [parse_fragment(inner) for inner
                         in hit["inner_hits"]["passages"]["hits"]["hits"]]
                        for hit in fragment_res["hits"]["hits"]}
            for result in results:
                result["fragments"] = distinct_passages(
                    passages.get(result["uri"], []), PASSAGES_PER_HIT)

        # the cursor resumes after the last candidate this page CONSUMED, not the
        # last one it showed -- a capped-out hit is decluttered for good, and page 2
        # picks up where page 1 stopped reading
        consumed = seen + used
        next_cursor = (encode_cursor(candidates[used - 1]["sort"], consumed,
                                     sort)
                       if used and consumed < total else None)
        return {"total": total,
                "next_cursor": next_cursor,
                "facets": {field: buckets(field)
                           for field in ("source", "kind", "year")},
                "results": results}

    def store_size(self):
        """Total on-disk size (bytes) of the index -- primaries only, which on the
        single-node clusters here is the whole thing -- or None when the index
        doesn't exist yet. A cluster that can't be reached raises (the ops caller
        renders that as 'unavailable' so the health page still loads)."""
        if not self.client.indices.exists(index=self.index):
            return None
        stats = _retry(lambda: self.client.indices.stats(
            index=self.index, metric="store"), "store stats")
        return stats["_all"]["primaries"]["store"]["size_in_bytes"]
