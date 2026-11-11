---
name: wf-roadmap
description: >-
  Turn an approved PRD and architecture spec into milestones and dependency-aware task waves; revise the plan as the project evolves. Triggers: "wf roadmap", "project roadmap", "plan milestones".
---

# wf-roadmap

Read `references/project.md`, `references/engineering.md`, `references/conventions.md` (section Asking
the human), and `templates/roadmap.md`. For a new project or major initiative, require approved
`development/project/prd.md` and `spec.md`. For a small existing-project task, use `wf task` instead.

Inspect the approved requirements, contracts, existing code where relevant, and previous milestone
learnings. If `development/project/prototype.md` exists, read its header and *Learnings for the spec
and roadmap* (`references/project.md` → Prototype). Without a `Decision`, ask with options whether to
wait for `wf prototype done`; if not, record `Prototype: revision <n>, undecided`. For `adopt`, the first
wave not yet started is the one adoption task, every later wave depends on it, and a later task makes each
remaining simulation real. For `reference`, name the prototype files a task may start from. Record the
decision in the `Prototype` header line.
Write `development/project/roadmap.md`. Make each milestone a working state with checkable
exit criteria. The first milestone establishes project layout, quality checks, and the tracked architecture document
the spec names; the next delivers a
thin usable path. Keep distant milestones coarse. Split a milestone into tasks, each one concern with
acceptance criteria, dependencies, and a size estimate. Tasks in one wave all start from its base
commit: group them only when none needs another's code and they share no mutable files. A dependent
task goes to a later wave. If uncertain, use consecutive waves. In *Depends on*, name earlier waves
(`WAVE-N`), tasks (`TASK-N`) or row keys (`M1.2`, the key that starts a row's concern); the resolver holds
a wave until all of them are merged and refuses a name no row has.

Keep one wave PR small enough to review. Name contracts that must be frozen before parallel tasks start.
Do not introduce stack choices that contradict the approved spec. On revision, increment `Revision`,
return to Draft, preserve already started tasks, and describe the change. Show milestones, waves,
dependencies, risks, review result, and proposed first task to the human. Before that gate, run the
review loop in `references/project.md` → Spec and roadmap review (`references/loop.md` for running a
fresh stage). Record explicit approval, then
report the next step (`references/conventions.md` → Reporting to the human).

## Review (`wf roadmap rev`, run by the review loop)

Work only from the PRD, spec, roadmap, and repository, following `references/review-rubric.md` with
`D<round>-<n>` finding IDs. Round 1 checks that no task in a wave needs another task of the same wave or
shares its mutable files, that dependencies point only to earlier waves, that every milestone leaves a
working state with checkable exit criteria, that each task is one concern, and that an adopted
prototype's adoption task is alone in the first wave not yet started and every later wave depends on
it. Later rounds are delta reviews of the latest revision. Do not edit the roadmap. The round has the
review header fields, a `### Method` section with what was read and run, and the findings. In delegated output
mode, write nothing and return only the round.
