"""Small shared utilities (ported from ferenda.util)."""

import os
import re
import shutil
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any


def text_slug(text, *, sep="-", maxlen=None):
    """A stable, URL/file-safe slug from arbitrary text: NFKD-folded to ASCII (so
    å/ä/ö/é/ü/… degrade to a/a/o/e/u), lower-cased, every run of non-alphanumerics
    collapsed to a single `sep`, optionally truncated to `maxlen` characters (the
    trailing `sep` a mid-word cut can leave is stripped). Stability, not
    readability, is the point -- it keys documents with no number of their own (a
    lagrådsremiss title, an atom feed id). The NFKD fold covers every diacritic,
    unlike a hand-rolled character map."""
    ascii_ = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", sep, ascii_.lower()).strip(sep)
    return slug[:maxlen].strip(sep) if maxlen else slug


def write_atomic(path, data):
    """Write `data` (bytes or str) to `path` via a same-directory temp file +
    atomic rename, so an interrupted run never leaves a partial file behind.
    The temp name is per-process unique: concurrent writers (parallel `lagen`
    invocations both pruning the runlog) must not consume each other's temp
    file -- with a fixed name, one writer's os.replace() raced away the file
    the other had just written and crashed it with FileNotFoundError."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp%d" % os.getpid())
    try:
        tmp.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def store_relpath(path, root):
    """Render an absolute `path` as a `root`-relative string, so an on-disk index
    (the catalog, the dv identity index, …) stays portable across data_root
    moves: an index rsync'd to a host with a different data_root still resolves
    via `load_relpath`. Raises if `path` is not under `root` -- a stray path from
    another root must surface, not be silently stored broken."""
    return str(Path(path).relative_to(root))


def load_relpath(root, stored):
    """Inverse of `store_relpath`: the absolute Path for a `root`-relative stored
    path, or None for an empty (stub) path."""
    return root / stored if stored else None


def basefile_slug(basefile):
    """Filesystem-safe form of a basefile; the true identifier lives in the
    record JSON, so this only has to be unique and stable."""
    return basefile.replace("/", "-").replace(":", "-").replace(" ", "_")


def record_path(root, subdir, basefile):
    """The harvest-record JSON path for `basefile` under `root/subdir`."""
    return Path(root) / subdir / (basefile_slug(basefile) + ".json")


def document_extension(data):
    """The file extension for a document, read from its leading magic bytes (a
    URL suffix or a served/on-disk extension is unreliable; the bytes are not).
    None when the bytes are not a document we recognize -- so a mislabelled asset
    (an image, an HTML error page served or stored as `.pdf`) is rejected rather
    than trusted."""
    if data[:4] == b"%PDF":
        return ".pdf"
    if data[:4] == b"PK\x03\x04":          # zip container -> Office Open XML
        return ".docx"
    if data[:4] == b"\xd0\xcf\x11\xe0":    # OLE compound document -> legacy .doc
        return ".doc"
    if data[:5] == b"{\\rtf":
        return ".rtf"
    if data[:4] == b"\xffWPC":             # WordPerfect
        return ".wpd"
    return None


def sniff_extension(path):
    """`document_extension` for an on-disk file, streamed -- only the leading
    8 bytes are read, so a large network-mounted asset isn't read whole just
    to inspect its header."""
    with open(path, "rb") as f:
        return document_extension(f.read(8))

# ETA timing state for `status`, self-tracked so callers need not thread a start
# time. A current/total run (sfs parse, then dv parse, …) is timed over its whole
# span from the first line; a new run is detected when `done` restarts or `total`
# changes, which rebases the clock -- so each source is estimated on its own pace.
#
# The estimate is (elapsed / actual) * (total - done): the measured cost of the
# work actually performed so far, times every job still ahead. `actual` is the
# running count of jobs that did real work, which the caller passes when it can
# tell a genuine build from a skip -- an incremental step walks a whole corpus but
# only rebuilds the documents whose inputs changed, skipping the rest near-
# instantly (`sfs mirror-pdf` over a 40k/75k-mirrored corpus skips those 40k at
# ~0s each). Dividing by `done` (every job seen) rather than `actual` would spread
# the real per-document cost across thousands of ~0s skips and start the ETA near
# zero, then have it climb as the skips ran out; dividing by `actual` bills each
# remaining job at the true per-build rate. We deliberately do NOT try to predict
# how many of the remaining jobs are real (that needs a second corpus walk); for a
# run whose skips and real work interleave evenly this over-estimates, but for the
# common skips-first / real-work-tail shape it is honest. A whole-run average (not
# a sliding window) is what keeps the number from swinging on a long queue: a
# window over the last N items lets a burst of fast skips or one slow document
# yank the estimate around, which is what `lagen all generate` showed. Callers
# that can't distinguish real work leave `actual` None, and every job counts.
_eta: dict[str, Any] = {"t0": 0.0, "actual0": 0, "total": object(), "done": -1}


def _eta_suffix(done, total, actual=None):
    """``ETA MM:SS`` for a current/total sequence, from the whole-run pace of the
    work actually performed, or '' when there is no usable estimate (the first
    line of a run, before any real work has happened, an unknown total, or the
    final line). `actual` is the running count of jobs that did work rather than
    being skipped as already up to date; when the caller can't tell, every job
    counts."""
    now = time.monotonic()
    work = done if actual is None else actual
    if done <= 1 or done < _eta["done"] or total != _eta["total"]:
        _eta.update(t0=now, actual0=work, total=total, done=done)  # re-base the run
        return ""
    _eta["done"] = done
    elapsed, performed = now - _eta["t0"], work - _eta["actual0"]
    if total is None or done >= total or performed <= 0 or elapsed <= 0:
        return ""
    remaining = (elapsed / performed) * (total - done)
    return "ETA %02d:%02d" % divmod(int(remaining + 0.5), 60)


def status(done, total, message="", *, actual=None, prefix="", tail="",
           stream=sys.stderr):
    """The single live one-line progress counter, overwritten in place -- shared
    by the per-document build loops (parse, generate, index, dump, bulk unpack)
    *and* the source-downloader harvest reporter (`progress`). Renders
    ``[prefix](<done>/<total>) <message>[tail]`` refreshed per item via a leading
    '\\r', with an ``ETA MM:SS`` estimate right-aligned to the terminal edge.
    `prefix` (a harvest's clock/scope/page) precedes the counter; `tail` (a
    harvest's ``[+dt]``) follows the message. '\\033[K' clears any tail a longer
    previous line left. The loop writes one trailing newline at the end (the line
    lives on stderr, so stdout summaries stay clean).

    On a terminal the line is clipped to one physical row: a line wider than the
    terminal wraps, and the leading '\\r' then only rewinds to the *last* wrapped
    row -- so the overflow of a long line (e.g. a sö/lr förarbete basefile) is
    left on screen instead of being overwritten. Any ETA stays right-aligned; the
    message is what gets clipped. Off a tty nothing wraps, so the full line is
    kept (and an 80-col ETA fallback preserved for redirected logs).

    `actual` is the running count of jobs that did real work (as opposed to being
    skipped as already up to date); pass it on a step that skips fresh items so the
    ETA is paced on the real builds and not diluted by the skips (see
    `_eta_suffix`)."""
    line = "%s(%d/%s) %s%s" % (prefix, done, "?" if total is None else total,
                               message, tail)
    eta = _eta_suffix(done, total, actual)
    if stream.isatty():
        line = _fit_line(line, eta, os.get_terminal_size(stream.fileno()).columns)
    elif eta:
        width = shutil.get_terminal_size((80, 24)).columns
        pad = width - 1 - len(line) - len(eta)
        line += (" " * pad + eta) if pad > 0 else ("  " + eta)
    stream.write("\r%s\033[K" % line)
    stream.flush()


def _fit_line(line, eta, width):
    """Clip `line` to a single `width`-column terminal row, keeping `eta`
    right-aligned at the edge -- the message is what gets cut. The ETA is dropped
    only when the row is too narrow to hold it with a gap. Bounds the result to
    ``width - 1`` columns so it never reaches the auto-wrap column."""
    budget = max(1, width - 1)
    if eta and budget > len(eta) + 1:
        line = line[:budget - len(eta) - 1]       # reserve a gap + the ETA at right
        return line + " " * (budget - len(line) - len(eta)) + eta
    return line[:budget]                           # no room for an ETA -- just clip


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
             stamp=False, note="", actual=None, stream=sys.stderr, **counts):
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
    status(seen, total, tally + note, actual=actual,
           prefix="%s%s%s" % (clock, head, pg), tail=tail, stream=stream)


def progress_break(stream=sys.stderr):
    """End a run of overwriting progress() lines: drop to a fresh line so the
    finished segment (a harvest year / page sweep) persists above the live one."""
    stream.write("\n")
    stream.flush()


def harvest_start(label, url):
    """The uniform banner that opens a harvest segment: ``<label>: Starting at
    <url>``. ``label`` is ``<source> <action>`` -- a source's ``download``, an
    extra action (``mirror-pdf``), or a subtype (``forarbete prop``). Printed once
    (to stdout, beside the segment's closing summary) before its live progress
    lines, so start and summary bracket the stderr progress uniformly across every
    source."""
    print("%s: Starting at %s" % (label, url), flush=True)


class Reporter:
    """Uniform harvest progress, shared by the four source downloaders so their
    reporting is identical despite their different enumeration (eurlex by year,
    sfs/dv by page, forarbete by doctype). Each harvest builds one Reporter and
    reports through it: a single self-overwriting line per segment carrying the
    wall clock, a scope/page label, the (seen/total) counter, the running
    tallies, and the time since the previous line.

      update(seen, total, scope=, page=, actual=, **counts)  -- rewrite the line
              (actual: count of items that did real work, not skips -- paces the ETA)
      done()    -- end a segment (a year/sweep/doctype) with a newline so it stays
      reset()   -- rebase the elapsed clock, e.g. after a slow per-segment query
                   whose cost should not be billed to the segment's first item
    """

    def __init__(self):
        self._last = time.perf_counter()
        self._shown = False        # a live line is on screen awaiting its newline

    def update(self, seen, total, *, scope=None, page=None, note="", actual=None,
               **counts):
        now = time.perf_counter()
        progress(seen, total, scope=scope, page=page, stamp=True,
                 elapsed=now - self._last, note=note, actual=actual, **counts)
        self._last = now
        self._shown = True

    def reset(self):
        self._last = time.perf_counter()

    def clear(self, stream=sys.stderr):
        """Wipe the current live line in place (no newline), so a persistent line
        can be printed cleanly above a still-running progress line -- the parallel
        harvest coordinator prints each finished agency's summary this way while
        its aggregate line keeps redrawing below."""
        if self._shown:
            stream.write("\r\033[K")
            stream.flush()
            self._shown = False

    def done(self):
        # only break to a fresh line when a live line was actually drawn: a
        # segment that showed nothing (an up-to-date source whose walk skipped
        # every item before the first update) must not emit a bare newline --
        # that is what littered `all download` with one blank line per idle source.
        if self._shown:
            progress_break()
            self._shown = False
        self._last = time.perf_counter()


class NullReporter:
    """A Reporter that draws nothing -- for a parallel harvest where many agencies
    run at once and their per-agency live lines would overwrite each other. Each
    worker reports through one of these; the coordinator shows a single aggregate
    line for the whole pool instead."""

    def update(self, *args, **counts):
        pass

    def reset(self):
        pass

    def clear(self):
        pass

    def done(self):
        pass


ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}

# matches only well-formed roman numerals
re_roman = re.compile(
    r"^M?M?M?(CM|CD|D?C?C?C?)(XC|XL|L?X?X?X?)(IX|IV|V?I?I?I?)$").match


def normalize_space(s):
    """Whitespace-collapsed and stripped display form. None-safe (an absent
    value normalizes to "")."""
    return " ".join((s or "").split())


def normalize_fold(s):
    """Whitespace-collapsed, stripped and case-folded -- the matching key for
    comparing titles/headings/terms case- and spacing-insensitively while the
    display form is kept elsewhere. None-safe (an absent value folds to ""); the
    lower-casing is what sets it apart from `normalize_space`."""
    return " ".join((s or "").split()).lower()


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


MONTHS: dict[str, int] = {m: i for i, m in enumerate(
    "januari februari mars april maj juni juli augusti september oktober "
    "november december".split(), 1)}
SV_DATE = re.compile(r"(\d{1,2})\s+(%s)\s+(\d{4})" % "|".join(MONTHS),
                     re.IGNORECASE)


def swedish_date(text):
    """'den 30 juni 2026' / '09 april 2026' -> ISO '2026-06-30', or None."""
    m = SV_DATE.search(text or "")
    return ("%s-%02d-%02d" % (m.group(3), MONTHS[m.group(2).lower()], int(m.group(1)))
            if m else None)
