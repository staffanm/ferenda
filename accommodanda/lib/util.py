"""Small shared utilities (ported from ferenda.util)."""

import re
import sys
import time


def status(done, total, message="", *, stream=sys.stderr):
    """A live one-line progress counter, overwritten in place -- the pattern the
    per-document loops (parse, generate, bulk unpack) share: ``(<done>/<total>)
    <message>``, refreshed per item via a leading '\\r'. '\\033[K' clears any tail
    a longer previous line left. The loop writes one trailing newline at the end
    (the line lives on stderr, so stdout summaries stay clean)."""
    stream.write("\r(%d/%s) %s\033[K"
                 % (done, "?" if total is None else total, message))
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

    Rewritten in place per call via a leading '\\r' (the parse/unpack pattern),
    so a long sweep shows one live line instead of a scroll. `total` None renders
    as '?'; `page` is omitted when None; `counts` are label=value pairs (new=...,
    skipped=..., changed=...) shown in call order. `stamp` prefixes the wall
    clock; `elapsed` (seconds since the previous line) is appended as [+<dt>] --
    so a slow per-document fetch is visible as it happens. The caller ends a
    segment (a harvest year / page sweep) with progress_break(), dropping to a
    fresh line so the finished segment persists above the next live one."""
    clock = time.strftime("[%H:%M:%S] ") if stamp else ""
    head = "%s " % scope if scope else ""
    pg = "page %d " % page if page is not None else ""
    tally = ", ".join("%d %s" % (value, label) for label, value in counts.items())
    tail = " [+%s]" % hms(elapsed) if elapsed is not None else ""
    stream.write("\r%s%s%s(%d/%s): %s%s\033[K"
                 % (clock, head, pg, seen, "?" if total is None else total,
                    tally, tail))
    stream.flush()


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
