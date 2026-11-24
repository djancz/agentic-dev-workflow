# Role setup

The agent, model and effort of each role (`test`, `rev`, `sec`, `fix`) and their usage-limit fallbacks
are the human's personal settings (`loop.md` → Arguments). This file is how to ask for them. `wf init`
always runs it. Any other stage that resolves roles runs *Nothing set* only when its `--sources` output
starts with `settings: none`, before its first delegated run. A new project therefore asks at its first
spec review. `wf sec audit` resolves only its own roles, `hunt` and `verify`, and runs *Audit roles*
instead. Paths are relative to the skill's directory.

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
2. **Fallbacks**: one question for each role whose agent is not `self`. When the read output lists
   fallbacks for the role already, or `none`, the first option keeps them. Recommend another installed
   agent with its default model, so that a usage limit does not stop the loop. Offer no fallback as an
   option. A typed answer also takes `agent,model,effort`; a fallback always names its agent.
3. **Where**: all projects, `~/.config/agentic-dev-workflow/settings.md` (recommended when that file sets
   no role), or this project only, `development/wf-settings.md`. Ask it with the fallbacks when it fits.
   Write to the exact path that `python3 scripts/resolve-roles.py --paths` prints for that choice:
   `$XDG_CONFIG_HOME` may move the user file.

Create the file if it is missing, and keep its other lines. Write one line per role,
`- <Role>: <agent>[,<model>[,<effort>]]`, even for `self` with its defaults, so that the setup is not
asked again. For each role whose fallbacks change, replace all its `- <Role> fallback:` lines in that
file, `none` included, with one line for each fallback in the order given, or with
`- <Role> fallback: none` for no fallback. Run the read command again. If it exits 2, fix the line it
names. Check that each role and its fallbacks now read as chosen. If not, the other file decides: a
project line overrides the user file, and `- <Role> fallback: none` in the project file turns off the
user file's fallbacks. Change that file only when the human agrees. Show the role and fallback lines to
the human.

## Something set (`wf init` only)

Show the role and fallback lines with their sources. Ask one question: keep them (recommended), or
change some. For a change, ask which roles (several may be chosen). Ask only those, as in *Nothing set*,
with the current value as the first option. Save each change in the file its line came from, and write
and check it as there. Ask *Where* when the line came from `default`, or when the human may mean this
project only. A run argument (`rev=…`) covers one run; never save it as a setting.

## Audit roles (`wf sec audit` only)

`hunt` maps the code and runs the hunters; `verify` runs the coverage critics and every verifier. Read
them with `--roles hunt,verify --sources`. Ask only for a role whose line ends `(from default)`, as
*Nothing set* says, and never for the four other roles:
1. **Hunt**: recommend `opencode` with a model the human types (`provider/model`, an open-weight model
   suits the volume of reading), or `self` when OpenCode is not installed.
2. **Verify**: recommend an installed agent of another model family than `hunt`, so that no model
   verifies its own candidates.
3. **Fallbacks** and **Where** as in *Nothing set*.
Write and check the lines as there. A run argument (`hunt=…`) covers one audit only. The audit roles
never come from the wf block of `AGENTS.md`, which the audited repository commits.
