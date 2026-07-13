"""Shared client for the Berget OpenAI-compatible chat-completions endpoint used
by the opt-in LLM passes (eurlex ai-annotate, wiki ai-annotate, remisser
ai-analyze). Besides the raw `complete`/`complete_thread` calls it owns `author`
-- the validate/self-repair-retry loop every ai-* pass runs, taking a
source-supplied validator as data so this stays source-agnostic. The LLM is
called only from those explicit ai-* actions on named ids -- never from a
corpus-wide parse/relate/generate."""

import base64
import os

import requests
from dotenv import load_dotenv

from .. import config

API_URL = "https://api.berget.ai/v1/chat/completions"
DEFAULT_MODEL = config.LLM_MODEL   # config.yml `llm_model` / $BERGET_MODEL override
TIMEOUT = 600          # the inputs are large and the model reasons over the whole


def vision_content(text, images):
    """A multimodal user-message `content` list -- the prompt text followed by
    each PNG (raw bytes) as an inline base64 ``image_url`` data URI, the shape
    the OpenAI-compatible endpoint takes for vision. `complete`/`author` build
    this when passed `images`; the SFS-specific prompt stays in the caller
    (rule:second-use-goes-to-lib -- this is the source-agnostic transport)."""
    return [{"type": "text", "text": text},
            *({"type": "image_url", "image_url": {"url":
               "data:image/png;base64," + base64.b64encode(b).decode()}}
              for b in images)]


def strip_fence(content):
    """Peel a ``` code fence if the model wrapped its JSON in one despite being
    told to emit bare JSON, so the payload parses."""
    s = content.strip()
    if s.startswith("```"):
        s = s[s.find("\n") + 1:]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()


def complete_thread(messages, model=DEFAULT_MODEL, timeout=TIMEOUT, max_tokens=None):
    """The model's reply (temperature 0) to a full `messages` thread
    (`[{"role": "user"|"assistant", "content": ...}, ...]`), code-fence stripped.
    Reads BERGET_API_KEY from the environment/.env. `max_tokens` caps the
    completion -- raise it for a reasoning model (gpt-oss) on a large input,
    whose chain-of-thought otherwise exhausts the endpoint's small default before
    it emits the answer (a `length` finish leaves the reply truncated).

    Use this (over `complete`) for a self-repair retry: replaying the model's own
    prior reply as a real `assistant` turn, followed by a short `user` turn naming
    what failed, lets it target the fix directly -- rather than re-deriving the
    whole answer from a single ever-growing user message with the correction
    tacked onto the end, which forces it to redo the entire task from scratch and
    often reproduces the same mistake."""
    load_dotenv()
    api_key = os.environ.get("BERGET_API_KEY")
    assert api_key, "BERGET_API_KEY is not set (add it to .env)"
    payload = {"model": model, "temperature": 0, "messages": messages}
    if max_tokens:
        payload["max_tokens"] = max_tokens
    resp = requests.post(
        API_URL, headers={"Authorization": "Bearer %s" % api_key},
        json=payload, timeout=timeout)
    resp.raise_for_status()
    choice = resp.json()["choices"][0]
    # a `length` finish means the model ran out of budget mid-answer -- the reply
    # is truncated and unparseable. raise (not assert, which -O strips): this is
    # load-bearing, driving the `author` retry loop / surfacing a too-small budget
    # rather than silently returning a half-answer (rule:errors-drive-retry-use-raise).
    if choice.get("finish_reason") == "length":
        raise ValueError("model reply truncated at max_tokens -- raise max_tokens")
    return strip_fence(choice["message"]["content"])


def complete(prompt, model=DEFAULT_MODEL, timeout=TIMEOUT, max_tokens=None,
             images=()):
    """The model's reply to a single user prompt -- see `complete_thread`. Pass
    `images` (PNG bytes) for a vision model: the prompt becomes a multimodal
    text+image message (`vision_content`); `model` must then be a vision model."""
    content = vision_content(prompt, images) if images else prompt
    return complete_thread([{"role": "user", "content": content}],
                           model=model, timeout=timeout, max_tokens=max_tokens)


# the retry turn: the model's own rejected reply is replayed as an `assistant`
# message and this short `user` message names exactly what failed, so it can fix
# the one broken part rather than re-deriving the whole answer (rule below)
RETRY_MESSAGE = (
    "DITT SVAR UNDERKÄNDES: %s\n"
    "Rätta ENDAST detta i ditt svar och följ alla regler i den ursprungliga "
    "instruktionen exakt. Svara med hela den korrigerade JSON-strukturen igen.")


def author(prompt, validate, model=DEFAULT_MODEL, timeout=TIMEOUT,
           max_tokens=None, images=()):
    """Call the model with `prompt`, feed the reply through `validate` and return
    its result; on a malformed reply retry *once* as a real follow-up turn -- the
    model's own prior reply replayed as an `assistant` message, then a short
    `user` message naming exactly what failed. This lets the model correct the one
    broken part directly, rather than re-deriving the whole answer from an
    ever-growing single user message with the correction tacked on (which forces a
    from-scratch redo and often reproduces the same mistake). The calls are
    temperature 0, so a bare re-prompt would just repeat the rejected answer.

    `validate(reply)` is a source-supplied callable that parses and shape-checks
    the reply and returns the payload to write, or raises `ValueError` naming the
    fault -- the raise is load-bearing (it drives this retry loop and, on the
    second failure, propagates so a bad reply is never written), so validators
    must `raise ValueError`, not `assert` (rule:errors-drive-retry-use-raise; -O
    would strip the assert and the check would silently pass). Raises if the
    second reply is still bad.

    Pass `images` (PNG bytes) to run a vision model: the first user turn becomes
    a multimodal text+image message. The retry turn stays text-only -- the
    correction names what the shape check rejected, which needs no re-sending of
    the (large) images."""
    content = vision_content(prompt, images) if images else prompt
    messages = [{"role": "user", "content": content}]
    for attempt in range(2):
        reply = complete_thread(messages, model=model, timeout=timeout,
                                max_tokens=max_tokens)
        try:
            return validate(reply)
        except ValueError as exc:
            if attempt:
                raise
            messages.append({"role": "assistant", "content": reply})
            messages.append({"role": "user", "content": RETRY_MESSAGE % exc})
