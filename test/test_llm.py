"""Tests for the shared LLM client (`accommodanda.lib.llm`): the validate/
self-repair-retry loop `author` and the truncation guard. The network call is
faked -- it is the one deliberately network-bound, on-demand step."""

import pytest

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
