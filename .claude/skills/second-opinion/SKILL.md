---
name: second-opinion
description: Request an independent, adversarial second opinion from a fresh subagent on working-tree changes before wrapping up or committing. Dissects diffs for blast radius, inappropriate helper coupling, corpus regressions, and tests that paper over defects. Use before finishing work on parsers, harvesters, extractors, or when reviewing working tree diffs.
---

# Second Opinion: Adversarial Pre-Commit Verification

This skill executes an independent review of the current working-tree changes using a
fresh subagent context. Its primary purpose is to catch regressions, false assumptions,
and corpus-scale collateral damage that unit tests miss.

The primary authoring agent often suffers from confirmation bias: it wrote the code,
wrote the unit tests that pass, and assumes its solution is sound. A fresh subagent has
no context bias and evaluates the changes critically.

---

## When to Run

Run this skill before wrapping up or committing whenever the session touches:
1. **Parsers, normalisers, or cleanup functions** (e.g. `clean_title`, regexes, text splits).
2. **Harvesters or enumeration logic** (e.g. `index_soups`, `DocRef` generation, selectors).
3. **Core models or metadata extraction** (e.g. `extract_metadata`, `andrar_target`, repeal targets).
4. **Shared library machinery** in `ferenda/lib/`.
5. Any change where unit tests pass, but corpus-scale behavior could be altered.

---

## Execution Steps

### 1. Launch the Fresh `second-opinion` Subagent

Invoke the subagent with the list of modified files and the high-level intent of the task.
Do NOT pass session rationale or justifications—let the subagent inspect the diff
objectively.

```text
Task: Provide an adversarial second opinion on the current working-tree diff.
Target files: <list of files from git status>
Goal: Verify whether the changes introduce unintended regressions, inappropriate tool coupling,
or corpus-level damage.
```

### 2. Subagent Inspection Checklist

The subagent must verify four specific failure modes:

#### A. Inappropriate Helper Coupling (Context Mismatch)
- Did the change call a helper designed for one representation in a different context?
  *(Example: running PDF two-column masthead interleaved cleaner on single-line web harvest titles).*
- What assumptions and preconditions does the helper make? Does the caller satisfy them?
- Could aggressive stripping rules (dates, numbers, series names) eat valid data in the new caller?

#### B. Blast Radius on Stored Corpus Data
When parsers, normalisers, or extractors are modified:
- **Do not rely solely on unit tests.** Run the blast radius comparison script against stored
  records (`site/data/downloaded/` or `site/data/artifact/`):
  ```bash
  .venv/bin/python .claude/skills/second-opinion/scripts/diff_blast_radius.py \
      --file <modified_file> \
      --function <modified_function> \
      --records "<glob_pattern_to_stored_records>" \
      --fields "<comma_separated_arg_fields>"
  ```
- **Evaluate the blast radius metrics:**
  - Are the number of changed records consistent with the intended fix?
    *(e.g., fixing 3 broken records should not alter 450 records across the corpus).*
  - Did legitimate dates, years, or ISO stamps get stripped?
  - Did cited designations or parenthetical references disappear?
  - Did any titles or fields turn empty or `None` unexpectedly?

#### C. Test Integrity and Circular Verification
- Inspect all new or modified test fixtures in `test/`.
- Did the author write a test that merely asserts the buggy behavior?
  *(e.g. asserting that a general title cleaner removes legitimate dates because the regex did so).*
- Were any existing assertions or test tolerances weakened or loosened? (`rule:never-weaken-tests`).
- Does the test use realistic production data or artificial strings that mask edge cases?

#### D. Error Handling & Architectural Invariants
- Did changes turn fatal errors into silent `Skip`/`None` fallbacks? (`rule:fail-fast`).
- Did changes broaden exception catches without recovering from the root cause? (`rule:narrow-what-you-catch`).
- Did vertical logic leak into `lib/`? (`rule:lib-never-imports-vertical`).

---

## 3. Triage Findings

The subagent reports findings categorized into:
- **Critical (Must Fix)**: Regressions on real data, inappropriate tool coupling, lost dates/designations, or circular tests.
- **Warning (Verify Intent)**: Changes affecting more documents than originally targeted.
- **Sound**: Validated improvements with zero unintended side effects.

If regressions are found, return to the code and fix the root cause before completing the wrapup.
