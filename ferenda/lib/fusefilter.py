"""A binary fuse filter with 16-bit fingerprints: a set of 64-bit keys in about
18 bits a key, answering "is this key in the set" with one false yes in 65 536.

The layout and the hashing are those of `BinaryFuse16` in Graf and Lemire's
reference implementation (github.com/FastFilter/xor_singleheader), so a client
reads the file with a direct port of its `contain`: three array reads and two
xors, no decompression. Built here by peeling, with numpy for the hashing and a
plain loop for the peeling itself (about a minute for seven million keys).

The file is `MAGIC`, the header (seed, segment length, segment count length,
array length), then the fingerprints as little-endian uint16."""

import math
import struct

import numpy as np

MAGIC = b"lagen-fuse16-1\n"
HEADER = struct.Struct("<QIII")       # seed, segment length, segment count length, array length
ARITY = 3
MASK64 = (1 << 64) - 1


def _murmur64(h):
    h ^= h >> np.uint64(33)
    h *= np.uint64(0xFF51AFD7ED558CCD)
    h ^= h >> np.uint64(33)
    h *= np.uint64(0xC4CEB9FE1A85EC53)
    h ^= h >> np.uint64(33)
    return h


def _splitmix64(state):
    """The next seed and the new state, as the reference `binary_fuse_rng_splitmix64`."""
    state = (state + 0x9E3779B97F4A7C15) & MASK64
    z = state
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & MASK64
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & MASK64
    return z ^ (z >> 31), state


def _sizes(size):
    """(segment length, segment count length, array length) for `size` keys."""
    if size <= 1:
        segment_length = 4
    else:
        segment_length = min(1 << int(math.floor(math.log(size) / math.log(3.33) + 2.25)), 262144)
    factor = 0 if size <= 1 else max(1.125, 0.875 + 0.25 * math.log(1_000_000) / math.log(size))
    capacity = 0 if size <= 1 else int(round(size * factor))
    init_segments = (capacity + segment_length - 1) // segment_length - (ARITY - 1)
    array_length = (init_segments + ARITY - 1) * segment_length
    segment_count = (array_length + segment_length - 1) // segment_length
    segment_count = 1 if segment_count <= ARITY - 1 else segment_count - (ARITY - 1)
    array_length = (segment_count + ARITY - 1) * segment_length
    return segment_length, segment_count * segment_length, array_length


def _locations(hashes, segment_length, segment_count_length):
    """The three array positions of each (mixed) hash, as `binary_fuse16_hash_batch`."""
    n = np.uint64(segment_count_length)
    hi = hashes >> np.uint64(32)
    lo = hashes & np.uint64(0xFFFFFFFF)
    h0 = (hi * n + ((lo * n) >> np.uint64(32))) >> np.uint64(32)      # mulhi(hash, n)
    mask = np.uint64(segment_length - 1)
    h1 = (h0 + np.uint64(segment_length)) ^ ((hashes >> np.uint64(18)) & mask)
    h2 = (h0 + np.uint64(2 * segment_length)) ^ (hashes & mask)
    return h0.astype(np.int64), h1.astype(np.int64), h2.astype(np.int64)


def _fingerprints(hashes):
    return ((hashes ^ (hashes >> np.uint64(32))) & np.uint64(0xFFFF)).astype(np.uint16)


def build(keys):
    """The filter over `keys` (uint64, duplicates allowed) as file bytes."""
    keys = np.unique(np.asarray(keys, dtype=np.uint64))
    segment_length, segment_count_length, array_length = _sizes(len(keys))
    state = 0x726B2B9D438B9D4D
    for _ in range(100):
        seed, state = _splitmix64(state)
        fingerprints = _populate(keys, seed, segment_length, segment_count_length, array_length)
        if fingerprints is not None:
            return (MAGIC + HEADER.pack(seed, segment_length, segment_count_length, array_length)
                    + fingerprints.astype("<u2").tobytes())
    raise RuntimeError("binary fuse filter: no seed peeled %d keys" % len(keys))


def _populate(keys, seed, segment_length, segment_count_length, array_length):
    """The fingerprint array for one seed, or None when the keys do not peel."""
    hashes = _murmur64(keys + np.uint64(seed))
    h0, h1, h2 = _locations(hashes, segment_length, segment_count_length)
    count = np.bincount(np.concatenate((h0, h1, h2)), minlength=array_length).tolist()
    # xor of the key indices at each position: a position with count 1 names its key
    xor_index = np.zeros(array_length, dtype=np.int64)
    idx = np.arange(len(keys), dtype=np.int64)
    for loc in (h0, h1, h2):
        np.bitwise_xor.at(xor_index, loc, idx)
    xor_index = xor_index.tolist()
    locs = (h0.tolist(), h1.tolist(), h2.tolist())
    stack = []                          # (key index, the position it was peeled from)
    queue = [p for p in range(array_length) if count[p] == 1]
    while queue:
        p = queue.pop()
        if count[p] != 1:
            continue
        k = xor_index[p]
        stack.append((k, p))
        for loc in locs:
            q = loc[k]
            count[q] -= 1
            xor_index[q] ^= k
            if count[q] == 1:
                queue.append(q)
    if len(stack) != len(keys):
        return None
    prints = _fingerprints(hashes).tolist()
    out = [0] * array_length
    for k, p in reversed(stack):
        a, b, c = locs[0][k], locs[1][k], locs[2][k]
        value = prints[k]
        for q in (a, b, c):
            if q != p:
                value ^= out[q]
        out[p] = value
    return np.array(out, dtype=np.uint16)


class Filter:
    """A built filter, read back: `key in filter` for a uint64 key."""

    def __init__(self, data):
        assert data[:len(MAGIC)] == MAGIC, "not a %r filter" % MAGIC
        self.seed, self.segment_length, self.segment_count_length, self.array_length = \
            HEADER.unpack_from(data, len(MAGIC))
        start = len(MAGIC) + HEADER.size
        self.fingerprints = np.frombuffer(data, dtype="<u2", count=self.array_length, offset=start)
        self.end = start + 2 * self.array_length

    def __contains__(self, key):
        h = _murmur64(np.array([key + self.seed & MASK64], dtype=np.uint64))
        a, b, c = (x[0] for x in _locations(h, self.segment_length, self.segment_count_length))
        f = int(_fingerprints(h)[0])
        return f ^ int(self.fingerprints[a]) ^ int(self.fingerprints[b]) ^ int(self.fingerprints[c]) == 0
