"""Tests for the DV structural golden (tools/golden_dv_structure.py).

Hermetic: a synthetic parsed referat exercising the full decision vocabulary
(delmål, instances, betänkande vs dom, domskäl/domslut, dissent), so it runs
without the corpus. Pins the structural normal form, the artifact-side reducer
contract (round-trip), and the diff."""

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "golden_dv_structure",
    Path(__file__).parent.parent / "tools" / "golden_dv_structure.py")
gds = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gds)

GOLDEN_SFS = gds.load_golden_sfs()

# a referat with one split case whose HD instans separates the föredragande's
# betänkande from the court's dom, plus a dissent -- and bodymeta/endmeta and a
# stray prose <p> that the normalizer must see through
PARSED = """<html xmlns="http://www.w3.org/1999/xhtml"><head>
<meta property="dcterms:identifier" content="NJA 2099 s. 1"/>
<link rel="rdf:type" href="http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ#Rattsfallsreferat"/>
</head><body about="https://lagen.nu/dom/nja/2099s1">
<div class="bodymeta"><p>headnote</p></div>
<div class="delmal" property="rinfoex:delmalordinal" content="I">
  <div class="instans" property="dcterms:creator" content="Stockholms tingsrätt">
    <p>TR prose</p><div class="dom"><div class="domskal"/></div>
  </div>
  <div class="instans" property="dcterms:creator" content="Högsta domstolen">
    <div class="betankande"><div class="domskal"/><div class="domslut"/></div>
    <div class="dom"><div class="domskal"/><div class="domslut"/></div>
    <div class="skiljaktig"/>
  </div>
</div>
<div class="endmeta"><p>sökord</p></div>
</body></html>"""


def _nf(tmp_path):
    p = tmp_path / "case.xhtml"
    p.write_text(PARSED)
    return gds.normalize(p)


def test_normalize_captures_full_decision_structure(tmp_path):
    nf = _nf(tmp_path)
    assert nf["uri"] == "https://lagen.nu/dom/nja/2099s1"
    assert nf["identifier"] == "NJA 2099 s. 1"
    assert nf["type"] == "Rattsfallsreferat"

    [delmal] = nf["structure"]
    assert delmal["type"] == "delmal" and delmal["id"] == "I"
    tr, hd = delmal["children"]
    # bodymeta/endmeta dropped; instances keyed by court; prose seen through
    assert (tr["type"], tr["id"]) == ("instans", "Stockholms tingsrätt")
    assert (hd["type"], hd["id"]) == ("instans", "Högsta domstolen")
    # the HD instans separates betänkande from dom, both with domskäl+domslut,
    # and carries a dissent
    kinds = [c["type"] for c in hd["children"]]
    assert kinds == ["betankande", "dom", "skiljaktig"]
    betankande, dom, _ = hd["children"]
    assert [c["type"] for c in betankande["children"]] == ["domskal", "domslut"]
    assert [c["type"] for c in dom["children"]] == ["domskal", "domslut"]


def test_identity_compare_has_no_diffs(tmp_path):
    nf = _nf(tmp_path)
    assert gds.compare(nf, nf, GOLDEN_SFS) == []


def test_artifact_reducer_round_trips_against_golden(tmp_path):
    # the artifact contract: a nested `structure` with court/ordinal -- a parser
    # emitting this exact shape matches the golden with zero diffs
    art = {"uri": "https://lagen.nu/dom/nja/2099s1",
           "identifier": "NJA 2099 s. 1", "doctype": "Rattsfallsreferat",
           "structure": [
               {"type": "delmal", "ordinal": "I", "children": [
                   {"type": "instans", "court": "Stockholms tingsrätt", "children": [
                       {"type": "rubrik", "text": "ignored leaf"},
                       {"type": "dom", "children": [{"type": "domskal"}]}]},
                   {"type": "instans", "court": "Högsta domstolen", "children": [
                       {"type": "betankande", "children": [
                           {"type": "domskal"}, {"type": "domslut"}]},
                       {"type": "dom", "children": [
                           {"type": "domskal"}, {"type": "domslut"}]},
                       {"type": "skiljaktig"}]}]}]}
    new = gds.skeleton_from_artifact(art)
    assert gds.compare(_nf(tmp_path), new, GOLDEN_SFS) == []


def test_missing_instance_and_dissent_are_flagged(tmp_path):
    old = _nf(tmp_path)
    # an artifact that found the betänkande but dropped the whole HD instans's
    # dom branch and the dissent
    art = {"uri": "https://lagen.nu/dom/nja/2099s1",
           "structure": [
               {"type": "delmal", "ordinal": "I", "children": [
                   {"type": "instans", "court": "Stockholms tingsrätt", "children": [
                       {"type": "dom", "children": [{"type": "domskal"}]}]},
                   {"type": "instans", "court": "Högsta domstolen", "children": [
                       {"type": "betankande", "children": [
                           {"type": "domskal"}, {"type": "domslut"}]}]}]}]}
    problems = gds.compare(old, gds.skeleton_from_artifact(art), GOLDEN_SFS)
    text = "\n".join(problems)
    assert "missing node dom" in text      # HD's own ruling dropped
    assert "missing node skiljaktig" in text


def test_wrong_uri_reported(tmp_path):
    old = _nf(tmp_path)
    new = gds.skeleton_from_artifact({"uri": "https://lagen.nu/dom/nja/2099s2",
                                      "structure": []})
    problems = gds.compare(old, new, GOLDEN_SFS)
    assert any(p.startswith("uri:") for p in problems)


def test_empty_golden_is_detectable_before_normalize(tmp_path):
    path = tmp_path / "removed.xhtml"
    path.write_bytes(b"")
    assert gds.empty_golden(path)


def test_core_skeleton_normalizes_old_direct_domslut_and_unknown_court():
    old = {"uri": "https://lagen.nu/dom/ad/2099:1", "structure": [
        {"type": "instans", "id": None, "children": [
            {"type": "domslut", "id": None, "children": []}
        ]}
    ]}
    new = {"uri": old["uri"], "structure": [
        {"type": "instans", "id": "Arbetsdomstolen", "children": [
            {"type": "dom", "id": None, "children": [
                {"type": "domskal", "id": None, "children": []},
                {"type": "domslut", "id": None, "children": []},
            ]}
        ]}
    ]}
    assert gds.compare(gds.core_skeleton(old), gds.core_skeleton(new),
                       GOLDEN_SFS) == []


def test_core_skeleton_keeps_betankande_dom_and_dissent():
    nf = {"uri": "https://lagen.nu/dom/nja/2099s1", "structure": [
        {"type": "instans", "id": "HD", "children": [
            {"type": "betankande", "id": None, "children": [
                {"type": "domskal", "id": None, "children": []}]},
            {"type": "dom", "id": None, "children": [
                {"type": "domslut", "id": None, "children": []}]},
            {"type": "skiljaktig", "id": None, "children": []},
        ]}
    ]}
    core = gds.core_skeleton(nf)
    assert [node["type"] for node in core["structure"][0]["children"]] == [
        "betankande", "dom", "skiljaktig"]
