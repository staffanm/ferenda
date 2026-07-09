"""The one place we shell out to the git CLI.

Three callers: the inline editor's commit engine (`api/editcart.py`), the
one-time MediaWiki history importer (`tools/mediawiki_to_markdown.py`) and the
SFS history export. They need the same `git -C <repo> …` invocation with
fail-fast errors, so it lives here rather than as copies
(rule:second-use-goes-to-lib).
"""

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
