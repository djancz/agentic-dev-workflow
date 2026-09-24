---
name: wf-prd
description: >-
  Define and approve the product requirements for a new project or major initiative. Use before architecture and roadmap planning. Triggers: "wf prd", "product requirements", "start a project".
---

# wf-prd

Read `references/project.md`, `references/engineering.md`, and `templates/prd.md`.

Inspect the repository and any brief the human provides. Interview for users, problem, desired outcomes,
must-have behavior, exclusions, constraints, data sensitivity, and measurable acceptance. Ask only what
the repository and supplied material cannot answer; recommend a default for each open choice. Do not
invent a stack or architecture yet.

Write `development/project/prd.md` from the template. Keep requirements testable and prioritize a
usable first version. Mark unknowns as assumptions or open decisions. For a revision, retain a short
decision history, increment `Revision`, and return `Status` to Draft.

Show a compact digest of goal, users, first-version requirements, exclusions, risks, and open decisions.
Wait for explicit human approval, then record their words and date. Next: `wf spec`. Do not write code.
