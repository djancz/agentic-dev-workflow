# wf settings

Personal settings of the wf roles, never committed: `development/` is private to this clone and shared
by its worktrees. `~/.config/agentic-dev-workflow/settings.md` (in `$XDG_CONFIG_HOME` when it is set)
takes the same lines for every project. A line here overrides the same key there, and a per-run argument
such as `rev=codex,,high` overrides both.

Roles: `Test` writes tests, `Rev` reviews, `Sec` runs the security review, `Fix` fixes review findings. A
role is `agent[,model[,effort]]`, where the agent is `self`, `claude`, `codex`, `gemini` or `opencode`; an
omitted part, or `default`, follows `Models`. Up to three `<Role> fallback` lines are tried in order when
the role's agent reaches its usage limit; each names its agent, and one equal to the role's choice is left
out. `<Role> fallback: none` turns off the fallbacks of the user file. `Max review rounds` overrides the
project's value in `AGENTS.md`. `wf init` asks for these settings and writes them here or in the user
file. To edit them by hand, move the lines you want out of the comment and replace `<model>` with a model
name.

<!--
- Models: default
- Max review rounds: 3
- Test: claude,<model>,high
- Rev: codex,<model>,high
- Rev fallback: claude,<model>,medium
- Sec: codex,<model>,high
- Sec fallback: claude,<model>,high
- Fix: claude,<model>,high
-->
