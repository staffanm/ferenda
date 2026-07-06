import json
from datetime import date, timedelta
from pathlib import Path

from accommodanda.lib.harvest import HarvestWatermark, ItemKey, Skip, walk


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
              limit=None, only=None, lookahead=3, safety_days=14):
    """Drive walk() over an in-memory model. `items` is the enumeration (basefile
    strings, optionally with Skip records); `dates`/`on_disk` back item_key."""
    wm = HarvestWatermark(tmp_path / "wm.json", lookahead_limit=lookahead,
                          safety_days=safety_days)
    return walk(items, resolve=resolve,
                item_key=lambda bf: ItemKey(basefile=bf, is_downloaded=bf in on_disk,
                                            date=dates[bf]),
                watermark=wm, full=full, only=only, limit=limit, scope="fs",
                log=lambda *a: None)


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
