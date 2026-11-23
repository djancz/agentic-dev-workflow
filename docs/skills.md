# Commands and approval gates

Type these as messages to an agent that has the skills installed. Day to day you need only `wf task` or
`wf prd`, `wf next`, your gate answers, and `wf wave done` (see the README). The table lists every stage
for manual control. `wf status` is read-only and shows where a project or task stands. Give an ID when
more than one task or wave could match.

| Command | Purpose | Next review point |
|---|---|---|
| `wf prd` | Define product users, behavior, and success | Human approves PRD |
| `wf spec` | Design boundaries and public contracts; bounded fresh review | Human approves spec |
| `wf prototype [focus]` | Optional, after the spec: build a runnable prototype on its own branch; feedback revises it | Human confirms scope, then tries it |
| `wf prototype done` | Adopt the prototype as the baseline, keep it as a reference, or discard it; later, clean it up | `wf roadmap`, or `wf prd` / `wf spec` when its learnings change them |
| `wf roadmap` | Plan milestones, tasks, and waves; bounded fresh review | Human approves roadmap |
| `wf init` | Detect and configure project checks; ask for or confirm your agent roles | Review managed project instructions |
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
| `wf finalize WAVE-N` | Draft and, on explicit yes, push and open one PR; later, fix PR comments and CI failures, or update the open PR after a new Gate 2 | Merge on forge |
| `wf wave done [WAVE-N]` | After the merge: mark wave and tasks done, offer local cleanup | `wf milestone done` or next task |
| `wf maintenance` | Scan dependencies and apply approved updates | Maintenance gate |
| `wf sec audit [profile] [paths]` | Ad hoc multi-agent security audit: coverage ledger, hunters, independent verifiers, report; never changes code | Audit gate, then the tasks you choose |
| `wf deploy` | Prepare and run an approved deployment | Deployment gate |
| `wf status [ID]` | Read status and the next action that `next-step.py` resolves | None; read-only |
| `wf next [ID]` | Run the stages `next-step.py` resolves until a human gate, question, or failure | The gate it stops at |
| `wf auto <goal>` | Autopilot: run every stage, pass Gate 1 and Gate 2 by policy, prepare the PR | Push and PR |

The next step comes from the records. `shared/scripts/next-step.py` reads the headers in `development/`
and the last round of each review, and names one action: a `wf` command, a `human:` answer (a gate, a
decision on open findings, a merge, or trying a prototype), or, with exit 1, the tasks and waves that
could continue, earliest wave first. A wave waits until every earlier wave its roadmap rows depend on is
merged; *Depends on* names waves, tasks or row keys such as `M1.2`. A `Where:` line names the branch,
and so the worktree, that the step runs in. Every stage reports the `Next:` line for the task or wave it worked on (`--id`), and `wf status`, `wf next` and
`wf auto` use it; a delegated run reports none, and `wf maintenance`, `wf sec audit` and `wf deploy` keep their own. A
review that requests changes on the current plan revision or commit waits for your decision; a stopped
loop never starts another run by itself, but your own change request goes back to the loop. Follow-ups
are listed only when no task or wave is open, and are never the next step.

`Ready to integrate` is per task. A stage names `wf wave integrate WAVE-N` as the next step only when every
task of the wave (except dropped ones) is ready; otherwise it lists the tasks that are not.

Review loops also run internal stages you do not type: `wf wave fix` (fixes after a wave review),
`wf spec rev` and `wf roadmap rev` (fresh reviews of the spec and roadmap).

## Agent and model options

Role settings are personal and never committed. `~/.config/agentic-dev-workflow/settings.md` (in
`$XDG_CONFIG_HOME` when set) applies to all your projects; `development/wf-settings.md`, private to the
clone and shared by its worktrees, overrides it key by key; per-run arguments override both. While no
role is set anywhere, the first stage that resolves roles (`wf init`, or a new project's spec review)
asks for them and for fallbacks (`shared/roles.md`), and saves the answers where you choose. `wf init`
also shows existing roles and asks whether to keep or change them. The file format is in
`skills/wf-init/templates/wf-settings.md`. Agent values are `self`,
`claude`, `codex`, `gemini`, and `opencode`. `self` means a fresh subagent of the current host or a fresh
run of its CLI. Agent CLI authentication is the user's responsibility.

| Role | Shorthand | Long form | Settings keys | Applies to |
|---|---|---|---|---|
| test | `test=a,m,e` | `test-agent=`, `test-model=`, `test-effort=` | `Test`, `Test fallback` | `wf test` |
| rev | `rev=a,m,e` | `rev-agent=`, `rev-model=`, `rev-effort=` | `Rev`, `Rev fallback` | Spec, roadmap, plan, implementation, wave reviews |
| sec | `sec=a,m,e` | `sec-agent=`, `sec-model=`, `sec-effort=` | `Sec`, `Sec fallback` | `wf sec`, in `wf impl loop` or standalone |
| fix | `fix=a,m,e` | `fix-agent=`, `fix-model=`, `fix-effort=` | `Fix`, `Fix fallback` | Plan, implementation, wave fixes |
| hunt | `hunt=a,m,e` | `hunt-agent=`, `hunt-model=`, `hunt-effort=` | `Hunt`, `Hunt fallback` | `wf sec audit`: reconnaissance and hunters |
| verify | `verify=a,m,e` | `verify-agent=`, `verify-model=`, `verify-effort=` | `Verify`, `Verify fallback` | `wf sec audit`: coverage critics and verifiers |

```markdown
- Rev: codex,<model>,xhigh
- Rev fallback: claude,<model>,medium
- Rev fallback: gemini
- Models: default
- Max review rounds: 3
```

In the shorthand `<role>=agent[,model[,effort]]`, an omitted or empty part is `default`: `rev=codex`,
`rev=codex,gpt-5`, `rev=codex,,high`. Model names may contain `:` or `/`
(`rev=opencode,ollama/llama3:8b,high`). Use one form per role; `rev=codex rev-effort=high` is an
error. An unset field comes from the project settings file, then the user file, then from
`models=default|auto` (argument, then `Models`, then `default`); an agent falls back to `self`. In a
settings file, an omitted part or `default` follows `Models`. A role takes up to three `fallback` lines,
in order, each naming its agent; one equal to the role's choice or an earlier fallback is left out.
`<Role> fallback: none` turns off those of the user file, and an argument for the role keeps them. A
committed `development/wf-settings.md` is refused. Unknown keys and invalid values stop the stage. Role
lines left in an older `AGENTS.md` block (`Rev agent`, …) count last, with a warning, until `wf init`
moves them. `rounds=` sets the review rounds in this run (settings, then the block's `Max review rounds`,
then 3). The stage prints the resolved settings, fallbacks included, before it starts. `hunt` and
`verify` are used only by `wf sec audit`, which asks for them on its first run; they never count as the
role setup that `wf init` asks for.

`default` passes no model or effort flag. `auto` chooses only a supported setting and records the actual
value. Unsupported settings stop the stage. `rounds` must be 1–10; a new run requires your explicit
request. Headless stages have a timeout. Each review has at most one retry for a transient execution
failure, and stagnation or an unresolved reviewer dispute stops the loop. An agent's usage limit
(`run-agent.sh` exit 6) is never retried under the same settings: the loop runs the stage with the role's
next fallback, records it and keeps it for the rest of that run. Without a fallback left, or when a write
stage had already changed files (exit 7), it asks: wait for the reset or use an agent you name for that
run, and for exit 7 whether to keep or discard the partial change. A delegated run that changes a role
settings file fails with exit 3, and the loop stops until you have checked it. A review round that marks a
check of the `AGENTS.md` table `NOT RUN` counts only after the orchestrator has run that check on the
reviewed commit and it passed; otherwise the round is not appended and the loop asks you.

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

Questions come early. A stage asks a decision that is yours and changes what gets built as soon as it
comes up: at most four questions at a time, each with 2–4 options, the recommended one first, through the
host's question tool or as a numbered list in chat (`shared/conventions.md` → Asking the human). `wf plan`
asks the decisions that shape the plan before writing it; the rest stay as the plan's open decisions,
written with the recommendation applied, and Gate 1 asks them before the approval. The approval counts
only when you chose every recommendation; another choice is a change request. A question that approves,
authorizes `wf auto`, accepts findings, or allows a push, deployment or deletion recommends no option, and
accepting findings needs your rationale in your own words. When you answer while `wf next` or `wf auto`
runs, the answer is recorded and the run goes on.

| Gate | Examine | Approval enables |
|---|---|---|
| PRD | Users, behavior, exclusions, success | Architecture spec |
| Spec | Boundaries, contracts, security, checks | Roadmap |
| Prototype scope | Covered requirements, simulations, dependencies | Building the prototype |
| Prototype outcome | Coverage, learnings; adopt, reference, or discard | Its use in the roadmap |
| Roadmap | Milestones, dependencies, wave safety | Task creation |
| Gate 1, each task | Scope, tests, security, plan review | Test author and implementation |
| Gate 2, each wave | Combined diff, full checks, integration review | PR preparation |
| Push and PR | Exact branch, commits, title, body | Push and PR creation |
| `wf auto` request | Your own assignment; one task, wave of one | Policy-passed Gates 1 and 2 |
| Maintenance | Updates, advisories, scan gaps | Dependency changes |
| Security audit | Commit, profile, scope, budget, roles, sandbox controls | Starting the audit agents |
| Deployment | Exact runbook, backups, checks | Deployment |

## Prototype

`wf prototype [focus]` is optional and needs an approved PRD and spec. It shows a scope for your yes
(covered requirements, simulations, dependencies and install scripts, branch), then builds on
`prototype/<slug>` in the sibling worktree `<repo>-worktrees/prototype-<slug>`, smoke-checks each
covered requirement, and records how to run it in `development/project/prototype.md`. The default scope
is the happy path of every Must requirement, with storage, sign-in and external services simulated
behind the spec's interfaces. A failing smoke step gets at most 3 fixes before it is reported as
`Broken`. `wf prototype <feedback>` makes a new revision; feedback that adds a requirement, a dependency
or another stack needs a new yes.

`wf prototype done adopt|reference|discard` records your decision; `wf roadmap` then plans the use.
Adopting needs a finished revision and puts one task, alone, in the first wave not yet started. That
task copies the listed files from the recorded commit, has `Sensitivity: high`, sets up the planned
checks, replaces or locks away simulated sign-in, and keeps human Gates 1 and 2 even under `wf auto`.
`wf deploy` stops while a `PROTOTYPE:` marker remains, unless you accept it by name. The prototype
branch is never pushed or merged, the test author never reads it, and `wf next` never starts a
prototype. Later, `wf prototype done` offers to remove the worktree and branch.

## Autopilot

`wf auto <goal>` (or `wf auto TASK-N` / `wf auto WAVE-N` to continue) is an explicit, up-front
authorization for Gate 1 and Gate 2 of one task in a wave of one. It stops at the push-and-PR question
with the PR prepared; that question, the push and the merge stay yours. Each gate passes only when
`scripts/auto-gate.py` reports it eligible from the recorded facts: the authorization in the ticket and
wave record, valid review rounds that match their headers, the checks, and the diff. Otherwise the run
stops at the ordinary gate and shows the policy's reasons. The policy, the protected paths, and what is
never automatic are in `shared/autopilot.md`. Approvals it records read
`Approved: … by autopilot (<timestamp>)`. It never pushes, opens or merges a PR, force-pushes, deploys,
accepts findings, or extends a round cap. Block keys: `Autopilot: allowed | disabled` and
`Autopilot paths: <glob>, …` (more protected paths; the built-in ones always apply).

## Security audit

`wf sec audit [quick|standard|deep] [<path>…] [since=<ref>] [budget=<n>] [waves=<n>] [parallel=<n>]
[hunt=…] [verify=…] [models=default|auto]` runs only when you ask: never from `wf next` or `wf auto`. It
follows Cloudflare's security-audit workflow (`skills/wf-sec-audit/references/AUDIT.md`, notice in
`UPSTREAM.md`): reconnaissance, coverage-led hunting waves with a critic after each wave and a final-clean
critic, validation of every candidate by a fresh verifier, `findings.json`, a fresh verification of every
final record, and the reports. `quick` runs one wave and merges the two verifications; paths and `since=`
make a partial run. Needs a clean tree on the commit to audit.

| Setting | Default | Limit |
|---|---|---|
| `budget` (agent runs, retries included) | quick 25, standard 60, deep 150 | 6 or 7 to 500 |
| `waves` | quick 1, standard 3, deep 6 | 10 |
| `parallel` (runs at once) | 2 | 8 |
| Reassignments of one unit / re-verifications of one record | — | 2 / 2 |

`scripts/audit.py` keeps the records in `development/audits/sec-audit-<YYYY-MM-DD>[-<n>]/`, writes every
agent prompt and refuses a launch that the budget, the wave cap or the reserves for the critics and
verifiers cannot fund, and only your number raises the budget; it validates each result without
repairing it, and two discarded results in a row from one role stop the run until you answer. Every agent runs through `run-agent.sh` in a fresh session:
reconnaissance, critics and record verifiers read only, hunters and candidate verifiers may write only to
their scratch directory (a check after each run, not a confinement: agents keep their CLI's own
permissions, so the independent verifier is the safeguard against a forged result). Target code runs only through `scripts/sandbox-run.py`: bubblewrap with no
network, an empty environment, read-only system and source (an export of the committed bytes), writes to a
size-limited tmpfs only, and cgroup and rlimit limits on tasks, memory, CPU, file size and time. Its probe
reports each control; inside Codex's sandbox it fails, for want of the user session bus. The command's bounded stdout and stderr are
the only evidence, promoted into the run by the parent after the agent has ended. A `confirmed` verdict
needs a local check that its verifier ran itself, backed by the runner's record of it; a verifier whose sandbox failed settles nothing. The audit gate shows the
host probe; when it fails, you choose between fixing it and a source-only run. It also lists the agent
configuration the target commits (hooks, plugins, MCP servers) and the ignored one in your clone, which
every agent CLI loads at startup outside the sandbox: approving with committed configuration takes
`--trust-agent-config`, else audit a clone without it. Every
agent run is pinned to the audited commit. `audit.py final` writes
`findings.json`; the run ends with `audit.py done` once the reports exist. The end report proposes one
task per confirmed root cause (`Sensitivity: high`); you choose which to create.

## Records and recovery

Plans and reviews live in `development/` in the main worktree; each task worktree links to it. A PRD, spec, or
roadmap revision loses its previous approval. A task review applies only to its recorded checkpoint;
a wave Gate 2 applies only to its recorded integrated commit. Run `wf status` after a pause or a failed
stage. When a loop escalates, read the unresolved findings and choose a new approach, an additional
bounded run, or explicit acceptance. A new approach is recorded as a human round in the review, so the
records send the item back to its loop. A plan revision approved after implementation makes the
task's test report and summary stale (their `Plan: revision <n>` line), so the records name `wf test`
and `wf impl` again. A missing tool is `NOT RUN`, never a pass.
