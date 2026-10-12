---
name: wf-auto
description: >-
  Autopilot: carry one change from the human's assignment to an integrated, approved wave with its pull
  request prepared. Runs the usual wf stages with their independent reviews, passes Gate 1 and Gate 2
  only when the deterministic autopilot policy allows it, and otherwise stops at the normal human gate.
  Never pushes, opens or merges a PR. Triggers: "wf auto", "autopilot", "dotáhni to až do PR",
  "udělej to celé až po pull request".
---

# wf-auto — from assignment to a prepared PR

Paths are relative to this skill's directory. Read `references/autopilot.md` and follow it; it overrides
the human-gate steps of the stages it runs and nothing else. Also read `references/workflow.md`,
`references/project.md`, and from `references/conventions.md` the sections Git, Checks, and Reporting to
the human. `<skills-dir>` is `dirname "$(realpath <this skill dir>)"`.

## Arguments

`wf auto <assignment> [rev=…] [fix=…] [sec=…] [test=…] [models=…] [rounds=<n>]` starts a new task.
`wf auto TASK-<N>` or `wf auto WAVE-<N>` continues an existing one: resolve its next stage exactly as
`<skills-dir>/wf-status/SKILL.md` → Status does, record the new authorization, and continue from there.
Role options pass to every stage that takes them (`references/loop.md` → Arguments). A reviewer from
another model family (for example `rev=codex`) is recommended, because nobody else reads the code before
the PR.

## Before the first stage

1. Run `git check-ignore -q development` and read the trusted `wf` block, from the base as
   `references/autopilot.md` → Policy shows. Without the block, stop with `Next: wf init`. With
   `- Autopilot: disabled`, stop as `references/autopilot.md` → Authorization says.
2. Print one line: the authorization you are acting on, quoted, and its scope (Gate 1 and Gate 2 of one
   task in a wave of one; push and PR stay with the human).

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
   the current `HEAD`, fetch and run `scripts/auto-gate.py wave` as `references/autopilot.md` → Policy
   shows. Eligible: record
   the approval. Not eligible: present the ordinary Gate 2 digest with the policy's reasons and stop.
6. **Prepare the PR.** `wf finalize WAVE-<N>` up to its push-and-PR question, with the *Autopilot*
   section in the body (`references/autopilot.md` → After Gate 2). Stop there.

Before each gate decision, re-read the headers from disk; never decide from memory of an earlier stage.
The policy refuses a gate when the ticket or wave record lacks the authorization, so record it first.
A human reply at a stopped gate is an ordinary gate answer: record it as the stage's skill says. The
autopilot continues only after a new `wf auto TASK-<N>` or `wf auto WAVE-<N>`.

## Never

Never push, open, merge, or mark a PR ready, force-push, deploy, delete branches, accept findings, extend
a round cap, skip a stage or an independent review, or pass a gate the policy refused. Never edit
`AGENTS.md`, the policy script, or the records' review sections to make a gate eligible.

## Report

Follow `references/autopilot.md` → Report. End with `— wf-auto · <timestamp>`.
