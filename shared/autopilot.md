# Autopilot

`wf auto` carries one change from the human's assignment to an integrated, approved wave with its pull
request prepared. It runs the ordinary stages with their independent reviews and fresh contexts; only
Gate 1 and Gate 2 change. The human authorizes them once, up front, in the `wf auto` message, and the
autopilot passes a gate only when `scripts/auto-gate.py` finds it eligible. Anything else stops at the
ordinary human gate. The push and the PR keep their own explicit question, so the human sees the whole
result before anything leaves the machine.

## Authorization

The `wf auto <assignment>` message is an explicit authorization, scoped to that one task and its wave of
one: approve Gate 1 and Gate 2 when the policy allows. It covers nothing else: not the push, the PR,
or the merge, and it does not carry over to other tasks, waves, sessions, or later PR feedback.
`wf auto TASK-<N>` or `wf auto WAVE-<N>` is a new authorization for that existing task and its wave, for
example to continue after the human passed a gate that the policy refused. The human's words must ask
for the autopilot by name (`wf auto`, `/wf-auto`, or "autopilot"); when a request only describes the
outcome ("take it all the way to a PR"), ask once whether to run the autopilot and record the reply
that names it. A request to continue (`wf next`, "continue") is not an authorization.

Record it in two places. At `wf task`, add `Autopilot authorization: <timestamp> — "<the human's words>"`
to the ticket's *Notes* (no wave record exists yet). At `wf wave start`, copy it into the wave record
header as `Autopilot: <timestamp> — "<the human's words>"`. For `wf auto TASK-<N>` or `WAVE-<N>`, add the
new authorization below the earlier ones in the ticket's *Notes* and, once the wave exists, replace the
wave record's `Autopilot:` line with it. The policy reads the latest one in the ticket and the one in the
wave record, and refuses a gate when it is missing, has no timestamp or words, does not ask for `wf auto`
or the autopilot, or names another task or wave. A project turns the autopilot off with
`- Autopilot: disabled` in its `wf` block; then `wf auto` stops at once and suggests `wf task` and
`wf next`.

## Scope

Only the existing-project route: one task in a wave of one. A PRD, spec, roadmap, milestone, a wave of
several tasks, `wf maintenance`, and `wf deploy` keep their human gates. If the assignment needs a split
or a new project, stop and say so.

## Policy

Run the policy at each gate with the trusted block (`conventions.md` → Checks). Fetch first, and use
the same base ref for the block and for `--base`, which must be the wave record's `Base branch`:

```bash
git fetch origin <base>
git show "$(git merge-base refs/remotes/origin/<base> HEAD):AGENTS.md" > "$tmp/AGENTS.md"
python3 scripts/auto-gate.py plan --agents-md "$tmp/AGENTS.md" development/tasks/TASK-<N>-<slug>
python3 scripts/auto-gate.py wave --agents-md "$tmp/AGENTS.md" --base origin/<base> --head HEAD \
  development/waves/WAVE-<N>.md
```

Exit 0 is eligible, 1 is not eligible with its reasons, and 2 is invalid input, which also stops. The
script's docstring is the full policy. In short:

- **Gate 1:** the ticket's *Notes* record the authorization, and a Draft plan of any Size and
  Sensitivity with a Tests line and no open decisions has a latest review that approved the current
  revision with no must-fix findings. A plan with `Sensitivity: high` gets its security review in
  `wf impl loop`, and Gate 2 requires that review approved.
- **Gate 2:** the wave record carries the authorization and lists exactly one task; the task's plan is
  still approved as `Approved: revision <current> …` (`workflow.md` → Human gates, or the autopilot's
  form below) and its plan review approved that revision with no must-fix findings, so a plan passed
  with accepted findings keeps Gate 2 with the human; an approved wave review of the current `HEAD` with
  no must-fix findings; the task's implementation review, and its security review (required unless the
  plan says `Sensitivity: normal`), approved its Checkpoint, which is the tip of the
  `task/TASK-<N>-<slug>` branch its ticket records; the full checks have one result per check of the
  block, at least one PASS, and otherwise only `N/A (<reason>)` or `FAIL (pre-existing: <evidence>)`;
  the base is merged into `HEAD`; and the diff against it, renames counted on both sides, touches no
  protected path. A `planned` check is `NOT RUN`, so it stops the autopilot until the check exists.
- **Records must agree.** The last `## Round` of every review must pass the same validator as a delegated
  round (`validate-review.py`, `validate-wave-review.py`), and the review header must match it. A
  repeated header field or round, a round inside a code fence or an HTML comment, or a fence or comment
  left open cannot approve anything. A commit counts only when its SHA names exactly that one commit.
- **Pre-existing failures.** A check that failed before the change may stay red when the integrator ran
  it on the base commit and recorded `FAIL (pre-existing: <base evidence>)` in the wave record: the same
  check fails on the base, and the wave adds no new failure (no new error, test, or file in its output).
  The result or its Evidence cell names the base commit's SHA and records what failed there, for
  example `FAIL (pre-existing: 190 errors on base 1a2b3c4, 190 on HEAD)`. The policy checks only the
  SHA; the wave review verifies the output like any other evidence. A new failure, `NOT RUN`, or a
  pre-existing failure without base evidence stops.
- **Protected paths** need a human: dependency manifests and lockfiles, migrations, CI, forge and
  repository files (`.gitignore`, `CODEOWNERS`, submodules), agent instructions and skill links, check,
  lint, type and scanner configuration and ignores, build and deploy files (`Makefile`, CMake, Docker),
  environment files, every symbolic link, every file a check command may run on the base or `HEAD`
  (named by its path or a shell glob, from the root, from a directory the command names, or through a
  linked directory, or by its file name alone, and the target of such a symlink), and the globs a
  project adds with `- Autopilot paths: <glob>, <glob>`. Matching ignores case. A project may add
  paths; it cannot remove the built-in ones. The block's `Autopilot`, `Autopilot paths` and
  `Base branch` lines must each appear at most once, written as `- Key: value` at the start of a line,
  and check names must be unique; otherwise the policy exits 2.

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
- a base sync merges anything after Gate 2: integrate and review again, then decide Gate 2 again.

The autopilot never pushes, opens, merges, or marks a PR ready, force-pushes, deploys, deletes branches,
or answers PR feedback. PR comments and failing CI later go through `wf finalize` with the human gates.

## After Gate 2

Run `wf finalize WAVE-<N>` up to its push-and-PR question and stop there. The prepared body adds an
*Autopilot* section: the authorization, each auto-passed gate with its policy output, the checks table,
and the review verdicts. The human's explicit yes to that question is an ordinary `wf finalize` answer;
the run does not need to continue for it.

## Report

End with the gates the autopilot passed and any it stopped at, the checks outcome, the prepared branch,
commits, PR title and body, and `Next: answer the push-and-PR question, then wf wave done WAVE-<N>` after
the merge. When it stops early, name the gate or question and the exact reply that lets it continue.
