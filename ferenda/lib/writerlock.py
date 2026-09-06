"""One writer at a time over the corpus.

The pipeline assumes a single writer and says so (`runlog`, whose ledger
accepts a prune race), but nothing enforced it. Two `lagen` runs on one corpus
lose each other's work in three separate ways:

  * the run ledger interleaves appends and each prune rewrites the file from
    its own earlier snapshot;
  * the fingerprint store is read into a process-local dict and written back
    whole, so the second writer to finish discards the first's completed
    entries;
  * a full relate builds into one fixed `catalog.sqlite.building` path, which
    each process deletes first -- two rebuilds then write the same scratch
    database and race the final rename. Publishing by atomic rename protects
    *readers* from a half-written catalog; it does nothing about two producers.

So a pipeline action takes this lease first. A second writer is refused with
the first one's identity, rather than quietly corrupting what it finds.

The lease is a directory, because `mkdir` is atomic on every filesystem this
corpus lives on -- including the NFS export the production data_root sits on,
where an `open(O_CREAT|O_EXCL)` on a stale handle is not the guarantee it
looks like, and where `flock` depends on a lock daemon that may not be
running. The holder's identity is written inside it, and a lease whose owner
is gone is broken by the next writer rather than needing a person.

Deliberately not a lock on individual files. The unit that must not overlap is
the *action* -- a whole relate, a whole parse pass -- not each write inside it.
"""

import json
import os
import pathlib
import sys
import threading
import time
import uuid
from pathlib import Path

from .. import config
from . import runlog, util

LOCK = config.DATA / ".build" / "writer.lock"

# How long a lease may go unrefreshed before a later writer treats its holder
# as gone. A live run touches the lease as it works (`Lease.touch`), and the
# longest gap between touches is one document -- an SFS consolidation with
# vision runs in single-digit minutes. An hour is far past that and far short
# of a person noticing a stuck build.
STALE_AFTER = 3600.0

# How often a held lease says it is still working. Well inside `STALE_AFTER`,
# so a few missed beats (a stalled NFS write, a paused process) do not make a
# working run look abandoned.
HEARTBEAT = 300.0


class Held(RuntimeError):
    """Another writer holds the corpus. Its identity is in the message: which
    host, which pid, which command, and when it started."""


def _started(pid):
    """When `pid` started, in kernel clock ticks since boot -- field 22 of
    ``/proc/<pid>/stat``. `None` means *unknown*, never *gone*: /proc may
    refuse the read (`hidepid`, a hardened host) while the process is very much
    alive.

    A pid on its own does not identify a process: pids are reused, and a lease
    left by a killed run would then look alive for ever and lock the corpus
    until someone removed it by hand. The pair (pid, start time) does identify
    one -- within one pid namespace.
    """
    try:
        stat = pathlib.Path("/proc/%d/stat" % pid).read_text()
    except OSError:
        return None
    # the comm field is parenthesised and may itself contain spaces, so fields
    # are counted from after the closing parenthesis
    return stat[stat.rindex(")") + 2:].split()[19]


def _pid_namespace():
    """This process's pid namespace, as an inode number, or `None` where the
    kernel does not say.

    Production runs the pipeline inside a container *and* sets `LAGEN_HOST`, so
    a container and its host report the same `runlog.this_host()` on purpose.
    Their pids come from different namespaces and mean different processes, so
    a lease written on one side must never be judged by looking up its pid on
    the other.
    """
    try:
        return os.stat("/proc/self/ns/pid").st_ino
    except OSError:
        return None


def _alive(holder, host):
    """Whether the holder's process is still running.

    Three answers, and the difference matters: `True` holds the lease, `False`
    releases it to the next writer, and `None` means this machine cannot tell
    -- the lease is then judged by age (`STALE_AFTER`) instead.

    `None` is the answer whenever the question is not really answerable: a
    lease from another host, from another pid namespace, or a pid whose start
    time /proc will not disclose. Reading any of those as `False` would take
    over a live writer's lease and put two of them on one corpus, which is the
    failure this whole module exists to stop -- so an unknown never becomes a
    takeover on its own, only elapsed time does.
    """
    if host != runlog.this_host():
        return None
    namespace = holder.get("pid_namespace")
    if namespace is not None and namespace != _pid_namespace():
        return None                     # its pids are not ours to look up
    try:
        os.kill(holder["pid"], 0)
    except ProcessLookupError:
        return False
    except PermissionError:      # someone else's process, so it exists
        pass
    started = holder.get("started_ticks")
    if started is None:                 # written by an older lease
        return True
    mine = _started(holder["pid"])
    if mine is None:                    # /proc will not say; the pid does exist
        return None
    # the pid exists; it is the holder's only if it is the same process
    return mine == started


def _read(lock):
    """The holder record inside `lock`, or None when the lease is malformed --
    a directory created by a writer that died between mkdir and write."""
    record = lock / "holder.json"
    if not record.is_file():
        return None
    text = record.read_text(encoding="utf-8")
    return json.loads(text) if text.strip() else None


def _stale(holder, lock):
    """Whether an existing lease may be taken over.

    A lease taken on this machine is judged by its holder's process alone: a
    relate over 200,000 artifacts runs for hours, and age would call a working
    run abandoned. Age decides only for a lease from another host, where no
    process can be asked about -- and a run there refreshes the lease as it
    works (`Lease`'s heartbeat), so an unrefreshed one really is gone.
    """
    if holder is None:
        return True
    alive = _alive(holder, holder["host"])
    if alive is not None:
        return not alive
    touched = (lock / "holder.json").stat().st_mtime
    return time.time() - touched > STALE_AFTER


class Lease:
    """The held lease. It refreshes itself while it is held, and leaving the
    `with` releases it.

    The heartbeat is what makes the lease safe for a corpus written from more
    than one machine: a writer on another host cannot ask this one about a pid,
    so it reads the lease's age instead, and a run that is working has to keep
    saying so. A daemon thread, so it never holds the process open.
    """

    def __init__(self, path, holder):
        self.path = path
        self.holder = holder
        self._stop = threading.Event()
        self._beat = threading.Thread(target=self._heartbeat, daemon=True,
                                      name="writer-lease")
        self._beat.start()

    def _heartbeat(self):
        while not self._stop.wait(HEARTBEAT):
            self.touch()

    def _held_unsafe(self):
        """Whether the lease on disk is still *this* one.

        It may not be. A lease this run was judged to have abandoned -- its
        process killed, or its host silent past `STALE_AFTER` -- is taken over
        by the next writer, which writes its own record into the same
        directory. This object must then do nothing at all: touching would
        make a dead writer look alive, and releasing would hand the corpus to
        a third run while the second is still writing it.
        """
        holder = _read(self.path)
        return holder is not None and holder.get("token") == self.holder["token"]

    def held(self):
        """`_held_unsafe`, with an unreadable lease answered "not ours".

        `release` runs in `build.main`'s `finally`, so a raise here would
        replace the run's own exception. And "cannot read it" is not "it is
        mine": leaving a lease alone costs one stale-lease takeover by the next
        writer, while deleting one that might be someone else's costs the
        corpus.
        """
        try:
            return self._held_unsafe()
        except (OSError, ValueError) as exc:
            print("writer lock: cannot read %s (%s); leaving it alone"
                  % (self.path, exc), file=sys.stderr, flush=True)
            return False

    def touch(self):
        """Say the holder is still working. A lease on another host that stops
        being touched for `STALE_AFTER` is treated as gone by the next writer."""
        if self.held():
            (self.path / "holder.json").touch()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.release()

    def release(self):
        """Give the lease up, if it is still ours.

        Never raises: this runs in `build.main`'s `finally`, ahead of the run
        ledger's own end record, so an exception here would replace the run's
        real failure and lose the ledger entry with it.
        """
        self._stop.set()
        if not self.held():
            return
        try:
            (self.path / "holder.json").unlink(missing_ok=True)
            self.path.rmdir()
        except OSError as exc:
            # gone already, a takeover put something back between the two
            # calls, or the export answered an error. Either way this run no
            # longer owns the lease, and the next writer's staleness check is
            # what clears it.
            print("writer lock: could not release %s (%s)" % (self.path, exc),
                  file=sys.stderr, flush=True)


def acquire(command, *, run_id=None, lock=LOCK):
    """Take the corpus writer lease for `command`, or raise `Held`.

    `command` is what the run is doing, in the words the operator typed
    ("sfs parse", "all relate") -- it is the whole message a second writer
    gets, so it has to name the run rather than the module.
    """
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        lock.mkdir()
    except FileExistsError:
        holder = _read(lock)
        if not _stale(holder, lock):
            raise Held(
                "%s is already writing this corpus: %s (pid %d on %s, started "
                "%s). Wait for it, or remove %s if you know it is gone."
                % (holder["command"], holder.get("run_id", "-"), holder["pid"],
                   holder["host"], holder["started"], lock)) from None
        # the holder is gone: take the lease over rather than asking a person
        # to clear up after a crash
        print("writer lock: taking over a lease left by %s"
              % (holder["command"] if holder else "an interrupted run"),
              flush=True)
    holder = {"command": command, "run_id": run_id, "pid": os.getpid(),
              "started_ticks": _started(os.getpid()),
              "pid_namespace": _pid_namespace(),
              "host": runlog.this_host(),
              # what tells this lease from one that replaced it after a
              # takeover: `Lease.held` compares it before touching or releasing
              "token": uuid.uuid4().hex,
              "started": time.strftime("%Y-%m-%d %H:%M:%S")}
    # written through a temp name and renamed, never unlinked first: a reader
    # arriving mid-takeover sees the old holder or the new one, never a
    # directory with no holder in it, which it would read as a free lease
    util.write_atomic(lock / "holder.json", json.dumps(holder))
    return Lease(lock, holder)


def scratch_name(base: Path, run_id: str) -> Path:
    """A scratch path this run alone owns: ``catalog.sqlite.<run_id>.building``.

    A fixed `.building` name is safe only while the writer lease holds. It is
    the second line: a run never deletes or writes a name another run could
    have chosen, so an aborted rebuild leaves a file that is obviously its own
    rather than one the next rebuild silently adopts.
    """
    return base.with_name("%s.%s.building" % (base.name, run_id))


def sweep_scratch(base: Path, keep: str) -> list[Path]:
    """Remove scratch files left by runs that are not `keep`, and return what
    was removed. Called by the writer that holds the lease, which is by then
    the only run that can own any of them.

    The trailing `*` takes SQLite's sidecars with the file: an aborted rebuild
    can leave `…building-wal` and `…building-shm` beside its database, and a
    sweep that removed only the database would leave those to accumulate one
    pair per abandoned run."""
    mine = scratch_name(base, keep).name
    removed = []
    for path in base.parent.glob("%s.*.building*" % base.name):
        if not path.name.startswith(mine):
            path.unlink(missing_ok=True)
            removed.append(path)
    return removed

