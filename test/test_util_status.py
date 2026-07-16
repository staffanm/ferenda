"""The live progress counter (accommodanda.lib.util.status) and its one-row
line clipping -- the fix for long sö/lr förarbete basefiles wrapping the
terminal so the leading '\\r' could no longer overwrite them -- plus the
sliding-window ETA."""

import io

import pytest

from accommodanda.lib import util


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
    util._eta["samples"].clear()
    util._eta["total"] = object()                 # no run in progress
    return fake


def _eta_seconds(suffix):
    minutes, seconds = suffix.removeprefix("ETA ").split(":")
    return int(minutes) * 60 + int(seconds)


def test_eta_ignores_the_skipped_prefix_of_a_run(clock):
    # sfs mirror-pdf over a corpus that is 40k/75k mirrored: the skips cost ~0,
    # and must not bill the real downloads that follow at their rate
    total = 75_000
    for done in range(1, 40_001):
        clock.now += 0.0001                        # already on disk: skipped
        util._eta_suffix(done, total)
    suffix = ""
    for done in range(40_001, 40_001 + 2 * util._ETA_WINDOW):
        clock.now += 0.5                           # a real download
        suffix = util._eta_suffix(done, total)
    # ~35k items left at ~0.5s each, not the near-zero the run-long average gave
    assert _eta_seconds(suffix) == pytest.approx((total - done) * 0.5, rel=0.02)


def test_eta_uses_the_run_pace_before_any_window_of_items(clock):
    for done in range(1, 11):
        clock.now += 2.0
        suffix = util._eta_suffix(done, 100)
    assert _eta_seconds(suffix) == pytest.approx((100 - 10) * 2.0, rel=0.02)


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
