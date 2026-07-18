#!/usr/bin/env python3
"""
PreToolUse hook (Edit|Write|MultiEdit): conventions guardrail.

Two tiers, per docs/conventions.md:

- Hard blocks (permissionDecision "deny"): edits under the read-only legacy
  trees ferenda/ and lagen/ (rule:legacy-read-only), and bare lint/type
  suppressions without a rationale (rule:fix-dont-annotate).
- Soft reminders (additionalContext): the two or three catalog rules relevant
  to the file being edited, injected at the moment they matter.

Always exits 0; decisions travel in the JSON payload.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

# Read-only legacy trees, relative to the project root (rule:legacy-read-only).
LEGACY_TREES = ("ferenda", "lagen")

# A suppression comment with nothing after the code(s) is "bare" unless a
# comment on one of the two preceding lines carries the rationale (the
# lib/poi.py jpype pattern). Judging rationale *quality* is the
# conventions-enforcer agent's job, not this hook's.
BARE_SUPPRESSIONS = (
    re.compile(r"#\s*noqa(?::\s*[A-Z]+\d+(?:\s*,\s*[A-Z]+\d+)*)?\s*$"),
    re.compile(r"#\s*(?:type|ty):\s*ignore(?:\[[^\]]*\])?\s*$"),
)


def bare_suppression_lines(text: str) -> list[int]:
    """0-based indexes of suppression lines with no rationale on the same
    line nor in a comment within the two lines above."""
    lines = text.splitlines()
    out = []
    for i, line in enumerate(lines):
        if not any(rx.search(line) for rx in BARE_SUPPRESSIONS):
            continue
        context = lines[max(0, i - 2):i]
        if any("#" in c and not any(rx.search(c) for rx in BARE_SUPPRESSIONS)
               for c in context):
            continue
        out.append(i)
    return out

VERTICALS = "sfs|dv|eurlex|forarbete|foreskrift|avg|wiki"

# (path regex, reminder) — first the specific, then the general; all matches
# are injected, deduped, in order.
PATH_REMINDERS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"accommodanda/lib/[^/]+\.py$"),
     "lib/ is source-agnostic: never import from a vertical — keying on "
     "artifact metadata is fine, importing source code is not "
     "(rule:lib-never-imports-vertical)."),
    (re.compile(rf"accommodanda/(?:{VERTICALS})/[^/]+\.py$"),
     "Verticals: check lib/ (util, net) before writing a helper; on second "
     "use, promote to lib/ instead of copying (rule:second-use-goes-to-lib); "
     "never import a sibling vertical (rule:lib-never-imports-vertical)."),
    (re.compile(r"accommodanda/(?:[^/]+/)?(?:extract|reader|tokenizer|"
                r"assembler|parse[^/]*|structure|register|nf)\.py$"),
     "Parser/extraction change: lock fixes in with a regression fixture or "
     "golden check (rule:lock-in-with-fixture); prove against the frozen "
     "corpus, not by eyeballing."),
    (re.compile(r"(?:^|/)test/[^/]+\.py$"),
     "Tests: never loosen an assertion/fixture/golden expectation to make a "
     "failure pass — adjudicate deliberately or fix the regression "
     "(rule:never-weaken-tests)."),
    (re.compile(r"accommodanda/.*\.py$"),
     "Asserts over fallbacks (rule:fail-fast); don't catch what you can't "
     "fix (rule:no-catch-log-continue); load-bearing validation raises "
     "ValueError, never assert (rule:errors-drive-retry-use-raise). "
     "Catalog: docs/conventions.md."),
)


def added_content(tool_name: str, tool_input: dict) -> str:
    """The text an Edit/Write/MultiEdit call would introduce."""
    if tool_name == "Write":
        return tool_input.get("content") or ""
    if tool_name == "Edit":
        return tool_input.get("new_string") or ""
    return "\n".join(e.get("new_string") or ""
                     for e in tool_input.get("edits") or [])


def _main() -> int:
    try:
        data = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        return 0
    tool_input = data.get("tool_input") or {}
    raw_path = tool_input.get("file_path")
    if not isinstance(raw_path, str):
        return 0

    project_dir = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd())
    path = Path(raw_path)
    if not path.is_absolute():
        path = project_dir / path
    try:
        rel = path.resolve().relative_to(project_dir.resolve()).as_posix()
    except ValueError:
        rel = path.as_posix()

    blocks: list[str] = []
    if rel.split("/", 1)[0] in LEGACY_TREES:
        blocks.append(
            f"{rel} is in the frozen legacy tree — ferenda/ and lagen/ are "
            "read-only reference (rule:legacy-read-only). Port the knowledge "
            "into accommodanda/ instead; if you believe the legacy tree "
            "itself must change, ask the user.")
    added = added_content(data.get("tool_name") or "", tool_input)
    if rel.endswith(".py") and bare_suppression_lines(added):
        blocks.append(
            "Bare suppression: `# noqa`/`# ty: ignore` needs a rationale "
            "naming the actual constraint — same line or a comment directly "
            "above, e.g. `# noqa: BLE001 — recorded in errors.jsonl, walk "
            "continues` (rule:fix-dont-annotate). Default move: fix the "
            "finding.")

    if blocks:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "\n\n".join(blocks)}}))
        return 0

    reminders = list(dict.fromkeys(
        msg for rx, msg in PATH_REMINDERS if rx.search(rel)))
    if reminders:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": "Conventions reminder:\n- "
                                 + "\n- ".join(reminders)}}))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
