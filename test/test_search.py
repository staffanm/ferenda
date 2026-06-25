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
    row = catalog.document(con, "https://lagen.nu/1962:700")
    actions = list(search.doc_actions(con, row))

    doc, frag = actions[0], actions[1]
    # the whole-document unit: searchable identity, but NO body text (the
    # fragment owns it, so a body query collapses to a paragraph, not the doc)
    assert doc["_id"] == "https://lagen.nu/1962:700"
    assert doc["_source"]["is_doc"] is True
    assert doc["_source"]["doc_uri"] == "https://lagen.nu/1962:700"
    assert doc["_source"]["uri"] == "https://lagen.nu/1962:700"
    assert doc["_source"]["title"] == "Brottsbalk (1962:700)"
    assert doc["_source"]["identifier"] == "SFS 1962:700"
    assert "text" not in doc["_source"]                  # fragments hold the text
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
    assert frag["_source"]["inbound_count"] == 1          # denormalised for ranking
    assert "title" not in frag["_source"]                 # not searchable on a frag
    assert "_routing" not in frag


def test_doc_actions_no_fragments_carries_full_text(tmp_path):
    # a flat artifact (no id-bearing nodes) -> the single document unit holds the
    # whole body text, since there is no fragment to own it
    con = _build_catalog(tmp_path)
    art = tmp_path / "flat.json"
    art.write_text(json.dumps({
        "uri": "https://lagen.nu/dom/x", "metadata": {"properties": {}},
        "body": [{"type": "stycke", "text": ["Domskälen anför följande."]}]}))
    [unit] = list(search.doc_actions(
        con, ("https://lagen.nu/dom/x", "dv", "case", "X", "X", str(art))))
    assert unit["_source"]["is_doc"] is True
    assert unit["_source"]["text"] == "Domskälen anför följande."


def test_doc_actions_skips_empty_artifact(tmp_path):
    con = _build_catalog(tmp_path)
    # a row whose artifact file is empty yields nothing
    empty = tmp_path / "empty.json"
    empty.write_bytes(b"")
    assert list(search.doc_actions(
        con, ("u", "sfs", "law", "L", "L", str(empty)))) == []


def test_query_body_collapses_by_document_and_ranks_by_inbound():
    body = search.query_body("mord", source="sfs", limit=5, offset=10)
    assert body["from"] == 10 and body["size"] == 5
    # one result per document
    assert body["collapse"] == {"field": "doc_uri"}
    # distinct-document total
    assert body["aggs"]["docs"]["cardinality"]["field"] == "doc_uri"
    fs = body["query"]["function_score"]
    assert fs["field_value_factor"]["field"] == "inbound_count"
    assert fs["boost_mode"] == "sum"
    assert {"term": {"source": "sfs"}} in fs["query"]["bool"]["filter"]
    # one query across all units (no has_child)
    assert "simple_query_string" in fs["query"]["bool"]["must"]


def test_query_body_no_filters_when_unscoped():
    body = search.query_body("mord")
    assert body["query"]["function_score"]["query"]["bool"]["filter"] == []


def test_parse_hit_fragment_representative():
    # a fragment unit won the group -> its pinpoint + highlight surface, and the
    # document identity comes from the display-only doc_title / doc_label
    hit = search.parse_hit({
        "_source": {"doc_uri": "https://lagen.nu/1962:700",
                    "uri": "https://lagen.nu/1962:700#K3P1", "is_doc": False,
                    "pinpoint": "K3P1", "doc_label": "SFS 1962:700",
                    "doc_title": "Brottsbalk", "source": "sfs", "kind": "law",
                    "inbound_count": 42},
        "_score": 7.5,
        "highlight": {"text": ["döms för <em>mord</em>"]},
    })
    assert hit["uri"] == "https://lagen.nu/1962:700"       # the document, not the frag
    assert hit["identifier"] == "SFS 1962:700" and hit["title"] == "Brottsbalk"
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
        # a scoped query still works
        assert index.search("brottsbalken", source="sfs")["total"] >= 1
    finally:
        if index.client.indices.exists(index="lagen-test"):
            index.client.indices.delete(index="lagen-test")
