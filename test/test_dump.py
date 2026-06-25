"""NDJSON bulk dumps (accommodanda/lib/dump.py)."""

import gzip
import json

from accommodanda.lib import dump


def _artifact(tmp_path, name, obj):
    p = tmp_path / name
    p.write_text(json.dumps(obj, indent=2, ensure_ascii=False))   # pretty on disk
    return p


def test_dump_round_trips_each_artifact(tmp_path):
    a = _artifact(tmp_path, "a.json", {"uri": "https://lagen.nu/1962:700",
                                       "title": "Brottsbalk", "body": []})
    b = _artifact(tmp_path, "b.json", {"uri": "https://lagen.nu/2018:585",
                                       "title": "Förvaltningslag"})
    out = tmp_path / "sfs.ndjson.gz"
    written = dump.dump_source([a, b], out)

    assert written == 2
    with gzip.open(out, "rt", encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    assert len(lines) == 2
    # each line parses and equals its source artifact
    assert json.loads(lines[0]) == json.loads(a.read_text())
    assert json.loads(lines[1]) == json.loads(b.read_text())
    # compact: re-serialised one-per-line regardless of on-disk pretty-printing
    assert "\n" not in lines[0] and ": " not in lines[0]


def test_dump_skips_empty_placeholders(tmp_path):
    good = _artifact(tmp_path, "good.json", {"uri": "https://lagen.nu/x"})
    empty = tmp_path / "empty.json"
    empty.write_bytes(b"")                              # SkipDocument placeholder
    out = tmp_path / "dv.ndjson.gz"

    written = dump.dump_source([good, empty], out)
    assert written == 1
    with gzip.open(out, "rt", encoding="utf-8") as fh:
        assert len(fh.read().splitlines()) == 1


def test_dump_reports_progress(tmp_path):
    a = _artifact(tmp_path, "a.json", {"uri": "u"})
    seen = []
    dump.dump_source([a], tmp_path / "o.ndjson.gz",
                     progress=lambda i, n: seen.append((i, n)))
    assert seen == [(1, 1)]
