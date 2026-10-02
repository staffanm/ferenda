"""The unit index: every document and every anchor a citation can name, each
keyed by the hash of its own uri, with the text the citation points at. It
serves privacy mode in /api/v1/range/filter and /api/v1/range/{prefix}.

A *unit* is a document uri ("https://lagen.nu/1915:218") or an anchor uri
("https://lagen.nu/1915:218#P3"). Its key is the first 8 bytes of sha256(uri),
read as a big-endian integer. Two things are built from the units:

* the filter (`range-filter.bin`): a binary fuse filter over every key
  (lib/fusefilter), which a client downloads whole and asks locally whether a
  citation's document and anchor exist -- so existence costs no request that
  names a citation. After the filter, the file lists the most-cited units'
  first 32 bits and their citation counts, for the client's filler requests.
* the store (`range-units.sqlite`): one row per unit, sorted by key, with the
  unit's text as markdown. A client asks for the units whose key starts with a
  prefix of `bits` bits (the caller chooses the length), and gets the text of
  each: an anchor's own text, the whole text of a document that has no anchors
  (a judgment), and no text for a document with anchors -- its units carry it.

The store follows the catalog by content hash, so a relate that changed a
hundred documents rewrites their units and nothing else. A change to the code
that makes the text (this module, mdtext, text, eu_structure) rebuilds it."""

import hashlib
import multiprocessing
import re
import sqlite3
import struct
import zlib
from pathlib import Path

import numpy as np

from . import catalog, compress, fusefilter, mdtext, sqlcache, text, util

STORE = "range-units.sqlite"
FILTER = "range-filter.bin"
MIN_BITS, MAX_BITS, DEFAULT_BITS = 12, 20, 16
SUFFIX_BITS = 32                  # an answer's entries: the 32 key bits after the prefix
WORKER_MEMORY = 1.5e9            # bytes a worker may need for a large consolidated act
COMMIT_EVERY = 2000
POPULAR = 4096                    # units listed after the filter, for filler requests
NO_TEXT = 0xFFFFFFFF              # a document whose anchors carry its text
ANSWER_MAGIC = b"LUR1"
_ENTRY = struct.Struct("<IHI")    # key suffix, uri length, text length (or a NO_TEXT marker)
_CODE = [Path(__file__).parent / name for name in
         ("unitindex.py", "mdtext.py", "text.py", "eu_structure.py")]

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (uri TEXT PRIMARY KEY, content_hash TEXT);
CREATE TABLE IF NOT EXISTS units (key INTEGER PRIMARY KEY, doc TEXT NOT NULL,
                                  uri TEXT NOT NULL, size INTEGER NOT NULL, body BLOB);
CREATE INDEX IF NOT EXISTS units_doc ON units(doc);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def key(uri):
    """A unit's key: the first 8 bytes of sha256(uri), big-endian, unsigned."""
    return int.from_bytes(hashlib.sha256(uri.encode()).digest()[:8], "big")


def _stored(k):
    """SQLite's integers are signed: shift so the stored order is the key order."""
    return k - (1 << 63)


def store_path(catalog_path):
    return Path(catalog_path).with_name(STORE)


def filter_path(catalog_path):
    return Path(catalog_path).with_name(FILTER)


def code_version():
    return hashlib.sha256(b"".join(p.read_bytes() for p in _CODE)).hexdigest()[:16]


# --- the text of a unit ------------------------------------------------------

def units_of(art, pinpoints):
    """``[(unit uri, markdown or None)]`` for one artifact: the document, and the
    anchors of it the range index publishes. The document carries its own text
    only when it has no anchors."""
    uri = art["uri"]
    anchors = sorted(text.citable_anchors(art)) if pinpoints else []
    anchors = [a for a in anchors if _matches(pinpoints, a)]
    if not anchors:
        return [(uri, mdtext.document_markdown(art))]
    out = [(uri, None)]
    # one walk each, not one per anchor: a consolidated act has thousands
    pages = text.page_nodes(art) if any(a.startswith("sid") for a in anchors) else {}
    nodes = {}
    for node in text.body_id_nodes(art):
        nodes.setdefault(node["id"], node)
    eu = text.eu_units(art) if art.get("structure") else {}
    for a in anchors:
        node = nodes.get(a)
        if node is not None:
            parts = [node]
        elif a.startswith("sid") and int(a[3:]) in pages:
            parts = pages[int(a[3:])]
        else:
            parts = eu.get(a) or []
        out.append((uri + "#" + a, mdtext.nodes_markdown(parts)))
    return out


def _matches(pinpoints, anchor):
    return re.fullmatch(pinpoints, anchor) is not None


def _workers(jobs):
    """`jobs` workers, or as many as the free memory holds, whichever is fewer."""
    return max(1, min(jobs, int(sqlcache.meminfo("MemAvailable") // WORKER_MEMORY)))


def _rows(job):
    """The store rows of one document (runs in a worker process)."""
    uri, path, pinpoints = job
    art = compress.read_json(path, empty=None) if path else None
    units = units_of(art, pinpoints) if art else [(uri, None)]
    rows = []
    for unit, md in units:
        if md is None:
            size, body = NO_TEXT, None
        else:
            raw = md.encode()
            deflate = zlib.compressobj(9, zlib.DEFLATED, -15)
            size, body = len(raw), deflate.compress(raw) + deflate.flush()
        rows.append((_stored(key(unit)), uri, unit, size, body))
    return uri, rows


# --- building ----------------------------------------------------------------

def begin(catalog_path):
    """Mark the store before a relate writes the catalog, and say whether the
    mark was already there. The documents a relate writes are named to
    `update` from memory: a run that stops between the catalog's commit and
    `update` leaves them unnamed, and the next run finds their catalog rows
    current. A mark left over from such a run is what tells the next one to
    compare every document instead. `update` removes it."""
    path = store_path(catalog_path)
    if not path.exists():
        return False          # no store: `update` builds it whole anyway
    store = sqlite3.connect(path)
    with store:
        left = store.execute("SELECT 1 FROM meta WHERE key = 'pending'").fetchone()
        store.execute("INSERT OR REPLACE INTO meta VALUES ('pending', '1')")
    store.close()
    return left is not None


def update(catalog_path, pinpoints, jobs=1, ignore_code=False, changed=None):
    """Bring the store and the filter beside `catalog_path` up to date with the
    catalog. `pinpoints` maps a source name to its `stage.Source.pinpoints`.
    `changed` is the set of document uris the relate wrote or dropped
    (`catalog.rebuild`, the concept stubs); only those are compared with the
    store. None compares every document instead -- the whole catalog and the
    whole store read, 2012 s on production's disk for a run that rewrote
    nothing (2026-09-29) -- and so does a store that predates the stored unit
    count. The caller passes None after a relate that stopped part way
    (`begin`). A change to the code that
    makes the text rebuilds every unit, unless `ignore_code` (the run's
    --ignore-code-changes): then the stored units stay, and the old version
    stays recorded, so a later run rebuilds them.
    Returns ``(units, documents rewritten)``."""
    store = sqlite3.connect(store_path(catalog_path))
    # the API reads the store while a relate updates it: WAL lets both run
    store.execute("PRAGMA journal_mode=WAL")
    # no fsync at a checkpoint: a crash loses at most the last commits, and
    # the 'pending' mark already makes the next run compare every document
    store.execute("PRAGMA synchronous=NORMAL")
    store.executescript(SCHEMA)
    meta = dict(store.execute("SELECT key, value FROM meta"))
    version = code_version()
    if "code" in meta and ignore_code:
        version = meta["code"]
    elif meta.get("code") != version:
        store.execute("DELETE FROM units")
        store.execute("DELETE FROM docs")
        changed = None
    if "units" not in meta:
        changed = None
    if changed is not None and not changed and filter_path(catalog_path).exists():
        with store:
            store.execute("DELETE FROM meta WHERE key = 'pending'")
        store.close()
        return int(meta["units"]), 0
    con = catalog.connect_ro(catalog_path)
    root = catalog.data_root(con)
    sql = "SELECT uri, source, path, content_hash FROM documents"
    rows = (con.execute(sql) if changed is None
            else util.select_in(con, sql + " WHERE uri IN (%s)", changed))
    current = {uri: (source, path, content_hash) for uri, source, path, content_hash in rows}
    sql = "SELECT uri, content_hash FROM docs"
    stored = dict(store.execute(sql) if changed is None
                  else util.select_in(store, sql + " WHERE uri IN (%s)", changed))
    stale = [uri for uri, (_, _, h) in current.items() if stored.get(uri, "") != (h or "")]
    gone = [uri for uri in stored if uri not in current]
    rewrite_filter = bool(stale or gone) or not filter_path(catalog_path).exists()
    jobs_list = [(uri, str(root / current[uri][1]) if current[uri][1] else None,
                  pinpoints.get(current[uri][0])) for uri in stale]
    total = int(meta.get("units", 0))
    with store:
        # cleared at the end: an update that stops before then leaves units
        # the next caller's `changed` does not name, so `begin` tells that
        # run to compare them all
        store.execute("INSERT OR REPLACE INTO meta VALUES ('pending', '1')")
        for uri in gone:
            total -= store.execute("DELETE FROM units WHERE doc = ?", (uri,)).rowcount
            store.execute("DELETE FROM docs WHERE uri = ?", (uri,))
        store.execute("INSERT OR REPLACE INTO meta VALUES ('code', ?)", (version,))
    # a few workers, each restarted now and then: a consolidated act's artifact
    # is hundreds of megabytes in memory. A document's units and its `docs` row
    # commit together every COMMIT_EVERY documents, so a stopped build resumes.
    workers = _workers(jobs)
    # a document's units sit on unrelated pages (the key is a hash): the batch
    # cache keeps the units_doc index and the inner pages between documents,
    # in the memory the workers leave
    reserve = workers * WORKER_MEMORY
    sqlcache.batch_cache(store, store_path(catalog_path), reserve)
    search_hours = sqlcache.search_hours()
    pool = (multiprocessing.Pool(workers, maxtasksperchild=200)
            if workers > 1 and len(jobs_list) > 100 else None)
    results = pool.imap_unordered(_rows, jobs_list, chunksize=16) if pool else map(_rows, jobs_list)
    try:
        for n, (uri, rows) in enumerate(results, 1):
            # a document can name one anchor twice; the store keeps one row.
            # Only the rows that differ are written: a changed document mostly
            # keeps its units' text, and every write is a random page in the
            # store, so rewriting identical rows was most of an update's I/O
            fresh = {row[0]: row for row in rows}
            old = {row[0]: row for row in store.execute(
                "SELECT key, doc, uri, size, body FROM units WHERE doc = ?", (uri,))}
            store.executemany("DELETE FROM units WHERE key = ?",
                              [(k,) for k in old.keys() - fresh.keys()])
            store.executemany("INSERT OR REPLACE INTO units VALUES (?, ?, ?, ?, ?)",
                              [row for k, row in fresh.items() if old.get(k) != row])
            total += len(fresh) - len(old)
            store.execute("INSERT OR REPLACE INTO docs VALUES (?, ?)",
                          (uri, current[uri][2] or ""))
            if n % COMMIT_EVERY == 0:
                store.commit()
                # an update can run for hours: into business hours the cache
                # gives memory back to the OpenSearch index, and takes it again after
                if (now := sqlcache.search_hours()) != search_hours:
                    search_hours = now
                    sqlcache.batch_cache(store, store_path(catalog_path), reserve)
        store.commit()
    finally:
        if pool:
            pool.terminate()
    if changed is None:
        total = store.execute("SELECT count(*) FROM units").fetchone()[0]
    if rewrite_filter:
        # the most-cited units ride along in the filter file, so they are read
        # only when it is rewritten: a GROUP BY over every link in the catalog
        popular = con.execute(
            "SELECT to_uri, count(*) AS n FROM links GROUP BY to_uri ORDER BY n DESC LIMIT ?",
            (POPULAR,)).fetchall()
        _write_filter(store, catalog_path, popular)
    con.close()
    with store:
        store.execute("INSERT OR REPLACE INTO meta VALUES ('units', ?)", (str(total),))
        store.execute("DELETE FROM meta WHERE key = 'pending'")
    store.close()
    return total, len(stale) + len(gone)


def _write_filter(store, catalog_path, popular):
    keys = np.fromiter(((k + (1 << 63)) for (k,) in store.execute("SELECT key FROM units")),
                       dtype=np.uint64)
    tail = struct.pack("<I", len(popular)) + b"".join(
        struct.pack("<II", key(uri) >> 32, n) for uri, n in popular)
    out = filter_path(catalog_path)
    tmp = out.with_suffix(".tmp")
    tmp.write_bytes(fusefilter.build(keys) + tail)
    tmp.replace(out)                      # atomic: readers see the old file or the new


# --- answering ---------------------------------------------------------------

def _range(prefix, bits):
    lo = prefix << (64 - bits)
    return _stored(lo), _stored(lo + (1 << (64 - bits)) - 1)


def _suffix(stored, bits):
    return ((stored + (1 << 63)) >> (64 - bits - SUFFIX_BITS)) & 0xFFFFFFFF


def content(catalog_path, prefix, bits):
    """The answer, as bytes: `ANSWER_MAGIC`, the prefix length, the unit count,
    then per unit its key suffix, uri and text (raw deflate, or the NO_TEXT
    marker). The server knows the prefix it answers, so the answer is not padded:
    padding would hide its size only from a party that sees the traffic but not
    the request."""
    con = sqlite3.connect("file:%s?mode=ro" % store_path(catalog_path), uri=True)
    lo, hi = _range(prefix, bits)
    rows = con.execute("SELECT key, uri, size, body FROM units WHERE key BETWEEN ? AND ? "
                       "ORDER BY key", (lo, hi)).fetchall()
    con.close()
    parts = [ANSWER_MAGIC, struct.pack("<BI", bits, len(rows))]
    for k, uri, size, body in rows:
        u = uri.encode()
        length = size if size == NO_TEXT else len(body)
        parts.append(_ENTRY.pack(_suffix(k, bits), len(u), length) + u + (body or b""))
    return b"".join(parts)
