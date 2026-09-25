---
name: wf-plan
description: >-
  Write the implementation plan for a wf task (TASK-N): approach, concrete steps with files, test
  strategy, security section with sensitivity, scope, risks and open decisions — grounded in the real
  code. Use after a task exists and before any code is written. Triggers: "wf plan", "plan TASK-",
  "create plan", "vytvoř plán", "napiš plán implementace", "naplánuj".
---

# wf-plan — write the plan

Role: **author**. Paths are relative to this skill's directory. Read:
- `references/workflow.md`: sections Resolving the task and Artifact headers
- `references/conventions.md`
- `references/project.md` and `references/engineering.md`
- `references/security.md`: sections Sensitivity and Plan
- `templates/plan.md`
- the ticket `TASK-<N>.md` and the `input/` files it lists
- the `wf` block of the project's `AGENTS.md`

## Preconditions

- The ticket exists and has status `Open` or `In progress`.
- There is no plan yet. If a `Draft` plan exists, use `wf plan fix`. If an `Approved` plan exists, ask
  the human whether they want to replan (see Deviations in `references/workflow.md`).

## Do

0. **Human input.** If the human's message adds facts or instructions for this task, first add them to
   the ticket, quoted: a requirement under *Constraints*, context under *Notes*. Reviewers and later
   sessions see only what is on disk.
1. **Ground the plan in the code.** Find what the change touches: the modules involved, their callers, the
   existing tests, and the conventions in use. Look for existing utilities to reuse before planning new
   ones. Keep the search targeted. If your agent has a search subagent, use it for broad sweeps so the raw
   file dumps stay out of your context.
2. **Size the task.**
   - `S`: a few files, one concern. Keep the plan short.
   - `M`: several files, one feature.
   - `L`: several subsystems, or more than about 8 steps, still one concern. Write the full plan and
     group the steps into commits by concern.
   - **Split only by Task scope.** If the ticket bundles unrelated changes or is a complete rework
     (`references/conventions.md` → Task scope), do not write the full plan. Write only *Goal*, *Context*
     and, under *Open decisions*, a proposed split. Then stop and ask the human; `wf task split` performs
     it. Size alone never leads to a split.
3. **Write** `TASK-<N>-plan.md` from the template:
   - **Bug fix: reproduce first.** For `Type: fix`, reproduce the bug and find its cause before the
     approach; fill *Reproduction and cause*. If it does not reproduce, say so and ask.
   - **Decide; do not list options.** Where there is a real choice, pick one, name the rejected
     alternative in one line, and say why.
   - **Be concrete.** Name files, functions and data structures. "Add validation" is not a step.
   - **Test strategy is the contract.** Every new behaviour and every acceptance criterion of the ticket
     gets a row: the level (unit, integration or e2e), the test, and a checkable acceptance. Plan an
     integration test wherever a boundary is crossed (DB, HTTP, filesystem, subprocess). For a bug fix, the
     first row is the regression test that fails before the fix. For behaviour a user sees in the
     browser, plan an e2e row when the `wf` block has `test-e2e`; otherwise say under *Not covered
     automatically* how it will be checked (a browser check by the agent, or by hand).
     For a feature or behavioral bug fix, mark `Tests: required`; `wf test` authors them from the approved contract in a
     fresh context before implementation. For a non-feature change, mark `Tests: N/A` with a reason
     only if there is no new behavior; existing checks still run.
   - **Wave.** Use the wave recorded in the ticket (`wf task` reserves one for a standalone change).
     Record dependencies and shared-file risks before recommending parallel work.
   - **Planned checks.** If the `wf` block marks checks `planned` and this task creates the toolchain
     they need (typically the first task of a new project), plan setting them up: the tool configuration,
     the command in the block's row, and a step that runs it. Naming the changed rows in *Steps* lets
     `wf impl` run the new commands (`references/conventions.md` → Checks).
   - **Security.** Set `Sensitivity` using the baseline, and when in doubt choose `high`. Answer the
     questions that apply.
   - **Commits.** Group the steps into commits by concern, in English and in Conventional Commits form
     even if the project's conventions say otherwise (`references/conventions.md` → Git).
   - **Out of scope** binds the whole task. Adjacent ideas go under *Follow-ups*.
   - **Open decisions** are only for choices that are genuinely the human's, each with a recommendation.
     If one question would unblock a design choice right now, ask it now instead.
   - **Length follows size.** Aim for roughly 40–70 lines for `S`, 80–150 for `M` and up to about 250 for
     `L`. Write the decisions and the facts a reviewer could disagree with, not explanations of how
     libraries behave. Do not pad, and do not plan for hypothetical future requirements.
4. **Header.** Set `Status: Draft` and `Revision: 1`. In the ticket, set `Status: In progress`.

Do not write code, and do not commit.

## Report

Report the plan path, the size and sensitivity, one line on the approach, and the open decisions. Then:
`Next: wf plan loop` (automatic review ⇄ fix, recommended) or `wf plan rev` (one round, ideally in a new
session). End with `— wf-plan · <timestamp>`.
