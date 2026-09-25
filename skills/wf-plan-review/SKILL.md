---
name: wf-plan-review
description: >-
  Independent, critical review of a wf task's implementation plan against the ticket and the real code:
  correctness, scope, test strategy, security, size. Appends one review round with a verdict and
  severity-rated findings. Round 2+ is a delta review. Use after wf plan or wf plan fix, in a fresh context.
  Triggers: "wf plan rev", "review plan", "plan review", "zkontroluj plán", "zrevidovat plán".
---

# wf-plan-review — attack the plan

Role: **reviewer**. Use only the artifacts and the repository; ignore any author reasoning you may have
seen. Paths are relative to this skill's directory. Read:
- `references/review-rubric.md` (all of it)
- `references/security.md`: sections Sensitivity and Plan
- `references/conventions.md`: the section Task scope
- `references/engineering.md`: check the proposed structure, coupling, control flow, observability,
  and testability against the project's existing patterns
- `templates/plan-review.md`
- `references/workflow.md`: the section Human gates (for the report)
- the ticket (and the `input/` files it lists), the plan, and the review file if it exists (its **header
  and latest round only**)

## Independence preflight

If this context authored or fixed the current plan, do not review it and do not write a verdict. Delegate
to a fresh subagent or headless session; if neither is available, tell the human to run `wf plan rev` in a
new session. Merely ignoring remembered reasoning is not a fresh context.

## Round

The round is the header's `Round + 1`, or 1 if there is no review file. If the header's `Reviewed` already
names the plan's current `Revision`, stop: that revision has already been reviewed.

## Round 1: full review

- **Solves the ticket?** Every acceptance criterion is covered and nothing is quietly dropped. Anything
  the ticket did not ask for is scope creep.
- **Grounded?** Open the files the plan names. Do the functions and signatures exist as the plan assumes?
  Is there existing code the plan should reuse? Check with a targeted search. Verify claims cheaply, with
  a file read or a one-line command. Do not prototype the implementation.
- **Cause, not symptom.** For a bug fix, is the reproduction real and does the approach remove the
  cause it names?
- **Sound?** Check the error paths, empty input, concurrency, retries and partial failure. Is anything
  irreversible (migrations, deletions)? Does every step leave the repository working?
- **Tests.** Every behaviour has a test at the right level, and the acceptance criteria are checkable.
  Would these tests fail if the feature were broken? Is there an integration test where a boundary is
  crossed?
- **Security.** Is `Sensitivity` right for the baseline triggers? Are the applicable questions answered
  concretely? Are new dependencies justified?
- **Size and scope.** Is the size honest? A split is wrong unless `references/conventions.md` → Task scope
  calls for it; a large diff alone is no reason. A ticket that bundles unrelated changes without a split
  proposal is a Major finding. An `L` plan groups its steps into commits by concern.
- **Open decisions.** Are they genuinely the human's, or is the plan dodging a call it should make itself?

## Round 2 and later: delta review

Follow the rubric: first the previous findings (resolved, not resolved, or accepted), then only the plan's
latest *Revision history* entry and the sections it names. New must-fix findings are allowed only if they
are Blockers or were introduced by the revision. Older misses go under *Late observations*.

## Write

- Append the round to `TASK-<N>-plan-review.md`, creating the file from the template if needed.
- Update the four header lines so they match the round. `Must-fix open` counts every open Blocker and
  Major, including ones carried over from earlier rounds.
- Do not edit the plan.

**Delegated output mode.** If your prompt contains `OUTPUT MODE (delegated, read-only)`, write nothing.
Your final message must be exactly the new round section, starting at `## Round <n>`. The caller appends it
and updates the header.

## Report

Skip this section in delegated output mode. Report the verdict, the must-fix count, and one line per
finding (`[P2-1] Major — title`). Then `Next`:
- `Changes requested`: `wf plan fix` (or `wf plan loop`).
- `Approved*`: Gate 1. Present the Gate 1 digest from `references/workflow.md` → Human gates, including
  how to answer, and stop.

End with `— wf-plan-review · <timestamp>`.
