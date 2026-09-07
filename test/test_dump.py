"""NDJSON bulk dumps (ferenda/lib/dump.py)."""

import gzip
import json

from ferenda.lib import dump


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


def test_pooled_reads_keep_the_callers_order(tmp_path):
    arts = [_artifact(tmp_path, "%d.json" % i, {"uri": "u%d" % i}) for i in range(5)]
    serial, pooled = tmp_path / "s.ndjson.gz", tmp_path / "p.ndjson.gz"
    assert dump.dump_source(arts, serial) == dump.dump_source(arts, pooled, jobs=2) == 5
    with gzip.open(serial, "rt", encoding="utf-8") as a, \
            gzip.open(pooled, "rt", encoding="utf-8") as b:
        assert a.read() == b.read()


def _records(paths):
    return [(str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in paths]


def test_new_documents_append_as_a_member_and_changes_force_a_rewrite(tmp_path):
    a = _artifact(tmp_path, "a.json", {"uri": "a"})
    b = _artifact(tmp_path, "b.json", {"uri": "b"})
    out = tmp_path / "forarbete.ndjson.gz"
    dump.dump_source([a, b], out)
    dump.write_records(out, _records([a, b]))
    assert dump.read_records(out) == _records([a, b])
    # a document the dump has not seen: appendable
    c = _artifact(tmp_path, "c.json", {"uri": "c"})
    new = dump.appendable(dump.read_records(out), _records([a, b, c]))
    assert new == [str(c)]
    assert dump.append_to_dump(new, out, jobs=2) == 1
    with gzip.open(out, "rt", encoding="utf-8") as fh:      # one stream to a reader
        assert [json.loads(l)["uri"] for l in fh.read().splitlines()] == ["a", "b", "c"]
    dump.write_records(out, _records([a, b, c]))
    # a document changed in place, or gone: the file is rewritten instead
    b.write_text(json.dumps({"uri": "b", "changed": True}))
    assert dump.appendable(dump.read_records(out), _records([a, b, c])) is None
    assert dump.appendable(dump.read_records(out), _records([a, c])) is None
    # no records yet (a dump from before they were kept): rewrite too
    assert dump.appendable(None, _records([a, b, c])) is None
    # nothing new and nothing changed: nothing to append
    assert dump.appendable(_records([a, b, c]), _records([a, b, c])) == []
