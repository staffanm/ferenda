"""Small shared utilities (ported from ferenda.util)."""

import re
import shutil
import sys
import time

# ETA timing state for `status`, self-tracked so callers need not thread a start
# time. A current/total run (sfs parse, then dv parse, …) is timed from its first
# line; a new run is detected when `done` restarts or `total` changes, which
# re-bases the clock -- so each source is estimated on its own pace.
_eta = {"start": None, "start_done": 0, "last_done": 0, "total": object()}


def _eta_suffix(done, total):
    """``ETA MM:SS`` for a current/total sequence, from (elapsed/processed) over
    the docs since timing began, or '' when there is no usable estimate (the
    first line of a run, an unknown total, or the final line)."""
    now = time.monotonic()
    if done <= 1 or done < _eta["last_done"] or total != _eta["total"]:
        _eta.update(start=now, start_done=done, last_done=done, total=total)
        return ""                                  # first line of the run: re-base
    _eta["last_done"] = done
    processed = done - _eta["start_done"]
    elapsed = now - _eta["start"]
    if total is None or done >= total or processed <= 0 or elapsed <= 0:
        return ""
    remaining = (elapsed / processed) * (total - done)
    return "ETA %02d:%02d" % divmod(int(remaining + 0.5), 60)


def status(done, total, message="", *, prefix="", tail="", stream=sys.stderr):
    """The single live one-line progress counter, overwritten in place -- shared
    by the per-document build loops (parse, generate, index, dump, bulk unpack)
    *and* the source-downloader harvest reporter (`progress`). Renders
    ``[prefix](<done>/<total>) <message>[tail]`` refreshed per item via a leading
    '\\r', with an ``ETA MM:SS`` estimate right-aligned to the terminal edge.
    `prefix` (a harvest's clock/scope/page) precedes the counter; `tail` (a
    harvest's ``[+dt]``) follows the message. '\\033[K' clears any tail a longer
    previous line left. The loop writes one trailing newline at the end (the line
    lives on stderr, so stdout summaries stay clean)."""
    line = "%s(%d/%s) %s%s" % (prefix, done, "?" if total is None else total,
                               message, tail)
    eta = _eta_suffix(done, total)
    if eta:
        width = shutil.get_terminal_size((80, 24)).columns
        pad = width - 1 - len(line) - len(eta)
        line += (" " * pad + eta) if pad > 0 else ("  " + eta)
    stream.write("\r%s\033[K" % line)
    stream.flush()


def hms(seconds):
    """A compact human duration: '9.1s', '1m42s', '1h07m'."""
    if seconds < 60:
        return "%.1fs" % seconds
    minutes, secs = divmod(int(seconds), 60)
    if minutes < 60:
        return "%dm%02ds" % (minutes, secs)
    hours, minutes = divmod(minutes, 60)
    return "%dh%02dm" % (hours, minutes)


def progress(seen, total=None, *, scope=None, page=None, elapsed=None,
             stamp=False, stream=sys.stderr, **counts):
    """One uniform, self-overwriting harvest line across the source downloaders:

        [HH:MM:SS] [scope ]page <p> (<seen>/<total>): <n> <label>, ... [+<dt>]

    Delegates to `status` -- the one renderer -- so the harvest line shares its
    `\\r` overwrite, '\\033[K', and right-aligned ``ETA MM:SS`` (shown whenever the
    total is known; '?' totals get none). The clock/scope/page form the `prefix`,
    the tallies the message, and ``[+<dt>]`` the `tail`. `total` None renders as
    '?'; `page` is omitted when None; `counts` are label=value pairs (new=...,
    skipped=..., changed=...) shown in call order. `stamp` prefixes the wall
    clock; `elapsed` (seconds since the previous line) is the [+<dt>] tail -- so a
    slow per-document fetch is visible as it happens. The caller ends a segment (a
    harvest year / page sweep) with progress_break(), dropping to a fresh line so
    the finished segment persists above the next live one."""
    clock = time.strftime("[%H:%M:%S] ") if stamp else ""
    head = "%s " % scope if scope else ""
    pg = "page %d " % page if page is not None else ""
    tally = ", ".join("%d %s" % (value, label) for label, value in counts.items())
    tail = " [+%s]" % hms(elapsed) if elapsed is not None else ""
    status(seen, total, tally, prefix="%s%s%s" % (clock, head, pg), tail=tail,
           stream=stream)


def progress_break(stream=sys.stderr):
    """End a run of overwriting progress() lines: drop to a fresh line so the
    finished segment (a harvest year / page sweep) persists above the live one."""
    stream.write("\n")
    stream.flush()


class Reporter:
    """Uniform harvest progress, shared by the four source downloaders so their
    reporting is identical despite their different enumeration (eurlex by year,
    sfs/dv by page, forarbete by doctype). Each harvest builds one Reporter and
    reports through it: a single self-overwriting line per segment carrying the
    wall clock, a scope/page label, the (seen/total) counter, the running
    tallies, and the time since the previous line.

      update(seen, total, scope=, page=, **counts)  -- rewrite the live line
      done()    -- end a segment (a year/sweep/doctype) with a newline so it stays
      reset()   -- rebase the elapsed clock, e.g. after a slow per-segment query
                   whose cost should not be billed to the segment's first item
    """

    def __init__(self):
        self._last = time.perf_counter()

    def update(self, seen, total, *, scope=None, page=None, **counts):
        now = time.perf_counter()
        progress(seen, total, scope=scope, page=page, stamp=True,
                 elapsed=now - self._last, **counts)
        self._last = now

    def reset(self):
        self._last = time.perf_counter()

    def done(self):
        progress_break()
        self._last = time.perf_counter()


ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}

# matches only well-formed roman numerals
re_roman = re.compile(
    r"^M?M?M?(CM|CD|D?C?C?C?)(XC|XL|L?X?X?X?)(IX|IV|V?I?I?I?)$").match


def normalize_space(s):
    return " ".join(s.split())


def split_numalpha(s):
    """'10 a §' -> ['', 10, ' a §'], so strings with mixed numbers and
    letters sort naturally."""
    res = []
    seg = ""
    if not s:
        return res
    if s[0].isdecimal():
        res.append("")  # every list starts with a string, so elements at
        # the same index always have comparable types
    digit = s[0].isdecimal()
    for c in s:
        if c.isdecimal() == digit:
            seg += c
        else:
            res.append(int(seg) if digit else seg)
            seg = c
            digit = not digit
    res.append(int(seg) if digit else seg)
    return res


def numcmp(x, y):
    nx, ny = split_numalpha(x), split_numalpha(y)
    return (nx > ny) - (nx < ny)


def from_roman(s):
    s = s.upper()
    total = 0
    prev = 0
    for c in reversed(s):
        val = ROMAN_VALUES[c]
        total += val if val >= prev else -val
        prev = max(prev, val)
    return total


SWEDISH_ORDINALS = ("första", "andra", "tredje", "fjärde", "femte", "sjätte",
                    "sjunde", "åttonde", "nionde", "tionde", "elfte", "tolfte")
SWEDISH_ORDINAL_MAP = {word: i + 1 for i, word in enumerate(SWEDISH_ORDINALS)}


def swedish_ordinal(s):
    """'första' -> 1, or None"""
    return SWEDISH_ORDINAL_MAP.get(s.lower())
