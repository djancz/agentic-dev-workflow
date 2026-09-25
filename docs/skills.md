# Commands and approval gates

Type these as messages to an agent that has the skills installed. Day to day you need only `wf task` or
`wf prd`, `wf next`, your gate answers, and `wf wave done` (see the README). The table lists every stage
for manual control. `wf status` is read-only and shows where a project or task stands. Give an ID when
more than one task or wave could match.

| Command | Purpose | Next review point |
|---|---|---|
| `wf prd` | Define product users, behavior, and success | Human approves PRD |
| `wf spec` | Design boundaries and public contracts; bounded fresh review | Human approves spec |
| `wf roadmap` | Plan milestones, tasks, and waves; bounded fresh review | Human approves roadmap |
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

Review loops also run internal stages you do not type: `wf wave fix` (fixes after a wave review),
`wf spec rev` and `wf roadmap rev` (fresh reviews of the spec and roadmap).

## Agent and model options

Set project defaults in the managed `AGENTS.md` block. Per-run arguments override them. Agent values
are `self`, `claude`, `codex`, `gemini`, and `opencode`. `self` means a fresh subagent of the current
host or a fresh run of its CLI. Agent CLI authentication is the user's responsibility.

| Role | Shorthand | Long form | Block keys | Applies to |
|---|---|---|---|---|
| test | `test=a,m,e` | `test-agent=`, `test-model=`, `test-effort=` | `Test agent`, `Test model`, `Test effort` | `wf test` |
| rev | `rev=a,m,e` | `rev-agent=`, `rev-model=`, `rev-effort=` | `Rev agent`, `Rev model`, `Rev effort` | Spec, roadmap, plan, implementation, wave reviews |
| sec | `sec=a,m,e` | `sec-agent=`, `sec-model=`, `sec-effort=` | `Sec agent`, `Sec model`, `Sec effort` | `wf sec`, in `wf impl loop` or standalone |
| fix | `fix=a,m,e` | `fix-agent=`, `fix-model=`, `fix-effort=` | `Fix agent`, `Fix model`, `Fix effort` | Plan, implementation, wave fixes |

In the shorthand `<role>=agent[,model[,effort]]`, an omitted or empty part is `default`: `rev=codex`,
`rev=codex,gpt-5`, `rev=codex,,high`. Model names may contain `:` or `/`
(`rev=opencode,ollama/llama3:8b,high`). Use one form per role; `rev=codex rev-effort=high` is an
error. An unset field comes from the block, then from `models=default|auto` (argument, then `Models`,
then `default`); an agent falls back to `self`. `rounds=` sets the review rounds in this run (project
`Max review rounds`, then 3). The stage prints the resolved settings before it starts.

`default` passes no model or effort flag. `auto` chooses only a supported setting and records the
actual value. Unsupported settings stop the stage. `rounds` must be 1–10; a new run requires your
explicit request. Headless stages have a timeout. Each review has at most one retry for a transient
execution failure, and stagnation or an unresolved reviewer dispute stops the loop.

Examples:

```text
wf test TASK-8 test=claude,sonnet
wf plan loop TASK-8 rev=codex,,xhigh rounds=2
wf impl loop TASK-8 rev=gemini fix=claude,sonnet
wf impl loop TASK-8 rev=codex sec=claude,opus
wf wave integrate WAVE-3 rev-agent=opencode rev-model=<provider/model> rounds=1
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
