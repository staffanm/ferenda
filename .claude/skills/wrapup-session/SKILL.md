---
name: wrapup-session
description: End-of-session ritual — full mechanical checks, conventions review with automatic triage-and-fix, doc-drift check, memory writes, then a commit plan that is executed once the user approves it. Use when the user says to wrap up, finish the session, or prepare the work for committing.
---

# Wrapping up a session

Run the phases in order; each phase gates the next. Phase 5 is the only
phase that changes git state, and only after the user approves the plan
(rule:no-unrequested-git). The git-guard hook backs this up in two tiers:
irreversible git (`push`, `reset`, `rebase`, …) is denied to everything
but `commit-planner`, while `commit`/`add`/`stage` prompt the user — so a
straightforward commit can be made directly, and only a tangled tree
needs the subagent.

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

## 2. Conventions review, triage, and fix

Launch the `conventions-enforcer` agent on the working-tree diff. Then
work its findings to closure rather than just relaying them.

**Triage each finding into exactly one bucket.** A finding is a claim,
not a verdict: read the cited `file:line` and the surrounding function
before acting on it.

Severity sets the default, the bucket sets the action. CRITICAL and HIGH
findings are in scope for auto-fix. MEDIUM findings are in scope only
when the fix is mechanical and the correct result is not a matter of
taste — a typo, a duplicated word, a verifiably wrong number, a missing
type on a new field. A MEDIUM finding that rewords the user's prose,
rewraps a paragraph, or picks between defensible phrasings is "needs the
user" no matter how obvious it looks (CLAUDE.md: don't fix what's not
broken). Every MEDIUM fix applied is enumerated individually in the
phase-6 report, because the user's single phase-5 approval otherwise
covers edits they never chose.

- **Fix now** — the finding is real and the correct fix is unambiguous:
  the fallback comes out, the catch narrows, the duplicated helper moves
  to `lib/`, the missing fixture gets written, the `list`-typed field
  gets its real type. Apply it.
- **False positive** — the code at `file:line` does not do what the
  finding says, or the cited rule does not apply here. Drop it and record
  one line of evidence (what the code actually does). Never make a
  cosmetic edit just to clear a finding.
- **Needs the user** — the fix is a judgment call: it changes behaviour,
  picks between designs, touches the golden corpus, or the right answer
  depends on domain intent that is not readable off the code. Leave the
  code alone and carry it to the report as an open question, with your
  recommendation.

Two constraints on every fix applied here: fix the cause, never silence
the symptom — a `# noqa`/`# ty: ignore` is not a fix unless its rationale
names a real constraint (rule:fix-dont-annotate) — and never loosen a
test or a golden expectation to make a finding go away
(rule:never-weaken-tests).

**Then verify the fixes.** Rerun phase 1's checks over the touched files,
and relaunch `conventions-enforcer` scoped to those files to confirm the
findings are actually resolved and that the fixes introduced nothing new.
At most two fix-and-verify rounds: whatever still stands after the second
round becomes "needs the user" and goes in the report. Say so explicitly
if a round hit that ceiling.

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

## 5. Commit plan and execution

For a tangled tree — several logical changes mixed together, or a file
that needs splitting across commits — launch the `commit-planner` agent.
For a tree that is already one coherent change, plan the commit inline
instead: the round trip through a subagent that re-derives a tree this
session already knows costs minutes and buys nothing. Either way the
approval gate below is the same.

**Plan.** Present the plan — grouped commits, subjects per
rule:commit-shape, the exact command sequence, anything left unstaged —
and ask the user once whether to proceed. That single approval covers the
whole plan; do not re-ask per commit.

**Execute.** On approval: if `commit-planner` produced the plan, continue
the *same* agent via SendMessage ("approved", plus any adjustments the
user gave) so it commits with its planning context intact; if the plan
was made inline, run the `git add`/`git commit` commands directly and
confirm at the hook's prompt. Never commit without the user's approval of
the plan (rule:no-unrequested-git) — the prompt is a backstop, not the
gate. If the user wants changes, re-present the revised plan; a modified
plan needs its own approval.

Report what actually landed: the new commits (`git log --oneline` over
the new range) and anything deliberately left uncommitted. If a commit
hook fails, `commit-planner` stops and reports it verbatim — surface that
failure and the resulting partially-committed state plainly; never retry
with `--no-verify`.

## 6. Report

End with a compact summary: what the session accomplished, check status
(green/failures + triage), conventions findings **fixed — with every
MEDIUM fix listed individually — / dismissed as false positives / left
for the user**, docs and memory updated, and the
commits made — or the plan still awaiting approval. The user should be
able to read only this and know exactly where the tree stands.
