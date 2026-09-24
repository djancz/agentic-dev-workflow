---
name: wf-spec
description: >-
  Turn an approved PRD into a reviewable, implementation-free architecture and contract spec. Use before the roadmap for new projects or major initiatives. Triggers: "wf spec", "architecture spec", "design the project".
---

# wf-spec

Read `references/project.md`, `references/engineering.md`, `references/security.md`, and
`templates/spec.md`. Require an approved `development/project/prd.md`. Inspect the existing project
structure and declared constraints before proposing a design.

Write `development/project/spec.md` with the simplest architecture that meets the PRD. Define the
feature boundaries, entry points, data ownership, public interfaces and contracts needed for independent
tasks, security boundaries, quality checks, deployment assumptions if relevant, and test strategy.
Choose a stack only where the human has not already chosen one; explain material tradeoffs. Use diagrams
only when they clarify a boundary. Keep later details flexible until the related milestone is planned.

Review the spec in a fresh context for feasibility, PRD coverage, security, and avoidable complexity.
Accept `reviewer=`, `model=`, and `effort=` overrides from the project defaults. Use a native subagent
when it supports the selected settings, otherwise a headless run with `scripts/run-agent.sh`. If a
setting is unsupported or the review fails, stop without an approval verdict.
Read `references/models.md` for non-default model or effort choices.
Record findings and revisions in the spec. Show the human the decisions, contracts, risks, and review
result. Explicit approval records their words and date; feedback returns it to Draft. Next: `wf roadmap`.
Do not scaffold code.
