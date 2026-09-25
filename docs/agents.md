# Agent setup

The source skills are portable Agent Skills: each has `SKILL.md` with a name and description. Project
instructions live in `AGENTS.md`, with `CLAUDE.md` and `GEMINI.md` importing it when those agents are
used. Install from a separate clone of this repository:

```bash
python3 /path/to/agentic-dev-wf/install.py --project /path/to/project --agents claude,codex,gemini,opencode
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
It never replaces a file or link it does not own. It links `development/` to private shared worktree
state, and stops if an existing directory would be overwritten.

The review runner is `shared/scripts/run-agent.sh`. It uses the selected CLI, a timeout, and output
validation. For a review it checks that neither the working tree nor the private `development/` records
(outside `runs/`) changed. An unsupported model or
effort setting stops the run rather than silently changing settings. Gemini CLI has no effort option;
OpenCode passes `effort=` as its provider-specific `--variant` value.
For a headless Gemini write stage, set `WF_GEMINI_SANDBOX=1`; otherwise use an interactive fresh
session. The runner requires the sandbox before using Gemini's automatic write mode.

Agent skill discovery and CLI flags may change. Check your installed CLI's help if a stage reports an
unsupported option. Use a manual fresh session and read the named skill if headless mode is not
available. The [OpenCode skill guide](https://opencode.ai/docs/skills) documents its skill directory;
[Gemini CLI's skill guide](https://codelabs.developers.google.com/gemini-cli/how-to-create-agent-skills-for-gemini-cli)
documents agent-compatible skills.
