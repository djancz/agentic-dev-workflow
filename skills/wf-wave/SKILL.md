---
name: wf-wave
description: >-
  Start, integrate, review, and approve a wave of related tasks for one PR. Use after roadmap task breakdown or when reviewed task branches are ready. Triggers: "wf wave", "integrate wave", "review the wave".
---

# wf-wave

Read `references/project.md`, `references/conventions.md`, `references/engineering.md`,
`references/review-rubric.md`, and
`templates/wave.md`. Resolve `WAVE-<N>` from the argument or the current branch; ask only if ambiguous.
Read `references/models.md` when model selection is not `default`.

## Start

Require an approved roadmap or an approved standalone task plan. Read the wave's task list,
dependencies, contracts, and parallel safety. If a task depends on another task of this wave, stop
and ask for a roadmap revision. Every earlier wave it depends on must be merged into the base branch;
otherwise stop and name it. Create the wave branch from the configured base branch;
record the base commit. Create `development/waves/WAVE-<N>.md` from the template. If a branch or record
already exists, inspect and resume it rather than creating a duplicate. Do not pull or overwrite
existing branches. Next: `wf new-worktree TASK-<N>` for each ready task.

## Integrate

Follow `references/project.md` → Waves. Ensure every task has tests authored in a fresh context,
implementation checks, and an independent approved task review. Squash-merge task branches one at a
time into the wave branch, grouping each task's related code and tests. Record task commit IDs and any
conflict resolution. Run the full trusted check table; report PASS, FAIL, NOT RUN, or N/A accurately.
If checks fail, fix the cause and repeat the affected checks before review. Sync with the base
(`references/wave-review.md` → Base sync) before the first review.

Obtain a fresh independent review of the combined wave diff against the PRD/spec/task criteria. Include
cross-task interactions and security. Follow `references/wave-review.md` for prompting, validation,
timeouts, and stop conditions. The bounded review settings are:
default 3 rounds, configurable via `reviewer=`, `model=`, `effort=`, `fix-agent=`, `fix-model=`,
`fix-effort=`, `rounds=`, and `models=`. Write `WAVE-<N>-review.md`; never self-approve a failed or
missing review. When approved, show Gate 2: combined behavior, commits, checks, gaps, and open findings.
Record only explicit human approval. Next: `wf finalize WAVE-<N>`.

## Fix (`wf wave fix WAVE-N`, run by the review loop)

Work in a fresh context on the wave branch. Read the header and latest round of `WAVE-<N>-review.md` and
`references/review-rubric.md` → Fix stage. Answer every open must-fix ID, change only what the findings
need, and follow `references/wave-review.md` → Fix for commits, checks and the resolution table. Never
push. Report the counts of fixed, disputed, deferred and needs-human findings.
