"""Transparent on-disk compression for the two big text trees -- ``artifact/``
(the parsed-JSON source of truth) and ``generated/`` (the rendered HTML site).

**Why Brotli.** The payload is text-heavy, structure-light JSON/HTML. On a
representative corpus Brotli at quality 11 lands around a *third* the size of
gzip -9 (~6% vs ~17% of the original here) *and* decompresses faster than gzip
(the format is asymmetric: slow max-quality encode, quick decode). Compression is
paid once per build; serving/reading happens forever after -- exactly the
asymmetry the design calls for, so artifacts are stored Brotli-only at the
configured (default max) quality.

**Brotli only, no gzip companion.** Both trees store a single ``.br`` variant --
saving disk is the whole point (this runs on a small VPS), so we do not also
keep a larger ``.gz``. Every current browser accepts ``Content-Encoding: br``, so
nginx's ``brotli_static`` serves the generated pages as-is; the one client class
that can't take brotli (a bare HTTP tool sending no/`gzip`-only
``Accept-Encoding``) is handled by the in-process ``api.app.SiteFiles`` fallback,
which decompresses the ``.br`` and serves plain identity bytes. The codec table
below still knows gzip, so re-enabling a companion is a one-line policy change if
a future deployment needs stock-nginx ``gzip_static`` without the brotli module.

**Transparency.** Callers keep passing *logical* paths (``foo.json``,
``bar.html``); the on-disk file is ``foo.json.br`` / ``bar.html.br`` (+ ``.gz``).
``read_bytes``/``read_text``/``exists``/``stat`` resolve a logical path to
whichever variant is present -- plain first (a file a source hasn't compressed, or
one below the size floor), then ``.br``, then ``.gz`` -- so a half-migrated tree
always reads. ``write_bytes``/``write_text`` emit the configured variant(s) and
delete any stale sibling, so a logical path never has two live representations.

Files below ``MIN_SIZE`` are stored plain: compressing a few hundred bytes barely
helps (and can inflate), and it keeps tiny always-served files like ``robots.txt``
and empty ``SkipDocument`` placeholders universally readable with no encoding.
"""

import gzip as _gzip
import mimetypes
import os
from pathlib import Path

import brotli

from .. import config
from .util import write_atomic

# encoding token (the HTTP `Content-Encoding` / `Accept-Encoding` name) -> the
# on-disk suffix, in *preference order* (best ratio first). nginx's
# brotli_static/gzip_static and the SiteFiles fallback both honour this order.
ENCODINGS = (("br", ".br"), ("gzip", ".gz"))
SUFFIX_FOR = dict(ENCODINGS)
ENCODING_FOR = {suffix: enc for enc, suffix in ENCODINGS}
SUFFIXES = tuple(suffix for _enc, suffix in ENCODINGS)

# storage policy: a single Brotli variant for both trees -- smallest on disk (the
# goal on a small VPS). Distinct names document intent at the call sites; both are
# br-only. A `.gz` companion for stock-nginx gzip_static is a one-token change.
ARTIFACT_ENCODINGS = ("br",)
PAGE_ENCODINGS = ("br",)

# below this many bytes, store plain -- the codec overhead is not worth it and a
# tiny file is cheap to serve uncompressed to any client (see module docstring).
MIN_SIZE = 512


def _quality():
    return config.COMPRESS_QUALITY


def compress_bytes(data, encoding):
    """Compress `data` (bytes) into the given `Content-Encoding`."""
    if encoding == "br":
        return brotli.compress(data, mode=brotli.MODE_TEXT, quality=_quality())
    if encoding == "gzip":
        # mtime=0 so the gzip header is reproducible (a rebuild of unchanged
        # content yields byte-identical output, keeping watermarks/etags stable).
        return _gzip.compress(data, compresslevel=9, mtime=0)
    raise ValueError("unknown encoding %r" % encoding)


def decompress_bytes(data, encoding):
    """Inverse of `compress_bytes`."""
    if encoding == "br":
        return brotli.decompress(data)
    if encoding == "gzip":
        return _gzip.decompress(data)
    raise ValueError("unknown encoding %r" % encoding)


def logical(path):
    """Strip a trailing compression suffix, giving the logical path callers use
    (``foo.json.br`` -> ``foo.json``); a path with no suffix is returned as-is."""
    p = Path(path)
    for suffix in SUFFIXES:
        if p.name.endswith(suffix):
            return p.with_name(p.name[: -len(suffix)])
    return p


def variant_suffix(path):
    """The compression suffix of an on-disk variant path, or ``""`` if plain."""
    name = Path(path).name
    for suffix in SUFFIXES:
        if name.endswith(suffix):
            return suffix
    return ""


def resolve(path):
    """The actual on-disk file for a logical `path`: the plain file if it exists,
    else the ``.br`` then ``.gz`` variant, else ``None``. `path` is taken as the
    logical name even if it already carries a suffix (so passing a resolved path
    back in is idempotent)."""
    p = logical(path)
    if p.exists():
        return p
    for suffix in SUFFIXES:
        candidate = p.with_name(p.name + suffix)
        if candidate.exists():
            return candidate
    return None


def exists(path):
    """Whether a logical `path` has any on-disk representation."""
    return resolve(path) is not None


def stat(path):
    """`os.stat` of the on-disk file backing a logical `path` (its real size +
    mtime -- what the freshness watermarks fingerprint). Raises like `os.stat`
    if nothing is present."""
    resolved = resolve(path)
    if resolved is None:
        raise FileNotFoundError(str(path))
    return resolved.stat()


def read_bytes(path):
    """The decompressed content behind a logical `path`, whatever variant is on
    disk. Raises `FileNotFoundError` if none is."""
    resolved = resolve(path)
    if resolved is None:
        raise FileNotFoundError(str(path))
    data = resolved.read_bytes()
    encoding = ENCODING_FOR.get(variant_suffix(resolved))
    return decompress_bytes(data, encoding) if encoding else data


def read_text(path, encoding="utf-8"):
    return read_bytes(path).decode(encoding)


def _clear_variants(logical_path, keep=()):
    """Remove every on-disk representation of `logical_path` except those whose
    suffix is in `keep` (``""`` keeps the plain file), so a logical path is left
    with exactly the variant set just written."""
    if "" not in keep and logical_path.exists():
        logical_path.unlink()
    for suffix in SUFFIXES:
        if suffix in keep:
            continue
        sibling = logical_path.with_name(logical_path.name + suffix)
        if sibling.exists():
            sibling.unlink()


def _selected(encodings):
    """The encodings actually written: the caller's request, gated by the master
    ``config.COMPRESS`` switch (off => store plain)."""
    return tuple(encodings) if config.COMPRESS else ()


def write_bytes(path, data, encodings=PAGE_ENCODINGS):
    """Write `data` (bytes) for a logical `path`, storing the configured
    compressed variant(s) and clearing any stale sibling. Small files (and, with
    compression disabled, all files) are stored plain.

    Every variant is written atomically (util.write_atomic: same-directory temp
    file + rename): this is the single write funnel for the artifact tree -- the
    source of truth -- and the served page tree, where an interrupted run must
    not leave a truncated file. A zero-byte artifact is *meaningful* (a
    SkipDocument placeholder the catalog deliberately drops), so a partial write
    surviving here would silently corrupt the corpus, not just a page
    (rule:artifact-is-truth)."""
    p = logical(path)
    encs = _selected(encodings)
    if not encs or len(data) < MIN_SIZE:
        write_atomic(p, data)
        _clear_variants(p, keep=("",))
        return
    kept = []
    for enc in encs:
        suffix = SUFFIX_FOR[enc]
        write_atomic(p.with_name(p.name + suffix), compress_bytes(data, enc))
        kept.append(suffix)
    _clear_variants(p, keep=tuple(kept))


def write_text(path, text, encodings=PAGE_ENCODINGS, encoding="utf-8"):
    write_bytes(path, text.encode(encoding), encodings=encodings)


def unlink(path):
    """Remove every representation of a logical `path` (plain + all variants)."""
    _clear_variants(logical(path), keep=())


def media_type(logical_path):
    """The ``Content-Type`` for a logical path, charset-tagged for text so a
    served ``.br``/``.gz`` still declares the right type (its filename suffix
    would otherwise mislead the guesser)."""
    guessed, _enc = mimetypes.guess_type(str(logical_path))
    if guessed is None:
        return "application/octet-stream"
    if guessed.startswith("text/") or guessed in (
            "application/json", "application/javascript"):
        return "%s; charset=utf-8" % guessed
    return guessed


def glob(directory, pattern):
    """`Path.glob` over the logical names of a compressed tree: match `pattern`
    against plain files *and* their `.br`/`.gz` variants, mapping each hit back to
    its logical path (deduplicated). Callers keep their exact glob pattern (e.g.
    the one-nesting-level ``*/*.json`` that must not recurse into an archive
    subtree) and transparently see compressed artifacts. Returns a set; the caller
    sorts/filters."""
    root = Path(directory)
    return {logical(p) for suffix in ("", *SUFFIXES)
            for p in root.glob(pattern + suffix)}


def variants_on_disk(directory, relpath):
    """The compressed variants of `relpath` present under `directory`, as
    ``{encoding: (full_path, os.stat_result)}`` -- the served-file lookup the
    SiteFiles fallback uses (nginx does the equivalent with the static modules).
    `directory` bounds the lookup; a `relpath` escaping it is ignored."""
    root = Path(directory).resolve()
    found = {}
    for enc, suffix in ENCODINGS:
        candidate = (root / (relpath + suffix)).resolve()
        if os.path.commonpath((root, candidate)) != str(root):
            continue
        if candidate.is_file():
            found[enc] = (candidate, candidate.stat())
    return found
