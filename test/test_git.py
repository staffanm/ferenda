"""accommodanda/lib/git.py -- the push_state probe the ops dashboard reads.
Real temp git repos (a bare 'remote' + a working clone), no network."""

import subprocess

from accommodanda.lib import git


def _run(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, text=True)


def _commit(repo, name):
    (repo / name).write_text("x")
    _run(repo, "add", "-A")
    _run(repo, "-c", "user.name=T", "-c", "user.email=t@e", "commit", "-qm", name)


def test_push_state_non_repo_is_none_false(tmp_path):
    assert git.push_state(tmp_path) == (None, False)


def test_push_state_no_upstream(tmp_path):
    repo = tmp_path / "solo"
    repo.mkdir()
    _run(repo, "init", "-q")
    _commit(repo, "a")
    # no upstream configured -> ahead is None (nothing to be ahead of), clean tree
    assert git.push_state(repo) == (None, False)


def test_push_state_ahead_then_dirty(tmp_path):
    remote = tmp_path / "remote.git"
    remote.mkdir()
    _run(remote, "init", "-q", "--bare")
    work = tmp_path / "work"
    _run(tmp_path, "clone", "-q", str(remote), str(work))
    _commit(work, "a")
    _run(work, "push", "-q", "-u", "origin", "HEAD")

    assert git.push_state(work) == (0, False)          # in sync with upstream

    _commit(work, "b")                                  # one unpushed commit
    assert git.push_state(work) == (1, False)

    (work / "wip.txt").write_text("uncommitted")        # + a dirty working tree
    assert git.push_state(work) == (1, True)
