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
  PRD → approve → spec → approve → roadmap and task waves → approve
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
project and run:

```bash
python3 /path/to/agentic-dev-wf/install.py --project /path/to/project --agents claude,codex,gemini,opencode
```

Choose only the agents you use. The installer links each `wf-*` skill into that agent's project skill
directory. Claude Code gets `.claude/skills/` symlinks; Codex gets `.agents/skills/`; Gemini CLI and
OpenCode get their project skill directories. Re-run the same command after updating this repository.
`--dry-run` shows changes, and `--uninstall` removes only skill links owned by this repository while
preserving private workflow records. A name
collision stops installation before changes are made. See [agent setup](docs/agents.md).

The installer also links `development/` in the project to private storage in its common Git directory.
All worktrees of this local clone see the same plans and reviews. The link is ignored by Git; do not add
workflow records to commits. Another clone does not receive those records, so PR text must explain the
change and its test evidence. If a project already has a `development/` directory, the installer stops
and tells you to move its contents to the private state location before trying again.

## Everyday use

Commands are **messages to your coding agent**, not shell commands. In an agent that exposes skills as
slash commands, `/wf-next` and similar names work too. You need four things:

1. **Start:** `wf task <problem and desired behavior>` for a change, or `wf prd <what to build and for
   whom>` for a new project or a major initiative.
2. **Continue:** `wf next`. The agent runs the next stages itself and stops when it needs you.
3. **Answer gates** in your own words and language: approve, or say what to change. Nothing is approved
   by silence, and nothing is pushed or deployed without your explicit yes.
4. **After you merge the PR:** `wf wave done`. It closes the wave and offers to clean up worktrees.

`wf status` shows where everything stands without changing anything. A typical small change:

```text
you:   wf task CSV export drops rows with empty names; export them with an empty cell
agent: TASK-7 created. Next: wf plan TASK-7
you:   wf next
agent: plan written and reviewed (approved in round 2). Gate 1: <plan digest>
you:   approved
you:   wf next
agent: tests, implementation, reviews and wave checks done. Gate 2: <diff, checks, findings>
you:   approved
you:   wf next
agent: PR draft: <branch, commits, title, body>. Push and open it?
you:   yes
you:   wf wave done        (after merging on your forge)
```

Behind `wf next`, a change runs `wf plan` → `wf plan loop` → Gate 1 → `wf wave start` →
`wf new-worktree` → `wf test` → `wf impl` → `wf impl loop` → `wf wave integrate` → Gate 2 →
`wf finalize`. A new project first runs `wf prd` → `wf spec` → `wf roadmap` → `wf init` →
`wf milestone`, each with its own approval. If a stage stops with findings or unavailable tooling, the
agent says why and what you can choose. Every stage also has its own command for manual control; see
[commands and gates](docs/skills.md).

## Start a new project

Create an empty Git repository, install the skills, and type `wf prd <idea>`. The agent asks for missing
product decisions and writes a PRD for your approval. `wf next` then proposes an architecture spec and a
roadmap of milestones and waves, each reviewed in a fresh context before you approve it, configures the
project checks (`wf init`), and creates the first milestone's tasks. From there, each task follows the
same path as a small change.

## Work in an existing project

Install the skills and run `wf init` on the project base branch. It detects the project's check commands,
reports which pass, fail, or cannot run, and writes them to a managed block in `AGENTS.md`; commit it
through your normal process. Then start each change with `wf task`. For a major initiative, start with
`wf prd`. `wf maintenance` checks routine dependency and security updates; `wf deploy` uses an approved
runbook when your project deploys to a server. If the PR gets comments or CI fails, type
`wf finalize WAVE-N` and the agent works them in and asks before pushing again.

## Choices you control

Four roles each have an agent, a model and an effort: `test` writes tests, `rev` reviews, `sec` runs
the security review, and `fix` fixes review findings. Project defaults live in the managed workflow block
of `AGENTS.md` (`Rev agent`, `Rev model`, `Rev effort`, …) with the maximum review rounds. They start as
`self`, `default` model/effort, and **3 rounds**. Override a role for one run with
`<role>=agent,model,effort` (leave a part out or empty for `default`) or with `<role>-agent=`,
`<role>-model=`, `<role>-effort=`:

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
`NOT RUN` with a reason. The agent uses the project's existing conventions and the
[engineering principles](shared/engineering.md).

Skills load only stage-relevant references. Review round one covers the full change; later rounds focus
on findings and the delta. The orchestrator reads short artifact headers and bounded command output.
A loop stops at its round cap, on stagnant findings or disagreement, or after repeated agent failures
or timeouts. It lists unresolved findings for your decision. It never approves itself or loops forever.

## Reference

- [Commands, options, and approval gates](docs/skills.md)
- [Agent installation and compatibility](docs/agents.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Engineering principles](shared/engineering.md)

The workflow is MIT licensed. Its design draws on concepts from
[Agentic Development Workflow](https://github.com/wilsonkichoi/agentic_development_workflow).
