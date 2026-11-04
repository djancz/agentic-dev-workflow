---
name: wf-prototype
description: >-
  Optional, after an approved PRD and spec: build a runnable prototype of the project on its own branch
  so the human can try it early, revise it from their feedback, then adopt it as the project baseline,
  keep it as a reference, or discard it. Never pushes or merges it. Triggers: "wf prototype",
  "wf prototype done", "build a prototype", "prototyp", "udělej prototyp", "postav prototyp".
---

# wf-prototype — something to try, early

Role: **author**. Paths are relative to this skill's directory. Read `references/project.md` (section
Prototype), `references/engineering.md`, `references/security.md` (sections Sensitivity and Plan, item 5
for dependencies), `templates/prototype.md`, and from `references/conventions.md` the sections Language,
Naming, Git, Checks, Output discipline, Writing style and Reporting to the human.
`<skills-dir>` is `dirname "$(realpath <this skill dir>)"`.

This stage is optional: no other stage requires it, and `wf next` never starts it.

## Arguments

`wf prototype [<focus>]` proposes a scope and, after the human's yes, builds the prototype. `<focus>`
narrows or widens the default scope. On a prototype that is `Ready for feedback`, the words are feedback
for a new revision. `wf prototype done [adopt|reference|discard]` records the human's decision.

## Preconditions

- `development/project/prd.md` and `spec.md` both have `Status: Approved`. Otherwise stop with
  `Next: wf prd` or `Next: wf spec`.
- `git check-ignore -q development` succeeds. Otherwise stop and name the installer command
  (`references/project.md` → Private state).
- If `development/project/prototype.md` exists, continue from its `Status`: `Scoping` shows the scope
  again, `Building` resumes the build, `Ready for feedback` takes the words as feedback. A closed
  prototype (`Adopted`, `Reference`, `Discarded`) starts a new revision only after the human's yes.
- If the PRD or spec revision is newer than the record's, say so and propose a new scope before building.

## Scope (one confirmation)

Read the PRD's requirements and the spec's layout, stack, contracts and security boundaries. Show one
compact digest:
- the PRD IDs covered and the user path for each; by default, the happy path of every Must requirement;
- the stack and layout from the spec, never a stack the spec does not name;
- what is simulated: storage (in memory or SQLite), sign-in (a fixed fake user), external services
  (stubs behind the spec's interfaces), seed data;
- each dependency to install, with its version and source;
- the run command and URL, and what is out of scope: error handling beyond the happy path, non-Must
  requirements, performance, deployment.

Write the record from the template with `Status: Scoping` and wait for an explicit yes. Record it as
`Scope approved: revision <n> (<timestamp>) — "<the human's words>"`. Corrections change the digest and
ask again. Silence or a generic "continue" is not a yes.

## Build

1. **Branch.** Use the `Base branch` of the `wf` block; before `wf init`, resolve it as
   `<skills-dir>/wf-init/SKILL.md` → Do, step 2 says. If the base has no commit, ask to create an empty
   one (`git commit --allow-empty -m "chore: initialize repository"`) and do it only after a yes. Create
   branch `prototype/<slug>` from the base tip in the sibling worktree `<repo>-worktrees/prototype-<slug>`,
   never inside the repository, and install the skills there as `<skills-dir>/wf-new-worktree/SKILL.md`
   does. If the branch or worktree exists, continue in it; never overwrite it. Record `Branch`,
   `Worktree` and `Base`, and set `Status: Building`. If you cannot work in the worktree, stop and name
   the directory and `wf prototype`.
2. **Build area by area** in the spec's layout, following `references/engineering.md`. Put each
   simulation behind the interface the spec defines, so a later task can replace it, mark it with a
   comment `PROTOTYPE: simulated <what>`, and list it under *Simulations and shortcuts*. Use fake data
   only: no real credentials, personal data or production endpoints, and serve on localhost only.
   Install only the dependencies the scope lists; ask before any other. UI copy follows the product's
   language.
3. **Commit** each area as `wip(prototype): <area>`, staging explicit paths; never `development/`.
4. **Smoke check.** Build and start the prototype, then walk the happy path of each covered requirement.
   For a web UI with a browser tool, start the server with
   `python3 <skills-dir>/wf-init/scripts/dev-server.py start --url <url> --server '<command>'` and always
   finish with `python3 <skills-dir>/wf-init/scripts/dev-server.py stop`; otherwise use the CLI or API.
   Fill *Coverage* with what you observed. A requirement you could not observe is `NOT VERIFIED`, never
   `Works`. Fix a failing step at most 3 times, and stop sooner when the same failure repeats; then mark
   it `Broken` and go on. If the `wf` block lists checks, run those that apply and report them in the
   conventions format; otherwise write `No project checks configured yet`.
5. **Record.** Fill *How to run*, *Dependencies* and *Learnings for the spec and roadmap*; set `Commit`
   to `git rev-parse HEAD` and `Status: Ready for feedback`.

Report the exact run commands and URL, what to try, what is simulated, and what is `Broken` or
`NOT VERIFIED`. Then `Next: try it, then wf prototype <feedback> or wf prototype done`.

## Feedback

Quote the human's feedback under *Feedback and revisions* and increment `Revision`. Changes within the
approved scope are built at once, as Build steps 2–5 describe. Feedback that adds a PRD requirement, a
dependency or another stack is a scope change: show the delta and wait for a yes. A gap it reveals in
the PRD or spec goes under *Learnings*; this stage never edits the PRD, spec or roadmap.

## Done (`wf prototype done`)

Show the coverage, the simulations and the learnings, and ask the human to choose. Record
`Decision: <choice> (<timestamp>) — "<the human's words>"`:
- **adopt** (`Status: Adopted`): the prototype becomes the starting code, through one ordinary task in
  the first wave (`references/project.md` → Prototype). Nothing is merged now.
- **reference** (`Status: Reference`): task plans may start from its files at the recorded commit.
- **discard** (`Status: Discarded`): the learnings stay. Offer to remove the worktree and the branch,
  naming both; run `git worktree remove` and `git branch -D` only after an explicit yes.

`Next: wf spec` when the learnings change the spec (a new revision loses its approval); otherwise
`Next: wf roadmap`, also to revise an approved roadmap for an adopted prototype.

## Never

Never push the prototype branch, open a PR, merge it into the base or a wave branch, or deploy it. Never
call it reviewed or tested beyond its smoke evidence. Never edit the PRD, spec, roadmap or `AGENTS.md`.

End with `— wf-prototype · <timestamp>`.
