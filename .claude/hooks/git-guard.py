#!/usr/bin/env python3
"""
PreToolUse hook (Bash): state-changing git is restricted to commit-planner.

rule:no-unrequested-git enforced structurally: only the `commit-planner`
subagent may run state-changing git (commit, add, push, reset, ...) -- it is
auto-allowed (permissionDecision "allow"); the main session and every other
subagent are denied (permissionDecision "deny") and must launch commit-planner
to commit. The caller is identified by the `agent_type` field the harness
passes the hook (the subagent's name; absent for the main session), which a
prompt cannot forge -- so this, not message provenance, is the trust boundary.
Read-only git (status, diff, log, show, ...) and the bare listing forms of
branch/tag/stash pass untouched.

Always exits 0; the decision travels in the JSON payload.
"""
from __future__ import annotations

import json
import re
import sys

STATE_CHANGING = {
    "commit", "add", "stage", "push", "reset", "revert", "stash",
    "checkout", "switch", "merge", "rebase", "branch", "tag", "restore",
    "rm", "mv", "clean", "cherry-pick", "pull", "am", "notes", "worktree",
    "filter-branch", "gc", "prune", "remote", "submodule", "config",
}

# `git <global flags> <subcommand> <args...>` occurrences anywhere in the
# command line (covers `cd x && git commit`, `git -C path add .`, pipes).
GIT_CALL = re.compile(
    r"\bgit\s+((?:-[\w=/.-]+\s+|-[cC]\s+\S+\s+)*)([a-z][\w-]*)((?:\s+\S+)*)")

# Subcommands whose bare/listing forms are read-only.
LISTING_OK = {
    "branch": re.compile(r"^\s*(?:(?:-[avr]+|--list|--all|--merged|"
                         r"--no-merged|--contains(?:\s+\S+)?)\s*)*$"),
    "tag": re.compile(r"^\s*(?:(?:-l|--list|-n\d*|--contains(?:\s+\S+)?|"
                      r"--sort=\S+)\s*)*$"),
    "stash": re.compile(r"^\s*(?:list|show)(?:\s+\S+)*\s*$"),
    "remote": re.compile(r"^\s*(?:-v|--verbose|show(?:\s+\S+)*|"
                         r"get-url(?:\s+\S+)*)?\s*$"),
    "config": re.compile(r"^\s*(?:--get\S*|--list|-l)(?:\s+\S+)*$"),
    "worktree": re.compile(r"^\s*list(?:\s+\S+)*$"),
    "notes": re.compile(r"^\s*(?:list|show)(?:\s+\S+)*$"),
}


def state_changing_calls(command: str) -> list[str]:
    """The state-changing `git <sub> ...` invocations found in a command.

    Quoted segments are stripped first so `rg 'git commit' docs/` doesn't
    trigger; the threat here is an absent-minded git call, not an
    adversarial bypass via `bash -c "..."`.
    """
    hits = []
    command = re.sub(r"'[^']*'|\"[^\"]*\"", " ", command)
    for m in GIT_CALL.finditer(command):
        sub, args = m.group(2), m.group(3)
        if sub not in STATE_CHANGING:
            continue
        listing = LISTING_OK.get(sub)
        if listing and listing.match(args):
            continue
        hits.append(f"git {sub}{args}".strip())
    return hits


def _main() -> int:
    try:
        data = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        return 0
    command = (data.get("tool_input") or {}).get("command")
    if not isinstance(command, str):
        return 0
    hits = state_changing_calls(command)
    if not hits:
        return 0
    joined = "; ".join(f"`{h}`" for h in hits)
    # Trust boundary: state-changing git is the commit-planner subagent's job
    # alone. `agent_type` is the calling subagent's name (absent -> the main
    # session); the harness sets it and a prompt cannot forge it, so this -- not
    # who "approved" in a message -- is the enforcement point. commit-planner is
    # auto-allowed; everyone else (the main session, every other subagent) is
    # denied and must launch commit-planner to make commits.
    if data.get("agent_type") == "commit-planner":
        decision, reason = "allow", "commit-planner (git-write agent): " + joined
    else:
        who = data.get("agent_type") or "the main session"
        decision = "deny"
        reason = (f"State-changing git is restricted to the commit-planner "
                  f"subagent (rule:no-unrequested-git); {who} may not run: "
                  f"{joined}. Launch commit-planner to make commits.")
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "permissionDecisionReason": reason}}))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
