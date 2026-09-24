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
                       optional gated deployment
```

A wave is a group of related tasks delivered in one PR. Independent tasks can run in parallel worktrees
when they have no unresolved dependencies or shared mutable files. A small change uses a wave of one.
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

Commands below are **messages to your coding agent**, not shell commands. In an agent that exposes
skills as slash commands, `/wf-prd` and similar names work too. Start by asking the agent to run `wf
init` in an existing project or `wf prd` in a new one.

## Start a new project

1. Create an empty Git repository, install the skills, and tell the agent `wf prd <what you want to
   build and for whom>`. It asks for missing product decisions and writes a PRD. Review and approve it.
2. Type `wf spec`. Review the proposed structure, contracts, security boundaries, and checks; approve
   or request a revision. Then type `wf roadmap` to approve working milestones and dependency-aware
   task waves.
3. Type `wf init` to configure project check commands in `AGENTS.md`. The first milestone sets up the
   toolchain and checks. Type `wf milestone M0` to create its tasks.
4. For each ready task: `wf plan TASK-1`, then `wf plan loop TASK-1`. Read Gate 1 and approve the plan.
   Type `wf wave start WAVE-1`, then `wf new-worktree TASK-1`. In that worktree run `wf test TASK-1`,
   `wf impl TASK-1`, and `wf impl loop TASK-1`.
5. After all wave tasks are ready, run `wf wave integrate WAVE-1` on the wave branch. Review the full
   check table, combined diff, and findings at Gate 2. Once approved, `wf finalize WAVE-1` prepares the
   PR and asks separately before pushing. Merge the PR on your forge.

You can stop after any stage and resume with `wf status`. The status output names the next command.
If a stage stops with findings or unavailable tooling, its record explains what remains.

## Work in an existing project

Install the skills, then run `wf init` on the project base branch. It detects existing project check
commands and reports which pass, fail, or cannot run. Review and commit its managed `AGENTS.md` block
through your normal process. For a small feature or bug, type `wf task <problem and desired behavior>`
and continue at `wf plan`. The task gets a one-task wave. For a major initiative, start with `wf prd`
and follow the new-project path. `wf maintenance` checks routine dependency and security updates;
`wf deploy` uses an approved runbook when your project has a deployment process.

## Choices you control

Project defaults live in the managed workflow block of `AGENTS.md`: test agent, reviewer, security
reviewer, fix agent, model and effort for each role, and maximum review rounds. They start as `self`,
`default` model/effort, and **3 rounds**. Override them for one run:

```text
wf test TASK-7 agent=gemini model=<model>
wf plan loop TASK-7 reviewer=codex model=<model> effort=high rounds=2
wf impl loop TASK-7 reviewer=opencode model=<provider/model> rounds=3
wf wave integrate WAVE-2 reviewer=claude model=<model> effort=high fix-agent=codex
```

Use `models=auto` to let the agent choose supported settings for a stage. The report records what
actually ran. If a selected CLI cannot apply your model or effort, the stage stops and explains why;
it does not silently substitute another setting. Gemini CLI has no effort flag; OpenCode uses its
provider-specific `--variant` option for `effort=`.
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

The workflow is MIT licensed. Its design draws on the local personal workflow and concepts from
[Agentic Development Workflow](https://github.com/wilsonkichoi/agentic_development_workflow).
