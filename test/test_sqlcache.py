"""The batch page cache (ferenda/lib/sqlcache.py): as large as the database
needs, as large as the host can spare, and smaller in business hours, when the
OpenSearch index keeps its place in the OS page cache."""

import datetime
import sqlite3

import pytest

from ferenda.lib import sqlcache

GB = 1 << 30


class _Index:
    def store_bytes(self):
        return 25 * GB


@pytest.fixture
def host(monkeypatch, tmp_path):
    """A 10 GB database on a host with 30 GB available and no cgroup limit."""
    db = tmp_path / "catalog.sqlite"
    db.touch()
    with open(db, "r+b") as f:
        f.truncate(10 * GB)                # sparse: the size is all that is read
    monkeypatch.setattr(sqlcache, "meminfo", lambda field: 30 * GB)
    monkeypatch.setattr(sqlcache, "_cgroup_room", lambda: None)
    monkeypatch.setattr(sqlcache, "SearchIndex", _Index)
    return db


def _at(monkeypatch, business, configured=True):
    monkeypatch.setattr(sqlcache, "business_hours", lambda: business)
    monkeypatch.setattr(sqlcache.config, "SEARCH_BUSINESS_HOURS", configured)


def test_at_night_the_cache_holds_the_whole_database(host, monkeypatch):
    _at(monkeypatch, False)
    assert sqlcache.cache_bytes(host) == 10 * GB


def test_in_business_hours_the_index_keeps_its_memory(host, monkeypatch):
    _at(monkeypatch, True)
    assert sqlcache.cache_bytes(host) == (30 - 25 - 2) * GB


def test_without_the_setting_business_hours_change_nothing(host, monkeypatch):
    _at(monkeypatch, True, configured=False)
    assert sqlcache.cache_bytes(host) == 10 * GB


def test_the_cgroup_limit_caps_the_cache(host, monkeypatch):
    _at(monkeypatch, False)
    monkeypatch.setattr(sqlcache, "_cgroup_room", lambda: 6 * GB)
    assert sqlcache.cache_bytes(host) == 4 * GB


def test_short_memory_leaves_the_minimum(host, monkeypatch):
    _at(monkeypatch, True)
    monkeypatch.setattr(sqlcache, "meminfo", lambda field: 20 * GB)
    assert sqlcache.cache_bytes(host) == sqlcache.MIN_CACHE


def test_a_small_database_asks_nothing_of_the_host(tmp_path, monkeypatch):
    db = tmp_path / "small.sqlite"
    sqlite3.connect(db).execute("CREATE TABLE t (x)")
    monkeypatch.setattr(sqlcache, "meminfo", None)
    monkeypatch.setattr(sqlcache, "SearchIndex", None)
    con = sqlcache.batch_cache(sqlite3.connect(db), db)
    assert con.execute("PRAGMA cache_size").fetchone()[0] == -(db.stat().st_size // 1024)


@pytest.mark.parametrize("when, business", [
    ("2026-10-02 08:00", True),            # a Friday
    ("2026-10-02 17:59", True),
    ("2026-10-02 18:00", False),
    ("2026-10-02 07:59", False),
    ("2026-10-03 12:00", False),           # a Saturday
])
def test_business_hours_are_weekdays_08_to_18_in_stockholm(when, business):
    now = datetime.datetime.fromisoformat(when).replace(tzinfo=sqlcache.STOCKHOLM)
    assert sqlcache.business_hours(now) is business


def test_the_workers_memory_is_reserved_first(host, monkeypatch):
    _at(monkeypatch, False)
    monkeypatch.setattr(sqlcache, "_cgroup_room", lambda: 20 * GB)
    # 30 GB available and 20 GB under the limit, 18 GB of it promised to workers
    assert sqlcache.cache_bytes(host, reserve=18 * GB) == sqlcache.MIN_CACHE
    assert sqlcache.cache_bytes(host, reserve=10 * GB) == (20 - 10 - 2) * GB
