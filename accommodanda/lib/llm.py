"""Shared client for the Berget OpenAI-compatible chat-completions endpoint used
by the opt-in LLM passes (eurlex ai-annotate, sfs ai-correspond). The LLM is
called only from those explicit ai-* actions on named ids -- never from a
corpus-wide parse/relate/generate."""

import os

import requests
from dotenv import load_dotenv

from .. import config

API_URL = "https://api.berget.ai/v1/chat/completions"
DEFAULT_MODEL = config.LLM_MODEL   # config.yml `llm_model` / $BERGET_MODEL override
TIMEOUT = 600          # the inputs are large and the model reasons over the whole


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
    assert choice.get("finish_reason") != "length", \
        "model reply truncated at max_tokens -- raise max_tokens"
    return strip_fence(choice["message"]["content"])


def complete(prompt, model=DEFAULT_MODEL, timeout=TIMEOUT, max_tokens=None):
    """The model's reply to a single user prompt -- see `complete_thread`."""
    return complete_thread([{"role": "user", "content": prompt}],
                           model=model, timeout=timeout, max_tokens=max_tokens)
