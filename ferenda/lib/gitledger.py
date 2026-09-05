"""A ledger file for an incremental history-as-git export (`sfs.asgit`,
`eurlex.asgit`): what has already been committed, so a re-run only appends
what's new -- plus the staged atomic publish that moves the branch ref once
a fast-import succeeds.

The ledger lives at `{repodir}/.git/lagen-ledger.json`: inside git's own
directory, so it travels with a plain copy of the repo (`cp -r`/`rsync`/
`tar`) the same way tracked content does, but as an ordinary file nothing
but this tool ever reads or writes -- ferenda's own established pattern for
"what has already been done" state (eurlex's `.no-content`/`.cellar-error`
markers, download watermarks), rather than a bespoke git-notes ref no other
part of the codebase uses. Untouched by `git add`/`commit`/`clone`,
invisible to `git log`/`git show`/`ls` the way a user browsing the repo
would expect.

Written *after* the branch ref moves, not before: a crash in between leaves
the ledger stale (undercounting what is actually committed), and the next
run re-streams the missing entries as new commits on top of the
already-moved tip -- a duplicate commit, the lesser failure. The reverse
order risks the ledger claiming an event is committed when the ref move
never happened, silently dropping that event from every future run."""

import json
import subprocess
from datetime import datetime, timezone

from . import git, util

BRANCH = "main"
BRANCH_REF = "refs/heads/" + BRANCH


def epoch(date, *, clamp=False):
    """A date-only string as a fast-import timestamp (noon UTC -- the sources
    carry no time of day).

    `clamp=True` floors the timestamp at the Unix epoch: git's own ident-line
    parser cannot read back a negative timestamp (`git log` shows 1970-01-01
    for one instead, confirmed against a pre-1970 EU regulation -- not merely
    a display quirk, since `git fsck` calls the commit corrupt). The clamp
    only affects the git-native date; eurlex still prints the true date in the
    commit message, and so does sfs (`Författardatum:` / `Incheckningsdatum:`
    lines). GitHub's receive-side fsck rejects a negative timestamp outright
    (`remote unpack failed: index-pack failed`), so an unclamped export cannot
    be pushed there at all."""
    d = datetime.fromisoformat(date).replace(hour=12, tzinfo=timezone.utc)
    stamp = int(d.timestamp())
    return "%d +0000" % (max(0, stamp) if clamp else stamp)


def data_payload(text):
    """One fast-import `data` block for `text` (str or bytes)."""
    payload = text.encode() if isinstance(text, str) else text
    return b"data %d\n%s\n" % (len(payload), payload)


def path(repodir):
    return repodir / ".git" / "lagen-ledger.json"


def read(repodir):
    """The ledger dict, or `None` when no ledger file exists (a fresh repo,
    or one predating this format -- the caller decides what that means)."""
    p = path(repodir)
    if not p.exists():
        return None
    return json.loads(p.read_text())


def write(repodir, ledger):
    util.write_atomic(path(repodir), json.dumps(
        ledger, ensure_ascii=False, sort_keys=True, indent=2))


def prepare_repo(repodir, branch):
    """Validate a clean dedicated worktree before a history ref can move --
    shared by sfs.asgit and eurlex.asgit so both refuse the same categories
    of target: not a directory, a non-empty directory with no `.git` yet, a
    bare repository, the wrong branch checked out, or uncommitted changes.
    Creates `repodir` and runs `git init` there when it does not exist yet.
    Returns the branch's current tip, or `""` for an unborn branch."""
    if repodir.exists() and not repodir.is_dir():
        raise ValueError("history-as-git target is not a directory: %s" % repodir)
    if not repodir.exists():
        repodir.mkdir(parents=True)
    dotgit = repodir / ".git"
    if not dotgit.exists():
        if any(repodir.iterdir()):
            raise ValueError("history-as-git target is not an empty directory: %s"
                             % repodir)
        git.run(repodir, "init", "-q", "-b", branch)
    if git.run(repodir, "rev-parse", "--is-bare-repository", capture=True) == "true":
        raise ValueError("history-as-git target must have a worktree: %s" % repodir)
    head = git.run(repodir, "symbolic-ref", "-q", "--short", "HEAD",
                   capture=True, check=False)
    if head != branch:
        raise ValueError("history-as-git target must have %s checked out" % branch)
    dirty = git.run(repodir, "status", "--porcelain", capture=True)
    if dirty:
        raise ValueError("history-as-git target has uncommitted changes")
    return git.run(repodir, "rev-parse", "--verify", "-q",
                   "refs/heads/" + branch, capture=True, check=False)


def publish(repodir, stream, *, branch_ref, staging_branch_ref, old_tip,
           reclaim=False):
    """Pipe `stream` (fast-import bytes for the staging ref) into one
    `git fast-import`, then atomically move `branch_ref` to the staging
    tip (compare-and-swap against `old_tip`, the tip this run started
    from -- a concurrent writer's `update-ref` aborts rather than being
    silently overwritten), clean up the staging ref, and materialize the
    worktree. Returns the new tip. Does *not* touch the ledger file --
    write that only after this returns (see the module docstring).

    `reclaim=True` (a rebuild) also expires every reflog and runs
    `git gc --prune=now`: a rebuild's commits share no history with what
    they replace (fast-import gives them no stable identity across runs to
    reuse), so the discarded ones are actually removed, not merely
    unreferenced -- otherwise the repository grows without bound. An
    append never discards anything, so it never needs this."""
    git.run(repodir, "update-ref", "-d", staging_branch_ref)
    proc = subprocess.Popen(["git", "-C", str(repodir), "fast-import",
                             "--quiet"], stdin=subprocess.PIPE)
    out = proc.stdin
    assert out is not None, "Popen(stdin=PIPE) always yields a pipe"
    try:
        for chunk in stream:
            out.write(chunk)
    except BaseException:
        out.close()
        proc.wait()
        raise
    out.close()
    if proc.wait() != 0:
        raise RuntimeError("git fast-import failed (exit %d)" % proc.returncode)
    new_tip = git.run(repodir, "rev-parse", "--verify", staging_branch_ref,
                      capture=True)
    args = ["update-ref", branch_ref, new_tip]
    if old_tip:
        args.append(old_tip)
    git.run(repodir, *args)
    git.run(repodir, "update-ref", "-d", staging_branch_ref)
    git.run(repodir, "reset", "--hard", branch_ref)
    if reclaim:
        git.run(repodir, "reflog", "expire", "--expire=now", "--all")
        git.run(repodir, "gc", "--prune=now", "--quiet")
    return new_tip
