"""Runtime configuration for the accommodanda pipeline.

A single optional ``config.yml`` at the repo root controls where the
downloaded and generated corpus is stored (the ``data_root`` key). It is
loaded with ruamel.yaml in round-trip mode, so the parsed document keeps
comments, formatting *and* source line numbers. The line numbers let a bad
value point at the offending line (``data_root invalid at config.yml:43``),
and round-trip writes (planned) can rewrite one key without disturbing the
rest of the file.

Scope is deliberately narrow: this module locates the *corpus*, and the
checkout the running site writes into -- ``wiki_root``, the markdown content
repo, which holds the commentaries, the concepts, the annotation layers *and*
the source patches the editor commits. Curated source resources that only ever
ship in the repo and are never written at runtime (e.g. ``sfs_namedlaws.json``)
are anchored to the package source tree by their own callers, not here.
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
DEFAULT_OPENSEARCH_URL = "http://localhost:9200"
# where the generated site answers the paths lib/layout.page_url mints -- the
# origin the MCP tools prefix onto them so a remote client gets a link it can
# follow. The rebuilt site, not lagen.nu: only a subset of these paths (statutes,
# cases, förarbeten) exists on the legacy site, while /celex/… and /dom/echr/…
# are the rebuild's own.
DEFAULT_PUBLIC_BASE_URL = "https://ferenda.lagen.nu"
DEFAULT_LLM_MODEL = "openai/gpt-oss-120b"
# the OpenAI-compatible endpoint the ai-* passes call, minus the
# /chat/completions path (lib/llm appends it). Berget unless pointed elsewhere.
DEFAULT_LLM_BASE_URL = "https://api.berget.ai/v1"
DEFAULT_LLM_TEMPERATURE = 0
DEFAULT_LLM_TOP_P = None                     # None => leave top_p out of the payload
# the per-LLM-call input budget (chars) for the batching ai-* passes (forarbete
# ai-genomforande). Sized so every real law's whole FK commentary is one call
# (splitting costs precision, see aigenomforande.BATCH_CHARS); shrink it for a
# deployment whose context window can't take batch + directive catalog + reply
# (the local 64k llama.cpp server needs ~60000 for the giant FKs).
DEFAULT_LLM_BATCH_CHARS = 150000
# the multimodal model for the vision passes (sfs ai-includegraphics): localizing
# a dropped graphic to a page+bbox. Kimi-K2.6 was the Phase-0 spike winner -- the
# only Berget vision model robust on both accuracy and a generic prompt, and it
# honours the requested coordinate space (Gemma reports in an opaque internal
# grid). Text-only gpt-oss cannot serve this.
DEFAULT_VISION_MODEL = "moonshotai/Kimi-K2.6"

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


def _resolve_str(doc, key, env_name, default, post=None):
    """Shared parse for a scalar string setting: the ``env_name`` environment
    variable, then ``key`` in config.yml, else ``default`` (a value, or a
    zero-arg callable for a default that must stay lazy -- e.g.
    `resolve_catalog_root` falling back to `resolve_data_root`, which must
    not run, and must not raise, just because ``CATALOG_ROOT`` was already
    set). ``if env`` rather than ``env is not None``: an exported-but-empty
    variable is how a shell spells "unset" and must fall through to the
    config key. ``post`` (e.g. ``Path.expanduser`` or ``str.rstrip``), if
    given, transforms the env value and the config value the same way, but
    never the literal ``default`` -- every one of these settings already
    stores its built-in default pre-transformed."""
    env = os.environ.get(env_name)
    if env:
        return post(env) if post else env
    if key not in doc:
        return default() if callable(default) else default
    value = doc[key]
    if not isinstance(value, str) or not value.strip():
        raise ConfigError("%s set to invalid value %r at %s"
                          % (key, value, _at(doc, key)))
    return post(value) if post else value


def resolve_data_root(doc):
    """The corpus root: where the downloaded + generated corpus is stored.
    Precedence mirrors every other scalar setting here -- the ``DATA_ROOT``
    environment variable, then the ``data_root`` key in config.yml, then
    ``<repo>/site/data``."""
    return _resolve_str(doc, "data_root", "DATA_ROOT", DEFAULT_DATA,
                        post=lambda v: Path(v).expanduser())


def resolve_catalog_root(doc):
    """The directory holding ``catalog.sqlite``, decoupled from ``data_root`` so
    the small, latency-sensitive SQLite index can sit on fast local disk while the
    bulk artifact corpus sits on slower/NFS storage. This matters because SQLite's
    per-statement locking turns into synchronous network round-trips on NFS -- a
    catalog read that is ~0.2 ms on local disk is ~8 ms per fresh connection over
    NFS, and a query-heavy page pays that many times over. Keeping the artifacts on
    NFS is fine (they are streamed/mmap'd, served from page cache); only the catalog
    must stay local. Precedence: the ``CATALOG_ROOT`` environment variable, then the
    ``catalog_root`` key in config.yml, then ``data_root`` (colocated -- the
    historical layout, and still the default)."""
    return _resolve_str(doc, "catalog_root", "CATALOG_ROOT",
                        lambda: resolve_data_root(doc),
                        post=lambda v: Path(v).expanduser())


def resolve_wiki_root(doc):
    """The git-backed markdown content repo the wiki source reads: begrepp,
    kommentar, the site chrome, the annotation layers, and the source patches
    (``<wiki_root>/patches``, `layout.PATCHES`). Precedence: the ``WIKI_ROOT``
    environment variable, then the ``wiki_root`` key in config.yml, then
    ``<repo>/../lagen-wiki`` (the sibling checkout). Authored separately in its
    own git repo (a sibling checkout, not a submodule), so it is not under
    ``data_root``.

    It names the checkout root rather than any one content directory because the
    two editors *commit* what they write, so they need the repo; deriving one
    from the other by asking git costs a subprocess per save and can disagree
    with the configured path whenever a symlink is involved."""
    return _resolve_str(doc, "wiki_root", "WIKI_ROOT", DEFAULT_WIKI_ROOT,
                        post=lambda v: Path(v).expanduser())


def resolve_opensearch_url(doc):
    """The OpenSearch endpoint for the search index. Precedence: the
    ``OPENSEARCH_URL`` environment variable (for ad-hoc overrides), then the
    ``opensearch_url`` key in config.yml, then ``http://localhost:9200``."""
    return _resolve_str(doc, "opensearch_url", "OPENSEARCH_URL", DEFAULT_OPENSEARCH_URL)


def resolve_public_base_url(doc):
    """The origin the generated site is served from, for the absolute URLs the MCP
    tools hand a remote client.

    Pages link each other by root-relative path (``lib/layout.page_url`` -- a
    statute is ``/2018:585``), which is right for a browser on our own origin and
    wrong for an MCP client: ChatGPT resolved the ``url`` field against
    ``https://chatgpt.com`` and rendered citations pointing there. So the MCP layer
    joins this base onto the path; nothing else uses it.

    Precedence: the ``PUBLIC_BASE_URL`` environment variable, then the
    ``public_base_url`` key in config.yml, then the production origin (correct for
    prod without a config edit; a dev serve on another port should set it)."""
    return _resolve_str(doc, "public_base_url", "PUBLIC_BASE_URL",
                        DEFAULT_PUBLIC_BASE_URL, post=_checked_origin)


def _checked_origin(value):
    """An absolute ``scheme://host`` origin with no trailing slash. A relative or
    scheme-less value is the exact defect this setting exists to prevent, so it
    raises here rather than producing a subtly wrong link in every tool result."""
    origin = value.rstrip("/")
    if not origin.startswith(("http://", "https://")):
        raise ValueError(
            "public_base_url must be an absolute http(s) origin, got %r" % value)
    return origin


def resolve_llm_model(doc):
    """The chat model for the opt-in LLM passes (eurlex ai-annotate, sfs
    ai-correspond). Precedence: the ``BERGET_MODEL`` environment variable (ad-hoc
    overrides), then the ``llm_model`` key in config.yml, then the built-in
    default. Picking a faster/smaller model here is the lever for the latency of
    those passes."""
    return _resolve_str(doc, "llm_model", "BERGET_MODEL", DEFAULT_LLM_MODEL)


def resolve_llm_base_url(doc):
    """The OpenAI-compatible chat-completions endpoint the opt-in LLM passes call,
    without the ``/chat/completions`` path (``lib/llm`` appends it). Point it at a
    local llama.cpp server (``http://127.0.0.1:8123/v1``) to run the passes on the
    workstation GPU instead of Berget -- unmetered and private, which is what makes
    bulk passes over a whole corpus affordable (``docs/local-llm.md``). A local
    endpoint needs no API key: ``lib/llm`` demands ``BERGET_API_KEY`` only for a
    remote host. Precedence: the ``LLM_BASE_URL`` environment variable, then the
    ``llm_base_url`` key in config.yml, then Berget."""
    return _resolve_str(doc, "llm_base_url", "LLM_BASE_URL", DEFAULT_LLM_BASE_URL,
                        post=lambda v: v.rstrip("/"))


def _resolve_float(doc, key, env_name, default, lo, hi):
    """Shared parse + range check for the float sampling knobs
    (`resolve_llm_temperature`, `resolve_llm_top_p`): env var, then the config key,
    else ``default``. Out-of-range or unparseable raises rather than clamping -- a
    silently corrected sampling knob would change every reply without saying so."""
    # `if env` rather than `env is not None`, as every sibling resolver here
    # does: an exported-but-empty variable is how a shell spells "unset", and it
    # must fall through to the config key rather than fail to parse as a float.
    env = os.environ.get(env_name)
    raw = env if env else doc.get(key)
    if raw is None:
        return default
    where = env_name if env else "%s at %s" % (key, _at(doc, key))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ConfigError("%s set to invalid value %r (expected a number %g-%g)"
                          % (where, raw, lo, hi)) from None
    if not lo <= value <= hi:
        raise ConfigError("%s set to %g (out of the valid range %g-%g)"
                          % (where, value, lo, hi))
    return value


def resolve_llm_temperature(doc):
    """The sampling temperature for the opt-in LLM passes. Default 0: the passes
    read structure out of a document and want the most probable reading, and
    `lib/llm.author`'s retry replays the rejected reply as a real follow-up turn
    rather than relying on resampling to shake out a different answer. Raise it for
    a model whose thinking mode degrades under greedy decoding -- Qwen3.6 asks for
    1.0 and loops without it (``docs/local-llm.md``). Precedence: the
    ``LLM_TEMPERATURE`` environment variable, then the ``llm_temperature`` key in
    config.yml, else 0."""
    return _resolve_float(doc, "llm_temperature", "LLM_TEMPERATURE",
                          DEFAULT_LLM_TEMPERATURE, 0, 2)


def resolve_llm_top_p(doc):
    """Nucleus-sampling cutoff for the opt-in LLM passes, or ``None`` (the default)
    to leave ``top_p`` out of the payload entirely so the endpoint's own default
    applies -- Berget's passes have never set it and must not start now. Set it
    alongside a raised `llm_temperature`, which is not the whole recipe on its own:
    Qwen3.6 wants 0.95 in thinking mode. Precedence: the ``LLM_TOP_P`` environment
    variable, then the ``llm_top_p`` key in config.yml, else unset."""
    return _resolve_float(doc, "llm_top_p", "LLM_TOP_P", DEFAULT_LLM_TOP_P, 0, 1)


def resolve_llm_batch_chars(doc):
    """The per-call input budget (chars) for the batching LLM passes.
    Precedence mirrors every other scalar here: the ``LLM_BATCH_CHARS``
    environment variable (the cmdline override: ``LLM_BATCH_CHARS=60000 lagen
    forarbete ai-genomforande …``), then the ``llm_batch_chars`` key in
    config.yml, else 150000."""
    return int(_resolve_float(doc, "llm_batch_chars", "LLM_BATCH_CHARS",
                              DEFAULT_LLM_BATCH_CHARS, 1000, 10_000_000))


def resolve_vision_model(doc):
    """The multimodal model for the vision passes (sfs ai-includegraphics).
    Precedence mirrors `resolve_llm_model`: the ``BERGET_VISION_MODEL`` env
    override, then the ``vision_model`` key in config.yml, then the built-in
    default (Kimi-K2.6). Kept separate from `llm_model` because the text passes
    run a reasoning model that has no vision, and the vision model is the pricier
    of the two -- one lever each."""
    return _resolve_str(doc, "vision_model", "BERGET_VISION_MODEL", DEFAULT_VISION_MODEL)


def _resolve_bool(doc, key, env_name, default):
    """Shared parse for an on/off setting: the ``env_name`` environment
    variable (``0``/``1``, ``false``/``true``, ``no``/``yes``, ``off``/``on``),
    then ``key`` in config.yml, else ``default``. A present-but-uninterpretable
    value raises rather than guessing."""
    env = os.environ.get(env_name)
    if env is not None:
        low = env.strip().lower()
        if low in ("1", "true", "yes", "on"):
            return True
        if low in ("0", "false", "no", "off"):
            return False
        raise ConfigError("%s set to invalid value %r "
                          "(expected a boolean)" % (env_name, env))
    if key not in doc:
        return default
    value = doc[key]
    if not isinstance(value, bool):
        raise ConfigError("%s set to invalid value %r at %s "
                          "(expected true/false)" % (key, value, _at(doc, key)))
    return value


def resolve_compress(doc):
    """Whether the artifact/ and generated/ trees are stored precompressed
    (lib/compress). On (the default) => a parsed artifact lands as ``.json.br``
    and a rendered page as ``.html.br`` (Brotli, no plain sibling) so nginx can
    serve the bytes as-is (`brotli_static`) with no app in the path -- and the
    tree stays small on disk; off => plain files, for a dev checkout that would
    rather diff them.
    Precedence: the ``FERENDA_COMPRESS`` environment variable (``0``/``1``,
    ``false``/``true``), then the ``compress`` key in config.yml, else on. A
    present-but-uninterpretable value raises rather than guessing."""
    return _resolve_bool(doc, "compress", "FERENDA_COMPRESS", True)


def _resolve_int(doc, key, env_name, default, lo, hi, invalid_hint, range_hint):
    """Shared parse + range check for an integer setting: the ``env_name``
    environment variable, then ``key`` in config.yml, else ``default``.
    Out-of-range or unparseable raises rather than clamping/guessing, same
    rationale as `_resolve_float`. ``hi`` may be ``None`` for an open-ended
    upper bound (e.g. a Matomo site id)."""
    env = os.environ.get(env_name)
    raw = env if env else doc.get(key)
    if raw is None:
        return default
    where = env_name if env else "%s at %s" % (key, _at(doc, key))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ConfigError("%s set to invalid value %r (%s)"
                          % (where, raw, invalid_hint)) from None
    if value < lo or (hi is not None and value > hi):
        raise ConfigError("%s set to %d (%s)" % (where, value, range_hint))
    return value


def resolve_compress_quality(doc):
    """The Brotli quality (0--11) the two text trees are compressed at. The
    payload is JSON/HTML compressed once per build and served/read forever, so
    the default is the maximum (11): on representative text it lands well under a
    third the size of gzip and decompresses faster, and the extra CPU is paid
    only at build time. Lower it (e.g. 9, ~13x faster for ~10% larger output)
    when build latency matters more than bytes. Precedence:
    ``FERENDA_COMPRESS_QUALITY`` env var, then the ``compress_quality`` config
    key, else 11."""
    return _resolve_int(doc, "compress_quality", "FERENDA_COMPRESS_QUALITY", 11, 0, 11,
                        "expected an integer 0-11", "out of the valid Brotli range 0-11")


def resolve_editor_secret(doc):
    """The HMAC key that signs the inline editor's session cookie (api/auth.py).
    Unset (``None``) disables editing entirely -- every mutating route answers
    403 -- and, since the ops dashboard (`/ops`) now rides the same editor
    session, disables that too. Precedence: the ``EDITOR_SECRET`` environment
    variable, then the ``editor_secret`` key in config.yml, else ``None``. A
    present-but-invalid value raises ``ConfigError`` rather than silently
    disabling auth."""
    return _resolve_str(doc, "editor_secret", "EDITOR_SECRET", None)


def resolve_cookie_secure(doc):
    """Whether the editor session cookie (api/auth.py) carries the ``Secure``
    flag. Default on: the prod deploy is https-only, so the cookie should never
    be sent in the clear. A per-request ``X-Forwarded-Proto`` check is
    spoofable by anyone who can reach the app directly (or a misconfigured
    proxy), so this is an explicit, config-driven switch instead -- flip it off
    only for a plain-http dev serve. Precedence: the ``EDITOR_COOKIE_SECURE``
    environment variable (``0``/``1``, ``false``/``true``), then the
    ``cookie_secure`` key in config.yml, else on."""
    return _resolve_bool(doc, "cookie_secure", "EDITOR_COOKIE_SECURE", True)


def resolve_matomo_url(doc):
    """The Matomo tracking endpoint the machine-facing surfaces (/api/v1, /mcp)
    report to -- ``api/analytics.py``. On prod this is the Matomo container over
    the compose network (``http://matomo/matomo.php``), not the public
    ``https://lagen.nu/matomo/matomo.php``: the hit never leaves the host, so it
    costs no TLS handshake and cannot be blocked by anything between us. Unset
    (``None``) disables server-side tracking entirely, which is what a dev serve
    wants. Precedence: the ``MATOMO_URL`` environment variable, then the
    ``matomo_url`` key in config.yml, else ``None``.

    The scheme is checked here so the common typo (a bare host, a path with no
    scheme) is reported against *this* setting by name. It is only half the
    guard: `api/analytics.py` parses the whole URL at import, because everything
    a prefix check cannot see -- a bad port, a stray newline -- would otherwise
    raise inside the tracking worker thread and stop analytics silently."""
    env = os.environ.get("MATOMO_URL")
    value = env if env else doc.get("matomo_url")
    if value is None:
        return None
    where = "MATOMO_URL" if env else "matomo_url at %s" % _at(doc, "matomo_url")
    if not isinstance(value, str) or not value.startswith(("http://", "https://")):
        raise ConfigError("%s set to invalid value %r (expected a http(s) URL to "
                          "Matomo's matomo.php)" % (where, value))
    return value


def resolve_matomo_site_api(doc):
    """The Matomo *site id* machine traffic (the REST API + the MCP server) is
    recorded under. Deliberately a different site from the one the reader-facing
    pages ping (that id lives in ``lib/assets/matomo.js``, since the pages track
    from the browser): agents and scripts have no bounce rate or visit duration
    worth reading, and mixing them into the human numbers would poison both.
    Unset (``None``) disables server-side tracking. Precedence: the
    ``MATOMO_SITE_API`` environment variable, then ``matomo_site_api`` in
    config.yml, else ``None``."""
    return _resolve_int(doc, "matomo_site_api", "MATOMO_SITE_API", None, 1, None,
                        "expected a Matomo site id", "Matomo site ids start at 1")


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
        loc = _at(raw, user)          # the entry's own line, not `editors:`'s
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
CATALOG_ROOT = resolve_catalog_root(_doc)
WIKI_ROOT = resolve_wiki_root(_doc)
OPENSEARCH_URL = resolve_opensearch_url(_doc)
PUBLIC_BASE_URL = resolve_public_base_url(_doc)
LLM_MODEL = resolve_llm_model(_doc)
LLM_BASE_URL = resolve_llm_base_url(_doc)
LLM_TEMPERATURE = resolve_llm_temperature(_doc)
LLM_TOP_P = resolve_llm_top_p(_doc)
LLM_BATCH_CHARS = resolve_llm_batch_chars(_doc)
VISION_MODEL = resolve_vision_model(_doc)
EDITOR_SECRET = resolve_editor_secret(_doc)
EDITORS = resolve_editors(_doc)
COMPRESS = resolve_compress(_doc)
COMPRESS_QUALITY = resolve_compress_quality(_doc)
COOKIE_SECURE = resolve_cookie_secure(_doc)
MATOMO_URL = resolve_matomo_url(_doc)
MATOMO_SITE_API = resolve_matomo_site_api(_doc)
