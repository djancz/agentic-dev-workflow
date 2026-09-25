---
name: wf-security-review
description: >-
  Deep, independent security review: threat model, secret/dependency/SAST scanners, manual source-to-sink
  review against a CWE checklist and language pitfalls, dependency vetting. Reviews a wf task's diff
  (required when the plan says Sensitivity: high) or audits given paths without a task. Appends a review
  round with a verdict; Critical/High issues are Blockers. Triggers: "wf sec", "security review",
  "bezpečnostní review", "zkontroluj bezpečnost", "security audit".
---

# wf-security-review — think like the attacker

Role: **security reviewer**. Use only the artifacts and the repository. Paths are relative to this skill's
directory. Read:
- `references/security.md` (all of it)
- `references/review-rubric.md`
- `references/conventions.md`: the section Checks
- `templates/security-review.md`
- `references/workflow.md`: the section Human gates (for the report, task mode)

For a standalone call, accept `agent=`, `model=`, and `effort=` with the project security defaults;
start a fresh native subagent or headless run when the requesting context authored the change. Record
the actual agent/model/effort. For headless mode, use `scripts/run-agent.sh --expect-clean <agent> rw
<prompt> <out>` and validate its round with `scripts/validate-review.py --kind security` before
appending it. Stop if a requested setting cannot be applied or validation fails.

For task mode, this must be a fresh context. If this context implemented or fixed the checkpoint, do not review
or write a verdict: delegate to a fresh subagent/headless session, or ask the human to run `wf sec` in a new
session. Audit mode may run in the requesting context.

## Mode and scope

- **Task mode** (the default when a task resolves). The scope is `git diff <base>...HEAD`; in round 2 and
  later it is `git diff <previous Reviewed sha>..HEAD`. Also read the plan's *Security* section, the
  summary's *Security notes*, and the header and latest round of `TASK-<N>-security-review.md`. The
  summary's `Checkpoint` must equal `HEAD`.
- **Audit mode** (`wf sec <path…>`, or no task). The scope is the given paths, or the whole repository.
  Write to `development/audits/security-<YYYY-MM-DD>-<slug>.md`.

Note the output of `git status --porcelain` first. You must leave the tree as you found it.

## Round 1

1. **Threat model**, 5–10 lines: the assets, the entry points, the trust boundaries, the attacker, and the
   worst misuse.
2. **Scanners.** Run the trusted `secrets`, `deps-audit` and `sast` rows as defined by
   `references/conventions.md`. Triage every High or Critical result: a true positive becomes a finding,
   and a false positive gets a one-line reason. A missing tool is `NOT RUN (tool missing)`.
3. **Manual review.** For each entry point, follow the data from source to sink: queries, shell, the
   filesystem, templates or HTML, deserialisation, outbound requests, logs. Work through the checklist and
   the language pitfalls. Look at what is **missing**, too: authorisation checks, limits, timeouts, and
   failing closed.
4. **Dependencies.** For each new or upgraded one: is it needed, maintained and pinned? Does it have
   install scripts? Is the name exactly right (typosquatting)?
5. **Findings.** Use IDs `S<round>-<n>` and give both severities and the CWE. Map severities with the table
   in `references/security.md`.

## Round 2 and later

A delta review, as the rubric describes. First the previous `S` findings, then the delta. Re-run the
scanners if dependencies or security-relevant code changed.

## Write

- Append the round and update the header (`Reviewed: commit <sha>`).
- Do not modify code, and do not print secret values. Redact them in evidence.
- Confirm the tree is unchanged.

**Delegated output mode.** If your prompt contains `OUTPUT MODE (delegated, read-only)`, write no files
and leave every file as it started.
Your final message must be exactly the new round section, starting at `## Round <n>`.

## Report

Skip this section in delegated output mode. Report the verdict, the must-fix count, one line per finding
with its CWE, and the scanners that did not run. Then `Next`:
- `Changes requested`: `wf impl fix` (or `wf impl loop`).
- Task mode, `Approved*`: follow the implementation review of this checkpoint. If it is `Approved*`,
  mark the task ready to integrate and report `Next: wf wave integrate WAVE-N`. If there is none
  yet, `wf impl rev` in a new session; if it requested changes, `wf impl fix` (or `wf impl loop`).
- Audit mode: propose `wf task`s for the must-fix findings, one per concern
  (`references/conventions.md` → Task scope).

End with `— wf-security-review · <timestamp>`.
