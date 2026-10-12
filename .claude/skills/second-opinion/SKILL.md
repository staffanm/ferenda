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
2. **Harvesters or enumeration logic** (e.g. `index_soups`, `DocRef` generation, selectors, `agencies.py`).
3. **Core models or metadata extraction** (e.g. `extract_metadata`, `andrar_target`, repeal targets, `parse_record`).
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

The subagent must verify five specific failure modes:

#### A. Inappropriate Helper Coupling & False Domain Assumptions
- Did the change call a helper designed for one representation in a different context?
  *(Example: running PDF two-column masthead interleaved cleaner on single-line web harvest titles).*
- **Never rationalize an author's filter or exclusion:** When the diff introduces an exclusion
  or heuristic (e.g. skipping documents with "tillkännagivande" from repeals, or restricting title lengths):
  - Do NOT assume standard legislative nomenclature or author rationales apply uniformly across all 76+ agencies.
  - **Actively search the corpus for counterexamples.** (e.g., search stored TFS documents to see if tillkännagivanden
    actually contain repeal clauses like "ersätter").
  - Tullverket, Migrationsverket, DIGG, SCB, and Elsäkerhetsverket each have divergent and legacy publishing practices.
- What assumptions and preconditions does the helper make? Does the caller satisfy them?
- Could aggressive stripping rules (dates, numbers, series names) eat valid data in the new caller?

#### B. Full-Document and Semantic Blast Radius on Stored Corpus Data
When parsers, normalisers, or extractors are modified:
- **Do not rely solely on unit tests, and do not test isolated string helpers alone.**
  Run the blast radius comparison script against stored records (`site/data/downloaded/` or `site/data/artifact/`).
  For field helpers:
  ```bash
  uv run python .agents/skills/second-opinion/scripts/diff_blast_radius.py \
      --file <modified_file> \
      --function <modified_function> \
      --records "<glob_pattern_to_stored_records>" \
      --fields "<comma_separated_arg_fields>"
  ```
  For whole-document parsers (`parse_record`):
  ```bash
  uv run python .agents/skills/second-opinion/scripts/diff_blast_radius.py \
      --file ferenda/foreskrift/parse.py \
      --function parse_record \
      --records "site/data/downloaded/foreskrift/*/*.json.br" \
      --record-arg \
      --root site/data/downloaded/foreskrift \
      --scopes "<optional_comma_separated_scopes>"
  ```
- **Evaluate semantic blast radius metrics:**
  - **Dropped repeals / relations:** Did any document lose non-empty `upphaver` or `andrar` lists?
  - **Chrome leakage in body:** Did documents with no PDF gain body text? If so, did landing page
    HTML chrome ("Dela sidan på Facebook", menu links, "Status: Upphävda", footers) leak into regulation bodies?
  - **Volume consistency:** Are the number of changed records consistent with the intended fix?
    *(e.g., fixing 3 broken records should not alter 450 records across the corpus).*
  - **Dropped fields:** Did legitimate dates, years, designations, or titles turn empty or `None`?
  - **Structure degradation:** Did any regulation lose all its `paragraf` or `kapitel` nodes?

#### C. Harvester and Enumeration Verification (Watermark Trap & Completeness)
When harvesters or enumeration logic are modified (e.g. `agencies.py`, `harvest.py`):
- **Incremental Watermark Trap:** Harvesters run incrementally by default and skip records already present on disk
  or stop when encountering existing items.
  - Adding archive links or predecessor series will NOT download missing documents unless `--force` or `--deep` is used.
  - Verify whether the harvest was executed with the required flags on affected scopes.
  - Check the filesystem: Do the new or missing records actually exist on disk under `site/data/downloaded/<scope>/`?
- **Issue Completeness Audit:** If the task addresses a batch of reported issues, verify that *every* issue has an
  actual corresponding code change and on-disk verification, not just the subset the author touched.

#### D. Test Integrity and Circular Verification
- Inspect all new or modified test fixtures in `test/`.
- Did the author write a test that merely asserts the buggy behavior?
  *(e.g. asserting that a general title cleaner removes legitimate dates because the regex did so).*
- Were any existing assertions or test tolerances weakened or loosened? (`rule:never-weaken-tests`).
- Does the test use realistic production data or artificial strings that mask edge cases?

#### E. Error Handling & Architectural Invariants
- Did changes turn fatal errors into silent `Skip`/`None` fallbacks? (`rule:fail-fast`).
- Did changes broaden exception catches without recovering from the root cause? (`rule:narrow-what-you-catch`).
- Did vertical logic leak into `lib/`? (`rule:lib-never-imports-vertical`).

---

## 3. Triage Findings

The subagent reports findings categorized into:
- **Critical (Must Fix)**: Regressions on real data, dropped repeals/dates/designations, chrome in body text,
  inappropriate tool coupling, or circular tests.
- **Warning (Verify Intent)**: Changes affecting more documents than originally targeted, unexecuted harvests,
  or unverified issues from the target list.
- **Sound**: Validated improvements with zero unintended side effects.

If regressions are found, return to the code and fix the root cause before completing the wrapup.
