"""Tests for the shared LLM client (`accommodanda.lib.llm`): the validate/
self-repair-retry loop `author`, the truncation guard, and the endpoint/sampling
config (local vs remote auth, temperature/top_p on the payload). The network call
is faked -- it is the one deliberately network-bound, on-demand step."""

import json

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


def test_author_raises_after_one_failed_retry(monkeypatch, tmp_path):
    # a reply bad on both attempts must propagate the validator's ValueError -- the
    # caller must never persist an unvalidated payload
    monkeypatch.setattr(llm, "complete_thread", lambda messages, **kw: "BAD")
    monkeypatch.setattr(llm, "DEBUG_DIR", tmp_path / "llm-debug")

    def validate(reply):
        raise ValueError("always bad")

    with pytest.raises(ValueError, match="always bad"):
        llm.author("PROMPT", validate)


def test_author_dumps_the_twice_rejected_reply_for_diagnosis(monkeypatch, tmp_path):
    # the rejected reply otherwise lives only in memory: on the second failure
    # the full thread + reply is persisted so the bytes survive the raise
    monkeypatch.setattr(llm, "complete_thread", lambda messages, **kw: "BAD BYTES")
    monkeypatch.setattr(llm, "DEBUG_DIR", tmp_path / "llm-debug")
    with pytest.raises(ValueError):
        llm.author("PROMPT", lambda reply: (_ for _ in ()).throw(
            ValueError("svaret saknar en 'mappings'-lista")))
    (dump,) = sorted((tmp_path / "llm-debug").glob("rejected-*.json"))
    saved = json.loads(dump.read_text())
    assert saved["reply"] == "BAD BYTES"
    assert "mappings" in saved["error"]
    assert saved["messages"][0] == {"role": "user", "content": "PROMPT"}
    assert saved["messages"][1] == {"role": "assistant", "content": "BAD BYTES"}


def test_json_values_salvages_prefix_objects_from_extra_data():
    # gemma on prop 2021/22:136 emitted a complete {"mappings": …} object and
    # kept writing -- json.loads raises "Extra data" and the valid answer was
    # lost. The parseable prefix values must survive; trailing prose is ignored.
    two = '{"mappings": [1]}\n{"mappings": [2]}'
    assert llm.json_values(two) == [{"mappings": [1]}, {"mappings": [2]}]
    prose = '{"mappings": [1]} Här är dessutom en förklaring.'
    assert llm.json_values(prose) == [{"mappings": [1]}]
    fenced = '```json\n{"a": 1}\n```'
    assert llm.json_values(fenced) == [{"a": 1}]
    with pytest.raises(ValueError):               # no leading JSON at all
        llm.json_values("not json at all")


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

    status_code = 200
    ok = True

    def __init__(self, seen):
        self.seen = seen

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
    # runs, not with a 401 halfway through a corpus. A RuntimeError, not an
    # AssertionError: `python -O` strips an assert, and a stripped check would
    # send an empty header and reach exactly that mid-corpus 401
    # (rule:errors-drive-retry-use-raise)
    monkeypatch.delenv("BERGET_API_KEY", raising=False)
    monkeypatch.setattr(llm, "API_URL", "https://api.berget.ai/v1/chat/completions")
    monkeypatch.setattr(llm, "load_dotenv", lambda: None)   # don't read a real .env
    with pytest.raises(RuntimeError, match="BERGET_API_KEY"):
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
        status_code = 200
        ok = True

        def json(self):
            return {"choices": [{"finish_reason": "length",
                                 "message": {"content": "half an ans"}}]}

    monkeypatch.setattr(llm.requests, "post", lambda *a, **kw: FakeResp())
    with pytest.raises(ValueError, match="truncated at max_tokens"):
        llm.complete_thread([{"role": "user", "content": "hi"}])


class Fake500:
    status_code = 500
    ok = False
    reason = "Server Error"
    url = "https://api.example/v1/chat/completions"
    headers = {}
    text = '{"error": "upstream overloaded"}'


def test_http_error_carries_the_response_body(monkeypatch):
    # the endpoint's own diagnosis lives in the body; requests' bare
    # raise_for_status reports only "400 Client Error: Bad Request for url: ..."
    # and throws it away, leaving a too-long prompt indistinguishable from a bad
    # key. this is the llama.cpp over-context reply that prompted the fix
    class Fake400:
        status_code = 400
        ok = False
        reason = "Bad Request"
        url = "http://127.0.0.1:8123/v1/chat/completions"
        headers = {"Content-Type": "application/json"}
        text = ('{"error":{"code":400,"message":"request (98435 tokens) exceeds '
                'the available context size (65536 tokens), try increasing it",'
                '"type":"exceed_context_size_error"}}')

    monkeypatch.setenv("BERGET_API_KEY", "x")
    monkeypatch.setattr(llm.requests, "post", lambda *a, **kw: Fake400())
    with pytest.raises(llm.requests.exceptions.HTTPError) as exc:
        llm.complete_thread([{"role": "user", "content": "hi"}])
    assert "exceeds the available context size" in str(exc.value)
    # the response's own url, not the module-level API_URL -- the message must
    # point at the endpoint that actually answered
    assert "127.0.0.1:8123" in str(exc.value)
    assert "exceed_context_size_error" in str(exc.value)
    assert "400 Bad Request" in str(exc.value)


def test_http_error_body_is_truncated(monkeypatch):
    # a gateway in front of the endpoint can answer with a whole HTML page --
    # quoted in full it swamps the traceback the message exists to clarify
    class FakeHTML:
        status_code = 502
        ok = False
        reason = "Bad Gateway"
        url = "https://gateway.example/v1/chat/completions"
        headers = {"Retry-After": "120", "CF-Ray": "deadbeef"}
        text = "<html>" + "x" * 9000 + "</html>"

    monkeypatch.setenv("BERGET_API_KEY", "x")
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    monkeypatch.setattr(llm.requests, "post", lambda *a, **kw: FakeHTML())
    with pytest.raises(llm.requests.exceptions.HTTPError) as exc:
        llm.complete_thread([{"role": "user", "content": "hi"}])
    assert len(str(exc.value)) < 2200
    assert "more chars]" in str(exc.value)
    # the diagnostic headers ride along: a throttle states itself in Retry-After,
    # not in the body, and that is exactly the case a bare 502 hides
    assert "Retry-After: 120" in str(exc.value)
    assert "CF-Ray: deadbeef" in str(exc.value)


def test_transient_5xx_is_retried_then_succeeds(monkeypatch):
    # one momentary 500 from a hosted endpoint must not kill a corpus run
    monkeypatch.setenv("BERGET_API_KEY", "x")
    replies = [Fake500(), FakeOK({})]
    calls = []
    monkeypatch.setattr(llm.time, "sleep", calls.append)
    monkeypatch.setattr(llm.requests, "post",
                        lambda *a, **kw: replies.pop(0))
    assert llm.complete_thread([{"role": "user", "content": "hi"}]) == "R"
    assert calls == [10]                      # backed off once


def test_persistent_5xx_raises_after_bounded_retries(monkeypatch):
    # bounded: three attempts, then the error propagates -- never an
    # unbounded hammer against a down endpoint
    monkeypatch.setenv("BERGET_API_KEY", "x")
    n = []
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    monkeypatch.setattr(llm.requests, "post",
                        lambda *a, **kw: n.append(1) or Fake500())
    with pytest.raises(llm.requests.exceptions.HTTPError):
        llm.complete_thread([{"role": "user", "content": "hi"}])
    assert len(n) == 3
