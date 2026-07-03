import json
from datetime import date, timedelta
from pathlib import Path

from accommodanda.lib.util import HarvestWatermark


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
