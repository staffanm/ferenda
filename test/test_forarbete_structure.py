"""Förarbete hierarchy: the flat block run grouped into a nested section tree
(accommodanda/forarbete/structure.py)."""

from accommodanda.forarbete.structure import flatten, nest


def _blocks():
    # front matter, then a 14 -> 14.3 -> 14.3.4 outline, then a sibling 15
    return [
        {"type": "stycke", "page": 1, "text": ["Regeringen överlämnar …"]},
        {"type": "rubrik", "page": 5, "level": 1, "text": ["14 Ikraftträdande"]},
        {"type": "stycke", "page": 5, "text": ["Lagen träder i kraft …"]},
        {"type": "rubrik", "page": 6, "level": 2, "text": ["14.3 Övergång"]},
        {"type": "stycke", "page": 6, "text": ["Äldre föreskrifter gäller …"]},
        {"type": "rubrik", "page": 7, "level": 3, "text": ["14.3.4 Detaljer"]},
        {"type": "stycke", "page": 7, "text": ["Detaljtext."]},
        {"type": "rubrik", "page": 8, "level": 1, "text": ["15 Kostnader"]},
        {"type": "stycke", "page": 8, "text": ["Förslaget medför …"]},
    ]


def test_nest_builds_the_numbered_outline():
    root = nest(_blocks())
    # front-matter stycke, then two top-level sections
    assert [n["type"] for n in root] == ["stycke", "avsnitt", "avsnitt"]
    s14, s15 = root[1], root[2]
    assert (s14["id"], s14["num"], s14["level"]) == ("a14", "14", 1)
    assert (s15["id"], s15["num"], s15["level"]) == ("a15", "15", 1)

    # 14 contains its stycke and the nested 14.3
    assert [c["type"] for c in s14["children"]] == ["stycke", "avsnitt"]
    s143 = s14["children"][1]
    assert s143["id"] == "a14.3" and s143["level"] == 2
    # 14.3 contains its stycke and the deeper 14.3.4
    s1434 = s143["children"][1]
    assert s1434["id"] == "a14.3.4" and s1434["level"] == 3
    assert [c["type"] for c in s1434["children"]] == ["stycke"]


def test_unnumbered_heading_gets_a_counter_id():
    root = nest([{"type": "rubrik", "level": 3, "text": ["Bakgrund"]},
                 {"type": "stycke", "text": ["…"]}])
    assert root[0]["id"] == "sec1" and "num" not in root[0]
    assert root[0]["children"][0]["type"] == "stycke"


def test_gap_in_levels_attaches_to_nearest_ancestor():
    # a level-3 heading directly under a level-1 (no level-2 between)
    root = nest([{"type": "rubrik", "level": 1, "text": ["1 A"]},
                 {"type": "rubrik", "level": 3, "text": ["Under A"]}])
    assert root[0]["children"][0]["text"] == ["Under A"]   # nested, not a sibling


def test_duplicate_section_numbers_disambiguate():
    root = nest([{"type": "rubrik", "level": 1, "text": ["1 A"]},
                 {"type": "rubrik", "level": 1, "text": ["1 A again"]}])
    assert [n["id"] for n in root] == ["a1", "a1-2"]


def test_flatten_is_the_inverse_document_order():
    blocks = _blocks()
    flat = flatten(nest(blocks))
    # same blocks, same order -- avsnitt sections become their rubrik again
    assert [b["type"] for b in flat] == [b["type"] for b in blocks]
    assert [b["text"] for b in flat] == [b["text"] for b in blocks]
    assert [b.get("page") for b in flat] == [b.get("page") for b in blocks]
