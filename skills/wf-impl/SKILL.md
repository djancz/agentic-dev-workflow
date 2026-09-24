---
name: wf-impl
description: >-
  Implement an approved wf plan (after Human Gate 1) in its task worktree, using independently authored
  contract tests; run all project checks, make a local checkpoint commit and write
  the implementation summary. Use only when the task's plan has Status Approved. Triggers: "wf impl",
  "implement TASK-", "start implementation", "implementuj", "začni implementaci".
---

# wf-impl — build what was approved

Role: **author**. Paths are relative to this skill's directory. Read:
- `references/workflow.md`: sections Resolving the task, Human gates and Deviations during implementation
- `references/conventions.md`
- `references/security.md`: sections Review checklist and Language pitfalls for this stack
- `references/project.md` and `references/engineering.md`
- `templates/impl-summary.md`
- the approved plan, the ticket (and the `input/` files it lists), and the trusted `wf` block defined by
  `references/conventions.md`

## Preconditions

The plan must have `Status: Approved`. There are two exceptions for a `Draft` plan whose latest review
covers the current revision: its verdict is `Approved*` and the human's message explicitly approves it
("plan approved, wf impl" or "schvaluji, implementuj"); or its verdict is `Changes requested` and the
human accepts the open findings as *Accepting open findings* in `references/workflow.md` → Human gates
describes. In either case, record the approval as that section says, and continue.

Otherwise stop: Gate 1 has not been passed. Say what is missing: if the latest review approved the current
revision, only the human's approval is missing (they can reply `schvaluji, implementuj`); otherwise the
plan needs `wf plan loop`, or `wf plan rev` / `wf plan fix`, or the human's acceptance of its open
findings.

## Do

1. **Branch.**
   - `git status --porcelain` must be empty (`development/` is ignored). If it is not, ask the human.
   - Require the recorded `task/TASK-<N>-<slug>` branch created by `wf new-worktree`. Do not branch
     directly from the project base.
   - For `Tests: required`, require `TASK-<N>-test-report.md` from a separate fresh agent session and
     its test checkpoint. If absent, stop with `Next: wf test TASK-<N>`.
   - Record the `Branch` in the ticket.
2. **Work through the plan's steps in order.**
   - Implement against the contract tests and plan. If tests need correction, record why and get an
     independent test-author review; do not silently weaken acceptance.
   - For a bug fix, ensure the regression test failed before the fix.
   - After each step, run the fast checks that are relevant (format, lint, the unit tests of the touched
     area), keeping only the summary of the output.
3. **Red test evidence.** Verify the independent test report recorded a failing test for new behavior
   or the bug before implementation. Do not spend another agent run mutating code just to re-prove the
   same failure. If the test could not fail first, explain why and have the independent reviewer inspect
   its ability to catch the intended behavior.
4. **Docs.** Update what the plan lists under *Docs to update*.
5. **Deviations.** Follow the rules in `references/workflow.md`: record small deviations; for a material
   one, **stop and ask**.
6. **Checkpoint commit.** Stage the files you changed by explicit path, then run
   `git commit -m "wip(TASK-<N>): implementation"`. Hooks stay on.
7. **Full checks.** Run every row of the `wf` block and fill in the Checks table.
   - On a failure, fix it, re-run, and make another checkpoint commit.
   - A failure that is unrelated to your change is recorded only if you show that it also fails on the
     base. Otherwise fix it.
8. **Browser check** (web UI only). Do it when the plan or the ticket has a criterion that a user sees
   in the browser, the `wf` block has a `Dev server`, and you have a browser tool (for example Claude
   Code's Chrome integration or a Playwright MCP server).
   - Start: `python3 <skills-dir>/wf-init/scripts/dev-server.py start --url <url> --server '<command>'`,
     where `<skills-dir>` is `dirname "$(realpath <this skill dir>)"`.
   - Walk through each such criterion and write down what you did and what you saw. Save screenshots to
     `development/tasks/TASK-<N>-<slug>/evidence/` when the tool can.
   - Always finish with `python3 <skills-dir>/wf-init/scripts/dev-server.py stop`.
   - If a condition is missing, skip the step and write down why. A browser check never goes into the
     Checks table, and one that did not happen is never described as done.
9. **Summary.** Write `TASK-<N>-impl-summary.md` from the template:
   - `Status: Review requested`, `Checkpoint:` the output of `git rev-parse HEAD`
   - behaviour-level description, deviations, the tests table with red test evidence, the checks table,
     the browser check, security notes, and honest gaps

Never push. Never use `--no-verify`.

## Report

Report what was built in two lines, the checks outcome (counts of PASS, FAIL and NOT RUN), and the
deviations. Then `Next: wf impl loop` (recommended) or `wf impl rev` (one round, in a new session). End
with `— wf-impl · <timestamp>`.
