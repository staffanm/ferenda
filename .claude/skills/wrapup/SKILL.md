---
name: wrapup
description: End-of-session ritual — full mechanical checks, conventions review of the diff, doc-drift check, memory writes, and a proposed (never executed) commit plan. Use when the user says to wrap up, finish the session, or prepare the work for committing.
---

# Wrapping up a session

Run the phases in order; each phase gates the next. Never run
state-changing git anywhere in this flow (rule:no-unrequested-git).

## 1. Mechanical pass (whole package, not just edited files)

```
.venv/bin/ruff check accommodanda
.venv/bin/ty check accommodanda
python3 .claude/hooks/check-layers.py          # package-wide: also flags stale allowlist entries
.venv/bin/python -m pytest -q                  # bare pytest collects exactly the new suites
```

Triage every failure: caused by this session's changes → fix now;
pre-existing → verify that (e.g. rerun against the pristine file from
`git show HEAD:<path>` in the scratchpad) and report it explicitly —
never silently absorb a failure into "known issues", and never weaken a
test to get past it (rule:never-weaken-tests).

## 2. Conventions review

Launch the `conventions-enforcer` agent on the working-tree diff. Apply
CRITICAL and HIGH findings now (then rerun phase 1 on the touched files);
list MEDIUM findings for the user to decide.

## 3. Documentation drift

If the session changed module layout, a vertical's status, pipeline
phases, or CLI invocations: launch the `docs-sync` agent
(rule:docs-follow-structure). Otherwise state in one line why no doc
update is needed.

## 4. Memory

Consider whether the session produced knowledge that belongs in
persistent memory: user corrections and confirmed approaches (with the
*why*), project decisions not derivable from code or git history, domain
facts learned the hard way. Do not save what the repo already records.
Write the memory files, update the index, and tell the user what was
saved.

## 5. Commit plan

Launch the `commit-planner` agent. Present its plan — grouped commits,
subjects per rule:commit-shape, exact commands — as the final section of
the wrap-up report, and ask the user whether to proceed. On approval,
continue the same agent via SendMessage ("approved", plus any
adjustments) so it executes the commits with its planning context
intact; never run the commit commands yourself, and never commit
without the user's approval of the plan (rule:no-unrequested-git).

## 6. Report

End with a compact summary: what the session accomplished, check status
(green/failures + triage), findings applied vs deferred, docs/memory
updated, and the commit plan. The user should be able to read only this
and know exactly where the tree stands.
