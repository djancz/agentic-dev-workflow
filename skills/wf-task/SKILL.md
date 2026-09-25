---
name: wf-task
description: >-
  Create, drop or split a pseudo-ticket (TASK-N) in the project's gitignored development/tasks/ directory
  — the entry point of the wf workflow. Use when the user describes something to build, fix or change and
  wants it tracked as a task, or asks to drop or split one. Triggers: "wf task", "new task", "create task",
  "wf task drop", "wf task split", "nový task", "založ task", "vytvoř ticket", "zahoď task".
---

# wf-task — create, drop or split a task

Paths are relative to this skill's directory. Read `references/workflow.md` (sections Layout and Artifact
headers), `references/project.md` (Waves), and `references/conventions.md` (sections Naming, Task scope
and Writing style).

To list tasks or see where one stands, use `wf status` instead.

## New task (default)

1. **Check the setup.** Run `git check-ignore -q development`. If `development/` is not ignored, stop and
   suggest `wf init`, because task files must never be committed.
2. **Number it.** Reserve the directory with `python3 scripts/new-id.py --kind task --slug <slug>
   development`; it prints `development/tasks/TASK-<N>-<slug>`. Never pick the number yourself: parallel
   sessions share `development/`.
3. **Write it.** Create `TASK-<N>.md` in that directory from `templates/task.md`, filled
   from what the user said:
   - The ticket says *what* and *why*, not *how*. Look at the code only to name things correctly, not to
     design the solution.
   - Acceptance criteria must be checkable: a command, an output, a behaviour. "Works well" is not a
     criterion.
   - If the human points to files (a spec, logs, screenshots), copy them to `input/` in the task
     directory only if they are needed and safe to retain; never copy secrets. List retained paths under
     *Problem*, so every later stage and reviewer can read them.
   - Use the roadmap's wave ID when present. For a standalone existing-project task, reserve a one-task
     wave with `python3 scripts/new-id.py --kind wave development` and record its ID in the ticket.
   - Ask at most 3 questions, and only questions whose answer changes what gets built. Record everything
     else you assumed under *Notes*.
4. **One concern, one task.** Several requests about the same concern go into one ticket, each with its
   own acceptance criteria. Propose separate tasks only when `references/conventions.md` → Task scope
   calls for a split: unrelated requests, or a complete rework. Otherwise write one ticket without
   asking.

Report: the path, the title, the acceptance criteria, and `Next: wf plan TASK-<N>`.

## Drop

Set `Status: Dropped` and add one line under *Notes* with the date and the reason. Do not delete anything
and do not touch the branch; tell the human the branch name if one exists. Then `Next: nothing`.

## Split

Use this when the human asks for a split, or accepts one that a plan proposed:
1. Create one new task per part, following the steps above. Under each new ticket's *Notes*, write
   `Split from TASK-<N>`.
2. Mark the original `Dropped`, with the note `Split into TASK-a, TASK-b`.

Report the new ids and the order in which to do them, then `Next: wf plan TASK-<first>`.

End with `— wf-task · <timestamp>`.
