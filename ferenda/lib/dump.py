"""NDJSON bulk dumps: the machine-readable corpus export.

A dump is just the source's artifacts concatenated, one compact JSON object per
line, gzipped. No transformation: a line is byte-for-byte the parsed artifact on
disk. The citation graph already lives inline in every artifact (the `text`
runs' link dicts), so each line is self-contained -- a consumer reloads the
whole corpus with `zcat sfs.ndjson.gz`, no catalog or context needed.

Empty artifacts (SkipDocument placeholders) are skipped.
"""

import gzip
import json
import os
import shutil
from pathlib import Path

from . import compress, util

# artifacts one read job takes: at 13-44 ms an artifact on the production NFS
# mount a job is a few seconds, and the progress line moves (relate's HASH_CHUNK)
READ_CHUNK = 200


def _lines_job(chunk):
    """One read job: each artifact's compact NDJSON line, or None for an empty
    one (a SkipDocument placeholder). The read is the NFS round trip that made
    the serial writer 22 ms per document (forarbete: 2151 s for 97,274 on
    2026-09-07); the pool overlaps them, the writer keeps the order."""
    out = []
    for path in chunk:
        raw = compress.read_bytes(Path(path))
        out.append(json.dumps(json.loads(raw), ensure_ascii=False,
                              separators=(",", ":")) + "\n"
                   if raw.strip() else None)
    return out


def _lines(artifact_paths, jobs, progress):
    total, seen = len(artifact_paths), 0
    for part in util.pooled(_lines_job, [str(p) for p in artifact_paths], jobs,
                            chunk=READ_CHUNK):
        for line in part:
            seen += 1
            if line is not None:
                yield line
            if progress:
                progress(seen, total)


def dump_source(artifact_paths, out_path, progress=None, jobs=1):
    """Write every artifact in `artifact_paths` as one NDJSON line to the
    gzipped `out_path`. Returns the number of documents written. Each artifact
    is re-serialised compactly (one line, no spaces) so the output is valid
    NDJSON regardless of how the artifact was pretty-printed on disk. The
    artifact reads run across `jobs` processes; the gzip stream is written
    here, in the callers' order.

    Written atomically: the multi-GB stream goes to a same-directory temp file
    and is `os.replace`d into place only once complete, so an interrupted dump
    never leaves a truncated `.gz` where a consumer would read it as the corpus
    export (cf. util.write_atomic, which the in-memory writers use)."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    written = 0
    try:
        # level 6 over the default 9: 2-3x faster over the multi-GB corpus for a
        # few percent larger output; the dumps carry no byte-identity contract
        with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=6) as fh:
            for line in _lines(artifact_paths, jobs, progress):
                fh.write(line)
                written += 1
        os.replace(tmp, out_path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return written


def append_to_dump(artifact_paths, out_path, progress=None, jobs=1):
    """Add the documents in `artifact_paths` to the existing dump at `out_path`
    as one more gzip member -- every gzip reader decompresses concatenated
    members as one stream, so the file stays plain NDJSON to its consumers.
    Only for documents the dump does not hold yet (`cmd_dump` decides, from
    the recorded stat records): a document that changed in place would
    otherwise appear twice, so those rewrite the file. Returns the number of
    lines added.

    Atomic like `dump_source`: the member is appended to a same-directory copy
    that replaces the original once complete. The copy is what appending
    costs (a 1 GB file: tens of seconds) against the full rewrite it spares
    (97,000 artifact reads)."""
    out_path = Path(out_path)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    written = 0
    try:
        shutil.copyfile(out_path, tmp)
        with gzip.open(tmp, "at", encoding="utf-8", compresslevel=6) as fh:
            for line in _lines(artifact_paths, jobs, progress):
                fh.write(line)
                written += 1
        os.replace(tmp, out_path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return written


def records_path(out_path):
    """The stat records of the artifacts a dump holds, beside it: one
    ``path\tsize\tmtime_ns`` line per artifact, gzipped. What tells the next
    run which changed artifacts are new (appendable) and which replaced or
    vanished (a rewrite)."""
    out_path = Path(out_path)
    return out_path.with_name(out_path.name + ".records")


def write_records(out_path, records):
    with gzip.open(records_path(out_path), "wt", encoding="utf-8") as fh:
        for path, size, mtime_ns in records:
            fh.write("%s\t%d\t%d\n" % (path, size, mtime_ns))


def read_records(out_path):
    """The recorded stat records, or None when the dump has none (a dump
    written before records were kept, or none at all)."""
    p = records_path(out_path)
    if not p.exists():
        return None
    with gzip.open(p, "rt", encoding="utf-8") as fh:
        return [(path, int(size), int(mtime))
                for path, size, mtime in (line.rstrip("\n").split("\t") for line in fh)]


def appendable(previous, current):
    """The artifacts to append when `current` (this run's stat records) differs
    from `previous` (the dump's) only by additions: their paths, in `current`'s
    order. None when a recorded artifact changed or vanished, or when there is
    no record -- the dump must then be rewritten."""
    if previous is None:
        return None
    before = {path: (size, mtime) for path, size, mtime in previous}
    now = {path: (size, mtime) for path, size, mtime in current}
    if any(path not in now or now[path] != mark for path, mark in before.items()):
        return None
    return [path for path, _size, _mtime in current if path not in before]
