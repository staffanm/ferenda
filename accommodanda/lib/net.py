"""Shared HTTP session setup for the source downloaders.

A single transient 5xx/429 or connection blip during a multi-thousand-document
harvest would otherwise abort the whole walk, so every session retries those
with exponential backoff. POST is included because the SFS and DV search
endpoints page over POST. ``raise_on_status=False`` leaves the final response
for the caller's ``raise_for_status()`` so error semantics are unchanged.
"""

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

_RETRY = Retry(total=4, backoff_factor=0.5,
               status_forcelist=(429, 500, 502, 503, 504),
               allowed_methods=frozenset({"GET", "POST"}),
               raise_on_status=False)


def make_session(user_agent):
    session = requests.Session()
    session.headers["User-Agent"] = user_agent
    adapter = HTTPAdapter(max_retries=_RETRY)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session
