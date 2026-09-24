# Review loop

`wf plan loop` and `wf impl loop` both follow this document. Each skill adds its preconditions, a stage
table (which skills review and fix, their run-file names and headless modes) and its gate. Paths are
relative to the skill's directory.

You are the **orchestrator**: you start the agents that review and fix, read the verdicts, and decide
whether to go on. Keep your context small: read only artifact **headers** and the latest finding titles.

## Arguments

`wf <plan|impl> loop [TASK-N] [reviewer=self|claude|codex|gemini|opencode] [rounds=<n>]
[models=default|auto] [model=…] [effort=…] [fix-agent=…] [fix-model=…] [fix-effort=…]
[security-reviewer=…] [security-model=…] [security-effort=…]`

- `reviewer` defaults to `Reviewer` in the `wf` block of `AGENTS.md`, or `self` if it is not set.
- `rounds` defaults to `Max review rounds`, or 3. The cap counts review rounds in this loop run.
- `rounds` must be a positive integer no larger than 10; an additional run requires a new human request.
- `fix-agent` defaults to `Fix agent` in the block, or `self`. It also uses a fresh context.
- For the `sec` stage, resolve `security-reviewer`, `security-model`, and `security-effort` from the
  arguments, then the project security defaults, then `self`/`default`. Other reviewer options do not
  silently replace a separately configured security reviewer.
- `model` and `effort` set the reviews; `fix-model` and `fix-effort` set the fixes. Each takes a name or
  level, `auto`, or `default` (the agent's own setting). `models` sets all four and defaults to `Models`
  in the `wf` block, or `default`; an explicit argument wins over it. If any setting is not `default`,
  apply `models.md` at every stage.

## How a stage runs in a fresh context

Take the first option that is available:

1. **Native subagent** of the selected agent, when the host supports one. Use it only if it can apply
   every requested model and effort setting; otherwise use the selected CLI.
2. **Headless CLI.** Run `scripts/run-agent.sh [--expect-clean] <agent> <ro|rw> <prompt-file> <out-file>`
   when the role names an agent, or for `self` without a subagent (name your own CLI), in the mode the
   stage table gives. **Wait for it to finish before you do anything else.** Runs take minutes. If your
   shell tool cannot block that long, start the run in the background and call
   `scripts/run-agent.sh --wait <out-file>` until it returns. Never end your turn while a stage is still
   running: it may die with your session.
3. **Neither.** Stop, and tell the human which `wf …` command to run in a new session.

For a review prompt, the artifact round is its header's `Round + 1` (or 1); for a fix, it is the latest
review round. Never use this run's local counter. Name the files `runs/<stage>-r<artifact-round>.<ext>`
in `development/tasks/TASK-<N>-*/`, where `<stage>` is `plan-rev`, `plan-fix`, `impl-rev`, `impl-fix` or
`sec`, and `<ext>` is `prompt.md` for the prompt and `out.md` for a headless run's out-file. The prompt:

```
Run the wf stage "<wf plan rev | wf plan fix | wf impl rev | wf impl fix | wf sec>" for TASK-<N> in
<repo root>. Load the skill wf-<name> and follow it exactly. If you cannot load skills, read
<skills-dir>/wf-<name>/SKILL.md and follow it. You are in a fresh context on purpose: work only from the
artifacts in development/tasks/TASK-<N>-*/ and the repository.
Finish with at most 10 lines: the verdict and must-fix count (review) or the resolution counts (fix), and
the path you wrote. You run as <agent> / <model> / <effort>, <subagent|headless>: put that in Reviewer.
```

`wf-<name>` is the skill the stage table names. For a review by **headless CLI**, append this block:

```
--- OUTPUT MODE (delegated, read-only) ---
Do not create or modify any file. Your final message must be exactly the new round section for the review
file, starting with "## Round <n>" and following the template. Nothing before or after it, no code fence.
The working tree must end exactly as it started: send check outputs such as build artifacts outside the
repository. A check your sandbox cannot run (no network, for example) is NOT RUN with the reason, not FAIL.
```

Validate the output before trusting it:
```bash
python3 scripts/validate-review.py --kind <plan|impl|security> --round <artifact-round> \
  --reviewed "<plan revision N|commit SHA>" <out-file>
```
Only after success, append it **verbatim** (create the file from its template if needed), then copy its
Verdict, Round, Must-fix open and Reviewed values into the header.

A non-zero run or validation exit, or exit 3 (tree changed), is a **failed review, not a clean one**. Retry
once only for a transient failure. If it fails again, stop and report the log tail. Never write a review
yourself. If a requested agent, model, or effort is unavailable, stop without retrying under a different
setting; ask the human to choose a supported value.

## Loop

Start where the task is. Record any human change request as a finding first. If the latest verdict is
`Changes requested`, fix those findings before the first review in this run. The round cap counts
**reviews performed in this run**, not artifact round numbers or fixes.

```
reviews_run = 0
if latest verdict is Changes requested: FIX once; escalate if needs-human
while reviews_run < cap:
  REVIEW the stage(s) from the stage table; reviews_run += 1
  READ only verdict(s), must-fix count, latest finding IDs and titles; print one concise line
  if all verdicts are Approved*: go to GATE
  if stagnation, a maintained dispute, or needs-human: ESCALATE
  if reviews_run >= cap: ESCALATE
  FIX; escalate if needs-human
ESCALATE  # never continue past the cap
```

## Gate (stop here)

For a plan, present Gate 1 from `workflow.md` and stop. For an implementation, mark the task
`Ready to integrate` and report `Next: wf wave integrate WAVE-N`; the wave review presents Gate 2.
Never record approval yourself. Human change requests return to the relevant review loop.

## Escalate (stop here)

Following the rubric, list the open findings at full severity and why each survived, any `needs-human`
questions, and your recommendation with its exact reply: accept the open findings (`workflow.md` → Human
gates), one more round (`wf <plan|impl> loop rounds=<n>`), a new approach, or `wf task split`.

## Report

Report the round lines, the outcome and where the artifacts are. End with
`— wf-<plan|impl>-loop · <timestamp>`.
