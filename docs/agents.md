# Agent setup

The source skills are portable Agent Skills: each has `SKILL.md` with a name and description. Project
instructions live in `AGENTS.md`, with `CLAUDE.md` and `GEMINI.md` importing it when those agents are
used. Install from a separate clone of this repository:

```bash
python3 /path/to/agentic-dev-workflow/install.py --project /path/to/project --agents claude,codex,gemini,opencode
```

| Agent | Project skill links | Headless review support |
|---|---|---|
| Claude Code | `.claude/skills/wf-*` | `claude -p` |
| Codex | `.agents/skills/wf-*` | `codex exec` |
| Gemini CLI | `.gemini/skills/wf-*` | `gemini -p` |
| OpenCode | `.opencode/skills/wf-*` | `opencode run` |
| Other agent | Read `skills/wf-*/SKILL.md` directly | Use its fresh-session mechanism |

If a skill does not appear, restart the agent session and check the symlink target and that the agent
can read `SKILL.md`. The installer supports `--dry-run`, `--uninstall`, and selecting fewer agents.
It never replaces a file or link it does not own. It keeps the private records in the main worktree's
`development/` and links each task worktree's `development/` to it, and stops if an existing directory
would be overwritten. `--migrate-state` moves records from the legacy `.git/wf-state` location.

Claude Code never auto-approves a write into `.git`, even through a symlink, and Codex keeps `.git`
read-only in its sandbox; that is why the records no longer live there. A session started in a task or
prototype worktree, or one that edits a sibling worktree, works outside its working directory. Claude
Code then prompts unless `permissions.additionalDirectories` lists the main clone and the
`<repo>-worktrees` directory; OpenCode asks through its `external_directory` permission; the Codex and
Gemini sandboxes write only inside the directory they start in. See
[troubleshooting](troubleshooting.md).

The review runner is `shared/scripts/run-agent.sh`. It uses the selected CLI, a timeout, and output
validation. For a review it checks that neither the working tree nor the private `development/` records
(outside `runs/`) changed, contents of untracked files included. In every mode, it fails with exit 3 when
the personal role settings changed (`development/wf-settings.md` and the user file in `$XDG_CONFIG_HOME`
or `~/.config`). An unsupported model or effort setting stops the run rather than silently changing
settings. A usage limit exits 6, and the loop may continue with a fallback the settings list for the role;
exit 7 is a usage limit after a write run had already changed files, which always stops and asks.
Gemini CLI has no effort option; OpenCode passes a role's effort as its provider-specific `--variant`
value. Of an OpenCode run the runner keeps only the final message, the text of its last step, as Claude and
Codex give it; the JSON events of the whole run stay in the run's log.
For a headless Gemini write stage, set `WF_GEMINI_SANDBOX=1`; otherwise use an interactive fresh
session. The runner requires the sandbox before using Gemini's automatic write mode.
For a headless Codex write stage, the runner adds the real `development/` directory as writable and,
with `Agent network: on` in the `wf` block (or `WF_AGENT_NETWORK=1`), enables network in the sandbox.
Codex cannot commit there; the delegating session commits. See [troubleshooting](troubleshooting.md).

Agent skill discovery and CLI flags may change. Check your installed CLI's help if a stage reports an
unsupported option. Use a manual fresh session and read the named skill if headless mode is not
available. The [OpenCode skill guide](https://opencode.ai/docs/skills) documents its skill directory;
[Gemini CLI's skill guide](https://codelabs.developers.google.com/gemini-cli/how-to-create-agent-skills-for-gemini-cli)
documents agent-compatible skills.

## Agents for wf sec audit

The audit's `hunt` role does most of the reading: reconnaissance and every hunter. An open-weight model
through OpenCode keeps that volume off your Claude or Codex quota (`hunt=opencode,<provider/model>`, any
provider OpenCode knows: a local Ollama or LM Studio, or a hosted one). Give it a context window of at
least 64k tokens: a hunter prompt carries the architecture summary, its ledger units, the attack-class
blocks and the result schema. Ollama's default context is much smaller; raise `num_ctx` for the model.
A weak model shows as discarded results (`audit.py apply` never repairs one), and two in a row stop the
run. The `verify` role, critics and verifiers, is best another model family than `hunt`. You can also run
the whole audit from an OpenCode session.

Local checks need `/usr/bin/bwrap` (bubblewrap) and `systemd-run --user` with the `cpu`, `memory` and
`pids` controllers delegated, as on current systemd distributions. `python3 <skill>/scripts/sandbox-run.py
probe` names each missing control. Inside a headless Codex run it cannot start: Codex's sandbox blocks the
user session bus that `systemd-run` needs. A Codex verifier can then still check the source and the
hunters' evidence, but not reproduce local results.

