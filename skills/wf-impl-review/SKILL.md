---
name: wf-impl-review
description: >-
  Independent review of a wf task's implementation: reads the whole diff against the approved plan,
  re-runs the project checks itself, judges correctness, independent test quality, security and
  maintainability, and appends one review round with a verdict. Round 2+ is a delta review of changes
  since the last reviewed commit. Use after wf impl or wf impl fix, in a fresh context.
  Triggers: "wf impl rev", "review implementation", "zkontroluj implementaci", "code review TASK-".
---

# wf-impl-review — attack the implementation

Role: **reviewer**. Use only the artifacts and the repository. Paths are relative to this skill's
directory. Read:
- `references/review-rubric.md` (all of it)
- `references/conventions.md`: the section Checks
- `references/engineering.md`: structure, architecture, function clarity, logging, and test behavior
- `references/security.md`: sections Review checklist, Language pitfalls for this stack, Scanners and
  Severity mapping
- `templates/impl-review.md`
- `references/workflow.md`: the section Human gates (for the report)
- the plan, the summary, the ticket's acceptance criteria, the trusted `wf` block defined by
  `references/conventions.md`, and the review file (its **header and latest round only**)

## Independence preflight

If this context implemented or fixed the current checkpoint, do not review it and do not write a verdict.
Delegate to a fresh subagent or headless session; if neither is available, tell the human to run
`wf impl rev` in a new session.

## Preflight

- The summary must have `Status: Review requested`, and `Checkpoint` must equal `git rev-parse HEAD`. If
  it does not, stop and report, because you would be reviewing something other than the summary
  describes.
- The round is the header's `Round + 1`. If `Reviewed` already names this checkpoint, stop.
- Note the output of `git status --porcelain`. You must leave the tree exactly as you found it.

## Read the diff

- Round 1: `git diff --stat <base>...HEAD`, then the whole `git diff <base>...HEAD`. Read the changed
  files in context wherever the diff alone is not enough.
- Round 2 and later: `git diff <previous Reviewed sha>..HEAD`, plus the summary's latest *Fix rounds*
  table.

## Run the checks yourself

The summary claiming that tests pass is not evidence.
- Round 1: run every row of the `wf` block.
- Later rounds: run the tests and the checks the delta can affect.

Put the real summary lines in the Method table. A failure caused by the change is must-fix, but classify
it by the rubric: a broken build, test failure or High/Critical security result is normally a Blocker;
format, lint and similar quality failures are normally Major. A proven pre-existing failure is reported
as a gap, not blamed on the change.

## Round 1: what to check

- **Plan compliance.** Every step is done or listed as a deviation. Diff content the plan did not ask for
  is scope creep and a Major finding.
- **Correctness.** Trace the main path, then the error paths, empty input, concurrency, retries, resource
  cleanup, and anything irreversible.
- **Tests.** Read them; do not just count them. Do they cover the plan's table and the acceptance
  criteria? Do they fail when the behaviour breaks? Break the central behaviour once yourself, watch the
  test fail, and restore the code exactly (not when the plan says the change adds no behaviour, as a
  maintenance plan does). Is anything mocked so heavily that the test proves nothing?
  For a criterion covered only by the author's browser check, repeat the check if you have a browser
  tool; otherwise say in the report that it rests on the author's record in the summary.
- **Security.** This section is always written. Go through the checklist items that apply and the pitfalls
  for this stack, and look at the scanner results. If the plan says `Sensitivity: high` and there is no
  security review of this checkpoint yet, say so in the report.
- **Maintainability and docs.** Does the code follow the repository's conventions? Is there duplication
  of existing utilities, or misleading names? Is changed public behaviour documented?

## Round 2 and later: delta review

Follow the rubric: first the previous findings, then the delta. New must-fix findings are allowed only as
Blockers, or when the delta introduced them.

## Write

- Append the round to `TASK-<N>-impl-review.md` and update the header. `Reviewed: commit <sha>`.
- Do not modify any code.
- Confirm that `git status --porcelain` matches the preflight exactly.

**Delegated output mode.** If your prompt contains `OUTPUT MODE (delegated, read-only)`, write no files.
Your final message must be exactly the new round section, starting at `## Round <n>`.

## Report

Skip this section in delegated output mode. Report the verdict, the must-fix count, and one line per
finding. `Next`:
- `Changes requested`: `wf impl fix` (or `wf impl loop`).
- `Approved*`, `Sensitivity: high`, and no security review whose latest verdict is `Approved*`: `wf sec`,
  in a new session.
- Otherwise `Approved*`: mark the task ready to integrate after any required security review, then
  report `Next: wf wave integrate WAVE-N`. The wave owns Gate 2.

End with `— wf-impl-review · <timestamp>`.
