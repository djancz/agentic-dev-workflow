# Working on this repository

This repository contains portable software-development workflow skills. Read `README.md` for the
human workflow and `docs/skills.md` for the command map. Source rules live in `shared/`; skills refer
to them through relative symlinks. Keep a skill short and load only the references needed for its stage.
Use `name` and `description` frontmatter only.

Preserve these invariants: every approval is explicit; feature tests and reviews use a fresh context;
checks report missing or skipped runs honestly; loops have finite caps and stop on stagnation; a wave
PR is created only after its integrated diff is reviewed and the human approves it. Work records under
`development/` stay private. The repository must contain no personal or company material from the
local references.

When changing a command or gate, update the skill, `README.md`, and `docs/skills.md` together. Add
behavioral tests for scripts and installer behavior. Run `python3 -m unittest discover -s tests -v`.
Use English Conventional Commits and stage explicit paths. Do not add AI attribution.
