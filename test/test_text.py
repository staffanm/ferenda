"""The shared artifact text flattener (accommodanda/lib/text.py)."""

from accommodanda.lib import text

ART = {
    "uri": "https://lagen.nu/1962:700",
    "structure": [
        {"type": "kapitel", "id": "K1", "text": ["1 kap. Inledande"],
         "children": [
            {"type": "paragraf", "id": "K1P1",
             "text": ["Den som ",
                      {"uri": "https://lagen.nu/1962:700#K3P1", "text": "dödar"},
                      " annan"],
             "children": [{"type": "punkt", "ordinal": "1",
                           "text": ["med uppsåt"]}]},
            {"type": "paragraf", "id": "K1P2", "text": ["Straffet är fängelse."]},
         ]},
        {"type": "tabell", "children": [
            {"type": "rad", "cells": [["Brott"], ["Straff ", {"text": "X"}]]}]},
    ],
    "amendments": [{"content": [{"type": "stycke", "text": ["Ändrat 1990."]}]}],
}


def test_runs_text_plain_and_links():
    assert text.runs_text(["a", {"text": "b"}, "c"]) == "abc"
    assert text.runs_text("bare string") == "bare string"
    assert text.runs_text([{"uri": "x"}]) == ""        # link dict, no text


def test_node_text_includes_descendants_and_cells():
    para = ART["structure"][0]["children"][0]
    assert text.node_text(para) == "Den som dödar annan med uppsåt"
    rad = ART["structure"][1]["children"][0]
    assert text.node_text(rad) == "Brott Straff X"


def test_document_text_spans_structure_and_amendments():
    full = text.document_text(ART)
    assert "1 kap. Inledande" in full
    assert "dödar annan med uppsåt" in full
    assert "Straffet är fängelse." in full
    assert "Brott Straff X" in full
    assert "Ändrat 1990." in full                       # amendment content


def test_fragment_texts_one_per_id_bearing_node():
    frags = dict(text.fragment_texts(ART))
    assert frags["https://lagen.nu/1962:700#K1P1"] == "Den som dödar annan med uppsåt"
    assert frags["https://lagen.nu/1962:700#K1P2"] == "Straffet är fängelse."
    # the kapitel rolls up its children's text
    assert "Inledande" in frags["https://lagen.nu/1962:700#K1"]
    # only id-bearing nodes are fragments (the punkt has no id)
    assert all(u.startswith("https://lagen.nu/1962:700#K") for u in frags)


def test_dv_body_section():
    art = {"uri": "https://lagen.nu/dom/nja/2009s796",
           "body": [{"type": "rubrik", "id": "r1", "text": ["Domskäl"]},
                    {"type": "stycke", "text": ["HD finner ", {"text": "att"}]}]}
    assert text.document_text(art) == "Domskäl HD finner att"
    assert text.fragment_texts(art) == [
        ("https://lagen.nu/dom/nja/2009s796#r1", "Domskäl")]
