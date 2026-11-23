# Role setup

The agent, model and effort of each role (`test`, `rev`, `sec`, `fix`) and their usage-limit fallbacks
are the human's personal settings (`loop.md` → Arguments). This file is how to ask for them. `wf init`
always runs it. Any other stage that resolves roles runs *Nothing set* only when its `--sources` output
starts with `settings: none`, before its first delegated run. A new project therefore asks at its first
spec review. Paths are relative to the skill's directory.

## Read what is set

```bash
python3 scripts/resolve-roles.py --agents-md AGENTS.md --before-init --sources
```

Each line ends with `(from <source>)`: a settings file, `AGENTS.md` (old block lines; `wf init` moves
them first), `argument` or `default`. The first line names the sources, or reads `settings: none`.
Find the installed agent CLIs with `command -v claude codex gemini opencode`. Offer only those and
`self`. Never invent a model name: the human types one, or the agent's `default` applies.

## Nothing set

Ask as `conventions.md` → Asking the human says, at most four questions at a time:
1. **Agents**: one question each for `test`, `rev`, `sec` and `fix`. Each option is `self` or an
   installed agent, with its default model and effort. Recommend `self` for `test` and `fix`. For `rev`
   and `sec`, recommend an installed agent of another model family than yours, so that the review is
   independent; without one, recommend `self`. Each option's description says the human may type
   `agent,model,effort` instead, for example to name a model.
2. **Fallbacks**: one question for each role whose agent is not `self`. Recommend another installed agent
   with its default model, so that a usage limit does not stop the loop. Offer no fallback as an option.
   A typed answer also takes `agent,model,effort`.
3. **Where**: all projects, `~/.config/agentic-dev-workflow/settings.md` (recommended when that file sets
   no role), or this project only, `development/wf-settings.md`. Ask it with the fallbacks when it fits.

Create the file if it is missing, and keep its other lines. Write one line per role,
`- <Role>: <agent>[,<model>[,<effort>]]`, even for `self` with its defaults, so that the setup is not
asked again. Add one `- <Role> fallback: …` line for each fallback, in the order given. Run the read
command again. If it exits 2, fix the line it names. Show the role lines to the human.

## Something set (`wf init` only)

Show the role and fallback lines with their sources. Ask one question: keep them (recommended), or
change some. For a change, ask which roles (several may be chosen). Ask only those, as in *Nothing set*,
with the current value as the first option. Save each change in the file its line came from. Ask
*Where* when the line came from `default`, or when the human may mean this project only. A run argument
(`rev=…`) covers one run; never save it as a setting.
