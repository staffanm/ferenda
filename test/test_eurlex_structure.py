"""EU-act hierarchy: the flat typed-block run grouped into its containment tree
(accommodanda/eurlex/structure.py)."""

from accommodanda.eurlex.structure import nest
from accommodanda.lib.eu_structure import flatten


def _blocks():
    # preamble, then enacting articles, then an annex with TITLE divisions and
    # articles whose paragraphs carry points
    return [
        {"type": "citation", "text": ["Having regard to …"]},
        {"type": "recital", "num": "1", "text": ["Whereas …"]},
        {"type": "article", "num": "1", "id": "1", "text": ["Article 1"]},
        {"type": "paragraph", "text": ["Enacting paragraph."]},
        {"type": "heading", "level": 1, "text": ["ANNEX"]},
        {"type": "heading", "level": 2, "text": ["TITLE I"]},
        {"type": "article", "num": "1", "id": "1", "text": ["Article 1"]},
        {"type": "paragraph", "num": "1", "id": "1.1", "text": ["First para."]},
        {"type": "point", "num": "a", "id": "1.1.a", "text": ["point a"]},
        {"type": "point", "num": "b", "id": "1.1.b", "text": ["point b"]},
        {"type": "heading", "level": 2, "text": ["TITLE II"]},
        {"type": "article", "num": "7", "id": "7", "text": ["Article 7"]},
        {"type": "paragraph", "text": ["Capital paragraph."]},
    ]


def test_nest_builds_division_article_paragraph_point():
    root = nest(_blocks())
    # preamble matter + the enacting article are at the root, then the ANNEX
    assert [n["type"] for n in root] == ["citation", "recital", "article", "heading"]

    enacting = root[2]
    assert enacting["children"][0]["text"] == ["Enacting paragraph."]

    annex = root[3]                                   # ANNEX (level 1)
    titles = [c for c in annex["children"] if c["type"] == "heading"]
    assert [t["text"][0] for t in titles] == ["TITLE I", "TITLE II"]   # nested under ANNEX

    title1 = titles[0]
    art1 = title1["children"][0]
    assert art1["type"] == "article" and art1["id"] == "1"
    para = art1["children"][0]
    assert para["type"] == "paragraph" and para["id"] == "1.1"
    # points nest under their paragraph
    assert [p["id"] for p in para["children"]] == ["1.1.a", "1.1.b"]


def test_article_ids_are_citation_anchors_untouched():
    root = nest(_blocks())
    annex = root[3]
    art7 = annex["children"][1]["children"][0]        # TITLE II > Article 7
    assert art7["id"] == "7"                          # the celex#7 citation target


def test_flatten_is_the_inverse_document_order():
    blocks = _blocks()
    flat = flatten(nest(blocks))
    assert [b["type"] for b in flat] == [b["type"] for b in blocks]
    assert [b["text"] for b in flat] == [b["text"] for b in blocks]
    assert [b.get("id") for b in flat] == [b.get("id") for b in blocks]
    # flattened blocks carry no `children` key (leaves again)
    assert all("children" not in b for b in flat)
