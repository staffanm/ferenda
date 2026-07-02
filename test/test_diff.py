"""Structural version diff (accommodanda/lib/diff.py)."""

from accommodanda.lib.diff import blocks, diff_html


def _stycke(text, nid=None, **extra):
    return {"type": "stycke", "id": nid, "text": [text], **extra}


def _paragraf(ordinal, *stycken):
    kids = list(stycken)
    if kids:
        kids[0] = dict(kids[0], beteckning="%s \xa7" % ordinal)
    return {"type": "paragraf", "id": "P" + ordinal, "ordinal": ordinal,
            "children": kids}


def _art(*nodes):
    return {"uri": "https://lagen.nu/1900:1", "structure": list(nodes)}


def test_blocks_flatten_with_markers():
    structure = [
        {"type": "rubrik", "id": None, "level": 2, "text": ["Inledning"]},
        {"type": "kapitel", "id": "K1", "ordinal": "1", "children": [
            _paragraf("1", _stycke("Första stycket.", "K1P1S1"),
                      _stycke("Andra stycket.")),
        ]},
        {"type": "tabell", "id": None, "children": [
            {"type": "rad", "cells": [["a"], ["b"]]}]},
    ]
    out = blocks(structure)
    # the provision marker (paragraf beteckning) is kept apart from the text,
    # so it can render as the normal view's span.num
    assert [(b["marker"], b["text"]) for b in out] == [
        ("", "Inledning"),
        ("1 \xa7", "Första stycket."),
        ("", "Andra stycket."),
        ("", "a | b"),
    ]
    assert out[0]["kind"] == "rubrik"
    assert out[1]["id"] == "K1P1S1"


def test_blocks_reads_inline_link_runs():
    node = _stycke("ignored")
    node["text"] = ["Se ", {"predicate": "dcterms:references",
                            "uri": "https://lagen.nu/1962:700",
                            "text": "brottsbalken"}, "."]
    assert blocks([node])[0]["text"] == "Se brottsbalken."


def test_diff_marks_word_level_change():
    old = _art(_paragraf("1", _stycke("Avgiften är fem kronor.")))
    new = _art(_paragraf("1", _stycke("Avgiften är tio kronor.")))
    html, changed = diff_html(old, new)
    assert changed == 1
    assert "<del>fem</del>" in html and "<ins>tio</ins>" in html
    assert 'class="diff-changed"' in html
    # unchanged words are not marked, the unchanged marker renders plain
    assert "<ins>Avgiften" not in html and "<del>Avgiften" not in html
    assert '<span class="num">1 \xa7</span>' in html


def test_diff_added_block():
    old = _art(_paragraf("1", _stycke("Kvar.")))
    new = _art(_paragraf("1", _stycke("Kvar.")),
               _paragraf("2", _stycke("Ny bestämmelse.")))
    html, changed = diff_html(old, new)
    assert changed == 1
    assert '<span class="num"><ins>2 \xa7</ins></span>' in html
    assert "<ins>Ny bestämmelse.</ins>" in html
    assert 'class="diff-added"' in html


def test_diff_removed_block():
    old = _art(_paragraf("1", _stycke("Kvar.")), _paragraf("2", _stycke("Bort.")))
    new = _art(_paragraf("1", _stycke("Kvar.")))
    html, changed = diff_html(old, new)
    assert changed == 1
    assert '<span class="num"><del>2 \xa7</del></span>' in html
    assert "<del>Bort.</del>" in html
    assert 'class="diff-removed"' in html


def test_diff_replace_pairs_blocks_for_word_marking():
    # a replaced run pairs old/new blocks positionally: the shared words stay
    # unmarked, only the difference is <del>/<ins>-wrapped
    old = _art(_paragraf("2", _stycke("Bort.")))
    new = _art(_paragraf("2", _stycke("Ny bestämmelse.")))
    html, changed = diff_html(old, new)
    assert changed == 1
    assert "<del>Bort.</del>" in html
    assert "<ins>Ny bestämmelse.</ins>" in html
    assert '<span class="num">2 \xa7</span>' in html   # the marker is unchanged


def test_diff_renumbered_paragraf_marks_the_marker():
    old = _art(_paragraf("2", _stycke("Samma text.")))
    new = _art(_paragraf("2 a", _stycke("Samma text.")))
    html, changed = diff_html(old, new)
    assert changed == 1
    assert ('<span class="num"><del>2 \xa7</del> <ins>2 a \xa7</ins></span>'
            in html)
    assert "<ins>Samma" not in html                    # the text is unchanged


def test_diff_headings_render_like_the_page():
    old = _art({"type": "rubrik", "id": "R1", "level": 2, "text": ["Gammal"]})
    new = _art({"type": "rubrik", "id": "R1", "level": 2, "text": ["Ny"]})
    html, _ = diff_html(old, new)
    # same element + class grammar as render_node's headings (h(level+1),
    # class rubrik), so the diff view keeps the reading typography
    assert '<h3 id="R1" class="rubrik diff-changed">' in html


def test_diff_equal_documents_mark_nothing():
    art = _art(_paragraf("1", _stycke("Samma text.")))
    html, changed = diff_html(art, art)
    assert changed == 0
    assert "<ins>" not in html and "<del>" not in html
    assert "Samma text." in html


def test_diff_escapes_markup_in_text():
    old = _art(_stycke("a < b"))
    new = _art(_stycke("a & b"))
    html, _ = diff_html(old, new)
    assert "&lt;" in html and "&amp;" in html
    assert "a < b" not in html


def test_diff_keeps_new_side_ids_for_anchors():
    old = _art(_paragraf("1", _stycke("Gammal.", "P1S1")))
    new = _art(_paragraf("1", _stycke("Ny.", "P1S1")))
    html, _ = diff_html(old, new)
    assert 'id="P1S1"' in html
