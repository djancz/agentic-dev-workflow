# TASK-<N> — Security review

<!-- For an audit outside a task, replace the title with: # Security audit — <scope> -->

- Latest verdict: <Approved | Approved with comments | Changes requested>
- Round: <n>
- Must-fix open: <count of open Blocker + Major>
- Reviewed: commit <sha>

## Round <n> — <YYYY-MM-DD HH:MM UTC>

- Verdict: <Approved | Approved with comments | Changes requested>
- Must-fix open: <n>
- Reviewed: commit <sha> (scope: <diff range or paths>)
- Reviewer: <agent / model / effort> (context: <subagent | headless | new session | audit requester>)

### Method

<What was read (diff size, files read in context) and what was run.>

### Threat model

<Round 1 only, 5–10 lines. Assets; entry points; trust boundaries; who the attacker is; worst misuse.>

### Scanners

| Check | Result | Evidence |
|---|---|---|
| secrets | | |
| deps-audit | | |
| sast | | |

<Triage of every High or Critical scanner result: a true positive becomes a finding; a false positive
needs a one-line reason.>

### Previous findings

<!-- Round 2+ only. One row per must-fix ID (Blocker/Major) that was open. -->

| ID | Status | Note |
|---|---|---|

### Findings

### [S<n>-1] <title>
- Severity: <Blocker | Major | Minor | Nit> (security: <Critical | High | Medium | Low | Info>, <CWE-n>)
- Location: `path:line`
- Evidence: <code quote, data flow from source to sink, or scanner output>
- Why it matters: <what an attacker gains>
- Fix direction: <direction>

### Dependencies

<Each new or upgraded dependency: needed? maintained? pinned? install scripts? name correct? Omit if none.>

### Not checked

<Deliberate gaps.>
