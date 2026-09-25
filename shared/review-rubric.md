# Review rubric

This rubric applies to plan, implementation and security reviews, and to the fix stages that answer them.
Its purpose is to find what is wrong, and then to **converge**.

## Independence

- Review in a fresh context, using only the artifacts on disk and the repository. Ignore any author
  reasoning you happen to see.
- The *Method* section says who reviewed (agent, model and effort), whether the context was fresh, which
  files were opened and which commands were run. A round with no findings is valid only if *Method* shows
  that real checking was done.

## Severity

| Severity | Meaning | Must fix |
|---|---|---|
| **Blocker** | Wrong, unsafe or unworkable: a security hole, data loss, a broken build, a plan that cannot be implemented as written | yes |
| **Major** | Works but is materially wrong: the core behaviour is untested, a failure mode is unhandled, scope creep, a significant design flaw, misleading docs | yes |
| **Minor** | Real but small: an edge-case test is missing, naming is awkward, a message is thin | no |
| **Nit** | Taste | no |

Calibration:
- Inflating severity is a defect in the review.
- Every Major needs a concrete consequence.
- Report at most 5 Nits; do not pad.
- For security findings, use the mapping in `security.md`.

## Finding format

IDs are `<P|I|S|D|H><round>-<n>`: P for plan, I for implementation, S for security, D for the spec or
roadmap, H for human. For
example, `P1-2` is plan review round 1, finding 2. An ID never changes.

```markdown
### [I1-2] Token compared with == (timing leak)
- Severity: Major
- Location: `src/auth.py:88`
- Evidence: `if token == expected:` — the comparison of secrets is not constant-time
- Why it matters: an attacker can recover the token byte by byte over the network
- Fix direction: use `hmac.compare_digest`
```

Evidence is a quote, a `path:line`, or command output; "looks wrong" is not evidence. Each finding covers
one problem. Suggest a direction, not a patch.

## Verdict

- `Changes requested`: at least one Blocker or Major is open.
- `Approved with comments`: only Minor or Nit findings are open.
- `Approved`: nothing is open.

## Rounds and convergence

**Round 1** is a full review.

**Round 2 and later** are **delta reviews**, in this order:
1. **Previous findings.** Write one line per open must-fix ID (Blocker or Major, including `H…`) with the
   result `resolved`, `not resolved`, or `accepted` (the reviewer accepts a dispute). Open Minor and Nit
   findings are not re-checked. Every `not resolved` row counts in *Must-fix open*.
2. **The delta.** Review only what changed since the last round: the plan's *Changes in revision N*, or
   `git diff <reviewed-sha>..HEAD`.
3. **New must-fix findings** are allowed only if they are Blockers, or if the delta itself introduced them.
   An older problem that round 1 missed is recorded as a **late observation** with severity Minor, unless
   it is a Blocker.

These rules exist because a loop in which every round may find new Majors in unchanged material never ends.

## Fix stage: the resolution table

The fixer answers every open must-fix ID:

| ID | Resolution | Note |
|---|---|---|
| P1-1 | fixed | Step 3 now validates before writing |
| P1-2 | disputed | The cache is per-process by design (plan § Approach); a race cannot happen |
| P1-4 | deferred | Minor; moved to Follow-ups |
| P1-5 | needs-human | Retention period is a product decision: 30 or 90 days? Recommend 30 |

- `deferred` is allowed only for Minor and Nit findings.
- `disputed` needs a reason the reviewer can check. The reviewer then either accepts the dispute or
  maintains the finding. A maintained dispute goes to the human; the two sides do not argue for another
  round.
- `needs-human` is for a decision that is not the agent's to make. State the question and your
  recommendation.

## Stop conditions

`wf plan loop` and `wf impl loop` stop and hand over to the human when:
- the verdict is `Approved` or `Approved with comments`: present Gate 1 for a plan, or mark an
  implementation ready for wave integration;
- the round cap is reached (default 3);
- the loop is stagnating: the same ID is `not resolved` in two rounds, or the must-fix count did not drop
  from one round to the next;
- a dispute is maintained, or a fix reports `needs-human`.

To escalate, list the open findings at full severity, say why each survived, and recommend one option:
accept the open findings (`workflow.md` → Human gates), one more round, change the approach, or split
the task. An honest escalation is a good
outcome. Downgrading a finding just to exit the loop is the failure this rubric exists to prevent.

## Review file structure

The review file is one per kind per task. The header shows the latest state, and rounds are appended at the
end.

```markdown
# TASK-7 — Plan review
- Latest verdict: Changes requested
- Round: 2
- Must-fix open: 1
- Reviewed: plan revision 2

## Round 1 — 2026-09-18 14:05 UTC
...
## Round 2 — 2026-09-18 14:40 UTC
...
```

Each round records its own verdict and *Method*. A fixer or reviewer in a later round reads the header and
the latest round, not the whole history.
