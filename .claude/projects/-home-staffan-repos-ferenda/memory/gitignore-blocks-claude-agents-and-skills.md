---
name: gitignore-blocks-claude-agents-and-skills
description: .gitignore excludes .claude/agents/ and .claude/skills/ from version control — new project agents/skills won't be tracked unless the ignore is amended
metadata:
  type: project
---

`.gitignore` has `/.claude/*` with only `!/.claude/hooks/` and
`!/.claude/settings.json` as exceptions. Anything written to
`.claude/agents/` or `.claude/skills/` is therefore invisible to `git
status`/`git add` as configured — it exists on disk and works locally,
but is not trackable and will not survive a fresh clone.

Discovered 2026-07-02: the four review/guardrail agents (`plan-reviewer`,
`conventions-enforcer`, `docs-sync`, `commit-planner`) and the `/wrapup`
skill built that session were all sitting untracked when the session's
changes were staged for committing. Flagged in
`docs/accommodanda-open-findings-2026-07-02.md` ("Also flagged this
session, outside the review's scope").

**Why:** whoever wrote the `.gitignore` rule was presumably scoping
version control to just the shared hooks + settings, not anticipating
that agents/skills would become a durable part of the guardrail system.

**How to apply:** before relying on any newly-created `.claude/agents/*`
or `.claude/skills/*` file surviving into a future session/clone, check
`git status` actually sees it. If the project wants these versioned
(likely, given they're load-bearing guardrails), the fix is adding
`!/.claude/agents/` and `!/.claude/skills/` to `.gitignore` — ask the
user first, since it's a decision about what gets committed forever, not
a mechanical bug fix.
