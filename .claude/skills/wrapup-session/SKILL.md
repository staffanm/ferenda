---
name: wrapup-session
description: End-of-session ritual — full mechanical checks, conventions review, second opinion and corpus blast-radius verification with a fresh subagent, doc-drift check, memory writes, then an approved commit plan. Use when the user says to wrap up, finish the session, or prepare the work for committing.
---

# Wrapping up a session

Run the phases in order; each phase gates the next. Phase 6 is the only
phase that changes git state, and only after the user approves the plan
(rule:no-unrequested-git). The git-guard hook backs this up in two tiers:
irreversible git (`push`, `reset`, `rebase`, …) is denied to everything
but `commit-planner`, while `commit`/`add`/`stage` prompt the user — so a
straightforward commit can be made directly, and only a tangled tree
needs the subagent.

## 1. Mechanical pass (whole package, not just edited files)

```
.venv/bin/ruff check ferenda
.venv/bin/ty check ferenda
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
phase-7 report, because the user's single phase-6 approval otherwise
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

## 3. Second opinion & blast-radius verification

A clean mechanical pass and a passing test suite do not prove that a parser
or normaliser is free of regressions across the corpus. Unit tests only
cover the examples the author thought to write.

For any session that touches parsers, harvesters, metadata extraction, or
shared library helpers: launch the fresh `second-opinion` subagent on the
diff. The subagent reviews the changes with zero author context bias.

1. **Inappropriate coupling:** Verify that helpers designed for one
   representation (e.g. PDF two-column masthead interleaved cleaner) are
   not inappropriately hooked into a different data source (e.g. web harvest
   listing titles).
2. **Blast radius on stored corpus data:** For parser/normaliser changes,
   run the blast radius tool against real stored records:
   ```
   .venv/bin/python .claude/skills/second-opinion/scripts/diff_blast_radius.py \
       --file <modified_file> \
       --function <modified_function> \
       --records "<glob_pattern>" \
       --fields "<args>"
   ```
   Check that legitimate dates, designations, and words are not stripped,
   and that the number of altered records matches the targeted defect.
3. **Test integrity:** Inspect all new tests to ensure they do not merely
   assert the buggy behavior, and that no tests were weakened
   (rule:never-weaken-tests).

Triage findings: any regression on real data or inappropriate coupling
must be resolved before moving to the commit plan. When the session only
touched documentation, comments, or chore edits with no parser or logic
changes: state in one line why no second opinion is needed.

## 4. Documentation drift

If the session changed module layout, a vertical's status, pipeline
phases, or CLI invocations: launch the `docs-sync` agent
(rule:docs-follow-structure). Otherwise state in one line why no doc
update is needed.

## 5. Memory

Consider whether the session produced knowledge that belongs in
persistent memory: user corrections and confirmed approaches (with the
*why*), project decisions not derivable from code or git history, domain
facts learned the hard way. Do not save what the repo already records.
Write the memory files, update the index, and tell the user what was
saved.

## 6. Commit plan and execution

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

## 7. Report

End with a compact summary: what the session accomplished, check status
(green/failures + triage), conventions findings **fixed — with every
MEDIUM fix listed individually — / dismissed as false positives / left
for the user**, second-opinion findings, docs and memory updated, and the
commits made — or the plan still awaiting approval. The user should be
able to read only this and know exactly where the tree stands.
