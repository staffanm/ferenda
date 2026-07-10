"""Legacy-compatible Atom feeds over the accommodanda catalog."""

import json
import os
import xml.etree.ElementTree as ET

from accommodanda.lib import catalog, feeds

ATOM = "{http://www.w3.org/2005/Atom}"


def _catalog(tmp_path):
    db = tmp_path / "catalog.sqlite"

    law = tmp_path / "law.json"
    law.write_text(json.dumps({
        "uri": "https://lagen.nu/2024:1",
        "metadata": {"properties": {
            "dcterms:title": "Lag (2024:1) om prov",
            "rpubl:utfardandedatum": "2024-01-02"}},
        "structure": [],
    }))
    regulation = tmp_path / "regulation.json"
    regulation.write_text(json.dumps({
        "uri": "https://lagen.nu/2025:2",
        "metadata": {"properties": {
            "dcterms:title": "Förordning (2025:2) om prov",
            "rpubl:utfardandedatum": "2025-02-03"}},
        "structure": [],
    }))
    # An older publication updated after the newer publication must lead a
    # new-and-updated feed.
    os.utime(regulation, ns=(1_700_000_001_000_000_000,) * 2)
    os.utime(law, ns=(1_700_000_002_000_000_000,) * 2)
    catalog.rebuild(db, "sfs", [law, regulation])

    rule = tmp_path / "rule.json"
    rule.write_text(json.dumps({
        "type": "foreskrift", "uri": "https://lagen.nu/nfs/2025:1",
        "identifier": "NFS 2025:1", "fs": "nfs",
        "metadata": {"title": "Provföreskrift", "publisher": "Naturvårdsverket",
                     "utkomFranTryck": "2025-03-04"},
        "structure": [],
    }))
    catalog.rebuild(db, "foreskrift", [rule])
    return catalog.connect(db)


def test_sfs_feed_is_newest_first_and_uses_stable_document_ids(tmp_path):
    con = _catalog(tmp_path)
    rows = feeds.entries(con, feeds.dataset("sfs"))
    assert [row.uri for row in rows] == [
        "https://lagen.nu/2024:1", "https://lagen.nu/2025:2"]
    atom = feeds.render_atom(feeds.dataset("sfs"), rows)
    root = ET.fromstring(atom)
    assert root.find(ATOM + "id").text == "https://lagen.nu/dataset/sfs/feed.atom"
    assert [node.text for node in root.findall(ATOM + "entry/" + ATOM + "id")] \
        == ["https://lagen.nu/2024:1", "https://lagen.nu/2025:2"]
    assert root.find(ATOM + "entry/" + ATOM + "published").text \
        == "2024-01-02T00:00:00Z"


def test_legacy_query_parameters_filter_feeds(tmp_path):
    con = _catalog(tmp_path)
    sfs = feeds.dataset("sfs")
    assert [row.uri for row in feeds.entries(con, sfs, rdf_type="type/lag")] \
        == ["https://lagen.nu/2024:1"]
    assert [row.uri for row in feeds.entries(
        con, sfs, rdf_type="type/forordning")] == ["https://lagen.nu/2025:2"]

    myndfs = feeds.dataset("myndfs")
    rows = feeds.entries(
        con, myndfs, dcterms_publisher="publisher/naturvardsverket")
    assert [row.uri for row in rows] == ["https://lagen.nu/nfs/2025:1"]
    atom = feeds.render_atom(
        myndfs, rows,
        {"dcterms_publisher": "publisher/naturvardsverket"})
    assert ("/dataset/myndfs/feed.atom?dcterms_publisher="
            "publisher%2Fnaturvardsverket") in atom


def test_nonmatching_publisher_filter_does_not_open_artifacts(tmp_path, monkeypatch):
    con = _catalog(tmp_path)
    monkeypatch.setattr(
        feeds.catalog, "load_artifact",
        lambda *_args: (_ for _ in ()).throw(AssertionError("artifact scan")))
    assert feeds.entries(
        con, feeds.dataset("myndfs"),
        dcterms_publisher="publisher/finns_inte") == []


def test_legacy_aliases_map_to_rebuilt_sources():
    assert feeds.dataset("forarbeten").source == "forarbete"
    assert feeds.dataset("myndfs").source == "foreskrift"
    assert feeds.dataset("myndprax").source == "avg"
    assert feeds.dataset("keyword").source == "begrepp"


def test_document_date_covers_every_source_field_with_stable_precedence():
    """catalog.document_date is the one home for the date-field policy (feeds
    ordering, documents.date at relate, chronology panels). Each source's field
    must resolve, in the documented precedence order."""
    chain = [
        ({"date": "2024-01-01"}, "2024-01-01"),                       # forarbete
        ({"avgorandedatum": "2024-02-02"}, "2024-02-02"),             # dv
        ({"metadata": {"beslutsdatum": "2024-03-03"}}, "2024-03-03"),  # avg
        ({"metadata": {"utkomFranTryck": "2024-04-04"}}, "2024-04-04"),  # foreskrift
        ({"metadata": {"properties":
                       {"rpubl:utfardandedatum": "2024-05-05"}}}, "2024-05-05"),  # sfs
        ({"metadata": {"properties":
                       {"rpubl:avgorandedatum": "2024-06-06"}}}, "2024-06-06"),
        ({"metadata": {"properties":
                       {"rpubl:beslutsdatum": "2024-07-07"}}}, "2024-07-07"),
    ]
    # each field alone resolves
    for art, expected in chain:
        assert catalog.document_date(art) == expected
    # all fields at once: the chain's head wins
    everything = {
        "date": "2024-01-01", "avgorandedatum": "2024-02-02",
        "metadata": {"beslutsdatum": "2024-03-03",
                     "utkomFranTryck": "2024-04-04",
                     "properties": {"rpubl:utfardandedatum": "2024-05-05",
                                    "rpubl:avgorandedatum": "2024-06-06",
                                    "rpubl:beslutsdatum": "2024-07-07"}}}
    assert catalog.document_date(everything) == "2024-01-01"
    assert catalog.document_date({"metadata": {"properties": {}}}) is None
