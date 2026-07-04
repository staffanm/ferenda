"""Runtime configuration for the accommodanda pipeline.

A single optional ``config.yml`` at the repo root controls where the
downloaded and generated corpus is stored (the ``data_root`` key). It is
loaded with ruamel.yaml in round-trip mode, so the parsed document keeps
comments, formatting *and* source line numbers. The line numbers let a bad
value point at the offending line (``data_root invalid at config.yml:43``),
and round-trip writes (planned) can rewrite one key without disturbing the
rest of the file.

Scope is deliberately narrow: this module locates the *corpus*, nothing
else. Curated source resources that ship in the repo (e.g. ``sfs_namedlaws.json``) are
anchored to the package source tree by their own callers, not here.
"""

import os
import re
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

# an editor `pwhash` as `api.auth.hash_password` mints it: pbkdf2$rounds$salt$hash
# (salt/hash are unpadded urlsafe-base64). Validated at config load so a mangled
# hash fails at boot, not as a 500 on that editor's first login.
_RE_PWHASH = re.compile(r"^pbkdf2\$\d+\$[A-Za-z0-9_-]+\$[A-Za-z0-9_-]+$")

REPO = Path(__file__).parent.parent          # the ferenda repo root
CONFIG_PATH = REPO / "config.yml"
DEFAULT_DATA = REPO / "site" / "data"
DEFAULT_WIKI_ROOT = REPO.parent / "lagen-wiki"   # git-backed markdown content repo
DEFAULT_LEGACY_ROOT = REPO.parent / "ferenda.old" / "data"   # frozen legacy corpora
DEFAULT_OPENSEARCH_URL = "http://localhost:9200"
DEFAULT_LLM_MODEL = "openai/gpt-oss-120b"

_yaml = YAML()                               # round-trip mode by default


class ConfigError(Exception):
    """A config value is present but invalid; the message carries its
    ``config.yml:line`` location."""


def load():
    """The parsed config document (round-trip), or an empty one if the file
    is absent or holds only comments."""
    if CONFIG_PATH.exists():
        return _yaml.load(CONFIG_PATH) or CommentedMap()
    return CommentedMap()


def _at(doc, key):
    """``config.yml:line`` (1-based) of ``key``'s value, for error messages."""
    line = doc.lc.value(key)[0] + 1
    return "%s:%d" % (CONFIG_PATH.name, line)


def resolve_data_root(doc):
    """The corpus root from ``doc``, defaulting to ``<repo>/site/data``."""
    if "data_root" not in doc:
        return DEFAULT_DATA
    value = doc["data_root"]
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(
            "data_root set to invalid value %r at %s" % (value, _at(doc, "data_root")))
    return Path(value).expanduser()


def resolve_wiki_root(doc):
    """The git-backed markdown content repo (begrepp + kommentar) the wiki source
    reads. Precedence: the ``WIKI_ROOT`` environment variable, then the
    ``wiki_root`` key in config.yml, then ``<repo>/../lagen-wiki`` (the sibling
    checkout). Authored separately in its own git repo (a sibling checkout, not a
    submodule), so it is not under ``data_root``."""
    env = os.environ.get("WIKI_ROOT")
    if env:
        return Path(env).expanduser()
    if "wiki_root" not in doc:
        return DEFAULT_WIKI_ROOT
    value = doc["wiki_root"]
    if not isinstance(value, str) or not value.strip():
        raise ConfigError("wiki_root set to invalid value %r at %s"
                          % (value, _at(doc, "wiki_root")))
    return Path(value).expanduser()


def resolve_legacy_root(doc):
    """Where the frozen legacy corpora (the old pipeline's ``downloaded/`` +
    ``entries/`` trees, REWRITE.md §7g) live. Import verbs walk it and the
    records they write reference body files inside it in place (the 410 GB
    soukb tree is never copied), so the key must keep pointing at the trees
    wherever they are mounted. Precedence: the ``LEGACY_ROOT`` environment
    variable, then the ``legacy_root`` key in config.yml, then
    ``<repo>/../ferenda.old/data`` (the sibling checkout)."""
    env = os.environ.get("LEGACY_ROOT")
    if env:
        return Path(env).expanduser()
    if "legacy_root" not in doc:
        return DEFAULT_LEGACY_ROOT
    value = doc["legacy_root"]
    if not isinstance(value, str) or not value.strip():
        raise ConfigError("legacy_root set to invalid value %r at %s"
                          % (value, _at(doc, "legacy_root")))
    return Path(value).expanduser()


def resolve_opensearch_url(doc):
    """The OpenSearch endpoint for the search index. Precedence: the
    ``OPENSEARCH_URL`` environment variable (for ad-hoc overrides), then the
    ``opensearch_url`` key in config.yml, then ``http://localhost:9200``."""
    env = os.environ.get("OPENSEARCH_URL")
    if env:
        return env
    if "opensearch_url" not in doc:
        return DEFAULT_OPENSEARCH_URL
    value = doc["opensearch_url"]
    if not isinstance(value, str) or not value.strip():
        raise ConfigError("opensearch_url set to invalid value %r at %s"
                          % (value, _at(doc, "opensearch_url")))
    return value


def resolve_llm_model(doc):
    """The chat model for the opt-in LLM passes (eurlex ai-annotate, sfs
    ai-correspond). Precedence: the ``BERGET_MODEL`` environment variable (ad-hoc
    overrides), then the ``llm_model`` key in config.yml, then the built-in
    default. Picking a faster/smaller model here is the lever for the latency of
    those passes."""
    env = os.environ.get("BERGET_MODEL")
    if env:
        return env
    if "llm_model" not in doc:
        return DEFAULT_LLM_MODEL
    value = doc["llm_model"]
    if not isinstance(value, str) or not value.strip():
        raise ConfigError("llm_model set to invalid value %r at %s"
                          % (value, _at(doc, "llm_model")))
    return value


def resolve_ops_token(doc):
    """The shared secret guarding the ops dashboard (`/ops`, api/ops.py). It is
    the HTTP-Basic password for user ``ops``; unset (``None``) leaves the
    dashboard disabled (its routes answer 403 with a hint). Precedence: the
    ``OPS_TOKEN`` environment variable, then the ``ops_token`` key in
    config.yml, else ``None``. Like its siblings, a present-but-invalid value
    (non-string or empty) raises ``ConfigError`` rather than silently falling
    back to ``None`` -- a typo must not disable auth quietly."""
    env = os.environ.get("OPS_TOKEN")
    if env:
        return env
    if "ops_token" not in doc:
        return None
    value = doc["ops_token"]
    if not isinstance(value, str) or not value.strip():
        raise ConfigError("ops_token set to invalid value %r at %s"
                          % (value, _at(doc, "ops_token")))
    return value


def resolve_editor_secret(doc):
    """The HMAC key that signs the inline editor's session cookie (api/auth.py).
    Unset (``None``) disables editing entirely -- every mutating route answers
    403, exactly like an unset ``ops_token`` disables the dashboard. Precedence:
    the ``EDITOR_SECRET`` environment variable, then the ``editor_secret`` key in
    config.yml, else ``None``. A present-but-invalid value raises ``ConfigError``
    rather than silently disabling auth."""
    env = os.environ.get("EDITOR_SECRET")
    if env:
        return env
    if "editor_secret" not in doc:
        return None
    value = doc["editor_secret"]
    if not isinstance(value, str) or not value.strip():
        raise ConfigError("editor_secret set to invalid value %r at %s"
                          % (value, _at(doc, "editor_secret")))
    return value


def resolve_editors(doc):
    """The registry of people allowed to edit content inline, keyed by login
    name. Each entry maps a username to a ``name``/``email`` (the git identity
    stamped on that user's commits, so history attributes each editor exactly as
    a `git clone` + commit would) and a ``pwhash`` (a ``pbkdf2$…`` string minted
    by ``python -m accommodanda.api.auth hash``; no plaintext password is ever
    stored). Absent -> ``{}`` (no one can log in). A malformed entry raises
    ``ConfigError`` -- a typo must not silently drop an editor or their identity.
    Read from config.yml only; there is no env-var form (identities are not a
    single scalar)."""
    if "editors" not in doc:
        return {}
    raw = doc["editors"]
    if not isinstance(raw, dict) or not raw:
        raise ConfigError("editors set to invalid value %r at %s"
                          % (raw, _at(doc, "editors")))
    editors = {}
    for user, entry in raw.items():
        loc = _at(doc, "editors")
        if not isinstance(entry, dict):
            raise ConfigError("editor %r is not a mapping at %s" % (user, loc))
        missing = [k for k in ("name", "email", "pwhash")
                   if not (isinstance(entry.get(k), str) and entry[k].strip())]
        if missing:
            raise ConfigError("editor %r missing %s at %s"
                              % (user, "/".join(missing), loc))
        if not _RE_PWHASH.match(entry["pwhash"]):
            raise ConfigError("editor %r has a malformed pwhash at %s -- mint one "
                              "with `python -m accommodanda.api.auth hash`"
                              % (user, loc))
        editors[str(user)] = {"name": entry["name"], "email": entry["email"],
                              "pwhash": entry["pwhash"]}
    return editors


_doc = load()                                # parse config.yml once
DATA = resolve_data_root(_doc)
WIKI_ROOT = resolve_wiki_root(_doc)
LEGACY_ROOT = resolve_legacy_root(_doc)
OPENSEARCH_URL = resolve_opensearch_url(_doc)
LLM_MODEL = resolve_llm_model(_doc)
OPS_TOKEN = resolve_ops_token(_doc)
EDITOR_SECRET = resolve_editor_secret(_doc)
EDITORS = resolve_editors(_doc)
