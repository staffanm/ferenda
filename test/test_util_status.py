"""The live progress counter (accommodanda.lib.util.status) and its one-row
line clipping -- the fix for long sö/lr förarbete basefiles wrapping the
terminal so the leading '\\r' could no longer overwrite them."""

import io

from accommodanda.lib import util


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
