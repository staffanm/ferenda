"""Unit tests for the incremental build driver's freshness engine
(accommodanda.build), exercised through a synthetic two-stage source over
temp files -- no real corpus, no JVM, fast."""


import json
import os
import socket
import sqlite3
from urllib.parse import urlparse

import pytest

from accommodanda import build, config
from accommodanda.build import RunOptions, Source, Stage, build_one, is_fresh
from accommodanda.foreskrift.agencies import REGISTRY
from accommodanda.foreskrift.model import Consolidation, Regulation
from accommodanda.lib import annstore, catalog, compress, layout, runlog
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


def test_fresh_skip_heals_stale_error(tmp_path, monkeypatch):
    """A doc skipped as fresh has a valid, up-to-date artifact, so a stale error
    from an earlier transient failure (e.g. an input momentarily missing) must be
    healed on the next incremental run -- without a --force re-parse."""
    _, src = make_source(tmp_path)
    manifest = {}
    apply(manifest, build_one(src, "parse", "a", manifest))   # now fresh on disk

    monkeypatch.setattr(build, "ERRORS", tmp_path / "errors.json")
    monkeypatch.setattr(build, "RUNS", tmp_path / "runs.ndjson")
    monkeypatch.setattr(build, "STATUS", tmp_path / "status.json")
    monkeypatch.setattr(build, "RUN_ID", "run-2")
    # an earlier transient run recorded a failure for this (now-fresh) doc
    runlog.apply_outcomes(build.ERRORS, "syn",
                          [("parse", "a", "OSError: gone", "tb")], [], "run-1")
    assert "syn/parse/a" in runlog.read_errors(build.ERRORS)

    res2 = build_one(src, "parse", "a", manifest)
    assert res2.planned == [] and ("parse", "a") in res2.fresh   # skipped as fresh
    build.report(src, "parse", res2, 1, True)                   # folds fresh -> clear
    assert "syn/parse/a" not in runlog.read_errors(build.ERRORS)


def test_stage_gate_skips_when_watermark_unchanged(tmp_path, monkeypatch, capsys):
    """The coarse watermark gate (shared by cmd_all and single-source `lagen sfs
    parse`): once the source is watermarked, a re-run with unchanged inputs skips
    the per-doc scan wholesale ("up to date -- skipped"); an input change re-runs."""
    _, src = make_source(tmp_path)
    monkeypatch.setattr(build, "MANIFEST", tmp_path / "manifest.json")
    monkeypatch.setattr(build, "_MANIFEST_CACHE", None)
    # settle downloads first (as `download` before `parse` does in real use), so a
    # later parse touches no inputs and the recorded watermark stays valid
    build.run_action(src, "parse", src.list_basefiles(), 1)
    capsys.readouterr()
    store = {}

    errs, recorded = build._run_stage_gated(src, "parse", 1, store)
    assert (errs, recorded) == (False, True)                 # ran + watermarked
    assert "up to date -- skipped" not in capsys.readouterr().out

    errs, recorded = build._run_stage_gated(src, "parse", 1, store)
    assert (errs, recorded) == (False, False)                # skipped wholesale
    assert "parse syn: up to date -- skipped" in capsys.readouterr().out

    # an input change re-stales the gate -> it runs again
    (tmp_path / "dl" / "a.txt").write_text("CHANGED")
    errs, recorded = build._run_stage_gated(src, "parse", 1, store)
    assert (errs, recorded) == (False, True)


def test_orphan_errors_reconciled_only_on_full_source(tmp_path, monkeypatch):
    """A full-source run drops error entries for basefiles the source no longer
    lists (orphans that fresh-skip healing can never reach); a targeted run must
    NOT -- else parsing one doc would nuke every other doc's recorded error."""
    _, src = make_source(tmp_path)                       # lists "a", "b"
    monkeypatch.setattr(build, "ERRORS", tmp_path / "errors.json")
    monkeypatch.setattr(build, "RUNS", tmp_path / "runs.ndjson")
    monkeypatch.setattr(build, "STATUS", tmp_path / "status.json")
    monkeypatch.setattr(build, "RUN_ID", "run-2")
    # an orphan (basefile with '/', no longer listed) + a different source's entry
    runlog.apply_outcomes(build.ERRORS, "syn",
                          [("parse", "gone/x", "KeyError: 'type'", "tb")], [], "r1")
    runlog.apply_outcomes(build.ERRORS, "other",
                          [("parse", "z", "KeyError", "tb")], [], "r1")

    # targeted run (full_source=False): reconcile must NOT fire
    build.report(src, "parse", build.Result(), 1, False)
    assert "syn/parse/gone/x" in runlog.read_errors(build.ERRORS)

    # full-source run: the orphan is dropped, other sources untouched
    build.report(src, "parse", build.Result(), 2, True)
    errs = runlog.read_errors(build.ERRORS)
    assert "syn/parse/gone/x" not in errs
    assert "other/parse/z" in errs                       # never touches another source


def test_report_failing_count_is_source_scoped(tmp_path, monkeypatch, capsys):
    """The `docs failing` line counts only the reported source -- `lagen dv parse`
    must not surface another source's errors."""
    _, src = make_source(tmp_path)                       # source "syn"
    monkeypatch.setattr(build, "ERRORS", tmp_path / "errors.json")
    monkeypatch.setattr(build, "RUNS", tmp_path / "runs.ndjson")
    monkeypatch.setattr(build, "STATUS", tmp_path / "status.json")
    monkeypatch.setattr(build, "RUN_ID", "r1")
    runlog.apply_outcomes(build.ERRORS, "syn",
                          [("parse", "a", "E", "tb")], [], "r0")
    runlog.apply_outcomes(build.ERRORS, "other",     # a different source, 3 errors
                          [("parse", x, "E", "tb") for x in ("x", "y", "z")], [], "r0")

    build.report(src, "parse", build.Result(), 1, False)   # targeted: no reconcile
    out = capsys.readouterr().out
    assert "1 docs failing in syn" in out                  # only syn's error counted
    assert "overall" not in out                            # no misleading global count


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


def test_sfs_graphics_is_part_of_parse_recipe():
    assert any(path.name == "graphics.py" for path in build.SFS_CODE)


def test_sfs_conventions_are_part_of_parse_recipe():
    assert "parallelappendix.py" in {path.name for path in build.SFS_CODE}


def test_targeted_generate_refreshes_parse_and_relate(tmp_path, monkeypatch):
    _, src = make_source(tmp_path)
    src.name = "sfs"
    monkeypatch.setattr(build, "MANIFEST", tmp_path / "manifest.json")
    monkeypatch.setattr(build, "_MANIFEST_CACHE", None)
    related = []
    monkeypatch.setattr(build, "cmd_relate", lambda names: related.append(names))

    assert not build._prepare_targeted_generate(src, ["a"], 1)
    assert src.stages["parse"].output("a").read_text() == "HELLO"
    assert related == [["sfs"]]


def test_targeted_generate_no_deps_leaves_parse_untouched(tmp_path, monkeypatch):
    _, src = make_source(tmp_path)
    src.name = "sfs"
    monkeypatch.setattr(build, "MANIFEST", tmp_path / "manifest.json")
    monkeypatch.setattr(build, "_MANIFEST_CACHE", None)
    monkeypatch.setattr(build, "cmd_relate",
                        lambda names: pytest.fail("must not relate with --no-deps"))
    monkeypatch.setattr(build.RUN, "no_deps", True)

    assert not build._prepare_targeted_generate(src, ["a"], 1)
    assert not src.stages["parse"].output("a").exists()


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


def test_artifacts_excludes_index_sidecars(tmp_path, monkeypatch):
    # layout.artifacts() is the single home for a source's artifact list, and
    # build.ARTIFACTS["dv"]/["kommentar"] delegate to it. The non-document json
    # that shares a source's artifact dir -- the case-law identity index, the
    # AI-guidance index, the sfs .versions.json layers -- must never surface as a
    # document, else relate tries to index a JSON list (art["uri"] on a list).
    monkeypatch.setattr(layout, "ARTIFACT", tmp_path)

    dom = tmp_path / "dom"; dom.mkdir()
    case = dom / "NJA_2020_s_1.json"; case.write_text("{}")
    (dom / layout.DOM_INDEX.name).write_text("[]")               # not a document
    assert layout.artifacts("dv") == [case]
    assert build.ARTIFACTS["dv"]() == [case]                     # build delegates

    komm = tmp_path / "kommentar" / "sfs"; komm.mkdir(parents=True)
    note = komm / "2009_400.json"; note.write_text("{}")
    (tmp_path / "kommentar" / layout.GUIDANCE_INDEX.name).write_text("{}")
    assert layout.artifacts("kommentar") == [note]
    assert build.ARTIFACTS["kommentar"]() == [note]

    sfs = tmp_path / "sfs" / "2020"; sfs.mkdir(parents=True)
    law = sfs / "1.json"; law.write_text("{}")
    (sfs / "1.versions.json").write_text("[]")                   # not a document
    assert layout.artifacts("sfs") == [law]

    fs = tmp_path / "foreskrift" / "fffs"; fs.mkdir(parents=True)
    reg = fs / "2013-10.json"; reg.write_text("{}")
    (fs / "2013-10.grund.json").write_text("{}")                 # not a document
    assert layout.artifacts("foreskrift") == [reg]


def test_foreskrift_parse_run_retires_grund_projection(tmp_path, monkeypatch):
    # the /grund page is a derived projection of the sidecar: when a re-parse
    # stops presenting a consolidation, the sidecar AND the generated page must
    # both go -- generate only plans pages whose sidecar exists, so a surviving
    # page would keep serving as an unrefreshable orphan
    monkeypatch.setattr(layout, "ARTIFACT", tmp_path / "artifact")
    monkeypatch.setattr(build, "GENERATED", tmp_path / "generated")
    record = tmp_path / "record.json"; record.write_text("{}")
    monkeypatch.setattr(build, "foreskrift_record", lambda bf: record)

    def reg(consolidations):
        return Regulation(
            uri="https://lagen.nu/fffs/2013:10", identifier="FFFS 2013:10",
            fs="fffs", arsutgava="2013", lopnummer="10",
            structure=[{"id": "P1"}], consolidations=consolidations)

    cons = Consolidation(of="https://lagen.nu/fffs/2013:10",
                         structure=[{"id": "P1"}])
    monkeypatch.setattr(build.foreskrift_parse, "parse_record",
                        lambda record, root: reg([cons]))
    build.foreskrift_parse_run("fffs/2013:10")
    sidecar = layout.foreskrift_grund_artifact("fffs/2013:10")
    assert compress.exists(sidecar)
    page = build.GENERATED / "fffs" / "2013_10_grund.html"
    page.parent.mkdir(parents=True); page.write_text("<html>")

    monkeypatch.setattr(build.foreskrift_parse, "parse_record",
                        lambda record, root: reg([]))
    build.foreskrift_parse_run("fffs/2013:10")
    assert not compress.exists(sidecar)
    assert not page.exists()


def test_foreskrift_grund_pages_enumerates_sidecars(tmp_path, monkeypatch):
    # the /grund sidecars become generate's extra page rows (the föreskrift
    # counterpart of build.sfs_version_pages): uri, source, path, title
    monkeypatch.setattr(layout, "ARTIFACT", tmp_path)
    fs = tmp_path / "foreskrift" / "fffs"; fs.mkdir(parents=True)
    (fs / "2013-10.json").write_text("{}")
    (fs / "2013-10.grund.json").write_text("{}")
    assert layout.foreskrift_grund_pages() == [
        ("https://lagen.nu/fffs/2013:10/grund", "foreskrift",
         str(fs / "2013-10.grund.json"),
         "FFFS 2013:10 i ursprunglig lydelse")]
    # a series slug outside the -fs suffix convention (BFNAR, RA-MS) is still
    # a valid föreskrift identity -- the slug grammar carries the exceptions
    bfnar = tmp_path / "foreskrift" / "bfnar"; bfnar.mkdir()
    (bfnar / "2002-1.grund.json").write_text("{}")
    assert layout.foreskrift_grund_pages()[0] == (
        "https://lagen.nu/bfnar/2002:1/grund", "foreskrift",
        str(bfnar / "2002-1.grund.json"), "BFNAR 2002:1 i ursprunglig lydelse")
    # a sidecar name that does not decode to a föreskrift identity is a
    # layout bug, not a page -- refuse, never guess
    (fs / "trasig.grund.json").write_text("{}")
    with pytest.raises(ValueError):
        layout.foreskrift_grund_pages()


def test_layout_grammar_covers_every_registered_fs():
    # the layout slug grammar (an -fs suffix plus named exceptions) must accept
    # every registered författningssamling -- a slug it misses falls through to
    # the SFS page branch and crashes grund-sidecar decoding, as bfnar and rams
    # once did. New series: extend layout._FS_SLUG's exceptions if needed.
    for fs in REGISTRY:
        assert layout.page_relpath("https://lagen.nu/%s/2020:1" % fs) \
            == "%s/2020_1.html" % fs, fs
        assert layout.page_relpath("https://lagen.nu/%s/2020:1/grund" % fs) \
            == "%s/2020_1_grund.html" % fs, fs


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


def _opensearch_up():
    u = urlparse(config.OPENSEARCH_URL)
    try:
        socket.create_connection((u.hostname, u.port or 9200), timeout=0.5).close()
        return True
    except OSError:
        return False


@pytest.mark.skipif(not _opensearch_up(),
                    reason="cmd_index needs a running OpenSearch (OPENSEARCH_URL)")
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


def test_rebuild_after_commit_drives_the_right_stages(monkeypatch):
    # the inline editor's post-commit rebuild (build.rebuild_after_commit) runs
    # synchronously inside the commit request, so its argument-shaping glue --
    # host mapping, host_uri/page_url, layout.artifact, the only=/source= into
    # cmd_generate -- must be correct or the commit lands but the page 500s. Spy
    # the pipeline stages (their own coverage is elsewhere) and assert the glue.
    parsed, related, generated = [], [], []
    monkeypatch.setattr(build, "kommentar_parse_run", lambda bf: parsed.append(("kommentar", bf)))
    monkeypatch.setattr(build, "begrepp_parse_run", lambda bf: parsed.append(("begrepp", bf)))
    monkeypatch.setattr(build, "site_parse_run", lambda bf: parsed.append(("site", bf)))
    monkeypatch.setattr(build, "cmd_relate", lambda names: related.append(list(names)))
    monkeypatch.setattr(
        build, "cmd_generate",
        lambda only=None, source=None, jobs=1, force=False:
            generated.append((only, source, force)))

    urls = build.rebuild_after_commit([
        {"kind": "kommentar", "basefile": "1962:700"},     # SFS host
        {"kind": "kommentar", "basefile": "32024R2847"},   # eurlex host (CELEX)
        {"kind": "begrepp", "basefile": "Avtal"},
        {"kind": "site", "basefile": "om/kontakt"},
    ])

    assert parsed == [("kommentar", "1962:700"), ("kommentar", "32024R2847"),
                      ("begrepp", "Avtal"), ("site", "om/kontakt")]
    assert related == [["kommentar", "begrepp"]]           # site carries no catalog rows
    # each host page regenerated by its own source, scoped to its artifact path,
    # and FORCED: the page is dirty by construction (the request just committed
    # an edit onto it), so the freshness signature must not be consulted --
    # a fresh-judged host page would ship the response without the edit live
    assert (generated == [
        ({str(layout.artifact("sfs", "1962:700"))}, "sfs", True),
        ({str(layout.artifact("eurlex", "32024R2847"))}, "eurlex", True),
        ({str(layout.artifact("begrepp", "Avtal"))}, "begrepp", True),
        (None, "site", False),           # write_site rewrites unconditionally
    ])
    # the public URLs the endpoint reports back
    assert urls == ["/1962:700", "/celex/32024R2847", "/begrepp/Avtal", "/om/kontakt"]


# --------------------------------------------------------------------------
# catalog_root (catalog off data_root) + the full-rebuild scratch/swap
# --------------------------------------------------------------------------

def _fake_sfs_artifact(data_root):
    """A minimal SFS artifact on disk under `data_root`, returning its path."""
    art = data_root / "sfs" / "artifact" / "9999" / "1.json"
    art.parent.mkdir(parents=True, exist_ok=True)
    art.write_text(json.dumps({"uri": "https://lagen.nu/9999:1", "kind": "law",
                               "label": "9999:1", "title": "Testlag", "body": []}))
    return art


def test_catalog_records_and_resolves_separated_data_root(tmp_path):
    """When the catalog lives outside the corpus (catalog_root != data_root), a
    rebuild records the absolute corpus root so stored (relative) artifact paths
    still resolve; a colocated build records nothing and falls back to the file's
    own directory (keeping the catalog rsync-portable)."""

    data_root = tmp_path / "corpus"
    data_root.mkdir()
    art = _fake_sfs_artifact(data_root)

    # separated: catalog on its own (fast) root, corpus elsewhere
    cat_root = tmp_path / "fast"
    cat_root.mkdir()
    db = cat_root / "catalog.sqlite"
    catalog.rebuild(db, "sfs", [art], data_root=data_root, exclusive=True)
    con = catalog.connect(db)
    assert catalog.data_root(con) == data_root
    stored = con.execute("SELECT path FROM documents WHERE source='sfs'").fetchone()[0]
    assert not os.path.isabs(stored)                       # stored data_root-relative
    assert catalog.load_artifact(catalog.data_root(con), stored)["title"] == "Testlag"
    con.close()

    # colocated: no meta row, so data_root falls back to the file's parent
    db2 = data_root / "catalog.sqlite"
    catalog.rebuild(db2, "sfs", [art], data_root=data_root)
    con2 = catalog.connect(db2)
    assert con2.execute("SELECT value FROM meta WHERE key='data_root'").fetchone() is None
    assert catalog.data_root(con2) == data_root
    con2.close()


def test_cmd_relate_full_rebuild_builds_via_scratch_and_swaps(monkeypatch, tmp_path):
    """A missing catalog and (--force + whole corpus) each trigger a full rebuild
    that builds a scratch file and atomically swaps it in, leaving no `.building`
    file behind; an incremental relate writes in place and never makes a scratch."""

    data_root = tmp_path / "corpus"
    data_root.mkdir()
    art = _fake_sfs_artifact(data_root)
    cat = tmp_path / "fast" / "catalog.sqlite"          # parent doesn't exist yet
    scratch = cat.with_name("catalog.sqlite.building")

    monkeypatch.setattr(build, "DATA", data_root)
    monkeypatch.setattr(build, "CATALOG", cat)
    monkeypatch.setattr(build, "WATERMARKS", tmp_path / "wm.json")
    monkeypatch.setattr(build, "RUNS", tmp_path / "runs.ndjson")
    monkeypatch.setattr(build, "ERRORS", tmp_path / "err.json")
    monkeypatch.setattr(build, "STATUS", tmp_path / "status.json")
    # the whole relatable corpus is just this one source, so `relate sfs` == "all"
    monkeypatch.setattr(build, "ARTIFACTS", {"sfs": lambda: [art]})
    # neutralise the cross-document post-passes (their own coverage is elsewhere)
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(annstore, "tree", lambda s: empty)
    monkeypatch.setattr(build.fa_genomforande, "resolve", lambda con: 0)
    monkeypatch.setattr(build.fa_fk, "resolve", lambda con: 0)
    monkeypatch.setattr(build.catalog, "set_correspondence", lambda con, rows: None)
    monkeypatch.setattr(build, "kommentar_anchor_warnings", lambda con: [])
    # count swaps so we can tell a full rebuild (scratch+swap) from an incremental
    swaps = []
    real_swap = build._swap_catalog
    monkeypatch.setattr(build, "_swap_catalog",
                        lambda s, d: (swaps.append(d), real_swap(s, d))[1])

    # 1) missing catalog -> full rebuild via scratch+swap
    assert not cat.exists()
    build.cmd_relate(["sfs"])
    assert cat.exists() and not scratch.exists()
    assert len(swaps) == 1
    con = catalog.connect(cat)
    assert con.execute("SELECT COUNT(*) FROM documents WHERE source='sfs'").fetchone()[0] == 1
    # separated layout -> the corpus root was recorded and resolves
    assert catalog.data_root(con) == data_root
    con.close()

    # 2) --force over the whole corpus -> full rebuild again (a second swap)
    build.RUN.force = True
    build.cmd_relate(["sfs"])
    build.RUN.force = False
    assert cat.exists() and not scratch.exists()
    assert len(swaps) == 2

    # 3) incremental (catalog present, no force) -> in place, no scratch, no swap
    build.cmd_relate(["sfs"])
    assert not scratch.exists()
    assert len(swaps) == 2


def test_swap_catalog_discards_stale_wal_of_old_catalog(tmp_path):
    """The atomic swap must quiesce the *old* catalog's WAL first. A live catalog is
    in WAL mode (any incremental relate leaves it so) and the serving layer keeps a
    `-wal`/`-shm` beside it; SQLite pairs a `-wal` with a database by filename, so a
    stale one left after the rename is silently re-applied onto the swapped-in file
    -- serving a corrupt old/new mix that `integrity_check` still calls "ok". Guards
    against a regression where `_swap_catalog` renames without discarding sidecars."""
    dest = tmp_path / "catalog.sqlite"
    # an old catalog in WAL mode with committed-but-uncheckpointed frames (tag OLD),
    # left open like a live serving connection so the sidecars persist through swap
    live = sqlite3.connect(dest)
    live.execute("PRAGMA journal_mode=WAL")
    live.execute("PRAGMA wal_autocheckpoint=0")
    live.execute("CREATE TABLE d (uri TEXT, tag TEXT)")
    live.executemany("INSERT INTO d VALUES (?,?)", [(f"u{i}", "OLD") for i in range(200)])
    live.commit()
    assert dest.with_name("catalog.sqlite-wal").exists()   # the hazard is present
    reader = sqlite3.connect("file:%s?mode=ro" % dest, uri=True)
    reader.execute("BEGIN")
    reader.execute("SELECT COUNT(*) FROM d").fetchone()    # a reader mid-transaction

    # a freshly built scratch (journal OFF, single file) with tag NEW
    scratch = dest.with_name("catalog.sqlite.building")
    sc = sqlite3.connect(scratch)
    sc.execute("PRAGMA journal_mode=OFF")
    sc.execute("CREATE TABLE d (uri TEXT, tag TEXT)")
    sc.executemany("INSERT INTO d VALUES (?,?)", [(f"u{i}", "NEW") for i in range(200)])
    sc.commit()
    sc.close()

    build._swap_catalog(scratch, dest)

    fresh = sqlite3.connect("file:%s?mode=ro" % dest, uri=True)
    assert fresh.execute("SELECT DISTINCT tag FROM d").fetchall() == [("NEW",)]
    assert fresh.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert not dest.with_name("catalog.sqlite-wal").exists()
    assert not dest.with_name("catalog.sqlite-shm").exists()
    fresh.close()
    reader.close()
    live.close()


def test_fa_soukb_scans_passes_politeness(monkeypatch):
    called = {}

    def fake_sync(root, limit=None, delay=None):
        called["delay"] = delay
        return 0, 0

    monkeypatch.setattr(build.fa_soukb, "sync", fake_sync)
    build.fa_soukb_scans([])
    assert called.get("delay") == build.POLITENESS

