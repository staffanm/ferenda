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
import json
import mimetypes
import os
from collections.abc import Sequence
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
DOWNLOAD_ENCODINGS = ("br",)

# The raw `downloaded/` tree is a mix: text (the fetched HTML landing pages, the
# eurlex Formex XML, the record/notice JSON+TTL) that Brotli shrinks 3-10x, and
# already-compressed payloads (born-digital PDFs are internally FlateDecode/JPEG,
# .zip/.docx are zip containers) where a max-quality re-encode costs minutes per
# build for a percent or two. Those are stored plain; everything else takes the
# configured download variant. Extension-driven so a caller need not know the
# codec -- `write_download` reads the policy off the logical name.
INCOMPRESSIBLE_SUFFIXES = frozenset({
    ".pdf", ".zip", ".gz", ".br", ".docx", ".doc", ".xlsx", ".pptx",
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".ico",
    ".woff", ".woff2", ".7z", ".xz", ".bz2", ".rar",
    ".mp4", ".mp3", ".mov", ".avi",
})

# below this many bytes, store plain -- the codec overhead is not worth it and a
# tiny file is cheap to serve uncompressed to any client (see module docstring).
MIN_SIZE = 512


def _quality():
    return config.COMPRESS_QUALITY


def compress_bytes(data: bytes, encoding: str, quality: int | None = None) -> bytes:
    """Compress `data` (bytes) into the given `Content-Encoding`.

    `quality` overrides the configured Brotli level for one call. The default
    (11) is right for the two trees this module was written for, where the ratio
    is what matters and the encode is paid once per document. It is wrong for a
    payload that is *highly* repetitive: on the inbound-citation tree, q11 buys
    17% over q9 and costs 180x the time (52 MB: 56 s vs 0.31 s), because q11's
    exhaustive match search has to work through millions of near-identical
    records. Decode speed is identical at every level, so the reader loses
    nothing. Ignored for gzip.
    """
    if encoding == "br":
        return brotli.compress(data, mode=brotli.MODE_TEXT,
                               quality=_quality() if quality is None else quality)
    if encoding == "gzip":
        # mtime=0 so the gzip header is reproducible (a rebuild of unchanged
        # content yields byte-identical output, keeping watermarks/etags stable).
        return _gzip.compress(data, compresslevel=9, mtime=0)
    raise ValueError("unknown encoding %r" % encoding)


def decompress_bytes(data: bytes, encoding: str) -> bytes:
    """Inverse of `compress_bytes`."""
    if encoding == "br":
        return brotli.decompress(data)
    if encoding == "gzip":
        return _gzip.decompress(data)
    raise ValueError("unknown encoding %r" % encoding)


def logical(path: Path | str) -> Path:
    """Strip a trailing compression suffix, giving the logical path callers use
    (``foo.json.br`` -> ``foo.json``); a path with no suffix is returned as-is."""
    p = Path(path)
    for suffix in SUFFIXES:
        if p.name.endswith(suffix):
            return p.with_name(p.name[: -len(suffix)])
    return p


def _variant_suffix(path: Path | str) -> str:
    """The compression suffix of an on-disk variant path, or ``""`` if plain."""
    name = Path(path).name
    for suffix in SUFFIXES:
        if name.endswith(suffix):
            return suffix
    return ""


def resolve(path: Path | str) -> Path | None:
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


def exists(path: Path | str) -> bool:
    """Whether a logical `path` has any on-disk representation."""
    return resolve(path) is not None


def stat(path: Path | str) -> os.stat_result:
    """`os.stat` of the on-disk file backing a logical `path` (its real size +
    mtime -- what the freshness watermarks fingerprint). Raises like `os.stat`
    if nothing is present."""
    resolved = resolve(path)
    if resolved is None:
        raise FileNotFoundError(str(path))
    return resolved.stat()


def read_bytes(path: Path | str) -> bytes:
    """The decompressed content behind a logical `path`, whatever variant is on
    disk. Raises `FileNotFoundError` if none is."""
    resolved = resolve(path)
    if resolved is None:
        raise FileNotFoundError(str(path))
    data = resolved.read_bytes()
    encoding = ENCODING_FOR.get(_variant_suffix(resolved))
    return decompress_bytes(data, encoding) if encoding else data


def read_text(path: Path | str, encoding: str = "utf-8") -> str:
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


def remove(path: Path | str) -> None:
    """Delete every on-disk representation of a logical `path`. The counterpart
    of `write_bytes` for a *derived* file that has become empty: a derived tree
    is only as trustworthy as its deletions, and a variant left behind would
    serve the previous build's answer forever. A path with nothing on disk is
    not an error -- that is the ordinary case, and the caller wants the same
    end state either way."""
    _clear_variants(logical(path), keep=())


def _selected(encodings):
    """The encodings actually written: the caller's request, gated by the master
    ``config.COMPRESS`` switch (off => store plain)."""
    return tuple(encodings) if config.COMPRESS else ()


def write_bytes(path: Path | str, data: bytes,
                encodings: Sequence[str] = PAGE_ENCODINGS,
                quality: int | None = None) -> None:
    """Write `data` (bytes) for a logical `path`, storing the configured
    compressed variant(s) and clearing any stale sibling. Small files (and, with
    compression disabled, all files) are stored plain. `quality` overrides the
    configured Brotli level -- see `compress_bytes`.

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
        write_atomic(p.with_name(p.name + suffix),
                     compress_bytes(data, enc, quality=quality))
        kept.append(suffix)
    _clear_variants(p, keep=tuple(kept))


def write_text(path: Path | str, text: str,
               encodings: Sequence[str] = PAGE_ENCODINGS,
               encoding: str = "utf-8") -> None:
    write_bytes(path, text.encode(encoding), encodings=encodings)


def download_encodings(path: Path | str) -> tuple[str, ...]:
    """The storage encodings for a downloaded logical `path`: none (store plain)
    for an already-compressed payload -- see ``INCOMPRESSIBLE_SUFFIXES`` -- else
    the configured download variant. Extension only, case-insensitively."""
    if logical(path).suffix.lower() in INCOMPRESSIBLE_SUFFIXES:
        return ()
    return DOWNLOAD_ENCODINGS


def write_download(path: Path | str, data: bytes | str) -> bool:
    """Persist a fetched file under the raw ``downloaded/`` tree, compressing text
    payloads and storing already-compressed ones (PDF, zip, ...) plain. `data` is
    bytes or str. The single write funnel for downloads, mirroring `write_bytes`
    for the artifact/page trees: callers pass the logical name (``foo.html``,
    ``bar.pdf``) and read it back through the compress-aware readers.

    A re-download that brings back the *same* bytes is not written: the file
    keeps its existing mtime. Two things downstream key on that mtime and would
    otherwise be thrown away for nothing -- the poppler conversion cache
    (`lib/pdftext._converted`: an entry older than its PDF is stale, and
    rebuilding one costs seconds per document) and the build's freshness
    watermarks, which fingerprint size+mtime and would re-parse the whole
    corpus. So `download --force` re-verifies every document over the network
    but only disturbs the ones that actually changed.

    Returns whether anything was written."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    if exists(path) and read_bytes(path) == data:
        return False
    write_bytes(path, data, encodings=download_encodings(path))
    return True


def unlink(path: Path | str) -> None:
    """Remove every representation of a logical `path` (plain + all variants)."""
    _clear_variants(logical(path), keep=())


def media_type(logical_path: Path | str) -> str:
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


def glob(directory: Path, pattern: str) -> set[Path]:
    """`Path.glob` over the logical names of a compressed tree: match `pattern`
    against plain files *and* their `.br`/`.gz` variants, mapping each hit back to
    its logical path (deduplicated). Callers keep their exact glob pattern (e.g.
    the one-nesting-level ``*/*.json`` that must not recurse into an archive
    subtree) and transparently see compressed artifacts. Returns a set; the caller
    sorts/filters."""
    root = Path(directory)
    return {logical(p) for suffix in ("", *SUFFIXES)
            for p in root.glob(pattern + suffix)}


def list_basefiles(root: Path | str, subdir: str) -> list[str]:
    """Every harvested basefile under `root/subdir`, read from the records.
    Compress-aware: a record stored as ``<slug>.json.br`` is enumerated and read
    transparently. Lives here (not `util`) because it walks the possibly-compressed
    download tree -- `util` is the lower layer this module builds on."""
    directory = Path(root) / subdir
    return sorted(json.loads(read_text(p))["basefile"]
                  for p in glob(directory, "*.json")
                  if not p.name.startswith("."))


def variants_on_disk(directory: str | os.PathLike[str],
                     relpath: str) -> dict[str, tuple[Path, os.stat_result]]:
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
