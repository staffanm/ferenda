"""Unit tests for the incremental build driver's freshness engine
(accommodanda.build), exercised through a synthetic two-stage source over
temp files -- no real corpus, no JVM, fast."""


import json

import pytest

from accommodanda import build
from accommodanda.build import RunOptions, Source, Stage, build_one, is_fresh
from accommodanda.lib import runlog
from accommodanda.lib.errors import SkipDocument


@pytest.fixture(autouse=True)
def reset_run():
    build.RUN = RunOptions()
    build.recipe_version.cache_clear()
    yield
    build.RUN = RunOptions()


def make_source(tmp_path, version_file=None):
    """A download->parse source: download copies a 'remote' string to a
    file, parse uppercases it. `version_file` (if given) is the parse
    stage's recipe-version input."""
    remote = {"a": "hello", "b": "world"}
    (tmp_path / "in").mkdir()
    (tmp_path / "dl").mkdir()
    (tmp_path / "out").mkdir()

    def dl_out(bf):
        return tmp_path / "dl" / ("%s.txt" % bf)

    def out(bf):
        return tmp_path / "out" / ("%s.txt" % bf)

    def dl_run(bf):
        dl_out(bf).write_text(remote[bf])

    def parse_run(bf):
        out(bf).write_text(dl_out(bf).read_text().upper())

    code = (version_file,) if version_file else ()
    return remote, Source("syn", lambda: sorted(remote), {
        "download": Stage("download", dl_run, dl_out),
        "parse": Stage("parse", parse_run, out, depends="download",
                       inputs=lambda bf: [dl_out(bf)], code=code),
    })


def apply(manifest, res):
    manifest.update(res.updates)


def test_build_then_skip(tmp_path):
    _, src = make_source(tmp_path)
    manifest = {}

    res = build_one(src, "parse", "a", manifest)
    apply(manifest, res)
    assert [s for s, _ in res.planned] == ["download", "parse"]  # dep first
    assert src.stages["parse"].output("a").read_text() == "HELLO"

    res2 = build_one(src, "parse", "a", manifest)
    assert res2.planned == []  # everything fresh -> nothing to do


def test_content_change_rebuilds_not_mtime(tmp_path):
    remote, src = make_source(tmp_path)
    manifest = {}
    apply(manifest, build_one(src, "parse", "a", manifest))

    # touch the downloaded input without changing content -> still fresh
    dl = src.stages["download"].output("a")
    dl.write_text(dl.read_text())
    assert is_fresh(manifest, src, src.stages["parse"], "a")

    # real content change -> stale
    dl.write_text("CHANGED")
    assert not is_fresh(manifest, src, src.stages["parse"], "a")
    res = build_one(src, "parse", "a", manifest)
    assert ["parse"] == [s for s, _ in res.planned]  # only parse, dl fresh


def test_force_rebuilds_named_stage_only(tmp_path):
    _, src = make_source(tmp_path)
    manifest = {}
    apply(manifest, build_one(src, "parse", "a", manifest))

    build.RUN.force = True
    res = build_one(src, "parse", "a", manifest)
    # force hits the named stage; its fresh dependency is not re-run
    assert [s for s, _ in res.planned] == ["parse"]


def test_recipe_version_invalidates(tmp_path):
    vfile = tmp_path / "recipe.py"
    vfile.write_text("VERSION = 1")
    _, src = make_source(tmp_path, version_file=vfile)
    manifest = {}
    apply(manifest, build_one(src, "parse", "a", manifest))
    assert is_fresh(manifest, src, src.stages["parse"], "a")

    # editing the recipe's own code re-stales the output (no --force needed)
    build.recipe_version.cache_clear()
    vfile.write_text("VERSION = 2")
    assert not is_fresh(manifest, src, src.stages["parse"], "a")


def test_code_version_gate_for_relate_index(tmp_path):
    # the recipe-version rule extended to relate/index (which are incremental on
    # data content, not via the per-doc manifest): editing their extraction/index
    # code re-stales the whole source, per source, suppressed by ignore-code-changes
    vfile = tmp_path / "extract.py"
    vfile.write_text("v1")
    code = (vfile,)
    manifest = {}

    assert build.code_changed(manifest, "relate", "sfs", code)      # no record yet
    build.record_code_version(manifest, "relate", "sfs", code)
    build.recipe_version.cache_clear()
    assert not build.code_changed(manifest, "relate", "sfs", code)  # now current
    assert build.code_changed(manifest, "relate", "dv", code)       # per-source

    vfile.write_text("v2")                                          # edit the code
    build.recipe_version.cache_clear()
    assert build.code_changed(manifest, "relate", "sfs", code)
    build.RUN.ignore_code_changes = True                           # pinned fresh
    assert not build.code_changed(manifest, "relate", "sfs", code)
    build.RUN.ignore_code_changes = False

    # relate and index are independent namespaces
    build.record_code_version(manifest, "relate", "sfs", code)
    build.recipe_version.cache_clear()
    assert build.code_changed(manifest, "index", "sfs", code)


def test_file_watermark_detects_add_remove_modify(tmp_path):
    a = tmp_path / "a.json"; a.write_text("1")
    b = tmp_path / "b.json"; b.write_text("2")
    base = build.file_watermark([a, b])
    assert build.file_watermark([a, b]) == base          # stable when untouched
    b.write_text("22")                                   # modify -> new size/mtime
    assert build.file_watermark([a, b]) != base
    assert build.file_watermark([a]) != base             # remove one
    c = tmp_path / "c.json"; c.write_text("3")
    assert build.file_watermark([a, b, c]) != base       # add one


def test_stage_watermark_tracks_inputs(tmp_path):
    _, src = make_source(tmp_path)
    manifest = {}
    build_one(src, "download", "a", manifest)        # materialise the inputs
    build_one(src, "download", "b", manifest)
    wm = build.stage_watermark(src, "parse")
    assert build.stage_watermark(src, "parse") == wm   # stable while untouched
    (tmp_path / "dl" / "a.txt").write_text("HELLO AGAIN")   # rewrite one input
    assert build.stage_watermark(src, "parse") != wm


def test_up_to_date_combines_watermark_code_and_force(tmp_path):
    vfile = tmp_path / "code.py"; vfile.write_text("v1")
    code = (vfile,)
    manifest = {}
    wm = "wm-1"
    assert not build.up_to_date(manifest, "relate", "sfs", wm, code)   # no record
    build.record_step(manifest, "relate", "sfs", wm, code)
    build.recipe_version.cache_clear()
    assert build.up_to_date(manifest, "relate", "sfs", wm, code)       # now fresh
    assert not build.up_to_date(manifest, "relate", "sfs", "wm-2", code)  # data moved
    vfile.write_text("v2"); build.recipe_version.cache_clear()
    assert not build.up_to_date(manifest, "relate", "sfs", wm, code)   # code moved
    vfile.write_text("v1"); build.recipe_version.cache_clear()
    build.RUN.force = True
    assert not build.up_to_date(manifest, "relate", "sfs", wm, code)   # --force


def test_no_deps_skips_upstream(tmp_path):
    _, src = make_source(tmp_path)
    manifest = {}

    build.RUN.no_deps = True
    res = build_one(src, "parse", "a", manifest)  # download never run
    # parse's input (download output) is missing -> recipe errors, recorded
    assert res.errors and res.errors[0][0] == "parse"
    assert not src.stages["parse"].output("a").exists()


def test_dry_run_writes_nothing(tmp_path):
    _, src = make_source(tmp_path)
    manifest = {}

    build.RUN.dry_run = True
    res = build_one(src, "parse", "a", manifest)
    assert [s for s, _ in res.planned] == ["download", "parse"]
    assert res.updates == {}
    assert not src.stages["parse"].output("a").exists()


# --------------------------------------------------------------------------
# run instrumentation: the ledger / errors.json / status.json written through
# lib.runlog. Driven end-to-end through main() over the synthetic source
# (registered into SOURCES, its state files redirected to tmp_path), -j1 so the
# child processes -- which re-import build fresh and wouldn't see the synthetic
# source -- are never spawned.
# --------------------------------------------------------------------------


def build_source(tmp_path, *, fail=(), skip=()):
    """A download->parse synthetic source whose parse raises ValueError for the
    basefiles in `fail` and SkipDocument for those in `skip` (both mutable sets
    so a test can toggle them and re-run)."""
    remote = {"a": "hello", "b": "world"}
    for d in ("dl", "out"):
        (tmp_path / d).mkdir(exist_ok=True)

    def dl_out(bf):
        return tmp_path / "dl" / ("%s.txt" % bf)

    def out(bf):
        return tmp_path / "out" / ("%s.txt" % bf)

    def dl_run(bf):
        dl_out(bf).write_text(remote[bf])

    def parse_run(bf):
        if bf in fail:
            raise ValueError("boom %s" % bf)
        if bf in skip:
            raise SkipDocument("empty %s" % bf)
        out(bf).write_text(dl_out(bf).read_text().upper())

    return Source("syn", lambda: sorted(remote), {
        "download": Stage("download", dl_run, dl_out),
        "parse": Stage("parse", parse_run, out, depends="download",
                       inputs=lambda bf: [dl_out(bf)]),
    })


@pytest.fixture
def wire(monkeypatch, tmp_path):
    """Register a source and redirect every build state file into tmp_path."""
    bd = tmp_path / ".build"
    mock_sources = {}
    monkeypatch.setattr(build, "SOURCES", mock_sources)

    def _wire(src):
        mock_sources[src.name] = src
        monkeypatch.setattr(build, "RUNS", bd / "runs.ndjson")
        monkeypatch.setattr(build, "ERRORS", bd / "errors.json")
        monkeypatch.setattr(build, "STATUS", bd / "status.json")
        monkeypatch.setattr(build, "MANIFEST", bd / "manifest.json")
        monkeypatch.setattr(build, "WATERMARKS", bd / "watermarks.json")
        monkeypatch.setattr(build, "_MANIFEST_CACHE", None)
        monkeypatch.setattr(build, "_WATERMARKS_CACHE", None)
        monkeypatch.setattr(build, "RUN_ID", None)
    return _wire


def _events(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def _materialise_downloads(src):
    """Run the download stage for every basefile so a later `parse` runs over
    ready inputs -- a cold parse would otherwise recurse into download and fold
    those extra runs into the parse segment (the documented cosmetic limit)."""
    for bf in src.list_basefiles():
        src.stages["download"].run(bf)


def test_run_writes_start_segment_end(wire, tmp_path):
    src = build_source(tmp_path)
    wire(src)
    _materialise_downloads(src)
    build.main(["syn", "parse", "-j1"])

    events = _events(build.RUNS)
    assert events[0]["event"] == "run-start"
    assert events[0]["argv"] == ["lagen", "syn", "parse", "-j1"]
    assert events[-1]["event"] == "run-end"
    assert events[-1]["ok"] is True and events[-1]["errors"] == 0
    (seg,) = [e for e in events if e["event"] == "segment"]
    assert seg["step"] == "parse" and seg["source"] == "syn"
    assert seg["total"] == 2 and seg["ran"] == 2 and seg["status"] == "ok"
    # a full-source run writes the cheap status cell
    cell = runlog.read_status(build.STATUS)["syn"]["parse"]
    assert cell["total"] == 2 and cell["fresh"] == 2 and cell["failed"] == 0


def test_failing_recipe_lands_in_errors_and_clears(wire, tmp_path):
    fail = {"b"}
    wire(build_source(tmp_path, fail=fail))
    with pytest.raises(SystemExit):        # a failed doc exits non-zero
        build.main(["syn", "parse", "-j1"])

    errors = runlog.read_errors(build.ERRORS)
    assert "syn/parse/b" in errors and "syn/parse/a" not in errors
    entry = errors["syn/parse/b"]
    assert entry["error"].startswith("ValueError: boom b")
    assert "ValueError" in entry["traceback"]      # a real captured traceback
    assert _events(build.RUNS)[-1]["ok"] is False

    # fixing the input and re-running heals the entry (self-healing store)
    fail.clear()
    build.main(["syn", "parse", "-j1"])
    assert "syn/parse/b" not in runlog.read_errors(build.ERRORS)


def test_dry_run_writes_no_state_files(wire, tmp_path):
    wire(build_source(tmp_path))
    build.main(["syn", "parse", "-n", "-j1"])
    assert not build.RUNS.exists()
    assert not build.ERRORS.exists()
    assert not build.STATUS.exists()
    assert build.RUN_ID is None            # no run id minted for a dry run


def test_dry_run_after_pipeline_emits_no_second_run(wire, tmp_path):
    # RUN_ID/RUN_ERRORS reset at the top of main() so a --dry-run (or any
    # non-pipeline verb) after a real run never inherits the stale id and emits a
    # phantom second run-end into the ledger
    src = build_source(tmp_path)
    wire(src)
    _materialise_downloads(src)
    build.main(["syn", "parse", "-j1"])
    before = _events(build.RUNS)
    build.main(["syn", "parse", "-n", "-j1"])       # dry run
    assert _events(build.RUNS) == before            # nothing appended
    assert build.RUN_ID is None


def test_clean_run_reports_ok_despite_prior_unrelated_failures(wire, tmp_path):
    # run-end's ok/errors reflect THIS run's segment errors, not the corpus-wide
    # currently-failing count: a stale unrelated errors.json entry must not make a
    # clean targeted run read as failed
    src = build_source(tmp_path)
    wire(src)
    runlog.apply_outcomes(build.ERRORS, "other", [("parse", "x", "boom", "tb")],
                          [], "old-run")            # pre-existing unrelated failure
    _materialise_downloads(src)
    build.main(["syn", "parse", "-j1"])
    end = _events(build.RUNS)[-1]
    assert end["event"] == "run-end"
    assert end["ok"] is True and end["errors"] == 0
    # the corpus-wide failing count still lives in errors.json, untouched
    assert "other/parse/x" in runlog.read_errors(build.ERRORS)


def test_skipdocument_counted_as_skipdoc(wire, tmp_path):
    wire(build_source(tmp_path, skip={"b"}))
    build.main(["syn", "parse", "-j1"])
    (seg,) = [e for e in _events(build.RUNS) if e["event"] == "segment"]
    assert seg["skipdoc"] == 1
    # a deliberate skip is not a failure and leaves no errors.json entry
    assert "syn/parse/b" not in runlog.read_errors(build.ERRORS)
    assert runlog.read_status(build.STATUS)["syn"]["parse"]["empty"] == 1


def test_targeted_run_leaves_status_cell_untouched(wire, tmp_path):
    wire(build_source(tmp_path))
    build.main(["syn", "parse", "-j1"])            # full run writes the cell
    before = build.STATUS.read_text()

    # a targeted single-basefile run must NOT clobber the source-wide cell
    build.main(["syn", "parse", "a", "-f", "-j1"])
    assert build.STATUS.read_text() == before
    # ... yet it still ran as its own run: the µs-resolution ids keep the two
    # back-to-back runs distinct in the ledger (newest first)
    runs = runlog.read_runs(build.RUNS)
    assert len(runs) == 2
    assert runs[0]["argv"] == ["lagen", "syn", "parse", "a", "-f", "-j1"]


def test_watermark_skipped_step_emits_skipped_segment(wire, tmp_path, monkeypatch):
    src = build_source(tmp_path)
    wire(src)
    _materialise_downloads(src)                    # stable parse inputs across runs
    # rebuild's derived steps need the real catalog/layout; stub them out so the
    # test isolates the parse watermark gate
    for fn in ("cmd_relate", "cmd_index", "cmd_dump", "cmd_generate"):
        monkeypatch.setattr(build, fn, lambda *a, **k: None)
    build.main(["syn", "rebuild", "-j1"])          # parse runs, records watermark
    build.main(["syn", "rebuild", "-j1"])          # unchanged -> parse skipped

    skipped = [e for e in _events(build.RUNS) if e["event"] == "segment"
               and e["step"] == "parse" and e["status"] == "skipped"]
    assert skipped and skipped[-1]["source"] == "syn"


def test_all_download_skips_derived_sources(wire, tmp_path, monkeypatch):
    harvest_called = []
    def harvest_run(scopes):
        harvest_called.append(scopes)

    src1 = Source("syn_harvest", lambda: [], {}, harvest=harvest_run, origin="http://example.com")
    src2 = Source("syn_derived", lambda: [], {}) # no harvest, no download stage
    
    wire(src1)
    monkeypatch.setitem(build.SOURCES, src2.name, src2)
    
    build.main(["all", "download", "-j1"])
    assert harvest_called == [[]]


def test_all_download_contains_harvest_exceptions(wire, tmp_path, monkeypatch):
    harvests = {}
    def harvest1(scopes):
        harvests["syn1"] = scopes
        raise ValueError("syn1 harvest crash")
        
    def harvest2(scopes):
        harvests["syn2"] = scopes
        
    src1 = Source("syn1", lambda: [], {}, harvest=harvest1, origin="http://example.com")
    src2 = Source("syn2", lambda: [], {}, harvest=harvest2, origin="http://example.com")
    
    wire(src1)
    monkeypatch.setitem(build.SOURCES, src2.name, src2)
    
    with pytest.raises(SystemExit) as exc_info:
        build.main(["all", "download", "-j1"])
        
    assert exc_info.value.code == 1
    assert "syn1" in harvests
    assert "syn2" in harvests


def test_all_all_contains_harvest_exceptions(wire, tmp_path, monkeypatch):
    harvests = {}
    def harvest1(scopes):
        harvests["syn1"] = scopes
        raise ValueError("syn1 harvest crash")
        
    def harvest2(scopes):
        harvests["syn2"] = scopes
        
    src1 = Source("syn1", lambda: [], {}, harvest=harvest1, origin="http://example.com")
    src2 = Source("syn2", lambda: [], {}, harvest=harvest2, origin="http://example.com")
    
    wire(src1)
    monkeypatch.setitem(build.SOURCES, src2.name, src2)
    
    # Stub rebuild's derived steps
    for fn in ("cmd_relate", "cmd_index", "cmd_dump", "cmd_generate"):
        monkeypatch.setattr(build, fn, lambda *a, **k: None)
        
    with pytest.raises(SystemExit) as exc_info:
        build.main(["all", "all", "-j1"])
        
    assert exc_info.value.code == 1
    assert "syn1" in harvests
    assert "syn2" in harvests


def test_explicit_derived_download_errors(wire, tmp_path, monkeypatch):
    src = Source("syn_derived", lambda: [], {}) # no harvest, no download stage
    wire(src)
    with pytest.raises(SystemExit) as exc_info:
        build.main(["syn_derived", "download", "-j1"])
    assert exc_info.value.code == 2


def test_cmd_relate_index_dump_skip_non_artifact_sources(monkeypatch, tmp_path):
    # Setup build context (CATALOG, watermarks, etc. mapped to tmp_path)
    monkeypatch.setattr(build, "SOURCES", {})
    monkeypatch.setattr(build, "CATALOG", tmp_path / "catalog.sqlite")
    monkeypatch.setattr(build, "WATERMARKS", tmp_path / "watermarks.json")
    monkeypatch.setattr(build, "RUNS", tmp_path / "runs.ndjson")
    monkeypatch.setattr(build, "ERRORS", tmp_path / "errors.json")
    monkeypatch.setattr(build, "STATUS", tmp_path / "status.json")
    
    # Register a source that has no entry in ARTIFACTS mapping (similar to remisser)
    src = Source("non_artifact_src", lambda: [], {})
    monkeypatch.setitem(build.SOURCES, src.name, src)
    
    # Running cmd_relate, cmd_index, cmd_dump should skip it without raising KeyError
    build.cmd_relate(["non_artifact_src"])
    build.cmd_index(["non_artifact_src"])
    build.cmd_dump(["non_artifact_src"])
