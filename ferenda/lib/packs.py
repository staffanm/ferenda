"""Pack generation and caching for Slopcheck privacy mode: `/api/v1/packs/{pack_id}`.

Static bundles of legal documents in their native JSON artifact format. Documents
are packaged deterministically by corpus segment so clients can fetch entire
decades, years, or volumes without disclosing specific citations:

  * `core`: the top 250 cited documents across all sources in catalog.sqlite;
  * `sfs/{decade}s`: statutes by decade (`sfs/1990s`);
  * `celex/1`: EU primary treaties;
  * `celex/{sector}/{year}`: EU secondary legislation or case law (`celex/3/2016`, `celex/6/2019`);
  * `dom/{court}/{5yr_block}`: court decisions in 5-year blocks (`dom/nja/2020-2024`, `dom/echr/2020-2024`);
  * `{forarbete_kind}/{year}`: preparatory works by year (`prop/1997`, `sou/1997`, `ds/2024`).

Packs are stored as a compressed JSON artifact map:
`{"pack": "<pack_id>", "documents": {"<uri>": { ...artifact... }}}`.
Packs are stored on disk as Brotli-compressed `.json.br` under `cache/packs/` and
served with `Content-Encoding: br`.
"""

import json
import re
import sqlite3
from pathlib import Path

from . import catalog, compress
from .util import write_atomic

SAFE_PACK_ID = re.compile(r"^[a-zA-Z0-9_-]+(?:/[a-zA-Z0-9_-]+)*$")
FORARBETE_KINDS = frozenset({"prop", "sou", "ds", "bet", "dir", "so", "lr", "pm", "skr", "fm"})
CORE_LIMIT = 250


def pack_path(pack_id: str, cache_dir: Path) -> Path:
    """The filesystem path for a cached pack: ``<cache_dir>/packs/<pack_id>.json.br``."""
    clean = "/".join(p for p in pack_id.split("/") if p and p != "..")
    return cache_dir / f"{clean}.json.br"


def resolve_pack_documents(con: sqlite3.Connection, pack_id: str) -> list[tuple[str, str]]:
    """``[(uri, relpath), ...]`` for a given `pack_id`, querying `catalog.sqlite`.

    Returns an empty list when `pack_id` matches no supported pattern or no documents.
    """
    if not SAFE_PACK_ID.match(pack_id):
        return []

    # 1. core: top cited documents across the whole corpus
    if pack_id == "core":
        return con.execute(
            "SELECT uri, path FROM documents WHERE inbound_count IS NOT NULL "
            "ORDER BY inbound_count DESC LIMIT ?",
            (CORE_LIMIT,),
        ).fetchall()

    # 2. sfs/{decade}s (e.g. sfs/1990s)
    m = re.fullmatch(r"sfs/(\d{3}0)s", pack_id)
    if m:
        start = int(m.group(1))
        return con.execute(
            "SELECT uri, path FROM documents WHERE source = 'sfs' "
            "AND substr(uri, 18, 4) BETWEEN ? AND ? ORDER BY uri",
            (str(start), str(start + 9)),
        ).fetchall()

    # 3. celex/1 (EU primary treaties)
    if pack_id == "celex/1":
        return con.execute(
            "SELECT uri, path FROM documents WHERE source = 'eurlex' "
            "AND substr(uri, 24, 1) = '1' ORDER BY uri"
        ).fetchall()

    # 4. celex/{sector}/{year} (e.g. celex/3/2016, celex/6/2019)
    m = re.fullmatch(r"celex/([0-9])/(\d{4})", pack_id)
    if m:
        sector, year = m.group(1), m.group(2)
        return con.execute(
            "SELECT uri, path FROM documents WHERE source = 'eurlex' "
            "AND substr(uri, 24, 1) = ? AND substr(uri, 25, 4) = ? ORDER BY uri",
            (sector, year),
        ).fetchall()

    # 5. dom/{court}/{start}-{end} or {court}/{start}-{end} (e.g. dom/nja/2020-2024)
    m = re.fullmatch(r"(?:dom/)?([a-z0-9_-]+)/(\d{4})-(\d{4})", pack_id)
    if m:
        court, start, end = m.group(1), m.group(2), m.group(3)
        return con.execute(
            "SELECT uri, path FROM documents WHERE source IN ('dv', 'hudoc') "
            "AND uri LIKE 'https://lagen.nu/dom/' || ? || '/%' "
            "AND substr(date, 1, 4) BETWEEN ? AND ? ORDER BY date, uri",
            (court, start, end),
        ).fetchall()

    # 6. förarbeten: prop/{year} or prop/{session} (e.g. prop/1997, prop/1997-98, prop/1997/98)
    m = re.fullmatch(r"prop/(\d{4}(?:[-/]\d{2,4})?)", pack_id)
    if m:
        val = m.group(1)
        if "-" in val or "/" in val:
            session = val.replace("-", "/")
            return con.execute(
                "SELECT uri, path FROM documents WHERE source = 'forarbete' AND kind = 'prop' "
                "AND uri LIKE 'https://lagen.nu/prop/' || ? || ':%' ORDER BY uri",
                (session,),
            ).fetchall()
        return con.execute(
            "SELECT uri, path FROM documents WHERE source = 'forarbete' AND kind = 'prop' "
            "AND (uri LIKE 'https://lagen.nu/prop/' || ? || ':%' "
            "     OR uri LIKE 'https://lagen.nu/prop/' || ? || '/%') ORDER BY uri",
            (val, val),
        ).fetchall()

    # 7. other förarbeten kinds: {kind}/{year} (e.g. sou/1997, ds/2024, bet/1971)
    m = re.fullmatch(r"([a-z]+)/(\d{4})", pack_id)
    if m and m.group(1) in FORARBETE_KINDS:
        kind, year = m.group(1), m.group(2)
        return con.execute(
            "SELECT uri, path FROM documents WHERE source = 'forarbete' AND kind = ? "
            "AND (uri LIKE 'https://lagen.nu/' || ? || '/' || ? || ':%' "
            "     OR substr(date, 1, 4) = ?) ORDER BY uri",
            (kind, kind, year, year),
        ).fetchall()

    return []


def assemble_pack(con: sqlite3.Connection, data_root: Path, pack_id: str) -> dict | None:
    """Read all artifacts belonging to `pack_id` and assemble the pack dictionary.

    Returns ``{"pack": pack_id, "documents": {uri: artifact}}``, or None if empty.
    """
    rows = resolve_pack_documents(con, pack_id)
    if not rows:
        return None
    documents = {}
    for uri, relpath in rows:
        art = compress.read_json(data_root / relpath, empty=None)
        if art is not None:
            documents[uri] = art
    if not documents:
        return None
    return {"pack": pack_id, "documents": documents}


def write_pack_cache(pack_data: dict, dest: Path) -> None:
    """Serialize `pack_data` as compact JSON and save as a Brotli `.json.br` file."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(pack_data, separators=(",", ":")).encode("utf-8")
    compressed = compress.compress_bytes(body, "br", quality=4)
    write_atomic(dest, compressed)


def get_cached_pack(catalog_path: Path, data_root: Path, cache_dir: Path, pack_id: str) -> Path | None:
    """Retrieve the cached pack file for `pack_id`, building and caching it if absent.

    Returns the `Path` to the `.json.br` file, or None if `pack_id` is invalid or empty.
    """
    dest = pack_path(pack_id, cache_dir)
    if dest.exists():
        return dest
    if not Path(catalog_path).exists():
        return None
    con = catalog.connect_ro(catalog_path)
    try:
        pack_data = assemble_pack(con, data_root, pack_id)
    finally:
        con.close()
    if pack_data is None:
        return None
    write_pack_cache(pack_data, dest)
    return dest
