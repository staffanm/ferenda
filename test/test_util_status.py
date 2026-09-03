"""The live progress counter (ferenda.lib.util.status) and its one-row
line clipping -- the fix for long sö/lr förarbete basefiles wrapping the
terminal so the leading '\\r' could no longer overwrite them -- plus the
whole-run ETA paced on the work actually performed."""

import io
import os
import shutil
import signal
import sys
import warnings

import pytest

from ferenda.lib import util


@pytest.fixture(autouse=True)
def _pretend_stderr_is_a_tty(monkeypatch):
    # invocation_bar() only opens the real two-bar machinery on a tty (see
    # test_invocation_bar_falls_back_to_a_no_op_off_a_tty below) -- every
    # other test in this file is exercising that machinery itself, on
    # pytest's own non-tty captured streams, so it needs to look like one.
    # Patched on the function, not the current sys.stderr instance: a test
    # using `capsys` swaps sys.stderr for its own capture object *after*
    # fixture setup, which would silently revert an instance-level patch.
    monkeypatch.setattr(util, "_stderr_is_a_tty", lambda: True)


class FakeClock:
    """A monotonic clock the ETA tests advance by hand."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    fake = FakeClock()
    monkeypatch.setattr(util.time, "monotonic", fake)
    util._eta.update(t0=0.0, actual0=0, total=object(), done=-1,
                 work0=0.0)                     # no run in progress
    return fake


def _eta_seconds(suffix):
    minutes, seconds = suffix.removeprefix("ETA ").split(":")
    return int(minutes) * 60 + int(seconds)


def test_eta_paces_on_actual_work_not_the_skipped_prefix(clock):
    # sfs mirror-pdf over a corpus that is 40k/75k mirrored: the skips cost ~0 and
    # don't advance `actual`, so the real downloads that follow set the pace -- not
    # the near-zero a run-long average over every job seen would have produced
    total = 75_000
    for done in range(1, 40_001):                  # already on disk: skipped, no work
        util._eta_suffix(done, total, actual=0)
    suffix = ""
    for built, done in enumerate(range(40_001, 41_001), 1):
        clock.now += 0.5                           # a real download
        suffix = util._eta_suffix(done, total, actual=built)
    assert _eta_seconds(suffix) == pytest.approx((total - done) * 0.5, rel=0.02)


def test_eta_stays_put_across_a_burst_of_skips(clock):
    # a window ETA lurches when the last N items are all fast skips; the whole-run
    # pace over `actual` does not -- the estimate holds at the real per-build rate
    total = 10_000
    suffix = ""
    for built, done in enumerate(range(1, 201), 1):
        clock.now += 1.0                           # 200 real builds at 1s each
        suffix = util._eta_suffix(done, total, actual=built)
    steady = _eta_seconds(suffix)
    for done in range(201, 401):                   # then 200 instant skips in a row
        suffix = util._eta_suffix(done, total, actual=200)
    # remaining fell by 200 jobs; the per-build rate (1s) is unmoved by the skips
    assert _eta_seconds(suffix) == pytest.approx(steady - 200, abs=2)


def test_eta_uses_the_run_pace_when_every_job_is_real(clock):
    for done in range(1, 11):
        clock.now += 2.0
        suffix = util._eta_suffix(done, 100)       # no `actual`: every job counts
    assert _eta_seconds(suffix) == pytest.approx((100 - 10) * 2.0, rel=0.02)


def test_eta_absent_until_the_first_real_job(clock):
    # a run that opens with skips shows no ETA -- there is no measured pace yet
    for done in range(1, 51):
        clock.now += 0.1
        assert util._eta_suffix(done, 100, actual=0) == ""


def test_eta_rebases_on_a_new_run(clock):
    for done in range(1, 21):
        clock.now += 2.0
        util._eta_suffix(done, 100)
    assert util._eta_suffix(1, 40) == ""           # a new current/total sequence
    clock.now += 0.1
    assert _eta_seconds(util._eta_suffix(2, 40)) == pytest.approx(38 * 0.1, abs=1)


def test_eta_absent_without_a_usable_estimate(clock):
    assert util._eta_suffix(1, 100) == ""          # first line of the run
    clock.now += 1.0
    assert util._eta_suffix(100, 100) == ""        # final line
    clock.now += 1.0
    assert util._eta_suffix(1, None) == ""         # unknown total
    clock.now += 1.0
    assert util._eta_suffix(2, None) == ""


def test_fit_line_clips_long_line_to_one_row():
    line = "(12129/15240) forarbete parse  ran 0  err 0  so/" + "x" * 200
    out = util._fit_line(line, "", width=80)
    assert len(out) == 79                       # never reaches the auto-wrap column
    assert out == line[:79]                     # the (long) message tail is cut


def test_fit_line_keeps_eta_right_aligned():
    out = util._fit_line("(5/100) forarbete parse  " + "y" * 200, "ETA 02:13", 80)
    assert len(out) == 79
    assert out.endswith("ETA 02:13")            # ETA survives at the edge
    assert "forarbete parse" in out             # counter/label kept, tail clipped


def test_fit_line_short_line_unpadded_without_eta():
    assert util._fit_line("(5/100) done", "", 80) == "(5/100) done"


def test_fit_line_short_line_padded_with_eta():
    out = util._fit_line("(5/100) done", "ETA 00:30", 80)
    assert len(out) == 79 and out.startswith("(5/100) done") and out.endswith("ETA 00:30")


def test_fit_line_drops_eta_when_row_too_narrow():
    out = util._fit_line("(5/100) working", "ETA 00:30", width=8)
    assert out == "(5/100)" and len(out) == 7   # 7-col budget, no room for the ETA


def test_status_off_tty_keeps_full_line():
    # a redirected (non-tty) stream never wraps, so the long basefile is preserved
    buf = io.StringIO()                          # StringIO.isatty() is False
    long_bf = "so/" + "z" * 200
    util.status(1, 10, "forarbete parse  " + long_bf, stream=buf)
    written = buf.getvalue()
    assert written.startswith("\r") and written.endswith("\033[K")
    assert long_bf in written                    # not clipped off a tty


def test_eta_paces_on_work_not_job_count_when_costs_are_given(clock):
    # the driver dispatches longest-expected first, so the jobs finished at any
    # point are the most expensive in the corpus. Pacing on job count then
    # applies the worst-case per-job rate to every job left -- the reason a full
    # förarbete reparse opened at ~57 h. Here: 100 jobs, the first 10 costing 10s
    # each and the remaining 90 costing 1s, one worker.
    costs = [10.0] * 10 + [1.0] * 90
    total_work, done_work = sum(costs), 0.0
    suffix = ""
    for done, cost in enumerate(costs[:10], 1):
        clock.now += cost
        done_work += cost
        suffix = util._eta_suffix(done, len(costs), actual=done,
                                  work=(done_work, total_work))
    # truth after the expensive head: 90 jobs x 1s = 90s
    assert _eta_seconds(suffix) == pytest.approx(90, rel=0.02)
    # the count-based estimate would have said 90 jobs x 10s/job
    assert _eta_seconds(util._eta_suffix(10, len(costs), actual=10)) > 800


def test_eta_survives_a_half_fresh_corpus(clock):
    # a fresh skip costs no wall-clock but carries its expected seconds, so it
    # dilutes the measured rate and the remaining work by the same factor and
    # the dilution cancels -- which is what keeps a half-stale run honest
    costs = [2.0] * 200
    total_work, done_work, real = sum(costs), 0.0, 0
    suffix = ""
    for done, cost in enumerate(costs[:100], 1):
        if done % 2:                       # every other job is a real build
            clock.now += cost
            real += 1
        done_work += cost
        suffix = util._eta_suffix(done, len(costs), actual=real,
                                  work=(done_work, total_work))
    # 100 jobs left, half of them real, 2s each => 100s
    assert _eta_seconds(suffix) == pytest.approx(100, rel=0.05)


def test_eta_without_costs_still_paces_on_job_count(clock):
    # callers with no per-item estimate (harvests) keep the old behaviour
    for done in range(1, 11):
        clock.now += 1.0
        suffix = util._eta_suffix(done, 100, actual=done)
    assert _eta_seconds(suffix) == pytest.approx(90, rel=0.05)


# --------------------------------------------------------------------------
# util.write -- the safe way to print a persistent line beside a live counter
# --------------------------------------------------------------------------

def test_write_breaks_the_counter_then_prints_on_the_named_stream():
    buf = io.StringIO()
    util.status(3, 10, "working", stream=buf)
    util.write("a persistent note", stream=buf)
    written = buf.getvalue()
    assert written == "\r(3/10) working\033[K\na persistent note\n"


def test_write_defaults_to_stdout_and_breaks_real_stderr(monkeypatch, capsys):
    err_buf = io.StringIO()
    monkeypatch.setattr(util.sys, "stderr", err_buf)
    util.write("hello")
    assert err_buf.getvalue() == "\n"           # the live counter's stream, broken
    assert capsys.readouterr().out == "hello\n"  # the message itself, on stdout


def test_write_err_true_prints_the_message_on_stderr():
    buf = io.StringIO()
    util.write("boom", err=True, stream=buf)
    assert buf.getvalue() == "\nboom\n"


def test_install_warnings_hook_routes_through_write(monkeypatch):
    err_buf = io.StringIO()
    monkeypatch.setattr(util.sys, "stderr", err_buf)
    original = warnings.showwarning
    try:
        util.install_warnings_hook()
        warnings.warn("something worth knowing", UserWarning)
    finally:
        warnings.showwarning = original
    out = err_buf.getvalue()
    assert out.startswith("\n")                 # the counter break
    assert "UserWarning: something worth knowing" in out


# --------------------------------------------------------------------------
# the whole-invocation bar: status() nests beneath it instead of drawing the
# lone overwriting line, and falls back to the plain line once it is closed
# --------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _no_leaked_bar():
    # invocation_bar's own finally always clears _outer/_inner, but a test
    # that raises before reaching it must not leak a bar into the next test
    yield
    if util._outer is not None:
        util._outer.close()
        util._outer = None


def test_status_renders_nested_while_an_invocation_bar_is_open():
    with util.invocation_bar(10.0, 1, desc="lagen all rebuild") as ib:
        assert util._outer is ib
        ib.start("syn parse")
        util.status(1, 5, "item 1")
        assert util._inner is not None
        assert util._inner.total == 5
        assert util._inner.n == 1
        ib.finish()
    assert util._outer is None
    assert util._inner is None                  # closed with the outer bar


def test_invocation_bar_start_primes_the_nested_bar_before_any_status_call():
    # a source's own setup before its first status() call (parse's
    # per-basefile freshness gate, especially on a source with many
    # never-built basefiles) can take long enough that the second line
    # would otherwise sit blank -- reading as hung, not merely quiet.
    # start() must show something immediately, before the caller ever
    # calls status() for this step.
    with util.invocation_bar(10.0, 1) as ib:
        ib.start("forarbete parse")
        assert util._inner is not None
        assert util._inner.n == 0
        assert util._inner.total is None


def test_priming_does_not_stick_the_total_once_real_progress_arrives():
    # the nested bar primed by start() carries an unknown (None) total;
    # the step's real first status() call reuses that same bar (same
    # step_no) rather than rebuilding it, so the total must still take
    # effect there, not stay stuck at the priming value forever
    with util.invocation_bar(10.0, 1) as ib:
        ib.start("forarbete parse")
        primed = util._inner
        util.status(1, 25018, "item 1")
        assert util._inner is primed              # same bar, not rebuilt
        assert util._inner.total == 25018
        assert util._inner.n == 1


def test_status_nested_paces_on_work_like_the_plain_line_does():
    with util.invocation_bar(10.0, 1) as _ib:
        util.status(1, 5, "item 1", work=(2.0, 8.0))
        assert util._inner.total == 8.0          # the work total, not the item total
        assert util._inner.n == 2.0
        util.status(2, 5, "item 2", work=(4.0, 8.0))
        assert util._inner.n == 4.0              # same bar, updated in place


def test_status_opens_a_fresh_nested_bar_on_a_new_stage():
    # matches corpus.cmd_all's real usage: every step is bracketed by
    # ib.start()/ib.finish(), which is the actual rebase signal (see below)
    with util.invocation_bar(10.0, 2) as ib:
        ib.start("stage one")
        util.status(5, 5, "last item of stage one")
        first_bar = util._inner
        ib.finish()
        ib.start("stage two")
        util.status(1, 3, "first item of stage two")
        assert util._inner is not first_bar
        assert util._inner.total == 3


def test_status_opens_a_fresh_nested_bar_even_when_the_new_stage_s_total_matches():
    # two never-built sources with equal basefile counts share a total (and,
    # costed by corpus.PLANNER_DEFAULT_SECS's flat fallback, a work total too)
    # -- routine on a fresh corpus, not a corner case. Rebasing on the outer
    # step count (ib.start(), the real step boundary) rather than on total
    # catches this; rebasing on total alone would miss it and leave the first
    # stage's elapsed clock ticking under the second stage's numbers.
    with util.invocation_bar(10.0, 2) as ib:
        ib.start("source a parse")
        util.status(3, 5, "item")
        first_bar = util._inner
        ib.finish()
        ib.start("source b parse")
        util.status(1, 5, "item")   # same total (5) as the previous stage
        assert util._inner is not first_bar


def test_invocation_bar_step_advances_by_real_elapsed_time(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(util.time, "perf_counter", clock)
    with util.invocation_bar(10.0, 2) as ib:
        ib.start("syn parse")
        clock.now += 3.0
        ib.finish()
        assert ib.bar.n == pytest.approx(3.0)
        ib.start("syn relate")
        clock.now += 2.0
        ib.finish()
        assert ib.bar.n == pytest.approx(5.0)


def test_invocation_bar_tracks_step_count():
    with util.invocation_bar(10.0, 3) as ib:
        ib.start("one")
        assert ib.step_no == 1
        ib.finish()
        ib.start("two")
        assert ib.step_no == 2
        ib.finish()


def test_invocation_bar_shows_the_step_count_not_predicted_seconds():
    # n/total (predicted-vs-actual seconds) still pace the bar's fill/ETA,
    # but a reader wants "how many steps in", not a seconds total nobody
    # could predict precisely -- see corpus.build_invocation_plan
    with util.invocation_bar(30000.0, 25) as ib:
        for _ in range(4):
            ib.start("eurlex parse")
            ib.finish()
        rendered = str(ib.bar)
    assert "4/25" in rendered
    assert "30000" not in rendered


def test_both_bars_columns_line_up():
    with util.invocation_bar(10.0, 1) as ib:
        ib.start("eurlex parse")
        util.status(12, 8326, "eurlex parse  ran 12  err 0  62022TJ0082")
        inner, outer = str(util._inner), str(ib.bar)
    assert inner.index("|") == outer.index("|")


def test_status_refreshes_the_outer_bar_between_steps():
    # status() is called far more often than start()/finish() (per document,
    # not per stage) -- it must piggyback a refresh so the outer bar's
    # elapsed time visibly progresses instead of sitting frozen until the
    # next step starts, which reads as stuck rather than "elapsed"
    with util.invocation_bar(10.0, 1) as ib:
        ib.start("eurlex parse")
        calls = []
        ib.bar.refresh = lambda *a, **kw: calls.append(1)
        util.status(1, 10, "item")
        assert calls


def test_status_falls_back_to_the_plain_line_once_the_bar_closes():
    with util.invocation_bar(1.0, 1):
        pass
    buf = io.StringIO()
    util.status(1, 10, "back to normal", stream=buf)
    assert buf.getvalue().startswith("\r(1/10) back to normal")


# --------------------------------------------------------------------------
# _TqdmRedirect -- a plain print() during an open invocation bar must not
# tear it (the "up to date -- skipped" line landing mid-bar was exactly this)
# --------------------------------------------------------------------------

def test_invocation_bar_redirects_print_through_tqdm_write(capsys):
    with util.invocation_bar(10.0, 1) as ib:
        assert sys.stdout is not sys.__stdout__     # redirected for the duration
        ib.start("sfs parse")
        print("parse sfs: up to date -- skipped")
        ib.finish()
    out = capsys.readouterr().out
    assert out.strip() == "parse sfs: up to date -- skipped"


def test_streams_are_restored_after_the_bar_closes(capsys):
    before_out, before_err = sys.stdout, sys.stderr
    with util.invocation_bar(1.0, 1):
        pass
    assert sys.stdout is before_out and sys.stderr is before_err


def test_streams_are_restored_even_if_the_body_raises():
    before_out, before_err = sys.stdout, sys.stderr
    with pytest.raises(RuntimeError):
        with util.invocation_bar(1.0, 1):
            raise RuntimeError("boom")
    assert sys.stdout is before_out and sys.stderr is before_err


def test_a_bare_newline_print_does_not_open_a_blank_line(capsys):
    with util.invocation_bar(1.0, 1):
        print()                        # print()'s own trailing "\n" content
    assert capsys.readouterr().out == ""


def test_invocation_bar_falls_back_to_a_no_op_off_a_tty(monkeypatch):
    # a stacked-position tqdm bar writes raw cursor-up ANSI codes to any file
    # it draws on, tty or not -- escape-code soup mixed into a
    # `lagen all rebuild > log 2>&1` log, which nobody is watching live to
    # begin with. Off a tty, invocation_bar must not open that machinery at
    # all: status() then falls through to its own plain single-line form.
    # (status()'s `stream` default binds sys.stderr at import time, not
    # per-test, so this checks an explicit stream rather than via capsys --
    # same pattern as the other status()-content tests in this file.)
    monkeypatch.setattr(util, "_stderr_is_a_tty", lambda: False)
    stdout_before = sys.stdout
    buf = io.StringIO()
    with util.invocation_bar(10.0, 2) as ib:
        assert util._outer is None
        assert sys.stdout is stdout_before           # no redirect installed
        ib.start("sfs parse")                        # no-op, must not raise
        util.status(1, 5, "item", stream=buf)
        ib.finish()
    out = buf.getvalue()
    # \033[K (clear-to-EOL) is status()'s own long-standing single-row
    # convention, present either way; \x1b[A (cursor UP a row) is what a
    # stacked-position tqdm bar adds and must not appear here
    assert "\x1b[A" not in out
    assert "(1/5) item" in out                        # the plain line still rendered


# --------------------------------------------------------------------------
# the nested bar's fixed formatting: "elapsed: desc |bar| n/total, ETA
# remaining" -- elapsed leads (plain wall-clock, not buried in tqdm's own
# "[elapsed<remaining]"), no percentage (redundant beside a bar and a count),
# ETA is a labelled field. The printed n/total is always the real document
# count (`done`/`total`), never the cost-paced `work` totals that drive the
# bar's own fill/rate/ETA when given: those are per-document expected-
# *seconds* weights summed across a whole stage, not a count of anything
# real, and printing them (as tqdm's own {n}/{total} fields would) reads as
# broken beside the bar's *actual* elapsed time -- "172927/178333, ETA
# 00:03" is not reconcilable with three real seconds. The real count is
# baked into the format string as plain text instead, decoupled from
# whatever paces the bar.
# --------------------------------------------------------------------------


def test_cost_paced_bar_shows_the_real_document_count_not_the_work_totals():
    with util.invocation_bar(10.0, 1):
        util.status(362, 5000, "forarbete parse  ran 362  err 0  prop/1915-133",
                   work=(172927.0, 178333.0))
        rendered = str(util._inner)
    assert "362/5000" in rendered      # the real count -- what a reader wants
    assert "172927" not in rendered and "178333" not in rendered

def test_nested_bar_leads_with_plain_elapsed_time():
    with util.invocation_bar(10.0, 1):
        util.status(1, 5, "item", work=(1.0, 2.0))
        rendered = str(util._inner)
    assert rendered.startswith("00:00: ")
    assert "<" not in rendered and "[" not in rendered   # no tqdm elapsed<remaining bracket


def test_nested_bar_eta_is_a_labelled_field_with_no_percentage():
    with util.invocation_bar(10.0, 1):
        util.status(362, 5000, "forarbete parse  ran 362  err 0  prop/1915-133",
                   work=(172927.0, 178333.0))
        rendered = str(util._inner)
    assert "ETA" in rendered
    assert "%" not in rendered


def test_cost_paced_bar_never_shows_the_raw_work_totals():
    with util.invocation_bar(10.0, 1):
        util.status(362, 5000, "forarbete parse  ran 362  err 0  prop/1915-133",
                   work=(172927.0, 178333.0))
        rendered = str(util._inner)
    assert "172927" not in rendered
    assert "178333" not in rendered


def test_item_count_bar_still_shows_a_real_n_of_total():
    with util.invocation_bar(10.0, 1):
        util.status(5, 10, "some item")
        rendered = str(util._inner)
    assert "5/10" in rendered


def test_nested_bar_formats_cost_totals_with_fixed_precision():
    # even though n/total are no longer printed, the bar fill and ETA are
    # still computed from them -- so a raw, unbounded-precision float must
    # never reach the rendered line through any other field either
    with util.invocation_bar(10.0, 1):
        util.status(1, 5, "forarbete parse  ran 923  err 0  prop/1960-126",
                   work=(166212.635, 178363.911))
        rendered = str(util._inner)
    assert "166212.63500000146" not in rendered


def test_nested_bar_never_shows_the_rate_as_seconds_per_second():
    with util.invocation_bar(10.0, 1):
        util.status(1, 5, "item", work=(1.0, 2.0))
        rendered = str(util._inner)
    assert "s/s" not in rendered
    assert ", s" not in rendered      # tqdm's own postfix comma, also avoided


def test_current_stage_bar_renders_above_the_invocation_bar():
    # tqdm's own convention: position 0 is the anchor row (topmost among
    # bars opened together), each higher `position=` one row further down --
    # stored internally negated, so a *higher* .pos is the row further up
    with util.invocation_bar(10.0, 1) as ib:
        ib.start("forarbete parse")
        util.status(1, 5, "item")
        assert util._inner.pos > ib.bar.pos


def test_nested_bar_drops_the_outer_bar_s_own_label_prefix():
    with util.invocation_bar(10.0, 1) as ib:
        ib.start("forarbete parse")
        util.status(923, 5000, "forarbete parse  ran 923  err 0  prop/1960-126")
        rendered = str(util._inner)
    assert "forarbete parse" not in rendered
    assert "ran 923  err 0  prop/1960-126" in rendered


def test_nested_bar_description_has_a_consistent_width():
    with util.invocation_bar(10.0, 1) as ib:
        ib.start("forarbete parse")
        util.status(1, 5, "forarbete parse  ran 923  err 0  prop/1960-126")
        short = util._inner.desc
        util.status(2, 5, "forarbete parse  ran 1444  err 0  "
                   "lr/2013-andring-i-arbetsformedlingens-register")
        long = util._inner.desc
    assert len(short) == len(long) == util._desc_width(None)


# --------------------------------------------------------------------------
# _desc_width -- a fixed description width doesn't fit a narrow terminal:
# below ~80 columns, format_meter cannot make the whole line (elapsed + desc
# + bar + count + ETA) fit even with the bar's own fill shrunk to nothing,
# and falls back to a raw line[:ncols] truncation -- a mid-number cut whose
# exact point shifts frame to frame as digit counts change, not graceful
# degradation. A tmux pane narrower than that hits this reliably even in a
# wide outer window, with no resize signal involved at all.
# --------------------------------------------------------------------------

def test_desc_width_shrinks_for_a_narrow_terminal(monkeypatch):
    monkeypatch.setattr(shutil, "get_terminal_size", lambda *a: os.terminal_size((60, 24)))
    assert util._desc_width(None) == 60 - util._DESC_RESERVE


def test_desc_width_never_shrinks_below_the_floor(monkeypatch):
    monkeypatch.setattr(shutil, "get_terminal_size", lambda *a: os.terminal_size((20, 24)))
    assert util._desc_width(None) == util._DESC_WIDTH_MIN


def test_desc_width_caps_at_the_max_on_a_wide_terminal(monkeypatch):
    monkeypatch.setattr(shutil, "get_terminal_size", lambda *a: os.terminal_size((300, 24)))
    assert util._desc_width(None) == util._DESC_WIDTH_MAX


def test_narrow_terminal_line_still_fits_without_tqdm_s_raw_truncation(monkeypatch):
    # the actual regression: at the *old* fixed 45-column description, a
    # 60-column terminal made format_meter fall back to line[:ncols],
    # cutting a real number in half ("6167" -> "616"). _desc_width's output,
    # used as the description width, must keep the whole rendered line
    # inside that same ncols -- checked directly against format_meter
    # (bypassing tqdm's own dynamic_ncols, which needs a real tty and so
    # cannot be driven from this non-tty test harness at all; _desc_width
    # itself is what production code calls to size the description, and
    # that is exactly what this checks).
    # 63 and 79 are a real user's actual daily columns (a phone SSH client's
    # default and preferred tmux pane width) -- pinned explicitly, not just
    # covered by the surrounding sweep, since a "cramped but not corrupted"
    # bar at exactly these widths is the point, not incidental
    for ncols in (45, 50, 60, 63, 70, 79, 80, 120):
        monkeypatch.setattr(shutil, "get_terminal_size",
                            lambda *a, _n=ncols: os.terminal_size((_n, 24)))
        w = util._desc_width(None)
        with util.invocation_bar(10.0, 1):
            util.status(6167, 11247, "eurlex parse  ran 6167  err 0  32023R1542",
                       work=(2000.0, 30000.0))
            util._inner.set_description_str(
                "ran 6167  err 0  32023R1542"[:w].ljust(w))
            line = util._inner.format_meter(
                **{**util._inner.format_dict, "ncols": ncols})
        assert len(line) <= ncols
        assert "6167" in line   # never a mid-number cut, at any width tested


def test_outer_bar_also_fits_at_a_phone_tmux_pane_width(monkeypatch):
    for ncols in (63, 79):
        monkeypatch.setattr(shutil, "get_terminal_size",
                            lambda *a, _n=ncols: os.terminal_size((_n, 24)))
        with util.invocation_bar(30000.0, 25) as ib:
            ib.start("eurlex parse")
            w = util._desc_width(ib._file)
            ib.bar.set_description_str("eurlex parse"[:w].ljust(w))
            line = ib.bar.format_meter(**{**ib.bar.format_dict, "ncols": ncols})
        assert len(line) <= ncols
        assert "25" in line   # the step count survives, not just the bar


# --------------------------------------------------------------------------
# SIGWINCH -- a shrinking terminal leaves a stale, now-too-wide frame on
# screen that the terminal itself wraps onto an extra row before tqdm's
# per-bar cursor math has any way to know; forcing an immediate redraw on
# the resize signal is the standard mitigation (best-effort, not a guarantee
# against every resize-timing race)
# --------------------------------------------------------------------------

@pytest.mark.skipif(not hasattr(signal, "SIGWINCH"), reason="Unix only")
def test_invocation_bar_installs_and_restores_a_sigwinch_handler():
    prior = signal.getsignal(signal.SIGWINCH)
    with util.invocation_bar(10.0, 1):
        assert signal.getsignal(signal.SIGWINCH) is not prior
    assert signal.getsignal(signal.SIGWINCH) is prior


@pytest.mark.skipif(not hasattr(signal, "SIGWINCH"), reason="Unix only")
def test_sigwinch_handler_only_sets_a_flag_never_writes():
    # a signal handler must never do I/O: it can fire between any two
    # bytecodes, including mid-write to the stream a bar's own refresh()
    # would write to next, and reentering a buffered writer from inside
    # itself is a hard RuntimeError -- verified by actually reproducing that
    # arrival-during-a-write below, not just asserting the design intent
    with util.invocation_bar(10.0, 1) as ib:
        ib.start("eurlex parse")
        util.status(1, 5, "item")
        assert util._resize_pending is False
        os.kill(os.getpid(), signal.SIGWINCH)
        assert util._resize_pending is True   # set; nothing written yet


@pytest.mark.skipif(not hasattr(signal, "SIGWINCH"), reason="Unix only")
def test_pending_resize_clears_and_refreshes_both_bars_on_the_next_status_call():
    with util.invocation_bar(10.0, 1) as ib:
        ib.start("eurlex parse")
        util.status(1, 5, "item")
        os.kill(os.getpid(), signal.SIGWINCH)
        calls = []
        ib.bar.clear = lambda *a, **kw: calls.append("outer-clear")
        util._inner.clear = lambda *a, **kw: calls.append("inner-clear")
        util.status(2, 5, "item2")
        assert set(calls) == {"outer-clear", "inner-clear"}
        assert util._resize_pending is False   # consumed


@pytest.mark.skipif(not hasattr(signal, "SIGWINCH"), reason="Unix only")
def test_sigwinch_arriving_mid_write_does_not_crash():
    # reproduces the exact failure: the signal delivered while the process is
    # already inside a write() call to the same stream a bar draws on
    class _SignalOnFirstWrite:
        def __init__(self, real):
            self._real, self._armed = real, True
        def write(self, s):
            if self._armed:
                self._armed = False
                os.kill(os.getpid(), signal.SIGWINCH)
            return self._real.write(s)
        def flush(self):
            return self._real.flush()
        def isatty(self):
            return self._real.isatty()
        def fileno(self):
            return self._real.fileno()

    with util.invocation_bar(10.0, 1) as ib:
        ib.start("eurlex parse")
        ib.bar.fp = _SignalOnFirstWrite(ib.bar.fp)
        util.status(1, 5, "item", work=(1.0, 2.0))
        util.status(2, 5, "item2", work=(1.5, 2.0))   # must not raise


def test_resize_recovery_does_not_desync_the_two_bars_cursor_positions():
    # a hand-rolled .clear() on each bar separately (the first attempt at
    # this) moves the cursor with *relative* jumps that are only correct
    # when every other active bar was cleared in the same coordinated pass
    # first -- two independent calls, in whatever order, desynced the two
    # bars' cursor bookkeeping permanently after a single resize, so every
    # later refresh kept opening a new line forever instead of overwriting.
    # tqdm's own `external_write_mode` is the coordinated version; this pins
    # that each bar's own relative position (`.pos`) is unchanged by it.
    with util.invocation_bar(10.0, 1) as ib:
        ib.start("eurlex parse")
        util.status(1, 5, "item")
        before = (util._inner.pos, ib.bar.pos)
        os.kill(os.getpid(), signal.SIGWINCH)
        for i in range(2, 6):
            util.status(i, 5, "item%d" % i)   # consumes the pending resize
        assert (util._inner.pos, ib.bar.pos) == before


# --------------------------------------------------------------------------
# a forked build worker (multiprocessing.Pool's default start method on
# Linux) is a COW copy of the parent process, not a fresh interpreter -- it
# inherits _outer/_real_streams and the _TqdmRedirect-wrapped stdout/stderr
# verbatim. None of that is safe to touch from a child process (tqdm bar
# objects the child does not own; a stream write racing the parent's own),
# so freshness._worker_init resets it -- see reset_worker_state.
# --------------------------------------------------------------------------

def test_reset_worker_state_clears_the_inherited_bar_globals():
    with util.invocation_bar(10.0, 1) as ib:
        ib.start("eurlex parse")
        util.status(1, 5, "item")
        assert util._outer is not None and util._inner is not None  # inherited by a fork
        util.reset_worker_state()
        assert util._outer is None
        assert util._inner is None
        assert util._real_streams is None
        assert sys.stdout is sys.__stdout__ and sys.stderr is sys.__stderr__


def test_reset_worker_state_makes_write_use_its_plain_fallback():
    # the actual failure this guards against: a warning firing inside a
    # forked worker used to call write() -> tqdm.write() against the
    # *parent's* bar objects, racing the parent's own concurrent writes to
    # the same fd. After the reset, write() takes its ordinary
    # print-and-break path instead.
    with util.invocation_bar(10.0, 1):
        util.reset_worker_state()
        buf = io.StringIO()
        util.write("worker warning", stream=buf)
        assert buf.getvalue() == "\nworker warning\n"
