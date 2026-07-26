#!/usr/bin/env python3
"""
PreToolUse hook (Bash): git writes are gated in two tiers.

rule:no-unrequested-git enforced structurally, by what the command can destroy:

- Tier 1, irreversible or publishing (push, reset, rebase, checkout, clean, ...):
  denied outright (permissionDecision "deny") to the main session and every
  subagent except `commit-planner`, which is auto-allowed.
- Tier 2, recoverable (commit, add, stage): "ask" -- the user confirms at a
  prompt. Claude cannot commit unrequested, and the gate is the user's consent
  rather than which agent typed the command.
- Read-only git (status, diff, log, show, ...) and the bare listing forms of
  branch/tag/stash pass untouched.

A command mixing both tiers is denied: in `git add . && git push`, the push
decides. The caller is identified by the `agent_type` field the harness passes
the hook (the subagent's name; absent for the main session), which a prompt
cannot forge -- so this, not message provenance, is the trust boundary.

Always exits 0; the decision travels in the JSON payload.
"""
from __future__ import annotations

import json
import re
import sys

# Classification is an ALLOWLIST: a subcommand is let through only by appearing
# in READ_ONLY (or matching its LISTING_OK pattern), and everything else -- known
# or not -- is gated. A denylist here failed open for every subcommand nobody
# thought of: `apply`, `bisect`, `update-ref`, `reflog expire`, `fetch`,
# `repack` all reached the shell unchecked, and `reflog expire` in particular
# destroys the very reflog that tier 2's recoverability argument rests on. A
# guardrail whose default for the unknown case is "allow" is a fallback masking
# a bug (rule:fail-fast).
READ_ONLY = {
    "status", "diff", "log", "show", "blame", "annotate", "describe",
    "shortlog", "whatchanged", "grep", "help", "version",
    "ls-files", "ls-tree", "ls-remote", "cat-file", "rev-parse", "rev-list",
    "merge-base", "name-rev", "diff-tree", "diff-index", "diff-files",
    "check-ignore", "check-attr", "check-ref-format", "count-objects",
    "verify-commit", "verify-tag", "fsck", "var", "instaweb",
}

# Recoverable: the objects stay reachable after the fact (a commit through the
# reflog, a staged blob through `fsck --lost-found`), so these return "ask" and
# the user confirms at a prompt rather than the call being denied. Rationale:
# the old rule denied them to everyone but commit-planner, which made every
# commit a ~5-minute round trip through a subagent re-deriving a tree the
# session already knew -- while gating on *which agent typed the command* rather
# than on the user's consent (commit-planner was auto-allowed on its first
# commit, plan or no plan). "ask" costs one keystroke and gates on the thing
# that actually matters (rule:no-unrequested-git).
CONFIRMABLE = {"commit", "add", "stage"}

# `git <global flags> <subcommand> <args...>` occurrences anywhere in the
# command line (covers `cd x && git commit`, `git -C path add .`, pipes).
#
# Three things this pattern has to get right, each of which was a live bypass:
#   * `-c key=value` is TWO tokens, so the `-[cC]\s+\S+\s+` alternative must be
#     tried before the single-token flag form -- otherwise `-c ` matches alone
#     and `foo=bar` is read as the subcommand, making `git -c foo=bar push`
#     invisible.
#   * the args group must not absorb a shell separator, glued or spaced:
#     `git add .;git push` and `git log -5;git push` otherwise parse as one
#     call whose arguments merely contain the text "git push".
#   * the subcommand itself must not be the literal `git`, or `GIT_DIR=.git git
#     push` swallows the real call.
GIT_CALL = re.compile(
    r"\bgit\s+((?:-[cC]\s+\S+\s+|-[\w=/.-]+\s+)*)"
    r"(?!git\b)([a-z][\w-]*)"
    r"((?:\s+(?!&&|\|\||;|\||git\b)[^\s;|&<>]+)*)")

# Subcommands that are read-only only in their listing/query forms; any other
# argument shape falls through to the gate. Read-only scope and format flags may
# precede the read action -- `git config --local --get user.email` is a query,
# and denying it only teaches the caller to go read .git/config by hand.
LISTING_OK = {
    "branch": re.compile(r"^\s*(?:(?:-[avr]+|--list|--all|--merged|"
                         r"--no-merged|--show-current|--format=\S+|"
                         r"--contains(?:\s+\S+)?)\s*)*$"),
    "tag": re.compile(r"^\s*(?:(?:-l|--list|-n\d*|--contains(?:\s+\S+)?|"
                      r"--format=\S+|--sort=\S+)\s*)*$"),
    "stash": re.compile(r"^\s*(?:list|show)(?:\s+\S+)*\s*$"),
    "remote": re.compile(r"^\s*(?:-v|--verbose|show(?:\s+\S+)*|"
                         r"get-url(?:\s+\S+)*)?\s*$"),
    "config": re.compile(r"^\s*(?:--(?:local|global|system|worktree|"
                         r"show-origin|show-scope|includes|no-includes|null|"
                         r"name-only|type=\S+)\s+)*"
                         r"(?:--get\S*|--list|-l)(?:\s+\S+)*$"),
    "worktree": re.compile(r"^\s*list(?:\s+\S+)*$"),
    "notes": re.compile(r"^\s*(?:list|show)(?:\s+\S+)*$"),
    # Bare `git reflog` and `reflog show` read; `expire` and `delete` destroy
    # the reflog that the confirmable tier's recoverability argument rests on.
    "reflog": re.compile(r"^\s*(?:show(?:\s+\S+)*)?\s*$"),
}


def state_changing_calls(command: str) -> tuple[list[str], list[str]]:
    """The gated `git <sub> ...` invocations found in a command.

    Returns (denied, confirmable) -- tier-1 hits and tier-2 hits respectively.
    Quoted segments are stripped first so `rg 'git commit' docs/` doesn't
    trigger; the threat here is an absent-minded git call, not an
    adversarial bypass via `bash -c "..."`.
    """
    hits: list[str] = []
    confirmable: list[str] = []
    command = re.sub(r"'[^']*'|\"[^\"]*\"", " ", command)
    for m in GIT_CALL.finditer(command):
        sub, args = m.group(2), m.group(3)
        if sub in READ_ONLY:
            continue
        listing = LISTING_OK.get(sub)
        if listing and listing.match(args):
            continue
        (confirmable if sub in CONFIRMABLE else hits).append(
            f"git {sub}{args}".strip())
    return hits, confirmable


def _emit(decision: str, reason: str) -> int:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "permissionDecisionReason": reason}}))
    return 0


def _main() -> int:
    # Fail closed. A hook that returns nothing is a hook that allowed the
    # command, so a payload this can't read must deny rather than shrug
    # (rule:fail-fast) -- the same reason a crash here would be a security bug.
    try:
        data = json.loads(sys.stdin.read())
    except json.JSONDecodeError as exc:
        return _emit("deny", f"git-guard could not parse the hook payload "
                             f"({exc}); denying rather than failing open.")
    command = (data.get("tool_input") or {}).get("command")
    if not isinstance(command, str):
        return _emit("deny", "git-guard received no command string; denying "
                             "rather than failing open.")
    hits, confirmable = state_changing_calls(command)
    if not hits and not confirmable:
        return 0
    joined = "; ".join(f"`{h}`" for h in hits + confirmable)
    # Trust boundary: `agent_type` is the calling subagent's name (absent -> the
    # main session); the harness sets it and a prompt cannot forge it, so this --
    # not who "approved" in a message -- is the enforcement point.
    #
    # commit-planner keeps its blanket allow. Everyone else: a tier-1 hit denies
    # the whole command (a mixed `git add . && git push` is a push, and the
    # dangerous half decides); a command that is purely tier-2 asks the user,
    # who is the only one entitled to authorise a commit (rule:no-unrequested-git).
    if data.get("agent_type") == "commit-planner":
        decision, reason = "allow", "commit-planner (git-write agent): " + joined
    elif hits:
        who = data.get("agent_type") or "the main session"
        decision = "deny"
        reason = (f"Irreversible git is restricted to the commit-planner "
                  f"subagent (rule:no-unrequested-git); {who} may not run: "
                  f"{joined}. Launch commit-planner to make commits.")
    else:
        decision = "ask"
        # Name what was actually found rather than asserting "this commit":
        # this prompt is the last line of defence precisely when the command is
        # not what it looks like.
        reason = (f"Confirm (rule:no-unrequested-git): {joined}. Claude must "
                  f"not run these unless you asked for this specific "
                  f"operation -- decline if you did not.")
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "permissionDecisionReason": reason}}))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
