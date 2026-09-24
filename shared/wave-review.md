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
`- Verdict: Approved | Changes requested`, and `- Must-fix open: <count>`; each must-fix finding starts
`- Major:` or `- Blocker:`. Validate with
`python3 scripts/validate-wave-review.py --reviewed <HEAD> --round <n> <out>` before appending it.
If invalid, retry once for a transient error, then stop.

Round one reviews the full combined change and cross-task interactions. Later rounds review previous
findings and the new diff. Fixes run in another fresh context on the wave branch, commit by concern,
and rerun affected checks. A fix moves `HEAD`; review of the previous commit no longer counts. Stop on
approval, the round cap, a repeated unresolved finding, nondecreasing must-fix count, disagreement, or
timeout. Show unresolved findings to the human; never infer approval. After all required verdicts are
approved for the current HEAD, present the wave Gate 2 digest from `project.md`.
