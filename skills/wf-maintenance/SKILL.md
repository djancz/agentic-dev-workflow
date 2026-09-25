---
name: wf-maintenance
description: >-
  Routine maintenance of a wf project: scan for outdated dependencies, known vulnerabilities, leaked
  secrets, SAST findings, end-of-life runtimes and outdated CI actions, and write a report. After the
  human approves the list at the maintenance gate, apply the routine patch and minor updates as a task
  in a one-task wave that continues through implementation review and wave integration. Major upgrades and code changes
  become proposed tasks. `wf maintenance scan` only writes the report. Triggers: "wf maintenance",
  "wf maintenance scan", "maintenance", "update dependencies", "check for updates", "údržba",
  "aktualizuj závislosti", "zkontroluj aktualizace".
---

# wf-maintenance — keep the project current

Role: **author**. Paths are relative to this skill's directory. Read:
- `references/ecosystems.md`
- `references/conventions.md`: sections Checks, Task scope, Git and Writing style
- `references/security.md`: sections Sensitivity, Scanners and Agent safety
- `references/workflow.md`: sections Layout, Artifact headers and Human gates
- `references/project.md`: Waves and Human gates
- `templates/report.md` and `templates/plan.md`

The update is **interactive only**: its gate needs the human. `wf maintenance scan` stops after the
report and may run unattended.

## Preconditions

- `AGENTS.md` has the `wf` block, and `git check-ignore -q development` succeeds. Otherwise suggest
  `wf init`.
- You are on the base branch, and `git status --porcelain` is empty. If `origin/<base>` is ahead
  (`git fetch origin <base>`), tell the human; do not pull on your own.
- **Resume.** If a plan with `Kind: maintenance` still has `Status: Draft`, show its gate again (step 5)
  instead of scanning, unless the human asks for a new scan. If it is `Approved` without a summary,
  continue at step 6.

## 1. Scan (read-only)

Run from the repository root, keeping only the summary (`2>&1 | tail -n 40`):
- **Dependencies.** For each ecosystem (`references/ecosystems.md` → Detection): current version, latest
  in range, latest.
- **Scanners.** The trusted `secrets`, `deps-audit` and `sast` rows (`references/conventions.md` →
  Checks). Triage every High or Critical result: a true positive, or a false positive with the reason.
- **Runtimes, images and CI.** Versions in use and their end of life; CI action versions; base images.
- **Setup.** Check rows still `planned`, and tools marked `not installed` in the `wf` block.

A lookup that needs the network and cannot reach it is `NOT RUN (<reason>)`, never a pass. Never print
secret values. A secret found in the history goes to the human at once, redacted.

## 2. Triage

Put every finding into one group:
- **Routine, this run:** patch and minor updates of direct dependencies, including those that close an
  advisory, and CI action updates within their major. Transitive dependencies move with the lockfile.
- **Proposed tasks:** each major upgrade, each update that needs code changes (a removed API, a
  deprecation), each end-of-life runtime, each scanner finding that needs code. One task per concern
  (`references/conventions.md` → Task scope): packages released together (`react`, `react-dom`) are one,
  and so are all CI action majors. A task that closes an advisory is marked urgent.
- **No action:** false positives, and pins the project keeps on purpose (a comment, `AGENTS.md`), each
  with its reason.

## 3. Report

Write `development/maintenance/<YYYY-MM-DD>.md` from `templates/report.md` (add `-2`, `-3` if the file
exists). For `wf maintenance scan`, or when nothing is routine, stop here: report and give `Next`:
`wf maintenance` to apply the routine updates, or `wf task <proposal>` for a proposed task.

## 4. Ticket and plan

Number and name the task as `wf task` does: `development/tasks/TASK-<N>-maintenance-<YYYY-MM>/`.
- `TASK-<N>.md` from `<skills-dir>/wf-task/templates/task.md`, where `<skills-dir>` is
  `dirname "$(realpath <this skill dir>)"`: `Type: chore`, the report path under *Problem*, one
  acceptance criterion per advisory closed, and "every check has the same result as on the base".
- `TASK-<N>-plan.md` from `templates/plan.md`: the exact updates (from → to), what is not updated and
  why, the update commands, commits by concern. `Status: Draft`, `Revision: 1`. Set the ticket to
  `In progress`, assign the next one-task `WAVE-<N>`, and name the task in the report's header.

## 5. Maintenance gate (stop here)

Present one screen:
- the updates, grouped by ecosystem, and the advisories they close
- what is not updated, and why
- the proposed tasks, numbered
- the scans that did not run
- how to answer: `schvaluji, aktualizuj` or `approved, update`, optionally with the proposed tasks to
  create (`schvaluji, aktualizuj, tasky 1 3`); or notes, which change the plan (`Revision + 1`) before
  the gate is shown again

Wait. **Never record an approval the human did not give** (`references/workflow.md` → Human gates). On
approval, record `Approved: <date> — "<their words>"` in the plan and set `Status: Approved`. This gate
takes the place of a plan review: the plan changes versions only, and the implementation review and
`wf sec` still review the result. Create the chosen proposed tasks as `wf task` does, each with the note
`From maintenance <YYYY-MM-DD>`.

## 6. Update

Start the one-task wave with `wf wave start WAVE-<N>` and its task worktree with
`wf new-worktree TASK-<N>`. Mark `Tests: N/A — dependency update adds no new feature behavior` in the
maintenance plan; the existing project checks remain mandatory. Then load `wf-impl` in the worktree.
It applies the approved update commands, runs every check, commits a checkpoint, and writes the summary.
- Update exactly the plan's list. Before installing, list new or changed install scripts
  (`references/ecosystems.md`) under the summary's *Security notes*.
- If an update does not resolve, or breaks a check in a way that needs code, stop and ask the human
  whether to leave that package out of this run. On a yes, record it under *Deviations* in the summary
  and add it to the report's proposed tasks.

## Report

Report the report path, the count in each group, the scans that did not run, and the task. Then `Next`:
the gate answer while it waits, or `wf impl loop` after the update (with `Sensitivity: high` it also runs
`wf sec`), then `wf wave integrate WAVE-<N>`. End with `— wf-maintenance · <timestamp>`.
