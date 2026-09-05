"""Small shared utilities (ported from ferenda.util)."""

import json
import os
import re
import shutil
import signal
import sys
import threading
import time
import unicodedata
import warnings
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

import tqdm as _tqdm


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


#: everything that is not a lowercase letter or a digit, for `own_number_slug`
RE_NUMBER_OTHER = re.compile(r"[^a-z0-9]+")


def own_number_slug(number: str) -> str:
    """The URI/file form of a number a body writes in a shape of its own:
    lowercased, with every run of other characters folded to one hyphen.
    ``"ESMA35-43-3448"`` -> ``esma35-43-3448``; ``"ESMA/2016/1477"`` ->
    ``esma-2016-1477``; ``"BoR (11) 67"`` -> ``bor-11-67``. The address still
    reads as the citation, one character class at a time, so a reader who has
    the number can type the page.

    One implementation for the minter (`guidance.issuers`) and the citation
    engine's VAGLEDNING formatters, for the same reason `number_slug` is
    shared: the address a citation resolves to cannot drift from the address
    the page is published under."""
    slug = RE_NUMBER_OTHER.sub("-", number.strip().lower()).strip("-")
    assert slug, "not a number: %r" % number
    return slug


def shouted(text: str) -> bool:
    """Whether `text` is set in capitals -- more than four fifths of its
    letters uppercase. The test a document's own typography answers: a shouted
    running head reprints the title in caps above the sentence-case one (the
    EBA's covers), and a heading set in caps is a heading however short (dv's
    domskäl)."""
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and sum(c.isupper() for c in letters) / len(letters) > 0.8


def json_canonical(obj) -> str:
    """The artifact tree's one JSON serialization: no ASCII escaping, stable
    key order, two-space indent -- so two builds of the same data diff
    readably. `compress.write_json` and `write_json_atomic` are its two
    writers; nothing on disk should be dumped with a different set of flags."""
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True)


def write_json_atomic(path: Path | str, obj) -> None:
    """`obj` as canonical JSON at `path`, written atomically. The plain-file
    counterpart of `compress.write_json`, for a sidecar that is never stored
    compressed."""
    write_atomic(path, json_canonical(obj).encode())


def write_atomic(path: Path | str, data: bytes | str) -> None:
    """Write `data` (bytes or str) to `path` via a same-directory temp file +
    atomic rename, so an interrupted run never leaves a partial file behind.
    The temp name is unique per process *and* per thread: concurrent writers
    (parallel `lagen` invocations both pruning the runlog; two API threads
    saving two users' edit carts) must not consume each other's temp file --
    with a fixed name, one writer's os.replace() raced away the file the other
    had just written and crashed it with FileNotFoundError."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp%d-%d"
                           % (os.getpid(), threading.get_ident()))
    try:
        tmp.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


class KeyedLocks:
    """One lock per key, so at most one thread at a time does the work a key
    names. The synchronous API endpoints run in a thread pool, so two readers
    can ask for the same expensive artifact at once: without this they both
    render it. The second waits and reads the first one's output instead.

    A lock per key ever asked for would grow without end, so idle ones are
    dropped once there are more than `limit`. A key dropped between lookup and
    acquire gets a second lock, and so a second render -- the same race a cache
    write has always tolerated, and it ends the same way."""

    def __init__(self, limit: int = 512):
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()
        self._limit = limit

    def __call__(self, key: str) -> threading.Lock:
        with self._guard:
            if len(self._locks) > self._limit:
                for old, lock in list(self._locks.items()):
                    if not lock.locked():
                        del self._locks[old]
            return self._locks.setdefault(key, threading.Lock())


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


def confine(rel: Path, basefile: str, tree: str) -> Path:
    """`rel`, refused if it leaves `tree`. A basefile is untrusted where a
    request supplies one: the /patch editor reads it out of a request body, and
    the path builders put it into a path -- several of them verbatim. A `..`
    segment or a leading slash would place the file outside the tree the caller
    then joins it onto. Lives here, not in `layout`, because `layout`, `harvest`
    and the foreskrift body path all build one and all import util. An escaping
    basefile is malformed input, not a server fault: ValueError."""
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError("basefile %r leaves the %s tree" % (basefile, tree))
    return rel


def record_path(root: Path | str, subdir: str, basefile: str) -> Path:
    """The harvest-record JSON path for `basefile` under `root/subdir`. `subdir`
    is the basefile's own leading segment at every call site, so it is confined
    against `root` here."""
    return Path(root) / confine(Path(subdir) / (basefile_slug(basefile) + ".json"),
                                basefile, str(root))


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
    (see `_eta_suffix`).

    When `invocation_bar` has opened a whole-invocation bar (a `lagen all
    parse`/`rebuild`), this stage's counter renders as a nested tqdm bar
    beneath it instead -- see `_status_nested`. Every other caller (a single
    `lagen <source> parse`, a harvest) is untouched: this branch is the only
    thing `invocation_bar` adds to `status`."""
    if _outer is not None:
        _status_nested(done, total, message, work=work)
        return
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


def write(msg, *, err=False, stream=None):
    """Print one persistent line without tearing whatever live progress line is
    on screen -- the one safe way to interleave a warning, a per-item audit
    line (`aireport.Report`) or a worker-crash notice with an active counter,
    instead of the two-call `progress_break()` + `print()` idiom (easy to
    reach for and easy to get wrong, since nothing enforces the order).

    Routed through `tqdm.write` when a nested invocation bar is open (it knows
    how to clear and redraw a registered bar around the line, on whichever
    stream each bar lives on); otherwise a bare newline first -- exactly
    `progress_break()` -- then the message, which is enough because the *next*
    `status()` call redraws the plain `\\r` counter fresh below it. `err` picks
    stderr over stdout for `msg` (matching `print`'s `file=sys.stderr` idiom);
    the break itself always targets real stderr unless `stream` is given
    explicitly, since that is where the live counter is regardless of which
    stream `msg` targets -- an explicit `stream` (tests; a caller that
    redirected both) breaks and prints on that one stream instead."""
    target = stream or (sys.stderr if err else sys.stdout)
    if _outer is not None:
        _tqdm.tqdm.write(msg, file=target)
    else:
        brk = stream or sys.stderr
        brk.write("\n")
        brk.flush()
        print(msg, file=target, flush=True)


def install_warnings_hook():
    """Route every `warnings.warn` (ours or a dependency's -- ferenda itself
    never calls it, but cryptography/lxml/etc. do) through `write`, so a
    warning surfaces as a clean persistent line instead of tearing an active
    progress counter mid-row. Called once, from the CLI entry point
    (`build.main`); never from a library import, so a test importing
    `ferenda.lib.util` does not silently change process-wide warning
    behaviour."""
    default = warnings.formatwarning

    def _show(message: Warning | str, category: type[Warning], filename: str,
             lineno: int, file: TextIO | None = None,
             line: str | None = None) -> None:
        write(default(message, category, filename, lineno, line).rstrip("\n"),
              err=True)

    # ty flags this despite an identical signature -- reassigning a stdlib
    # function is inherently this shape, not a real type mismatch
    warnings.showwarning = _show  # ty: ignore[invalid-assignment]


# --------------------------------------------------------------------------
# the whole-invocation bar (`lagen all parse`/`rebuild`): an outer tqdm bar
# over the corpus.build_invocation_plan() step sequence, with the current
# stage's own `status()` counter nested beneath it as a second tqdm bar. Only
# `cmd_all` opens one; every other caller of `status`/`progress` is
# unaffected (see `status`'s `_outer is not None` branch above).
# --------------------------------------------------------------------------

_outer = None       # the open whole-invocation tqdm bar, or None
_inner = None       # the nested per-stage tqdm bar status() drives while open
_inner_key = object()   # the outer step_no (or total/work-total, unwrapped) status() last rebased on
_real_streams = None    # (real stdout, real stderr), captured before redirecting
_resize_pending = False   # set by the SIGWINCH handler, acted on in _status_nested

_DESC_WIDTH_MAX = 45   # description width on a wide-enough terminal
_DESC_WIDTH_MIN = 10   # never shrink the description below this
# everything in a nested bar's rendered line other than {desc} and {bar}
# itself: "elapsed: " + " |" + "|" + " " + "n/total, ETA remaining", sized
# for the widest realistic case (hours-long elapsed/ETA, 7-digit counts) plus
# a few columns of margin so a *slightly* wider case still doesn't tip into
# tqdm's raw-truncation fallback
_DESC_RESERVE = 40


def _desc_width(stream):
    """How wide a nested bar's description may be right now, so the *whole*
    rendered line (elapsed + desc + bar + count + ETA) fits the terminal.

    Below `_DESC_RESERVE` + `_DESC_WIDTH_MIN` columns, `format_meter` cannot
    fit everything even with the bar's own fill shrunk to nothing, and falls
    back to a raw `line[:ncols]` truncation -- not graceful degradation, a
    mid-number cut ("6167" -> "616") whose exact cutpoint shifts frame to
    frame as elapsed/count digits change width. That, not a cursor-position
    bug, is what a terminal narrower than ~80 columns actually hit: this
    file's own fixed 45-column description left no way to ever fit under
    it. A tmux pane split well under 80 columns hits this reliably even in a
    wide outer window, independent of any resize signal at all -- this is
    the same bug at ncols=60 whether or not the terminal was ever resized
    live."""
    try:
        if stream is not None and stream.isatty():
            cols = os.get_terminal_size(stream.fileno()).columns
        else:
            cols = shutil.get_terminal_size((80, 24)).columns
    except OSError:
        cols = shutil.get_terminal_size((80, 24)).columns
    return max(_DESC_WIDTH_MIN, min(_DESC_WIDTH_MAX, cols - _DESC_RESERVE))


def _status_nested(done, total, message, *, work=None):
    """The nested-bar counterpart of status()'s plain-line rendering: rebases
    on the outer bar's own step count (`_outer.step_no`, incremented once per
    `InvocationBar.start()`) rather than on `total`/`work[1]` changing, since
    two different steps can share a total (see the rebasing comment inline);
    same two ETA modes as the plain line, rendered as tqdm's own bar/rate/ETA
    instead of `_eta_suffix`'s. `_outer is not None` is `status`'s only entry
    point here. Rendered at `position=0`, *above* the outer bar (see
    `InvocationBar`).

    A fixed `bar_format` (no bare tqdm default): "elapsed: desc |bar| n/total,
    ETA remaining". Elapsed leads (plain wall-clock time since this stage
    began, so it reads as what it is, not buried at the end inside tqdm's own
    `[elapsed<remaining]` idiom -- a person has to already know tqdm to parse
    `<` as "versus", not an inequality); the bar needs no percentage beside
    it, a redundant third way of saying what the fill already shows; and the
    ETA is a labelled field, not the second half of that same unlabelled pair.

    `n`/`total` shown are always the real document count (`done`/`total`, the
    same pair every other caller of `status` sees) -- *never* the cost-paced
    `work` totals, which are the per-document expected-*seconds* weights
    summed across a whole stage: an internal number this rail was already
    using to compute an ETA before this bar existed (`_eta_suffix`'s `work=`
    branch), not a count of anything real. `work[0]`/`work[1]` still drive the
    bar's own fill/percentage/rate/ETA when given (pacing on cost, not job
    count, is the entire point of `work=` -- see `_eta_suffix`), but the
    *printed* count is baked into the format string as plain text instead of
    tqdm's own `{n}`/`{total}` fields, decoupling "what paces the bar" from
    "what number a reader sees": showing `172927/178333` (the cost weights)
    beside `ETA 00:03` reads as broken (172927 of what, in three seconds?);
    showing the real `done`/`total` next to that same ETA does not. (The
    default bar_format's own flaws stay fixed either way: raw floats --
    `166212.63500000146` -- whose digit count jitters the bar's rendered
    width, and a `{rate_fmt}` of `4988.53s/s` that reads as nothing a person
    asked for.)

    The description gets the same fixed-width treatment, for the same reason:
    `message` is `"<source> <verb>  ran N  err N  <basefile>"`
    (`freshness._progress`), and a basefile ranges from "a" to a sö/lr
    förarbete slug dozens of characters long, so the raw message left the
    bar's own fill width visibly resizing update to update. The leading
    "<source> <verb>" is dropped first when it matches the outer bar's
    current step (`_outer.current_label`) -- `InvocationBar` already names
    that beneath this one, so repeating it here is pure width lost to
    something the reader can already see. What remains is padded/truncated to
    `_desc_width()` -- the same call `InvocationBar` makes for its own
    description, so the two bars' `|bar|` columns line up -- not a fixed
    width: see `_desc_width`'s own docstring for why a constant one is a
    second bug in this same area, distinct from the resize-cursor one.

    Also refreshes the outer bar on every call (cheap): `status()` is called
    far more often than `InvocationBar.start`/`finish` (per document, not per
    stage), so piggybacking its redraw here is what makes the outer bar's own
    elapsed time tick continuously instead of jumping only at stage
    boundaries -- a value that visibly holds still between ticks does not
    read as "elapsed", it reads as stuck.

    Also where a pending `SIGWINCH` gets acted on (`invocation_bar`'s handler
    only sets `_resize_pending` -- a signal handler must never do I/O, since it
    can fire mid-write to the very stream it would try to write to next, and
    that reentrant call is a hard `RuntimeError`, not a rare race). Found here
    rather than needing its own poll loop because `status()` already runs
    this often.

    The recovery is `tqdm.external_write_mode()` around an empty block, not
    calling `.clear()` on each bar by hand: `clear()`/`refresh()` move the
    cursor with *relative* jumps (`moveto()` -- `\\n` down, an ANSI cursor-up
    up), correct only when the cursor is exactly where that one bar's own
    bookkeeping expects it, given every other active bar was cleared in the
    same coordinated pass first. Two independent `.clear()` calls, in
    whatever order this function happens to make them, do not honour that --
    proven the hard way: it desynced the two bars' cursor math permanently
    after a single resize, so every following refresh (using that same
    relative math) kept opening a new line instead of overwriting, forever,
    not just for the one stale frame. `external_write_mode` is tqdm's own
    answer to "clear every active bar, do something, put them all back" (it
    is what `tqdm.write` itself uses) -- the coordinated version of the same
    idea, under the one lock and in the order tqdm's own bookkeeping expects."""
    global _inner, _inner_key, _resize_pending
    # status()'s own `if _outer is not None:` is the only entry point here,
    # and invocation_bar()/reset_worker_state() only ever set _outer and
    # _real_streams together -- both are guaranteed non-None below, not
    # merely likely: an `X if _outer else fallback` around every use would
    # be a branch this function can never actually take, hiding exactly the
    # bug rule:fail-fast asks a plain attribute access to surface instead.
    assert _outer is not None and _real_streams is not None, (
        "_status_nested reached with no invocation_bar open")
    # rebased on the outer bar's own step count, not on `total`/`work[1]`
    # changing: two different steps can share the same total (a fresh
    # corpus's never-built sources, or two steps costed by the same
    # PLANNER_DEFAULT_SECS fallback -- routine, not a corner case), and a
    # collision there left `_inner` un-rebuilt across the boundary, so its
    # `start_t` kept ticking from the *previous* stage -- a stale elapsed
    # clock and a nonsensical ETA at the moment a new stage actually began.
    # `_outer.step_no` increments exactly once per `InvocationBar.start()`,
    # which brackets every real step in `corpus.cmd_all` -- an unambiguous
    # step-boundary signal `total` never was.
    key = _outer.step_no
    if _inner is None or key != _inner_key:
        if _inner is not None:
            _inner.close()
        _inner = _tqdm.tqdm(
            total=work[1] if work else total, position=0, leave=False,
            dynamic_ncols=True, file=_real_streams[1])
        _inner_key = key
    else:
        # refreshed on every call, not only at creation: InvocationBar.start()
        # primes this bar with an unknown total (0, None, "") before the
        # step's own first status() call arrives, so the real total must
        # still take effect on that first call despite reusing this same bar
        _inner.total = work[1] if work else total
    _inner.n = work[0] if work else done
    count = "%s/%s" % (done, "?" if total is None else total)
    _inner.bar_format = "{elapsed}: {desc} |{bar}| %s, ETA {remaining}" % count
    label = _outer.current_label
    if label and message.startswith(label + "  "):
        message = message[len(label) + 2:]
    w = _desc_width(_real_streams[1])
    _inner.set_description_str(message[:w].ljust(w))
    if _resize_pending:
        _resize_pending = False
        with _tqdm.tqdm.external_write_mode(file=_real_streams[1]):
            pass
    _inner.refresh()
    _outer.refresh()


class _TqdmRedirect:
    """A file-like standing in for `sys.stdout`/`sys.stderr` for the lifetime
    of an open invocation bar, so a plain `print()` anywhere in the pipeline
    -- "parse sfs: up to date -- skipped", a source's own diagnostic -- lands
    through `tqdm.write` instead of tearing a bar mid-row the way a foreign
    write does. Line-oriented like `print`'s own writes: a bare `\\n` (its
    `end` argument, written separately from the content) is dropped rather
    than opening a blank line, since `tqdm.write` appends its own.

    `tqdm.write` only clears and redraws a bar when its own check --
    `inst.fp is file` or "both `file` and `inst.fp` are `sys.stdout`/
    `sys.stderr`" -- passes, and it reads `sys.stdout`/`sys.stderr` *at call
    time*. While this wrapper is installed, those names hold the wrapper
    itself, not the real streams the bars were built against, so that check
    silently fails and every bar goes uncleared -- exactly the corruption
    this class exists to prevent. `write` restores the real pair for the
    duration of the call so tqdm's own check sees what it expects."""

    def __init__(self, real):
        self._real = real

    def write(self, s):
        if s and s != "\n":
            # a _TqdmRedirect instance only exists while sys.stdout/sys.stderr
            # hold one -- invocation_bar() installs and clears both together
            # with _real_streams, so this is never None here
            assert _real_streams is not None, (
                "_TqdmRedirect.write reached with no invocation_bar open")
            cur = sys.stdout, sys.stderr
            sys.stdout, sys.stderr = _real_streams
            try:
                _tqdm.tqdm.write(s.rstrip("\n"), file=self._real)
            finally:
                sys.stdout, sys.stderr = cur

    def flush(self):
        self._real.flush()

    def isatty(self):
        return self._real.isatty()

    def fileno(self):
        return self._real.fileno()


def reset_worker_state():
    """Undo whatever bar/redirect state a forked build worker inherited from
    the parent process at fork time: on Linux (this codebase's only fork
    start method today), a `multiprocessing.Pool` worker is a COW copy of the
    parent's whole address space, not a fresh interpreter -- `_outer`/
    `_real_streams` name the *parent's* tqdm bar objects and streams, and
    `sys.stdout`/`sys.stderr` are still the parent's `_TqdmRedirect` wrappers.
    None of that is safe or meaningful in a child: a warning firing mid-parse
    (`install_warnings_hook`'s hook calls `write`, which calls `tqdm.write` on
    `_outer is not None`) would touch tqdm bar objects that do not track this
    process's own writes, racing the parent's concurrent writes to the same
    fd. Resetting to `None`/the real streams makes `write` take its plain
    print-and-break path instead, on this process's own stdout/stderr.

    Called once, from `freshness._worker_init` -- the one place a forked
    worker already resets other inherited process-global state
    (`protocol.set_run`)."""
    global _outer, _inner, _real_streams
    _outer = None
    _inner = None
    _real_streams = None
    sys.stdout, sys.stderr = sys.__stdout__, sys.__stderr__


def _stderr_is_a_tty():
    """Whether the real stderr (`sys.stderr` at call time -- before any
    `invocation_bar` redirect) is a tty; a `contextmanager`-testable seam
    rather than an inline `sys.stderr.isatty()`, since a test that also uses
    `capsys` swaps `sys.stderr` for its own capture object *after* an
    ordinary `monkeypatch.setattr(sys.stderr, ...)` on the pre-swap instance
    would have taken effect, silently reverting it."""
    return sys.stderr.isatty()


class _NullInvocationBar:
    """Stand-in `invocation_bar` yields when the real stderr is not a tty:
    `start`/`finish` are no-ops and `_outer` is never set, so every `status()`
    call keeps rendering through its own plain, already tty-agnostic
    single-line form instead of the two-bar tqdm overlay. tqdm's stacked
    `position=0`/`1` bars write raw cursor-up ANSI codes (`\\x1b[A`) on every
    refresh regardless of the target's tty-ness -- harmless to a real
    terminal, escape-code soup mixed into a `lagen all rebuild > log 2>&1`
    file. A run nobody is watching live gains nothing from the bar; the
    closing failure summary (`build._print_failure_summary`) is what such a
    run actually needs to explain itself."""

    def start(self, label):
        pass

    def finish(self):
        pass


class InvocationBar:
    """The outer whole-invocation bar `cmd_all` drives: one `start()`/`finish()`
    pair per `PlannedStep`. `total_secs` (the plan's summed predictions) and
    `total_steps` -- both from `corpus.build_invocation_plan` -- pace the ETA
    and the step count respectively.

    Rendered at `position=1`, *below* the current stage's own nested counter
    (`position=0`): the stage line is what changes moment to moment and reads
    first; this one is the slower-moving "how far through the whole run" context
    beneath it. Its description is padded to the same `_desc_width()` as the
    nested bar's, so the two bars' `|bar|` columns start at the same place --
    computed fresh each `start()`, not a fixed width (see `_desc_width`), and
    `dynamic_ncols=True` matches the nested bar's own construction: without it
    tqdm measures the terminal once, at bar-open time, and never again, so the
    two bars' lines can end up different widths (and their right edges out of
    line with each other) even on an unchanged, unresized terminal, wherever
    that one-time measurement happened to land.

    `n`/`total` are the step count itself ("4/25" fills the bar to 4/25,
    exactly what a reader reads next to it) -- *not* predicted-vs-actual
    seconds, which a highly lopsided plan (a handful of sources skip in
    milliseconds, one runs for hours) made worse than the problem it solved:
    a bar that had barely moved through several real, completed steps because
    they cost almost none of the plan's predicted total time. The ETA is
    still paced on that cost model (`secs_done`/`total_secs`, real elapsed vs.
    predicted), computed by hand (`_eta_str`) and baked into `bar_format` as
    literal text on every refresh, exactly as the nested bar already bakes in
    its own real done/total beside a cost-paced fill (see `_status_nested`)."""

    def __init__(self, total_secs, total_steps, desc, *, file=None):
        self._t0 = 0.0
        self._run_t0 = time.perf_counter()
        self._file = file
        self.total_steps = total_steps
        self.total_secs = total_secs
        self.secs_done = 0.0
        self.step_no = 0
        self.current_label = ""
        self.bar = _tqdm.tqdm(
            total=total_steps, desc=desc, position=1, leave=True,
            dynamic_ncols=True, file=file)

    def _eta_str(self):
        elapsed = time.perf_counter() - self._run_t0
        if self.secs_done <= 0 or elapsed <= 0:
            return "?"
        rate = self.secs_done / elapsed
        remaining = self.total_secs - self.secs_done
        return _tqdm.tqdm.format_interval(remaining / rate) if remaining > 0 else "0:00"

    def refresh(self):
        """Recompute the ETA text and redraw -- called on every nested-bar
        refresh too (see `_status_nested`), the same piggyback that keeps the
        nested bar's own elapsed time ticking between step boundaries; this
        is what makes the ETA keep counting down between them as well,
        instead of only updating at `start()`/`finish()`."""
        self.bar.bar_format = ("{elapsed}: {desc} |{bar}| %d/%d, ETA %s"
                              % (self.step_no, self.total_steps, self._eta_str()))
        self.bar.refresh()

    def start(self, label):
        """Announce the step about to run -- the "current source+command" and
        the steps-remaining count the outer line names while its own inner
        counter is still at 0/?. `current_label` is read by `_status_nested`
        to drop this same "source verb" prefix from its own description,
        which would otherwise just repeat what this line already says."""
        self.step_no += 1
        # the plan is a prediction, the same way its seconds are (see
        # `corpus._history_secs`): a step whose loop condition the planner
        # read differently is still a step that ran. Let the total follow the
        # real count rather than render "26/25" -- the ETA already
        # self-corrects on real elapsed time in `finish`.
        self.total_steps = max(self.total_steps, self.step_no)
        self.bar.total = self.total_steps
        self._t0 = time.perf_counter()
        self.current_label = label
        self.bar.n = self.step_no
        w = _desc_width(self._file)
        self.bar.set_description_str(label[:w].ljust(w))
        self.refresh()
        # prime the nested bar right away, at 0/? -- a source's own setup
        # before its first status() call (parse's per-basefile freshness
        # gate, in particular, on a source like forarbete with a lot of
        # never-built basefiles) can take long enough that the second line
        # would otherwise sit blank for a while, reading as hung rather than
        # merely not-yet-reporting. _status_nested rebases on step_no, just
        # incremented above, so the real first status() call of this step
        # reuses this same bar instead of rebuilding it.
        _status_nested(0, None, "")

    def finish(self):
        """Add this step's real elapsed time to the ETA's cost tally,
        whatever its prediction said -- a step that ran fast (a skip the plan
        could not predict) or slow never desyncs the ETA from wall-clock
        reality. The bar's own fill already advanced in `start()`, since it
        tracks completed steps, not this step's own duration."""
        self.secs_done += time.perf_counter() - self._t0
        self.refresh()

    def close(self):
        global _inner
        if _inner is not None:
            _inner.close()
            _inner = None
        self.bar.close()


@contextmanager
def invocation_bar(total_secs, total_steps, desc="lagen all"):
    """Open the whole-invocation bar for the run's lifetime: `cmd_all` calls
    `ib.start(label)` / `ib.finish()` around each planned step. Every `status()`
    call made while this is open renders nested beneath it instead of as the
    lone overwriting line (see `status`).

    Also redirects `sys.stdout`/`sys.stderr` to `_TqdmRedirect` for the same
    lifetime (restored in `finally`, so a crash or Ctrl-C never leaves the
    process talking through the wrapper): the two tqdm bars are built against
    the *real* streams first, captured in `_real_streams`, so they draw
    directly and never round-trip through their own redirect.

    Also installs a `SIGWINCH` handler for the same lifetime (Unix only; a
    no-op where the signal does not exist), restored in `finally`. tqdm's
    `dynamic_ncols` re-measures the terminal lazily, on the *next* update it
    was going to draw anyway -- fine when the terminal grows (the old, shorter
    frame is simply overwritten), not when it shrinks: the stale frame is
    still on screen at the old, wider layout, and the terminal itself wraps
    it onto an extra row the instant it gets narrower, before tqdm's cursor
    math (one row per bar, fixed) has any way to know. `SIGWINCH` forces both
    bars to redraw immediately, at the point the resize actually happened,
    which is the standard mitigation -- not a guarantee against every
    resize-timing race, but the difference between "self-heals on the next
    tick" and "silently corrupted until the run ends".

    Yields a `_NullInvocationBar` instead, with none of the above, in two
    cases. First, when the real stderr is not a tty (`lagen all rebuild > log
    2>&1`, a cron job): a stacked-position tqdm bar writes raw cursor-up ANSI
    codes to *any* file it is given, tty or not, and a run nobody is watching
    live gains nothing from the bar to offset that. Second, when the plan holds
    fewer than two steps (`lagen all generate`, `lagen sfs parse`): the outer
    line would say "1/1" for the whole run and repeat what the step's own
    counter already says, so a single-step run keeps the plain one-line
    counter.

    Every step announces itself through `step()`, from wherever its own loop
    lives -- `cmd_relate`/`cmd_index`/`cmd_dump` per source, `cmd_all` and
    `build._dispatch` around the calls they make. `step()` is a no-op while no
    bar is open, so the same call sites serve a single-source run too."""
    real = (sys.stdout, sys.stderr)
    if total_steps < 2 or not _stderr_is_a_tty():
        yield _NullInvocationBar()
        return
    global _outer, _real_streams
    _real_streams = real
    ib = InvocationBar(total_secs, total_steps, desc, file=real[1])
    _outer = ib
    sys.stdout, sys.stderr = _TqdmRedirect(real[0]), _TqdmRedirect(real[1])
    global _resize_pending
    _resize_pending = False
    prior_handler = None
    if hasattr(signal, "SIGWINCH"):
        def _on_resize(signum, frame):
            # a signal handler must never do I/O: it can fire between any two
            # bytecodes, including mid-write to the very stream a bar's own
            # refresh() would write to next, and re-entering a buffered
            # writer from inside itself is a hard RuntimeError, not a race
            # that only sometimes shows up. Set a flag; the actual refresh
            # happens from _status_nested, ordinary code that runs only
            # between writes, never inside one.
            global _resize_pending
            _resize_pending = True
        prior_handler = signal.signal(signal.SIGWINCH, _on_resize)
    try:
        yield ib
    finally:
        if prior_handler is not None:
            signal.signal(signal.SIGWINCH, prior_handler)
        sys.stdout, sys.stderr = real
        ib.close()
        _outer = None
        _real_streams = None


@contextmanager
def step(label):
    """Announce one planned step of a multi-step run -- "<source> <verb>" --
    on the outer invocation bar, for as long as it runs. A no-op when no bar
    is open, which is what lets the same call site serve `lagen all relate`
    (a bar, one step per source) and `lagen sfs parse` (no bar, the source's
    own counter alone)."""
    if _outer is None:
        yield
        return
    _outer.start(label)
    try:
        yield
    finally:
        _outer.finish()


def checking(label):
    """Report a staleness scan that has no item count to report yet -- the
    artifact walk `relate`/`dump` open with, the catalog signature `index`
    and a full `generate` open with. Those scans read the whole corpus off
    disk and used to run silently, so the seconds (minutes, on a cold cache)
    before the first real progress line read as a hang. Same wording as the
    per-basefile scan `freshness.stage_fingerprint` reports."""
    status(0, None, "%s  checking staleness" % label)


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


# --------------------------------------------------------------------------
# document uris
# --------------------------------------------------------------------------

# every document uri this site mints starts here
BASE = "https://lagen.nu/"


def local(uri: str) -> str:
    """The local id a document uri names -- the site base stripped off. An
    off-site uri is returned unchanged. `util` is the bottom of `lib`, so the
    modules below catalog (labels, catalog_rows) reach the one copy without
    importing catalog back."""
    return uri[len(BASE):] if uri.startswith(BASE) else uri


ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}

# matches only well-formed roman numerals
re_roman = re.compile(
    r"^M?M?M?(CM|CD|D?C?C?C?)(XC|XL|L?X?X?X?)(IX|IV|V?I?I?I?)$").match


def normalize_space(s: str) -> str:
    """Whitespace-collapsed and stripped display form. None-safe (an absent
    value normalizes to "")."""
    return " ".join((s or "").split())


# the invisible characters a CMS sets its headings with: the line-breaking hints
# (a soft hyphen, a zero-width space) plus the joiners, directional marks and
# byte-order mark that travel with them -- Läkemedelsverket writes its own
# designation "HSLF\u200d-\u200dFS", which must read as "HSLF-FS"
RE_BREAK_HINT = re.compile("[\u00ad\u200b-\u200f\u2060\ufeff]")


def normalize_hints(s: str) -> str:
    """Whitespace-collapsed and stripped, with the line-breaking hints removed.
    They are invisible on the page but not in a stored title or a search index --
    imy.se writes "Dataskyddsom\xadbudens roll", which must be filed as
    "Dataskyddsombudens roll"."""
    return RE_BREAK_HINT.sub("", normalize_space(s))


def element_text(element) -> str:
    """A parsed HTML element's display text, through `normalize_hints`. Shared by
    every scraper that reads a heading or a label off a CMS page. `element` is a
    `bs4.Tag` at every call site, but the parameter is left unannotated on
    purpose: this module is pure stdlib, and importing bs4 for one annotation
    would put a parser behind every import of util."""
    return normalize_hints(element.get_text(" ", strip=True))


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
    real echoes the single-shape rules each missed, and no genuine
    content."""
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
