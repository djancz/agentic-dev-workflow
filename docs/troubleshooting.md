# Troubleshooting

## The agent cannot find a skill

Run `python3 install.py --project <project> --agents <agent> --dry-run` from this workflow clone.
Check for a collision, stale symlink, or a session started before installation. Restart the agent.
For an unsupported agent, point it directly at the relevant `skills/wf-*/SKILL.md`.

## A project already has development/

The installer refuses a `development/` it did not create, or one that Git tracks. Inspect its contents,
move it out of the main clone (or rename a tracked one), then rerun the installer. Keep a backup until
`wf status` sees the records.

## The installer reports records in .git/wf-state

Older installations kept the records in `<common-git-dir>/wf-state` and linked `development/` to it.
Claude Code never auto-approves a write that resolves into `.git`, so every record write prompted, even
in auto mode. The installer now changes nothing until you migrate. From this workflow clone, once per
project clone:

```bash
python3 install.py --project <main clone> --agents <agents> --migrate-state --dry-run
python3 install.py --project <main clone> --agents <agents> --migrate-state
```

The migration renames the directory to `<main clone>/development` (no copy, so nothing is half-moved)
and re-points every worktree link that leads to the old location, absolute or relative. A worktree
without `development/`, or with a link elsewhere, is left alone and listed. It never overwrites: if both
locations hold records, it stops so you can merge them by hand. If it is interrupted, run the same
command again; it continues where it stopped. Run it in each clone that uses the old layout.

## Claude prompts for edits in task worktrees

Task worktrees live in `<repo>-worktrees/`, outside the main clone, and their `development/` links back
to the main clone. A session started in the main clone prompts for writes to a task worktree; a session
started in a task worktree prompts for record writes, because they resolve to the main clone. List both
directories in `permissions.additionalDirectories`, for example in `~/.claude/settings.json` or each
checkout's `.claude/settings.local.json`:

```json
{ "permissions": { "additionalDirectories": ["/path/to/repo", "/path/to/repo-worktrees"] } }
```

The parent `<repo>-worktrees` entry covers future worktrees too. `/add-dir` does the same for one session.

## Records disappeared after git clean

`development/` in the main clone is ignored, so `git clean -x` or `-X` deletes it with every record.
Use `git clean -n` first and never pass `-x` or `-X` in a clone with workflow records. Recover from a
backup; the PRs hold the delivered behavior and test evidence.

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
