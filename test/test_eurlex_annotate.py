"""Tests for `lagen eurlex ai-annotate`: the act->markdown rendering and the
response validation. The LLM call itself is not exercised (it is the one
deliberately network-bound, on-demand step)."""

import json

import pytest

from accommodanda.eurlex import annotate
from accommodanda.lib import annstore, compress, layout, llm

ART = {
    "celex": "32099R0001", "title": "Testförordning", "lang": "swe",
    "structure": [
        {"type": "citation", "text": ["med beaktande av fördraget"]},
        {"type": "recital", "num": "1", "text": ["Bakgrund."]},
        {"type": "article", "id": "4", "num": "4", "text": ["Artikel 4 – Skyldigheter"]},
        {"type": "paragraph", "num": "1", "text": ["Datahållaren ska dela data."]},
        {"type": "point", "num": "a", "text": ["på ett säkert sätt."]},
    ],
}


def _act(*blocks, lang="swe"):
    return {**ART, "lang": lang, "structure": list(blocks)}


ARTICLE = {"type": "article", "id": "4", "num": "4",
           "text": ["Artikel 4 – Skyldigheter"]}
BODY = {"type": "paragraph", "num": "1", "text": ["Datahållaren ska dela data."]}


def test_act_markdown_preserves_numbering():
    md = annotate.act_markdown(ART)
    assert "# Testförordning" in md
    assert "(1) Bakgrund." in md                 # numbered recital
    assert "## Artikel 4 – Skyldigheter" in md    # article heading, no doubled num
    assert "1. Datahållaren ska dela data." in md  # numbered paragraph
    assert "(a) på ett säkert sätt." in md        # lettered point


def test_act_markdown_drops_the_trailing_annex():
    # annexes contribute nothing to the recital groups / article<->recital layer
    # but dominate the prompt (83% of CLP is annex VI's classification table), so
    # the tail from the annex heading on is left out of the *prompt* only
    md = annotate.act_markdown(_act(
        ARTICLE, BODY,
        {"type": "heading", "level": 1, "text": ["BILAGA I"]},
        {"type": "row", "text": ["H200 | Instabilt explosivt | GHS01"]},
        {"type": "paragraph", "text": ["Kriterier för klassificering."]}))
    assert "Artikel 4" in md and "Datahållaren ska dela data." in md
    assert "BILAGA" not in md
    assert "H200" not in md
    assert "Kriterier" not in md


def test_act_markdown_drops_annex_by_parse_anchor_alone():
    # the anchor `eurlex.parse.append_annex` mints is the explicit, language-neutral
    # signal: an annex whose title reads nothing like "BILAGA" still goes
    md = annotate.act_markdown(_act(
        ARTICLE, BODY,
        {"type": "heading", "level": 1, "id": "bilaga-3",
         "text": ["Förteckning över de ämnen som avses i artikel 4"]},
        {"type": "row", "text": ["Bly | 7439-92-1"]}))
    assert "Artikel 4" in md
    assert "Förteckning" not in md and "7439-92-1" not in md


def test_act_markdown_keeps_an_annex_heading_before_the_last_article():
    # a table of contents (REACH) and an amending act quoting a replacement annex
    # mid-body (2001/18) both put an annex heading *before* enacting terms. Only a
    # heading after the last article may cut, so no article can ever be dropped.
    md = annotate.act_markdown(_act(
        {"type": "heading", "level": 1, "text": ["BILAGA I ALLMÄNNA BESTÄMMELSER"]},
        {"type": "paragraph", "text": ["Bilaga I ska ersättas med följande."]},
        ARTICLE, BODY,
        {"type": "heading", "level": 1, "text": ["BILAGA II"]},
        {"type": "row", "text": ["ballast | ballast"]}))
    assert "Artikel 4" in md and "Datahållaren ska dela data." in md
    assert "BILAGA I ALLMÄNNA BESTÄMMELSER" in md      # kept: it precedes the article
    assert "ballast" not in md                         # dropped: the real annex


def test_act_markdown_annex_words_are_language_aware():
    # the corpus holds Swedish and English manifestations; the annex vocabulary
    # comes from `lang.VOCAB[art["lang"]]`, never from one hardcoded list
    eng = annotate.act_markdown(_act(
        {"type": "article", "id": "4", "num": "4", "text": ["Article 4"]},
        {"type": "heading", "level": 1, "text": ["ANNEX I"]},
        {"type": "row", "text": ["ballast"]},
        {"type": "heading", "level": 1, "text": ["APPENDIX"]}, lang="eng"))
    assert "Article 4" in eng
    assert "ANNEX" not in eng and "ballast" not in eng
    # "BILAGA" is not English vocabulary -- an English act is not cut by it
    assert "BILAGA" in annotate.act_markdown(_act(
        {"type": "article", "id": "4", "num": "4", "text": ["Article 4"]},
        {"type": "heading", "level": 1, "text": ["BILAGA I"]}, lang="eng"))


def test_act_markdown_never_trims_an_act_without_articles():
    # an annex-only manifestation has no enacting terms to protect *and* nothing
    # to cross-reference; leave it whole rather than guess where its body starts
    md = annotate.act_markdown(_act(
        {"type": "heading", "level": 1, "text": ["BILAGA"]},
        {"type": "paragraph", "text": ["Hela texten."]}))
    assert "BILAGA" in md and "Hela texten." in md


def test_act_markdown_leaves_an_annexless_act_untouched():
    # an act with no annex (GDPR) must come through whole -- every block, and the
    # trailing signature/footnote matter that follows the last article
    act = _act(ARTICLE, BODY,
               {"type": "heading", "level": 2, "text": ["KAPITEL XI"]},
               {"type": "signature", "text": ["Utfärdad i Bryssel den 27 april 2016."]},
               {"type": "note", "num": "1", "text": ["EUT L 119, 4.5.2016, s. 1."]})
    md = annotate.act_markdown(act)
    for block in act["structure"]:
        assert block["text"][0] in md


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


def test_validate_rejects_group_without_two_integer_range():
    # the renderer unpacks `lo, hi = g["range"]` and ranges over it, so a group
    # lacking a two-integer range would crash every later generate -- reject it at
    # write time (fed back on retry), never write it to the .ann
    with pytest.raises(ValueError, match="two-integer range"):
        annotate._validate(json.dumps(
            {"recitalGroups": [{"id": "a", "label": "x", "articleRefs": []}],
             "articleToRecitals": {}}))
    with pytest.raises(ValueError, match="two-integer range"):
        annotate._validate(json.dumps(
            {"recitalGroups": [{"range": [1, 2, 3]}], "articleToRecitals": {}}))
    with pytest.raises(ValueError, match="two-integer range"):
        annotate._validate(json.dumps(
            {"recitalGroups": [{"range": ["1", "2"]}], "articleToRecitals": {}}))


def test_validate_rejects_inverted_range():
    with pytest.raises(ValueError, match="inverted"):
        annotate._validate(json.dumps(
            {"recitalGroups": [{"range": [5, 2]}], "articleToRecitals": {}}))


def test_validate_rejects_non_integer_article_recitals():
    # articleToRecitals values are recital numbers the renderer iterates; a string
    # or a bool there crashes generate, so it must be rejected here
    with pytest.raises(ValueError, match="non-integer recital"):
        annotate._validate(json.dumps(
            {"recitalGroups": [], "articleToRecitals": {"4": ["1", "2"]}}))
    with pytest.raises(ValueError, match="non-empty list"):
        annotate._validate(json.dumps(
            {"recitalGroups": [], "articleToRecitals": {"4": []}}))


def test_author_retries_once_then_succeeds(monkeypatch):
    # first reply over-sections (rejected), second reply is within the cap; the
    # retry loop now lives in lib.llm.author, driven by eurlex's own validator
    replies = iter([_layer_with_groups(annotate.MAX_RECITAL_GROUPS + 5),
                    _layer_with_groups(3)])
    seen = []

    def fake_complete_thread(messages, **kw):
        seen.append(list(messages))
        return next(replies)

    monkeypatch.setattr(llm, "complete_thread", fake_complete_thread)
    layer = llm.author("BASE PROMPT", annotate._validate)
    assert len(layer["recitalGroups"]) == 3
    assert len(seen) == 2                                  # one retry
    # the retry turn replays the rejected reply as an assistant message + a user
    # message naming the failure
    assert seen[1][0] == {"role": "user", "content": "BASE PROMPT"}
    assert seen[1][1]["role"] == "assistant"
    assert "UNDERKÄNDES" in seen[1][2]["content"]
    assert "too many recital groups" in seen[1][2]["content"]


def test_author_raises_after_one_failed_retry(monkeypatch):
    monkeypatch.setattr(llm, "complete_thread",
                        lambda messages, **kw: _layer_with_groups(99))
    with pytest.raises(ValueError, match="too many recital groups"):
        llm.author("BASE PROMPT", annotate._validate)


def test_annotate_rejects_non_sector3(monkeypatch):
    # a judgment (sector 6) is out of scope; fails before any network call
    with pytest.raises(AssertionError):
        annotate.annotate("62019CJ0311")


def test_annotate_never_writes_a_bad_ann(tmp_path, monkeypatch):
    # a malformed-but-JSON reply that survives both attempts must raise, and no
    # `.ann` (nor a truncated `.ann.tmp`) may be left behind for generate to trip
    # on -- the write goes through util.write_atomic *after* validation succeeds
    monkeypatch.setattr(layout, "ARTIFACT", tmp_path / "artifact")
    monkeypatch.setattr(annstore, "ROOT", tmp_path / "ann")
    celex = "32099R0001"
    art_path = layout.artifact("eurlex", celex)
    art_path.parent.mkdir(parents=True, exist_ok=True)
    compress.write_bytes(art_path, json.dumps(ART).encode("utf-8"))
    monkeypatch.setattr(llm, "complete_thread",
                        lambda messages, **kw: _layer_with_groups(99))
    with pytest.raises(ValueError, match="too many recital groups"):
        annotate.annotate(celex)
    ann = annstore.path("eurlex", celex)
    assert not ann.exists()
    assert not ann.with_suffix(".ann.tmp").exists()


def test_annotate_writes_generated_envelope(tmp_path, monkeypatch):
    # a fresh layer lands in the curated store (not the artifact tree) as a
    # `generated` envelope: meta beside the payload, input hash recorded
    monkeypatch.setattr(layout, "ARTIFACT", tmp_path / "artifact")
    monkeypatch.setattr(annstore, "ROOT", tmp_path / "ann")
    celex = "32099R0001"
    art_path = layout.artifact("eurlex", celex)
    art_path.parent.mkdir(parents=True, exist_ok=True)
    compress.write_bytes(art_path, json.dumps(ART).encode("utf-8"))
    monkeypatch.setattr(llm, "complete_thread",
                        lambda messages, **kw: _layer_with_groups(2))
    out = annotate.annotate(celex)
    assert out == annstore.path("eurlex", celex)
    env = json.loads(out.read_text())
    assert env["meta"]["status"] == "generated"
    assert list(env["meta"]["inputs"]) == ["artifact:eurlex/32099R0001"]
    assert len(env["editorialLayer"]["recitalGroups"]) == 2
    assert annstore.drifted(env["meta"]["inputs"]) == []   # authored against current


def test_annotate_refuses_to_overwrite_verified(tmp_path, monkeypatch):
    # once a human flips meta.status to verified, regeneration must refuse --
    # BEFORE any LLM call is attempted -- unless force
    monkeypatch.setattr(layout, "ARTIFACT", tmp_path / "artifact")
    monkeypatch.setattr(annstore, "ROOT", tmp_path / "ann")
    celex = "32099R0001"
    art_path = layout.artifact("eurlex", celex)
    art_path.parent.mkdir(parents=True, exist_ok=True)
    compress.write_bytes(art_path, json.dumps(ART).encode("utf-8"))
    ann = annstore.path("eurlex", celex)
    ann.parent.mkdir(parents=True, exist_ok=True)
    ann.write_text(json.dumps({"meta": {"status": "verified", "inputs": {}},
                               "editorialLayer": {"recitalGroups": [],
                                                  "articleToRecitals": {}}}))

    def boom(messages, **kw):
        raise AssertionError("LLM must not be called for a verified layer")

    monkeypatch.setattr(llm, "complete_thread", boom)
    with pytest.raises(ValueError, match="verified"):
        annotate.annotate(celex)
    # --force regenerates: the curation is consciously discarded
    monkeypatch.setattr(llm, "complete_thread",
                        lambda messages, **kw: _layer_with_groups(1))
    out = annotate.annotate(celex, force=True)
    assert json.loads(out.read_text())["meta"]["status"] == "generated"
