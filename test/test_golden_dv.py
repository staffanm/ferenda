"""DV golden metadata reducer tests; no frozen corpus required."""

import importlib.util
from pathlib import Path

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import DCTERMS, RDF

_spec = importlib.util.spec_from_file_location(
    "golden_dv", Path(__file__).parent.parent / "tools" / "golden_dv.py")
golden_dv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(golden_dv)

RPUBL = Namespace(golden_dv.RPUBL)


def test_old_case_reads_referat_and_linked_verdict_metadata(tmp_path):
    doc = URIRef("https://lagen.nu/dom/nja/2020s1")
    verdict_a = URIRef("https://lagen.nu/dom/hd/T1-20/2020-01-02")
    verdict_b = URIRef("https://lagen.nu/dom/hd/T2-20/2020-01-02")
    graph = Graph()
    graph.add((doc, RDF.type, RPUBL.Rattsfallsreferat))
    graph.add((doc, DCTERMS.identifier, Literal("NJA 2020 s. 1")))
    graph.add((doc, RPUBL.referatrubrik, Literal("  En   sammanfattning ")))
    graph.add((doc, RPUBL.referatAvDomstolsavgorande, verdict_a))
    graph.add((doc, RPUBL.referatAvDomstolsavgorande, verdict_b))
    graph.add((verdict_a, RPUBL.avgorandedatum, Literal("2020-01-02")))
    graph.add((verdict_a, RPUBL.malnummer, Literal("T 1-20")))
    graph.add((verdict_b, RPUBL.malnummer, Literal("T 2-20")))
    path = tmp_path / "case.rdf"
    graph.serialize(path, format="xml")

    uri, _, metadata = golden_dv.old_case(path)
    assert uri == str(doc)
    assert metadata == {
        "identifier": ["NJA 2020 s. 1"],
        "referatrubrik": ["En sammanfattning"],
        "avgorandedatum": ["2020-01-02"],
        "malnummer": ["T 1-20", "T 2-20"],
    }


def test_new_metadata_and_status_are_exact_not_fuzzy():
    artifact = {
        "referat": ["NJA 2020 s. 1"],
        "avgorandedatum": "2020-01-02",
        "malnummer": ["T 1-20"],
        "metadata": {"sammanfattning": "En   sammanfattning"},
    }
    metadata = golden_dv.new_metadata(artifact)
    assert metadata["referatrubrik"] == ["En sammanfattning"]
    assert golden_dv.metadata_status(set(metadata["referatrubrik"]),
                                     {"En sammanfattning"}) == "exact"
    assert golden_dv.metadata_status({"A"}, {"B"}) == "disjoint"
    assert golden_dv.metadata_status(set(), {"B"}) == "old-missing"
    assert golden_dv.metadata_status({"A"}, set()) == "new-missing"
    assert golden_dv.metadata_status({"A"}, {"A", "B"}) == "new-superset"


def test_metadata_canonicalizes_referat_and_malnummer_surfaces():
    assert (golden_dv.canonical_metadata("identifier", ["NJA 2020 s. 1"])
            == {"https://lagen.nu/dom/nja/2020s1"})
    assert (golden_dv.canonical_metadata("identifier", ["HFD 2012:41"])
            == golden_dv.canonical_metadata(
                "identifier", ["HFD 2012 ref. 41"]))
    assert (golden_dv.canonical_metadata("identifier", ["NJA 2016:31"])
            != golden_dv.canonical_metadata("identifier", ["NJA 2016 s. 341"]))
    assert (golden_dv.canonical_metadata("malnummer", ["A 125-92"])
            == golden_dv.canonical_metadata("malnummer", ["A125-92"])
            == golden_dv.canonical_metadata("malnummer", ["A-125-1992"]))
    assert (golden_dv.canonical_metadata("malnummer", ["A-33-38-2011"])
            == golden_dv.canonical_metadata(
                "malnummer", ["A 33-11", "A 34-11", "A 35-11", "A 36-11",
                               "A 37-11", "A 38-11"]))


def test_referatrubrik_status_only_calls_boundary_prefix_truncation():
    prefix = "x" * 2000
    assert (golden_dv.referatrubrik_status({prefix + "resten"}, {prefix})
            == "new-truncated")
    assert (golden_dv.referatrubrik_status({prefix}, {prefix + "resten"})
            == "old-truncated")
    assert (golden_dv.referatrubrik_status({"kort sammanfattning"}, {"kort"})
            == "disjoint")


def test_artifact_text_date_requires_formal_publishing_court_ruling():
    artifact = {
        "court": "HDO",
        "court_namn": "Högsta domstolen",
        "referat": ["NJA 2018 s. 405"],
        "avgorandedatum": "2018-06-12",
        "structure": [{
            "type": "stycke",
            "ordinal": None,
            "text": ["HD meddelade den 12 juni 2018 följande dom."],
        }],
    }
    assert golden_dv.artifact_text_date(artifact) == "2018-06-12"
    artifact["structure"][0]["text"] = [
        "Tingsrätten meddelade den 12 juni 2018 beslut."]
    assert golden_dv.artifact_text_date(artifact) is None
