"""Unit tests for the incremental build driver's freshness engine
(accommodanda.build), exercised through a synthetic two-stage source over
temp files -- no real corpus, no JVM, fast."""

import json

import pytest

from accommodanda import build
from accommodanda.build import (Result, RunOptions, Source, Stage, build_one,
                               is_fresh)


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
