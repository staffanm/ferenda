"""Tests for `lagen remisser ai-analyze`: the section-outline build, the strict
reply validation, and the end-to-end write + retry. The LLM call itself is
faked (it is the one deliberately network-bound, on-demand step)."""

import json

import pytest

from accommodanda.lib import layout
from accommodanda.remisser import ai_analyze

STRUCTURE = [
    {"type": "avsnitt", "id": "a3.1", "text": ["3.1 Utredningens uppdrag"],
     "children": [
         {"type": "stycke", "text": ["Uppdraget avser..."]},
         {"type": "avsnitt", "id": "a3.2", "text": ["3.2 Avgränsningar"],
          "children": []},
     ]},
    {"type": "avsnitt", "id": "sec4", "text": ["Överväganden och förslag"],
     "children": []},
]

FULL_TEXT = [
    "Kammarkollegiet tillstyrker i huvudsak förslaget men har vissa synpunkter.",
    "När det gäller avgränsningarna i avsnitt 3.2 anser kollegiet att de är för snäva.",
    "Övervägandena om finansiering är däremot väl underbyggda.",
]

VALID_REPLY = json.dumps({
    "overall": {"sentiment": 0.4,
                "quote": "Kammarkollegiet tillstyrker i huvudsak förslaget"},
    "segments": [
        {"forarbete_id": "a3.2", "sentiment": -0.6,
         "quote": "kollegiet att de är för snäva"},
        {"forarbete_id": "sec4", "sentiment": 0.8,
         "quote": "Övervägandena om finansiering är däremot väl underbyggda"},
    ],
}, ensure_ascii=False)


# ---- section outline -------------------------------------------------------

def test_section_outline_walks_avsnitt_tree():
    outline, ids = ai_analyze.section_outline(STRUCTURE)
    assert ids == {"a3.1", "a3.2", "sec4"}
    assert outline.splitlines() == [
        "[a3.1] 3.1 Utredningens uppdrag",
        "[a3.2] 3.2 Avgränsningar",          # nested child, in document order
        "[sec4] Överväganden och förslag",
    ]


def test_section_outline_truncates_long_headings():
    long = [{"type": "avsnitt", "id": "a1", "text": ["x" * 500], "children": []}]
    outline, _ = ai_analyze.section_outline(long)
    assert len(outline) == len("[a1] ") + ai_analyze.LABEL_MAX


# ---- validation ------------------------------------------------------------

HAYSTACK = ai_analyze.normalize_space("\n\n".join(FULL_TEXT))
IDS = {"a3.1", "a3.2", "sec4"}


def _validate(reply):
    return ai_analyze._validate(reply, IDS, HAYSTACK)


def test_validate_accepts_well_formed_reply():
    out = _validate(VALID_REPLY)
    assert out["overall"]["sentiment"] == 0.4
    assert [s["forarbete_id"] for s in out["segments"]] == ["a3.2", "sec4"]


def test_validate_accepts_empty_segments():
    # a purely general answer, no section-specific commentary, is valid
    out = _validate(json.dumps(
        {"overall": {"sentiment": 0.0, "quote": FULL_TEXT[0]}, "segments": []},
        ensure_ascii=False))
    assert out["segments"] == []


def test_validate_rejects_unknown_forarbete_id():
    with pytest.raises(ValueError, match="not in the outline"):
        _validate(json.dumps(
            {"overall": {"sentiment": 0.4, "quote": FULL_TEXT[0]},
             "segments": [{"forarbete_id": "a9.9", "sentiment": 0.0,
                           "quote": FULL_TEXT[0]}]}, ensure_ascii=False))


def test_validate_rejects_fabricated_quote():
    with pytest.raises(ValueError, match="verbatim substring"):
        _validate(json.dumps(
            {"overall": {"sentiment": 0.4,
                         "quote": "detta citat står inte i svaret alls"},
             "segments": []}, ensure_ascii=False))


def test_validate_rejects_sentiment_out_of_range():
    with pytest.raises(ValueError, match="outside"):
        _validate(json.dumps(
            {"overall": {"sentiment": 1.7, "quote": FULL_TEXT[0]},
             "segments": []}, ensure_ascii=False))


def test_validate_rejects_missing_segments():
    with pytest.raises(ValueError, match="segments list"):
        _validate(json.dumps(
            {"overall": {"sentiment": 0.4, "quote": FULL_TEXT[0]}},
            ensure_ascii=False))


def test_validate_rejects_boolean_sentiment():
    # bool is an int subclass; a JSON `true` must not sneak through as a score
    with pytest.raises(ValueError, match="non-numeric sentiment"):
        _validate(json.dumps(
            {"overall": {"sentiment": True, "quote": FULL_TEXT[0]},
             "segments": []}, ensure_ascii=False))


# ---- end-to-end analyze() --------------------------------------------------

@pytest.fixture
def corpus(tmp_path, monkeypatch):
    """A synthetic remissvar artifact + its referred förarbete artifact on disk,
    with the artifact roots pointed at tmp_path."""
    monkeypatch.setitem(layout.ARTIFACT_ROOT, "remisser", tmp_path / "remisser")
    monkeypatch.setitem(layout.ARTIFACT_ROOT, "forarbete", tmp_path / "forarbete")

    fa_path = layout.artifact("forarbete", "sou/2026-14")   # slugged (colon -> dash)
    fa_path.parent.mkdir(parents=True, exist_ok=True)
    fa_path.write_text(json.dumps(
        {"basefile": "sou/2026-14", "structure": STRUCTURE}, ensure_ascii=False))

    basefile = "en-remiss-2026/kammarkollegiet"
    art_path = layout.artifact("remisser", basefile)
    art_path.parent.mkdir(parents=True, exist_ok=True)
    art_path.write_text(json.dumps({
        "basefile": basefile,
        "case_basefile": "en-remiss-2026",
        "organisation": "Kammarkollegiet",
        "case_titel": "En utredning",
        # colon identifier as harvested -- analyze() slugs it for the join
        "remitterat": [{"typ": "sou", "basefile": "2026:14"}],
        "source_url": "https://example.org/svar.pdf",
        "full_text": FULL_TEXT,
    }, ensure_ascii=False))
    return basefile


def test_analyze_writes_ann_sidecar(corpus, monkeypatch):
    monkeypatch.setattr(ai_analyze.llm, "complete_thread",
                        lambda messages, max_tokens=None: VALID_REPLY)
    path = ai_analyze.analyze(corpus)
    assert path.suffix == ".ann"
    data = json.loads(path.read_text())
    assert data["overall"]["sentiment"] == 0.4
    assert [s["forarbete_id"] for s in data["segments"]] == ["a3.2", "sec4"]


def test_analyze_passes_outline_and_text_to_model(corpus, monkeypatch):
    seen = []

    def fake_complete_thread(messages, max_tokens=None):
        seen.append(list(messages))
        return VALID_REPLY

    monkeypatch.setattr(ai_analyze.llm, "complete_thread", fake_complete_thread)
    ai_analyze.analyze(corpus)
    first_prompt = seen[0][0]["content"]
    assert seen[0][0]["role"] == "user"
    assert "[a3.2] 3.2 Avgränsningar" in first_prompt      # outline spliced in
    assert FULL_TEXT[1] in first_prompt                     # answer text spliced in


def test_analyze_retries_once_then_succeeds(corpus, monkeypatch):
    # first reply cites an unknown id (rejected), second is valid
    bad_reply = json.dumps(
        {"overall": {"sentiment": 0.4, "quote": FULL_TEXT[0]},
         "segments": [{"forarbete_id": "a9.9", "sentiment": 0.0,
                       "quote": FULL_TEXT[0]}]}, ensure_ascii=False)
    replies = iter([bad_reply, VALID_REPLY])
    seen = []

    def fake_complete_thread(messages, max_tokens=None):
        seen.append(list(messages))
        return next(replies)

    monkeypatch.setattr(ai_analyze.llm, "complete_thread", fake_complete_thread)
    ai_analyze.analyze(corpus)
    assert len(seen) == 2
    # the retry call is a real follow-up turn: the original user prompt, the
    # model's own actual first reply replayed as an assistant turn, then a short
    # user turn naming the failure -- not the same ever-growing single message
    assert len(seen[1]) == 3
    assert seen[1][0] == seen[0][0]                         # original prompt, unchanged
    assert seen[1][1] == {"role": "assistant", "content": bad_reply}
    assert seen[1][2]["role"] == "user"
    assert "UNDERKÄNDES" in seen[1][2]["content"]
    assert "not in the outline" in seen[1][2]["content"]


def test_analyze_raises_after_one_failed_retry(corpus, monkeypatch):
    monkeypatch.setattr(ai_analyze.llm, "complete_thread",
                        lambda messages, max_tokens=None: "not json at all")
    with pytest.raises(ValueError):
        ai_analyze.analyze(corpus)


def test_analyze_asserts_on_empty_remitterat(corpus, monkeypatch):
    art_path = layout.artifact("remisser", corpus)
    data = json.loads(art_path.read_text())
    data["remitterat"] = []
    art_path.write_text(json.dumps(data, ensure_ascii=False))
    with pytest.raises(AssertionError, match="remitterat is empty"):
        ai_analyze.analyze(corpus)


def test_analyze_asserts_on_missing_forarbete(corpus, monkeypatch):
    art_path = layout.artifact("remisser", corpus)
    data = json.loads(art_path.read_text())
    data["remitterat"] = [{"typ": "sou", "basefile": "9999:99"}]   # no artifact
    art_path.write_text(json.dumps(data, ensure_ascii=False))
    with pytest.raises(AssertionError, match="run `lagen forarbete parse"):
        ai_analyze.analyze(corpus)
