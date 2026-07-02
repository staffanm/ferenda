"""Tests for `lagen eurlex ai-annotate`: the act->markdown rendering and the
response validation. The LLM call itself is not exercised (it is the one
deliberately network-bound, on-demand step)."""

import json

import pytest

from accommodanda.eurlex import annotate


ART = {
    "celex": "32099R0001", "title": "Testförordning",
    "structure": [
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
    with pytest.raises(ValueError):
        annotate._validate(json.dumps({"recitalGroups": []}))   # no articleToRecitals
    with pytest.raises(ValueError):
        annotate._validate(json.dumps({"recitalGroups": {}, "articleToRecitals": {}}))


def _layer_with_groups(n):
    return json.dumps({
        "recitalGroups": [{"id": str(i), "label": "g%d" % i, "range": [i, i],
                           "articleRefs": []} for i in range(1, n + 1)],
        "articleToRecitals": {}})


def test_validate_rejects_too_many_recital_groups():
    annotate._validate(_layer_with_groups(annotate.MAX_RECITAL_GROUPS))   # cap: ok
    with pytest.raises(ValueError, match="too many recital groups"):
        annotate._validate(_layer_with_groups(annotate.MAX_RECITAL_GROUPS + 1))


def test_author_retries_once_then_succeeds(monkeypatch):
    # first reply over-sections (rejected), second reply is within the cap
    replies = iter([_layer_with_groups(annotate.MAX_RECITAL_GROUPS + 5),
                    _layer_with_groups(3)])
    seen = []

    def fake_complete(prompt):
        seen.append(prompt)
        return next(replies)

    monkeypatch.setattr(annotate.llm, "complete", fake_complete)
    layer = annotate._author("BASE PROMPT")
    assert len(layer["recitalGroups"]) == 3
    assert len(seen) == 2                                  # one retry
    assert "UNDERKÄNDES" in seen[1] and "too many recital groups" in seen[1]


def test_author_raises_after_one_failed_retry(monkeypatch):
    monkeypatch.setattr(annotate.llm, "complete",
                        lambda prompt: _layer_with_groups(99))
    with pytest.raises(ValueError, match="too many recital groups"):
        annotate._author("BASE PROMPT")


def test_annotate_rejects_non_sector3(monkeypatch):
    # a judgment (sector 6) is out of scope; fails before any network call
    with pytest.raises(AssertionError):
        annotate.annotate("62019CJ0311")
