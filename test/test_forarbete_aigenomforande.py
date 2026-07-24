"""The LLM directive->paragraf transposition pass (forarbete.aigenomforande)
and the relate-time layer preference (genomforande.prop_implements) -- the
non-LLM core: candidate selection, per-law batching, the tagged multi-directive
catalog, entry-id rendering, batch-reply validation (pinpoint- and bare-number
articles, drop-not-poison), edge fan-out, and the .ann-supersedes-mechanical
join. The LLM call itself is not exercised."""

import json

import pytest

from accommodanda.forarbete import aigenomforande as A
from accommodanda.forarbete import genomforande as G
from accommodanda.lib import annstore

NIS2 = "32022L2555"
CER = "32022L2557"
NIS2_URI = A.CELEX_BASE + NIS2
CER_URI = A.CELEX_BASE + CER

CATALOG = [
    {"tag": "A", "celex": NIS2, "uri": NIS2_URI, "label": "NIS 2-direktivet",
     "articles": {"2": "Tillämpningsområde", "21": "Riskhantering",
                  "23": "Rapportering"}, "valid": {"2", "21", "23"}},
    {"tag": "B", "celex": CER, "uri": CER_URI, "label": "CER-direktivet",
     "articles": {"11": "Riskåtgärder", "13": "Incidentrapportering"},
     "valid": {"11", "13"}},
]


def _entry(kommentar, chapter="1", paragrafer=("5",), law="15.1 Förslaget "
           "till cybersäkerhetslag", page=42):
    return {"law": law, "chapter": chapter, "paragrafer": list(paragrafer),
            "kommentar": kommentar, "page": page}


def _reply(mappings):
    return json.dumps({"mappings": mappings})


def test_detect_directives_strips_pinpoint_fragments_and_dedupes():
    art = {"implements": [
        {"directive": NIS2_URI + "#21.1"}, {"directive": NIS2_URI},
        {"directive": CER_URI + "#11"}, {"directive": None}]}
    assert A.detect_directives(art) == [NIS2, CER]


def test_candidate_entries_keeps_only_directive_mentioning_with_paragrafer():
    art = {"kommentarer": [
        _entry("Paragrafen genomför artikel 21 i direktivet.", paragrafer=["3"]),
        _entry("Paragrafen reglerar överklagande.", paragrafer=["4"]),   # neither token
        {"law": "L", "chapter": None, "paragrafer": [],                  # no paragraf
         "kommentar": "Lagen genomför direktivet.", "page": 1}]}
    assert [e["paragrafer"] for e in A.candidate_entries(art)] == [["3"]]


def test_batches_split_on_law_change_and_budget():
    a1 = _entry("x" * 30, paragrafer=["1"], law="L1")
    a2 = _entry("y" * 30, paragrafer=["2"], law="L1")
    b1 = _entry("z" * 30, paragrafer=["1"], law="L2")
    assert A.batches([a1, a2, b1], budget=100) == [[a1, a2], [b1]]
    assert A.batches([a1, a2, b1], budget=40) == [[a1], [a2], [b1]]
    big = _entry("q" * 200, paragrafer=["9"], law="L1")
    assert A.batches([big], budget=40) == [[big]]


def test_render_batch_ids_entries_and_maps_them_back():
    batch = [_entry("Kommentar A", paragrafer=["1"]),
             _entry("Kommentar B", chapter="2", paragrafer=["3", "4"])]
    by_id, fk = A.render_batch(batch)
    assert list(by_id) == ["E1", "E2"] and by_id["E2"] is batch[1]
    assert fk.startswith("[E1] 1 kap. 1 §\nKommentar A")
    assert "[E2] 2 kap. 3, 4 §\nKommentar B" in fk


def test_directives_block_tags_each_directive_with_numeric_articles():
    block = A.directives_block(CATALOG)
    assert block.startswith("[A] NIS 2-direktivet:\n  2 = Tillämpningsområde")
    assert "[B] CER-direktivet:\n  11 = Riskåtgärder\n  13 = Incidentrapportering" \
        in block


def test_validate_accepts_bare_numbers():
    by_id = {"E1": _entry("Paragrafen genomför artikel 21 i NIS 2-direktivet.")}
    items = A.validate(_reply([
        {"entry": "E1", "dir": "A", "articles": ["21"], "partial": True,
         "quote": "Paragrafen genomför artikel 21 i NIS 2-direktivet."}]),
        by_id, CATALOG)
    assert items == [{"entry": "E1", "tag": "A", "articles": ["21"],
                      "pinpoints": ["21"], "partial": True,
                      "quote": "Paragrafen genomför artikel 21 i NIS 2-direktivet."}]


def test_validate_accepts_dotted_pinpoints_and_reduces_to_base_article():
    # the regression that made batch runs look catastrophic: a model that emits
    # "21.1–21.3" / "2.2 f" instead of bare "21" / "2" must NOT be rejected --
    # the base article is validated, the pinpoints are kept for the margin
    by_id = {"E1": _entry("Paragrafen genomför artikel 21.1–21.3 samt artikel "
                          "2.2 f i NIS 2-direktivet.")}
    items = A.validate(_reply([
        {"entry": "E1", "dir": "A", "articles": ["21.1–21.3", "2.2 f"],
         "quote": "Paragrafen genomför artikel 21.1–21.3 samt artikel 2.2 f"}]),
        by_id, CATALOG)
    assert items[0]["articles"] == ["21", "2"]
    assert items[0]["pinpoints"] == ["21.1", "21.2", "21.3", "2.2 f"]


def test_validate_drops_bad_items_without_raising():
    by_id = {"E1": _entry("Paragrafen genomför artikel 21.")}
    items = A.validate(_reply([
        {"entry": "E9", "dir": "A", "articles": ["21"], "quote": "Paragrafen genomför artikel 21."},  # unknown id
        {"entry": "E1", "dir": "Z", "articles": ["21"], "quote": "Paragrafen genomför artikel 21."},  # unknown tag
        {"entry": "E1", "dir": "B", "articles": ["21"], "quote": "Paragrafen genomför artikel 21."},  # art from wrong dir
        {"entry": "E1", "dir": "A", "articles": [], "quote": "Paragrafen genomför artikel 21."},       # no article
        {"entry": "E1", "dir": "A", "articles": ["21"], "quote": "en helt annan mening"},              # quote absent
        {"entry": "E1", "dir": "A", "articles": ["21"], "quote": ""},                                  # empty quote
        {"entry": "E1", "dir": "A", "articles": ["21"]},                                               # no quote at all
    ]), by_id, CATALOG)
    assert items == []


def test_validate_raises_on_structurally_unusable_reply():
    with pytest.raises(ValueError, match="mappings"):
        A.validate('{"nope": 1}', {}, CATALOG)
    with pytest.raises(ValueError):        # JSONDecodeError is a ValueError
        A.validate("not json at all", {}, CATALOG)


def test_edges_for_fans_out_paragrafer_mirrors_implements_shape():
    entry = _entry("Paragraferna genomför artikel 23.1 i NIS 2-direktivet.",
                   chapter="4", paragrafer=["2", "3"])
    by_id = {"E1": entry}
    item = {"entry": "E1", "tag": "A", "articles": ["23"], "pinpoints": ["23.1"],
            "partial": True,
            "quote": "Paragraferna genomför artikel 23.1 i NIS 2-direktivet."}
    edges = A.edges_for(item, by_id, CATALOG)
    assert [e["paragraf"] for e in edges] == ["2", "3"]
    e = edges[0]
    assert e["predicate"] == "rpubl:genomforDirektiv" and e["directive"] == NIS2_URI
    assert e["articles"] == ["23"] and e["pinpoints"] == ["23.1"]
    assert e["uris"] == [NIS2_URI + "#23"] and e["partial"] is True
    assert e["chapter"] == "4" and e["page"] == 42


# --- the relate-time layer preference -------------------------------------

def _mech(directive, article, para):
    return {"predicate": "rpubl:genomforDirektiv", "directive": directive,
            "articles": [article], "pinpoints": [], "uris": [directive + "#" + article],
            "partial": False, "law": "L", "chapter": "1", "paragraf": para}


def test_prop_implements_without_layer_returns_mechanical():
    art = {"implements": [_mech(NIS2_URI, "2", "1")]}
    assert G.prop_implements(art, None) == art["implements"]
    assert G.prop_implements(art, []) == art["implements"]


def test_prop_implements_supersedes_fragment_bearing_mechanical_edge():
    # the mechanical extractor's alias resolution yields a fragment-bearing
    # `directive` for a minority of edges (51/372 on the 2025/26 props); the
    # supersede join must reduce both sides to the base uri or the covered
    # directive's mechanical edge survives beside the authored one
    layer_edge = {"directive": NIS2_URI, "articles": ["21"], "paragraf": "9"}
    art = {"implements": [_mech(NIS2_URI + "#21.1", "21", "1")]}
    assert G.prop_implements(art, [layer_edge]) == [layer_edge]


def test_prop_implements_layer_supersedes_covered_directive_keeps_others():
    layer_edge = {"directive": NIS2_URI, "articles": ["21"], "paragraf": "9"}
    other = A.CELEX_BASE + "32022L2999"          # a directive the layer didn't map
    art = {"implements": [_mech(NIS2_URI, "2", "1"),     # superseded (same directive)
                          _mech(other, "5", "3")]}        # kept (other directive)
    got = G.prop_implements(art, [layer_edge])
    assert layer_edge in got
    assert _mech(other, "5", "3") in got
    assert not any(r.get("directive") == NIS2_URI and r.get("articles") == ["2"]
                   for r in got)


# --- the adjudicated golden corpus ----------------------------------------
# Ground truth for riksmöte 2025/26: every eligible prop's FK was read and
# adjudicated (paragraf→direktivartikel, validated through the same parser and
# quote checks as the live pass) into `.ann.golden` files beside the `.ann`
# layers in the annstore. Any generated layer present is scored against its
# golden here, so a prompt/validator regression that degrades real mappings
# fails the suite. Thresholds from the 2026-07-23 four-model benchmark: the
# stored layers measured 0.95–1.00 precision, 1.00 recall.


def _edge_keys(edges):
    return {(e.get("law"), e.get("chapter"), e.get("paragraf"),
             e["directive"], a) for e in edges for a in e["articles"]}


def test_stored_layers_hold_against_adjudicated_golden():
    pairs = [(p, p.with_suffix(".ann.golden"))
             for p in sorted(annstore.tree("forarbete").rglob("*.ann"))
             if p.with_suffix(".ann.golden").exists()]
    if not pairs:
        pytest.skip("no .ann + .ann.golden pairs in the annstore")
    for ann, golden in pairs:
        layer = json.loads(ann.read_text()).get("genomforande")
        if not layer:                       # a future non-genomforande .ann
            continue
        got = _edge_keys(layer["edges"])
        want = _edge_keys(json.loads(golden.read_text())["genomforande"]["edges"])
        tp = len(got & want)
        prec = tp / len(got) if got else 1.0
        rec = tp / len(want) if want else 1.0
        assert prec >= 0.90 and rec >= 0.90, \
            "%s vs golden: precision %.2f recall %.2f (fp %s | fn %s)" % (
                ann.name, prec, rec, sorted(got - want), sorted(want - got))


def test_genomforande_layers_keys_edges_by_proposition_uri(tmp_path, monkeypatch):
    monkeypatch.setattr(annstore, "ROOT", tmp_path / "ann")
    tree = annstore.tree("forarbete") / "prop" / "2025"
    tree.mkdir(parents=True)
    (tree / "2025-26-28.ann").write_text(json.dumps({
        "meta": {"status": "generated"},
        "genomforande": {"directives": [NIS2_URI],
                         "proposition": "https://lagen.nu/prop/x",
                         "edges": [{"directive": NIS2_URI, "articles": ["21"]}]}}))
    (tree / "other.ann").write_text(json.dumps(          # a non-genomforande .ann
        {"meta": {"status": "generated"}, "editorialLayer": {}}))
    layers = G.genomforande_layers()
    assert list(layers) == ["https://lagen.nu/prop/x"]
    assert layers["https://lagen.nu/prop/x"] == [{"directive": NIS2_URI,
                                                  "articles": ["21"]}]
