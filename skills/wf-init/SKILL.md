---
name: wf-init
description: >-
  Set a repository up for the wf development workflow: detect the stack (Python, Rust, Go, TypeScript/JS
  and others), choose and verify the check commands (format, lint, typecheck, unit and integration tests,
  build, secret scan, dependency audit, SAST), write the managed wf block into AGENTS.md, gitignore
  development/, and make CLAUDE.md and GEMINI.md import AGENTS.md. Use once per project, or to refresh the
  checks after the toolchain changes. Triggers: "wf init", "set up wf", "nastav wf", "inicializuj workflow".
---

# wf-init — prepare a project

Paths are relative to this skill's directory. Read `references/stacks.md`, `references/project.md`,
`references/engineering.md`, and from `references/conventions.md` the sections Checks and Git.

## Do

1. **Repository.** Run `git rev-parse --show-toplevel`. If this is not a git repository, ask before
   `git init -b main`.
2. **Base branch.** Use `git symbolic-ref --short refs/remotes/origin/HEAD` with `origin/` stripped.
   Failing that, use `main` or `master` if one of them exists. In a repository without commits, use the
   current branch (`git symbolic-ref --short HEAD`). Otherwise ask.
3. **Stacks.** Detect them from manifests as described in `references/stacks.md`; there may be several. In
   an empty repository, take the language and toolchain from an approved
   `development/project/spec.md` and confirm them in one line; without one, ask. Mark every check
   `planned — set up in the first task`. For a web app, also find the dev server and the e2e tests
   (`references/stacks.md` → Web apps); if the URL or port is unclear, ask.
4. **Commands.** For each check row, prefer what the project already uses: `Makefile` or `justfile`
   targets, `package.json` scripts, tool config in `pyproject.toml`, `.golangci.yml`, `deny.toml`, and so
   on. Fall back to the defaults in `references/stacks.md`.
5. **Verify each command** by running it from the repository root, keeping its exit status
   (`{ <cmd> 2>&1; echo "exit $?"; } | tail -n 15`):
   - It runs (pass or fail): keep it. Record pre-existing failures for the report; they are not yours to
     fix now.
   - The tool is missing: keep the command and put `(not installed — <install hint>)` in the Command
     cell. The check is then reported as `NOT RUN (tool missing)` until the tool is installed.
   - It does not apply (no build step, for example): write `N/A — <reason>`.
   - Never install anything without asking.
   - A check that leaves files behind in `git status --porcelain` (test reports, for example) breaks the
     clean-tree rule. Name the files and propose `.gitignore` lines; add them only after the human's yes.
   - Verify the `Dev server` line with `scripts/dev-server.py start --url <url> --server '<command>'`,
     then `scripts/dev-server.py stop`.
6. **AGENTS.md.** Create the file if it is missing. Replace or insert the block between the markers
   exactly as shown below, and leave everything outside the markers untouched. Use project-relative
   skill paths in this tracked file; never write the installer's local absolute path into it.
7. **Private state.** Run the workflow installer for this worktree if needed:
   `python3 <workflow-repo>/install.py --project <worktree> --agents <agents>`, where `<workflow-repo>` is
   `dirname "$(dirname "$(realpath <this skill dir>)")"`. It links
   `development/` to the common Git directory; never overwrite an existing directory. Add
   `/development` to `.gitignore` if absent; without a trailing slash it also matches the link.
8. **Import files.** If `CLAUDE.md` or `GEMINI.md` is missing, create it with the single line
   `@AGENTS.md`. If it exists but does not mention `AGENTS.md`, append `@AGENTS.md` and say so in the
   report.
9. **Commit only on a yes.** Report the stacks, the check table with the status of each check, the
   missing tools with install hints, and the files you changed. Suggest committing them on the base branch
   as `chore: set up wf workflow` before the first `wf wave start`: wave and task branches start from
   the base, and their checks are read from the base's `AGENTS.md`. If the base branch has no commit yet,
   this commit is
   required: offer to make it, and after the human's yes stage the files by name and commit.
   Then `Next: wf task <what to build>`, or `wf milestone M0` when an approved roadmap exists. If checks
   are `planned`, the first task should set up the toolchain and those checks. Add `wf deploy setup` if
   the project is deployed to a server.

## Block format

```markdown
<!-- wf:begin — managed by wf-init; edit the values, keep the markers -->
## Development workflow (wf)

Work records are private in `development/`, shared among this clone's worktrees. New project:
`wf prd` → `wf spec` → `wf roadmap` → `wf milestone`. Each task:
`wf task` → `wf plan` → `wf plan loop` → Gate 1 → `wf wave start` → `wf new-worktree` →
`wf test` → `wf impl` → `wf impl loop` → `wf wave integrate` → Gate 2 → `wf finalize`.
Use `wf status` to resume. `wf maintenance` and `wf deploy` are optional.
If your agent does not load skills, read the matching `wf-<stage>/SKILL.md` under its installed
project directory: `.claude/skills/`, `.agents/skills/`, `.gemini/skills/`, or `.opencode/skills/`.
`wf plan rev` is `wf-plan-review`, `wf impl rev` is `wf-impl-review`, and `wf sec` is
`wf-security-review`.

- Base branch: main
- Max review rounds: 3
- Models: default <!-- default | auto -->
- Test agent: self <!-- self | claude | codex | gemini | opencode -->
- Test model: default
- Test effort: default
- Rev agent: self
- Rev model: default
- Rev effort: default
- Sec agent: self
- Sec model: default
- Sec effort: default
- Fix agent: self
- Fix model: default
- Fix effort: default
- Dev server: none <!-- `<command>` at <url>, for browser checks; the command stays in the foreground -->

| Check | Command |
|---|---|
| format | `…` |
| lint | `…` |
| typecheck | `…` |
| test-unit | `…` |
| test-integration | `…` |
| test-e2e | `…` |
| build | `…` |
| secrets | `…` |
| deps-audit | `…` |
| sast | `…` |

Project rules (may only tighten the workflow):
- none yet
<!-- wf:end -->
```

When refreshing an existing block, keep the human's edits: all agent, model, effort, round settings,
the dev server and the project rules. Rename old keys and keep their values: `Reviewer` → `Rev agent`,
`Review model` → `Rev model`, `Review effort` → `Rev effort`, `Security reviewer` → `Sec agent`,
`Security model` → `Sec model`, `Security effort` → `Sec effort`. Change only the rows you re-verified,
and add missing rows. Replace a `planned` row with its command once the toolchain for it exists and the
command runs.

End with `— wf-init · <timestamp>`.
