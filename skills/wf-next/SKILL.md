---
name: wf-next
description: >-
  Run the wf workflow from where it stands up to the next human gate: resolve the next stage as
  wf status does, run it and the stages after it, and stop at every gate, question, failure, or
  ambiguity. Triggers: "wf next", "continue", "next step", "pokračuj".
---

# wf-next

Read `references/project.md`, `references/workflow.md`, and from `references/conventions.md` the
sections Asking the human and Reporting to the human. `<skills-dir>` is
`dirname "$(realpath <this skill dir>)"`.

Resolve the next command exactly as `<skills-dir>/wf-status/SKILL.md` → Status does, say it in one
line, then load that stage's skill from `<skills-dir>/wf-<stage>/SKILL.md` and follow it with the
arguments the human gave. When it finishes, resolve again and continue with the following stage.

A gate (PRD, spec, prototype scope, roadmap, Gate 1, Gate 2) or a stage's question: ask it as
`references/conventions.md` → Asking the human says. When the human answers explicitly, the stage's skill
records the answer as at any gate, and `wf next` continues from the new state; a change request goes
back to that stage's review. Stop, and report where things stand and the exact next action, when:
- a question or gate gets no explicit answer;
- push and PR, maintenance, deployment, or worktree cleanup is next: the stage asks, and `wf next` ends
  with that question;
- the resolved next action is the human trying a prototype that is `Ready for feedback`, then
  `wf prototype <feedback>` or `wf prototype done`;
- a stage fails, escalates, or reports `NOT RUN` for a required stage; ask its escalation choice as
  `references/conventions.md` → Asking the human says;
- a stage ends without changing the recorded state, so the same command would run again;
- more than one task or wave could continue and no ID was given: ask which, with each one and its next
  command as an option;
- the next stage runs in another worktree and you cannot work there: name the directory and the command.

`wf next` never records an approval on its own (the stage's skill records only the human's explicit
answer), never pushes, never runs `wf deploy`, and never skips a stage or an
independent review. It never starts `wf prototype`, which is optional; it only resumes one that is
`Building`. Passing Gate 1 and Gate 2 by policy, up to the push-and-PR question, is `wf auto`,
which needs its own explicit request; `wf next` never acts on an earlier `wf auto` authorization. Stages that need a fresh context
delegate as their skills say. End with `— wf-next · <timestamp>`.
