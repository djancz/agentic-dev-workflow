---
name: wf-roadmap
description: >-
  Turn an approved PRD and architecture spec into milestones and dependency-aware task waves; revise the plan as the project evolves. Triggers: "wf roadmap", "project roadmap", "plan milestones".
---

# wf-roadmap

Read `references/project.md`, `references/engineering.md`, and `templates/roadmap.md`. For a new
project or major initiative, require approved `development/project/prd.md` and `spec.md`. For a small
existing-project task, use `wf task` instead.

Inspect the approved requirements, contracts, existing code where relevant, and previous milestone
learnings. Write `development/project/roadmap.md`. Make each milestone a working state with checkable
exit criteria. The first milestone establishes project layout and quality checks; the next delivers a
thin usable path. Keep distant milestones coarse. Split a milestone into tasks, each one concern with
acceptance criteria, dependencies, and a size estimate. Group tasks into waves only when their
dependencies and mutable files permit safe parallel execution. If uncertain, schedule sequentially.

Keep one wave PR small enough to review. Name contracts that must be frozen before parallel tasks start.
Do not introduce stack choices that contradict the approved spec. On revision, increment `Revision`,
return to Draft, preserve already started tasks, and describe the change. Show milestones, waves,
dependencies, risks, and proposed first task to the human. Record explicit approval, then
`Next: wf init` for a new project or `wf milestone M<N>` for an existing one.
