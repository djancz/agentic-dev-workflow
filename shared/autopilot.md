# Autopilot

`wf auto` carries one change from the human's assignment to an opened **draft** pull request. It runs
the ordinary stages with their independent reviews and fresh contexts; only the human gates change. The
human authorizes them once, up front, in the `wf auto` message, and the autopilot passes a gate only when
`scripts/auto-gate.py` finds it eligible. Anything else stops at the ordinary human gate. The pull request
is where the human reviews the result: the autopilot never merges it.

## Authorization

The `wf auto <assignment>` message is an explicit authorization, scoped to that one task and its wave of
one: approve Gate 1 and Gate 2 when the policy allows, push the wave branch, and open a draft PR. It
covers nothing else and does not carry over to other tasks, waves, sessions, or later PR feedback.
`wf auto TASK-<N>` or `wf auto WAVE-<N>` is a new authorization for that existing task and its wave, for
example to continue after the human passed a gate that the policy refused. A generic request to continue
(`wf next`, "continue") is not an authorization.

Record it before the first stage, in the wave record header: `Autopilot: <timestamp> — "<the human's
words>"`. A project turns the autopilot off with `- Autopilot: disabled` in its `wf` block; then
`wf auto` stops at once and suggests `wf task` and `wf next`.

## Scope

Only the existing-project route: one task in a wave of one. A PRD, spec, roadmap, milestone, a wave of
several tasks, `wf maintenance`, and `wf deploy` keep their human gates. If the assignment needs a split
or a new project, stop and say so.

## Policy

Run the policy at each gate with the trusted block (`conventions.md` → Checks):

```bash
git show "$(git merge-base <base> HEAD):AGENTS.md" > "$tmp/AGENTS.md"
python3 scripts/auto-gate.py plan --agents-md "$tmp/AGENTS.md" development/tasks/TASK-<N>-<slug>
python3 scripts/auto-gate.py wave --agents-md "$tmp/AGENTS.md" --base origin/<base> --head HEAD \
  development/waves/WAVE-<N>.md
```

Exit 0 is eligible, 1 is not eligible with its reasons, and 2 is invalid input, which also stops. The
script's docstring is the full policy. In short:

- **Gate 1:** a Draft plan of Size S or M, Sensitivity normal, with a Tests line and no open decisions,
  whose latest review approved the current revision with no must-fix findings.
- **Gate 2:** an approved wave review of the current `HEAD` with no must-fix findings; each task's
  implementation review, and its security review if one exists, approved its current Checkpoint; the
  full checks have at least one PASS and otherwise only N/A or `FAIL (pre-existing ...)`; and the diff
  against the base touches no protected path.
- **Pre-existing failures.** A check that failed before the change may stay red when the integrator ran
  it on the base commit and recorded `FAIL (pre-existing: <base evidence>)` in the wave record: the same
  check fails on the base, and the wave adds no new failure (no new error, test, or file in its output).
  The wave review verifies that claim like any other evidence. A new failure, `NOT RUN`, or a pre-existing
  failure without base evidence stops.
- **Protected paths** need a human: dependency manifests and lockfiles, migrations, CI and forge files,
  agent instructions and skill links, scanner configuration and ignores, `.gitignore`, build and deploy
  files, environment files, and the globs a project adds with `- Autopilot paths: <glob>, <glob>`.
  A project may add paths; it cannot remove the built-in ones.

## Recording an automatic approval

Record it where the human's approval would go, marked as the autopilot's:
- plan: `Status: Approved`, `Approved: revision <n> by autopilot (<timestamp>)`;
- wave: `Status: Approved`, `Approved: commit <sha> by autopilot (<timestamp>)`.

Add the policy output to the plan's *Revision history* or the wave's *Review and open findings*. The
approval covers exactly that plan revision or commit; anything that changes it voids the approval, and
the gate is decided again.

## Stop, never pass

Stop and present the ordinary gate, question, or escalation when:
- the policy says not eligible or exits 2;
- a stage asks a question, escalates, reaches its round cap or stagnates, or reports `needs-human`;
- implementation finds a material deviation (`workflow.md` → Deviations);
- open findings would have to be accepted: only the human accepts findings;
- a base sync merges anything after Gate 2: integrate and review again, then decide Gate 2 again;
- no authenticated forge CLI can open the PR: print the compare URL, title and body, and stop.

The autopilot never merges, marks a PR ready for review, force-pushes, deploys, deletes branches, or
answers PR feedback. PR comments and failing CI later go through `wf finalize` with the human gates.

## The pull request

`wf finalize` opens the PR as a draft (`gh pr create --draft`, or the forge's equivalent) without the
separate push question; it pushes only the wave branch, fast-forward. After creating it, read the PR body
back and remove anything the host appended that `conventions.md` → Git forbids (attribution footers,
session links). The body adds an *Autopilot* section: the authorization, each auto-passed gate with its
policy output, the checks table, and the review verdicts.

## Report

End with the PR URL, the gates the autopilot passed and any it stopped at, the checks outcome, and
`Next: review and merge the draft PR, then wf wave done WAVE-<N>`. When it stops early, name the gate or
question and the exact reply that lets it continue.
