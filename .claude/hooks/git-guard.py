#!/usr/bin/env python3
"""
PreToolUse hook (Bash): state-changing git needs explicit confirmation.

CLAUDE.md / rule:no-unrequested-git: never run a git command that changes
state without an explicit instruction for that operation. This hook turns
every state-changing git invocation into a permission prompt
(permissionDecision "ask") so it surfaces even under acceptEdits/auto modes.
Read-only git (status, diff, log, show, ...) passes untouched, as do the
bare listing forms of branch/tag/stash.

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
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "ask",
        "permissionDecisionReason":
            "State-changing git (rule:no-unrequested-git): "
            + "; ".join(f"`{h}`" for h in hits)
            + ". Confirm only if the user explicitly asked for this "
              "operation — \"fix X\" is not permission to commit X."}}))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
