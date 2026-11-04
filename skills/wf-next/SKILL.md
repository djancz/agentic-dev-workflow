---
name: wf-next
description: >-
  Run the wf workflow from where it stands up to the next human gate: resolve the next stage as
  wf status does, run it and the stages after it, and stop at every gate, question, failure, or
  ambiguity. Triggers: "wf next", "continue", "next step", "pokračuj".
---

# wf-next

Read `references/project.md`, `references/workflow.md`, and from `references/conventions.md` the
section Reporting to the human. `<skills-dir>` is `dirname "$(realpath <this skill dir>)"`.

Resolve the next command exactly as `<skills-dir>/wf-status/SKILL.md` → Status does, say it in one
line, then load that stage's skill from `<skills-dir>/wf-<stage>/SKILL.md` and follow it with the
arguments the human gave. When it finishes, resolve again and continue with the following stage. Stop,
and report where things stand and the exact next action, when:
- a human gate or approval is next: PRD, spec, prototype scope, roadmap, Gate 1, Gate 2, push and PR,
  maintenance, deployment, or worktree cleanup;
- the resolved next action is the human trying a prototype that is `Ready for feedback`, then
  `wf prototype <feedback>` or `wf prototype done`;
- a stage asks the human a question, fails, escalates, or reports `NOT RUN` for a required stage;
- a stage ends without changing the recorded state, so the same command would run again;
- more than one task or wave could continue and no ID was given: list them and ask;
- the next stage runs in another worktree and you cannot work there: name the directory and the command.

`wf next` never records an approval, never pushes, never runs `wf deploy`, and never skips a stage or an
independent review. It never starts `wf prototype`, which is optional; it only resumes one that is
`Building`. Passing Gate 1 and Gate 2 by policy, up to the push-and-PR question, is `wf auto`,
which needs its own explicit request; `wf next` never acts on an earlier `wf auto` authorization. Stages that need a fresh context
delegate as their skills say. End with `— wf-next · <timestamp>`.
