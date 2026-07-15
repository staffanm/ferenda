"""Tests for the shared LLM client (`accommodanda.lib.llm`): the validate/
self-repair-retry loop `author`, the truncation guard, and the endpoint/sampling
config (local vs remote auth, temperature/top_p on the payload). The network call
is faked -- it is the one deliberately network-bound, on-demand step."""

import pytest

from accommodanda import config
from accommodanda.lib import llm


def test_author_returns_validator_result_on_first_success(monkeypatch):
    monkeypatch.setattr(llm, "complete_thread", lambda messages, **kw: "GOOD")
    assert llm.author("P", lambda reply: reply.lower()) == "good"


def test_author_retries_as_a_real_follow_up_turn(monkeypatch):
    # the retry is a genuine follow-up thread: the original user prompt, the
    # model's own rejected reply replayed as an assistant turn, then a short user
    # turn naming the failure -- not the same ever-growing single user message
    replies = iter(["BAD", "OK"])
    seen = []

    def fake_complete_thread(messages, **kw):
        seen.append([dict(m) for m in messages])
        return next(replies)

    def validate(reply):
        if reply != "OK":
            raise ValueError("reply was %r" % reply)
        return reply

    monkeypatch.setattr(llm, "complete_thread", fake_complete_thread)
    assert llm.author("PROMPT", validate) == "OK"
    assert len(seen) == 2
    assert seen[0] == [{"role": "user", "content": "PROMPT"}]
    assert seen[1][0] == {"role": "user", "content": "PROMPT"}
    assert seen[1][1] == {"role": "assistant", "content": "BAD"}   # own reply replayed
    assert seen[1][2]["role"] == "user"
    assert "UNDERKÄNDES" in seen[1][2]["content"]
    assert "reply was 'BAD'" in seen[1][2]["content"]              # failure fed back


def test_author_raises_after_one_failed_retry(monkeypatch):
    # a reply bad on both attempts must propagate the validator's ValueError -- the
    # caller must never persist an unvalidated payload
    monkeypatch.setattr(llm, "complete_thread", lambda messages, **kw: "BAD")

    def validate(reply):
        raise ValueError("always bad")

    with pytest.raises(ValueError, match="always bad"):
        llm.author("PROMPT", validate)


def test_author_stops_after_two_calls(monkeypatch):
    # exactly two model calls -- one initial + one retry, never a third
    calls = []
    monkeypatch.setattr(llm, "complete_thread",
                        lambda messages, **kw: calls.append(1) or "BAD")

    def validate(reply):
        raise ValueError("nope")

    with pytest.raises(ValueError):
        llm.author("PROMPT", validate)
    assert len(calls) == 2


def test_author_forwards_max_tokens(monkeypatch):
    seen = {}
    monkeypatch.setattr(llm, "complete_thread",
                        lambda messages, **kw: seen.update(kw) or "R")
    llm.author("P", lambda reply: reply, max_tokens=12345)
    assert seen["max_tokens"] == 12345


class FakeOK:
    """A well-formed chat-completions reply, capturing what was posted."""

    def __init__(self, seen):
        self.seen = seen

    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"finish_reason": "stop", "message": {"content": "R"}}]}


def _capture(monkeypatch):
    seen = {}
    monkeypatch.setattr(llm.requests, "post",
                        lambda url, **kw: seen.update(url=url, **kw) or FakeOK(seen))
    return seen


def test_local_endpoint_needs_no_api_key(monkeypatch):
    # a llama.cpp server takes no key -- requiring one would be a fabricated
    # precondition that makes the local endpoint unusable (docs/local-llm.md)
    monkeypatch.delenv("BERGET_API_KEY", raising=False)
    monkeypatch.setattr(llm, "API_URL", "http://127.0.0.1:8123/v1/chat/completions")
    seen = _capture(monkeypatch)
    assert llm.complete_thread([{"role": "user", "content": "hi"}]) == "R"
    assert seen["headers"] == {}                     # no Authorization sent


def test_remote_endpoint_still_demands_an_api_key(monkeypatch):
    # against Berget a missing key is a real misconfiguration: fail before the pass
    # runs, not with a 401 halfway through a corpus
    monkeypatch.delenv("BERGET_API_KEY", raising=False)
    monkeypatch.setattr(llm, "API_URL", "https://api.berget.ai/v1/chat/completions")
    monkeypatch.setattr(llm, "load_dotenv", lambda: None)   # don't read a real .env
    with pytest.raises(AssertionError, match="BERGET_API_KEY"):
        llm.complete_thread([{"role": "user", "content": "hi"}])


def test_remote_endpoint_sends_the_bearer_token(monkeypatch):
    monkeypatch.setenv("BERGET_API_KEY", "secret")
    monkeypatch.setattr(llm, "API_URL", "https://api.berget.ai/v1/chat/completions")
    seen = _capture(monkeypatch)
    llm.complete_thread([{"role": "user", "content": "hi"}])
    assert seen["headers"] == {"Authorization": "Bearer secret"}


def test_payload_carries_configured_temperature_and_top_p(monkeypatch):
    monkeypatch.setattr(llm, "API_URL", "http://127.0.0.1:8123/v1/chat/completions")
    monkeypatch.setattr(llm, "TEMPERATURE", 1.0)
    monkeypatch.setattr(llm, "TOP_P", 0.95)
    seen = _capture(monkeypatch)
    llm.complete_thread([{"role": "user", "content": "hi"}])
    assert seen["json"]["temperature"] == 1.0
    assert seen["json"]["top_p"] == 0.95


def test_top_p_is_omitted_when_unset(monkeypatch):
    # the default must leave Berget's existing passes byte-identical: temperature 0
    # and no top_p key at all, so the endpoint's own default applies
    monkeypatch.setattr(llm, "API_URL", "http://127.0.0.1:8123/v1/chat/completions")
    monkeypatch.setattr(llm, "TEMPERATURE", 0)
    monkeypatch.setattr(llm, "TOP_P", None)
    seen = _capture(monkeypatch)
    llm.complete_thread([{"role": "user", "content": "hi"}])
    assert seen["json"]["temperature"] == 0
    assert "top_p" not in seen["json"]


def test_base_url_defaults_to_berget():
    assert config.resolve_llm_base_url({}) == "https://api.berget.ai/v1"


def test_base_url_env_override_wins_and_drops_a_trailing_slash(monkeypatch):
    # a trailing slash would build .../v1//chat/completions
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:8123/v1/")
    assert config.resolve_llm_base_url({}) == "http://127.0.0.1:8123/v1"


def test_sampling_defaults(monkeypatch):
    monkeypatch.delenv("LLM_TEMPERATURE", raising=False)
    monkeypatch.delenv("LLM_TOP_P", raising=False)
    assert config.resolve_llm_temperature({}) == 0
    assert config.resolve_llm_top_p({}) is None


def test_sampling_env_overrides(monkeypatch):
    monkeypatch.setenv("LLM_TEMPERATURE", "1.0")
    monkeypatch.setenv("LLM_TOP_P", "0.95")
    assert config.resolve_llm_temperature({}) == 1.0
    assert config.resolve_llm_top_p({}) == 0.95


@pytest.mark.parametrize("value", ["3.0", "-1", "hot"])
def test_out_of_range_temperature_raises(monkeypatch, value):
    # raise rather than clamp: a silently corrected knob changes every reply
    monkeypatch.setenv("LLM_TEMPERATURE", value)
    with pytest.raises(config.ConfigError, match="LLM_TEMPERATURE"):
        config.resolve_llm_temperature({})


def test_complete_thread_raises_on_length_truncation(monkeypatch, tmp_path):
    # a `length` finish means the reply is truncated; it must raise (not assert,
    # which -O strips) so `author` retries / a too-small budget surfaces
    monkeypatch.setenv("BERGET_API_KEY", "x")

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"finish_reason": "length",
                                 "message": {"content": "half an ans"}}]}

    monkeypatch.setattr(llm.requests, "post", lambda *a, **kw: FakeResp())
    with pytest.raises(ValueError, match="truncated at max_tokens"):
        llm.complete_thread([{"role": "user", "content": "hi"}])
