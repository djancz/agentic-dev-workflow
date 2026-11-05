---
name: wf-prototype
description: >-
  Optional, after an approved PRD and spec: build a runnable prototype of the project on its own branch
  so the human can try it early, revise it from their feedback, then adopt it as the project baseline,
  keep it as a reference, or discard it. Never pushes or merges it. Triggers: "wf prototype",
  "wf prototype done", "prototype the project", "prototyp", "udělej prototyp", "postav prototyp".
---

# wf-prototype — something to try, early

Role: **author**. Paths are relative to this skill's directory. Read `references/project.md` (section
Prototype), `references/engineering.md`, `references/security.md` (section Agent safety, and Plan item
5 for dependencies), `templates/prototype.md`, and from `references/conventions.md` the sections
Language, Naming, Git, Checks, Output discipline, Writing style, Asking the human, Reporting to the
human and Untrusted content. `<skills-dir>` is `dirname "$(realpath <this skill dir>)"`. This stage is
optional: no other stage requires it, and `wf next` never starts it.

## Arguments

`wf prototype [<words>]`: with no record, the words narrow or widen the default scope; in `Scoping` they
correct the scope digest; in `Ready for feedback` they are feedback for a new revision.
`wf prototype done [adopt|reference|discard]` records the human's decision; the argument is the choice.

## Preconditions

- `development/project/prd.md` and `spec.md` both have `Status: Approved`. Otherwise stop, name what
  is missing (`wf prd` or `wf spec`), and report the next step (Reporting to the human).
- `git check-ignore -q development` succeeds. Otherwise stop and name the installer command,
  `python3 <workflow-repo>/install.py --project <main clone> --agents <agents>`, where `<workflow-repo>`
  is `dirname "<skills-dir>"`.
- An existing record continues from its `Status` (see Arguments; `Building` resumes the build). A
  closed record (`Adopted`, `Reference`, `Discarded`) is never revised: on a new request, and only
  after the human's yes, rename it to `development/project/prototype-<slug>.md` and start a new record
  with a new slug. Refuse while an adopted prototype's adoption wave is not `Merged`.
- If the PRD or spec revision is newer than the record's, say so and propose a new scope before building.

## Scope (one confirmation)

Read the PRD's requirements and the spec's layout, stack, contracts and security boundaries. Show one
compact digest:
- the PRD IDs covered and the user path for each; by default, the happy path of every requirement of
  the PRD's highest priority (`Must`);
- the stack and layout from the spec, never a stack the spec does not name;
- what is simulated: storage (in memory or SQLite), sign-in (a fixed fake user), external services
  (stubs behind the spec's interfaces), seed data;
- the package manager and registry, each dependency with its version, any install script a package
  needs, and any toolchain to install;
- the slug and branch `prototype/<slug>` (lowercase, hyphenated, at most 5 words), and an empty initial
  commit on the base branch when it has no commit yet;
- the run command and URL, and what is out of scope: error handling beyond the happy path, lower
  priorities, performance, deployment.

Write `development/project/prototype.md` from the template with `Status: Scoping`, the digest under
*Scope* and `Branch`, then wait for an explicit yes. Record it as `Scope approved: revision <n>
(<timestamp>) — "<the human's words>"`; it holds for later revisions within this scope. Corrections
change the digest and ask again. Silence or a generic "continue" is not a yes.

## Build

1. **Branch.** Use the `Base branch` of the `wf` block; before `wf init`, resolve it as
   `<skills-dir>/wf-init/SKILL.md` → Do, step 2 says. If the base has no commit and the scope named the
   initial commit, run `git commit --allow-empty -m "chore: initialize repository"` only when
   `git diff --cached --quiet` succeeds; otherwise ask. Create the recorded branch from the base tip in the
   sibling worktree `<repo>-worktrees/prototype-<slug>`, never inside the repository, and install the
   skills there as `<skills-dir>/wf-new-worktree/SKILL.md` does. Record `Worktree`, `Base branch` and
   `Base commit`, and set `Status: Building`. If the branch or worktree exists, continue in it with the
   recorded base; never overwrite it. On a resume, `git -C <Worktree> status --porcelain` and
   `git -C <Worktree> log --oneline <Base commit>..HEAD` show what is built. If you cannot work in the
   worktree (a sandbox, or a prompt on every write), stop and name the directory, `wf prototype`, and
   the fix: start a session there, or allow `<repo>-worktrees` in the agent's permissions.
2. **Build area by area** in the spec's layout, following `references/engineering.md`. Put each
   simulation behind the interface the spec defines, so a later task can replace it, mark it with a
   comment `PROTOTYPE: simulated <what>`, and list it under *Simulations and shortcuts*. Use fake data
   only: no real credentials, personal data or production endpoints, and serve on localhost only.
   UI copy follows the product's language.
3. **Install** only what the scope lists, and only into the worktree. Turn dependency install scripts
   off where the package manager allows, unless the scope names a package that needs one. Ask before
   any other package, and get a separate yes that names it for `sudo`, a global install or a piped
   download script.
4. **Commit** each area as `wip(prototype): <area>`, staging explicit paths; never `development/`.
5. **Smoke check.** Build and start the prototype, then walk the happy path of each covered requirement.
   For a web UI with a browser tool, run from `<Worktree>`
   `python3 <skills-dir>/wf-init/scripts/dev-server.py start --url <url> --server '<command>'`, and
   always finish from there with `python3 <skills-dir>/wf-init/scripts/dev-server.py stop`; if the URL
   already answers, ask the human to stop their server. Otherwise use the CLI or API. Fill *Coverage*
   with what you observed. A requirement you could not observe is `NOT VERIFIED`, never `Works`. Fix a
   failing step at most 3 times, and stop sooner when the same failure repeats; then mark it `Broken` and
   go on. If the `wf` block lists checks, report every row in the conventions format (`NOT RUN` or `N/A`
   with its reason when it did not run); if none ran, say so. Without a block, write
   `No project checks configured yet`.
6. **Record.** Fill *How to run*, *Dependencies* and *Learnings for the spec and roadmap*; set `Commit`
   to `git -C <Worktree> rev-parse HEAD` and `Status: Ready for feedback`.

Report the exact run commands and URL, what to try, what is simulated, and what is `Broken` or
`NOT VERIFIED`. Then the next step: try it, then `wf prototype <feedback>` or `wf prototype done`.

## Feedback

Quote the human's feedback under *Feedback and revisions*, increment `Revision`, and set
`Status: Building`. Within the approved scope, build it as Build steps 2–6 describe. Feedback that adds
a requirement, a dependency or another stack is a scope change: set `Status: Scoping`, write the delta
under *Scope*, and wait for a yes. A gap it reveals in the PRD or spec goes under *Learnings*; this stage
never edits the PRD, spec or roadmap.

## Done (`wf prototype done`)

Without an argument, show the coverage, the simulations and the learnings, and ask the human to choose.
`adopt` and `reference` need `Ready for feedback`, a clean worktree, and a branch `HEAD` equal to
`Commit`; otherwise finish the revision first. From `Scoping` or `Building`, offer only `discard` or
finishing the build. Record `Decision: <choice> (<timestamp>) — "<the human's words>"`:
- **adopt** (`Status: Adopted`): the prototype becomes the starting code, through one ordinary task in
  the first wave not yet started (`references/project.md` → Prototype). Nothing is merged now.
- **reference** (`Status: Reference`): task plans may start from its files at the recorded commit.
- **discard** (`Status: Discarded`): the learnings stay. Offer the cleanup.

On a closed record, `wf prototype done` offers only the cleanup: for `Adopted` once the adoption wave is
`Merged`, for `Reference` when the human says no task needs it, for `Discarded` at any time. Name the
worktree and branch, and follow `references/project.md` → Prototype (*Cleanup*) only after an explicit
yes.

Then report the next step (`wf roadmap`, also to revise a roadmap for an adopted prototype). When the
learnings change the PRD or spec, ask first whether to revise it (a new revision loses its approval).

## Never

Never push the prototype branch, open a PR, merge it into the base or a wave branch, or deploy it. Never
call it reviewed or tested beyond its smoke evidence. Never edit the PRD, spec, roadmap or `AGENTS.md`.

End with `— wf-prototype · <timestamp>`.
