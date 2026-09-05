"""Unit tests for the upfront step plans the outer progress bar reads its
total from -- `corpus.build_invocation_plan` for `lagen all rebuild`/`all`,
`corpus.plan_verb_steps` for a single-verb run like `lagen all relate` -- over
a synthetic source, no real corpus."""

import dataclasses

import pytest

from ferenda.lib import corpus, freshness, layout, runlog
from ferenda.lib.stage import RunOptions, Source, Stage, set_run


@pytest.fixture(autouse=True)
def reset_run():
    set_run(RunOptions())
    freshness.recipe_version.cache_clear()
    yield
    set_run(RunOptions())


@pytest.fixture
def wired(monkeypatch, tmp_path):
    """Redirect every build state file into tmp_path, like test_build.py's
    `wire` fixture -- kept local so this file has no dependency on it."""
    bd = tmp_path / ".build"
    monkeypatch.setattr(freshness, "RUNS", bd / "runs.ndjson")
    monkeypatch.setattr(freshness, "MANIFEST", bd / "manifest.json")
    monkeypatch.setattr(freshness, "MANIFEST_DB", bd / "manifest.sqlite")
    monkeypatch.setattr(freshness, "FINGERPRINTS", bd / "fingerprints.json")
    monkeypatch.setattr(freshness, "_MANIFEST_CACHE", None)
    monkeypatch.setattr(freshness, "_FINGERPRINTS_CACHE", None)
    monkeypatch.setattr(layout, "CATALOG", tmp_path / "corpus" / "catalog.sqlite")
    return tmp_path


def _source(tmp_path, basefiles=("a", "b"), *, artifacts=None):
    """A one-stage (parse) synthetic source; `artifacts` defaults to one file
    per basefile under tmp_path (so relate/index/dump/generate plan a step)."""
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)

    def art(bf):
        return out / ("%s.json" % bf)

    for bf in basefiles:
        art(bf).write_text("{}")

    return Source("syn", lambda: list(basefiles), {
        "parse": Stage("parse", lambda bf: None, art, inputs=lambda bf: []),
    }, artifacts=(artifacts if artifacts is not None else
                 lambda: [art(bf) for bf in basefiles]))


def _by(plan, source, verb):
    return next(s for s in plan if s.source == source and s.verb == verb)


def test_unbuilt_source_plans_parse_as_not_skipped(wired):
    source = _source(wired)
    plan = corpus.build_invocation_plan({"syn": source}, ["syn"], whole_corpus=False)
    step = _by(plan, "syn", "parse")
    assert step.skip is False
    assert step.secs > 0    # expected_secs' mean fallback for two never-built basefiles


def test_fresh_source_plans_parse_as_skipped(wired):
    # the planner never calls stage_fingerprint (a per-basefile stat pass,
    # too expensive to pay twice) -- it predicts a skip from the run ledger's
    # own record of what happened last time this (step, source) ran
    source = _source(wired)
    runlog.emit_segment(freshness.RUNS, "run1", "parse", "syn", 0.0,
                        ran=0, status="skipped")
    plan = corpus.build_invocation_plan({"syn": source}, ["syn"], whole_corpus=False)
    step = _by(plan, "syn", "parse")
    assert step.skip is True and step.secs == 0.0


def test_a_run_that_actually_built_is_not_predicted_as_skipped(wired):
    source = _source(wired)
    runlog.emit_segment(freshness.RUNS, "run1", "parse", "syn", 12.0,
                        total=2, ran=2, status="ok")
    plan = corpus.build_invocation_plan({"syn": source}, ["syn"], whole_corpus=False)
    step = _by(plan, "syn", "parse")
    assert step.skip is False and step.secs == pytest.approx(12.0)


def test_planning_never_calls_stage_fingerprint(wired, monkeypatch):
    # stage_fingerprint stats every input file of every basefile -- exactly
    # the cost that made planning itself a felt delay before rebuild's first
    # printed line
    def _boom(*a, **kw):
        raise AssertionError("build_invocation_plan must not call stage_fingerprint")

    monkeypatch.setattr(freshness, "stage_fingerprint", _boom)
    source = _source(wired)
    corpus.build_invocation_plan({"syn": source}, ["syn"], whole_corpus=False)


def test_relate_is_always_planned_as_will_run(wired):
    # relate's own gate (file_fingerprint over every artifact) is a stat pass
    # per source -- too expensive to pay twice just to predict a skip, so the
    # plan always shows it as running, timed from history alone. Confirmed
    # here even with the catalog present and relate's real gate already
    # recorded fresh (the state under which a live run *would* skip it) --
    # the plan must not call source.artifacts() to notice that
    source = _source(wired)
    layout.CATALOG.parent.mkdir(parents=True, exist_ok=True)
    layout.CATALOG.touch()
    store = freshness.load_fingerprints()
    wm = freshness.file_fingerprint(source.artifacts())
    freshness.record_step(store, "relate", "syn", wm, corpus.RELATE_CODE)
    freshness.save_fingerprints(store)
    plan = corpus.build_invocation_plan({"syn": source}, ["syn"], whole_corpus=False)
    assert _by(plan, "syn", "relate").skip is False


def test_planning_never_calls_a_source_s_artifacts_lister(wired):
    # source.artifacts() walks the parsed-artifact tree on disk -- for a real
    # corpus, expensive enough that calling it once per named source before
    # printing anything is a felt delay at the start of `lagen all rebuild`.
    def _boom():
        raise AssertionError("build_invocation_plan must not call artifacts()")

    source = _source(wired, artifacts=_boom)
    corpus.build_invocation_plan({"syn": source}, ["syn"], whole_corpus=False)


def test_relate_index_and_dump_plan_one_step_per_source(wired):
    # cmd_relate/cmd_index/cmd_dump each announce one util.step per source
    # they visit, so the plan counts the same sequence -- one step per source,
    # plus relate's cross-document passes over the finished catalog
    a, b = _source(wired, ("a",)), _source(wired, ("b",))
    b = dataclasses.replace(b, name="syn2")   # Source is frozen on `name`
    plan = corpus.build_invocation_plan({"syn": a, "syn2": b}, ["syn", "syn2"],
                                        whole_corpus=False)
    for verb in ("relate", "index", "dump"):
        assert [s.source for s in plan if s.verb == verb] == ["syn", "syn2"]
    assert _by(plan, "", "relate cross-passes").skip is False


def test_download_steps_are_planned_only_for_a_run_that_downloads(wired):
    source = _source(wired)
    offline = corpus.build_invocation_plan({"syn": source}, ["syn"],
                                           whole_corpus=False)
    assert not [s for s in offline if s.verb == "download"]
    # the synthetic source has neither a harvest nor a download stage, so even
    # a downloading run plans no step for it -- the same condition
    # cmd_download_all's own loop skips on
    online = corpus.build_invocation_plan({"syn": source}, ["syn"],
                                          whole_corpus=False, download=True)
    assert not [s for s in online if s.verb == "download"]
    assert corpus.runs_step(source, "download") is False


def test_generate_step_per_source_when_not_whole_corpus(wired):
    source = _source(wired)
    plan = corpus.build_invocation_plan({"syn": source}, ["syn"], whole_corpus=False)
    step = _by(plan, "syn", "generate")
    assert step.label == "syn generate"


def test_generate_is_one_aggregate_step_for_whole_corpus(wired):
    source = _source(wired)
    plan = corpus.build_invocation_plan({"syn": source}, ["syn"], whole_corpus=True)
    generate_steps = [s for s in plan if s.verb == "generate"]
    assert len(generate_steps) == 1
    assert generate_steps[0].source == ""


def test_generate_step_survives_a_source_with_no_artifacts(wired):
    # site/stats/remisser register no `artifacts` lister but still get a
    # generate call from cmd_all -- the plan must not KeyError building it
    source = dataclasses.replace(_source(wired), artifacts=None)
    plan = corpus.build_invocation_plan({"syn": source}, ["syn"], whole_corpus=False)
    step = _by(plan, "syn", "generate")
    assert step.skip is False
    # no artifacts lister -> no relate/index/dump step (cmd_relate/index/dump
    # skip such sources outright), but generate is still planned
    assert not [s for s in plan if s.verb in ("relate", "index", "dump")
               and s.source == "syn"]


def test_history_uses_the_ledger_s_raw_median_seconds(wired):
    # no document-count scaling: that would need a fresh list_basefiles()/
    # artifacts() call per source, which is exactly the cost this plan must
    # not pay. The prediction is just "how long did this take last time"
    source = _source(wired)
    runlog.emit_segment(freshness.RUNS, "run1", "generate", "syn", 4.0,
                        total=2, ran=2, status="ok")
    runlog.emit_segment(freshness.RUNS, "run1", "generate", "syn", 6.0,
                        total=3, ran=3, status="ok")
    plan = corpus.build_invocation_plan({"syn": source}, ["syn"], whole_corpus=False)
    step = _by(plan, "syn", "generate")
    assert step.secs == pytest.approx(5.0)   # median of [4.0, 6.0]


def test_planning_never_calls_list_basefiles(wired):
    # _history_secs' estimate is the run ledger's own raw seconds -- no
    # document count needed at all, for any step, so no reason left to call
    # list_basefiles() during planning either
    source = _source(wired)
    called = []
    real_list = source.list_basefiles
    source.list_basefiles = lambda: (called.append(1), real_list())[1]
    corpus.build_invocation_plan({"syn": source}, ["syn"], whole_corpus=False)
    assert not called


def test_history_falls_back_to_a_default_for_a_step_never_timed(wired):
    source = _source(wired)
    plan = corpus.build_invocation_plan({"syn": source}, ["syn"], whole_corpus=False)
    assert _by(plan, "syn", "index").secs == corpus.PLANNER_DEFAULT_SECS


def test_a_large_never_built_source_does_not_inflate_the_total(wired):
    # expected_secs' per-basefile fallback (the corpus mean, or 1.0s with no
    # history at all) used to be summed over every never-built basefile here,
    # so a source with no manifest history at all -- a sparse manifest after a
    # recipe-version bump, or a genuinely first build -- turned "unknown" into
    # "918410s" for a real corpus. The ledger-based estimate must not scale
    # with document count when it has nothing to go on: PLANNER_DEFAULT_SECS
    # flat, however large the source is.
    basefiles = tuple("doc%d" % i for i in range(50_000))
    source = Source("syn", lambda: list(basefiles), {
        "parse": Stage("parse", lambda bf: None, lambda bf: wired / ("%s.json" % bf),
                       inputs=lambda bf: []),
    }, artifacts=lambda: [])
    plan = corpus.build_invocation_plan({"syn": source}, ["syn"], whole_corpus=False)
    assert _by(plan, "syn", "parse").secs == corpus.PLANNER_DEFAULT_SECS


# --------------------------------------------------------------------------
# plan_verb_steps: the same thing for a single-verb run (`lagen all relate`)
# --------------------------------------------------------------------------

def test_plan_verb_steps_counts_one_step_per_source(wired):
    a, b = _source(wired, ("a",)), _source(wired, ("b",))
    b = dataclasses.replace(b, name="syn2")
    sources = {"syn": a, "syn2": b}
    assert [s.label for s in corpus.plan_verb_steps(sources, ["syn", "syn2"],
                                                    "parse")] \
        == ["syn parse", "syn2 parse"]
    assert [s.label for s in corpus.plan_verb_steps(sources, ["syn", "syn2"],
                                                    "relate")] \
        == ["syn relate", "syn2 relate", "relate cross-passes"]


def test_plan_verb_steps_skips_a_source_that_lacks_the_verb(wired):
    source = _source(wired)                      # registers parse only
    assert corpus.plan_verb_steps({"syn": source}, ["syn"], "versions") == []
    assert corpus.runs_step(source, "versions") is False
    # ... and an artifact-less source gets no relate/index/dump step
    bare = dataclasses.replace(source, artifacts=None)
    assert corpus.plan_verb_steps({"syn": bare}, ["syn"], "dump") == []


def test_a_one_source_run_plans_a_single_step(wired):
    # which is what makes `lagen sfs parse` keep the plain one-line counter:
    # invocation_bar opens no outer bar below two steps
    source = _source(wired)
    assert len(corpus.plan_verb_steps({"syn": source}, ["syn"], "parse")) == 1
