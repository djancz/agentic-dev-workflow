---
name: wf-impl-fix
description: >-
  Fix a wf task's implementation according to the latest implementation and/or security review (or the
  human's notes during task review): resolve every must-fix finding, re-run affected checks, make a checkpoint
  commit and record a resolution table in the implementation summary. Use after a review returned Changes
  requested. Triggers: "wf impl fix", "fix implementation", "oprav implementaci", "zapracuj review".
---

# wf-impl-fix — answer the implementation review

Role: **author**. Paths are relative to this skill's directory. Read:
- `references/review-rubric.md`: the section Fix stage
- `references/conventions.md`: sections Git, Checks and Scope
- the review files with verdict `Changes requested` (**header and latest round only**): the
  implementation review, the security review, or both
- the summary, and the plan sections that the findings point to

## Do

0. **Human change requests.** If the human asks for changes directly (at a gate, or in this message),
   first record them in the review file as a `Round <n> — human` section: verdict `Changes requested`,
   findings `H<n>-1…` quoting the human, and the header updated. Then treat them like any other finding.
1. **Answer every open must-fix finding** (`I…`, `S…`, `H…`):
   - `fixed`: fix the cause. For a bug-type finding, add or adjust a test that would have caught it.
   - `disputed`: give a reason with evidence (`path:line`, command output) that the reviewer can verify.
   - `needs-human`: state the question and your recommendation.
   - A security finding of Medium or above cannot be `deferred`.
2. **Minor and Nit findings.** Fix them if cheap; otherwise mark them `deferred` and add them to
   *Follow-ups* in the summary.
3. **Stay in scope.** Change only what the findings require. No opportunistic refactoring: it grows the
   delta the next round must review.
4. **Material deviation.** If a finding reveals that the plan itself is wrong, stop and ask the human
   (see Deviations in `references/workflow.md`).
5. **Check.** Run the tests and checks the fix affects, then the fast suite. If the fix is broad, or it
   touches security findings, run every row of the `wf` block. If the fix changes behaviour that the
   browser check covered, repeat that part as `wf impl` describes, or mark it stale in the summary.
6. **Checkpoint commit.** Stage explicit paths, then run `git commit -m "wip(TASK-<N>): fix round <n>"`.
   Hooks stay on.
7. **Update the summary.**
   - Set `Checkpoint` to the new `HEAD` and keep `Status: Review requested`.
   - Refresh the Checks table and *Deviations*.
   - Append to *Fix rounds*:
     ```markdown
     ### Fix round <n> — <timestamp> (checkpoint <short sha>)
     | ID | Resolution | Note |
     |---|---|---|
     ```

Never push.

## Report

Report the counts of fixed, disputed, deferred and needs-human findings, quote any `needs-human` question,
and give the checks outcome. Then `Next: wf impl rev` in a new session (or let `wf impl loop` continue).
End with `— wf-impl-fix · <timestamp>`.
