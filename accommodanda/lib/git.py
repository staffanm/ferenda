"""The one place we shell out to the git CLI.

Two callers: the inline editor's commit engine (`api/editcart.py`) and the
one-time MediaWiki history importer (`tools/mediawiki_to_markdown.py`). Both need
the same `git -C <repo> …` invocation with `check=True` -- a git failure is a
real error, surfaced, never swallowed -- so it lives here rather than as two
copies (rule:second-use-goes-to-lib).
"""

import subprocess


def run(repo, *args, env=None, capture=False):
    """Run one ``git -C <repo> <args>``. Returns the stripped stdout when
    `capture` is set (a value we want, e.g. `rev-parse HEAD`), else `None` with
    git's own stdout chatter discarded. `env` overrides the process environment
    (the importer/editor set `GIT_AUTHOR_*`/`GIT_COMMITTER_*` there)."""
    if capture:
        done = subprocess.run(["git", "-C", str(repo), *args], check=True,
                              env=env, text=True, capture_output=True)
        return done.stdout.strip()
    subprocess.run(["git", "-C", str(repo), *args], check=True, env=env,
                   stdout=subprocess.DEVNULL)
    return None
