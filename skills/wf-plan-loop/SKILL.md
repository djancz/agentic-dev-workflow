---
name: wf-plan-loop
description: >-
  Drive a wf task's plan review ⇄ plan fix loop automatically, running every review and every fix as a
  separate agent in a fresh context, until the review approves the plan, the round cap is reached or the
  loop stagnates; then present Human Gate 1 and stop. Supports a cross-model reviewer (claude, codex,
  gemini, opencode). Use after wf plan, while the plan is a Draft. Triggers: "wf plan loop", "plan loop",
  "smyčka review plánu", "dotoč review plánu", "spusť review smyčku" (for the plan).
---

# wf-plan-loop — plan review ⇄ fix until approved or escalated

Paths are relative to this skill's directory. Read `references/loop.md` and follow it for the plan. Also
read `references/workflow.md` (sections Next step, Human gates and Resolving the task) and
`references/review-rubric.md` (section Stop conditions).

## Preconditions

The plan exists and has `Status: Draft`. If it is `Approved`, there is nothing to review: say so and give
`Next: wf wave start WAVE-N`, then `wf new-worktree TASK-N`, `wf test TASK-N` for a feature, and
`wf impl TASK-N`.

## Stages

| Loop step | Command | Skill | Run-file stage | Headless mode |
|---|---|---|---|---|
| REVIEW | `wf plan rev` | `wf-plan-review` | `plan-rev` | `ro` |
| FIX | `wf plan fix` | `wf-plan-fix` | `plan-fix` | `rw` |

Validate a delegated review with `--kind plan` and `--reviewed "plan revision <n>"`, where `<n>` is the
plan's current `Revision`.

## Gate

When the verdict is `Approved*` for the current revision, present the **Gate 1** digest as
`references/loop.md` → Gate describes. On explicit human approval, record the approved plan revision,
then `Next: wf wave start WAVE-N` if needed, followed by `wf new-worktree TASK-N` and `wf test TASK-N`
for a feature. Feedback creates a new plan revision and returns to review.

End with `— wf-plan-loop · <timestamp>`.
