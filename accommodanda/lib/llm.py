"""Shared client for the Berget OpenAI-compatible chat-completions endpoint used
by the opt-in LLM passes (eurlex ai-annotate, sfs ai-correspond). The LLM is
called only from those explicit ai-* actions on named ids -- never from a
corpus-wide parse/relate/generate."""

import os

import requests
from dotenv import load_dotenv

API_URL = "https://api.berget.ai/v1/chat/completions"
DEFAULT_MODEL = "google/gemma-4-31B-it"
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


def complete(prompt, model=DEFAULT_MODEL, timeout=TIMEOUT):
    """The model's reply to a single user prompt (temperature 0), code-fence
    stripped. Reads BERGET_API_KEY from the environment/.env."""
    load_dotenv()
    api_key = os.environ.get("BERGET_API_KEY")
    assert api_key, "BERGET_API_KEY is not set (add it to .env)"
    resp = requests.post(
        API_URL, headers={"Authorization": "Bearer %s" % api_key},
        json={"model": model, "temperature": 0,
              "messages": [{"role": "user", "content": prompt}]},
        timeout=timeout)
    resp.raise_for_status()
    return strip_fence(resp.json()["choices"][0]["message"]["content"])
