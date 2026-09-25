---
name: wf-status
description: >-
  Show read-only status and next commands for the current project, wave, and tasks. To run the next
  stages, use wf next. Triggers: "wf status", "task status", "where are we".
---

# wf-status

Read `references/project.md` and `references/workflow.md`. Read artifact headers first; read a body only
to clarify an ambiguous next step.

## Status (`wf status [ID]`)

This skill is strictly read-only: do not fetch, switch branches, or write files.

Show PRD, spec, and roadmap revision/status when they exist. Show the current milestone and each wave's
status, branch, task counts, latest integration review verdict, and next command. For each active task,
show ID, concern, wave, plan status, test report status, implementation checkpoint, latest review verdict,
and the next action. If no work exists, suggest `wf prd` for a new project or `wf task` for an existing
one. If multiple tasks could be resumed, list them rather than choosing arbitrarily.

For `wf status TASK-N` or `wf status WAVE-N`, show the relevant artifact paths and Git branch/worktree
state. Distinguish observed facts from inferred next action. A changed checkpoint invalidates review
approval. A task approved for integration still waits for the wave's full checks, review, and Gate 2.
End with `Nothing was changed`.
