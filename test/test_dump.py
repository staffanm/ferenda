"""NDJSON bulk dumps (ferenda/lib/dump.py)."""

import gzip
import json

from ferenda.lib import dump, freshness, layout


def _artifact(tmp_path, name, obj):
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
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


def test_new_documents_append_as_a_member_and_changes_force_a_rewrite(tmp_path, monkeypatch):
    monkeypatch.setattr(dump, "RECORDS_DIR", tmp_path / ".build")
    a = _artifact(tmp_path, "a.json", {"uri": "a"})
    b = _artifact(tmp_path, "b.json", {"uri": "b"})
    out = tmp_path / "dumps" / "forarbete.ndjson.gz"
    dump.dump_source([a, b], out)
    dump.write_records(out, _records([a, b]))
    assert dump.records_path(out) == tmp_path / ".build" / "forarbete.ndjson.gz.records"
    assert not (tmp_path / "dumps" / "forarbete.ndjson.gz.records").exists()
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


def test_legacy_records_beside_dump_migrate_into_build_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(dump, "RECORDS_DIR", tmp_path / ".build")
    a = _artifact(tmp_path, "a.json", {"uri": "a"})
    out = tmp_path / "dumps" / "sfs.ndjson.gz"
    out.parent.mkdir(parents=True)
    legacy = out.with_name("sfs.ndjson.gz.records")
    freshness.write_stat_records(legacy, _records([a]))

    assert dump.read_records(out) == _records([a])
    assert not legacy.exists()
    assert (tmp_path / ".build" / "sfs.ndjson.gz.records").exists()


def test_version_artifacts_reads_sidecars_and_skips_unindexed_files(tmp_path, monkeypatch):
    art_root = tmp_path / "artifact"
    monkeypatch.setattr(layout, "ARTIFACT", art_root)
    monkeypatch.setattr(layout, "SFS_ARTIFACT", art_root / "sfs")

    # SFS: one indexed version, plus an unreferenced mislabeled file in archive/
    v1 = layout.sfs_version_artifact("1998:204", "2003:466")
    _artifact(v1.parent, v1.name, {"uri": "https://lagen.nu/1998:204/konsolidering/2003:466"})
    orphan = layout.sfs_version_artifact("1998:204", "1998:204")
    _artifact(orphan.parent, orphan.name, {"uri": "orphan"})
    sc_sfs = layout.sfs_versions_sidecar("1998:204")
    _artifact(sc_sfs.parent, sc_sfs.name, {
        "versions": [{"version": "2003:466",
                      "uri": "https://lagen.nu/1998:204/konsolidering/2003:466"}],
        "skipped": []})

    # EUR-Lex: one indexed version, plus a statute with an empty sidecar and no archive
    ev1 = layout.eurlex_version_artifact("32016R0679", "2018-05-25")
    _artifact(ev1.parent, ev1.name, {"uri": "https://lagen.nu/celex/32016R0679/konsolidering/2018-05-25"})
    sc_eur = layout.eurlex_versions_sidecar("32016R0679")
    _artifact(sc_eur.parent, sc_eur.name, {
        "versions": [{"version": "2018-05-25",
                      "uri": "https://lagen.nu/celex/32016R0679/konsolidering/2018-05-25"}],
        "skipped": []})
    sc_empty = layout.eurlex_versions_sidecar("32020R0001")
    _artifact(sc_empty.parent, sc_empty.name, {"versions": [], "skipped": []})

    assert layout.version_artifacts("sfs") == [v1]
    assert layout.version_artifacts("eurlex") == [ev1]
