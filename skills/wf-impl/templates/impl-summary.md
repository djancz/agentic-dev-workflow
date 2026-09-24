# TASK-<N> — Implementation summary

- Status: Review requested
- Branch: task/TASK-<N>-<slug>
- Base: <wave branch>
- Checkpoint: <sha of the commit under review>
- Approved: —
- Updated: <YYYY-MM-DD HH:MM UTC>

## What was built

<Behaviour, not a list of functions. Two to five bullets.>

## Deviations from the plan

<"None", or each deviation with the reason. The reviewer reads this first.>

## Tests

| Behaviour (plan § Test strategy) | Test | Result |
|---|---|---|
| | `tests/…::test_name` | PASS |

Red test evidence: <which independent test failed before implementation, or reason and reviewer follow-up>

## Checks

<Every row of the AGENTS.md wf block, run at the Checkpoint commit.>

| Check | Result | Evidence |
|---|---|---|
| | PASS / FAIL / NOT RUN (<reason>) / N/A (<reason>) | |

## Browser check

<Criteria a user sees in the browser, checked against the running dev server. Or "Not done: <reason>"
(no such criterion, no `Dev server` line, no browser tool). Never counted in the Checks table.>

| Criterion | What was done | What was seen | Evidence |
|---|---|---|---|
| | | | `evidence/<file>.png` |

## Security notes

<What was done for plan § Security; scanner outcome; anything a security reviewer should look at.>

## Not tested / known limitations

<Honest gaps. "None" only if true.>

## Follow-ups

<Deferred Minor findings and out-of-scope ideas.>

## Fix rounds

<!-- wf impl fix appends one resolution table per round. -->
