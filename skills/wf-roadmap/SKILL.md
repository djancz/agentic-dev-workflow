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
acceptance criteria, dependencies, and a size estimate. Tasks in one wave all start from its base
commit: group them only when none needs another's code and they share no mutable files. A dependent
task goes to a later wave. If uncertain, use consecutive waves.

Keep one wave PR small enough to review. Name contracts that must be frozen before parallel tasks start.
Do not introduce stack choices that contradict the approved spec. On revision, increment `Revision`,
return to Draft, preserve already started tasks, and describe the change. Show milestones, waves,
dependencies, risks, review result, and proposed first task to the human. Before that gate, run the
review loop in `references/project.md` → Spec and roadmap review (`references/loop.md` for running a
fresh stage). Record explicit approval, then
`Next: wf init` for a new project or `wf milestone M<N>` for an existing one.

## Review (`wf roadmap rev`, run by the review loop)

Work only from the PRD, spec, roadmap, and repository, following `references/review-rubric.md` with
`D<round>-<n>` finding IDs. Round 1 checks that no task in a wave needs another task of the same wave or
shares its mutable files, that dependencies point only to earlier waves, that every milestone leaves a
working state with checkable exit criteria, and that each task is one concern. Later rounds are delta
reviews of the latest revision. Do not edit the roadmap. In delegated output mode, write nothing and
return only the round.
