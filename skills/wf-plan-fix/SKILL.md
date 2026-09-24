---
name: wf-plan-fix
description: >-
  Revise a wf task's plan in place to answer the latest plan review (or the human's notes at Gate 1):
  fix every must-fix finding, dispute with reasons where the reviewer is wrong, record a resolution table
  and bump the revision. Use after a plan review returned Changes requested. Triggers: "wf plan fix",
  "fix plan", "oprav plán", "zapracuj review plánu".
---

# wf-plan-fix — answer the plan review

Role: **author**. Paths are relative to this skill's directory. Read:
- `references/review-rubric.md`: the section Fix stage
- `references/conventions.md`: the section Writing style
- the plan
- the review file (its **header and latest round only**)
- the ticket, only if a finding refers to it

## Do

0. **Human change requests.** If the human asks for changes directly (at a gate, or in this message),
   first record them in the review file as a `Round <n> — human` section: verdict `Changes requested`,
   findings `H<n>-1…` quoting the human, and the header updated. Then treat them like any other finding.
1. **Answer every open must-fix finding** (Blocker and Major), including carried-over ones marked
   `not resolved` and human findings (`H…`):
   - `fixed`: change the plan so the finding no longer applies. Fix the cause, not the wording.
   - `disputed`: the reviewer is wrong. Give a reason they can verify, pointing to a file or plan section.
   - `needs-human`: the decision is not yours to make. State the question and your recommendation.
2. **Minor and Nit findings.** Fix them if it is cheap. Otherwise mark them `deferred` and add them to
   *Follow-ups*.
3. **Stay in scope.** Change only what the findings require. A revision that rewrites unrelated sections
   forces a larger re-review.
4. **Record the revision.**
   - In the header, increment `Revision`, keep `Status: Draft`, and update `Updated`. If the plan was
     `Approved`, set it back to `Draft` and clear `Approved`, because the human must approve again.
   - Append to *Revision history*:
     ```markdown
     ### Revision <n> — <timestamp>
     Changes: <bullets naming the sections that changed>

     | ID | Resolution | Note |
     |---|---|---|
     ```

Do not write code, and do not commit.

## Report

Report the counts of fixed, disputed, deferred and needs-human findings, and quote any `needs-human`
question. Then `Next: wf plan rev` in a new session (or let `wf plan loop` continue). End with
`— wf-plan-fix · <timestamp>`.
