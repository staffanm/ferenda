import json
import threading
import time
from datetime import date, timedelta

import pytest

from ferenda.lib import compress, net
from ferenda.lib.harvest import (
    HarvestWatermark,
    ItemKey,
    Skip,
    dispatch_scopes,
    fan_out,
    pdf_path,
    select_pending,
    walk,
    walk_records,
)


def test_harvest_watermark_new(tmp_path):
    filepath = tmp_path / "watermark.json"
    w = HarvestWatermark(filepath)
    assert w.last_harvest is None
    assert w.get_limit_date() is None

    # Should not stop on anything when empty/new
    assert w.should_stop(is_downloaded=True) is False
    assert w.should_stop(is_downloaded=False) is False


def test_harvest_watermark_save_and_load(tmp_path):
    filepath = tmp_path / "watermark.json"
    w = HarvestWatermark(filepath)
    w.save("2026-07-03")

    w2 = HarvestWatermark(filepath)
    assert w2.last_harvest == "2026-07-03"
    assert w2.get_limit_date() == date(2026, 7, 3) - timedelta(days=14)


def test_harvest_watermark_should_stop_by_consecutive(tmp_path):
    filepath = tmp_path / "watermark.json"
    w = HarvestWatermark(filepath, lookahead_limit=3)

    # 1st consecutive seen
    assert w.should_stop(is_downloaded=True) is False
    # 2nd consecutive seen
    assert w.should_stop(is_downloaded=True) is False
    # Reset on missing
    assert w.should_stop(is_downloaded=False) is False
    # 1st consecutive seen again
    assert w.should_stop(is_downloaded=True) is False
    # 2nd consecutive
    assert w.should_stop(is_downloaded=True) is False
    # 3rd consecutive -> stop!
    assert w.should_stop(is_downloaded=True) is True


def test_harvest_watermark_should_stop_by_date(tmp_path):
    filepath = tmp_path / "watermark.json"
    w = HarvestWatermark(filepath, lookahead_limit=5, safety_days=10)
    w.save("2026-07-15")  # limit_date will be 2026-07-05

    # Reset watermark object to load from saved file
    w = HarvestWatermark(filepath, lookahead_limit=5, safety_days=10)

    # Newer than limit_date, already downloaded -> don't stop
    assert w.should_stop(is_downloaded=True, item_date_str="2026-07-10") is False

    # Older than limit_date, but NOT downloaded -> don't stop (it's a gap)
    assert w.should_stop(is_downloaded=False, item_date_str="2026-07-01") is False

    # Older than limit_date AND already downloaded -> stop!
    assert w.should_stop(is_downloaded=True, item_date_str="2026-07-01") is True


# --- hardening: never-regress date, dirty flag ------------------------------

def test_old_format_file_loads_clean(tmp_path):
    # a pre-dirty file ({"last_harvest": ...}) loads: dirty defaults False
    fp = tmp_path / "watermark.json"
    fp.write_text(json.dumps({"last_harvest": "2026-07-03"}))
    w = HarvestWatermark(fp)
    assert w.last_harvest == "2026-07-03"
    assert w.dirty is False


def test_save_none_never_regresses(tmp_path):
    fp = tmp_path / "watermark.json"
    w = HarvestWatermark(fp)
    w.save("2026-07-03")
    w.save(None)                          # a run that saw no dated items
    assert w.last_harvest == "2026-07-03"
    assert HarvestWatermark(fp).last_harvest == "2026-07-03"


def test_begin_marks_dirty_complete_clears_on_clean_run(tmp_path):
    fp = tmp_path / "watermark.json"
    w = HarvestWatermark(fp)
    w.save("2026-01-01")
    w.begin()
    assert HarvestWatermark(fp).dirty is True     # persisted immediately
    w.complete("2026-07-01", errors=0)
    reloaded = HarvestWatermark(fp)
    assert reloaded.dirty is False
    assert reloaded.last_harvest == "2026-07-01"


def test_complete_with_errors_stays_dirty_but_advances_date(tmp_path):
    fp = tmp_path / "watermark.json"
    w = HarvestWatermark(fp)
    w.save("2026-01-01")
    w.begin()
    w.complete("2026-07-01", errors=1)
    reloaded = HarvestWatermark(fp)
    assert reloaded.dirty is True                 # errors keep the store dirty
    assert reloaded.last_harvest == "2026-07-01"  # ... but the date still advances


def test_complete_none_date_keeps_prior(tmp_path):
    fp = tmp_path / "watermark.json"
    w = HarvestWatermark(fp)
    w.save("2026-01-01")
    w.begin()
    w.complete(None, errors=1)
    reloaded = HarvestWatermark(fp)
    assert reloaded.last_harvest == "2026-01-01"
    assert reloaded.dirty is True


def test_dirty_disables_consecutive_but_keeps_date_conclusive(tmp_path):
    fp = tmp_path / "watermark.json"
    fp.write_text(json.dumps({"last_harvest": "2026-07-15", "dirty": True}))
    w = HarvestWatermark(fp, lookahead_limit=3, safety_days=10)  # limit 2026-07-05
    # a long run of consecutive already-downloaded items never stops while dirty
    for _ in range(10):
        assert w.should_stop(is_downloaded=True, item_date_str="2026-07-14") is False
    # but the date-conclusive stop still fires: an old, already-downloaded item
    assert w.should_stop(is_downloaded=True, item_date_str="2026-07-01") is True


# --- the shared download walk (lib.harvest.walk) ----------------------------

def _run_walk(tmp_path, items, dates, on_disk, resolve, *, full=False,
              limit=None, only=None, budget=None, lookahead=3, safety_days=14):
    """Drive walk() over an in-memory model. `items` is the enumeration (basefile
    strings, optionally with Skip records); `dates`/`on_disk` back item_key."""
    wm = HarvestWatermark(tmp_path / "wm.json", lookahead_limit=lookahead,
                          safety_days=safety_days)
    return walk(items, resolve=resolve,
                item_key=lambda bf: ItemKey(basefile=bf, is_downloaded=bf in on_disk,
                                            date=dates[bf]),
                watermark=wm, full=full, only=only, limit=limit, budget=budget,
                scope="fs", log=lambda *a: None)


def test_walk_backfill_fetches_all_and_completes_clean(tmp_path):
    bfs = ["fs/2026:%d" % n for n in range(5, 0, -1)]
    dates = {bf: "2026-06-30" for bf in bfs}
    on_disk: set[str] = set()
    fetched = []

    def resolve(bf):
        fetched.append(bf)
        on_disk.add(bf)
        return True

    result = _run_walk(tmp_path, list(bfs), dates, on_disk, resolve)
    assert fetched == bfs and result.new == 5
    wm = HarvestWatermark(tmp_path / "wm.json")
    assert wm.dirty is False and wm.last_harvest == "2026-06-30"


def test_walk_dirty_run_retries_a_stranded_doc(tmp_path):
    # run 1 fails one doc mid-walk; run 2 must reach and fetch it despite a run of
    # already-downloaded items longer than the lookahead above it (the dv/-limit
    # and foreskrift/565 permanent-skip bugs, fixed by the dirty flag).
    bfs = ["fs/2026:%d" % n for n in range(8, 0, -1)]      # newest-first
    dates = {bf: "2026-06-30" for bf in bfs}
    stranded = bfs[4]
    on_disk: set[str] = set()

    def resolve_run1(bf):
        if bf == stranded:
            raise ValueError("transient resolve failure")
        on_disk.add(bf)
        return True

    r1 = _run_walk(tmp_path, list(bfs), dates, on_disk, resolve_run1)
    assert r1.errors == 1 and stranded not in on_disk
    assert HarvestWatermark(tmp_path / "wm.json").dirty is True

    fetched2 = []

    def resolve_run2(bf):
        fetched2.append(bf)
        on_disk.add(bf)
        return True

    r2 = _run_walk(tmp_path, list(bfs), dates, on_disk, resolve_run2)
    assert stranded in on_disk and fetched2 == [stranded] and r2.new == 1
    # the run healed cleanly -> dirty cleared
    assert HarvestWatermark(tmp_path / "wm.json").dirty is False


def test_walk_clean_watermark_stops_and_would_strand(tmp_path):
    # the contrast: with a *clean* watermark the consecutive-hit stop fires and a
    # doc below the un-fetched backlog is never reached -- which is exactly why the
    # dirty flag exists.
    bfs = ["fs/2026:%d" % n for n in range(8, 0, -1)]
    dates = {bf: "2026-06-30" for bf in bfs}
    stranded = bfs[4]
    on_disk = set(bfs) - {stranded}
    # seed a clean watermark so the walk is incremental, not backfill
    HarvestWatermark(tmp_path / "wm.json", lookahead_limit=3).save("2026-06-30")
    fetched = []

    def resolve(bf):
        fetched.append(bf)
        on_disk.add(bf)
        return True

    _run_walk(tmp_path, list(bfs), dates, on_disk, resolve, lookahead=3)
    assert stranded not in fetched          # stopped above it after 3 consecutive


def test_walk_zero_items_run_is_not_a_clean_completion(tmp_path):
    result = _run_walk(tmp_path, [], {}, set(), lambda bf: True)
    assert result.seen == 0
    assert HarvestWatermark(tmp_path / "wm.json").dirty is True


def test_walk_skip_leaves_store_dirty(tmp_path):
    bfs = ["fs/2026:2", "fs/2026:1"]
    dates = {bf: "2026-06-30" for bf in bfs}
    on_disk: set[str] = set()
    items = [bfs[0], Skip("page 2 down"), bfs[1]]

    def resolve(bf):
        on_disk.add(bf)
        return True

    result = _run_walk(tmp_path, items, dates, on_disk, resolve)
    assert result.skips == 1 and result.new == 2
    assert HarvestWatermark(tmp_path / "wm.json").dirty is True


def test_walk_limit_truncation_leaves_store_dirty(tmp_path):
    bfs = ["fs/2026:%d" % n for n in range(5, 0, -1)]
    dates = {bf: "2026-06-30" for bf in bfs}
    on_disk: set[str] = set()

    def resolve(bf):
        on_disk.add(bf)
        return True

    result = _run_walk(tmp_path, list(bfs), dates, on_disk, resolve, limit=2)
    assert result.new == 2
    assert HarvestWatermark(tmp_path / "wm.json").dirty is True   # backlog remains


def test_walk_only_does_not_touch_watermark(tmp_path):
    HarvestWatermark(tmp_path / "wm.json").save("2026-01-01")     # clean, dated
    bfs = ["fs/2026:2", "fs/2026:1"]
    dates = {bf: "2026-06-30" for bf in bfs}
    fetched = []

    result = _run_walk(tmp_path, list(bfs), dates, set(), fetched.append,
                       only="fs/2026:1")
    assert fetched == ["fs/2026:1"] and result.new == 1
    wm = HarvestWatermark(tmp_path / "wm.json")
    assert wm.dirty is False and wm.last_harvest == "2026-01-01"


# --- the sanity trip (walk budget + request deadline) -----------------------

def test_walk_budget_trip_truncates_and_stays_dirty(tmp_path):
    # an incremental walk past its budget stops like a --limit truncation: the
    # store stays dirty and the watermark date does not advance past the
    # un-walked backlog (the skolfs hung-register pathology)
    HarvestWatermark(tmp_path / "wm.json").save("2026-01-01")  # incremental, clean
    bfs = ["fs/2026:%d" % n for n in range(5, 0, -1)]
    dates = {bf: "2026-06-30" for bf in bfs}
    fetched = []

    result = _run_walk(tmp_path, list(bfs), dates, set(), fetched.append,
                       budget=0.0)
    assert fetched == [] and result.seen == 0
    wm = HarvestWatermark(tmp_path / "wm.json")
    assert wm.dirty is True
    assert wm.last_harvest == "2026-01-01"


def test_walk_budget_exempts_backfill(tmp_path):
    # a first harvest (no watermark date) is a backfill and must run to the end
    # regardless of the budget
    bfs = ["fs/2026:%d" % n for n in range(5, 0, -1)]
    dates = {bf: "2026-06-30" for bf in bfs}
    on_disk: set[str] = set()

    def resolve(bf):
        on_disk.add(bf)
        return True

    result = _run_walk(tmp_path, list(bfs), dates, on_disk, resolve, budget=0.0)
    assert result.new == 5
    assert HarvestWatermark(tmp_path / "wm.json").dirty is False


def test_request_past_deadline_raises_without_an_attempt():
    class Session:
        deadline = 0.0                     # monotonic epoch, long past

        def request(self, *args, **kwargs):
            raise AssertionError("no attempt may start past the deadline")

    with pytest.raises(net.BudgetExceeded):
        net.request(Session(), "GET", "https://example.invalid/")


def test_request_caps_timeout_to_remaining_budget():
    seen_timeouts = []

    class Response:
        status_code = 200

        def raise_for_status(self):
            pass

    class Session:
        deadline = time.monotonic() + 5.0

        def request(self, method, url, **kwargs):
            seen_timeouts.append(kwargs["timeout"])
            return Response()

    net.request(Session(), "GET", "https://example.invalid/")
    # the last call is the document; `net.request` reads the host's robots.txt
    # first, which is capped to the same budget and would otherwise be the one
    # this asserts on
    assert seen_timeouts and seen_timeouts[-1] <= 5.0


def test_walk_full_reresolves_downloaded(tmp_path):
    # --full re-resolves items already on disk (foreskrift amendment refresh /
    # the jo_sync --full fall-through)
    bfs = ["fs/2026:2", "fs/2026:1"]
    dates = {bf: "2026-06-30" for bf in bfs}
    on_disk = set(bfs)
    fetched = []

    def resolve(bf):
        fetched.append(bf)
        return True

    result = _run_walk(tmp_path, list(bfs), dates, on_disk, resolve, full=True)
    assert fetched == bfs and result.new == 2


# --- watermark=None: the complete-listing policy (edpb, rs) ----------------

def test_walk_without_watermark_visits_every_entry():
    # a short, complete listing has no depth to stop short of: no watermark is
    # kept and no early stop applies, so every entry is looked at every run
    resolved = []
    items = ["a", "b", "c", "d"]
    current = {"b", "d"}                       # already current on disk

    result = walk(items, resolve=lambda i: resolved.append(i) or True,
                  item_key=lambda i: ItemKey(i, i in current), watermark=None,
                  scope="fk", total=len(items))
    # the current ones are skipped without a fetch; the rest are resolved
    assert resolved == ["a", "c"]
    assert (result.seen, result.new, result.errors) == (4, 2, 0)


def test_walk_without_watermark_full_reresolves_current_entries():
    resolved = []
    result = walk(["a", "b"], resolve=lambda i: resolved.append(i) or True,
                  item_key=lambda i: ItemKey(i, True), watermark=None, full=True)
    assert resolved == ["a", "b"] and result.new == 2


def test_walk_without_watermark_counts_a_failed_fetch_as_an_error(capsys):
    # a document fetch that stores nothing raises, so the record is not written:
    # the item is counted as an error and named, and the next run retries it
    def resolve(item):
        if item == "b":
            raise ValueError("no PDF could be stored; record left unwritten")
        return True

    logged = []
    result = walk(["a", "b", "c"], resolve=resolve,
                  item_key=lambda i: ItemKey(i, False), watermark=None,
                  scope="riktlinjer", log=logged.append)
    assert (result.seen, result.new, result.errors) == (3, 2, 1)
    assert any("riktlinjer b" in line and "left unwritten" in line
               for line in logged)


# --- the record store: one record, one PDF, one scope (edpb, rs) ------------

def test_walk_records_files_a_record_beside_its_pdf(tmp_path):
    # the basefile's first segment names the store subdirectory, and the record
    # asserts its document is on disk, so both land under it together
    record = {"basefile": "riktlinjer/05-2020", "serie": "riktlinjer",
              "titel": "Riktlinjer om samtycke"}
    fetched = []

    def body():
        fetched.append(1)
        return b"%PDF-1.7 minimal"

    assert walk_records(tmp_path, [(record, body)], delay=0) == (1, 1)
    assert compress.exists(tmp_path / "riktlinjer" / "riktlinjer-05-2020.json")
    assert compress.exists(pdf_path(tmp_path, "riktlinjer/05-2020"))
    # a second run over an unchanged listing costs nothing at all -- neither a
    # record rewrite (which would re-stale the parse) nor a refetch
    assert walk_records(tmp_path, [(record, body)], delay=0) == (1, 0)
    assert fetched == [1]


def test_walk_records_full_refetches_and_rewrites(tmp_path):
    record = {"basefile": "wp/248", "serie": "wp"}
    fetched = []
    walk_records(tmp_path, [(record, lambda: b"%PDF-1.7 a")], delay=0)
    assert walk_records(
        tmp_path, [(record, lambda: fetched.append(1) or b"%PDF-1.7 b")],
        delay=0, full=True) == (1, 1)
    assert fetched == [1]


def test_select_pending_narrows_to_one_basefile():
    pending = [({"basefile": "fk/2025:01"}, None), ({"basefile": "fk/2025:02"}, None)]
    assert select_pending(pending, None, "no %s") == pending
    assert select_pending(pending, "fk/2025:02", "no %s") == pending[1:]
    with pytest.raises(ValueError, match="no fk/1999:01"):
        select_pending(pending, "fk/1999:01", "no %s")


def test_dispatch_scopes_hands_only_to_the_scope_that_owns_it(tmp_path):
    # `only` is a basefile, so it names its own scope: the runner it belongs to
    # gets it and every other runner is told None, or a scope would try to
    # narrow itself to a document that is not its own and find nothing
    seen = {}

    def runner(scope):
        def run(root, full=False, only=None, limit=None, delay=0.5):
            seen[scope] = (root, only, full, limit, delay)
            return (1, 0)
        return run

    runners = {"fi": runner("fi"), "fk": runner("fk")}
    totals = dispatch_scopes(tmp_path, None, runners, ("fi", "fk"),
                             only="fk/2025:01", delay=0)
    assert totals == {"fi": (1, 0), "fk": (1, 0)}
    assert seen["fi"][1] is None and seen["fk"][1] == "fk/2025:01"
    assert seen["fk"][0] == str(tmp_path)

    # `scopes` narrows the run to the named ones, in the order given
    seen.clear()
    assert set(dispatch_scopes(tmp_path, ["fk"], runners, ("fi", "fk"))) == {"fk"}
    assert set(seen) == {"fk"}

    with pytest.raises(ValueError, match="no harvest scope 'nope'"):
        dispatch_scopes(tmp_path, ["nope"], runners, ("fi", "fk"))

    # an --only whose scope is not in the run raises instead of silently
    # harvesting everything BUT the one document asked for
    with pytest.raises(ValueError, match="names no scope in this run"):
        dispatch_scopes(tmp_path, ["fi"], runners, ("fi", "fk"),
                        only="fk/2025:01")


def test_fan_out_strict_a_failing_scope_takes_the_run_down():
    def work(scope, log):
        if scope == "b":
            raise ValueError("b is broken")
        return (1, 1)

    with pytest.raises(ValueError, match="b is broken"):
        fan_out(["a", "b", "c"], work, jobs=1, label="t",
                log=lambda line: None)


def test_fan_out_non_strict_serial_runs_the_rest_and_ends_red():
    ran = []
    lines = []

    def work(scope, log):
        ran.append(scope)
        if scope == "b":
            raise ValueError("b is broken")
        return (1, 2)

    with pytest.raises(RuntimeError, match="1 of 3 scopes failed") as ei:
        fan_out(["a", "b", "c"], work, jobs=1, label="t",
                log=lines.append, strict=False)
    # the failure withholds nothing: the scope after it still ran
    assert ran == ["a", "b", "c"]
    assert "b is broken" in str(ei.value)
    # and the run's log carries the scope's failure line, traceback and all
    assert "t b: FAILED ValueError: b is broken" in lines
    assert any("Traceback" in line for line in lines)


def test_fan_out_non_strict_fanned_out_runs_the_rest_and_ends_red():
    ran = []
    lock = threading.Lock()

    def work(scope, log):
        with lock:
            ran.append(scope)
        if scope == "b":
            raise ValueError("b is broken")
        time.sleep(0.02)
        return (1, 1)

    with pytest.raises(RuntimeError, match="1 of 4 scopes failed") as ei:
        fan_out(["a", "b", "c", "d"], work, jobs=4, label="t",
                log=lambda line: None, strict=False)
    # the raising worker's exception reached the coordinator, and the other
    # workers finished their scopes behind it
    assert sorted(ran) == ["a", "b", "c", "d"]
    assert "b is broken" in str(ei.value)
