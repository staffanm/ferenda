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
from pathlib import Path


def dump_source(artifact_paths, out_path, progress=None):
    """Write every artifact in `artifact_paths` as one NDJSON line to the
    gzipped `out_path`. Returns the number of documents written. Each artifact
    is re-serialised compactly (one line, no spaces) so the output is valid
    NDJSON regardless of how the artifact was pretty-printed on disk."""
    out_path = Path(out_path)
    total = len(artifact_paths)
    written = 0
    with gzip.open(out_path, "wt", encoding="utf-8") as fh:
        for i, path in enumerate(map(Path, artifact_paths)):
            raw = path.read_bytes()
            if raw.strip():
                json.dump(json.loads(raw), fh, ensure_ascii=False,
                          separators=(",", ":"))
                fh.write("\n")
                written += 1
            if progress:
                progress(i + 1, total)
    return written
