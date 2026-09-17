"""The range index: which documents and provisions the corpus holds, published
so that a client can check a citation without saying which one.

A client hashes the citation's document uri, sends the first `PREFIX_HEX` hex
characters, and gets back every hash this corpus holds for the ~100 documents
that share them -- each document's own and one per anchor a citation can name
(`text.citable_anchors`, narrowed by the source's `pinpoints`). It looks for its citation in the answer itself. We
learn one bucket of `BUCKETS`; the prefix comes from the *document* uri so that
the document and its provisions share a bucket, and one request answers both
"is this statute held" and "does it have a 3 a §".

    prefix = sha256(document uri).hexdigest()[:PREFIX_HEX]
    entry  = sha256(document uri [+ "#" + anchor]).digest()[:ENTRY_BYTES]

Uris are hashed exactly as the catalog stores them -- no case folding: 233,907
of 437,347 document uris carry upper case, and four pairs differ in case alone.

Two stores. Relate writes one `range_anchors` row per document beside its links
(`catalog._index_document`), so the hashes are incremental like everything else
there. `write_sidecar` then folds the rows into `range-index.bin` beside the
catalog, which is what serves: one bucket is one contiguous read, where the
rows behind it are ~100 scattered ones.

Every answer has the same length, `capacity` entries: a bucket's size would
otherwise name it. `fill` makes up the difference with entries that look like
the real ones and are fixed per bucket, so asking twice shows nothing either. A
fill entry matches a citation with probability 2^-64.
"""

import hashlib
import itertools
import re
import struct
from pathlib import Path

from . import text

PREFIX_HEX = 3
BUCKETS = 16 ** PREFIX_HEX
ENTRY_BYTES = 8

SIDECAR = "range-index.bin"
# bumped when the file layout or the hashing changes: `bucket` refuses an
# older sidecar rather than answer from it
_MAGIC = b"lagen-rangeindex-1\n"
_HEADER = struct.Struct("<II")               # capacity, entries
_OFFSET = struct.Struct("<I")


def entry(uri):
    """The published hash of one document or fragment uri."""
    return hashlib.sha256(uri.encode()).digest()[:ENTRY_BYTES]


def prefix(uri):
    """The bucket a *document* uri falls in, as an int."""
    return int(hashlib.sha256(uri.encode()).hexdigest()[:PREFIX_HEX], 16)


def anchor_hashes(art, pinpoints):
    """One document's `range_anchors` blob: the entry of every anchor a citation
    can name, sorted and concatenated. `pinpoints` is the source's own statement
    of which those are (`stage.Source.pinpoints`), a regex the whole anchor must
    match; None publishes no anchor. The document's own entry is not in the
    blob -- it is derivable from the uri, and a catalog row without an artifact
    (a begrepp stub) has no blob at all."""
    if pinpoints is None:
        return b""
    return b"".join(sorted(entry(art["uri"] + "#" + anchor)
                           for anchor in text.citable_anchors(art)
                           if re.fullmatch(pinpoints, anchor)))


def sidecar_path(catalog_path):
    return Path(catalog_path).with_name(SIDECAR)


def write_sidecar(con, catalog_path):
    """Fold the catalog behind `con` into the sidecar beside `catalog_path`:
    `(documents, entries, capacity)`. The caller owns the connection -- the
    catalog imports this module for `anchor_hashes`, so this one cannot import
    it back.

    Two sequential scans, merged here. The join that says the same thing is one
    index probe per document, and on production's disk that is the difference
    between seconds and an hour (see lib/pathgraph)."""
    # raw bytes per bucket, cut into entries one bucket at a time: 15 million
    # 8-byte objects held at once are over a gigabyte, the bytes are 120 MB
    raw = [bytearray() for _ in range(BUCKETS)]
    documents = 0
    for (uri,) in con.execute("SELECT uri FROM documents"):
        raw[prefix(uri)] += entry(uri)
        documents += 1
    for uri, blob in con.execute("SELECT uri, hashes FROM range_anchors"):
        raw[prefix(uri)] += blob
    buckets = [b"".join(sorted(_entries(data))) for data in raw]
    sizes = [len(data) // ENTRY_BYTES for data in buckets]
    out = sidecar_path(catalog_path)
    tmp = out.with_suffix(".tmp")
    with open(tmp, "wb") as f:
        f.write(_MAGIC)
        f.write(_HEADER.pack(max(sizes), sum(sizes)))
        for start in itertools.accumulate(sizes, initial=0):   # BUCKETS + 1, in entries
            f.write(_OFFSET.pack(start))
        f.write(b"".join(buckets))
    tmp.replace(out)                         # atomic: readers see old or new
    return documents, sum(sizes), max(sizes)


def _entries(data):
    """The distinct entries in a run of concatenated ones."""
    return {bytes(data[i:i + ENTRY_BYTES]) for i in range(0, len(data), ENTRY_BYTES)}


def bucket(catalog_path, number):
    """``(entries, capacity)`` for bucket `number`: its sorted entries, and the
    size of the largest bucket in the index."""
    with open(sidecar_path(catalog_path), "rb") as f:
        assert f.read(len(_MAGIC)) == _MAGIC, \
            "%s was written in another layout -- run relate" % sidecar_path(catalog_path)
        capacity, _total = _HEADER.unpack(f.read(_HEADER.size))
        table = len(_MAGIC) + _HEADER.size
        f.seek(table + number * _OFFSET.size)
        start, end = struct.unpack("<II", f.read(2 * _OFFSET.size))
        f.seek(table + (BUCKETS + 1) * _OFFSET.size + start * ENTRY_BYTES)
        data = f.read((end - start) * ENTRY_BYTES)
    return sorted(_entries(data)), capacity


def fill(number, entries, capacity):
    """`entries` made up to `capacity` with stand-ins, sorted. The stand-ins are
    a hash chain seeded by the bucket and its real content, so they change only
    when the bucket does."""
    out = set(entries)
    seed = hashlib.sha256(b"%d\n" % number + b"".join(entries)).digest()
    while len(out) < capacity:
        seed = hashlib.sha256(seed).digest()
        out.add(seed[:ENTRY_BYTES])
    return sorted(out)
