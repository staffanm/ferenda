"""The OpenSearch indexer's pure parts (accommodanda/lib/search.py): artifact ->
bulk actions, the query body, and hit parsing. The cluster round-trip needs a
running OpenSearch and is exercised by the integration test at the bottom, gated
on OPENSEARCH_URL."""

import json
import os

import pytest

from accommodanda.lib import catalog, search


def _build_catalog(tmp_path):
    """Two SFS artifacts where 2018:585 cites 1962:700#K3P1, so 1962:700 has a
    real inbound_count -- exercises the ranking-signal read in doc_actions."""
    art_dir = tmp_path / "artifact"
    art_dir.mkdir()
    bb = art_dir / "bb.json"
    bb.write_text(json.dumps({
        "uri": "https://lagen.nu/1962:700",
        "metadata": {"properties": {"dcterms:title": "Brottsbalk (1962:700)"}},
        "structure": [{"type": "paragraf", "id": "K3P1",
                       "text": ["Den som dödar annan döms för mord."]}]}))
    fl = art_dir / "fl.json"
    fl.write_text(json.dumps({
        "uri": "https://lagen.nu/2018:585",
        "metadata": {"properties": {"dcterms:title": "Förvaltningslag (2018:585)"}},
        "structure": [{"type": "paragraf", "id": "P1",
                       "text": ["Se ", {"uri": "https://lagen.nu/1962:700#K3P1",
                                        "text": "3 kap. 1 §"}, " brottsbalken."]}]}))
    cat = tmp_path / "catalog.sqlite"
    catalog.rebuild(cat, "sfs", [bb, fl])
    return catalog.connect(cat)


def test_doc_actions_document_and_fragment_units(tmp_path):
    con = _build_catalog(tmp_path)
    uri = "https://lagen.nu/1962:700"
    row = catalog.document(con, uri)
    row = (*row[:5], str(catalog.data_root(con) / row[5]))   # stored path is relative
    actions = list(search.doc_actions(
        row, catalog.document_inbound_count(con, uri), version="h1"))
    assert actions[0]["_source"]["version"] == "h1"     # carried for the diff
    assert actions[0]["_source"]["year"] == "1962"      # shared search facet

    doc, frag = actions[0], actions[1]
    # the whole-document unit owns full text for exact cursor paging; fragment
    # units duplicate bounded sections for the second pinpoint lookup
    assert doc["_id"] == "https://lagen.nu/1962:700"
    assert doc["_source"]["is_doc"] is True
    assert doc["_source"]["doc_uri"] == "https://lagen.nu/1962:700"
    assert doc["_source"]["uri"] == "https://lagen.nu/1962:700"
    assert doc["_source"]["title"] == "Brottsbalk (1962:700)"
    assert doc["_source"]["identifier"] == "SFS 1962:700"
    # no shortname/abbr on the artifact -> the shown heading is just the title
    assert doc["_source"]["display"] == "Brottsbalk (1962:700)"
    assert doc["_source"]["text"] == "Den som dödar annan döms för mord."
    assert doc["_source"]["inbound_count"] == 1          # 2018:585 cites K3P1
    assert "_routing" not in doc and "relation" not in doc["_source"]

    # the fragment unit: standalone, owns the body text; document identity is
    # carried as display-only (non-searchable) doc_title / doc_label
    assert frag["_id"] == "https://lagen.nu/1962:700#K3P1"
    assert frag["_source"]["is_doc"] is False
    assert frag["_source"]["doc_uri"] == "https://lagen.nu/1962:700"
    assert frag["_source"]["uri"] == "https://lagen.nu/1962:700#K3P1"
    assert frag["_source"]["pinpoint"] == "K3P1"
    assert frag["_source"]["text"] == "Den som dödar annan döms för mord."
    assert frag["_source"]["doc_title"] == "Brottsbalk (1962:700)"
    assert frag["_source"]["doc_label"] == "SFS 1962:700"
    assert frag["_source"]["doc_display"] == "Brottsbalk (1962:700)"
    assert frag["_source"]["inbound_count"] == 1          # denormalised for ranking
    assert "title" not in frag["_source"]                 # not searchable on a frag
    assert "_routing" not in frag


def test_doc_actions_display_uses_shortname_and_abbr(tmp_path):
    # an eurlex act carrying shortname/abbr (the CRA): the hit heading is the
    # short name + acronym, while the searchable `title` stays the full official
    # title -- so the readable label costs no findability
    art_dir = tmp_path / "artifact"
    art_dir.mkdir()
    cra = art_dir / "cra.json"
    cra.write_text(json.dumps({
        "uri": "https://lagen.nu/ext/celex/32024R2847", "celex": "32024R2847",
        "doctype": "regulation", "shortname": "Cyberresiliensförordningen",
        "abbr": "CRA",
        "title": "Europaparlamentets och rådets förordning (EU) 2024/2847 ... "
                 "(cyberresiliensförordningen) (Text av betydelse för EES)",
        "structure": [{"type": "article", "id": "1", "text": ["Syfte och mål."]}]}))
    cat = tmp_path / "catalog.sqlite"
    catalog.rebuild(cat, "eurlex", [cra])
    con = catalog.connect(cat)
    uri = "https://lagen.nu/ext/celex/32024R2847"
    row = catalog.document(con, uri)
    row = (*row[:5], str(catalog.data_root(con) / row[5]))   # stored path is relative
    doc, frag = list(search.doc_actions(row, 0))
    assert doc["_source"]["display"] == "Cyberresiliensförordningen (CRA)"
    assert doc["_source"]["title"].startswith("Europaparlamentets")   # full, searchable
    assert doc["_source"]["identifier"] == "32024R2847"               # CELEX, the sub
    assert frag["_source"]["doc_display"] == "Cyberresiliensförordningen (CRA)"


def test_doc_actions_no_fragments_carries_full_text(tmp_path):
    # a flat artifact (no id-bearing nodes) -> the single document unit holds the
    # whole body text, since there is no fragment to own it
    art = tmp_path / "flat.json"
    art.write_text(json.dumps({
        "uri": "https://lagen.nu/dom/x", "metadata": {"properties": {}},
        "body": [{"type": "stycke", "text": ["Domskälen anför följande."]}]}))
    [unit] = list(search.doc_actions(
        ("https://lagen.nu/dom/x", "dv", "case", "X", "X", str(art)), 0))
    assert unit["_source"]["is_doc"] is True
    assert unit["_source"]["text"] == "Domskälen anför följande."


def test_doc_actions_alternate_citation_is_searchable(tmp_path):
    # a published alternate citation with no body span (a JO decision's
    # ämbetsberättelse) rides the whole-doc unit's text, so querying
    # "JO 1990/91 s. 70" finds the decision
    art = tmp_path / "jo.json"
    art.write_text(json.dumps({
        "uri": "https://lagen.nu/avg/jo/1672-1987",
        "metadata": {"officialReport": "JO 1990/91 s. 70"},
        "structure": [{"type": "stycke", "text": ["Beslutets text."]}]}))
    [unit] = list(search.doc_actions(
        ("https://lagen.nu/avg/jo/1672-1987", "avg", "jo",
         "JO dnr 1672-1987", "Förföljande med polisfordon", str(art)), 0))
    assert unit["_source"]["text"] == "JO 1990/91 s. 70\nBeslutets text."


def test_doc_actions_skips_empty_artifact(tmp_path):
    # a row whose artifact file is empty yields nothing
    empty = tmp_path / "empty.json"
    empty.write_bytes(b"")
    assert list(search.doc_actions(
        ("u", "sfs", "law", "L", "L", str(empty)), 0)) == []


def test_doc_actions_pathless_stub_indexes_identity_only():
    # a synthesized stub (begrepp concept, no artifact on disk -> empty path)
    # must not read a file; it indexes one whole-doc unit carrying its name
    [unit] = list(search.doc_actions(
        ("https://lagen.nu/begrepp/Uppsat", "begrepp", "begrepp",
         "Uppsåt", "Uppsåt", ""), 3, version="v"))
    assert unit["_id"] == "https://lagen.nu/begrepp/Uppsat"
    assert unit["_source"]["is_doc"] is True
    assert unit["_source"]["title"] == "Uppsåt"
    assert unit["_source"]["version"] == "v"
    assert "text" not in unit["_source"]            # no body, no fragments


def test_query_body_pages_exact_document_units_and_ranks_by_inbound():
    body = search.query_body("mord", source="sfs", year="1962",
                             limit=5, offset=10)
    assert body["from"] == 10 and body["size"] == 5
    assert "collapse" not in body                         # one unit per document
    assert body["track_total_hits"] is True                # exact result count
    assert body["sort"] == [{"_score": "desc"}, {"doc_uri": "asc"}]
    fs = body["query"]["function_score"]
    assert fs["field_value_factor"]["field"] == "inbound_count"
    assert fs["boost_mode"] == "sum"
    # filtering happens in post_filter only (facet counts stay unnarrowed)
    assert {"term": {"is_doc": True}} in fs["query"]["bool"]["filter"]
    assert {"term": {"source": "sfs"}} in body["post_filter"]["bool"]["filter"]
    assert {"term": {"year": "1962"}} in body["post_filter"]["bool"]["filter"]
    # A facet omits its own selected value, but retains the other filters.
    source_filters = body["aggs"]["source"]["filter"]["bool"]["filter"]
    assert {"term": {"source": "sfs"}} not in source_filters
    assert {"term": {"year": "1962"}} in source_filters
    # Exact-token and automatic-prefix branches search all standalone units.
    queries = fs["query"]["bool"]["must"]["bool"]["should"]
    assert queries[0]["simple_query_string"]["query"] == "mord"
    assert queries[1]["simple_query_string"]["query"] == "mord*"


def test_query_body_no_filters_when_unscoped():
    body = search.query_body("mord")
    assert body["query"]["function_score"]["query"]["bool"]["filter"] \
        == [{"term": {"is_doc": True}}]
    assert body["post_filter"]["bool"]["filter"] == []


def test_cursor_roundtrip_and_search_after_query():
    cursor = search.encode_cursor([7.5, "https://lagen.nu/1962:700"], 20)
    sort, seen = search.decode_cursor(cursor)
    assert sort == [7.5, "https://lagen.nu/1962:700"] and seen == 20
    body = search.query_body("mord", search_after=sort)
    assert body["search_after"] == sort and "from" not in body
    with pytest.raises(ValueError, match="invalid search cursor"):
        search.decode_cursor("not-json")


def test_fragment_query_is_bounded_to_page_documents():
    body = search.fragment_query_body("mord", ["u1", "u2"])
    assert body["size"] == 2 and body["collapse"] == {"field": "doc_uri"}
    assert {"term": {"is_doc": False}} in body["query"]["bool"]["filter"]
    assert {"terms": {"doc_uri": ["u1", "u2"]}} in body["query"]["bool"]["filter"]


def test_prefix_query_handles_incomplete_legal_compounds_and_syntax():
    assert search.prefix_query("avtalsl") == "avtalsl*"
    assert search.prefix_query('\"upphovsr rätt\" (36 §)') == "upphovsr* rätt* 36*"


def test_parse_hit_fragment_representative():
    # a fragment unit won the group -> its pinpoint + highlight surface, and the
    # document identity comes from the display-only doc_title / doc_label
    hit = search.parse_hit({
        "_source": {"doc_uri": "https://lagen.nu/1962:700",
                    "uri": "https://lagen.nu/1962:700#K3P1", "is_doc": False,
                    "pinpoint": "K3P1", "doc_label": "SFS 1962:700",
                    "doc_title": "Brottsbalk", "doc_display": "Brottsbalk",
                    "source": "sfs", "kind": "law", "inbound_count": 42},
        "_score": 7.5,
        "highlight": {"text": ["döms för <em>mord</em>"]},
    })
    assert hit["uri"] == "https://lagen.nu/1962:700"       # the document, not the frag
    assert hit["identifier"] == "SFS 1962:700" and hit["title"] == "Brottsbalk"
    assert hit["display"] == "Brottsbalk"                  # the heading, from doc_display
    assert hit["inbound_count"] == 42 and hit["score"] == 7.5
    assert hit["fragments"] == [{"uri": "https://lagen.nu/1962:700#K3P1",
                                 "pinpoint": "K3P1",
                                 "highlight": ["döms för <em>mord</em>"]}]


def test_parse_hit_document_representative_has_no_fragment():
    # the whole-document unit won (e.g. a title match) -> no pinpoint fragment
    hit = search.parse_hit({
        "_source": {"doc_uri": "https://lagen.nu/1962:700",
                    "uri": "https://lagen.nu/1962:700", "is_doc": True,
                    "title": "Brottsbalk", "source": "sfs", "inbound_count": 42},
        "_score": 3.0,
        "highlight": {"title": ["<em>Brottsbalk</em>"]},
    })
    assert hit["uri"] == "https://lagen.nu/1962:700"
    assert hit["fragments"] == []
    assert hit["highlight"] == ["<em>Brottsbalk</em>"]    # falls back to title


def test_search_parses_filtered_total_and_facet_buckets():
    class Client:
        def search(self, index, body):
            assert index == "test"
            assert {"term": {"year": "1962"}} in body["post_filter"]["bool"]["filter"]
            return {
                "hits": {"total": {"value": 12, "relation": "eq"}, "hits": []},
                "aggregations": {
                    "source": {"values": {"buckets": [
                        {"key": "sfs", "doc_count": 9}]}},
                    "kind": {"values": {"buckets": [
                        {"key": "law", "doc_count": 9}]}},
                    "year": {"values": {"buckets": [
                        {"key": "1962", "doc_count": 12}]}},
                },
            }

    index = object.__new__(search.SearchIndex)
    index.index = "test"
    index.client = Client()
    result = index.search("mord", year="1962")
    assert result["total"] == 12
    assert result["facets"]["source"] == [{"value": "sfs", "count": 9}]
    assert result["facets"]["year"] == [{"value": "1962", "count": 12}]
    assert result["next_cursor"] is None


def test_search_returns_cursor_and_merges_best_fragment():
    class Client:
        def __init__(self):
            self.calls = 0

        def search(self, index, body):
            self.calls += 1
            if self.calls == 1:
                return {
                    "hits": {"total": {"value": 2, "relation": "eq"}, "hits": [{
                        "_source": {"doc_uri": "u1", "uri": "u1", "is_doc": True,
                                    "title": "One", "source": "sfs"},
                        "_score": 5.0, "sort": [5.0, "u1"],
                    }]},
                    "aggregations": {field: {"values": {"buckets": []}}
                                     for field in ("source", "kind", "year")},
                }
            assert body["collapse"] == {"field": "doc_uri"}
            return {"hits": {"hits": [{
                "_source": {"doc_uri": "u1", "uri": "u1#P1", "is_doc": False,
                            "pinpoint": "P1", "doc_title": "One", "source": "sfs"},
                "highlight": {"text": ["<em>mord</em>"]},
            }]}}

    index = object.__new__(search.SearchIndex)
    index.index = "test"
    index.client = Client()
    result = index.search("mord", limit=1)
    assert result["results"][0]["fragments"][0]["pinpoint"] == "P1"
    sort, seen = search.decode_cursor(result["next_cursor"])
    assert sort == [5.0, "u1"] and seen == 1


@pytest.mark.skipif(not os.environ.get("OPENSEARCH_URL"),
                    reason="needs a running OpenSearch (set OPENSEARCH_URL)")
def test_index_and_search_round_trip(tmp_path):
    """End-to-end against a live cluster: index two acts, then a free-text query
    returns one result per document (collapsed by doc_uri), represented by the
    matching paragraph."""
    con = _build_catalog(tmp_path)
    index = search.SearchIndex(index="lagen-test")
    try:
        index.index_source(con, "sfs")
        res = index.search("mord")
        assert res["total"] == 1                             # one distinct document
        top = res["results"][0]
        assert top["uri"] == "https://lagen.nu/1962:700"
        assert top["inbound_count"] == 1                     # cited by 2018:585
        assert top["fragments"][0]["pinpoint"] == "K3P1"     # the matching paragraph
        # the real analyzer + wildcard path, not merely the pure query shape
        assert index.search("mor")["total"] == 1
        # a scoped query still works
        assert index.search("brottsbalken", source="sfs")["total"] >= 1
    finally:
        if index.client.indices.exists(index="lagen-test"):
            index.client.indices.delete(index="lagen-test")


@pytest.mark.skipif(not os.environ.get("OPENSEARCH_URL"),
                    reason="needs a running OpenSearch (set OPENSEARCH_URL)")
def test_index_source_is_incremental(tmp_path):
    """Against a live cluster: a re-index with nothing changed touches nothing;
    editing one document re-indexes only it; removing it from the catalog drops
    its units. Exercises the content-hash diff + deletion sync, with jobs>1."""
    art = tmp_path / "artifact"
    art.mkdir()
    a = art / "a.json"
    a.write_text(json.dumps({
        "uri": "https://lagen.nu/1999:1", "metadata": {"properties":
        {"dcterms:title": "Alfa (1999:1)"}}, "structure": [
            {"type": "paragraf", "id": "P1", "text": ["Alfaregeln gäller."]}]}))
    b = art / "b.json"
    b.write_text(json.dumps({
        "uri": "https://lagen.nu/1999:2", "metadata": {"properties":
        {"dcterms:title": "Beta (1999:2)"}}, "structure": [
            {"type": "paragraf", "id": "P1", "text": ["Betaregeln gäller."]}]}))
    cat = tmp_path / "catalog.sqlite"
    catalog.rebuild(cat, "sfs", [a, b])
    con = catalog.connect(cat)
    index = search.SearchIndex(index="lagen-test")
    try:
        _, indexed, _, _, skipped, deleted = index.index_source(con, "sfs", jobs=2)
        assert (indexed, skipped, deleted) == (4, 0, 0)   # 2 docs * (doc + frag)

        # nothing changed -> nothing re-indexed, both skipped
        _, indexed, _, _, skipped, deleted = index.index_source(con, "sfs", jobs=2)
        assert (indexed, skipped, deleted) == (0, 2, 0)

        # edit one document -> only it is re-indexed
        a.write_text(json.dumps({
            "uri": "https://lagen.nu/1999:1", "metadata": {"properties":
            {"dcterms:title": "Alfa (1999:1)"}}, "structure": [
                {"type": "paragraf", "id": "P1", "text": ["Alfaregeln ändrad."]}]}))
        catalog.rebuild(cat, "sfs", [a, b])
        con = catalog.connect(cat)
        _, indexed, _, _, skipped, deleted = index.index_source(con, "sfs", jobs=2)
        assert (indexed, skipped, deleted) == (2, 1, 1)   # re-index a, skip b
        assert index.search("ändrad")["total"] == 1

        # drop one document from the catalog -> its units are deleted
        catalog.rebuild(cat, "sfs", [b])
        con = catalog.connect(cat)
        _, _, _, _, skipped, deleted = index.index_source(con, "sfs", jobs=2)
        assert (skipped, deleted) == (1, 1)
        assert index.search("alfaregeln")["total"] == 0
        assert index.search("betaregeln")["total"] == 1
    finally:
        if index.client.indices.exists(index="lagen-test"):
            index.client.indices.delete(index="lagen-test")


@pytest.mark.skipif(not os.environ.get("OPENSEARCH_URL"),
                    reason="needs a running OpenSearch (set OPENSEARCH_URL)")
def test_index_source_force_reindexes_all(tmp_path):
    """`force=True` reindexes every document regardless of content hash -- the
    full rebuild used when the index code changed (no hand-deleting the index)."""
    con = _build_catalog(tmp_path)
    index = search.SearchIndex(index="lagen-test")
    try:
        index.index_source(con, "sfs")
        _, indexed, _, _, skipped, _ = index.index_source(con, "sfs")
        assert (indexed, skipped) == (0, 2)              # nothing changed
        _, indexed, _, _, skipped, deleted = index.index_source(
            con, "sfs", force=True)
        assert skipped == 0 and indexed > 0 and deleted == 2   # both re-indexed
        assert index.search("mord")["total"] == 1        # still correct after
    finally:
        if index.client.indices.exists(index="lagen-test"):
            index.client.indices.delete(index="lagen-test")
