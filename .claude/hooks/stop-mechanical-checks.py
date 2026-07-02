#!/usr/bin/env python3
"""
Stop hook: run ruff + ty on the accommodanda/ files edited this session.

Extracts file_path values from Edit/Write/MultiEdit tool calls in the session
transcript, keeps existing .py files under accommodanda/ (the active package --
legacy ferenda/ and lagen/ are deliberately out of scope), and runs
`ruff check` and `ty check` on just those files. Findings are fed back to
Claude via the Stop hook block mechanism so they get addressed before the turn
ends. Skips itself when `stop_hook_active` is true to avoid loops on issues
that genuinely cannot be auto-fixed (suppress with a localized
`# ty: ignore[rule]  # reason` instead).

Adapted from the vibe repo's stop-mechanical-checks.py, trimmed to one repo
root (no worktree grouping) and scoped to the accommodanda/ package.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Same shapes and rationale semantics as conventions-reminder.py (which
# denies these at edit time); re-checked here in case a suppression arrived
# via bash/sed (rule:fix-dont-annotate). A suppression is bare when neither
# its own line nor a comment within the two preceding lines carries a
# rationale.
BARE_SUPPRESSIONS = (
    re.compile(r"#\s*noqa(?::\s*[A-Z]+\d+(?:\s*,\s*[A-Z]+\d+)*)?\s*$"),
    re.compile(r"#\s*(?:type|ty):\s*ignore(?:\[[^\]]*\])?\s*$"),
)


def _bare_suppressions(files: list[str], project_dir: Path) -> str:
    """file:line listing of suppression comments lacking a rationale."""
    hits = []
    for rel in files:
        lines = (project_dir / rel).read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if not any(rx.search(line) for rx in BARE_SUPPRESSIONS):
                continue
            context = lines[max(0, i - 2):i]
            if any("#" in c
                   and not any(rx.search(c) for rx in BARE_SUPPRESSIONS)
                   for c in context):
                continue
            hits.append(f"{rel}:{i + 1}: {line.strip()}")
    return "\n".join(hits)


def _edited_paths(transcript_path: Path) -> set[str]:
    """file_path values from Edit/Write/MultiEdit tool calls in the transcript."""
    paths: set[str] = set()
    try:
        with transcript_path.open(encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                content = msg.get("message", {}).get("content")
                if not isinstance(content, list):
                    continue
                for block in content:
                    if (isinstance(block, dict)
                            and block.get("type") == "tool_use"
                            and block.get("name") in ("Edit", "Write", "MultiEdit")):
                        fp = block.get("input", {}).get("file_path")
                        if isinstance(fp, str):
                            paths.add(fp)
    except OSError:
        pass
    return paths


def _find_tool(name: str, project_dir: Path) -> str | None:
    """Locate a checker, preferring the project venv then PATH."""
    venv = project_dir / ".venv" / "bin" / name
    return str(venv) if venv.exists() else shutil.which(name)


def _run(cmd: list[str], cwd: Path) -> tuple[int, str]:
    """Invoke a checker subprocess; return (returncode, combined stdout+stderr)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd,
                           timeout=120, check=False)
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return 1, f"hook error: {e}"
    return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()


def _main() -> int:
    """Read the Stop payload, run checks on edited files, block on findings."""
    try:
        data = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        return 0
    if data.get("stop_hook_active"):
        return 0
    transcript_raw = data.get("transcript_path")
    if not isinstance(transcript_raw, str):
        return 0
    transcript = Path(transcript_raw)
    if not transcript.exists():
        return 0

    project_dir = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd())
    pkg = (project_dir / "accommodanda").resolve()

    files: set[str] = set()
    for raw in _edited_paths(transcript):
        p = Path(raw)
        if not p.is_absolute():
            p = project_dir / p
        p = p.resolve()
        if p.suffix == ".py" and p.exists() and p.is_relative_to(pkg):
            try:
                files.add(str(p.relative_to(project_dir)))
            except ValueError:
                files.add(str(p))
    if not files:
        return 0
    file_args = sorted(files)

    findings: list[tuple[str, str]] = []
    ruff = _find_tool("ruff", project_dir)
    if ruff:
        rc, out = _run([ruff, "check", *file_args], project_dir)
        if rc != 0 and out:
            findings.append(("ruff check", out))
    ty = _find_tool("ty", project_dir)
    if ty:
        rc, out = _run([ty, "check", *file_args], project_dir)
        if rc != 0 and out:
            findings.append(("ty check", out))
    layers = project_dir / ".claude" / "hooks" / "check-layers.py"
    rc, out = _run([sys.executable, str(layers), *file_args], project_dir)
    if rc != 0 and out:
        findings.append(("layer boundaries (docs/conventions.md "
                         "rule:lib-never-imports-vertical)", out))
    out = _bare_suppressions(file_args, project_dir)
    if out:
        findings.append(("bare suppressions (rule:fix-dont-annotate — add "
                         "a rationale naming the constraint, or fix)", out))

    if not findings:
        return 0

    parts = [
        "Mechanical checks failed on accommodanda/ files edited this session.",
        "Fix these before stopping. Prefer a real fix; suppressions need a "
        "same-line rationale naming the constraint (rule:fix-dont-annotate); "
        "see docs/conventions.md.",
        "",
    ]
    for name, body in findings:
        parts += [f"## {name}", body, ""]
    sys.stdout.write(json.dumps(
        {"decision": "block", "reason": "\n".join(parts).rstrip()}) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
