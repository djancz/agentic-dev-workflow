# Commands and approval gates

Type these as messages to an agent that has the skills installed. `wf status` is read-only and shows
where a project or task stands. Give an ID when more than one task or wave could match.

| Command | Purpose | Next review point |
|---|---|---|
| `wf prd` | Define product users, behavior, and success | Human approves PRD |
| `wf spec` | Design boundaries and public contracts | Human approves spec |
| `wf roadmap` | Plan milestones, tasks, and waves | Human approves roadmap |
| `wf init` | Detect and configure project checks | Review managed project instructions |
| `wf milestone M1` | Create milestone tasks; `done` verifies exit criteria | Task plans |
| `wf task <goal>` | Create a standalone task or revise task scope | Task plan |
| `wf plan TASK-N` | Plan behavior, tests, code, security, and commits | `wf plan loop` |
| `wf plan loop TASK-N` | Fresh plan review and bounded fixes | Gate 1: approve task plan |
| `wf plan rev TASK-N` | One independent plan review round | Plan fix or Gate 1 |
| `wf plan fix TASK-N` | Address plan findings and increment revision | Plan review |
| `wf wave start WAVE-N` | Create wave branch | Task worktrees |
| `wf new-worktree TASK-N` | Create task branch and sibling worktree | `wf test` |
| `wf test TASK-N` | Fresh spec-based test author writes tests | `wf impl` |
| `wf impl TASK-N` | Implement approved plan, run checks | `wf impl loop` |
| `wf impl loop TASK-N` | Fresh code and security review, bounded fixes | Ready to integrate |
| `wf impl rev TASK-N` | One independent implementation review round | Fix or integration |
| `wf impl fix TASK-N` | Resolve implementation findings and rerun affected checks | Implementation review |
| `wf sec TASK-N` | Independent deep security review | Fix or integration |
| `wf wave integrate WAVE-N` | Combine tasks, full checks, fresh integration review | Gate 2: approve wave |
| `wf finalize WAVE-N` | Draft and, on explicit yes, push and open one PR; later, fix PR comments and CI failures | Merge on forge |
| `wf wave done [WAVE-N]` | After the merge: mark wave and tasks done, offer local cleanup | `wf milestone done` or next task |
| `wf maintenance` | Scan dependencies and apply approved updates | Maintenance gate |
| `wf deploy` | Prepare and run an approved deployment | Deployment gate |
| `wf status [ID]` | Read status and next action | None; read-only |
| `wf next [ID]` | Run the next stages until a human gate, question, or failure | The gate it stops at |

## Agent and model options

Set project defaults in the managed `AGENTS.md` block. Per-run arguments override them. Agent values
are `self`, `claude`, `codex`, `gemini`, and `opencode`. `self` means a fresh subagent of the current
host or a fresh run of its CLI. Agent CLI authentication is the user's responsibility.

| Option | Applies to | Default |
|---|---|---|
| `agent=` | `wf test` test author | Test agent, then `self` |
| `reviewer=` | Plan, implementation, security, wave reviews | Reviewer, then `self` |
| `fix-agent=` | Review-loop fixes | Fix agent, then `self` |
| `security-reviewer=`, `security-model=`, `security-effort=` | Dedicated security stage in `wf impl loop` | Security role defaults |
| `model=`, `effort=` | Reviewer, or `wf test` author | Role's configured model/effort, then `default` |
| `fix-model=`, `fix-effort=` | Fix agent | Fix defaults, then `default` |
| `models=default|auto` | All unspecified model/effort choices in a loop | Project Models, then `default` |
| `rounds=` | Review rounds in this run | Project Max review rounds, then 3 |

`default` passes no model or effort flag. `auto` chooses only a supported setting and records the
actual value. Unsupported settings stop the stage. `rounds` must be 1–10; a new run requires your
explicit request. Headless stages have a timeout. Each review has at most one retry for a transient
execution failure, and stagnation or an unresolved reviewer dispute stops the loop.

Examples:

```text
wf test TASK-8 agent=claude model=sonnet
wf plan loop TASK-8 reviewer=codex effort=xhigh rounds=2
wf impl loop TASK-8 reviewer=gemini fix-agent=claude fix-model=sonnet
wf impl loop TASK-8 reviewer=codex security-reviewer=claude security-model=opus
wf wave integrate WAVE-3 reviewer=opencode model=<provider/model> rounds=1
```

## Answering a gate

A gate shows a digest and the exact document revision or commit being approved. Answer in your own
language with clear approval or corrections; no fixed English phrase is required. Corrections create a
new revision or reviewed commit and return to review. Explicit acceptance of open findings names the
finding IDs and is recorded in the PR. Silence is not approval.

| Gate | Examine | Approval enables |
|---|---|---|
| PRD | Users, behavior, exclusions, success | Architecture spec |
| Spec | Boundaries, contracts, security, checks | Roadmap |
| Roadmap | Milestones, dependencies, wave safety | Task creation |
| Gate 1, each task | Scope, tests, security, plan review | Test author and implementation |
| Gate 2, each wave | Combined diff, full checks, integration review | PR preparation |
| Push and PR | Exact branch, commits, title, body | Push and PR creation |
| Maintenance | Updates, advisories, scan gaps | Dependency changes |
| Deployment | Exact runbook, backups, checks | Deployment |

## Records and recovery

Plans and reviews live in `development/`, shared across worktrees in one local clone. A PRD, spec, or
roadmap revision loses its previous approval. A task review applies only to its recorded checkpoint;
a wave Gate 2 applies only to its recorded integrated commit. Run `wf status` after a pause or a failed
stage. When a loop escalates, read the unresolved findings and choose a new approach, an additional
bounded run, or explicit acceptance. A missing tool is `NOT RUN`, never a pass.
