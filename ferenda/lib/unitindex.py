"""The unit index: every document and every anchor a citation can name, each
keyed by the hash of its own uri, with the text the citation points at. It
serves privacy mode in /api/v1/range/filter and /api/v1/range/units/{prefix}.

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
import json
import multiprocessing
import re
import sqlite3
import struct
import zlib
from pathlib import Path

import numpy as np

from . import catalog, compress, eu_structure, fusefilter, mdtext, text

STORE = "range-units.sqlite"
FILTER = "range-filter.bin"
MIN_BITS, MAX_BITS, DEFAULT_BITS = 12, 20, 16
SUFFIX_BITS = 32                  # an answer's entries: the 32 key bits after the prefix
WORKER_MEMORY = 1.5e9            # bytes a worker may need for a large consolidated act
COMMIT_EVERY = 2000
TEXT_CAP = 64 * 1024              # a longer unit is answered without text (NO_TEXT_OVER_CAP)
POPULAR = 4096                    # units listed after the filter, for filler requests
NO_TEXT = 0xFFFFFFFF              # a document whose anchors carry its text
NO_TEXT_OVER_CAP = 0xFFFFFFFE     # a unit whose text is longer than TEXT_CAP
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

def _page_markdown(art):
    """``{"sid39": markdown}``: each printed page's own text, in document order. A
    node without a page of its own is on the page of the node before it; a
    container contributes its heading, not its children's text, which follows."""
    pages = {}
    current = None
    for section in text.body_sections(art):
        stack = list(reversed(section if isinstance(section, list) else [section]))
        while stack:
            node = stack.pop()
            if not isinstance(node, dict):
                continue
            if node.get("bilaga"):
                current = None
                continue
            current = node.get("page") or current
            own = {k: v for k, v in node.items() if k != "children"}
            md = mdtext.node_markdown(own) if own.get("text") else ""
            if current and md:
                pages.setdefault("sid%d" % current, []).append(md)
            stack.extend(reversed(node.get("children") or []))
    return {page: "\n\n".join(parts) for page, parts in pages.items()}


def _eu_blocks(art):
    """An EU act's blocks in document order, each with its anchor key or None."""
    anchors = eu_structure.Anchors()
    return [(anchors.key(b.get("type"), b.get("num"), b.get("id"), b.get("depth")), b)
            for b in eu_structure.flatten(art.get("structure") or [])]


def _eu_markdown(blocks, positions, anchor):
    """The block an EU anchor names and the blocks under it ("6.1" and its points
    "6.1.a" …), since the flat block list keeps a paragraph's points apart.
    `positions` maps each key to its first block's index."""
    start = positions.get(anchor)
    if start is None:
        return ""
    parts = [blocks[start][1]]
    for k, b in blocks[start + 1:]:
        if k is not None and not k.startswith(anchor + "."):
            break
        if k is None and b.get("type") in ("heading", "article"):
            break
        parts.append(b)
    return "\n\n".join(mdtext.node_markdown(b) for b in parts)


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
    pages = _page_markdown(art) if any(a.startswith("sid") for a in anchors) else {}
    # one walk each, not one per anchor: a consolidated act has thousands
    nodes = {}
    for node in text.body_id_nodes(art):
        nodes.setdefault(node["id"], node)
    blocks = _eu_blocks(art) if art.get("structure") else []
    positions = {}
    for i, (k, _) in enumerate(blocks):
        if k is not None:
            positions.setdefault(k, i)
    for a in anchors:
        node = nodes.get(a)
        if node is not None:
            md = mdtext.node_markdown(node)
        elif a in pages:
            md = pages[a]
        elif a in positions:
            md = _eu_markdown(blocks, positions, a)
        else:
            md = text.anchor_text(art, a)
        out.append((uri + "#" + a, md or ""))
    return out


def _matches(pinpoints, anchor):
    return re.fullmatch(pinpoints, anchor) is not None


def _workers(jobs):
    """`jobs` workers, or as many as the free memory holds, whichever is fewer."""
    try:
        with open("/proc/meminfo") as f:
            free = next(int(line.split()[1]) * 1024 for line in f if line.startswith("MemAvailable:"))
    except (OSError, StopIteration):
        return max(1, jobs)
    return max(1, min(jobs, int(free // WORKER_MEMORY)))


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
            if len(raw) > TEXT_CAP:
                size, body = NO_TEXT_OVER_CAP, None
            else:
                deflate = zlib.compressobj(9, zlib.DEFLATED, -15)
                size, body = len(raw), deflate.compress(raw) + deflate.flush()
        rows.append((_stored(key(unit)), uri, unit, size, body))
    return uri, rows


# --- building ----------------------------------------------------------------

def update(catalog_path, pinpoints, jobs=1, ignore_code=False):
    """Bring the store and the filter beside `catalog_path` up to date with the
    catalog. `pinpoints` maps a source name to its `stage.Source.pinpoints`.
    A change to the code that makes the text rebuilds every unit, unless
    `ignore_code` (the run's --ignore-code-changes): then the stored units stay,
    and the old version stays recorded, so a later run rebuilds them.
    Returns ``(units, documents rewritten)``."""
    con = catalog.connect_ro(catalog_path)
    root = catalog.data_root(con)
    current = {uri: (source, path, content_hash) for uri, source, path, content_hash in
               con.execute("SELECT uri, source, path, content_hash FROM documents")}
    popular = con.execute(
        "SELECT to_uri, count(*) AS n FROM links GROUP BY to_uri ORDER BY n DESC LIMIT ?",
        (POPULAR,)).fetchall()
    con.close()
    store = sqlite3.connect(store_path(catalog_path))
    # the API reads the store while a relate updates it: WAL lets both run
    store.execute("PRAGMA journal_mode=WAL")
    store.executescript(SCHEMA)
    version = code_version()
    row = store.execute("SELECT value FROM meta WHERE key = 'code'").fetchone()
    if row and ignore_code:
        version = row[0]
    elif not row or row[0] != version:
        store.execute("DELETE FROM units")
        store.execute("DELETE FROM docs")
    stored = dict(store.execute("SELECT uri, content_hash FROM docs"))
    stale = [uri for uri, (_, _, h) in current.items() if stored.get(uri, "") != (h or "")]
    gone = [uri for uri in stored if uri not in current]
    jobs_list = [(uri, str(root / current[uri][1]) if current[uri][1] else None,
                  pinpoints.get(current[uri][0])) for uri in stale]
    with store:
        for uri in gone:
            store.execute("DELETE FROM units WHERE doc = ?", (uri,))
            store.execute("DELETE FROM docs WHERE uri = ?", (uri,))
        store.execute("INSERT OR REPLACE INTO meta VALUES ('code', ?)", (version,))
    # a few workers, each restarted now and then: a consolidated act's artifact
    # is hundreds of megabytes in memory. A document's units and its `docs` row
    # commit together every COMMIT_EVERY documents, so a stopped build resumes.
    workers = _workers(jobs)
    pool = (multiprocessing.Pool(workers, maxtasksperchild=200)
            if workers > 1 and len(jobs_list) > 100 else None)
    results = pool.imap_unordered(_rows, jobs_list, chunksize=16) if pool else map(_rows, jobs_list)
    try:
        for n, (uri, rows) in enumerate(results, 1):
            store.execute("DELETE FROM units WHERE doc = ?", (uri,))
            store.executemany("INSERT OR REPLACE INTO units VALUES (?, ?, ?, ?, ?)", rows)
            store.execute("INSERT OR REPLACE INTO docs VALUES (?, ?)",
                          (uri, current[uri][2] or ""))
            if n % COMMIT_EVERY == 0:
                store.commit()
        store.commit()
    finally:
        if pool:
            pool.terminate()
    total = store.execute("SELECT count(*) FROM units").fetchone()[0]
    if stale or gone or not filter_path(catalog_path).exists():
        _write_meta(store)
        _write_filter(store, catalog_path, popular)
    store.close()
    return total, len(stale) + len(gone)


def _write_meta(store):
    """Each prefix length's padding: the largest entry count, and the size an
    answer with text pads to -- the largest answer's, so every answer of a prefix
    length has one size. The 64 kB cap on a unit's text keeps that small: the
    largest bucket was 1.1 (12 bits) to 1.7 (16 bits) times the median, measured
    on the corpus on 2026-09-28."""
    counts = np.zeros(1 << MAX_BITS, dtype=np.int64)
    sizes = np.zeros(1 << MAX_BITS, dtype=np.int64)
    shift = 64 - MAX_BITS
    for k, uri, body in store.execute("SELECT key, uri, body FROM units"):
        b = (k + (1 << 63)) >> shift
        counts[b] += 1
        sizes[b] += _ENTRY.size + len(uri.encode()) + (len(body) if body else 0)
    padding = {}
    for bits in range(MIN_BITS, MAX_BITS + 1):
        group = 1 << (MAX_BITS - bits)
        c = counts.reshape(-1, group).sum(axis=1)
        s = sizes.reshape(-1, group).sum(axis=1) + len(ANSWER_MAGIC) + 5
        padding[bits] = {"entries": int(c.max()), "bytes": int(s.max())}
    with store:
        store.execute("INSERT OR REPLACE INTO meta VALUES ('padding', ?)", (json.dumps(padding),))


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

def _padding(con):
    row = con.execute("SELECT value FROM meta WHERE key = 'padding'").fetchone()
    return {int(bits): v for bits, v in json.loads(row[0]).items()}


def _range(prefix, bits):
    lo = prefix << (64 - bits)
    return _stored(lo), _stored(lo + (1 << (64 - bits)) - 1)


def _suffix(stored, bits):
    return ((stored + (1 << 63)) >> (64 - bits - SUFFIX_BITS)) & 0xFFFFFFFF


def entries(catalog_path, prefix, bits):
    """The answer without text: each unit's 32 key bits after the prefix, as
    8-hex lines, sorted and made up to the largest bucket of this prefix length
    with stand-ins that change only when the bucket does."""
    con = sqlite3.connect("file:%s?mode=ro" % store_path(catalog_path), uri=True)
    capacity = _padding(con)[bits]["entries"]
    lo, hi = _range(prefix, bits)
    real = {_suffix(k, bits) for (k,) in con.execute(
        "SELECT key FROM units WHERE key BETWEEN ? AND ?", (lo, hi))}
    con.close()
    out = set(real)
    seed = hashlib.sha256(b"%d/%d\n" % (bits, prefix) + b"".join(
        s.to_bytes(4, "big") for s in sorted(real))).digest()
    while len(out) < capacity:
        seed = hashlib.sha256(seed).digest()
        out.add(int.from_bytes(seed[:4], "big"))
    return "".join("%08x\n" % s for s in sorted(out))


def content(catalog_path, prefix, bits):
    """The answer with text, as bytes: `ANSWER_MAGIC`, the prefix length, the
    unit count, then per unit its key suffix, uri and text (raw deflate, or a
    NO_TEXT marker), zero-padded to the size of this prefix length's largest
    answer."""
    con = sqlite3.connect("file:%s?mode=ro" % store_path(catalog_path), uri=True)
    target = _padding(con)[bits]["bytes"]
    lo, hi = _range(prefix, bits)
    rows = con.execute("SELECT key, uri, size, body FROM units WHERE key BETWEEN ? AND ? "
                       "ORDER BY key", (lo, hi)).fetchall()
    con.close()
    parts = [ANSWER_MAGIC, struct.pack("<BI", bits, len(rows))]
    for k, uri, size, body in rows:
        u = uri.encode()
        length = size if size in (NO_TEXT, NO_TEXT_OVER_CAP) else len(body)
        parts.append(_ENTRY.pack(_suffix(k, bits), len(u), length) + u + (body or b""))
    data = b"".join(parts)
    return data + bytes(max(0, target - len(data)))
