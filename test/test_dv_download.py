"""Hermetic (network-free) tests for the dv downloader: the paged walk over a
fake /sok API, driven through the shared lib.harvest walk/watermark lifecycle.
Locks in the two download-loop regressions from the 2026-07-06 review: a
crashed or --limit-truncated run must not let the next run stop above (and the
watermark advance past) the un-fetched backlog, and a referat published long
after its avgörandedatum (the norm for curated referat) must still be reached
by an incremental scan."""

import json

import pytest

from accommodanda.dv import download
from accommodanda.lib.harvest import HarvestWatermark


def rec(n, datum, innehall="<p>x</p>"):
    """A minimal API search hit: valid UUID + court code (record_dir validates
    both), the decision date the walk is ordered by, no attachments."""
    return {"id": "00000000-0000-0000-0000-%012d" % n,
            "domstol": {"domstolKod": "HDO", "domstolNamn": "Högsta domstolen"},
            "avgorandedatum": datum, "bilagaLista": [], "innehall": innehall}


class FakeApi:
    """The paged POST /sok endpoint over an in-memory corpus: pages of
    `page_size` in ascending or descending avgörandedatum order, then an empty
    page -- the shape sync()'s enumeration consumes."""

    def __init__(self, records, page_size=3):
        self.records = sorted(records, key=lambda r: r["avgorandedatum"])
        self.page_size = page_size

    def add(self, *records):
        self.records = sorted(self.records + list(records),
                              key=lambda r: r["avgorandedatum"])

    def search_page(self, session, index, asc):
        ordered = self.records if asc else self.records[::-1]
        return {"publiceringLista":
                ordered[index * self.page_size:(index + 1) * self.page_size],
                "total": len(ordered)}


def on_disk(destdir, record):
    return download.record_dir(destdir, record).with_suffix(".json").exists()


def seed_downloaded(destdir, api, *records):
    """Put records on disk *and* in the fake corpus, as a completed prior run
    would have left them."""
    api.add(*records)
    for record in records:
        download.save_record(destdir, record)


def watermark(destdir):
    return HarvestWatermark(destdir / ".watermark.json")


def run_sync(destdir, **kwargs):
    return download.sync(destdir, bilagor=False, delay=0, **kwargs)


# --------------------------------------------------------------------------
# backfill -- first run walks everything oldest-first and completes clean
# --------------------------------------------------------------------------

def test_backfill_downloads_all_and_completes_clean(tmp_path, monkeypatch):
    api = FakeApi([rec(n, "2026-0%d-15" % n) for n in range(1, 7)])
    monkeypatch.setattr(download, "search_page", api.search_page)

    seen, changed = run_sync(tmp_path)
    assert (seen, changed) == (6, 6)
    assert all(on_disk(tmp_path, r) for r in api.records)
    wm = watermark(tmp_path)
    assert wm.dirty is False and wm.last_harvest == "2026-06-15"


def test_interrupted_backfill_resumes_as_backfill(tmp_path, monkeypatch):
    # no completed run yet (last_harvest None, even though begin() wrote the
    # watermark file): the next run must still walk oldest-first from the top
    api = FakeApi([rec(n, "2026-0%d-15" % n) for n in range(1, 7)])
    monkeypatch.setattr(download, "search_page", api.search_page)

    calls = {"n": 0}
    real_save = download.save_record

    def crashing_save(destdir, record):
        calls["n"] += 1
        if calls["n"] == 3:
            raise KeyboardInterrupt
        return real_save(destdir, record)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(download, "save_record", crashing_save)
        with pytest.raises(KeyboardInterrupt):
            run_sync(tmp_path)
    assert watermark(tmp_path).last_harvest is None    # nothing completed

    seen, changed = run_sync(tmp_path)
    assert changed == 4                                # the 2 written are kept
    assert all(on_disk(tmp_path, r) for r in api.records)
    assert watermark(tmp_path).dirty is False


# --------------------------------------------------------------------------
# review finding 1 (HIGH): a crashed incremental run must not strand the
# backlog below the records it already wrote
# --------------------------------------------------------------------------

def test_crashed_incremental_run_leaves_dirty_and_next_run_gets_backlog(
        tmp_path, monkeypatch):
    api = FakeApi([])
    monkeypatch.setattr(download, "search_page", api.search_page)
    # a completed prior state: 30 recent + an old tail on disk, clean watermark
    seed_downloaded(tmp_path, api,
                    *[rec(100 + n, "2026-06-%02d" % (n + 1)) for n in range(30)],
                    *[rec(200 + n, "2024-01-%02d" % (n + 1)) for n in range(5)])
    HarvestWatermark(tmp_path / ".watermark.json").save("2026-06-30")

    # upstream publishes 10 new records; the run crashes after writing 4
    fresh = [rec(n, "2026-07-%02d" % n) for n in range(1, 11)]
    api.add(*fresh)
    calls = {"n": 0}
    real_save = download.save_record

    def crashing_save(destdir, record):
        calls["n"] += 1
        if calls["n"] == 5:
            raise KeyboardInterrupt
        return real_save(destdir, record)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(download, "save_record", crashing_save)
        with pytest.raises(KeyboardInterrupt):
            run_sync(tmp_path)

    # the newest 4 are on disk, the watermark is dirty and NOT advanced
    assert sum(on_disk(tmp_path, r) for r in fresh) == 4
    wm = watermark(tmp_path)
    assert wm.dirty is True and wm.last_harvest == "2026-06-30"

    # the next plain run walks past the crashed run's fresh records down to
    # the date boundary and downloads the stranded backlog, then heals
    seen, changed = run_sync(tmp_path)
    assert changed == 6
    assert all(on_disk(tmp_path, r) for r in fresh)
    assert seen < len(api.records)          # date-conclusive stop, not a full walk
    wm = watermark(tmp_path)
    assert wm.dirty is False and wm.last_harvest == "2026-07-10"


def test_limit_truncated_run_stays_dirty_and_does_not_advance_watermark(
        tmp_path, monkeypatch):
    api = FakeApi([])
    monkeypatch.setattr(download, "search_page", api.search_page)
    seed_downloaded(tmp_path, api,
                    *[rec(100 + n, "2026-06-%02d" % (n + 1)) for n in range(10)],
                    *[rec(200 + n, "2024-01-%02d" % (n + 1)) for n in range(5)])
    HarvestWatermark(tmp_path / ".watermark.json").save("2026-06-10")

    fresh = [rec(n, "2026-07-%02d" % n) for n in range(1, 6)]
    api.add(*fresh)
    seen, changed = run_sync(tmp_path, limit=2)
    assert changed == 2
    wm = watermark(tmp_path)
    assert wm.dirty is True                    # truncated: never completed
    assert wm.last_harvest == "2026-06-10"     # not advanced past the backlog

    seen, changed = run_sync(tmp_path)         # the untruncated follow-up heals
    assert changed == 3
    assert all(on_disk(tmp_path, r) for r in fresh)
    wm = watermark(tmp_path)
    assert wm.dirty is False and wm.last_harvest == "2026-07-05"


# --------------------------------------------------------------------------
# review finding 2 (MEDIUM): a referat published months after its
# avgörandedatum sorts below already-downloaded records; the scan must still
# reach it (safety window sized to the curated series' publication cadence)
# --------------------------------------------------------------------------

def test_late_published_referat_within_window_is_downloaded(
        tmp_path, monkeypatch):
    api = FakeApi([])
    monkeypatch.setattr(download, "search_page", api.search_page)
    # 25 consecutive already-downloaded recent records -- more than the old
    # lookahead of 20 that used to stop the scan -- plus an old tail past the
    # window so the date-conclusive stop terminates the run
    seed_downloaded(tmp_path, api,
                    *[rec(100 + n, "2026-06-%02d" % (n + 1)) for n in range(25)],
                    *[rec(200 + n, "2024-01-%02d" % (n + 1)) for n in range(5)])
    HarvestWatermark(tmp_path / ".watermark.json").save("2026-06-25")

    # a referat published today whose decision date is 3+ months back: below
    # all 25 downloaded records in the walk, above the one-year boundary
    late = rec(1, "2026-03-15")
    api.add(late)
    seen, changed = run_sync(tmp_path)
    assert changed == 1 and on_disk(tmp_path, late)
    assert seen < len(api.records)          # stopped at the boundary, not a full walk
    wm = watermark(tmp_path)
    assert wm.dirty is False and wm.last_harvest == "2026-06-25"


def test_full_sweep_picks_up_upstream_edits(tmp_path, monkeypatch):
    # the documented backstop for arbitrarily-late publications and record
    # edits: --full re-resolves records already on disk
    api = FakeApi([])
    monkeypatch.setattr(download, "search_page", api.search_page)
    seed_downloaded(tmp_path, api, rec(1, "2026-05-01"), rec(2, "2026-06-01"))
    HarvestWatermark(tmp_path / ".watermark.json").save("2026-06-01")

    api.records[0] = rec(1, "2026-05-01", innehall="<p>rättad</p>")
    seen, changed = run_sync(tmp_path, full=True)
    assert (seen, changed) == (2, 1)
    stored = json.loads(download.record_dir(tmp_path, api.records[0])
                        .with_suffix(".json").read_text())
    assert stored["innehall"] == "<p>rättad</p>"
    wm = watermark(tmp_path)
    assert wm.dirty is False and wm.last_harvest == "2026-06-01"
