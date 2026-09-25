---
name: wf-wave
description: >-
  Start, integrate, review, and approve a wave of related tasks for one PR, and close it after the PR
  is merged. Use after roadmap task breakdown, when reviewed task branches are ready, or after a merge.
  Triggers: "wf wave", "integrate wave", "review the wave", "wf wave done", "the PR is merged".
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
record the base commit. Fill `development/waves/WAVE-<N>.md` from the template; an empty file is a
reservation from `wf task`. If a branch or a filled record already exists, inspect and resume it rather
than creating a duplicate. Do not pull or overwrite
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

## Done (`wf wave done [WAVE-N]`)

Without an ID, use the only wave in `PR opened`; if there are several, ask. Run
`git fetch origin <base>`. Confirm the PR is merged: its state from an authenticated forge CLI, or
`git merge-base --is-ancestor <wave HEAD> origin/<base>`. A squash-merged PR is not an ancestor; without
a forge CLI, ask the human to confirm the merge explicitly. Otherwise stop: nothing is closed early.

Set the wave `Merged` with the merge commit and date, set its tasks `Done`, and fill their rows in the
roadmap. Then list the task worktrees and the local task and wave branches, and ask once whether to
remove them. On a yes, use `git worktree remove` (never `--force`) and `git branch -d`; report a dirty
worktree instead of removing it. After a squash merge `-d` refuses. Use `git branch -D` only for a listed
branch whose `HEAD` equals the recorded commit that went into the merge: a task branch its integrated
`Checkpoint`, the wave branch its recorded `Pushed` commit. Keep and report any branch with other
commits. Never delete remote branches or private records.
If this closed the milestone's last wave, suggest `wf milestone done`; otherwise `wf status`.
