"""The shared run report of the `ai-*` actions -- the three things a build
stage's loop has and each of the seven LLM actions used to improvise:

* the live one-line counter (`util.status`, the same line parse and generate
  overwrite), so a component that takes an hour is not a silent terminal;
* one persistent line per written layer (the audit trail of what changed in
  the content repo) and one closing line, `<source> <action>: N layer(s)
  written, M skipped (<reason> ...), K failed in <time>`, the failed ids listed
  -- they are exactly what to re-run;
* the run-ledger segment the stages emit (`freshness._emit_segment`), now with
  counts, so `runs.ndjson` says what an `ai-hierarki --all` covered instead of a
  bare "ok" -- and, for a run that enumerated the whole eligible set itself
  (`ai-hierarki --all`), a `status.json` cell with the coverage the run
  proved: how many of the ids it enumerated carry a layer now. A run over a
  subset (`remisser ai-analyze --matching`, `--update`) is targeted and writes
  no cell, or two subsets would overwrite each other's "coverage".

Used as a context manager so the closing line and the segment are written even
when an item raises: an action's failure policy is its own (rule:narrow-what-
you-catch -- remisser tolerates an `Unanalyzable` answer and counts it here as
`fail`; the rest let a fault propagate), and this only makes sure the work done
before a fault is on record. The action returns the report; `build.py`'s
dispatcher then skips its own bare segment for it.

    with aireport.Report("eurlex", "ai-annotate", len(basefiles)) as report:
        for celex in basefiles:
            out = annstore.path("eurlex", celex)
            if protocol.RUN.dry_run:
                report.plan(celex, "annotate -> %s" % out); continue
            if report.verified(celex, out):
                continue
            report.item(celex)
            annotate.annotate(celex)
            report.wrote(celex, out)
    return report

`skip` takes a reason, and the closing line prints the skips grouped by it --
"skipped 400 (layers present 380, no graphic gaps 20)" -- so a bulk run's
skips are counted, not narrated (one line per skipped id is noise at 500
components, and the reason is what a reader wants). A skip whose reason is that
the layer is already there (`present=True`) counts toward coverage.
"""

import sys
import time
from collections import Counter
from pathlib import Path

from . import annstore, freshness, util
from .stage import RUN


def _duration(secs):
    if secs < 90:
        return "%.0f s" % secs
    if secs < 5400:
        return "%d min" % round(secs / 60)
    return "%dh%02dm" % (secs // 3600, secs % 3600 // 60)


class Report:
    def __init__(self, source, action, total=None, *, corpus_wide=False):
        """`total` is how many ids the run will visit (the counter's
        denominator; None when unknown). `corpus_wide` says the run enumerated
        its own ids, so its coverage is the corpus's and goes to status.json."""
        self.source, self.action = source, action
        self.total = total
        self.corpus_wide = corpus_wide
        self.written = []          # (label, paths) per written item
        self.layers = 0            # files written (a hierarki component writes many)
        self.skipped = Counter()   # reason -> items
        self.present = 0           # skips that mean "the layer is already there"
        self.failed = []           # labels
        self.planned = 0           # dry-run items
        self.t0 = time.perf_counter()
        self._counter_shown = False

    # -- per item ---------------------------------------------------------

    @property
    def done(self):
        return len(self.written) + sum(self.skipped.values()) + len(self.failed)

    def _label(self, label):
        return "%s %s %s" % (self.source, self.action, label)

    def item(self, label, detail=""):
        """Show the item about to run (or still running -- call again with a
        `detail` to update the line: an LLM call count, a batch index)."""
        # `actual` paces the ETA on the items that did work: a skip is instant,
        # and a resumed run may skip hundreds before its first write
        util.status(self.done + 1, self.total,
                    self._label(label) + (": " + detail if detail else ""),
                    actual=len(self.written))
        self._counter_shown = True

    def _persist(self, line, *, err=False):
        # a persistent line must not land on the overwriting counter's row
        # (util.write breaks it, or -- inside a lagen all invocation bar --
        # clears and redraws it around the line); nothing to protect when no
        # counter has been drawn yet, so a plain print skips the blank line
        if self._counter_shown:
            util.write(line, err=err)
            self._counter_shown = False
        else:
            print(line, file=sys.stderr if err else sys.stdout, flush=True)

    def wrote(self, label, path=None, *, note="", layers=None):
        """Record a written item: `path` is one layer file or a list of them
        (`layers` overrides the count when the paths are not at hand)."""
        paths = [] if path is None else ([path] if isinstance(path, (str, Path)) else list(path))
        n = len(paths) if layers is None else layers
        self.written.append((label, paths))
        self.layers += n
        what = ("wrote %s" % ", ".join(str(p) for p in paths) if paths
                else "wrote %d layer(s)" % n)
        self._persist("%s: %s%s" % (self._label(label), what,
                                    " (%s)" % note if note else ""))

    def skip(self, label, reason, *, present=False):
        self.skipped[reason] += 1
        if present:
            self.present += 1

    def verified(self, label, path):
        """True (and counted as a skip) when `path` is a hand-verified layer
        this run may not overwrite -- the check every action makes before the
        LLM spend (`annstore.guard` still refuses at the write)."""
        if RUN.force or annstore.status(path) != annstore.VERIFIED:
            return False
        self.skip(label, "verified, kept", present=True)
        return True

    def fail(self, label, exc):
        self.failed.append(label)
        self._persist("%s: FAILED -- %s" % (self._label(label), exc), err=True)

    def plan(self, label, what):
        """A dry run's "would ..." line; counted so the closing line says how
        much a real run would do."""
        self.planned += 1
        self._persist("%s: would %s" % (self._label(label), what))

    # -- closing ----------------------------------------------------------

    def summary(self):
        """The closing line."""
        head = "%s %s: " % (self.source, self.action)
        if self.planned and not self.done:
            return head + "would run %d item(s)" % self.planned
        parts = ["%d layer(s) written" % self.layers]
        if self.planned:                 # a dry run that also skipped
            parts.append("would run %d" % self.planned)
        if len(self.written) != self.layers:
            parts[0] += " over %d item(s)" % len(self.written)
        if self.skipped:
            parts.append("%d skipped (%s)" % (
                sum(self.skipped.values()),
                ", ".join("%s %d" % (r, n) for r, n in self.skipped.most_common())))
        parts.append("%d failed" % len(self.failed))
        line = head + ", ".join(parts) + " in " + _duration(time.perf_counter() - self.t0)
        if self.corpus_wide and self.total:
            line += " -- %d of %d carry a layer" % (
                len(self.written) + self.present, self.total)
        if self.failed:
            line += "\n  failed: " + " ".join(self.failed)
        return line

    def close(self, ok=True):
        if self._counter_shown:
            util.progress_break()
            self._counter_shown = False
        print(self.summary(), flush=True)
        freshness._emit_segment(
            self.action, self.source, time.perf_counter() - self.t0,
            total=self.total if self.total is not None else self.done,
            ran=len(self.written), errors=len(self.failed),
            skipped_fresh=sum(self.skipped.values()),
            status="ok" if ok and not self.failed else "errors")
        if ok and self.corpus_wide and self.total:
            # the coverage this run proved over the ids it enumerated: like
            # corpus.report's cell, written only by a run that owned the whole
            # set -- a targeted run must not overwrite it
            have = len(self.written) + self.present
            freshness._update_status_cell(self.source, self.action, {
                "total": self.total, "fresh": have, "stale": 0,
                "missing": self.total - have - len(self.failed),
                "failed": len(self.failed), "empty": 0,
                "run": freshness.RUN_ID})

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        # a usage error (SystemExit) before any work leaves nothing to report;
        # a fault mid-run still gets the work so far on record
        if exc_type is None or self.done or self.planned:
            self.close(ok=exc_type is None)
        return False
