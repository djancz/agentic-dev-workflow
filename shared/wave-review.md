# Integrated wave review

Use this only after all task branches have been integrated and full checks run. The review target is
the current wave `HEAD` and the diff from the recorded wave base commit. Store review rounds in
`development/waves/WAVE-<N>-review.md`; the header records `Round`, `Reviewed: commit <sha>`, `Verdict`,
and `Must-fix open`. Store agent prompts and outputs under `development/waves/runs/`.

Resolve `reviewer`, `model`, `effort`, `fix-agent`, `fix-model`, `fix-effort`, `models`, and `rounds` by
the precedence in `project.md`. The default is three rounds, maximum ten per requested run. The
reviewer must be a fresh context. A headless reviewer gets a read-only prompt to inspect the PRD/spec,
wave/task criteria, combined diff, check evidence, and security boundaries. It must report finding IDs,
severity, exact evidence, and a verdict. It writes no code. Save the prompt as
`runs/wave-review-r<n>.prompt.md` and use
`scripts/run-agent.sh --expect-clean <agent> rw <prompt> <out>` in the wave skill's directory, so it
can rerun relevant checks while leaving the source tree unchanged. The response starts `## Round <n>`
and includes `- Reviewed: commit <sha>`,
`- Verdict: Approved | Changes requested`, `- Must-fix open: <count>`, and a `### Method` section with
what was read and run; each must-fix finding starts `- Major:` or `- Blocker:`. Validate with
`python3 scripts/validate-wave-review.py --reviewed <HEAD> --round <n> <out>` before appending it.
If invalid, retry once for a transient error, then stop.

Round one reviews the full combined change and cross-task interactions. Later rounds review previous
findings and the new diff.

**Fix.** A fix runs in another fresh context on the wave branch: save the prompt as
`runs/wave-fix-r<n>.prompt.md` ("Run wf wave fix WAVE-N", as in `loop.md`) and use
`scripts/run-agent.sh <fix-agent> rw <prompt> <out>`. The fixer follows the rubric's Fix stage, appends
its resolution table under `### Fix round <n>` in `WAVE-<N>-review.md`, commits by concern as final
Conventional Commits (no `wip`), and reruns the affected checks. A fix moves `HEAD`; review of the
previous commit no longer counts.

**Base sync.** Before the first review and again before a push, run `git fetch origin <base>`. If
`git merge-base --is-ancestor origin/<base> HEAD` fails, merge it: `git merge origin/<base>` (never rebase,
never force-push). Resolve conflicts as in integration, rerun the full checks, record `Base synced:
<sha>` in the wave record, and review the merge in the next round. An earlier Gate 2 approval is void
because `HEAD` moved. Stop on
approval, the round cap, a repeated unresolved finding, nondecreasing must-fix count, disagreement, or
timeout. Show unresolved findings to the human; never infer approval. After all required verdicts are
approved for the current HEAD, present the wave Gate 2 digest from `project.md`.
