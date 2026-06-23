"""Tests for `lagen eurlex ai-annotate`: the act->markdown rendering and the
response validation. The LLM call itself is not exercised (it is the one
deliberately network-bound, on-demand step)."""

import json

import pytest

from accommodanda.eurlex import annotate


ART = {
    "celex": "32099R0001", "title": "Testförordning",
    "body": [
        {"type": "citation", "text": ["med beaktande av fördraget"]},
        {"type": "recital", "num": "1", "text": ["Bakgrund."]},
        {"type": "article", "id": "4", "num": "4", "text": ["Artikel 4 – Skyldigheter"]},
        {"type": "paragraph", "num": "1", "text": ["Datahållaren ska dela data."]},
        {"type": "point", "num": "a", "text": ["på ett säkert sätt."]},
    ],
}


def test_act_markdown_preserves_numbering():
    md = annotate.act_markdown(ART)
    assert "# Testförordning" in md
    assert "(1) Bakgrund." in md                 # numbered recital
    assert "## Artikel 4 – Skyldigheter" in md    # article heading, no doubled num
    assert "1. Datahållaren ska dela data." in md  # numbered paragraph
    assert "(a) på ett säkert sätt." in md        # lettered point


def test_validate_accepts_well_formed_layer():
    layer = annotate._validate(json.dumps(
        {"recitalGroups": [{"id": "a", "label": "x", "range": [1, 2],
                            "articleRefs": ["1"]}],
         "articleToRecitals": {"1": [1, 2]}}))
    assert layer["articleToRecitals"] == {"1": [1, 2]}
    assert layer["recitalGroups"][0]["label"] == "x"


def test_validate_strips_code_fence():
    layer = annotate._validate(
        '```json\n{"recitalGroups": [], "articleToRecitals": {}}\n```')
    assert layer == {"recitalGroups": [], "articleToRecitals": {}}


def test_validate_rejects_missing_keys():
    with pytest.raises(AssertionError):
        annotate._validate(json.dumps({"recitalGroups": []}))   # no articleToRecitals
    with pytest.raises(AssertionError):
        annotate._validate(json.dumps({"recitalGroups": {}, "articleToRecitals": {}}))


def test_annotate_rejects_non_sector3(monkeypatch):
    # a judgment (sector 6) is out of scope; fails before any network call
    with pytest.raises(AssertionError):
        annotate.annotate("62019CJ0311")
