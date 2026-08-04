"""Tests for `lagen remisser ai-analyze`: the section-outline build, the strict
reply validation, and the end-to-end write + retry. The LLM call itself is
faked (it is the one deliberately network-bound, on-demand step)."""

import json

import pytest

from accommodanda.lib import annstore, layout
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
                "quote": "Kammarkollegiet tillstyrker i huvudsak förslaget",
                "quote_type": "standpunkt"},
    "segments": [
        {"forarbete_id": "a3.2", "sentiment": -0.6,
         "quote": "kollegiet att de är för snäva", "quote_type": "grund"},
        {"forarbete_id": "sec4", "sentiment": 0.8,
         "quote": "Övervägandena om finansiering är däremot väl underbyggda",
         "quote_type": "grund"},
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


# ---- ärende expansion ------------------------------------------------------

@pytest.fixture
def arende(tmp_path, monkeypatch):
    """A stored `Remiss` record with three instances, one of them not fetched."""
    monkeypatch.setattr(layout, "REMISSER_DOWNLOADED", tmp_path / "downloaded")
    path = layout.remisser_arende("sou/2026:14")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "basefile": "sou/2026:14", "titel": "En utredning",
        "url": "https://example.org/remiss",
        "svar": [
            {"organisation": "Kammarkollegiet", "downloaded": True,
             "source_url": "https://example.org/a/kammarkollegiet.pdf"},
            # asked but has not answered yet -- no PDF, so nothing to analyze
            {"organisation": "Domstolsverket", "downloaded": False,
             "source_url": "https://example.org/a/domstolsverket.pdf"},
            {"organisation": "Riksdagens ombudsmän", "downloaded": True,
             "source_url": "https://example.org/a/riksdagens-ombudsman.pdf"},
        ],
    }, ensure_ascii=False))
    return path


def test_answers_expands_an_arende_to_its_fetched_answers(arende):
    # the slug spelling the CLI takes ("2026-14") finds the record, and the
    # basefiles come back in the record's own colon spelling
    assert ai_analyze.answers("sou/2026-14") == [
        "sou/2026:14/kammarkollegiet",
        "sou/2026:14/riksdagens-ombudsman",
    ]


def test_answers_leaves_a_named_answer_alone(arende):
    # three segments = already one answer; returned unchanged (not re-spelled),
    # which is what lets the caller tell an expansion from a direct naming
    assert ai_analyze.answers("sou/2026-14/kammarkollegiet") == [
        "sou/2026-14/kammarkollegiet"]


# ---- validation ------------------------------------------------------------

UNITS = ai_analyze.answer_units(FULL_TEXT)
HAYSTACK = ai_analyze.normalize_space("\n\n".join(FULL_TEXT))
IDS = {"a3.1", "a3.2", "sec4"}


def _validate(reply):
    return ai_analyze._validate(reply, IDS, UNITS, HAYSTACK)


def test_validate_accepts_well_formed_reply():
    out = _validate(VALID_REPLY)
    assert out["overall"]["sentiment"] == 0.4
    assert [s["forarbete_id"] for s in out["segments"]] == ["a3.2", "sec4"]


def test_validate_accepts_empty_segments():
    # a purely general answer, no section-specific commentary, is valid
    out = _validate(json.dumps(
        {"overall": {"sentiment": 0.0, "quote": FULL_TEXT[0],
                     "quote_type": "standpunkt"}, "segments": []},
        ensure_ascii=False))
    assert out["segments"] == []


def test_validate_rejects_unknown_forarbete_id():
    with pytest.raises(ValueError, match="not in the outline"):
        _validate(json.dumps(
            {"overall": {"sentiment": 0.4, "quote": FULL_TEXT[0],
                         "quote_type": "grund"},
             "segments": [{"forarbete_id": "a9.9", "sentiment": 0.0,
                           "quote": FULL_TEXT[0], "quote_type": "grund"}]},
            ensure_ascii=False))


def test_validate_rejects_a_quote_that_is_nowhere_in_the_answer():
    """The end-to-end guarantee the whole pass rests on: nothing reaches the
    `.ann` that the organisation did not write. `snap_to_source` repairs a
    *reworded* quote, but one matching no passage still has to fail the layer --
    covering `snap_to_source` alone would not show that `_validate` rejects."""
    with pytest.raises(ValueError, match="verbatim substring"):
        _validate(json.dumps(
            {"overall": {"sentiment": 0.4, "quote_type": "grund",
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
            {"overall": {"sentiment": 0.4, "quote": FULL_TEXT[0],
                         "quote_type": "grund"}},
            ensure_ascii=False))


def test_validate_accepts_a_standpunkt_quote():
    """A remissinstans that endorses without giving any reason is the normal
    case, not a failure: "standpunkt" is how the model says so, and licensing it
    is what stops it inventing grounds the answer never stated."""
    out = _validate(json.dumps(
        {"overall": {"sentiment": 1.0, "quote": FULL_TEXT[0],
                     "quote_type": "standpunkt"},
         "segments": [{"forarbete_id": "sec4", "sentiment": 1.0,
                       "quote": FULL_TEXT[0], "quote_type": "standpunkt"}]},
        ensure_ascii=False))
    assert out["overall"]["quote_type"] == "standpunkt"
    assert out["segments"][0]["quote_type"] == "standpunkt"


def test_validate_rejects_a_missing_quote_type():
    with pytest.raises(ValueError, match="quote_type"):
        _validate(json.dumps(
            {"overall": {"sentiment": 0.4, "quote": FULL_TEXT[0]},
             "segments": []}, ensure_ascii=False))


def test_validate_rejects_an_invented_quote_type():
    # the two values are the whole point of the field -- a third one ("citat",
    # "motivering", an English guess) would silently defeat the distinction
    with pytest.raises(ValueError, match="expected one of"):
        _validate(json.dumps(
            {"overall": {"sentiment": 0.4, "quote": FULL_TEXT[0],
                         "quote_type": "motivering"},
             "segments": []}, ensure_ascii=False))


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
    with the artifact tree and the curated store pointed under tmp_path."""
    monkeypatch.setattr(layout, "ARTIFACT", tmp_path / "artifact")
    monkeypatch.setattr(annstore, "ROOT", tmp_path / "ann")

    fa_path = layout.artifact("forarbete", "sou/2026-14")   # slugged (colon -> dash)
    fa_path.parent.mkdir(parents=True, exist_ok=True)
    fa_path.write_text(json.dumps(
        {"basefile": "sou/2026-14", "structure": STRUCTURE}, ensure_ascii=False))

    basefile = "sou/2026:14/kammarkollegiet"
    art_path = layout.artifact("remisser", basefile)
    art_path.parent.mkdir(parents=True, exist_ok=True)
    art_path.write_text(json.dumps({
        "basefile": basefile,
        "arende_basefile": "sou/2026:14",
        "organisation": "Kammarkollegiet",
        "arende_titel": "En utredning",
        # colon identifier as harvested -- analyze() slugs it for the join
        "remitterat": [{"typ": "sou", "basefile": "2026:14"}],
        "source_url": "https://example.org/svar.pdf",
        "full_text": FULL_TEXT,
    }, ensure_ascii=False))
    return basefile


def test_analyze_writes_ann_layer(corpus, monkeypatch):
    monkeypatch.setattr(ai_analyze.llm, "complete_thread",
                        lambda messages, **kw: VALID_REPLY)
    path = ai_analyze.analyze(corpus)
    assert path == annstore.path("remisser", corpus)   # the curated store
    data = json.loads(path.read_text())
    assert data["overall"]["sentiment"] == 0.4
    assert [s["forarbete_id"] for s in data["segments"]] == ["a3.2", "sec4"]
    # the envelope records provenance: fresh = generated, both inputs hashed
    assert data["meta"]["status"] == "generated"
    assert sorted(data["meta"]["inputs"]) == [
        "artifact:forarbete/sou/2026-14",
        "artifact:remisser/sou/2026:14/kammarkollegiet"]
    assert annstore.drifted(data["meta"]["inputs"]) == []


def test_analyze_joins_a_promemoria_across_a_case_mismatch(tmp_path, monkeypatch):
    """A promemoria is keyed on its diarienummer, and regeringen.se prints the
    department prefix with either case -- the remiss page says "JU2026/01595"
    where the promemoria's own listing says "Ju2026/01595". The tree's spelling
    settles the join, and the recorded input key uses that same spelling so
    drift detection keeps working."""
    monkeypatch.setattr(layout, "ARTIFACT", tmp_path / "artifact")
    monkeypatch.setattr(annstore, "ROOT", tmp_path / "ann")

    fa_path = layout.artifact("forarbete", "pm/Ju2026-01595")   # as forarbete has it
    fa_path.parent.mkdir(parents=True, exist_ok=True)
    fa_path.write_text(json.dumps(
        {"basefile": "pm/Ju2026/01595", "structure": STRUCTURE}, ensure_ascii=False))

    # the ärende is keyed on the promemoria, in the spelling its own remiss page
    # used -- the one that does *not* match the forarbete tree
    basefile = "pm/JU2026/01595/kammarkollegiet"
    art_path = layout.artifact("remisser", basefile)
    art_path.parent.mkdir(parents=True, exist_ok=True)
    art_path.write_text(json.dumps({
        "basefile": basefile, "arende_basefile": "pm/JU2026/01595",
        "organisation": "Kammarkollegiet", "arende_titel": "En promemoria",
        "remitterat": [{"typ": "pm", "basefile": "JU2026/01595"}],   # remiss casing
        "source_url": "https://example.org/svar.pdf", "full_text": FULL_TEXT,
    }, ensure_ascii=False))

    monkeypatch.setattr(ai_analyze.llm, "complete_thread",
                        lambda messages, **kw: VALID_REPLY)
    data = json.loads(ai_analyze.analyze(basefile).read_text())
    assert sorted(data["meta"]["inputs"]) == [
        "artifact:forarbete/pm/Ju2026-01595",
        "artifact:remisser/pm/JU2026/01595/kammarkollegiet"]
    assert annstore.drifted(data["meta"]["inputs"]) == []


def test_analyze_joins_a_promemoria_on_its_landing_slug(tmp_path, monkeypatch):
    """forarbete keys a promemoria on its diarienummer only when its own listing
    text stated one -- otherwise on the landing-page slug. The remiss page states
    neither (it carries its *own* dnr, which usually but not always coincides),
    so it hands the join both candidates and the tree settles it. ~30% of the
    promemoria ärenden harvested resolve this way, not by dnr."""
    monkeypatch.setattr(layout, "ARTIFACT", tmp_path / "artifact")
    monkeypatch.setattr(annstore, "ROOT", tmp_path / "ann")

    # the tree knows it only by slug; nothing is filed under the remiss's dnr
    fa_path = layout.artifact("forarbete", "pm/en-veterangard-for-vila")
    fa_path.parent.mkdir(parents=True, exist_ok=True)
    fa_path.write_text(json.dumps({"basefile": "pm/en-veterangard-for-vila",
                                   "structure": STRUCTURE}, ensure_ascii=False))

    basefile = "pm/Fö2024/01914/kammarkollegiet"
    art_path = layout.artifact("remisser", basefile)
    art_path.parent.mkdir(parents=True, exist_ok=True)
    art_path.write_text(json.dumps({
        "basefile": basefile, "arende_basefile": "pm/Fö2024/01914",
        "organisation": "Kammarkollegiet", "arende_titel": "En promemoria",
        "remitterat": [{"typ": "pm", "basefile": "Fö2024/01914",
                        "slug": "en-veterangard-for-vila"}],
        "source_url": "https://example.org/svar.pdf", "full_text": FULL_TEXT,
    }, ensure_ascii=False))

    monkeypatch.setattr(ai_analyze.llm, "complete_thread",
                        lambda messages, **kw: VALID_REPLY)
    data = json.loads(ai_analyze.analyze(basefile).read_text())
    assert "artifact:forarbete/pm/en-veterangard-for-vila" in data["meta"]["inputs"]
    assert annstore.drifted(data["meta"]["inputs"]) == []


def test_resolve_basefile_prefers_the_primary_over_an_alternate(tmp_path, monkeypatch):
    """The alternate is a second candidate, not a replacement: when the tree
    holds the dnr-keyed document, that is the one the join must land on."""
    monkeypatch.setattr(layout, "ARTIFACT", tmp_path / "artifact")
    for bf in ("pm/KN2026-01597", "pm/nationellt-forbud-mot-pfas"):
        p = layout.artifact("forarbete", bf)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}")
    assert layout.resolve_basefile(
        "forarbete", "pm/KN2026-01597", "pm/nationellt-forbud-mot-pfas"
    ) == "pm/KN2026-01597"


def test_resolve_basefile_leaves_an_unresolvable_name_alone(tmp_path, monkeypatch):
    """Case is the only licence: a basefile matching nothing on disk comes back
    untouched, so the caller's own missing-artifact error names what it looked
    for instead of a silently substituted neighbour."""
    monkeypatch.setattr(layout, "ARTIFACT", tmp_path / "artifact")
    fa_path = layout.artifact("forarbete", "pm/Ju2026-01595")
    fa_path.parent.mkdir(parents=True, exist_ok=True)
    fa_path.write_text("{}")

    assert layout.resolve_basefile("forarbete", "pm/JU2026-01595") == "pm/Ju2026-01595"
    assert layout.resolve_basefile("forarbete", "pm/Ju2026-01595") == "pm/Ju2026-01595"
    # a different document entirely -- not a respelling of anything present
    assert layout.resolve_basefile("forarbete", "pm/Fi2026-00001") == "pm/Fi2026-00001"


def test_analyze_refuses_to_overwrite_verified(corpus, monkeypatch):
    # a hand-verified analysis is curation: refuse before the LLM spend
    ann = annstore.path("remisser", corpus)
    ann.parent.mkdir(parents=True, exist_ok=True)
    ann.write_text(json.dumps({"meta": {"status": "verified", "inputs": {}},
                               "overall": {}, "segments": []}))

    def boom(messages, **kw):
        raise AssertionError("LLM must not be called for a verified layer")

    monkeypatch.setattr(ai_analyze.llm, "complete_thread", boom)
    with pytest.raises(ValueError, match="verified"):
        ai_analyze.analyze(corpus)


def test_analyze_passes_outline_and_text_to_model(corpus, monkeypatch):
    seen = []

    def fake_complete_thread(messages, **kw):
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
        {"overall": {"sentiment": 0.4, "quote": FULL_TEXT[0],
                     "quote_type": "grund"},
         "segments": [{"forarbete_id": "a9.9", "sentiment": 0.0,
                       "quote": FULL_TEXT[0], "quote_type": "grund"}]},
        ensure_ascii=False)
    replies = iter([bad_reply, VALID_REPLY])
    seen = []

    def fake_complete_thread(messages, **kw):
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
                        lambda messages, **kw: "not json at all")
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


# ---- snapping a reworded quote back to the answer's wording -----------------
# Measured over every quote this corpus had rejected: none was invented, all were
# 60-95% similar to a real passage. The failure is misquotation, so the repair is
# a lookup -- but only where one passage is clearly the one meant.

SNAP_TEXT = ["Kommunen avstyrker förslaget om skattebroms.",
             "Skälet är att förslaget förordar en ordning som inskränker "
             "självstyret på ett sätt som inte har utretts."]
SNAP_UNITS = ai_analyze.answer_units(SNAP_TEXT)
SNAP_HAY = ai_analyze.normalize_space("\n\n".join(SNAP_TEXT))


def test_snap_recovers_a_single_changed_word():
    # the recurring real failure: the model writes "förordrar" for "förordar"
    got = ai_analyze.snap_to_source(
        "Skälet är att förslaget förordrar en ordning som inskränker "
        "självstyret på ett sätt som inte har utretts.", SNAP_UNITS, SNAP_HAY)
    assert got == SNAP_TEXT[1]          # the answer's spelling, not the model's


def test_snap_refuses_when_nothing_is_close():
    assert ai_analyze.snap_to_source(
        "Kommunen anser att bidraget bör höjas kraftigt.", SNAP_UNITS,
        SNAP_HAY) is None


def test_snap_refuses_when_two_passages_compete():
    """The margin is what stops a confident-looking snap onto the wrong one of
    two near-identical sentences -- an answer that says nearly the same thing
    about two sections is common."""
    text = ["Kommunen avstyrker förslaget om skattebroms i avsnitt 8.1.",
            "Kommunen avstyrker förslaget om skattebroms i avsnitt 8.2."]
    assert ai_analyze.snap_to_source(
        "Kommunen avstyrker förslaget om skattebroms i avsnitt 8.",
        ai_analyze.answer_units(text),
        ai_analyze.normalize_space("\n\n".join(text))) is None


def test_validate_snaps_a_reworded_quote_into_the_layer():
    """End to end: a reworded quote no longer fails the layer, and what gets
    stored is the answer's wording."""
    haystack = ai_analyze.normalize_space("\n\n".join(SNAP_TEXT))
    out = ai_analyze._validate(json.dumps(
        {"overall": {"sentiment": -0.8, "quote_type": "grund",
                     "quote": "Skälet är att förslaget förordrar en ordning som "
                              "inskränker självstyret på ett sätt som inte har "
                              "utretts."},
         "segments": []}, ensure_ascii=False), set(), SNAP_UNITS, haystack)
    assert out["overall"]["quote"] == SNAP_TEXT[1]


def test_snap_refuses_a_span_that_is_not_contiguous_in_the_answer():
    """Joining two units with a space does not always reproduce the source: the
    unit split drops the letterless dash introducing a bulleted ground, so a
    two-unit span would reassemble text that never occurs -- exactly the splice
    the prompt forbids. Candidates are filtered against the answer first, so the
    one guarantee this pass rests on survives the repair path."""
    text = ["CKS avstyrker därför skattebromsavgift av följande skäl: – "
            "Utredningens egna data ger inte stöd för detta."]
    units = ai_analyze.answer_units(text)
    hay = ai_analyze.normalize_space("\n\n".join(text))
    assert " ".join(units) not in hay          # the dash is gone from the join
    got = ai_analyze.snap_to_source(
        "CKS avstyrker därför skattebromsavgiften av följande skäl: "
        "Utredningens egna data ger inte stöd", units, hay)
    assert got is None or ai_analyze.normalize_space(got) in hay


def test_answer_units_split_a_verdict_from_its_reason():
    """The one sub-sentence trim the old free-form quoting used well: a colon or
    dash introducing the grounds now ends a unit, so the reason is quotable
    without the verdict in front of it."""
    units = ai_analyze.answer_units(
        ["CKS avstyrker därför skattebromsavgift av följande skäl: – "
         "Utredningens egna data ger inte stöd för detta."])
    assert units == ["CKS avstyrker därför skattebromsavgift av följande skäl:",
                     "Utredningens egna data ger inte stöd för detta."]
