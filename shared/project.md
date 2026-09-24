# Project and wave workflow

This file governs project planning and wave execution. The task review rules remain in `workflow.md`,
`loop.md`, `review-rubric.md`, and `security.md`. Paths are relative to the installed skill.

## Routes

- New project or major initiative: `wf prd` → human approval → `wf spec` → human approval →
  `wf roadmap` → human approval → `wf init` → `wf milestone` → task plans.
- Small existing-project feature or bug: `wf init` if needed → `wf task` → task plan.
- A task plan has its own independent review and Gate 1 before any implementation.
- In a feature or behavioral bug task, `wf test` authors tests in a fresh context from the approved spec, public contract,
  and acceptance criteria. It must not read implementation code or the author's conversation. The test
  commit may fail until implementation; this is expected and recorded.
- `wf impl` writes the implementation in another fresh context. `wf impl loop` independently reviews
  and fixes the task, but approval means **ready to integrate**, not permission to push.
- `wf wave integrate` combines reviewed tasks, runs the full trusted checks, and obtains an independent
  integration review. Gate 2 approves that combined diff. `wf finalize` then makes the wave PR.
- A one-task change is a wave of one. A task can run while another independent task runs in its own
  worktree; do not start a task whose dependency is unresolved.

## Private state

`install.py` links `development/` in each worktree to `wf-state/` in `git rev-parse --git-common-dir`.
The link is ignored locally. Never add the state directory or its contents to a commit. The state is
shared across worktrees of this clone, but not across other clones. PR text must include enough context
for a teammate to understand the delivered behavior, tests, and accepted findings.

Project records: `development/project/prd.md`, `spec.md`, and `roadmap.md`.
Wave records: `development/waves/WAVE-<N>.md` and `WAVE-<N>-review.md`.
Task records keep the `development/tasks/TASK-<N>-<slug>/` layout defined in `workflow.md`.
Records use a `Status`, `Revision`, and explicit `Approved` line; a revised document loses approval.

## Waves

The roadmap assigns every task to a wave. A wave lists task IDs, dependencies, branch, base commit,
acceptance criteria, and `Status: Planned | In progress | Review requested | Approved | PR opened`.
Parallel tasks must not share mutable files, database tables, configuration, migrations, or unstated API
contracts. If the boundary is uncertain, schedule them sequentially.

`wf wave start WAVE-N` creates `wave/WAVE-N-<slug>` from the configured base branch, records its base
commit, and leaves the branch checked out. `wf new-worktree TASK-N` starts a task branch from that wave
commit and creates a sibling worktree; it installs the skills and private state link there. If an
existing branch or worktree has the same task ID, report it and stop.

`wf wave integrate WAVE-N` requires every task plan approved, task review approved for its current
checkpoint, and all task branches clean. Integrate one task at a time in dependency order using
`git merge --squash <task-branch>`. Commit related code and tests together, usually as one Conventional
Commit per task; use separate commits only for independently reversible concerns named in the approved
plan. Save `git write-tree` immediately after the squash merge and compare it with `HEAD^{tree}` after
the final task commit; both must match. If a
conflict occurs, resolve it, rerun relevant checks, and document the resolution. Never silently drop
either side. Run all project checks from the trusted base configuration on the combined wave tree.
Review the full wave diff in a fresh context, including interactions across tasks, security, and
acceptance criteria. Fix findings with bounded review rounds. Then show Gate 2 with the combined diff,
checks, open findings, and proposed commits. Only the human can approve it.

## Agent selection

The managed `AGENTS.md` block holds defaults: `Test agent`, `Reviewer`, `Security reviewer`, `Fix agent`,
`Max review rounds`, and model and effort defaults for each role. Per-run arguments override defaults.
Agent values: `self` (a fresh native subagent or headless run of the current CLI), `claude`, `codex`,
`gemini`, `opencode`. `model=default` and `effort=default` pass no setting. `models=auto` may select a
supported model and effort from available CLIs, but must record what actually ran. A requested setting
that the selected CLI cannot apply stops the stage with an explanation; it is never claimed as applied.

Review loops accept `reviewer=`, `model=`, `effort=`, `fix-agent=`, `fix-model=`, `fix-effort=`, and
`rounds=`. The implementation loop also accepts `security-reviewer=`, `security-model=`, and
`security-effort=`; otherwise it uses the project's security role defaults. `wf test` and standalone
`wf sec` accept `agent=`, `model=`, and `effort=` for their own roles. The default round cap is 3.
Every run has a finite, positive cap and a timeout; a human may
explicitly request another bounded run after escalation. No stage extends its own cap.

## Human gates

The human approves the PRD, spec, roadmap, each task plan, the integrated wave, and deployment when
applicable. Present the document or diff, risks, checks, and exact next action before recording approval.
Feedback creates a new revision and returns to review. Explicit acceptance of unresolved findings
lists their IDs and severities in the record and PR. No approval is inferred from silence or a prior gate.
Commit and local worktree actions follow the task's authorization. Push and PR creation require explicit
approval after showing the branch, commits, title, and body.
