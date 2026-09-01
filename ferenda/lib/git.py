"""The one place we shell out to the git CLI.

Callers: the inline editor's commit engine (`api/editcart.py`), the patch-file
editor (`api/patch.py`), the ops dashboard's push-state probe (`api/ops.py`),
the one-time MediaWiki history importer (`tools/migrations/mediawiki_to_markdown.py`),
and the history-as-git exports (`sfs/asgit.py`, `eurlex/asgit.py`) -- both
through `lib/gitledger.py`'s shared ledger, and `sfs/asgit.py` itself again for
its legacy-trailer detection (`legacy_ledger`). They need the same
`git -C <repo> …` invocation with fail-fast errors, so it lives here rather
than as copies (rule:second-use-goes-to-lib) -- as does `commit_as`, the
attributed commit both editors make.
"""

import os
import subprocess


def run(repo, *args, env=None, capture=False, check=True):
    """Run one ``git -C <repo> <args>``. Returns the stripped stdout when
    `capture` is set (a value we want, e.g. `rev-parse HEAD`), else `None` with
    git's own stdout chatter discarded. `env` overrides the process environment
    (the importer/editor set `GIT_AUTHOR_*`/`GIT_COMMITTER_*` there).
    `check=False` is only for capture-mode existence probes (e.g. `rev-parse
    --verify` of a maybe-unborn ref) where a nonzero exit is an answer, not an
    error; non-capture invocations always fail fast."""
    if capture:
        done = subprocess.run(["git", "-C", str(repo), *args], check=check,
                              env=env, text=True, capture_output=True)
        return done.stdout.strip()
    assert check, "non-capture git runs always fail fast"
    subprocess.run(["git", "-C", str(repo), *args], check=True, env=env,
                   stdout=subprocess.DEVNULL)
    return None


def commit_as(repo, paths, message, *, name, email):
    """Stage exactly `paths` and commit them authored *and* committed as
    (`name`, `email`) -- both identities set, so `git log` attributes a web edit
    to the person who made it, indistinguishable from a local commit. Returns
    the new sha, or the unchanged HEAD when the staged paths hold no change (a
    no-op edit, which `git commit` would otherwise exit non-zero on and 500).

    `add -A` so a pathspec whose file was *deleted* (the patch editor removing a
    patch) stages the deletion; for a path that exists it is plain `git add`."""
    run(repo, "add", "-A", "--", *paths)
    if not run(repo, "status", "--porcelain", "--", *paths, capture=True):
        return run(repo, "rev-parse", "HEAD", capture=True)
    env = {**os.environ,
           "GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email,
           "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email}
    run(repo, "commit", "-m", message, "--", *paths, env=env)
    return run(repo, "rev-parse", "HEAD", capture=True)


def push_state(repo):
    """`(ahead, dirty)` for a working checkout: how many commits `HEAD` is ahead
    of its configured upstream, and whether the working tree has uncommitted
    changes. `ahead` is ``None`` when there is no upstream (nothing to be ahead
    of) or `repo` isn't a git checkout at all. All probes run capture-mode with
    ``check=False`` -- a missing upstream / non-repo is an answer, not an error
    (the ops dashboard reads this best-effort and must render regardless)."""
    if run(repo, "rev-parse", "--is-inside-work-tree", capture=True, check=False) != "true":
        return None, False
    upstream = run(repo, "rev-parse", "--abbrev-ref", "@{u}", capture=True, check=False)
    ahead = (int(run(repo, "rev-list", "--count", "@{u}..HEAD",
                     capture=True, check=False) or 0)
             if upstream else None)
    dirty = bool(run(repo, "status", "--porcelain", capture=True, check=False))
    return ahead, dirty
