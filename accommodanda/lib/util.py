"""Small shared utilities (ported from ferenda.util)."""

import json
import os
import re
import shutil
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def text_slug(text: str, *, sep: str = "-", maxlen: int | None = None) -> str:
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


RE_NUMBER_UNSAFE = re.compile(r"[/\s]+")


def number_slug(number: str) -> str:
    """The URI/file form of an agency's own number ("1/23/VER" -> "1-23-VER",
    "131 599911-10/111" -> "131-599911-10-111"). One implementation for the
    minter (`rs.agencies`) and the citation engine's STALLNINGSTAGANDE
    formatter, so the address a citation resolves to cannot drift from the
    address the page is published under."""
    return RE_NUMBER_UNSAFE.sub("-", number.strip()).strip("-")


def write_atomic(path: Path | str, data: bytes | str) -> None:
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


def store_relpath(path: Path | str, root: Path) -> str:
    """Render an absolute `path` as a `root`-relative string, so an on-disk index
    (the catalog, the dv identity index, …) stays portable across data_root
    moves: an index rsync'd to a host with a different data_root still resolves
    via `load_relpath`. Raises if `path` is not under `root` -- a stray path from
    another root must surface, not be silently stored broken."""
    return str(Path(path).relative_to(root))


def load_relpath(root: Path, stored: str) -> Path | None:
    """Inverse of `store_relpath`: the absolute Path for a `root`-relative stored
    path, or None for an empty (stub) path."""
    return root / stored if stored else None


def basefile_slug(basefile: str) -> str:
    """Filesystem-safe form of a basefile; the true identifier lives in the
    record JSON, so this only has to be unique and stable."""
    return basefile.replace("/", "-").replace(":", "-").replace(" ", "_")


def href(anchor):
    """The single-valued ``href`` of a bs4 anchor.

    bs4 types an attribute as ``str | list[str]`` because HTML permits
    multi-valued ones, so every scrape that follows a link has to narrow it.
    Fifteen sites in `foreskrift/agencies.py` did that with a message-less
    ``assert isinstance(href, str)``, which `-O` strips -- letting a list reach
    the url join and the download (rule:errors-drive-retry-use-raise). `href` is
    never multi-valued in practice, so a list here means the parser handed back
    something that is not the anchor the selector promised: a changed page,
    which must stop the harvest rather than be skipped past."""
    value = anchor["href"]
    if not isinstance(value, str):
        raise ValueError("<a> href is %r, not a string -- page structure changed"
                         % (value,))
    return value


def record_path(root: Path | str, subdir: str, basefile: str) -> Path:
    """The harvest-record JSON path for `basefile` under `root/subdir`."""
    return Path(root) / subdir / (basefile_slug(basefile) + ".json")


def document_extension(data: bytes) -> str | None:
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


def sniff_extension(path: Path | str) -> str | None:
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
_eta: dict[str, Any] = {"t0": 0.0, "actual0": 0, "total": object(), "done": -1,
                        "work0": 0.0}


def _eta_suffix(done, total, actual=None, work=None):
    """``ETA MM:SS`` for a current/total sequence, from the whole-run pace of the
    work actually performed, or '' when there is no usable estimate (the first
    line of a run, before any real work has happened, an unknown total, or the
    final line). `actual` is the running count of jobs that did work rather than
    being skipped as already up to date; when the caller can't tell, every job
    counts.

    `work` is ``(done, total)`` in *expected seconds* -- the per-document
    durations the manifest recorded, which the build driver already has because
    it dispatches on them. Given it, the estimate is paced on work rather than
    on job count, which is the difference between a useful number and a useless
    one whenever the two are not proportional. The driver dispatches longest-
    expected first (so the slow tail starts earliest and the last straggler is a
    fast document), so the jobs finished at any point are the most expensive in
    the corpus: a count-based rate is the worst-case rate, and applying it to
    every remaining job overestimated a full förarbete reparse by around an
    order of magnitude at the start. Dividing wall-clock by expected-seconds
    absorbs the parallelism too -- the ratio is what it is regardless of worker
    count -- so no separate concurrency factor is needed.

    Fresh skips are counted in *both* halves of the ratio, which is what makes a
    half-stale corpus come out right: a skip costs no wall-clock but carries its
    expected seconds, so it dilutes the measured rate and the remaining work by
    the same factor and the dilution cancels. The count-based estimate could not
    do this -- it measured the rate over real builds only and then applied it to
    every remaining job, skips included, which is why it overestimated a mixed
    run as well as a full one.

    The one assumption is that freshness is uncorrelated with document size. It
    usually is -- whether a document changed has nothing to do with how long it
    takes to parse. Where it does not hold, notably resuming a run that was
    killed partway (which leaves exactly the slowest documents fresh, since they
    were dispatched first), the estimate reads low until the run reaches the
    stale tail. Correcting that needs a freshness pre-scan of the whole corpus,
    which costs more at startup than the estimate is worth."""
    now = time.monotonic()
    performed_count = done if actual is None else actual
    if done <= 1 or done < _eta["done"] or total != _eta["total"]:
        _eta.update(t0=now, actual0=performed_count, total=total, done=done,
                    work0=(work[0] if work else 0.0))     # re-base the run
        return ""
    _eta["done"] = done
    elapsed = now - _eta["t0"]
    if total is None or done >= total or elapsed <= 0:
        return ""
    if work is not None:
        done_work, total_work = work
        performed = done_work - _eta["work0"]
        remaining_work = total_work - done_work
        if performed <= 0 or remaining_work <= 0:
            return ""
        remaining = (elapsed / performed) * remaining_work
    else:
        performed = performed_count - _eta["actual0"]
        if performed <= 0:
            return ""
        remaining = (elapsed / performed) * (total - done)
    return "ETA %02d:%02d" % divmod(int(remaining + 0.5), 60)


def status(done, total, message="", *, actual=None, work=None, prefix="",
           tail="", stream=sys.stderr):
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
    ETA is paced on the real builds and not diluted by the skips. `work` is
    ``(done, total)`` in expected seconds, which paces the ETA on work rather
    than on job count -- pass it wherever per-item cost estimates exist, since
    the two are wildly disproportionate on a corpus dispatched slowest-first
    (see `_eta_suffix`)."""
    line = "%s(%d/%s) %s%s" % (prefix, done, "?" if total is None else total,
                               message, tail)
    eta = _eta_suffix(done, total, actual, work)
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


def _hms(seconds: float) -> str:
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
    tail = " [+%s]" % _hms(elapsed) if elapsed is not None else ""
    status(seen, total, tally + note, actual=actual,
           prefix="%s%s%s" % (clock, head, pg), tail=tail, stream=stream)


def progress_break(stream=sys.stderr):
    """End a run of overwriting progress() lines: drop to a fresh line so the
    finished segment (a harvest year / page sweep) persists above the live one."""
    stream.write("\n")
    stream.flush()


def harvest_start(label: str, url: str) -> None:
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


def normalize_space(s: str) -> str:
    """Whitespace-collapsed and stripped display form. None-safe (an absent
    value normalizes to "")."""
    return " ".join((s or "").split())


def normalize_fold(s: str) -> str:
    """Whitespace-collapsed, stripped and case-folded -- the matching key for
    comparing titles/headings/terms case- and spacing-insensitively while the
    display form is kept elsewhere. None-safe (an absent value folds to ""); the
    lower-casing is what sets it apart from `normalize_space`."""
    return " ".join((s or "").split()).lower()


def split_numalpha(s: str) -> list[str | int]:
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


def from_roman(s: str) -> int:
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


def swedish_ordinal(s: str) -> int | None:
    """'första' -> 1, or None"""
    return SWEDISH_ORDINAL_MAP.get(s.lower())


MONTHS: dict[str, int] = {m: i for i, m in enumerate(
    "januari februari mars april maj juni juli augusti september oktober "
    "november december".split(), 1)}
SV_DATE = re.compile(r"(\d{1,2})\s+(%s)\s+(\d{4})" % "|".join(MONTHS),
                     re.IGNORECASE)


def swedish_date(text: str) -> str | None:
    """'den 30 juni 2026' / '09 april 2026' -> ISO '2026-06-30', or None."""
    m = SV_DATE.search(text or "")
    return ("%s-%02d-%02d" % (m.group(3), MONTHS[m.group(2).lower()], int(m.group(1)))
            if m else None)


FOLD_SWEDISH = str.maketrans("åäöÅÄÖ", "aaoAAO")


def fold_swedish(s: str) -> str:
    """å/ä/ö (either case) to their ASCII base letters: 'SÄIFS' -> 'SAIFS'."""
    return s.translate(FOLD_SWEDISH)


def match_fold(text: str) -> str:
    """`text` lowercased with everything but letters and digits removed -- for
    comparing two renderings of the same title (one off a PDF, one off an index
    page), which differ freely in case, punctuation and the spaces a line break
    leaves behind."""
    return re.sub(r"[^0-9a-zåäö]+", "", text.lower())


# folded characters a partial title echo must reach before it counts -- below
# this, a bare digit or short word would match the start of almost any title
TITLE_ECHO_MIN = 8


def drop_leading_title_echo(blocks, titel, *, text_of, lead=None):
    """Drop the document's own printed copy of its title from the leading
    `blocks` -- the page already shows the title as the h1, so the body would
    open by repeating itself. Two shapes count as the echo, matched on
    `match_fold`ed text: a block that *ends with* the title (a letterhead
    printed before it, rs's shape) and a block that is the *start of* the
    title (a cover line break left only the first piece, edpb's shape). A
    block that folds away entirely is stray cover punctuation and is stepped
    over; `lead` marks per-source letterhead captions that go regardless of
    the title. Only leading blocks go -- a later heading echoing the title
    stays, as the real section it is. The union of the two shapes was
    measured over both corpora (315 documents, 2026-08-08): it removed six
    real echoes the single-shape rules each missed, and no genuine content."""
    folded_title = match_fold(titel or "")
    while blocks:
        text = text_of(blocks[0])
        if lead is not None and lead(text):
            blocks = blocks[1:]
            continue
        if not folded_title:
            break
        head = match_fold(text)
        if (head == ""
                or head.endswith(folded_title)
                or (len(head) >= TITLE_ECHO_MIN
                    and folded_title.startswith(head))):
            blocks = blocks[1:]
            continue
        break
    return blocks


MONTHS_EN: dict[str, int] = {m: i for i, m in enumerate(
    "january february march april may june july august september october "
    "november december".split(), 1)}
# month matched by three-letter prefix, so "27 Jun 2001" and "27 January 1980"
# both parse (the UNTC listing mixes the two forms)
_MONTH_EN_PREFIX = {m[:3]: i for m, i in MONTHS_EN.items()}
EN_DATE = re.compile(r"(\d{1,2})\s+(%s)[a-z]*\s+(\d{4})"
                     % "|".join(_MONTH_EN_PREFIX), re.IGNORECASE)


def english_date(text: str) -> str | None:
    """'27 January 1980' / '27 Jun 2001' -> ISO '1980-01-27', or None."""
    m = EN_DATE.search(text or "")
    return ("%04d-%02d-%02d" % (int(m.group(3)),
                                _MONTH_EN_PREFIX[m.group(2)[:3].lower()],
                                int(m.group(1)))
            if m else None)


RE_RIKSMOTE = re.compile(r"^(\d{4})/(\d{2}|\d{4})$")


def approximate_date(value: str | None) -> str | None:
    """A partial date as one representative day: the middle of the span it can
    mean. `None` for anything that names no time at all.

        2004-05-17  -> 2004-05-17     (already a day)
        2004-04     -> 2004-04-15     (mid-month)
        2004        -> 2004-07-01     (mid-year)
        2004/05     -> 2005-01-01     (a riksmöte, autumn to summer)

    For dating a citation against the act in force when it was written, which is
    what the caller wants: an exact day is rarely on record, and the midpoint is
    the choice that minimises how far off it can be. A year read as 01-01 would
    put every document written in it before a law that took effect that January,
    and 12-31 would put them all after -- the middle is wrong by at most six
    months in either direction rather than twelve in one.

    A riksmöte runs from one autumn into the next summer, so its middle is the
    turn of the year: "2004/05" is January 2005. This is what a prop's basefile
    carries when the document itself records no date."""
    value = (value or "").strip()
    if not value:
        return None
    if m := RE_RIKSMOTE.match(value):
        # a riksmöte always spans two consecutive years, so the second is the
        # first plus one -- computed, not read off the suffix, which would need
        # century logic to get 1999/2000 right. The suffix has to agree: a value
        # that is not the next year is not a riksmöte and names no span this can
        # place (rule:fail-fast).
        start, end = int(m.group(1)), m.group(2)
        later = start + 1
        if end != ("%04d" % later if len(end) == 4 else "%02d" % (later % 100)):
            return None
        return "%d-01-01" % later
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return value
    if re.fullmatch(r"\d{4}-\d{2}", value):
        return value + "-15"
    if re.fullmatch(r"\d{4}", value):
        return value + "-07-01"
    return None


# --------------------------------------------------------------------------
# ndjson ledgers -- shared by the run ledger (lib/runlog) and the served-site
# error ledger (lib/errorlog), which are append-only files with the same
# durability requirement and had identical copies of both of these
# --------------------------------------------------------------------------

def now_iso(dt: datetime | None = None) -> str:
    """ISO-8601 UTC second-resolution timestamp; `dt` injectable for tests."""
    return (dt or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")


def append_json_line(path: Path | str, obj: object) -> None:
    """Append one JSON object as a line to an ndjson ledger, flushed so a crash
    right after the write still leaves the record on disk."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")
        f.flush()


def read_json_lines(path: Path | str, *, errors: str = "strict") -> list[dict]:
    """Every record in an ndjson ledger, in file order; a missing file is just
    empty.

    A torn *final* line -- a crash caught mid-append, the one corruption an
    append-only file produces in normal operation -- is dropped, so one
    interrupted write does not brick every subsequent read. The tolerance is
    deliberately narrowed to the last line: a malformed line anywhere earlier is
    a real integrity failure and still raises (rule:narrow-what-you-catch)."""
    path = Path(path)
    if not path.exists():
        return []
    lines = [line for line in
             path.read_text(encoding="utf-8", errors=errors).splitlines()
             if line.strip()]
    out = []
    for i, line in enumerate(lines):
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            if i == len(lines) - 1:
                break
            raise
    return out
