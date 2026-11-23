# Agentic Development Workflow

A practical workflow for building software with Claude Code, Codex, Gemini CLI, OpenCode, or another
agent that can read Agent Skills. It works with any programming language: each project declares its own
format, lint, type, test, build, and security checks. Conversations follow your language; code,
workflow records, commits, and PRs use English.

The agent does the research and implementation. You approve the product direction, task plans, and
integrated code. Reviews run in a fresh context, and the review loops stop after a fixed number of
rounds. The workflow keeps working records private to your local clone.

## The workflow at a glance

```text
New project or major initiative
  PRD → approve → spec → approve → [optional prototype → try it] → roadmap and task waves → approve
                                  ↓
Existing project, small change → task → plan → independent review → approve
                                  ↓
                 task worktree → independent tests → implementation
                                  ↓
                    independent code/security review
                                  ↓
               integrate wave → full checks → review → approve
                                  ↓
                         push and open one wave PR
                                  ↓
                       merge → close the wave
                                  ↓
                       optional gated deployment
```

A wave is a group of related tasks delivered in one PR. Its tasks all start from the wave's base
commit, so they must not need each other's code or share mutable files; they can then run in parallel
worktrees. A task that builds on another task's code goes into a later wave. A small change uses a
wave of one.
Related code and tests are committed together; a wave may have several commits.

## Install once per project

Prerequisites: Git, Python 3, and at least one supported agent CLI. Clone this repository outside the
project and run its installer:

```bash
git clone https://github.com/djancz/agentic-dev-workflow.git
python3 agentic-dev-workflow/install.py --project /path/to/project --agents claude,codex,gemini,opencode
```

Choose only the agents you use. The installer links each `wf-*` skill into that agent's project skill
directory. Claude Code gets `.claude/skills/` symlinks; Codex gets `.agents/skills/`; Gemini CLI and
OpenCode get their project skill directories. Re-run the same command after updating this repository.
`--dry-run` shows changes, and `--uninstall` removes only skill links owned by this repository while
preserving private workflow records. A name
collision stops installation before changes are made. See [agent setup](docs/agents.md).

The installer also keeps private workflow records in `development/` in the clone's main worktree and
links `development/` in every task worktree to it, so all worktrees of this local clone see the same
plans and reviews. Git ignores it; do not add workflow records to commits, and never run `git clean -x`
or `-X` in the main clone, which would delete them. Another clone does not receive those records, so PR
text must explain the change and its test evidence. If a project already has an unrelated
`development/` directory, the installer stops before making changes.

Installations made before this layout kept the records in `.git/wf-state`, where agents cannot write
without a prompt. The installer detects that, changes nothing, and prints the command to run:
`install.py --project <main clone> --agents <agents> --migrate-state --dry-run`, then the same command
without `--dry-run`. See [troubleshooting](docs/troubleshooting.md).

## Everyday use

Commands are **messages to your coding agent**, not shell commands. In an agent that exposes skills as
slash commands, `/wf-next` and similar names work too. You need four things:

1. **Start:** `wf task <problem and desired behavior>` for a change, or `wf prd <what to build and for
   whom>` for a new project or a major initiative.
2. **Continue:** `wf next`. The agent runs the next stages itself and stops when it needs you.
3. **Answer gates** in your own words and language: approve, or say what to change. Nothing is approved
   by silence, and nothing is pushed or deployed without your explicit yes.
4. **After you merge the PR:** `wf wave done`. It closes the wave and offers to clean up worktrees.

When the agent needs a decision from you, it asks as soon as the question comes up, not only at the
next gate: at most four questions at a time, each with two to four options and the recommended one
first. It uses your agent's question picker when there is one, and otherwise a numbered list in chat,
and waits for your answer. You can always answer in your own words. A question that approves something,
authorizes `wf auto`, accepts findings, or allows a push, deployment or deletion recommends no option.
When you answer while `wf next` or `wf auto` runs, the agent records your answer and goes on.

`wf status` shows where everything stands without changing anything. The next step always comes from
the records, not from the agent's memory: a script (`shared/scripts/next-step.py`) reads the headers in
`development/` and names one next command, a question for you, or the tasks to choose between, plus the
branch whose worktree that command runs in. Every stage ends with its `Next:` line, and `wf status`,
`wf next` and `wf auto` use it. Tasks to choose between come earliest wave first, and a wave does not
start until the waves its roadmap rows depend on (`WAVE-N`, `TASK-N` or a row key such as `M1.2`) are
merged. A follow-up recorded during a task is never offered as the next step while a task or wave is
still open. A plan revised after its task was implemented sends that task back through tests,
implementation and review, and its wave waits for it. A typical small change:

```text
you:   wf task CSV export drops rows with empty names; export them with an empty cell
agent: TASK-7 created. Next: wf plan TASK-7
you:   wf next
agent: plan written and reviewed (approved in round 2). Gate 1: <plan digest>
you:   approved
agent: tests, implementation, reviews and wave checks done. Gate 2: <diff, checks, findings>
you:   approved
agent: PR draft: <branch, commits, title, body>. Push and open it?
you:   yes
you:   wf wave done        (after merging on your forge)
```

Behind `wf next`, a change runs `wf plan` → `wf plan loop` → Gate 1 → `wf wave start` →
`wf new-worktree` → `wf test` → `wf impl` → `wf impl loop` → `wf wave integrate` → Gate 2 →
`wf finalize`. A new project first runs `wf prd` → `wf spec` → `wf roadmap` → `wf init` →
`wf milestone`, each with its own approval; `wf prototype` after the spec is optional.
`wf wave integrate` is offered only once every task of the wave is ready to integrate; until then the
agent lists the tasks that are not. If a stage stops with findings or unavailable tooling, the agent
says why and what you can choose. Every stage also has its own command for manual control; see
[commands and gates](docs/skills.md).

## Autopilot: from assignment to a prepared PR

`wf auto <problem and desired behavior>` runs the same stages, reviews and checks as `wf next`, but passes
Gate 1 and Gate 2 itself when a fixed policy allows it. It stops with the integrated, approved wave and
the PR prepared: branch, commits, title and body. Your message, which names `wf auto` or the autopilot,
is the authorization, for those two gates of that one task only; pushing, opening the PR and merging
stay with you.

```text
you:   wf auto CSV export drops rows with empty names; export them with an empty cell rev=codex
agent: TASK-7 … plan approved by autopilot … wave approved by autopilot … PR draft: <…>. Push and open it?
```

The policy (`shared/scripts/auto-gate.py`) reads only the recorded facts, including your recorded
authorization. It passes Gate 1 for a plan with no open decisions and an approved review of its current
revision. It passes Gate 2 for a wave of that one task only when the integrated wave review approved the
current commit, the task's implementation review (and its security review, for a sensitive task) approved
the tip of its task branch, every check passed or is N/A or a failure proven to exist on the base
already, and the diff leaves dependencies, migrations, CI, agent instructions, scanner ignores, build or
deploy files, symbolic links, submodules, and the files the checks run untouched. Gate 2 also needs the
plan approved as `Approved: revision <n> …` with an approving plan review of that revision, and check
commands it can read statically (no `cd`, shell globs, `..`, variable or symlinked paths, or submodules).
A review counts only when its last round is valid and matches its header. Otherwise it stops at the usual
gate with its reasons; after your answer, `wf auto TASK-N` continues. A reviewer from another model
family (`rev=codex`, `rev=gemini`) is recommended, also for its fallback. Add `- Autopilot: disabled` to
the managed block to turn it off, or `- Autopilot paths: <glob>, …` to protect more paths.

## Start a new project

Create an empty Git repository, install the skills, and type `wf prd <idea>`. The agent asks for missing
product decisions and writes a PRD for your approval. `wf next` then proposes an architecture spec and a
roadmap of milestones and waves, each reviewed in a fresh context before you approve it, configures the
project checks (`wf init`), and creates the first milestone's tasks. From there, each task follows the
same path as a small change. To try the product before the roadmap, run `wf prototype` after the spec.

## Try a prototype early

The full route takes a while before there is anything to click. Once the spec is approved,
`wf prototype` builds a runnable prototype from the PRD and spec so you can try the product first. It
is optional. The agent first shows the scope for your yes: by default the happy path of every Must
requirement, with storage, sign-in and external services simulated, and the dependencies it will
install. It then builds on its own branch, `prototype/<slug>` in a sibling worktree, checks each covered
requirement, and tells you how to run it. Reply with `wf prototype <feedback>` for a new revision. When
you have seen enough, `wf prototype done` asks you to choose:

- **adopt**: the prototype becomes the starting code. It reaches the base branch only through one
  ordinary task at the start of the roadmap: plan review and Gate 1, independently written tests,
  implementation and security review, wave integration and Gate 2, and your yes for the PR.
- **reference**: later tasks may start from its files.
- **discard**: only its learnings stay.

The prototype gets no plan or code review of its own; the adoption task reviews all of it. Its branch is
never pushed or merged, and `wf next` never starts a prototype. Its learnings feed the roadmap and,
when needed, a PRD or spec revision. Simulated sign-in never reaches the base branch as it is.

## Work in an existing project

Install the skills and run `wf init` on the project base branch. It detects the project's check commands,
reports which pass, fail, or cannot run, and writes them to a managed block in `AGENTS.md`; commit it
through your normal process. Your agent roles stay in the private `development/wf-settings.md`. Then
start each change with `wf task`. For a major initiative, start with `wf prd`. `wf maintenance` checks
routine dependency and security updates; `wf sec audit` runs a deep security audit when you ask for one;
`wf deploy` uses an approved runbook when your project deploys to a server. If the PR gets comments or CI fails, type
`wf finalize WAVE-N` and the agent works them in and asks before pushing again. When a wave whose PR is
open goes back for changes and passes Gate 2 again, the same command updates that PR, again only after
your yes, and never opens a second one.

## Choices you control

Four roles each have an agent, a model and an effort: `test` writes tests, `rev` reviews, `sec` runs
the security review, and `fix` fixes review findings. They are your personal choice, so they live
outside the repository and never reach a commit or a teammate: `~/.config/agentic-dev-workflow/settings.md`
for all your projects, and `development/wf-settings.md` for one project, which overrides the first key by
key. You do not have to write them by hand. While no role is set, the first stage that needs one
(`wf init`, or the spec review of a new project) asks you for each role's agent and a fallback. It
offers the agent CLIs you have installed, and you can type a model. It then saves your answers for all
projects or for this one. Each later `wf init` shows the current roles and asks whether to keep or
change them. A role left at `self` runs on the agent's `default` model and effort. The maximum review
rounds (**3** by default) stays a project setting in the managed block of `AGENTS.md`; a settings file
may override it for you. A block that still has role lines (`Rev agent`, …) keeps working with a
warning until `wf init` moves them into `development/wf-settings.md`.

```markdown
- Rev: codex,<model>,xhigh
- Rev fallback: claude,<model>,medium
- Sec: codex,<model>,xhigh
- Test: claude,<model>,high
- Fix: claude,<model>,high
```

`wf sec audit` adds two roles of its own, `hunt` and `verify`, and asks for them on its first run.
A role is `agent[,model[,effort]]`; an omitted part follows `Models: default | auto`. Up to three
`fallback` lines per role are tried in order when the role's agent reaches its usage limit, and the
stage records which one ran. Override a role for one run with `<role>=agent,model,effort` (leave a part
out or empty for `default`) or with `<role>-agent=`, `<role>-model=`, `<role>-effort=`; the role keeps
its fallbacks:

```text
wf test TASK-7 test=gemini,<model>
wf plan loop TASK-7 rev=codex,<model>,high rounds=2
wf impl loop TASK-7 rev=opencode,<provider/model> sec=claude,,xhigh rounds=3
wf wave integrate WAVE-2 rev-agent=claude rev-effort=high fix=codex
```

Use `models=auto` to let the agent choose supported settings for a stage. The report records what
actually ran. If a selected CLI cannot apply your model or effort, the stage stops and explains why;
it does not silently substitute another setting. Gemini CLI has no effort flag; OpenCode uses its
provider-specific `--variant` option for the effort.
A review round cap is a positive number up to 10. To run more after escalation, you must request another
finite run. See [commands and gates](docs/skills.md).

## Review, security, and token use

A fresh agent writes feature tests from the approved contract before implementation. A different fresh
context reviews the implementation. Every task runs its declared checks; security-sensitive tasks also
receive a dedicated review. `PASS` means the check ran and passed; missing tools and skipped checks are
`NOT RUN` with a reason. A review counts only once every check it could not run in its sandbox has run
and passed on the reviewed commit. When a delegated agent reaches its usage limit (exit 6), the loop
runs the stage again with the role's next fallback from your settings and records it; without one, it
asks you whether to wait for the reset or use another agent, and it never picks one itself. A write
stage that had already changed files when it hit the limit (exit 7) always stops and asks. The agent
uses the project's existing conventions and the [engineering principles](shared/engineering.md).

Skills load only stage-relevant references. Review round one covers the full change; later rounds focus
on findings and the delta. The orchestrator reads short artifact headers and bounded command output.
A loop stops at its round cap, on stagnant findings or disagreement, or after repeated agent failures
or timeouts. It lists unresolved findings for your decision. It never approves itself or loops forever.

## Deep security audit

`wf sec audit [quick|standard|deep] [paths]` audits the whole repository, or the given paths, when you ask
for it; nothing else starts it. It adapts Cloudflare's
[security-audit skill](https://github.com/cloudflare/security-audit-skill): reconnaissance agents map the
code, a coverage ledger splits it into units of entry surface, trust boundary, subsystem and attack class,
hunters review the units in waves, coverage critics look for gaps, and fresh verifiers try to refute every
candidate and check every final record. A finding is `confirmed` only with a source trace and a result
observed in a local sandbox; a lead that needs a fact outside the source is `needs_validation`, without
severity. The report, the ledger and the findings stay in `development/audits/`; the audit never changes
code, and it proposes one `wf task` per confirmed root cause for you to choose.

Two roles of its own run the agents: `hunt` (reconnaissance and hunters, for example an open-weight model
through OpenCode, `hunt=opencode,<provider/model>`) and `verify` (critics and verifiers, best another model
family). It asks for them on its first run. Before any agent starts, the audit gate shows the commit, the
scope, the budget of agent runs (25, 60 or 150 by profile), the roles and which sandbox controls hold, and
waits for your yes. Target code runs only in `sandbox-run.py`: bubblewrap inside a systemd user scope, with
no network, an empty environment, a read-only copy of the commit and low limits. Where that sandbox is not
available (macOS, for example), you may choose a source-only run. See [commands and gates](docs/skills.md).

## Reference

- [Commands, options, and approval gates](docs/skills.md)
- [Agent installation and compatibility](docs/agents.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Engineering principles](shared/engineering.md)

The workflow is MIT licensed. Its design draws on concepts from
[Agentic Development Workflow](https://github.com/wilsonkichoi/agentic_development_workflow). `wf sec audit`
includes adapted text of Cloudflare's MIT-licensed
[security-audit skill](https://github.com/cloudflare/security-audit-skill); its notice and the list of
changes are in `skills/wf-sec-audit/references/UPSTREAM.md`.
