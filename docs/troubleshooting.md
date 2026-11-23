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

Task worktrees, and the `wf prototype` worktree, live in `<repo>-worktrees/`, outside the main clone,
and their `development/` links back to the main clone. A session started in the main clone prompts for
writes to a task worktree; a session started in a task worktree prompts for record writes, because they
resolve to the main clone. List both directories in `permissions.additionalDirectories`, for example in
`~/.claude/settings.json` or each checkout's `.claude/settings.local.json`:

```json
{ "permissions": { "additionalDirectories": ["/path/to/repo", "/path/to/repo-worktrees"] } }
```

The parent `<repo>-worktrees` entry covers future worktrees too. `/add-dir` does the same for one session.

## A headless Codex run cannot write records, commit or reach the network

`scripts/run-agent.sh` starts Codex with `workspace-write`, which writes only inside the directory it
starts in and keeps `.git` read-only. For a write run the runner therefore adds the real path of
`development/` with `--add-dir` when a task worktree links it to the main clone. Codex still cannot
commit; the delegating session commits the run's changes (`loop.md` → Delegated runs).

The sandbox has no network by default. If a check in the `AGENTS.md` table needs it (a module proxy,
a vulnerability database, a secret-scanner download), set `- Agent network: on` in the `wf` block, or
`WF_AGENT_NETWORK=1` for one run. A Codex run that is told to delegate again fails with
`Read-only file system (os error 30)`; every delegated prompt forbids starting another agent.

## Records disappeared after git clean

`development/` in the main clone is ignored, so `git clean -x` or `-X` deletes it with every record.
Use `git clean -n` first and never pass `-x` or `-X` in a clone with workflow records. Recover from a
backup; the PRs hold the delivered behavior and test evidence.

## A review does not finish

Read the stage's `.out.md.log` in the task or wave `runs/` directory. A timeout, invalid response,
changed review tree, or unsupported model is a failed stage, not approval. `wf status` shows the next
safe action. Choose another available agent/model or request a new bounded run; do not edit a review
verdict by hand. The failed run's files stay as `<stage>-r<n>-attempt<k>.out.md` beside the rerun.

## An agent reached its usage limit

`scripts/run-agent.sh` exits 6 and repeats the agent's message, for example Codex's "You’ve hit your
usage limit … try again at 9:32 PM" or Claude's "You've hit your 5-hour limit · resets 9pm". The run
produced nothing. If your settings list a fallback for the role (`- Rev fallback: claude,<model>,medium`
in `development/wf-settings.md` or `~/.config/agentic-dev-workflow/settings.md`), the loop runs the same
stage with it, records it in the round, and keeps it for the rest of that run. Otherwise, or when every
fallback is limited too, it stops: wait for the reset and ask for the same stage again, or name another
agent and model for that run (`rev=claude,<model>`, for example). It never switches to an agent you did
not name. Exit 7 means a write stage (tests or a fix) had already changed files when it hit the limit;
it always stops so you can look at the partial change before anything else runs.

## A review could not run a check

A delegated review in a sandbox can report a check `NOT RUN` (integration tests without sockets, an
audit without network). The validator names it on a `not run:` line. Before the round is appended, the
orchestrator runs it on the reviewed commit. When it passes, the round goes into the review file with an
`### Orchestrator checks` section; when it fails or cannot run there either, the round is not appended, so
the commit has no review and no gate can pass, and the loop asks you. Fix the cause for later runs, for
example with `Agent network: on`.

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

## The security audit's sandbox probe fails

`sandbox-run.py probe` lists every control with what is missing. No bubblewrap: install it (`bubblewrap`
package). `bwrap: setting up uid map: Permission denied` on Ubuntu: AppArmor restricts unprivileged user
namespaces (`kernel.apparmor_restrict_unprivileged_userns=1`); the distribution's bubblewrap package ships
the profile that allows it, a bwrap from elsewhere does not. The cgroup limits need cgroup v2. A cgroup limit that is missing means systemd
does not delegate that controller to your user session. Inside a delegated run the probe can fail although
it passes on the host: Codex's sandbox blocks the user session bus (`Failed to connect to user scope
bus`), and a Claude sandbox can block namespaces; the audit then stops and asks. A source-only run
is your explicit choice at the audit gate.

## A check ends with status 137, or the desktop says a wf-sandbox scope was terminated for low memory

A check went past its own memory limit (`MemoryMax`, from `sandbox-run.py run --memory-mb`, default
1024 MiB), and the kernel killed it; the system did not run out of memory. The check's evidence reads
`"status": 137` and `"killed": null`, often with empty stderr, and its `scope` field names the unit:
`journalctl --user -u <scope>` shows "The kernel OOM killer killed some processes in this unit". Rerun the
check with a larger `--memory-mb` (at most 4096) when it needs more.

KDE and GNOME report every user unit that fails with the result `oom-kill` as "terminated by the Linux
kernel because the system is low on memory". The sandbox scope continues after the kill instead
(`OOMPolicy=continue`), so the notice no longer appears; on systemd before 253, where a scope takes no
`OOMPolicy`, it still does, also once for every run of the test suite, which checks that the limit holds.

## The security audit discards agent results

`audit.py apply` accepts only exactly one JSON object that matches the contract, and never repairs one: a
local model with a small context or weak instruction following produces prose, a truncated object or
invented fields. Check the out-file under `development/audits/<run>/runs/`. Raise the model's context, use a
stronger `hunt` model, or lower the units per hunter. Each discarded result may get one fresh rerun; two in
a row from one role stop the run until you answer (`prompt … --after-human-answer`).

## An OpenCode hunter hangs until its timeout

OpenCode does not end a run whose provider refuses it: after a free model's "Free usage limit reached" it
waits until `WF_AGENT_TIMEOUT` stops it (30 minutes by default). Its run log in
`development/audits/<run>/runs/` shows nothing, so `run-agent.sh` reports exit 5, not a usage limit (6);
OpenCode's own log, `~/.local/share/opencode/log/opencode.log`, has the message. Run the stage again with
another `hunt` model or agent (`prompt … --ran-as <agent,model,effort>`). Two OpenCode runs that start at
the same moment can also fail with `database is locked`, from OpenCode's local database; a fresh rerun
usually works, and `parallel=1` avoids it.

