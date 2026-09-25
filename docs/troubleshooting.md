# Troubleshooting

## The agent cannot find a skill

Run `python3 install.py --project <project> --agents <agent> --dry-run` from this workflow clone.
Check for a collision, stale symlink, or a session started before installation. Restart the agent.
For an unsupported agent, point it directly at the relevant `skills/wf-*/SKILL.md`.

## A project already has development/

The installer refuses to replace it. Inspect its contents and the path from
`git rev-parse --path-format=absolute --git-common-dir`; move the old private records to
`<common-git-dir>/wf-state/`, then rerun the installer. Keep a backup until `wf status` sees the records.

## A review does not finish

Read the stage's `.out.md.log` in the task or wave `runs/` directory. A timeout, invalid response,
changed review tree, or unsupported model is a failed stage, not approval. `wf status` shows the next
safe action. Choose another available agent/model or request a new bounded run; do not edit a review
verdict by hand.

## A loop stops with findings

The round cap or lack-of-progress rule stopped it. Read the finding IDs and severity in the review.
Request a revised approach, explicitly ask for another finite run, or explicitly accept named findings.
Accepted findings appear in the wave PR. A missing check stays `NOT RUN` with its reason.

## The PR got comments or CI failed

Type `wf finalize WAVE-N`. The agent records the actionable comments and failing checks as findings,
fixes them through the wave review, shows Gate 2 again, and asks before pushing. It does not reply to
reviewers unless you ask.

## A merged wave must be undone

Create a normal task: `wf task revert WAVE-N <reason>`. A revert is a change like any other and goes
through plan, review, and a wave PR. Never force-push the base branch.

## Parallel tasks conflict

Stop integration at the conflict. Compare both task plans and frozen contracts, resolve the code on the
wave branch, rerun affected checks, and include the resolution in the wave review. If the conflict
changes an approved public contract, revise the spec and affected task plans before continuing.

## Working records are missing in another clone

`development/` is private to one clone by design. Use the PR and tracked project documentation to
share delivered behavior and test evidence. Do not commit raw workflow records or logs to recover them.
