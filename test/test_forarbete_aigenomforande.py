"""The LLM directive->paragraf transposition pass (forarbete.aigenomforande)
and the relate-time layer preference (genomforande.prop_implements) -- the
non-LLM core: candidate selection, per-law batching, the tagged multi-directive
catalog, entry-id rendering, batch-reply validation (pinpoint- and bare-number
articles, drop-not-poison), edge fan-out, and the .ann-supersedes-mechanical
join. The LLM call itself is not exercised."""

import json

import pytest

from ferenda.forarbete import aigenomforande as A
from ferenda.forarbete import genomforande as G
from ferenda.lib import annstore

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


def test_validate_keeps_wellformed_sfs_pinpoint_and_disregards_malformed():
    # the optional Swedish-side stycke/punkt pinpoint ("sfs": "S1" / "S3N2"):
    # kept when it has the element-id shape, silently disregarded when not --
    # a malformed pinpoint must never cost the mapping itself (forgiving by
    # design; whether the stycke exists in the published law is checked later,
    # at resolve time)
    by_id = {"E1": _entry("Paragrafen genomför artikel 21 i NIS 2-direktivet.")}
    quote = "Paragrafen genomför artikel 21 i NIS 2-direktivet."
    items = A.validate(_reply([
        {"entry": "E1", "dir": "A", "articles": ["21"], "sfs": "S1", "quote": quote},
        {"entry": "E1", "dir": "A", "articles": ["21"], "sfs": "S3N2", "quote": quote},
        {"entry": "E1", "dir": "A", "articles": ["21"], "sfs": "stycke 2", "quote": quote},
        {"entry": "E1", "dir": "A", "articles": ["21"], "quote": quote},
    ]), by_id, CATALOG)
    assert [i.get("sfs") for i in items] == ["S1", "S3N2", None, None]


def test_edges_carry_the_sfs_pinpoint():
    entry = _entry("Paragrafens första stycke genomför artikel 21.",
                   chapter="4", paragrafer=["2"])
    by_id = {"E1": entry}
    item = {"entry": "E1", "tag": "A", "articles": ["21"], "pinpoints": [],
            "partial": False, "sfs": "S1",
            "quote": "Paragrafens första stycke genomför artikel 21."}
    (edge,) = A.edges_for(item, by_id, CATALOG)
    assert edge["sfs"] == "S1"
    plain = A.edges_for({**item, "sfs": None}, by_id, CATALOG)[0]
    assert "sfs" not in plain                     # absent, not null noise


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


def test_validate_salvages_a_reply_with_trailing_extra_data():
    # gemma sometimes writes a complete answer and keeps going (a second
    # {"mappings": …} object, or trailing prose) -- json.loads' "Extra data"
    # used to lose the whole valid answer. Every parseable object's mappings
    # are merged; the per-item checks still guard each one.
    by_id = {"E1": _entry("Paragrafen genomför artikel 21 i NIS 2-direktivet."),
             "E2": _entry("Paragrafen genomför artikel 23 i NIS 2-direktivet.",
                          paragrafer=["9"])}
    q1 = "Paragrafen genomför artikel 21 i NIS 2-direktivet."
    q2 = "Paragrafen genomför artikel 23 i NIS 2-direktivet."
    reply = (_reply([{"entry": "E1", "dir": "A", "articles": ["21"], "quote": q1}])
             + "\n"
             + _reply([{"entry": "E2", "dir": "A", "articles": ["23"], "quote": q2}])
             + "\nHär är en avslutande förklaring.")
    items = A.validate(reply, by_id, CATALOG)
    assert [(i["entry"], i["articles"]) for i in items] == \
        [("E1", ["21"]), ("E2", ["23"])]


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
