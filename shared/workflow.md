# Workflow

`project.md` defines the project, wave, shared state, and final approval flow. This file defines a
single task's artifacts and gates. The agent must follow the user's actual authorization; a gate is a
review point, never an excuse to discard an explicit decision already given in the session.

## Layout

All working records are under gitignored `development/`, linked to the common Git directory by
`install.py`. A task has `development/tasks/TASK-<N>-<slug>/` with `TASK-<N>.md`, a plan, plan review,
test report for a feature, implementation summary, implementation review, and security review when
needed. Headless run prompts and output live under its `runs/`. Task and wave IDs are reserved
with `scripts/new-id.py`, never chosen by hand. The agent records branch, wave, and checkpoint commits in the task header.

## Resolving the task

An explicit task ID wins. Otherwise use the current branch's task ID, then the sole task awaiting this
stage. If several match, ask which one. `wf status` reads records and reports the next valid command.
A one-off small change in an existing project gets a wave of one; `wf wave start` creates it after
the plan is approved.

## Artifact headers

- Ticket: `Status: Open | In progress | Ready to integrate | Done | Dropped`, `Wave`, `Branch`, `Commit`.
- Plan: `Status: Draft | Approved`, `Revision`, `Approved`, `Size`, `Sensitivity`.
- Review: `Latest verdict: Approved | Approved with comments | Changes requested`, `Round`, `Must-fix
  open`, `Reviewed`. A wave review uses only `Approved | Changes requested`.
- Summary: `Status: In progress | Review requested | Ready to integrate`, `Branch`, `Base`, `Checkpoint`.
- Every review names the exact plan revision or commit it covers. A changed target invalidates its
  approval. A test report names the spec/plan revision and the agent that authored tests.

## Next step

`wf task` → `wf plan` → `wf plan loop` → human Gate 1 → `wf wave start` (if needed) →
`wf new-worktree` → `wf test` for features → `wf impl` → `wf impl loop` →
`wf wave integrate` → human Gate 2 → `wf finalize` → PR merge → `wf wave done`.
For a non-feature change, the plan may say `Tests: N/A` with a reason, so `wf test` is skipped.
`wf maintenance` and `wf deploy` retain their own explicit gates.

**Ready is per task; integration is per wave.** `Ready to integrate` describes one task, but
`wf wave integrate` needs every task of the wave. When a stage leaves a task ready, read `Tasks:` in the
wave record and each ticket's `Status` before naming a next command. If every task that is not `Dropped`
is `Ready to integrate` or `Done`, report `Next: wf wave integrate WAVE-N`. Otherwise report this task as
ready, list each task that is not, with its status and the command that moves it on (`wf status` resolves
it), and do not name `wf wave integrate`.

## Human gates

Gate 1 shows the task goal, scope, acceptance tests, security level, approach, review verdict, and
open decisions. The human approves the exact plan revision before implementation; record it in the plan
as `Status: Approved` and `Approved: revision <n> (<timestamp>) — "<the human's words>"`. Feedback makes
a new revision and sends it through review again. A plan review verdict never substitutes for human
approval.

Gate 2 belongs to the integrated wave, not an individual task. It shows the combined diff summary,
commit list, full checks table, integration review verdict, security findings, deviations, and PR draft.
Only explicit approval of this wave and current commit permits `wf finalize`. Push and PR creation
require a separate explicit yes after the final text is shown. A task implementation review marked
Approved means ready to integrate into the wave. It does not authorize a push or PR.

**Accepting open findings.** A human may explicitly accept named unresolved findings. Record IDs,
severities, rationale, and their words in the plan or wave record; include them in the PR. Do not
silently downgrade High/Critical security findings or call an unrun check a pass. Never infer approval
from silence, an earlier gate, or a generic request to continue.

**Autopilot.** `wf auto` is the one exception: the human authorizes Gate 1 and Gate 2 up front for one
task in a wave of one, and the autopilot passes a gate only when `scripts/auto-gate.py` finds it
eligible (`autopilot.md`). It records such an approval as the autopilot's, never accepts findings, and
stops at the ordinary gate whenever the policy refuses. Push and PR creation keep their own question.

## Deviations during implementation

Record small differences that preserve the approved behavior. If an interface, acceptance criterion,
security boundary, dependency, or scope changes, stop that task, revise and review its plan, and obtain
Gate 1 approval again. Other independent tasks may continue.

## Project level

`project.md` defines the PRD, spec, roadmap, waves, test authoring, and shared worktree state. A new
project or major initiative uses that full route. A small existing-project task starts at `wf task`.

## Independence

A review must run in a fresh context, with access to files and the target diff but not the author's
reasoning. The test author also runs in a fresh context and sees the contract before implementation.
A stage unable to obtain the required independent context reports that fact and stops. A missing,
failed, or invalid delegated review is not an approval.
