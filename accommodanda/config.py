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
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

REPO = Path(__file__).parent.parent          # the ferenda repo root
CONFIG_PATH = REPO / "config.yml"
DEFAULT_DATA = REPO / "site" / "data"
DEFAULT_OPENSEARCH_URL = "http://localhost:9200"

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


_doc = load()                                # parse config.yml once
DATA = resolve_data_root(_doc)
OPENSEARCH_URL = resolve_opensearch_url(_doc)
