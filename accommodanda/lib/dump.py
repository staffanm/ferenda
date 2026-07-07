"""NDJSON bulk dumps -- the machine-readable corpus export (REWRITE.md §6) that
replaces the retired RDF/Fuseki dumps.

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
from pathlib import Path

from . import compress


def dump_source(artifact_paths, out_path, progress=None):
    """Write every artifact in `artifact_paths` as one NDJSON line to the
    gzipped `out_path`. Returns the number of documents written. Each artifact
    is re-serialised compactly (one line, no spaces) so the output is valid
    NDJSON regardless of how the artifact was pretty-printed on disk.

    Written atomically: the multi-GB stream goes to a same-directory temp file
    and is `os.replace`d into place only once complete, so an interrupted dump
    never leaves a truncated `.gz` where a consumer would read it as the corpus
    export (cf. util.write_atomic, which the in-memory writers use)."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    total = len(artifact_paths)
    written = 0
    try:
        # level 6 over the default 9: 2-3x faster over the multi-GB corpus for a
        # few percent larger output; the dumps carry no byte-identity contract
        with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=6) as fh:
            for i, path in enumerate(map(Path, artifact_paths)):
                raw = compress.read_bytes(path)      # decompressed artifact bytes
                if raw.strip():
                    json.dump(json.loads(raw), fh, ensure_ascii=False,
                              separators=(",", ":"))
                    fh.write("\n")
                    written += 1
                if progress:
                    progress(i + 1, total)
        os.replace(tmp, out_path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return written
