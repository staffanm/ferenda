"""The per-layer provenance record (lib.llm `start_record`/`record`/`rearm`) and
the `meta.run` stamp it produces (lib.annstore.write): a layer says which host
and model authored it, under what sampling, at what token cost, and from which
prompts."""

import json

import pytest

from ferenda.lib import annstore, layout, llm, stage
from ferenda.lib.stage import RunOptions
from ferenda.remisser import ai_analyze as remisser_analyze
from ferenda.remisser import source as remisser_source


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(layout, "ARTIFACT", tmp_path / "artifact")
    monkeypatch.setattr(annstore, "ROOT", tmp_path / "ann")
    monkeypatch.setattr(llm, "_RECORD", None)
    monkeypatch.setattr(llm, "API_URL", "https://api.berget.ai/v1/chat/completions")
    monkeypatch.setattr(llm, "TEMPERATURE", 0.0)
    monkeypatch.setattr(llm, "TOP_P", None)
    return tmp_path


def _call(messages=None, model="moonshotai/Kimi-K2.6", pt=100, ct=20):
    llm._observe(messages or [{"role": "user", "content": "hi"}], model,
                 {"prompt_tokens": pt, "completion_tokens": ct})


def test_no_window_records_nothing(store):
    _call()
    assert llm.record() is None


def test_an_armed_but_unused_window_records_nothing(store):
    """A mechanically derived layer arms nothing and must not claim a model run."""
    llm.start_record()
    assert llm.record() is None


def test_a_window_sums_its_calls(store):
    llm.start_record()
    _call(pt=100, ct=20)
    _call(pt=250, ct=30)
    rec = llm.record()
    assert rec["calls"] == 2
    assert rec["prompt_tokens"] == 350 and rec["completion_tokens"] == 50
    assert rec["host"] == "api.berget.ai", "the host, never the full base URL"
    assert rec["model"] == "moonshotai/Kimi-K2.6"
    assert rec["temperature"] == 0.0
    assert "top_p" not in rec, "an unset knob is left out, not recorded as null"


def test_the_prompt_hash_follows_the_prompt(store):
    llm.start_record()
    _call([{"role": "user", "content": "locate the map"}])
    first = llm.record()["prompt_sha"]
    llm.start_record()
    _call([{"role": "user", "content": "locate the map"}])
    assert llm.record()["prompt_sha"] == first, "same prompt, same hash"
    llm.start_record()
    _call([{"role": "user", "content": "locate the table"}])
    assert llm.record()["prompt_sha"] != first


def test_the_prompt_hash_covers_the_images(store):
    """A vision call's images are as much the prompt as its text: swap the page
    and the answer changes, so the hash must move."""
    def call(png):
        llm.start_record()
        _call([{"role": "user", "content": [{"type": "text", "text": "locate"},
                                            {"type": "image_url",
                                             "image_url": {"url": png}}]}])
        return llm.record()["prompt_sha"]
    assert call("data:image/png;base64,AAAA") != call("data:image/png;base64,BBBB")


INPUTS = {"artifact:sfs/2006:171": "abc"}


def test_write_stamps_the_run(store):
    llm.start_record()
    _call()
    p = annstore.write(annstore.path("sfs", "2006:171", ".graphics"),
                       {"g-1": {"page": 2}}, INPUTS, model="moonshotai/Kimi-K2.6")
    run = json.loads(p.read_text())["meta"]["run"]
    assert run["calls"] == 1 and run["prompt_tokens"] == 100
    assert run["host"] == "api.berget.ai"
    assert run["started"] <= run["ended"]


def test_a_derived_layer_carries_no_run(store):
    p = annstore.write(annstore.path("sfs", "2007:90", ".graphics"),
                       {"g-1": {"page": 1}}, INPUTS, model="roadsign",
                       status=annstore.DERIVED)
    assert "run" not in json.loads(p.read_text())["meta"]


def test_each_layer_records_only_its_own_calls(store):
    """`ai-includegraphics a b` authors two layers in one process; the second
    must not inherit the first's tokens (lib.llm.rearm)."""
    llm.start_record()
    _call(pt=100, ct=20)
    first = annstore.write(annstore.path("sfs", "2001:1", ".graphics"),
                           {"g": {"page": 1}}, INPUTS)
    _call(pt=7, ct=3)
    second = annstore.write(annstore.path("sfs", "2002:2", ".graphics"),
                            {"g": {"page": 1}}, INPUTS)
    assert json.loads(first.read_text())["meta"]["run"]["prompt_tokens"] == 100
    assert json.loads(second.read_text())["meta"]["run"]["prompt_tokens"] == 7


def test_the_running_usage_tally_is_untouched_by_a_write(store):
    """forarbete ai-genomforande prints llm.USAGE *after* its write."""
    before = dict(llm.USAGE)
    llm.start_record()
    _call(pt=100, ct=20)
    annstore.write(annstore.path("sfs", "2003:3", ".graphics"),
                   {"g": {"page": 1}}, INPUTS)
    assert llm.USAGE == before, "write must not reset the process-wide tally"


class _Resp:
    """The one endpoint reply shape `complete_thread` reads."""
    status_code = 200

    def __init__(self, usage):
        self._usage = usage

    def json(self):
        return {"choices": [{"message": {"content": "{}"},
                             "finish_reason": "stop"}],
                "usage": self._usage}


def test_a_real_completion_feeds_the_window(store, monkeypatch):
    """The link the unit tests above stub out: `complete_thread` is the single
    HTTP call site, so folding the reply's `usage` in there is what makes every
    ai- pass recorded without each one remembering to."""
    monkeypatch.setattr(llm.requests, "post",
                        lambda *a, **kw: _Resp({"prompt_tokens": 4096,
                                                "completion_tokens": 311}))
    monkeypatch.setattr(llm, "auth_headers", lambda url: {})
    llm.start_record()
    llm.complete_thread([{"role": "user", "content": "locate the map"}],
                        model="moonshotai/Kimi-K2.6")
    rec = llm.record()
    assert rec["calls"] == 1
    assert rec["prompt_tokens"] == 4096 and rec["completion_tokens"] == 311
    assert rec["model"] == "moonshotai/Kimi-K2.6"
    assert len(rec["prompt_sha"]) == 64


def test_a_document_that_writes_no_layer_does_not_taint_the_next(
        store, monkeypatch):
    """The window must be opened per document, not merely re-opened by a
    successful write -- and this drives the real `remisser ai-analyze` loop,
    because the defect was where `start_record` sat, not what it does.

    An answer the model twice fails to answer usably raises `Unanalyzable`,
    writes no layer and is skipped. Its spent calls must not be stamped onto
    the next answer's layer, which is committed data claiming what produced it.
    """
    written = {}

    def fake_analyze(basefile, force=False):
        if basefile == "doomed":
            _call(pt=900, ct=90)            # spent, then abandoned
            raise remisser_analyze.Unanalyzable(basefile)
        _call(pt=7, ct=3)
        written[basefile] = annstore.write(
            annstore.path("sfs", "2020:1", ".graphics"), {"g": {"page": 1}},
            INPUTS)
        return written[basefile]

    monkeypatch.setattr(remisser_analyze, "analyze", fake_analyze)
    monkeypatch.setattr(remisser_analyze, "is_arende", lambda arg: False)
    monkeypatch.setattr(stage, "RUN", RunOptions())
    # the command exits 1 because one answer failed; the other still wrote
    with pytest.raises(SystemExit):
        remisser_source.remisser_ai_analyze(["doomed", "good"])

    run = json.loads(written["good"].read_text())["meta"]["run"]
    assert run["calls"] == 1 and run["prompt_tokens"] == 7, \
        "the abandoned answer's calls leaked into the next layer"
