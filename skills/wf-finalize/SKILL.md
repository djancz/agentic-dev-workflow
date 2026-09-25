---
name: wf-finalize
description: >-
  Prepare and, only after explicit confirmation, push an approved wave branch and open one PR or MR. Triggers: "wf finalize", "prepare wave PR", "open the PR".
---

# wf-finalize

Read `references/project.md`, `references/conventions.md`, `references/wave-review.md`,
`templates/pr.md`, `templates/merge-commit.md`, and the approved wave record. Resolve `WAVE-<N>` from the argument or current branch. Require the wave's `Status: Approved`,
the current `HEAD` equal to the commit reviewed at Gate 2, and a clean worktree. If any of these differs,
return to `wf wave integrate`; do not use an older approval.

The task branches were squash-merged by concern into the wave branch. Check `git log <base>..HEAD` and
`git diff <base>...HEAD` against the approved wave record. Do not rewrite reviewed commits. Draft a
concise English Conventional Commit-style PR title and body: behavior, acceptance, checks with honest
PASS/FAIL/NOT RUN, security or unresolved findings, and any compatibility note. Do not include private
workflow records, credentials, personal data, model attribution, or raw logs.

Before showing them, sync with the base as `references/wave-review.md` → Base sync says; if that
merges anything, return to `wf wave integrate` for review and a new Gate 2.

Show the exact branch, commits, PR title and body. Ask for explicit approval to push and create the PR.
If approved, push only the wave branch. Use a supported forge CLI when authenticated; otherwise print
its compare URL and the exact title/body for manual creation. Never force-push or merge the PR without a
separate explicit instruction. Record the PR URL in the wave record and mark it `PR opened`.
Prepare the optional merge commit text from its template for a forge that preserves wave commits;
do not require squashing the entire wave into one commit.
