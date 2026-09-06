"""The corpus writer lease: one pipeline writer at a time."""

import json
import os
import time
from pathlib import Path

import pytest

from ferenda.lib import writerlock


@pytest.fixture
def lock(tmp_path):
    return tmp_path / "writer.lock"


def test_a_second_writer_is_refused_and_told_who_holds_it(lock):
    with writerlock.acquire("sfs parse", run_id="r1", lock=lock):
        with pytest.raises(writerlock.Held, match="sfs parse"):
            writerlock.acquire("all relate", run_id="r2", lock=lock)


def test_the_lease_is_released_when_the_run_ends(lock):
    with writerlock.acquire("sfs parse", lock=lock):
        assert lock.is_dir()
    assert not lock.exists()
    # and the next writer takes it without complaint
    with writerlock.acquire("all relate", lock=lock):
        assert lock.is_dir()


def test_a_lease_whose_process_is_gone_is_taken_over(lock):
    """A run killed mid-write leaves the directory behind. The next writer
    must not need a person to clear it: the holder's pid is recorded, and a pid
    that no longer exists on this host is a lease nobody owns."""
    lease = writerlock.acquire("sfs parse", lock=lock)
    holder = json.loads((lock / "holder.json").read_text())
    holder["pid"] = _dead_pid()
    (lock / "holder.json").write_text(json.dumps(holder))
    with writerlock.acquire("all relate", lock=lock) as second:
        assert second.holder["command"] == "all relate"
        assert second.holder["pid"] == os.getpid()
    assert lease  # the first lease object is stale, which is the point


def test_a_lease_from_another_host_is_judged_by_age(lock, monkeypatch):
    """A shared corpus is written from more than one machine, and one host
    cannot ask another about a pid. Age is the only answer it has."""
    writerlock.acquire("sfs parse", lock=lock)
    holder = json.loads((lock / "holder.json").read_text())
    holder["host"] = "some-other-box"
    (lock / "holder.json").write_text(json.dumps(holder))

    with pytest.raises(writerlock.Held, match="some-other-box"):
        writerlock.acquire("all relate", lock=lock)

    # ...until it goes untouched for longer than the lease allows
    monkeypatch.setattr(writerlock, "STALE_AFTER", -1.0)
    with writerlock.acquire("all relate", lock=lock) as second:
        assert second.holder["command"] == "all relate"


def test_a_malformed_lease_is_not_a_deadlock(lock):
    """A writer killed between `mkdir` and the write leaves a lease with no
    holder in it. That must not lock the corpus for ever."""
    lock.mkdir(parents=True)
    with writerlock.acquire("all relate", lock=lock) as lease:
        assert lease.holder["command"] == "all relate"


def test_the_lease_reports_itself_to_a_shell_caller(lock, capsys):
    """The deploy asks whether anything is writing before it recreates the
    container -- `docker compose exec` dies with its container, so a deploy
    that lands mid-pipeline truncates the run with no error at all. A shell
    caller gets the answer as an exit code: 3 while a writer holds the corpus,
    0 when it is free, and the two are distinct so a check that could not run
    is never read as `free`."""
    assert writerlock.holder(lock) is None
    assert writerlock.main(lock=lock) == 0
    with writerlock.acquire("all rebuild", lock=lock):
        held = writerlock.holder(lock)
        assert held is not None and held["command"] == "all rebuild"
        assert writerlock.main(lock=lock) == 3
    assert writerlock.holder(lock) is None
    assert writerlock.main(lock=lock) == 0


def test_a_lease_nobody_owns_reads_as_free_to_the_deploy(lock):
    """The deploy must use the same judgement the next writer would: a lease
    whose process is gone is not a reason to hold a release back, or a crashed
    pipeline would block every deploy for five hours and then give up."""
    writerlock.acquire("sfs parse", lock=lock)
    holder = json.loads((lock / "holder.json").read_text())
    holder["pid"] = _dead_pid()
    (lock / "holder.json").write_text(json.dumps(holder))
    assert writerlock.holder(lock) is None


def test_scratch_files_are_named_per_run(tmp_path):
    base = tmp_path / "catalog.sqlite"
    mine = writerlock.scratch_name(base, "r1")
    theirs = writerlock.scratch_name(base, "r2")
    assert mine != theirs
    assert mine.name == "catalog.sqlite.r1.building"


def test_the_sweep_removes_every_scratch_but_this_run_s(tmp_path):
    base = tmp_path / "catalog.sqlite"
    for run in ("r1", "r2", "r3"):
        scratch = writerlock.scratch_name(base, run)
        scratch.write_text("x")
        # what an aborted rebuild leaves beside its database
        scratch.with_name(scratch.name + "-wal").write_text("x")
    removed = writerlock.sweep_scratch(base, keep="r2")
    assert sorted(p.name for p in removed) == [
        "catalog.sqlite.r1.building", "catalog.sqlite.r1.building-wal",
        "catalog.sqlite.r3.building", "catalog.sqlite.r3.building-wal"]
    mine = writerlock.scratch_name(base, "r2")
    assert mine.exists() and mine.with_name(mine.name + "-wal").exists()


def _dead_pid():
    """A pid nothing is using: the first one above the current maximum that
    /proc does not list. The test only needs `os.kill` to raise
    ProcessLookupError for it."""
    for pid in range(os.getpid() + 1, os.getpid() + 10000):
        if not Path("/proc/%d" % pid).exists():
            return pid
    raise AssertionError("no free pid found")


def test_a_long_run_on_this_host_is_never_taken_over(lock, monkeypatch):
    """A relate over 200,000 artifacts runs for hours without a beat reaching
    disk on a stalled mount. Age must not call it abandoned: on this machine
    the holder's own process is the answer."""
    writerlock.acquire("all relate", lock=lock)
    monkeypatch.setattr(writerlock, "STALE_AFTER", -1.0)   # "long past its age"
    with pytest.raises(writerlock.Held, match="all relate"):
        writerlock.acquire("sfs parse", lock=lock)


def test_the_lease_refreshes_itself_while_it_is_held(lock, monkeypatch):
    """The heartbeat is what makes the age rule honest for a writer on another
    host, which has no pid it can ask about."""
    monkeypatch.setattr(writerlock, "HEARTBEAT", 0.01)
    record = lock / "holder.json"
    with writerlock.acquire("all relate", lock=lock):
        before = record.stat().st_mtime_ns
        os.utime(record, ns=(before - 10**9, before - 10**9))
        deadline = time.monotonic() + 2.0
        while record.stat().st_mtime_ns <= before - 10**9:
            assert time.monotonic() < deadline, "the lease was never refreshed"
            time.sleep(0.01)


def test_a_taken_over_lease_is_not_released_by_the_run_it_replaced(lock):
    """The failure this guards: run A's lease is taken over by run B, then A
    ends and its `release()` removes B's holder record and the directory with
    it. A third run then acquires freely and two writers share one corpus --
    silently, which is worse than the race the lease exists to stop."""
    first = writerlock.acquire("run A", run_id="a", lock=lock)
    _make_holder_look_dead(lock)
    second = writerlock.acquire("run B", run_id="b", lock=lock)

    first.release()                      # A ends, unaware it was replaced

    assert lock.is_dir(), "A removed the lease B is holding"
    assert json.loads((lock / "holder.json").read_text())["command"] == "run B"
    with pytest.raises(writerlock.Held, match="run B"):
        writerlock.acquire("run C", lock=lock)
    second.release()


def test_a_taken_over_lease_is_not_refreshed_by_the_run_it_replaced(lock):
    """The same object's heartbeat must not touch the new holder's record:
    an age check on another host would read a dead writer as alive."""
    first = writerlock.acquire("run A", run_id="a", lock=lock)
    _make_holder_look_dead(lock)
    writerlock.acquire("run B", run_id="b", lock=lock)

    record = lock / "holder.json"
    old = record.stat().st_mtime_ns - 10**9
    os.utime(record, ns=(old, old))
    first.touch()
    assert record.stat().st_mtime_ns == old, "A refreshed B's lease"


def test_a_reused_pid_does_not_lock_the_corpus_for_ever(lock):
    """A pid alone does not identify a process. A lease left by a killed run
    whose pid was later reused would otherwise look alive for ever, and only a
    person removing the directory could clear it."""
    writerlock.acquire("a run that was killed", run_id="doomed", lock=lock)
    holder = json.loads((lock / "holder.json").read_text())
    # this process is alive, but it is not the one that took the lease
    holder["started_ticks"] = str(int(holder["started_ticks"]) + 1)
    (lock / "holder.json").write_text(json.dumps(holder))
    with writerlock.acquire("the next run", lock=lock) as taken:
        assert taken.holder["command"] == "the next run"


def _make_holder_look_dead(lock):
    """Rewrite the lease on disk so the next `acquire` judges it abandoned."""
    holder = json.loads((lock / "holder.json").read_text())
    holder["pid"] = _dead_pid()
    (lock / "holder.json").write_text(json.dumps(holder))


def test_a_lease_whose_start_time_cannot_be_read_is_not_taken_over(lock,
                                                                   monkeypatch):
    """`/proc` can refuse the read while the process is alive (hidepid, a
    hardened host). Reading "cannot tell" as "dead" would take over a live
    writer's lease -- worse than the reused-pid hole it was added to close."""
    writerlock.acquire("a live run", run_id="live", lock=lock)
    monkeypatch.setattr(writerlock, "_started", lambda pid: None)
    with pytest.raises(writerlock.Held, match="a live run"):
        writerlock.acquire("the next run", lock=lock)


def test_a_lease_from_another_pid_namespace_is_judged_by_age(lock, monkeypatch):
    """Production runs the pipeline in a container and sets LAGEN_HOST, so the
    container and its host report one name on purpose. Their pids come from
    different namespaces and name different processes, so this pid must not be
    looked up at all -- age decides, as it does for another host."""
    writerlock.acquire("a run inside the container", run_id="c", lock=lock)
    holder = json.loads((lock / "holder.json").read_text())
    holder["pid_namespace"] = (holder.get("pid_namespace") or 0) + 1
    (lock / "holder.json").write_text(json.dumps(holder))

    with pytest.raises(writerlock.Held, match="inside the container"):
        writerlock.acquire("a run on the host", lock=lock)

    monkeypatch.setattr(writerlock, "STALE_AFTER", -1.0)
    with writerlock.acquire("a run on the host", lock=lock) as taken:
        assert taken.holder["command"] == "a run on the host"


def test_release_never_raises_over_an_unreadable_lease(lock, capsys):
    """`release` runs in build.main's `finally`, ahead of the run ledger's end
    record. A raise there would replace the run's real exception and lose the
    ledger entry with it."""
    lease = writerlock.acquire("a run", run_id="r", lock=lock)
    (lock / "holder.json").write_text("{ this is not json")
    lease.release()                       # must not raise
    assert "cannot read" in capsys.readouterr().err
    # and it left the lease alone rather than deleting one it could not read
    assert (lock / "holder.json").exists()
