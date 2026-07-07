"""Tests for the SFS old->new paragraf correspondence pass (the non-LLM core:
inventory, old-law detection, FK slicing, and edge validation)."""

import json

from accommodanda.forarbete import kommentar
from accommodanda.sfs import correspond as C

CELEX = "https://lagen.nu/"


def _sfs(uri, structure, amendments=None, title="Lag (2000:1)"):
    return {"uri": uri, "structure": structure,
            "amendments": amendments or [],
            "metadata": {"properties": {"dcterms:title": title}}}


def test_paragraf_index_chaptered_and_flat():
    chaptered = _sfs(CELEX + "2018:585", [
        {"type": "kapitel", "ordinal": "3", "children": [
            {"type": "paragraf", "id": "K3P17", "ordinal": "17"},
            {"type": "paragraf", "id": "K3P18", "ordinal": "18"}]}])
    assert C.paragraf_index(chaptered) == [("K3P17", "3 kap. 17 §"),
                                           ("K3P18", "3 kap. 18 §")]
    flat = _sfs(CELEX + "1996:627", [
        {"type": "paragraf", "id": "P32a", "ordinal": "32 a"}])
    assert C.paragraf_index(flat) == [("P32a", "32 a §")]


def test_paragraf_index_excludes_id_suppressed():
    # an id-suppressed paragraf (temporal/dedup, "id": None) has no anchor;
    # it must not enter the LLM inventory as a mappable target
    art = _sfs(CELEX + "2018:585", [
        {"type": "paragraf", "id": "P1", "ordinal": "1"},
        {"type": "paragraf", "id": None, "ordinal": "2"}])
    assert C.paragraf_index(art) == [("P1", "1 §")]


def test_detect_old_law_from_repeal_clause():
    new = _sfs(CELEX + "2018:585", [], amendments=[{"content": [{"children": [
        {"type": "punkt", "text": [
            "Genom lagen upphävs ",
            {"predicate": "dcterms:references", "uri": CELEX + "1996:627",
             "text": "säkerhetsskyddslagen (1996:627)"}, "."]},
        # a same-clause reference that is NOT the repealed law and a pinpoint that
        # must be ignored (only whole-SFS repeal references count)
        {"type": "punkt", "text": [
            "Hänvisning till ",
            {"predicate": "dcterms:references", "uri": CELEX + "2018:585#P14",
             "text": "14 §"}]}]}]}])
    assert C.detect_old_law(new) == CELEX + "1996:627"


def test_detect_old_law_none_when_no_repeal():
    assert C.detect_old_law(_sfs(CELEX + "2020:9", [])) is None


def test_validate_edges_keeps_valid_drops_hallucinations():
    new_anchors, old_anchors = {"K1P1", "K3P17"}, {"P1", "P32a"}
    fk = ("I paragrafen, som delvis motsvarar 1 § 1996 års säkerhetsskyddslag, "
          "anges lagens syfte. Paragrafen har förts över från 32 a §.")
    raw = [
        # valid correspondence
        {"newParagraf": "K1P1", "oldParagraf": "P1", "relation": "motsvarar",
         "scope": "delvis", "quote": "I paragrafen, som delvis motsvarar 1 § 1996 års säkerhetsskyddslag"},
        # valid transfer, scope null
        {"newParagraf": "K3P17", "oldParagraf": "P32a", "relation": "overfort",
         "scope": None, "quote": "Paragrafen har förts över från 32 a §"},
        # hallucinated new anchor -> dropped
        {"newParagraf": "K9P9", "oldParagraf": "P1", "relation": "motsvarar",
         "scope": "helt", "quote": "I paragrafen, som delvis motsvarar 1 §"},
        # invented quote (not in FK) -> dropped
        {"newParagraf": "K1P1", "oldParagraf": "P1", "relation": "motsvarar",
         "scope": "helt", "quote": "Denna mening finns inte i kommentaren alls."},
        # bad relation vocabulary -> dropped
        {"newParagraf": "K1P1", "oldParagraf": "P1", "relation": "liknar",
         "scope": "helt", "quote": "I paragrafen, som delvis motsvarar 1 §"},
    ]
    edges, rejected = C.validate_edges(raw, new_anchors, old_anchors,
                                       CELEX + "1996:627", fk)
    assert [e["newParagraf"] for e in edges] == ["K1P1", "K3P17"]
    assert edges[0]["oldUri"] == CELEX + "1996:627#P1"
    assert edges[1]["relation"] == "overfort" and edges[1]["scope"] is None
    assert len(rejected) == 3


def test_correspond_end_to_end_monkeypatched(monkeypatch):
    # exercise the whole pipeline with a stubbed LLM (no network): the model
    # returns one good edge and one hallucinated anchor; only the good one is kept
    new = _sfs(CELEX + "2018:585", [
        {"type": "kapitel", "ordinal": "1", "children": [
            {"type": "paragraf", "id": "K1P1", "ordinal": "1"}]}],
        title="Säkerhetsskyddslag (2018:585)")
    old = _sfs(CELEX + "1996:627", [{"type": "paragraf", "id": "P1", "ordinal": "1"}])
    prop = {"uri": CELEX + "prop/2017/18:89", "identifier": "Prop. 2017/18:89",
            "structure": [
                {"type": "rubrik", "level": 1, "text": ["16 Författningskommentar"]},
                {"type": "rubrik", "level": 2,
                 "text": ["16.1 Förslaget till säkerhetsskyddslag"]},
                {"type": "stycke",
                 "text": ["Paragrafen motsvarar 1 § 1996 års säkerhetsskyddslag."]}]}

    def fake_complete(prompt):
        assert "K1P1 = 1 kap. 1 §" in prompt and "P1 = 1 §" in prompt
        return json.dumps({"correspondences": [
            {"newParagraf": "K1P1", "oldParagraf": "P1", "relation": "motsvarar",
             "scope": "helt", "quote": "Paragrafen motsvarar 1 § 1996 års säkerhetsskyddslag"},
            {"newParagraf": "K7P7", "oldParagraf": "P1", "relation": "motsvarar",
             "scope": "helt", "quote": "Paragrafen motsvarar 1 §"}]})

    monkeypatch.setattr(C.llm, "complete", fake_complete)
    # build composes the two verticals: förarbete extracts the FK text, sfs derives
    fk = kommentar.fk_section(prop, "Säkerhetsskyddslag (2018:585)")
    sidecar, stats = C.correspond(new, prop, old, fk)
    assert stats == {"raw": 2, "emitted": 1, "rejected": 1}
    assert sidecar["correspondence"]["edges"] == [{
        "newParagraf": "K1P1", "oldParagraf": "P1",
        "oldUri": CELEX + "1996:627#P1", "relation": "motsvarar",
        "scope": "helt", "quote": "Paragrafen motsvarar 1 § 1996 års säkerhetsskyddslag"}]
    assert sidecar["correspondence"]["newLaw"] == CELEX + "2018:585"
    assert sidecar["correspondence"]["oldLaw"] == CELEX + "1996:627"
    # the catalog rows join the new paragraf anchor onto the new law's uri
    assert C.corr_rows(sidecar) == [
        (CELEX + "2018:585#K1P1", CELEX + "1996:627#P1", "motsvarar", "helt",
         CELEX + "prop/2017/18:89")]
