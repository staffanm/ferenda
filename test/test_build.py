"""Unit tests for the incremental build driver's freshness engine
(accommodanda.build), exercised through a synthetic two-stage source over
temp files -- no real corpus, no JVM, fast."""


import pytest

from accommodanda import build
from accommodanda.build import RunOptions, Source, Stage, build_one, is_fresh


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
