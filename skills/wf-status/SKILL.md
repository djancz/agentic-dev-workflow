---
name: wf-status
description: >-
  Show read-only status and next commands for the current project, wave, and tasks. To run the next
  stages, use wf next. Triggers: "wf status", "task status", "where are we".
---

# wf-status

Read `references/project.md`, `references/workflow.md`, and from `references/conventions.md` the section
Reporting to the human. Read artifact headers first; read a body only to show a fact the human asked for.

## Status (`wf status [ID]`)

This skill is strictly read-only: do not fetch, switch branches, or write files.

Run `python3 scripts/next-step.py --branch "$(git branch --show-current)"
"$(git rev-parse --show-toplevel)/development"`, with `--id <ID>` when the human gave one. It only reads
the records. Its item lines are the open items with their next actions, its `Next:` line is the next
step, and a `Where:` line names the branch whose worktree that step runs in; never work any of them out
yourself. On exit 1, list the items that could continue in its order (earliest wave first) rather than
choosing one; on exit 2, report the record it names. Show its `Follow-ups:` line, when there is one, as information.

Around those lines, show PRD, spec, and roadmap revision/status when they exist. For a prototype record,
show its status, revision, branch, worktree and decision; mention `wf prototype` only as optional after
an approved spec. Show the current milestone and each wave's status, branch, task counts, and latest
integration review verdict. For each active task, show ID, concern, wave, plan status, test report
status, implementation checkpoint, and latest review verdict. Mark approvals recorded `by autopilot` and
waves with an `Autopilot:` authorization; a stopped autopilot run continues only with `wf auto TASK-N` or
`wf auto WAVE-N`.

For `wf status TASK-N` or `wf status WAVE-N`, show the relevant artifact paths and Git branch/worktree
state. Distinguish observed facts from the resolved next action. A changed checkpoint invalidates review
approval. A task approved for integration still waits for the wave's full checks, review, and Gate 2.
End with `Nothing was changed`.
