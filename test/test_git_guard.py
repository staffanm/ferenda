"""git-guard hook: the two-tier permission decision.

The hook is a guardrail, so its decision matrix is a test, not an eyeball
check. Tier 1 (irreversible/publishing) denies, tier 2 (commit/add/stage)
asks the user, read-only passes through, and `commit-planner` keeps its
blanket allow.
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).parent.parent / ".claude" / "hooks" / "git-guard.py"


def decide(command: str, agent_type: str | None = None) -> str:
    """Run the hook the way the harness does; return its permission decision."""
    payload = {"tool_input": {"command": command}}
    if agent_type:
        payload["agent_type"] = agent_type
    proc = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                          capture_output=True, text=True, timeout=20)
    # A crashing PreToolUse hook fails *open* -- every git command would run
    # unguarded -- so a non-empty stderr is itself a failure, not a warning.
    assert proc.stderr == "", f"hook wrote to stderr:\n{proc.stderr}"
    assert proc.returncode == 0, f"hook exited {proc.returncode}"
    if not proc.stdout.strip():
        return "pass-through"
    return json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecision"]


@pytest.mark.parametrize("command", [
    "git push",
    "git push origin modernization",
    "git reset --hard HEAD~3",
    "git checkout master",
    "git branch -D topic",
    "git clean -fd",
    "git rebase -i HEAD~2",
    "git stash",
])
def test_irreversible_is_denied(command):
    assert decide(command) == "deny"


@pytest.mark.parametrize("command", [
    "git commit -m x",
    'git commit -m "fix the thing"',
    "git add .",
    "git add docs/ REWRITE.md",
    "git add . && git commit -m x",
    "cd /tmp && git commit -m x",
    "git -C /repo add .",
])
def test_recoverable_asks_the_user(command):
    assert decide(command) == "ask"


@pytest.mark.parametrize("command", [
    "git status --porcelain",
    "git diff --stat",
    "git log --oneline -20",
    "git show HEAD:file.py",
    "git branch -a",
    "git tag --list",
    "git stash list",
    "git status && git diff",
    'rg "git push" docs/',          # quoted mention, not a call
])
def test_read_only_passes_through(command):
    assert decide(command) == "pass-through"


@pytest.mark.parametrize("command", [
    "git add . && git push",
    "git add . ; git push",
    "git commit -m x && git push origin",
    "git push && git add .",
    # Glued separators: no space before the operator. The first fix only
    # stopped the args group at a *token* starting with a separator, so these
    # still parsed as one call whose arguments contained the text "git push".
    "git add .;git push",
    "git add .&&git push",
    "git commit -m x;git checkout master",
    "git add .;git clean -fdx",
    # Worse class: a read-only leading call swallowed the dangerous one, so
    # the command reached the shell with no decision at all.
    "git log --oneline -5;git push",
    "git status --porcelain|git push",
    "git diff > /tmp/x&&git push",
])
def test_tier_mixing_denies_on_the_dangerous_half(command):
    """Regression: the args group used to swallow a following `git <sub>`.

    `git add . && git push` parsed as one `add` whose arguments merely
    contained the text "git push", downgrading a publish to a confirmable
    add -- and `git log -5;git push` downgraded it to no decision at all.
    Each git call must be seen separately so the dangerous one decides.
    """
    assert decide(command) == "deny"


@pytest.mark.parametrize("command", [
    "git -c foo=bar push",
    "git -c core.pager=cat push origin",
])
def test_two_token_global_flags_do_not_hide_the_subcommand(command):
    """Regression: `-c key=value` is two tokens.

    The single-token flag alternative matched `-c ` alone, so `foo=bar` was
    read as the subcommand and the real one was never classified.
    """
    assert decide(command) == "deny"


def test_global_flags_before_a_confirmable_subcommand_still_ask():
    """Same parse bug, tier-2 half: the classification must land on `commit`.

    "ask" (not "deny") is correct here -- commit is confirmable by design, and
    the user sees the full command, `-c user.email=...` included, at the
    prompt. The bug was that this reached the shell classified as nothing.
    """
    assert decide("git -c user.email=x commit -m y") == "ask"


def test_a_token_ending_in_git_does_not_swallow_the_call():
    """Regression: `([a-z][\\w-]*)` happily matched the literal `git`."""
    assert decide("GIT_DIR=.git git push") == "deny"


@pytest.mark.parametrize("command", [
    "git apply patch.diff",
    "git bisect start",
    "git update-ref -d refs/heads/topic",
    "git reflog expire --expire=now --all",
    "git fetch --prune",
    "git repack -ad",
    "git sparse-checkout set x",
    "git update-index --assume-unchanged f",
    "git fast-import",
    "git replace a b",
])
def test_unlisted_subcommands_fail_closed(command):
    """The classifier is an allowlist: anything unrecognised is gated.

    As a denylist these all passed through unchecked -- including
    `reflog expire`, which destroys the reflog that the confirmable tier's
    recoverability argument depends on.
    """
    assert decide(command) == "deny"


@pytest.mark.parametrize("command", [
    "git config --local --get user.email",
    "git config --show-origin --get user.email",
    "git config --global --get user.name",
    "git branch --show-current",
])
def test_read_only_queries_with_scope_flags_pass(command):
    """Denying these only teaches the caller to go read .git/config by hand."""
    assert decide(command) == "pass-through"


@pytest.mark.parametrize("command", [
    "git config user.email x@y.z",
    "git config --local user.name Test",
    "git config --unset user.email",
])
def test_config_writes_are_still_denied(command):
    assert decide(command) == "deny"


def test_malformed_payload_denies_rather_than_failing_open():
    proc = subprocess.run([sys.executable, str(HOOK)], input="not json",
                          capture_output=True, text=True, timeout=20)
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["hookSpecificOutput"][
        "permissionDecision"] == "deny"


@pytest.mark.parametrize("command", ["git commit -m x", "git push"])
def test_commit_planner_keeps_its_blanket_allow(command):
    assert decide(command, agent_type="commit-planner") == "allow"


def test_other_subagents_get_the_same_treatment_as_the_main_session():
    assert decide("git push", agent_type="docs-sync") == "deny"
    assert decide("git commit -m x", agent_type="docs-sync") == "ask"


def test_hook_module_imports_cleanly():
    """Guards against a syntax/NameError that would make the hook fail open."""
    spec = importlib.util.spec_from_file_location("git_guard", HOOK)
    assert spec is not None and spec.loader is not None, f"cannot load {HOOK}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.CONFIRMABLE == {"commit", "add", "stage"}
    assert "status" in module.READ_ONLY and "diff" in module.READ_ONLY
    # Classification is an allowlist, so the sets must stay disjoint: a
    # subcommand in both would be let through by whichever check ran first.
    assert not (module.READ_ONLY & module.CONFIRMABLE), "tiers must not overlap"
    assert not (module.READ_ONLY & set(module.LISTING_OK)), "tiers must not overlap"
    # `push` is deliberately in no set at all -- it is gated by falling through.
    assert "push" not in module.READ_ONLY | module.CONFIRMABLE
