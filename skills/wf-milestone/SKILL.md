---
name: wf-milestone
description: >-
  Create task tickets for an approved roadmap milestone or verify and close a completed milestone. Triggers: "wf milestone", "wf milestone done", "start milestone", "close milestone".
---

# wf-milestone

Read `references/project.md`, `references/workflow.md`, `references/conventions.md`, and the approved
`development/project/roadmap.md`. Resolve the requested milestone ID or choose the first unfinished
milestone. If no roadmap exists, suggest `wf roadmap`.

## Start

Read only this milestone's rows in the roadmap. For each row without a task ID, propose one task ticket
per concern with its acceptance criteria, wave, dependencies, and frozen interface references. If the
rows bundle unrelated changes, ask for a roadmap revision. Show the proposed tasks and ask the human to
approve creating them. On approval, use `wf task` to create tickets; copy the chosen wave and dependency
IDs into their headers and fill the roadmap's Task column. Set the milestone to `In progress`. Plan
ready tasks with `wf plan TASK-N`; independent tasks may then use separate worktrees.

## Done

Check that each task's wave PR is merged into the base branch and each milestone exit criterion is met
with evidence observed now. Mark a criterion `NOT VERIFIED` if it cannot be checked; do not infer it
from a task status. Record the differences learned during implementation. Show the status and ask the
human to close the milestone. On explicit approval, mark it `Done` with the date and move `Current
milestone` to the next one. Suggest `wf roadmap` if learnings change future milestones.
