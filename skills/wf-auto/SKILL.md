---
name: wf-auto
description: >-
  Autopilot: carry one change from the human's assignment to an opened draft pull request. Runs the
  usual wf stages with their independent reviews, passes Gate 1 and Gate 2 only when the deterministic
  autopilot policy allows it, and otherwise stops at the normal human gate. Never merges. Triggers:
  "wf auto", "autopilot", "dotáhni to až do PR", "udělej to celé až po pull request".
---

# wf-auto — from assignment to draft PR

Paths are relative to this skill's directory. Read `references/autopilot.md` and follow it; it overrides
the human-gate steps of the stages it runs and nothing else. Also read `references/workflow.md`,
`references/project.md`, and from `references/conventions.md` the sections Git, Checks, and Reporting to
the human. `<skills-dir>` is `dirname "$(realpath <this skill dir>)"`.

## Arguments

`wf auto <assignment> [rev=…] [fix=…] [sec=…] [test=…] [models=…] [rounds=<n>]` starts a new task.
`wf auto TASK-<N>` or `wf auto WAVE-<N>` continues an existing one. Role options pass to every stage
that takes them (`references/loop.md` → Arguments). A reviewer from another model family (for example
`rev=codex`) is recommended, because nobody else reads the code before the PR.

## Before the first stage

1. Run `git check-ignore -q development` and read the trusted `wf` block. Without the block, stop with
   `Next: wf init`. With `- Autopilot: disabled`, stop as `references/autopilot.md` → Authorization says.
2. Check the forge: `gh auth status` (or `glab auth status`). If none is authenticated, say now that the
   run will stop before the PR with a compare URL; continue.
3. Print one line: the authorization you are acting on, quoted, and its scope (one task, wave of one,
   draft PR).

## Run

Load each stage's skill from `<skills-dir>/wf-<stage>/SKILL.md` and follow it, as `wf next` does. Stages
that need a fresh context delegate exactly as their skills say; never write a review yourself. After each
stage print one line: stage, outcome, artifact.

1. **Task.** `wf task <assignment>`. Ask a question only when no reasonable assumption exists; record
   every assumption under *Notes*. Add the authorization to *Notes*:
   `Autopilot authorization: <timestamp> — "<the human's words>"`. If the stage proposes a split or a new
   project, stop.
2. **Plan.** `wf plan`, then `wf plan loop`. At Gate 1 run `scripts/auto-gate.py plan` as
   `references/autopilot.md` → Policy shows. Eligible: record the approval as *Recording an automatic
   approval* says and continue. Not eligible: present the ordinary Gate 1 digest, add the policy's
   reasons, and stop.
3. **Wave.** `wf wave start WAVE-<N>`. Put the `Autopilot:` line in the wave record header. Then
   `wf new-worktree TASK-<N>`. If you cannot work in the new worktree, stop and name the directory and
   `wf auto TASK-<N>`.
4. **Build.** `wf test` when the plan says `Tests: required`, then `wf impl`, then `wf impl loop`. A
   material deviation, an escalation, or `needs-human` stops.
5. **Integrate.** `wf wave integrate WAVE-<N>`. Record each pre-existing check failure as
   `references/autopilot.md` → Policy describes, with the base evidence. When the wave review approves
   the current `HEAD`, run `git fetch origin <base>`, then `scripts/auto-gate.py wave`. Eligible: record
   the approval. Not eligible: present the ordinary Gate 2 digest with the policy's reasons and stop.
6. **Pull request.** `wf finalize WAVE-<N>` under the autopilot: draft PR, no separate push question, the
   PR body read back and cleaned, an *Autopilot* section in the body. Record the PR URL and `Pushed`.

Before each gate decision, re-read the headers from disk; never decide from memory of an earlier stage.
A human reply at a stopped gate is an ordinary gate answer: record it as the stage's skill says. The
autopilot continues only after a new `wf auto TASK-<N>` or `wf auto WAVE-<N>`.

## Never

Never merge, mark the PR ready for review, force-push, deploy, delete branches, accept findings, extend a
round cap, skip a stage or an independent review, or pass a gate the policy refused. Never edit
`AGENTS.md`, the policy script, or the records' review sections to make a gate eligible.

## Report

Follow `references/autopilot.md` → Report. End with `— wf-auto · <timestamp>`.
