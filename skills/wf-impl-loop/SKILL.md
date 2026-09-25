---
name: wf-impl-loop
description: >-
  Drive a wf task's implementation review ⇄ fix loop automatically, running every review (plus the
  security review for Sensitivity: high) and every fix as a separate agent in a fresh context, until the
  reviews approve, the round cap is reached or the loop stagnates; then mark the task ready to integrate.
  Supports a cross-model reviewer (claude, codex, gemini, opencode). Use after wf impl. Triggers: "wf impl loop",
  "impl loop", "smyčka review implementace", "dotoč review", "spusť review smyčku" (for the code).
---

# wf-impl-loop — implementation review ⇄ fix until approved or escalated

Paths are relative to this skill's directory. Read `references/loop.md` and follow it for the
implementation. Also read `references/workflow.md` (sections Next step, Human gates and Resolving the
task) and `references/review-rubric.md` (section Stop conditions).

## Preconditions

The plan has `Status: Approved`, the summary has `Status: Review requested`, `Checkpoint` equals `HEAD`,
and `git status --porcelain` is empty.

## Stages

| Loop step | Command | Skill | Run-file stage | Headless mode |
|---|---|---|---|---|
| REVIEW | `wf impl rev` | `wf-impl-review` | `impl-rev` | `rw --expect-clean` |
| REVIEW (security) | `wf sec` | `wf-security-review` | `sec` | `rw --expect-clean` |
| FIX | `wf impl fix` | `wf-impl-fix` | `impl-fix` | `rw` |

- **Security review.** In round 1 with `Sensitivity: high`, run `wf sec` after `wf impl rev`. In later
  rounds, run it again only while its verdict is `Changes requested`. It uses the `sec` role
  (`references/loop.md` → Arguments). Step 3 of the loop needs every
  verdict that exists to be `Approved*`.
- Validate a delegated review with `--kind impl` or `--kind security`, and
  `--reviewed "commit <Checkpoint sha>"`.
- **After a fix,** check that `HEAD` moved and the summary's `Checkpoint` equals it. If the fixer changed
  files but could not commit (some sandboxes protect `.git`), stage the paths from `git status --porcelain`
  by name (never `development/`), commit `wip(TASK-<N>): fix round <n>` yourself, and set `Checkpoint`.

## Gate

When every verdict is `Approved*` for the current `Checkpoint`, set the task and summary to
`Ready to integrate`. Report `Next: wf wave integrate WAVE-N`. The combined wave review presents Gate 2.

End with `— wf-impl-loop · <timestamp>`.
