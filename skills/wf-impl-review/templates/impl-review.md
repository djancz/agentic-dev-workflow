# TASK-<N> — Implementation review

- Latest verdict: <Approved | Approved with comments | Changes requested>
- Round: <n>
- Must-fix open: <count of open Blocker + Major>
- Reviewed: commit <sha>

## Round <n> — <YYYY-MM-DD HH:MM UTC>

- Verdict: <Approved | Approved with comments | Changes requested>
- Must-fix open: <n>
- Reviewed: commit <sha> (diff: `<base>...<sha>` in round 1, `<previous-sha>..<sha>` later)
- Reviewer: <agent / model / effort> (fresh context: <subagent | headless | new session>)

### Method

<What was read (diff size, files read in context), what was run.>

| Check | Result | Evidence |
|---|---|---|
| | PASS / FAIL / NOT RUN (<reason>) | <summary line of the real output> |

### Previous findings

<!-- Round 2+ only. One row per must-fix ID (Blocker/Major) that was open. -->

| ID | Status | Note |
|---|---|---|

### Findings

<!-- Severity-ordered, format per review rubric. -->

### [I<n>-1] <title>
- Severity: <Blocker | Major | Minor | Nit>
- Location: `path:line`
- Evidence: <code quote or command output>
- Why it matters: <consequence>
- Fix direction: <direction>

### Plan compliance

<Only mismatches: plan steps not done and not listed as deviations, or diff content the plan did not ask
for. "Matches the plan" otherwise.>

### Tests

<Do they fail when the behaviour breaks — which one did you check and how? Is anything mocked so heavily
that the test proves nothing? Are the acceptance criteria covered?>

### Security

<Always present. What was checked (checklist items that apply, pitfalls for this stack) and the result.>

### Late observations

<!-- Round 2+ only. Omit if none. -->

### Not checked

<Deliberate gaps in this review.>
