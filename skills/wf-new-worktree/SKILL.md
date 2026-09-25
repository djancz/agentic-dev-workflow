---
name: wf-new-worktree
description: >-
  Create a task branch and sibling Git worktree from its wave branch, with installed skills and shared private records. Triggers: "wf new-worktree", "new worktree", "create task worktree".
---

# wf-new-worktree

Read `references/project.md` and `references/conventions.md`. Require a task ID with an approved plan
and a started wave. Derive the task slug from its ticket, lowercase with hyphens. Inspect
`git worktree list`, all branches, and the target path; if this task already has a branch or worktree,
report it and stop. Do not overwrite it.

Use the configured wave branch as the base, at its recorded commit. Put the worktree in a sibling
directory named `<repo>-worktrees/TASK-<N>-<slug>`, never inside the repository. Create branch
`task/TASK-<N>-<slug>`. Create the sibling parent directory first. Derive `<installed agents>` from
the skill directories already installed in this checkout, not from which CLIs happen to be on PATH.
Run `python3 <workflow-repo>/install.py --project <worktree> --agents <installed agents>` to link skills
and the shared `development/` state, where `<workflow-repo>` is
`dirname "$(dirname "$(realpath <this skill dir>)")"`, the clone that holds `install.py`.
If installation fails, report the branch and worktree so the user can recover; do not delete work. Record branch, path, and base commit in the
task ticket. Report the next `wf test TASK-<N>` command.
